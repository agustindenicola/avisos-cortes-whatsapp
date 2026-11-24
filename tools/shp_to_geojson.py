#!/usr/bin/env python3
import json
import struct
import subprocess
import sys
from pathlib import Path


SOURCE_PROJ = [
    "+proj=tmerc",
    "+lat_0=-90",
    "+lon_0=-60",
    "+k=1",
    "+x_0=5500000",
    "+y_0=0",
    "+ellps=WGS84",
    "+units=m",
]
TARGET_PROJ = ["+proj=longlat", "+datum=WGS84"]


def dbf_rows(path):
    if not path.exists():
        return []
    data = path.read_bytes()
    row_count = struct.unpack("<I", data[4:8])[0]
    header_len = struct.unpack("<H", data[8:10])[0]
    record_len = struct.unpack("<H", data[10:12])[0]
    fields = []
    offset = 32
    while offset < header_len - 1:
        chunk = data[offset:offset + 32]
        if chunk[0] == 0x0D:
            break
        name = chunk[:11].split(b"\x00", 1)[0].decode("latin1").strip()
        length = chunk[16]
        fields.append((name, length))
        offset += 32

    rows = []
    pos = header_len
    for _ in range(row_count):
        record = data[pos:pos + record_len]
        pos += record_len
        if not record or record[:1] == b"*":
            continue
        values = {}
        cursor = 1
        for name, length in fields:
            raw = record[cursor:cursor + length]
            cursor += length
            values[name] = raw.decode("latin1", errors="replace").strip()
        rows.append(values)
    return rows


def read_polygons(path):
    data = path.read_bytes()
    pos = 100
    records = []
    while pos < len(data):
        record_number, content_words = struct.unpack(">2i", data[pos:pos + 8])
        pos += 8
        content = data[pos:pos + content_words * 2]
        pos += content_words * 2
        shape_type = struct.unpack("<i", content[:4])[0]
        if shape_type == 0:
            records.append((record_number, []))
            continue
        if shape_type != 5:
            raise ValueError(f"Unsupported shapefile shape type: {shape_type}")

        part_count, point_count = struct.unpack("<2i", content[36:44])
        part_offsets = list(struct.unpack(f"<{part_count}i", content[44:44 + part_count * 4]))
        points_start = 44 + part_count * 4
        points = [
            struct.unpack("<2d", content[points_start + index * 16:points_start + index * 16 + 16])
            for index in range(point_count)
        ]
        rings = []
        for index, start in enumerate(part_offsets):
            end = part_offsets[index + 1] if index + 1 < len(part_offsets) else point_count
            rings.append(points[start:end])
        records.append((record_number, rings))
    return records


def transform_points(points):
    if not points:
        return []
    payload = "".join(f"{x} {y}\n" for x, y in points)
    result = subprocess.run(
        ["cs2cs", "-f", "%.12f", *SOURCE_PROJ, "+to", *TARGET_PROJ],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
    )
    transformed = []
    for line in result.stdout.splitlines():
        parts = line.split()
        transformed.append([float(parts[0]), float(parts[1])])
    return transformed


def clean_label(value):
    text = str(value or "").strip()
    return "" if not text or set(text) == {"*"} else text


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: shp_to_geojson.py input.shp output.geojson")

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
        label = clean_label(row.get("etiq")) or clean_label(row.get("id")) or f"Manzana {record_number}"
        features.append({
            "type": "Feature",
            "properties": {
                "id": f"manzana-{record_number}",
                "label": str(label),
                **{key: value for key, value in row.items() if key not in {"id", "etiq"}},
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
