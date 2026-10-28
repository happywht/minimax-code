"""Bounded existing PDF text, in physical page order; no rendering or OCR.

Only the resource-limited worker calls this parser. Library budgets supplement
that process boundary; they do not provide an OS filesystem/network sandbox.
"""
from __future__ import annotations

import io

from .attachment_metadata import PDF_SCOPE, failure

MAX_PAGES = 200
PAGE_BYTES = 8 * 1024 * 1024
TOTAL_PAGE_BYTES = 32 * 1024 * 1024
NO_TEXT = "[no extracted text; OCR not attempted]"


def _failure(reason, *, unavailable=False):
    return failure(reason, unavailable=unavailable, scope=PDF_SCOPE)


def parse_pdf(data):
    try:
        from pypdf import PdfReader, apply_configuration
        from pypdf.errors import DependencyError, LimitReachedError, PdfReadError
    except ImportError:
        return _failure("missing_dependency", unavailable=True)
    if not data.startswith(b"%PDF-"):
        return _failure("invalid_package")
    try:
        # Public API only. Never allow image filters to discover an external CLI.
        with apply_configuration(
            maximum_declared_stream_length=PAGE_BYTES,
            array_based_stream_maximum_output_length=PAGE_BYTES,
            zlib_maximum_output_length=PAGE_BYTES,
            lzw_maximum_output_length=PAGE_BYTES,
            run_length_maximum_output_length=PAGE_BYTES,
            page_tree_maximum_entries=512,
            page_tree_maximum_depth=32,
            xform_maximum_invocations_per_extraction=256,
            stream_decoding_work_maximum_length=TOTAL_PAGE_BYTES,
            stream_parsing_work_maximum_count=200_000,
            jbig2dec_binary=None,
        ), PdfReader(io.BytesIO(data), strict=True, xref_stream_maximum_entries=100_000) as reader:
            # The upstream constructor probes empty passwords. Reject all
            # encryption before accessing pages, including empty-password files.
            if reader.is_encrypted:
                return _failure("unsupported_feature")
            if len(reader.pages) > MAX_PAGES:
                return _failure("package_limit")
            output, size, lines, total = [], 0, 0, 0
            truncated, has_text = False, False
            for number, page in enumerate(reader.pages, 1):
                content = page.get_contents()
                decoded = content.get_data() if content is not None else b""
                total += len(decoded)
                if len(decoded) > PAGE_BYTES or total > TOTAL_PAGE_BYTES:
                    return _failure("package_limit")
                # Keep checking later pages after display truncation: a later
                # parse failure invalidates the whole preview. No other PDF APIs.
                text = page.extract_text(extraction_mode="plain")
                if not isinstance(text, str) or "\x00" in text:
                    return _failure("invalid_package")
                visible = bool(text.strip())
                has_text |= visible
                value = f"[page {number}]\n{text if visible else NO_TEXT}\n"
                bounded = value.encode("utf8")[:max(0, 65536 - size)].decode("utf8", "ignore")
                bounded = "".join(bounded.splitlines(keepends=True)[:max(0, 2000 - lines)])
                truncated |= bounded != value
                output.append(bounded)
                size += len(bounded.encode("utf8"))
                lines += len(bounded.splitlines())
            status = "empty" if not has_text else "truncated" if truncated else "parsed"
            return {"parser_status": status,
                    "parser_reason": "no_extracted_body_text_no_ocr" if not has_text else
                        "preview_limit" if truncated else "ok",
                    "parser_scope": PDF_SCOPE, "parser_version": 2,
                    "preview_text": "".join(output) if has_text else "",
                    "preview_truncated": int(has_text and truncated),
                    "quality_status": "unverified"}
    except (LimitReachedError, MemoryError, RecursionError):
        return _failure("package_limit")
    except (DependencyError, NotImplementedError):
        return _failure("unsupported_feature")
    except PdfReadError:
        return _failure("invalid_package")
    except Exception:
        # Includes incompatible dependency/configuration APIs. No unbounded
        # fallback, exception text, document snippets, filenames or host paths.
        return _failure("invalid_package")
