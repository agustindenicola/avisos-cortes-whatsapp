#!/usr/bin/env python3
import json
import re
import sys
import unicodedata
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path
from zipfile import ZipFile


NS = {"a": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
MANUAL_WORDS = ("calle", "calles", "perimetro", "perímetro", "entre", "desde", "hasta", "ruta", "camino", "vias", "vías", "km")


def normalize(value):
    text = str(value or "")
    text = text.replace("Ã‘", "Ñ").replace("Ã", "Á").replace("Ã‰", "É").replace("Ã", "Í").replace("Ã“", "Ó").replace("Ãš", "Ú")
    text = unicodedata.normalize("NFD", text)
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def label_name(label):
    return re.sub(r"^\s*\d+\s*-\s*", "", str(label or "")).strip()


def read_xlsx_rows(path):
    with ZipFile(path) as z:
        strings = []
        root = ET.fromstring(z.read("xl/sharedStrings.xml"))
        for si in root.findall("a:si", NS):
            strings.append("".join(t.text or "" for t in si.findall(".//a:t", NS)))
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))

    def value(cell):
        raw = cell.find("a:v", NS)
        if raw is None:
            return ""
        text = raw.text or ""
        return strings[int(text)] if cell.attrib.get("t") == "s" else text

    rows = []
    for row in sheet.findall(".//a:sheetData/a:row", NS):
        rows.append([value(cell) for cell in row.findall("a:c", NS)])
    return rows


def split_terms(perimeter):
    pieces = []
    for part in re.split(r";|\n", perimeter or ""):
        part = re.sub(r"\([^)]*\)", "", part).strip(" .")
        part = re.sub(r"^(barrio|barrios)\s+", "", part, flags=re.I).strip()
        if part:
            pieces.append(part)
    return pieces


def best_match(term, barrios):
    normalized = normalize(term)
    if not normalized or any(word in normalized.split() for word in MANUAL_WORDS):
        return None

    candidates = []
    for barrio in barrios:
        barrio_name = normalize(label_name(barrio["label"]))
        barrio_full = normalize(barrio["label"])
        if normalized == barrio_name or normalized == barrio_full:
            score = 1
        elif normalized in barrio_name or barrio_name in normalized:
            score = 0.92
        else:
            score = SequenceMatcher(None, normalized, barrio_name).ratio()
        candidates.append((score, barrio))
    score, barrio = max(candidates, key=lambda item: item[0])
    return barrio if score >= 0.78 else None


def main():
    if len(sys.argv) != 4:
        raise SystemExit("Usage: build_whatsapp_groups.py groups.xlsx barrios.geojson output.json")

    rows = read_xlsx_rows(Path(sys.argv[1]))
    barrios_data = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    barrios = [
        {
            "id": feature["properties"]["id"],
            "label": feature["properties"]["label"],
        }
        for feature in barrios_data["features"]
    ]

    groups = []
    for row in rows[1:]:
        if len(row) < 3:
            continue
        group, perimeter, channel = row[:3]
        terms = split_terms(perimeter)
        matched = []
        unmatched = []
        requires_manual = not perimeter.strip()
        for term in terms:
            if any(word in normalize(term).split() for word in MANUAL_WORDS):
                requires_manual = True
                unmatched.append(term)
                continue
            match = best_match(term, barrios)
            if match and match not in matched:
                matched.append(match)
            else:
                unmatched.append(term)
        groups.append({
            "group": group,
            "label": f"Grupo {group}",
            "perimeter": perimeter,
            "whatsapp": channel,
            "barrios": matched,
            "unmatched_terms": unmatched,
            "requires_manual_perimeter": requires_manual,
        })

    output = {
        "source": str(Path(sys.argv[1])),
        "barrios_source": str(Path(sys.argv[2])),
        "groups": groups,
    }
    Path(sys.argv[3]).write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(groups)} groups to {sys.argv[3]}")


if __name__ == "__main__":
    main()
