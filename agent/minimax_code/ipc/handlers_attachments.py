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
    AttachmentListParams,
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
                    session_id = values.pop("session_id")
                    if source_ids:
                        if not session_id:
                            raise ValueError("A project-bound session is required for research sources")
                        from ..agent.tools.research_sources import ResearchSources
                        from .host_session_gate import require_host_session
                        await require_host_session(session_id)
                        research = ResearchSources(service.dao.db, session_id, values["project_id"])
                        if await research._binding() != values["expected_service_id"]:
                            raise ValueError("Research service identity changed")
                        values["sources"] = await research.resolve_pages(source_ids, values["sources"])
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
    async def extract_xlsx(params, ctx):
        try:
            from ..attachment_parser import extract_xlsx as extract_sheet
            from ..agent.tools.generate_xlsx_from_xlsx import _MESSAGES
            from .host_session_gate import require_host_session
            request = AttachmentExtractXlsxParams.model_validate(params)
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
            result = await extract_sheet(data, request.sheet_name)
            if not result["ok"]:
                raise ValueError(_MESSAGES.get(result["reason"], "Workbook is unsupported or parser unavailable"))
            await check_scope()
            await ctx.reply({**result, "project_id": request.project_id, "session_id": request.session_id,
                "service_id": request.expected_service_id, "attachment_id": request.attachment_id,
                "sha256": request.expected_sha256, "sheet_name": request.sheet_name})
        except ValidationError:
            await ctx.reply_error(INVALID_PARAMS, "Invalid XLSX extraction parameters")
        except (ValueError, OSError) as exc:
            await ctx.reply_error(-32000, str(exc))
        except Exception:
            logger.exception("attachment.extract_xlsx failed")
            await ctx.reply_error(INTERNAL_ERROR, "XLSX extraction failed")

    server.register("attachment.extract_xlsx", extract_xlsx)
    server.register("attachment.generate_report_pair", handler(AttachmentGeneratePairWithResearchParams, "generate_report_pair"))
    server.register("attachment.generate_pdf", handler(AttachmentGenerateWithResearchParams, "generate_pdf"))
    server.register("attachment.generate_docx", handler(AttachmentGenerateWithResearchParams, "generate_docx"))
    server.register("attachment.list", handler(AttachmentListParams, "list"))
    server.register("attachment.get", handler(AttachmentGetParams, "get"))
