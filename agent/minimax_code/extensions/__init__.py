"""Extension system — management-plane wire DTO (R64).

Fusion of grok-build's ``xai-hooks-plugins-types`` crate: the JSON contract
the shell exchanges with the pager over the ACP for the *management plane*
of the extension system — hooks, plugins, MCP servers, and the marketplace.

This package is the **aggregated view layer**, distinct from the sibling
subsystems that own the live runtime:

* :mod:`minimax_code.hooks`          — hook *execution* (4-event recipe,
  stdin/stdout subprocess). :class:`~minimax_code.hooks.types.HookEvent`
  there is the executable subset.
* :mod:`minimax_code.plugins`        — plugin *declaration* loader
  (``plugin.json`` → :class:`~minimax_code.plugins.manifest.PluginManifest`).
* :mod:`minimax_code.mcp`            — MCP *client* runtime.

R64 ships the type layer only — the wire DTOs future IPC handlers
(``hooks.list`` / ``plugins.list`` / ``marketplace.list`` / ``*.action``)
will produce and consume. Handlers are a later round; nothing here does I/O.

Package layout
--------------

* :mod:`.types` — constants, sanitize helpers, enums, pydantic wire models.
"""

from __future__ import annotations

from .types import (
    MAX_COMPONENT_DESC_CHARS,
    MAX_COMPONENT_NAME_CHARS,
    MAX_COMPONENTS_PER_CATEGORY,
    ActionOutcome,
    AddSource,
    ClaudeInstalled,
    ClaudeMarketplace,
    CliOverride,
    ComponentCategory,
    ComponentItem,
    ConfigPath,
    HookEvent,
    HookHandlerType,
    HookInfo,
    HooksAction,
    HooksActionRequest,
    HooksListResponse,
    HookStatus,
    MarketplaceAction,
    MarketplaceActionRequest,
    MarketplaceInstallOrigin,
    MarketplaceListResponse,
    MarketplacePluginEntry,
    MarketplaceScanResult,
    McpServerInfo,
    McpServersListResponse,
    McpServerSource,
    McpSessionStatus,
    McpStatus,
    McpToolInfo,
    OutcomeStatus,
    PluginComponents,
    PluginInfo,
    PluginOrigin,
    PluginsAction,
    PluginsActionRequest,
    PluginScope,
    PluginsListResponse,
    ProjectClaude,
    ProjectGrok,
    UnknownOrigin,
    UserClaude,
    UserGrok,
    hook_event_display,
    parse_hooks_action,
    parse_marketplace_action,
    parse_plugin_origin,
    parse_plugins_action,
    strip_control_chars,
    truncate_chars,
)

__all__ = [
    "MAX_COMPONENT_DESC_CHARS",
    "MAX_COMPONENT_NAME_CHARS",
    "MAX_COMPONENTS_PER_CATEGORY",
    "ActionOutcome",
    "AddSource",
    "ClaudeInstalled",
    "ClaudeMarketplace",
    "CliOverride",
    "ComponentCategory",
    "ComponentItem",
    "ConfigPath",
    "HookEvent",
    "HookHandlerType",
    "HookInfo",
    "HookStatus",
    "HooksAction",
    "HooksActionRequest",
    "HooksListResponse",
    "MarketplaceAction",
    "MarketplaceActionRequest",
    "MarketplaceInstallOrigin",
    "MarketplaceListResponse",
    "MarketplacePluginEntry",
    "MarketplaceScanResult",
    "McpServerInfo",
    "McpServerSource",
    "McpServersListResponse",
    "McpSessionStatus",
    "McpStatus",
    "McpToolInfo",
    "OutcomeStatus",
    "PluginComponents",
    "PluginInfo",
    "PluginOrigin",
    "PluginScope",
    "PluginsAction",
    "PluginsActionRequest",
    "PluginsListResponse",
    "ProjectClaude",
    "ProjectGrok",
    "UnknownOrigin",
    "UserClaude",
    "UserGrok",
    "hook_event_display",
    "parse_hooks_action",
    "parse_marketplace_action",
    "parse_plugin_origin",
    "parse_plugins_action",
    "strip_control_chars",
    "truncate_chars",
]
