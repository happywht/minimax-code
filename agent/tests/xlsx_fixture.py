"""Small explicit OOXML fixtures: stored formula caches are never calculated."""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape, quoteattr

NS = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
REL = "http://schemas.openxmlformats.org/package/2006/relationships"
OFFICE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
CT = "http://schemas.openxmlformats.org/package/2006/content-types"
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml."


def cell(coordinate, value=None, *, kind="n", formula=None, formula_type=None, style=None):
    attributes = f"r={quoteattr(coordinate)} t={quoteattr(kind)}"
    if style is not None:
        attributes += f' s="{style}"'
    body = ""
    if formula is not None:
        formula_attributes = "" if formula_type is None else f" t={quoteattr(formula_type)}"
        body += f"<f{formula_attributes}>{escape(formula)}</f>"
    if value is not None:
        body += (
            f'<is><t xml:space="preserve">{escape(str(value))}</t></is>'
            if kind == "inlineStr"
            else f"<v>{escape(str(value))}</v>"
        )
    return f"<c {attributes}>{body}</c>"


def worksheet(rows=(), *, dimension="A1:T100", tail=""):
    data = "".join(f'<row r="{number}">{contents}</row>' for number, contents in rows)
    return f'<worksheet xmlns="{NS}"><dimension ref={quoteattr(dimension)}/><sheetData>{data}</sheetData>{tail}</worksheet>'


def xlsx_bytes(sheets=None, *, shared_strings=(), changes=None, epoch="1900"):
    if sheets is None:
        sheets = [
            (
                "预算",
                worksheet([(1, cell("A1", "123.40") + cell("B1", "中文", kind="inlineStr"))]),
                "visible",
            )
        ]
    parts = {}
    overrides = [("/xl/workbook.xml", MIME + "sheet.main+xml")]
    relationships = []
    nodes = []
    for index, (name, xml, state) in enumerate(sheets, 1):
        target = f"xl/worksheets/sheet{index}.xml"
        parts[target] = xml
        overrides.append(("/" + target, MIME + "worksheet+xml"))
        relationships.append(
            f'<Relationship Id="rId{index}" Type="{OFFICE}/worksheet" Target="worksheets/sheet{index}.xml"/>'
        )
        nodes.append(
            f'<sheet name={quoteattr(name)} sheetId="{index}" state={quoteattr(state)} r:id="rId{index}"/>'
        )
    if shared_strings:
        parts["xl/sharedStrings.xml"] = (
            f'<sst xmlns="{NS}">'
            + "".join(f"<si><t>{escape(value)}</t></si>" for value in shared_strings)
            + "</sst>"
        )
        overrides.append(("/xl/sharedStrings.xml", MIME + "sharedStrings+xml"))
        relationships.append(
            f'<Relationship Id="strings" Type="{OFFICE}/sharedStrings" Target="sharedStrings.xml"/>'
        )
    parts["xl/workbook.xml"] = (
        f'<workbook xmlns="{NS}" xmlns:r="{OFFICE}"><workbookPr date1904="{1 if epoch == "1904" else 0}"/><sheets>{"".join(nodes)}</sheets></workbook>'
    )
    parts["_rels/.rels"] = (
        f'<Relationships xmlns="{REL}"><Relationship Id="main" Type="{OFFICE}/officeDocument" Target="xl/workbook.xml"/></Relationships>'
    )
    parts["xl/_rels/workbook.xml.rels"] = (
        f'<Relationships xmlns="{REL}">{"".join(relationships)}</Relationships>'
    )
    parts["[Content_Types].xml"] = (
        f'<Types xmlns="{CT}"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        + "".join(f'<Override PartName="{name}" ContentType="{mime}"/>' for name, mime in overrides)
        + "</Types>"
    )
    parts.update(changes or {})
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w") as archive:
        for name, content in parts.items():
            info = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            archive.writestr(info, content.encode() if isinstance(content, str) else content)
    return result.getvalue()
