"""Tests for sampler.chat_completion_streaming (R208,
``xai-grok-sampling-types`` ``types.rs``).

Covers the third ``types.rs`` slice -- the 4 streaming-delta leaves of the
OpenAI-compatible ChatCompletion chunk family:

- :class:`ToolCallFunctionDelta` (``#[derive(Default)]`` struct) -- ``name`` /
  ``arguments`` fragments of one streamed tool call.
- :class:`ToolCallDelta` (``#[derive(Default)]`` struct) -- one streamed tool
  call (``index`` / ``id`` / ``kind`` (wire ``type``) / ``function``).
- :class:`ChatChunkDelta` (``#[derive(Default)]`` struct) -- the delta body of
  one choice (``role`` / ``content`` / ``reasoning_content`` / ``tool_calls`` /
  ``tool_call_id``). ``tool_calls`` carries the grok
  ``deserialize_with="deserialize_null_default"`` helper -- inlined here as a
  null-tolerant parser (a ``null`` / missing / non-list ``tool_calls`` -> empty
  tuple), and the ``Vec<ToolCallDelta>`` -> ``tuple[ToolCallDelta, ...]``.
- :class:`ChatChunkChoice` (struct) -- one choice in a chunk (``index`` /
  ``delta`` / ``finish_reason``).

The 4 leaves close against the R206 atomic slice (:class:`Role` /
:class:`FinishReason`) plus each other. No I/O (``dict.get`` / ``isinstance``
are pure mappings). Mirrors the grok serde shapes:

- ``#[derive(Default)]`` -> ``default()`` classmethod + tolerant ``from_payload``.
- ``#[serde(rename="type")] Option<String>`` -> ``kind`` field holding the wire
  ``type`` string.
- ``#[serde(deserialize_with="deserialize_null_default")] Vec<T>`` ->
  null/missing/non-list tolerates to the empty tuple.
- ``Option<Role>`` / ``Option<FinishReason>`` parse through the enum's strict
  ``from_payload`` only when the wire value is a string (``null`` / non-string
  -> ``None``).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sampler.chat_completion_streaming import (
    ChatChunkChoice,
    ChatChunkDelta,
    ToolCallDelta,
    ToolCallFunctionDelta,
)

# ---------------------------------------------------------------------------
# Module surface
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_four_symbols() -> None:
    """4 streaming-delta leaves re-exported."""
    import minimax_code.sampler.chat_completion_streaming as streaming

    assert len(streaming.__all__) == 4
    assert set(streaming.__all__) == {
        "ChatChunkChoice",
        "ChatChunkDelta",
        "ToolCallDelta",
        "ToolCallFunctionDelta",
    }


def test_package_barrel_re_exports_streaming_leaves() -> None:
    """The package barrel flattens the 4 streaming-delta leaves."""
    import minimax_code.sampler as sampler

    for name in (
        "ChatChunkChoice",
        "ChatChunkDelta",
        "ToolCallDelta",
        "ToolCallFunctionDelta",
    ):
        assert name in sampler.__all__


# ---------------------------------------------------------------------------
# ToolCallFunctionDelta: Default struct (name/arguments fragment).
# ---------------------------------------------------------------------------


def test_tool_call_function_delta_default_is_all_none() -> None:
    """``#[derive(Default)]`` -> both fields ``None``."""
    assert ToolCallFunctionDelta.default() == ToolCallFunctionDelta(name=None, arguments=None)


def test_tool_call_function_delta_from_payload_maps_name_and_arguments() -> None:
    delta = ToolCallFunctionDelta.from_payload(
        {"name": "get_weather", "arguments": '{"city": "sf"}'}
    )
    assert delta.name == "get_weather"
    assert delta.arguments == '{"city": "sf"}'


def test_tool_call_function_delta_from_payload_missing_keys_default_none() -> None:
    delta = ToolCallFunctionDelta.from_payload({"name": "f"})
    assert delta.name == "f"
    assert delta.arguments is None


def test_tool_call_function_delta_from_payload_non_dict_defaults() -> None:
    """A missing / null / non-dict payload -> the all-``None`` default."""
    assert ToolCallFunctionDelta.from_payload(None) == ToolCallFunctionDelta.default()
    assert ToolCallFunctionDelta.from_payload("oops") == ToolCallFunctionDelta.default()
    assert ToolCallFunctionDelta.from_payload([]) == ToolCallFunctionDelta.default()


# ---------------------------------------------------------------------------
# ToolCallDelta: Default struct (index/id/kind/function).
# ---------------------------------------------------------------------------


def test_tool_call_delta_default_is_index_zero_rest_none() -> None:
    """``#[derive(Default)]`` -> ``index`` 0, the rest ``None``."""
    assert ToolCallDelta.default() == ToolCallDelta(index=0)


def test_tool_call_delta_from_payload_reads_kind_from_wire_type_key() -> None:
    """``#[serde(rename="type")]`` -> ``kind`` holds the wire ``type`` value."""
    delta = ToolCallDelta.from_payload(
        {"index": 2, "id": "call_42", "type": "function", "function": {"name": "f"}}
    )
    assert delta.index == 2
    assert delta.id == "call_42"
    assert delta.kind == "function"
    assert delta.function.name == "f"


def test_tool_call_delta_from_payload_index_defaults_to_zero() -> None:
    delta = ToolCallDelta.from_payload({"id": "call_1"})
    assert delta.index == 0
    assert delta.id == "call_1"
    assert delta.kind is None
    assert delta.function is None


def test_tool_call_delta_from_payload_non_dict_function_stays_none() -> None:
    """``function`` parses through :meth:`ToolCallFunctionDelta.from_payload`
    only when present + dict-valued (else ``None``)."""
    delta = ToolCallDelta.from_payload({"function": "not a dict"})
    assert delta.function is None
    delta2 = ToolCallDelta.from_payload({})
    assert delta2.function is None


def test_tool_call_delta_from_payload_non_dict_defaults() -> None:
    assert ToolCallDelta.from_payload(None) == ToolCallDelta.default()
    assert ToolCallDelta.from_payload(123) == ToolCallDelta.default()


# ---------------------------------------------------------------------------
# ChatChunkDelta: Default struct (the streamed choice delta body).
# ---------------------------------------------------------------------------


def test_chat_chunk_delta_default_is_role_none_and_empty_tool_calls() -> None:
    """``#[derive(Default)]`` -> ``role`` / ``content`` / ``reasoning_content``
    / ``tool_call_id`` ``None``, ``tool_calls`` the empty tuple."""
    delta = ChatChunkDelta.default()
    assert delta.role is None
    assert delta.content is None
    assert delta.reasoning_content is None
    assert delta.tool_calls == ()
    assert delta.tool_call_id is None


def test_chat_chunk_delta_role_parses_through_strict_role_enum() -> None:
    """``Option<Role>`` parses through :meth:`Role.from_payload` when the wire
    value is a string."""
    delta = ChatChunkDelta.from_payload({"role": "assistant"})
    assert delta.role == "assistant"


def test_chat_chunk_delta_non_string_role_stays_none() -> None:
    """A ``null`` / non-string ``role`` stays ``None`` (mirrors ``Option::None``)."""
    assert ChatChunkDelta.from_payload({"role": None}).role is None
    assert ChatChunkDelta.from_payload({"role": 123}).role is None
    assert ChatChunkDelta.from_payload({}).role is None


def test_chat_chunk_delta_tool_calls_is_tuple_not_list() -> None:
    """``Vec<ToolCallDelta>`` -> ``tuple[ToolCallDelta, ...]`` (the list-carrying
    Vec becomes an immutable tuple)."""
    delta = ChatChunkDelta.from_payload({"tool_calls": [{"index": 1}, {"index": 2}]})
    assert isinstance(delta.tool_calls, tuple)
    assert len(delta.tool_calls) == 2
    assert delta.tool_calls[0].index == 1
    assert delta.tool_calls[1].index == 2


def test_chat_chunk_delta_null_tool_calls_becomes_empty_tuple() -> None:
    """The grok ``deserialize_null_default`` helper (inlined): a ``null``
    ``tool_calls`` tolerates to the empty tuple (NOT a parse failure)."""
    assert ChatChunkDelta.from_payload({"tool_calls": None}).tool_calls == ()


def test_chat_chunk_delta_missing_tool_calls_becomes_empty_tuple() -> None:
    """A missing ``tool_calls`` key -> empty tuple (mirrors ``default``)."""
    assert ChatChunkDelta.from_payload({}).tool_calls == ()


def test_chat_chunk_delta_non_list_tool_calls_becomes_empty_tuple() -> None:
    """A non-list ``tool_calls`` wire value -> empty tuple (tolerant parser)."""
    assert ChatChunkDelta.from_payload({"tool_calls": "oops"}).tool_calls == ()
    assert ChatChunkDelta.from_payload({"tool_calls": {"index": 1}}).tool_calls == ()


def test_chat_chunk_delta_tool_calls_skips_non_dict_items() -> None:
    """A non-dict item in the ``tool_calls`` list is skipped (not crash)."""
    delta = ChatChunkDelta.from_payload({"tool_calls": [{"index": 0}, "junk", 7]})
    assert len(delta.tool_calls) == 1
    assert delta.tool_calls[0].index == 0


def test_chat_chunk_delta_from_payload_non_dict_defaults() -> None:
    assert ChatChunkDelta.from_payload(None) == ChatChunkDelta.default()
    assert ChatChunkDelta.from_payload("oops") == ChatChunkDelta.default()


def test_chat_chunk_delta_full_payload_round_trips() -> None:
    delta = ChatChunkDelta.from_payload(
        {
            "role": "assistant",
            "content": "hello",
            "reasoning_content": "thinking...",
            "tool_call_id": "tc_9",
        }
    )
    assert delta.role == "assistant"
    assert delta.content == "hello"
    assert delta.reasoning_content == "thinking..."
    assert delta.tool_call_id == "tc_9"


# ---------------------------------------------------------------------------
# ChatChunkChoice: struct (index + delta + finish_reason).
# ---------------------------------------------------------------------------


def test_chat_chunk_choice_from_payload_maps_index_delta_finish() -> None:
    choice = ChatChunkChoice.from_payload(
        {
            "index": 0,
            "delta": {"role": "assistant", "content": "hi"},
            "finish_reason": "stop",
        }
    )
    assert choice.index == 0
    assert choice.delta.role == "assistant"
    assert choice.delta.content == "hi"
    assert choice.finish_reason == "stop"


def test_chat_chunk_choice_index_defaults_to_zero() -> None:
    choice = ChatChunkChoice.from_payload({"delta": {}})
    assert choice.index == 0


def test_chat_chunk_choice_non_string_finish_reason_stays_none() -> None:
    """A ``null`` / non-string ``finish_reason`` stays ``None`` (mirrors
    ``Option::None``)."""
    assert ChatChunkChoice.from_payload({"finish_reason": None}).finish_reason is None
    assert ChatChunkChoice.from_payload({"finish_reason": 5}).finish_reason is None
    assert ChatChunkChoice.from_payload({}).finish_reason is None


def test_chat_chunk_choice_non_dict_payload_falls_back_to_zero_index_empty_delta() -> None:
    """A malformed chunk never crashes the stream: a non-dict payload -> a
    zero-index empty-delta fallback."""
    choice = ChatChunkChoice.from_payload(None)
    assert choice.index == 0
    assert choice.delta == ChatChunkDelta.default()
    assert choice.finish_reason is None


def test_chat_chunk_choice_nests_delta_tool_call_function() -> None:
    """Full nesting: choice -> delta -> tool_calls[i] -> function (the recursive
    parse reaches the innermost ToolCallFunctionDelta)."""
    choice = ChatChunkChoice.from_payload(
        {
            "index": 0,
            "delta": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call_1",
                        "type": "function",
                        "function": {"name": "get_weather", "arguments": "{}"},
                    }
                ],
            },
            "finish_reason": "tool_calls",
        }
    )
    assert choice.delta.role == "assistant"
    assert choice.finish_reason == "tool_calls"
    assert len(choice.delta.tool_calls) == 1
    tc = choice.delta.tool_calls[0]
    assert tc.index == 0
    assert tc.id == "call_1"
    assert tc.kind == "function"
    assert tc.function.name == "get_weather"
    assert tc.function.arguments == "{}"


# ---------------------------------------------------------------------------
# Value semantics: frozen + slots + hashable.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "obj",
    [
        ToolCallFunctionDelta(name="f", arguments="{}"),
        ToolCallDelta(index=1, id="x", kind="function"),
        ChatChunkDelta(content="hi"),
        ChatChunkChoice(index=0, delta=ChatChunkDelta.default()),
    ],
)
def test_streaming_leaves_are_frozen(obj: object) -> None:
    """``@dataclass(frozen=True)`` -> mutating the first declared slot raises
    (mirrors grok's immutable struct). Uses ``setattr`` with a variable field
    name (the first declared slot) so the raise is driven by the dataclass
    ``__setattr__`` (B010-safe: the attribute name is a variable, not a literal
    constant property name)."""
    field_name = next(iter(type(obj).__slots__))
    with pytest.raises(FrozenInstanceError):
        setattr(obj, field_name, "rewritten")  # type: ignore[misc]


def test_streaming_leaves_are_hashable_and_equal() -> None:
    """``frozen=True`` -> hashable + equal-by-value."""
    a = ToolCallFunctionDelta(name="f", arguments="{}")
    b = ToolCallFunctionDelta(name="f", arguments="{}")
    assert a == b
    assert hash(a) == hash(b)
    c = ChatChunkDelta(content="x", tool_calls=(ToolCallDelta(index=1),))
    d = ChatChunkDelta(content="x", tool_calls=(ToolCallDelta(index=1),))
    assert c == d
    assert hash(c) == hash(d)


def test_streaming_leaves_declare_slots() -> None:
    """``slots=True`` -> the class declares ``__slots__`` over its fields."""
    assert ToolCallFunctionDelta.__slots__ == ("name", "arguments")
    assert ToolCallDelta.__slots__ == ("index", "id", "kind", "function")
    assert ChatChunkDelta.__slots__ == (
        "role",
        "content",
        "reasoning_content",
        "tool_calls",
        "tool_call_id",
    )
    assert ChatChunkChoice.__slots__ == ("index", "delta", "finish_reason")
