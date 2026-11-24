#!/usr/bin/env python3
import json
import sqlite3
import struct
import sys
from pathlib import Path


def read_uint(data, offset, endian):
    return struct.unpack_from(endian + "I", data, offset)[0], offset + 4


def read_double(data, offset, endian):
    return struct.unpack_from(endian + "d", data, offset)[0], offset + 8


def dimensions_for(geom_type):
    if 1000 <= geom_type < 2000 or 2000 <= geom_type < 3000:
        return 3
    if 3000 <= geom_type < 4000:
        return 4
    return 2


def base_type(geom_type):
    return geom_type % 1000


def parse_point(data, offset, endian, dimensions):
    x, offset = read_double(data, offset, endian)
    y, offset = read_double(data, offset, endian)
    for _ in range(dimensions - 2):
        _, offset = read_double(data, offset, endian)
    return [x, y], offset


def parse_linestring(data, offset, endian, dimensions):
    count, offset = read_uint(data, offset, endian)
    points = []
    for _ in range(count):
        point, offset = parse_point(data, offset, endian, dimensions)
        points.append(point)
    return points, offset


def parse_polygon_body(data, offset, endian, dimensions):
    ring_count, offset = read_uint(data, offset, endian)
    rings = []
    for _ in range(ring_count):
        ring, offset = parse_linestring(data, offset, endian, dimensions)
        rings.append(ring)
    return rings, offset


def parse_wkb(data, offset=0):
    byte_order = data[offset]
    endian = "<" if byte_order == 1 else ">"
    offset += 1
    geom_type, offset = read_uint(data, offset, endian)
    geometry_type = base_type(geom_type)
    dimensions = dimensions_for(geom_type)

    if geometry_type == 3:
        coordinates, offset = parse_polygon_body(data, offset, endian, dimensions)
        return {"type": "Polygon", "coordinates": coordinates}, offset

    if geometry_type == 6:
        polygon_count, offset = read_uint(data, offset, endian)
        polygons = []
        for _ in range(polygon_count):
            polygon, offset = parse_wkb(data, offset)
            if polygon["type"] != "Polygon":
                raise ValueError(f"Unexpected geometry inside MultiPolygon: {polygon['type']}")
            polygons.append(polygon["coordinates"])
        return {"type": "MultiPolygon", "coordinates": polygons}, offset

    raise ValueError(f"Unsupported WKB geometry type: {geom_type}")


def gpkg_wkb(blob):
    if blob[:2] != b"GP":
        return blob

    flags = blob[3]
    envelope_type = (flags >> 1) & 0b111
    envelope_sizes = {
        0: 0,
        1: 32,
        2: 48,
        3: 48,
        4: 64,
    }
    offset = 8 + envelope_sizes.get(envelope_type, 0)
    return blob[offset:]


def feature_label(row):
    name = row["Name"] or row["nombre"] or str(row["fid"])
    return fix_text(str(name).strip())


def fix_text(value):
    if "Ã" in value:
        try:
            value = value.encode("latin1").decode("utf-8")
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
        value = value.replace(bad, good)
    return value


def main():
    if len(sys.argv) != 4:
        raise SystemExit("Usage: gpkg_to_geojson.py input.gpkg layer output.geojson")

    input_path = Path(sys.argv[1])
    layer = sys.argv[2]
    output_path = Path(sys.argv[3])

    with sqlite3.connect(input_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(f'SELECT fid, geom, Name, nombre FROM "{layer}" ORDER BY fid').fetchall()

    features = []
    for index, row in enumerate(rows):
        geometry, _ = parse_wkb(gpkg_wkb(row["geom"]))
        label = feature_label(row)
        number = label.split("-", 1)[0].strip() if "-" in label else str(row["nombre"] or row["fid"])
        features.append({
            "type": "Feature",
            "properties": {
                "id": f"barrio-{row['fid']}",
                "fid": row["fid"],
                "label": label,
                "number": number,
                "color": None,
            },
            "geometry": geometry,
        })

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({
        "type": "FeatureCollection",
        "name": layer,
        "source": str(input_path),
        "features": features,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Wrote {len(features)} features to {output_path}")


if __name__ == "__main__":
    main()
