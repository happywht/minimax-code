"""FastAPI routes for inline code completion.

Registers ``POST /complete`` on the FastAPI app. This endpoint
bypasses the IPCServer and calls :class:`MiniMaxClient` directly
for minimal latency.

The route reuses the same model preference logic as the chat flow
(``ModelPrefsDAO`` + ``ProviderDAO``) so the completion uses the
user's configured model and API key.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from fastapi import Request
from fastapi.responses import JSONResponse

from .completion import CompletionRequest, complete
from .llm import MiniMaxClient

if TYPE_CHECKING:
    from ..ipc.server import IPCServer

logger = logging.getLogger(__name__)

_MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MiB — generous for code


def register_completion_routes(
    app: "fastapi.FastAPI",  # noqa: F821
    server: "IPCServer",
) -> None:
    """Register ``POST /complete`` on the FastAPI app."""

    @app.post("/complete")
    async def post_complete(request: Request) -> JSONResponse:
        """Handle an inline code completion request."""
        # Guard: body size
        body_bytes = await request.body()
        if len(body_bytes) > _MAX_BODY_BYTES:
            return JSONResponse(
                {"error": "request body too large"},
                status_code=413,
            )

        # Parse JSON body
        try:
            body = await request.json()
        except Exception:
            return JSONResponse(
                {"error": "invalid JSON body"},
                status_code=400,
            )

        # Validate required fields
        file_path = body.get("file_path")
        if not file_path:
            return JSONResponse(
                {"error": "'file_path' is required"},
                status_code=400,
            )

        req = CompletionRequest(
            file_path=file_path,
            content_before=body.get("content_before", ""),
            content_after=body.get("content_after", ""),
            language=body.get("language"),
            max_tokens=min(int(body.get("max_tokens", 256)), 1024),
            temperature=max(0.0, min(float(body.get("temperature", 0.2)), 1.0)),
        )

        # Build LLM client from stored preferences
        llm = await _build_llm_client(server)

        # Execute completion
        try:
            resp = await complete(req, llm)
        except Exception as exc:
            logger.exception("completion failed")
            return JSONResponse(
                {"error": str(exc)},
                status_code=500,
            )

        return JSONResponse({
            "text": resp.text,
            "model": resp.model,
            "tokens_in": resp.tokens_in,
            "tokens_out": resp.tokens_out,
            "latency_ms": resp.latency_ms,
        })


async def _build_llm_client(server: "IPCServer") -> MiniMaxClient:
    """Construct a MiniMaxClient from the server's stored preferences.

    Reuses the same logic as ``builtins.handle_agent_send_message``:
    reads model preference and provider config from the DB.
    Falls back to default (mock mode) when no config is available.
    """
    try:
        from ..storage.dao.model_prefs import ModelPrefsDAO
        from ..storage.dao.providers import ProviderDAO
        from ..app import get_db

        db = get_db()
        if db is not None:
            prefs_dao = ModelPrefsDAO(db)
            pref = await prefs_dao.get()
            model_name = pref.get("model") if pref else None

            provider_dao = ProviderDAO(db)
            provider = await provider_dao.get_active()

            if provider:
                return MiniMaxClient(
                    protocol=provider.get("protocol", "anthropic"),
                    api_key=provider.get("api_key", ""),
                    base_url=provider.get("base_url"),
                    model=model_name or "MiniMax-M3",
                )
            if model_name:
                return MiniMaxClient(model=model_name)
    except Exception:
        logger.debug("could not read model prefs; using default client")

    return MiniMaxClient()  # mock mode when no API key
