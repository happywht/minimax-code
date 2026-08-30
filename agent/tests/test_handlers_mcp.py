"""Tests for ``mcp.*`` IPC handlers."""

from __future__ import annotations

import shutil
from typing import Any

import pytest

from minimax_code import app
from minimax_code.ipc.client import IPCClient
from minimax_code.mcp import MCPRegistry


class FakeMcpServersDAO:
    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def create(self, **kwargs):
        row = {
            "id": kwargs["id"],
            "name": kwargs["name"],
            "transport": kwargs.get("transport", "stdio"),
            "command": kwargs.get("command"),
            "url": kwargs.get("url"),
            "env": kwargs.get("env"),
            "enabled": kwargs.get("enabled", True),
            "bearer_token": kwargs.get("bearer_token"),
            "headers": kwargs.get("headers"),
            "oauth_client_id": kwargs.get("oauth_client_id"),
            "oauth_client_secret": kwargs.get("oauth_client_secret"),
            "oauth_scopes": kwargs.get("oauth_scopes"),
            "oauth_callback_port": kwargs.get("oauth_callback_port"),
            "tool_states": kwargs.get("tool_states"),
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
        }
        self.rows[row["id"]] = row
        return row

    async def get(self, server_id: str):
        return self.rows.get(server_id)

    async def list(self, **kwargs):
        rows = list(self.rows.values())
        if "enabled" in kwargs:
            rows = [r for r in rows if r["enabled"] == kwargs["enabled"]]
        return rows

    async def update(self, server_id: str, **kwargs):
        row = self.rows.get(server_id)
        if row is None:
            return None
        for key in (
            "name", "transport", "command", "url", "env", "enabled",
            "bearer_token", "headers", "oauth_client_id", "oauth_client_secret",
            "oauth_scopes", "oauth_callback_port", "tool_states",
        ):
            if key in kwargs and kwargs[key] is not None:
                row[key] = kwargs[key]
        return row

    async def delete(self, server_id: str):
        return self.rows.pop(server_id, None) is not None


class FakeToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Any] = {}

    def register(self, tool):
        self._tools[tool.name] = tool

    def unregister(self, name: str):
        self._tools.pop(name, None)

    def has(self, name: str):
        return name in self._tools


@pytest.fixture
def client() -> IPCClient:
    dao = FakeMcpServersDAO()
    registry = MCPRegistry(FakeToolRegistry())
    app.set_mcp_servers_dao(dao)
    app.set_mcp_registry(registry)
    return IPCClient()


@pytest.mark.asyncio
async def test_mcp_list_servers_empty(client: IPCClient) -> None:
    result = await client.request("mcp.list_servers", {})
    assert result["servers"] == []


@pytest.mark.asyncio
async def test_mcp_add_and_list_server(client: IPCClient) -> None:
    await client.request(
        "mcp.add_server",
        {
            "id": "fs",
            "name": "Filesystem",
            "transport": "stdio",
            "command": ["npx", "@modelcontextprotocol/server-filesystem", "."],
        },
    )
    result = await client.request("mcp.list_servers", {})
    assert len(result["servers"]) == 1
    assert result["servers"][0]["name"] == "Filesystem"
    # add_server attaches the server for real (it spawns the stdio command),
    # so `connected` tracks the environment: without npx the attach cannot
    # succeed; with npx it is best-effort (npm availability / network) and
    # may legitimately come back True.
    connected = result["servers"][0]["connected"]
    if shutil.which("npx") is None:
        assert connected is False
    else:
        assert isinstance(connected, bool)


@pytest.mark.asyncio
async def test_mcp_remove_server(client: IPCClient) -> None:
    await client.request(
        "mcp.add_server",
        {"id": "rm", "name": "RemoveMe", "transport": "stdio", "command": ["echo"]},
    )
    result = await client.request("mcp.remove_server", {"server_id": "rm"})
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_mcp_invoke_tool_on_disconnected_server(client: IPCClient) -> None:
    with pytest.raises(RuntimeError, match="is not connected"):
        await client.request(
            "mcp.invoke_tool",
            {"server_name": "missing", "tool_name": "read", "arguments": {}},
        )


@pytest.mark.asyncio
async def test_mcp_add_sse_server_persists_auth_fields(client: IPCClient) -> None:
    result = await client.request(
        "mcp.add_server",
        {
            "id": "remote",
            "name": "Remote",
            "transport": "sse",
            "url": "http://localhost:3001/sse",
            "bearer_token": "secret",
            "headers": {"X-Custom": "yes"},
            "oauth_client_id": "client",
            "oauth_client_secret": "cs",
            "oauth_scopes": ["read"],
            "oauth_callback_port": 8765,
        },
    )
    server = result["server"]
    assert server["transport"] == "sse"
    assert server["url"] == "http://localhost:3001/sse"
    assert server["bearer_token"] == "secret"
    assert server["headers"] == {"X-Custom": "yes"}
    assert server["oauth_client_id"] == "client"
    assert server["oauth_client_secret"] == "cs"
    assert server["oauth_scopes"] == ["read"]
    assert server["oauth_callback_port"] == 8765


@pytest.mark.asyncio
async def test_mcp_update_tool_states(client: IPCClient) -> None:
    await client.request(
        "mcp.add_server",
        {"id": "tools", "name": "Tools", "transport": "stdio", "command": ["echo"]},
    )
    result = await client.request(
        "mcp.update_server",
        {"server_id": "tools", "tool_states": {"read": True, "write": False}},
    )
    assert result["server"]["tool_states"] == {"read": True, "write": False}
