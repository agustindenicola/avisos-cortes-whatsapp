#!/usr/bin/env python3
import json
import sys
from copy import deepcopy
from pathlib import Path


def main():
    if len(sys.argv) != 3:
        raise SystemExit("Usage: apply_zone_aliases.py zones.geojson aliases.json")

    zones_path = Path(sys.argv[1])
    aliases_path = Path(sys.argv[2])
    data = json.loads(zones_path.read_text(encoding="utf-8"))
    aliases = json.loads(aliases_path.read_text(encoding="utf-8"))
    features = data.get("features", [])
    by_id = {feature.get("properties", {}).get("id"): feature for feature in features}
    existing_ids = {feature.get("properties", {}).get("id") for feature in features}

    added = 0
    for alias_info in aliases:
        if alias_info["id"] in existing_ids:
            continue
        source = by_id.get(alias_info["source_id"])
        if not source:
            print(f"Alias omitido, no existe source_id={alias_info['source_id']}")
            continue
        alias = deepcopy(source)
        alias["properties"] = {
            **alias.get("properties", {}),
            "id": alias_info["id"],
            "label": alias_info["label"],
            "number": alias_info["number"],
            "aliasOf": alias_info["source_id"],
            "aliasNote": alias_info.get("alias_note", ""),
        }
        features.append(alias)
        existing_ids.add(alias_info["id"])
        added += 1

    zones_path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Aliases agregados: {added}")


if __name__ == "__main__":
    main()
