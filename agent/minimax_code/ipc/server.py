"""Asyncio JSON-RPC 2.0 server over stdio.

Responsibilities
----------------
1. Read line-delimited JSON from stdin.
2. Classify each line as request / notification / event / response.
3. Dispatch requests and notifications to registered handlers.
4. Push responses, notifications, and events to stdout.
5. Optionally fan out events/notifications to in-process listeners
   so non-stdio transports (e.g. the HTTP/WS bridge) can observe
   what the server emits.

The server is intentionally decoupled from the rest of the agent —
handlers receive a ``Context`` object that gives them the ability to
emit push events and write response values without knowing about
stdio at all.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, IO, Optional

from ..config import Config
from .handler_utils import HandlerError
from .protocol import (
    Event,
    INTERNAL_ERROR,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    Notification,
    PARSE_ERROR,
    Response,
    RPCError,
    parse_envelope,
)

logger = logging.getLogger(__name__)


# Type aliases
ParamsType = Any
HandlerResult = Any
Handler = Callable[[ParamsType, "Context"], Awaitable[HandlerResult]]
NotificationHandler = Callable[[ParamsType, "Context"], Awaitable[None]]


@dataclass
class Context:
    """Per-call context passed to handlers.

    Handlers use ``ctx.reply(value)`` to send a response, ``ctx.notify(...)``
    for fire-and-forget notifications, and ``ctx.emit(event, data)`` to
    push a stream event. All write paths serialize through the server's
    single writer lock, so handlers can be ``async`` without worrying
    about interleaving bytes.
    """

    server: "IPCServer"
    method: str
    request_id: str | int | None = None
    _extra: dict[str, Any] = field(default_factory=dict)

    async def reply(self, result: Any) -> None:
        if self.request_id is None:
            raise RuntimeError("cannot reply to a notification")
        resp = Response(id=self.request_id, result=result)
        # Capture for non-stdio transports (HTTP) that need the
        # response object directly without re-parsing stdout.
        self._extra["_response"] = resp.model_dump(exclude_none=True)
        await self.server._send(resp.to_bytes())
        self._extra["_replied"] = True

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        if self.request_id is None:
            raise RuntimeError("cannot reply_error to a notification")
        resp = Response(
            id=self.request_id,
            error=RPCError(code=code, message=message, data=data),
        )
        self._extra["_response"] = resp.model_dump(exclude_none=True)
        await self.server._send(resp.to_bytes())
        self._extra["_replied"] = True

    async def notify(self, method: str, params: Any = None) -> None:
        env = Notification(method=method, params=params)
        await self.server._send(env.to_bytes())
        # Mirror to in-process listeners so e.g. the HTTP/WS
        # bridge can forward outbound notifications. Stdout
        # behaviour is preserved for the CLI debug case.
        self.server.notify(env.model_dump(exclude_none=True))

    async def emit(
        self,
        event: str,
        data: Any = None,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        # v0.3.0 §1: ``metadata`` is a transport-level envelope
        # field (e.g. ``agent.message_chunk`` carries
        # ``{thinking_count, tokens_in, tokens_out}`` on the
        # trailing chunk of a turn). We merge it into the data
        # dict so receivers that only see ``data.metadata`` work
        # uniformly, and we leave the wire envelope itself
        # untouched so older clients (v0.2.0) keep functioning.
        if metadata:
            if not isinstance(data, dict):
                # We only know how to attach metadata to a dict
                # payload; for other shapes (None, list, scalar)
                # we surface a warning so the bug is visible in
                # tests but we don't break the emit.
                import logging as _log

                _log.getLogger(__name__).warning(
                    "Context.emit received metadata= but data is %s; "
                    "metadata is dropped",
                    type(data).__name__,
                )
            else:
                data = {**data, "metadata": metadata}
        env = Event(event=event, data=data)
        await self.server._send(env.to_bytes())
        # Mirror to in-process listeners — the HTTP/WS bridge
        # subscribes here and forwards the event to every open
        # WebSocket. Stdout behaviour is unchanged.
        self.server.notify(env.model_dump(exclude_none=True))


class IPCServer:
    """JSON-RPC 2.0 over stdio server.

    Parameters
    ----------
    config:
        Runtime config. Used for ``max_message_bytes``.
    stdin, stdout:
        Streams to read from / write to. Tests can pass ``io.StringIO``
        or any object that supports ``.readline()`` and ``.write()``.
    """

    def __init__(
        self,
        config: Config,
        stdin: IO[str] | None = None,
        stdout: IO[str] | None = None,
    ) -> None:
        import sys

        self.config = config
        self.stdin: IO[str] = stdin if stdin is not None else sys.stdin
        self.stdout: IO[str] = stdout if stdout is not None else sys.stdout
        self._handlers: dict[str, Handler] = {}
        self._notification_handlers: dict[str, NotificationHandler] = {}
        self._write_lock = asyncio.Lock()
        self._stop = asyncio.Event()
        # In-process event/notification subscribers. The HTTP/WS
        # bridge adds itself here so that anything handlers emit
        # via ``ctx.emit`` / ``ctx.notify`` is mirrored to open
        # WebSocket clients. The callback is a sync callable that
        # receives a plain dict (the serialised envelope). It must
        # not block; ``notify`` wraps each call in try/except so
        # a single misbehaving listener cannot stop the others.
        self.listeners: list[Callable[[dict[str, Any]], None]] = []
        # Default handlers — registered in `register_defaults`.
        self._register_defaults()

    # -- registration -------------------------------------------------------

    def register(self, method: str, handler: Handler) -> None:
        if method in self._handlers:
            raise ValueError(f"duplicate request handler for {method!r}")
        self._handlers[method] = handler

    def register_notification(self, method: str, handler: NotificationHandler) -> None:
        if method in self._notification_handlers:
            raise ValueError(f"duplicate notification handler for {method!r}")
        self._notification_handlers[method] = handler

    def register_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Subscribe to in-process server events.

        ``callback`` is invoked (synchronously, but never allowed to
        block the dispatch loop) for every :class:`Event` and
        :class:`Notification` written by a handler. ``reply`` /
        ``reply_error`` responses are *not* fanned out — those
        belong to the originating transport.

        The callback must be cheap. Schedule any I/O via
        ``asyncio.create_task`` from inside the callback; the
        server runs on a single event loop and the callback fires
        on the handler's hot path.
        """
        if callback not in self.listeners:
            self.listeners.append(callback)

    def unregister_listener(self, callback: Callable[[dict[str, Any]], None]) -> None:
        """Remove a previously-registered listener. No-op if absent."""
        try:
            self.listeners.remove(callback)
        except ValueError:
            pass

    def notify(self, env: dict[str, Any]) -> None:
        """Fan ``env`` out to every registered listener.

        Each listener is wrapped in its own try/except so one
        misbehaving subscriber cannot prevent the rest from
        receiving the event. Iterates over a snapshot of the
        listener list so listeners may safely unregister
        themselves from within the callback.
        """
        for cb in list(self.listeners):
            try:
                cb(env)
            except Exception:  # pragma: no cover — defensive
                logger.exception("ipc listener raised; continuing")

    def _register_defaults(self) -> None:
        """Built-in handlers (always available)."""
        from .builtins import handle_ping, handle_shutdown, handle_status

        self.register("ping", handle_ping)
        self.register("status", handle_status)
        self.register("shutdown", handle_shutdown)

    # -- main loop ----------------------------------------------------------

    async def run_forever(self) -> None:
        """Block reading stdin until EOF or ``stop()`` is called."""
        logger.info("ipc server started, waiting for messages on stdin")
        loop = asyncio.get_running_loop()
        # Run blocking stdin reads in a thread so we never tie up the loop.
        try:
            while not self._stop.is_set():
                line = await loop.run_in_executor(None, self._readline_blocking)
                if line == "":
                    # EOF — Tauri sidecar closed stdin. Shut down cleanly.
                    logger.info("ipc server received EOF, shutting down")
                    break
                await self._handle_line(line)
        except asyncio.CancelledError:
            logger.info("ipc server cancelled")
            raise
        except Exception:  # pragma: no cover — defensive
            logger.exception("ipc server crashed")
        finally:
            self._stop.set()

    def _readline_blocking(self) -> str:
        """Blocking readline. Called from a thread executor.

        Tolerates a leading UTF-8 BOM (``\\ufeff``) which some
        host environments (notably Windows PowerShell ``Get-Content``)
        prepend to the first line. We strip the BOM at the start of
        the stream so JSON parsing succeeds.
        """
        try:
            line = self.stdin.readline()
        except (EOFError, ValueError):
            return ""
        if not isinstance(line, str):
            # Tauri sidecar piping bytes — decode.
            try:
                line = line.decode("utf-8", errors="replace")
            except Exception:  # pragma: no cover
                return ""
        # Strip a leading BOM if the host injected one.
        if line.startswith("\ufeff"):
            line = line.lstrip("\ufeff")
        return line

    async def _handle_line(self, line: str) -> None:
        if len(line) > self.config.max_message_bytes:
            await self._send(
                Response(
                    id=0,
                    error=RPCError(
                        code=PARSE_ERROR,
                        message="message too large",
                        data={"max": self.config.max_message_bytes},
                    ),
                ).to_bytes()
            )
            return

        try:
            obj = parse_envelope(line)
        except ValueError as exc:
            await self._send(
                Response(
                    id=0,
                    error=RPCError(code=PARSE_ERROR, message=str(exc)),
                ).to_bytes()
            )
            return

        if not isinstance(obj, dict):
            await self._send(
                Response(
                    id=0,
                    error=RPCError(
                        code=INVALID_REQUEST, message="envelope must be a JSON object"
                    ),
                ).to_bytes()
            )
            return

        # Pushed event from a peer? Just log — the spec only has
        # client→server requests, but we tolerate server→server events
        # for forward compatibility.
        if "event" in obj and "method" not in obj and "id" not in obj:
            logger.debug("ignoring inbound event: %s", obj.get("event"))
            return

        if "method" not in obj:
            await self._send(
                Response(
                    id=obj.get("id", 0),
                    error=RPCError(
                        code=INVALID_REQUEST, message="missing 'method'"
                    ),
                ).to_bytes()
            )
            return

        method = str(obj["method"])
        params = obj.get("params")
        request_id = obj.get("id")

        if request_id is None:
            # Notification — fire and forget.
            handler = self._notification_handlers.get(method)
            if handler is None:
                # Use the request handler if it can also handle notifications
                # by ignoring ``ctx.reply``. Convention: every method can be
                # invoked as a notification if registered.
                handler = self._handlers.get(method)
                if handler is None:
                    logger.debug("no handler for notification %s", method)
                    return
            ctx = Context(server=self, method=method, request_id=None)
            try:
                await handler(params, ctx)
            except Exception as exc:  # pragma: no cover — defensive
                logger.exception("notification handler %s crashed", method)
        else:
            # Request — dispatch and reply.
            handler = self._handlers.get(method)
            if handler is None:
                await self._send(
                    Response(
                        id=request_id,
                        error=RPCError(
                            code=METHOD_NOT_FOUND,
                            message=f"unknown method: {method}",
                        ),
                    ).to_bytes()
                )
                return
            ctx = Context(server=self, method=method, request_id=request_id)
            try:
                result = await handler(params, ctx)
                # Handler should have called ctx.reply, but if it returned
                # a value we auto-reply for convenience.
                if not ctx._extra.get("_replied"):
                    await ctx.reply(result)
                ctx._extra["_replied"] = True
            except HandlerError as exc:
                # Handler raised but didn't catch — convert to JSON-RPC error.
                logger.debug("handler %s raised HandlerError: %s", method, exc.message)
                await self._send(
                    Response(
                        id=request_id,
                        error=RPCError(code=exc.code, message=exc.message, data=exc.data),
                    ).to_bytes()
                )
            except Exception as exc:
                logger.exception("handler %s raised", method)
                await self._send(
                    Response(
                        id=request_id,
                        error=RPCError(
                            code=-32603, message="internal error"
                        ),
                    ).to_bytes()
                )

    # -- request API (transport-agnostic) -----------------------------------

    async def handle_request(self, obj: dict[str, Any]) -> dict[str, Any] | None:
        """Process a single parsed JSON-RPC 2.0 envelope and return the response.

        Used by the HTTP/WS bridge (``POST /rpc``) and any other
        non-stdio transport that already has a parsed object. The
        stdio path uses :meth:`_handle_line` instead — it owns
        the line framing, BOM stripping, and per-line error
        envelopes. This method only handles the dispatch.

        Returns
        -------
        dict | None
            - ``None`` when ``obj`` is a notification (or an inbound
              event the server is ignoring) — no reply expected.
            - A JSON-RPC 2.0 response dict for requests. The dict
              has the canonical ``{"jsonrpc": "2.0", "id": ..., "result"|"error": ...}``
              shape and is safe to hand to ``json.dumps``.

        Side effects
        ------------
        - Calls :meth:`notify` for any events / notifications
          emitted by the handler (via ``ctx.emit`` / ``ctx.notify``).
        - Still writes to ``self.stdout`` for parity with the
          stdio path — production HTTP setups just redirect
          stdout to a log file. Tests pass a ``StringIO`` to keep
          the buffer out of the response.
        """
        if "event" in obj and "method" not in obj and "id" not in obj:
            logger.debug("ignoring inbound event: %s", obj.get("event"))
            return None

        if "method" not in obj:
            return Response(
                id=obj.get("id", 0),
                error=RPCError(code=INVALID_REQUEST, message="missing 'method'"),
            ).model_dump(exclude_none=True)

        method = str(obj["method"])
        params = obj.get("params")
        request_id = obj.get("id")

        if request_id is None:
            # Notification — dispatch, no response.
            handler = self._notification_handlers.get(method) or self._handlers.get(method)
            if handler is None:
                logger.debug("no handler for notification %s", method)
                return None
            ctx = Context(server=self, method=method, request_id=None)
            try:
                await handler(params, ctx)
            except Exception:  # pragma: no cover — defensive
                logger.exception("notification handler %s crashed", method)
            return None

        # Request — dispatch and reply.
        handler = self._handlers.get(method)
        if handler is None:
            return Response(
                id=request_id,
                error=RPCError(
                    code=METHOD_NOT_FOUND,
                    message=f"unknown method: {method}",
                ),
            ).model_dump(exclude_none=True)

        ctx = Context(server=self, method=method, request_id=request_id)
        try:
            result = await handler(params, ctx)
            if not ctx._extra.get("_replied"):
                await ctx.reply(result)
            return ctx._extra.get("_response") or Response(
                id=request_id, result=None
            ).model_dump(exclude_none=True)
        except HandlerError as exc:
            logger.debug("handler %s raised HandlerError: %s", method, exc.message)
            return Response(
                id=request_id,
                error=RPCError(code=exc.code, message=exc.message, data=exc.data),
            ).model_dump(exclude_none=True)
        except Exception as exc:
            logger.exception("handler %s raised", method)
            return Response(
                id=request_id,
                error=RPCError(code=INTERNAL_ERROR, message="internal error"),
            ).model_dump(exclude_none=True)

    # -- outbound ----------------------------------------------------------

    async def _send(self, payload: bytes) -> None:
        async with self._write_lock:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, self._stdout_write_blocking, payload)

    def _stdout_write_blocking(self, payload: bytes) -> None:
        """Synchronous write — called from a thread executor."""
        try:
            self.stdout.write(payload.decode("utf-8") if isinstance(payload, bytes) else payload)
            self.stdout.flush()
        except (OSError, ValueError) as exc:  # pragma: no cover
            logger.error("failed to write to stdout: %s", exc)

    # -- control -----------------------------------------------------------

    def stop(self) -> None:
        """Signal the server to stop and cancel in-flight agent runs."""
        self._stop.set()
        # Cancel any active agent cores so they don't hang the
        # event loop during shutdown.
        try:
            from .builtins import _ACTIVE_RUNS

            for sid, entry in list(_ACTIVE_RUNS.items()):
                try:
                    core = entry.get("core") if isinstance(entry, dict) else entry
                    if core:
                        core.cancel()
                except Exception:
                    pass
            _ACTIVE_RUNS.clear()
        except Exception:
            pass


__all__ = ["IPCServer", "Context"]
