"""Attachment metadata reads; originals enter only via bounded raw HTTP uploads."""
from __future__ import annotations

import logging

from pydantic import ValidationError

from ..attachments import get_attachment_service
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    AttachmentGenerateDocxParams,
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
                await ctx.reply(await getattr(service, operation)(**values))
            except ValidationError:
                await ctx.reply_error(INVALID_PARAMS, "Invalid attachment parameters")
            except (ValueError, OSError) as exc:
                await ctx.reply_error(-32000, str(exc))
            except Exception:
                logger.exception("attachment.%s failed", operation)
                await ctx.reply_error(INTERNAL_ERROR, "Attachment storage operation failed")
        return handle
    server.register("attachment.generate_docx", handler(AttachmentGenerateDocxParams, "generate_docx"))
    server.register("attachment.list", handler(AttachmentListParams, "list"))
    server.register("attachment.get", handler(AttachmentGetParams, "get"))
