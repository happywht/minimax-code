"""MCP server management IPC handlers (v0.11.0).

Provides ``mcp.*`` CRUD over persisted server configs plus runtime
introspection and invocation of attached MCP servers.
"""

from __future__ import annotations

import logging
from typing import Any

from ..app import get_mcp_registry, get_mcp_servers_dao
from ..mcp import MCPClientError, MCPRegistry, MCPServerConfig
from .handler_utils import HandlerError
from .protocol import INTERNAL_ERROR, INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)


def _dao() -> Any:
    dao = get_mcp_servers_dao()
    if dao is None:
        raise HandlerError(INTERNAL_ERROR, "storage not available")
    return dao


def _registry() -> MCPRegistry | None:
    return get_mcp_registry()


def _server_row_with_status(row: dict[str, Any], registry: MCPRegistry | None) -> dict[str, Any]:
    connected = registry.is_connected(row["name"]) if registry else False
    return {
        **row,
        "connected": connected,
    }


def _config_from_row(row: dict[str, Any]) -> MCPServerConfig:
    """Build a runtime :class:`MCPServerConfig` from a persisted DAO row."""
    return MCPServerConfig(
        name=row["name"],
        transport=row.get("transport") or "stdio",
        command=row.get("command"),
        url=row.get("url"),
        env=row.get("env"),
        cwd=row.get("cwd"),
        headers=row.get("headers"),
        bearer_token=row.get("bearer_token"),
        oauth_client_id=row.get("oauth_client_id"),
        oauth_client_secret=row.get("oauth_client_secret"),
        oauth_scopes=row.get("oauth_scopes"),
        oauth_callback_port=row.get("oauth_callback_port"),
        tool_states=row.get("tool_states"),
        enabled=bool(row.get("enabled", True)),
    )


def _validate_opt_string(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise HandlerError(INVALID_PARAMS, f"'{name}' must be a string")
    return value


def _validate_opt_dict_str(value: Any, name: str) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise HandlerError(INVALID_PARAMS, f"'{name}' must be an object with string values")
    return value


def _validate_opt_list_str(value: Any, name: str) -> list[str] | None:
    if value is None:
        return None
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise HandlerError(INVALID_PARAMS, f"'{name}' must be a list of strings")
    return value


def _validate_opt_dict_bool(value: Any, name: str) -> dict[str, bool] | None:
    if value is None:
        return None
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, bool) for k, v in value.items()):
        raise HandlerError(INVALID_PARAMS, f"'{name}' must be an object with boolean values")
    return value


def _validate_opt_int(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int):
        raise HandlerError(INVALID_PARAMS, f"'{name}' must be an integer")
    return value


def _validate_transport_command_url(transport: str, command: Any, url: Any) -> None:
    if transport not in {"stdio", "sse"}:
        raise HandlerError(INVALID_PARAMS, "'transport' must be 'stdio' or 'sse'")
    if transport == "stdio" and command is not None and (not isinstance(command, list) or not command):
        raise HandlerError(INVALID_PARAMS, "'command' must be a non-empty argv list for stdio transport")
    if transport == "sse" and url is not None and (not isinstance(url, str) or not url):
        raise HandlerError(INVALID_PARAMS, "'url' must be a non-empty string for sse transport")


async def handle_mcp_list_servers(_params: Any, ctx: Context) -> None:
    """``mcp.list_servers`` — return persisted server configs + live status."""
    try:
        dao = _dao()
        registry = _registry()
        rows = await dao.list()
        await ctx.reply({"servers": [_server_row_with_status(r, registry) for r in rows]})
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
    except Exception:
        logger.exception("mcp.list_servers failed")
        await ctx.reply_error(INTERNAL_ERROR, "mcp.list_servers failed")


async def handle_mcp_add_server(params: Any, ctx: Context) -> None:
    """``mcp.add_server`` — persist a server config and optionally attach it."""
    try:
        p = params if isinstance(params, dict) else {}
        server_id = p.get("id") or p.get("name")
        name = p.get("name")
        if not isinstance(server_id, str) or not server_id:
            raise HandlerError(INVALID_PARAMS, "'id' or 'name' must be a non-empty string")
        if not isinstance(name, str) or not name:
            raise HandlerError(INVALID_PARAMS, "'name' must be a non-empty string")

        transport = p.get("transport", "stdio")
        command = p.get("command")
        url = p.get("url")
        _validate_transport_command_url(transport, command, url)

        env = _validate_opt_dict_str(p.get("env"), "env")
        enabled = bool(p.get("enabled", True))
        bearer_token = _validate_opt_string(p.get("bearer_token"), "bearer_token")
        headers = _validate_opt_dict_str(p.get("headers"), "headers")
        oauth_client_id = _validate_opt_string(p.get("oauth_client_id"), "oauth_client_id")
        oauth_client_secret = _validate_opt_string(p.get("oauth_client_secret"), "oauth_client_secret")
        oauth_scopes = _validate_opt_list_str(p.get("oauth_scopes"), "oauth_scopes")
        oauth_callback_port = _validate_opt_int(p.get("oauth_callback_port"), "oauth_callback_port")
        tool_states = _validate_opt_dict_bool(p.get("tool_states"), "tool_states")

        dao = _dao()
        existing = await dao.get(server_id)
        if existing is not None:
            raise HandlerError(INVALID_PARAMS, f"MCP server {server_id!r} already exists")

        row = await dao.create(
            id=server_id,
            name=name,
            transport=transport,
            command=command,
            url=url,
            env=env,
            enabled=enabled,
            bearer_token=bearer_token,
            headers=headers,
            oauth_client_id=oauth_client_id,
            oauth_client_secret=oauth_client_secret,
            oauth_scopes=oauth_scopes,
            oauth_callback_port=oauth_callback_port,
            tool_states=tool_states,
        )

        registry = _registry()
        if registry is not None and enabled:
            cfg = _config_from_row(row)
            try:
                await registry.add_server(cfg)
            except Exception as exc:  # noqa: BLE001 — fail-open, still persisted
                logger.warning("failed to attach MCP server %s at add time: %s", name, exc)

        await ctx.reply({"server": _server_row_with_status(row, _registry())})
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
    except Exception:
        logger.exception("mcp.add_server failed")
        await ctx.reply_error(INTERNAL_ERROR, "mcp.add_server failed")


async def handle_mcp_remove_server(params: Any, ctx: Context) -> None:
    """``mcp.remove_server`` — detach and delete a persisted server config."""
    try:
        p = params if isinstance(params, dict) else {}
        server_id = p.get("server_id")
        if not isinstance(server_id, str) or not server_id:
            raise HandlerError(INVALID_PARAMS, "'server_id' must be a non-empty string")
        dao = _dao()
        row = await dao.get(server_id)
        if row is None:
            raise HandlerError(INVALID_PARAMS, f"unknown server_id: {server_id!r}")
        registry = _registry()
        if registry is not None:
            await registry.remove_server(row["name"])
        deleted = await dao.delete(server_id)
        await ctx.reply({"ok": deleted, "server_id": server_id})
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
    except Exception:
        logger.exception("mcp.remove_server failed")
        await ctx.reply_error(INTERNAL_ERROR, "mcp.remove_server failed")


async def handle_mcp_update_server(params: Any, ctx: Context) -> None:
    """``mcp.update_server`` — update a persisted server config.

    Re-attaches the server if any runtime field changes and it is enabled.
    """
    try:
        p = params if isinstance(params, dict) else {}
        server_id = p.get("server_id")
        if not isinstance(server_id, str) or not server_id:
            raise HandlerError(INVALID_PARAMS, "'server_id' must be a non-empty string")
        dao = _dao()
        existing = await dao.get(server_id)
        if existing is None:
            raise HandlerError(INVALID_PARAMS, f"unknown server_id: {server_id!r}")

        transport = p.get("transport", existing.get("transport", "stdio"))
        command = p.get("command")
        url = p.get("url")
        _validate_transport_command_url(transport, command, url)

        name = _validate_opt_string(p.get("name"), "name")
        env = _validate_opt_dict_str(p.get("env"), "env")
        enabled = p.get("enabled")
        if enabled is not None:
            enabled = bool(enabled)
        bearer_token = _validate_opt_string(p.get("bearer_token"), "bearer_token")
        headers = _validate_opt_dict_str(p.get("headers"), "headers")
        oauth_client_id = _validate_opt_string(p.get("oauth_client_id"), "oauth_client_id")
        oauth_client_secret = _validate_opt_string(p.get("oauth_client_secret"), "oauth_client_secret")
        oauth_scopes = _validate_opt_list_str(p.get("oauth_scopes"), "oauth_scopes")
        oauth_callback_port = _validate_opt_int(p.get("oauth_callback_port"), "oauth_callback_port")
        tool_states = _validate_opt_dict_bool(p.get("tool_states"), "tool_states")

        registry = _registry()
        # If runtime fields change, detach first so we can re-attach with new config.
        runtime_fields = (
            command, url, env, enabled, transport,
            bearer_token, headers,
            oauth_client_id, oauth_client_secret, oauth_scopes, oauth_callback_port,
            tool_states,
        )
        should_reattach = any(field is not None for field in runtime_fields)
        if registry is not None and should_reattach:
            await registry.remove_server(existing["name"])

        row = await dao.update(
            server_id,
            name=name,
            transport=transport,
            command=command,
            url=url,
            env=env,
            enabled=enabled,
            bearer_token=bearer_token,
            headers=headers,
            oauth_client_id=oauth_client_id,
            oauth_client_secret=oauth_client_secret,
            oauth_scopes=oauth_scopes,
            oauth_callback_port=oauth_callback_port,
            tool_states=tool_states,
        )

        if registry is not None and row and row.get("enabled"):
            cfg = _config_from_row(row)
            try:
                await registry.add_server(cfg)
            except Exception as exc:  # noqa: BLE001
                logger.warning("failed to re-attach MCP server %s after update: %s", row["name"], exc)

        await ctx.reply({"server": _server_row_with_status(row, _registry())})
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
    except Exception:
        logger.exception("mcp.update_server failed")
        await ctx.reply_error(INTERNAL_ERROR, "mcp.update_server failed")


async def handle_mcp_list_tools(params: Any, ctx: Context) -> None:
    """``mcp.list_tools`` — list tools exposed by an attached MCP server."""
    try:
        p = params if isinstance(params, dict) else {}
        server_name = p.get("server_name")
        if not isinstance(server_name, str) or not server_name:
            raise HandlerError(INVALID_PARAMS, "'server_name' must be a non-empty string")
        registry = _registry()
        if registry is None or not registry.is_connected(server_name):
            raise HandlerError(INVALID_PARAMS, f"MCP server {server_name!r} is not connected")
        tools = await registry.list_server_tools(server_name)
        await ctx.reply({"server_name": server_name, "tools": tools})
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
    except Exception:
        logger.exception("mcp.list_tools failed")
        await ctx.reply_error(INTERNAL_ERROR, "mcp.list_tools failed")


async def handle_mcp_invoke_tool(params: Any, ctx: Context) -> None:
    """``mcp.invoke_tool`` — invoke a tool on an attached MCP server."""
    try:
        p = params if isinstance(params, dict) else {}
        server_name = p.get("server_name")
        tool_name = p.get("tool_name")
        arguments = p.get("arguments") or {}
        if not isinstance(server_name, str) or not server_name:
            raise HandlerError(INVALID_PARAMS, "'server_name' must be a non-empty string")
        if not isinstance(tool_name, str) or not tool_name:
            raise HandlerError(INVALID_PARAMS, "'tool_name' must be a non-empty string")
        if not isinstance(arguments, dict):
            raise HandlerError(INVALID_PARAMS, "'arguments' must be an object")
        registry = _registry()
        if registry is None:
            raise HandlerError(INTERNAL_ERROR, "MCP registry not available")
        try:
            result = await registry.call_tool(server_name, tool_name, arguments)
        except MCPClientError as exc:
            raise HandlerError(INTERNAL_ERROR, str(exc)) from exc
        texts: list[str] = []
        structured: list[dict[str, Any]] = []
        for block in result.content:
            if block.type == "text":
                texts.append(block.text)  # type: ignore[union-attr]
            else:
                structured.append(block.model_dump())
        await ctx.reply(
            {
                "ok": not result.isError,
                "server_name": server_name,
                "tool_name": tool_name,
                "text": "\n".join(texts) if texts else None,
                "content": structured if structured else None,
                "isError": result.isError,
            }
        )
    except HandlerError as exc:
        await ctx.reply_error(exc.code, exc.message, exc.data)
    except Exception:
        logger.exception("mcp.invoke_tool failed")
        await ctx.reply_error(INTERNAL_ERROR, "mcp.invoke_tool failed")


def register_mcp_handlers(server: Any) -> None:
    """Register ``mcp.*`` handlers on ``server``."""
    server.register("mcp.list_servers", handle_mcp_list_servers)
    server.register("mcp.add_server", handle_mcp_add_server)
    server.register("mcp.update_server", handle_mcp_update_server)
    server.register("mcp.remove_server", handle_mcp_remove_server)
    server.register("mcp.list_tools", handle_mcp_list_tools)
    server.register("mcp.invoke_tool", handle_mcp_invoke_tool)
