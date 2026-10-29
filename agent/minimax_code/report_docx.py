"""Generate bounded DOCX reports from structured operator-supplied text."""
from __future__ import annotations

import threading
from io import BytesIO

from .attachments import display_name

MAX_TEXT_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
_GATE = threading.BoundedSemaphore(1)


def _valid_text(value: str) -> bool:
    return all(char in "\t\n" or 0x20 <= ord(char) <= 0xD7FF
               or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF
               for char in value)


def generate_document(filename: str, title: str, paragraphs=None, sections=None) -> bytes:
    if not _GATE.acquire(blocking=False):
        raise ValueError("DOCX generation is busy; retry later")
    try:
        return _generate_document(filename, title, paragraphs, sections)
    finally:
        _GATE.release()


def _generate_document(filename: str, title: str, paragraphs, sections) -> bytes:
    from .ipc.protocol import AttachmentGenerateDocxParams

    validated = AttachmentGenerateDocxParams.model_validate({
        "project_id": "document", "expected_service_id": "svc_" + "0" * 32,
        "filename": filename, "title": title, "paragraphs": paragraphs, "sections": sections,
    })
    display_name(filename)
    if (not filename.lower().endswith(".docx") or any(c in filename for c in '/\\:*?"<>|')
            or not title.strip() or any(c in title for c in "\r\n")):
        raise ValueError("Invalid DOCX filename or title")
    # Build a canonical ordered list; a heading is distinct from a paragraph.
    blocks = [("heading0", title)]
    if validated.paragraphs is not None:
        blocks.extend(("paragraph", paragraph) for paragraph in validated.paragraphs)
    else:
        for section in validated.sections or []:
            if not section.heading.strip() or "\n" in section.heading or "\r" in section.heading:
                raise ValueError("Section heading must be a nonempty single line")
            blocks.append(("heading1", section.heading))
            blocks.extend((block.kind, block.text if block.kind == "paragraph" else block.rows)
                          for block in section.blocks)
    strings = []
    for kind, value in blocks:
        if kind == "table":
            strings.extend(cell for row in value for cell in row)
        else:
            strings.append(value)
    if (any(len(value) > 4096 or not _valid_text(value) for value in strings)
            or sum(len(value.encode("utf-8")) for value in strings) > MAX_TEXT_BYTES):
        raise ValueError("DOCX text contains invalid characters or exceeds its size limits")
    try:
        from docx import Document
        from docx.oxml.ns import qn
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError as exc:
        raise ValueError("DOCX generation requires the attachments dependency") from exc
    document = Document()
    for kind, value in blocks:
        if kind == "heading0":
            document.add_heading(value, level=0)
        elif kind == "heading1":
            document.add_heading(value, level=1)
        elif kind == "paragraph":
            document.add_paragraph(value)
        else:
            table = document.add_table(rows=len(value), cols=len(value[0]))
            table.style = "Table Grid"
            for document_row, row in zip(table.rows, value, strict=True):
                for document_cell, cell in zip(document_row.cells, row, strict=True):
                    document_cell.text = cell
    output = BytesIO()
    document.save(output)
    data = output.getvalue()
    if not 0 < len(data) <= MAX_OUTPUT_BYTES:
        raise ValueError("Generated DOCX exceeds 2 MiB")
    # Inspect the actual saved document in body order, including every cell.
    try:
        reopened = Document(BytesIO(data))
        actual = []
        for element in reopened.element.body:
            if element.tag == qn("w:p"):
                paragraph = Paragraph(element, reopened)
                style = paragraph.style.name
                kind = "heading0" if style == "Title" else "heading1" if style == "Heading 1" else "paragraph"
                actual.append((kind, paragraph.text))
            elif element.tag == qn("w:tbl"):
                table = Table(element, reopened)
                actual.append(("table", [[cell.text for cell in row.cells] for row in table.rows]))
            elif element.tag != qn("w:sectPr"):
                raise ValueError("Unexpected document body element")
    except Exception as exc:
        raise ValueError("Generated DOCX could not be verified") from exc
    if actual != blocks:
        raise ValueError("Generated DOCX body did not round-trip")
    return data
