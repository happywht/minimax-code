"""Strict, stdlib-only persisted Office receipt validation.

This validates the bounded receipt contract, not Office file contents. Bundles
may import it without loading a writer, parser, service or application runtime.
"""
from __future__ import annotations

import json
import re

MAX_REPORT_BYTES = 64 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
SCOPE = "xlsx_plain_cells_v1"
WRITER_VERSION = "3.1.5"
VALIDATOR_VERSION = 1
RECORD_KEYS = (
    "id", "project_id", "service_id", "attachment_id", "run_id", "request_key",
    "request_hash", "input_sha256", "writer_version", "validator_version",
    "validation_report", "created_at",
)
REPORT_KEYS = {
    "version", "scope", "input_sha256", "file_sha256", "size_bytes", "sheet_name",
    "data_rows", "column_count", "cell_count", "formula_count", "external_relationship_count",
    "macro_count", "structure_status", "business_quality", "render_status", "writer",
    "validator_version",
}
CHART_REPORT_KEYS = {
    "version", "scope", "input_sha256", "file_sha256", "size_bytes", "sheet_name",
    "data_rows", "column_count", "cell_count", "cell_check", "chart_reference_check",
    "business_quality", "render_status", "writer", "validator_version",
}
_HEX = re.compile(r"[0-9a-f]{64}\Z")
_OFFICE = re.compile(r"off_[0-9a-f]{32}\Z")
_ATTACHMENT = re.compile(r"att_[0-9a-f]{32}\Z")
_SERVICE = re.compile(r"svc_[0-9a-f]{32}\Z")
_REQUEST_KEY = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _require(condition: object, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _text(value: object, maximum: int) -> bool:
    if type(value) is not str or not 1 <= len(value) <= maximum:
        return False
    try:
        value.encode("utf-8")
    except UnicodeError:
        return False
    return True


def _hex(value: object) -> bool:
    return type(value) is str and _HEX.fullmatch(value) is not None


def _integer(value: object, minimum: int, maximum: int) -> bool:
    return type(value) is int and minimum <= value <= maximum


def _sheet(value: object) -> bool:
    return (_text(value, 31) and bool(value.strip())
            and not value.startswith("'") and not value.endswith("'")
            and not any(char in "[]:*?/\\" or ord(char) < 32
                        or ord(char) in {0xFFFE, 0xFFFF} for char in value))


def _validate_join_processing(value: dict) -> None:
    _require(set(value) == ({"version", "operation", "sources", "keys", "counts", "join_mode"}
                            if value.get("version") in (4, 5, 6) else
                            {"version", "operation", "sources", "keys", "counts"})
             and type(value["version"]) is int and value["version"] in {2, 3, 4, 5, 6}
             and (value["version"] != 4 or (type(value["join_mode"]) is str
                  and value["join_mode"] in {"matched", "unmatched"}))
             and (value["version"] != 5 or value["join_mode"] == "right_unmatched")
             and (value["version"] != 6 or value["join_mode"] == "reconciliation")
             and value["operation"] == "left_join", "Invalid join processing description")
    sources = value["sources"]
    _require(type(sources) is list and len(sources) == 2, "Invalid join sources")
    for source in sources:
        _require(type(source) is dict and set(source) == {"type", "attachment_id", "sha256", "sheet", "columns"}
                 and type(source["type"]) is str and source["type"] in {"csv", "xlsx"}
                 and type(source["attachment_id"]) is str and _ATTACHMENT.fullmatch(source["attachment_id"])
                 and _hex(source["sha256"])
                 and (source["sheet"] is None if source["type"] == "csv" else _text(source["sheet"], 31))
                 and type(source["columns"]) is list and 1 <= len(source["columns"]) <= 20,
                 "Invalid join source")
        for column in source["columns"]:
            _require(type(column) is dict and set(column) == {"source", "name", "kind", "trim", "null_values", "changed_cells"}
                     and _text(column["source"], 80) and _text(column["name"], 80)
                     and type(column["kind"]) is str and column["kind"] in {"text", "number", "boolean"}
                     and type(column["trim"]) is bool and type(column["null_values"]) is list
                     and len(column["null_values"]) <= 20
                     and all(type(marker) is str and len(marker) <= 4096 for marker in column["null_values"])
                     and _integer(column["changed_cells"], 0, 1000), "Invalid join column")
    keys = value["keys"]
    _require(type(keys) is dict and set(keys) == {"left", "right"}, "Invalid join keys")
    if value["version"] == 2:
        _require(all(_text(keys[k], 80) for k in keys), "Invalid join keys")
    else:
        _require(all(type(keys[k]) is list and 1 <= len(keys[k]) <= 20
                     and all(_text(name, 80) for name in keys[k])
                     and len(set(keys[k])) == len(keys[k]) for k in keys)
                 and len(keys["left"]) == len(keys["right"]), "Invalid join keys")
        left_columns = {column["name"]: column["kind"] for column in sources[0]["columns"]}
        right_columns = {column["name"]: column["kind"] for column in sources[1]["columns"]}
        _require(all(left in left_columns and right in right_columns
                     and left_columns[left] == right_columns[right]
                     for left, right in zip(keys["left"], keys["right"], strict=True)),
                 "Invalid join key columns")
    counts = value["counts"]
    _require(type(counts) is dict and set(counts) == ({"left_rows", "right_rows", "matched_rows", "unmatched_rows", "right_blank_keys", "right_unmatched_rows"}
             if value["version"] in (5, 6) else {"left_rows", "right_rows", "matched_rows", "unmatched_rows", "right_blank_keys"})
             and all(_integer(counts[k], 0, 1000) for k in counts)
             and counts["matched_rows"] + counts["unmatched_rows"] == counts["left_rows"]
             and counts["right_blank_keys"] <= counts["right_rows"]
             and (value["version"] not in (5, 6) or counts["right_blank_keys"] <= counts["right_unmatched_rows"] <= counts["right_rows"]), "Invalid join counts")


def validate_report(value: object) -> dict:
    """Return the original valid report dict, or raise ValueError."""
    _require(type(value) is dict, "Invalid Office report fields")
    chart = value.get("scope") == "xlsx_chart_values_v1"
    keys = CHART_REPORT_KEYS if chart else REPORT_KEYS
    _require(set(value) - {"processing", "operator_notes"} == keys, "Invalid Office report fields")
    if "operator_notes" in value:
        notes = value["operator_notes"]
        _require(type(notes) is dict and set(notes) == {"source", "method", "limitations"}
                 and all(type(notes[key]) is str and len(notes[key]) <= 2000
                         and (not notes[key] or _text(notes[key], 2000)) for key in notes)
                 and any(notes[key].strip() for key in notes), "Invalid operator notes")
    if "processing" in value:
        processing = value["processing"]
        if type(processing) is dict and processing.get("version") in (2, 3, 4, 5, 6):
            _validate_join_processing(processing)
        else:
            processing_keys = {
                "version", "source", "columns", "calculated_column", "filters",
                "deduplicate_by", "group_by", "sort_by", "summaries", "counts"
            }
            _require(type(processing) is dict
                     and set(processing) - {"calculated_columns", "fill_missing", "pivot_by"} == processing_keys
                     and type(processing["version"]) is int and processing["version"] == 1,
                     "Invalid Office processing description")
            source = processing["source"]
            _require(type(source) is dict and type(source.get("type")) is str
                     and type(source["type"]) is str and source["type"] in {"csv", "xlsx"}
                     and set(source) - ({"delivery_id"} if source.get("type") == "xlsx" else set()) == ({"type", "attachment_id", "sha256"} if source.get("type") == "csv"
                                         else {"type", "attachment_id", "sha256", "sheet", "date_columns",
                                               "percentage_columns", "currency_columns"})
                     and type(source.get("attachment_id")) is str
                     and _ATTACHMENT.fullmatch(source["attachment_id"]) and _hex(source.get("sha256"))
                     and ("delivery_id" not in source or (type(source["delivery_id"]) is str
                          and re.fullmatch(r"off_[0-9a-f]{32}", source["delivery_id"]))),
                     "Invalid Office processing source")
            if source["type"] == "xlsx":
                _require(_text(source["sheet"], 31) and all(
                    type(source[key]) is list and len(source[key]) <= 20
                    and all(_text(item, 4096) for item in source[key])
                    for key in ("date_columns", "percentage_columns", "currency_columns")),
                    "Invalid Office worksheet processing source")
            columns = processing["columns"]
            _require(type(columns) is list and 1 <= len(columns) <= 20, "Invalid Office processing columns")
            for column in columns:
                _require(type(column) is dict and set(column) == {
                    "source", "name", "kind", "trim", "null_values", "changed_cells"
                } and _text(column["source"], 80) and _text(column["name"], 80)
                    and type(column["kind"]) is str and type(column["kind"]) is str and column["kind"] in {"text", "number", "boolean"}
                    and type(column["trim"]) is bool and type(column["null_values"]) is list
                    and len(column["null_values"]) <= 20
                    and all(type(marker) is str and len(marker) <= 4096 for marker in column["null_values"])
                    and _integer(column["changed_cells"], 0, 1000), "Invalid Office processing column")
            if "fill_missing" in processing:
                fills = processing["fill_missing"]
                column_kinds = {column["name"]: column["kind"] for column in columns}
                _require(type(fills) is list and 1 <= len(fills) <= 20,
                         "Invalid Office fill description")
                seen_fills = set()
                for fill in fills:
                    _require(type(fill) is dict and set(fill) == {"column", "value", "filled_cells"}
                             and type(fill["column"]) is str and fill["column"] in column_kinds
                             and fill["column"] not in seen_fills
                             and _integer(fill["filled_cells"], 0, 1000),
                             "Invalid Office fill description")
                    seen_fills.add(fill["column"])
                    val, kind = fill["value"], column_kinds[fill["column"]]
                    _require((kind == "text" and _text(val, 4096))
                             or (kind == "boolean" and type(val) is bool)
                             or (kind == "number" and type(val) in (int, float)
                                 and abs(val) <= 999999999999999),
                             "Invalid Office fill value")
            counts = processing["counts"]
            _require(type(counts) is dict and set(counts) == {
                "source_rows", "matched_rows", "removed_duplicates", "output_rows"
            } and all(_integer(counts[key], 0, 1000) for key in counts)
                and counts["matched_rows"] <= counts["source_rows"]
                and counts["removed_duplicates"] <= counts["matched_rows"]
                and counts["output_rows"] <= counts["matched_rows"] - counts["removed_duplicates"],
                "Invalid Office processing counts")
            if "fill_missing" in processing:
                _require(all(fill["filled_cells"] <= counts["source_rows"] for fill in processing["fill_missing"]),
                         "Invalid Office fill counts")
            _require(all(type(processing[key]) is list and len(processing[key]) <= limit
                         for key, limit in (("filters", 10), ("deduplicate_by", 20),
                                            ("sort_by", 3), ("summaries", 20)))
                     and (processing["calculated_column"] is None or type(processing["calculated_column"]) is dict)
                     and ("calculated_columns" not in processing or (
                         processing["calculated_column"] is None
                         and type(processing["calculated_columns"]) is list
                         and 1 <= len(processing["calculated_columns"]) <= 10
                         and all(type(item) is dict for item in processing["calculated_columns"])))
                     and (processing["group_by"] is None or type(processing["group_by"]) is dict)
                     and ("pivot_by" not in processing or (type(processing["pivot_by"]) is dict
                          and processing["group_by"] is None and not processing["summaries"])),
                     "Invalid Office processing operations")
    checks = (("cell_check", "passed"), ("chart_reference_check", "passed")) if chart else (
        ("formula_count", 0), ("external_relationship_count", 0),
        ("macro_count", 0), ("structure_status", "passed"))
    for key, constant in (("version", 1),
        ("scope", "xlsx_chart_values_v1" if chart else SCOPE), *checks,
        ("business_quality", "unverified"), ("render_status", "not_attempted"),
        ("validator_version", VALIDATOR_VERSION),
    ):
        _require(type(value[key]) is type(constant) and value[key] == constant,
                 "Unsupported Office report value")
    _require(_hex(value["input_sha256"]) and _hex(value["file_sha256"]),
             "Invalid Office report hash")
    _require(_integer(value["size_bytes"], 1, MAX_FILE_BYTES)
             and _sheet(value["sheet_name"])
             and _integer(value["data_rows"], 0, 1000)
             and _integer(value["column_count"], 1, 20)
             and _integer(value["cell_count"], 1, 5000)
             and value["cell_count"] == (value["data_rows"] + 1) * value["column_count"],
             "Invalid Office report dimensions")
    writer = value["writer"]
    _require(type(writer) is dict and set(writer) == {"name", "version"}
             and type(writer["name"]) is str and writer["name"] == "openpyxl"
             and type(writer["version"]) is str and writer["version"] == WRITER_VERSION,
             "Unsupported Office writer")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid Office report JSON") from exc
    _require(len(encoded) <= MAX_REPORT_BYTES, "Office report exceeds size limit")
    return value


def _pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        _require(key not in result, "Duplicate Office report JSON key")
        result[key] = value
    return result


def _constant(_value: str) -> None:
    raise ValueError("Non-finite Office report JSON value")


def validate_record(row: object, attachment: object = None) -> dict:
    """Decode an exact persisted row and optionally check its attachment binding.

    Callers convert SQLite rows to dict first. Historical project/run IDs remain
    references; no project lookup, migration, file access or generation occurs.
    """
    _require(type(row) is dict and set(row) == set(RECORD_KEYS), "Invalid Office record fields")
    _require(type(row["id"]) is str and _OFFICE.fullmatch(row["id"])
             and type(row["service_id"]) is str and _SERVICE.fullmatch(row["service_id"])
             and type(row["attachment_id"]) is str and _ATTACHMENT.fullmatch(row["attachment_id"])
             and _text(row["project_id"], 128)
             and (row["run_id"] is None or _text(row["run_id"], 128))
             and type(row["request_key"]) is str and _REQUEST_KEY.fullmatch(row["request_key"])
             and _text(row["created_at"], 100)
             and _hex(row["request_hash"]) and _hex(row["input_sha256"]),
             "Invalid Office record identity")
    _require(type(row["writer_version"]) is str and row["writer_version"] == WRITER_VERSION
             and type(row["validator_version"]) is int
             and row["validator_version"] == VALIDATOR_VERSION,
             "Unsupported Office record version")
    raw = row["validation_report"]
    _require(type(raw) is str, "Office report must be persisted JSON text")
    try:
        _require(len(raw.encode("utf-8")) <= MAX_REPORT_BYTES, "Office report exceeds size limit")
        report = validate_report(json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant))
    except (UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("Invalid Office report JSON") from exc
    _require(report["input_sha256"] == row["input_sha256"]
             and report["writer"]["version"] == row["writer_version"]
             and report["validator_version"] == row["validator_version"],
             "Office record and report differ")
    if attachment is not None:
        _require(type(attachment) is dict, "Invalid Office attachment metadata")
        for key, expected in (
            ("id", row["attachment_id"]), ("project_id", row["project_id"]),
            ("service_id", row["service_id"]), ("sha256", report["file_sha256"]),
            ("size_bytes", report["size_bytes"]), ("declared_type", "xlsx"),
            ("recognized_type", "xlsx"),
        ):
            actual = attachment.get(key)
            _require(type(actual) is type(expected) and actual == expected,
                     "Office attachment binding differs")
    return {**row, "validation_report": report}
