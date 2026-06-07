"""JSON-RPC handlers for the ``secrets.*`` namespace.

These wrap :mod:`minimax_code.secrets` so the Settings page can
show whether a key is configured, where it's coming from, and let
the user write / clear it without having to touch the OS
Credential Manager directly.

Endpoints
---------
``secrets.status``  -> ``{"configured": bool, "source": "keyring" | "env" | "none"}``
``secrets.set``     -> ``{"configured": true, "source": "keyring"}``
``secrets.clear``   -> ``{"configured": false, "source": "none"}``

Security notes
--------------
* The actual key value is never echoed back through the wire
  — only whether one is configured and which lookup layer
  served it. The IPC layer treats the key as a write-only
  secret: ``set`` accepts it, ``status`` / ``clear`` never
  return it.
* ``secrets.clear`` removes the keyring entry but does not
  touch the env var — that's an external environment override
  the agent intentionally respects, not the user's stored
  preference. The status will flip to ``"env"`` if the env var
  is still set, which is the right behavior.
* ``secrets.set`` writes to the keyring; backend failures are
  surfaced as JSON-RPC ``-32603`` so the UI can show "could
  not write to Credential Manager" rather than silently
  dropping the key.

Wire-up
-------
:func:`register_secret_handlers` is called from
:func:`minimax_code.app.register_app_handlers`. The handlers
are stateless — every call goes straight to the ``secrets``
module, so there is no factory / lazy-build dance to play.
"""

from __future__ import annotations

import logging
from typing import Any

from .protocol import INVALID_PARAMS
from .handler_utils import HandlerError
from .server import Context

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_secret_handlers(server: Any) -> None:
    """Register the ``secrets.*`` handlers on ``server``."""

    async def handle_secrets_status(_params: Any, ctx: Context) -> None:
        try:
            from .. import secrets

            await ctx.reply(
                {
                    "configured": secrets.has_api_key(),
                    "source": secrets.key_source(),
                }
            )
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("secrets.status failed")
            await ctx.reply({"configured": False, "source": "none", "error": str(exc)})

    async def handle_secrets_set(params: Any, ctx: Context) -> None:
        try:
            if not isinstance(params, dict) or "value" not in params:
                raise HandlerError(
                    INVALID_PARAMS, "params must include a 'value' string"
                )
            value = params["value"]
            if not isinstance(value, str):
                raise HandlerError(
                    INVALID_PARAMS, "'value' must be a string"
                )
            value = value.strip()
            if not value:
                raise HandlerError(
                    INVALID_PARAMS, "'value' must be a non-empty string"
                )
            from .. import secrets

            secrets.set_api_key(value)
            await ctx.reply(
                {
                    "configured": True,
                    "source": secrets.key_source(),
                }
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:
            # The ``secrets`` module already logs the underlying
            # keyring error; we just translate to a friendly IPC
            # envelope so the UI can surface it.
            logger.exception("secrets.set failed")
            await ctx.reply_error(-32603, "could not write to keyring")

    async def handle_secrets_clear(_params: Any, ctx: Context) -> None:
        try:
            from .. import secrets

            secrets.clear_api_key()
            await ctx.reply(
                {
                    "configured": secrets.has_api_key(),
                    "source": secrets.key_source(),
                }
            )
        except Exception as exc:
            logger.exception("secrets.clear failed")
            await ctx.reply_error(-32603, "could not clear keyring entry")

    server.register("secrets.status", handle_secrets_status)
    server.register("secrets.set", handle_secrets_set)
    server.register("secrets.clear", handle_secrets_clear)

__all__ = ["register_secret_handlers"]
