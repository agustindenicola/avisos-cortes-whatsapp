#!/usr/bin/env python3
import json
import sys
from pathlib import Path


def main():
    if len(sys.argv) != 4:
        raise SystemExit("Usage: build_wsp_street_lines.py grupos_wsp_calles.json calles.geojson output.geojson")

    groups_path = Path(sys.argv[1])
    streets_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])
    groups = json.loads(groups_path.read_text(encoding="utf-8"))
    streets = json.loads(streets_path.read_text(encoding="utf-8"))
    street_by_id = {
        feature.get("properties", {}).get("@id"): feature
        for feature in streets.get("features", [])
        if feature.get("properties", {}).get("@id")
    }

    features = []
    seen = set()
    for group in groups.get("groups", []):
        for term in group.get("terms", []):
            for match in term.get("matches", []):
                for street in match.get("streets", []):
                    source = street_by_id.get(street.get("id"))
                    if not source:
                        continue
                    key = (group["group"], term["term"], street["id"])
                    if key in seen:
                        continue
                    seen.add(key)
                    features.append({
                        "type": "Feature",
                        "properties": {
                            "group": group["group"],
                            "label": group["label"],
                            "term": term["term"],
                            "phrase": match["phrase"],
                            "street_id": street["id"],
                            "street_name": street["name"],
                            "score": street["score"],
                        },
                        "geometry": source.get("geometry"),
                    })

    output_path.write_text(json.dumps({
        "type": "FeatureCollection",
        "name": "grupos_wsp_calles_detectadas",
        "features": features,
    }, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Calles detectadas exportadas: {len(features)}")


if __name__ == "__main__":
    main()
