#!/usr/bin/env python3
import argparse
import csv
import json
import re
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from xml.etree import ElementTree as ET
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EXCEL = Path("/home/agustin/Downloads/DATOS - INSTITUCIONES 1.xlsx")
DATA_DIR = ROOT / "data"
OUTPUT_JSON = DATA_DIR / "instituciones.json"
CACHE_JSON = DATA_DIR / "geocode-cache.json"
ARCGIS_CACHE_JSON = DATA_DIR / "arcgis-geocode-cache.json"
EXTRA_CSV = DATA_DIR / "lugares_extra.csv"
WATER_WELLS_EXCEL = DATA_DIR / "Pozos de agua.xlsx"
STREETS_GEOJSON = DATA_DIR / "calles_ciudad.geojson"
MANUAL_REVIEW_CSV = DATA_DIR / "revision_manual" / "instituciones_no_identificadas.csv"
MANUAL_OVERRIDES_CSV = DATA_DIR / "manual_overrides.csv"

WATER_WELL_MANUAL_ESTIMATES = {
    # Estimaciones visuales sobre captura limacalles.png. Reemplazar por
    # coordenadas municipales cuando esten disponibles.
    "LI006": {"lat": -34.0430, "lon": -59.1997, "source": "estimado_manual_lima"},
    "LI007": {"lat": -34.0402, "lon": -59.2073, "source": "estimado_manual_lima"},
    "LI008": {"lat": -34.0408, "lon": -59.1907, "source": "estimado_manual_lima"},
    "LI009": {"lat": -34.0492, "lon": -59.1961, "source": "estimado_manual_lima"},
    "LI010": {"lat": -34.0432, "lon": -59.2010, "source": "estimado_manual_lima"},
    # Sin coordenadas exactas. A pedido operativo se agrupan sobre la ruta que
    # conecta Ciudad con Localidad, como referencia visual del corredor.
    "ZA018": {"lat": -34.08890, "lon": -59.06400, "source": "estimado_ruta_ciudad_lima"},
    "ZA019B": {"lat": -34.08895, "lon": -59.06410, "source": "estimado_ruta_ciudad_lima"},
    "ZA020B": {"lat": -34.08900, "lon": -59.06420, "source": "estimado_ruta_ciudad_lima"},
    "ZA021B": {"lat": -34.08905, "lon": -59.06430, "source": "estimado_ruta_ciudad_lima"},
    "ZA022": {"lat": -34.08910, "lon": -59.06440, "source": "estimado_ruta_ciudad_lima"},
    "ZA023": {"lat": -34.08915, "lon": -59.06450, "source": "estimado_ruta_ciudad_lima"},
    "ZA024B": {"lat": -34.08920, "lon": -59.06460, "source": "estimado_ruta_ciudad_lima"},
    "ZA053": {"lat": -34.08925, "lon": -59.06470, "source": "estimado_ruta_ciudad_lima"},
    # Centros de poligonos locales de barrios/loteos.
    "ZA073": {"lat": -34.12386, "lon": -59.07200, "source": "estimado_barrio"},
    "ZA075": {"lat": -34.12177, "lon": -59.05177, "source": "estimado_barrio"},
    # Alias local: el listado dice Pividal, la capa OSM local lo trae como Rafael Pidival.
    "ZA070": {"lat": -34.115276, "lon": -59.0355766, "source": "estimado_calles_alias"},
}

NS = {
    "a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "r": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

RUBRO_ALIASES = {
    "EDUCACION": "EDUCACION",
    "EDUCACIÒN": "EDUCACION",
    "EDUCACIÓN": "EDUCACION",
    "TRAB/PROFESIONALES": "PROFESIONALES",
    "COMEDOR": "COMEDORES",
    "COMEDOR-MERENDERO": "COMEDORES",
    "VECINAL": "UNION VECINAL",
    "SOC.DE FOMENTO": "SOC. DE FOMENTO",
    "ASOC. CIVIL": "ASOC. CIVIL",
}


def strip_accents(value):
    normalized = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def clean_text(value):
    value = str(value or "").replace("\xa0", " ").strip()
    value = re.sub(r"\s+", " ", value)
    return value


def normalize_rubro(value):
    value = clean_text(value).upper()
    value = strip_accents(value)
    return RUBRO_ALIASES.get(value, value)


def column_index(cell_ref):
    letters = re.match(r"([A-Z]+)", cell_ref).group(1)
    value = 0
    for char in letters:
        value = value * 26 + ord(char) - 64
    return value - 1


def read_sheet_rows(xlsx_path, sheet_name):
    with ZipFile(xlsx_path) as archive:
        shared = []
        if "xl/sharedStrings.xml" in archive.namelist():
            shared_root = ET.fromstring(archive.read("xl/sharedStrings.xml"))
            for item in shared_root.findall("a:si", NS):
                shared.append(
                    "".join(
                        text.text or ""
                        for text in item.iter(
                            "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
                        )
                    )
                )

        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        rels = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
        rel_map = {rel.attrib["Id"]: rel.attrib["Target"] for rel in rels}

        target = None
        for sheet in workbook.find("a:sheets", NS):
            if sheet.attrib["name"].strip() == sheet_name:
                rel_id = sheet.attrib[
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
                ]
                target = rel_map[rel_id]
                break

        if target is None:
            raise RuntimeError(f"No se encontro la hoja {sheet_name!r}")
        if not target.startswith("xl/"):
            target = f"xl/{target}"

        root = ET.fromstring(archive.read(target))
        rows = []
        for row in root.findall(".//a:sheetData/a:row", NS):
            values = []
            for cell in row.findall("a:c", NS):
                idx = column_index(cell.attrib["r"])
                while len(values) <= idx:
                    values.append("")

                value_node = cell.find("a:v", NS)
                value = "" if value_node is None else value_node.text or ""
                if cell.attrib.get("t") == "s" and value:
                    value = shared[int(value)]
                elif cell.attrib.get("t") == "inlineStr":
                    value = "".join(
                        text.text or ""
                        for text in cell.iter(
                            "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}t"
                        )
                    )
                values[idx] = clean_text(value)
            rows.append(values)
        return rows


def list_sheet_names(xlsx_path):
    with ZipFile(xlsx_path) as archive:
        workbook = ET.fromstring(archive.read("xl/workbook.xml"))
        return [sheet.attrib["name"].strip() for sheet in workbook.find("a:sheets", NS)]


def record_key(name, address=""):
    base = strip_accents(clean_text(name)).upper()
    addr = strip_accents(clean_text(address)).upper()
    return f"{base}|{addr}"


def load_observations(xlsx_path):
    observations = {}
    by_name = {}
    for sheet_name in list_sheet_names(xlsx_path):
        rows = read_sheet_rows(xlsx_path, sheet_name)
        header_index = None
        headers = []
        for index, row in enumerate(rows[:5]):
            normalized = [normalize_header(value) for value in row]
            if "observaciones" in normalized and (
                "denominacion" in normalized or "denominacion_" in normalized
            ):
                header_index = index
                headers = normalized
                break
        if header_index is None:
            continue

        for row in rows[header_index + 1 :]:
            row = row + [""] * max(0, len(headers) - len(row))
            record = {headers[index]: clean_text(value) for index, value in enumerate(row[: len(headers)])}
            obs = record.get("observaciones", "")
            name = record.get("denominacion", "") or record.get("denominacion_", "")
            address = record.get("direccion", "")
            if not obs or not name:
                continue
            observations[record_key(name, address)] = obs
            by_name.setdefault(record_key(name), obs)
    return observations, by_name


def load_institutions(xlsx_path):
    rows = read_sheet_rows(xlsx_path, "PADRON GENERAL")
    observations, observations_by_name = load_observations(xlsx_path)
    headers = [normalize_header(value) for value in rows[0]]
    records = []

    for row in rows[1:]:
        row = row + [""] * max(0, len(headers) - len(row))
        record = {headers[index]: clean_text(value) for index, value in enumerate(row[: len(headers)])}

        name = record.get("denominacion", "")
        address = record.get("direccion", "")
        if not name or not address:
            continue

        city = clean_text(record.get("ciudad", "")) or "Ciudad"
        rubro = normalize_rubro(record.get("rubro", "SIN RUBRO"))
        full_address = build_full_address(address, city)
        obs = observations.get(record_key(name, address), "")
        if not obs:
            obs = observations_by_name.get(record_key(name), "")

        records.append(
            {
                "id": len(records) + 1,
                "grupo": clean_text(record.get("grupo", "")),
                "numero": clean_text(record.get("no", "")),
                "nombre": name,
                "rubro": rubro,
                "direccion": address,
                "ciudad": city.title() if city.isupper() else city,
                "telefono": clean_text(record.get("telefono", "")),
                "observaciones": obs,
                "direccion_busqueda": full_address,
                "lat": None,
                "lon": None,
                "geocode_estado": "pendiente",
            }
        )
    return records


def load_extra_places():
    if not EXTRA_CSV.exists():
        return []

    records = []
    with EXTRA_CSV.open(newline="", encoding="utf-8-sig") as file:
        for row in csv.DictReader(file):
            name = clean_text(row.get("nombre", ""))
            rubro = normalize_rubro(row.get("rubro", "PLAZA") or "PLAZA")
            lat = clean_text(row.get("lat", ""))
            lon = clean_text(row.get("lon", ""))
            if not name or not lat or not lon:
                continue

            records.append(
                {
                    "id": f"extra-{len(records) + 1}",
                    "grupo": "EXTRA",
                    "numero": "",
                    "nombre": name,
                    "rubro": rubro,
                    "direccion": clean_text(row.get("direccion", "")),
                    "ciudad": clean_text(row.get("ciudad", "Ciudad")) or "Ciudad",
                    "telefono": clean_text(row.get("telefono", "")),
                    "observaciones": clean_text(row.get("observaciones", "")),
                    "direccion_busqueda": "",
                    "lat": float(lat.replace(",", ".")),
                    "lon": float(lon.replace(",", ".")),
                    "geocode_estado": "manual",
                    "geocode_nombre": clean_text(row.get("fuente", "")),
                    "tipo_registro": "extra",
                }
            )
    return records


def load_water_wells():
    if not WATER_WELLS_EXCEL.exists():
        return []

    rows = read_sheet_rows(WATER_WELLS_EXCEL, "Hoja1")
    if not rows:
        return []

    headers = [normalize_header(value) for value in rows[0]]
    records = []
    for row in rows[1:]:
        row = row + [""] * max(0, len(headers) - len(row))
        record = {headers[index]: clean_text(value) for index, value in enumerate(row[: len(headers)])}
        code = record.get("pozo", "")
        address = record.get("ubicacion", "")
        if not code or not address:
            continue

        city = clean_text(record.get("localidad", "")) or "Ciudad"
        flow = clean_text(record.get("caudal_m3_h", ""))
        observations = f"Caudal: {flow} m3/h" if flow else "Pozo de agua"
        records.append(
            {
                "id": f"pozo-{code}",
                "grupo": "SERVICIOS",
                "numero": code,
                "nombre": f"Pozo {code}",
                "rubro": "POZOS DE AGUA",
                "direccion": address,
                "ciudad": city.title() if city.isupper() else city,
                "telefono": "",
                "observaciones": observations,
                "direccion_busqueda": build_full_address(address, city),
                "lat": None,
                "lon": None,
                "geocode_estado": "pendiente",
                "tipo_registro": "pozo_agua",
                "caudal_m3_h": flow,
            }
        )
    return records


def load_manual_overrides(max_file_line=None):
    overrides = {}
    for path, use_line_limit in ((MANUAL_OVERRIDES_CSV, False), (MANUAL_REVIEW_CSV, True)):
        if not path.exists():
            continue

        with path.open(newline="", encoding="utf-8-sig") as file:
            reader = csv.DictReader(file)
            for data_line, row in enumerate(reader, start=2):
                if use_line_limit and max_file_line is not None and data_line > max_file_line:
                    break
                status = clean_text(row.get("estado", "")).upper()
                if status != "IDENTIFICADA":
                    continue
                record_id = clean_text(row.get("id", ""))
                if not record_id:
                    continue
                overrides[record_id] = {
                    "nombre": clean_text(row.get("nombre", "")),
                    "rubro": normalize_rubro(row.get("rubro", "")),
                    "direccion": clean_text(row.get("direccion", "")),
                    "ciudad": clean_text(row.get("ciudad", "")),
                    "telefono": clean_text(row.get("telefono", "")),
                    "observaciones": clean_text(row.get("observaciones", "")),
                    "lat_manual": clean_text(row.get("lat_manual", "")),
                    "lon_manual": clean_text(row.get("lon_manual", "")),
                    "observacion_revision": clean_text(row.get("observacion_revision", "")),
                }
    return overrides


def write_manual_overrides_from_records(records, ids):
    fields = [
        "id",
        "estado",
        "nombre",
        "rubro",
        "direccion",
        "ciudad",
        "telefono",
        "observaciones",
        "lat_manual",
        "lon_manual",
        "observacion_revision",
    ]
    rows = []
    for record in records:
        if str(record["id"]) not in ids:
            continue
        rows.append(
            {
                "id": record["id"],
                "estado": "IDENTIFICADA",
                "nombre": record.get("nombre", ""),
                "rubro": record.get("rubro", ""),
                "direccion": record.get("direccion", ""),
                "ciudad": record.get("ciudad", ""),
                "telefono": record.get("telefono", ""),
                "observaciones": record.get("observaciones", ""),
                "lat_manual": "",
                "lon_manual": "",
                "observacion_revision": "",
            }
        )
    if not rows:
        return
    with MANUAL_OVERRIDES_CSV.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def load_review_manual_overrides(max_file_line=None):
    if not MANUAL_REVIEW_CSV.exists():
        return {}

    overrides = {}
    with MANUAL_REVIEW_CSV.open(newline="", encoding="utf-8-sig") as file:
        reader = csv.DictReader(file)
        for data_line, row in enumerate(reader, start=2):
            if max_file_line is not None and data_line > max_file_line:
                break
            status = clean_text(row.get("estado", "")).upper()
            if status != "IDENTIFICADA":
                continue
            record_id = clean_text(row.get("id", ""))
            if not record_id:
                continue
            overrides[record_id] = {
                "nombre": clean_text(row.get("nombre", "")),
                "rubro": normalize_rubro(row.get("rubro", "")),
                "direccion": clean_text(row.get("direccion", "")),
                "ciudad": clean_text(row.get("ciudad", "")),
                "telefono": clean_text(row.get("telefono", "")),
                "observaciones": clean_text(row.get("observaciones", "")),
                "lat_manual": clean_text(row.get("lat_manual", "")),
                "lon_manual": clean_text(row.get("lon_manual", "")),
                "observacion_revision": clean_text(row.get("observacion_revision", "")),
            }
    return overrides


def apply_manual_overrides(records, max_file_line=None):
    overrides = load_manual_overrides(max_file_line)
    for record in records:
        override = overrides.get(str(record["id"]))
        if not override:
            continue

        for field in ("nombre", "rubro", "direccion", "ciudad", "telefono", "observaciones"):
            if override.get(field):
                record[field] = override[field]
        if override.get("observacion_revision"):
            record["observaciones"] = clean_text(
                f"{record.get('observaciones', '')} | Revision: {override['observacion_revision']}"
            ).strip(" |")
        record["direccion_busqueda"] = build_full_address(record["direccion"], record["ciudad"])
        record["geocode_estado"] = "revision_manual"

        if override.get("lat_manual") and override.get("lon_manual"):
            record["lat"] = float(override["lat_manual"].replace(",", "."))
            record["lon"] = float(override["lon_manual"].replace(",", "."))
            record["geocode_estado"] = "manual"
            record["geocode_nombre"] = "Coordenadas cargadas manualmente"


def apply_cached_geocodes(records):
    cache = load_cache()
    arcgis_cache = load_arcgis_cache()
    for record in records:
        cached_queries = [query for query in build_search_queries(record) if query in cache]
        result = next((cache[query] for query in cached_queries if cache[query]), None)
        if not result:
            arcgis_cached_queries = [query for query in build_search_queries(record) if query in arcgis_cache]
            result = next((arcgis_cache[query] for query in arcgis_cached_queries if arcgis_cache[query]), None)
        if result:
            record["lat"] = result["lat"]
            record["lon"] = result["lon"]
            record["geocode_estado"] = "ok"
            record["geocode_nombre"] = result.get("display_name", "")
        elif cached_queries:
            record["geocode_estado"] = "sin_resultado"


def normalize_header(value):
    value = strip_accents(clean_text(value).lower())
    value = value.replace("º", "o").replace("°", "o")
    value = re.sub(r"[^a-z0-9]+", "_", value).strip("_")
    return value or "_"


def build_full_address(address, city):
    address = re.sub(r"\([^)]*\)", "", address)
    address = clean_text(address)
    city_clean = strip_accents(clean_text(city)).upper()
    if city_clean == "LIMA":
        return f"{address}, Localidad, Buenos Aires, Argentina"
    if city_clean == "CIUDAD":
        return f"{address}, Ciudad, Buenos Aires, Argentina"
    return f"{address}, Ciudad, Buenos Aires, Argentina"


def city_for_search(city):
    city_clean = strip_accents(clean_text(city)).upper()
    if city_clean == "LIMA":
        return "Localidad"
    if city_clean == "CIUDAD":
        return "Ciudad"
    return "Ciudad"


def search_address(address):
    address = re.sub(r"\([^)]*\)", "", address)
    address = clean_text(address)
    address = re.sub(r"\bB\s?\d{4}[A-Z]{0,3}\b", "", address, flags=re.I)
    address = re.sub(r"\bA\s?\d{4}[A-Z]{0,3}\b", "", address, flags=re.I)
    address = re.sub(r"\bProvincia de Buenos Aires\b", "", address, flags=re.I)
    address = re.sub(r"\bDr\.?\b", "", address, flags=re.I)
    address = re.sub(r"\bC\.\s*", "Calle ", address, flags=re.I)
    address = re.sub(r"([A-Za-zÁÉÍÓÚáéíóúÑñ])(\d)", r"\1 \2", address)
    address = re.sub(r"\bNRO\.?\b", "", address, flags=re.I)
    address = re.sub(r"\bN[°ºª]\b", "", address, flags=re.I)
    address = re.sub(r"\bAVDA?\.?\b", "Avenida", address, flags=re.I)
    address = re.sub(r"\bANT[,.\s]+ARGENTINA\b", "Antartida Argentina", address, flags=re.I)
    address = re.sub(r"\bBDO\.?\s+IRIGOYEN\b", "Bernardo de Irigoyen", address, flags=re.I)
    address = re.sub(r"\bH\.?\s+YRIGOYEN\b", "Hipolito Yrigoyen", address, flags=re.I)
    address = re.sub(r"\bSGTO\.?\s+CABRAL\b", "Calle Central", address, flags=re.I)
    address = re.sub(r"\bSTO\.?\s+CABRAL\b", "Calle Central", address, flags=re.I)
    address = re.sub(r"\bGRAL\.?\s+URQUIZA\b", "General Urquiza", address, flags=re.I)
    address = re.sub(r"\bF\.?\s*PAGOLA\b", "Felix Pagola", address, flags=re.I)
    address = re.sub(r"\bPAHOLA\b", "Pagola", address, flags=re.I)
    address = re.sub(r"\bBELSARIO\b", "Belisario", address, flags=re.I)
    address = re.sub(r"\bJ\.?\s*ACOSTA\b", "Julio A Costa", address, flags=re.I)
    address = re.sub(r"\bJ\s*B\.?\s*JUSTO\b", "Juan B Justo", address, flags=re.I)
    address = re.sub(r"\bALEN\b", "Alem", address, flags=re.I)
    address = re.sub(r"\bALAMEADA\b", "Alameda", address, flags=re.I)
    address = re.sub(r"\bROMULONOYA\b", "Romulo Noya", address, flags=re.I)
    address = re.sub(r"\bHERNADEZ\b", "Hernandez", address, flags=re.I)
    address = re.sub(r"\bMATEHU\b", "Matheu", address, flags=re.I)
    address = re.sub(r"\bCCALE\b", "Calle", address, flags=re.I)
    address = re.sub(r"\bARODROMO\b", "Aerodromo", address, flags=re.I)
    address = re.sub(r"\bE/CALLE\b", "entre calle", address, flags=re.I)
    address = re.sub(r"\bPJE\.?\b", "Pasaje", address, flags=re.I)
    address = re.sub(r"\bPSJE\b", "Pasaje", address, flags=re.I)
    return clean_text(address.replace("-", " "))


def address_variants(address):
    base = search_address(address)
    variants = [base]
    variants.append(
        re.sub(
            r"\b(CASA|LOTE|MZA|MANZANA|ETAPA|PISO|LOCAL COMERCIAL)\b.*$",
            "",
            base,
            flags=re.I,
        )
    )
    if re.search(r"\s+Y\s+", base, flags=re.I):
        variants.append(re.sub(r"\s+Y\s+", " esquina ", base, flags=re.I))
    if "ESQ" in base.upper() or "ESQUINA" in base.upper():
        variants.append(re.sub(r"\bESQ(?:UINA)?\.?\b", "y", base, flags=re.I))
    variants.append(re.sub(r"\bS/N[°º]?\b", "", base, flags=re.I))

    result = []
    seen = set()
    for variant in variants:
        variant = clean_text(variant)
        if variant and variant not in seen:
            seen.add(variant)
            result.append(variant)
    return result


def build_search_queries(record):
    city = city_for_search(record.get("ciudad", "Ciudad"))
    suffix = f"{city}, Buenos Aires, Argentina"
    queries = [record.get("direccion_busqueda", "")]
    queries.extend(f"{address}, {suffix}" for address in address_variants(record["direccion"]))
    queries.append(f"{record['nombre']}, {suffix}")

    result = []
    seen = set()
    for query in queries:
        query = clean_text(query)
        if query not in seen:
            seen.add(query)
            result.append(query)
    return result


def normalize_street_name(value):
    value = strip_accents(clean_text(value)).lower()
    value = re.sub(r"\btaboccini\b", "tabocchini", value)
    value = re.sub(r"\b(avda?|avenida|calle|dr|gral|general)\b\.?", " ", value)
    value = re.sub(r"\bj\.?\s+", "jose ", value)
    value = re.sub(r"\bh\.?\s+", "hipolito ", value)
    value = re.sub(r"\bjuan\s+b\.?\b", "juan b", value)
    value = re.sub(r"[^a-z0-9]+", " ", value)
    return re.sub(r"\s+", " ", value).strip()


def street_tokens_from_address(address):
    text = strip_accents(clean_text(address))
    text = re.sub(r"\([^)]*\)", "", text)
    text = re.sub(r"\besq(?:uina)?\.?\b", " y ", text, flags=re.I)
    text = re.sub(r"\be/calle\b", " y ", text, flags=re.I)
    parts = re.split(r"\s+(?:y|e)\s+", text, maxsplit=1, flags=re.I)
    if len(parts) < 2:
        return []
    return [normalize_street_name(part) for part in parts[:2] if normalize_street_name(part)]


def flatten_line_coordinates(geometry):
    if not geometry:
        return []
    if geometry.get("type") == "LineString":
        return [geometry.get("coordinates") or []]
    if geometry.get("type") == "MultiLineString":
        return geometry.get("coordinates") or []
    return []


def load_street_index():
    if not STREETS_GEOJSON.exists():
        return {}
    payload = json.loads(STREETS_GEOJSON.read_text(encoding="utf-8"))
    index = {}
    for feature in payload.get("features", []):
        name = feature.get("properties", {}).get("name", "")
        normalized = normalize_street_name(name)
        lines = flatten_line_coordinates(feature.get("geometry", {}))
        if normalized and lines:
            index.setdefault(normalized, []).extend(lines)
    return index


def street_lines_for_token(street_index, token):
    if not token:
        return []
    if token in street_index:
        return street_index[token]
    matches = []
    for name, lines in street_index.items():
        if name.startswith(f"{token} ") or token.startswith(f"{name} "):
            matches.extend(lines)
    return matches


def segment_intersection(a, b, c, d):
    ax, ay = a
    bx, by = b
    cx, cy = c
    dx, dy = d
    denom = (ax - bx) * (cy - dy) - (ay - by) * (cx - dx)
    if abs(denom) < 1e-12:
        return None
    px = ((ax * by - ay * bx) * (cx - dx) - (ax - bx) * (cx * dy - cy * dx)) / denom
    py = ((ax * by - ay * bx) * (cy - dy) - (ay - by) * (cx * dy - cy * dx)) / denom
    if (
        min(ax, bx) - 1e-9 <= px <= max(ax, bx) + 1e-9
        and min(ay, by) - 1e-9 <= py <= max(ay, by) + 1e-9
        and min(cx, dx) - 1e-9 <= px <= max(cx, dx) + 1e-9
        and min(cy, dy) - 1e-9 <= py <= max(cy, dy) + 1e-9
    ):
        return [px, py]
    return None


def closest_point_on_segment(point, a, b):
    px, py = point
    ax, ay = a
    bx, by = b
    vx = bx - ax
    vy = by - ay
    denom = vx * vx + vy * vy
    if not denom:
        return a
    t = max(0, min(1, ((px - ax) * vx + (py - ay) * vy) / denom))
    return [ax + t * vx, ay + t * vy]


def squared_distance(a, b):
    return (a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2


def closest_between_segments(a, b, c, d):
    intersection = segment_intersection(a, b, c, d)
    if intersection:
        return intersection, 0
    candidates = [
        (closest_point_on_segment(a, c, d), a),
        (closest_point_on_segment(b, c, d), b),
        (closest_point_on_segment(c, a, b), c),
        (closest_point_on_segment(d, a, b), d),
    ]
    point, source = min(candidates, key=lambda pair: squared_distance(pair[0], pair[1]))
    return point, squared_distance(point, source)


def estimated_intersection(street_index, address):
    tokens = street_tokens_from_address(address)
    if len(tokens) < 2:
        return None
    first_lines = street_lines_for_token(street_index, tokens[0])
    second_lines = street_lines_for_token(street_index, tokens[1])
    if not first_lines or not second_lines:
        return None
    best = None
    for first in first_lines:
        for second in second_lines:
            for index_a in range(len(first) - 1):
                for index_b in range(len(second) - 1):
                    point, distance = closest_between_segments(
                        first[index_a],
                        first[index_a + 1],
                        second[index_b],
                        second[index_b + 1],
                    )
                    if best is None or distance < best[1]:
                        best = (point, distance)
    if not best or best[1] > 0.00005:
        return None
    point = best[0]
    return {"lon": point[0], "lat": point[1], "source": "estimado_calles"}


def interpolate_line_point(lines, index, total):
    points = [point for line in lines for point in line]
    if not points:
        return None
    if len(points) == 1:
        return {"lon": points[0][0], "lat": points[0][1], "source": "estimado_distribuido"}
    position = (index + 1) / (total + 1)
    segment_count = len(points) - 1
    segment_index = min(segment_count - 1, int(position * segment_count))
    local = (position * segment_count) - segment_index
    a = points[segment_index]
    b = points[segment_index + 1]
    return {
        "lon": a[0] + (b[0] - a[0]) * local,
        "lat": a[1] + (b[1] - a[1]) * local,
        "source": "estimado_distribuido",
    }


def load_cache():
    if CACHE_JSON.exists():
        return json.loads(CACHE_JSON.read_text(encoding="utf-8"))
    return {}


def load_arcgis_cache():
    if ARCGIS_CACHE_JSON.exists():
        return json.loads(ARCGIS_CACHE_JSON.read_text(encoding="utf-8"))
    return {}


def save_cache(cache):
    DATA_DIR.mkdir(exist_ok=True)
    CACHE_JSON.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def save_arcgis_cache(cache):
    DATA_DIR.mkdir(exist_ok=True)
    ARCGIS_CACHE_JSON.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def geocode_one(query):
    params = urllib.parse.urlencode(
        {
            "format": "json",
            "limit": 1,
            "countrycodes": "ar",
            "q": query,
        }
    )
    request = urllib.request.Request(
        f"https://nominatim.openstreetmap.org/search?{params}",
        headers={
            "User-Agent": "municipio-empresa-ciudad/1.0 (local project)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not payload:
        return None
    first = payload[0]
    return {
        "lat": float(first["lat"]),
        "lon": float(first["lon"]),
        "display_name": first.get("display_name", ""),
    }


def geocode_one_arcgis(query):
    params = urllib.parse.urlencode(
        {
            "f": "json",
            "singleLine": query,
            "maxLocations": 1,
            "countryCode": "ARG",
            "outFields": "Match_addr,Addr_type,Score",
        }
    )
    request = urllib.request.Request(
        "https://geocode.arcgis.com/arcgis/rest/services/World/GeocodeServer/findAddressCandidates?"
        + params,
        headers={
            "User-Agent": "municipio-empresa-ciudad/1.0 (local project)",
            "Accept": "application/json",
        },
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        payload = json.loads(response.read().decode("utf-8"))
    candidates = payload.get("candidates", [])
    if not candidates:
        return None
    first = candidates[0]
    score = float(first.get("score") or 0)
    location = first.get("location") or {}
    lat = float(location.get("y"))
    lon = float(location.get("x"))
    if score < 85 or not (-34.35 <= lat <= -33.85 and -59.35 <= lon <= -58.75):
        return None
    return {
        "lat": lat,
        "lon": lon,
        "display_name": first.get("address", ""),
        "score": score,
        "source": "arcgis",
    }


def geocode_records(records, limit=None, max_errors=5):
    cache = load_cache()
    arcgis_cache = load_arcgis_cache()
    done = 0
    errors = 0
    for record in records:
        if record.get("lat") and record.get("lon"):
            continue
        result = None
        for query in build_search_queries(record):
            if query in cache:
                result = cache[query]
                if result:
                    break
                continue

            if limit is not None and done >= limit:
                continue
            print(f"Geocodificando: {query}", flush=True)
            try:
                result = geocode_one(query)
                errors = 0
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                errors += 1
                print(f"Error de red ({errors}/{max_errors}): {exc}", flush=True)
                if errors >= max_errors:
                    print("Se corta la geocodificacion para conservar lo ya resuelto.", flush=True)
                    save_cache(cache)
                    return
                time.sleep(5)
                continue
            cache[query] = result
            save_cache(cache)
            done += 1
            time.sleep(1.1)
            if result:
                break

        if not result:
            for query in build_search_queries(record):
                if query in arcgis_cache:
                    result = arcgis_cache[query]
                    if result:
                        break
                    continue

                if limit is not None and done >= limit:
                    continue
                print(f"Geocodificando respaldo: {query}", flush=True)
                try:
                    result = geocode_one_arcgis(query)
                    errors = 0
                except (urllib.error.URLError, TimeoutError, OSError) as exc:
                    errors += 1
                    print(f"Error de red ({errors}/{max_errors}): {exc}", flush=True)
                    if errors >= max_errors:
                        print("Se corta la geocodificacion para conservar lo ya resuelto.", flush=True)
                        save_cache(cache)
                        save_arcgis_cache(arcgis_cache)
                        return
                    time.sleep(5)
                    continue
                arcgis_cache[query] = result
                save_arcgis_cache(arcgis_cache)
                done += 1
                time.sleep(0.4)
                if result:
                    break

        if result:
            record["lat"] = result["lat"]
            record["lon"] = result["lon"]
            record["geocode_estado"] = "ok"
            record["geocode_nombre"] = result.get("display_name", "")
        else:
            record["geocode_estado"] = "sin_resultado"
    save_cache(cache)
    save_arcgis_cache(arcgis_cache)


def is_unreliable_water_well(record):
    if record.get("rubro") != "POZOS DE AGUA":
        return False

    if not record.get("lat") or not record.get("lon"):
        return True

    geocode_name = strip_accents(clean_text(record.get("geocode_nombre", ""))).lower()
    address = strip_accents(clean_text(record.get("direccion", ""))).lower()

    # Generic city-level geocodes put many wells in the same downtown point.
    if geocode_name in {
        "ciudad, provincia de buenos aires",
        "ciudad, partido de ciudad, buenos aires, 2800, argentina",
    }:
        return True

    # The water wells spreadsheet has numbered pumps on Camino Viejo a Localidad.
    # Public geocoders collapse all of them to Calle Justa Localidad, which is not
    # a reliable well coordinate.
    if "camino viejo a lima" in address and "calle justa lima" in geocode_name:
        return True

    return False


def apply_water_well_estimates(records):
    street_index = load_street_index()
    estimated = []
    unresolved = []
    for record in records:
        if record.get("rubro") != "POZOS DE AGUA" or not is_unreliable_water_well(record):
            continue

        estimate = WATER_WELL_MANUAL_ESTIMATES.get(record.get("numero", ""))
        if not estimate:
            estimate = estimated_intersection(street_index, record.get("direccion", ""))
        if estimate:
            record["lat"] = estimate["lat"]
            record["lon"] = estimate["lon"]
            record["geocode_estado"] = estimate["source"]
            record["geocode_nombre"] = f"Coordenada {estimate['source']} desde direccion: {record.get('direccion', '')}"
            record["observaciones"] = clean_text(
                f"{record.get('observaciones', '')}. Ubicacion estimada, verificar con coordenada municipal."
            )
            estimated.append(record)
        else:
            record["lat"] = None
            record["lon"] = None
            record["geocode_estado"] = "pendiente_verificacion"
            record["geocode_nombre"] = "Sin coordenada confiable; requiere ubicacion municipal"
            unresolved.append(record)

    if estimated:
        print(f"Pozos de agua con coordenada estimada: {len(estimated)}")
        for record in estimated:
            print(f"  - {record.get('nombre')}: {record.get('direccion')} -> {record.get('geocode_estado')}")
    if unresolved:
        print(f"Pozos de agua pendientes de verificacion: {len(unresolved)}")
        for record in unresolved:
            print(f"  - {record.get('nombre')}: {record.get('direccion')}")


def write_output(records):
    apply_water_well_estimates(records)
    records = records + load_extra_places()
    DATA_DIR.mkdir(exist_ok=True)
    rubros = sorted({record["rubro"] for record in records})
    payload = {
        "meta": {
            "total": len(records),
            "con_coordenadas": sum(1 for record in records if record["lat"] and record["lon"]),
            "rubros": rubros,
        },
        "instituciones": records,
    }
    OUTPUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Escrito: {OUTPUT_JSON}")
    print(f"Total: {payload['meta']['total']}")
    print(f"Con coordenadas: {payload['meta']['con_coordenadas']}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--excel", default=str(DEFAULT_EXCEL))
    parser.add_argument("--geocode", action="store_true")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--max-errors", type=int, default=5)
    parser.add_argument("--manual-lines", type=int, default=None)
    args = parser.parse_args()

    records = load_institutions(Path(args.excel))
    records.extend(load_water_wells())
    apply_manual_overrides(records, args.manual_lines)
    apply_cached_geocodes(records)
    if args.geocode:
        geocode_records(records, args.limit, args.max_errors)
    write_output(records)


if __name__ == "__main__":
    main()
