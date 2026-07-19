"""Tests for :mod:`minimax_code.config_types` (R66).

Fusion of grok-build's ``xai-grok-config-types`` — the leaf configuration
value types for the grok CLI. Coverage:

* :mod:`.flags`      — :func:`env_bool` parsing, :func:`resolve_bool_flag`
  priority chain, :class:`BoolFlag` builder, :class:`ConfigSource` wire values.
* :mod:`.permission` — :class:`ToolFilter` wire fidelity (``webfetch``!),
  CWE-1188 ``RuleAction`` default, :class:`PermissionRule` / ``PermissionConfig``.
* :mod:`.pool`       — :class:`PoolConfig` defaults + partial override + empty-table.
* :mod:`.memory`     — every struct's defaults, MMR ``lambda`` clamp, flush
  semantic-dedup clamp, :meth:`MemorySearchConfig.effective_half_life_days`
  three-way priority.
* :mod:`.mcp`        — flatten + untagged dispatch (stdio / http), ``to_wire``
  re-flatten, :meth:`expand_strings`, camelCase OAuth block,
  :meth:`RelaySyncConfig.is_enabled` env override, :func:`resolve_oauth_client_secret`.
* :mod:`.types`      — :class:`RemoteSettings` all-``None`` default, round-trips,
  tolerant announcements / goal role-models (one bad item must not poison),
  :meth:`imagine_tool_disabled`, :class:`DisplayRefreshSettings` tolerant
  bool/u32 + extra preservation + ``is_default``, :class:`CampaignOverride`
  flatten patch, :class:`DoomLoopRecoverySettings` skip-``None`` wire.
"""

from __future__ import annotations

import pytest

from minimax_code.config_types import (
    DEFAULT_RECENCY_DECAY,
    BoolFlag,
    CampaignOverride,
    ConfigSource,
    ContextualHintsRemote,
    DisplayRefreshSettings,
    DoomLoopRecoverySettings,
    GoalRoleModel,
    McpConfig,
    McpJsonOAuthBlock,
    McpServerConfig,
    MemoryEmbeddingConfig,
    MemoryFlushConfig,
    MemoryIndexConfig,
    MemorySearchConfig,
    MmrConfig,
    PatternMode,
    PermissionConfig,
    PermissionRule,
    PoolConfig,
    PruningConfig,
    RelaySyncConfig,
    RemoteSettings,
    Resolved,
    RuleAction,
    StdioTransport,
    StreamableHttpTransport,
    TemporalDecayConfig,
    ToolFilter,
    env_bool,
    resolve_bool_flag,
    resolve_oauth_client_secret,
)

# ===========================================================================
# flags — env_bool / resolve_bool_flag / BoolFlag / ConfigSource
# ===========================================================================


class TestEnvBool:
    def test_unset_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("R66_TEST_FLAG", raising=False)
        assert env_bool("R66_TEST_FLAG") is None

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", " yes ", "On"])
    def test_truthy_spellings(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("R66_TEST_FLAG", raw)
        assert env_bool("R66_TEST_FLAG") is True

    @pytest.mark.parametrize("raw", ["0", "false", "NO", " off ", "Off"])
    def test_falsy_spellings(self, monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
        monkeypatch.setenv("R66_TEST_FLAG", raw)
        assert env_bool("R66_TEST_FLAG") is False

    def test_unrecognised_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("R66_TEST_FLAG", "maybe")
        assert env_bool("R66_TEST_FLAG") is None


class TestConfigSourceWire:
    def test_snake_case_splits_camel_case(self) -> None:
        # strum "snake_case" SPLITS CamelCase, unlike serde "lowercase".
        assert ConfigSource.SYSTEM_MANAGED_CONFIG == "system_managed_config"
        assert ConfigSource.MANAGED_CONFIG == "managed_config"
        assert ConfigSource.USER_CONFIG == "user_config"
        assert ConfigSource.REQUIREMENT == "requirement"
        assert ConfigSource.CLI == "cli"
        assert ConfigSource.ENV == "env"
        assert ConfigSource.CONFIG == "config"
        assert ConfigSource.REMOTE == "remote"
        assert ConfigSource.DEFAULT == "default"


class TestResolveBoolFlag:
    def test_requirement_wins(self) -> None:
        r = resolve_bool_flag(
            requirement=True, cli=False, env_var=None, config=False,
            managed=False, feature_flag=False, default=False,
        )
        assert r == Resolved(True, ConfigSource.REQUIREMENT)

    def test_cli_beats_env_config(self) -> None:
        r = resolve_bool_flag(
            requirement=None, cli=True, env_var=None, config=False,
            managed=False, feature_flag=False, default=False,
        )
        assert r == Resolved(True, ConfigSource.CLI)

    def test_env_recognised_beats_config(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("R66_FLAG", "true")
        r = resolve_bool_flag(
            requirement=None, cli=None, env_var="R66_FLAG", config=False,
            managed=False, feature_flag=False, default=False,
        )
        assert r == Resolved(True, ConfigSource.ENV)

    def test_env_unrecognised_falls_through_to_config(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # env var set but value unrecognised → env_bool None → fall to config.
        monkeypatch.setenv("R66_FLAG", "maybe")
        r = resolve_bool_flag(
            requirement=None, cli=None, env_var="R66_FLAG", config=True,
            managed=False, feature_flag=False, default=False,
        )
        assert r == Resolved(True, ConfigSource.CONFIG)

    def test_managed_beats_feature_flag(self) -> None:
        r = resolve_bool_flag(
            requirement=None, cli=None, env_var=None, config=None,
            managed=True, feature_flag=False, default=False,
        )
        assert r == Resolved(True, ConfigSource.MANAGED_CONFIG)

    def test_feature_flag_maps_to_remote(self) -> None:
        r = resolve_bool_flag(
            requirement=None, cli=None, env_var=None, config=None,
            managed=None, feature_flag=True, default=False,
        )
        assert r == Resolved(True, ConfigSource.REMOTE)

    def test_default_is_last_resort(self) -> None:
        r = resolve_bool_flag(
            requirement=None, cli=None, env_var=None, config=None,
            managed=None, feature_flag=None, default=True,
        )
        assert r == Resolved(True, ConfigSource.DEFAULT)


class TestBoolFlag:
    def test_builder_chain_resolves(self) -> None:
        flag = (
            BoolFlag()
            .with_default(False)
            .with_config(True)
            .with_cli(False)
        )
        # cli is highest priority among the three set tiers.
        assert flag.resolve() == Resolved(False, ConfigSource.CLI)

    def test_resolved_display_string(self) -> None:
        assert str(Resolved(True, ConfigSource.CLI)) == "True (cli)"
        assert str(Resolved(False, ConfigSource.DEFAULT)) == "False (default)"


# ===========================================================================
# permission — ToolFilter wire / RuleAction default / PermissionRule
# ===========================================================================


class TestPermissionEnums:
    def test_tool_filter_webfetch_is_not_split(self) -> None:
        # serde "lowercase" does NOT split CamelCase: WebFetch → "webfetch".
        assert ToolFilter.WEB_FETCH.value == "webfetch"
        assert ToolFilter.WEB_FETCH == "webfetch"
        assert ToolFilter("webfetch") is ToolFilter.WEB_FETCH

    def test_tool_filter_other_values(self) -> None:
        assert ToolFilter.ANY == "any"
        assert ToolFilter.BASH == "bash"
        assert ToolFilter.EDIT == "edit"
        assert ToolFilter.READ == "read"
        assert ToolFilter.GREP == "grep"
        assert ToolFilter.MCP == "mcp"

    def test_rule_action_values(self) -> None:
        assert RuleAction.ALLOW == "allow"
        assert RuleAction.DENY == "deny"
        assert RuleAction.ASK == "ask"

    def test_pattern_mode_values(self) -> None:
        assert PatternMode.GLOB == "glob"
        assert PatternMode.DOMAIN == "domain"


class TestPermissionRule:
    def test_action_is_required(self) -> None:
        # action has NO serde default in Rust → pydantic should require it.
        with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError is private path
            PermissionRule()  # type: ignore[call-arg]

    def test_defaults_for_optional_fields(self) -> None:
        rule = PermissionRule(action="deny")
        assert rule.action is RuleAction.DENY
        assert rule.tool is ToolFilter.ANY
        assert rule.pattern_mode is PatternMode.GLOB
        assert rule.pattern is None

    def test_round_trip(self) -> None:
        rule = PermissionRule.model_validate(
            {"action": "allow", "tool": "webfetch", "pattern": "*.x.ai", "pattern_mode": "domain"}
        )
        assert rule.action is RuleAction.ALLOW
        assert rule.tool is ToolFilter.WEB_FETCH
        assert rule.pattern == "*.x.ai"
        assert rule.pattern_mode is PatternMode.DOMAIN
        wire = rule.model_dump(mode="json", by_alias=True)
        assert wire["action"] == "allow"
        assert wire["tool"] == "webfetch"
        assert wire["pattern_mode"] == "domain"


class TestPermissionConfig:
    def test_default_is_empty(self) -> None:
        cfg = PermissionConfig.default()
        assert cfg.rules == []

    def test_rule_list_round_trip(self) -> None:
        cfg = PermissionConfig(
            rules=[
                PermissionRule(action="deny", tool="bash", pattern="rm -rf*"),
                PermissionRule(action="ask", tool="edit"),
            ]
        )
        wire = cfg.model_dump(mode="json")
        restored = PermissionConfig.model_validate(wire)
        assert len(restored.rules) == 2
        assert restored.rules[0].action is RuleAction.DENY
        assert restored.rules[0].tool is ToolFilter.BASH
        assert restored.rules[0].pattern == "rm -rf*"
        assert restored.rules[1].tool is ToolFilter.EDIT
        assert restored.rules[1].action is RuleAction.ASK


# ===========================================================================
# pool — defaults / partial / empty-table
# ===========================================================================


class TestPoolConfig:
    def test_defaults(self) -> None:
        p = PoolConfig.default()
        assert p.enabled is True
        assert p.pool_size == 2
        assert p.file_count_threshold == 50000
        assert p.parallelism == 3

    def test_partial_override_keeps_other_defaults(self) -> None:
        p = PoolConfig.model_validate({"pool_size": 8})
        assert p.pool_size == 8
        assert p.enabled is True
        assert p.file_count_threshold == 50000
        assert p.parallelism == 3

    def test_empty_table_applies_all_defaults(self) -> None:
        # An empty {} table must apply every per-field default.
        p = PoolConfig.model_validate({})
        assert p == PoolConfig.default()


# ===========================================================================
# memory — defaults / clamps / effective_half_life_days
# ===========================================================================


class TestMemoryDefaults:
    def test_index(self) -> None:
        c = MemoryIndexConfig.default()
        assert c.max_chunk_chars == 1600
        assert c.chunk_overlap_chars == 320

    def test_embedding(self) -> None:
        c = MemoryEmbeddingConfig.default()
        assert c.provider == "api"
        assert c.model is None
        assert c.dimensions == 1024

    def test_search(self) -> None:
        c = MemorySearchConfig.default()
        assert c.max_results == 6
        assert c.min_score == 0.35
        assert c.vector_weight == 0.7
        assert c.text_weight == 0.3
        assert c.recency_decay == DEFAULT_RECENCY_DECAY == 0.95
        assert c.source_weights == {"workspace": 1.0, "session": 1.0, "global": 1.0}

    def test_temporal_decay(self) -> None:
        c = TemporalDecayConfig.default()
        assert c.enabled is True
        assert c.half_life_days == 7.0

    def test_pruning(self) -> None:
        c = PruningConfig.default()
        assert c.enabled is True
        assert c.keep_last_n_turns == 3
        assert c.soft_trim_threshold == 4000
        assert c.soft_trim_head == 1500
        assert c.soft_trim_tail == 1500
        assert c.hard_clear_age_turns == 10


class TestMmrClamp:
    def test_default_lambda(self) -> None:
        c = MmrConfig.default()
        assert c.enabled is False
        assert c.lambda_ == 0.7

    def test_alias_lambda_wire(self) -> None:
        c = MmrConfig.model_validate({"lambda": 0.4})
        assert c.lambda_ == 0.4
        # round-trips via alias
        assert c.model_dump(by_alias=True)["lambda"] == 0.4

    def test_clamp_above_one(self) -> None:
        c = MmrConfig.model_validate({"lambda": 5.0})
        assert c.lambda_ == 1.0

    def test_clamp_below_zero(self) -> None:
        c = MmrConfig.model_validate({"lambda": -0.3})
        assert c.lambda_ == 0.0


class TestFlushClamp:
    def test_none_passes_through(self) -> None:
        c = MemoryFlushConfig.default()
        assert c.semantic_dedup_threshold is None
        assert c.soft_threshold_tokens == 4000
        assert c.max_flush_write_chars == 8000

    def test_clamp_into_unit(self) -> None:
        c = MemoryFlushConfig.model_validate({"semantic_dedup_threshold": 2.5})
        assert c.semantic_dedup_threshold == 1.0

    def test_clamp_negative(self) -> None:
        c = MemoryFlushConfig.model_validate({"semantic_dedup_threshold": -1.0})
        assert c.semantic_dedup_threshold == 0.0


class TestEffectiveHalfLife:
    def test_temporal_decay_enabled_wins(self) -> None:
        c = MemorySearchConfig.default()  # temporal_decay.enabled=True, half_life=7.0
        assert c.effective_half_life_days() == 7.0

    def test_recency_decay_conversion_when_temporal_disabled(self) -> None:
        c = MemorySearchConfig(
            recency_decay=0.5,
            temporal_decay=TemporalDecayConfig(enabled=False, half_life_days=7.0),
        )
        # -1.0 / log2(0.5) = -1.0 / -1.0 = 1.0
        assert c.effective_half_life_days() == pytest.approx(1.0)

    def test_none_when_temporal_disabled_and_recency_default(self) -> None:
        c = MemorySearchConfig(
            temporal_decay=TemporalDecayConfig(enabled=False, half_life_days=7.0),
        )
        assert c.effective_half_life_days() is None


# ===========================================================================
# mcp — flatten/untagged dispatch, to_wire, expand_strings, relay sync
# ===========================================================================


class TestMcpTransportDispatch:
    def test_stdio_dispatch_by_command(self) -> None:
        cfg = McpServerConfig.model_validate(
            {"command": "npx", "args": ["-y", "server"], "env": {"K": "V"}}
        )
        assert isinstance(cfg.transport, StdioTransport)
        assert cfg.transport.command == "npx"
        assert cfg.transport.args == ["-y", "server"]
        assert cfg.transport.env == {"K": "V"}
        assert cfg.enabled is True  # default

    def test_http_dispatch_by_url(self) -> None:
        cfg = McpServerConfig.model_validate(
            {"url": "https://srv.example/mcp", "type": "streamable-http"}
        )
        assert isinstance(cfg.transport, StreamableHttpTransport)
        assert cfg.transport.url == "https://srv.example/mcp"
        assert cfg.transport.transport_type == "streamable-http"

    def test_explicit_internal_transport_form_accepted(self) -> None:
        cfg = McpServerConfig(transport=StdioTransport(command="cmd"))
        assert isinstance(cfg.transport, StdioTransport)
        assert cfg.transport.command == "cmd"


class TestMcpToWire:
    def test_stdio_reflatten(self) -> None:
        cfg = McpServerConfig.model_validate(
            {"command": "npx", "args": ["server"], "enabled": False, "startup_timeout_sec": 30}
        )
        wire = cfg.to_wire()
        assert wire["command"] == "npx"
        assert wire["args"] == ["server"]
        assert wire["enabled"] is False
        assert wire["startup_timeout_sec"] == 30
        # absent optional fields omitted
        assert "env" not in wire
        assert "cwd" not in wire
        assert "oauth" not in wire

    def test_http_reflatten(self) -> None:
        cfg = McpServerConfig.model_validate(
            {"url": "https://srv/mcp", "headers": {"Authorization": "Bearer x"}}
        )
        wire = cfg.to_wire()
        assert wire["url"] == "https://srv/mcp"
        assert wire["headers"] == {"Authorization": "Bearer x"}
        assert "type" not in wire  # None omitted

    def test_oauth_block_in_wire(self) -> None:
        cfg = McpServerConfig.model_validate(
            {
                "command": "cmd",
                "oauth": {"clientId": "abc", "callbackPort": 8080, "scopes": ["read"]},
            }
        )
        wire = cfg.to_wire()
        assert wire["oauth"] == {"clientId": "abc", "callbackPort": 8080, "scopes": ["read"]}


class TestExpandStrings:
    def test_stdio_expand(self) -> None:
        cfg = McpServerConfig.model_validate(
            {"command": "$HOME/srv", "args": ["$X"], "env": {"K": "$V"}, "cwd": "$HOME/wd"}
        )
        cfg.expand_strings(lambda s: s.replace("$HOME", "/h").replace("$X", "ex").replace("$V", "vv"))
        assert cfg.transport.command == "/h/srv"  # type: ignore[union-attr]
        assert cfg.transport.args == ["ex"]  # type: ignore[union-attr]
        assert cfg.transport.env == {"K": "vv"}  # type: ignore[union-attr]
        assert cfg.transport.cwd == "/h/wd"  # type: ignore[union-attr]

    def test_http_expand(self) -> None:
        cfg = McpServerConfig.model_validate(
            {"url": "https://$HOST/mcp", "headers": {"Auth": "Bearer $T"}}
        )
        cfg.expand_strings(lambda s: s.replace("$HOST", "srv").replace("$T", "tok"))
        assert cfg.transport.url == "https://srv/mcp"  # type: ignore[union-attr]
        assert cfg.transport.headers == {"Auth": "Bearer tok"}  # type: ignore[union-attr]


class TestMcpJsonOAuthBlock:
    def test_camel_case_wire(self) -> None:
        b = McpJsonOAuthBlock.model_validate(
            {"clientId": "id", "clientSecretEnvVar": "SEC", "callbackPort": 99, "scopes": ["a"]}
        )
        assert b.client_id == "id"
        assert b.client_secret_env_var == "SEC"
        assert b.callback_port == 99
        assert b.scopes == ["a"]
        wire = b.to_wire()
        assert wire == {
            "clientId": "id", "clientSecretEnvVar": "SEC", "callbackPort": 99, "scopes": ["a"],
        }

    def test_to_wire_omits_absent(self) -> None:
        assert McpJsonOAuthBlock().to_wire() == {}


class TestMcpConfig:
    def test_default_empty(self) -> None:
        cfg = McpConfig.default()
        assert cfg.mcp_servers == {}

    def test_alias_mcp_servers(self) -> None:
        cfg = McpConfig.model_validate(
            {"mcpServers": {"fs": {"command": "fs-srv"}}}
        )
        assert "fs" in cfg.mcp_servers
        assert isinstance(cfg.mcp_servers["fs"].transport, StdioTransport)


class TestRelaySync:
    def test_env_true_overrides_disabled(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GROK_RELAY_SYNC_ENABLED", "true")
        assert RelaySyncConfig(enabled=False).is_enabled() is True

    def test_env_one_enables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GROK_RELAY_SYNC_ENABLED", "1")
        assert RelaySyncConfig().is_enabled() is True

    def test_env_other_value_disables(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GROK_RELAY_SYNC_ENABLED", "nope")
        assert RelaySyncConfig(enabled=True).is_enabled() is False

    def test_falls_back_to_config_then_false(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("GROK_RELAY_SYNC_ENABLED", raising=False)
        assert RelaySyncConfig(enabled=True).is_enabled() is True
        assert RelaySyncConfig(enabled=False).is_enabled() is False
        assert RelaySyncConfig().is_enabled() is False


class TestResolveOAuthSecret:
    def test_returns_env_value_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("R66_OAUTH_SECRET", "shh")
        assert resolve_oauth_client_secret("R66_OAUTH_SECRET") == "shh"

    def test_none_env_var_yields_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("R66_OAUTH_SECRET", raising=False)
        assert resolve_oauth_client_secret("R66_OAUTH_SECRET") is None

    def test_empty_env_var_yields_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("R66_OAUTH_SECRET", "")
        assert resolve_oauth_client_secret("R66_OAUTH_SECRET") is None

    def test_none_arg_yields_none(self) -> None:
        assert resolve_oauth_client_secret(None) is None


# ===========================================================================
# types — RemoteSettings defaults / round-trips / tolerant parses
# ===========================================================================


class TestRemoteSettingsDefaults:
    def test_empty_payload_all_none(self) -> None:
        s = RemoteSettings.model_validate({})
        # spot-check a sample across every declared tier
        assert s.leader_mode is None
        assert s.cursor_sessions_enabled is None
        assert s.goal_enabled is None
        assert s.announcements is None
        assert s.doom_loop_recovery is None
        assert s.display_refresh is None
        assert s.contextual_hints is None
        # the two Vec fields default to empty (NOT None)
        assert s.campaigns == []
        assert s.goal_skeptic_models == []

    def test_extra_keys_ignored(self) -> None:
        # Rust has no deny_unknown_fields — unknown keys must not break parse.
        s = RemoteSettings.model_validate({"leader_mode": True, "future_knob": 42})
        assert s.leader_mode is True


class TestRemoteSettingsRoundTrips:
    def test_vendor_sessions_round_trip(self) -> None:
        s = RemoteSettings.model_validate(
            {"cursor_sessions_enabled": True, "claude_sessions_enabled": False, "codex_sessions_enabled": True}
        )
        assert (s.cursor_sessions_enabled, s.claude_sessions_enabled, s.codex_sessions_enabled) == (
            True,
            False,
            True,
        )
        restored = RemoteSettings.model_validate(s.model_dump(mode="json"))
        assert restored.cursor_sessions_enabled is True
        assert restored.claude_sessions_enabled is False
        assert restored.codex_sessions_enabled is True

    def test_model_pin_round_trip(self) -> None:
        s = RemoteSettings.model_validate({"prompt_suggestion_model": "grok-build-0.1"})
        assert s.prompt_suggestion_model == "grok-build-0.1"
        restored = RemoteSettings.model_validate(s.model_dump(mode="json"))
        assert restored.prompt_suggestion_model == "grok-build-0.1"

    def test_nested_doom_loop_round_trip(self) -> None:
        s = RemoteSettings.model_validate(
            {"doom_loop_recovery": {"enabled": True, "max_threshold": 16, "max_retries": 3}}
        )
        assert s.doom_loop_recovery is not None
        assert s.doom_loop_recovery.enabled is True
        assert s.doom_loop_recovery.max_threshold == 16
        restored = RemoteSettings.model_validate(s.model_dump(mode="json"))
        assert restored.doom_loop_recovery is not None
        assert restored.doom_loop_recovery.max_retries == 3


class TestTolerantAnnouncements:
    def test_absent_yields_none(self) -> None:
        assert RemoteSettings.model_validate({}).announcements is None

    def test_populated(self) -> None:
        s = RemoteSettings.model_validate(
            {"announcements": [{"id": "a", "message": "m"}]}
        )
        assert s.announcements is not None
        assert len(s.announcements) == 1
        assert s.announcements[0].id == "a"
        assert s.announcements[0].message == "m"

    def test_one_bad_item_does_not_poison(self) -> None:
        # ``message`` is ``str | None``; a dict value can never coerce to str,
        # so this item fails RemoteAnnouncement validation and is dropped.
        s = RemoteSettings.model_validate(
            {
                "announcements": [
                    {"id": "good", "message": "ok"},
                    {"id": "bad", "message": {"nested": "dict"}},  # wrong type → drop
                ]
            }
        )
        assert s.announcements is not None
        assert len(s.announcements) == 1
        assert s.announcements[0].id == "good"

    def test_non_array_yields_none(self) -> None:
        s = RemoteSettings.model_validate({"announcements": "oops"})
        assert s.announcements is None


class TestTolerantGoalModels:
    def test_role_model_valid(self) -> None:
        s = RemoteSettings.model_validate(
            {"goal_planner_model": {"model": "grok-4", "agent_type": "grok-build-plan"}}
        )
        assert s.goal_planner_model is not None
        assert s.goal_planner_model.model == "grok-4"
        assert s.goal_planner_model.agent_type == "grok-build-plan"

    def test_role_model_malformed_drops_to_none(self) -> None:
        # present-but-malformed (missing required agent_type) → None, NOT a parse error.
        s = RemoteSettings.model_validate(
            {"goal_strategist_model": {"model": "grok-4"}}  # missing agent_type
        )
        assert s.goal_strategist_model is None

    def test_role_model_null_is_none(self) -> None:
        s = RemoteSettings.model_validate({"goal_planner_model": None})
        assert s.goal_planner_model is None

    def test_skeptic_non_array_is_empty(self) -> None:
        s = RemoteSettings.model_validate({"goal_skeptic_models": "nope"})
        assert s.goal_skeptic_models == []

    def test_skeptic_drops_malformed_keeps_survivors_in_order(self) -> None:
        s = RemoteSettings.model_validate(
            {
                "goal_skeptic_models": [
                    {"model": "a", "agent_type": "t"},
                    {"model": "bad"},  # missing agent_type → dropped
                    {"model": "c", "agent_type": "t"},
                ]
            }
        )
        assert [m.model for m in s.goal_skeptic_models] == ["a", "c"]


class TestImagineDenylist:
    def test_disabled_when_listed(self) -> None:
        s = RemoteSettings.model_validate({"imagine_tools_disabled": ["image_edit"]})
        assert s.imagine_tool_disabled("image_edit") is True

    def test_enabled_when_not_listed(self) -> None:
        s = RemoteSettings.model_validate({"imagine_tools_disabled": ["image_edit"]})
        assert s.imagine_tool_disabled("image_gen") is False

    def test_absent_defers_to_default(self) -> None:
        s = RemoteSettings.model_validate({})
        assert s.imagine_tool_disabled("image_edit") is False


# ===========================================================================
# types — DisplayRefresh tolerance + extra preservation
# ===========================================================================


class TestDisplayRefresh:
    def test_is_default_when_empty(self) -> None:
        assert DisplayRefreshSettings.model_validate({}).is_default() is True

    def test_tolerant_bool_wrong_type_to_none(self) -> None:
        s = DisplayRefreshSettings.model_validate({"probe_enabled": "yes"})
        assert s.probe_enabled is None
        assert s.is_default() is True

    def test_tolerant_bool_true_passes(self) -> None:
        s = DisplayRefreshSettings.model_validate({"probe_enabled": True})
        assert s.probe_enabled is True
        assert s.is_default() is False

    def test_tolerant_u32_in_range(self) -> None:
        s = DisplayRefreshSettings.model_validate({"floor_ms": 8, "max_hz": 165})
        assert s.floor_ms == 8
        assert s.max_hz == 165

    def test_tolerant_u32_negative_to_none(self) -> None:
        s = DisplayRefreshSettings.model_validate({"floor_ms": -1})
        assert s.floor_ms is None

    def test_tolerant_u32_wrong_type_to_none(self) -> None:
        s = DisplayRefreshSettings.model_validate({"ceiling_ms": "16"})
        assert s.ceiling_ms is None

    def test_extra_keys_preserved(self) -> None:
        s = DisplayRefreshSettings.model_validate({"floor_ms": 10, "future_knob": 99})
        assert s.floor_ms == 10
        assert s.model_extra == {"future_knob": 99}
        assert s.is_default() is False
        wire = s.to_wire()
        assert wire["floor_ms"] == 10
        assert wire["future_knob"] == 99

    def test_to_wire_omits_none(self) -> None:
        s = DisplayRefreshSettings.model_validate({"probe_enabled": False})
        wire = s.to_wire()
        assert wire == {"probe_enabled": False}


# ===========================================================================
# types — CampaignOverride / DoomLoop / ContextualHints / GoalRoleModel
# ===========================================================================


class TestCampaignOverride:
    def test_alias_campaign_id(self) -> None:
        c = CampaignOverride.model_validate({"campaign_id": "summer"})
        assert c.id == "summer"

    def test_patch_captures_extra_keys(self) -> None:
        c = CampaignOverride.model_validate(
            {"id": "s", "memory_enabled": True, "goal_enabled": False}
        )
        assert c.id == "s"
        assert c.patch == {"memory_enabled": True, "goal_enabled": False}

    def test_to_wire_flattens_patch(self) -> None:
        c = CampaignOverride.model_validate(
            {"campaign_id": "x", "tips": ["hi"]}
        )
        wire = c.to_wire()
        assert wire == {"campaign_id": "x", "tips": ["hi"]}


class TestDoomLoopRecovery:
    def test_to_wire_skips_none(self) -> None:
        d = DoomLoopRecoverySettings(enabled=True)
        assert d.to_wire() == {"enabled": True}

    def test_to_wire_all_set(self) -> None:
        d = DoomLoopRecoverySettings(enabled=False, max_threshold=16, max_retries=3)
        assert d.to_wire() == {"enabled": False, "max_threshold": 16, "max_retries": 3}

    def test_to_wire_empty(self) -> None:
        assert DoomLoopRecoverySettings().to_wire() == {}


class TestContextualHints:
    def test_all_default_none(self) -> None:
        c = ContextualHintsRemote()
        assert c.undo is None
        assert c.plan_mode is None
        assert c.image_input is None
        assert c.send_now is None
        assert c.small_screen is None
        assert c.word_select is None

    def test_partial_set(self) -> None:
        c = ContextualHintsRemote.model_validate({"undo": False, "send_now": True})
        assert c.undo is False
        assert c.send_now is True
        assert c.image_input is None


class TestGoalRoleModel:
    def test_required_fields(self) -> None:
        m = GoalRoleModel.model_validate({"model": "grok-4", "agent_type": "cursor"})
        assert m.model == "grok-4"
        assert m.agent_type == "cursor"

    def test_missing_field_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 — pydantic ValidationError is private path
            GoalRoleModel.model_validate({"model": "grok-4"})
