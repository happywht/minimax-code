"""Extension management-plane wire DTO — fusion of grok-build's
``xai-hooks-plugins-types`` crate (R64).

This module is the **pure type layer** for the extension system's management
plane: the JSON contract future IPC handlers (``hooks.list`` / ``plugins.list``
/ ``mcp.list`` / ``marketplace.list`` / ``*.action``) will produce and consume.
Nothing here does I/O — no subprocess, no filesystem, no network.

Wire-shape fidelity
-------------------

* Structs serialize ``camelCase`` (grok ``#[serde(rename_all = "camelCase")]``)
  via :class:`_Wire` (``alias_generator = to_camel``).
* Tagged-union variants serialize their *tag* snake_case and their *fields*
  in the original snake_case — serde's enum ``rename_all`` renames variants
  only, not struct-variant fields. So :class:`_Variant` uses **no** alias
  generator. (e.g. ``HooksAction.Enable { hook_name }`` →
  ``{"type":"enable","hook_name":"..."}``, not ``hookName``.)
* ``serde(other)`` forward-compat on :class:`PluginOrigin` is delivered by
  :func:`parse_plugin_origin`, which degrades unknown tags to
  :class:`UnknownOrigin` rather than rejecting them.

Distinct from the three sibling extension subsystems
-----------------------------------------------------

* :mod:`minimax_code.hooks` — hook *execution* (4-event recipe). Its
  :class:`~minimax_code.hooks.types.HookEvent` is the executable subset; the
  :class:`HookEvent` here is the management-plane wire vocabulary (14 events).
* :mod:`minimax_code.plugins` — plugin *declaration* loader
  (:class:`~minimax_code.plugins.manifest.PluginManifest` on disk).
* :mod:`minimax_code.mcp` — MCP *client* runtime.
"""

from __future__ import annotations

import unicodedata
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel

# ---------------------------------------------------------------------------
# Constants (mirror grok's module-level consts)
# ---------------------------------------------------------------------------

MAX_COMPONENT_NAME_CHARS = 120
MAX_COMPONENT_DESC_CHARS = 120
#: Maximum items kept per component category when sanitizing catalog data.
MAX_COMPONENTS_PER_CATEGORY = 50


# ---------------------------------------------------------------------------
# Sanitize helpers — defend against terminal-escape injection from
# catalog-supplied strings. Direct port of grok's private fns.
# ---------------------------------------------------------------------------

#: Zero-width / bidi / BOM code points grok strips on top of control chars.
#: (U+200B–200F, U+202A–202E, U+2066–2069, U+FEFF)
_BIDI_INVISIBLE_RANGES: tuple[tuple[int, int], ...] = (
    (0x200B, 0x200F),
    (0x202A, 0x202E),
    (0x2066, 0x2069),
)
_BIDI_INVISIBLE_POINTS: frozenset[int] = frozenset({0xFEFF})


def _is_bidi_or_invisible(code_point: int) -> bool:
    if code_point in _BIDI_INVISIBLE_POINTS:
        return True
    return any(lo <= code_point <= hi for lo, hi in _BIDI_INVISIBLE_RANGES)


def strip_control_chars(s: str) -> str:
    """Remove Unicode control chars (Cc) plus bidi/zero-width/BOM points.

    Mirrors Rust's ``char::is_control`` (General Category Cc) and grok's
    explicit bidi/zero-width ranges. Keeps everything else — including
    printable CJK and emoji — intact.
    """
    return "".join(
        c
        for c in s
        if unicodedata.category(c) != "Cc" and not _is_bidi_or_invisible(ord(c))
    )


def truncate_chars(s: str, max_chars: int) -> str:
    """Truncate to ``max_chars`` Unicode scalar values (code-point boundary)."""
    if len(s) <= max_chars:
        return s
    return s[:max_chars]


# ---------------------------------------------------------------------------
# Simple enums — snake_case wire (grok ``rename_all = "snake_case"``)
# ---------------------------------------------------------------------------


class PluginScope(StrEnum):
    """Where a plugin lives in the config hierarchy."""

    CLI = "cli"
    PROJECT = "project"
    USER = "user"
    CONFIG = "config"


class HookEvent(StrEnum):
    """Management-plane hook-event vocabulary (14 variants).

    NOTE: this is the wire vocabulary for listing/management. The executable
    subset actually fired by :mod:`minimax_code.hooks` is the 4-variant
    :class:`~minimax_code.hooks.types.HookEvent` — a different type in a
    different namespace. Do not confuse the two.
    """

    SESSION_START = "session_start"
    SESSION_END = "session_end"
    STOP = "stop"
    STOP_FAILURE = "stop_failure"
    PRE_TOOL_USE = "pre_tool_use"
    POST_TOOL_USE = "post_tool_use"
    POST_TOOL_USE_FAILURE = "post_tool_use_failure"
    PERMISSION_DENIED = "permission_denied"
    USER_PROMPT_SUBMIT = "user_prompt_submit"
    NOTIFICATION = "notification"
    SUBAGENT_START = "subagent_start"
    SUBAGENT_STOP = "subagent_stop"
    PRE_COMPACT = "pre_compact"
    POST_COMPACT = "post_compact"


_HOOK_EVENT_DISPLAY: dict[HookEvent, str] = {
    HookEvent.SESSION_START: "Session Start",
    HookEvent.SESSION_END: "Session End",
    HookEvent.STOP: "Stop",
    HookEvent.STOP_FAILURE: "Stop Failure",
    HookEvent.PRE_TOOL_USE: "Pre-Tool Use",
    HookEvent.POST_TOOL_USE: "Post-Tool Use",
    HookEvent.POST_TOOL_USE_FAILURE: "Post-Tool Use Failure",
    HookEvent.PERMISSION_DENIED: "Permission Denied",
    HookEvent.USER_PROMPT_SUBMIT: "User Prompt Submit",
    HookEvent.NOTIFICATION: "Notification",
    HookEvent.SUBAGENT_START: "Subagent Start",
    HookEvent.SUBAGENT_STOP: "Subagent Stop",
    HookEvent.PRE_COMPACT: "Pre-Compact",
    HookEvent.POST_COMPACT: "Post-Compact",
}


def hook_event_display(event: HookEvent) -> str:
    """Human-readable form of a :class:`HookEvent` (grok ``Display`` impl)."""
    return _HOOK_EVENT_DISPLAY[event]


class HookHandlerType(StrEnum):
    """How a hook is invoked."""

    COMMAND = "command"
    HTTP = "http"


class HookStatus(StrEnum):
    """Aggregated hook status for a plugin (shared with MCP)."""

    ACTIVE = "active"
    ACTIVE_INLINE = "active_inline"
    BLOCKED = "blocked"
    NONE = "none"


class McpStatus(StrEnum):
    """Aggregated MCP status for a plugin (shared vocabulary with hooks)."""

    ACTIVE = "active"
    ACTIVE_INLINE = "active_inline"
    BLOCKED = "blocked"
    NONE = "none"


class OutcomeStatus(StrEnum):
    """Machine-readable outcome of a management action."""

    SUCCESS = "success"
    VALIDATION_ERROR = "validation_error"
    CONFIRMATION_REQUIRED = "confirmation_required"
    NOT_FOUND = "not_found"
    INTERNAL_ERROR = "internal_error"
    UNSUPPORTED = "unsupported"


class McpServerSource(StrEnum):
    """Where an MCP server config comes from."""

    MANAGED = "managed"
    LOCAL = "local"


class McpSessionStatus(StrEnum):
    """Session-level readiness of an MCP server."""

    READY = "ready"
    INITIALIZING = "initializing"
    UNAVAILABLE = "unavailable"


class ComponentCategory(StrEnum):
    """One of the six plugin-component categories.

    Not a serde type in grok (no ``Serialize``/``Deserialize`` derive) — this
    is the canonical category enumeration for in-process consumers. Values are
    snake_case to align with :class:`PluginComponents` field names.
    """

    SKILLS = "skills"
    COMMANDS = "commands"
    AGENTS = "agents"
    MCP_SERVERS = "mcp_servers"
    HOOKS = "hooks"
    LSP_SERVERS = "lsp_servers"


# ---------------------------------------------------------------------------
# Wire-model base classes
# ---------------------------------------------------------------------------


class _Wire(BaseModel):
    """CamelCase wire model for grok ``rename_all = "camelCase"`` structs."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="ignore",
    )


class _Variant(BaseModel):
    """Tagged-union variant for grok ``tag = "type"`` enums.

    The ``type`` discriminator serializes snake_case (the variant name); the
    variant's own fields keep their original snake_case names. No alias
    generator here — serde's enum ``rename_all`` renames variants, not
    struct-variant fields.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="ignore",
    )


# ---------------------------------------------------------------------------
# Component inventory (from marketplace catalogs)
# ---------------------------------------------------------------------------


class ComponentItem(_Wire):
    """One concrete thing a plugin provides (a skill, command, agent, ...).

    Mirrors grok ``ComponentItem`` (derives ``Default``). Serde bypasses the
    sanitizing constructor, so consumers of catalog-derived data must call
    :meth:`sanitize` at ingestion.
    """

    name: str = ""
    description: str | None = None

    @classmethod
    def new(cls, name: str, description: str | None = None) -> ComponentItem:
        """Build an item with control chars stripped + description truncated."""
        item = cls(name=name, description=description)
        item.sanitize()
        return item

    def sanitize(self) -> None:
        """Strip control chars + truncate; drop an empty description."""
        self.name = truncate_chars(strip_control_chars(self.name), MAX_COMPONENT_NAME_CHARS)
        if self.description is not None:
            cleaned = truncate_chars(
                strip_control_chars(self.description), MAX_COMPONENT_DESC_CHARS
            )
            self.description = cleaned or None


class PluginComponents(_Wire):
    """Full inventory of a plugin's components from a marketplace catalog.

    Every list defaults to empty (grok derives ``Default``). Consumers that
    render catalog-derived data to a terminal must call :meth:`sanitize` at
    the ingestion point — serde bypasses :meth:`ComponentItem.new`.
    """

    skills: list[ComponentItem] = Field(default_factory=list)
    commands: list[ComponentItem] = Field(default_factory=list)
    agents: list[ComponentItem] = Field(default_factory=list)
    mcp_servers: list[ComponentItem] = Field(default_factory=list)
    #: ``name`` = hook event (e.g. "PreToolUse"), ``description`` = matcher.
    hooks: list[ComponentItem] = Field(default_factory=list)
    lsp_servers: list[ComponentItem] = Field(default_factory=list)

    def categories(self) -> list[tuple[ComponentCategory, list[ComponentItem]]]:
        """Canonical category list (source of truth for fields + order)."""
        return [
            (ComponentCategory.SKILLS, self.skills),
            (ComponentCategory.COMMANDS, self.commands),
            (ComponentCategory.AGENTS, self.agents),
            (ComponentCategory.MCP_SERVERS, self.mcp_servers),
            (ComponentCategory.HOOKS, self.hooks),
            (ComponentCategory.LSP_SERVERS, self.lsp_servers),
        ]

    def is_empty(self) -> bool:
        """True when every category is empty."""
        return all(not items for _, items in self.categories())

    def summary_line(self) -> str | None:
        """One-line summary like ``"3 skills · 1 MCP server · 2 commands"``.

        Omits empty categories; returns ``None`` when there is nothing to show.
        The separator is a middle dot (U+00B7).
        """
        singular_plural: dict[ComponentCategory, tuple[str, str]] = {
            ComponentCategory.SKILLS: ("skill", "skills"),
            ComponentCategory.COMMANDS: ("command", "commands"),
            ComponentCategory.AGENTS: ("agent", "agents"),
            ComponentCategory.MCP_SERVERS: ("MCP server", "MCP servers"),
            ComponentCategory.HOOKS: ("hook", "hooks"),
            ComponentCategory.LSP_SERVERS: ("LSP server", "LSP servers"),
        }
        parts: list[str] = []
        for category, items in self.categories():
            if not items:
                continue
            singular, plural = singular_plural[category]
            label = singular if len(items) == 1 else plural
            parts.append(f"{len(items)} {label}")
        if not parts:
            return None
        return " · ".join(parts)

    def sanitize(self) -> None:
        """Strip control chars, truncate descriptions, cap each category."""
        for _category, items in self.categories():
            if len(items) > MAX_COMPONENTS_PER_CATEGORY:
                # Truncate in place so the cap is visible on the same list ref.
                items[MAX_COMPONENTS_PER_CATEGORY:] = []
            for item in items:
                item.sanitize()


# ---------------------------------------------------------------------------
# PluginOrigin — tagged union (tag="type", snake_case) with serde(other)
# ---------------------------------------------------------------------------


class CliOverride(_Variant):
    type: Literal["cli_override"] = "cli_override"


class ProjectGrok(_Variant):
    type: Literal["project_grok"] = "project_grok"


class ProjectClaude(_Variant):
    type: Literal["project_claude"] = "project_claude"


class UserGrok(_Variant):
    type: Literal["user_grok"] = "user_grok"


class UserClaude(_Variant):
    type: Literal["user_claude"] = "user_claude"


class ClaudeMarketplace(_Variant):
    type: Literal["claude_marketplace"] = "claude_marketplace"
    marketplace: str


class ClaudeInstalled(_Variant):
    type: Literal["claude_installed"] = "claude_installed"
    marketplace: str | None = None


class MarketplaceInstallOrigin(_Variant):
    """Origin: installed from a marketplace (grok ``PluginOrigin::MarketplaceInstall``)."""

    type: Literal["marketplace_install"] = "marketplace_install"
    source_name: str | None = None
    git_url: str | None = None


class ConfigPath(_Variant):
    type: Literal["config_path"] = "config_path"


class UnknownOrigin(_Variant):
    """Forward-compat sink for unrecognized origins (grok ``#[serde(other)]``)."""

    type: Literal["unknown"] = "unknown"


_KNOWN_PLUGIN_ORIGINS: dict[str, type[BaseModel]] = {
    "cli_override": CliOverride,
    "project_grok": ProjectGrok,
    "project_claude": ProjectClaude,
    "user_grok": UserGrok,
    "user_claude": UserClaude,
    "claude_marketplace": ClaudeMarketplace,
    "claude_installed": ClaudeInstalled,
    "marketplace_install": MarketplaceInstallOrigin,
    "config_path": ConfigPath,
}

#: All concrete PluginOrigin variants (isinstance check for parse shortcuts).
_PLUGIN_ORIGIN_VARIANTS: tuple[type[BaseModel], ...] = (
    CliOverride,
    ProjectGrok,
    ProjectClaude,
    UserGrok,
    UserClaude,
    ClaudeMarketplace,
    ClaudeInstalled,
    MarketplaceInstallOrigin,
    ConfigPath,
    UnknownOrigin,
)

#: PluginOrigin wire union (UnknownOrigin included as the serde(other) sink).
PluginOrigin = (
    CliOverride
    | ProjectGrok
    | ProjectClaude
    | UserGrok
    | UserClaude
    | ClaudeMarketplace
    | ClaudeInstalled
    | MarketplaceInstallOrigin
    | ConfigPath
    | UnknownOrigin
)


def parse_plugin_origin(data: Any) -> PluginOrigin:
    """Parse a PluginOrigin, degrading unknown tags to UnknownOrigin.

    Delivers grok's ``#[serde(other)]`` semantics: a dict whose ``type`` is not
    one of the known origins becomes :class:`UnknownOrigin` (data dropped),
    rather than raising. A dict with ``type == "unknown"`` round-trips back to
    :class:`UnknownOrigin` too.
    """
    if isinstance(data, _PLUGIN_ORIGIN_VARIANTS):
        return data  # type: ignore[return-value]
    if not isinstance(data, dict):
        raise ValueError(
            f"PluginOrigin must be a dict or variant, got {type(data).__name__}"
        )
    raw_tag = data.get("type")
    cls = _KNOWN_PLUGIN_ORIGINS.get(str(raw_tag)) if raw_tag is not None else None
    if cls is None:
        return UnknownOrigin()
    return cls.model_validate(data)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# HooksAction — tagged union (tag="type", snake_case), no serde(other)
# ---------------------------------------------------------------------------


class HooksActionReload(_Variant):
    type: Literal["reload"] = "reload"


class HooksActionTrust(_Variant):
    type: Literal["trust"] = "trust"


class HooksActionUntrust(_Variant):
    type: Literal["untrust"] = "untrust"


class HooksActionAdd(_Variant):
    type: Literal["add"] = "add"
    path: str


class HooksActionRemove(_Variant):
    type: Literal["remove"] = "remove"
    path: str


class HooksActionEnable(_Variant):
    type: Literal["enable"] = "enable"
    hook_name: str


class HooksActionDisable(_Variant):
    type: Literal["disable"] = "disable"
    hook_name: str


class HooksActionToggleSource(_Variant):
    type: Literal["toggle_source"] = "toggle_source"
    hook_names: list[str]
    disable: bool


_KNOWN_HOOKS_ACTIONS: dict[str, type[BaseModel]] = {
    "reload": HooksActionReload,
    "trust": HooksActionTrust,
    "untrust": HooksActionUntrust,
    "add": HooksActionAdd,
    "remove": HooksActionRemove,
    "enable": HooksActionEnable,
    "disable": HooksActionDisable,
    "toggle_source": HooksActionToggleSource,
}

_HOOKS_ACTION_VARIANTS: tuple[type[BaseModel], ...] = tuple(
    _KNOWN_HOOKS_ACTIONS.values()
)

HooksAction = (
    HooksActionReload
    | HooksActionTrust
    | HooksActionUntrust
    | HooksActionAdd
    | HooksActionRemove
    | HooksActionEnable
    | HooksActionDisable
    | HooksActionToggleSource
)


def parse_hooks_action(data: Any) -> HooksAction:
    """Parse a HooksAction. Unknown tags raise ``ValueError`` (requests reject)."""
    if isinstance(data, _HOOKS_ACTION_VARIANTS):
        return data  # type: ignore[return-value]
    if not isinstance(data, dict):
        raise ValueError(
            f"HooksAction must be a dict or variant, got {type(data).__name__}"
        )
    raw_tag = data.get("type")
    cls = _KNOWN_HOOKS_ACTIONS.get(str(raw_tag)) if raw_tag is not None else None
    if cls is None:
        raise ValueError(f"Unknown HooksAction type: {raw_tag!r}")
    return cls.model_validate(data)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# PluginsAction — tagged union (tag="type", snake_case), no serde(other)
# ---------------------------------------------------------------------------


class PluginsActionReload(_Variant):
    type: Literal["reload"] = "reload"


class PluginsActionInstall(_Variant):
    type: Literal["install"] = "install"
    source: str


class PluginsActionUninstall(_Variant):
    type: Literal["uninstall"] = "uninstall"
    plugin_id: str
    #: If true, skip multi-plugin repo confirmation.
    confirmed: bool = False


class PluginsActionUpdate(_Variant):
    type: Literal["update"] = "update"
    plugin_id: str | None = None


class PluginsActionAdd(_Variant):
    type: Literal["add"] = "add"
    path: str


class PluginsActionRemove(_Variant):
    type: Literal["remove"] = "remove"
    path: str


class PluginsActionEnable(_Variant):
    type: Literal["enable"] = "enable"
    plugin_id: str


class PluginsActionDisable(_Variant):
    type: Literal["disable"] = "disable"
    plugin_id: str


_KNOWN_PLUGINS_ACTIONS: dict[str, type[BaseModel]] = {
    "reload": PluginsActionReload,
    "install": PluginsActionInstall,
    "uninstall": PluginsActionUninstall,
    "update": PluginsActionUpdate,
    "add": PluginsActionAdd,
    "remove": PluginsActionRemove,
    "enable": PluginsActionEnable,
    "disable": PluginsActionDisable,
}

_PLUGINS_ACTION_VARIANTS: tuple[type[BaseModel], ...] = tuple(
    _KNOWN_PLUGINS_ACTIONS.values()
)

PluginsAction = (
    PluginsActionReload
    | PluginsActionInstall
    | PluginsActionUninstall
    | PluginsActionUpdate
    | PluginsActionAdd
    | PluginsActionRemove
    | PluginsActionEnable
    | PluginsActionDisable
)


def parse_plugins_action(data: Any) -> PluginsAction:
    """Parse a PluginsAction. Unknown tags raise ``ValueError``."""
    if isinstance(data, _PLUGINS_ACTION_VARIANTS):
        return data  # type: ignore[return-value]
    if not isinstance(data, dict):
        raise ValueError(
            f"PluginsAction must be a dict or variant, got {type(data).__name__}"
        )
    raw_tag = data.get("type")
    cls = _KNOWN_PLUGINS_ACTIONS.get(str(raw_tag)) if raw_tag is not None else None
    if cls is None:
        raise ValueError(f"Unknown PluginsAction type: {raw_tag!r}")
    return cls.model_validate(data)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# MarketplaceAction — tagged union (tag="type", snake_case), no serde(other)
# ---------------------------------------------------------------------------


class MarketplaceActionRefresh(_Variant):
    type: Literal["refresh"] = "refresh"
    #: If set, only refresh this source (by canonical URL/path).
    source_url_or_path: str | None = None


class MarketplaceActionInstall(_Variant):
    type: Literal["install"] = "install"
    source_url_or_path: str
    plugin_relative_path: str


class MarketplaceActionUpdate(_Variant):
    type: Literal["update"] = "update"
    source_url_or_path: str
    plugin_relative_path: str


class MarketplaceActionUninstall(_Variant):
    type: Literal["uninstall"] = "uninstall"
    source_url_or_path: str
    plugin_relative_path: str


class AddSource(_Variant):
    type: Literal["add_source"] = "add_source"
    #: Git URL of the marketplace repo.
    url: str


class MarketplaceActionRemoveSource(_Variant):
    type: Literal["remove_source"] = "remove_source"
    source_url_or_path: str


_KNOWN_MARKETPLACE_ACTIONS: dict[str, type[BaseModel]] = {
    "refresh": MarketplaceActionRefresh,
    "install": MarketplaceActionInstall,
    "update": MarketplaceActionUpdate,
    "uninstall": MarketplaceActionUninstall,
    "add_source": AddSource,
    "remove_source": MarketplaceActionRemoveSource,
}

_MARKETPLACE_ACTION_VARIANTS: tuple[type[BaseModel], ...] = tuple(
    _KNOWN_MARKETPLACE_ACTIONS.values()
)

MarketplaceAction = (
    MarketplaceActionRefresh
    | MarketplaceActionInstall
    | MarketplaceActionUpdate
    | MarketplaceActionUninstall
    | AddSource
    | MarketplaceActionRemoveSource
)


def parse_marketplace_action(data: Any) -> MarketplaceAction:
    """Parse a MarketplaceAction. Unknown tags raise ``ValueError``."""
    if isinstance(data, _MARKETPLACE_ACTION_VARIANTS):
        return data  # type: ignore[return-value]
    if not isinstance(data, dict):
        raise ValueError(
            f"MarketplaceAction must be a dict or variant, got {type(data).__name__}"
        )
    raw_tag = data.get("type")
    cls = _KNOWN_MARKETPLACE_ACTIONS.get(str(raw_tag)) if raw_tag is not None else None
    if cls is None:
        raise ValueError(f"Unknown MarketplaceAction type: {raw_tag!r}")
    return cls.model_validate(data)  # type: ignore[no-any-return]


# ---------------------------------------------------------------------------
# Top-level structs (camelCase wire)
# ---------------------------------------------------------------------------


class HookInfo(_Wire):
    """A single hook's metadata for display."""

    #: Full name including scope prefix.
    name: str
    event: HookEvent
    handler_type: HookHandlerType
    #: Raw matcher pattern from config; None = matches all tools.
    matcher: str | None = None
    #: Command path (command handlers).
    command: str | None = None
    #: HTTP URL (http handlers).
    url: str | None = None
    #: Timeout in milliseconds.
    timeout_ms: int = 0
    #: Source directory of the hook definition file.
    source_dir: str = ""
    #: Whether disabled via the disabled-hooks list.
    disabled: bool = False


class HooksListResponse(_Wire):
    """Response for ``x.ai/hooks/list``."""

    hooks: list[HookInfo] = Field(default_factory=list)
    #: Whether the current project's git root is trusted for hook execution.
    project_trusted: bool = False
    #: Errors from loading hook config files (parse failures, etc.).
    load_errors: list[str] = Field(default_factory=list)


class PluginInfo(_Wire):
    """A single plugin's metadata for display in the pager."""

    name: str
    #: Stable plugin ID (format: ``<scope>/<hex8>/<name>``).
    id: str
    #: Absolute path to plugin root directory.
    root: str
    scope: PluginScope
    #: Deprecated: always ``True`` (kept for serialization compatibility).
    trusted: bool = True
    #: Whether the plugin is enabled (not in the disabled list).
    enabled: bool = True
    version: str | None = None
    description: str | None = None
    skill_count: int = 0
    skill_names: list[str] = Field(default_factory=list)
    agent_count: int = 0
    agent_names: list[str] = Field(default_factory=list)
    hook_status: HookStatus = HookStatus.NONE
    hook_count: int = 0
    mcp_server_count: int = 0
    mcp_status: McpStatus = McpStatus.NONE
    #: Marketplace source display name (None for non-marketplace installs).
    marketplace_source: str | None = None
    #: Concrete discovery source (None when sent by an older shell).
    origin: PluginOrigin | None = None
    #: Warning when this plugin shadowed another with the same name.
    conflict: str | None = None

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> PluginInfo:  # type: ignore[override]
        # Forward-compatible origin parsing: an unknown origin tag degrades to
        # UnknownOrigin instead of failing the whole PluginInfo.
        if isinstance(obj, dict) and isinstance(obj.get("origin"), dict):
            obj = {**obj, "origin": parse_plugin_origin(obj["origin"])}
        return super().model_validate(obj, *args, **kwargs)  # type: ignore[no-any-return]


class PluginsListResponse(_Wire):
    """Response for ``x.ai/plugins/list``."""

    plugins: list[PluginInfo] = Field(default_factory=list)


class McpToolInfo(_Wire):
    """A tool exposed by an MCP server."""

    name: str
    description: str | None = None


class McpServerInfo(_Wire):
    """Summary of an MCP server for display in the pager."""

    name: str
    source: McpServerSource
    enabled: bool = False
    status: McpSessionStatus | None = None
    #: Number of tools this server exposes.
    tool_count: int = 0
    #: Tool names (for display when expanded).
    tools: list[McpToolInfo] = Field(default_factory=list)
    #: Config source label (e.g. ``plugin: my-plugin``, ``config.toml``).
    config_source: str | None = None


class McpServersListResponse(_Wire):
    """Response for ``x.ai/mcp/list`` as consumed by the pager."""

    servers: list[McpServerInfo] = Field(default_factory=list)


class HooksActionRequest(_Wire):
    """Request wrapper for ``x.ai/hooks/action``."""

    session_id: str
    action: HooksAction

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> HooksActionRequest:  # type: ignore[override]
        if isinstance(obj, dict) and isinstance(obj.get("action"), dict):
            obj = {**obj, "action": parse_hooks_action(obj["action"])}
        return super().model_validate(obj, *args, **kwargs)  # type: ignore[no-any-return]


class PluginsActionRequest(_Wire):
    """Request wrapper for ``x.ai/plugins/action``."""

    session_id: str
    action: PluginsAction

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> PluginsActionRequest:  # type: ignore[override]
        if isinstance(obj, dict) and isinstance(obj.get("action"), dict):
            obj = {**obj, "action": parse_plugins_action(obj["action"])}
        return super().model_validate(obj, *args, **kwargs)  # type: ignore[no-any-return]


class ActionOutcome(_Wire):
    """Shared action response for hooks/plugins ``action`` endpoints."""

    status: OutcomeStatus
    #: Human-readable result message.
    message: str = ""
    #: Whether the pager should auto-trigger a plugins reload.
    requires_reload: bool = False
    #: Whether the change requires a session restart to take effect.
    requires_restart: bool = False


class MarketplacePluginEntry(_Wire):
    """A marketplace plugin with install status."""

    name: str
    version: str | None = None
    description: str | None = None
    category: str | None = None
    author: str | None = None
    tags: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    domains: list[str] = Field(default_factory=list)
    homepage: str | None = None
    relative_path: str = ""
    skill_count: int = 0
    has_hooks: bool = False
    has_agents: bool = False
    has_mcp: bool = False
    install_status: str = ""
    installed_version: str | None = None
    #: Structured inventory from the marketplace catalog.
    components: PluginComponents | None = None
    #: Remote git URL for URL-sourced plugins.
    remote_url: str | None = None
    #: Git ref (branch/tag) for remote URL sources.
    remote_ref: str | None = None
    remote_sha: str | None = None
    remote_subdir: str | None = None


class MarketplaceScanResult(_Wire):
    """Result of scanning a single marketplace source."""

    source_name: str
    source_kind: str
    source_url_or_path: str
    plugins: list[MarketplacePluginEntry] = Field(default_factory=list)
    error: str | None = None


class MarketplaceListResponse(_Wire):
    """Response for ``x.ai/marketplace/list``."""

    sources: list[MarketplaceScanResult] = Field(default_factory=list)

    def sanitize(self) -> None:
        """Sanitize all catalog-derived components in the response.

        Consumers that render this data to a terminal must call this at the
        ingestion point (serde bypasses :meth:`ComponentItem.new`).
        """
        for source in self.sources:
            for plugin in source.plugins:
                if plugin.components is not None:
                    plugin.components.sanitize()


class MarketplaceActionRequest(_Wire):
    """Request wrapper for ``x.ai/marketplace/action``."""

    session_id: str
    action: MarketplaceAction

    @classmethod
    def model_validate(cls, obj: Any, *args: Any, **kwargs: Any) -> MarketplaceActionRequest:  # type: ignore[override]
        if isinstance(obj, dict) and isinstance(obj.get("action"), dict):
            obj = {**obj, "action": parse_marketplace_action(obj["action"])}
        return super().model_validate(obj, *args, **kwargs)  # type: ignore[no-any-return]
