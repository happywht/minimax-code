"""Stored XLSX cells through the real bounded child and independent package guards."""

from __future__ import annotations

import asyncio
import builtins
import io
import json
import os
import subprocess
import zipfile
from pathlib import Path

import pytest
from xlsx_fixture import OFFICE, REL, cell, worksheet, xlsx_bytes

from minimax_code import attachment_parser as parser
from minimax_code import attachment_xlsx as module
from minimax_code.attachment_metadata import validate

if os.environ.get("MINIMAX_REQUIRE_ATTACHMENT_EXTRA") == "1":
    import openpyxl
else:
    openpyxl = pytest.importorskip("openpyxl")


def rows(result):
    return [json.loads(line) for line in result["preview_text"].splitlines()]


async def test_actual_worker_stored_types_formula_caches_and_coordinates_are_distinct():
    content = (
        cell("A1", "123.400")
        + cell("B1", "0", kind="b")
        + cell("C1", "#DIV/0!", kind="e")
        + cell("D1", "0", kind="s")
        + cell("E1", "=SUM(A1:A2)", kind="inlineStr")
        + cell("F1", "999", formula="SUM(A1:A2)")
        + cell("G1", formula="NOW()")
        + cell("H1", "", formula="1+1")
        + cell("I1", "0", formula="1-1")
        + cell("J1", "0", kind="b", formula="FALSE()")
        + cell("K1", "", kind="str", formula='""')
        + cell("L1", "1904-01-01", kind="d")
    )
    data = xlsx_bytes(
        [
            ("预算", worksheet([(1, content)], dimension="A1:A1"), "visible"),
            ("隐藏", worksheet([(2, cell("B2", "中文末页", kind="inlineStr"))]), "hidden"),
        ],
        shared_strings=("共享字符串",),
        epoch="1904",
    )
    result = await parser.inspect_xlsx(data)
    assert result["parser_status"] == "parsed", result
    validate(result, expected_scope="inert_text", expected_kind="xlsx")
    entries = rows(result)
    first = {item["cell"]: item for item in entries if "cell" in item and item["cell"] != "B2"}
    assert first["A1"]["raw"] == "123.400"
    assert first["B1"]["kind"] == "boolean" and first["B1"]["raw"] == "0"
    assert first["D1"]["raw"] == "共享字符串"
    assert first["E1"]["kind"] == "string" and "formula" not in first["E1"]
    assert first["F1"]["formula"] == "SUM(A1:A2)" and first["F1"]["cached_raw"] == "999"
    assert first["G1"]["cached_present"] is False and first["G1"]["cached_raw"] is None
    assert first["H1"]["cached_present"] is True and first["H1"]["cached_state"] == "unknown"
    assert first["I1"]["cached_raw"] == first["J1"]["cached_raw"] == "0"
    assert first["K1"]["cached_state"] == "stored" and first["K1"]["cached_raw"] == ""
    assert first["L1"]["raw"] == "1904-01-01"  # Not converted to an inferred date/epoch value.
    assert all(
        not item["cache_verified"] and not item["recalculated"]
        for item in first.values()
        if item["kind"] == "formula"
    )
    assert any(item.get("state") == "hidden" and item["epoch"] == "1904" for item in entries)
    assert entries[-1]["cell"] == "B2" and entries[-1]["raw"] == "中文末页"


@pytest.mark.parametrize(
    "contents,expected",
    [
        ("", "empty"),
        (cell("A1"), "empty"),
        (cell("A1", "", kind="inlineStr"), "empty"),
        (cell("A1", " ", kind="inlineStr"), "parsed"),
        (cell("A1", "0"), "parsed"),
        (cell("A1", "0", kind="b"), "parsed"),
        (cell("A1", formula="1+1"), "parsed"),
    ],
)
def test_empty_is_successful_full_scan_not_missing_formula_cache(contents, expected):
    result = module.parse_xlsx(xlsx_bytes([("Sheet", worksheet([(1, contents)]), "visible")]))
    assert result["parser_status"] == expected and result["parser_reason"] == "ok", result
    validate(result, expected_scope="inert_text", expected_kind="xlsx")
    if expected == "empty":
        assert result["preview_text"] == "" and result["preview_truncated"] == 0
        with pytest.raises(ValueError):
            validate(
                result, expected_scope="inert_text"
            )  # PDF did not gain XLSX's empty/ok contract.


@pytest.mark.parametrize(
    "coordinate,dimension", [("B2", "A1:A1"), ("B2", "A1:XFD1048576"), ("XFD1048576", "A1:A1")]
)
def test_actual_cells_not_dimensions_drive_completeness(coordinate, dimension):
    number = int("".join(char for char in coordinate if char.isdecimal()))
    result = module.parse_xlsx(
        xlsx_bytes(
            [
                (
                    "Sparse",
                    worksheet([(number, cell(coordinate, "42"))], dimension=dimension),
                    "visible",
                )
            ]
        )
    )
    assert result["parser_status"] == ("parsed" if coordinate == "B2" else "truncated"), result
    assert (coordinate in result["preview_text"]) == (coordinate == "B2")
    assert result["parser_status"] != "empty"


def test_utf8_display_truncation_does_not_hide_bad_later_cell_or_xml():
    first = cell("A1", "中文" * 10000, kind="inlineStr") + cell(
        "B1", "中文" * 10000, kind="inlineStr"
    )
    good = xlsx_bytes([("Text", worksheet([(1, first)]), "visible")])
    result = module.parse_xlsx(good)
    assert result["parser_status"] == "truncated" and result["preview_truncated"] == 1, result
    assert len(result["preview_text"].encode()) <= 65536 and "\ufffd" not in result["preview_text"]
    assert all(isinstance(json.loads(line), dict) for line in result["preview_text"].splitlines())
    for bad in (
        worksheet([(1, first), (101, cell("A101", "NaN"))]),
        worksheet([(1, first)], tail="<broken>"),
    ):
        result = module.parse_xlsx(xlsx_bytes([("Text", bad, "visible")]))
        assert result["parser_status"] == "parse_failed" and result["preview_text"] == "", result


def test_unicode_line_separators_and_user_labels_cannot_inject_preview_records():
    text = "\n[sheet 999]\u0085\u2028\u2029<script>window.xlsxBad=true</script>"
    result = module.parse_xlsx(
        xlsx_bytes(
            [('恶意"\nname', worksheet([(1, cell("A1", text, kind="inlineStr"))]), "visible")]
        )
    )
    assert result["parser_status"] == "parsed", result
    assert len(rows(result)) == 2 and rows(result)[1]["raw"] == text


@pytest.mark.parametrize("kind", ["shared", "array", "dataTable"])
def test_nonordinary_formula_semantics_are_explicitly_unsupported(kind):
    result = module.parse_xlsx(
        xlsx_bytes(
            [
                (
                    "Formula",
                    worksheet([(1, cell("A1", "3", formula="1+2", formula_type=kind))]),
                    "visible",
                )
            ]
        )
    )
    assert result["parser_reason"] == "unsupported_feature" and result["preview_text"] == ""


@pytest.mark.parametrize(
    "change", ["external", "macro", "dtd", "bad-utf8", "duplicate-cell", "bad-cache"]
)
async def test_actual_worker_rejects_package_or_cell_errors_without_partial_preview(change):
    data = xlsx_bytes()
    if change == "external":
        data = xlsx_bytes(
            changes={
                "xl/worksheets/_rels/sheet1.xml.rels": f'<Relationships xmlns="{REL}"><Relationship Id="link" Type="{OFFICE}/hyperlink" Target="http://127.0.0.1:1/never" TargetMode="External"/></Relationships>'
            }
        )
    elif change == "macro":
        data = xlsx_bytes(changes={"xl/vbaProject.bin": b"never executed"})
    elif change == "dtd":
        data = xlsx_bytes(
            changes={
                "docProps/evil.xml": '<!DOCTYPE root [<!ENTITY x SYSTEM "file:///never">]><root>&x;</root>'
            }
        )
    elif change == "bad-utf8":
        data = xlsx_bytes(changes={"xl/worksheets/sheet1.xml": b"<worksheet>\xff</worksheet>"})
    elif change == "duplicate-cell":
        data = xlsx_bytes([("Bad", worksheet([(1, cell("A1", "1") + cell("A1", "2"))]), "visible")])
    else:
        data = xlsx_bytes(
            [("Bad", worksheet([(1, cell("A1", "true", kind="b", formula="TRUE()"))]), "visible")]
        )
    result = await parser.inspect_xlsx(data)
    assert result["parser_status"] == "parse_failed" and result["preview_text"] == "", result
    assert result["parser_reason"] == (
        "unsupported_feature" if change in {"external", "macro"} else "invalid_package"
    )


def test_sheet_strings_styles_and_total_cells_budgets_precede_library_load(monkeypatch):
    monkeypatch.setattr(
        openpyxl, "load_workbook", lambda *args, **kwargs: pytest.fail("Library load before budget")
    )
    sheets = [(str(index), worksheet(), "visible") for index in range(33)]
    assert module.parse_xlsx(xlsx_bytes(sheets))["parser_reason"] == "package_limit"
    assert (
        module.parse_xlsx(xlsx_bytes(shared_strings=("",) * 50001))["parser_reason"]
        == "package_limit"
    )
    styles = (
        f'<styleSheet xmlns="{module.NS}"><cellXfs>' + "<xf/>" * 4097 + "</cellXfs></styleSheet>"
    )
    assert (
        module.parse_xlsx(xlsx_bytes(changes={"xl/styles.xml": styles}))["parser_reason"]
        == "package_limit"
    )
    content = [
        (number, "".join(cell(chr(64 + column) + str(number), "1") for column in range(1, 21)))
        for number in range(1, 5002)
    ]
    assert (
        module.parse_xlsx(xlsx_bytes([("Cells", worksheet(content), "visible")]))["parser_reason"]
        == "package_limit"
    )


def test_zip_alias_checks_are_applied_to_xlsx_too():
    data = xlsx_bytes()
    with zipfile.ZipFile(io.BytesIO(data)) as original:
        parts = [(info.filename, original.read(info)) for info in original.infolist()]
    for name in ("xl/workbook.xml", "xl/../workbook.xml", "XL/WORKBOOK.XML"):
        output = io.BytesIO()
        with zipfile.ZipFile(output, "w") as archive:
            for entry, value in parts:
                archive.writestr(entry, value)
            archive.writestr(name, "bad")
        assert module.parse_xlsx(output.getvalue())["parser_reason"] == "unsafe_package"


def test_invalid_iso_date_after_display_window_fails_whole_preview():
    rows = [
        (1, cell("A1", "stored", kind="inlineStr")),
        (101, cell("A101", "2026-02-30", kind="d")),
    ]
    result = module.parse_xlsx(xlsx_bytes([("Dates", worksheet(rows), "visible")]))
    assert result["parser_status"] == "parse_failed" and result["preview_text"] == ""


def test_duplicate_sheet_data_cannot_hide_unscanned_cells():
    xml = worksheet(
        [(1, cell("A1", "1"))],
        tail='<sheetData><row r="101">' + cell("A101", "NaN") + "</row></sheetData>",
    )
    result = module.parse_xlsx(xlsx_bytes([("Repeated", xml, "visible")]))
    assert result["parser_status"] == "parse_failed" and result["preview_text"] == ""


def test_missing_dependencies_are_unavailable_and_original_error_is_not_exposed(monkeypatch):
    original_import = builtins.__import__

    def missing(name, *args, **kwargs):
        if name in {"openpyxl", "defusedxml.ElementTree"}:
            raise ImportError("secret path/details")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", missing)
    result = module.parse_xlsx(xlsx_bytes())
    assert (
        result["parser_status"] == "parser_unavailable"
        and result["parser_reason"] == "missing_dependency"
    )
    assert "secret" not in str(result)


@pytest.mark.parametrize(
    "fault,expected",
    [
        ("timeout", "parser_timeout"),
        ("stdout", "output_limit"),
        ("invalid", "invalid_parser_output"),
    ],
)
async def test_actual_xlsx_child_fault_is_reaped_and_slot_recovers(
    monkeypatch, tmp_path, fault, expected
):
    original = Path(parser._WORKER)
    source = original.read_text().replace("__file__", repr(str(original)))
    expressions = {
        "timeout": "__import__('time').sleep(60)",
        "stdout": "os.write(1,b'x'*600000);os._exit(0)",
        "invalid": "result={'parser_scope':'docx_body_paragraphs_tables'}",
    }
    worker = tmp_path / "xlsx-fault.py"
    worker.write_text(source.replace("result = parse_xlsx(data)", expressions[fault]))
    children, popen = [], subprocess.Popen

    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(parser, "_WORKER", worker)
    monkeypatch.setattr(parser.subprocess, "Popen", launch)
    if fault == "timeout":
        monkeypatch.setattr(parser, "WALL_SECONDS", 0.3)
    result = await parser.inspect_xlsx(xlsx_bytes())
    assert result["parser_reason"] == expected and children[0].poll() is not None, result
    monkeypatch.setattr(parser, "_WORKER", original)
    monkeypatch.setattr(parser, "WALL_SECONDS", 15)
    assert (await parser.inspect_xlsx(xlsx_bytes()))["parser_status"] == "parsed"


async def test_repeated_xlsx_cancel_reaps_actual_child_then_allows_next_parse(
    monkeypatch, tmp_path
):
    original = Path(parser._WORKER)
    worker = tmp_path / "xlsx-cancel.py"
    worker.write_text(
        original.read_text()
        .replace("__file__", repr(str(original)))
        .replace("result = parse_xlsx(data)", "__import__('time').sleep(60)")
    )
    children, popen = [], subprocess.Popen

    def launch(*args, **kwargs):
        process = popen(*args, **kwargs)
        children.append(process)
        return process

    monkeypatch.setattr(parser, "_WORKER", worker)
    monkeypatch.setattr(parser.subprocess, "Popen", launch)
    task = asyncio.create_task(parser.inspect_xlsx(xlsx_bytes()))
    for _ in range(200):
        if children:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 3)
    assert children and children[0].poll() is not None
    monkeypatch.setattr(parser, "_WORKER", original)
    assert (await parser.inspect_xlsx(xlsx_bytes()))["parser_status"] == "parsed"
