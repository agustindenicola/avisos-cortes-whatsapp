#!/usr/bin/env python3
import argparse
import json
import re
import subprocess
from pathlib import Path
from xml.etree import ElementTree as ET


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PDF = Path("/home/agustin/BARRIOS_LOTEOS_COMPLETO_COLOR.pdf")
OUTPUT_DIR = ROOT / "data" / "plano_pdf"


def run(command):
    subprocess.run(command, check=True)


def ensure_svg_pages(pdf_path, output_dir):
    output_dir.mkdir(parents=True, exist_ok=True)
    for page in (1, 2, 3):
        page_pdf = output_dir / f"page-{page}.pdf"
        page_svg = output_dir / f"page-{page}.svg"
        page_bbox = output_dir / f"page-{page}-bbox.html"
        if not page_pdf.exists():
            run(["pdfseparate", "-f", str(page), "-l", str(page), str(pdf_path), str(page_pdf)])
        if not page_svg.exists():
            run(["pdftocairo", "-svg", str(page_pdf), str(page_svg)])
        if not page_bbox.exists():
            run(["pdftotext", "-bbox-layout", "-f", str(page), "-l", str(page), str(pdf_path), str(page_bbox)])


def rgb_values(style):
    match = re.search(r"fill:rgb\(([^)]*)\)", style)
    if not match:
        return None
    return tuple(float(part.strip().rstrip("%")) for part in match.group(1).split(","))


def is_color(rgb):
    return rgb and max(rgb) - min(rgb) >= 8


def matrix_transform(tag):
    match = re.search(r'transform="matrix\(([^)]*)\)"', tag)
    if not match:
        return 1, 1, 0, 0
    values = [float(value) for value in re.split(r"[ ,]+", match.group(1).strip()) if value]
    if len(values) != 6:
        return 1, 1, 0, 0
    a, _b, _c, d, e, f = values
    return a, d, e, f


def path_bounds(tag):
    match = re.search(r'\sd="([^"]+)"', tag)
    if not match:
        return None
    numbers = [float(value) for value in re.findall(r"-?\d+(?:\.\d+)?", match.group(1))]
    if len(numbers) < 4:
        return None
    points = list(zip(numbers[0::2], numbers[1::2]))
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    sx, sy, tx, ty = matrix_transform(tag)
    x1, x2 = min(xs) * sx + tx, max(xs) * sx + tx
    y1, y2 = min(ys) * sy + ty, max(ys) * sy + ty
    return {
        "x": round(x1, 3),
        "y": round(y1, 3),
        "width": round(x2 - x1, 3),
        "height": round(y2 - y1, 3),
        "area": round((x2 - x1) * (y2 - y1), 3),
    }


def extract_paths(svg_path):
    text = svg_path.read_text(errors="ignore")
    paths = re.findall(r"<path\b[^>]*>", text)
    candidates = []
    for index, tag in enumerate(paths):
        rgb = rgb_values(tag)
        if not is_color(rgb) or "fill-opacity:1" not in tag:
            continue
        bounds = path_bounds(tag)
        if not bounds or bounds["area"] < 40:
            continue
        # Bottom strip contains legend swatches/logos; the interactive map is above it.
        if bounds["y"] >= 2050:
            continue
        d_match = re.search(r'\sd="([^"]+)"', tag)
        transform_match = re.search(r'transform="([^"]+)"', tag)
        style_match = re.search(r'style="([^"]+)"', tag)
        candidates.append(
            {
                "source_path_index": index,
                "fill_rgb_percent": [round(value, 6) for value in rgb],
                "bounds": bounds,
                "style": style_match.group(1) if style_match else "",
                "transform": transform_match.group(1) if transform_match else "",
                "path_d": d_match.group(1) if d_match else "",
            }
        )
    return candidates


def write_clean_svg(svg_path, output_path):
    text = svg_path.read_text(errors="ignore")

    def clean_path(match):
        tag = match.group(0)
        bounds = path_bounds(tag)
        rgb = rgb_values(tag)
        if rgb and bounds and bounds["y"] < 2050:
            is_dark_text = (
                max(rgb) - min(rgb) <= 3
                and max(rgb) <= 35
                and bounds["area"] <= 900
                and bounds["width"] <= 55
                and bounds["height"] <= 35
                and "stroke:none" in tag
            )
            if is_dark_text:
                return ""

        if not is_color(rgb) or "fill-opacity:1" not in tag:
            return tag
        if not bounds or bounds["y"] >= 2050:
            return tag
        tag = re.sub(r"fill:rgb\([^)]*\)", "fill:rgb(100%,100%,100%)", tag)
        tag = re.sub(r"fill-opacity:[^;]+", "fill-opacity:1", tag)
        return tag

    output_path.write_text(re.sub(r"<path\b[^>]*>", clean_path, text), encoding="utf-8")


def color_key(values):
    return "|".join(f"{value:.6f}" for value in values)


def extract_words(bbox_path):
    root = ET.fromstring(bbox_path.read_text(errors="ignore"))
    words = []
    for word in root.findall(".//{http://www.w3.org/1999/xhtml}word"):
        words.append(
            {
                "text": "".join(word.itertext()).strip(),
                "x_min": float(word.attrib["xMin"]),
                "x_max": float(word.attrib["xMax"]),
                "y_min": float(word.attrib["yMin"]),
                "y_max": float(word.attrib["yMax"]),
            }
        )
    return words


def extract_legend_swatches(svg_path):
    text = svg_path.read_text(errors="ignore")
    swatches = []
    for index, tag in enumerate(re.findall(r"<path\b[^>]*>", text)):
        rgb = rgb_values(tag)
        if not is_color(rgb) or "fill-opacity:1" not in tag:
            continue
        bounds = path_bounds(tag)
        if not bounds:
            continue
        is_legend_swatch = (
            bounds["y"] >= 2050
            and 80 <= bounds["area"] <= 700
            and 8 <= bounds["width"] <= 32
            and 5 <= bounds["height"] <= 22
        )
        if not is_legend_swatch:
            continue
        swatches.append(
            {
                "source_path_index": index,
                "fill_rgb_percent": [round(value, 6) for value in rgb],
                "bounds": bounds,
            }
        )
    return swatches


def text_next_to_swatch(swatch, words, next_swatch_x=None):
    bounds = swatch["bounds"]
    y_center = bounds["y"] + bounds["height"] / 2
    x_left = bounds["x"] + bounds["width"] + 4
    x_right = min(bounds["x"] + 185, (next_swatch_x or bounds["x"] + 185) - 4)
    row_words = [
        word
        for word in words
        if abs(((word["y_min"] + word["y_max"]) / 2) - y_center) <= 10
        and x_left <= word["x_min"] <= x_right
    ]
    row_words.sort(key=lambda word: word["x_min"])
    return " ".join(word["text"] for word in row_words).strip()


def parse_legend_label(value):
    value = re.sub(r"\s+\d{1,3}\s*-\s*.+$", "", value.strip())
    match = re.match(r"^\s*(\d{1,3})\s*-\s*(.+?)\s*$", value)
    if not match:
        return None
    return {"number": int(match.group(1)), "name": normalize_name(match.group(2))}


def extract_legend_by_color(svg_path, bbox_path):
    swatches = extract_legend_swatches(svg_path)
    words = extract_words(bbox_path)
    swatches_by_row = {}
    for swatch in swatches:
        row_key = round((swatch["bounds"]["y"] + swatch["bounds"]["height"] / 2) / 18) * 18
        swatches_by_row.setdefault(row_key, []).append(swatch)

    entries = []
    for row in swatches_by_row.values():
        row.sort(key=lambda item: item["bounds"]["x"])
        for index, swatch in enumerate(row):
            next_swatch_x = row[index + 1]["bounds"]["x"] if index + 1 < len(row) else None
            label = parse_legend_label(text_next_to_swatch(swatch, words, next_swatch_x))
            if not label:
                continue
            entries.append(
                {
                    **label,
                    "fill_rgb_percent": swatch["fill_rgb_percent"],
                    "source_swatch_path_index": swatch["source_path_index"],
                    "swatch_bounds": swatch["bounds"],
                }
            )
    return entries


def normalize_name(value):
    value = re.sub(r"\s+", " ", value.strip())
    return value.strip(" -")


def extract_legend(pdf_path, output_dir):
    text_path = output_dir / "barrios_loteos_text.txt"
    if not text_path.exists():
        run(["pdftotext", "-layout", str(pdf_path), str(text_path)])
    pages = text_path.read_text(errors="ignore").split("\f")
    legend = {}
    for page_number, page_text in enumerate(pages, start=1):
        rows = []
        for number, name in re.findall(r"(?<![A-Z0-9])(\d{1,3})-([A-ZÁÉÍÓÚÜÑ0-9. ]+?)(?=\s+\d{1,3}-|\s*$)", page_text):
            cleaned = normalize_name(name)
            if cleaned and len(cleaned) > 1:
                rows.append({"number": int(number), "name": cleaned})
        legend[str(page_number)] = rows
    return legend


def build_dataset(pdf_path, output_dir):
    ensure_svg_pages(pdf_path, output_dir)
    pages = {}
    for page in (1, 2, 3):
        svg_path = output_dir / f"page-{page}.svg"
        clean_svg_path = output_dir / f"page-{page}-limpio.svg"
        bbox_path = output_dir / f"page-{page}-bbox.html"
        write_clean_svg(svg_path, clean_svg_path)
        legend_by_color = {
            color_key(entry["fill_rgb_percent"]): entry
            for entry in extract_legend_by_color(svg_path, bbox_path)
        }
        zones = extract_paths(svg_path)
        for zone in zones:
            assignment = legend_by_color.get(color_key(zone["fill_rgb_percent"]))
            if assignment:
                zone["auto_assignment"] = {
                    "number": assignment["number"],
                    "name": assignment["name"],
                    "source": "legend_color_match",
                    "source_swatch_path_index": assignment["source_swatch_path_index"],
                }
        pages[str(page)] = {
            "svg": str(svg_path.relative_to(ROOT)),
            "clean_svg": str(clean_svg_path.relative_to(ROOT)),
            "legend_by_color": sorted(legend_by_color.values(), key=lambda row: (row["number"], row["name"])),
            "zones": zones,
        }
    return {
        "meta": {
            "source_pdf": str(pdf_path),
            "note": "Extraccion preliminar desde PDF exportado de QGIS. Requiere validacion visual numero por numero.",
        },
        "legend": extract_legend(pdf_path, output_dir),
        "pages": pages,
    }


def main():
    parser = argparse.ArgumentParser(description="Extrae zonas coloreadas del PDF municipal de barrios y loteos.")
    parser.add_argument("--pdf", type=Path, default=DEFAULT_PDF)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()

    dataset = build_dataset(args.pdf, args.output_dir)
    output_json = args.output_dir / "zonas_extraidas_preliminar.json"
    output_json.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")
    for page, data in dataset["pages"].items():
        print(f"pagina {page}: {len(data['zones'])} paths coloreados candidatos")
    for page, rows in dataset["legend"].items():
        print(f"leyenda pagina {page}: {len(rows)} entradas detectadas")
    print(output_json)


if __name__ == "__main__":
    main()
