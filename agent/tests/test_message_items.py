"""Tests for ``sampler.conversation_message_items`` (R224,
``xai-grok-sampling-types`` ``conversation.rs``).

Covers the conversation-layer **message item** structs -- the "Message Items"
block: one row of a conversation turn. Four plain
``#[derive(Serialize, Deserialize)]`` structs (no ``#[serde(tag)]``
discriminator -> flat wire objects), the struct members of the (still
un-migrated) :class:`ConversationItem` tagged union:

- :class:`SystemItem` -- a system-prompt row (``content`` body, required).
- :class:`UserItem` -- a user-turn row (a ``Vec<ContentPart>`` body + the two
  R218 catch-all reason enums + an optional ``prompt_index``; carries
  ``#[derive(Default)]``).
- :class:`AssistantItem` -- an assistant-turn row (``content`` body +
  ``Vec<ToolCall>`` + ``model_id`` + ``model_fingerprint`` (the
  ``system_fingerprint`` alias + the :func:`empty_string_as_none` hook) +
  ``reasoning_effort``).
- :class:`ToolResultItem` -- a tool-result row (``tool_call_id`` + ``content``
  + ``Vec<ContentPart>`` images).

This is the second **plain struct** slice in ``conversation.rs`` (after the
R222 :class:`ToolCall` / :class:`ToolSpec` pair) and the first to **compose
multiple already-migrated sampler leaves** into one struct (ContentPart /
ToolCall / SyntheticReason / PriorTurnInterrupt / ReasoningEffort +
empty_string_as_none) -- so the strict-required + tolerant-optional R222
discipline is exercised here against composed-leaf field types, plus the
alias + deserialize_with fingerprint path that has no prior-plain-struct
precedent in the package.

Three R224-specific behaviors get dedicated coverage:

1. **UserItem ``content`` double semantics** -- grok's ``content`` has no
   field-level ``#[serde(default)]`` (serde-strict), but the struct carries
   ``#[derive(Default)]`` (programmatic ``UserItem::default()`` ->
   ``content: vec![]`` is legal). R224 honors the programmatic surface:
   ``content`` defaults to ``()`` and :meth:`from_payload` tolerates a missing
   / null ``content`` -> ``()`` (deliberate deviation from grok's serde
   failure -- recorded in the field docstring).
2. **AssistantItem ``model_fingerprint`` alias + hook** -- ``from_payload``
   checks ``model_fingerprint`` first, falls back to the ``system_fingerprint``
   serde alias, then runs :func:`empty_string_as_none` (``""`` -> ``None``).
   A dedicated :class:`TestModelFingerprint` class covers the matrix.
3. **``reasoning_effort`` strict parse** -- unlike the catch-all
   :class:`SyntheticReason` / :class:`PriorTurnInterrupt` (never raise),
   :class:`ReasoningEffort` has no catch-all, so an unknown effort ->
   ``ValueError`` (parity with the R221 strict :class:`ContentPart` /
   R222 strict :class:`ToolCall`).

No barrel collision -> no ``Conversation`` prefix.
"""

from __future__ import annotations

import pytest

import minimax_code.sampler.conversation_message_items as _mi
from minimax_code.sampler import (
    AssistantItem,
    PriorTurnInterrupt,
    ReasoningEffort,
    SyntheticReason,
    SystemItem,
    TextPart,
    ToolCall,
    ToolResultItem,
    UserItem,
)


class TestBarrelReExport:
    """The sampler barrel re-exports every conversation_message_items symbol by
    identity (no accidental shadowing / re-wrapping at the package surface)."""

    def test_barrel_symbols_are_direct_module_references(self) -> None:
        assert SystemItem is _mi.SystemItem
        assert UserItem is _mi.UserItem
        assert AssistantItem is _mi.AssistantItem
        assert ToolResultItem is _mi.ToolResultItem


# ===========================================================================
# SystemItem: the simplest row -- one required ``content`` string.
# ===========================================================================


class TestSystemItemAsPayload:
    """``#[derive(Serialize, Deserialize)]`` with no tag -> a flat
    ``{"content": ...}`` object."""

    def test_serializes_to_content(self) -> None:
        assert SystemItem(content="be helpful").as_payload() == {"content": "be helpful"}

    def test_empty_content_preserved(self) -> None:
        assert SystemItem(content="").as_payload() == {"content": ""}


class TestSystemItemFromPayload:
    """Strict inverse of :meth:`as_payload` (no ``#[serde(default)]`` on the
    lone field -> serde failure parity, the R222 discipline)."""

    def test_dict_returns_system_item(self) -> None:
        result = SystemItem.from_payload({"content": "be helpful"})
        assert result == SystemItem(content="be helpful")

    def test_extra_keys_tolerated(self) -> None:
        result = SystemItem.from_payload({"content": "x", "role": "system"})
        assert result == SystemItem(content="x")

    def test_non_dict_raises(self) -> None:
        for raw in (None, 42, 3.14, True, "x", ["x"], ("x",)):
            with pytest.raises(ValueError):
                SystemItem.from_payload(raw)

    def test_missing_content_raises(self) -> None:
        with pytest.raises(ValueError):
            SystemItem.from_payload({})

    def test_non_string_content_raises(self) -> None:
        for bad in (None, 42, True, ["x"], {"k": 1}):
            with pytest.raises(ValueError):
                SystemItem.from_payload({"content": bad})


class TestSystemItemRoundTrip:
    """Every SystemItem round-trips through its wire shape in both directions."""

    def test_variant_round_trips_through_payload(self) -> None:
        for item in (SystemItem(content="be helpful"), SystemItem(content="")):
            assert SystemItem.from_payload(item.as_payload()) == item

    def test_wire_round_trips_through_variant(self) -> None:
        for raw in ({"content": "x"}, {"content": ""}):
            assert SystemItem.from_payload(raw).as_payload() == raw


# ===========================================================================
# UserItem: ``#[derive(Default)]`` -- tolerant content + tolerant options.
# ===========================================================================


class TestUserItemDefaults:
    """``#[derive(Default)]`` -> all fields collapse to their default (the
    programmatic ``UserItem::default()`` surface)."""

    def test_default_construction(self) -> None:
        item = UserItem()
        assert item.content == ()
        assert item.synthetic_reason is None
        assert item.prior_turn_interrupt is None
        assert item.prompt_index is None


class TestUserItemAsPayload:
    """``content`` is always emitted (even when empty -- no
    ``skip_serializing_if`` on the body); the three options only when not
    ``None``."""

    def test_default_emits_only_content(self) -> None:
        # content is the body -- always emitted, even when empty.
        assert UserItem().as_payload() == {"content": []}

    def test_content_parts_serialized(self) -> None:
        item = UserItem(content=(TextPart(text="hi"),))
        assert item.as_payload() == {"content": [{"type": "text", "text": "hi"}]}

    def test_synthetic_reason_emitted_when_set(self) -> None:
        item = UserItem(synthetic_reason=SyntheticReason.TASK_COMPLETED)
        payload = item.as_payload()
        assert payload["synthetic_reason"] == "task_completed"

    def test_prior_turn_interrupt_emitted_when_set(self) -> None:
        item = UserItem(prior_turn_interrupt=PriorTurnInterrupt.MID_TURN_ABORT)
        payload = item.as_payload()
        assert payload["prior_turn_interrupt"] == "mid_turn_abort"

    def test_prompt_index_emitted_when_set(self) -> None:
        item = UserItem(prompt_index=3)
        assert item.as_payload()["prompt_index"] == 3

    def test_all_fields_emitted(self) -> None:
        item = UserItem(
            content=(TextPart(text="hi"),),
            synthetic_reason=SyntheticReason.AUTO_CONTINUE,
            prior_turn_interrupt=PriorTurnInterrupt.PERMISSION_REJECTED,
            prompt_index=0,
        )
        assert item.as_payload() == {
            "content": [{"type": "text", "text": "hi"}],
            "synthetic_reason": "auto_continue",
            "prior_turn_interrupt": "permission_rejected",
            "prompt_index": 0,
        }


class TestUserItemFromPayload:
    """Tolerant ``content`` (missing / null -> ``()``, the ``#[derive(Default)]``
    surface) + tolerant options (absent / null -> ``None`` / catch-all enum)."""

    def test_dict_with_content_returns_user_item(self) -> None:
        result = UserItem.from_payload({"content": [{"type": "text", "text": "hi"}]})
        assert result == UserItem(content=(TextPart(text="hi"),))

    def test_missing_content_defaults_empty_tuple(self) -> None:
        # The #[derive(Default)] programmatic surface -- grok serde fails on a
        # missing content, but R224 honors the derive-Default surface instead.
        result = UserItem.from_payload({})
        assert result.content == ()

    def test_null_content_defaults_empty_tuple(self) -> None:
        result = UserItem.from_payload({"content": None})
        assert result.content == ()

    def test_non_list_content_raises(self) -> None:
        for bad in ("x", 42, True, {"k": 1}):
            with pytest.raises(ValueError):
                UserItem.from_payload({"content": bad})

    def test_synthetic_reason_absent_defaults_none(self) -> None:
        assert UserItem.from_payload({}).synthetic_reason is None

    def test_synthetic_reason_known_string(self) -> None:
        result = UserItem.from_payload({"synthetic_reason": "task_completed"})
        assert result.synthetic_reason is SyntheticReason.TASK_COMPLETED

    def test_synthetic_reason_unknown_string_is_unknown(self) -> None:
        # Catch-all enum never raises -- forward-compat for newer sessions.
        result = UserItem.from_payload({"synthetic_reason": "bogus"})
        assert result.synthetic_reason is SyntheticReason.UNKNOWN

    def test_synthetic_reason_non_string_is_unknown(self) -> None:
        result = UserItem.from_payload({"synthetic_reason": 42})
        assert result.synthetic_reason is SyntheticReason.UNKNOWN

    def test_prior_turn_interrupt_known_string(self) -> None:
        result = UserItem.from_payload({"prior_turn_interrupt": "mid_turn_abort"})
        assert result.prior_turn_interrupt is PriorTurnInterrupt.MID_TURN_ABORT

    def test_prior_turn_interrupt_unknown_string_is_unknown(self) -> None:
        result = UserItem.from_payload({"prior_turn_interrupt": "bogus"})
        assert result.prior_turn_interrupt is PriorTurnInterrupt.UNKNOWN

    def test_prompt_index_absent_defaults_none(self) -> None:
        assert UserItem.from_payload({}).prompt_index is None

    def test_prompt_index_null_defaults_none(self) -> None:
        assert UserItem.from_payload({"prompt_index": None}).prompt_index is None

    def test_prompt_index_int_preserved(self) -> None:
        assert UserItem.from_payload({"prompt_index": 7}).prompt_index == 7

    def test_prompt_index_zero_preserved(self) -> None:
        assert UserItem.from_payload({"prompt_index": 0}).prompt_index == 0

    def test_prompt_index_bool_rejected(self) -> None:
        # bool is an int subclass in Python but not a valid usize on the wire.
        for bad in (True, False):
            with pytest.raises(ValueError):
                UserItem.from_payload({"prompt_index": bad})

    def test_prompt_index_non_int_rejected(self) -> None:
        for bad in ("x", 3.14, ["x"], {"k": 1}):
            with pytest.raises(ValueError):
                UserItem.from_payload({"prompt_index": bad})

    def test_extra_keys_tolerated(self) -> None:
        result = UserItem.from_payload({"content": [], "role": "user"})
        assert result == UserItem()

    def test_non_dict_raises(self) -> None:
        for raw in (None, 42, 3.14, True, "x", ["x"], ("x",)):
            with pytest.raises(ValueError):
                UserItem.from_payload(raw)


class TestUserItemRoundTrip:
    """Every UserItem round-trips; a missing ``content`` collapses to the
    canonical empty-list shape on the wire."""

    def test_default_round_trips(self) -> None:
        # The body is always emitted, so {} -> UserItem() -> {"content": []}.
        assert UserItem.from_payload(UserItem().as_payload()) == UserItem()

    def test_full_item_round_trips(self) -> None:
        item = UserItem(
            content=(TextPart(text="hi"),),
            synthetic_reason=SyntheticReason.AUTO_CONTINUE,
            prior_turn_interrupt=PriorTurnInterrupt.MID_TURN_ABORT,
            prompt_index=2,
        )
        assert UserItem.from_payload(item.as_payload()) == item

    def test_missing_content_collapses_to_empty_list(self) -> None:
        # {} -> UserItem() -> {"content": []} (the canonical round-trip shape).
        canonical = UserItem.from_payload({}).as_payload()
        assert canonical == {"content": []}


# ===========================================================================
# AssistantItem: strict content + tolerant options + the fingerprint alias.
# ===========================================================================


class TestAssistantItemAsPayload:
    """``content`` always emitted; ``tool_calls`` only when non-empty
    (``skip_serializing_if = "Vec::is_empty"``); the three options only when
    not ``None``."""

    def test_bare_content_only(self) -> None:
        assert AssistantItem(content="hello").as_payload() == {"content": "hello"}

    def test_tool_calls_emitted_when_non_empty(self) -> None:
        call = ToolCall(id="call_1", name="read_file", arguments="{}")
        item = AssistantItem(content="hi", tool_calls=(call,))
        payload = item.as_payload()
        assert payload["tool_calls"] == [
            {"id": "call_1", "name": "read_file", "arguments": "{}"}
        ]

    def test_empty_tool_calls_omitted(self) -> None:
        payload = AssistantItem(content="hi").as_payload()
        assert "tool_calls" not in payload

    def test_model_id_emitted_when_set(self) -> None:
        assert AssistantItem(content="hi", model_id="grok-1").as_payload()[
            "model_id"
        ] == "grok-1"

    def test_model_fingerprint_emitted_when_set(self) -> None:
        assert AssistantItem(content="hi", model_fingerprint="fp").as_payload()[
            "model_fingerprint"
        ] == "fp"

    def test_reasoning_effort_emitted_as_wire_string(self) -> None:
        payload = AssistantItem(
            content="hi", reasoning_effort=ReasoningEffort.HIGH
        ).as_payload()
        assert payload["reasoning_effort"] == "high"

    def test_all_fields_emitted(self) -> None:
        call = ToolCall(id="c1", name="fn", arguments="{}")
        item = AssistantItem(
            content="hi",
            tool_calls=(call,),
            model_id="grok-1",
            model_fingerprint="fp",
            reasoning_effort=ReasoningEffort.MEDIUM,
        )
        assert item.as_payload() == {
            "content": "hi",
            "tool_calls": [{"id": "c1", "name": "fn", "arguments": "{}"}],
            "model_id": "grok-1",
            "model_fingerprint": "fp",
            "reasoning_effort": "medium",
        }


class TestAssistantItemFromPayload:
    """``content`` strict; ``tool_calls`` / ``model_id`` tolerant;
    ``reasoning_effort`` strict (no catch-all). The ``model_fingerprint`` alias
    + hook is covered by :class:`TestModelFingerprint` below."""

    def test_dict_with_content_returns_assistant_item(self) -> None:
        result = AssistantItem.from_payload({"content": "hello"})
        assert result == AssistantItem(content="hello")

    def test_tool_calls_parsed(self) -> None:
        result = AssistantItem.from_payload(
            {
                "content": "hi",
                "tool_calls": [{"id": "c1", "name": "fn", "arguments": "{}"}],
            }
        )
        assert result.tool_calls == (ToolCall(id="c1", name="fn", arguments="{}"),)

    def test_tool_calls_absent_defaults_empty(self) -> None:
        assert AssistantItem.from_payload({"content": "hi"}).tool_calls == ()

    def test_tool_calls_null_defaults_empty(self) -> None:
        assert AssistantItem.from_payload(
            {"content": "hi", "tool_calls": None}
        ).tool_calls == ()

    def test_tool_calls_non_list_raises(self) -> None:
        for bad in ("x", 42, True, {"k": 1}):
            with pytest.raises(ValueError):
                AssistantItem.from_payload({"content": "hi", "tool_calls": bad})

    def test_model_id_absent_defaults_none(self) -> None:
        assert AssistantItem.from_payload({"content": "hi"}).model_id is None

    def test_model_id_null_defaults_none(self) -> None:
        assert AssistantItem.from_payload(
            {"content": "hi", "model_id": None}
        ).model_id is None

    def test_model_id_non_string_raises(self) -> None:
        for bad in (42, True, ["x"], {"k": 1}):
            with pytest.raises(ValueError):
                AssistantItem.from_payload({"content": "hi", "model_id": bad})

    def test_reasoning_effort_known_string(self) -> None:
        result = AssistantItem.from_payload(
            {"content": "hi", "reasoning_effort": "high"}
        )
        assert result.reasoning_effort is ReasoningEffort.HIGH

    def test_reasoning_effort_absent_defaults_none(self) -> None:
        assert AssistantItem.from_payload({"content": "hi"}).reasoning_effort is None

    def test_reasoning_effort_null_defaults_none(self) -> None:
        assert AssistantItem.from_payload(
            {"content": "hi", "reasoning_effort": None}
        ).reasoning_effort is None

    def test_reasoning_effort_unknown_string_raises(self) -> None:
        # No catch-all -> strict parity (contrast the UserItem catch-all enums).
        with pytest.raises(ValueError):
            AssistantItem.from_payload(
                {"content": "hi", "reasoning_effort": "bogus"}
            )

    def test_reasoning_effort_non_string_raises(self) -> None:
        with pytest.raises(ValueError):
            AssistantItem.from_payload({"content": "hi", "reasoning_effort": 42})

    def test_non_dict_raises(self) -> None:
        for raw in (None, 42, 3.14, True, "x", ["x"], ("x",)):
            with pytest.raises(ValueError):
                AssistantItem.from_payload(raw)

    def test_missing_content_raises(self) -> None:
        with pytest.raises(ValueError):
            AssistantItem.from_payload({})

    def test_non_string_content_raises(self) -> None:
        for bad in (None, 42, True, ["x"], {"k": 1}):
            with pytest.raises(ValueError):
                AssistantItem.from_payload({"content": bad})

    def test_extra_keys_tolerated(self) -> None:
        result = AssistantItem.from_payload({"content": "hi", "role": "assistant"})
        assert result == AssistantItem(content="hi")


class TestModelFingerprint:
    """``model_fingerprint`` resolves the ``system_fingerprint`` serde alias +
    the :func:`empty_string_as_none` hook (``""`` -> ``None``). This is the
    R224-specific path with no prior plain-struct precedent in the package."""

    def test_absent_defaults_none(self) -> None:
        assert AssistantItem.from_payload({"content": "hi"}).model_fingerprint is None

    def test_model_fingerprint_present(self) -> None:
        result = AssistantItem.from_payload(
            {"content": "hi", "model_fingerprint": "fp"}
        )
        assert result.model_fingerprint == "fp"

    def test_model_fingerprint_null_defaults_none(self) -> None:
        result = AssistantItem.from_payload(
            {"content": "hi", "model_fingerprint": None}
        )
        assert result.model_fingerprint is None

    def test_model_fingerprint_empty_string_becomes_none(self) -> None:
        # empty_string_as_none: "" -> None (mirrors the grok deserialize_with hook).
        result = AssistantItem.from_payload(
            {"content": "hi", "model_fingerprint": ""}
        )
        assert result.model_fingerprint is None

    def test_model_fingerprint_non_string_raises(self) -> None:
        for bad in (42, True, ["x"], {"k": 1}):
            with pytest.raises(ValueError):
                AssistantItem.from_payload(
                    {"content": "hi", "model_fingerprint": bad}
                )

    def test_system_fingerprint_alias_resolves(self) -> None:
        # The serde alias = "system_fingerprint" -- a payload with only the
        # alias key populates the field.
        result = AssistantItem.from_payload(
            {"content": "hi", "system_fingerprint": "fp"}
        )
        assert result.model_fingerprint == "fp"

    def test_system_fingerprint_empty_string_becomes_none(self) -> None:
        result = AssistantItem.from_payload(
            {"content": "hi", "system_fingerprint": ""}
        )
        assert result.model_fingerprint is None

    def test_system_fingerprint_null_defaults_none(self) -> None:
        result = AssistantItem.from_payload(
            {"content": "hi", "system_fingerprint": None}
        )
        assert result.model_fingerprint is None

    def test_system_fingerprint_non_string_raises(self) -> None:
        for bad in (42, True, ["x"], {"k": 1}):
            with pytest.raises(ValueError):
                AssistantItem.from_payload(
                    {"content": "hi", "system_fingerprint": bad}
                )

    def test_model_fingerprint_wins_over_alias(self) -> None:
        # When both keys are present, the primary model_fingerprint key wins
        # (mirrors serde checking the primary field first under the alias).
        result = AssistantItem.from_payload(
            {
                "content": "hi",
                "model_fingerprint": "primary",
                "system_fingerprint": "alias",
            }
        )
        assert result.model_fingerprint == "primary"

    def test_empty_primary_collapses_to_none_even_when_alias_present(self) -> None:
        # The primary key is checked first; "" there runs empty_string_as_none
        # -> None, regardless of the alias value (serde alias only fires when
        # the primary key is absent).
        result = AssistantItem.from_payload(
            {"content": "hi", "model_fingerprint": "", "system_fingerprint": "alias"}
        )
        assert result.model_fingerprint is None


class TestAssistantItemRoundTrip:
    """Every AssistantItem round-trips; the empty-``model_fingerprint`` and
    empty-``tool_calls`` fields collapse to their canonical omitted shape."""

    def test_full_item_round_trips(self) -> None:
        call = ToolCall(id="c1", name="fn", arguments="{}")
        item = AssistantItem(
            content="hi",
            tool_calls=(call,),
            model_id="grok-1",
            model_fingerprint="fp",
            reasoning_effort=ReasoningEffort.HIGH,
        )
        assert AssistantItem.from_payload(item.as_payload()) == item

    def test_bare_content_round_trips(self) -> None:
        item = AssistantItem(content="hi")
        assert AssistantItem.from_payload(item.as_payload()) == item

    def test_empty_fingerprint_collapses_to_omitted(self) -> None:
        # A wire ``"model_fingerprint": ""`` parses as None, then re-emits
        # without the key -- the round-trip collapses to the canonical shape.
        canonical = AssistantItem.from_payload(
            {"content": "hi", "model_fingerprint": ""}
        ).as_payload()
        assert canonical == {"content": "hi"}


# ===========================================================================
# ToolResultItem: strict id + content, tolerant images.
# ===========================================================================


class TestToolResultItemAsPayload:
    """``tool_call_id`` + ``content`` always emitted; ``images`` only when
    non-empty (``skip_serializing_if = "Vec::is_empty"``)."""

    def test_bare_id_and_content(self) -> None:
        item = ToolResultItem(tool_call_id="c1", content="ok")
        assert item.as_payload() == {"tool_call_id": "c1", "content": "ok"}

    def test_images_emitted_when_non_empty(self) -> None:
        item = ToolResultItem(
            tool_call_id="c1",
            content="ok",
            images=(TextPart(text="caption"),),
        )
        payload = item.as_payload()
        assert payload["images"] == [{"type": "text", "text": "caption"}]

    def test_empty_images_omitted(self) -> None:
        payload = ToolResultItem(tool_call_id="c1", content="ok").as_payload()
        assert "images" not in payload


class TestToolResultItemFromPayload:
    """``tool_call_id`` + ``content`` strict; ``images`` tolerant (absent / null
    -> ``()``)."""

    def test_dict_returns_tool_result_item(self) -> None:
        result = ToolResultItem.from_payload({"tool_call_id": "c1", "content": "ok"})
        assert result == ToolResultItem(tool_call_id="c1", content="ok")

    def test_images_parsed(self) -> None:
        result = ToolResultItem.from_payload(
            {
                "tool_call_id": "c1",
                "content": "ok",
                "images": [{"type": "text", "text": "caption"}],
            }
        )
        assert result.images == (TextPart(text="caption"),)

    def test_images_absent_defaults_empty(self) -> None:
        assert (
            ToolResultItem.from_payload(
                {"tool_call_id": "c1", "content": "ok"}
            ).images
            == ()
        )

    def test_images_null_defaults_empty(self) -> None:
        assert (
            ToolResultItem.from_payload(
                {"tool_call_id": "c1", "content": "ok", "images": None}
            ).images
            == ()
        )

    def test_images_non_list_raises(self) -> None:
        for bad in ("x", 42, True, {"k": 1}):
            with pytest.raises(ValueError):
                ToolResultItem.from_payload(
                    {"tool_call_id": "c1", "content": "ok", "images": bad}
                )

    def test_missing_tool_call_id_raises(self) -> None:
        with pytest.raises(ValueError):
            ToolResultItem.from_payload({"content": "ok"})

    def test_non_string_tool_call_id_raises(self) -> None:
        for bad in (None, 42, True, ["x"], {"k": 1}):
            with pytest.raises(ValueError):
                ToolResultItem.from_payload({"tool_call_id": bad, "content": "ok"})

    def test_missing_content_raises(self) -> None:
        with pytest.raises(ValueError):
            ToolResultItem.from_payload({"tool_call_id": "c1"})

    def test_non_string_content_raises(self) -> None:
        for bad in (None, 42, True, ["x"], {"k": 1}):
            with pytest.raises(ValueError):
                ToolResultItem.from_payload(
                    {"tool_call_id": "c1", "content": bad}
                )

    def test_extra_keys_tolerated(self) -> None:
        result = ToolResultItem.from_payload(
            {"tool_call_id": "c1", "content": "ok", "role": "tool"}
        )
        assert result == ToolResultItem(tool_call_id="c1", content="ok")

    def test_non_dict_raises(self) -> None:
        for raw in (None, 42, 3.14, True, "x", ["x"], ("x",)):
            with pytest.raises(ValueError):
                ToolResultItem.from_payload(raw)

    def test_empty_dict_raises(self) -> None:
        with pytest.raises(ValueError):
            ToolResultItem.from_payload({})


class TestToolResultItemRoundTrip:
    """Every ToolResultItem round-trips; empty ``images`` collapse to omitted."""

    def test_bare_item_round_trips(self) -> None:
        item = ToolResultItem(tool_call_id="c1", content="ok")
        assert ToolResultItem.from_payload(item.as_payload()) == item

    def test_item_with_images_round_trips(self) -> None:
        item = ToolResultItem(
            tool_call_id="c1",
            content="ok",
            images=(TextPart(text="caption"),),
        )
        assert ToolResultItem.from_payload(item.as_payload()) == item


# ===========================================================================
# Value semantics: frozen+slots -> structural equality.
# ===========================================================================


class TestValueSemantics:
    """frozen dataclass -> structural equality (not identity); same fields ->
    equal; any differing field -> not equal; different struct types never
    equal."""

    def test_equal_system_items_equal(self) -> None:
        assert SystemItem(content="x") == SystemItem(content="x")

    def test_differing_system_content_not_equal(self) -> None:
        assert SystemItem(content="x") != SystemItem(content="y")

    def test_equal_user_items_equal(self) -> None:
        assert UserItem(content=(TextPart(text="hi"),)) == UserItem(
            content=(TextPart(text="hi"),)
        )

    def test_differing_user_content_not_equal(self) -> None:
        assert UserItem(content=(TextPart(text="hi"),)) != UserItem(
            content=(TextPart(text="bye"),)
        )

    def test_differing_user_synthetic_reason_not_equal(self) -> None:
        a = UserItem(synthetic_reason=SyntheticReason.TASK_COMPLETED)
        b = UserItem(synthetic_reason=SyntheticReason.AUTO_CONTINUE)
        assert a != b

    def test_user_synthetic_reason_none_vs_set_not_equal(self) -> None:
        assert UserItem() != UserItem(synthetic_reason=SyntheticReason.INTERJECTION)

    def test_differing_user_prompt_index_not_equal(self) -> None:
        assert UserItem(prompt_index=1) != UserItem(prompt_index=2)

    def test_equal_assistant_items_equal(self) -> None:
        assert AssistantItem(content="hi") == AssistantItem(content="hi")

    def test_differing_assistant_content_not_equal(self) -> None:
        assert AssistantItem(content="hi") != AssistantItem(content="bye")

    def test_differing_assistant_tool_calls_not_equal(self) -> None:
        call = ToolCall(id="c1", name="fn", arguments="{}")
        assert AssistantItem(content="hi") != AssistantItem(
            content="hi", tool_calls=(call,)
        )

    def test_equal_tool_result_items_equal(self) -> None:
        assert ToolResultItem(tool_call_id="c1", content="ok") == ToolResultItem(
            tool_call_id="c1", content="ok"
        )

    def test_differing_tool_result_id_not_equal(self) -> None:
        assert ToolResultItem(tool_call_id="c1", content="ok") != ToolResultItem(
            tool_call_id="c2", content="ok"
        )

    def test_different_struct_types_not_equal(self) -> None:
        # The four structs are distinct types -- never equal across types even
        # if a field happens to coincide.
        assert SystemItem(content="x") != AssistantItem(content="x")
        assert UserItem() != ToolResultItem(tool_call_id="", content="")


# ===========================================================================
# Slots + immutability: frozen+slots -> no __dict__; exact field slots; frozen
# rejects setattr on any field.
# ===========================================================================


class TestSlotsAndImmutability:
    """frozen+slots -> no ``__dict__``; each struct carries exactly its fields
    and is immutable after construction."""

    def test_system_item_slots(self) -> None:
        assert set(SystemItem.__slots__) == {"content"}

    def test_user_item_slots(self) -> None:
        assert set(UserItem.__slots__) == {
            "content",
            "synthetic_reason",
            "prior_turn_interrupt",
            "prompt_index",
        }

    def test_assistant_item_slots(self) -> None:
        assert set(AssistantItem.__slots__) == {
            "content",
            "tool_calls",
            "model_id",
            "model_fingerprint",
            "reasoning_effort",
        }

    def test_tool_result_item_slots(self) -> None:
        assert set(ToolResultItem.__slots__) == {"tool_call_id", "content", "images"}

    def test_instances_have_no_dict(self) -> None:
        for item in (
            SystemItem(content="x"),
            UserItem(),
            AssistantItem(content="x"),
            ToolResultItem(tool_call_id="c", content="x"),
        ):
            assert not hasattr(item, "__dict__")

    def test_field_values_round_trip(self) -> None:
        call = ToolCall(id="c1", name="fn", arguments="{}")
        assistant = AssistantItem(
            content="hi",
            tool_calls=(call,),
            model_id="grok-1",
            model_fingerprint="fp",
            reasoning_effort=ReasoningEffort.HIGH,
        )
        assert assistant.content == "hi"
        assert assistant.tool_calls == (call,)
        assert assistant.model_id == "grok-1"
        assert assistant.model_fingerprint == "fp"
        assert assistant.reasoning_effort is ReasoningEffort.HIGH

    def test_system_item_is_frozen(self) -> None:
        # Variable attribute name keeps the B010 check honest across renames;
        # frozen rejects the assignment regardless of which field.
        item = SystemItem(content="x")
        attr = "content"
        with pytest.raises(AttributeError):
            setattr(item, attr, "y")

    def test_user_item_is_frozen(self) -> None:
        item = UserItem(prompt_index=1)
        attr = "prompt_index"
        with pytest.raises(AttributeError):
            setattr(item, attr, 2)

    def test_assistant_item_is_frozen(self) -> None:
        item = AssistantItem(content="hi", model_fingerprint="fp")
        attr = "model_fingerprint"
        with pytest.raises(AttributeError):
            setattr(item, attr, "other")

    def test_tool_result_item_is_frozen(self) -> None:
        item = ToolResultItem(tool_call_id="c1", content="ok")
        attr = "content"
        with pytest.raises(AttributeError):
            setattr(item, attr, "changed")
