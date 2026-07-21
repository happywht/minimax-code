"""Tests for ``sampler.conversation_tool_choice`` (R220,
``xai-grok-sampling-types`` ``conversation.rs``).

Covers the conversation-layer tool-choice tagged union -- the sampler
package's first **externally-tagged mixed enum** (the third serde
representation shape, after R84's ``untagged`` :class:`JsonRpcId` and R89's
``internally-tagged`` :class:`HookEvent`). ``#[serde(rename_all =
"snake_case")]`` + serde's default external tagging means:

- the three field-less variants (:class:`ConversationAuto` /
  :class:`ConversationNone` / :class:`ConversationRequired`) serialize as
  bare snake_case strings;
- the ``Function(String)`` newtype variant (:class:`ConversationFunction`)
  serializes as the single-key object ``{"function": name}``.

This is the standard OpenAI ``tool_choice`` wire shape, hoisted into the
conversation layer. Distinct from the wire-layer :class:`ToolChoice` family
(types.rs): the uniform ``Conversation`` variant prefix dodges the barrel
collision (esp. :class:`FunctionToolChoice`), mirroring the
:class:`ConversationStopReason` rename (R219).
"""

from __future__ import annotations

import pytest

import minimax_code.sampler.conversation_tool_choice as _ctc
from minimax_code.sampler import (
    ConversationAuto,
    ConversationFunction,
    ConversationNone,
    ConversationRequired,
    ConversationToolChoice,
)


class TestBarrelReExport:
    """The sampler barrel re-exports every conversation_tool_choice symbol by
    identity (no accidental shadowing / re-wrapping at the package surface)."""

    def test_barrel_symbols_are_direct_module_references(self) -> None:
        assert ConversationToolChoice is _ctc.ConversationToolChoice
        assert ConversationAuto is _ctc.ConversationAuto
        assert ConversationNone is _ctc.ConversationNone
        assert ConversationRequired is _ctc.ConversationRequired
        assert ConversationFunction is _ctc.ConversationFunction


# ---------------------------------------------------------------------------
# as_payload: externally-tagged serialization (variant -> wire shape).
# ---------------------------------------------------------------------------


class TestAsPayloadSerializesToExternallyTaggedWire:
    """``#[serde(rename_all="snake_case")]`` + default external tagging: the
    field-less variants -> their bare snake_case string; the
    ``Function(String)`` newtype variant -> ``{"function": name}``."""

    def test_auto_serializes_to_bare_string(self) -> None:
        assert ConversationAuto().as_payload() == "auto"

    def test_none_serializes_to_bare_string(self) -> None:
        assert ConversationNone().as_payload() == "none"

    def test_required_serializes_to_bare_string(self) -> None:
        assert ConversationRequired().as_payload() == "required"

    def test_function_serializes_to_single_key_object(self) -> None:
        assert ConversationFunction(name="get_weather").as_payload() == {
            "function": "get_weather"
        }

    def test_function_preserves_arbitrary_name(self) -> None:
        # The function name is opaque to this layer -- any string survives.
        assert ConversationFunction(name="my-tool.v2").as_payload() == {
            "function": "my-tool.v2"
        }


# ---------------------------------------------------------------------------
# from_payload: externally-tagged deserialization (wire shape -> variant).
# ---------------------------------------------------------------------------


class TestFromPayloadParsesExternallyTaggedWire:
    """The strict (no catch-all) inverse of :meth:`as_payload`: a bare
    snake_case string -> the matching field-less variant; a single-key
    ``{"function": <name>}`` object -> :class:`ConversationFunction`;
    anything else -> ``ValueError`` (parity with the R219 strict
    :class:`ConversationStopReason`; contrast the R218 ``UNKNOWN`` catch-all
    enums which never raise)."""

    def test_bare_string_returns_unit_variant(self) -> None:
        assert isinstance(
            ConversationToolChoice.from_payload("auto"), ConversationAuto
        )
        assert isinstance(
            ConversationToolChoice.from_payload("none"), ConversationNone
        )
        assert isinstance(
            ConversationToolChoice.from_payload("required"), ConversationRequired
        )

    def test_function_object_returns_function_variant(self) -> None:
        result = ConversationToolChoice.from_payload({"function": "search"})
        assert isinstance(result, ConversationFunction)
        assert result.name == "search"

    def test_unknown_string_raises(self) -> None:
        # No #[serde(other)] catch-all -- an unknown tag fails (the caller
        # decides forward-compat), parity with the strict enums.
        with pytest.raises(ValueError):
            ConversationToolChoice.from_payload("specific")
        with pytest.raises(ValueError):
            ConversationToolChoice.from_payload("")

    def test_non_string_non_dict_raises(self) -> None:
        # serde would fail on the type mismatch; the parser surfaces it.
        for raw in (None, 42, 3.14, True, ["auto"], ("auto",)):
            with pytest.raises(ValueError):
                ConversationToolChoice.from_payload(raw)

    def test_malformed_dict_raises(self) -> None:
        # Wrong key, extra key, or empty dict -- none match the single-key
        # {"function": <name>} newtype shape.
        with pytest.raises(ValueError):
            ConversationToolChoice.from_payload({"tool": "search"})
        with pytest.raises(ValueError):
            ConversationToolChoice.from_payload({"function": "x", "extra": 1})
        with pytest.raises(ValueError):
            ConversationToolChoice.from_payload({})

    def test_non_string_function_value_raises(self) -> None:
        # grok ``Function(String)`` rejects a non-string payload.
        for bad in (None, 42, True, ["x"], {"nested": 1}):
            with pytest.raises(ValueError):
                ConversationToolChoice.from_payload({"function": bad})


# ---------------------------------------------------------------------------
# Round-trip: from_payload(as_payload(x)) == x and the wire inverse.
# ---------------------------------------------------------------------------


class TestRoundTrip:
    """Every variant round-trips through its wire shape in both directions."""

    def test_variant_round_trips_through_payload(self) -> None:
        # from_payload(as_payload(x)) == x for every variant.
        for choice in (
            ConversationAuto(),
            ConversationNone(),
            ConversationRequired(),
            ConversationFunction(name="run_tests"),
        ):
            again = ConversationToolChoice.from_payload(choice.as_payload())
            assert again == choice

    def test_wire_round_trips_through_variant(self) -> None:
        # as_payload(from_payload(raw)) == raw for every well-formed payload.
        for raw in ("auto", "none", "required", {"function": "calc"}):
            assert ConversationToolChoice.from_payload(raw).as_payload() == raw


# ---------------------------------------------------------------------------
# Value semantics: frozen+slots union -- structural equality.
# ---------------------------------------------------------------------------


class TestValueSemantics:
    """frozen dataclass -> structural equality (not identity); same variant +
    same field -> equal; different variant or field -> not equal."""

    def test_same_unit_variants_equal(self) -> None:
        assert ConversationAuto() == ConversationAuto()
        assert ConversationNone() == ConversationNone()
        assert ConversationRequired() == ConversationRequired()

    def test_different_unit_variants_not_equal(self) -> None:
        assert ConversationAuto() != ConversationNone()
        assert ConversationAuto() != ConversationRequired()
        assert ConversationNone() != ConversationRequired()

    def test_function_equality_is_structural(self) -> None:
        assert ConversationFunction(name="a") == ConversationFunction(name="a")
        assert ConversationFunction(name="a") != ConversationFunction(name="b")

    def test_function_not_equal_to_unit_variants(self) -> None:
        assert ConversationFunction(name="auto") != ConversationAuto()
        assert ConversationAuto() != ConversationFunction(name="auto")

    def test_all_variants_are_conversation_tool_choice(self) -> None:
        # The union base: every variant isinstance-checks against it.
        for choice in (
            ConversationAuto(),
            ConversationNone(),
            ConversationRequired(),
            ConversationFunction(name="x"),
        ):
            assert isinstance(choice, ConversationToolChoice)


class TestSlotsAndImmutability:
    """frozen+slots -> the variants carry no ``__dict__`` and are immutable
    after construction; the union base is field-less, the unit variants are
    field-less, and :class:`ConversationFunction` carries exactly ``name``."""

    def test_base_is_fieldless(self) -> None:
        assert set(ConversationToolChoice.__slots__) == set()

    def test_unit_variants_are_fieldless(self) -> None:
        assert set(ConversationAuto.__slots__) == set()
        assert set(ConversationNone.__slots__) == set()
        assert set(ConversationRequired.__slots__) == set()

    def test_function_has_single_name_slot(self) -> None:
        assert set(ConversationFunction.__slots__) == {"name"}

    def test_instances_have_no_dict(self) -> None:
        # slots -> no __dict__ on the instance (no ad-hoc attribute stash).
        for choice in (
            ConversationAuto(),
            ConversationNone(),
            ConversationRequired(),
            ConversationFunction(name="x"),
        ):
            assert not hasattr(choice, "__dict__")

    def test_function_field_value_round_trips(self) -> None:
        # The one data-carrying variant: name is readable + structural.
        assert ConversationFunction(name="hello").name == "hello"

    def test_function_is_frozen(self) -> None:
        # Variable attribute name keeps the B010 check honest if the field
        # ever renames; frozen rejects the assignment regardless.
        choice = ConversationFunction(name="x")
        attr = "name"
        with pytest.raises(AttributeError):
            setattr(choice, attr, "y")

    def test_unit_variant_is_frozen(self) -> None:
        # A field-less frozen+slots subclass also rejects setattr -- the
        # assignment fails either way (immutability holds), but the exact
        # exception type is a Python-version edge: a slot-bearing variant
        # raises FrozenInstanceError (an AttributeError); a slot-less one
        # trips the dataclass-generated __setattr__'s super() path and raises
        # TypeError. Either outcome proves the variant is immutable.
        attr = "anything"
        with pytest.raises((AttributeError, TypeError)):
            setattr(ConversationAuto(), attr, 1)
