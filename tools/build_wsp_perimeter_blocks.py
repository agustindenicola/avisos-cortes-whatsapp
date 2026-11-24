#!/usr/bin/env python3
import json
import sys
from pathlib import Path


def cross(origin, a, b):
    return (a[0] - origin[0]) * (b[1] - origin[1]) - (a[1] - origin[1]) * (b[0] - origin[0])


def convex_hull(points):
    unique = sorted({(round(point[0], 7), round(point[1], 7)) for point in points})
    if len(unique) < 3:
        return []
    lower = []
    for point in unique:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], point) <= 0:
            lower.pop()
        lower.append(point)
    upper = []
    for point in reversed(unique):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], point) <= 0:
            upper.pop()
        upper.append(point)
    hull = lower[:-1] + upper[:-1]
    if hull and hull[0] != hull[-1]:
        hull.append(hull[0])
    return [[lon, lat] for lon, lat in hull]


def load_streets(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return {
        feature.get("properties", {}).get("@id"): feature
        for feature in data.get("features", [])
        if feature.get("properties", {}).get("@id")
    }


def main():
    if len(sys.argv) != 4:
        raise SystemExit("Usage: build_wsp_perimeter_blocks.py grupos_wsp_calles.json calles.geojson output.geojson")

    groups_path = Path(sys.argv[1])
    streets_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])
    groups = json.loads(groups_path.read_text(encoding="utf-8"))
    streets = load_streets(streets_path)

    features = []
    for group in groups.get("groups", []):
        for term in group.get("terms", []):
            street_ids = []
            phrases = []
            for match in term.get("matches", []):
                if not match.get("streets"):
                    continue
                phrases.append(match["phrase"])
                for street in match["streets"]:
                    if street.get("id"):
                        street_ids.append(street["id"])
            if len(set(phrases)) < 3:
                continue
            points = []
            names = []
            for street_id in dict.fromkeys(street_ids):
                street = streets.get(street_id)
                if not street:
                    continue
                points.extend(street.get("geometry", {}).get("coordinates", []))
                name = street.get("properties", {}).get("name")
                if name and name not in names:
                    names.append(name)
            hull = convex_hull(points)
            if len(hull) < 4:
                continue
            features.append({
                "type": "Feature",
                "properties": {
                    "group": group["group"],
                    "label": group["label"],
                    "term": term["term"],
                    "streets": names,
                    "method": "convex_hull_from_detected_streets",
                    "status": "draft_review_required",
                },
                "geometry": {
                    "type": "Polygon",
                    "coordinates": [hull],
                },
            })

    output_path.write_text(json.dumps({
        "type": "FeatureCollection",
        "name": "grupos_wsp_perimetros_borrador",
        "features": features,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Perímetros borrador: {len(features)}")
    for feature in features:
        props = feature["properties"]
        print(f"{props['label']}: {props['term']} ({len(props['streets'])} calles)")


if __name__ == "__main__":
    main()
