"""Tests for sampler.request_params (R203, ``xai-grok-sampling-types`` ``messages.rs``).

Covers the migrated request-side enums + leaf structs: the :class:`MessageRole`
lowercase enum, the :class:`ThinkingDisplay` snake_case enum, the
:class:`ThinkingConfig` / :class:`OutputFormat` / :class:`ToolChoiceParam`
tagged unions, and the :class:`OutputConfig` / :class:`ToolParam` /
:class:`Metadata` flat structs. Mirrors the grok wire shapes:

- ``#[serde(rename_all="lowercase")] enum`` (:class:`MessageRole`) ->
  :class:`enum.StrEnum`.
- ``#[serde(rename_all="snake_case")] enum`` (:class:`ThinkingDisplay`) ->
  :class:`enum.StrEnum`.
- ``#[serde(tag="type", rename_all="snake_case")] enum`` (the 3 tagged unions)
  -- strict, no catch-all (unknown ``type`` raises, contrasting the R201
  :class:`StopReason` catch-all which must never fail a terminal stream).
- ``serde_json::Value`` -> ``dict`` (:class:`OutputFormat` ``schema``,
  :class:`ToolParam` ``input_schema``).

No I/O (``dict.get`` is a pure mapping).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sampler.request_params import (
    AdaptiveThinkingConfig,
    AnyToolChoiceParam,
    AutoToolChoiceParam,
    DisabledThinkingConfig,
    EnabledThinkingConfig,
    JsonSchemaOutputFormat,
    MessageRole,
    Metadata,
    NamedToolChoiceParam,
    OutputConfig,
    OutputFormat,
    ThinkingConfig,
    ThinkingDisplay,
    ToolChoiceParam,
    ToolParam,
)

# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_fifteen_symbols() -> None:
    """8 type families -> 15 re-exported symbols: 2 StrEnums + 3 tagged-union
    bases (ThinkingConfig/OutputFormat/ToolChoiceParam) + 6 union variants
    (3+1+2) + 3 flat structs (OutputConfig/ToolParam/Metadata)."""
    import minimax_code.sampler.request_params as request_params

    assert len(request_params.__all__) == 15
    assert set(request_params.__all__) == {
        "AdaptiveThinkingConfig",
        "AnyToolChoiceParam",
        "AutoToolChoiceParam",
        "DisabledThinkingConfig",
        "EnabledThinkingConfig",
        "JsonSchemaOutputFormat",
        "MessageRole",
        "Metadata",
        "NamedToolChoiceParam",
        "OutputConfig",
        "OutputFormat",
        "ThinkingConfig",
        "ThinkingDisplay",
        "ToolChoiceParam",
        "ToolParam",
    }


# ---------------------------------------------------------------------------
# MessageRole: lowercase enum (User/Assistant -> "user"/"assistant").
# ---------------------------------------------------------------------------


def test_message_role_wire_values_are_lowercase() -> None:
    """grok ``#[serde(rename_all="lowercase")]`` -> ``User`` -> ``user``,
    ``Assistant`` -> ``assistant`` (lowercase, not snake_case)."""
    assert MessageRole.USER == "user"
    assert MessageRole.ASSISTANT == "assistant"


def test_message_role_str_is_wire_value() -> None:
    """``StrEnum.__str__`` returns the wire value (what serde would emit)."""
    assert str(MessageRole.USER) == "user"
    assert str(MessageRole.ASSISTANT) == "assistant"


def test_message_role_has_two_variants() -> None:
    assert {role.value for role in MessageRole} == {"user", "assistant"}


# ---------------------------------------------------------------------------
# ThinkingDisplay: snake_case enum (Omitted/Summarized).
# ---------------------------------------------------------------------------


def test_thinking_display_wire_values_are_snake_case() -> None:
    """grok ``#[serde(rename_all="snake_case")]`` -> ``Omitted`` -> ``omitted``,
    ``Summarized`` -> ``summarized``."""
    assert ThinkingDisplay.OMITTED == "omitted"
    assert ThinkingDisplay.SUMMARIZED == "summarized"


def test_thinking_display_has_two_variants() -> None:
    assert {d.value for d in ThinkingDisplay} == {"omitted", "summarized"}


# ---------------------------------------------------------------------------
# ThinkingConfig: 3-variant tagged union (tag="type", rename_all="snake_case").
# ---------------------------------------------------------------------------


def test_thinking_config_enabled_variant() -> None:
    cfg = ThinkingConfig.from_payload({"type": "enabled", "budget_tokens": 4096})
    assert isinstance(cfg, EnabledThinkingConfig)
    assert cfg.budget_tokens == 4096
    assert isinstance(cfg, ThinkingConfig)


def test_thinking_config_enabled_missing_budget_defaults_zero() -> None:
    """A missing ``budget_tokens`` tolerates to ``0`` (the platform never fails a
    request build on a malformed thinking config)."""
    cfg = ThinkingConfig.from_payload({"type": "enabled"})
    assert isinstance(cfg, EnabledThinkingConfig)
    assert cfg.budget_tokens == 0


def test_thinking_config_adaptive_variant_with_display() -> None:
    cfg = ThinkingConfig.from_payload({"type": "adaptive", "display": "summarized"})
    assert isinstance(cfg, AdaptiveThinkingConfig)
    assert cfg.display is ThinkingDisplay.SUMMARIZED


def test_thinking_config_adaptive_variant_without_display() -> None:
    cfg = ThinkingConfig.from_payload({"type": "adaptive"})
    assert isinstance(cfg, AdaptiveThinkingConfig)
    assert cfg.display is None


def test_thinking_config_adaptive_invalid_display_raises() -> None:
    """``display`` is ``Option<ThinkingDisplay>`` (strict snake_case enum); an
    unknown value fails the parse, mirroring serde's tagged-union semantics."""
    with pytest.raises(ValueError):
        ThinkingConfig.from_payload({"type": "adaptive", "display": "future"})


def test_thinking_config_disabled_variant() -> None:
    cfg = ThinkingConfig.from_payload({"type": "disabled"})
    assert isinstance(cfg, DisabledThinkingConfig)
    assert isinstance(cfg, ThinkingConfig)


def test_thinking_config_unknown_type_raises() -> None:
    """grok tagged union has no catch-all -> an unknown type fails the parse
    (contrast the R201 StopReason catch-all)."""
    with pytest.raises(ValueError, match="unknown thinking config type"):
        ThinkingConfig.from_payload({"type": "future_mode"})


# ---------------------------------------------------------------------------
# OutputFormat: 1-variant tagged union (tag="type", rename_all="snake_case").
# ---------------------------------------------------------------------------


def test_output_format_json_schema_variant() -> None:
    fmt = OutputFormat.from_payload(
        {"type": "json_schema", "schema": {"type": "object"}}
    )
    assert isinstance(fmt, JsonSchemaOutputFormat)
    assert fmt.schema == {"type": "object"}
    assert isinstance(fmt, OutputFormat)


def test_output_format_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown output format type"):
        OutputFormat.from_payload({"type": "future_format"})


# ---------------------------------------------------------------------------
# OutputConfig: leaf struct (effort + optional OutputFormat).
# ---------------------------------------------------------------------------


def test_output_config_full() -> None:
    cfg = OutputConfig.from_payload(
        {"effort": "high", "format": {"type": "json_schema", "schema": {"x": 1}}}
    )
    assert cfg.effort == "high"
    assert isinstance(cfg.format, JsonSchemaOutputFormat)
    assert cfg.format.schema == {"x": 1}


def test_output_config_empty() -> None:
    cfg = OutputConfig.from_payload({})
    assert cfg.effort is None
    assert cfg.format is None


def test_output_config_format_non_dict_ignored() -> None:
    """A non-dict ``format`` (a stray string) is ignored -> ``None`` rather than
    crash the parse."""
    cfg = OutputConfig.from_payload({"format": "not-a-dict"})
    assert cfg.format is None


# ---------------------------------------------------------------------------
# ToolChoiceParam: 3-variant tagged union (tag="type", rename_all="snake_case").
# ---------------------------------------------------------------------------


def test_tool_choice_auto_variant() -> None:
    choice = ToolChoiceParam.from_payload({"type": "auto"})
    assert isinstance(choice, AutoToolChoiceParam)
    assert isinstance(choice, ToolChoiceParam)


def test_tool_choice_any_variant() -> None:
    choice = ToolChoiceParam.from_payload({"type": "any"})
    assert isinstance(choice, AnyToolChoiceParam)


def test_tool_choice_tool_named_variant() -> None:
    """serde variant ``Tool { name }`` -> wire ``{"type":"tool","name":...}`` ->
    :class:`NamedToolChoiceParam`."""
    choice = ToolChoiceParam.from_payload({"type": "tool", "name": "get_weather"})
    assert isinstance(choice, NamedToolChoiceParam)
    assert choice.name == "get_weather"


def test_tool_choice_unknown_type_raises() -> None:
    with pytest.raises(ValueError, match="unknown tool choice type"):
        ToolChoiceParam.from_payload({"type": "future_choice"})


# ---------------------------------------------------------------------------
# ToolParam: leaf struct (name + description + input_schema).
# ---------------------------------------------------------------------------


def test_tool_param_full() -> None:
    tool = ToolParam.from_payload(
        {
            "name": "get_weather",
            "description": "Get the weather",
            "input_schema": {"type": "object"},
        }
    )
    assert tool.name == "get_weather"
    assert tool.description == "Get the weather"
    assert tool.input_schema == {"type": "object"}


def test_tool_param_missing_description() -> None:
    tool = ToolParam.from_payload(
        {"name": "get_weather", "input_schema": {"type": "object"}}
    )
    assert tool.name == "get_weather"
    assert tool.description is None


# ---------------------------------------------------------------------------
# Metadata: leaf struct (user_id).
# ---------------------------------------------------------------------------


def test_metadata_user_id() -> None:
    meta = Metadata.from_payload({"user_id": "u_123"})
    assert meta.user_id == "u_123"


def test_metadata_empty() -> None:
    meta = Metadata.from_payload({})
    assert meta.user_id is None


# ---------------------------------------------------------------------------
# Value semantics: frozen + slots + hashable + union base.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "obj",
    [
        EnabledThinkingConfig(budget_tokens=64),
        AdaptiveThinkingConfig(display=ThinkingDisplay.SUMMARIZED),
        AdaptiveThinkingConfig(),
        JsonSchemaOutputFormat(schema={"x": 1}),
        OutputConfig(effort="high"),
        NamedToolChoiceParam(name="t"),
        ToolParam(name="n", input_schema={}),
        Metadata(user_id="u"),
    ],
)
def test_request_param_variants_are_frozen(obj: object) -> None:
    """``@dataclass(frozen=True)`` -> mutating the first slot raises (mirrors
    grok's immutable struct). Each parametrized variant carries >=1 field."""
    field_name = next(iter(type(obj).__slots__))
    with pytest.raises(FrozenInstanceError):
        setattr(obj, field_name, "rewritten")  # type: ignore[misc]


def test_thinking_config_variants_share_base() -> None:
    """All 3 ThinkingConfig variants are subclasses of the union base."""
    assert isinstance(EnabledThinkingConfig(budget_tokens=1), ThinkingConfig)
    assert isinstance(AdaptiveThinkingConfig(), ThinkingConfig)
    assert isinstance(DisabledThinkingConfig(), ThinkingConfig)


def test_tool_choice_variants_share_base() -> None:
    """All 3 ToolChoiceParam variants are subclasses of the union base."""
    assert isinstance(AutoToolChoiceParam(), ToolChoiceParam)
    assert isinstance(AnyToolChoiceParam(), ToolChoiceParam)
    assert isinstance(NamedToolChoiceParam(name="t"), ToolChoiceParam)


def test_output_format_variant_shares_base() -> None:
    assert isinstance(JsonSchemaOutputFormat(schema={}), OutputFormat)


def test_str_enums_are_hashable_and_equal() -> None:
    """StrEnum members are hashable + equal by value (usable as dict keys +
    match serde string round-trip)."""
    assert MessageRole.USER == MessageRole.USER
    assert hash(MessageRole.USER) == hash(MessageRole.USER)
    assert ThinkingDisplay.OMITTED == ThinkingDisplay.OMITTED
    assert hash(ThinkingDisplay.OMITTED) == hash(ThinkingDisplay.OMITTED)
