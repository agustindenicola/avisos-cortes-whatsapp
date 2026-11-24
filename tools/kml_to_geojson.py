#!/usr/bin/env python3
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


NS = {"kml": "http://www.opengis.net/kml/2.2"}


def parse_coordinates(text):
    points = []
    for raw in (text or "").split():
        parts = raw.split(",")
        if len(parts) < 2:
            continue
        lon = float(parts[0])
        lat = float(parts[1])
        points.append([lon, lat])
    return points


def polygon_coordinates(polygon):
    rings = []
    outer = polygon.find(".//kml:outerBoundaryIs/kml:LinearRing/kml:coordinates", NS)
    if outer is not None:
        rings.append(parse_coordinates(outer.text))
    for inner in polygon.findall(".//kml:innerBoundaryIs/kml:LinearRing/kml:coordinates", NS):
        rings.append(parse_coordinates(inner.text))
    return [ring for ring in rings if len(ring) >= 4]


def placemark_name(placemark, fallback):
    name = placemark.find("kml:name", NS)
    if name is not None and name.text and name.text.strip():
        return name.text.strip()
    simple = placemark.find(".//kml:SimpleData[@name='etiq']", NS)
    if simple is not None and simple.text and simple.text.strip():
        return simple.text.strip()
    return fallback


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: kml_to_geojson.py input.kml output.geojson")

    input_path = Path(sys.argv[1])
    output_path = Path(sys.argv[2])
    tree = ET.parse(input_path)
    root = tree.getroot()
    features = []

    for index, placemark in enumerate(root.findall(".//kml:Placemark", NS), start=1):
        polygons = []
        for polygon in placemark.findall(".//kml:Polygon", NS):
            rings = polygon_coordinates(polygon)
            if rings:
                polygons.append(rings)
        if not polygons:
            continue
        features.append({
            "type": "Feature",
            "properties": {
                "id": f"manzana-{index}",
                "label": placemark_name(placemark, f"Manzana {index}"),
            },
            "geometry": {
                "type": "MultiPolygon" if len(polygons) > 1 else "Polygon",
                "coordinates": polygons if len(polygons) > 1 else polygons[0],
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
