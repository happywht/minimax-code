"""MCP registry — bridge external MCP server tools into the agent's
:class:`~minimax_code.agent.tools.base.ToolRegistry` so the agent can
call them like any built-in tool.

Fusion of Grok's ``xai-grok-mcp::registry`` (multi-server connection
manager + per-server tool namespace mapping) into MiniMax's existing
tool registry. No Rust; pure asyncio on top of :mod:`.client`.

Naming convention
-----------------

Bridged tools are exposed as ``mcp__<server>__<tool>``. The double
underscore prefix keeps them out of the way of built-in tools, which
use plain snake_case names without the ``mcp__`` prefix. Both the
server and the tool token are sanitised to ``[a-z0-9_]``.

Fail-open policy
----------------

A misbehaving external server must never break agent startup:
handshake failures, list-tools failures, and tool-name collisions are
logged and skipped, not raised.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..agent.tools.base import Tool, ToolRegistry, ToolResult
from .client import MCPClient, MCPClientError
from .transport import MCPTransport, StdioTransport
from .types import CallToolResult
from .types import Tool as MCPTool

logger = logging.getLogger(__name__)

TOOL_PREFIX = "mcp__"


def _sanitize(name: str) -> str:
    """Reduce a server/tool name to a safe lowercase namespace token."""
    safe = "".join(c if c.isalnum() or c == "_" else "_" for c in name).strip("_").lower()
    return safe or "anon"


def bridged_name(server: str, tool: str) -> str:
    """Public helper — the canonical name a bridged tool gets."""
    return f"{TOOL_PREFIX}{_sanitize(server)}__{_sanitize(tool)}"


# ---------------------------------------------------------------------------
# Config + connection record
# ---------------------------------------------------------------------------


@dataclass
class MCPServerConfig:
    """Declarative recipe for one external MCP server connection.

    Attributes
    ----------
    name:
        Short human-friendly id used as the tool namespace. Sanitised
        before use, so spaces/cases are fine in config.
    command:
        Argv to launch the server (e.g. ``["npx", "@modelcontextprotocol/server-filesystem", "."]``).
    env / cwd:
        Optional overrides passed to the child process.
    enabled:
        When ``False`` the server is skipped on :meth:`MCPRegistry.add_server`.
    """

    name: str
    command: list[str]
    env: dict[str, str] | None = None
    cwd: str | None = None
    enabled: bool = True


@dataclass
class _ServerConn:
    config: MCPServerConfig
    client: MCPClient
    transport: MCPTransport
    tool_names: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Bridged tool
# ---------------------------------------------------------------------------


class _BridgedTool(Tool):
    """A single MCP server tool wrapped as a MiniMax :class:`Tool`."""

    def __init__(
        self,
        *,
        server: str,
        mcp_tool: MCPTool,
        client: MCPClient,
        registry: MCPRegistry,
    ) -> None:
        self.name = bridged_name(server, mcp_tool.name)
        self.description = mcp_tool.description or f"MCP tool {mcp_tool.name!r} from server {server!r}"
        schema = dict(mcp_tool.inputSchema)
        schema.setdefault("type", "object")
        schema.setdefault("properties", {})
        self.parameters = schema
        self._server = server
        self._tool_name = mcp_tool.name
        self._client = client
        self._registry = registry

    async def run(self, **kwargs: Any) -> ToolResult:
        if not self._registry.is_connected(self._server):
            return ToolResult.fail(
                f"MCP server {self._server!r} is not connected",
                metadata={"server": self._server, "tool": self._tool_name},
            )
        try:
            result = await self._client.call_tool(self._tool_name, kwargs)
        except MCPClientError as exc:
            return ToolResult.fail(
                str(exc),
                metadata={"server": self._server, "tool": self._tool_name},
            )
        return _to_tool_result(result, server=self._server, tool=self._tool_name)


def _to_tool_result(
    result: CallToolResult, *, server: str, tool: str
) -> ToolResult:
    """Translate an MCP :class:`CallToolResult` into a MiniMax :class:`ToolResult`."""
    metadata: dict[str, Any] = {"server": server, "tool": tool, "isError": result.isError}
    texts: list[str] = []
    structured: list[dict[str, Any]] = []
    for block in result.content:
        if block.type == "text":
            texts.append(block.text)  # type: ignore[union-attr]
        else:
            structured.append(block.model_dump())
    output: dict[str, Any] = {}
    if texts:
        output["text"] = "\n".join(texts)
    if structured:
        output["content"] = structured
    if result.isError:
        return ToolResult.fail(
            output.get("text") or "MCP tool reported an error",
            output=output or None,
            **metadata,
        )
    return ToolResult.ok(output or None, **metadata)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class MCPRegistry:
    """Manages connections to multiple MCP servers and bridges their tools."""

    def __init__(self, tool_registry: ToolRegistry) -> None:
        self._tool_registry = tool_registry
        self._servers: dict[str, _ServerConn] = {}

    # -- queries ----------------------------------------------------------

    def is_connected(self, server: str) -> bool:
        """True if ``server`` is attached and its handshake completed."""
        conn = self._servers.get(_sanitize(server))
        return conn is not None and conn.client.is_initialized

    def _get_conn(self, server: str) -> _ServerConn | None:
        return self._servers.get(_sanitize(server))

    async def list_server_tools(self, server: str) -> list[dict[str, Any]]:
        """Return the tools exposed by an attached server.

        Returns an empty list if the server is not connected.
        """
        conn = self._get_conn(server)
        if conn is None or not conn.client.is_initialized:
            return []
        try:
            tools_result = await conn.client.list_tools()
        except Exception:  # noqa: BLE001 — fail-open
            return []
        return [t.model_dump() for t in tools_result.tools]

    async def call_tool(self, server: str, tool: str, arguments: dict[str, Any]) -> CallToolResult:
        """Call ``tool`` on ``server`` with ``arguments``."""
        conn = self._get_conn(server)
        if conn is None or not conn.client.is_initialized:
            raise MCPClientError(f"MCP server {server!r} is not connected")
        return await conn.client.call_tool(tool, arguments)

    def list_servers(self) -> list[dict[str, Any]]:
        """Snapshot of attached servers (for IPC / UI)."""
        return [
            {
                "name": conn.config.name,
                "tools": list(conn.tool_names),
                "connected": conn.client.is_initialized,
            }
            for conn in self._servers.values()
        ]

    # -- mutation ---------------------------------------------------------

    async def add_server(self, config: MCPServerConfig) -> bool:
        """Launch ``config.command``, handshake, and bridge the tools.

        Returns ``True`` if the server attached successfully, ``False``
        if it was disabled or failed to handshake (fail-open).
        """
        if not config.enabled:
            logger.info("MCP server %s disabled, skipping", config.name)
            return False
        if not config.command:
            raise ValueError(f"MCP server {config.name!r} has empty command")
        transport: MCPTransport = StdioTransport(config.command, env=config.env, cwd=config.cwd)
        client = MCPClient(transport)
        return await self._attach_client(config, client, transport)

    async def _attach_client(
        self,
        config: MCPServerConfig,
        client: MCPClient,
        transport: MCPTransport,
    ) -> bool:
        """Handshake an already-built client and bridge its tools.

        Split from :meth:`add_server` so tests can inject an in-process
        transport pair instead of spawning a subprocess.
        """
        key = _sanitize(config.name)
        if key in self._servers:
            raise ValueError(f"MCP server {config.name!r} already attached")
        try:
            await client.initialize()
            tools_result = await client.list_tools()
        except Exception as exc:  # noqa: BLE001 — fail-open: never break startup
            logger.warning("MCP server %s attach failed: %s", config.name, exc)
            await client.close()
            return False
        conn = _ServerConn(config=config, client=client, transport=transport)
        for mcp_tool in tools_result.tools:
            self._register_one(conn, mcp_tool)
        self._servers[key] = conn
        logger.info(
            "MCP server %s attached, bridged %d/%d tools",
            config.name,
            len(conn.tool_names),
            len(tools_result.tools),
        )
        return True

    def _register_one(self, conn: _ServerConn, mcp_tool: MCPTool) -> None:
        tool = _BridgedTool(
            server=conn.config.name, mcp_tool=mcp_tool, client=conn.client, registry=self
        )
        if self._tool_registry.has(tool.name):
            logger.warning("tool name collision, skipping %s", tool.name)
            return
        try:
            self._tool_registry.register(tool)
        except ValueError as exc:  # pragma: no cover — defensive
            logger.warning("could not register %s: %s", tool.name, exc)
            return
        conn.tool_names.append(tool.name)

    async def remove_server(self, name: str) -> bool:
        """Detach a server: unregister its tools and close the client."""
        key = _sanitize(name)
        conn = self._servers.pop(key, None)
        if conn is None:
            return False
        for tool_name in conn.tool_names:
            self._tool_registry.unregister(tool_name)
        await conn.client.close()
        logger.info("MCP server %s removed", conn.config.name)
        return True

    async def add_many(self, configs: Iterable[MCPServerConfig]) -> int:
        """Attach a batch of servers; returns the count that attached."""
        count = 0
        for cfg in configs:
            if await self.add_server(cfg):
                count += 1
        return count

    async def shutdown(self) -> None:
        """Detach every attached server (used at agent shutdown)."""
        for conn in list(self._servers.values()):
            await self.remove_server(conn.config.name)


__all__ = [
    "MCPServerConfig",
    "MCPRegistry",
    "bridged_name",
]
