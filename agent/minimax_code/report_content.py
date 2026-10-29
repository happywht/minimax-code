"""Shared validation for structured text reports."""
from __future__ import annotations

from .attachments import display_name

MAX_TEXT_BYTES = 256 * 1024


def _valid_text(value: str) -> bool:
    return all(char in "\t\n" or 0x20 <= ord(char) <= 0xD7FF
               or 0xE000 <= ord(char) <= 0xFFFD or 0x10000 <= ord(char) <= 0x10FFFF
               for char in value)


def report_blocks(filename, title, paragraphs, sections, extension, sources=None):
    from .ipc.protocol import AttachmentGenerateDocxParams

    validated = AttachmentGenerateDocxParams.model_validate({
        "project_id": "document", "expected_service_id": "svc_" + "0" * 32,
        "filename": filename, "title": title, "paragraphs": paragraphs, "sections": sections,
        "sources": [] if sources is None else sources,
    })
    display_name(filename)
    if (not filename.lower().endswith(extension) or any(c in filename for c in '/\\:*?"<>|')
            or not title.strip() or any(c in title for c in "\r\n")):
        raise ValueError("Invalid report filename or title")
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
    if validated.sources:
        blocks.append(("heading1", "参考来源（由提供者填写，未自动核验）"))
        for index, source in enumerate(validated.sources, 1):
            blocks.append(("paragraph", f"[{index}] {source.title}"))
            if source.title_truncated:
                blocks.append(("paragraph", "页面标题已截断。"))
            blocks.append(("paragraph", f"网址：{source.url}"))
            if source.accessed_at is not None:
                blocks.append(("paragraph", f"访问时间：{source.accessed_at}"))
            if source.excerpt is not None:
                blocks.append(("paragraph", f"来源摘录（未经核验）：{source.excerpt}"))
            if source.excerpt_truncated:
                blocks.append(("paragraph", "页面内容或摘录已截断，不能代表全文。"))
    strings = []
    for kind, value in blocks:
        if kind == "table":
            strings.extend(cell for row in value for cell in row)
        else:
            strings.append(value)
    if (any(len(value) > 4096 or not _valid_text(value) for value in strings)
            or sum(len(value.encode("utf-8")) for value in strings) > MAX_TEXT_BYTES):
        raise ValueError("DOCX text contains invalid characters or exceeds its size limits")
    return blocks
