"""Tests for MCP registry per-tool enablement and transport selection."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from minimax_code.agent.tools.base import ToolRegistry
from minimax_code.mcp import protocol
from minimax_code.mcp.client import MCPClient
from minimax_code.mcp.registry import MCPRegistry, MCPServerConfig, bridged_name
from minimax_code.mcp.transport import (
    InProcessTransport,
    SSETransport,
    StdioTransport,
    make_in_process_pair,
)

CallImpl = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _init_result() -> dict[str, Any]:
    return {
        "protocolVersion": protocol.LATEST_PROTOCOL_VERSION,
        "capabilities": {"tools": True},
        "serverInfo": {"name": "mock", "version": "1.0.0"},
    }


async def _run_mock_server(
    server_t: InProcessTransport,
    *,
    tools: list[dict[str, Any]],
    on_call: CallImpl,
) -> None:
    async def _ret(value: Any) -> Any:
        return value

    async def _tools_list(_: Any) -> dict[str, Any]:
        return {"tools": tools}

    handlers: dict[str, Any] = {
        protocol.METHOD_INITIALIZE: lambda p: _ret(_init_result()),
        protocol.METHOD_TOOLS_LIST: _tools_list,
        protocol.METHOD_TOOLS_CALL: on_call,
        protocol.METHOD_PING: lambda p: _ret({}),
    }
    try:
        async for msg in server_t.messages():
            if "method" not in msg or "id" not in msg:
                continue
            handler = handlers.get(msg["method"])
            if handler is None:
                await server_t.send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32601, "message": "method not found"},
                    }
                )
                continue
            try:
                result = await handler(msg.get("params", {}))
                await server_t.send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
            except Exception as exc:  # noqa: BLE001
                await server_t.send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32603, "message": str(exc)},
                    }
                )
    except Exception:  # noqa: BLE001
        return


def _setup(
    tools: list[dict[str, Any]], on_call: CallImpl
) -> tuple[ToolRegistry, MCPRegistry, MCPClient, InProcessTransport, asyncio.Task[None]]:
    reg = ToolRegistry()
    mcp_reg = MCPRegistry(reg)
    client_t, server_t = make_in_process_pair()
    client = MCPClient(client_t)
    task = asyncio.create_task(_run_mock_server(server_t, tools=tools, on_call=on_call))
    return reg, mcp_reg, client, client_t, task


@pytest.mark.asyncio
async def test_tool_states_disable_specific_tools() -> None:
    tools = [
        {"name": "keep", "inputSchema": {"type": "object"}},
        {"name": "skip", "inputSchema": {"type": "object"}},
    ]

    async def _noop(_: Any) -> dict[str, Any]:
        return {"content": [], "isError": False}

    reg, mcp_reg, client, client_t, task = _setup(tools, _noop)
    try:
        await mcp_reg._attach_client(  # noqa: SLF001
            MCPServerConfig(
                name="srv",
                command=["mock"],
                tool_states={"skip": False},
            ),
            client,
            client_t,
        )
        assert reg.has(bridged_name("srv", "keep"))
        assert not reg.has(bridged_name("srv", "skip"))
        servers = mcp_reg.list_servers()
        assert servers[0]["tools"] == [bridged_name("srv", "keep")]
    finally:
        await mcp_reg.shutdown()
        await asyncio.wait_for(task, timeout=1.0)


def test_build_transport_stdio_requires_command() -> None:
    reg = MCPRegistry(ToolRegistry())
    with pytest.raises(ValueError, match="empty command"):
        reg._build_transport(MCPServerConfig(name="x", transport="stdio"))  # noqa: SLF001


def test_build_transport_stdio() -> None:
    reg = MCPRegistry(ToolRegistry())
    transport = reg._build_transport(MCPServerConfig(name="x", transport="stdio", command=["echo"]))  # noqa: SLF001
    assert isinstance(transport, StdioTransport)


def test_build_transport_sse_requires_url() -> None:
    reg = MCPRegistry(ToolRegistry())
    with pytest.raises(ValueError, match="empty SSE url"):
        reg._build_transport(MCPServerConfig(name="x", transport="sse"))  # noqa: SLF001


def test_build_transport_sse() -> None:
    reg = MCPRegistry(ToolRegistry())
    transport = reg._build_transport(
        MCPServerConfig(
            name="x",
            transport="sse",
            url="http://localhost/sse",
            bearer_token="tok",
            headers={"X": "1"},
        )
    )  # noqa: SLF001
    assert isinstance(transport, SSETransport)
