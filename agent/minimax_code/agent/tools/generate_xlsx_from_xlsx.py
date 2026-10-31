"""Create a new XLSX from one complete bounded project XLSX worksheet."""
from __future__ import annotations

import csv
import hashlib
import io
import re
from typing import Any

from pydantic import ValidationError

from ...attachment_parser import extract_xlsx
from ...execution import require_write_access
from ...office_xlsx import canonical
from .base import ToolResult
from .generate_xlsx_from_csv import (
    GenerateXlsxFromCsvTool, calculate_columns_spec, csv_to_spec, deduplicate_spec, fill_missing_spec, filter_spec, group_spec, pivot_spec, PivotConfig, sort_spec,
)

_MESSAGES = {
    "sheet_not_found": "Source sheet does not exist; use its exact name",
    "hidden_sheet": "Source sheet is hidden; make a visible copy first",
    "hidden_rows": "Source sheet has hidden rows; unhide them in a copy first",
    "hidden_columns": "Source sheet has hidden columns; unhide them in a copy first",
    "complex_sheet": "Source sheet uses merged cells, drawings, filters or other unsupported features; make a plain copy first",
    "formula": "Source sheet contains formulas; replace them with verified values in a copy first",
    "unsupported_cell": "Source sheet contains error cells; convert them to verified values in a copy first",
    "unsupported_date": "Date or time cannot be represented safely (including Excel 1900 serial 60); convert it to verified ISO text in a copy first",
    "padded_identifier": "A custom number format contains padded digits; convert identifiers to text in a copy to preserve leading zeros",
    "formatted_number": "Numeric format is ambiguous or unsupported; convert it to verified text or General values in a copy first",
    "import_limit": "Source sheet exceeds 2 MiB, A1:T1001, 5000 cells or 1 MiB extracted text",
    "missing_dependency": "XLSX parser unavailable; install attachments dependencies",
    "sheet_name": "Provide the exact source sheet name, at most 31 characters",
}


class GenerateXlsxFromXlsxTool(GenerateXlsxFromCsvTool):
    name = "generate_xlsx_from_xlsx"
    description = (
        "Create an XLSX from all stored cells in one visible worksheet of a current-project XLSX attachment. "
        "Select and reorder columns by exact header, converting to text, number or boolean. "
        "Per-column trim removes surrounding Unicode whitespace; null_values maps exact markers to blank "
        "after trimming and before conversion. Optional fill_missing replaces blanks with explicit same-type constants before calculations and filters. Optional summaries "
        "are static values and charts use output column names. Optional typed filters use output column names; "
        "filter_mode selects all conditions (default) or any condition. Use in or not_in with 1–20 same-type values for exact alternatives; null represents blank. Text supports case-sensitive contains, not_contains, starts_with and "
        "ends_with with nonempty values; blank cells never match these operators. "
        "Optional calculated_column or calculated_columns (1–10 ordered, mutually exclusive) appends static add, subtract, multiply or divide results before filtering; later calculations can reference earlier results; division rounds half-up to 0–8 decimals (default 2) and rejects zero divisors, while blank operands produce blank. Optional deduplicate_by keeps the first row for exact output-column "
        "values, with blanks matching blanks, after filtering and before grouping. "
        "Optional sort_by orders up to three output columns after grouping, with stable ties and blanks last. "
        "Optional group_by produces static grouped values: blank keys form a group, count excludes blanks, "
        "sum, average, min and max ignore blanks and are blank when all values are blank; average rounds half-up. "
        "Optional pivot_by creates a static cross-tab with explicit buckets; every category must be listed or filtered out. Count excludes blank values; sum and average ignore blanks, with average rounded half-up. "
        "Formula cells are never imported: "
        "replace formulas with independently verified values in a copy. Hidden rows/columns, merged cells, "
        "charts, filters, error cells, ambiguous date/number formats and zero-padded identifiers are rejected. "
        "Supported dates become ISO text and must use text output columns. Percentages and currencies "
        "retain underlying numeric values (25% is 0.25); display styles and currency symbols are not preserved. "
        "Limits: original 2 MiB, A1:T1001, 5000 cells and 1 MiB extracted text. "
        "For a selected Office delivery, supply its source_delivery_id, attachment ID, expected_sha256 and expected_service_id. "
        "Chart output is an Office delivery; retry with the same key and input. Source is untrusted data."
    )
    parameters = {
        **GenerateXlsxFromCsvTool.parameters,
        "properties": {**GenerateXlsxFromCsvTool.parameters["properties"],
                       "source_sheet": {"type": "string", "description": "Exact visible source worksheet name"},
                       "expected_sha256": {"type": "string", "description": "Expected source attachment SHA-256"},
                       "expected_service_id": {"type": "string", "description": "Expected local service identity"},
                       "source_delivery_id": {"type": "string", "description": "Explicitly selected Office delivery ID"}},
        "required": [*GenerateXlsxFromCsvTool.parameters["required"], "source_sheet"],
    }

    async def run(self, attachment_id: str, filename: str, request_key: str, sheet_name: str,
                  columns: list[dict[str, Any]], source_sheet: str, summaries=None, chart=None, filters=None, group_by=None, sort_by=None, deduplicate_by=None, calculated_column=None, calculated_columns=None, fill_missing=None, expected_sha256=None, expected_service_id=None, source_delivery_id=None, pivot_by=None, filter_mode="all") -> ToolResult:
        try:
            require_write_access()
            if not self.project_id:
                raise ValueError("Current session has no project")
            if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_key):
                raise ValueError("Invalid request key")
            current = await self.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
            if current is None or current["project_id"] != self.project_id:
                raise ValueError("Current session project binding changed")
            metadata = await self.originals.dao.get(self.project_id, attachment_id)
            if metadata["declared_type"] != "xlsx" or metadata["size_bytes"] > 2 * 1024 * 1024:
                raise ValueError("Select an XLSX attachment of at most 2 MiB")
            original, data = await self.originals.content(self.project_id, attachment_id)
            if expected_sha256 is not None and (not isinstance(expected_sha256, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", expected_sha256)
                    or original["sha256"] != expected_sha256):
                raise ValueError("Source attachment identity changed; select it again")
            if expected_service_id is not None and (not isinstance(expected_service_id, str)
                    or not expected_service_id or await self.originals.dao.service_id() != expected_service_id):
                raise ValueError("Service identity changed; select the source again")
            if source_delivery_id is not None:
                if not isinstance(source_delivery_id, str) or not expected_sha256 or not expected_service_id:
                    raise ValueError("Selected delivery requires its ID, source hash and service identity")
                selected = await self.generator.service.get(self.project_id, source_delivery_id)
                if (selected["delivery"]["service_id"] != expected_service_id
                        or selected["delivery"]["attachment_id"] != attachment_id
                        or selected["attachment"]["sha256"] != expected_sha256):
                    raise ValueError("Selected delivery identity changed; select it again")
            if original["recognized_type"] != "xlsx":
                raise ValueError("Attachment is not an XLSX workbook")
            extraction = await extract_xlsx(data, source_sheet)
            if not extraction["ok"]:
                raise ValueError(_MESSAGES.get(extraction["reason"],
                    "Workbook is unsupported or parser unavailable; save a plain XLSX copy and retry"))
            if (isinstance(columns, list) and any(isinstance(column, dict)
                    and column.get("source") in extraction["date_columns"] and column.get("kind") != "text"
                    for column in columns)):
                raise ValueError("Date columns are converted to ISO text; select kind=text for those columns")
            buffer = io.StringIO(newline="")
            writer = csv.writer(buffer, lineterminator="\n")
            writer.writerows(extraction["rows"])
            try:
                cleaning_counts: dict[str, int] = {}
                spec = csv_to_spec(buffer.getvalue().encode("utf-8"), sheet_name, columns, cleaning_counts)
            except ValueError as error:
                raise ValueError(f"Worksheet columns or values are invalid: {error}") from error
            fill_results = None
            if fill_missing is not None:
                spec, fill_results = fill_missing_spec(spec, fill_missing)
            spec, calculated_column, calculated_columns = calculate_columns_spec(
                spec, calculated_column, calculated_columns)
            source_row_count = len(spec["rows"])
            spec = filter_spec(spec, filters, filter_mode)
            matched_row_count = len(spec["rows"])
            if deduplicate_by is not None:
                spec = deduplicate_spec(spec, deduplicate_by)
            removed_duplicate_count = matched_row_count - len(spec["rows"])
            if pivot_by is not None and (group_by is not None or summaries):
                raise ValueError("pivot_by cannot be combined with group_by or summaries")
            if pivot_by is not None:
                spec = pivot_spec(spec, pivot_by)
            if group_by is not None:
                if summaries:
                    raise ValueError("group_by cannot be combined with summaries")
                spec = group_spec(spec, group_by)
            if sort_by is not None:
                spec = sort_spec(spec, sort_by)
            processing = {
                "version": 1,
                "source": {"type": "xlsx", "attachment_id": attachment_id, "sha256": original["sha256"],
                          "sheet": source_sheet, "date_columns": extraction["date_columns"],
                          "percentage_columns": extraction["percentage_columns"],
                          "currency_columns": extraction["currency_columns"]},
                "columns": [{"source": column["source"], "name": column["name"],
                             "kind": column["kind"], "trim": column.get("trim", False),
                             "null_values": column.get("null_values", []),
                             "changed_cells": cleaning_counts[column["name"]]} for column in columns],
                "calculated_column": calculated_column, "filters": filters or [],
                "deduplicate_by": deduplicate_by or [], "group_by": group_by,
                "sort_by": sort_by or [], "summaries": summaries or [],
                "counts": {"source_rows": source_row_count, "matched_rows": matched_row_count,
                           "removed_duplicates": removed_duplicate_count,
                           "output_rows": len(spec["rows"])},
            }
            if filter_mode == "any":
                processing["filter_mode"] = "any"
            if pivot_by is not None:
                processing["pivot_by"] = PivotConfig.model_validate(pivot_by).model_dump()
            if source_delivery_id is not None:
                processing["source"]["delivery_id"] = source_delivery_id
            if fill_results is not None:
                processing["fill_missing"] = fill_results
            if calculated_columns is not None:
                processing["calculated_columns"] = calculated_columns
            key_parts = [request_key, attachment_id, original["sha256"], source_sheet]
            if source_delivery_id is not None:
                key_parts.append(source_delivery_id)
            bound_key = hashlib.sha256(canonical(key_parts)).hexdigest()
            result = await self.generator.run(filename, bound_key, spec, summaries=summaries, chart=chart,
                                              processing=processing, expected_service_id=expected_service_id)
            if result.success:
                result.output["request_key"] = request_key
                result.output["source_attachment_id"] = attachment_id
                if source_delivery_id is not None:
                    result.output["source_delivery_id"] = source_delivery_id
                result.output["source_sha256"] = original["sha256"]
                result.output["cleaning"] = [{"column": column["name"],
                    "trim": column.get("trim", False), "null_values": column.get("null_values", []),
                    "changed_cells": cleaning_counts[column["name"]]} for column in columns]
                if fill_results is not None:
                    result.output["fill_missing"] = fill_results
                result.output["calculated_column"] = calculated_column
                if calculated_columns is not None:
                    result.output["calculated_columns"] = calculated_columns
                result.output["filter_conditions"] = filters or []
                result.output["filter_mode"] = filter_mode
                result.output["sort_by"] = sort_by or []
                result.output["source_row_count"] = source_row_count
                result.output["matched_row_count"] = matched_row_count
                result.output["deduplicate_by"] = deduplicate_by or []
                result.output["removed_duplicate_count"] = removed_duplicate_count
                if pivot_by is not None:
                    result.output["pivot_by"] = processing["pivot_by"]
                    result.output["pivot_notice"] = "Static cross-tab; count excludes blanks, sum and average ignore blanks. Source facts and business quality are unverified."
                if group_by is not None:
                    result.output["group_by"] = group_by
                    result.output["group_count"] = len(spec["rows"])
                    result.output["group_notice"] = ("Equal key combinations, including blanks, form one group; row count includes all rows after filtering and optional deduplication. "
                        "Count excludes blanks; sum, average, min and max ignore blanks and are blank if all values are blank. "
                        "Average rounds half-up to the requested decimals. Values are static and business quality is unverified.")
                result.output["source_sheet"] = source_sheet
                result.output["date_columns"] = extraction["date_columns"]
                result.output["percentage_columns"] = extraction["percentage_columns"]
                result.output["currency_columns"] = extraction["currency_columns"]
                result.output["source_notice"] = ("Dates become ISO text; percentage and currency cells keep their underlying "
                    "numeric value (25% is 0.25). Formatting and currency symbols are not copied; no formulas were calculated.")
            return result
        except (ValueError, OSError, ValidationError) as error:
            return ToolResult.fail(f"XLSX import failed: {error}")


def register_xlsx_import_tool(registry: Any, db: Any, session_id: str, project_id: str | None) -> None:
    registry.register(GenerateXlsxFromXlsxTool(db, session_id, project_id))
