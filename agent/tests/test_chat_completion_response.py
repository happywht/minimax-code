"""Tests for ``sampler.chat_completion_response`` (R215,
``xai-grok-sampling-types`` ``types.rs`` 459-587).

Covers the 5 ChatCompletion response + streaming-chunk *exterior* envelopes:
:class:`ToolCallResponse` / :class:`ChatResponseMessage` / :class:`ChatChoice`
/ :class:`ChatCompletionResponse` / :class:`ChatCompletionChunk` -- the
non-streaming peer of the R208 streaming-delta *interior*.

Two keystones under test:

1. The R211 :func:`empty_string_as_none` hook on
   :attr:`ChatCompletionChunk.system_fingerprint` -- an empty wire string
   normalizes to ``None`` (some upstream services emit ``""`` to mean
   "absent"). This is the single external dependency that made R208 defer
   :class:`ChatCompletionChunk`; R211 landed the helper, R215 lifts the
   deferral.
2. The strict required-role posture on :class:`ChatResponseMessage` -- a
   missing / non-string / unknown ``role`` raises ``ValueError`` (mirrors
   serde's missing-required-field failure; same posture as the R210
   :class:`ChatRequestMessage`). :class:`ChatChoice` /
   :class:`ChatCompletionResponse` / :class:`ChatCompletionChunk` carry the
   same required-envelope posture (a non-dict payload raises), while
   :class:`ToolCallResponse` uses the tolerant posture (a non-dict payload ->
   the all-empty instance, mirroring the R207 :class:`ToolCallRequest` -- a
   single bad tool call never aborts the surrounding response parse).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import minimax_code.sampler.chat_completion_response as _ccr
from minimax_code.sampler import (
    ChatChoice,
    ChatCompletionChunk,
    ChatCompletionResponse,
    ChatResponseMessage,
    ToolCallResponse,
)
from minimax_code.sampler.chat_completion_leaves import (
    FinishReason,
    Role,
)
from minimax_code.sampler.chat_completion_mid import ChatUsage
from minimax_code.sampler.chat_completion_streaming import ChatChunkChoice

# A valid role / finish_reason wire value, taken dynamically so the suite does
# not hard-code a specific variant name (the R206 enums keep OpenAI's standard
# snake_case wire labels, but the assertions stay variant-agnostic).
_VALID_ROLE = next(iter(Role)).value
_VALID_FINISH = next(iter(FinishReason)).value


class TestBarrelReExport:
    """The sampler barrel re-exports all 5 envelopes by identity."""

    @pytest.mark.parametrize(
        ("barrel", "direct"),
        [
            (ToolCallResponse, _ccr.ToolCallResponse),
            (ChatResponseMessage, _ccr.ChatResponseMessage),
            (ChatChoice, _ccr.ChatChoice),
            (ChatCompletionResponse, _ccr.ChatCompletionResponse),
            (ChatCompletionChunk, _ccr.ChatCompletionChunk),
        ],
    )
    def test_barrel_symbol_is_direct_module(self, barrel: object, direct: object) -> None:
        assert barrel is direct


# ---------------------------------------------------------------------------
# ToolCallResponse: tolerant posture (non-dict -> all-empty instance).
# ---------------------------------------------------------------------------


class TestToolCallResponse:
    """``kind`` reads the wire ``type`` key (a free-form string, NOT the
    :class:`ToolType` enum); a non-dict payload -> the all-empty instance."""

    def test_full_payload_round_trip(self) -> None:
        tc = ToolCallResponse.from_payload(
            {
                "id": "call_abc",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"SF"}'},
            }
        )
        assert tc.id == "call_abc"
        assert tc.kind == "function"
        assert tc.function.name == "get_weather"
        assert tc.function.arguments == '{"city":"SF"}'

    def test_kind_reads_wire_type_key_as_free_string(self) -> None:
        # The discriminator is a free-form string -- a non-ToolType value still
        # parses (the response echoes whatever the server emitted).
        tc = ToolCallResponse.from_payload({"id": "x", "type": "custom_kind"})
        assert tc.kind == "custom_kind"

    def test_non_dict_payload_returns_all_empty_instance(self) -> None:
        for bad in [None, 42, "x", [], 1.5]:
            tc = ToolCallResponse.from_payload(bad)
            assert tc.id == ""
            assert tc.kind == ""
            assert tc.function.name == ""

    def test_missing_id_defaults_to_empty_string(self) -> None:
        tc = ToolCallResponse.from_payload({"type": "function"})
        assert tc.id == ""

    def test_missing_kind_defaults_to_empty_string(self) -> None:
        tc = ToolCallResponse.from_payload({"id": "x"})
        assert tc.kind == ""

    def test_missing_function_falls_back_to_empty_tool_call_function(self) -> None:
        tc = ToolCallResponse.from_payload({"id": "x", "type": "function"})
        assert tc.function.name == ""
        assert tc.function.arguments == ""

    def test_non_dict_function_falls_back_to_empty(self) -> None:
        tc = ToolCallResponse.from_payload(
            {"id": "x", "type": "function", "function": "not-a-dict"}
        )
        assert tc.function.name == ""


# ---------------------------------------------------------------------------
# ChatResponseMessage: strict required-role posture.
# ---------------------------------------------------------------------------


class TestChatResponseMessage:
    """``role`` is required (a missing / unknown role raises); the optional
    fields + the ``tool_calls`` Vec tolerate absence."""

    def test_full_payload_round_trip(self) -> None:
        msg = ChatResponseMessage.from_payload(
            {
                "role": _VALID_ROLE,
                "content": "Hello!",
                "reasoning_content": "thinking...",
                "tool_calls": [
                    {"id": "c1", "type": "function", "function": {"name": "f"}},
                ],
                "tool_call_id": "tc_1",
                "citations": ["src_a", "src_b"],
            }
        )
        assert msg.role.value == _VALID_ROLE
        assert msg.content == "Hello!"
        assert msg.reasoning_content == "thinking..."
        assert len(msg.tool_calls) == 1
        assert msg.tool_calls[0].id == "c1"
        assert msg.tool_call_id == "tc_1"
        assert msg.citations == ("src_a", "src_b")

    def test_non_dict_payload_raises(self) -> None:
        for bad in [None, 42, "x", []]:
            with pytest.raises(ValueError, match="must be a dict"):
                ChatResponseMessage.from_payload(bad)

    def test_missing_role_raises(self) -> None:
        # grok `pub role: Role` has no #[serde(default)] -> missing field fails.
        with pytest.raises(ValueError):
            ChatResponseMessage.from_payload({"content": "x"})

    def test_non_string_role_raises(self) -> None:
        with pytest.raises(ValueError):
            ChatResponseMessage.from_payload({"role": 123})

    def test_unknown_role_raises(self) -> None:
        with pytest.raises(ValueError):
            ChatResponseMessage.from_payload({"role": "not-a-real-role"})

    def test_optional_fields_default_to_none_when_absent(self) -> None:
        msg = ChatResponseMessage.from_payload({"role": _VALID_ROLE})
        assert msg.content is None
        assert msg.reasoning_content is None
        assert msg.tool_call_id is None
        assert msg.citations is None

    def test_tool_calls_defaults_to_empty_tuple_when_absent(self) -> None:
        msg = ChatResponseMessage.from_payload({"role": _VALID_ROLE})
        assert msg.tool_calls == ()

    def test_tool_calls_null_tolerates_to_empty_tuple(self) -> None:
        msg = ChatResponseMessage.from_payload(
            {"role": _VALID_ROLE, "tool_calls": None}
        )
        assert msg.tool_calls == ()

    def test_tool_calls_non_list_tolerates_to_empty_tuple(self) -> None:
        msg = ChatResponseMessage.from_payload(
            {"role": _VALID_ROLE, "tool_calls": "not-a-list"}
        )
        assert msg.tool_calls == ()

    def test_tool_calls_non_dict_items_skipped(self) -> None:
        msg = ChatResponseMessage.from_payload(
            {
                "role": _VALID_ROLE,
                "tool_calls": [
                    None,
                    {"id": "c1", "type": "function", "function": {"name": "f"}},
                    42,
                    {"id": "c2", "type": "function", "function": {"name": "g"}},
                ],
            }
        )
        assert [tc.id for tc in msg.tool_calls] == ["c1", "c2"]

    def test_citations_skips_non_string_items(self) -> None:
        msg = ChatResponseMessage.from_payload(
            {"role": _VALID_ROLE, "citations": ["a", 42, "b", None, "c"]}
        )
        assert msg.citations == ("a", "b", "c")

    def test_citations_empty_list_yields_empty_tuple_not_none(self) -> None:
        # grok `Option<Vec<String>>` with `skip_serializing_if="Option::is_none"`
        # -> Some(vec![]) is legal wire (empty tuple, not None).
        msg = ChatResponseMessage.from_payload(
            {"role": _VALID_ROLE, "citations": []}
        )
        assert msg.citations == ()

    def test_citations_null_yields_none(self) -> None:
        msg = ChatResponseMessage.from_payload(
            {"role": _VALID_ROLE, "citations": None}
        )
        assert msg.citations is None


# ---------------------------------------------------------------------------
# ChatChoice: required index + message, optional finish_reason.
# ---------------------------------------------------------------------------


class TestChatChoice:
    """``index`` defaults to 0; ``message`` is required (strict); the
    optional ``finish_reason`` parses only when the wire value is a string."""

    def test_full_payload_round_trip(self) -> None:
        choice = ChatChoice.from_payload(
            {
                "index": 2,
                "message": {"role": _VALID_ROLE, "content": "Hi"},
                "finish_reason": _VALID_FINISH,
            }
        )
        assert choice.index == 2
        assert choice.message.role.value == _VALID_ROLE
        assert choice.message.content == "Hi"
        assert isinstance(choice.finish_reason, FinishReason)

    def test_non_dict_payload_raises(self) -> None:
        for bad in [None, 42, "x", []]:
            with pytest.raises(ValueError, match="must be a dict"):
                ChatChoice.from_payload(bad)

    def test_missing_index_defaults_to_zero(self) -> None:
        choice = ChatChoice.from_payload({"message": {"role": _VALID_ROLE}})
        assert choice.index == 0

    def test_missing_message_raises(self) -> None:
        # message is required -> the inner ChatResponseMessage.from_payload(None)
        # raises on the missing role field.
        with pytest.raises(ValueError):
            ChatChoice.from_payload({"index": 0})

    def test_finish_reason_absent_is_none(self) -> None:
        choice = ChatChoice.from_payload({"message": {"role": _VALID_ROLE}})
        assert choice.finish_reason is None

    def test_finish_reason_null_is_none(self) -> None:
        choice = ChatChoice.from_payload(
            {"message": {"role": _VALID_ROLE}, "finish_reason": None}
        )
        assert choice.finish_reason is None

    def test_finish_reason_non_string_is_none(self) -> None:
        # A non-string finish_reason stays None (no enum lookup attempted).
        choice = ChatChoice.from_payload(
            {"message": {"role": _VALID_ROLE}, "finish_reason": 123}
        )
        assert choice.finish_reason is None


# ---------------------------------------------------------------------------
# ChatCompletionResponse: the non-streaming reply envelope.
# ---------------------------------------------------------------------------


class TestChatCompletionResponse:
    """The envelope metadata defaults tolerantly; ``choices`` tolerates
    absence; ``usage`` parses only when the wire value is a dict."""

    def test_full_payload_round_trip(self) -> None:
        resp = ChatCompletionResponse.from_payload(
            {
                "id": "chatcmpl-1",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "grok-1",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": _VALID_ROLE, "content": "Hi"},
                        "finish_reason": _VALID_FINISH,
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
                "citations": ["src_a"],
            }
        )
        assert resp.id == "chatcmpl-1"
        assert resp.object == "chat.completion"
        assert resp.created == 1700000000
        assert resp.model == "grok-1"
        assert len(resp.choices) == 1
        assert resp.choices[0].message.content == "Hi"
        assert isinstance(resp.usage, ChatUsage)
        assert resp.citations == ("src_a",)

    def test_non_dict_payload_raises(self) -> None:
        for bad in [None, 42, "x", []]:
            with pytest.raises(ValueError, match="must be a dict"):
                ChatCompletionResponse.from_payload(bad)

    def test_envelope_metadata_defaults_when_absent(self) -> None:
        resp = ChatCompletionResponse.from_payload({})
        assert resp.id == ""
        assert resp.object == ""
        assert resp.created == 0
        assert resp.model == ""
        assert resp.choices == ()
        assert resp.usage is None
        assert resp.citations is None

    def test_choices_null_tolerates_to_empty_tuple(self) -> None:
        resp = ChatCompletionResponse.from_payload({"choices": None})
        assert resp.choices == ()

    def test_choices_non_list_tolerates_to_empty_tuple(self) -> None:
        resp = ChatCompletionResponse.from_payload({"choices": "nope"})
        assert resp.choices == ()

    def test_choices_non_dict_items_skipped(self) -> None:
        resp = ChatCompletionResponse.from_payload(
            {
                "choices": [
                    None,
                    {"index": 0, "message": {"role": _VALID_ROLE}},
                    42,
                ]
            }
        )
        assert len(resp.choices) == 1

    def test_usage_non_dict_is_none(self) -> None:
        resp = ChatCompletionResponse.from_payload({"usage": 123})
        assert resp.usage is None

    def test_usage_absent_is_none(self) -> None:
        resp = ChatCompletionResponse.from_payload({})
        assert resp.usage is None


# ---------------------------------------------------------------------------
# ChatCompletionChunk: the streaming reply envelope (keystone: the R211 hook).
# ---------------------------------------------------------------------------


class TestChatCompletionChunk:
    """The streaming envelope carries the R208 :class:`ChatChunkChoice`
    interior; ``system_fingerprint`` normalizes through the R211
    :func:`empty_string_as_none` hook (an empty wire string -> ``None``)."""

    def test_full_payload_round_trip(self) -> None:
        chunk = ChatCompletionChunk.from_payload(
            {
                "id": "chatcmpl-1",
                "object": "chat.completion.chunk",
                "created": 1700000000,
                "model": "grok-1",
                "choices": [{"index": 0, "delta": {"role": _VALID_ROLE}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
                "system_fingerprint": "fp_abc",
            }
        )
        assert chunk.id == "chatcmpl-1"
        assert chunk.object == "chat.completion.chunk"
        assert chunk.created == 1700000000
        assert chunk.model == "grok-1"
        assert len(chunk.choices) == 1
        assert isinstance(chunk.choices[0], ChatChunkChoice)
        assert isinstance(chunk.usage, ChatUsage)
        assert chunk.system_fingerprint == "fp_abc"

    def test_non_dict_payload_raises(self) -> None:
        for bad in [None, 42, "x", []]:
            with pytest.raises(ValueError, match="must be a dict"):
                ChatCompletionChunk.from_payload(bad)

    def test_envelope_metadata_defaults_when_absent(self) -> None:
        chunk = ChatCompletionChunk.from_payload({})
        assert chunk.id == ""
        assert chunk.object == ""
        assert chunk.created == 0
        assert chunk.model == ""
        assert chunk.choices == ()
        assert chunk.usage is None
        assert chunk.system_fingerprint is None

    # --- Keystone: the R211 empty_string_as_none hook. ---------------------

    def test_system_fingerprint_empty_string_normalizes_to_none(self) -> None:
        # The single behavior that made R208 defer this envelope: an empty
        # wire string means "absent" -> None.
        chunk = ChatCompletionChunk.from_payload({"system_fingerprint": ""})
        assert chunk.system_fingerprint is None

    def test_system_fingerprint_non_empty_preserved(self) -> None:
        chunk = ChatCompletionChunk.from_payload({"system_fingerprint": "fp_123"})
        assert chunk.system_fingerprint == "fp_123"

    def test_system_fingerprint_missing_is_none(self) -> None:
        chunk = ChatCompletionChunk.from_payload({})
        assert chunk.system_fingerprint is None

    def test_system_fingerprint_null_is_none(self) -> None:
        chunk = ChatCompletionChunk.from_payload({"system_fingerprint": None})
        assert chunk.system_fingerprint is None

    def test_choices_non_dict_items_skipped(self) -> None:
        chunk = ChatCompletionChunk.from_payload(
            {"choices": [None, {"index": 0, "delta": {}}, 42]}
        )
        assert len(chunk.choices) == 1
        assert isinstance(chunk.choices[0], ChatChunkChoice)

    def test_usage_non_dict_is_none(self) -> None:
        chunk = ChatCompletionChunk.from_payload({"usage": "nope"})
        assert chunk.usage is None


# ---------------------------------------------------------------------------
# Full nested parse chain: response -> choice -> message -> tool_call.
# ---------------------------------------------------------------------------


class TestNestedParseChain:
    """The 5 envelopes compose: a full ChatCompletion response descends
    through choice -> message -> tool_call -> function in one ``from_payload``
    call, each layer dispatching to the next."""

    def test_response_descends_to_function_layer(self) -> None:
        resp = ChatCompletionResponse.from_payload(
            {
                "id": "chatcmpl-x",
                "object": "chat.completion",
                "created": 1700000000,
                "model": "grok-1",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": _VALID_ROLE,
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "get_weather",
                                        "arguments": '{"city":"SF"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": _VALID_FINISH,
                    }
                ],
            }
        )
        assert isinstance(resp, ChatCompletionResponse)
        assert isinstance(resp.choices[0], ChatChoice)
        assert isinstance(resp.choices[0].message, ChatResponseMessage)
        assert isinstance(resp.choices[0].message.tool_calls[0], ToolCallResponse)
        # Deepest layer: the resolved tool function name + arguments.
        tool_call = resp.choices[0].message.tool_calls[0]
        assert tool_call.function.name == "get_weather"
        assert tool_call.function.arguments == '{"city":"SF"}'

    def test_malformed_tool_call_does_not_abort_response_parse(self) -> None:
        # ToolCallResponse uses the tolerant posture -- a non-dict tool call
        # entry is skipped at the list level; a dict tool call with a missing
        # function falls back to the empty ToolCallFunction (never raises up
        # to abort the whole response).
        resp = ChatCompletionResponse.from_payload(
            {
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": _VALID_ROLE,
                            "tool_calls": [
                                {"id": "c1", "type": "function"},  # no function
                            ],
                        },
                    }
                ]
            }
        )
        tool_call = resp.choices[0].message.tool_calls[0]
        assert tool_call.id == "c1"
        assert tool_call.function.name == ""  # tolerant fallback, not a raise


# ---------------------------------------------------------------------------
# frozen + slots dataclass semantics.
# ---------------------------------------------------------------------------


class TestFrozenSlotsSemantics:
    """All 5 envelopes are ``@dataclass(frozen=True, slots=True)``: mutation
    raises :class:`FrozenInstanceError`, the instances carry ``__slots__``,
    and they are hashable (frozen enables ``__hash__``)."""

    @pytest.mark.parametrize(
        ("factory", "expected_slots"),
        [
            (lambda: ToolCallResponse(), ("id", "kind", "function")),
            (
                lambda: ChatResponseMessage.from_payload({"role": _VALID_ROLE}),
                (
                    "role",
                    "content",
                    "reasoning_content",
                    "tool_calls",
                    "tool_call_id",
                    "citations",
                ),
            ),
            (
                lambda: ChatChoice.from_payload(
                    {"index": 0, "message": {"role": _VALID_ROLE}}
                ),
                ("index", "message", "finish_reason"),
            ),
            (lambda: ChatCompletionResponse(), ("id", "object", "created", "model", "choices", "usage", "citations")),
            (lambda: ChatCompletionChunk(), ("id", "object", "created", "model", "choices", "usage", "system_fingerprint")),
        ],
    )
    def test_mutation_raises_frozen_instance_error(
        self, factory, expected_slots: tuple[str, ...]
    ) -> None:
        obj = factory()
        assert type(obj).__slots__ == expected_slots
        # Mutating the first real slot field must raise (variable field name,
        # not a constant -- avoids the B010 setattr-constant-attribute rule).
        field_name = next(iter(expected_slots))
        with pytest.raises(FrozenInstanceError):
            setattr(obj, field_name, "mutated")

    @pytest.mark.parametrize(
        "factory",
        [
            lambda: ToolCallResponse(),
            lambda: ChatResponseMessage.from_payload({"role": _VALID_ROLE}),
            lambda: ChatChoice.from_payload(
                {"index": 0, "message": {"role": _VALID_ROLE}}
            ),
            lambda: ChatCompletionResponse(),
            lambda: ChatCompletionChunk(),
        ],
    )
    def test_instance_has_no_dict(self, factory) -> None:
        # slots=True -> instances carry no per-instance __dict__.
        obj = factory()
        assert not hasattr(obj, "__dict__")

    def test_frozen_instances_are_hashable(self) -> None:
        # frozen=True restores __hash__ -- envelopes can be set/dict keys.
        assert hash(ToolCallResponse(id="x")) == hash(ToolCallResponse(id="x"))
        assert ToolCallResponse(id="x") == ToolCallResponse(id="x")
