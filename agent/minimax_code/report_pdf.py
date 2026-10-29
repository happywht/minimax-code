"""Generate a text PDF with an embedded, operator-provided CJK TrueType font."""
from __future__ import annotations

import os
import threading
from io import BytesIO
from pathlib import Path
from xml.sax.saxutils import escape

from .report_content import report_blocks

_GATE = threading.BoundedSemaphore(1)
MAX_OUTPUT_BYTES = 5 * 1024 * 1024


def _font_paths():
    explicit = os.environ.get("MINIMAX_CODE_PDF_FONT")
    if explicit:
        path = Path(explicit).expanduser()
        if not path.is_absolute():
            raise ValueError("MINIMAX_CODE_PDF_FONT must be an absolute path to a CJK TrueType font")
        return [path]
    windows = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    return [windows / "simhei.ttf", windows / "Deng.ttf",
            Path("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"),
            Path("/usr/share/fonts/truetype/arphic/ukai.ttc"),
            Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"),
            Path("/usr/share/fonts/truetype/noto/NotoSansSC-Regular.ttf")]


def generate_pdf(filename: str, title: str, paragraphs=None, sections=None) -> bytes:
    if not _GATE.acquire(blocking=False):
        raise ValueError("PDF generation is busy; retry later")
    try:
        return _generate_pdf(filename, title, paragraphs, sections)
    finally:
        _GATE.release()


def _generate_pdf(filename, title, paragraphs, sections):
    blocks = report_blocks(filename, title, paragraphs, sections, ".pdf")
    if any(kind == "table" and (len(value[0]) > 10 or sum(len(row) for row in value) > 1000)
           for kind, value in blocks):
        raise ValueError("PDF tables support at most 10 columns and 1000 cells each")
    try:
        from reportlab.lib import colors
        from reportlab.lib.enums import TA_LEFT
        from reportlab.lib.pagesizes import A4
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        from reportlab.platypus import LongTable, Paragraph, SimpleDocTemplate, Spacer, TableStyle
    except ImportError as exc:
        raise ValueError("PDF generation requires attachments dependencies: uv sync --extra attachments") from exc
    font = None
    for path in _font_paths():
        if not path.is_file():
            continue
        try:
            candidate = TTFont("MiniMaxReportCJK", str(path))
            chars = {ord(c) for kind, value in blocks for text in (
                [c for row in value for c in row] if kind == "table" else [value])
                for c in text if c not in "\t\n"}
            if not all(code in candidate.face.charWidths for code in chars):
                continue
            pdfmetrics.registerFont(candidate)
            font = candidate.fontName
            break
        except Exception:
            # Try the other locally configured fonts if this one cannot be embedded.
            continue
    if not font:
        raise ValueError("No compatible Chinese TrueType font covers this report; install a CJK TTF and set MINIMAX_CODE_PDF_FONT to its absolute path")
    styles = {
        "heading0": ParagraphStyle("title", fontName=font, fontSize=18, leading=25, spaceAfter=14, wordWrap="CJK", alignment=TA_LEFT),
        "heading1": ParagraphStyle("heading", fontName=font, fontSize=14, leading=20, spaceBefore=9, spaceAfter=8, wordWrap="CJK"),
        "paragraph": ParagraphStyle("body", fontName=font, fontSize=10, leading=16, spaceAfter=8, wordWrap="CJK"),
        "cell": ParagraphStyle("cell", fontName=font, fontSize=9, leading=13, wordWrap="CJK"),
    }

    def paragraph(value, style):
        return Paragraph(escape(value).replace("\n", "<br/>").replace("\t", "    "), styles[style])

    output = BytesIO()
    document = SimpleDocTemplate(output, pagesize=A4, leftMargin=42, rightMargin=42,
                                 topMargin=42, bottomMargin=42, title=title, author="MiniMax Code")
    story = []
    for kind, value in blocks:
        if kind != "table":
            story.append(paragraph(value, kind))
        else:
            table = LongTable([[paragraph(cell, "cell") for cell in row] for row in value],
                              colWidths=[document.width / len(value[0])] * len(value[0]), hAlign="LEFT")
            table.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), .4, colors.grey),
                                       ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            story.extend([table, Spacer(1, 8)])
    try:
        document.build(story)
    except Exception as exc:
        raise ValueError("PDF layout failed; shorten oversized table cells or split long rows") from exc
    data = output.getvalue()
    if not 0 < len(data) <= MAX_OUTPUT_BYTES:
        raise ValueError("Generated PDF exceeds 5 MiB")
    return data
