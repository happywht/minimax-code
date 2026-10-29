"""Current-session-bound document generation for ordinary chat."""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ...attachments import AttachmentService
from ...execution import require_write_access
from ...ipc.protocol import AttachmentGenerateDocxParams
from .base import Tool, ToolResult


class GenerateReportDocxTool(Tool):
    name = "generate_report_docx"
    description = (
        "Create and register a DOCX report in the current project's attachments from a title "
        "and either paragraphs or sections containing ordered paragraph/table blocks. "
        "Use only one of paragraphs or sections. Include source citations supplied or verified by the user. "
        "The file is not rendered or fact-checked. An ambiguous failure may have committed: "
        "check project attachments before retrying."
    )
    parameters = {
        "type": "object",
        "properties": {
            "filename": {"type": "string", "description": "A .docx display filename"},
            "title": {"type": "string"},
            "paragraphs": {"type": "array", "items": {"type": "string"}},
            "sections": {"type": "array", "items": {
                "type": "object", "properties": {
                    "heading": {"type": "string"},
                    "blocks": {"type": "array", "items": {
                        "type": "object", "properties": {
                            "kind": {"type": "string", "enum": ["paragraph", "table"]},
                            "text": {"type": "string"},
                            "rows": {"type": "array", "items": {
                                "type": "array", "items": {"type": "string"},
                            }},
                        }, "required": ["kind"], "additionalProperties": False,
                    }},
                }, "required": ["heading", "blocks"], "additionalProperties": False,
            }},
        },
        "required": ["filename", "title"],
        "anyOf": [{"required": ["paragraphs"]}, {"required": ["sections"]}],
        "additionalProperties": False,
    }

    def __init__(self, db: Any, session_id: str, project_id: str | None):
        self.db, self.session_id, self.project_id = db, session_id, project_id
        self.service = AttachmentService(db, db.path.parent)

    async def run(self, filename: str, title: str, paragraphs: list[str] | None = None,
                  sections: list[dict[str, Any]] | None = None) -> ToolResult:
        try:
            require_write_access()
            if not isinstance(self.project_id, str) or not self.project_id:
                raise ValueError("Current session has no project")
            row = await self.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
            if row is None or row["project_id"] != self.project_id:
                raise ValueError("Current session project binding changed")
            identity = await self.service.dao.service_id()
            params = AttachmentGenerateDocxParams.model_validate({
                "project_id": self.project_id, "expected_service_id": identity,
                "filename": filename, "title": title, "paragraphs": paragraphs, "sections": sections,
            })
            result = await self.service.generate_docx(**params.model_dump(), expected_session=self.session_id)
            attachment = result["attachment"]
            return ToolResult.ok({
                "attachment_id": attachment["id"], "filename": attachment["original_name"],
                "version": attachment["version"], "sha256": attachment["sha256"],
                "size_bytes": attachment["size_bytes"], "parser_status": attachment["parser_status"],
                "quality_status": "unverified", "render_status": "not_attempted",
                "notice": "Available in project attachments; facts, sources and layout are not verified.",
            })
        except (ValueError, OSError, ValidationError) as error:
            return ToolResult.fail(str(error))


def register_report_docx_tool(registry: Any, db: Any, session_id: str,
                              project_id: str | None) -> None:
    registry.register(GenerateReportDocxTool(db, session_id, project_id))
