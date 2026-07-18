"""MCP transports — the byte-pipe layer under the JSON-RPC client.

Fusion of Grok's ``xai-grok-mcp::servers`` transport story (rmcp's
``TokioChildProcess`` + ``StreamableHttpClientTransport``) into pure
Python ``asyncio``.

Two transports ship in v0:

* :class:`StdioTransport` — spawns an external MCP server as a child
  process and talks line-delimited JSON-RPC over its stdin/stdout. This
  is the canonical MCP stdio transport and how most community servers
  (filesystem, git, computer-use, …) are launched.
* :class:`InProcessTransport` — an in-memory paired pipe used by tests
  and by the future in-process server bridge. No subprocess.

Both implement the same :class:`MCPTransport` ABC so the client
(:mod:`minimax_code.mcp.client`) is transport-agnostic.

Lifecycle contract
------------------

* ``await transport.start()`` — open the pipe (spawn / wire queues).
* ``await transport.send(msg)`` — write one JSON-RPC message.
* ``async for msg in transport.messages()`` — iterate inbound messages;
  the async iterator exits cleanly on :meth:`close`.
* ``await transport.close()`` — idempotent teardown.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)


class MCPTransportError(RuntimeError):
    """Raised when a transport fails to start, send, or recv."""


class MCPTransport(ABC):
    """Abstract bidirectional JSON-RPC pipe."""

    @abstractmethod
    async def start(self) -> None: ...

    @abstractmethod
    async def send(self, message: dict[str, Any]) -> None: ...

    @abstractmethod
    def messages(self) -> AsyncIterator[dict[str, Any]]: ...

    @abstractmethod
    async def close(self) -> None: ...


# ---------------------------------------------------------------------------
# stdio transport — subprocess + line-delimited JSON
# ---------------------------------------------------------------------------


class StdioTransport(MCPTransport):
    """Launch an MCP server as a child process and talk JSON-RPC over stdio.

    Parameters
    ----------
    command:
        Argv for the server process, e.g. ``["npx", "fs-mcp"]`` or
        ``["python", "-m", "my_mcp_server"]``.
    env:
        Extra environment variables for the child (merged over the
        current environment). Pass ``None`` to inherit as-is.
    cwd:
        Working directory for the child. Defaults to current.
    """

    def __init__(
        self,
        command: list[str],
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> None:
        if not command:
            raise ValueError("command must be a non-empty argv list")
        self._command = list(command)
        self._env = env
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
        self._reader_task: asyncio.Task[None] | None = None
        self._closed = False

    @property
    def pid(self) -> int | None:
        return self._proc.pid if self._proc is not None else None

    async def start(self) -> None:
        if self._proc is not None:
            return  # idempotent
        env: dict[str, str] | None = None
        if self._env is not None:
            env = dict(os.environ)
            env.update(self._env)
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *self._command,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=self._cwd,
            )
        except (OSError, FileNotFoundError) as exc:
            raise MCPTransportError(f"failed to spawn MCP server: {exc}") from exc
        self._reader_task = asyncio.create_task(self._read_loop())
        logger.debug("started MCP server %s pid=%s", self._command[0], self._proc.pid)

    async def _read_loop(self) -> None:
        """Pump stdout lines into the queue until EOF or close."""
        assert self._proc is not None and self._proc.stdout is not None
        while not self._closed:
            try:
                raw = await self._proc.stdout.readline()
            except Exception as exc:  # pragma: no cover — defensive
                logger.warning("stdio transport read error: %s", exc)
                break
            if not raw:
                break  # EOF
            line = raw.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                await self._queue.put(json.loads(line))
            except json.JSONDecodeError as exc:
                logger.warning("stdio transport dropping malformed line: %s", exc)
        await self._queue.put(None)  # sentinel: stream end

    async def send(self, message: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPTransportError("transport not started")
        payload = json.dumps(message, separators=(",", ":")) + "\n"
        try:
            self._proc.stdin.write(payload.encode("utf-8"))
            await self._proc.stdin.drain()
        except (BrokenPipeError, ConnectionResetError) as exc:
            raise MCPTransportError(f"failed to send: {exc}") from exc

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._queue.get()
            if item is None:
                return  # stream closed
            yield item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._reader_task is not None:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        if self._proc is not None:
            try:
                self._proc.terminate()
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except Exception:  # noqa: BLE001 — best-effort teardown
                try:
                    self._proc.kill()
                except ProcessLookupError:
                    pass


# ---------------------------------------------------------------------------
# In-process transport — paired asyncio queues (tests + server bridge)
# ---------------------------------------------------------------------------


def make_in_process_pair() -> tuple[InProcessTransport, InProcessTransport]:
    """Create two transports wired to each other (client-side ↔ server-side)."""
    a_to_b: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    b_to_a: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    return InProcessTransport(b_to_a, a_to_b), InProcessTransport(a_to_b, b_to_a)


class InProcessTransport(MCPTransport):
    """In-memory transport backed by two asyncio queues.

    Construct via :func:`make_in_process_pair` so both ends share the
    queue pair. Each transport reads from its ``inbox`` and writes to
    its ``outbox`` (which is the peer's inbox).
    """

    def __init__(
        self,
        inbox: asyncio.Queue[dict[str, Any] | None],
        outbox: asyncio.Queue[dict[str, Any] | None],
    ) -> None:
        self._inbox = inbox
        self._outbox = outbox
        self._closed = False

    async def start(self) -> None:
        return  # no-op — queues exist at construction

    async def send(self, message: dict[str, Any]) -> None:
        if self._closed:
            raise MCPTransportError("transport closed")
        await self._outbox.put(message)

    async def messages(self) -> AsyncIterator[dict[str, Any]]:
        while True:
            item = await self._inbox.get()
            if item is None:
                return
            yield item

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._outbox.put(None)  # signal peer


__all__ = [
    "InProcessTransport",
    "MCPTransport",
    "MCPTransportError",
    "StdioTransport",
    "make_in_process_pair",
]


# Keep ``sys`` referenced for type-checkers in env-rich environments; the
# import is cheap and documents that stdio semantics are platform-aware.
_ = sys.platform
