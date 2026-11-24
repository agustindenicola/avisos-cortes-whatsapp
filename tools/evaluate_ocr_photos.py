#!/usr/bin/env python3
import argparse
import csv
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import app  # noqa: E402


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}


def expected_from_name(path):
    normalized = app.normalize_text(path.stem)
    expected_type = ""
    for key in ("restablecido", "programado", "imprevisto"):
        if key in normalized:
            expected_type = key
            break

    expected_group = ""
    group_match = re.search(r"grupo[_\s-]+([a-z])(?:[_\s-]*[a-z])?", normalized)
    if group_match:
        expected_group = group_match.group(1).upper()
    range_match = re.search(r"grupo[_\s-]+([a-z])[_\s-]*([a-z])", normalized)
    if range_match:
        expected_group = f"{range_match.group(1).upper()}-{range_match.group(2).upper()}"

    return expected_type, expected_group


def load_known_groups():
    groups = set()
    try:
        data = app.json.loads((ROOT / "data" / "grupos_wsp_zonas.json").read_text(encoding="utf-8"))
    except Exception:
        return groups
    for item in data.get("groups", []):
        key = app.group_key(item.get("group", ""))
        if key:
            groups.add(key)
    return groups


def map_status(expected_group, known_groups):
    if not expected_group:
        return "sin_grupo_esperado", False
    if expected_group in known_groups:
        return "grupo_del_mapa", True
    if "-" in expected_group:
        parts = [part for part in expected_group.split("-") if part]
        if parts and all(part in known_groups for part in parts):
            return "combina_grupos_del_mapa", True
    return "fuera_del_mapa", False


def analyze_photo(path):
    text = app.ocr_image_text(path)
    group = app.infer_group(text)
    if not group and "grupo" in app.normalize_text(text):
        group = app.ocr_group_letter_from_plate(path)
    if not group:
        group = app.infer_group_from_zone_text(text)
    notice_type = app.infer_outage_type(text)
    if notice_type == "sin_clasificar" and any(
        word in app.normalize_text(text) for word in ("corte", "afectada", "reposicion")
    ):
        notice_type = "imprevisto"
    expected_type, expected_group = expected_from_name(path)
    status, trusted = map_status(expected_group, KNOWN_GROUPS)
    return {
        "file": str(path.relative_to(ROOT)),
        "map_status": status,
        "trusted_for_training": str(trusted),
        "expected_type": expected_type,
        "detected_type": notice_type,
        "type_ok": "" if not expected_type else str(expected_type == notice_type),
        "expected_group": expected_group,
        "detected_group": group,
        "group_ok": "" if not expected_group else str(expected_group == group),
        "ocr_preview": " ".join(text.split())[:260],
    }


def iter_images(paths):
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS:
            yield path
        elif path.is_dir():
            for item in sorted(path.rglob("*")):
                if item.is_file() and item.suffix.lower() in IMAGE_EXTENSIONS:
                    yield item


def write_markdown(rows, output_path):
    total = len(rows)
    trusted_rows = [row for row in rows if row["trusted_for_training"] == "True"]
    typed = [row for row in rows if row["expected_type"]]
    grouped = [row for row in rows if row["expected_group"]]
    trusted_typed = [row for row in trusted_rows if row["expected_type"]]
    trusted_grouped = [row for row in trusted_rows if row["expected_group"]]
    type_ok = sum(1 for row in typed if row["type_ok"] == "True")
    group_ok = sum(1 for row in grouped if row["group_ok"] == "True")
    trusted_type_ok = sum(1 for row in trusted_typed if row["type_ok"] == "True")
    trusted_group_ok = sum(1 for row in trusted_grouped if row["group_ok"] == "True")
    lines = [
        "# Reporte OCR de fotos",
        "",
        f"- Fotos analizadas: {total}",
        f"- Fotos confiables para entrenar: {len(trusted_rows)}",
        f"- Tipo correcto: {type_ok}/{len(typed)}" if typed else "- Tipo correcto: sin esperado",
        f"- Grupo correcto: {group_ok}/{len(grouped)}" if grouped else "- Grupo correcto: sin esperado",
        f"- Tipo correcto en confiables: {trusted_type_ok}/{len(trusted_typed)}" if trusted_typed else "- Tipo correcto en confiables: sin esperado",
        f"- Grupo correcto en confiables: {trusted_group_ok}/{len(trusted_grouped)}" if trusted_grouped else "- Grupo correcto en confiables: sin esperado",
        "",
        "| Foto | Mapa | Entrena | Tipo esperado | Tipo detectado | Grupo esperado | Grupo detectado | OCR |",
        "|---|---|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        lines.append(
            "| {file} | {map_status} | {trusted_for_training} | {expected_type} | {detected_type} | {expected_group} | {detected_group} | {ocr_preview} |".format(
                **{key: str(value).replace("|", "/") for key, value in row.items()}
            )
        )
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description="Evalua lectura OCR de fotos de cortes/restablecimientos.")
    parser.add_argument("paths", nargs="*", default=["data/fotos_referencia/validadas"])
    parser.add_argument("--csv", default="data/ocr_training_report.csv")
    parser.add_argument("--md", default="data/ocr_training_report.md")
    args = parser.parse_args()

    rows = [analyze_photo(path) for path in iter_images(args.paths)]
    csv_path = ROOT / args.csv
    md_path = ROOT / args.md
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if rows:
        with csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    else:
        csv_path.write_text("", encoding="utf-8")
    write_markdown(rows, md_path)
    print(f"Fotos analizadas: {len(rows)}")
    print(f"CSV: {csv_path}")
    print(f"Markdown: {md_path}")


KNOWN_GROUPS = load_known_groups()


if __name__ == "__main__":
    main()
