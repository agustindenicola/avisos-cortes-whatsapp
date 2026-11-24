#!/usr/bin/env python3
import json
import re
import sys
import unicodedata
from difflib import SequenceMatcher
from pathlib import Path

from build_whatsapp_groups import read_xlsx_rows


MANUAL_WORDS = {"calle", "calles", "perimetro", "perímetro", "entre", "desde", "hasta", "ruta", "camino", "vias", "vías", "km"}
MANUAL_PREFIXES = (
    "calle ",
    "camino ",
    "ruta ",
    "perimetro ",
    "perímetro ",
    "lima desde ",
    "lima entre ",
    "entrada ",
)
ROMAN_RE = re.compile(r"\b(?:I|II|III|IV|V|VI|VII|VIII|IX|X)\b", re.I)


def fix_text(value):
    text = str(value or "").strip()
    replacements = {
        "Ã‘": "Ñ",
        "Ã\u2018": "Ñ",
        "Ãœ": "Ü",
        "Ã\u0153": "Ü",
        "Ã“": "Ó",
        "Ã”": "Ó",
    }
    for bad, good in replacements.items():
        text = text.replace(bad, good)
    return text


def normalize(value):
    text = fix_text(value)
    text = text.replace("RENABAP ZTE", "")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def zone_name(label):
    return re.sub(r"^\s*\d+\s*-\s*", "", str(label or "")).strip()


def split_terms(perimeter):
    terms = []
    for part in re.split(r";|\n", perimeter or ""):
        part = re.sub(r"^(barrio|barrios)\s+", "", part.strip(" ."), flags=re.I)
        if part:
            terms.append(part)
    return terms


def has_manual_words(term):
    words = set(normalize(term).split())
    return bool(words & MANUAL_WORDS)


def clean_match_term(term):
    cleaned = re.sub(r"\([^)]*\)", "", term).strip(" .")
    cleaned = re.sub(r"\bparcial\b", "", cleaned, flags=re.I).strip(" .")
    return cleaned


def is_pure_manual_perimeter(term):
    normalized = normalize(term)
    return any(normalized.startswith(normalize(prefix)) for prefix in MANUAL_PREFIXES)


def roman_values(text):
    return [match.group(0).upper() for match in ROMAN_RE.finditer(text)]


def expanded_terms(term):
    base = clean_match_term(term)
    values = roman_values(base)
    if len(values) <= 1:
        return [base]
    prefix = base[:ROMAN_RE.search(base).start()].strip(" ,")
    return [f"{prefix} {roman}".strip() for roman in values]


def score_match(term, zone):
    target = normalize(term)
    name = normalize(zone["name"])
    label = normalize(zone["label"])
    aliases = [name, label]
    if name.startswith("villa "):
        aliases.append(name.replace("villa ", "", 1))
    if target.startswith("villa "):
        aliases.append(target.replace("villa ", "", 1))
    if target.startswith("estancia "):
        aliases.append(target.replace("estancia ", "", 1))

    best = 0
    for alias in aliases:
        if not target or not alias:
            continue
        if target == alias:
            best = max(best, 1)
        elif target in alias or alias in target:
            best = max(best, 0.94)
        else:
            best = max(best, SequenceMatcher(None, target, alias).ratio())
    return best


def best_match(term, zones):
    scored = [(score_match(term, zone), zone) for zone in zones]
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] < 0.9:
        return None, scored[0][0] if scored else 0
    return scored[0][1], scored[0][0]


def load_zones(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    zones = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        zones.append({
            "id": props["id"],
            "label": props["label"],
            "name": props.get("name") or zone_name(props["label"]),
            "number": str(props.get("number", "")),
            "kind": props.get("sourceKind") or props.get("kind") or "barrio_loteo",
        })
    return zones


def append_unique(items, item):
    if not any(existing["id"] == item["id"] for existing in items):
        items.append(item)


def main():
    if len(sys.argv) != 4:
        raise SystemExit("Usage: build_wsp_zone_mapping.py groups.xlsx zonas_municipales.geojson output.json")

    xlsx_path = Path(sys.argv[1])
    zones_path = Path(sys.argv[2])
    output_path = Path(sys.argv[3])
    rows = read_xlsx_rows(xlsx_path)
    zones = load_zones(zones_path)

    groups = []
    for row in rows[1:]:
        if len(row) < 3:
            continue
        group, perimeter, channel = row[:3]
        matched = []
        review_terms = []
        for term in split_terms(perimeter):
            term_needs_review = has_manual_words(term) or "parcial" in normalize(term)
            term_matches = []
            if not is_pure_manual_perimeter(term):
                for expanded in expanded_terms(term):
                    zone, score = best_match(expanded, zones)
                    if zone:
                        match = {
                            "id": zone["id"],
                            "label": zone["label"],
                            "name": zone["name"],
                            "number": zone["number"],
                            "kind": zone["kind"],
                            "matched_from": term,
                            "score": round(score, 3),
                            "needs_review": term_needs_review,
                        }
                        append_unique(matched, match)
                        term_matches.append(match)
            if term_needs_review or not term_matches:
                review_terms.append({
                    "term": term,
                    "reason": "perimetro/calle/parcial" if term_needs_review else "sin_match",
                    "matches": term_matches,
                })
        groups.append({
            "group": str(group),
            "label": f"Grupo {group}",
            "perimeter": perimeter,
            "whatsapp": channel,
            "barrios": matched,
            "review_terms": review_terms,
            "requires_review": bool(review_terms),
        })

    payload = {
        "source": str(xlsx_path),
        "zones_source": str(zones_path),
        "groups": groups,
    }
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Grupos: {len(groups)}")
    print(f"Requieren revisión: {sum(1 for group in groups if group['requires_review'])}")
    for group in groups:
        print(f"{group['label']}: {len(group['barrios'])} zonas, {len(group['review_terms'])} revisar")


if __name__ == "__main__":
    main()
