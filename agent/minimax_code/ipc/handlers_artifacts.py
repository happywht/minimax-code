"""Explicit text artifact registration; no caller host or absolute-path authority."""
from __future__ import annotations

import logging

from pydantic import ValidationError

from ..text_artifacts import get_artifact_service
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    ArtifactGetParams,
    ArtifactScopedListParams,
    ArtifactRegisterParams,
)

logger = logging.getLogger(__name__)


def register_artifact_handlers(server):
    def handler(model, operation):
        async def handle(params, ctx):
            try:
                values = model.model_validate(params).model_dump()
                service = await get_artifact_service()
                result = await getattr(service, operation)(**values)
                await ctx.reply({"artifact": result} if operation == "register" else result)
            except ValidationError:
                await ctx.reply_error(INVALID_PARAMS, "Invalid artifact parameters")
            except (ValueError, OSError) as exc:
                await ctx.reply_error(-32000, str(exc))
            except Exception:
                logger.exception("artifact.%s failed", operation)
                await ctx.reply_error(INTERNAL_ERROR, "Artifact storage operation failed")
        return handle

    server.register("artifact.register", handler(ArtifactRegisterParams, "register"))
    server.register("artifact.list", handler(ArtifactScopedListParams, "list"))
    server.register("artifact.get", handler(ArtifactGetParams, "get"))
