"""Unit tests for the MCP registry (R5).

Drives :class:`MCPRegistry` against in-process mock servers (paired
:class:`InProcessTransport`) so we exercise the full bridge — attach →
handshake → list_tools → wrap → register → dispatch → remove — without
spawning a subprocess.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from minimax_code.agent.tools.base import Tool, ToolRegistry
from minimax_code.mcp import protocol
from minimax_code.mcp.client import MCPClient
from minimax_code.mcp.registry import MCPRegistry, MCPServerConfig, bridged_name
from minimax_code.mcp.transport import make_in_process_pair

CallImpl = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


def _init_result() -> dict[str, Any]:
    return {
        "protocolVersion": protocol.LATEST_PROTOCOL_VERSION,
        "capabilities": {"tools": True},
        "serverInfo": {"name": "mock", "version": "1.0.0"},
    }


async def _run_mock_server(
    server_t: Any,
    *,
    tools: list[dict[str, Any]],
    on_call: CallImpl,
) -> None:
    """Minimal MCP server: initialize / tools-list / tools-call / ping."""

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
            except Exception as exc:  # noqa: BLE001 — surface as JSON-RPC error
                await server_t.send(
                    {
                        "jsonrpc": "2.0",
                        "id": msg["id"],
                        "error": {"code": -32603, "message": str(exc)},
                    }
                )
    except Exception:  # noqa: BLE001 — transport closed
        return


def _setup(
    tools: list[dict[str, Any]], on_call: CallImpl
) -> tuple[ToolRegistry, MCPRegistry, MCPClient, Any, asyncio.Task[None]]:
    reg = ToolRegistry()
    mcp_reg = MCPRegistry(reg)
    client_t, server_t = make_in_process_pair()
    client = MCPClient(client_t)
    task = asyncio.create_task(_run_mock_server(server_t, tools=tools, on_call=on_call))
    return reg, mcp_reg, client, client_t, task


# ---------------------------------------------------------------------------
# naming
# ---------------------------------------------------------------------------


def test_bridged_name_sanitises_and_prefixes() -> None:
    assert bridged_name("Filesystem", "read_file") == "mcp__filesystem__read_file"
    assert bridged_name("git-server", "log") == "mcp__git_server__log"


# ---------------------------------------------------------------------------
# attach + bridge
# ---------------------------------------------------------------------------


async def test_attach_bridges_tools_into_registry() -> None:
    tools = [
        {
            "name": "search",
            "description": "search files",
            "inputSchema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
        }
    ]

    async def _noop(_: Any) -> dict[str, Any]:
        return {"content": [], "isError": False}

    reg, mcp_reg, client, client_t, task = _setup(tools, _noop)
    try:
        ok = await mcp_reg._attach_client(  # noqa: SLF001 — exercising the injectable seam
            MCPServerConfig(name="fs", command=["mock"]), client, client_t
        )
        assert ok is True
        assert mcp_reg.is_connected("fs")
        assert reg.has("mcp__fs__search")
        names = reg.names()
        assert "mcp__fs__search" in names
    finally:
        await mcp_reg.shutdown()
        await asyncio.wait_for(task, timeout=1.0)


async def test_call_bridged_tool_via_dispatch() -> None:
    tools = [
        {
            "name": "echo",
            "description": "echo",
            "inputSchema": {"type": "object", "properties": {"msg": {"type": "string"}}},
        }
    ]

    async def _echo(params: Any) -> dict[str, Any]:
        msg = params.get("arguments", {}).get("msg", "")
        return {"content": [{"type": "text", "text": msg}], "isError": False}

    reg, mcp_reg, client, client_t, task = _setup(tools, _echo)
    try:
        await mcp_reg._attach_client(  # noqa: SLF001
            MCPServerConfig(name="srv", command=["mock"]), client, client_t
        )
        result = await reg.dispatch("mcp__srv__echo", {"msg": "hello"})
        assert result.success is True
        assert result.output == {"text": "hello"}
        assert result.metadata["server"] == "srv"
        assert result.metadata["tool"] == "echo"
    finally:
        await mcp_reg.shutdown()
        await asyncio.wait_for(task, timeout=1.0)


async def test_tool_isError_becomes_failed_result() -> None:
    tools = [{"name": "lookup", "inputSchema": {"type": "object", "properties": {}}}]

    async def _err(_: Any) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "not found"}], "isError": True}

    reg, mcp_reg, client, client_t, task = _setup(tools, _err)
    try:
        await mcp_reg._attach_client(  # noqa: SLF001
            MCPServerConfig(name="db", command=["mock"]), client, client_t
        )
        result = await reg.dispatch("mcp__db__lookup", {})
        assert result.success is False
        assert "not found" in (result.error or "")
        assert result.metadata["isError"] is True
    finally:
        await mcp_reg.shutdown()
        await asyncio.wait_for(task, timeout=1.0)


async def test_remove_server_unregisters_tools() -> None:
    tools = [{"name": "a", "inputSchema": {"type": "object"}}, {"name": "b", "inputSchema": {"type": "object"}}]

    async def _noop(_: Any) -> dict[str, Any]:
        return {"content": [], "isError": False}

    reg, mcp_reg, client, client_t, task = _setup(tools, _noop)
    try:
        await mcp_reg._attach_client(  # noqa: SLF001
            MCPServerConfig(name="srv", command=["mock"]), client, client_t
        )
        assert reg.has("mcp__srv__a") and reg.has("mcp__srv__b")
        removed = await mcp_reg.remove_server("srv")
        assert removed is True
        assert not reg.has("mcp__srv__a") and not reg.has("mcp__srv__b")
        assert mcp_reg.is_connected("srv") is False
    finally:
        await asyncio.wait_for(task, timeout=1.0)


async def test_collision_is_skipped_fail_open() -> None:
    # Pre-occupy the bridged name with a built-in-style tool.
    class _Occupier(Tool):
        name = "mcp__srv__dup"
        description = "occupier"
        parameters = {"type": "object", "properties": {}}

    reg = ToolRegistry()
    reg.register(_Occupier())
    mcp_reg = MCPRegistry(reg)

    tools = [{"name": "dup", "inputSchema": {"type": "object"}}, {"name": "other", "inputSchema": {"type": "object"}}]

    async def _noop(_: Any) -> dict[str, Any]:
        return {"content": [], "isError": False}

    client_t, server_t = make_in_process_pair()
    client = MCPClient(client_t)
    task = asyncio.create_task(_run_mock_server(server_t, tools=tools, on_call=_noop))
    try:
        await mcp_reg._attach_client(  # noqa: SLF001
            MCPServerConfig(name="srv", command=["mock"]), client, client_t
        )
        # The occupier is untouched; "other" was bridged; "dup" skipped.
        assert reg.has("mcp__srv__other")
        servers = mcp_reg.list_servers()
        assert servers[0]["tools"] == ["mcp__srv__other"]
    finally:
        await mcp_reg.shutdown()
        await asyncio.wait_for(task, timeout=1.0)


async def test_list_servers_reports_state() -> None:
    tools = [{"name": "x", "inputSchema": {"type": "object"}}]

    async def _noop(_: Any) -> dict[str, Any]:
        return {"content": [], "isError": False}

    _reg, mcp_reg, client, client_t, task = _setup(tools, _noop)
    try:
        await mcp_reg._attach_client(  # noqa: SLF001
            MCPServerConfig(name="srv", command=["mock"]), client, client_t
        )
        servers = mcp_reg.list_servers()
        assert len(servers) == 1
        assert servers[0]["name"] == "srv"
        assert servers[0]["connected"] is True
        assert servers[0]["tools"] == ["mcp__srv__x"]
    finally:
        await mcp_reg.shutdown()
        await asyncio.wait_for(task, timeout=1.0)


async def test_disabled_server_not_attached() -> None:
    reg = ToolRegistry()
    mcp_reg = MCPRegistry(reg)
    ok = await mcp_reg.add_server(
        MCPServerConfig(name="off", command=["mock"], enabled=False)
    )
    assert ok is False
    assert mcp_reg.list_servers() == []
