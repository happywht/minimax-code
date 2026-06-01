"""Asyncio JSON-RPC 2.0 server over stdio.

Responsibilities
----------------
1. Read line-delimited JSON from stdin.
2. Classify each line as request / notification / event / response.
3. Dispatch requests and notifications to registered handlers.
4. Push responses, notifications, and events to stdout.

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
from .protocol import (
    Event,
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
        await self.server._send(
            Response(id=self.request_id, result=result).to_bytes()
        )
        self._extra["_replied"] = True

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        if self.request_id is None:
            raise RuntimeError("cannot reply_error to a notification")
        await self.server._send(
            Response(
                id=self.request_id,
                error=RPCError(code=code, message=message, data=data),
            ).to_bytes()
        )
        self._extra["_replied"] = True

    async def notify(self, method: str, params: Any = None) -> None:
        await self.server._send(Notification(method=method, params=params).to_bytes())

    async def emit(self, event: str, data: Any = None) -> None:
        await self.server._send(Event(event=event, data=data).to_bytes())


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
            except Exception as exc:
                logger.exception("handler %s raised", method)
                await self._send(
                    Response(
                        id=request_id,
                        error=RPCError(
                            code=-32603, message=f"internal error: {exc}"
                        ),
                    ).to_bytes()
                )

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
        self._stop.set()


__all__ = ["IPCServer", "Context"]
