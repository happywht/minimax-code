"""Tests for sampler.chat_completion_leaves (R206,
``xai-grok-sampling-types`` ``types.rs``).

Covers the first ``types.rs`` slice -- 9 zero-dependency atomic leaves of the
OpenAI-compatible ChatCompletion type family: 4 wire-string enums (``Role`` /
``ToolType`` / ``FinishReason`` / ``ReasoningEffort``) + 5 flat leaf structs
(``ImageUrl`` / ``ToolChoiceFunction`` / ``ToolCallFunction`` /
``PromptTokensDetails`` / ``CompletionTokensDetails``). Mirrors the grok wire
shapes:

- ``#[serde(rename_all="lowercase")] enum`` (``Role`` / ``ToolType`` /
  ``ReasoningEffort``) -> :class:`enum.StrEnum` lowercase wire values.
- ``#[serde(rename_all="snake_case")] enum`` (``FinishReason``) ->
  :class:`enum.StrEnum` snake_case wire values.
- ``#[default] Medium`` -> :data:`DEFAULT_REASONING_EFFORT`.
- plain struct (``ImageUrl`` / ``ToolChoiceFunction`` / ``ToolCallFunction``) ->
  frozen+slots with a tolerant ``from_payload``.
- ``#[derive(Default)] struct`` (``PromptTokensDetails`` /
  ``CompletionTokensDetails``) -> frozen+slots + ``default()`` + ``from_payload``.
- ``ToolCallFunction::from_json`` -> ``json.dumps`` the value into the
  ``arguments`` string.

Strict enum parse: NO ``#[serde(other)]`` catch-all -> an unknown wire string
/ non-string raises ``ValueError`` (mirrors serde's enum failure -- contrast
the R201 :class:`StopReason` catch-all which must never fail a terminal
stream). The grok ``ReasoningEffort::None`` variant maps to ``NONE`` (``None``
is a Python keyword). ``types.rs`` ``Role`` is distinct from the R203
:class:`MessageRole`.

No I/O (``dict.get`` / ``isinstance`` / ``json.dumps`` are pure mappings).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sampler.chat_completion_leaves import (
    DEFAULT_REASONING_EFFORT,
    CompletionTokensDetails,
    FinishReason,
    ImageUrl,
    PromptTokensDetails,
    ReasoningEffort,
    Role,
    ToolCallFunction,
    ToolChoiceFunction,
    ToolType,
)

# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_ten_symbols() -> None:
    """9 atomic types + 1 default constant = 10 re-exported symbols: 4
    wire-string enums + DEFAULT_REASONING_EFFORT + 5 flat leaf structs."""
    import minimax_code.sampler.chat_completion_leaves as leaves

    assert len(leaves.__all__) == 10
    assert set(leaves.__all__) == {
        "DEFAULT_REASONING_EFFORT",
        "CompletionTokensDetails",
        "FinishReason",
        "ImageUrl",
        "PromptTokensDetails",
        "ReasoningEffort",
        "Role",
        "ToolCallFunction",
        "ToolChoiceFunction",
        "ToolType",
    }


# ---------------------------------------------------------------------------
# Role: ChatCompletion message role (#[serde(rename_all="lowercase")]).
# ---------------------------------------------------------------------------


def test_role_labels_match_serde_lowercase() -> None:
    """grok ``System`` -> ``"system"``; ``User`` -> ``"user"``; ``Assistant`` ->
    ``"assistant"``; ``Tool`` -> ``"tool"`` (lowercase wire values)."""
    assert Role.SYSTEM == "system"
    assert Role.USER == "user"
    assert Role.ASSISTANT == "assistant"
    assert Role.TOOL == "tool"


def test_role_str_is_wire_value() -> None:
    """``StrEnum.__str__`` returns the wire value (what serde would emit)."""
    assert str(Role.ASSISTANT) == "assistant"


def test_role_from_payload_known() -> None:
    """A known wire string -> the matching member (``is`` -- StrEnum members
    are singletons)."""
    assert Role.from_payload("tool") is Role.TOOL
    assert Role.from_payload("system") is Role.SYSTEM


def test_role_from_payload_unknown_raises() -> None:
    """grok enum has no ``#[serde(other)]`` catch-all -> an unknown role raises
    (contrast the R201 StopReason catch-all)."""
    with pytest.raises(ValueError, match="unknown role wire value"):
        Role.from_payload("future_role")


def test_role_from_payload_non_string_raises() -> None:
    """A non-string wire value raises (the enum parses wire strings only)."""
    with pytest.raises(ValueError, match="role wire value must be a string"):
        Role.from_payload(42)
    with pytest.raises(ValueError, match="role wire value must be a string"):
        Role.from_payload(None)


def test_role_is_distinct_from_message_role() -> None:
    """``types.rs`` ``Role`` (4 ChatCompletion variants) is a different enum
    from the R203 :class:`MessageRole` (2 Anthropic variants) -- never collide
    in the barrel. ``Role`` carries ``TOOL`` / ``SYSTEM``; ``MessageRole``
    does not."""
    from minimax_code.sampler.request_params import MessageRole

    assert Role is not MessageRole
    assert Role.TOOL == "tool"
    assert Role.SYSTEM == "system"
    assert not hasattr(MessageRole, "TOOL")
    assert not hasattr(MessageRole, "SYSTEM")


# ---------------------------------------------------------------------------
# ToolType: tool discriminator (#[serde(rename_all="lowercase")]).
# ---------------------------------------------------------------------------


def test_tool_type_single_function_variant() -> None:
    """grok ships a 1-variant enum -> ``Function`` -> ``"function"``."""
    assert ToolType.FUNCTION == "function"
    assert {t.value for t in ToolType} == {"function"}


def test_tool_type_from_payload_known_then_unknown() -> None:
    assert ToolType.from_payload("function") is ToolType.FUNCTION
    with pytest.raises(ValueError, match="unknown tool type wire value"):
        ToolType.from_payload("xml")


# ---------------------------------------------------------------------------
# FinishReason: ChatCompletion finish reason (#[serde(rename_all="snake_case")]).
# ---------------------------------------------------------------------------


def test_finish_reason_labels_match_serde_snake_case() -> None:
    """grok ``ToolCalls`` -> ``"tool_calls"``; ``ContentFilter`` ->
    ``"content_filter"`` (snake_case wire values)."""
    assert FinishReason.STOP == "stop"
    assert FinishReason.LENGTH == "length"
    assert FinishReason.TOOL_CALLS == "tool_calls"
    assert FinishReason.CONTENT_FILTER == "content_filter"
    assert FinishReason.FUNCTION_CALL == "function_call"


def test_finish_reason_from_payload_known_then_unknown() -> None:
    assert FinishReason.from_payload("tool_calls") is FinishReason.TOOL_CALLS
    assert FinishReason.from_payload("content_filter") is FinishReason.CONTENT_FILTER
    with pytest.raises(ValueError, match="unknown finish reason wire value"):
        FinishReason.from_payload("future_reason")


# ---------------------------------------------------------------------------
# ReasoningEffort: reasoning effort level (lowercase, #[default] Medium).
# ---------------------------------------------------------------------------


def test_reasoning_effort_labels_match_serde_lowercase() -> None:
    """grok ``None`` -> ``"none"`` (mapped to :attr:`NONE` -- ``None`` is a
    Python keyword); ``Xhigh`` -> ``"xhigh"``."""
    assert ReasoningEffort.NONE == "none"
    assert ReasoningEffort.MINIMAL == "minimal"
    assert ReasoningEffort.LOW == "low"
    assert ReasoningEffort.MEDIUM == "medium"
    assert ReasoningEffort.HIGH == "high"
    assert ReasoningEffort.XHIGH == "xhigh"


def test_default_reasoning_effort_is_medium() -> None:
    """grok ``#[default] Medium`` -> :data:`DEFAULT_REASONING_EFFORT`."""
    assert DEFAULT_REASONING_EFFORT is ReasoningEffort.MEDIUM
    assert DEFAULT_REASONING_EFFORT == "medium"


def test_reasoning_effort_from_payload_known_then_unknown() -> None:
    assert ReasoningEffort.from_payload("none") is ReasoningEffort.NONE
    assert ReasoningEffort.from_payload("xhigh") is ReasoningEffort.XHIGH
    with pytest.raises(ValueError, match="unknown reasoning effort wire value"):
        ReasoningEffort.from_payload("ultra")


# ---------------------------------------------------------------------------
# ImageUrl: image URL leaf struct.
# ---------------------------------------------------------------------------


def test_image_url_full_payload() -> None:
    img = ImageUrl.from_payload({"url": "https://example.com/y.png"})
    assert img.url == "https://example.com/y.png"


def test_image_url_missing_url_defaults_empty() -> None:
    """A missing / null / non-dict payload tolerates to the empty string (the
    platform never fails a parse on a malformed image block)."""
    assert ImageUrl.from_payload({}).url == ""
    assert ImageUrl.from_payload(None).url == ""
    assert ImageUrl.from_payload("not-a-dict").url == ""


def test_image_url_declares_slots() -> None:
    """``slots=True`` -> ``__slots__`` over the single field + no per-instance
    ``__dict__``."""
    assert ImageUrl.__slots__ == ("url",)
    assert not hasattr(ImageUrl(url="x"), "__dict__")


# ---------------------------------------------------------------------------
# ToolChoiceFunction: named-function tool choice leaf struct.
# ---------------------------------------------------------------------------


def test_tool_choice_function_from_payload() -> None:
    tcf = ToolChoiceFunction.from_payload({"name": "get_weather"})
    assert tcf.name == "get_weather"
    assert ToolChoiceFunction.from_payload({}).name == ""
    assert ToolChoiceFunction.from_payload(None).name == ""


# ---------------------------------------------------------------------------
# ToolCallFunction: tool call function name + serialized arguments.
# ---------------------------------------------------------------------------


def test_tool_call_function_from_payload() -> None:
    tcf = ToolCallFunction.from_payload({"name": "get_weather", "arguments": '{"city": "SF"}'})
    assert tcf.name == "get_weather"
    assert tcf.arguments == '{"city": "SF"}'
    # missing keys / non-dict default to the empty string
    assert ToolCallFunction.from_payload({}).name == ""
    assert ToolCallFunction.from_payload({}).arguments == ""
    assert ToolCallFunction.from_payload(None).arguments == ""


def test_tool_call_function_from_json_serializes_value() -> None:
    """``from_json`` mirrors ``serde_json::Value::to_string``: a dict -> JSON
    string, a string -> quoted, a number -> bare."""
    assert ToolCallFunction.from_json("f", {"city": "SF"}).arguments == '{"city": "SF"}'
    assert ToolCallFunction.from_json("f", "hi").arguments == '"hi"'
    assert ToolCallFunction.from_json("f", 42).arguments == "42"
    assert ToolCallFunction.from_json("f", [1, 2]).arguments == "[1, 2]"


def test_tool_call_function_from_json_keeps_name() -> None:
    assert ToolCallFunction.from_json("search", {}).name == "search"


# ---------------------------------------------------------------------------
# PromptTokensDetails / CompletionTokensDetails (#[derive(Default)]).
# ---------------------------------------------------------------------------


def test_prompt_tokens_details_default_is_zeros() -> None:
    """``default()`` mirrors ``#[derive(Default)]``: both fields 0."""
    details = PromptTokensDetails.default()
    assert details.cached_tokens == 0
    assert details.audio_tokens == 0
    assert PromptTokensDetails.from_payload({}) == PromptTokensDetails.default()


def test_prompt_tokens_details_from_payload_parses_fields() -> None:
    details = PromptTokensDetails.from_payload({"cached_tokens": 10, "audio_tokens": 2})
    assert details.cached_tokens == 10
    assert details.audio_tokens == 2
    # a missing key defaults to 0
    partial = PromptTokensDetails.from_payload({"cached_tokens": 5})
    assert partial.cached_tokens == 5
    assert partial.audio_tokens == 0


def test_prompt_tokens_details_non_dict_defaults() -> None:
    """A null / non-dict payload -> the all-zero default rather than crash."""
    assert PromptTokensDetails.from_payload(None) == PromptTokensDetails.default()
    assert PromptTokensDetails.from_payload("nope") == PromptTokensDetails.default()


def test_completion_tokens_details_default_is_zeros() -> None:
    details = CompletionTokensDetails.default()
    assert details.reasoning_tokens == 0
    assert details.audio_tokens == 0
    assert details.accepted_prediction_tokens == 0
    assert details.rejected_prediction_tokens == 0


def test_completion_tokens_details_from_payload_parses_all_four_fields() -> None:
    details = CompletionTokensDetails.from_payload(
        {
            "reasoning_tokens": 100,
            "audio_tokens": 4,
            "accepted_prediction_tokens": 7,
            "rejected_prediction_tokens": 1,
        }
    )
    assert details.reasoning_tokens == 100
    assert details.audio_tokens == 4
    assert details.accepted_prediction_tokens == 7
    assert details.rejected_prediction_tokens == 1
    # missing keys default to 0
    partial = CompletionTokensDetails.from_payload({"reasoning_tokens": 50})
    assert partial.reasoning_tokens == 50
    assert partial.accepted_prediction_tokens == 0


def test_completion_tokens_details_non_dict_defaults() -> None:
    assert CompletionTokensDetails.from_payload(None) == CompletionTokensDetails.default()


# ---------------------------------------------------------------------------
# Value semantics: frozen + slots + hashable.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "obj",
    [
        ImageUrl(url="x"),
        ToolChoiceFunction(name="f"),
        ToolCallFunction(name="f", arguments="{}"),
        PromptTokensDetails(cached_tokens=1),
        CompletionTokensDetails(reasoning_tokens=2),
    ],
)
def test_leaf_structs_are_frozen(obj: object) -> None:
    """``@dataclass(frozen=True)`` -> mutating the first declared slot raises
    (mirrors grok's immutable struct). Uses ``setattr`` with a variable field
    name (the first declared slot) so the raise is driven by the dataclass
    ``__setattr__`` rather than a slots-name lookup -- a frozen+slots dataclass
    raises ``FrozenInstanceError`` on a real field but a ``TypeError`` on an
    out-of-slots name (so the target must be a declared field)."""
    field_name = next(iter(type(obj).__slots__))
    with pytest.raises(FrozenInstanceError):
        setattr(obj, field_name, "rewritten")  # type: ignore[misc]


def test_leaf_structs_are_hashable_and_equal() -> None:
    """``frozen=True`` -> hashable + equal-by-value (usable as dict keys,
    mirrors grok's immutable struct)."""
    assert ImageUrl(url="x") == ImageUrl(url="x")
    assert hash(ImageUrl(url="x")) == hash(ImageUrl(url="x"))
    assert ToolCallFunction(name="f", arguments="{}") == ToolCallFunction(
        name="f", arguments="{}"
    )
    assert hash(ToolCallFunction(name="f", arguments="{}")) == hash(
        ToolCallFunction(name="f", arguments="{}")
    )
    assert PromptTokensDetails(cached_tokens=1) == PromptTokensDetails(cached_tokens=1)
    assert hash(PromptTokensDetails(cached_tokens=1)) == hash(PromptTokensDetails(cached_tokens=1))
    assert CompletionTokensDetails() == CompletionTokensDetails.default()
    assert hash(CompletionTokensDetails()) == hash(CompletionTokensDetails.default())
