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
    server.register("attachment.generate_report_pair", handler(AttachmentGeneratePairWithResearchParams, "generate_report_pair"))
    server.register("attachment.generate_pdf", handler(AttachmentGenerateWithResearchParams, "generate_pdf"))
    server.register("attachment.generate_docx", handler(AttachmentGenerateWithResearchParams, "generate_docx"))
    server.register("attachment.list", handler(AttachmentListParams, "list"))
    server.register("attachment.get", handler(AttachmentGetParams, "get"))
