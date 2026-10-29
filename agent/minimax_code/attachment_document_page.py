"""Bounded, cursor-based reading of PDF pages or DOCX body paragraphs."""
from __future__ import annotations

import io

from .attachment_docx import Reject, package_parts, require, validate_xml
from .attachment_pdf import MAX_PAGES, PAGE_BYTES, TOTAL_PAGE_BYTES

MAX_CHARS = 4000
MAX_UNIT_CHARS = 1024 * 1024
MAX_PARAGRAPHS = 10000


def _segment(kind, unit, offset, total, location, text):
    require(type(text) is str and "\x00" not in text and len(text) <= MAX_UNIT_CHARS, "text_limit")
    require(offset <= len(text), "cursor_out_of_range")
    text.encode("utf-8")
    end = min(offset + MAX_CHARS, len(text))
    next_cursor = ({"unit": unit, "offset": end} if end < len(text)
                   else {"unit": unit + 1, "offset": 0} if unit < total else None)
    return {"ok": True, "kind": kind, "unit": unit, "unit_total": total, "location": location,
            "start": offset, "end": end, "text": text[offset:end], "truncated": end < len(text),
            "next_cursor": next_cursor, "status": "text" if text.strip() else "empty",
            "ocr": "not_attempted"}


def read_pdf_page(data, unit, offset):
    try:
        from pypdf import PdfReader, apply_configuration
        from pypdf.errors import DependencyError, LimitReachedError, PdfReadError
    except ImportError:
        return {"ok": False, "reason": "missing_dependency"}
    try:
        require(data.startswith(b"%PDF-"))
        with apply_configuration(maximum_declared_stream_length=PAGE_BYTES,
                array_based_stream_maximum_output_length=PAGE_BYTES,
                zlib_maximum_output_length=PAGE_BYTES, lzw_maximum_output_length=PAGE_BYTES,
                run_length_maximum_output_length=PAGE_BYTES, page_tree_maximum_entries=512,
                page_tree_maximum_depth=32, xform_maximum_invocations_per_extraction=256,
                stream_decoding_work_maximum_length=TOTAL_PAGE_BYTES,
                stream_parsing_work_maximum_count=200_000, jbig2dec_binary=None), \
                PdfReader(io.BytesIO(data), strict=True, xref_stream_maximum_entries=100_000) as reader:
            require(not reader.is_encrypted, "unsupported_feature")
            total = len(reader.pages)
            require(total <= MAX_PAGES, "package_limit")
            require(1 <= unit <= total, "cursor_out_of_range")
            page = reader.pages[unit - 1]
            content = page.get_contents()
            decoded = content.get_data() if content is not None else b""
            require(len(decoded) <= PAGE_BYTES, "package_limit")
            return _segment("pdf", unit, offset, total, f"page {unit}", page.extract_text(extraction_mode="plain"))
    except Reject as error:
        return {"ok": False, "reason": error.reason}
    except (LimitReachedError, MemoryError, RecursionError):
        return {"ok": False, "reason": "package_limit"}
    except (DependencyError, NotImplementedError):
        return {"ok": False, "reason": "unsupported_feature"}
    except (PdfReadError, ValueError, UnicodeError):
        return {"ok": False, "reason": "invalid_package"}
    except Exception:
        return {"ok": False, "reason": "invalid_package"}


def read_docx_segment(data, unit, offset):
    try:
        import docx
        from defusedxml.ElementTree import fromstring
        from docx.table import Table, _Cell
        from docx.text.paragraph import Paragraph
    except ImportError:
        return {"ok": False, "reason": "missing_dependency"}
    try:
        require(hasattr(docx.document.Document, "iter_inner_content")
                and hasattr(_Cell, "iter_inner_content"), "unsupported_feature")
        parts = package_parts(data)
        validate_xml(parts, fromstring)
        document = docx.Document(io.BytesIO(data))
        total = 0
        chosen = None

        def walk(container, position="body", depth=0):
            nonlocal total, chosen
            require(depth <= 8, "package_limit")
            for index, item in enumerate(container.iter_inner_content(), 1):
                label = f"{position}/{index}"
                if isinstance(item, Paragraph):
                    text = item.text
                    if text:
                        total += 1
                        require(total <= MAX_PARAGRAPHS, "package_limit")
                        if total == unit:
                            chosen = (label, text)
                elif isinstance(item, Table):
                    seen = set()
                    for row_index, row in enumerate(item.rows, 1):
                        for column_index, cell in enumerate(row.cells, 1):
                            if cell._tc in seen:
                                continue
                            seen.add(cell._tc)
                            walk(cell, f"{label}/row{row_index}/cell{column_index}", depth + 1)

        walk(document)
        if total == 0 and unit == 1 and offset == 0:
            return {"ok": True, "kind": "docx", "unit": 0, "unit_total": 0, "location": "body",
                    "start": 0, "end": 0, "text": "", "truncated": False, "next_cursor": None,
                    "status": "empty", "ocr": "not_attempted"}
        require(chosen is not None, "cursor_out_of_range")
        return _segment("docx", unit, offset, total, chosen[0], chosen[1])
    except Reject as error:
        return {"ok": False, "reason": error.reason}
    except (MemoryError, RecursionError):
        return {"ok": False, "reason": "package_limit"}
    except Exception:
        return {"ok": False, "reason": "invalid_package"}
