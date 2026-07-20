"""Tests for ``minimax_code.computer_hub_sdk.observability`` (R140).

Mirrors grok-build's ``xai-computer-hub-sdk/src/observability.rs`` (275 lines)
-- the SDK crate's 8th leaf. The bridge is a thin facade: compute the
event_type, dispatch the metrics counter, then delegate frame construction +
wire dispatch to ``harness.emit_session_event``. The harness leaf
(``harness.rs``, 2940 lines) has not landed, so the Rust tests that spawn a
real ``ToolHarness::local_only_with`` are reproduced here with a
:class:`types.SimpleNamespace` duck-typed harness (exactly the
``await harness.emit_session_event(event)`` surface the bridge calls).

R140-specific coverage beyond the Rust set:

* :func:`_event_type` is split out as a pure module function (the Rust
  ``match`` arm) and pinned directly: the to_wire-extraction path, the
  ``"unknown"`` fallback, and the full 6-variant -> snake_case mapping.
* :meth:`ObservabilityBridge.emit` delegate + pass-through is pinned with the
  mock harness (the Rust set only asserts the no-panic contract; the Python
  mock lets us assert the event is forwarded unchanged).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from minimax_code.computer_hub_sdk.observability import (
    ObservabilityBridge,
    _event_type,
)
from minimax_code.tool_protocol.ids import SessionId
from minimax_code.tool_protocol.session_event import (
    PhaseChanged,
    SessionPhase,
    ToolCallCompleted,
    ToolCallOutcome,
    ToolCallStarted,
    TurnEnded,
    TurnStarted,
    Unknown,
)
from minimax_code.tool_protocol.turn_hook import TurnHookOutcome


def _sid() -> SessionId:
    return SessionId("session-obs-1")


# ---------------------------------------------------------------------------
# _event_type -- pure module function (Rust match arm)
# ---------------------------------------------------------------------------
def test_event_type_extracts_from_to_wire() -> None:
    # A fake variant whose to_wire emits event_type -- the extraction path.
    fake = SimpleNamespace(to_wire=lambda: {"event_type": "turn_started", "extra": 1})
    assert _event_type(fake) == "turn_started"


def test_event_type_falls_back_to_unknown_when_missing() -> None:
    # A variant whose to_wire omits event_type -- the forward-compat catch-all.
    fake = SimpleNamespace(to_wire=lambda: {"no_event_type_here": 1})
    assert _event_type(fake) == "unknown"


@pytest.mark.parametrize(
    ("event", "expected"),
    [
        (TurnStarted(turn_number=1, model_id="grok-3"), "turn_started"),
        (
            TurnEnded(
                turn_number=1,
                outcome=TurnHookOutcome.COMPLETED,
                duration_ms=5,
                tool_call_count=0,
                model_id="grok-3",
            ),
            "turn_ended",
        ),
        (
            ToolCallStarted(tool_call_id="c1", tool_name="bash", turn_number=1),
            "tool_call_started",
        ),
        (
            ToolCallCompleted(
                tool_call_id="c1",
                tool_name="bash",
                duration_ms=10,
                outcome=ToolCallOutcome.SUCCESS,
            ),
            "tool_call_completed",
        ),
        (PhaseChanged(phase=SessionPhase.SAMPLING), "phase_changed"),
        (Unknown(), "unknown"),
    ],
)
def test_event_type_maps_all_six_real_variants(event: object, expected: str) -> None:
    # DRY contract: _event_type reuses to_wire's event_type rather than an
    # isinstance chain, so every real variant maps correctly without bespoke
    # per-variant logic. Mirrors the Rust match arm coverage.
    assert _event_type(event) == expected  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# has_harness + session_id accessors
# ---------------------------------------------------------------------------
def test_has_harness_returns_false_when_none() -> None:
    bridge = ObservabilityBridge(None, _sid())
    assert bridge.has_harness() is False


def test_has_harness_returns_true_when_present() -> None:
    bridge = ObservabilityBridge(SimpleNamespace(emit_session_event=None), _sid())
    assert bridge.has_harness() is True


def test_session_id_accessor_returns_constructor_value() -> None:
    sid = _sid()
    bridge = ObservabilityBridge(None, sid)
    assert bridge.session_id() == sid


# ---------------------------------------------------------------------------
# emit -- no-op without harness, delegate with harness
# ---------------------------------------------------------------------------
async def test_emit_without_harness_is_noop() -> None:
    # No harness -> emit MUST NOT raise and MUST NOT attempt any dispatch.
    bridge = ObservabilityBridge(None, _sid())
    await bridge.emit(TurnStarted(turn_number=1, model_id="grok-3"))  # no raise


async def test_emit_delegates_to_harness_emit_session_event_unchanged() -> None:
    # With a harness, emit forwards the event verbatim via await
    # harness.emit_session_event(event). The Rust set only pins the no-panic
    # contract; the Python mock lets us assert the exact pass-through.
    received: list[object] = []

    async def _record(event: object) -> None:
        received.append(event)

    harness = SimpleNamespace(emit_session_event=_record)
    bridge = ObservabilityBridge(harness, _sid())
    event = TurnEnded(
        turn_number=1,
        outcome=TurnHookOutcome.COMPLETED,
        duration_ms=42,
        tool_call_count=3,
        model_id="grok-3",
    )
    await bridge.emit(event)
    assert len(received) == 1, "harness.emit_session_event called exactly once"
    assert received[0] is event, "event forwarded by identity (unchanged)"


async def test_emit_all_event_variants_does_not_panic() -> None:
    # Smoke: every variant + every outcome round-trips through emit() with a
    # harness without raising -- mirrors the Rust emit_all_event_variants test.
    # 9 variants = TurnStarted + TurnEnded(3 outcomes) + ToolCallStarted +
    # ToolCallCompleted(3 outcomes) + PhaseChanged (Unknown covered separately
    # by test_event_type_maps_all_six_real_variants).
    async def _noop(_event: object) -> None:
        return None

    bridge = ObservabilityBridge(SimpleNamespace(emit_session_event=_noop), _sid())
    events = [
        TurnStarted(turn_number=1, model_id="grok-3"),
        TurnEnded(
            turn_number=1,
            outcome=TurnHookOutcome.COMPLETED,
            duration_ms=5,
            tool_call_count=0,
            model_id="grok-3",
        ),
        TurnEnded(
            turn_number=2,
            outcome=TurnHookOutcome.ERROR,
            duration_ms=5,
            tool_call_count=1,
            model_id="grok-3",
        ),
        TurnEnded(
            turn_number=3,
            outcome=TurnHookOutcome.CANCELLED,
            duration_ms=5,
            tool_call_count=0,
            model_id="grok-3",
        ),
        ToolCallStarted(tool_call_id="c1", tool_name="bash", turn_number=1),
        ToolCallCompleted(
            tool_call_id="c1",
            tool_name="bash",
            duration_ms=10,
            outcome=ToolCallOutcome.SUCCESS,
        ),
        ToolCallCompleted(
            tool_call_id="c2",
            tool_name="bash",
            duration_ms=10,
            outcome=ToolCallOutcome.ERROR,
        ),
        ToolCallCompleted(
            tool_call_id="c3",
            tool_name="bash",
            duration_ms=10,
            outcome=ToolCallOutcome.CANCELLED,
        ),
        PhaseChanged(phase=SessionPhase.SAMPLING),
    ]
    for event in events:
        await bridge.emit(event)  # none of these may raise
