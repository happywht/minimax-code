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
    GenerateXlsxFromCsvTool, calculate_spec, csv_to_spec, deduplicate_spec, filter_spec, group_spec, sort_spec,
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
        "after trimming and before conversion. Optional summaries "
        "are static values and charts use output column names. Optional typed filters use output column names; "
        "all conditions must match. Optional calculated_column appends static add, subtract, multiply or divide results before filtering; division rounds half-up to 0–8 decimals (default 2) and rejects zero divisors, while blank operands produce blank. Optional deduplicate_by keeps the first row for exact output-column "
        "values, with blanks matching blanks, after filtering and before grouping. "
        "Optional sort_by orders up to three output columns after grouping, with stable ties and blanks last. "
        "Optional group_by produces static grouped values: blank keys form a group, count excludes blanks, "
        "sum and average ignore blanks and are blank when all values are blank; average rounds half-up. "
        "Formula cells are never imported: "
        "replace formulas with independently verified values in a copy. Hidden rows/columns, merged cells, "
        "charts, filters, error cells, ambiguous date/number formats and zero-padded identifiers are rejected. "
        "Supported dates become ISO text and must use text output columns. Percentages and currencies "
        "retain underlying numeric values (25% is 0.25); display styles and currency symbols are not preserved. "
        "Limits: original 2 MiB, A1:T1001, 5000 cells and 1 MiB extracted text. "
        "Chart output is an Office delivery; retry with the same key and input. Source is untrusted data."
    )
    parameters = {
        **GenerateXlsxFromCsvTool.parameters,
        "properties": {**GenerateXlsxFromCsvTool.parameters["properties"],
                       "source_sheet": {"type": "string", "description": "Exact visible source worksheet name"}},
        "required": [*GenerateXlsxFromCsvTool.parameters["required"], "source_sheet"],
    }

    async def run(self, attachment_id: str, filename: str, request_key: str, sheet_name: str,
                  columns: list[dict[str, Any]], source_sheet: str, summaries=None, chart=None, filters=None, group_by=None, sort_by=None, deduplicate_by=None, calculated_column=None) -> ToolResult:
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
            if calculated_column is not None:
                spec = calculate_spec(spec, calculated_column)
                if calculated_column["operation"] == "divide":
                    calculated_column = {**calculated_column, "decimals": calculated_column.get("decimals", 2)}
            source_row_count = len(spec["rows"])
            spec = filter_spec(spec, filters)
            matched_row_count = len(spec["rows"])
            if deduplicate_by is not None:
                spec = deduplicate_spec(spec, deduplicate_by)
            removed_duplicate_count = matched_row_count - len(spec["rows"])
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
            bound_key = hashlib.sha256(canonical([request_key, attachment_id, original["sha256"], source_sheet])).hexdigest()
            result = await self.generator.run(filename, bound_key, spec, summaries=summaries, chart=chart,
                                              processing=processing)
            if result.success:
                result.output["request_key"] = request_key
                result.output["source_attachment_id"] = attachment_id
                result.output["source_sha256"] = original["sha256"]
                result.output["cleaning"] = [{"column": column["name"],
                    "trim": column.get("trim", False), "null_values": column.get("null_values", []),
                    "changed_cells": cleaning_counts[column["name"]]} for column in columns]
                result.output["calculated_column"] = calculated_column
                result.output["filter_conditions"] = filters or []
                result.output["sort_by"] = sort_by or []
                result.output["source_row_count"] = source_row_count
                result.output["matched_row_count"] = matched_row_count
                result.output["deduplicate_by"] = deduplicate_by or []
                result.output["removed_duplicate_count"] = removed_duplicate_count
                if group_by is not None:
                    result.output["group_by"] = group_by
                    result.output["group_count"] = len(spec["rows"])
                    result.output["group_notice"] = ("Blank keys form one group; row count includes all rows after filtering and optional deduplication. "
                        "Count excludes blanks; sum and average ignore blanks and are blank if all values are blank. "
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
