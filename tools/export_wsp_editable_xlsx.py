#!/usr/bin/env python3
import json
import re
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile
from xml.sax.saxutils import escape


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data" / "grupos_wsp_zonas.json"
OUTPUT = ROOT / "data" / "grupos_wsp_editable_para_bloques.xlsx"


def clean(value):
    return "" if value is None else str(value)


def sheet_xml(rows):
    body = []
    for r_index, row in enumerate(rows, start=1):
        cells = []
        for c_index, value in enumerate(row, start=1):
            ref = f"{column_name(c_index)}{r_index}"
            cells.append(
                f'<c r="{ref}" t="inlineStr"><is><t>{escape(clean(value))}</t></is></c>'
            )
        body.append(f'<row r="{r_index}">{"".join(cells)}</row>')
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheetData>
    {"".join(body)}
  </sheetData>
</worksheet>'''


def column_name(index):
    name = ""
    while index:
        index, rem = divmod(index - 1, 26)
        name = chr(65 + rem) + name
    return name


def safe_sheet_name(name):
    return re.sub(r"[][\\/*?:]", " ", name)[:31] or "Hoja"


def workbook_xml(sheet_names):
    sheets = []
    for index, name in enumerate(sheet_names, start=1):
        sheets.append(
            f'<sheet name="{escape(safe_sheet_name(name))}" sheetId="{index}" r:id="rId{index}"/>'
        )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"
 xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
  <sheets>{"".join(sheets)}</sheets>
</workbook>'''


def workbook_rels(sheet_count):
    rels = []
    for index in range(1, sheet_count + 1):
        rels.append(
            f'<Relationship Id="rId{index}" '
            'Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" '
            f'Target="worksheets/sheet{index}.xml"/>'
        )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  {"".join(rels)}
</Relationships>'''


def root_rels():
    return '''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>'''


def content_types(sheet_count):
    overrides = [
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>',
    ]
    for index in range(1, sheet_count + 1):
        overrides.append(
            f'<Override PartName="/xl/worksheets/sheet{index}.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        )
    return f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  {"".join(overrides)}
</Types>'''


def build_rows(data):
    resumen = [[
        "grupo", "grupo_label", "canal_whatsapp", "perimetro_original",
        "barrios_detectados", "barrios_a_revisar", "terminos_pendientes",
        "estado_para_bloques", "observacion"
    ]]
    barrios = [[
        "grupo", "grupo_label", "numero", "barrio_zona", "label_municipal",
        "tipo", "cobertura", "requiere_revision", "origen_texto",
        "estado_bloque", "observacion"
    ]]
    pendientes = [[
        "grupo", "grupo_label", "termino_original", "motivo",
        "sugerencias", "accion_sugerida", "estado", "observacion"
    ]]

    for group in data.get("groups", []):
        matched = group.get("barrios", [])
        review = [item for item in matched if item.get("needs_review")]
        pending = group.get("review_terms", [])
        unresolved = [item for item in pending if not item.get("matches")]
        estado = "listo_por_barrio"
        if review or unresolved or group.get("requires_review"):
            estado = "requiere_revision_editable"

        resumen.append([
            group.get("group", ""),
            group.get("label", ""),
            group.get("whatsapp", ""),
            group.get("perimeter", ""),
            len(matched),
            len(review),
            len(unresolved),
            estado,
            "",
        ])

        for zone in matched:
            cobertura = "parcial" if zone.get("needs_review") else "completo"
            barrios.append([
                group.get("group", ""),
                group.get("label", ""),
                zone.get("number", ""),
                zone.get("name", ""),
                zone.get("label", ""),
                zone.get("kind", ""),
                cobertura,
                "si" if zone.get("needs_review") else "no",
                zone.get("matched_from", ""),
                "usar_barrio_completo" if cobertura == "completo" else "validar_o_dibujar_parcial",
                "",
            ])

        for item in pending:
            suggestions = "; ".join(match.get("label", "") for match in item.get("matches", []))
            action = "validar_sugerencia" if suggestions else "dibujar_perimetro_manual"
            pendientes.append([
                group.get("group", ""),
                group.get("label", ""),
                item.get("term", ""),
                item.get("reason", ""),
                suggestions,
                action,
                "pendiente",
                "",
            ])

    return {
        "Resumen grupos": resumen,
        "Barrios por grupo": barrios,
        "Pendientes editable": pendientes,
    }


def write_xlsx(sheets):
    names = list(sheets)
    with ZipFile(OUTPUT, "w", ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types(len(names)))
        zf.writestr("_rels/.rels", root_rels())
        zf.writestr("xl/workbook.xml", workbook_xml(names))
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels(len(names)))
        for index, name in enumerate(names, start=1):
            zf.writestr(f"xl/worksheets/sheet{index}.xml", sheet_xml(sheets[name]))


def main():
    data = json.loads(DATA.read_text(encoding="utf-8"))
    write_xlsx(build_rows(data))
    print(OUTPUT)


if __name__ == "__main__":
    main()
