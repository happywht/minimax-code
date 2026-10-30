"""Attachment metadata reads; originals enter only via bounded raw HTTP uploads."""
from __future__ import annotations

import logging

from pydantic import ValidationError

from ..attachments import get_attachment_service
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    AttachmentGenerateWithResearchParams,
    AttachmentGeneratePairWithResearchParams,
    AttachmentGetParams,
    AttachmentExtractXlsxParams,
    AttachmentXlsxParams,
    AttachmentListParams,
    AttachmentReadDocumentParams,
    AttachmentOcrParams,
    AttachmentPdfOcrParams,
)

logger = logging.getLogger(__name__)


def register_attachment_handlers(server):
    def handler(model, operation):
        async def handle(params, ctx):
            try:
                values = model.model_validate(params).model_dump()
                service = await get_attachment_service()
                if operation.startswith("generate_"):
                    source_ids = values.pop("page_source_ids")
                    excerpt_ranges = values.pop("page_excerpt_ranges")
                    document_excerpts = values.pop("document_excerpts")
                    if excerpt_ranges and not source_ids:
                        raise ValueError("Excerpt ranges require page source IDs")
                    session_id = values.pop("session_id")
                    if document_excerpts:
                        if not session_id:
                            raise ValueError("A project-bound session is required for document excerpts")
                        from ..agent.tools.generate_report_docx import GenerateReportDocxTool
                        from .host_session_gate import require_host_session
                        await require_host_session(session_id)
                        if await service.dao.service_id() != values["expected_service_id"]:
                            raise ValueError("Attachment service identity changed")
                        resolver = GenerateReportDocxTool(service.dao.db, session_id, values["project_id"])
                        values["paragraphs"], values["sections"] = await resolver._document_excerpts(
                            document_excerpts, values["paragraphs"], values["sections"])
                        if await service.dao.service_id() != values["expected_service_id"]:
                            raise ValueError("Attachment service identity changed")
                        await require_host_session(session_id)
                        values["expected_session"] = session_id
                    if source_ids:
                        if not session_id:
                            raise ValueError("A project-bound session is required for research sources")
                        from ..agent.tools.research_sources import ResearchSources
                        from .host_session_gate import require_host_session
                        await require_host_session(session_id)
                        research = ResearchSources(service.dao.db, session_id, values["project_id"])
                        if await research._binding() != values["expected_service_id"]:
                            raise ValueError("Research service identity changed")
                        values["sources"] = await research.resolve_pages(source_ids, values["sources"], excerpt_ranges)
                        if await research._binding() != values["expected_service_id"]:
                            raise ValueError("Research service or project binding changed")
                        await require_host_session(session_id)
                        values["expected_session"] = session_id
                await ctx.reply(await getattr(service, operation)(**values))
            except ValidationError:
                await ctx.reply_error(INVALID_PARAMS, "Invalid attachment parameters")
            except (ValueError, OSError) as exc:
                await ctx.reply_error(-32000, str(exc))
            except Exception:
                logger.exception("attachment.%s failed", operation)
                await ctx.reply_error(INTERNAL_ERROR, "Attachment storage operation failed")
        return handle
    async def xlsx_operation(params, ctx, *, listing=False):
        try:
            from ..attachment_parser import extract_xlsx as extract_sheet, list_xlsx_sheets
            from ..agent.tools.generate_xlsx_from_xlsx import _MESSAGES
            from .host_session_gate import require_host_session
            request = (AttachmentXlsxParams if listing else AttachmentExtractXlsxParams).model_validate(params)
            service = await get_attachment_service()
            async def check_scope():
                await require_host_session(request.session_id)
                session = await service.dao.db.fetchone("SELECT project_id FROM sessions WHERE id=?", (request.session_id,))
                if not session or session["project_id"] != request.project_id or await service.dao.service_id() != request.expected_service_id:
                    raise ValueError("Session, project or service binding changed")
            await check_scope()
            metadata = await service.dao.get(request.project_id, request.attachment_id)
            if metadata["declared_type"] != "xlsx" or metadata["size_bytes"] > 2 * 1024 * 1024 or metadata["sha256"] != request.expected_sha256:
                raise ValueError("Select the same XLSX attachment of at most 2 MiB")
            original, data = await service.content(request.project_id, request.attachment_id)
            if original["recognized_type"] != "xlsx" or original["sha256"] != request.expected_sha256:
                raise ValueError("XLSX attachment identity changed")
            result = await list_xlsx_sheets(data) if listing else await extract_sheet(data, request.sheet_name, use_formula_cache=request.use_formula_cache)
            if not result["ok"]:
                messages = {"formula_cache_missing": "A formula has a missing or empty cached value; no sheet was imported",
                            "formula_cache_type": "A formula cache has an unsupported type; no sheet was imported"}
                raise ValueError(messages.get(result["reason"], _MESSAGES.get(result["reason"], "Workbook is unsupported or parser unavailable")))
            await check_scope()
            await ctx.reply({**result, "project_id": request.project_id, "session_id": request.session_id,
                "service_id": request.expected_service_id, "attachment_id": request.attachment_id,
                "sha256": request.expected_sha256,
                **({} if listing else {"sheet_name": request.sheet_name, "use_formula_cache": request.use_formula_cache})})
        except ValidationError:
            await ctx.reply_error(INVALID_PARAMS, "Invalid XLSX extraction parameters")
        except (ValueError, OSError) as exc:
            await ctx.reply_error(-32000, str(exc))
        except Exception:
            logger.exception("attachment.extract_xlsx failed")
            await ctx.reply_error(INTERNAL_ERROR, "XLSX extraction failed")

    async def extract_xlsx(params, ctx):
        await xlsx_operation(params, ctx)

    async def list_xlsx_sheets(params, ctx):
        await xlsx_operation(params, ctx, listing=True)

    async def read_document(params, ctx):
        try:
            from ..attachment_parser import read_document_segment
            from .host_session_gate import require_host_session

            request = AttachmentReadDocumentParams.model_validate(params)
            service = await get_attachment_service()

            async def check_scope():
                await require_host_session(request.session_id)
                session = await service.dao.db.fetchone(
                    "SELECT project_id FROM sessions WHERE id=?", (request.session_id,))
                if (not session or session["project_id"] != request.project_id
                        or await service.dao.service_id() != request.expected_service_id):
                    raise ValueError("Session, project or service binding changed")

            await check_scope()
            row, data = await service.content(request.project_id, request.attachment_id)
            kind = row["recognized_type"]
            if row["sha256"] != request.expected_sha256:
                raise ValueError("Attachment identity changed")
            if row["declared_type"] in {"txt", "csv"} and kind == "utf8_text":
                try:
                    text = data.decode("utf-8", errors="strict")
                except UnicodeDecodeError as exc:
                    raise ValueError("Text must be valid UTF-8") from exc
                if "\x00" in text or request.unit != 1 or request.offset > len(text):
                    raise ValueError("Invalid text or cursor outside this document")
                end = min(request.offset + 4000, len(text))
                truncated = end < len(text)
                result = {"ok": True, "kind": row["declared_type"], "unit": 1, "unit_total": 1,
                          "location": "text", "start": request.offset, "end": end,
                          "text": text[request.offset:end], "truncated": truncated,
                          "next_cursor": {"unit": 1, "offset": end} if truncated else None,
                          "status": "text" if text else "empty", "ocr": "not_attempted"}
            elif kind in {"pdf", "docx"} and row["declared_type"] == kind:
                result = await read_document_segment(data, kind, request.unit, request.offset)
            else:
                raise ValueError("Select a matching PDF, DOCX, UTF-8 TXT or CSV attachment")
            if not result["ok"]:
                messages = {
                    "missing_dependency": "Document parser dependency is unavailable; install the attachments extra",
                    "parser_timeout": "Document parser timed out",
                    "queue_timeout": "Document parser is busy; try again",
                    "limits_unavailable": "Document parser resource limits are unavailable",
                    "cursor_out_of_range": "Cursor is outside this document",
                    "text_limit": "This page or paragraph exceeds the text limit",
                }
                raise ValueError(messages.get(result["reason"], "Document cannot be safely parsed"))
            await check_scope()
            await ctx.reply({**result, "project_id": request.project_id, "session_id": request.session_id,
                             "service_id": request.expected_service_id, "attachment_id": request.attachment_id,
                             "sha256": request.expected_sha256})
        except ValidationError:
            await ctx.reply_error(INVALID_PARAMS, "Invalid document reading parameters")
        except (ValueError, OSError) as exc:
            await ctx.reply_error(-32000, str(exc))
        except Exception:
            logger.exception("attachment.read_document failed")
            await ctx.reply_error(INTERNAL_ERROR, "Document reading failed")

    async def ocr_image(params, ctx, *, pdf=False):
        try:
            from ..attachment_parser import ocr_image as recognize, ocr_pdf_page
            from .host_session_gate import require_host_session

            request = (AttachmentPdfOcrParams if pdf else AttachmentOcrParams).model_validate(params)
            service = await get_attachment_service()

            async def check_scope():
                await require_host_session(request.session_id)
                session = await service.dao.db.fetchone(
                    "SELECT project_id FROM sessions WHERE id=?", (request.session_id,))
                if (not session or session["project_id"] != request.project_id
                        or await service.dao.service_id() != request.expected_service_id):
                    raise ValueError("Session, project or service binding changed")

            await check_scope()
            row, data = await service.content(request.project_id, request.attachment_id)
            kind = row["recognized_type"]
            if (kind not in ({"pdf"} if pdf else {"png", "jpeg"}) or row["declared_type"] != kind
                    or row["sha256"] != request.expected_sha256):
                raise ValueError("Select the same PDF attachment" if pdf else "Select the same PNG or JPEG attachment")
            result = await ocr_pdf_page(data, request.page, request.language) if pdf else await recognize(data, kind, request.language)
            if not result["ok"]:
                messages = {
                    "missing_dependency": "Local OCR requires Pillow and tesserocr; PDF OCR also requires PyMuPDF. Install locally and restart the service",
                    "invalid_pdf": "PDF cannot be rendered safely",
                    "pdf_limit": "PDF page count, size or rendered pixel limit exceeded, or PDF requires a password",
                    "page_out_of_range": "Requested physical page does not exist",
                    "missing_language": "Local OCR initialization failed; check Tesseract and the selected language data",
                    "invalid_image": "Image cannot be decoded safely",
                    "image_limit": "Image exceeds 12 million pixels or contains multiple frames",
                    "parser_timeout": "Local OCR timed out (15 seconds)",
                    "queue_timeout": "Local parser is busy; try again",
                    "limits_unavailable": "Local parser resource limits are unavailable",
                    "output_limit": "Local OCR output exceeded its limit",
                }
                raise ValueError(messages.get(result["reason"], "Local OCR failed safely"))
            await check_scope()
            await ctx.reply({**result, "project_id": request.project_id, "session_id": request.session_id,
                             "service_id": request.expected_service_id, "attachment_id": request.attachment_id,
                             "sha256": request.expected_sha256, "language": request.language,
                             "quality": "unverified", **({"page": request.page} if pdf else {})})
        except ValidationError:
            await ctx.reply_error(INVALID_PARAMS, "Invalid OCR parameters")
        except (ValueError, OSError) as exc:
            await ctx.reply_error(-32000, str(exc))
        except Exception:
            logger.exception("attachment.ocr_image failed")
            await ctx.reply_error(INTERNAL_ERROR, "Local OCR failed")

    async def ocr_pdf(params, ctx):
        await ocr_image(params, ctx, pdf=True)

    server.register("attachment.ocr_pdf_page", ocr_pdf)
    server.register("attachment.ocr_image", ocr_image)
    server.register("attachment.read_document", read_document)
    server.register("attachment.extract_xlsx", extract_xlsx)
    server.register("attachment.list_xlsx_sheets", list_xlsx_sheets)
    server.register("attachment.generate_report_pair", handler(AttachmentGeneratePairWithResearchParams, "generate_report_pair"))
    server.register("attachment.generate_pdf", handler(AttachmentGenerateWithResearchParams, "generate_pdf"))
    server.register("attachment.generate_docx", handler(AttachmentGenerateWithResearchParams, "generate_docx"))
    server.register("attachment.list", handler(AttachmentListParams, "list"))
    server.register("attachment.get", handler(AttachmentGetParams, "get"))
