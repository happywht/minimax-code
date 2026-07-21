"""Tests for ``sampler.sampling_config`` (R216, ``xai-grok-sampling-types``
``types.rs`` 1032-1055).

Covers :class:`SamplingConfig` -- the sampling-client configuration container
that consumes the R214 :class:`ApiBackend` (its ``#[serde(default)]
api_backend`` field) + the R206 :class:`ReasoningEffort` (its optional
``reasoning_effort`` field). This is the leaf that resolves the two grok deps
the R206 / R214 docstrings flagged as the deferral reason; both keystones are
under test here:

1. ``NonZeroU64`` -> ``int`` with a ``> 0`` invariant: ``context_window`` is a
   required positive integer (a missing / non-int / bool / ``<= 0`` value
   raises ``ValueError``, mirroring serde's ``NonZeroU64`` deserialize failure
   on ``0``; ``bool`` is an ``int`` subclass in Python but grok ``u64``
   rejects a JSON ``true``).
2. ``IndexMap<String, String>`` -> ``tuple[tuple[str, str], ...]``:
   ``extra_headers`` preserves insertion order as a hashable tuple of pairs
   (the frozen+slots-consistent representation of an ordered map), skips
   non-string keys/values, and falls back to the empty tuple when the wire
   value is missing / null / non-object.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

import minimax_code.sampler.sampling_config as _sc
from minimax_code.sampler import ApiBackend, ReasoningEffort, SamplingConfig
from minimax_code.sampler.api_backend import DEFAULT_API_BACKEND

# A valid reasoning-effort wire value, taken dynamically so the suite does not
# hard-code a specific variant name (the assertions stay variant-agnostic).
_VALID_EFFORT = next(iter(ReasoningEffort)).value


class TestBarrelReExport:
    """The sampler barrel re-exports SamplingConfig by identity."""

    def test_barrel_symbol_is_direct_module(self) -> None:
        assert SamplingConfig is _sc.SamplingConfig


# ---------------------------------------------------------------------------
# Required fields: base_url / model / context_window (strict posture).
# ---------------------------------------------------------------------------


class TestSamplingConfigRequired:
    """``base_url`` / ``model`` are required strings; ``context_window`` is a
    required positive ``int``. A non-dict payload raises (the container is
    strict -- it carries required fields, unlike the R215 ToolCallResponse
    tolerant posture)."""

    def test_non_dict_payload_raises(self) -> None:
        for bad in [None, 42, "x", []]:
            with pytest.raises(ValueError, match="must be a dict"):
                SamplingConfig.from_payload(bad)

    def test_missing_base_url_raises(self) -> None:
        # grok `pub base_url: String` has no #[serde(default)] -> missing fails.
        with pytest.raises(ValueError, match="base_url"):
            SamplingConfig.from_payload({"model": "m", "context_window": 128000})

    def test_non_string_base_url_raises(self) -> None:
        with pytest.raises(ValueError, match="base_url"):
            SamplingConfig.from_payload(
                {"base_url": 123, "model": "m", "context_window": 128000}
            )

    def test_missing_model_raises(self) -> None:
        with pytest.raises(ValueError, match="model"):
            SamplingConfig.from_payload(
                {"base_url": "u", "context_window": 128000}
            )

    def test_non_string_model_raises(self) -> None:
        with pytest.raises(ValueError, match="model"):
            SamplingConfig.from_payload(
                {"base_url": "u", "model": None, "context_window": 128000}
            )

    def test_missing_context_window_raises(self) -> None:
        # grok `pub context_window: NonZeroU64` has no #[serde(default)].
        with pytest.raises(ValueError, match="context_window"):
            SamplingConfig.from_payload({"base_url": "u", "model": "m"})

    def test_zero_context_window_raises(self) -> None:
        # NonZeroU64 deserialize rejects 0 -- mirror the failure.
        with pytest.raises(ValueError, match="> 0"):
            SamplingConfig.from_payload(
                {"base_url": "u", "model": "m", "context_window": 0}
            )

    def test_negative_context_window_raises(self) -> None:
        with pytest.raises(ValueError, match="> 0"):
            SamplingConfig.from_payload(
                {"base_url": "u", "model": "m", "context_window": -1}
            )

    def test_bool_context_window_raises(self) -> None:
        # bool is an int subclass but NonZeroU64 rejects a JSON true.
        with pytest.raises(ValueError, match="context_window"):
            SamplingConfig.from_payload(
                {"base_url": "u", "model": "m", "context_window": True}
            )

    def test_non_int_context_window_raises(self) -> None:
        with pytest.raises(ValueError, match="context_window"):
            SamplingConfig.from_payload(
                {"base_url": "u", "model": "m", "context_window": "big"}
            )


# ---------------------------------------------------------------------------
# Optional fields: tolerate absence / type mismatch -> defaults.
# ---------------------------------------------------------------------------


class TestSamplingConfigOptionals:
    """The 7 optional fields tolerate absence / type mismatch -> their
    defaults (mirrors grok ``Option`` / ``#[serde(default)]`` semantics)."""

    def test_minimal_payload_defaults_optionals(self) -> None:
        cfg = SamplingConfig.from_payload(
            {
                "base_url": "https://api.x.ai",
                "model": "grok-3",
                "context_window": 131072,
            }
        )
        assert cfg.base_url == "https://api.x.ai"
        assert cfg.model == "grok-3"
        assert cfg.context_window == 131072
        assert cfg.api_backend is DEFAULT_API_BACKEND
        assert cfg.max_completion_tokens is None
        assert cfg.temperature is None
        assert cfg.top_p is None
        assert cfg.extra_headers == ()
        assert cfg.reasoning_effort is None
        assert cfg.stream_tool_calls is None

    def test_full_payload_round_trip(self) -> None:
        cfg = SamplingConfig.from_payload(
            {
                "base_url": "https://api.x.ai",
                "model": "grok-3",
                "context_window": 131072,
                "max_completion_tokens": 4096,
                "temperature": 0.7,
                "top_p": 0.95,
                "api_backend": "responses",
                "extra_headers": {"X-Custom": "abc", "X-Trace": "def"},
                "reasoning_effort": _VALID_EFFORT,
                "stream_tool_calls": True,
            }
        )
        assert cfg.max_completion_tokens == 4096
        assert cfg.temperature == 0.7
        assert cfg.top_p == 0.95
        assert cfg.api_backend is ApiBackend.RESPONSES
        assert cfg.extra_headers == (("X-Custom", "abc"), ("X-Trace", "def"))
        assert cfg.reasoning_effort.value == _VALID_EFFORT
        assert cfg.stream_tool_calls is True

    def test_api_backend_null_falls_back_to_default(self) -> None:
        # Platform tolerance: an explicit null is treated as "absent" (grok
        # serde would fail since the field is not Option<ApiBackend>).
        cfg = SamplingConfig.from_payload(
            {"base_url": "u", "model": "m", "context_window": 1, "api_backend": None}
        )
        assert cfg.api_backend is DEFAULT_API_BACKEND

    def test_api_backend_unknown_string_raises(self) -> None:
        with pytest.raises(ValueError, match="api_backend"):
            SamplingConfig.from_payload(
                {
                    "base_url": "u",
                    "model": "m",
                    "context_window": 1,
                    "api_backend": "wat",
                }
            )

    def test_max_completion_tokens_non_int_is_none(self) -> None:
        cfg = SamplingConfig.from_payload(
            {
                "base_url": "u",
                "model": "m",
                "context_window": 1,
                "max_completion_tokens": "big",
            }
        )
        assert cfg.max_completion_tokens is None

    def test_max_completion_tokens_bool_is_none(self) -> None:
        # u32 rejects a JSON true (bool is an int subclass in Python).
        cfg = SamplingConfig.from_payload(
            {
                "base_url": "u",
                "model": "m",
                "context_window": 1,
                "max_completion_tokens": True,
            }
        )
        assert cfg.max_completion_tokens is None

    def test_temperature_accepts_int(self) -> None:
        # grok f32 accepts JSON integers -- an int wire value is kept as-is.
        cfg = SamplingConfig.from_payload(
            {"base_url": "u", "model": "m", "context_window": 1, "temperature": 1}
        )
        assert cfg.temperature == 1

    def test_temperature_bool_is_none(self) -> None:
        cfg = SamplingConfig.from_payload(
            {"base_url": "u", "model": "m", "context_window": 1, "temperature": False}
        )
        assert cfg.temperature is None

    def test_stream_tool_calls_non_bool_is_none(self) -> None:
        cfg = SamplingConfig.from_payload(
            {
                "base_url": "u",
                "model": "m",
                "context_window": 1,
                "stream_tool_calls": "yes",
            }
        )
        assert cfg.stream_tool_calls is None

    def test_reasoning_effort_non_string_is_none(self) -> None:
        cfg = SamplingConfig.from_payload(
            {
                "base_url": "u",
                "model": "m",
                "context_window": 1,
                "reasoning_effort": 3,
            }
        )
        assert cfg.reasoning_effort is None

    def test_reasoning_effort_unknown_string_raises(self) -> None:
        # A present string parses strictly (mirrors serde enum failure).
        with pytest.raises(ValueError):
            SamplingConfig.from_payload(
                {
                    "base_url": "u",
                    "model": "m",
                    "context_window": 1,
                    "reasoning_effort": "ultra",
                }
            )


# ---------------------------------------------------------------------------
# extra_headers: IndexMap -> tuple-of-pairs (keystone #2).
# ---------------------------------------------------------------------------


class TestExtraHeaders:
    """``IndexMap<String, String>`` -> ``tuple[tuple[str, str], ...]``:
    insertion order preserved, non-string items skipped, missing / null /
    non-object -> the empty tuple (mirrors ``#[serde(default)]``)."""

    def test_preserves_insertion_order(self) -> None:
        cfg = SamplingConfig.from_payload(
            {
                "base_url": "u",
                "model": "m",
                "context_window": 1,
                "extra_headers": {"b": "2", "a": "1", "c": "3"},
            }
        )
        assert cfg.extra_headers == (("b", "2"), ("a", "1"), ("c", "3"))

    def test_skips_non_string_items(self) -> None:
        cfg = SamplingConfig.from_payload(
            {
                "base_url": "u",
                "model": "m",
                "context_window": 1,
                "extra_headers": {"ok": "v", "bad": 42, None: "x", "k": None},
            }
        )
        assert cfg.extra_headers == (("ok", "v"),)

    def test_missing_is_empty_tuple(self) -> None:
        cfg = SamplingConfig.from_payload(
            {"base_url": "u", "model": "m", "context_window": 1}
        )
        assert cfg.extra_headers == ()

    def test_null_is_empty_tuple(self) -> None:
        cfg = SamplingConfig.from_payload(
            {
                "base_url": "u",
                "model": "m",
                "context_window": 1,
                "extra_headers": None,
            }
        )
        assert cfg.extra_headers == ()

    def test_non_object_is_empty_tuple(self) -> None:
        cfg = SamplingConfig.from_payload(
            {
                "base_url": "u",
                "model": "m",
                "context_window": 1,
                "extra_headers": "nope",
            }
        )
        assert cfg.extra_headers == ()


# ---------------------------------------------------------------------------
# frozen + slots dataclass semantics.
# ---------------------------------------------------------------------------


class TestFrozenSlotsSemantics:
    """SamplingConfig is ``@dataclass(frozen=True, slots=True)``: mutation
    raises :class:`FrozenInstanceError`, the instance carries ``__slots__``
    with no per-instance ``__dict__``, and it is hashable (frozen enables
    ``__hash__``; ``extra_headers`` is a tuple, so the whole config stays
    hashable -- the payoff of the IndexMap -> tuple-of-pairs mapping)."""

    def test_slots_and_mutation_raises(self) -> None:
        cfg = SamplingConfig.from_payload(
            {"base_url": "u", "model": "m", "context_window": 1}
        )
        assert type(cfg).__slots__ == (
            "base_url",
            "model",
            "context_window",
            "api_backend",
            "max_completion_tokens",
            "temperature",
            "top_p",
            "extra_headers",
            "reasoning_effort",
            "stream_tool_calls",
        )
        # Variable field name (not a constant) -- avoids the B010 rule.
        field_name = next(iter(type(cfg).__slots__))
        with pytest.raises(FrozenInstanceError):
            setattr(cfg, field_name, "mutated")

    def test_no_instance_dict(self) -> None:
        # slots=True -> instances carry no per-instance __dict__.
        cfg = SamplingConfig.from_payload(
            {"base_url": "u", "model": "m", "context_window": 1}
        )
        assert not hasattr(cfg, "__dict__")

    def test_hashable_with_extra_headers(self) -> None:
        # frozen=True restores __hash__ -- the tuple-of-pairs extra_headers
        # keeps the whole config hashable (a dict-typed field would not).
        a = SamplingConfig.from_payload(
            {
                "base_url": "u",
                "model": "m",
                "context_window": 1,
                "extra_headers": {"X-A": "1", "X-B": "2"},
            }
        )
        b = SamplingConfig.from_payload(
            {
                "base_url": "u",
                "model": "m",
                "context_window": 1,
                "extra_headers": {"X-A": "1", "X-B": "2"},
            }
        )
        assert hash(a) == hash(b)
        assert a == b
