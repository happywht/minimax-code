"""Official MCP SDK facade; one task owns every async context in a connection.

No SDK stdio launcher is used: the custom transport reuses the project's
execution policy, credential filtering and process-tree ownership.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from typing import Any, TypeVar

import anyio
import mcp.types as sdk_types
from mcp import Client
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.message import SessionMessage

from .client import MCPClientError
from .http_policy import http_client_factory, quiet_sdk_diagnostics, validate_headers, validate_url
from .transport import MCPTransport
from .types import (
    CallToolResult,
    ListPromptsResult,
    ListResourcesResult,
    ListToolsResult,
    ReadResourceResult,
)

T = TypeVar("T")
MAX_LIST_PAGES = 100
MAX_LIST_ITEMS = 1000
MAX_LIST_BYTES = 8 * 1024 * 1024


def _failure(operation: str, exc: BaseException) -> MCPClientError:
    # Remote messages, HTTP exception text and URLs may contain configured
    # secrets. Error class/status are sufficient diagnostics at this boundary.
    # AnyIO wraps transport/handshake failures in task-group exceptions.
    # Keep the first leaf class/status, without formatting exception text.
    while isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        exc = exc.exceptions[0]
    status = getattr(getattr(exc, "response", None), "status_code", None)
    suffix = f", HTTP {status}" if isinstance(status, int) else ""
    return MCPClientError(f"MCP {operation} failed ({type(exc).__name__}{suffix})")


class _WatchedReadStream:
    """Notify the owner on EOF; SDK dispatcher EOF alone has no callback."""

    def __init__(self, stream, disconnected: Callable[[], None]) -> None:
        self._stream = stream
        self._disconnected = disconnected

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            return await self.receive()
        except anyio.EndOfStream:
            raise StopAsyncIteration from None

    async def receive(self):
        try:
            result = await self._stream.receive()
        except Exception:
            self._disconnected()
            raise
        if isinstance(result, Exception):
            self._disconnected()
        return result

    async def aclose(self):
        await self._stream.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        await self.aclose()


@asynccontextmanager
async def _watch(transport, disconnected: Callable[[], None]):
    async with transport as (reader, writer):
        yield _WatchedReadStream(reader, disconnected), writer


@asynccontextmanager
async def controlled_transport(transport: MCPTransport):
    """Adapt the existing managed pipe to the SDK's public Transport protocol."""
    incoming, reader = anyio.create_memory_object_stream[SessionMessage | Exception](8)
    writer, outgoing = anyio.create_memory_object_stream[SessionMessage](8)

    async def read() -> None:
        try:
            async for message in transport.messages():
                parsed = sdk_types.jsonrpc_message_adapter.validate_python(message)
                await incoming.send(SessionMessage(parsed))
        except Exception as exc:
            await incoming.send(_failure("stdio receive", exc))
        finally:
            await incoming.aclose()

    async def write() -> None:
        try:
            async for message in outgoing:
                await transport.send(
                    message.message.model_dump(
                        by_alias=True,
                        mode="json",
                        exclude_unset=True,
                    )
                )
        except Exception as exc:
            await incoming.send(_failure("stdio send", exc))
            await incoming.aclose()

    try:
        await transport.start()
        async with incoming, reader, writer, outgoing, anyio.create_task_group() as pumps:
            pumps.start_soon(read)
            pumps.start_soon(write)
            try:
                yield reader, writer
            finally:
                pumps.cancel_scope.cancel()
    finally:
        # No AnyIO cancel scope is entered here: the SDK/task-group scopes
        # must unwind in strict LIFO order in the same owner task.
        await transport.close()


class SDKMCPClient:
    """Compatible registry-facing operations, backed by mcp.Client 2.3.0."""

    def __init__(
        self,
        *,
        transport: MCPTransport | None = None,
        url: str | None = None,
        http_transport: str = "sse",
        headers: dict[str, str] | None = None,
        bearer_token: str | None = None,
        timeout: float = 30.0,
        close_timeout: float = 5.0,
    ) -> None:
        if (transport is None) == (url is None):
            raise ValueError("Configure one controlled stdio pipe or HTTP endpoint")
        if not isinstance(http_transport, str) or http_transport not in {"sse", "streamable_http"}:
            raise ValueError("Unknown MCP HTTP transport")
        self._http_transport = http_transport
        self._url = validate_url(url) if url is not None else None
        self._headers = validate_headers(headers, bearer_token)
        self._transport = transport
        self._timeout = timeout
        self._close_timeout = close_timeout
        self._owner: asyncio.Task[None] | None = None
        self._close_task: asyncio.Task[None] | None = None
        self._ready: asyncio.Future[None] | None = None
        self._stop = asyncio.Event()
        self._sdk: Client | None = None
        self._closing = False
        self._initialized = False
        self._close_error: MCPClientError | None = None
        self._operations: set[asyncio.Task[Any]] = set()
        self._slots = asyncio.Semaphore(8)

    @property
    def is_initialized(self) -> bool:
        return self._initialized and not self._closing

    @property
    def protocol_version(self) -> str | None:
        return self._sdk.protocol_version if self.is_initialized and self._sdk else None

    @property
    def server_info(self):
        return self._sdk.server_info if self.is_initialized and self._sdk else None

    async def initialize(self) -> None:
        if self._closing:
            raise MCPClientError("MCP client closed")
        if self._owner is None:
            self._ready = asyncio.get_running_loop().create_future()
            self._owner = asyncio.create_task(self._run(), name="mcp-sdk-owner")
        assert self._ready is not None
        try:
            await asyncio.shield(self._ready)
        except BaseException:
            await self.close()
            raise

    async def _run(self) -> None:
        assert self._ready is not None
        quiet_sdk_diagnostics()
        try:
            async with AsyncExitStack() as stack:
                if self._transport is not None:
                    transport = controlled_transport(self._transport)
                elif self._http_transport == "streamable_http":
                    # Enter the HTTP client before the SDK contexts so DELETE
                    # session termination still has an open client on exit.
                    http = await stack.enter_async_context(
                        http_client_factory(
                            self._url,
                            streamable_http=True,
                        )(headers=self._headers)
                    )
                    transport = streamable_http_client(self._url, http_client=http)
                else:
                    transport = sse_client(
                        self._url,
                        headers=self._headers,
                        timeout=self._timeout,
                        sse_read_timeout=300.0,
                        httpx_client_factory=http_client_factory(self._url),
                    )
                sdk = Client(
                    _watch(transport, self._disconnected),
                    mode="auto",
                    cache=None,
                    read_timeout_seconds=self._timeout,
                    input_required_max_rounds=0,
                    # No sampling / elicitation / roots callbacks: the SDK
                    # advertises none and rejects unsolicited requests.
                )
                async with asyncio.timeout(self._timeout):
                    await stack.enter_async_context(sdk)
                self._sdk = sdk
                self._initialized = True
                self._ready.set_result(None)
                await self._stop.wait()
        except BaseException as exc:
            if not self._ready.done():
                self._ready.set_exception(_failure("connect", exc))
            elif not isinstance(exc, asyncio.CancelledError):
                self._close_error = _failure("close", exc)
        finally:
            self._initialized = False
            self._sdk = None

    def _disconnected(self) -> None:
        self._initialized = False
        self._stop.set()

    async def _operate(self, name: str, operation: Callable[[Client], Awaitable[T]]) -> T:
        if not self.is_initialized or self._sdk is None:
            raise MCPClientError("MCP client is not connected")
        try:
            # One total deadline includes waiting for a concurrency slot and
            # every page/retry in the operation, not a reset per request.
            async with asyncio.timeout(self._timeout), self._slots:
                if not self.is_initialized or self._sdk is None:
                    raise MCPClientError("MCP client closed")
                task = asyncio.create_task(operation(self._sdk), name=f"mcp-sdk-{name}")
                self._operations.add(task)
                try:
                    return await task
                finally:
                    self._operations.discard(task)
        except asyncio.CancelledError:
            raise
        except MCPClientError:
            raise
        except Exception as exc:
            raise _failure(name, exc) from None

    async def _list(self, method: str, field: str, model):
        async def pages(sdk: Client):
            items: list[dict[str, Any]] = []
            cursors: set[str] = set()
            cursor = None
            size = 0
            for _ in range(MAX_LIST_PAGES):
                result = await getattr(sdk, method)(cursor=cursor)
                raw = result.model_dump(by_alias=True, mode="json", exclude_unset=True)
                size += len(json.dumps(raw, ensure_ascii=True).encode())
                items.extend(raw[field])
                if len(items) > MAX_LIST_ITEMS or size > MAX_LIST_BYTES:
                    raise MCPClientError("MCP list limit exceeded")
                cursor = result.next_cursor
                if cursor is None:
                    return model.model_validate({field: items})
                if len(cursor) > 4096 or cursor in cursors:
                    raise MCPClientError("MCP pagination cursor is invalid or repeated")
                cursors.add(cursor)
            raise MCPClientError("MCP pagination page limit exceeded")

        return await self._operate(method, pages)

    async def list_tools(self) -> ListToolsResult:
        result = await self._list("list_tools", "tools", ListToolsResult)
        names = [tool.name for tool in result.tools]
        if len(names) != len(set(names)):
            raise MCPClientError("MCP tool listing contains duplicate names")
        return result

    async def list_resources(self) -> ListResourcesResult:
        return await self._list("list_resources", "resources", ListResourcesResult)

    async def list_prompts(self) -> ListPromptsResult:
        return await self._list("list_prompts", "prompts", ListPromptsResult)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> CallToolResult:
        async def call(sdk: Client) -> CallToolResult:
            result = await sdk.call_tool(name, arguments or {}, read_timeout_seconds=self._timeout)
            return CallToolResult.model_validate(
                result.model_dump(
                    by_alias=True,
                    mode="json",
                    exclude_unset=True,
                )
            )

        return await self._operate("tools_call", call)

    async def read_resource(self, uri: str) -> ReadResourceResult:
        async def read(sdk: Client) -> ReadResourceResult:
            result = await sdk.read_resource(uri)
            return ReadResourceResult.model_validate(
                result.model_dump(
                    by_alias=True,
                    mode="json",
                    exclude_unset=True,
                )
            )

        return await self._operate("resources_read", read)

    async def ping(self) -> bool:
        try:
            # Modern MCP removed ping. A bounded tools/list is the liveness
            # operation available on the tool servers managed here.
            await self._operate(
                "ping",
                lambda sdk: (
                    sdk.list_tools() if sdk.protocol_version == "2026-07-28" else sdk.send_ping()
                ),
            )
            return True
        except MCPClientError:
            return False

    async def close(self) -> None:
        if self._close_task is None:
            self._closing = True
            self._close_task = asyncio.create_task(self._finish_close(), name="mcp-sdk-close")
        # The cleanup retains ownership despite repeated cancellation of a
        # caller, matching execution._finish_owned / runtime shutdown.
        cancelled = False
        while not self._close_task.done():
            try:
                await asyncio.shield(self._close_task)
            except asyncio.CancelledError:
                cancelled = True
        self._close_task.result()
        if cancelled:
            raise asyncio.CancelledError

    async def _finish_close(self) -> None:
        self._stop.set()
        operations = list(self._operations)
        for task in operations:
            task.cancel()
        if operations:
            await asyncio.gather(*operations, return_exceptions=True)
        if self._owner is not None:
            if self._ready is not None and not self._ready.done():
                self._owner.cancel()
            try:
                await asyncio.wait_for(asyncio.shield(self._owner), self._close_timeout)
            except asyncio.CancelledError:
                if not self._owner.cancelled():
                    raise
                # Owner cancelled before its coroutine started: no context
                # was entered, but initialize waiters must still settle.
                if self._ready is not None and not self._ready.done():
                    self._ready.set_exception(MCPClientError("MCP client closed"))
            except TimeoutError:
                self._owner.cancel()
                try:
                    await asyncio.wait_for(asyncio.shield(self._owner), self._close_timeout)
                except TimeoutError:
                    raise MCPClientError(
                        "MCP close timeout; owner cleanup is unconfirmed"
                    ) from None
        if self._ready is not None and self._ready.done():
            # A close-before-initialize waiter must not emit an unobserved
            # Future exception warning. initialize() still sees the exception.
            self._ready.exception()
        if self._close_error is not None:
            raise self._close_error
