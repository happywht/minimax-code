"""MCP client — connects to an external MCP server and speaks the protocol.

Holds one :class:`MCPTransport`, runs a background reader that demuxes
JSON-RPC responses to per-request futures and forwards notifications
to a handler. The high-level methods (:meth:`list_tools`,
:meth:`call_tool`, …) hide the request id bookkeeping.

Fusion of Grok's ``xai-grok-mcp::servers`` client lifecycle (handshake →
tool invocation → error classification → liveness) into pure asyncio.

Lifecycle
---------

1. ``client = MCPClient(transport)``
2. ``await client.initialize()`` — runs the ``initialize`` /
   ``notifications/initialized`` handshake and starts the reader.
3. ``await client.list_tools()`` / ``call_tool(...)`` / …
4. ``await client.close()`` — stops the reader, closes the transport.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from . import protocol
from .transport import MCPTransport, MCPTransportError
from .types import (
    CallToolResult,
    ClientCapabilities,
    Implementation,
    InitializeResult,
    ListPromptsResult,
    ListResourcesResult,
    ListToolsResult,
    ReadResourceResult,
)

logger = logging.getLogger(__name__)

NotificationHandler = Callable[[dict[str, Any]], Awaitable[None] | None]


class MCPClientError(RuntimeError):
    """Raised for protocol-level failures (handshake, method errors)."""


class MCPClient:
    """One connection to one MCP server."""

    def __init__(
        self,
        transport: MCPTransport,
        *,
        client_info: Implementation | None = None,
        capabilities: ClientCapabilities | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._transport = transport
        self._client_info = client_info or Implementation(
            name="minimax-code", version="0.9.0"
        )
        self._capabilities = capabilities or ClientCapabilities()
        self._timeout = timeout
        self._id = 0
        self._pending: dict[int, asyncio.Future[Any]] = {}
        self._reader: asyncio.Task[None] | None = None
        self._server_info: InitializeResult | None = None
        self._on_notification: NotificationHandler | None = None
        self._closed = False

    # -- properties --------------------------------------------------------

    @property
    def server_info(self) -> InitializeResult | None:
        return self._server_info

    @property
    def is_initialized(self) -> bool:
        return self._server_info is not None

    def set_notification_handler(self, handler: NotificationHandler | None) -> None:
        self._on_notification = handler

    # -- lifecycle --------------------------------------------------------

    async def initialize(self) -> InitializeResult:
        """Run the MCP handshake and start the inbound reader."""
        await self._transport.start()
        if self._reader is None:
            self._reader = asyncio.create_task(self._reader_loop())
        params = {
            "protocolVersion": protocol.LATEST_PROTOCOL_VERSION,
            "capabilities": self._capabilities.model_dump(exclude_none=True),
            "clientInfo": self._client_info.model_dump(),
        }
        result_raw = await self._request(protocol.METHOD_INITIALIZE, params)
        self._server_info = InitializeResult.model_validate(result_raw)
        # Acknowledge the handshake (notification — no reply expected).
        await self._notify(protocol.METHOD_INITIALIZED, {})
        logger.debug(
            "MCP handshake ok with %s %s (protocol %s)",
            self._server_info.serverInfo.name,
            self._server_info.serverInfo.version,
            self._server_info.protocolVersion,
        )
        return self._server_info

    async def ping(self) -> bool:
        """Liveness probe. Returns ``True`` on success."""
        try:
            await self._request(protocol.METHOD_PING, {})
            return True
        except (TimeoutError, MCPClientError, MCPTransportError):
            return False

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._reader is not None:
            self._reader.cancel()
            try:
                await self._reader
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
        # Fail any in-flight requests so callers don't hang.
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(MCPClientError("client closed"))
        self._pending.clear()
        await self._transport.close()

    # -- high-level operations -------------------------------------------

    async def list_tools(self) -> ListToolsResult:
        raw = await self._request(protocol.METHOD_TOOLS_LIST, {})
        return ListToolsResult.model_validate(raw)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        raw = await self._request(
            protocol.METHOD_TOOLS_CALL, {"name": name, "arguments": arguments or {}}
        )
        return CallToolResult.model_validate(raw)

    async def list_resources(self) -> ListResourcesResult:
        raw = await self._request(protocol.METHOD_RESOURCES_LIST, {})
        return ListResourcesResult.model_validate(raw)

    async def read_resource(self, uri: str) -> ReadResourceResult:
        raw = await self._request(protocol.METHOD_RESOURCES_READ, {"uri": uri})
        return ReadResourceResult.model_validate(raw)

    async def list_prompts(self) -> ListPromptsResult:
        raw = await self._request(protocol.METHOD_PROMPTS_LIST, {})
        return ListPromptsResult.model_validate(raw)

    # -- JSON-RPC plumbing -----------------------------------------------

    def _next_id(self) -> int:
        self._id += 1
        return self._id

    async def _request(self, method: str, params: dict[str, Any]) -> Any:
        if self._closed:
            raise MCPClientError("client closed")
        request_id = self._next_id()
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[Any] = loop.create_future()
        self._pending[request_id] = fut
        message = {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
        try:
            await self._transport.send(message)
        except MCPTransportError as exc:
            self._pending.pop(request_id, None)
            raise MCPClientError(f"send failed: {exc}") from exc
        try:
            return await asyncio.wait_for(fut, timeout=self._timeout)
        except TimeoutError as exc:
            self._pending.pop(request_id, None)
            raise MCPClientError(f"timeout waiting for {method!r}") from exc

    async def _notify(self, method: str, params: dict[str, Any]) -> None:
        await self._transport.send({"jsonrpc": "2.0", "method": method, "params": params})

    async def _reader_loop(self) -> None:
        """Demux inbound messages: responses → futures, notifications → handler."""
        try:
            async for msg in self._transport.messages():
                if "id" in msg and ("result" in msg or "error" in msg):
                    self._dispatch_response(msg)
                elif "method" in msg:
                    # Notification or server-initiated request (v0: log only).
                    await self._handle_notification(msg)
        except Exception as exc:  # noqa: BLE001 — reader must not die silently
            logger.warning("MCP reader loop ended: %s", exc)
        # Transport closed — fail pending.
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(MCPClientError("transport closed"))
        self._pending.clear()

    def _dispatch_response(self, msg: dict[str, Any]) -> None:
        request_id = msg["id"]
        fut = self._pending.pop(int(request_id), None)
        if fut is None or fut.done():
            return
        if msg.get("error"):
            err = msg["error"]
            fut.set_exception(
                MCPClientError(f"server error {err.get('code')}: {err.get('message')}")
            )
        else:
            fut.set_result(msg.get("result"))

    async def _handle_notification(self, msg: dict[str, Any]) -> None:
        if self._on_notification is None:
            return
        result = self._on_notification(msg)
        if asyncio.iscoroutine(result):
            await result


__all__ = ["MCPClient", "MCPClientError"]
