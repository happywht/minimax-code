"""One short-lived bounded parser process; bytes only, no vault/DB paths.

The launcher is internal trusted code. It is not a shell tool and accepts no
command, URL, plugin, environment override or installation instruction.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import sysconfig
import threading
import time
from pathlib import Path

from .attachment_metadata import PDF_SCOPE, SCOPE, failure, validate

WALL_SECONDS = 15
QUEUE_SECONDS = 5
STDOUT_LIMIT = 512 * 1024
STDERR_LIMIT = 16 * 1024
_GATE = threading.BoundedSemaphore(1)
_WORKER = Path(__file__).with_name("_attachment_parser_worker.py")


def _environment():
    # Python isolated mode also ignores Python env flags. No service secrets.
    return {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "SYSTEMDRIVE", "TESSDATA_PREFIX") if key in os.environ}


def _command(kind):
    if kind not in {"docx", "pdf", "xlsx", "xlsx_extract", "xlsx_extract_cache", "xlsx_list", "pdf_page", "docx_segment", "ocr_image", "ocr_pdf_page"}:
        raise ValueError("Invalid fixed parser kind")
    # Windows venv python.exe is a redirector. Kill/wait must target the actual
    # parser PID, so launch the base interpreter and add only trusted venv libs
    # after its resource gate. Never process site .pth or inherit PYTHONPATH.
    executable = getattr(sys, "_base_executable", sys.executable) if os.name == "nt" else sys.executable
    # -I ignores Python environment flags; read-only parsing must not cache in the installation.
    command = [executable, "-I", "-B", "-S", str(_WORKER)]
    if sys.platform == "win32":
        from ._windows_process_host import parent_arguments

        command.extend(parent_arguments()[1:])
    elif sys.platform == "linux":
        command.append(str(os.getpid()))
    paths = sysconfig.get_paths()
    command.extend([kind, paths["purelib"], paths["platlib"]])
    return command


def _run(data, cancelled, scope=SCOPE, kind="docx"):
    def failed(reason, **kwargs):
        return {"ok": False, "reason": reason} if kind in {"xlsx_extract", "xlsx_extract_cache", "xlsx_list", "pdf_page", "docx_segment", "ocr_image", "ocr_pdf_page"} else failure(reason, scope=scope, **kwargs)

    if sys.platform not in {"linux", "win32"}:
        return failed("limits_unavailable", unavailable=True)
    deadline = time.monotonic() + QUEUE_SECONDS
    acquired = False
    while not cancelled.is_set() and time.monotonic() < deadline:
        if _GATE.acquire(timeout=min(.05, max(0, deadline - time.monotonic()))):
            acquired = True
            break
    if not acquired:
        return failed("queue_timeout", unavailable=True)
    process, readers = None, []
    output, errors = bytearray(), bytearray()
    overflow = threading.Event()
    try:
        if cancelled.is_set():
            return failed("parser_crash")
        try:
            process = subprocess.Popen(_command(kind), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                       stderr=subprocess.PIPE, env=_environment(), close_fds=True,
                                       creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        except (OSError, ValueError, RuntimeError):
            return failed("limits_unavailable", unavailable=True)
        deadline = time.monotonic() + WALL_SECONDS

        def read(stream, target, limit):
            try:
                while chunk := stream.read(4096):
                    remaining = max(0, limit - len(target))
                    target.extend(chunk[:remaining])
                    if len(chunk) > remaining:
                        overflow.set()
            finally:
                stream.close()

        def write():
            try:
                process.stdin.write(data)
                process.stdin.flush()
            except (OSError, ValueError):
                pass
            finally:
                process.stdin.close()

        output_limit = 3 * 1024 * 1024 if kind in {"xlsx_extract", "xlsx_extract_cache"} else STDOUT_LIMIT
        for stream, target, limit in ((process.stdout, output, output_limit),
                                      (process.stderr, errors, STDERR_LIMIT)):
            reader = threading.Thread(target=read, args=(stream, target, limit), daemon=True)
            reader.start()
            readers.append(reader)
        writer = threading.Thread(target=write, daemon=True)
        writer.start()
        readers.append(writer)
        reason = None
        while process.poll() is None:
            if cancelled.is_set():
                reason = "parser_crash"
            elif overflow.is_set():
                reason = "output_limit"
            elif time.monotonic() >= deadline:
                reason = "parser_timeout"
            if reason:
                process.kill()
                break
            cancelled.wait(.02)
        process.wait()  # Actual child reaped before releasing the only parser slot.
        for reader in readers:
            reader.join()
        if reason or overflow.is_set():
            return failed(reason or "output_limit")
        if process.returncode == 126:
            return failed("limits_unavailable", unavailable=True)
        if process.returncode:
            return failed("parser_crash")
        try:
            result = json.loads(output)
            if kind in {"ocr_image", "ocr_pdf_page"}:
                if type(result) is not dict:
                    raise ValueError
                reasons = {"missing_dependency", "missing_language", "invalid_image", "image_limit", "invalid_pdf", "pdf_limit", "page_out_of_range"}
                if result.get("ok") is False and set(result) == {"ok", "reason"} and result["reason"] in reasons:
                    return result
                if (set(result) != {"ok", "text", "truncated"} or result["ok"] is not True
                        or type(result["text"]) is not str or len(result["text"]) > 16000
                        or type(result["truncated"]) is not bool):
                    raise ValueError
                return result
            if kind in {"pdf_page", "docx_segment"}:
                if type(result) is not dict:
                    raise ValueError
                errors = {"invalid_package", "unsafe_package", "unsupported_feature", "missing_dependency",
                          "package_limit", "text_limit", "cursor_out_of_range"}
                if result.get("ok") is False and set(result) == {"ok", "reason"} and result["reason"] in errors:
                    return result
                keys = {"ok", "kind", "unit", "unit_total", "location", "start", "end", "text", "truncated", "next_cursor", "status", "ocr"}
                if (set(result) != keys or result["ok"] is not True
                        or result["kind"] != ("pdf" if kind == "pdf_page" else "docx")
                        or type(result["unit"]) is not int or type(result["unit_total"]) is not int
                        or not 0 <= result["unit"] <= result["unit_total"] <= (200 if kind == "pdf_page" else 10000)
                        or type(result["start"]) is not int or type(result["end"]) is not int
                        or not 0 <= result["start"] <= result["end"] <= 1024 * 1024
                        or type(result["text"]) is not str or len(result["text"]) > 4000
                        or result["end"] - result["start"] != len(result["text"])
                        or type(result["location"]) is not str or len(result["location"]) > 256
                        or type(result["truncated"]) is not bool or result["status"] not in {"text", "empty"}
                        or result["ocr"] != "not_attempted"):
                    raise ValueError
                requested_unit = int.from_bytes(data[:4], "big")
                requested_offset = int.from_bytes(data[4:8], "big")
                empty_document = kind == "docx_segment" and result["unit_total"] == 0 and result["unit"] == 0
                if (not empty_document and result["unit"] != requested_unit
                        or result["start"] != requested_offset
                        or empty_document and (requested_unit != 1 or requested_offset != 0 or result["text"])):
                    raise ValueError
                cursor = result["next_cursor"]
                if cursor is not None and (type(cursor) is not dict or set(cursor) != {"unit", "offset"}
                        or type(cursor["unit"]) is not int or type(cursor["offset"]) is not int
                        or not 1 <= cursor["unit"] <= result["unit_total"]
                        or not 0 <= cursor["offset"] <= 1024 * 1024):
                    raise ValueError
                expected = ({"unit": result["unit"], "offset": result["end"]} if result["truncated"]
                            else {"unit": result["unit"] + 1, "offset": 0}
                            if result["unit"] < result["unit_total"] else None)
                if cursor != expected:
                    raise ValueError
                return result
            if kind == "xlsx_list":
                reasons = {"missing_dependency", "invalid_package", "unsafe_package",
                           "package_limit", "unsupported_feature"}
                if type(result) is not dict:
                    raise ValueError
                if result.get("ok") is False and set(result) == {"ok", "reason"} and result["reason"] in reasons:
                    return result
                if result.get("ok") is not True or set(result) != {"ok", "sheets"}:
                    raise ValueError
                sheets = result["sheets"]
                if (type(sheets) is not list or not 1 <= len(sheets) <= 32
                        or any(type(sheet) is not dict or set(sheet) != {"name", "state"}
                               or type(sheet["name"]) is not str or not 1 <= len(sheet["name"]) <= 31
                               or sheet["state"] not in {"visible", "hidden", "veryHidden"}
                               for sheet in sheets)
                        or len({sheet["name"].casefold() for sheet in sheets}) != len(sheets)):
                    raise ValueError
                return result
            if kind in {"xlsx_extract", "xlsx_extract_cache"}:
                reasons = {"sheet_name", "sheet_not_found", "hidden_sheet", "hidden_rows", "hidden_columns",
                           "complex_sheet", "import_limit", "formula", "formula_cache_missing", "formula_cache_type", "unsupported_cell", "formatted_number", "unsupported_date", "padded_identifier",
                           "missing_dependency", "invalid_package", "unsafe_package", "package_limit", "unsupported_feature"}
                if type(result) is not dict:
                    raise ValueError
                if result.get("ok") is False and set(result) == {"ok", "reason"} and result["reason"] in reasons:
                    return result
                expected_fields = {"ok", "rows", "date_columns", "percentage_columns", "currency_columns"}
                if kind == "xlsx_extract_cache":
                    expected_fields.add("formula_cells")
                if result.get("ok") is not True or set(result) != expected_fields:
                    raise ValueError
                rows = result["rows"]
                if (type(rows) is not list or not 1 <= len(rows) <= 1001
                        or any(type(row) is not list or not 1 <= len(row) <= 20 for row in rows)
                        or any(len(row) != len(rows[0]) for row in rows)
                        or len(rows) * len(rows[0]) > 5000
                        or any(type(value) is not str or len(value) > 4096 for row in rows for value in row)):
                    raise ValueError
                for field in ("date_columns", "percentage_columns", "currency_columns"):
                    columns = result[field]
                    if (type(columns) is not list or len(columns) > 20
                            or any(type(value) is not str or value not in rows[0] for value in columns)
                            or len(set(columns)) != len(columns)):
                        raise ValueError
                if kind == "xlsx_extract_cache":
                    cells = result["formula_cells"]
                    if (type(cells) is not list or len(cells) > len(rows) * len(rows[0]) or
                            any(type(cell) is not str for cell in cells) or len(set(cells)) != len(cells)):
                        raise ValueError
                    for cell in cells:
                        match = re.fullmatch(r"([A-T])([1-9][0-9]{0,3})", cell)
                        if not match:
                            raise ValueError
                        row, column = int(match[2]), ord(match[1]) - ord("A") + 1
                        if row > len(rows) or column > len(rows[0]) or rows[row - 1][column - 1] == "":
                            raise ValueError
                return result
            return validate(result, expected_scope=scope,
                            expected_kind="xlsx" if kind == "xlsx" else None)
        except (ValueError, UnicodeError, TypeError):
            return failed("invalid_parser_output")
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        for reader in readers:
            reader.join()
        _GATE.release()


async def inspect_docx(data):
    return await _inspect(data, SCOPE, "docx")


async def inspect_pdf(data):
    return await _inspect(data, PDF_SCOPE, "pdf")


async def inspect_xlsx(data):
    return await _inspect(data, PDF_SCOPE, "xlsx")


async def list_xlsx_sheets(data):
    if len(data) > 2 * 1024 * 1024:
        return {"ok": False, "reason": "package_limit"}
    return await _inspect(data, PDF_SCOPE, "xlsx_list")


async def extract_xlsx(data, sheet_name, *, use_formula_cache=False):
    if type(use_formula_cache) is not bool or not isinstance(sheet_name, str) or not 1 <= len(sheet_name) <= 31:
        return {"ok": False, "reason": "sheet_name"}
    try:
        encoded = sheet_name.encode("utf-8")
    except UnicodeError:
        return {"ok": False, "reason": "sheet_name"}
    if len(encoded) > 124 or len(data) > 2 * 1024 * 1024:
        return {"ok": False, "reason": "import_limit"}
    framed = len(encoded).to_bytes(4, "big") + encoded + data
    return await _inspect(framed, PDF_SCOPE, "xlsx_extract_cache" if use_formula_cache else "xlsx_extract")


async def read_document_segment(data, kind, unit=1, offset=0):
    if (kind not in {"pdf", "docx"} or type(unit) is not int or not 1 <= unit <= 10000
            or type(offset) is not int or not 0 <= offset <= 1024 * 1024
            or len(data) > 10 * 1024 * 1024):
        return {"ok": False, "reason": "cursor_out_of_range"}
    framed = unit.to_bytes(4, "big") + offset.to_bytes(4, "big") + data
    return await _inspect(framed, PDF_SCOPE if kind == "pdf" else SCOPE,
                          "pdf_page" if kind == "pdf" else "docx_segment")


async def ocr_image(data, kind, language):
    kinds = {"png": 1, "jpeg": 2}
    languages = {"eng": 1, "chi_sim": 2, "eng+chi_sim": 3}
    if kind not in kinds or language not in languages or len(data) > 10 * 1024 * 1024:
        return {"ok": False, "reason": "invalid_image"}
    return await _inspect(bytes((kinds[kind], languages[language])) + data, SCOPE, "ocr_image")


async def ocr_pdf_page(data, page, language):
    languages = {"eng": 1, "chi_sim": 2, "eng+chi_sim": 3}
    if (type(page) is not int or not 1 <= page <= 200 or language not in languages
            or len(data) > 10 * 1024 * 1024):
        return {"ok": False, "reason": "invalid_pdf"}
    return await _inspect(page.to_bytes(4, "big") + bytes((languages[language],)) + data,
                          PDF_SCOPE, "ocr_pdf_page")


async def _inspect(data, scope, kind):
    cancelled = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(_run, data, cancelled, scope, kind))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        cancelled.set()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
        task.result()
        raise
