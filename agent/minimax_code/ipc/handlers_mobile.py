"""JSON-RPC handlers for the ``mobile.*`` namespace.

Surface
-------

* ``mobile.pair_start``        — mint a 10-min pairing token + QR payload
* ``mobile.pair_confirm``      — device side: validate token, write row
* ``mobile.list``              — every paired device, most recent first
* ``mobile.unpair``            — remove by ``device_id``
* ``mobile.touch``             — heartbeat (bump ``last_seen_at``)
* ``mobile.push_notification`` — push a notification to device(s) via WS
* ``mobile.device_status``     — return online status for all paired devices

The token cache is process-local (see :mod:`minimax_code.mobile`).
In Phase 2 production the device's ``public_key`` will become a
verified certificate fingerprint, not an opaque blob.
"""

from __future__ import annotations

import logging
from typing import Any

from .protocol import INTERNAL_ERROR, INVALID_PARAMS
from .handler_utils import HandlerError
from .server import Context

logger = logging.getLogger(__name__)

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
                raise HandlerError(
                    INTERNAL_ERROR,
                    "pairing manager is not available; agent not fully initialized",
                )
            suggested_name = (params or {}).get("suggested_name") if params else None
            if suggested_name is not None and not isinstance(suggested_name, str):
                raise HandlerError(INVALID_PARAMS, "suggested_name must be a string")
            pair_info = mgr.generate_pairing_token(suggested_name=suggested_name)
            qr_payload = f"minimax-code://pair?token={pair_info['token']}"
            await ctx.reply(
                {
                    "token": pair_info["token"],
                    "expires_at": pair_info["expires_at"],
                    "qr_payload": qr_payload,
                }
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover
            logger.exception("mobile.pair_start failed")
            await ctx.reply_error(INTERNAL_ERROR, "mobile.pair_start failed")

    async def handle_pair_confirm(params: Any, ctx: Context) -> None:
        try:
            mgr, _ = await _resolve_manager_and_dao()
            if mgr is None:
                raise HandlerError(
                    INTERNAL_ERROR, "pairing manager is not available"
                )
            if not isinstance(params, dict):
                raise HandlerError(INVALID_PARAMS, "params must be an object")
            for key in ("token", "device_id", "name", "public_key"):
                val = params.get(key)
                if not isinstance(val, str) or not val.strip():
                    raise HandlerError(
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
                    raise HandlerError(INVALID_PARAMS, exc.message)
                logger.exception("mobile.pair_confirm failed")
                raise HandlerError(INTERNAL_ERROR, "mobile.pair_confirm failed")
            await ctx.reply({"device": device})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover
            logger.exception("mobile.pair_confirm failed")
            await ctx.reply_error(INTERNAL_ERROR, "mobile.pair_confirm failed")

    async def handle_list(params: Any, ctx: Context) -> None:
        try:
            _, d = await _resolve_manager_and_dao()
            if d is None:
                raise HandlerError(
                    INTERNAL_ERROR, "mobile storage is not available"
                )
            devices = await d.list_all()
            await ctx.reply({"devices": devices})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover
            logger.exception("mobile.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "mobile.list failed")

    async def handle_unpair(params: Any, ctx: Context) -> None:
        try:
            _, d = await _resolve_manager_and_dao()
            if d is None:
                raise HandlerError(
                    INTERNAL_ERROR, "mobile storage is not available"
                )
            if not isinstance(params, dict):
                raise HandlerError(INVALID_PARAMS, "params must be an object")
            device_id = params.get("device_id")
            if not isinstance(device_id, str) or not device_id.strip():
                raise HandlerError(INVALID_PARAMS, "device_id must be a non-empty string")
            removed = await d.unregister(device_id)
            if not removed:
                raise HandlerError(
                    INVALID_PARAMS, f"unknown device_id: {device_id!r}"
                )
            await ctx.reply({"ok": True, "device_id": device_id})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover
            logger.exception("mobile.unpair failed")
            await ctx.reply_error(INTERNAL_ERROR, "mobile.unpair failed")

    async def handle_touch(params: Any, ctx: Context) -> None:
        try:
            _, d = await _resolve_manager_and_dao()
            if d is None:
                raise HandlerError(
                    INTERNAL_ERROR, "mobile storage is not available"
                )
            if not isinstance(params, dict):
                raise HandlerError(INVALID_PARAMS, "params must be an object")
            device_id = params.get("device_id")
            if not isinstance(device_id, str) or not device_id.strip():
                raise HandlerError(INVALID_PARAMS, "device_id must be a non-empty string")
            row = await d.touch_last_seen(device_id)
            if row is None:
                raise HandlerError(
                    INVALID_PARAMS, f"unknown device_id: {device_id!r}"
                )
            await ctx.reply({"ok": True, "device": row})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover
            logger.exception("mobile.touch failed")
            await ctx.reply_error(INTERNAL_ERROR, "mobile.touch failed")

    async def handle_push_notification(params: Any, ctx: Context) -> None:
        """Push a notification payload to one or all connected devices.

        Params:
            device_id (str, optional): target device; omit to broadcast.
            notification (dict): ``{type, title, body, …}``.
        """
        try:
            _, d = await _resolve_manager_and_dao()
            if d is None:
                raise HandlerError(
                    INTERNAL_ERROR, "mobile storage is not available"
                )
            if not isinstance(params, dict):
                raise HandlerError(INVALID_PARAMS, "params must be an object")
            notification = params.get("notification")
            if not isinstance(notification, dict) or not notification.get("title"):
                raise HandlerError(
                    INVALID_PARAMS,
                    "notification must be an object with at least a 'title' field",
                )
            device_id = params.get("device_id")
            payload = {
                "jsonrpc": "2.0",
                "method": "mobile.notification",
                "params": notification,
            }
            # Resolve the WS manager from app state
            ws_mgr = None
            try:
                from ..app import get_http_app
                app = get_http_app()
                if app is not None:
                    ws_mgr = app.state.ws_manager
            except Exception:
                pass
            if ws_mgr is None:
                raise HandlerError(
                    INTERNAL_ERROR,
                    "WebSocket manager not available; HTTP server not running",
                )
            if device_id:
                delivered = await ws_mgr.send_to_device(device_id, payload)
                await ctx.reply({"ok": True, "device_id": device_id, "delivered": delivered})
            else:
                # Broadcast to all connected devices
                online = ws_mgr.get_online_devices()
                delivered = 0
                for did in online:
                    if await ws_mgr.send_to_device(did, payload):
                        delivered += 1
                await ctx.reply({
                    "ok": True,
                    "broadcast": True,
                    "total_devices": len(online),
                    "delivered": delivered,
                })
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover
            logger.exception("mobile.push_notification failed")
            await ctx.reply_error(
                INTERNAL_ERROR, f"mobile.push_notification failed: {exc}"
            )

    async def handle_device_status(params: Any, ctx: Context) -> None:
        """Return online status for all paired devices.

        Params: (none)

        Returns ``{devices: [{id, name, paired_at, online}, …]}``.
        """
        try:
            _, d = await _resolve_manager_and_dao()
            if d is None:
                raise HandlerError(
                    INTERNAL_ERROR, "mobile storage is not available"
                )
            devices = await d.list_all()
            # Resolve the WS manager to get online set
            online_set: set[str] = set()
            try:
                from ..app import get_http_app
                app = get_http_app()
                if app is not None:
                    ws_mgr = app.state.ws_manager
                    online_set = ws_mgr.get_online_devices()
            except Exception:
                pass
            result = []
            for dev in devices:
                entry = dict(dev)
                dev_id = entry.get("id") or entry.get("device_id", "")
                entry["online"] = dev_id in online_set
                result.append(entry)
            await ctx.reply({"devices": result})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover
            logger.exception("mobile.device_status failed")
            await ctx.reply_error(
                INTERNAL_ERROR, f"mobile.device_status failed: {exc}"
            )

    server.register("mobile.pair_start", handle_pair_start)
    server.register("mobile.pair_confirm", handle_pair_confirm)
    server.register("mobile.list", handle_list)
    server.register("mobile.unpair", handle_unpair)
    server.register("mobile.touch", handle_touch)
    server.register("mobile.push_notification", handle_push_notification)
    server.register("mobile.device_status", handle_device_status)

__all__ = ["register_mobile_handlers"]
