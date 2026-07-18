"""Tests for intra-compaction config + trigger decision (R28).

Mirrors grok-build's ``intra_compaction::{config, trigger}`` test modules
input-for-input — every Rust ``#[test]`` becomes a Python test with the
same policy and token counts — then adds Python-specific guard tests:

* the strict-``>`` trigger boundary (vs R27's ``>=`` helper), and
* the R27 reuse contract — the trigger's ``percent`` field equals a direct
  call to ``usage_percentage_truncated_u8``.

All pure data / arithmetic, so every test is a plain assertion over
literals — no fixtures, no async, no IO.
"""

from __future__ import annotations

import pytest

from minimax_code.compaction import (
    DEFAULT_COMPACTION_MODEL_NAME,
    IntraCompactionConfig,
    IntraCompactionMode,
    IntraCompactionTrigger,
    IntraSummarizer,
    should_compact,
)
from minimax_code.token_estimation import usage_percentage_truncated_u8

# -- shared policy builders (mirror grok's enabled_policy helpers) ----------


def _enabled_policy(**overrides: object) -> IntraCompactionConfig:
    """Default policy + enabled, trigger=85, target=50, min_steps=3."""
    base: dict[str, object] = {
        "enabled": True,
        "trigger_threshold_percent": 85,
        "target_threshold_percent": 50,
        "min_steps_before_compact": 3,
    }
    base.update(overrides)
    return IntraCompactionConfig(**base)  # type: ignore[arg-type]


def _enabled_partial_policy(mode: IntraCompactionMode) -> IntraCompactionConfig:
    return _enabled_policy(mode=mode)


# =========================================================================
# config tests (mirror config.rs #[cfg(test)])
# =========================================================================


def test_default_is_disabled():
    """Every unset field takes its documented default."""
    p = IntraCompactionConfig.default()
    assert p.enabled is False
    assert p.mode is IntraCompactionMode.FULL_REPLACE
    assert p.summarizer is IntraSummarizer.SHARED
    assert p.trigger_threshold_percent == 85
    assert p.target_threshold_percent == 50
    assert p.compaction_model_name == DEFAULT_COMPACTION_MODEL_NAME
    assert p.effective_compaction_model_name() == DEFAULT_COMPACTION_MODEL_NAME
    assert p.max_attempts == 2
    assert p.retry_delay_secs == 3
    assert p.steps_trigger_ratio == pytest.approx(0.3)
    assert p.min_steps_before_compact == 3
    assert p.min_compactable_tokens == 5_000
    assert p.max_reduction_ratio == pytest.approx(0.8)
    assert p.sampling_timeout_secs == 120
    assert p.compaction_version == "intra-v1"
    assert p.user_message_truncate_chars == 3_000


@pytest.mark.parametrize(
    ("model_name", "expected"),
    [
        (None, DEFAULT_COMPACTION_MODEL_NAME),
        ("", DEFAULT_COMPACTION_MODEL_NAME),
        ("   ", DEFAULT_COMPACTION_MODEL_NAME),
        ("custom-model", "custom-model"),
    ],
)
def test_effective_compaction_model_name_blank_or_none_uses_default(
    model_name: object, expected: str
):
    """None / empty / whitespace → default; non-blank → trimmed value."""
    p = IntraCompactionConfig(compaction_model_name=model_name)  # type: ignore[arg-type]
    assert p.effective_compaction_model_name() == expected


def test_default_mode_is_full_replace():
    assert IntraCompactionMode.default() is IntraCompactionMode.FULL_REPLACE


@pytest.mark.parametrize(
    ("mode", "serialized"),
    [
        (IntraCompactionMode.FULL_REPLACE, "full_replace"),
        (IntraCompactionMode.STEPS_ONLY, "steps_only"),
        (IntraCompactionMode.HISTORY_ONLY, "history_only"),
        (IntraCompactionMode.HISTORY_THEN_STEPS, "history_then_steps"),
    ],
)
def test_mode_serde_round_trip(mode: IntraCompactionMode, serialized: str):
    """snake_case serde parity — value matches and config round-trips."""
    assert mode.value == serialized
    assert IntraCompactionMode(serialized) is mode
    cfg = IntraCompactionConfig(mode=mode)
    data = cfg.to_dict()
    assert data["mode"] == serialized
    assert IntraCompactionConfig.from_dict(data).mode is mode


def test_summarizer_defaults_to_shared():
    assert IntraSummarizer.default() is IntraSummarizer.SHARED
    assert IntraCompactionConfig.default().summarizer is IntraSummarizer.SHARED


@pytest.mark.parametrize(
    ("summarizer", "serialized"),
    [
        (IntraSummarizer.SHARED, "shared"),
        (IntraSummarizer.LEGACY, "legacy"),
    ],
)
def test_summarizer_serde_round_trip(summarizer: IntraSummarizer, serialized: str):
    assert summarizer.value == serialized
    assert IntraSummarizer(serialized) is summarizer


def test_from_dict_partial_json_fills_defaults():
    """Partial JSON — #[serde(default)] fills missing fields."""
    data = {"enabled": True, "trigger_threshold_percent": 80}
    p = IntraCompactionConfig.from_dict(data)
    assert p.enabled is True
    assert p.trigger_threshold_percent == 80
    # Defaults preserved.
    assert p.target_threshold_percent == 50
    assert p.compaction_version == "intra-v1"
    assert p.mode is IntraCompactionMode.FULL_REPLACE


def test_to_dict_from_dict_roundtrip():
    """Full struct survives a to_dict → from_dict round-trip."""
    original = _enabled_policy(
        mode=IntraCompactionMode.HISTORY_THEN_STEPS,
        summarizer=IntraSummarizer.LEGACY,
        compaction_model_name="my-model",
        steps_trigger_ratio=0.42,
    )
    rebuilt = IntraCompactionConfig.from_dict(original.to_dict())
    assert rebuilt == original


def test_from_dict_unknown_enum_falls_back_to_default():
    """An unknown mode string degrades to the default (serde tolerance)."""
    p = IntraCompactionConfig.from_dict({"enabled": True, "mode": "nonsense"})
    assert p.mode is IntraCompactionMode.FULL_REPLACE


def test_from_dict_explicit_null_compaction_model_name():
    """JSON null → None (not the default string), so effective() falls back."""
    p = IntraCompactionConfig.from_dict({"compaction_model_name": None})
    assert p.compaction_model_name is None
    assert p.effective_compaction_model_name() == DEFAULT_COMPACTION_MODEL_NAME


# =========================================================================
# trigger tests (mirror trigger.rs #[cfg(test)])
# =========================================================================


def test_returns_none_when_disabled():
    """A disabled policy never triggers regardless of token pressure."""
    p = _enabled_policy(enabled=False)
    assert should_compact(p, 90_000, 100_000, 10) is None


def test_returns_none_when_below_threshold():
    """84% of 100K = 84_000 < 85% threshold = 85_000 → no trigger."""
    p = _enabled_policy()
    assert should_compact(p, 84_000, 100_000, 10) is None


def test_returns_some_when_above_threshold():
    """90% usage fires; trigger carries the inputs + computed percent."""
    p = _enabled_policy()
    t = should_compact(p, 90_000, 100_000, 10)
    assert t is not None
    assert t.last_prompt_tokens == 90_000
    assert t.context_window == 100_000
    assert t.percent == 90
    assert t.step == 10


def test_full_replace_keeps_field_but_ignores_min_steps():
    """FullReplace uses token threshold alone — fires even at step 0."""
    p = _enabled_policy()
    assert p.mode is IntraCompactionMode.FULL_REPLACE
    assert p.min_steps_before_compact == 3
    t = should_compact(p, 90_000, 100_000, 0)
    assert t is not None
    assert t.step == 0
    # Below min_steps but still triggers under FullReplace.
    assert should_compact(p, 90_000, 100_000, 2) is not None


@pytest.mark.parametrize(
    "mode",
    [
        IntraCompactionMode.STEPS_ONLY,
        IntraCompactionMode.HISTORY_ONLY,
        IntraCompactionMode.HISTORY_THEN_STEPS,
    ],
)
def test_partial_modes_enforce_min_steps(mode: IntraCompactionMode):
    """Partial modes gate on min_steps; FullReplace does not."""
    p = _enabled_partial_policy(mode)
    # step=2 < min_steps=3 → no trigger even at 90% usage.
    assert should_compact(p, 90_000, 100_000, 2) is None
    t = should_compact(p, 90_000, 100_000, 3)
    assert t is not None
    assert t.step == 3


def test_returns_none_when_context_window_zero():
    """A zero/missing context window never triggers."""
    p = _enabled_policy()
    assert should_compact(p, 1_000, 0, 10) is None


def test_percent_caps_at_100():
    """Usage beyond the window still reports 100, not >100."""
    p = _enabled_policy()
    t = should_compact(p, 200_000, 100_000, 10)
    assert t is not None
    assert t.percent == 100


def test_boundary_exact_threshold_does_not_trigger():
    """Strict ``>`` contract — the threshold itself does NOT fire.

    At cw=100_000, pct=85 the threshold is 85_000; ``85_000 <= 85_000`` →
    ``None``. One token above (85_001) fires. This deliberately differs from
    R27's ``exceeds_threshold`` (``>=``), which would fire at 85_000.
    """
    p = _enabled_policy()
    assert should_compact(p, 85_000, 100_000, 10) is None
    assert should_compact(p, 85_001, 100_000, 10) is not None


# =========================================================================
# Python-specific guards
# =========================================================================


def test_percent_matches_r27_usage_percentage():
    """The trigger reuses R27's truncated helper — values agree exactly."""
    p = _enabled_policy()
    # All four sit above the 85% trigger threshold, so each fires — then we
    # check the rendered percent equals R27's helper directly. 250_000/256_000
    # is a non-round ratio (97.656% → 97 truncated) to exercise the truncation
    # path the helper shares with the threshold crossing.
    for used, cw in [
        (90_000, 100_000),
        (200_000, 100_000),
        (85_001, 100_000),
        (250_000, 256_000),
    ]:
        t = should_compact(p, used, cw, 10)
        assert t is not None
        assert t.percent == usage_percentage_truncated_u8(used, cw)


def test_default_config_does_not_trigger():
    """The shipped default is disabled, so it never fires."""
    assert should_compact(IntraCompactionConfig.default(), 99_999, 100_000, 100) is None


def test_trigger_dataclass_fields():
    """IntraCompactionTrigger is a plain dataclass with the four fields."""
    t = IntraCompactionTrigger(
        last_prompt_tokens=10, context_window=20, percent=50, step=7
    )
    assert t.last_prompt_tokens == 10
    assert t.context_window == 20
    assert t.percent == 50
    assert t.step == 7
