"""Create a verified plain-cell XLSX for the current chat project."""
from __future__ import annotations

import hashlib
import re
from typing import Any

from pydantic import ValidationError

from ...execution import require_write_access
from ...ipc.protocol import OfficeGenerateParams
from ...office_deliveries import OfficeService
from ...office_xlsx import canonical
from .base import Tool, ToolResult


class GenerateTableXlsxTool(Tool):
    name = "generate_table_xlsx"
    description = (
        "Generate a verified plain-cell XLSX in the current project's Office deliveries. "
        "Use only text, number, boolean, or null cells; no formulas are calculated. "
        "Use a new request_key for a new document; reuse the same key and identical input "
        "to safely retry an uncertain result. Cite sources in the surrounding response or cells. "
        "Full cell values are checked, but business correctness and rendering are not verified."
    )
    parameters = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "Plain .xlsx display filename"},
            "request_key": {"type": "string", "description": "Stable 1-128 ASCII letters, digits, _ or -"},
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

    async def run(self, filename: str, request_key: str, spec: dict[str, Any]) -> ToolResult:
        try:
            require_write_access()
            if not isinstance(self.project_id, str) or not self.project_id:
                raise ValueError("Current session has no project")
            if not isinstance(request_key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", request_key):
                raise ValueError("Invalid request key")
            row = await self.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
            if row is None or row["project_id"] != self.project_id:
                raise ValueError("Current session project binding changed")
            service_id = await self.service.originals.dao.service_id()
            key = "agent_" + hashlib.sha256(canonical([self.session_id, request_key])).hexdigest()
            params = OfficeGenerateParams.model_validate({
                "project_id": self.project_id, "expected_service_id": service_id,
                "request_key": key, "filename": filename, "spec": spec,
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
            return ToolResult.fail(f"{error}; if the outcome is uncertain, retry the same key and input")


def register_table_xlsx_tool(registry: Any, db: Any, session_id: str,
                             project_id: str | None) -> None:
    registry.register(GenerateTableXlsxTool(db, session_id, project_id))
