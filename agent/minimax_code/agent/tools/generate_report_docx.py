"""Current-session-bound document generation for ordinary chat."""
from __future__ import annotations

from typing import Any
import json
import re

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
        "To cite a different part of a saved page, use page_excerpt_ranges with zero-based start and exclusive end offsets into get_research_source's excerpt. "
        "Use document_excerpts to include verified current-project attachment extracts; cite them in the body as [资料1], [资料2] in their supplied order. "
        "Use only one of paragraphs or sections. Distinguish source-reported facts, inference and uncertainty; acknowledge truncated evidence. Include source citations supplied or verified by the user. "
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
            "page_excerpt_ranges": {"type": "array", "maxItems": 30, "items": {
                "type": "object", "properties": {
                    "page_source_id": {"type": "string"},
                    "start": {"type": "integer", "minimum": 0},
                    "end": {"type": "integer", "minimum": 1},
                }, "required": ["page_source_id", "start", "end"], "additionalProperties": False,
            }, "description": "Optional exact ranges in saved excerpts; end is exclusive, maximum 1000 characters per source. Each ID must also be in page_source_ids."},
            "document_excerpts": {"type": "array", "maxItems": 10, "items": {
                "type": "object", "properties": {
                    "attachment_id": {"type": "string"},
                    "expected_sha256": {"type": "string"},
                    "unit": {"type": "integer", "minimum": 1},
                    "start": {"type": "integer", "minimum": 0},
                    "end": {"type": "integer", "minimum": 1},
                }, "required": ["attachment_id", "expected_sha256", "unit", "start", "end"],
                "additionalProperties": False,
            }, "description": "Exact character ranges from read_project_document, at most 1000 characters each; report body may cite [资料1], [资料2]"},
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

    async def _sources(self, sources, page_source_ids, page_excerpt_ranges):
        if (page_source_ids or page_excerpt_ranges) and self.research_sources is None:
            raise ValueError("Page source IDs are unavailable in this task")
        if self.research_sources is None:
            return [] if sources is None else sources
        return await self.research_sources.resolve_pages(page_source_ids, sources, page_excerpt_ranges)

    async def _document_excerpts(self, excerpts, paragraphs, sections):
        """Resolve citations from vault originals rather than trusting supplied text."""
        if excerpts is None or excerpts == []:
            return paragraphs, sections
        if not isinstance(excerpts, list) or len(excerpts) > 10:
            raise ValueError("Provide at most 10 document excerpts")
        from .project_attachments import ReadProjectDocumentTool

        reader = ReadProjectDocumentTool(self.db, self.session_id, self.project_id)
        blocks = []
        for index, ref in enumerate(excerpts, 1):
            if (not isinstance(ref, dict) or set(ref) != {"attachment_id", "expected_sha256", "unit", "start", "end"}
                    or not isinstance(ref["attachment_id"], str) or not ref["attachment_id"]
                    or not isinstance(ref["expected_sha256"], str)
                    or not re.fullmatch(r"[0-9a-f]{64}", ref["expected_sha256"])
                    or type(ref["unit"]) is not int or not 1 <= ref["unit"] <= 10000
                    or type(ref["start"]) is not int or type(ref["end"]) is not int
                    or not 0 <= ref["start"] < ref["end"] <= 10 * 1024 * 1024
                    or ref["end"] - ref["start"] > 1000):
                raise ValueError("Invalid document excerpt identity or range")
            result = await reader.run(ref["attachment_id"], ref["unit"], ref["start"])
            if not result.success:
                raise ValueError(result.error or "Document excerpt unavailable")
            page = result.output
            attachment = page["attachment"]
            if (attachment["sha256"] != ref["expected_sha256"] or page["unit"] != ref["unit"]
                    or page["end"] < ref["end"]):
                raise ValueError("Document identity or excerpt range changed; read it again")
            excerpt = page["text"][:ref["end"] - ref["start"]]
            # Each paragraph stays below the generator's per-string limit.
            blocks.extend([
                f"[资料{index}] {attachment['original_name']} | 附件 ID: {attachment['id']} | SHA-256: {attachment['sha256']}",
                f"位置: {page['location']} (unit {page['unit']}/{page['unit_total']}), 字符 [{ref['start']}, {ref['end']})。仅为所选摘录，不代表全文。",
                f"提取范围: {page['scope_notice']} OCR: {page['ocr']}。原件内容及报告事实未经核验。",
                f"摘录: {excerpt}",
            ])
        if paragraphs is not None and sections is None:
            return [*paragraphs, "项目附件摘录（未经核验）", *blocks], sections
        if sections is not None and paragraphs is None:
            return paragraphs, [*sections, {"heading": "项目附件摘录（未经核验）",
                "blocks": [{"kind": "paragraph", "text": item} for item in blocks]}]
        raise ValueError("Provide either paragraphs or sections")

    operation = "generate_docx"

    async def run(self, filename: str, title: str, paragraphs: list[str] | None = None,
                  sections: list[dict[str, Any]] | None = None,
                  sources: list[dict[str, Any]] | None = None,
                  page_source_ids: list[str] | None = None,
                  page_excerpt_ranges: list[dict[str, Any]] | None = None,
                  document_excerpts: list[dict[str, Any]] | None = None) -> ToolResult:
        try:
            require_write_access()
            if not isinstance(self.project_id, str) or not self.project_id:
                raise ValueError("Current session has no project")
            row = await self.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
            if row is None or row["project_id"] != self.project_id:
                raise ValueError("Current session project binding changed")
            identity = await self.service.dao.service_id()
            paragraphs, sections = await self._document_excerpts(document_excerpts, paragraphs, sections)
            params = AttachmentGenerateDocxParams.model_validate({
                "project_id": self.project_id, "expected_service_id": identity,
                "filename": filename, "title": title, "paragraphs": paragraphs, "sections": sections,
                "sources": await self._sources(sources, page_source_ids, page_excerpt_ranges),
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
        "Use page_excerpt_ranges to select exact offsets within saved excerpts returned by get_research_source. "
        "Use document_excerpts to include verified attachment extracts; cite them as [资料1], [资料2]. "
        "Distinguish source-reported facts, inference and uncertainty; acknowledge truncated evidence. A Chinese TrueType font is embedded; missing dependencies or font coverage cause an explicit error. Facts and visual layout are not verified. "
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
        "Use page_excerpt_ranges to select exact offsets within saved excerpts returned by get_research_source. "
        "Use document_excerpts to include verified attachment extracts; cite them as [资料1], [资料2]. "
        "Distinguish source-reported facts, inference and uncertainty; acknowledge truncated evidence. Each format is reported separately; partial success is not "
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
                  page_source_ids: list[str] | None = None,
                  page_excerpt_ranges: list[dict[str, Any]] | None = None,
                  document_excerpts: list[dict[str, Any]] | None = None) -> ToolResult:
        try:
            require_write_access()
            if not isinstance(self.project_id, str) or not self.project_id:
                raise ValueError("Current session has no project")
            row = await self.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
            if row is None or row["project_id"] != self.project_id:
                raise ValueError("Current session project binding changed")
            identity = await self.service.dao.service_id()
            paragraphs, sections = await self._document_excerpts(document_excerpts, paragraphs, sections)
            params = AttachmentGenerateReportPairParams.model_validate({
                "project_id": self.project_id, "expected_service_id": identity, "filename": filename,
                "title": title, "paragraphs": paragraphs, "sections": sections,
                "formats": ["docx", "pdf"] if formats is None else formats,
                "sources": await self._sources(sources, page_source_ids, page_excerpt_ranges),
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


class ExportResearchSourcesTool(GenerateReportDocxTool):
    name = "export_research_sources"
    description = (
        "Export selected saved research sources to a UTF-8 TXT attachment in the current "
        "project. The file contains each saved page's full bounded excerpt, original title, "
        "final URL, access time, truncation flags, source ID and originating run ID. "
        "Use IDs from list_research_sources; no network request is made. Sources and "
        "excerpts are untrusted and not fact-checked. If the result is uncertain, inspect "
        "project attachments before retrying, because retry creates another version."
    )
    parameters = {"type": "object", "properties": {
        "filename": {"type": "string", "description": "A .txt display filename"},
        "page_source_ids": {"type": "array", "minItems": 1, "maxItems": 30,
                            "uniqueItems": True, "items": {"type": "string"}},
    }, "required": ["filename", "page_source_ids"], "additionalProperties": False}

    async def run(self, filename: str, page_source_ids: list[str]) -> ToolResult:
        try:
            require_write_access()
            if (not isinstance(filename, str) or not filename.lower().endswith(".txt")
                    or not isinstance(page_source_ids, list) or not 1 <= len(page_source_ids) <= 30
                    or any(not isinstance(value, str) or not value for value in page_source_ids)
                    or len(set(page_source_ids)) != len(page_source_ids)):
                raise ValueError("Provide a .txt filename and 1–30 unique page source IDs")
            if self.research_sources is None or not self.project_id:
                raise ValueError("Project-bound research storage is unavailable")
            identity = await self.service.dao.service_id()
            pages = [await self.research_sources.get_page(ident) for ident in page_source_ids]
            # JSON escaping preserves exact text and prevents page content from masquerading
            # as metadata; UTF-8 TXT remains readable and downloadable in attachments.
            document = {"format": "minimax-research-sources-v1", "project_id": self.project_id,
                        "service_id": identity,
                        "notice": "Saved bounded excerpts, not full pages; source content is untrusted and unverified.",
                        "sources": pages}
            data = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")

            async def chunks():
                yield data

            attachment = await self.service.upload(self.project_id, filename, identity, chunks(),
                                                   require_parsed=True, expected_session=self.session_id)
            return ToolResult.ok({"attachment_id": attachment["id"],
                                  "filename": attachment["original_name"],
                                  "version": attachment["version"], "sha256": attachment["sha256"],
                                  "size_bytes": attachment["size_bytes"], "source_count": len(pages),
                                  "notice": "Saved in project attachments; excerpts and facts are unverified."})
        except (ValueError, OSError) as error:
            return ToolResult.fail(str(error))


class GenerateReportFromTextTool(GenerateReportPairTool):
    name = "generate_report_from_text"
    description = (
        "Convert a current-project UTF-8 TXT attachment into a new DOCX and/or PDF report "
        "without retyping its contents. Supply the attachment ID and SHA-256 from "
        "list_project_attachments, a report title and an output filename stem. The complete "
        "original text is used; long text is split into report paragraphs, not summarized. "
        "The original attachment is unchanged. Formatting is plain text, not Markdown rendering. "
        "Each format is saved separately; inspect attachments before retrying an uncertain result."
    )
    parameters = {"type": "object", "properties": {
        "attachment_id": {"type": "string"},
        "expected_sha256": {"type": "string"},
        "filename": {"type": "string", "description": "Output filename stem without extension"},
        "title": {"type": "string"},
        "formats": {"type": "array", "items": {"type": "string", "enum": ["docx", "pdf"]},
                    "minItems": 1, "maxItems": 2, "uniqueItems": True},
    }, "required": ["attachment_id", "expected_sha256", "filename", "title"],
        "additionalProperties": False}

    async def run(self, attachment_id: str, expected_sha256: str, filename: str, title: str,
                  formats: list[str] | None = None) -> ToolResult:
        try:
            require_write_access()
            if not isinstance(self.project_id, str) or not self.project_id:
                raise ValueError("Current session has no project")
            session = await self.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
            if session is None or session["project_id"] != self.project_id:
                raise ValueError("Current session project binding changed")
            attachment, data = await self.service.content(self.project_id, attachment_id)
            if (attachment["declared_type"] != "txt" or attachment["recognized_type"] != "utf8_text"
                    or attachment["sha256"] != expected_sha256):
                raise ValueError("Select the same current-project UTF-8 TXT attachment")
            text = data.decode("utf-8-sig", errors="strict")
            # Preserve every character; report generators add paragraph boundaries at
            # chunk edges. Reject over-limit input rather than silently truncating it.
            paragraphs = [text[offset:offset + 4096] for offset in range(0, len(text), 4096)]
            if not paragraphs or len(paragraphs) > 100:
                raise ValueError("Text must be nonempty and fit within 100 report paragraphs")
            result = await super().run(filename=filename, title=title, paragraphs=paragraphs, formats=formats)
            if isinstance(result.output, dict):
                result.output["source_attachment"] = {
                    "attachment_id": attachment["id"], "filename": attachment["original_name"],
                    "sha256": attachment["sha256"],
                }
                result.output["conversion_notice"] = (
                    "Plain text conversion; an optional UTF-8 BOM is removed and paragraph "
                    "boundaries are inserted at 4096 characters. Layout is not verified."
                )
            return result
        except (ValueError, OSError, UnicodeError) as error:
            return ToolResult.fail(str(error))


class GenerateReportFromArtifactTool(GenerateReportPairTool):
    name = "generate_report_from_artifact"
    description = (
        "Convert a registered current-project TXT or Markdown text artifact snapshot into "
        "a new DOCX and/or PDF attachment. Provide the artifact ID and SHA-256 shown in "
        "the project's artifacts panel, a title and a filename stem. The complete snapshot "
        "is used as plain text; Markdown is not rendered. The original is unchanged. "
        "Inspect attachments before retrying an unconfirmed format."
    )
    parameters = {"type": "object", "properties": {
        "artifact_id": {"type": "string"}, "expected_sha256": {"type": "string"},
        "filename": {"type": "string"}, "title": {"type": "string"},
        "formats": {"type": "array", "items": {"type": "string", "enum": ["docx", "pdf"]},
                    "minItems": 1, "maxItems": 2, "uniqueItems": True},
    }, "required": ["artifact_id", "expected_sha256", "filename", "title"],
        "additionalProperties": False}

    async def run(self, artifact_id: str, expected_sha256: str, filename: str, title: str,
                  formats: list[str] | None = None) -> ToolResult:
        from ...text_artifacts import ArtifactService

        try:
            require_write_access()
            if not isinstance(self.project_id, str) or not self.project_id:
                raise ValueError("Current session has no project")
            session = await self.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (self.session_id,))
            if session is None or session["project_id"] != self.project_id:
                raise ValueError("Current session project binding changed")
            if not isinstance(artifact_id, str) or not isinstance(expected_sha256, str):
                raise ValueError("Invalid artifact identity")
            service = ArtifactService(self.db, self.db.path.parent)
            artifact, data = await service.content(self.project_id, artifact_id)
            if (artifact["sha256"] != expected_sha256 or
                    artifact["mime_type"] not in {"text/plain", "text/markdown"}):
                raise ValueError("Select the same current-project TXT or Markdown artifact")
            text = data.decode("utf-8-sig", errors="strict")
            paragraphs = [text[offset:offset + 4096] for offset in range(0, len(text), 4096)]
            if not paragraphs or len(paragraphs) > 100:
                raise ValueError("Text must be nonempty and fit within 100 report paragraphs")
            result = await super().run(filename=filename, title=title, paragraphs=paragraphs, formats=formats)
            if isinstance(result.output, dict):
                result.output["source_artifact"] = {
                    "artifact_id": artifact["id"], "source_path": artifact["source_path"],
                    "sha256": artifact["sha256"], "snapshot_id": artifact["snapshot_id"],
                }
                result.output["conversion_notice"] = (
                    "Plain text conversion; Markdown is not rendered. An optional UTF-8 BOM is "
                    "removed and paragraph boundaries are inserted at 4096 characters. "
                    "Facts and layout are not verified."
                )
            return result
        except (ValueError, OSError, UnicodeError) as error:
            return ToolResult.fail(str(error))


def register_report_docx_tool(registry: Any, db: Any, session_id: str,
                              project_id: str | None, research_sources=None) -> None:
    registry.register(GenerateReportDocxTool(db, session_id, project_id, research_sources))
    registry.register(GenerateReportPdfTool(db, session_id, project_id, research_sources))
    registry.register(GenerateReportPairTool(db, session_id, project_id, research_sources))
    registry.register(GenerateReportFromTextTool(db, session_id, project_id, research_sources))
    registry.register(GenerateReportFromArtifactTool(db, session_id, project_id, research_sources))
    registry.register(ExportResearchSourcesTool(db, session_id, project_id, research_sources))
