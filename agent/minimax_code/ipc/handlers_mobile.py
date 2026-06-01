"""JSON-RPC handlers for the ``mobile.*`` namespace.

Surface
-------

* ``mobile.pair_start``   — mint a 10-min pairing token + QR payload
* ``mobile.pair_confirm`` — device side: validate token, write row
* ``mobile.list``         — every paired device, most recent first
* ``mobile.unpair``       — remove by ``device_id``
* ``mobile.touch``        — heartbeat (bump ``last_seen_at``)

The token cache is process-local (see :mod:`minimax_code.mobile`).
In Phase 2 production the device's ``public_key`` will become a
verified certificate fingerprint, not an opaque blob.
"""

from __future__ import annotations

import logging
from typing import Any

from .protocol import INTERNAL_ERROR, INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class _HandlerError(Exception):
    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_mobile_handlers(
    server: Any,
    *,
    dao: Any = None,
    manager: Any = None,
) -> None:
    """Register ``mobile.*`` handlers on ``server``.

    Parameters
    ----------
    server:
        IPC server instance.
    dao:
        Optional pre-built :class:`MobileDeviceDAO`.
    manager:
        Optional pre-built :class:`PairingManager`. When ``None``,
        handlers fall back to the singleton from
        :func:`minimax_code.mobile.get_pairing_manager`.
    """

    async def _resolve_manager_and_dao() -> tuple[Any, Any]:
        """Lazy-resolve the manager and DAO singletons.

        If neither the explicit args nor the singletons are present,
        try to bring up the runtime — that opens the DB and
        populates both singletons as a side effect.
        """
        mgr = manager
        d = dao
        if mgr is None or d is None:
            from ..mobile import get_mobile_dao, get_pairing_manager

            if mgr is None:
                mgr = get_pairing_manager()
            if d is None:
                d = get_mobile_dao()
            if mgr is None or d is None:
                # Try to bring up the runtime to wire the singletons.
                try:
                    from ..app import init_runtime
                    await init_runtime()
                except Exception:  # pragma: no cover — defensive
                    logger.debug("init_runtime failed; trying without")
                if mgr is None:
                    mgr = get_pairing_manager()
                if d is None:
                    d = get_mobile_dao()
        return mgr, d

    async def handle_pair_start(params: Any, ctx: Context) -> None:
        try:
            mgr, _ = await _resolve_manager_and_dao()
            if mgr is None:
                raise _HandlerError(
                    INTERNAL_ERROR,
                    "pairing manager is not available; agent not fully initialized",
                )
            suggested_name = (params or {}).get("suggested_name") if params else None
            if suggested_name is not None and not isinstance(suggested_name, str):
                raise _HandlerError(INVALID_PARAMS, "suggested_name must be a string")
            pair_info = mgr.generate_pairing_token(suggested_name=suggested_name)
            qr_payload = f"minimax-code://pair?token={pair_info['token']}"
            await ctx.reply(
                {
                    "token": pair_info["token"],
                    "expires_at": pair_info["expires_at"],
                    "qr_payload": qr_payload,
                }
            )
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover
            logger.exception("mobile.pair_start failed")
            await ctx.reply_error(INTERNAL_ERROR, f"mobile.pair_start failed: {exc}")

    async def handle_pair_confirm(params: Any, ctx: Context) -> None:
        try:
            mgr, _ = await _resolve_manager_and_dao()
            if mgr is None:
                raise _HandlerError(
                    INTERNAL_ERROR, "pairing manager is not available"
                )
            if not isinstance(params, dict):
                raise _HandlerError(INVALID_PARAMS, "params must be an object")
            for key in ("token", "device_id", "name", "public_key"):
                val = params.get(key)
                if not isinstance(val, str) or not val.strip():
                    raise _HandlerError(
                        INVALID_PARAMS, f"param {key!r} must be a non-empty string"
                    )
            try:
                device = await mgr.confirm_pairing(
                    params["token"],
                    params["device_id"],
                    params["name"],
                    params["public_key"],
                )
            except Exception as exc:
                # PairingError or other DAO errors → INVALID_PARAMS or INTERNAL_ERROR
                from ..mobile import PairingError
                if isinstance(exc, PairingError):
                    raise _HandlerError(INVALID_PARAMS, exc.message)
                logger.exception("mobile.pair_confirm failed")
                raise _HandlerError(INTERNAL_ERROR, f"mobile.pair_confirm failed: {exc}")
            await ctx.reply({"device": device})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover
            logger.exception("mobile.pair_confirm failed")
            await ctx.reply_error(INTERNAL_ERROR, f"mobile.pair_confirm failed: {exc}")

    async def handle_list(params: Any, ctx: Context) -> None:
        try:
            _, d = await _resolve_manager_and_dao()
            if d is None:
                raise _HandlerError(
                    INTERNAL_ERROR, "mobile storage is not available"
                )
            devices = await d.list_all()
            await ctx.reply({"devices": devices})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover
            logger.exception("mobile.list failed")
            await ctx.reply_error(INTERNAL_ERROR, f"mobile.list failed: {exc}")

    async def handle_unpair(params: Any, ctx: Context) -> None:
        try:
            _, d = await _resolve_manager_and_dao()
            if d is None:
                raise _HandlerError(
                    INTERNAL_ERROR, "mobile storage is not available"
                )
            if not isinstance(params, dict):
                raise _HandlerError(INVALID_PARAMS, "params must be an object")
            device_id = params.get("device_id")
            if not isinstance(device_id, str) or not device_id.strip():
                raise _HandlerError(INVALID_PARAMS, "device_id must be a non-empty string")
            removed = await d.unregister(device_id)
            if not removed:
                raise _HandlerError(
                    INVALID_PARAMS, f"unknown device_id: {device_id!r}"
                )
            await ctx.reply({"ok": True, "device_id": device_id})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover
            logger.exception("mobile.unpair failed")
            await ctx.reply_error(INTERNAL_ERROR, f"mobile.unpair failed: {exc}")

    async def handle_touch(params: Any, ctx: Context) -> None:
        try:
            _, d = await _resolve_manager_and_dao()
            if d is None:
                raise _HandlerError(
                    INTERNAL_ERROR, "mobile storage is not available"
                )
            if not isinstance(params, dict):
                raise _HandlerError(INVALID_PARAMS, "params must be an object")
            device_id = params.get("device_id")
            if not isinstance(device_id, str) or not device_id.strip():
                raise _HandlerError(INVALID_PARAMS, "device_id must be a non-empty string")
            row = await d.touch_last_seen(device_id)
            if row is None:
                raise _HandlerError(
                    INVALID_PARAMS, f"unknown device_id: {device_id!r}"
                )
            await ctx.reply({"ok": True, "device": row})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover
            logger.exception("mobile.touch failed")
            await ctx.reply_error(INTERNAL_ERROR, f"mobile.touch failed: {exc}")

    server.register("mobile.pair_start", handle_pair_start)
    server.register("mobile.pair_confirm", handle_pair_confirm)
    server.register("mobile.list", handle_list)
    server.register("mobile.unpair", handle_unpair)
    server.register("mobile.touch", handle_touch)


__all__ = ["register_mobile_handlers"]
