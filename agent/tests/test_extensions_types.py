"""Tests for :mod:`minimax_code.extensions.types` (R64).

Fusion of grok-build's ``xai-hooks-plugins-types`` — the management-plane wire
DTO for hooks / plugins / MCP / marketplace. Coverage focuses on:

* Enum snake_case serialization (wire fidelity).
* The sanitize security core (control-char + bidi stripping, truncation).
* Tagged-union round-trips and ``serde(other)`` forward-compat degradation.
* Top-level struct camelCase wire shape.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from minimax_code.extensions import types as ext
from minimax_code.extensions.types import (
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
    HooksActionAdd,
    HooksActionReload,
    HooksActionRequest,
    HooksActionToggleSource,
    HooksListResponse,
    HookStatus,
    MarketplaceActionInstall,
    MarketplaceActionRefresh,
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
    PluginsActionDisable,
    PluginsActionReload,
    PluginsActionRequest,
    PluginsActionUninstall,
    PluginScope,
    PluginsListResponse,
    ProjectGrok,
    UnknownOrigin,
    UserClaude,
    hook_event_display,
    parse_hooks_action,
    parse_marketplace_action,
    parse_plugin_origin,
    parse_plugins_action,
    strip_control_chars,
    truncate_chars,
)


def _wire(model: BaseModel) -> dict:
    """Serialize a model to its wire shape (camelCase aliases, skip None)."""
    return model.model_dump(by_alias=True, exclude_none=True)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def test_constants_match_grok():
    assert ext.MAX_COMPONENT_NAME_CHARS == 120
    assert ext.MAX_COMPONENT_DESC_CHARS == 120
    assert ext.MAX_COMPONENTS_PER_CATEGORY == 50


# ---------------------------------------------------------------------------
# Simple enums — snake_case wire fidelity
# ---------------------------------------------------------------------------


def test_plugin_scope_wire_values():
    assert PluginScope.CLI.value == "cli"
    assert PluginScope.PROJECT.value == "project"
    assert PluginScope.USER.value == "user"
    assert PluginScope.CONFIG.value == "config"


def test_hook_event_has_fourteen_snake_case_variants():
    expected = {
        "session_start",
        "session_end",
        "stop",
        "stop_failure",
        "pre_tool_use",
        "post_tool_use",
        "post_tool_use_failure",
        "permission_denied",
        "user_prompt_submit",
        "notification",
        "subagent_start",
        "subagent_stop",
        "pre_compact",
        "post_compact",
    }
    assert {e.value for e in HookEvent} == expected
    assert len(expected) == 14


def test_hook_event_display_returns_title_case():
    assert hook_event_display(HookEvent.SESSION_START) == "Session Start"
    assert hook_event_display(HookEvent.PRE_TOOL_USE) == "Pre-Tool Use"
    assert hook_event_display(HookEvent.POST_TOOL_USE_FAILURE) == "Post-Tool Use Failure"


def test_status_enums_share_active_blocked_none_vocabulary():
    for shared in ("active", "active_inline", "blocked", "none"):
        assert HookStatus(shared) is not None
        assert McpStatus(shared) is not None


def test_outcome_status_wire_values():
    assert OutcomeStatus.SUCCESS.value == "success"
    assert OutcomeStatus.VALIDATION_ERROR.value == "validation_error"
    assert OutcomeStatus.CONFIRMATION_REQUIRED.value == "confirmation_required"
    assert OutcomeStatus.NOT_FOUND.value == "not_found"
    assert OutcomeStatus.INTERNAL_ERROR.value == "internal_error"
    assert OutcomeStatus.UNSUPPORTED.value == "unsupported"


def test_mcp_enums_wire_values():
    assert McpServerSource.MANAGED.value == "managed"
    assert McpServerSource.LOCAL.value == "local"
    assert McpSessionStatus.READY.value == "ready"
    assert McpSessionStatus.INITIALIZING.value == "initializing"
    assert McpSessionStatus.UNAVAILABLE.value == "unavailable"


def test_hook_handler_type_wire_values():
    assert HookHandlerType.COMMAND.value == "command"
    assert HookHandlerType.HTTP.value == "http"


def test_component_category_six_variants_align_with_fields():
    expected = {
        "skills",
        "commands",
        "agents",
        "mcp_servers",
        "hooks",
        "lsp_servers",
    }
    assert {c.value for c in ComponentCategory} == expected


# ---------------------------------------------------------------------------
# Sanitize helpers
# ---------------------------------------------------------------------------


def test_strip_control_chars_removes_cc_and_bidi_keeps_cjk():
    # ASCII control + bidi overrides + zero-width + BOM all stripped.
    raw = "a\x00b\x1fc‮d​e⁨f﻿g中文🚀"
    assert strip_control_chars(raw) == "abcdefg中文🚀"


def test_strip_control_chars_preserves_newline_and_tab():
    # Rust char::is_control is Cc-only; common whitespace is Cc too, so these
    # are stripped as well — the helper is for single-line catalog strings.
    assert strip_control_chars("a\nb\tc") == "abc"


def test_truncate_chars_on_code_point_boundary():
    # '中文' is two code points but six UTF-8 bytes; truncate at 1 keeps one char.
    assert truncate_chars("中文🚀", 1) == "中"
    assert truncate_chars("abc", 5) == "abc"  # no-op when under the cap
    assert truncate_chars("abcdef", 3) == "abc"


# ---------------------------------------------------------------------------
# ComponentItem
# ---------------------------------------------------------------------------


def test_component_item_new_strips_control_chars_and_drops_empty_description():
    # Control chars stripped from the name; an empty description collapses to None.
    # (strip_control_chars filters Cc + bidi only — it does not trim whitespace,
    # so only a truly empty string drops to None, matching grok's is_empty gate.)
    item = ComponentItem.new("safe\x1bname", "")
    assert item.name == "safename"
    assert item.description is None  # empty string → None


def test_component_item_new_preserves_whitespace_only_description():
    # Whitespace is NOT a control char, so "  " survives sanitize as-is — this
    # pins grok's semantics (char::is_control + bidi ranges, not str::trim).
    item = ComponentItem.new("n", "  ")
    assert item.description == "  "


def test_component_item_new_truncates_overlong_name_and_description():
    item = ComponentItem.new("x" * 200, "y" * 200)
    assert len(item.name) == ext.MAX_COMPONENT_NAME_CHARS
    assert len(item.description or "") == ext.MAX_COMPONENT_DESC_CHARS


def test_component_item_default_constructible():
    item = ComponentItem()
    assert item.name == ""
    assert item.description is None


def test_component_item_sanitize_mutates_in_place():
    item = ComponentItem(name="dirty\x00", description="ok")
    item.sanitize()
    assert item.name == "dirty"
    assert item.description == "ok"


# ---------------------------------------------------------------------------
# PluginComponents — categories / is_empty / summary_line / sanitize
# ---------------------------------------------------------------------------


def _item(name: str, desc: str | None = None) -> ComponentItem:
    # Bypass the sanitizing constructor so tests can assert sanitize() behavior.
    return ComponentItem(name=name, description=desc)


def test_plugin_components_default_empty():
    pc = PluginComponents()
    assert pc.is_empty() is True
    assert pc.summary_line() is None


def test_plugin_components_categories_order_is_canonical():
    pc = PluginComponents(
        skills=[_item("s")],
        commands=[_item("c")],
        agents=[_item("a")],
        mcp_servers=[_item("m")],
        hooks=[_item("h")],
        lsp_servers=[_item("l")],
    )
    order = [cat for cat, _items in pc.categories()]
    assert order == [
        ComponentCategory.SKILLS,
        ComponentCategory.COMMANDS,
        ComponentCategory.AGENTS,
        ComponentCategory.MCP_SERVERS,
        ComponentCategory.HOOKS,
        ComponentCategory.LSP_SERVERS,
    ]


def test_summary_line_singular_plural_and_middot():
    pc = PluginComponents(
        skills=[_item("a")],  # 1 → "skill"
        commands=[_item("b"), _item("c")],  # 2 → "commands"
        mcp_servers=[_item("m")],  # 1 → "MCP server"
    )
    line = pc.summary_line()
    assert line is not None
    assert line == "1 skill · 2 commands · 1 MCP server"
    assert "·" in line  # middle dot separator


def test_summary_line_omits_empty_categories():
    pc = PluginComponents(hooks=[_item("h"), _item("g")])
    assert pc.summary_line() == "2 hooks"


def test_plugin_components_sanitize_caps_at_fifty_per_category():
    pc = PluginComponents(skills=[_item(f"k{i}") for i in range(60)])
    pc.sanitize()
    assert len(pc.skills) == ext.MAX_COMPONENTS_PER_CATEGORY


def test_plugin_components_sanitize_sanitizes_each_item():
    pc = PluginComponents(skills=[_item("dirty\x00")])
    pc.sanitize()
    assert pc.skills[0].name == "dirty"


# ---------------------------------------------------------------------------
# PluginOrigin — tagged union + serde(other) degradation
# ---------------------------------------------------------------------------


def test_plugin_origin_unit_variant_round_trip():
    origin = CliOverride()
    assert _wire(origin) == {"type": "cli_override"}
    assert isinstance(parse_plugin_origin({"type": "cli_override"}), CliOverride)


def test_plugin_origin_struct_variant_keeps_snake_case_fields():
    origin = MarketplaceInstallOrigin(source_name="mp", git_url="https://x")
    # Variant fields stay snake_case (serde enum rename_all ≠ field rename).
    assert _wire(origin) == {
        "type": "marketplace_install",
        "source_name": "mp",
        "git_url": "https://x",
    }
    parsed = parse_plugin_origin(
        {"type": "marketplace_install", "source_name": "mp", "git_url": "https://x"}
    )
    assert isinstance(parsed, MarketplaceInstallOrigin)
    assert parsed.source_name == "mp"


def test_plugin_origin_serde_other_degrades_unknown_to_unknown():
    parsed = parse_plugin_origin({"type": "cloud_install", "vendor": "acme"})
    assert isinstance(parsed, UnknownOrigin)
    # Unknown carries no fields — vendor data is dropped (serde(other) semantics).
    assert _wire(parsed) == {"type": "unknown"}


def test_plugin_origin_unknown_tag_round_trips():
    # A literal "unknown" tag also resolves to UnknownOrigin.
    assert isinstance(parse_plugin_origin({"type": "unknown"}), UnknownOrigin)


def test_plugin_origin_returns_variant_unchanged_on_shortcut():
    origin = ClaudeMarketplace(marketplace="mp")
    assert parse_plugin_origin(origin) is origin


@pytest.mark.parametrize(
    "data",
    [None, 42, "cli_override", []],
)
def test_plugin_origin_rejects_non_dict_non_variant(data):
    with pytest.raises(ValueError):
        parse_plugin_origin(data)


def test_all_known_plugin_origins_parse():
    cases = {
        "project_grok": ProjectGrok,
        "project_claude": ext.ProjectClaude,
        "user_grok": ext.UserGrok,
        "user_claude": UserClaude,
        "config_path": ConfigPath,
        "claude_installed": ClaudeInstalled,
    }
    for tag, cls in cases.items():
        assert isinstance(parse_plugin_origin({"type": tag}), cls)


# ---------------------------------------------------------------------------
# HooksAction / PluginsAction / MarketplaceAction — tagged unions
# ---------------------------------------------------------------------------


def test_hooks_action_unit_and_struct_variants_round_trip():
    reload_action = HooksActionReload()
    assert _wire(reload_action) == {"type": "reload"}

    add_action = HooksActionAdd(path="/home/u/.grok/hooks")
    assert _wire(add_action) == {"type": "add", "path": "/home/u/.grok/hooks"}
    assert parse_hooks_action(_wire(add_action)) == add_action


def test_hooks_action_toggle_source_keeps_snake_case_fields():
    action = HooksActionToggleSource(hook_names=["a", "b"], disable=True)
    assert _wire(action) == {
        "type": "toggle_source",
        "hook_names": ["a", "b"],
        "disable": True,
    }


def test_hooks_action_unknown_tag_raises():
    with pytest.raises(ValueError):
        parse_hooks_action({"type": "nuke_everything"})


def test_hooks_action_request_validates_nested_action():
    req = HooksActionRequest.model_validate(
        {"sessionId": "s1", "action": {"type": "enable", "hook_name": "h"}}
    )
    assert req.session_id == "s1"
    assert isinstance(req.action, ext.HooksActionEnable)
    assert req.action.hook_name == "h"


def test_plugins_action_variants_round_trip():
    uninstall = PluginsActionUninstall(plugin_id="p1", confirmed=True)
    assert _wire(uninstall) == {
        "type": "uninstall",
        "plugin_id": "p1",
        "confirmed": True,
    }
    disable = PluginsActionDisable(plugin_id="p2")
    assert _wire(disable) == {"type": "disable", "plugin_id": "p2"}
    assert parse_plugins_action(_wire(disable)) == disable


def test_plugins_action_reload_minimal_wire():
    assert _wire(PluginsActionReload()) == {"type": "reload"}


def test_plugins_action_request_validates_nested():
    req = PluginsActionRequest.model_validate(
        {"sessionId": "s2", "action": {"type": "install", "source": "./p"}}
    )
    assert isinstance(req.action, ext.PluginsActionInstall)
    assert req.action.source == "./p"


def test_plugins_action_unknown_tag_raises():
    with pytest.raises(ValueError):
        parse_plugins_action({"type": "purge"})


def test_marketplace_action_variants_round_trip():
    install = MarketplaceActionInstall(
        source_url_or_path="https://git", plugin_relative_path="plugins/x"
    )
    assert _wire(install) == {
        "type": "install",
        "source_url_or_path": "https://git",
        "plugin_relative_path": "plugins/x",
    }
    refresh = MarketplaceActionRefresh()  # optional source defaults to None
    assert _wire(refresh) == {"type": "refresh"}
    add_src = AddSource(url="https://git")
    assert _wire(add_src) == {"type": "add_source", "url": "https://git"}


def test_marketplace_action_request_validates_nested():
    req = MarketplaceActionRequest.model_validate(
        {
            "sessionId": "s3",
            "action": {"type": "add_source", "url": "https://repo"},
        }
    )
    assert isinstance(req.action, AddSource)
    assert req.action.url == "https://repo"


def test_marketplace_action_unknown_tag_raises():
    with pytest.raises(ValueError):
        parse_marketplace_action({"type": "vaporize"})


# ---------------------------------------------------------------------------
# Top-level structs — camelCase wire
# ---------------------------------------------------------------------------


def test_hook_info_camel_case_wire():
    info = HookInfo(
        name="global/safety:pre_tool_use[0].hooks[0]",
        event=HookEvent.PRE_TOOL_USE,
        handler_type=HookHandlerType.COMMAND,
        matcher=None,
        command="/usr/bin/lint",
        timeout_ms=5000,
        source_dir="/home/u",
    )
    wire = _wire(info)
    assert wire["event"] == "pre_tool_use"
    assert wire["handlerType"] == "command"
    assert wire["timeoutMs"] == 5000
    assert wire["sourceDir"] == "/home/u"
    assert "matcher" not in wire  # None excluded
    assert wire["command"] == "/usr/bin/lint"


def test_hooks_list_response_round_trip():
    resp = HooksListResponse(
        hooks=[HookInfo(name="h", event=HookEvent.STOP, handler_type=HookHandlerType.HTTP)],
        project_trusted=True,
        load_errors=["bad config"],
    )
    wire = _wire(resp)
    assert wire["projectTrusted"] is True
    assert wire["loadErrors"] == ["bad config"]
    assert HooksListResponse.model_validate(wire).project_trusted is True


def test_plugin_info_camel_case_wire_and_defaults():
    info = PluginInfo(
        name="my-plugin",
        id="user/abcd1234/my-plugin",
        root="/root",
        scope=PluginScope.USER,
        hook_status=HookStatus.ACTIVE,
        mcp_status=McpStatus.NONE,
    )
    wire = _wire(info)
    assert wire["skillCount"] == 0
    assert wire["hookStatus"] == "active"
    assert wire["mcpStatus"] == "none"
    assert wire["scope"] == "user"
    assert wire["trusted"] is True
    assert wire["enabled"] is True


def test_plugin_info_degrades_unknown_origin_on_validate():
    # An unknown origin tag must not invalidate the whole PluginInfo.
    info = PluginInfo.model_validate(
        {
            "name": "p",
            "id": "user/abc/p",
            "root": "/r",
            "scope": "user",
            "origin": {"type": "future_origin", "data": 1},
        }
    )
    assert isinstance(info.origin, UnknownOrigin)


def test_plugins_list_response_round_trip():
    resp = PluginsListResponse(
        plugins=[
            PluginInfo(name="p", id="user/0/p", root="/r", scope=PluginScope.USER)
        ]
    )
    wire = _wire(resp)
    assert PluginsListResponse.model_validate(wire).plugins[0].name == "p"


def test_mcp_server_info_wire():
    info = McpServerInfo(
        name="weather",
        source=McpServerSource.LOCAL,
        enabled=True,
        status=McpSessionStatus.READY,
        tool_count=2,
        tools=[McpToolInfo(name="get_forecast")],
        config_source="config.toml",
    )
    wire = _wire(info)
    assert wire["source"] == "local"
    assert wire["status"] == "ready"
    assert wire["toolCount"] == 2
    assert wire["configSource"] == "config.toml"
    assert McpServersListResponse.model_validate({"servers": [wire]}).servers[0].name == "weather"


# ---------------------------------------------------------------------------
# MarketplaceListResponse.sanitize
# ---------------------------------------------------------------------------


def test_marketplace_list_response_sanitize_recurses_into_components():
    entry = MarketplacePluginEntry(
        name="p",
        relative_path="plugins/p",
        components=PluginComponents(
            skills=[_item(f"k{i}\x00") for i in range(ext.MAX_COMPONENTS_PER_CATEGORY + 5)]
        ),
    )
    resp = MarketplaceListResponse(
        sources=[MarketplaceScanResult(source_name="s", source_kind="git", source_url_or_path="u", plugins=[entry])]
    )
    resp.sanitize()
    skills = resp.sources[0].plugins[0].components.skills
    assert len(skills) == ext.MAX_COMPONENTS_PER_CATEGORY
    assert all("\x00" not in item.name for item in skills)


def test_marketplace_list_response_sanitize_skips_when_no_components():
    entry = MarketplacePluginEntry(name="p", relative_path="p")  # components None
    resp = MarketplaceListResponse(
        sources=[
            MarketplaceScanResult(
                source_name="s", source_kind="git", source_url_or_path="u", plugins=[entry]
            )
        ]
    )
    resp.sanitize()  # must not raise on a None components field
    assert resp.sources[0].plugins[0].components is None
