#!/usr/bin/env python3
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path


STREET_STOPWORDS = {
    "perimetro", "comprendido", "entre", "las", "los", "calle", "calles",
    "av", "avenida", "desde", "hasta", "y", "sur", "norte", "su", "culminacion",
}
STREET_PREFIXES = ("avenida ", "av ", "general ", "pasaje ", "calle ")


def normalize(value):
    text = str(value or "")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def street_name(feature):
    return feature.get("properties", {}).get("name", "")


def named_streets(data):
    streets = []
    for feature in data.get("features", []):
        if feature.get("geometry", {}).get("type") != "LineString":
            continue
        name = street_name(feature)
        if not name:
            continue
        streets.append({
            "name": name,
            "normalized": normalize(name),
            "id": feature.get("properties", {}).get("@id", ""),
            "geometry": feature.get("geometry"),
        })
    return streets


def candidate_phrases(text):
    raw = re.sub(r"\([^)]*\)", " ", text)
    raw = raw.replace("/", ";")
    pieces = re.split(r";|,|\sy\s|\bentre\b|\bdesde\b|\bhasta\b|\bpor\b", raw, flags=re.I)
    phrases = []
    for piece in pieces:
        words = [word for word in normalize(piece).split() if word not in STREET_STOPWORDS]
        if not words:
            continue
        phrase = " ".join(words)
        if len(phrase) >= 3:
            phrases.append(phrase)
    return phrases


def best_street_matches(phrase, streets):
    scored = []
    for street in streets:
        name = street["normalized"]
        if not name:
            continue
        aliases = {name}
        for prefix in STREET_PREFIXES:
            if name.startswith(prefix):
                aliases.add(name.removeprefix(prefix))
        if phrase in aliases:
            score = 1
        elif any(phrase == alias or phrase in alias.split() for alias in aliases):
            score = 0.94
        else:
            score = max(SequenceMatcher(None, phrase, alias).ratio() for alias in aliases)
        if score >= 0.78:
            scored.append((score, street))
    scored.sort(key=lambda item: item[0], reverse=True)
    return scored[:5]


def main():
    if len(sys.argv) != 4:
        raise SystemExit("Usage: match_wsp_streets.py grupos_wsp_zonas.json calles.geojson output.json")

    groups_path = Path(sys.argv[1])
    streets_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])
    groups = json.loads(groups_path.read_text(encoding="utf-8"))
    streets = named_streets(json.loads(streets_path.read_text(encoding="utf-8")))

    output = []
    for group in groups.get("groups", []):
        terms = []
        for review in group.get("review_terms", []):
            phrases = candidate_phrases(review["term"])
            matches = []
            for phrase in phrases:
                matches.append({
                    "phrase": phrase,
                    "streets": [
                        {"name": street["name"], "id": street["id"], "score": round(score, 3)}
                        for score, street in best_street_matches(phrase, streets)
                    ],
                })
            if matches:
                terms.append({"term": review["term"], "matches": matches})
        output.append({
            "group": group["group"],
            "label": group["label"],
            "terms": terms,
        })

    output_path.write_text(json.dumps({
        "source_groups": str(groups_path),
        "source_streets": str(streets_path),
        "groups": output,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    for group in output:
        count = sum(len(match["streets"]) for term in group["terms"] for match in term["matches"])
        print(f"{group['label']}: {count} coincidencias de calles")


if __name__ == "__main__":
    main()
