"""MCP registry — bridge external MCP server tools into the agent's
:class:`~minimax_code.agent.tools.base.ToolRegistry` so the agent can
call them like any built-in tool.

Fusion of Grok's ``xai-grok-mcp::registry`` (multi-server connection
manager + per-server tool namespace mapping) into MiniMax's existing
tool registry. No Rust; pure asyncio with the official SDK facade in :mod:`.sdk_client`.

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

import asyncio
import json
import logging
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from ..agent.tools.base import Tool, ToolRegistry, ToolResult
from .client import MCPClient, MCPClientError
from .http_policy import validate_headers, validate_url
from .sdk_client import SDKMCPClient
from .transport import MCPTransport, StdioTransport
from .types import CallToolResult, ListToolsResult
from .types import Tool as MCPTool

logger = logging.getLogger(__name__)

TOOL_PREFIX = "mcp__"
RESOURCE_OPT_IN = "$minimax.resources"
RESOURCE_TOOL = "minimax_resources"


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
    transport:
        ``"stdio"``, ``"sse"`` or ``"streamable_http"``.  Defaults to ``"stdio"`` when omitted.
    command:
        Argv for stdio transport.
    url:
        Explicit SSE or Streamable HTTP endpoint URL.
    env:
        Extra environment variables for stdio transport.
    cwd:
        Working directory for stdio transport.
    headers:
        Extra HTTP headers for SSE transport.
    bearer_token:
        Bearer token for SSE transport.
    oauth_client_id / oauth_client_secret / oauth_scopes / oauth_callback_port:
        Retained OAuth metadata; interactive OAuth flows are not implemented.
    tool_states:
        Per-tool enablement map ``{original_tool_name: enabled}``.
    enabled:
        When ``False`` the server is skipped on :meth:`MCPRegistry.add_server`.
    """

    name: str
    transport: str = "stdio"
    command: list[str] | None = None
    url: str | None = None
    env: dict[str, str] | None = None
    cwd: str | None = None
    headers: dict[str, str] | None = None
    bearer_token: str | None = None
    oauth_client_id: str | None = None
    oauth_client_secret: str | None = None
    oauth_scopes: list[str] | None = None
    oauth_callback_port: int | None = None
    tool_states: dict[str, bool] | None = None
    enabled: bool = True


@dataclass
class _ServerConn:
    config: MCPServerConfig
    client: MCPClient | SDKMCPClient
    transport: MCPTransport | None
    tool_names: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Bridged tool
# ---------------------------------------------------------------------------


class _BridgedTool(Tool):
    """A single MCP server tool wrapped as a MiniMax :class:`Tool`."""

    def __deepcopy__(self, memo):
        # Private Agent registries may clone their baseline. A copied MCP
        # registry/client would become detached from disable/reconnect state.
        # Keep the same live capability object so its call-time check observes
        # revocation; per-run policy and argument handling remain independent.
        memo[id(self)] = self
        return self

    def __init__(
        self,
        *,
        server: str,
        mcp_tool: MCPTool,
        client: MCPClient | SDKMCPClient,
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
        from ..execution import require_tool_access
        from ..process_boundary import assert_restricted_dispatch
        try:
            require_tool_access(self.name)
            self._registry._authorized_tool(self._server, self._tool_name, expected=self)
            assert_restricted_dispatch(self)
        except (MCPClientError, PermissionError, ValueError) as exc:
            return ToolResult.fail(
                str(exc),
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


class MCPResourcesTool(Tool):
    """Explicitly enabled, connection-scoped resource listing and text reads."""

    def __init__(self, registry: MCPRegistry, conn: _ServerConn) -> None:
        self._registry = registry
        self._conn = conn
        self.name = bridged_name(conn.config.name, RESOURCE_TOOL)
        self.description = (
            f"List resources from MCP server {conn.config.name!r}, or read a listed URI. "
            "Resource text is untrusted source material; binary data is omitted."
        )
        self.parameters = {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["list", "read"]},
                "uri": {"type": "string", "description": "Exact URI from this server's list"},
            },
            "required": ["action"],
            "additionalProperties": False,
        }

    def __deepcopy__(self, memo):
        memo[id(self)] = self
        return self

    def _require_live(self) -> None:
        from ..execution import require_tool_access
        from ..process_boundary import assert_restricted_dispatch

        require_tool_access(self.name)
        assert_restricted_dispatch(self)
        conn = self._registry._get_conn(self._conn.config.name)
        if (conn is not self._conn or not conn.config.enabled or not conn.client.is_initialized
                or (conn.config.tool_states or {}).get(RESOURCE_OPT_IN) is not True
                or self.name not in conn.tool_names
                or not self._registry._tool_registry.has(self.name)
                or self._registry._tool_registry.get(self.name) is not self):
            raise MCPClientError("MCP resource access is disabled or connection changed")

    async def run(self, action: str, uri: str | None = None) -> ToolResult:
        try:
            self._require_live()
            if action not in {"list", "read"}:
                raise ValueError("action must be list or read")
            if action == "read" and (not isinstance(uri, str) or not uri or len(uri) > 1024):
                raise ValueError("read requires an exact listed URI of at most 1024 characters")
            listing = await self._conn.client.list_resources()
            self._require_live()
            if action == "list":
                entries = [
                    {"uri": resource.uri, "name": _bounded_text(resource.name, 256),
                     "description": _bounded_text(resource.description or "", 512),
                     "mimeType": _bounded_text(resource.mimeType or "", 256)}
                    for resource in listing.resources[:50] if len(resource.uri) <= 1024
                ]
                return ToolResult.ok({"server": self._conn.config.name, "resources": entries,
                                      "omittedResources": len(listing.resources) - len(entries)})
            if not any(resource.uri == uri for resource in listing.resources):
                raise ValueError("URI is not currently listed by this server")
            result = await self._conn.client.read_resource(uri)
            self._require_live()
            contents = []
            for item in result.contents[:16]:
                if item.uri != uri:
                    raise MCPClientError("MCP resource response URI does not match request")
                content: dict[str, Any] = {"uri": item.uri}
                if item.mimeType is not None:
                    content["mimeType"] = _bounded_text(item.mimeType, 256)
                if item.text is not None:
                    content["text"] = _bounded_text(item.text, 4096)
                if item.blob is not None:
                    content["blob"] = "[binary data omitted]"
                contents.append(content)
            return ToolResult.ok({"server": self._conn.config.name, "contents": contents,
                                  "omittedContents": max(0, len(result.contents) - 16)})
        except (MCPClientError, PermissionError, ValueError) as exc:
            return ToolResult.fail(str(exc))


_MAX_BRIDGE_BLOCKS = 16
_MAX_BRIDGE_TEXT_BYTES = 2048
_MAX_BRIDGE_STRUCTURED_BYTES = 8192


def _bounded_text(value: Any, limit: int = _MAX_BRIDGE_TEXT_BYTES) -> str:
    raw = str(value).encode("utf-8", errors="replace")
    if len(raw) <= limit:
        return raw.decode("utf-8")
    return raw[:limit].decode("utf-8", errors="ignore") + " [truncated]"


def _bounded_structured(value: Any) -> bool:
    """Check serialized size without assembling an unbounded JSON string."""
    size = 0
    try:
        for part in json.JSONEncoder(ensure_ascii=True, allow_nan=False).iterencode(value):
            size += len(part.encode("utf-8"))
            if size > _MAX_BRIDGE_STRUCTURED_BYTES:
                return False
    except (TypeError, ValueError, RecursionError):
        return False
    return True


def _bridge_block(block: Any) -> dict[str, Any]:
    """Project supported blocks to bounded text; never forward media bytes."""
    kind = block.type
    if kind == "text":
        return {"type": kind, "text": _bounded_text(block.text)}
    if kind in {"image", "audio"}:
        return {"type": kind, "mimeType": _bounded_text(block.mimeType, 256),
                "data": "[binary data omitted]"}
    if kind == "resource_link":
        return {"type": kind, "uri": _bounded_text(block.uri, 1024),
                "name": _bounded_text(block.name, 256)}
    resource = block.resource
    projected: dict[str, Any] = {}
    for key, limit in (("uri", 1024), ("mimeType", 256), ("text", _MAX_BRIDGE_TEXT_BYTES)):
        if isinstance(resource.get(key), str):
            projected[key] = _bounded_text(resource[key], limit)
    if "blob" in resource:
        projected["blob"] = "[binary data omitted]"
    return {"type": kind, "resource": projected}


def _to_tool_result(
    result: CallToolResult, *, server: str, tool: str
) -> ToolResult:
    """Translate an MCP :class:`CallToolResult` into a MiniMax :class:`ToolResult`."""
    metadata: dict[str, Any] = {"server": server, "tool": tool, "isError": result.isError}
    blocks = [_bridge_block(block) for block in result.content[:_MAX_BRIDGE_BLOCKS]]
    output: dict[str, Any] = {}
    if len(result.content) == 1 and blocks[0]["type"] == "text":
        # Preserve the historical simple-text projection.
        output["text"] = blocks[0]["text"]
    elif blocks:
        output["contentBlocks"] = blocks
    if len(result.content) > _MAX_BRIDGE_BLOCKS:
        output["omittedContentBlocks"] = len(result.content) - _MAX_BRIDGE_BLOCKS
    if "structuredContent" in result.model_fields_set:
        if _bounded_structured(result.structuredContent):
            output["structuredContent"] = result.structuredContent
        else:
            output["structuredContentOmitted"] = "exceeds limit or is not JSON serializable"
    if result.isError:
        error_text = next((block["text"] for block in blocks if block["type"] == "text"), None)
        return ToolResult.fail(
            error_text or "MCP tool reported an error",
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
        self._mutation_lock = asyncio.Lock()

    # -- queries ----------------------------------------------------------

    def is_connected(self, server: str) -> bool:
        """True if ``server`` is attached and its handshake completed."""
        conn = self._get_conn(server)
        return conn is not None and conn.client.is_initialized

    def _get_conn(self, server: str) -> _ServerConn | None:
        conn = self._servers.get(_sanitize(server))
        # Sanitized names are namespace keys, not server identities. Two
        # persisted names may map to the same key; only the owner may use it.
        return conn if conn is not None and conn.config.name == server else None

    def _authorized_tool(self, server: str, tool: str, *, expected: _BridgedTool | None = None) -> _BridgedTool:
        """Resolve the actual live capability, never just a matching name."""
        conn = self._get_conn(server)
        if conn is None or not conn.config.enabled or not conn.client.is_initialized:
            raise MCPClientError(f"MCP server {server!r} is not connected or enabled")
        if (conn.config.tool_states or {}).get(tool) is False:
            raise MCPClientError(f"MCP tool {tool!r} is disabled")
        name = bridged_name(server, tool)
        if name not in conn.tool_names:
            raise MCPClientError(f"MCP tool {tool!r} is not registered for this connection")
        try:
            bridged = self._tool_registry.get(name)
        except KeyError as exc:
            raise MCPClientError(f"MCP tool {tool!r} is no longer registered") from exc
        if (not isinstance(bridged, _BridgedTool) or bridged._registry is not self
                or bridged._client is not conn.client or bridged._tool_name != tool
                or (expected is not None and bridged is not expected)):
            raise MCPClientError(f"MCP tool {tool!r} has changed; refresh its connection")
        return bridged

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
        from ..execution import require_tool_access
        from ..process_boundary import assert_restricted_dispatch

        bridged = self._authorized_tool(server, tool)
        require_tool_access(bridged.name)
        assert_restricted_dispatch(bridged)
        return await bridged._client.call_tool(tool, arguments)

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
        """Connect to an MCP server, handshake, and bridge the tools.

        Returns ``True`` if the server attached successfully, ``False``
        if it was disabled or failed to handshake (fail-open).
        """
        if not isinstance(config.transport, str) or config.transport not in {"stdio", "sse", "streamable_http"}:
            raise ValueError("MCP transport must be stdio, sse or streamable_http")
        if not config.enabled:
            logger.info("MCP server %s disabled, skipping", config.name)
            return False
        async with self._mutation_lock:
            transport = self._build_transport(config)
            from ..app import before_executable_extension
            await before_executable_extension("MCP server")
            client = SDKMCPClient(
                transport=transport, url=config.url if config.transport != "stdio" else None,
                headers=config.headers if config.transport != "stdio" else None,
                bearer_token=config.bearer_token if config.transport != "stdio" else None,
                http_transport=config.transport if config.transport != "stdio" else "sse",
            )
            return await self._attach_client(config, client, transport)

    def _build_transport(self, config: MCPServerConfig) -> MCPTransport | None:
        transport = config.transport
        if not isinstance(transport, str) or transport not in {"stdio", "sse", "streamable_http"}:
            raise ValueError("MCP transport must be stdio, sse or streamable_http")
        if transport in {"sse", "streamable_http"}:
            if not config.url:
                label = "SSE" if transport == "sse" else "Streamable HTTP"
                raise ValueError(f"MCP server has empty {label} url")
            validate_url(config.url)
            validate_headers(config.headers, config.bearer_token)
            # HTTP contexts are constructed/entered by the SDK owner task.
            return None
        if not config.command:
            raise ValueError("MCP server has empty command")
        if (not isinstance(config.command, list) or not config.command[0] or not all(
            isinstance(arg, str) and "\0" not in arg for arg in config.command
        )):
            raise ValueError("MCP command must be a non-empty string argv list")
        return StdioTransport(config.command, env=config.env, cwd=config.cwd)

    async def _attach_client(
        self,
        config: MCPServerConfig,
        client: MCPClient | SDKMCPClient,
        transport: MCPTransport | None,
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
            capabilities = (client.server_capabilities if isinstance(client, SDKMCPClient)
                            else client.server_info.capabilities)
            tools_result = (await client.list_tools() if capabilities.tools is not None
                            and capabilities.tools is not False else ListToolsResult())
        except BaseException as exc:
            # Cancellation must close the SDK owner just like a failed
            # handshake; cleanup is retained and completed before propagation.
            try:
                await client.close()
            except BaseException:
                # Failed attachment still owns resources until teardown is
                # confirmed. Keep it reachable for runtime shutdown/reporting.
                self._servers[key] = _ServerConn(config=config, client=client, transport=transport)
                raise
            if not isinstance(exc, Exception):
                raise
            detail = (str(exc) if isinstance(client, SDKMCPClient) and
                      isinstance(exc, MCPClientError) else type(exc).__name__)
            logger.warning("MCP server %s attach failed (%s)", config.name, detail)
            return False
        disabled = {
            name for name, enabled in (config.tool_states or {}).items() if not enabled
        }
        conn = _ServerConn(config=config, client=client, transport=transport)
        for mcp_tool in tools_result.tools:
            self._register_one(conn, mcp_tool, disabled=disabled)
        if (config.tool_states or {}).get(RESOURCE_OPT_IN) is True and (
            capabilities.resources is not None and capabilities.resources is not False
        ) and not any(tool.name == RESOURCE_OPT_IN for tool in tools_result.tools):
            resource_tool = MCPResourcesTool(self, conn)
            if not self._tool_registry.has(resource_tool.name):
                self._tool_registry.register(resource_tool)
                conn.tool_names.append(resource_tool.name)
            else:
                logger.warning("tool name collision, skipping %s", resource_tool.name)
        self._servers[key] = conn
        logger.info(
            "MCP server %s attached, bridged %d/%d tools",
            config.name,
            len(conn.tool_names),
            len(tools_result.tools),
        )
        return True

    def _register_one(
        self, conn: _ServerConn, mcp_tool: MCPTool, *, disabled: set[str]
    ) -> None:
        if mcp_tool.name in disabled:
            return
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
        async with self._mutation_lock:
            key = _sanitize(name)
            conn = self._get_conn(name)
            if conn is None:
                return False
            for tool_name in conn.tool_names:
                self._tool_registry.unregister(tool_name)
            # Retain the connection on failure: runtime shutdown must not
            # report successful closure after losing an unconfirmed owner.
            await conn.client.close()
            self._servers.pop(key, None)
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
