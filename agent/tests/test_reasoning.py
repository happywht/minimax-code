"""Tests for the reasoning-effort type layer — fusion of grok's ``xai-grok-sampling-types`` (R53).

Mirrors grok's contract (six lowercase wire variants with ``Medium`` default;
the ``max`` CLI alias of ``xhigh`` accepted only by the token parsers; ``none``
/ ``minimal`` omitted and ``xhigh`` → ``"max"`` on the Anthropic Messages API;
the model-meta readers collapse absent / wrong-type / unknown into a single
``None`` fallback and skip invalid array entries; the option untagged ``Bare``
/ ``Full`` deserialise via a bare canonical string or a full table) and pins
the Python mapping: the ``str`` enum + serde parity, the strict / lenient
parsers, the model-meta readers, and the before / after validator pair on
:class:`ReasoningEffortOption`.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ValidationError

from minimax_code.agent import reasoning as R

# --- meta keys (grok pub const) ---------------------------------------------


def test_meta_keys_match_grok_constants():
    """The three meta-key constants match grok's ``pub const`` strings."""
    assert R.REASONING_EFFORT_META_KEY == "reasoningEffort"
    assert R.SUPPORTS_REASONING_EFFORT_META_KEY == "supportsReasoningEffort"
    assert R.REASONING_EFFORTS_META_KEY == "reasoningEfforts"


# --- enum variants + wire format --------------------------------------------


def test_effort_variants_are_lowercase_wire():
    """grok ``rename_all = "lowercase"``: each variant's value is the wire token."""
    assert R.ReasoningEffort.NONE.value == "none"
    assert R.ReasoningEffort.MINIMAL.value == "minimal"
    assert R.ReasoningEffort.LOW.value == "low"
    assert R.ReasoningEffort.MEDIUM.value == "medium"
    assert R.ReasoningEffort.HIGH.value == "high"
    assert R.ReasoningEffort.XHIGH.value == "xhigh"


def test_effort_default_is_medium():
    """grok ``#[default] Medium``: :meth:`default` returns ``Medium``."""
    assert R.ReasoningEffort.default() is R.ReasoningEffort.MEDIUM


def test_effort_as_str_is_canonical_wire():
    """grok ``as_str``: returns the canonical wire token for each variant."""
    assert R.ReasoningEffort.LOW.as_str() == "low"
    assert R.ReasoningEffort.XHIGH.as_str() == "xhigh"


def test_effort_to_messages_api_none_minimal_omitted():
    """grok ``to_messages_api``: ``None`` / ``Minimal`` are omitted (→ ``None``)."""
    assert R.ReasoningEffort.NONE.to_messages_api() is None
    assert R.ReasoningEffort.MINIMAL.to_messages_api() is None


def test_effort_to_messages_api_xhigh_maps_to_max():
    """grok ``to_messages_api``: ``Xhigh`` surfaces as ``"max"`` on the Messages API."""
    assert R.ReasoningEffort.XHIGH.to_messages_api() == "max"
    # The middle variants pass through unchanged.
    assert R.ReasoningEffort.LOW.to_messages_api() == "low"
    assert R.ReasoningEffort.MEDIUM.to_messages_api() == "medium"
    assert R.ReasoningEffort.HIGH.to_messages_api() == "high"


def test_effort_str_is_wire_token():
    """``str(effort)`` is the canonical wire token (grok ``Display → as_str``)."""
    assert str(R.ReasoningEffort.NONE) == "none"
    assert str(R.ReasoningEffort.XHIGH) == "xhigh"


def test_effort_serializes_to_lowercase_wire_as_pydantic_field():
    """serde parity: as a pydantic field the enum dumps to the lowercase token.

    A ``str`` enum's value *is* the wire string, so ``model_dump(mode="json")``
    yields the lowercase token — not ``"ReasoningEffort.HIGH"``. This pins the
    pydantic ↔ grok serde ``lowercase`` parity relied on by
    :func:`reasoning_efforts_meta_value`.
    """

    class _Wrap(BaseModel):
        effort: R.ReasoningEffort

    dumped = _Wrap(effort=R.ReasoningEffort.HIGH).model_dump(mode="json")
    assert dumped == {"effort": "high"}


# --- parse_effort_token (lenient, grok parse_canonical_effort_token) ---------


def test_parse_effort_token_accepts_canonical():
    """Canonical tokens parse to their variant (case-insensitive)."""
    assert R.parse_effort_token("medium") is R.ReasoningEffort.MEDIUM
    assert R.parse_effort_token("XHIGH") is R.ReasoningEffort.XHIGH


def test_parse_effort_token_accepts_max_alias():
    """The ``max`` CLI alias parses to ``Xhigh`` (grok ``FromStr`` rule)."""
    assert R.parse_effort_token("max") is R.ReasoningEffort.XHIGH
    assert R.parse_effort_token("MAX") is R.ReasoningEffort.XHIGH


def test_parse_effort_token_returns_none_on_unknown():
    """An unknown token yields ``None`` (grok ``.parse().ok()``), not a raise."""
    assert R.parse_effort_token("bogus") is None
    assert R.parse_effort_token("") is None


# --- parse_effort_strict (grok FromStr, Err = String) -----------------------


def test_parse_effort_strict_accepts_canonical_and_max():
    """Strict parse accepts canonical tokens and the ``max`` alias."""
    assert R.parse_effort_strict("low") is R.ReasoningEffort.LOW
    assert R.parse_effort_strict("max") is R.ReasoningEffort.XHIGH


def test_parse_effort_strict_raises_on_invalid():
    """An invalid token raises ``ValueError`` whose message lists valid tokens."""
    with pytest.raises(ValueError) as exc_info:
        R.parse_effort_strict("bogus")
    msg = str(exc_info.value)
    # The message surfaces the accepted set so a caller can self-correct.
    assert "none" in msg and "xhigh" in msg and "max" in msg


# --- supports_reasoning_effort_meta (grok .as_bool().unwrap_or(false)) ------


def test_supports_reasoning_effort_meta_reads_bool():
    """``True`` only when the key is a literal ``True``."""
    assert R.supports_reasoning_effort_meta({"supportsReasoningEffort": True}) is True
    assert R.supports_reasoning_effort_meta({"supportsReasoningEffort": False}) is False


def test_supports_reasoning_effort_meta_absent_or_non_bool_is_false():
    """Absent key, ``None`` meta, or a non-bool value all collapse to ``False``."""
    assert R.supports_reasoning_effort_meta(None) is False
    assert R.supports_reasoning_effort_meta({}) is False
    # A truthy *string* is not a bool — grok ``as_bool`` would not coerce it.
    assert R.supports_reasoning_effort_meta({"supportsReasoningEffort": "true"}) is False


# --- parse_reasoning_effort_meta (grok parse_reasoning_effort_meta) ---------


def test_parse_reasoning_effort_meta_reads_canonical():
    """A canonical value parses to its variant."""
    meta = {"reasoningEffort": "high"}
    assert R.parse_reasoning_effort_meta(meta) is R.ReasoningEffort.HIGH


def test_parse_reasoning_effort_meta_absent_is_none():
    """A missing key (or ``None`` meta) yields ``None`` (no fallback)."""
    assert R.parse_reasoning_effort_meta(None) is None
    assert R.parse_reasoning_effort_meta({}) is None


def test_parse_reasoning_effort_meta_non_string_is_none():
    """A non-string value is ignored (warn) → ``None`` (no overwrite of a pref)."""
    assert R.parse_reasoning_effort_meta({"reasoningEffort": 5}) is None


def test_parse_reasoning_effort_meta_unknown_is_none():
    """An unknown variant is ignored (warn) → ``None`` (forward-compat)."""
    assert R.parse_reasoning_effort_meta({"reasoningEffort": "turbo"}) is None


# --- reasoning_effort_meta_value -------------------------------------------


def test_reasoning_effort_meta_value_is_canonical_string():
    """Serialise an effort to its meta wire value (grok string value)."""
    assert R.reasoning_effort_meta_value(R.ReasoningEffort.LOW) == "low"
    assert R.reasoning_effort_meta_value(R.ReasoningEffort.XHIGH) == "xhigh"


# --- ReasoningEffortOption: bare string (grok Bare branch, FromStr) ---------


def test_option_bare_string_derives_id_and_label():
    """A bare canonical value derives ``id`` / ``label`` from the value.

    Mirrors grok ``RawReasoningEffortOption::Bare`` (parse via ``FromStr``).
    """
    opt = R.ReasoningEffortOption.model_validate("xhigh")
    assert opt.value is R.ReasoningEffort.XHIGH
    assert opt.id == "xhigh"
    assert opt.label == "Xhigh"  # humanize: first char upper-cased
    assert opt.description is None
    assert opt.default is False


def test_option_bare_string_accepts_max_alias():
    """The bare path honours the ``max`` alias (parses via ``FromStr``)."""
    opt = R.ReasoningEffortOption.model_validate("max")
    assert opt.value is R.ReasoningEffort.XHIGH
    assert opt.id == "xhigh"


# --- ReasoningEffortOption: full table (grok Full branch) ------------------


def test_option_full_table_backfills_id_and_label():
    """A full table with only ``value`` backfills ``id`` / ``label`` defaults."""
    opt = R.ReasoningEffortOption.model_validate({"value": "low"})
    assert opt.value is R.ReasoningEffort.LOW
    assert opt.id == "low"
    assert opt.label == "Low"


def test_option_full_table_passes_through_explicit_id_label():
    """Explicit ``id`` / ``label`` / ``description`` / ``default`` pass through."""
    opt = R.ReasoningEffortOption.model_validate(
        {
            "value": "high",
            "id": "custom-id",
            "label": "Custom Label",
            "description": "top tier",
            "default": True,
        }
    )
    assert opt.value is R.ReasoningEffort.HIGH
    assert opt.id == "custom-id"
    assert opt.label == "Custom Label"
    assert opt.description == "top tier"
    assert opt.default is True


def test_option_object_value_rejects_max_alias():
    """An object's ``value`` is coerced by pydantic to the enum — ``max`` rejected.

    Parity with grok's serde rename: the bare-string path accepts ``max`` (via
    ``FromStr``), but a *table's* ``value`` field deserialises through serde
    ``rename_all = "lowercase"`` which has no ``max`` variant.
    """
    with pytest.raises(ValidationError):
        R.ReasoningEffortOption.model_validate({"value": "max"})


def test_option_ignores_unknown_fields():
    """``extra = "ignore"``: unknown table keys are dropped silently."""
    opt = R.ReasoningEffortOption.model_validate(
        {"value": "medium", "tier": "flagship", "cost": 9}
    )
    assert opt.value is R.ReasoningEffort.MEDIUM
    assert not hasattr(opt, "tier")


# --- parse_reasoning_effort_options (skip invalid) -------------------------


def test_parse_reasoning_effort_options_skips_invalid_entries():
    """Invalid array entries are skipped (warn); valid ones survive in order."""
    arr = ["low", {"value": "high"}, {"value": "turbo"}, "max", 42]
    opts = R.parse_reasoning_effort_options(arr)
    # "turbo" (unknown) and 42 (non-coercible) are dropped; order preserved.
    assert [o.value for o in opts] == [
        R.ReasoningEffort.LOW,
        R.ReasoningEffort.HIGH,
        R.ReasoningEffort.XHIGH,
    ]


# --- parse_reasoning_efforts_meta (grok parse_reasoning_efforts_meta) ------


def test_parse_reasoning_efforts_meta_reads_array():
    """An array of options parses to a list of options."""
    meta = {"reasoningEfforts": ["low", {"value": "high", "label": "Hot"}]}
    opts = R.parse_reasoning_efforts_meta(meta)
    assert opts is not None
    assert [o.value for o in opts] == [R.ReasoningEffort.LOW, R.ReasoningEffort.HIGH]
    assert opts[1].label == "Hot"


def test_parse_reasoning_efforts_meta_absent_or_non_array_is_none():
    """Absent key, ``None`` meta, or a non-array value all yield ``None``."""
    assert R.parse_reasoning_efforts_meta(None) is None
    assert R.parse_reasoning_efforts_meta({}) is None
    assert R.parse_reasoning_efforts_meta({"reasoningEfforts": "low"}) is None


def test_parse_reasoning_efforts_meta_empty_or_all_invalid_is_none():
    """An empty array, or one where every entry is invalid, yields ``None``.

    "Absent" and "present-but-unusable" collapse to the same fallback path.
    """
    assert R.parse_reasoning_efforts_meta({"reasoningEfforts": []}) is None
    assert (
        R.parse_reasoning_efforts_meta({"reasoningEfforts": ["turbo", "bogus"]})
        is None
    )


# --- reasoning_efforts_meta_value ------------------------------------------


def test_reasoning_efforts_meta_value_emits_json_native_list():
    """Serialise a list of options to JSON-native dicts (``value`` lowercase)."""
    opts = R.parse_reasoning_efforts_meta(
        {"reasoningEfforts": ["low", {"value": "high"}]}
    )
    assert opts is not None
    dumped = R.reasoning_efforts_meta_value(opts)
    assert dumped == [
        {
            "value": "low",
            "id": "low",
            "label": "Low",
            "description": None,
            "default": False,
        },
        {
            "value": "high",
            "id": "high",
            "label": "High",
            "description": None,
            "default": False,
        },
    ]
