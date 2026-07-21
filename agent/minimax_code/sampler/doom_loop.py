"""Server-side doom-loop check wire contract + tolerant parsers (R200).

Fuses ``xai-grok-sampling-types`` ``doom_loop.rs`` -- the single home for the
doom-loop wire shape consumed by the inference streaming layer. When the client
opts in via the ``x-grok-doom-loop-check`` request header, the API reports
detected generation loops in two places: a non-standard mid-stream SSE event
(``response.doom_loop_check``) carrying the cumulative trigger set, and a
``doom_loop_check`` field on the terminal response object. Triggers are opaque
labels with the grammar ``tail_repetition:{threshold}@{channel}`` or
``low_logprob@{channel}``; presence is itself the detection signal.

This module is no-I/O (``serde_json::Value`` -> ``json.loads`` / ``dict.get``
pure mapping). Everything is best-effort by design: malformed payloads yield
``Unknown`` kinds or empty trigger sets, never an error, so the feature can
never fail a stream. The parsed :class:`DoomLoopSignal` ``raw`` labels feed the
R199 :class:`~minimax_code.sampler.types.DoomLoopDetected` variant.

Migration map (grok -> Python):

- ``DoomLoopSignalKind`` enum (3 variants) -> frozen+slots discriminated union
  (base + :class:`TailRepetition` / :class:`LowLogprob` / :class:`Unknown`).
- ``DoomLoopPeek`` enum (3 variants) -> frozen+slots discriminated union
  (base + :class:`CheckEvent` / :class:`ResponseField` / :class:`NoDoomLoop`;
  grok's ``None`` variant is renamed to avoid clashing with Python's ``None``).
- ``u32`` threshold parse -> ``int()`` + ``[0, 2**32 - 1]`` range guard (mirrors
  ``str::parse::<u32>()`` rejection of negatives / overflow).
- ``serde_json::Value`` pointer / ``.get().as_str()`` -> ``dict.get`` chains.
- ``RangeInclusive<u32>`` -> ``tuple[int, int]`` :class:`~typing.ClassVar`.
- ``#[serde(default)]`` tolerance -> :meth:`DoomLoopRecoveryPolicy.from_payload`.

YAGNI: ``serde`` ``Serialize``/``Deserialize`` derives are not needed --
equality plus the ``parse(raw)`` round-trip cover the wire contract.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar

# ---------------------------------------------------------------------------
# Wire constants
# ---------------------------------------------------------------------------

# Request header whose presence enables the server-side check.
DOOM_LOOP_CHECK_HEADER = "x-grok-doom-loop-check"

# ``type`` of the non-standard mid-stream SSE event (also its SSE ``event:``
# name). Typed event decoders do not know this variant, so raw payloads must be
# intercepted before typed decoding.
DOOM_LOOP_CHECK_EVENT_TYPE = "response.doom_loop_check"

# Channel label of the model's thinking stream -- the only channel recovery
# acts on (loops in visible output are the user's to judge).
THINKING_CHANNEL = "thinking"

# Byte-exact ``data:`` payload fixtures (verbatim from the server wire sample),
# exported so transport tests pin the real bytes, not a paraphrase.
SAMPLE_CHECK_EVENT_DATA = (
    '{"sequence_number":4176,"type":"response.doom_loop_check",'
    '"doom_loop_check":{"triggers":["tail_repetition:4@response"]}}'
)
SAMPLE_CHECK_EVENT_DATA_CUMULATIVE = (
    '{"sequence_number":4178,"type":"response.doom_loop_check",'
    '"doom_loop_check":{"triggers":'
    '["tail_repetition:4@response","tail_repetition:2@response"]}}'
)

# Rust ``u32`` upper bound; thresholds outside ``[0, 2**32 - 1]`` degrade to
# ``Unknown`` to mirror ``str::parse::<u32>()`` rejection.
_U32_MAX = 0xFFFFFFFF


# ---------------------------------------------------------------------------
# DoomLoopSignalKind: 3-variant discriminated union.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DoomLoopSignalKind:
    """Parsed classification of a single trigger label (discriminated-union base)."""


@dataclass(frozen=True, slots=True)
class TailRepetition(DoomLoopSignalKind):
    """``tail_repetition:{threshold}@{channel}`` -- a repeating tail was found."""

    threshold: int


@dataclass(frozen=True, slots=True)
class LowLogprob(DoomLoopSignalKind):
    """``low_logprob@{channel}`` -- degenerate low-entropy generation."""


@dataclass(frozen=True, slots=True)
class Unknown(DoomLoopSignalKind):
    """Any label this client version cannot classify; the unparsed kind segment."""

    kind: str


# ---------------------------------------------------------------------------
# DoomLoopSignal: one reported trigger + tolerant parser.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DoomLoopSignal:
    """One doom-loop trigger reported by the server."""

    kind: DoomLoopSignalKind
    # Channel the loop was detected on (e.g. ``thinking``, ``response``); empty
    # when the label carries no ``@channel`` suffix.
    channel: str
    # The verbatim label; the stable identity used for deduplication + logging.
    raw: str

    @classmethod
    def parse(cls, raw: str) -> DoomLoopSignal:
        """Parse a trigger label. Never fails: any grammar mismatch yields
        :class:`Unknown` with the raw head segment preserved."""
        at = raw.split("@", 1)
        head = at[0]
        channel = at[1] if len(at) == 2 else ""
        colon = head.split(":", 1)
        if len(colon) == 2:
            kind_head, threshold = colon
            if kind_head == "tail_repetition":
                try:
                    value = int(threshold)
                except ValueError:
                    return cls(kind=Unknown(head), channel=channel, raw=raw)
                if value < 0 or value > _U32_MAX:
                    return cls(kind=Unknown(head), channel=channel, raw=raw)
                return cls(kind=TailRepetition(value), channel=channel, raw=raw)
            return cls(kind=Unknown(head), channel=channel, raw=raw)
        if head == "low_logprob":
            return cls(kind=LowLogprob(), channel=channel, raw=raw)
        return cls(kind=Unknown(head), channel=channel, raw=raw)

    @staticmethod
    def tightest(raws: Iterable[str]) -> str | None:
        """The tightest label among ``raws``: the ``tail_repetition`` trigger
        with the LOWEST threshold (tighter repetition = stronger evidence),
        falling back to the first label when none parse as ``tail_repetition``.
        Empty input -> ``None``. Raw labels only -- telemetry-safe."""
        first: str | None = None
        best: tuple[int, str] | None = None
        for raw in raws:
            if first is None:
                first = raw
            kind = DoomLoopSignal.parse(raw).kind
            if isinstance(kind, TailRepetition) and (
                best is None or kind.threshold < best[0]
            ):
                best = (kind.threshold, raw)
        if best is not None:
            return best[1]
        return first


# ---------------------------------------------------------------------------
# DoomLoopRecoveryPolicy: resolved runtime tunables + decision helpers.
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DoomLoopRecoveryPolicy:
    """Resolved runtime tunables for doom-loop recovery (env > config > default).

    Produced once per session by the config resolver, which returns ``None``
    when the check is disabled -- absence IS the off state, so there is no
    separate enabled flag to keep in sync. When present on the sampler config,
    the sampler sends the opt-in header AND parses reported triggers; the
    tunables drive the recovery decision logic.
    """

    # Field defaults mirror grok's ``default_max_threshold`` / ``default_max_retries``
    # serde-default fns; the ClassVars below are the named aliases.
    max_threshold: int = 8
    max_retries: int = 2

    # Clamp range for ``max_threshold`` (grok ``RangeInclusive``).
    MAX_THRESHOLD_RANGE: ClassVar[tuple[int, int]] = (2, 64)
    # Clamp range for ``max_retries``.
    MAX_RETRIES_RANGE: ClassVar[tuple[int, int]] = (0, 5)
    # Default ``max_threshold`` (lowest common threshold in the backtest corpus).
    DEFAULT_MAX_THRESHOLD: ClassVar[int] = 8
    # Default ``max_retries``.
    DEFAULT_MAX_RETRIES: ClassVar[int] = 2

    @staticmethod
    def clamp_max_threshold(value: int) -> int:
        """Clamp a configured ``max_threshold`` into :data:`MAX_THRESHOLD_RANGE`."""
        low, high = DoomLoopRecoveryPolicy.MAX_THRESHOLD_RANGE
        return max(low, min(value, high))

    @staticmethod
    def clamp_max_retries(value: int) -> int:
        """Clamp a configured ``max_retries`` into :data:`MAX_RETRIES_RANGE`."""
        low, high = DoomLoopRecoveryPolicy.MAX_RETRIES_RANGE
        return max(low, min(value, high))

    def is_confident(self, signal: DoomLoopSignal) -> bool:
        """A signal this policy treats as a real loop worth acting on: tail
        repetition in the thinking channel at or below the confidence threshold
        (lower detector thresholds mean tighter repetition). Everything else --
        other channels, ``low_logprob``, unknown kinds, looser thresholds -- is
        warn-only."""
        return (
            signal.channel == THINKING_CHANNEL
            and isinstance(signal.kind, TailRepetition)
            and signal.kind.threshold <= self.max_threshold
        )

    def confident_triggers(self, signals: Sequence[DoomLoopSignal]) -> list[str]:
        """Raw labels of the confident signals in ``signals``; empty when none."""
        return [s.raw for s in signals if self.is_confident(s)]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DoomLoopRecoveryPolicy:
        """Tolerant constructor mirroring serde ``#[serde(default)]``: missing
        fields fall back to defaults; unknown keys are ignored (forwards-compat
        with configs persisted by older / future versions)."""
        kwargs: dict[str, int] = {}
        if "max_threshold" in payload:
            kwargs["max_threshold"] = payload["max_threshold"]
        if "max_retries" in payload:
            kwargs["max_retries"] = payload["max_retries"]
        return cls(**kwargs)


# ---------------------------------------------------------------------------
# DoomLoopPeek: tolerant SSE payload classification (3-variant union).
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DoomLoopPeek:
    """Result of peeking a raw SSE ``data:`` payload for doom-loop content."""


@dataclass(frozen=True, slots=True)
class CheckEvent(DoomLoopPeek):
    """The non-standard ``response.doom_loop_check`` event: the caller must
    swallow it (never forward to the typed event parser); ``signals`` is empty
    when the payload is malformed."""

    signals: tuple[DoomLoopSignal, ...]


@dataclass(frozen=True, slots=True)
class ResponseField(DoomLoopPeek):
    """An ordinary event whose ``response`` object carries a ``doom_loop_check``
    field (the terminal belt-and-braces copy). Forward the event as usual after
    recording the signals."""

    signals: tuple[DoomLoopSignal, ...]


@dataclass(frozen=True, slots=True)
class NoDoomLoop(DoomLoopPeek):
    """Nothing doom-loop related; forward untouched (grok ``DoomLoopPeek::None``,
    renamed to avoid clashing with Python's ``None``)."""


# ---------------------------------------------------------------------------
# Free functions: tolerant peek + check-event detection.
# ---------------------------------------------------------------------------


def _parse_triggers(triggers: Any) -> tuple[DoomLoopSignal, ...]:
    """Parse a ``triggers`` JSON value into signals, skipping non-string entries.

    A missing or non-list value yields an empty tuple."""
    if not isinstance(triggers, list):
        return ()
    return tuple(DoomLoopSignal.parse(item) for item in triggers if isinstance(item, str))


def peek_doom_loop(data: str) -> DoomLoopPeek:
    """Tolerantly peek a raw SSE ``data:`` JSON payload for doom-loop content.

    Cheap for the common case: payloads that don't mention ``doom_loop_check``
    return :class:`NoDoomLoop` without a JSON parse. Anything malformed (non-JSON,
    wrong types, missing keys) degrades to :class:`NoDoomLoop` or an empty
    trigger tuple -- never an error."""
    if "doom_loop_check" not in data:
        return NoDoomLoop()
    try:
        value = json.loads(data)
    except ValueError:
        return NoDoomLoop()
    if not isinstance(value, dict):
        return NoDoomLoop()
    if value.get("type") == DOOM_LOOP_CHECK_EVENT_TYPE:
        check = value.get("doom_loop_check")
        triggers = check.get("triggers") if isinstance(check, dict) else None
        return CheckEvent(_parse_triggers(triggers))
    response = value.get("response")
    if isinstance(response, dict):
        check = response.get("doom_loop_check")
        if isinstance(check, dict):
            return ResponseField(_parse_triggers(check.get("triggers")))
    return NoDoomLoop()


def is_check_event(event_name: str, data: str) -> bool:
    """True when an SSE frame IS the doom-loop check event -- by its SSE
    ``event:`` name, or (for servers that omit the name) by a tolerant peek of
    the payload's ``"type"`` tag, gated on a cheap substring precheck so normal
    traffic never pays a JSON parse. The type confirmation prevents
    false-swallowing a legitimate event whose content merely quotes the
    event-type string. An unnamed frame with an unparseable payload is NOT the
    check event -- a real server frame always carries the name or a parseable
    ``type`` tag."""
    if event_name == DOOM_LOOP_CHECK_EVENT_TYPE:
        return True
    if DOOM_LOOP_CHECK_EVENT_TYPE not in data:
        return False
    try:
        value = json.loads(data)
    except ValueError:
        return False
    return isinstance(value, dict) and value.get("type") == DOOM_LOOP_CHECK_EVENT_TYPE


__all__ = [
    "DOOM_LOOP_CHECK_EVENT_TYPE",
    "DOOM_LOOP_CHECK_HEADER",
    "SAMPLE_CHECK_EVENT_DATA",
    "SAMPLE_CHECK_EVENT_DATA_CUMULATIVE",
    "THINKING_CHANNEL",
    "CheckEvent",
    "DoomLoopPeek",
    "DoomLoopRecoveryPolicy",
    "DoomLoopSignal",
    "DoomLoopSignalKind",
    "LowLogprob",
    "NoDoomLoop",
    "ResponseField",
    "TailRepetition",
    "Unknown",
    "is_check_event",
    "peek_doom_loop",
]
