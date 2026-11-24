#!/usr/bin/env python3
import json
import sys
from pathlib import Path

from shp_to_geojson import dbf_rows, read_polygons, transform_points


def fix_text(value):
    text = str(value or "").strip()
    if "Ã" in text:
        try:
            text = text.encode("latin1").decode("utf-8")
        except UnicodeError:
            pass
    replacements = {
        "Ã‘": "Ñ",
        "Ã\u2018": "Ñ",
        "Ãœ": "Ü",
        "Ã\u0153": "Ü",
        "Ã“": "Ó",
        "Ã”": "Ó",
        "ï¿½": "ó",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text


def label_from_row(row, index):
    raw = fix_text(row.get("Nombre_Bar") or row.get("nombre_bar") or row.get("id") or "")
    if raw == "ESTANCION ESCALADA":
        raw = "ESTACIÓN ESCALADA"
    return raw if raw and set(raw) != {"*"} else f"RENABAP {index}"


def number_from_label(label, row, index):
    if "-" in label:
        return label.split("-", 1)[0].strip()
    return fix_text(row.get("motivo_n_2") or row.get("id_renabap") or index)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: renabap_to_geojson.py input.shp output.geojson")

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    records = read_polygons(input_path)
    rows = dbf_rows(input_path.with_suffix(".dbf"))
    all_points = [point for _, rings in records for ring in rings for point in ring]
    transformed = transform_points(all_points)

    cursor = 0
    features = []
    for record_index, (record_number, rings) in enumerate(records):
        polygons = []
        for ring in rings:
            count = len(ring)
            polygons.append([transformed[cursor:cursor + count]])
            cursor += count
        row = rows[record_index] if record_index < len(rows) else {}
        label = label_from_row(row, record_number)
        number = number_from_label(label, row, record_number)
        features.append({
            "type": "Feature",
            "properties": {
                "id": f"renabap-{record_number}",
                "fid": record_number,
                "label": label,
                "number": str(number),
                "kind": "renabap",
                "color": None,
            },
            "geometry": {
                "type": "MultiPolygon",
                "coordinates": polygons,
            },
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({
        "type": "FeatureCollection",
        "name": input_path.stem,
        "source": str(input_path),
        "features": features,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(features)} features to {output_path}")


if __name__ == "__main__":
    main()
