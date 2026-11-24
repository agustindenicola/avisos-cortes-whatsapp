#!/usr/bin/env python3
import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CIUDAD_BOUNDS = {
    "min_lon": -60.0,
    "max_lon": -58.0,
    "min_lat": -35.0,
    "max_lat": -33.0,
}


REQUIRED_FILES = [
    DATA / "indec_personas_radios_2022_ciudad.gpkg",
    DATA / "poblacion_por_zona_2022.json",
    DATA / "zonas_municipales.geojson",
    DATA / "grupos_wsp_zonas.json",
    DATA / "grupos_whatsapp_manual.geojson",
    DATA / "instituciones.json",
    ROOT / "mapa.html",
    ROOT / "app.py",
]


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


def group_key(value):
    return str(value or "").upper().replace("GRUPO", "").strip()


def iter_coordinates(value):
    if isinstance(value, list) and len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        yield float(value[0]), float(value[1])
        return
    if isinstance(value, list):
        for item in value:
            yield from iter_coordinates(item)


def geometry_bounds(feature):
    coords = list(iter_coordinates((feature.get("geometry") or {}).get("coordinates", [])))
    if not coords:
        return None
    lons = [item[0] for item in coords]
    lats = [item[1] for item in coords]
    return min(lons), max(lons), min(lats), max(lats)


def bounds_inside_ciudad(bounds):
    if not bounds:
        return False
    min_lon, max_lon, min_lat, max_lat = bounds
    return (
        CIUDAD_BOUNDS["min_lon"] <= min_lon <= max_lon <= CIUDAD_BOUNDS["max_lon"]
        and CIUDAD_BOUNDS["min_lat"] <= min_lat <= max_lat <= CIUDAD_BOUNDS["max_lat"]
    )


def check_required_files(errors):
    for path in REQUIRED_FILES:
        if not path.exists():
            errors.append(f"Falta archivo requerido: {path.relative_to(ROOT)}")


def source_totals_from_gpkg(errors):
    path = DATA / "indec_personas_radios_2022_ciudad.gpkg"
    if not path.exists():
        return None
    try:
        with sqlite3.connect(path) as conn:
            row = conn.execute(
                """
                select
                    count(*) as radios,
                    round(sum(Radios_poblacion_total)) as population,
                    round(sum(Radios_hogares_total)) as households
                from features
                """
            ).fetchone()
    except sqlite3.Error as exc:
        errors.append(f"No se pudo leer GPKP INDEC: {exc}")
        return None
    return {"radios": int(row[0] or 0), "population": int(row[1] or 0), "households": int(row[2] or 0)}


def check_population(errors, warnings):
    population_path = DATA / "poblacion_por_zona_2022.json"
    if not population_path.exists():
        return {}
    payload = load_json(population_path)
    meta = payload.get("meta", {})
    source = source_totals_from_gpkg(errors)
    if source:
        if int(meta.get("total_radios") or 0) != source["radios"]:
            errors.append(f"INDEC total_radios no coincide: json={meta.get('total_radios')} gpkg={source['radios']}")
        if int(meta.get("total_population_source") or 0) != source["population"]:
            errors.append(
                f"INDEC poblacion fuente no coincide: json={meta.get('total_population_source')} gpkg={source['population']}"
            )
        if int(meta.get("total_households_source") or 0) != source["households"]:
            errors.append(f"INDEC hogares fuente no coincide: json={meta.get('total_households_source')} gpkg={source['households']}")

    groups_data = load_json(DATA / "grupos_wsp_zonas.json")
    expected_groups = {group_key(item.get("group")) for item in groups_data.get("groups", []) if group_key(item.get("group"))}
    population_groups = {group_key(item.get("id") or item.get("group")) for item in payload.get("groups", [])}
    missing_population = sorted(expected_groups - population_groups)
    if missing_population:
        warnings.append("Grupos WSP sin estimacion INDEC: " + ", ".join(missing_population))

    empty_groups = sorted(group_key(item.get("id")) for item in payload.get("groups", []) if not int(item.get("population") or 0))
    if empty_groups:
        warnings.append("Grupos WSP con poblacion cero: " + ", ".join(empty_groups))

    return {
        "population": int(meta.get("total_population_source") or 0),
        "households": int(meta.get("total_households_source") or 0),
        "radios": int(meta.get("total_radios") or 0),
        "groups": len(payload.get("groups", [])),
        "barrios": len(payload.get("barrios", [])),
        "group_unmatched_radios": int(meta.get("group_unmatched_radios") or 0),
        "barrio_unmatched_radios": int(meta.get("barrio_unmatched_radios") or 0),
    }


def check_geometries(errors, warnings):
    path = DATA / "zonas_municipales.geojson"
    if not path.exists():
        return {}
    payload = load_json(path)
    out_of_bounds = []
    empty = []
    for feature in payload.get("features", []):
        bounds = geometry_bounds(feature)
        label = feature.get("properties", {}).get("label") or feature.get("properties", {}).get("id")
        if not bounds:
            empty.append(label)
            continue
        if not bounds_inside_ciudad(bounds):
            out_of_bounds.append(label)
    if empty:
        errors.append(f"Zonas sin geometria util: {len(empty)}")
    if out_of_bounds:
        errors.append(f"Zonas fuera del area esperada de Ciudad: {len(out_of_bounds)}")
        warnings.append("Primeras zonas fuera de rango: " + ", ".join(out_of_bounds[:8]))
    omitted = payload.get("omitted", [])
    if omitted:
        warnings.append(f"Zonas omitidas por build_municipal_zones.py: {len(omitted)}")
    return {
        "zones": len(payload.get("features", [])),
        "missing": len(payload.get("missing", [])),
        "omitted": len(omitted),
        "out_of_bounds": len(out_of_bounds),
    }


def check_frontend_backend(errors):
    mapa = (ROOT / "mapa.html").read_text(encoding="utf-8") if (ROOT / "mapa.html").exists() else ""
    app = (ROOT / "app.py").read_text(encoding="utf-8") if (ROOT / "app.py").exists() else ""
    if "data/poblacion_por_zona_2022.json" not in mapa:
        errors.append("mapa.html no carga data/poblacion_por_zona_2022.json")
    if "/data/poblacion_por_zona_2022.json" not in app:
        errors.append("app.py no sirve data/poblacion_por_zona_2022.json")
    if "populationForGroup" not in mapa or "populationForZone" not in mapa:
        errors.append("mapa.html no expone helpers de poblacion por grupo/zona")
    if "population_for_group" not in app:
        errors.append("app.py no adjunta poblacion a eventos del backend")


def main():
    errors = []
    warnings = []
    check_required_files(errors)
    population_summary = check_population(errors, warnings)
    geometry_summary = check_geometries(errors, warnings)
    check_frontend_backend(errors)

    print("Validacion de integracion Municipio Empresa")
    print("")
    print("INDEC:")
    print(f"- radios fuente: {population_summary.get('radios', 0)}")
    print(f"- poblacion fuente: {population_summary.get('population', 0):,}".replace(",", "."))
    print(f"- hogares fuente: {population_summary.get('households', 0):,}".replace(",", "."))
    print(f"- grupos WSP con estimacion: {population_summary.get('groups', 0)}")
    print(f"- barrios con estimacion: {population_summary.get('barrios', 0)}")
    print(f"- radios sin match WSP directo: {population_summary.get('group_unmatched_radios', 0)}")
    print(f"- radios sin match barrios: {population_summary.get('barrio_unmatched_radios', 0)}")
    print("")
    print("Mapa:")
    print(f"- zonas municipales operativas: {geometry_summary.get('zones', 0)}")
    print(f"- zonas faltantes desde leyenda PDF: {geometry_summary.get('missing', 0)}")
    print(f"- zonas omitidas: {geometry_summary.get('omitted', 0)}")
    print(f"- zonas fuera de rango: {geometry_summary.get('out_of_bounds', 0)}")

    if warnings:
        print("")
        print("Advertencias:")
        for warning in warnings:
            print(f"- {warning}")

    if errors:
        print("")
        print("Errores:")
        for error in errors:
            print(f"- {error}")
        return 1

    print("")
    print("OK: integracion consistente.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
