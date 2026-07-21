"""Tests for ``sampler.conversation_tool_defs`` (R222,
``xai-grok-sampling-types`` ``conversation.rs``).

Covers the conversation-layer tool-definition-and-call pair -- the first
**plain struct** shape to land in the ``conversation.rs`` migration (the prior
five slices were enums or free functions). Two flat
``#[derive(Serialize, Deserialize)]`` structs with no ``#[serde(tag)]``
discriminator:

- :class:`ToolCall` -- an assistant-emitted tool invocation the client
  executes locally (``id`` + ``name`` + JSON-encoded ``arguments``).
- :class:`ToolSpec` -- a tool/function definition offered to the model
  (``name`` + optional ``description`` + JSON-Schema ``parameters``).

Strict-required parse discipline (the R222 milestone): unlike the R206
ChatCompletion flat structs (:class:`ImageUrl` / :class:`ToolCallFunction`)
which tolerate missing fields with defaults because they parse *responses*,
these are conversation-persistence structs that cross the wire in both
directions. Their grok fields carry no ``#[serde(default)]``, so a missing
required field is a serde failure (a corrupt session record).
:meth:`from_payload` mirrors that -- a missing or wrong-typed required field
raises ``ValueError``; the optional ``description`` -> ``None`` when absent;
extra keys tolerated. Same strict-no-catch-all philosophy as the R221
:class:`ContentPart` / R220 :class:`ConversationToolChoice` enums, applied to a
flat struct.

Distinct from the wire-layer :class:`ToolCallFunction` (R206, ChatCompletion
``function`` payload -- ``name`` + ``arguments`` only, no ``id``, tolerant
parse); this is the conversation-persistence peer carrying the binding ``id``.
No barrel collision -> no ``Conversation`` prefix.
"""

from __future__ import annotations

import pytest

import minimax_code.sampler.conversation_tool_defs as _td
from minimax_code.sampler import ToolCall, ToolSpec


class TestBarrelReExport:
    """The sampler barrel re-exports every conversation_tool_defs symbol by
    identity (no accidental shadowing / re-wrapping at the package surface)."""

    def test_barrel_symbols_are_direct_module_references(self) -> None:
        assert ToolCall is _td.ToolCall
        assert ToolSpec is _td.ToolSpec


# ---------------------------------------------------------------------------
# ToolCall: as_payload serialization (flat struct, no tag).
# ---------------------------------------------------------------------------


class TestToolCallAsPayload:
    """``#[derive(Serialize, Deserialize)]`` with no tag -> a flat ``{"id": ...,
    "name": ..., "arguments": ...}`` object."""

    def test_serializes_to_id_name_arguments(self) -> None:
        call = ToolCall(id="call_1", name="read_file", arguments='{"path":"a"}')
        assert call.as_payload() == {
            "id": "call_1",
            "name": "read_file",
            "arguments": '{"path":"a"}',
        }

    def test_preserves_arbitrary_arguments_string(self) -> None:
        # arguments is an opaque JSON string at this layer -- partial fragments
        # during streaming and complete objects when non-streaming both survive
        # verbatim, as does the empty string.
        for args in ("", "{", '{"a":1}', '{"nested":{"k":[1,2]}}'):
            call = ToolCall(id="x", name="fn", arguments=args)
            assert call.as_payload()["arguments"] == args

    def test_preserves_arbitrary_id_and_name(self) -> None:
        call = ToolCall(id="", name="", arguments="")
        assert call.as_payload() == {"id": "", "name": "", "arguments": ""}


# ---------------------------------------------------------------------------
# ToolCall: from_payload strict parse (no default -> serde failure parity).
# ---------------------------------------------------------------------------


class TestToolCallFromPayload:
    """The strict inverse of :meth:`as_payload`: a dict with string ``id`` /
    ``name`` / ``arguments`` -> the struct; anything else -> ``ValueError``
    (parity with the R221 strict :class:`ContentPart`; contrast the R206
    tolerant :class:`ToolCallFunction`)."""

    def test_dict_returns_tool_call(self) -> None:
        result = ToolCall.from_payload(
            {"id": "call_1", "name": "read_file", "arguments": "{}"}
        )
        assert result == ToolCall(id="call_1", name="read_file", arguments="{}")

    def test_extra_keys_tolerated(self) -> None:
        # serde's default ignores unknown fields on a struct -- the wire may
        # carry forward-compat keys the conversation layer does not model yet.
        result = ToolCall.from_payload(
            {"id": "x", "name": "fn", "arguments": "{}", "extra": 1}
        )
        assert result == ToolCall(id="x", name="fn", arguments="{}")

    def test_non_dict_raises(self) -> None:
        for raw in (None, 42, 3.14, True, "x", ["x"], ("x",)):
            with pytest.raises(ValueError):
                ToolCall.from_payload(raw)

    def test_missing_id_raises(self) -> None:
        with pytest.raises(ValueError):
            ToolCall.from_payload({"name": "fn", "arguments": "{}"})

    def test_missing_name_raises(self) -> None:
        with pytest.raises(ValueError):
            ToolCall.from_payload({"id": "x", "arguments": "{}"})

    def test_missing_arguments_raises(self) -> None:
        with pytest.raises(ValueError):
            ToolCall.from_payload({"id": "x", "name": "fn"})

    def test_non_string_id_raises(self) -> None:
        for bad in (None, 42, True, ["x"], {"k": 1}):
            with pytest.raises(ValueError):
                ToolCall.from_payload({"id": bad, "name": "fn", "arguments": "{}"})

    def test_non_string_name_raises(self) -> None:
        for bad in (None, 42, True, ["x"], {"k": 1}):
            with pytest.raises(ValueError):
                ToolCall.from_payload({"id": "x", "name": bad, "arguments": "{}"})

    def test_non_string_arguments_raises(self) -> None:
        for bad in (None, 42, True, ["x"], {"k": 1}):
            with pytest.raises(ValueError):
                ToolCall.from_payload({"id": "x", "name": "fn", "arguments": bad})

    def test_empty_dict_raises(self) -> None:
        with pytest.raises(ValueError):
            ToolCall.from_payload({})


# ---------------------------------------------------------------------------
# ToolCall: round-trip.
# ---------------------------------------------------------------------------


class TestToolCallRoundTrip:
    """Every ToolCall round-trips through its wire shape in both directions."""

    def test_variant_round_trips_through_payload(self) -> None:
        for call in (
            ToolCall(id="call_1", name="read_file", arguments='{"path":"a"}'),
            ToolCall(id="", name="", arguments=""),
            ToolCall(id="abc", name="x", arguments="{"),
        ):
            again = ToolCall.from_payload(call.as_payload())
            assert again == call

    def test_wire_round_trips_through_variant(self) -> None:
        for raw in (
            {"id": "x", "name": "fn", "arguments": "{}"},
            {"id": "y", "name": "g", "arguments": '{"a":1}'},
        ):
            assert ToolCall.from_payload(raw).as_payload() == raw

    def test_extra_keys_dropped_on_round_trip(self) -> None:
        canonical = ToolCall.from_payload(
            {"id": "x", "name": "fn", "arguments": "{}", "extra": 1}
        ).as_payload()
        assert canonical == {"id": "x", "name": "fn", "arguments": "{}"}


# ---------------------------------------------------------------------------
# ToolSpec: as_payload serialization (skip_serializing_if on description).
# ---------------------------------------------------------------------------


class TestToolSpecAsPayload:
    """``name`` + ``parameters`` always emitted; ``description`` only when not
    ``None`` (``skip_serializing_if = "Option::is_none"``)."""

    def test_with_description_emits_all_three(self) -> None:
        spec = ToolSpec(
            name="read_file",
            parameters={"type": "object"},
            description="Read a file",
        )
        assert spec.as_payload() == {
            "name": "read_file",
            "parameters": {"type": "object"},
            "description": "Read a file",
        }

    def test_without_description_omits_it(self) -> None:
        spec = ToolSpec(name="read_file", parameters={"type": "object"})
        assert spec.as_payload() == {
            "name": "read_file",
            "parameters": {"type": "object"},
        }

    def test_explicit_none_description_omitted(self) -> None:
        spec = ToolSpec(name="x", parameters={}, description=None)
        assert "description" not in spec.as_payload()

    def test_parameters_preserved_verbatim(self) -> None:
        # parameters is opaque JSON (serde_json::Value) -- any JSON value
        # survives as-is, not just a schema dict.
        for params in (
            {"type": "object", "properties": {}},
            [],
            "plain",
            42,
            True,
            None,
            {"nested": {"k": [1, {"x": 2}]}},
        ):
            assert ToolSpec(name="x", parameters=params).as_payload()["parameters"] == params


# ---------------------------------------------------------------------------
# ToolSpec: from_payload strict-required + tolerant-optional parse.
# ---------------------------------------------------------------------------


class TestToolSpecFromPayload:
    """``name`` (string) + ``parameters`` (any JSON, key required) are strict;
    ``description`` is tolerant (absent/null -> ``None``, present must be
    string)."""

    def test_dict_with_description_returns_spec(self) -> None:
        result = ToolSpec.from_payload(
            {"name": "read_file", "parameters": {"type": "object"}, "description": "d"}
        )
        assert result == ToolSpec(
            name="read_file", parameters={"type": "object"}, description="d"
        )

    def test_dict_without_description_defaults_none(self) -> None:
        result = ToolSpec.from_payload({"name": "read_file", "parameters": {}})
        assert result == ToolSpec(name="read_file", parameters={}, description=None)

    def test_null_description_accepted_as_none(self) -> None:
        result = ToolSpec.from_payload(
            {"name": "x", "parameters": {}, "description": None}
        )
        assert result.description is None

    def test_parameters_null_preserved(self) -> None:
        # serde_json::Value accepts null; the key is present so it is legal.
        result = ToolSpec.from_payload({"name": "x", "parameters": None})
        assert result.parameters is None

    def test_parameters_any_json_value(self) -> None:
        for params in ({}, [], "s", 42, True, None, {"k": [1, 2]}):
            result = ToolSpec.from_payload({"name": "x", "parameters": params})
            assert result.parameters == params

    def test_extra_keys_tolerated(self) -> None:
        result = ToolSpec.from_payload(
            {"name": "x", "parameters": {}, "strict": True}
        )
        assert result == ToolSpec(name="x", parameters={}, description=None)

    def test_non_dict_raises(self) -> None:
        for raw in (None, 42, 3.14, True, "x", ["x"], ("x",)):
            with pytest.raises(ValueError):
                ToolSpec.from_payload(raw)

    def test_missing_name_raises(self) -> None:
        with pytest.raises(ValueError):
            ToolSpec.from_payload({"parameters": {}})

    def test_non_string_name_raises(self) -> None:
        for bad in (None, 42, True, ["x"], {"k": 1}):
            with pytest.raises(ValueError):
                ToolSpec.from_payload({"name": bad, "parameters": {}})

    def test_missing_parameters_raises(self) -> None:
        with pytest.raises(ValueError):
            ToolSpec.from_payload({"name": "x"})

    def test_non_string_description_raises(self) -> None:
        for bad in (42, True, ["x"], {"k": 1}):
            with pytest.raises(ValueError):
                ToolSpec.from_payload(
                    {"name": "x", "parameters": {}, "description": bad}
                )

    def test_empty_dict_raises(self) -> None:
        with pytest.raises(ValueError):
            ToolSpec.from_payload({})


# ---------------------------------------------------------------------------
# ToolSpec: round-trip.
# ---------------------------------------------------------------------------


class TestToolSpecRoundTrip:
    """Every ToolSpec round-trips through its wire shape; the ``description``
    field collapses to the canonical (omitted-when-None) shape."""

    def test_with_description_round_trips(self) -> None:
        spec = ToolSpec(
            name="read_file", parameters={"type": "object"}, description="d"
        )
        assert ToolSpec.from_payload(spec.as_payload()) == spec

    def test_without_description_round_trips(self) -> None:
        spec = ToolSpec(name="read_file", parameters={"type": "object"})
        assert ToolSpec.from_payload(spec.as_payload()) == spec

    def test_wire_with_description_round_trips(self) -> None:
        raw = {"name": "x", "parameters": {"type": "object"}, "description": "d"}
        assert ToolSpec.from_payload(raw).as_payload() == raw

    def test_wire_without_description_round_trips(self) -> None:
        raw = {"name": "x", "parameters": {"type": "object"}}
        assert ToolSpec.from_payload(raw).as_payload() == raw

    def test_explicit_null_description_collapses_to_omitted(self) -> None:
        # A wire ``"description": null`` is parsed as None, then re-emitted
        # without the key -- the round-trip collapses to the canonical shape.
        canonical = ToolSpec.from_payload(
            {"name": "x", "parameters": {}, "description": None}
        ).as_payload()
        assert canonical == {"name": "x", "parameters": {}}

    def test_extra_keys_dropped_on_round_trip(self) -> None:
        canonical = ToolSpec.from_payload(
            {"name": "x", "parameters": {}, "extra": 1}
        ).as_payload()
        assert canonical == {"name": "x", "parameters": {}}


# ---------------------------------------------------------------------------
# Value semantics: frozen+slots -> structural equality.
# ---------------------------------------------------------------------------


class TestValueSemantics:
    """frozen dataclass -> structural equality (not identity); same fields ->
    equal; any differing field -> not equal."""

    def test_equal_tool_calls_equal(self) -> None:
        assert ToolCall(id="x", name="fn", arguments="{}") == ToolCall(
            id="x", name="fn", arguments="{}"
        )

    def test_differing_id_not_equal(self) -> None:
        assert ToolCall(id="x", name="fn", arguments="{}") != ToolCall(
            id="y", name="fn", arguments="{}"
        )

    def test_differing_name_not_equal(self) -> None:
        assert ToolCall(id="x", name="fn", arguments="{}") != ToolCall(
            id="x", name="g", arguments="{}"
        )

    def test_differing_arguments_not_equal(self) -> None:
        assert ToolCall(id="x", name="fn", arguments="{}") != ToolCall(
            id="x", name="fn", arguments='{"a":1}'
        )

    def test_equal_tool_specs_equal(self) -> None:
        assert ToolSpec(name="x", parameters={}, description="d") == ToolSpec(
            name="x", parameters={}, description="d"
        )

    def test_tool_spec_description_none_vs_string_not_equal(self) -> None:
        assert ToolSpec(name="x", parameters={}) != ToolSpec(
            name="x", parameters={}, description="d"
        )

    def test_tool_spec_differing_parameters_not_equal(self) -> None:
        assert ToolSpec(name="x", parameters={"a": 1}) != ToolSpec(
            name="x", parameters={"b": 2}
        )

    def test_tool_call_not_equal_to_tool_spec(self) -> None:
        # Different types are never equal.
        assert ToolCall(id="x", name="y", arguments="z") != ToolSpec(
            name="y", parameters="z"
        )


class TestSlotsAndImmutability:
    """frozen+slots -> no ``__dict__``; the structs carry exactly their fields
    and are immutable after construction."""

    def test_tool_call_slots(self) -> None:
        assert set(ToolCall.__slots__) == {"id", "name", "arguments"}

    def test_tool_spec_slots(self) -> None:
        assert set(ToolSpec.__slots__) == {"name", "parameters", "description"}

    def test_instances_have_no_dict(self) -> None:
        call = ToolCall(id="x", name="fn", arguments="{}")
        spec = ToolSpec(name="x", parameters={})
        assert not hasattr(call, "__dict__")
        assert not hasattr(spec, "__dict__")

    def test_tool_call_field_values_round_trip(self) -> None:
        call = ToolCall(id="call_1", name="read_file", arguments='{"path":"a"}')
        assert call.id == "call_1"
        assert call.name == "read_file"
        assert call.arguments == '{"path":"a"}'

    def test_tool_spec_field_values_round_trip(self) -> None:
        spec = ToolSpec(name="read_file", parameters={"type": "object"}, description="d")
        assert spec.name == "read_file"
        assert spec.parameters == {"type": "object"}
        assert spec.description == "d"

    def test_tool_spec_default_description_is_none(self) -> None:
        assert ToolSpec(name="x", parameters={}).description is None

    def test_tool_call_is_frozen(self) -> None:
        # Variable attribute name keeps the B010 check honest across renames;
        # frozen rejects the assignment regardless of which field.
        call = ToolCall(id="x", name="fn", arguments="{}")
        attr = "name"
        with pytest.raises(AttributeError):
            setattr(call, attr, "y")

    def test_tool_spec_is_frozen(self) -> None:
        spec = ToolSpec(name="x", parameters={})
        attr = "parameters"
        with pytest.raises(AttributeError):
            setattr(spec, attr, {"new": True})
