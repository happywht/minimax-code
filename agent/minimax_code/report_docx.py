"""Generate a bounded, plain DOCX from operator-supplied text.

No source paths, macros, external links, model calls, or document rendering.
"""
from __future__ import annotations

import threading
from io import BytesIO

from .attachments import display_name

MAX_TEXT_BYTES = 256 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
_GATE = threading.BoundedSemaphore(1)


def _valid_text(value: str) -> bool:
    return all(char in "\t\n\r" or 0x20 <= ord(char) <= 0xD7FF
               or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF
               for char in value)


def generate_document(filename: str, title: str, paragraphs: list[str]) -> bytes:
    if not _GATE.acquire(blocking=False):
        raise ValueError("DOCX generation is busy; retry later")
    try:
        return _generate_document(filename, title, paragraphs)
    finally:
        _GATE.release()


def _generate_document(filename: str, title: str, paragraphs: list[str]) -> bytes:
    display_name(filename)
    if (not filename.lower().endswith(".docx") or any(c in filename for c in '/\\:*?"<>|')
            or not title.strip() or len(title) > 200 or any(c in title for c in "\r\n")
            or not 1 <= len(paragraphs) <= 100
            or any(not isinstance(p, str) or len(p) > 4096 or "\r" in p for p in paragraphs)
            or not all(_valid_text(value) for value in [title, *paragraphs])):
        raise ValueError("Invalid DOCX filename, title, or paragraphs")
    if sum(len(value.encode("utf-8")) for value in [title, *paragraphs]) > MAX_TEXT_BYTES:
        raise ValueError("DOCX text exceeds 256 KiB")
    try:
        from docx import Document
    except ImportError as exc:
        raise ValueError("DOCX generation requires the attachments dependency") from exc
    document = Document()
    document.add_heading(title, level=0)
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    output = BytesIO()
    document.save(output)
    data = output.getvalue()
    if not 0 < len(data) <= MAX_OUTPUT_BYTES:
        raise ValueError("Generated DOCX exceeds 2 MiB")
    # Reopen the actual bytes and compare all paragraphs before publication.
    try:
        actual = [paragraph.text for paragraph in Document(BytesIO(data)).paragraphs]
    except Exception as exc:
        raise ValueError("Generated DOCX could not be verified") from exc
    if actual != [title, *paragraphs]:
        raise ValueError("Generated DOCX text did not round-trip")
    return data
