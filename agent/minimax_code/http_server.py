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

# Maximum request body size for POST /rpc (10 MB).
# Prevents OOM from oversized payloads.
MAX_RPC_BODY_BYTES: int = 10 * 1024 * 1024


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

    v0.7.0: Added ``_device_connections`` for targeted push to
    specific mobile devices.  When a WebSocket connection includes
    a ``device_id`` query parameter, the mapping is stored so
    handlers can push notifications to individual devices.
    """

    def __init__(self, server: "IPCServer", version: str) -> None:
        self._server = server
        self._version = version
        self._clients: set[WebSocket] = set()
        self._lock = asyncio.Lock()
        self._listener: Any = None  # the IPCServer listener callback
        # v0.7.0 — device-level tracking for targeted push
        self._device_connections: dict[str, WebSocket] = {}
        # v0.8.0 — heartbeat: periodic ping to detect dead connections
        self._ping_task: asyncio.Task[None] | None = None
        # v0.8.0 — backpressure: per-client send queues with bounded depth
        self._send_queues: dict[int, asyncio.Queue[dict[str, Any] | None]] = {}
        self._sender_tasks: dict[int, asyncio.Task[None]] = {}
        self._max_queue_depth: int = 256  # drop oldest when exceeded

    @property
    def has_clients(self) -> bool:
        return bool(self._clients)

    def get_online_devices(self) -> set[str]:
        """Return the set of device IDs with active WS connections."""
        return set(self._device_connections.keys())

    async def send_to_device(self, device_id: str, payload: dict[str, Any]) -> bool:
        """Send *payload* to a specific device. Returns ``True`` on success."""
        ws = self._device_connections.get(device_id)
        if ws is None:
            return False
        try:
            await ws.send_json(payload)
            return True
        except Exception:
            logger.warning("send_to_device failed for %s; removing mapping", device_id)
            self._device_connections.pop(device_id, None)
            return False

    async def on_connect(self, ws: WebSocket) -> None:
        """Accept the upgrade, send ``agent.ready``, register listener.

        If the WS upgrade request includes a ``device_id`` query
        parameter, the mapping is stored for targeted push.
        """
        await ws.accept()
        # Extract device_id from query params (v0.7.0)
        device_id: str | None = None
        try:
            for key, val in ws.query_params.items():
                if key == "device_id" and val:
                    device_id = val
                    break
        except Exception:
            pass

        async with self._lock:
            self._clients.add(ws)
            if device_id:
                self._device_connections[device_id] = ws
            if self._listener is None:
                self._listener = self._on_event
                self._server.register_listener(self._listener)
            # Start heartbeat ping task when first client connects
            if self._ping_task is None:
                self._ping_task = asyncio.create_task(self._heartbeat_loop())
            # Start per-client sender queue for backpressure
            self._start_sender(ws)
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
            # Stop per-client sender queue
            self._stop_sender(ws)
            # Remove any device_id mapping pointing to this ws
            to_remove = [did for did, s in self._device_connections.items() if s is ws]
            for did in to_remove:
                del self._device_connections[did]
            if not self._clients and self._listener is not None:
                self._server.unregister_listener(self._listener)
                self._listener = None
            # Stop heartbeat when last client disconnects
            if not self._clients and self._ping_task is not None:
                self._ping_task.cancel()
                self._ping_task = None

    async def _heartbeat_loop(self) -> None:
        """Periodically send WS protocol-level pings to detect dead clients.

        Runs as a background task while any client is connected.
        Sends a ping every 30 seconds; clients that fail to pong
        within the WebSocket library timeout are auto-disconnected
        by FastAPI / Starlette.
        """
        try:
            while True:
                await asyncio.sleep(30)
                dead: list[WebSocket] = []
                for ws in list(self._clients):
                    try:
                        await ws.send_json(
                            {"jsonrpc": "2.0", "method": "agent.ping", "params": None}
                        )
                    except Exception:
                        dead.append(ws)
                # Clean up clients that failed to receive the ping
                for ws in dead:
                    logger.warning("heartbeat ping failed; dropping client")
                    await self._cleanup_disconnect(ws)
        except asyncio.CancelledError:
            pass  # Normal shutdown
        except Exception:
            logger.exception("heartbeat loop crashed")

    def _start_sender(self, ws: WebSocket) -> None:
        """Spawn a per-client sender task that drains the queue sequentially."""
        ws_id = id(ws)
        queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue(
            maxsize=self._max_queue_depth
        )
        self._send_queues[ws_id] = queue

        async def _sender() -> None:
            """Consume from the queue and send to the client in order."""
            try:
                while True:
                    item = await queue.get()
                    if item is None:
                        break  # Sentinel — shut down
                    try:
                        await ws.send_json(item)
                    except Exception:
                        logger.warning("ws send failed; stopping sender for client")
                        break
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("ws sender task crashed")

        self._sender_tasks[ws_id] = asyncio.create_task(_sender())

    def _stop_sender(self, ws: WebSocket) -> None:
        """Stop the per-client sender task and clean up."""
        ws_id = id(ws)
        queue = self._send_queues.pop(ws_id, None)
        task = self._sender_tasks.pop(ws_id, None)
        if queue is not None:
            # Send sentinel to wake up the sender
            try:
                queue.put_nowait(None)
            except asyncio.QueueFull:
                pass
        if task is not None:
            task.cancel()

    def _enqueue(self, ws: WebSocket, payload: dict[str, Any]) -> None:
        """Queue a payload for a client. Drops the oldest item if full."""
        ws_id = id(ws)
        queue = self._send_queues.get(ws_id)
        if queue is None:
            return
        try:
            queue.put_nowait(payload)
        except asyncio.QueueFull:
            # Backpressure: drop the oldest queued item to make room
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
            try:
                queue.put_nowait(payload)
            except asyncio.QueueFull:
                logger.warning("ws queue still full after drop; discarding event")


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
                self._enqueue(ws, payload)
            except Exception:
                logger.exception("failed to enqueue ws send; dropping client")
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
            # ── Graceful shutdown sequence ──────────────────────────
            # 1. Cancel any in-flight agent runs.
            from .ipc.builtins import _ACTIVE_CORES

            for sid, core in list(_ACTIVE_CORES.items()):
                try:
                    core.cancel()
                except Exception:
                    pass
            _ACTIVE_CORES.clear()
            logger.info("graceful shutdown: cancelled %d active cores", len(_ACTIVE_CORES))

            # 2. Stop the APScheduler if it is running.
            try:
                from .scheduler import get_scheduler

                sched = get_scheduler()
                if sched is not None:
                    sched.shutdown(wait=False)
                    logger.info("graceful shutdown: scheduler stopped")
            except Exception:
                pass

            # 3. Close the LLM client (httpx connection pool).
            try:
                from .app import get_subagent_llm

                llm = get_subagent_llm()
                if llm is not None and hasattr(llm, "close"):
                    await llm.close()
                    logger.info("graceful shutdown: LLM client closed")
            except Exception:
                pass

            # 4. Close the SQLite database.
            try:
                from .app import get_db

                db = get_db()
                if db is not None and hasattr(db, "close"):
                    await db.close()
                    logger.info("graceful shutdown: database closed")
            except Exception:
                pass

            # 5. Close any still-open WebSocket connections.
            for ws in list(ws_manager._clients):
                try:
                    await ws.close()
                except Exception:
                    pass
            logger.info("graceful shutdown: %d ws clients closed", len(ws_manager._clients))

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
            # Reject oversized payloads before parsing.
            if len(raw) > MAX_RPC_BODY_BYTES:
                return JSONResponse(
                    content=Response(
                        id=0,
                        error=RPCError(
                            code=PARSE_ERROR,
                            message=(
                                f"request body too large "
                                f"({len(raw)} > {MAX_RPC_BODY_BYTES} bytes)"
                            ),
                        ),
                    ).model_dump(exclude_none=True),
                    status_code=200,
                )
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
                        message="request body could not be read",
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
                # Heartbeat pong from client — acknowledged silently.
                method = parsed.get("method")
                if method == "agent.pong":
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
        db_ok = False
        try:
            from .app import get_db

            db = get_db()
            if db is not None:
                await db.execute("SELECT 1")
                db_ok = True
        except Exception:
            pass
        return {
            "ok": db_ok,
            "db": db_ok,
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
