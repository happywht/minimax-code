"""Tests for sampler.reasoning_effort_meta (R213, ``xai-grok-sampling-types``
``types.rs`` 765-1008).

Covers the ``types.rs`` reasoning-effort meta read/write subsystem -- 3 wire
constants + the canonical wire-token parser + singular readers/writer + the
:class:`ReasoningEffortOption` menu-entry struct + the plural readers/writer
with the untagged Bare-string-vs-Full-table option shape + skip-invalid
forward-compat. Consumes the R206 :class:`ReasoningEffort` wire-string enum.

The keystone invariant under test is the **dual-parse-path asymmetry**:
grok's ``FromStr`` (used by the Bare-string option path + the singular meta
reader, via :func:`parse_canonical_effort_token`) accepts the ``"max"``
CLI/UX alias of ``Xhigh``, while serde ``Deserialize`` (used by the
Full-table ``value`` field, via :meth:`ReasoningEffort.from_payload`) is
strict and rejects ``"max"``. A Bare ``"max"`` round-trips to an option;
a Full table ``{"value": "max"}`` is skipped with a warn.

Other invariants:

- The capability bool defaults to ``False`` on absent / non-bool.
- The singular meta reader returns ``None`` on type-mismatch / unknown
  variant (never overwriting the user's pref with a bad persisted value).
- The plural meta reader collapses "absent" / "not-an-array" /
  "all-skipped" to ``None`` (one fallback path in every consumer).
- ``Vec`` -> :class:`tuple` (project convention for parsed wire sequences).
- ``description: None`` serializes to ``"description": null`` (serde's
  default for ``Option`` without ``skip_serializing_if``).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.sampler import (
    REASONING_EFFORT_META_KEY,
    REASONING_EFFORTS_META_KEY,
    SUPPORTS_REASONING_EFFORT_META_KEY,
    ReasoningEffortOption,
    parse_canonical_effort_token,
    parse_reasoning_effort_meta,
    parse_reasoning_effort_options,
    parse_reasoning_efforts_meta,
    reasoning_effort_meta_value,
    reasoning_efforts_meta_value,
    supports_reasoning_effort_meta,
)
from minimax_code.sampler.chat_completion_leaves import ReasoningEffort

# ---------------------------------------------------------------------------
# Module + package barrel surface.
# ---------------------------------------------------------------------------


def test_module_barrel_exposes_eleven_symbols() -> None:
    """11 re-exported symbols (3 constants + 1 struct + 7 free functions)."""
    import minimax_code.sampler.reasoning_effort_meta as rem

    assert rem.__all__ == [
        "REASONING_EFFORT_META_KEY",
        "REASONING_EFFORTS_META_KEY",
        "SUPPORTS_REASONING_EFFORT_META_KEY",
        "ReasoningEffortOption",
        "parse_canonical_effort_token",
        "parse_reasoning_effort_meta",
        "parse_reasoning_effort_options",
        "parse_reasoning_efforts_meta",
        "reasoning_effort_meta_value",
        "reasoning_efforts_meta_value",
        "supports_reasoning_effort_meta",
    ]


def test_package_barrel_re_exports_all_eleven_symbols() -> None:
    """The package barrel flattens all 11 reasoning_effort_meta symbols."""
    import minimax_code.sampler as sampler

    for sym in (
        "REASONING_EFFORT_META_KEY",
        "REASONING_EFFORTS_META_KEY",
        "SUPPORTS_REASONING_EFFORT_META_KEY",
        "ReasoningEffortOption",
        "parse_canonical_effort_token",
        "parse_reasoning_effort_meta",
        "parse_reasoning_effort_options",
        "parse_reasoning_efforts_meta",
        "reasoning_effort_meta_value",
        "reasoning_efforts_meta_value",
        "supports_reasoning_effort_meta",
    ):
        assert sym in sampler.__all__


# ---------------------------------------------------------------------------
# Wire constants.
# ---------------------------------------------------------------------------


def test_wire_constants_match_grok_keys() -> None:
    """The 3 wire constants carry the exact ACP ``meta`` dict key strings."""
    assert REASONING_EFFORT_META_KEY == "reasoningEffort"
    assert SUPPORTS_REASONING_EFFORT_META_KEY == "supportsReasoningEffort"
    assert REASONING_EFFORTS_META_KEY == "reasoningEfforts"


# ---------------------------------------------------------------------------
# parse_canonical_effort_token: the FromStr mirror (accepts "max" alias).
# ---------------------------------------------------------------------------


def test_canonical_token_parses_all_six_variants() -> None:
    """Each lowercase wire token maps to its :class:`ReasoningEffort` variant."""
    assert parse_canonical_effort_token("none") is ReasoningEffort.NONE
    assert parse_canonical_effort_token("minimal") is ReasoningEffort.MINIMAL
    assert parse_canonical_effort_token("low") is ReasoningEffort.LOW
    assert parse_canonical_effort_token("medium") is ReasoningEffort.MEDIUM
    assert parse_canonical_effort_token("high") is ReasoningEffort.HIGH
    assert parse_canonical_effort_token("xhigh") is ReasoningEffort.XHIGH


def test_canonical_token_accepts_max_alias() -> None:
    """``"max"`` is a CLI/UX alias of :attr:`ReasoningEffort.XHIGH` (grok
    ``FromStr`` accepts it, even though serde Deserialize does not)."""
    assert parse_canonical_effort_token("max") is ReasoningEffort.XHIGH


def test_canonical_token_is_case_insensitive() -> None:
    """grok lowercases before matching -- uppercase / mixed-case tokens parse."""
    assert parse_canonical_effort_token("HIGH") is ReasoningEffort.HIGH
    assert parse_canonical_effort_token("Medium") is ReasoningEffort.MEDIUM
    assert parse_canonical_effort_token("MAX") is ReasoningEffort.XHIGH


def test_canonical_token_returns_none_on_unknown() -> None:
    """An unknown token -> ``None`` (grok ``token.parse().ok()``)."""
    assert parse_canonical_effort_token("ultra") is None
    assert parse_canonical_effort_token("") is None


# ---------------------------------------------------------------------------
# supports_reasoning_effort_meta: capability bool (default False).
# ---------------------------------------------------------------------------


def test_supports_returns_true_when_flag_true() -> None:
    meta = {SUPPORTS_REASONING_EFFORT_META_KEY: True}
    assert supports_reasoning_effort_meta(meta) is True


def test_supports_returns_false_when_flag_false() -> None:
    meta = {SUPPORTS_REASONING_EFFORT_META_KEY: False}
    assert supports_reasoning_effort_meta(meta) is False


def test_supports_defaults_false_when_meta_none() -> None:
    assert supports_reasoning_effort_meta(None) is False


def test_supports_defaults_false_when_key_absent() -> None:
    """An absent key -> ``False`` (the model does not declare the capability)."""
    assert supports_reasoning_effort_meta({"other": True}) is False


def test_supports_defaults_false_on_non_bool_value() -> None:
    """A truthy-but-not-bool value (``1`` / ``"true"``) -> ``False`` -- grok
    requires a JSON bool, not a truthy coerced value."""
    assert supports_reasoning_effort_meta(
        {SUPPORTS_REASONING_EFFORT_META_KEY: 1}
    ) is False
    assert supports_reasoning_effort_meta(
        {SUPPORTS_REASONING_EFFORT_META_KEY: "true"}
    ) is False


# ---------------------------------------------------------------------------
# parse_reasoning_effort_meta: singular persisted-effort reader.
# ---------------------------------------------------------------------------


def test_parse_singular_reads_valid_effort() -> None:
    meta = {REASONING_EFFORT_META_KEY: "high"}
    assert parse_reasoning_effort_meta(meta) is ReasoningEffort.HIGH


def test_parse_singular_accepts_max_alias() -> None:
    """The singular reader routes through ``parse_canonical_effort_token``
    (``FromStr``), so ``"max"`` -> :attr:`ReasoningEffort.XHIGH`."""
    meta = {REASONING_EFFORT_META_KEY: "max"}
    assert parse_reasoning_effort_meta(meta) is ReasoningEffort.XHIGH


def test_parse_singular_returns_none_when_meta_none() -> None:
    assert parse_reasoning_effort_meta(None) is None


def test_parse_singular_returns_none_when_key_absent() -> None:
    assert parse_reasoning_effort_meta({"other": "high"}) is None


def test_parse_singular_returns_none_on_non_string() -> None:
    """A non-string persisted value -> ``None`` (warn logged); a bad value
    never overwrites the user's pref on the next save."""
    assert parse_reasoning_effort_meta({REASONING_EFFORT_META_KEY: 5}) is None
    assert parse_reasoning_effort_meta({REASONING_EFFORT_META_KEY: None}) is None


def test_parse_singular_returns_none_on_unknown_token() -> None:
    """An unknown string variant -> ``None`` (warn logged)."""
    assert (
        parse_reasoning_effort_meta({REASONING_EFFORT_META_KEY: "ultra"})
        is None
    )


# ---------------------------------------------------------------------------
# reasoning_effort_meta_value: singular writer (Value::String(as_str)).
# ---------------------------------------------------------------------------


def test_meta_value_serializes_to_wire_string() -> None:
    """Serialize each effort to its lowercase wire token (grok
    ``Value::String(effort.as_str())``; StrEnum ``__str__``)."""
    assert reasoning_effort_meta_value(ReasoningEffort.HIGH) == "high"
    assert reasoning_effort_meta_value(ReasoningEffort.XHIGH) == "xhigh"
    assert reasoning_effort_meta_value(ReasoningEffort.NONE) == "none"


# ---------------------------------------------------------------------------
# ReasoningEffortOption: struct shape + value semantics + to_payload.
# ---------------------------------------------------------------------------


def test_option_is_frozen() -> None:
    """``@dataclass(frozen=True)`` -> mutation raises (mirrors grok's
    immutable struct)."""
    opt = ReasoningEffortOption(
        id="high",
        value=ReasoningEffort.HIGH,
        label="High",
        description=None,
        default=False,
    )
    field = next(iter(type(opt).__slots__))
    with pytest.raises(FrozenInstanceError):
        setattr(opt, field, "mutated")


def test_option_declares_slots() -> None:
    """``slots=True`` -> the class declares ``__slots__`` over its 5 fields (no
    per-instance ``__dict__``)."""
    assert ReasoningEffortOption.__slots__ == (
        "id",
        "value",
        "label",
        "description",
        "default",
    )
    opt = ReasoningEffortOption(
        id="x",
        value=ReasoningEffort.LOW,
        label="X",
        description=None,
        default=False,
    )
    assert not hasattr(opt, "__dict__")


def test_option_to_payload_emits_all_five_fields_in_struct_order() -> None:
    """``#[derive(Serialize)]`` emits all 5 fields in struct order; the
    ``value`` field emits its lowercase wire string."""
    opt = ReasoningEffortOption(
        id="high",
        value=ReasoningEffort.HIGH,
        label="High",
        description="Best for hard problems",
        default=True,
    )
    assert opt.to_payload() == {
        "id": "high",
        "value": "high",
        "label": "High",
        "description": "Best for hard problems",
        "default": True,
    }


def test_option_to_payload_serializes_none_description_as_null() -> None:
    """``description: None`` serializes to ``"description": null`` (serde's
    default for ``Option`` without ``skip_serializing_if``)."""
    opt = ReasoningEffortOption(
        id="low",
        value=ReasoningEffort.LOW,
        label="Low",
        description=None,
        default=False,
    )
    payload = opt.to_payload()
    assert payload["description"] is None
    assert "description" in payload  # explicitly present, not omitted


# ---------------------------------------------------------------------------
# parse_reasoning_effort_options: the untagged Bare-vs-Full option parse.
# ---------------------------------------------------------------------------


def test_options_bare_string_defaults_id_label_and_flags() -> None:
    """A Bare canonical value string -> an option whose ``id`` defaults to the
    wire string, ``label`` to the humanized id, ``description`` -> ``None``,
    ``default`` -> ``False``."""
    options = parse_reasoning_effort_options(["high"])
    assert options == (
        ReasoningEffortOption(
            id="high",
            value=ReasoningEffort.HIGH,
            label="High",
            description=None,
            default=False,
        ),
    )


def test_options_bare_max_alias_round_trips() -> None:
    """KEYSTONE: a Bare ``"max"`` (CLI/UX alias) round-trips to an option
    whose ``value`` is :attr:`ReasoningEffort.XHIGH` and whose defaulted
    ``id`` is the canonical ``"xhigh"`` wire string (the alias is normalized,
    not preserved)."""
    options = parse_reasoning_effort_options(["max"])
    assert options[0].value is ReasoningEffort.XHIGH
    assert options[0].id == "xhigh"
    assert options[0].label == "Xhigh"


def test_options_bare_unknown_is_skipped() -> None:
    """A Bare unknown string -> skipped (warn logged), the rest survive."""
    options = parse_reasoning_effort_options(["high", "ultra", "low"])
    assert [opt.value for opt in options] == [
        ReasoningEffort.HIGH,
        ReasoningEffort.LOW,
    ]


def test_options_full_table_value_only_defaults_everything() -> None:
    """A Full table with just ``value`` -> ``id`` defaults to the wire string,
    ``label`` to the humanized id, ``description`` -> ``None``,
    ``default`` -> ``False``."""
    options = parse_reasoning_effort_options([{"value": "medium"}])
    assert options[0] == ReasoningEffortOption(
        id="medium",
        value=ReasoningEffort.MEDIUM,
        label="Medium",
        description=None,
        default=False,
    )


def test_options_full_table_with_all_fields() -> None:
    """A Full table carrying all 5 fields -> a fully-populated option."""
    options = parse_reasoning_effort_options(
        [
            {
                "id": "turbo",
                "value": "xhigh",
                "label": "Turbo Mode",
                "description": "Maximum reasoning",
                "default": True,
            }
        ]
    )
    assert options[0].id == "turbo"
    assert options[0].value is ReasoningEffort.XHIGH
    assert options[0].label == "Turbo Mode"
    assert options[0].description == "Maximum reasoning"
    assert options[0].default is True


def test_options_full_table_missing_value_is_skipped() -> None:
    """A Full table without ``value`` -> skipped (the required field is absent)."""
    options = parse_reasoning_effort_options([{"id": "x", "label": "X"}])
    assert options == ()


def test_options_full_table_value_max_is_skipped() -> None:
    """KEYSTONE: a Full table ``{"value": "max"}`` -> skipped. The Full-table
    ``value`` field routes through the STRICT serde Deserialize
    (:meth:`ReasoningEffort.from_payload`), which rejects ``"max"`` -- the
    alias is a ``FromStr``-only concession, NOT a wire variant. Contrast the
    Bare-string path (see :func:`test_options_bare_max_alias_round_trips`)
    which accepts ``"max"``."""
    options = parse_reasoning_effort_options([{"value": "max"}])
    assert options == ()


def test_options_full_table_explicit_none_value_is_skipped() -> None:
    """A Full table with ``value: None`` -> skipped (serde rejects a null for
    the non-optional ``ReasoningEffort`` field)."""
    options = parse_reasoning_effort_options([{"value": None}])
    assert options == ()


def test_options_full_table_non_string_id_is_skipped() -> None:
    """An ``id`` that is present but neither string nor null -> skipped (serde
    rejects a non-string for ``Option<String>``)."""
    options = parse_reasoning_effort_options([{"value": "high", "id": 5}])
    assert options == ()


def test_options_full_table_non_string_label_is_skipped() -> None:
    """A ``label`` that is present but neither string nor null -> skipped."""
    options = parse_reasoning_effort_options([{"value": "high", "label": 5}])
    assert options == ()


def test_options_full_table_non_string_description_is_skipped() -> None:
    """A ``description`` that is present but neither string nor null -> skipped."""
    options = parse_reasoning_effort_options([{"value": "high", "description": 5}])
    assert options == ()


def test_options_full_table_non_bool_default_is_skipped() -> None:
    """A ``default`` that is present but not a bool -> skipped (``#[serde(
    default)]`` accepts absent, not non-bool truthy)."""
    options = parse_reasoning_effort_options([{"value": "high", "default": 1}])
    assert options == ()


def test_options_full_table_explicit_null_optional_fields_are_ok() -> None:
    """Explicit ``null`` for the ``Option<String>`` fields is accepted (serde
    maps null to ``None``) -- ``id`` falls back to the value wire string,
    ``label`` to the humanized id, ``description`` stays ``None``."""
    options = parse_reasoning_effort_options(
        [{"value": "high", "id": None, "label": None, "description": None}]
    )
    assert options[0].id == "high"
    assert options[0].label == "High"
    assert options[0].description is None


def test_options_non_string_non_mapping_element_is_skipped() -> None:
    """An element that is neither a string nor a table (e.g. a number) ->
    skipped."""
    options = parse_reasoning_effort_options([42, "high"])  # type: ignore[list-item]
    assert [opt.value for opt in options] == [ReasoningEffort.HIGH]


def test_options_empty_array_returns_empty_tuple() -> None:
    """An empty JSON array -> an empty :class:`tuple` (Vec -> tuple)."""
    assert parse_reasoning_effort_options([]) == ()


def test_options_returns_tuple_not_list() -> None:
    """Project convention: a parsed wire sequence is a :class:`tuple`."""
    options = parse_reasoning_effort_options(["low", "high"])
    assert isinstance(options, tuple)


def test_options_mixed_bare_and_full_and_invalid_keeps_only_valid() -> None:
    """A mixed array -- Bare strings + Full tables + invalid entries -- keeps
    only the parseable entries in input order."""
    options = parse_reasoning_effort_options(
        [
            "low",
            {"value": "high", "default": True},
            "ultra",  # bare unknown -> skip
            {"value": "max"},  # full-table strict value -> skip
            42,  # not string/object -> skip
            "xhigh",
        ]
    )
    assert [opt.value for opt in options] == [
        ReasoningEffort.LOW,
        ReasoningEffort.HIGH,
        ReasoningEffort.XHIGH,
    ]
    assert options[1].default is True


# ---------------------------------------------------------------------------
# parse_reasoning_efforts_meta: plural per-model menu reader.
# ---------------------------------------------------------------------------


def test_efforts_meta_reads_valid_menu() -> None:
    meta = {
        REASONING_EFFORTS_META_KEY: ["low", {"value": "high", "default": True}]
    }
    options = parse_reasoning_efforts_meta(meta)
    assert options is not None
    assert [opt.value for opt in options] == [
        ReasoningEffort.LOW,
        ReasoningEffort.HIGH,
    ]
    assert options[1].default is True


def test_efforts_meta_returns_none_when_meta_none() -> None:
    assert parse_reasoning_efforts_meta(None) is None


def test_efforts_meta_returns_none_when_key_absent() -> None:
    assert parse_reasoning_efforts_meta({"other": []}) is None


def test_efforts_meta_returns_none_on_non_array() -> None:
    """A non-array value -> ``None`` (warn logged)."""
    assert (
        parse_reasoning_efforts_meta({REASONING_EFFORTS_META_KEY: "high"})
        is None
    )
    assert parse_reasoning_efforts_meta({REASONING_EFFORTS_META_KEY: 5}) is None


def test_efforts_meta_returns_none_on_empty_array() -> None:
    """An empty array -> ``None`` (grok ``(!options.is_empty()).then_some``)."""
    assert parse_reasoning_efforts_meta({REASONING_EFFORTS_META_KEY: []}) is None


def test_efforts_meta_returns_none_when_all_entries_skipped() -> None:
    """An array whose every entry fails to parse -> ``None`` -- "present-but-
    unusable" collapses to the same fallback path as "absent"."""
    meta = {REASONING_EFFORTS_META_KEY: ["ultra", {"value": "max"}, 42]}
    assert parse_reasoning_efforts_meta(meta) is None


# ---------------------------------------------------------------------------
# reasoning_efforts_meta_value: plural writer (serde_json::to_value).
# ---------------------------------------------------------------------------


def test_efforts_meta_value_serializes_menu() -> None:
    """Serialize a menu of options to its wire array (each entry via
    :meth:`ReasoningEffortOption.to_payload`)."""
    options = (
        ReasoningEffortOption(
            id="low",
            value=ReasoningEffort.LOW,
            label="Low",
            description=None,
            default=False,
        ),
        ReasoningEffortOption(
            id="high",
            value=ReasoningEffort.HIGH,
            label="High",
            description="Best for hard problems",
            default=True,
        ),
    )
    assert reasoning_efforts_meta_value(options) == [
        {
            "id": "low",
            "value": "low",
            "label": "Low",
            "description": None,
            "default": False,
        },
        {
            "id": "high",
            "value": "high",
            "label": "High",
            "description": "Best for hard problems",
            "default": True,
        },
    ]


def test_efforts_meta_value_empty_menu_yields_empty_array() -> None:
    """An empty menu -> an empty wire array."""
    assert reasoning_efforts_meta_value(()) == []


# ---------------------------------------------------------------------------
# Round-trip: parse then re-serialize is faithful for canonical options.
# ---------------------------------------------------------------------------


def test_parse_then_serialize_round_trips_canonical_menu() -> None:
    """A menu of canonical Bare-string entries round-trips through parse ->
    serialize, preserving the value wire string + defaulted id/label."""
    meta = {REASONING_EFFORTS_META_KEY: ["low", "high", "xhigh"]}
    options = parse_reasoning_efforts_meta(meta)
    assert options is not None
    payload = reasoning_efforts_meta_value(options)
    assert [entry["value"] for entry in payload] == ["low", "high", "xhigh"]
    assert [entry["id"] for entry in payload] == ["low", "high", "xhigh"]
    assert [entry["label"] for entry in payload] == ["Low", "High", "Xhigh"]
    assert all(entry["description"] is None for entry in payload)
    assert all(entry["default"] is False for entry in payload)
