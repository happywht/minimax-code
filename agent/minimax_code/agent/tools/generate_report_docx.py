"""Current-session-bound document generation for ordinary chat."""
from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ...attachments import AttachmentService
from ...execution import require_write_access
from ...ipc.protocol import AttachmentGenerateDocxParams, AttachmentGenerateReportPairParams
from .base import Tool, ToolResult


class GenerateReportDocxTool(Tool):
    name = "generate_report_docx"
    description = (
        "Create and register a DOCX report in the current project's attachments from a title "
        "and either paragraphs or sections containing ordered paragraph/table blocks. "
        "Provide sources as title, HTTP(S) URL, and optional actual access time; cite them in body as [1], [2] in source order. Never invent sources or access times. "
        "Pass page_source_ids returned by read_public_page or list_research_sources for this project; actual page metadata and bounded excerpt are inserted automatically. Manual sources come first, then page_source_ids in order. "
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
            "sources": {"type": "array", "maxItems": 30, "items": {"type": "object", "properties": {
                "title": {"type": "string"}, "url": {"type": "string", "description": "HTTP(S) URL"},
                "accessed_at": {"type": "string", "description": "Optional ISO date or timezone-aware timestamp"},
            }, "required": ["title", "url"], "additionalProperties": False}},
            "page_source_ids": {"type": "array", "maxItems": 30, "uniqueItems": True,
                                "items": {"type": "string"}, "description": "IDs of pages read in this project on this service"},
            "sections": {"type": "array", "items": {
                "type": "object", "properties": {
                    "heading": {"type": "string"},
                    "blocks": {"type": "array", "items": {
                        "type": "object", "properties": {
                            "kind": {"type": "string", "enum": ["paragraph", "table"]},
                            "text": {"type": "string"},
                            "repeat_header": {"type": "boolean", "description": "For a table, repeat its first row on subsequent pages"},
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

    def __init__(self, db: Any, session_id: str, project_id: str | None, research_sources=None):
        self.db, self.session_id, self.project_id = db, session_id, project_id
        self.research_sources = research_sources
        self.service = AttachmentService(db, db.path.parent)

    async def _sources(self, sources, page_source_ids):
        if page_source_ids is not None and page_source_ids != [] and self.research_sources is None:
            raise ValueError("Page source IDs are unavailable in this task")
        if self.research_sources is None:
            return [] if sources is None else sources
        return await self.research_sources.resolve_pages(page_source_ids, sources)

    operation = "generate_docx"

    async def run(self, filename: str, title: str, paragraphs: list[str] | None = None,
                  sections: list[dict[str, Any]] | None = None,
                  sources: list[dict[str, Any]] | None = None,
                  page_source_ids: list[str] | None = None) -> ToolResult:
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
                "sources": await self._sources(sources, page_source_ids),
            })
            result = await getattr(self.service, self.operation)(**params.model_dump(), expected_session=self.session_id)
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



class GenerateReportPdfTool(GenerateReportDocxTool):
    name = "generate_report_pdf"
    operation = "generate_pdf"
    description = (
        "Create and register a PDF report in the current project using a title and either "
        "paragraphs or structured sections with ordered paragraph/table blocks. Include source "
        "citations where available in sources and cite them in body as [1], [2] in source order; do not invent sources or access times. "
        "Pass project page_source_ids from read_public_page or list_research_sources for page metadata and excerpts, ordered after manual sources. "
        "A Chinese TrueType font is embedded; missing dependencies or font coverage cause an explicit error. Facts and visual layout are not verified. "
        "If the outcome is uncertain, inspect project attachments before retrying."
    )
    parameters = {**GenerateReportDocxTool.parameters, "properties": {
        **GenerateReportDocxTool.parameters["properties"],
        "filename": {"type": "string", "description": "A .pdf display filename"},
    }}


class GenerateReportPairTool(GenerateReportDocxTool):
    name = "generate_report_pair"
    description = (
        "Generate DOCX and PDF from the same title and paragraphs or sections, saving each in "
        "current-project attachments. Provide known sources with actual URLs and optional access times, and cite them as [1], [2] in source order; never invent them. "
        "Use project page_source_ids from read_public_page or list_research_sources to append actual page metadata and excerpts after manual sources. "
        "Each format is reported separately; partial success is not "
        "full success. Check project attachments before retrying an unconfirmed format, then request "
        "only that format. Neither factual accuracy nor visual rendering is verified."
    )
    parameters = {**GenerateReportDocxTool.parameters, "properties": {
        **GenerateReportDocxTool.parameters["properties"],
        "filename": {"type": "string", "description": "Common filename stem without .docx or .pdf"},
        "formats": {"type": "array", "items": {"type": "string", "enum": ["docx", "pdf"]},
                    "minItems": 1, "maxItems": 2, "uniqueItems": True},
    }}

    async def run(self, filename: str, title: str, paragraphs: list[str] | None = None,
                  sections: list[dict[str, Any]] | None = None, formats: list[str] | None = None,
                  sources: list[dict[str, Any]] | None = None,
                  page_source_ids: list[str] | None = None) -> ToolResult:
        try:
            require_write_access()
            if not isinstance(self.project_id, str) or not self.project_id:
                raise ValueError("Current session has no project")
            row = await self.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
            if row is None or row["project_id"] != self.project_id:
                raise ValueError("Current session project binding changed")
            identity = await self.service.dao.service_id()
            params = AttachmentGenerateReportPairParams.model_validate({
                "project_id": self.project_id, "expected_service_id": identity, "filename": filename,
                "title": title, "paragraphs": paragraphs, "sections": sections,
                "formats": ["docx", "pdf"] if formats is None else formats,
                "sources": await self._sources(sources, page_source_ids),
            })
            result = await self.service.generate_report_pair(**params.model_dump(), expected_session=self.session_id)
            row = await self.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
            if row is None or row["project_id"] != self.project_id:
                raise ValueError("Session project changed; inspect the original project's attachments")
            for item in result["results"]:
                if item["status"] == "saved":
                    attachment = item.pop("attachment")
                    item.pop("preview_text", None)
                    item.update({"attachment_id": attachment["id"], "filename": attachment["original_name"],
                                 "version": attachment["version"], "sha256": attachment["sha256"]})
            result["notice"] = ("Inspect project attachments for unconfirmed results before retrying only those formats; "
                                "generation is not idempotent. Facts and layout remain unverified.")
            if result["status"] != "complete":
                return ToolResult.fail("Not all requested formats were confirmed saved", output=result)
            return ToolResult.ok(result)
        except (ValueError, OSError, ValidationError) as error:
            return ToolResult.fail(str(error))

def register_report_docx_tool(registry: Any, db: Any, session_id: str,
                              project_id: str | None, research_sources=None) -> None:
    registry.register(GenerateReportDocxTool(db, session_id, project_id, research_sources))
    registry.register(GenerateReportPdfTool(db, session_id, project_id, research_sources))
    registry.register(GenerateReportPairTool(db, session_id, project_id, research_sources))
