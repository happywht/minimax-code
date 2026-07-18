"""Intra-compaction configuration (R28).

Ports grok-build's ``xai-grok-compaction::intra_compaction::config`` — the
per-agent config struct that gates *when* intra-compaction fires and *what*
it may compact. This is the compaction crate's first deliverable slice: a
pure data module (two enums + one config dataclass) with no async, no LLM
call, no IO — the trigger decision in :mod:`minimax_code.compaction.trigger`
consumes it, and the heavier select/sample/apply passes are future rounds.

Serde parity
------------
grok tags both enums ``#[serde(rename_all = "snake_case")]`` and the struct
``#[serde(default)]``. The :class:`StrEnum` values below are the snake_case
strings so a config persisted by the Rust implementation round-trips here;
:meth:`IntraCompactionConfig.from_dict` reads every field through
``.get(..., default)`` so a partial JSON degrades to the field defaults —
parity with ``#[serde(default)]``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

__all__ = [
    "DEFAULT_COMPACTION_MODEL_NAME",
    "IntraCompactionConfig",
    "IntraCompactionMode",
    "IntraSummarizer",
]

#: Code-level default compaction model name (last resort). Override order
#: (highest first): agent field (non-blank) → service/agent config → this.
#: Mirrors grok's ``DEFAULT_COMPACTION_MODEL_NAME``.
DEFAULT_COMPACTION_MODEL_NAME: str = "grok-4.20"


class IntraCompactionMode(StrEnum):
    """Which targets intra-compaction may compact.

    Values are snake_case to match grok's ``#[serde(rename_all =
    "snake_case")]``. The default is :attr:`FULL_REPLACE`.
    """

    #: Summarize the whole conversation and rebuild context as
    #: ``[system] + [summary]`` — grok-build's full-replace strategy. No
    #: tail is kept; ``min_steps_before_compact`` is ignored at trigger time.
    FULL_REPLACE = "full_replace"
    #: Compact only accumulated step turns within the current loop (keep tail).
    STEPS_ONLY = "steps_only"
    #: Compact only prior conversation history; leave current step turns alone.
    HISTORY_ONLY = "history_only"
    #: Compact history first, then steps only if they still dominate.
    HISTORY_THEN_STEPS = "history_then_steps"

    @classmethod
    def default(cls) -> IntraCompactionMode:
        """The default mode (mirrors grok's ``#[default]`` on FullReplace)."""
        return cls.FULL_REPLACE


class IntraSummarizer(StrEnum):
    """Which summarization algorithm produces the replacement summary.

    Orthogonal to :class:`IntraCompactionMode` (which picks *what* to
    compact); this picks *how*. Values are snake_case for serde parity.
    """

    #: Shared summarization core — ``build_summary_prompt`` + degenerate
    #: reject + ``format_compact_summary`` cleaning. Default.
    SHARED = "shared"
    #: Previous per-target prompt algorithm, no cleaning. Kept for switchability.
    LEGACY = "legacy"

    @classmethod
    def default(cls) -> IntraSummarizer:
        """The default summarizer (mirrors grok's ``#[default]`` on Shared)."""
        return cls.SHARED


def _enum_or_default(
    enum_cls: type[StrEnum], value: Any, default_member: StrEnum
) -> StrEnum:
    """Parse a config dict value into an enum, falling back on a default.

    ``None`` (field absent / JSON null) or an unknown string returns the
    default member — parity with grok's ``#[serde(default)]`` tolerance.
    """
    if value is None:
        return default_member
    try:
        return enum_cls(value)
    except ValueError:
        return default_member


@dataclass
class IntraCompactionConfig:
    """Per-agent intra-compaction configuration.

    Mirrors grok's ``IntraCompactionConfig`` struct 1:1 — fifteen fields with
    the same defaults (see :meth:`default`), plus the
    :meth:`effective_compaction_model_name` helper. ``FullReplace`` (the
    default mode) ignores ``min_steps_before_compact`` at trigger time, but
    the field is still stored for every mode (YAML / agent config may set it).

    The common block (enablement, trigger gate, reduction guards, LLM call,
    audit) is read by every mode; the mode-specific block is consumed by a
    subset (see the bracketed ``[...]`` tags in grok's field docs).
    """

    # ── Common (all modes) ──
    #: Enable intra-compaction between steps. Default: ``False`` (disabled).
    enabled: bool = False
    #: Which targets may be compacted. Default: :attr:`IntraCompactionMode.FULL_REPLACE`.
    mode: IntraCompactionMode = IntraCompactionMode.FULL_REPLACE
    #: Context window usage % that triggers compaction (0-100). Default: ``85``.
    trigger_threshold_percent: int = 85
    #: Minimum completed steps before compaction (ignored by FullReplace).
    #: Default: ``3``.
    min_steps_before_compact: int = 3
    #: Minimum tokens reducible before compaction is worth running. Default: ``5000``.
    min_compactable_tokens: int = 5_000
    #: Discard compaction if it didn't shrink tokens below this ratio. Default: ``0.8``.
    max_reduction_ratio: float = 0.8
    #: Compaction model name; blank/None → :data:`DEFAULT_COMPACTION_MODEL_NAME`.
    compaction_model_name: str | None = DEFAULT_COMPACTION_MODEL_NAME
    #: End-to-end timeout for the compaction LLM call (seconds). Default: ``120``.
    sampling_timeout_secs: int = 120
    #: Max attempts for the compaction LLM call (total tries). Default: ``2``.
    max_attempts: int = 2
    #: Delay between retries (seconds). Default: ``3``.
    retry_delay_secs: int = 3
    #: Version string recorded in audit logs. Default: ``"intra-v1"``.
    compaction_version: str = "intra-v1"

    # ── Mode-specific (subset of modes; FullReplace ignores these) ──
    #: [Partial modes] Summarization algorithm. Default: :attr:`IntraSummarizer.SHARED`.
    summarizer: IntraSummarizer = IntraSummarizer.SHARED
    #: [Partial modes] Target usage % after compaction. Default: ``50``.
    target_threshold_percent: int = 50
    #: [HistoryThenSteps] Compact steps only above this fraction of history.
    #: Default: ``0.3``.
    steps_trigger_ratio: float = 0.3
    #: [History targets] Char threshold to middle-truncate original user
    #: messages in the ``<grok_user_queries>`` preamble. Default: ``3000``.
    user_message_truncate_chars: int = 3_000

    @classmethod
    def default(cls) -> IntraCompactionConfig:
        """The unset/blank defaults (mirrors grok's ``Default`` impl)."""
        return cls()

    def effective_compaction_model_name(self) -> str:
        """Agent field; blank/None/whitespace → :data:`DEFAULT_COMPACTION_MODEL_NAME`.

        Override order: agent field (non-blank after trim) → the default
        constant. Parity with grok's
        ``compaction_model_name.as_deref().map(trim).filter(non-empty).unwrap_or(DEFAULT)``.
        """
        if self.compaction_model_name is not None:
            trimmed = self.compaction_model_name.strip()
            if trimmed:
                return trimmed
        return DEFAULT_COMPACTION_MODEL_NAME

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a JSON-compatible dict (snake_case enum values)."""
        return {
            "enabled": self.enabled,
            "mode": self.mode.value,
            "trigger_threshold_percent": self.trigger_threshold_percent,
            "min_steps_before_compact": self.min_steps_before_compact,
            "min_compactable_tokens": self.min_compactable_tokens,
            "max_reduction_ratio": self.max_reduction_ratio,
            "compaction_model_name": self.compaction_model_name,
            "sampling_timeout_secs": self.sampling_timeout_secs,
            "max_attempts": self.max_attempts,
            "retry_delay_secs": self.retry_delay_secs,
            "compaction_version": self.compaction_version,
            "summarizer": self.summarizer.value,
            "target_threshold_percent": self.target_threshold_percent,
            "steps_trigger_ratio": self.steps_trigger_ratio,
            "user_message_truncate_chars": self.user_message_truncate_chars,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IntraCompactionConfig:
        """Deserialize from a dict.

        Every field is read through ``.get(..., default)`` so a partial JSON
        (or a legacy snapshot missing newer fields) degrades to the field
        defaults — parity with grok's ``#[serde(default)]``. An unknown enum
        string falls back to that field's default member.
        """
        return cls(
            enabled=bool(data.get("enabled", False)),
            mode=_enum_or_default(
                IntraCompactionMode,
                data.get("mode"),
                IntraCompactionMode.FULL_REPLACE,
            ),
            trigger_threshold_percent=int(data.get("trigger_threshold_percent", 85)),
            min_steps_before_compact=int(data.get("min_steps_before_compact", 3)),
            min_compactable_tokens=int(data.get("min_compactable_tokens", 5_000)),
            max_reduction_ratio=float(data.get("max_reduction_ratio", 0.8)),
            compaction_model_name=data.get(
                "compaction_model_name", DEFAULT_COMPACTION_MODEL_NAME
            ),
            sampling_timeout_secs=int(data.get("sampling_timeout_secs", 120)),
            max_attempts=int(data.get("max_attempts", 2)),
            retry_delay_secs=int(data.get("retry_delay_secs", 3)),
            compaction_version=str(data.get("compaction_version", "intra-v1")),
            summarizer=_enum_or_default(
                IntraSummarizer, data.get("summarizer"), IntraSummarizer.SHARED
            ),
            target_threshold_percent=int(data.get("target_threshold_percent", 50)),
            steps_trigger_ratio=float(data.get("steps_trigger_ratio", 0.3)),
            user_message_truncate_chars=int(data.get("user_message_truncate_chars", 3_000)),
        )


# Silence ruff: ``field`` is imported for symmetry / future mutable defaults.
_ = field
