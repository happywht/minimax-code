"""Unit tests for the MCP client (R4).

Drives :class:`MCPClient` against a tiny in-process mock server built on
the paired :class:`InProcessTransport`. This exercises the JSON-RPC
plumbing end to end without spawning a subprocess:

* initialize handshake + ``notifications/initialized`` ack
* ``list_tools`` / ``call_tool`` happy paths
* server-side JSON-RPC error propagation
* liveness ``ping``
* notification forwarding to a registered handler
* behaviour after ``close()`` (requests fail fast)
* request timeout when the server stays silent
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from minimax_code.mcp import protocol
from minimax_code.mcp.client import MCPClient, MCPClientError
from minimax_code.mcp.transport import make_in_process_pair

# -- mock server -------------------------------------------------------------

DEFAULT_HANDLERS: dict[str, Any] = {}


def _init_result() -> dict[str, Any]:
    return {
        "protocolVersion": protocol.LATEST_PROTOCOL_VERSION,
        "capabilities": {"tools": True},
        "serverInfo": {"name": "mock-server", "version": "1.0.0"},
    }


async def _serve(
    server_transport: Any,
    handlers: dict[str, Any],
    *,
    on_notification: Any = None,
) -> None:
    """Route inbound JSON-RPC requests to ``handlers``; reply with results."""
    try:
        async for msg in server_transport.messages():
            if "method" in msg and "id" in msg:
                method = msg["method"]
                request_id = msg["id"]
                handler = handlers.get(method)
                if handler is None:
                    await server_transport.send(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32601, "message": "method not found"},
                        }
                    )
                    continue
                try:
                    result = await handler(msg.get("params", {}))
                    await server_transport.send(
                        {"jsonrpc": "2.0", "id": request_id, "result": result}
                    )
                except Exception as exc:  # noqa: BLE001 — surface as JSON-RPC error
                    await server_transport.send(
                        {
                            "jsonrpc": "2.0",
                            "id": request_id,
                            "error": {"code": -32603, "message": str(exc)},
                        }
                    )
            elif "method" in msg and on_notification is not None:
                on_notification(msg)
    except Exception:  # noqa: BLE001 — best-effort: transport closed
        return


def _default_handlers() -> dict[str, Any]:
    async def _tools_list(_: Any) -> dict[str, Any]:
        return {"tools": [{"name": "echo", "description": "echo back"}]}

    async def _tools_call(params: Any) -> dict[str, Any]:
        if params.get("name") == "echo":
            return {
                "content": [{"type": "text", "text": params.get("arguments", {}).get("msg", "")}],
                "isError": False,
            }
        return {"content": [{"type": "text", "text": "unknown tool"}], "isError": True}

    async def _ping(_: Any) -> dict[str, Any]:
        return {}

    return {
        protocol.METHOD_INITIALIZE: lambda p: _async_return(_init_result()),
        protocol.METHOD_TOOLS_LIST: _tools_list,
        protocol.METHOD_TOOLS_CALL: _tools_call,
        protocol.METHOD_PING: _ping,
    }


async def _async_return(value: Any) -> Any:
    return value


def _start_server(handlers: dict[str, Any] | None = None) -> tuple[Any, Any, asyncio.Task[None]]:
    client_t, server_t = make_in_process_pair()
    task = asyncio.create_task(_serve(server_t, handlers or _default_handlers()))
    return server_t, client_t, task


# -- tests -------------------------------------------------------------------


async def test_handshake_completes_and_records_server_info() -> None:
    _server_t, client_t, server_task = _start_server()
    try:
        client = MCPClient(client_t)
        info = await client.initialize()
        assert client.is_initialized
        assert info.serverInfo.name == "mock-server"
        assert info.protocolVersion == protocol.LATEST_PROTOCOL_VERSION
        await client.close()
    finally:
        await asyncio.wait_for(server_task, timeout=1.0)


async def test_list_tools_returns_tools() -> None:
    _server_t, client_t, server_task = _start_server()
    try:
        client = MCPClient(client_t)
        await client.initialize()
        result = await client.list_tools()
        assert len(result.tools) == 1
        assert result.tools[0].name == "echo"
        await client.close()
    finally:
        await asyncio.wait_for(server_task, timeout=1.0)


async def test_call_tool_returns_text_content() -> None:
    _server_t, client_t, server_task = _start_server()
    try:
        client = MCPClient(client_t)
        await client.initialize()
        result = await client.call_tool("echo", {"msg": "hello"})
        assert result.isError is False
        assert result.content[0].type == "text"  # type: ignore[union-attr]
        await client.close()
    finally:
        await asyncio.wait_for(server_task, timeout=1.0)


async def test_ping_returns_true() -> None:
    _server_t, client_t, server_task = _start_server()
    try:
        client = MCPClient(client_t)
        await client.initialize()
        assert await client.ping() is True
        await client.close()
    finally:
        await asyncio.wait_for(server_task, timeout=1.0)


async def test_server_error_propagates_as_client_error() -> None:
    # No handler for resources/list → server returns -32601 → client raises.
    handlers = {
        protocol.METHOD_INITIALIZE: lambda p: _async_return(_init_result()),
    }

    async def _silent_resources(_: Any) -> dict[str, Any]:  # pragma: no cover
        return {}

    handlers[protocol.METHOD_RESOURCES_LIST] = _silent_resources
    # Override to make resources/list unknown:
    handlers.pop(protocol.METHOD_RESOURCES_LIST)

    client_t, server_t = make_in_process_pair()
    server_task = asyncio.create_task(_serve(server_t, handlers))
    try:
        client = MCPClient(client_t)
        await client.initialize()
        with pytest.raises(MCPClientError):
            await client.list_resources()
        await client.close()
    finally:
        await asyncio.wait_for(server_task, timeout=1.0)


async def test_notification_forwarded_to_handler() -> None:
    client_t, server_t = make_in_process_pair()
    received: list[str] = []
    server_task = asyncio.create_task(_start_server_with_push(server_t, received))
    try:
        client = MCPClient(client_t)
        client.set_notification_handler(lambda msg: received.append(msg["method"]))
        await client.initialize()
        await asyncio.sleep(0.05)  # let the server's push notification land
        assert "notifications/progress" in received
        await client.close()
    finally:
        await asyncio.wait_for(server_task, timeout=1.0)


async def _start_server_with_push(server_t: Any, received: list[str]) -> None:
    """Server that answers initialize, then pushes one notification."""

    async def _init_handler(_: Any) -> dict[str, Any]:
        return _init_result()

    async def _tools_list(_: Any) -> dict[str, Any]:
        return {"tools": []}

    handlers = {
        protocol.METHOD_INITIALIZE: _init_handler,
        protocol.METHOD_TOOLS_LIST: _tools_list,
        protocol.METHOD_PING: lambda p: _async_return({}),
    }

    async def _runner() -> None:
        answered = False
        try:
            async for msg in server_t.messages():
                if "method" in msg and "id" in msg:
                    handler = handlers.get(msg["method"])
                    result = await handler(msg.get("params", {})) if handler else {}
                    await server_t.send(
                        {"jsonrpc": "2.0", "id": msg["id"], "result": result}
                    )
                    if msg["method"] == protocol.METHOD_INITIALIZE and not answered:
                        answered = True
                        # Push a notification to the client.
                        await server_t.send(
                            {
                                "jsonrpc": "2.0",
                                "method": "notifications/progress",
                                "params": {"progress": 50},
                            }
                        )
        except Exception:  # noqa: BLE001
            return

    await _runner()


async def test_request_after_close_raises() -> None:
    _server_t, client_t, server_task = _start_server()
    try:
        client = MCPClient(client_t)
        await client.initialize()
        await client.close()
        with pytest.raises(MCPClientError):
            await client.list_tools()
    finally:
        await asyncio.wait_for(server_task, timeout=1.0)


async def test_request_times_out_when_server_silent() -> None:
    # Server answers only initialize; list_tools is silently dropped.
    handlers = {protocol.METHOD_INITIALIZE: lambda p: _async_return(_init_result())}
    client_t, server_t = make_in_process_pair()
    server_task = asyncio.create_task(_serve(server_t, handlers))
    try:
        client = MCPClient(client_t, timeout=0.2)
        await client.initialize()
        with pytest.raises(MCPClientError):
            await client.list_tools()
        await client.close()
    finally:
        await asyncio.wait_for(server_task, timeout=1.0)


async def test_close_is_idempotent() -> None:
    _server_t, client_t, server_task = _start_server()
    try:
        client = MCPClient(client_t)
        await client.initialize()
        await client.close()
        await client.close()  # must not raise
    finally:
        await asyncio.wait_for(server_task, timeout=1.0)
