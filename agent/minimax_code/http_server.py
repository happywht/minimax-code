"""HTTP + WebSocket transport for the JSON-RPC 2.0 agent.

In v0.2.0 the Python agent drops the Tauri sidecar and becomes a
plain localhost HTTP service. This module is the bridge: a FastAPI
app that re-uses the existing :class:`IPCServer` (same handler
registry, same in-process event bus) and exposes three endpoints:

- ``POST /rpc`` — one-shot JSON-RPC 2.0 request → response.
- ``GET  /ws``  — server-push events (after upgrading to WebSocket).
- ``GET  /health`` — liveness probe.

The transport contract is in ``docs/v0.2.0-web-architecture.md``.

Concurrency model
-----------------
- The ASGI app runs on a single asyncio loop (uvicorn's loop).
- The shared :class:`IPCServer` lives in the same process and the
  same loop. ``server.handle_request`` and ``Context.emit`` are
  both ``async``; we just ``await`` them.
- WebSocket fan-out: a single listener is registered on the
  :class:`IPCServer` the first time a client connects and removed
  when the last client disconnects. The listener is sync
  (``Callable[[dict], None]``) per the contract — it just
  schedules a ``send_json`` task per open client.

CORS
----
Allow-list is exactly the Vite dev server origins
(``http://localhost:5173`` and ``http://127.0.0.1:5173``). The
HTTP server binds ``127.0.0.1`` so anything on the LAN is
unreachable, but we still set a tight CORS allow-list so a
malicious site can't impersonate the agent.
"""

from __future__ import annotations

import asyncio
import importlib.metadata
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any, TYPE_CHECKING

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .ipc.protocol import (
    INVALID_REQUEST,
    PARSE_ERROR,
    Response,
    RPCError,
)

if TYPE_CHECKING:  # pragma: no cover — typing only
    from .ipc.server import IPCServer

logger = logging.getLogger(__name__)


# CORS allow-list — the Vite dev server only. Production would
# need a real allow-list per deployment, but v0.2.0 is dev-only.
CORS_ALLOW_ORIGINS: list[str] = [
    "http://localhost:5173",
    "http://127.0.0.1:5173",
]
CORS_ALLOW_METHODS: list[str] = ["GET", "POST"]
CORS_ALLOW_HEADERS: list[str] = ["Content-Type", "Authorization"]


# ---------------------------------------------------------------------------
# Version
# ---------------------------------------------------------------------------


def _resolve_version() -> str:
    """Return the agent's version string (best-effort).

    Reads the installed package metadata first, then falls back to
    the ``__version__`` attribute on the package (so running from a
    source checkout without an editable install still works). On
    total failure returns ``"unknown"`` rather than raising — the
    ``/health`` endpoint is supposed to be infallible.
    """
    try:
        return importlib.metadata.version("minimax-code-agent")
    except Exception:  # pragma: no cover — defensive
        pass
    try:
        from . import __version__ as pkg_version

        return str(pkg_version)
    except Exception:  # pragma: no cover
        return "unknown"


# ---------------------------------------------------------------------------
# WebSocket fan-out manager
# ---------------------------------------------------------------------------


class _WSManager:
    """Owns the open WebSocket set and the single IPCServer listener.

    The listener is registered on the server lazily: the first
    connecting client triggers the registration; when the last
    client disconnects, the listener is unregistered. This keeps
    stdio-only runs (and any future no-WS transport) free of dead
    listener callbacks.

    The listener must be cheap — it is invoked on the hot path of
    every handler that calls ``ctx.emit`` / ``ctx.notify``. Each
    per-client send is scheduled as a separate task so a slow /
    disconnected client cannot block the others.
    """

    def __init__(self, server: "IPCServer", version: str) -> None:
        self._server = server
        self._version = version
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._listener: Any = None  # the IPCServer listener callback

    @property
    def has_clients(self) -> bool:
        return bool(self._clients)

    async def on_connect(self, ws: WebSocket) -> None:
        """Accept the upgrade, send ``agent.ready``, register listener."""
        await ws.accept()
        async with self._lock:
            self._clients.add(ws)
            if self._listener is None:
                self._listener = self._on_event
                self._server.register_listener(self._listener)
        # Send the lifecycle event. Done after the lock release so
        # the listener is fully wired before any handler can fire
        # events. ``agent.ready`` is the one event we *don't* route
        # through the listener — it's per-client lifecycle, not
        # shared broadcast.
        try:
            await ws.send_json(
                {
                    "jsonrpc": "2.0",
                    "method": "agent.ready",
                    "params": {
                        "server": "minimax-code-agent",
                        "version": self._version,
                    },
                }
            )
        except Exception:
            logger.exception("failed to send agent.ready; closing socket")
            await self._cleanup_disconnect(ws)
            raise

    async def on_disconnect(self, ws: WebSocket) -> None:
        """Drop a client; tear down the listener if no clients remain."""
        await self._cleanup_disconnect(ws)

    async def _cleanup_disconnect(self, ws: WebSocket) -> None:
        async with self._lock:
            self._clients.discard(ws)
            if not self._clients and self._listener is not None:
                self._server.unregister_listener(self._listener)
                self._listener = None

    def _on_event(self, env: dict[str, Any]) -> None:
        """IPCServer listener — fan ``env`` out to every open client.

        The callback runs in whatever loop is dispatching the
        handler. The listeners field is the IPCServer's own list,
        so this method is bound to that server's loop in practice
        (the ASGI app's loop).

        ``env`` may be the canonical :class:`Event` shape
        ``{event, data}`` or the :class:`Notification` shape
        ``{method, params}``. The WS wire format (per the
        architecture doc) uses ``{method, params}``; we translate
        the Event shape to match so the client sees a single,
        consistent envelope.
        """
        if "event" in env and "method" not in env:
            payload: dict[str, Any] = {
                "jsonrpc": "2.0",
                "method": env["event"],
                "params": env.get("data"),
            }
        else:
            payload = env
        # Snapshot the client set under the lock to avoid races
        # with concurrent connect/disconnect.
        for ws in list(self._clients):
            try:
                loop = asyncio.get_event_loop()
            except RuntimeError:
                loop = None
            try:
                if loop is not None and loop.is_running():
                    loop.create_task(ws.send_json(payload))
                else:  # pragma: no cover — defensive
                    pass
            except Exception:
                logger.exception("failed to schedule ws send; dropping client")
                self._clients.discard(ws)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def build_app(
    server: "IPCServer",
    *,
    version: str | None = None,
    server_started_at: float | None = None,
) -> FastAPI:
    """Build a FastAPI app bound to ``server``.

    The app re-uses the same handler registry as the stdio server,
    so registering handlers once (via ``register_app_handlers``)
    lights up both transports. The returned app is the ASGI
    callable — pass it to uvicorn (``uvicorn.run(app, ...)``) or
    wrap it in an ``ASGITransport`` for in-process tests.
    """
    app_version = version if version is not None else _resolve_version()
    started_at = server_started_at if server_started_at is not None else time.time()
    ws_manager = _WSManager(server, app_version)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # No background tasks to spawn — the IPCServer handlers
        # are the work, and they are driven by incoming requests.
        # The lifespan hook exists so future work (a heartbeat
        # task, a metrics exporter) has a clean place to register.
        logger.info(
            "http server ready: version=%s, ws clients=%d", app_version, 0
        )
        try:
            yield
        finally:
            # Best-effort: close any still-open sockets. uvicorn
            # also closes them on shutdown; this is belt-and-braces.
            for ws in list(ws_manager._clients):
                try:
                    await ws.close()
                except Exception:
                    pass

    app = FastAPI(
        title="minimax-code-agent",
        version=app_version,
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ALLOW_ORIGINS,
        allow_credentials=True,
        allow_methods=CORS_ALLOW_METHODS,
        allow_headers=CORS_ALLOW_HEADERS,
    )

    # Stash the WS manager on the app state so tests / future
    # code can reach it without re-walking module globals.
    app.state.ws_manager = ws_manager
    app.state.server = server
    app.state.started_at = started_at

    # ---- POST /rpc ------------------------------------------------------

    @app.post("/rpc")
    async def post_rpc(request: Request) -> JSONResponse:
        # The HTTP body is one JSON envelope. We always return
        # HTTP 200 — the JSON-RPC ``error`` field carries the
        # failure mode. A 5xx is reserved for "agent itself
        # crashed", which would surface as an unhandled exception
        # in the dispatch path; FastAPI returns 500 for those
        # automatically.
        try:
            raw = await request.body()
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            return JSONResponse(
                content=Response(
                    id=0,
                    error=RPCError(
                        code=PARSE_ERROR,
                        message=f"invalid JSON: {exc}",
                    ),
                ).model_dump(exclude_none=True),
                status_code=200,
            )
        except Exception as exc:  # pragma: no cover — defensive
            return JSONResponse(
                content=Response(
                    id=0,
                    error=RPCError(
                        code=PARSE_ERROR,
                        message=f"request body could not be read: {exc}",
                    ),
                ).model_dump(exclude_none=True),
                status_code=200,
            )

        if not isinstance(obj, dict):
            return JSONResponse(
                content=Response(
                    id=0,
                    error=RPCError(
                        code=INVALID_REQUEST,
                        message="envelope must be a JSON object",
                    ),
                ).model_dump(exclude_none=True),
                status_code=200,
            )

        response = await server.handle_request(obj)
        if response is None:
            # Notification — fire and forget, no body to return.
            # Return an empty 200 so curl / fetch don't barf on
            # the lack of a JSON body.
            return JSONResponse(content={}, status_code=200)
        return JSONResponse(content=response, status_code=200)

    # ---- GET /ws --------------------------------------------------------

    @app.websocket("/ws")
    async def ws_endpoint(websocket: WebSocket) -> None:
        await ws_manager.on_connect(websocket)
        try:
            # The server is push-only over WS. We still drain
            # incoming frames so the client can send keepalives
            # / pings; ignore any client→server envelopes (the
            # HTTP /rpc endpoint is the right place for those).
            while True:
                msg = await websocket.receive_text()
                if not msg:
                    continue
                # Best-effort parse — drop anything malformed.
                try:
                    parsed = json.loads(msg)
                except json.JSONDecodeError:
                    continue
                if not isinstance(parsed, dict):
                    continue
                # The web client uses the HTTP endpoint for
                # requests, so we do nothing here beyond draining.
                logger.debug("ws received (ignored): %s", parsed)
        except WebSocketDisconnect:
            pass
        except Exception:  # pragma: no cover — defensive
            logger.exception("ws endpoint crashed")
        finally:
            await ws_manager.on_disconnect(websocket)

    # ---- GET /health ----------------------------------------------------

    @app.get("/health")
    async def get_health() -> dict[str, Any]:
        return {
            "ok": True,
            "version": app_version,
            "uptime_s": int(time.time() - started_at),
        }

    # ---- POST /hooks/{path} — inbound webhooks (v0.5.0) ----------------

    @app.post("/hooks/{path:path}")
    async def post_webhook(path: str, request: Request) -> JSONResponse:
        """Receive inbound webhook events (GitHub / Gitee / custom).

        1. Look up the webhook config by ``url_path``.
        2. Verify HMAC signature (if a secret is configured).
        3. Parse the payload and dispatch the mapped action.
        4. Always return 200 so the sender does not retry.
        """
        from .webhooks import WebhookReceiver, dispatch_webhook_action
        from .storage.dao.webhooks import WebhookDAO

        url_path = f"/hooks/{path}"
        body = await request.body()
        headers = {
            k: v for k, v in request.headers.items()
            if k.lower().startswith("x-")
        }

        # Resolve the webhook config from storage.
        db = server._app_state.get("db") if hasattr(server, "_app_state") else None
        if db is None:
            from .app import get_db
            db = get_db()
        if db is None:
            return JSONResponse(content={"ok": False, "error": "no db"}, status_code=200)

        dao = WebhookDAO(db)
        cfg = await dao.get_by_path(url_path)
        if cfg is None:
            logger.warning("webhook not found for path=%s", url_path)
            return JSONResponse(content={"ok": False, "error": "not found"}, status_code=200)

        receiver = WebhookReceiver()
        payload = await receiver.handle_request(cfg, headers, body)
        if payload is None:
            return JSONResponse(content={"ok": False, "error": "signature mismatch"}, status_code=200)

        # Fire-and-forget dispatch.
        try:
            await dispatch_webhook_action(
                payload,
                action_type=cfg.get("action_type", "send-message"),
                action_config=cfg.get("action_config", {}),
                webhook_id=cfg.get("id", ""),
            )
        except Exception:
            logger.exception("webhook action dispatch failed for %s", cfg.get("id"))

        return JSONResponse(content={"ok": True}, status_code=200)

    # ---- POST /complete — inline code completion (v0.6.0) ---------------

    from .agent.completion_routes import register_completion_routes
    register_completion_routes(app, server)

    return app


# ---------------------------------------------------------------------------
# Entry point helper
# ---------------------------------------------------------------------------


def run(
    server: "IPCServer",
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    version: str | None = None,
    log_level: str = "INFO",
) -> None:
    """Bind uvicorn to ``host:port`` and serve the JSON-RPC bridge.

    Used by ``__main__`` after the application handlers are
    registered. This call blocks until uvicorn is shut down
    (SIGINT / SIGTERM). The default ``host`` is ``127.0.0.1`` —
    never expose this on ``0.0.0.0`` for v0.2.0.
    """
    import uvicorn

    app = build_app(server, version=version)
    uvicorn.run(
        app,
        host=host,
        port=port,
        log_level=log_level.lower(),
        # We don't want uvicorn's default access-log noise; the
        # agent has its own logger setup.
        access_log=False,
    )


__all__ = [
    "CORS_ALLOW_ORIGINS",
    "build_app",
    "run",
]
