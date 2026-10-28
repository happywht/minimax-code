"""Explicit authenticated operator API, not a model tool or run admission."""
from __future__ import annotations

import logging

from pydantic import ValidationError

from ..office_deliveries import get_office_service
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    ArtifactListParams,
    OfficeGenerateParams,
    OfficeGetParams,
)

logger = logging.getLogger(__name__)


def register_office_handlers(server):
    def handler(model, operation):
        async def handle(params, ctx):
            try:
                request = model.model_validate(params)
                service = await get_office_service()
                result = (await service.generate(request) if operation == "generate"
                          else await getattr(service, operation)(**request.model_dump()))
                await ctx.reply(result)
            except ValidationError:
                await ctx.reply_error(INVALID_PARAMS, "Invalid plain-cell XLSX parameters")
            except (ValueError, PermissionError) as error:
                await ctx.reply_error(-32000, str(error))
            except (OSError, TimeoutError):
                await ctx.reply_error(-32000, "Office storage or generation deadline unavailable; refresh before retry")
            except Exception as error:
                # Never log inputs, document values, response bodies or exception text.
                logger.warning("Office operation failed (%s)", type(error).__name__)
                await ctx.reply_error(INTERNAL_ERROR, "Office operation failed; refresh before retry")
        return handle

    server.register("office.generate_xlsx", handler(OfficeGenerateParams, "generate"))
    server.register("office.list", handler(ArtifactListParams, "list"))
    server.register("office.get", handler(OfficeGetParams, "get"))
