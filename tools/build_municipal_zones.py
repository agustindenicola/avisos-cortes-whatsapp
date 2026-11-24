#!/usr/bin/env python3
import json
import re
import unicodedata
from copy import deepcopy
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PDF_DATA = ROOT / "data" / "plano_pdf" / "zonas_extraidas_preliminar.json"
SOURCE_GEOJSONS = [
    ROOT / "data" / "barrios_2026.geojson",
    ROOT / "data" / "renabap_zte.geojson",
]
OUTPUT = ROOT / "data" / "zonas_municipales.geojson"
CIUDAD_BOUNDS = {
    "min_lon": -60.0,
    "max_lon": -58.0,
    "min_lat": -35.0,
    "max_lat": -33.0,
}


def normalize(value):
    text = str(value or "")
    if "-" in text:
        text = text.split("-", 1)[-1]
    text = text.replace("RENABAP ZTE", "").strip()
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def slug(value):
    return re.sub(r"[^a-z0-9]+", "-", normalize(value)).strip("-") or "zona"


def feature_name(feature):
    return normalize(feature.get("properties", {}).get("label", ""))


def feature_number(feature):
    return str(feature.get("properties", {}).get("number", "")).strip()


def iter_coordinates(value):
    if isinstance(value, list) and len(value) >= 2 and all(isinstance(item, (int, float)) for item in value[:2]):
        yield float(value[0]), float(value[1])
        return
    if isinstance(value, list):
        for item in value:
            yield from iter_coordinates(item)


def geometry_in_ciudad_bounds(geometry):
    coords = list(iter_coordinates((geometry or {}).get("coordinates", [])))
    if not coords:
        return False
    return all(
        CIUDAD_BOUNDS["min_lon"] <= lon <= CIUDAD_BOUNDS["max_lon"]
        and CIUDAD_BOUNDS["min_lat"] <= lat <= CIUDAD_BOUNDS["max_lat"]
        for lon, lat in coords
    )


def number_distance(feature, row):
    try:
        return abs(int(feature_number(feature)) - int(row["number"]))
    except (TypeError, ValueError):
        return 9999


def load_source_features():
    features = []
    for path in SOURCE_GEOJSONS:
        data = json.loads(path.read_text(encoding="utf-8"))
        for feature in data.get("features", []):
            copied = deepcopy(feature)
            copied["_source_file"] = path.name
            features.append(copied)
    return features


def build_indexes(features):
    by_pair = {}
    by_name = {}
    for feature in features:
        name = feature_name(feature)
        number = feature_number(feature)
        if name:
            by_name.setdefault(name, []).append(feature)
        if name and number:
            by_pair.setdefault((number, name), []).append(feature)
    return by_pair, by_name


def match_feature(row, by_pair, by_name):
    name = normalize(row["name"])
    number = str(row["number"])
    pair_matches = by_pair.get((number, name), [])
    if pair_matches:
        return pair_matches[0], "number_name"
    name_matches = by_name.get(name, [])
    if len(name_matches) == 1:
        return name_matches[0], "name"
    if name_matches:
        return sorted(name_matches, key=lambda feature: number_distance(feature, row))[0], "nearest_number_name"
    return None, "missing"


def main():
    pdf = json.loads(PDF_DATA.read_text(encoding="utf-8"))
    source_features = load_source_features()
    by_pair, by_name = build_indexes(source_features)

    output_features = []
    missing = []
    omitted = []
    used_ids = set()
    for page in ("1", "2", "3"):
        for row in pdf.get("legend", {}).get(page, []):
            source, match_method = match_feature(row, by_pair, by_name)
            if not source:
                missing.append({"page": page, **row})
                continue
            if not geometry_in_ciudad_bounds(source.get("geometry")):
                omitted.append({
                    "page": page,
                    **row,
                    "reason": "geometria_fuera_de_ciudad",
                    "sourceFile": source.get("_source_file", ""),
                    "sourceLabel": source.get("properties", {}).get("label", ""),
                })
                continue
            feature = deepcopy(source)
            props = feature.get("properties", {})
            canonical_id = f"zona-p{page}-{row['number']}-{slug(row['name'])}"
            if canonical_id in used_ids:
                canonical_id = f"{canonical_id}-{len(used_ids) + 1}"
            used_ids.add(canonical_id)
            feature["properties"] = {
                **props,
                "id": canonical_id,
                "label": f"{row['number']}-{row['name']}",
                "number": str(row["number"]),
                "name": row["name"],
                "pdfPage": int(page),
                "pdfNumber": row["number"],
                "pdfName": row["name"],
                "sourceZoneId": props.get("id", ""),
                "sourceLabel": props.get("label", ""),
                "sourceKind": props.get("kind", "barrio_loteo"),
                "matchMethod": match_method,
                "sourceFile": source.get("_source_file", ""),
            }
            output_features.append(feature)

    OUTPUT.write_text(json.dumps({
        "type": "FeatureCollection",
        "name": "zonas_municipales",
        "source": "PDF municipal validado + capas GIS barrios/RENABAP",
        "features": output_features,
        "missing": missing,
        "omitted": omitted,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Zonas municipales: {len(output_features)}")
    print(f"Faltantes: {len(missing)}")
    print(f"Omitidas: {len(omitted)}")
    if missing:
        for item in missing:
            print(f"p{item['page']} {item['number']} {item['name']}")
    if omitted:
        for item in omitted:
            print(f"omitida p{item['page']} {item['number']} {item['name']}: {item['reason']} ({item['sourceFile']})")


if __name__ == "__main__":
    main()
