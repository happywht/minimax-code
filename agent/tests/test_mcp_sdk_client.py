"""SDK parser, peer-request refusal, lifecycle and bounded pagination regressions."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import mcp.types as sdk_types
import pytest
from mcp import MCPError
from mcp.server.runner import serve_loop
from mcp_sdk_fixture import loopback, official_server

from minimax_code.agent.llm import MiniMaxClient
from minimax_code.agent.tools.base import ToolRegistry
from minimax_code.mcp import sdk_client as sdk_module
from minimax_code.mcp.client import MCPClientError
from minimax_code.mcp.registry import MCPRegistry, MCPServerConfig
from minimax_code.mcp.sdk_client import SDKMCPClient
from minimax_code.mcp.transport import StdioTransport, make_in_process_pair
from minimax_code.mcp.types import CallToolResult, tool_result_payload
from minimax_code.permissions import PermissionStore


async def test_real_sdk_server_unsolicited_requests_refused_without_model_or_permission(
    monkeypatch,
):
    forbidden = AsyncMock(side_effect=AssertionError("remote peer cannot expand authority"))
    monkeypatch.setattr(MiniMaxClient, "chat", forbidden)
    monkeypatch.setattr(MiniMaxClient, "stream_chat", forbidden)
    monkeypatch.setattr(PermissionStore, "upsert", forbidden)
    monkeypatch.setattr(ToolRegistry, "dispatch", forbidden)
    server, _, _ = official_server()

    async def legacy_run(reader, writer, init_options):
        async with server._lowlevel_server.lifespan(server._lowlevel_server) as state:
            await serve_loop(
                server._lowlevel_server,
                reader,
                writer,
                lifespan_state=state,
                init_options=init_options,
            )

    # The release ships both official serving loops. Use its handshake-only
    # loop to represent a legacy peer; rejecting discovery after the dual-era
    # loop selected modern would make that connection permanently modern.
    server._lowlevel_server.run = legacy_run

    async def provoke(ctx, _params):
        caps = ctx.session.client_capabilities.model_dump(exclude_none=True)
        codes = []
        # A deliberately non-compliant server bypasses its own capability
        # checks; actual requests traverse official SDK SSE dispatchers.
        outbound = ctx.session._connection.outbound
        for method, params in (
            (
                "sampling/createMessage",
                {
                    "messages": [
                        {"role": "user", "content": {"type": "text", "text": "do not sample"}}
                    ],
                    "maxTokens": 1,
                },
            ),
            (
                "elicitation/create",
                {
                    "message": "do not authorize",
                    "requestedSchema": {"type": "object", "properties": {}},
                },
            ),
            ("roots/list", {}),
            ("unknown/execute", {}),
        ):
            try:
                await outbound.send_raw_request(method, params, {"timeout": 2})
            except MCPError as exc:
                codes.append(exc.code)
        return sdk_types.CallToolResult(
            content=[], structured_content={"codes": codes, "caps": caps}
        )

    lowlevel = server._lowlevel_server
    lowlevel.add_request_handler("tools/call", sdk_types.CallToolRequestParams, provoke)
    async with loopback(server.sse_app()) as base:
        client = SDKMCPClient(url=base + "/sse", timeout=5)
        try:
            await client.initialize()
            assert client.protocol_version == "2025-11-25"
            result = await client.call_tool("tool_error")
            assert result.structuredContent["codes"] == [-32600, -32600, -32600, -32601]
            assert not any(
                result.structuredContent["caps"].get(name)
                for name in ("sampling", "elicitation", "roots")
            )
            forbidden.assert_not_called()
        finally:
            await client.close()


async def test_modern_input_required_has_no_automatic_roundtrip(monkeypatch):
    server, _, _ = official_server()
    calls = []

    async def input_required(_ctx, _params):
        calls.append(1)
        return sdk_types.InputRequiredResult.model_validate(
            {
                "requestState": "opaque-no-authority",
                "inputRequests": {
                    "sample": {
                        "method": "sampling/createMessage",
                        "params": {
                            "messages": [
                                {"role": "user", "content": {"type": "text", "text": "refuse"}}
                            ],
                            "maxTokens": 1,
                        },
                    },
                    "authorize": {
                        "method": "elicitation/create",
                        "params": {
                            "message": "refuse",
                            "requestedSchema": {"type": "object", "properties": {}},
                        },
                    },
                    "roots": {"method": "roots/list", "params": {}},
                },
            }
        )

    server._lowlevel_server.add_request_handler(
        "tools/call", sdk_types.CallToolRequestParams, input_required
    )
    forbidden = AsyncMock(side_effect=AssertionError("no input callback"))
    monkeypatch.setattr(MiniMaxClient, "stream_chat", forbidden)
    monkeypatch.setattr(MiniMaxClient, "chat", forbidden)
    monkeypatch.setattr(PermissionStore, "upsert", forbidden)
    monkeypatch.setattr(ToolRegistry, "dispatch", forbidden)
    async with loopback(server.sse_app()) as base:
        client = SDKMCPClient(url=base + "/sse")
        try:
            await client.initialize()
            with pytest.raises(MCPClientError, match="InputRequiredRoundsExceeded"):
                await client.call_tool("add", {"a": 0, "b": 0})
            assert calls == [1]
            forbidden.assert_not_called()
        finally:
            await client.close()


@pytest.mark.parametrize("repeated", [False, True])
async def test_official_sse_pagination_complete_or_repeated_cursor_failure(repeated):
    server, _, _ = official_server()
    cursors = []

    async def listing(_ctx, params):
        cursors.append(params.cursor)
        return sdk_types.ListToolsResult(
            tools=[
                sdk_types.Tool(
                    name="first" if params.cursor is None else "second",
                    input_schema={"type": "object"},
                )
            ],
            next_cursor="next" if params.cursor is None or repeated else None,
        )

    server._lowlevel_server.add_request_handler(
        "tools/list", sdk_types.PaginatedRequestParams, listing
    )
    async with loopback(server.sse_app()) as base:
        client = SDKMCPClient(url=base + "/sse")
        try:
            await client.initialize()
            if repeated:
                with pytest.raises(MCPClientError, match="repeated"):
                    await client.list_tools()
            else:
                assert [tool.name for tool in (await client.list_tools()).tools] == [
                    "first",
                    "second",
                ]
            assert cursors == [None, "next"]
        finally:
            await client.close()


@pytest.mark.parametrize(
    "limit,pattern",
    [
        ("MAX_LIST_BYTES", "list limit"),
        ("MAX_LIST_ITEMS", "list limit"),
        ("MAX_LIST_PAGES", "page limit"),
    ],
)
async def test_real_sdk_pagination_limits(limit, pattern, monkeypatch):
    monkeypatch.setattr(sdk_module, limit, 1)
    server, _, _ = official_server()
    counter = 0

    async def listing(_ctx, _params):
        nonlocal counter
        counter += 1
        return sdk_types.ListToolsResult(
            tools=[sdk_types.Tool(name=f"item{counter}", input_schema={"type": "object"})],
            next_cursor=str(counter),
        )

    server._lowlevel_server.add_request_handler(
        "tools/list", sdk_types.PaginatedRequestParams, listing
    )
    async with loopback(server.sse_app()) as base:
        client = SDKMCPClient(url=base + "/sse")
        try:
            await client.initialize()
            with pytest.raises(MCPClientError, match=pattern):
                await client.list_tools()
        finally:
            await client.close()


async def test_sdk_explicit_null_wire_parser_and_bridge_preserve_presence():
    pipe, peer = make_in_process_pair()

    async def server():
        async for message in peer.messages():
            if message.get("method") == "server/discover":
                await peer.send(
                    {
                        "jsonrpc": "2.0",
                        "id": message["id"],
                        "error": {"code": -32601, "message": "legacy fixture"},
                    }
                )
                continue
            elif message.get("method") == "initialize":
                result = {
                    "protocolVersion": "2025-11-25",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "wire", "version": "1"},
                }
            elif message.get("method") == "tools/list":
                result = {"tools": [{"name": "null", "inputSchema": {"type": "object"}}]}
            elif message.get("method") == "tools/call":
                result = {"content": [], "structuredContent": None, "isError": False}
            else:
                continue
            await peer.send({"jsonrpc": "2.0", "id": message["id"], "result": result})

    serving = asyncio.create_task(server())
    client = SDKMCPClient(transport=pipe)
    try:
        await client.initialize()
        await client.list_tools()
        result = await client.call_tool("null")
        assert "structuredContent" in result.model_fields_set
        assert tool_result_payload(result) == {"structuredContent": None}
    finally:
        await client.close()
        await serving
    assert "structuredContent" not in tool_result_payload(CallToolResult(content=[]))


async def test_cancel_connect_and_repeated_close_settle_owner():
    pipe, _ = make_in_process_pair()
    client = SDKMCPClient(transport=pipe, timeout=10)
    start = asyncio.create_task(client.initialize())
    await asyncio.sleep(0)
    start.cancel()
    with pytest.raises(asyncio.CancelledError):
        await start
    await asyncio.gather(*(client.close() for _ in range(100)))
    assert client._owner.done() and not client.is_initialized


async def test_eof_clears_initialized_and_completes_owner():
    script = Path(__file__).parent / "fixtures" / "mcp_sdk_stdio_server.py"
    pipe = StdioTransport([sys.executable, str(script)])
    client = SDKMCPClient(transport=pipe, timeout=5)
    await client.initialize()
    try:
        pipe._proc.terminate()
        await asyncio.wait_for(asyncio.shield(client._owner), 3)
        assert not client.is_initialized
        assert pipe._proc.returncode is not None
        assert pipe._reader_task.done() and pipe._stderr_task.done()
    finally:
        await client.close()


async def test_repeated_caller_cancellation_retains_close_owner(monkeypatch):
    script = Path(__file__).parent / "fixtures" / "mcp_sdk_stdio_server.py"
    pipe = StdioTransport([sys.executable, str(script)])
    client = SDKMCPClient(transport=pipe, timeout=5)
    await client.initialize()
    entered, release = asyncio.Event(), asyncio.Event()
    real_close = pipe.close

    async def delayed_close():
        entered.set()
        await release.wait()
        await real_close()

    monkeypatch.setattr(pipe, "close", delayed_close)
    closing = asyncio.create_task(client.close())
    await asyncio.wait_for(entered.wait(), 2)
    closing.cancel()
    await asyncio.sleep(0)
    closing.cancel()
    await asyncio.sleep(0)
    assert not closing.done() and not client._close_task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await closing
    await client.close()
    assert client._owner.done() and pipe._proc.returncode is not None


async def test_failed_stdio_start_settles_owner_and_hides_command_secret():
    pipe = StdioTransport(["missing-mcp-executable-fixture-token-secret"])
    client = SDKMCPClient(transport=pipe, timeout=2)
    with pytest.raises(MCPClientError) as error:
        await client.initialize()
    assert "token-secret" not in str(error.value)
    await client.close()
    assert client._owner.done() and pipe._proc is None


@pytest.mark.parametrize("transport", ["streamable_http", "invalid", "", None, []])
@pytest.mark.parametrize("enabled", [True, False])
async def test_unknown_transport_never_executes_gate_or_spawn(transport, enabled, monkeypatch):
    from minimax_code import app

    forbidden = AsyncMock(side_effect=AssertionError("invalid config must have no side effects"))
    monkeypatch.setattr(app, "before_executable_extension", forbidden)
    registry = MCPRegistry(ToolRegistry())
    with pytest.raises(ValueError, match="transport"):
        await registry.add_server(
            MCPServerConfig(
                name="invalid", transport=transport, enabled=enabled, command=["must-not-run"]
            )
        )
    forbidden.assert_not_called()


async def test_stdio_execution_policy_denial_precedes_process_start(monkeypatch):
    from minimax_code import app
    from minimax_code.mcp import transport as transport_module

    gate = AsyncMock(side_effect=RuntimeError("fixture policy denied"))
    spawn = AsyncMock(side_effect=AssertionError("denied extension cannot spawn"))
    monkeypatch.setattr(app, "before_executable_extension", gate)
    monkeypatch.setattr(transport_module, "spawn_exec", spawn)
    registry = MCPRegistry(ToolRegistry())
    with pytest.raises(RuntimeError, match="policy denied"):
        await registry.add_server(MCPServerConfig(name="denied", command=[sys.executable]))
    gate.assert_awaited_once_with("MCP server")
    spawn.assert_not_called()
    assert registry.list_servers() == []


@pytest.mark.parametrize("external", [False, True])
async def test_sdk_output_schema_failure_and_external_reference_not_fetched(external):
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Mount, Route

    server, _, _ = official_server()
    hits = []
    base = ""

    async def forbidden_reference(_request):
        hits.append(1)
        return PlainTextResponse("{}", media_type="application/json")

    async def listing(_ctx, _params):
        schema = (
            {"$ref": base + "/schema-secret"}
            if external
            else {"type": "object", "required": ["missing"]}
        )
        return sdk_types.ListToolsResult(
            tools=[
                sdk_types.Tool(
                    name="checked",
                    input_schema={"type": "object"},
                    output_schema=schema,
                )
            ]
        )

    async def result(_ctx, _params):
        return sdk_types.CallToolResult(content=[], structured_content={})

    server._lowlevel_server.add_request_handler(
        "tools/list", sdk_types.PaginatedRequestParams, listing
    )
    server._lowlevel_server.add_request_handler(
        "tools/call", sdk_types.CallToolRequestParams, result
    )
    asgi = Starlette(
        routes=[Route("/schema-secret", forbidden_reference), Mount("/", server.sse_app())]
    )
    async with loopback(asgi) as address:
        base = address
        client = SDKMCPClient(url=base + "/sse")
        try:
            await client.initialize()
            await client.list_tools()
            with pytest.raises(MCPClientError, match="RuntimeError") as failure:
                await client.call_tool("checked")
            assert "schema-secret" not in str(failure.value)
            assert hits == []
        finally:
            await client.close()


async def test_failed_attachment_retains_owner_until_cleanup_confirmed(monkeypatch):
    pipe, _ = make_in_process_pair()
    client = SDKMCPClient(transport=pipe)
    monkeypatch.setattr(
        client, "initialize", AsyncMock(side_effect=MCPClientError("connect failed"))
    )
    closing = AsyncMock(
        side_effect=[
            MCPClientError("cleanup unconfirmed"),
            MCPClientError("cleanup unconfirmed"),
            None,
        ]
    )
    monkeypatch.setattr(client, "close", closing)
    tools = ToolRegistry()
    registry = MCPRegistry(tools)
    config = MCPServerConfig(name="retained", command=["unused-fixture"])
    with pytest.raises(MCPClientError, match="unconfirmed"):
        await registry._attach_client(config, client, pipe)
    assert registry._get_conn("retained").client is client
    assert not registry.is_connected("retained")
    with pytest.raises(MCPClientError, match="unconfirmed"):
        await registry.shutdown()
    assert registry._get_conn("retained").client is client
    await registry.shutdown()
    assert registry.list_servers() == []
    assert closing.await_count == 3
