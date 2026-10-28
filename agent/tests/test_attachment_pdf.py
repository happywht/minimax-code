"""Real text-layer PDFs through the actual resource-limited worker."""
from __future__ import annotations

import asyncio
import builtins
import io
import os
import subprocess
from pathlib import Path

import pytest
from pdf_fixture import pdf_bytes

from minimax_code import attachment_parser as parser
from minimax_code.attachment_metadata import validate
from minimax_code.attachment_pdf import parse_pdf

if os.environ.get("MINIMAX_REQUIRE_ATTACHMENT_EXTRA") == "1":
    import pypdf
else:
    pypdf = pytest.importorskip("pypdf")


async def test_actual_worker_physical_order_cjk_and_empty_page():
    result = await parser.inspect_pdf(pdf_bytes(("First ASCII", "", "中文末页")))
    assert result["parser_status"] == "parsed", result
    assert result["parser_version"] == 2 and result["parser_scope"] == "inert_text"
    assert result["quality_status"] == "unverified"
    assert result["preview_text"].index("First ASCII") < result["preview_text"].index("[page 2]") < result["preview_text"].index("中文末页")
    assert "[no extracted text; OCR not attempted]" in result["preview_text"]
    validate(result, expected_scope="inert_text")


@pytest.mark.parametrize("pages", [(), ("",), ("   ", "")])
async def test_actual_worker_empty_no_ocr(pages):
    result = await parser.inspect_pdf(pdf_bytes(pages))
    assert result["parser_status"] == "empty", result
    assert result["parser_reason"] == "no_extracted_body_text_no_ocr"
    assert result["preview_text"] == "" and result["preview_truncated"] == 0


async def test_actual_worker_image_only_and_mixed_do_not_attempt_ocr():
    scanned = await parser.inspect_pdf(pdf_bytes(("",), image_pages=(0,)))
    assert scanned["parser_status"] == "empty" and scanned["parser_reason"] == "no_extracted_body_text_no_ocr"
    mixed = await parser.inspect_pdf(pdf_bytes(("ASCII", "", "中文"), image_pages=(1,)))
    assert mixed["parser_status"] == "parsed" and "[page 2]\n[no extracted text; OCR not attempted]" in mixed["preview_text"]


async def test_actual_worker_line_budget_includes_page_marker_and_stream_bomb():
    result = await parser.inspect_pdf(pdf_bytes(("line\n" * 2100,)))
    assert result["parser_status"] == "truncated", result
    assert len(result["preview_text"].splitlines()) == 2000
    assert result["preview_text"].startswith("[page 1]\n")
    bomb = await parser.inspect_pdf(pdf_bytes(("x" * 2_200_000,), compressed=True))
    assert bomb["parser_reason"] == "package_limit" and bomb["preview_text"] == "", bomb


@pytest.mark.parametrize("password", ["", "private"])
async def test_actual_worker_rejects_encryption_including_empty_password(password):
    writer = pypdf.PdfWriter(clone_from=io.BytesIO(pdf_bytes()))
    writer.encrypt(password)
    data = io.BytesIO()
    writer.write(data)
    result = await parser.inspect_pdf(data.getvalue())
    assert result["parser_status"] == "parse_failed" and result["parser_reason"] == "unsupported_feature", result
    assert result["preview_text"] == ""


@pytest.mark.parametrize("data,reason", [(b"%PDF-1.7\nwrong", "invalid_package"), (pdf_bytes(tuple("x" for _ in range(201))), "package_limit")], ids=["malformed", "page-limit"])
async def test_actual_worker_malformed_and_page_budget(data, reason):
    result = await parser.inspect_pdf(data)
    assert result["parser_reason"] == reason and result["preview_text"] == "", result


async def test_actual_worker_preview_utf8_limit_and_later_corruption():
    pages = ("中文" * 14000, "later page")
    result = await parser.inspect_pdf(pdf_bytes(pages, compressed=True))
    assert result["parser_status"] == "truncated", result
    assert result["parser_reason"] == "preview_limit" and result["preview_truncated"] == 1
    assert len(result["preview_text"].encode("utf8")) <= 65536
    assert "\ufffd" not in result["preview_text"]
    broken = await parser.inspect_pdf(pdf_bytes(pages, broken_page=1, compressed=True))
    assert broken["parser_status"] == "parse_failed" and broken["preview_text"] == "", broken


def test_public_configuration_disables_external_binary_and_fails_closed(monkeypatch):
    original = pypdf.apply_configuration
    observed = {}
    def configured(**kwargs):
        observed.update(kwargs)
        return original(**kwargs)
    monkeypatch.setattr(pypdf, "apply_configuration", configured)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: pytest.fail("External command"))
    actions = b"/OpenAction << /S /JavaScript /JS (throw sentinel) >> /AA << /WC << /S /Launch /F (C:/pdf-sentinel) >> >> /Names << /EmbeddedFiles << /Names [(hidden) << /F (C:/pdf-sentinel) >>] >> >>"
    assert parse_pdf(pdf_bytes(("Visible",), catalog=actions))["parser_status"] == "parsed"
    assert observed["jbig2dec_binary"] is None
    assert observed["page_tree_maximum_entries"] == 512
    def incompatible(**kwargs):
        raise TypeError("secret config details")
    monkeypatch.setattr(pypdf, "apply_configuration", incompatible)
    assert parse_pdf(pdf_bytes())["parser_reason"] == "invalid_package"


def test_missing_dependency_and_library_budget_are_explicit(monkeypatch):
    original_import = builtins.__import__
    def missing(name, *args, **kwargs):
        if name == "pypdf":
            raise ImportError("secret dependency details")
        return original_import(name, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "__import__", missing)
        result = parse_pdf(pdf_bytes())
    assert result["parser_status"] == "parser_unavailable" and result["parser_reason"] == "missing_dependency"
    from minimax_code import attachment_pdf
    monkeypatch.setattr(attachment_pdf, "PAGE_BYTES", 200)
    assert parse_pdf(pdf_bytes(("x" * 1000,), compressed=True))["parser_reason"] == "package_limit"


@pytest.mark.parametrize("kind", ["timeout", "stdout", "memory", "invalid_scope"])
async def test_pdf_actual_child_limits_reap_and_recover(monkeypatch, tmp_path, kind):
    original = Path(parser._WORKER)
    source = original.read_text(encoding="utf8").replace("__file__", repr(str(original)))
    expressions = {"timeout": "__import__('time').sleep(60)",
                   "stdout": "os.write(1, b'x' * (600 * 1024)); os._exit(0)",
                   "memory": "result = bytearray(800 * 1024 * 1024)",
                   "invalid_scope": "result = {'parser_scope': 'docx_body_paragraphs_tables'}"}
    worker = tmp_path / "pdf-resource-probe.py"
    worker.write_text(source.replace("result = parse_pdf(data)", expressions[kind]), encoding="utf8")
    monkeypatch.setattr(parser, "_WORKER", worker)
    if kind == "timeout":
        monkeypatch.setattr(parser, "WALL_SECONDS", .3)
    children, popen = [], subprocess.Popen
    def launched(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(parser.subprocess, "Popen", launched)
    result = await parser.inspect_pdf(pdf_bytes())
    assert len(children) == 1 and children[0].poll() is not None
    expected = {"timeout": "parser_timeout", "stdout": "output_limit", "memory": "parser_crash", "invalid_scope": "invalid_parser_output"}[kind]
    assert result["parser_reason"] == expected and result["parser_scope"] == "inert_text", result
    monkeypatch.setattr(parser, "_WORKER", original)
    monkeypatch.setattr(parser, "WALL_SECONDS", 15)
    assert (await parser.inspect_pdf(pdf_bytes()))["parser_status"] == "parsed"


async def test_pdf_repeated_cancel_reaps_child_before_slot_reuse(monkeypatch, tmp_path):
    original = Path(parser._WORKER)
    source = original.read_text(encoding="utf8").replace("__file__", repr(str(original)))
    worker = tmp_path / "pdf-cancel-probe.py"
    worker.write_text(source.replace("result = parse_pdf(data)", "__import__('time').sleep(60)"), encoding="utf8")
    monkeypatch.setattr(parser, "_WORKER", worker)
    children, popen = [], subprocess.Popen
    def launched(*args, **kwargs):
        child = popen(*args, **kwargs)
        children.append(child)
        return child
    monkeypatch.setattr(parser.subprocess, "Popen", launched)
    task = asyncio.create_task(parser.inspect_pdf(pdf_bytes()))
    for _ in range(200):
        if children:
            break
        await asyncio.sleep(.01)
    task.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert children and children[0].poll() is not None
    monkeypatch.setattr(parser, "_WORKER", original)
    assert (await parser.inspect_pdf(pdf_bytes()))["parser_status"] == "parsed"


@pytest.mark.parametrize("kind", ["missing_dependency", "limits_unavailable"])
async def test_pdf_actual_worker_unavailable_retains_scope(monkeypatch, tmp_path, kind):
    original = Path(parser._WORKER)
    source = original.read_text(encoding="utf8").replace("__file__", repr(str(original)))
    if kind == "missing_dependency":
        source = source.replace("sys.path.extend(sys.argv[-2:])", "pass")
    else:
        source = source.replace("job = establish_limits()", "raise RuntimeError('gate failed')")
    worker = tmp_path / "pdf-unavailable-probe.py"
    worker.write_text(source, encoding="utf8")
    monkeypatch.setattr(parser, "_WORKER", worker)
    result = await parser.inspect_pdf(pdf_bytes())
    assert result["parser_status"] == "parser_unavailable" and result["parser_reason"] == kind
    assert result["parser_scope"] == "inert_text" and result["preview_text"] == ""


async def test_pdf_queue_timeout_is_explicit_and_shares_single_slot(monkeypatch):
    assert parser._GATE.acquire(timeout=1)
    monkeypatch.setattr(parser, "QUEUE_SECONDS", .05)
    try:
        result = await parser.inspect_pdf(pdf_bytes())
        assert result["parser_status"] == "parser_unavailable" and result["parser_reason"] == "queue_timeout"
        assert result["parser_scope"] == "inert_text"
    finally:
        parser._GATE.release()
