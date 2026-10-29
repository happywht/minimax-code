"""Create a verified plain-cell XLSX for the current chat project."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import ValidationError

from ...execution import require_write_access
from ...ipc.protocol import OfficeGenerateParams
from ...office_analysis import AnalysisSpec, Chart, Summary, materialize
from ...office_deliveries import OfficeService
from ...office_process import generate_chart_xlsx
from ...office_xlsx import XlsxSpec, canonical
from .base import Tool, ToolResult


class GenerateTableXlsxTool(Tool):
    name = "generate_table_xlsx"
    description = (
        "Generate XLSX for the current project: plain cells in Office deliveries, "
        "or with an embedded chart in project attachments. "
        "Use only text, number, boolean, or null cells; no formulas are calculated. "
        "Use a new request_key for a new document; reuse the same key and identical input "
        "to safely retry an uncertain result. Cite sources in the surrounding response or cells. "
        "Optional summaries store actual values: count counts nonblank cells, sum ignores blanks, "
        "average ignores blanks and rounds half-up to decimals (default 2); empty average is blank. "
        "Optional bar or line chart is a separate attachment; on uncertain chart result, inspect "
        "attachments before retrying because chart creation is not idempotent. "
        "Business correctness and rendering are not verified."
    )
    parameters = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Plain .xlsx display filename"},
            "request_key": {"type": "string", "description": "Stable 1-128 ASCII letters, digits, _ or -"},
            "summaries": {"type": "array", "items": {"type": "object", "properties": {
                "column": {"type": "string"}, "operation": {"type": "string", "enum": ["sum", "count", "average"]},
                "decimals": {"type": "integer", "minimum": 0, "maximum": 8},
            }, "required": ["column", "operation"], "additionalProperties": False}},
            "chart": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["bar", "line"]},
                "category_column": {"type": "string"}, "value_column": {"type": "string"},
                "title": {"type": "string"},
            }, "required": ["kind", "category_column", "value_column", "title"], "additionalProperties": False},
            "spec": {
                "type": "object",
                "properties": {
                    "sheet_name": {"type": "string"},
                    "columns": {"type": "array", "items": {
                        "type": "object", "properties": {
                            "name": {"type": "string"},
                            "kind": {"type": "string", "enum": ["text", "number", "boolean"]},
                        }, "required": ["name", "kind"], "additionalProperties": False,
                    }},
                    "rows": {"type": "array", "items": {"type": "array", "items": {
                        "anyOf": [{"type": "string"}, {"type": "number"},
                                  {"type": "boolean"}, {"type": "null"}],
                    }}},
                },
                "required": ["sheet_name", "columns", "rows"], "additionalProperties": False,
            },
        },
        "required": ["filename", "request_key", "spec"], "additionalProperties": False,
    }

    def __init__(self, db: Any, session_id: str, project_id: str | None):
        self.db, self.session_id, self.project_id = db, session_id, project_id
        self.service = OfficeService(db, db.path.parent)

    async def run(self, filename: str, request_key: str, spec: dict[str, Any],
                  summaries: list[dict[str, Any]] | None = None, chart: dict[str, Any] | None = None) -> ToolResult:
        try:
            require_write_access()
            if not isinstance(self.project_id, str) or not self.project_id:
                raise ValueError("Current session has no project")
            if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_key):
                raise ValueError("Invalid request key")
            row = await self.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
            if row is None or row["project_id"] != self.project_id:
                raise ValueError("Current session project binding changed")
            original = XlsxSpec.model_validate(spec)
            if summaries is not None and not isinstance(summaries, list):
                raise ValueError("summaries must be a list")
            materialized = materialize(original, [Summary.model_validate(item) for item in summaries or []])
            service_id = await self.service.originals.dao.service_id()
            if chart is not None:
                chart_config = Chart.model_validate(chart)
                source_names = {column.name for column in original.columns}
                if chart_config.category_column not in source_names or chart_config.value_column not in source_names:
                    raise ValueError("Chart columns must belong to the original data")
                analysis = AnalysisSpec(spec=materialized, chart=chart_config, source_rows=len(original.rows))
                # Match the same filename constraints as the plain XLSX route.
                from ...office_xlsx import filename as validate_filename

                validate_filename(filename)
                data, report = await generate_chart_xlsx(analysis)

                async def chunks():
                    yield data

                attachment = await self.service.originals.upload(
                    self.project_id, filename, service_id, chunks(), require_parsed=True,
                    expected_session=self.session_id)
                row = await self.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
                if row is None or row["project_id"] != self.project_id:
                    raise ValueError("Session project binding changed; inspect original project attachments")
                return ToolResult.ok({
                    "attachment_id": attachment["id"], "filename": attachment["original_name"],
                    "version": attachment["version"], "sha256": attachment["sha256"],
                    "validation_scope": report["scope"], "cell_check": report["cell_check"],
                    "chart_reference_check": report["chart_reference_check"],
                    "validation_report": report,
                    "business_quality": "unverified", "render_status": "not_attempted",
                    "notice": "Open the XLSX from project attachments. Summaries are static values, "
                              "not formulas. Chart creation is not idempotent; inspect attachments before retrying.",
                })
            key = "agent_" + hashlib.sha256(canonical([self.session_id, request_key])).hexdigest()
            params = OfficeGenerateParams.model_validate({
                "project_id": self.project_id, "expected_service_id": service_id,
                "request_key": key, "filename": filename, "spec": materialized.model_dump(),
            })
            result = await self.service.generate(params, expected_session=self.session_id)
            row = await self.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
            if row is None or row["project_id"] != self.project_id:
                raise ValueError("Session project binding changed; refresh the original project's deliveries")
            delivery, attachment = result["delivery"], result["attachment"]
            return ToolResult.ok({
                "delivery_id": delivery["id"], "attachment_id": attachment["id"],
                "filename": attachment["original_name"], "version": attachment["version"],
                "sha256": attachment["sha256"], "request_key": request_key,
                "structure_status": delivery["validation_report"]["structure_status"],
                "validation_scope": delivery["validation_report"]["scope"],
                "business_quality": "unverified", "render_status": "not_attempted",
                "notice": "Open this ID in the current project's Office deliveries. "
                          "No formulas, business verification or layout rendering were performed.",
            })
        except ValidationError:
            return ToolResult.fail("Invalid XLSX input; check filename, key, table types and limits")
        except (ValueError, OSError, TimeoutError) as error:
            recovery = ("inspect project attachments before retrying; chart generation is not idempotent"
                        if chart is not None else "if the outcome is uncertain, retry the same key and input")
            return ToolResult.fail(f"{error}; {recovery}")


def register_table_xlsx_tool(registry: Any, db: Any, session_id: str,
                             project_id: str | None) -> None:
    registry.register(GenerateTableXlsxTool(db, session_id, project_id))
