"""Controlled real HTTP+SSE and stdio interoperability with official mcp 2.3.0."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mcp.types import AudioContent, CallToolResult, EmbeddedResource, ResourceLink
from mcp_sdk_fixture import loopback, official_server

from minimax_code import app
from minimax_code.agent.tools.base import ToolRegistry
from minimax_code.ipc import handlers_mcp
from minimax_code.ipc.handlers_mcp import handle_mcp_invoke_tool
from minimax_code.mcp.client import MCPClientError
from minimax_code.mcp.registry import MCPRegistry, MCPServerConfig
from minimax_code.mcp.sdk_client import SDKMCPClient
from minimax_code.mcp.transport import StdioTransport


async def test_official_sse_registry_bridge_and_ipc(monkeypatch):
    server, _, _ = official_server()
    tools = ToolRegistry()
    registry = MCPRegistry(tools)
    gate = AsyncMock()
    monkeypatch.setattr(app, "before_executable_extension", gate)
    monkeypatch.setattr(handlers_mcp, "get_mcp_registry", lambda: registry)
    async with loopback(server.sse_app()) as base:
        assert await registry.add_server(
            MCPServerConfig(
                name="real",
                transport="sse",
                url=base + "/sse",
                bearer_token="fixture-only-token",
                headers={"X-Fixture": "configured"},
                tool_states={"delayed": False},
            )
        )
        try:
            gate.assert_awaited_once_with("MCP server")
            assert isinstance(registry._get_conn("real").client, SDKMCPClient)
            assert not tools.has("mcp__real__delayed")
            result = await tools.get("mcp__real__add").run(a=2, b=3)
            assert result.success and result.output["structuredContent"] == {"result": 5}
            ctx = type("Context", (), {"reply": AsyncMock(), "reply_error": AsyncMock()})()
            await handle_mcp_invoke_tool(
                {
                    "server_name": "real",
                    "tool_name": "add",
                    "arguments": {"a": 4, "b": 5},
                },
                ctx,
            )
            assert ctx.reply.await_args.args[0]["structuredContent"] == {"result": 9}
            error = await tools.get("mcp__real__tool_error").run()
            assert not error.success and error.metadata["isError"]
        finally:
            await registry.shutdown()
        assert not registry.is_connected("real")
        assert not tools.has("mcp__real__add")


@pytest.mark.parametrize("value", [{}, [], 0, False, None])
async def test_official_sse_structured_values_preserve_presence(value):
    server, _, _ = official_server()
    async with loopback(server.sse_app()) as base:
        client = SDKMCPClient(url=base + "/sse")
        try:
            await client.initialize()
            result = await client.call_tool("structured", {"value": value})
            assert result.structuredContent == value
            # Official high-level server omits a None field on serialization.
            # Explicit wire null is covered by the SDK parser replay test.
            assert ("structuredContent" in result.model_fields_set) == (value is not None)
        finally:
            await client.close()


async def test_official_sse_cross_task_cancel_timeout_close_and_reuse():
    server, entered, release = official_server()
    async with loopback(server.sse_app()) as base:
        client = SDKMCPClient(url=base + "/sse", timeout=2)
        await asyncio.create_task(client.initialize())
        blocked = asyncio.create_task(client.call_tool("delayed"))
        await asyncio.wait_for(entered.wait(), 2)
        blocked.cancel()
        with pytest.raises(asyncio.CancelledError):
            await blocked
        # Cancelling one request leaves the owner/context and unrelated IO usable.
        assert (await client.call_tool("add", {"a": 1, "b": 2})).structuredContent == {"result": 3}
        client._timeout = 0.1
        with pytest.raises(MCPClientError, match="Timeout"):
            await client.call_tool("delayed")
        release.set()
        await asyncio.gather(*(asyncio.create_task(client.close()) for _ in range(20)))
        assert not client.is_initialized
        assert client._owner.done()
        assert not client._operations
        with pytest.raises(MCPClientError, match="closed"):
            await client.initialize()


async def test_official_stdio_uses_project_spawn_and_environment(monkeypatch):
    from mcp.client import client as sdk_client_module
    from mcp.client import stdio as sdk_stdio

    from minimax_code.mcp import transport as transport_module

    forbidden = AsyncMock(side_effect=AssertionError("SDK must not own process spawn"))
    monkeypatch.setattr(sdk_stdio, "stdio_client", forbidden)
    monkeypatch.setattr(sdk_client_module, "stdio_client", forbidden, raising=False)
    real_spawn = transport_module.spawn_exec
    calls = []

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        return await real_spawn(*args, **kwargs)

    monkeypatch.setattr(transport_module, "spawn_exec", spawn)
    monkeypatch.setenv("MCP_TEST_AMBIENT_SECRET", "must-not-reach-child")
    script = Path(__file__).parent / "fixtures" / "mcp_sdk_stdio_server.py"
    pipe = StdioTransport([sys.executable, str(script)], env={"MCP_TEST_EXPLICIT": "configured"})
    client = SDKMCPClient(transport=pipe, timeout=5)
    try:
        await client.initialize()
        assert {tool.name for tool in (await client.list_tools()).tools} == {"add", "environment"}
        result = await client.call_tool("environment")
        assert result.structuredContent == {
            "MCP_TEST_AMBIENT_SECRET": None,
            "MCP_TEST_EXPLICIT": "configured",
        }
        assert len(calls) == 1
        forbidden.assert_not_called()
        proc = pipe._proc
        assert proc is not None and proc.returncode is None
    finally:
        await client.close()
        await client.close()
    assert proc.returncode is not None
    assert pipe._reader_task.done() and pipe._stderr_task.done()


async def test_official_sse_rich_content():
    from mcp.types import TextResourceContents

    server, _, _ = official_server()

    @server.tool()
    def rich() -> CallToolResult:
        return CallToolResult(
            content=[
                AudioContent(type="audio", data="AA==", mime_type="audio/wav"),
                ResourceLink(type="resource_link", name="fixture", uri="fixture://local"),
                EmbeddedResource(
                    type="resource",
                    resource=TextResourceContents(uri="fixture://text", text="inert"),
                ),
            ]
        )

    async with loopback(server.sse_app()) as base:
        client = SDKMCPClient(url=base + "/sse")
        try:
            await client.initialize()
            result = await client.call_tool("rich")
            assert [block.type for block in result.content] == [
                "audio",
                "resource_link",
                "resource",
            ]
            assert result.content[0].mimeType == "audio/wav"
        finally:
            await client.close()


async def test_registry_managed_stdio_preserves_empty_arguments(monkeypatch):
    from minimax_code.ipc.handlers_mcp import _validate_transport_command_url

    monkeypatch.setattr(app, "before_executable_extension", AsyncMock())
    script = Path(__file__).parent / "fixtures" / "mcp_sdk_stdio_server.py"
    argv = [sys.executable, str(script), "", "middle", ""]
    # Both IPC configuration and the runtime registry accept ordinary empty
    # arguments. Only the executable itself must be nonempty.
    _validate_transport_command_url("stdio", argv, None)
    registry = MCPRegistry(ToolRegistry())
    assert await registry.add_server(MCPServerConfig(name="empty-args", command=argv))
    connection = registry._get_conn("empty-args")
    proc = connection.transport._proc
    try:
        result = await registry.call_tool("empty-args", "environment", {"include_argv": True})
        assert result.structuredContent == {"argv": ["", "middle", ""]}
        assert proc.returncode is None
    finally:
        await registry.shutdown()
    assert proc.returncode is not None
