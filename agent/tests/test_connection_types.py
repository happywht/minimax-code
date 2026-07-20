"""Tests for ``minimax_code.computer_hub_sdk.connection_types`` (R150).

Mirrors the TYPE-LAYER half of grok-build's
``xai-computer-hub-sdk/src/connection.rs`` lines 58-347 -- the SDK crate's
18th leaf (18a: pure-data foundation). The actor half (``HubConnection`` /
``HubConnectionInner`` lines 360+, the ``WriterControl<S>`` state machine
961-1382, and the 1310-line ``#[cfg(test)]`` block) is a later leaf (R151+):
the actor pulls in tokio mpsc / sink / WebSocket framing / reconnect driver
state, none of which the type layer depends on.

Rust test -> Python test mapping
--------------------------------

* Rust ``reconnect_attempt_budget`` / ``default_reconnect_backoff`` /
  ``resolve_*`` arms (lines 68-286) -> the resolve-function tests below.
* Rust ``ConnHealth`` clock-jump detector (lines 80-167) -> the
  :class:`ConnHealth` snapshot / record_inbound / reset tests (the wall-vs-
  mono excess is exercised via a fake ``time`` module so the lock + window
  roll are observable without sleeping).
* Rust ``enum DisconnectCause`` impl (lines 168-201) -> the per-variant
  ``label`` / ``close_code`` / ``detail`` tests.
* Rust ``impl From<DeadlineCallError> for ClientError`` (lines 212-225) -> the
  ``to_client_error`` tests (``TimedOut`` -> :class:`NetworkError` with the
  timeout in the message; ``OtherError`` -> the wrapped error verbatim).
* Rust ``struct WaiterGuard`` + ``impl Drop`` (lines 226-234) -> the
  :func:`waiter_guard` tests (the guard drains the waiter on both normal and
  exceptional scope exit).
* Rust ``struct ConnKey`` ``PartialEq + Eq + Hash`` (lines 310-324) -> the
  pool-dedup-key equality / hashing tests.
"""

from __future__ import annotations

import dataclasses

import pytest

from minimax_code.computer_hub_sdk import connection_types as ct
from minimax_code.computer_hub_sdk.auth import PrincipalKey
from minimax_code.computer_hub_sdk.connection_types import (
    CLOCK_JUMP_ACCUM_MIN_MS,
    CLOCK_JUMP_REPORT_MIN_MS,
    DEFAULT_WS_PING_INTERVAL,
    RECONNECT_ATTEMPT_MIN_BUDGET,
    RECONNECT_BACKOFF_MS,
    CloseFrame,
    ConnectionTuning,
    ConnHealth,
    ConnKey,
    DeadlineCallError,
    DisconnectCause,
    Eof,
    Forced,
    LivenessDeadline,
    OtherError,
    OutageInfo,
    ReadError,
    ReconnectEvent,
    TimedOut,
    WriteError,
    WriteErrorSlot,
    reconnect_attempt_budget,
    resolve_reconnect_backoff,
    resolve_ws_liveness_deadline,
    resolve_ws_ping_interval,
    waiter_guard,
)
from minimax_code.computer_hub_sdk.error import NetworkError
from minimax_code.tool_protocol.ids import ConnectionId


# ===========================================================================
# Constants (lines 55-78).
# ===========================================================================
def test_constants_match_rust_table() -> None:
    assert ct.OUTBOUND_BUFFER == 256
    assert RECONNECT_BACKOFF_MS == (0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)
    assert RECONNECT_ATTEMPT_MIN_BUDGET == 30.0
    assert DEFAULT_WS_PING_INTERVAL == 30.0
    assert ct.SERVE_ATTEMPT_TIMEOUT == 30.0
    assert ct.SERVE_MAX_ATTEMPTS == 3
    assert ct.CLOCK_PROBE_INTERVAL == 5.0
    assert CLOCK_JUMP_ACCUM_MIN_MS == 100
    assert CLOCK_JUMP_REPORT_MIN_MS == 2000


# ===========================================================================
# Resolve functions (lines 68-286).
# ===========================================================================
def test_reconnect_attempt_budget_floored_at_min() -> None:
    # Below the floor -> floored.
    assert reconnect_attempt_budget(5.0) == RECONNECT_ATTEMPT_MIN_BUDGET
    # At the floor -> floor.
    assert reconnect_attempt_budget(RECONNECT_ATTEMPT_MIN_BUDGET) == 30.0
    # Above the floor -> verbatim.
    assert reconnect_attempt_budget(60.0) == 60.0


def test_default_reconnect_backoff_matches_const() -> None:
    assert ct.default_reconnect_backoff() == RECONNECT_BACKOFF_MS


def test_resolve_reconnect_backoff_fallbacks_and_overrides() -> None:
    # None -> default table.
    assert resolve_reconnect_backoff(None) == RECONNECT_BACKOFF_MS
    # Empty tuple (degenerate) -> default table.
    assert resolve_reconnect_backoff(()) == RECONNECT_BACKOFF_MS
    # Non-empty -> honored verbatim.
    custom = (1.0, 2.0, 4.0)
    assert resolve_reconnect_backoff(custom) is custom


def test_resolve_ws_ping_interval_clamps_zero_and_unset() -> None:
    assert resolve_ws_ping_interval(None) == DEFAULT_WS_PING_INTERVAL
    assert resolve_ws_ping_interval(0.0) == DEFAULT_WS_PING_INTERVAL
    assert resolve_ws_ping_interval(-1.0) == DEFAULT_WS_PING_INTERVAL
    # Positive override honored.
    assert resolve_ws_ping_interval(15.0) == 15.0


def test_resolve_ws_liveness_deadline_defaults_to_2_5x_ping() -> None:
    # Unset / zero / negative -> 2.5x ping.
    assert resolve_ws_liveness_deadline(None, 10.0) == 25.0
    assert resolve_ws_liveness_deadline(0.0, 10.0) == 25.0
    assert resolve_ws_liveness_deadline(-1.0, 10.0) == 25.0
    # Positive override honored verbatim.
    assert resolve_ws_liveness_deadline(60.0, 10.0) == 60.0


# ===========================================================================
# ConnHealth clock-jump tracking (lines 80-167).
# ===========================================================================
class _FakeTime:
    """Stand-in for the ``time`` module bound to fixed monotonic/wall values.

    ``ConnHealth`` reads ``time.monotonic()`` / ``time.time()``; swapping the
    module attribute on :mod:`connection_types` isolates the patch to this
    module (the global ``time`` module is untouched).
    """

    def __init__(self, mono: float, wall: float) -> None:
        self._mono = mono
        self._wall = wall

    def monotonic(self) -> float:
        return self._mono

    def time(self) -> float:
        return self._wall


def test_conn_health_fresh_snapshot_reports_no_jump() -> None:
    health = ConnHealth()
    snap = health.snapshot()
    assert snap.clock_jump_ms == 0
    assert snap.since_last_probe_monotonic_ms >= 0
    assert snap.since_last_probe_wall_ms >= 0


def test_conn_health_snapshot_reports_wall_excess_as_jump(monkeypatch: pytest.MonkeyPatch) -> None:
    health = ConnHealth()
    base_mono = health._state.mono_ref
    base_wall = health._state.wall_ref
    # monotonic advances 100ms; wall advances 3000ms -> 2900ms excess (>= REPORT).
    monkeypatch.setattr(ct, "time", _FakeTime(base_mono + 0.1, base_wall + 3.0))
    snap = health.snapshot()
    assert snap.clock_jump_ms >= CLOCK_JUMP_REPORT_MIN_MS


def test_conn_health_snapshot_below_accum_threshold_no_jump(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = ConnHealth()
    base_mono = health._state.mono_ref
    base_wall = health._state.wall_ref
    # 50ms excess < ACCUM_MIN (100) -> not accumulated -> no jump reported.
    monkeypatch.setattr(ct, "time", _FakeTime(base_mono + 0.05, base_wall + 0.1))
    snap = health.snapshot()
    assert snap.clock_jump_ms == 0


def test_conn_health_record_inbound_rolls_window_and_stamps(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = ConnHealth()
    base_mono = health._state.mono_ref
    base_wall = health._state.wall_ref
    monkeypatch.setattr(ct, "time", _FakeTime(base_mono + 0.1, base_wall + 3.0))
    health.record_inbound()
    # Window rolled: the 2900ms excess is accumulated.
    assert health._state.clock_jump_accum_ms >= CLOCK_JUMP_REPORT_MIN_MS
    # last_inbound stamped to the (rolled) monotonic reading.
    assert health._state.last_inbound_mono == base_mono + 0.1
    # Probe refs rolled forward to the fake readings.
    assert health._state.mono_ref == base_mono + 0.1
    assert health._state.wall_ref == base_wall + 3.0


def test_conn_health_accumulation_persists_across_rolls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    health = ConnHealth()
    base_mono = health._state.mono_ref
    base_wall = health._state.wall_ref
    # First roll: 2900ms excess accumulated, refs roll forward to t1.
    monkeypatch.setattr(ct, "time", _FakeTime(base_mono + 0.1, base_wall + 3.0))
    health.refresh_clock()
    first_accum = health._state.clock_jump_accum_ms
    assert first_accum >= CLOCK_JUMP_REPORT_MIN_MS
    # Second roll from the rolled-forward refs: advance the fake clock another
    # 2900ms-excess window so the accumulator keeps growing (a fixed clock
    # would yield zero deltas on the second roll, since refs already match).
    monkeypatch.setattr(ct, "time", _FakeTime(base_mono + 0.2, base_wall + 6.0))
    health.refresh_clock()
    assert health._state.clock_jump_accum_ms >= first_accum + CLOCK_JUMP_REPORT_MIN_MS


def test_conn_health_reset_clears_accumulation() -> None:
    health = ConnHealth()
    health._state.clock_jump_accum_ms = 99999
    health.reset()
    assert health._state.clock_jump_accum_ms == 0


# ===========================================================================
# WriteErrorSlot (line 79).
# ===========================================================================
def test_write_error_slot_lifecycle() -> None:
    slot = WriteErrorSlot()
    assert slot.get() is None
    slot.set("boom")
    assert slot.get() == "boom"
    slot.set("again")  # overwrite.
    assert slot.get() == "again"
    slot.clear()
    assert slot.get() is None


# ===========================================================================
# DisconnectCause variants (lines 168-201).
# ===========================================================================
def test_disconnect_cause_labels_and_payloads() -> None:
    assert CloseFrame(1000).label() == "close_frame"
    assert CloseFrame(1000).close_code() == 1000
    assert CloseFrame(None).close_code() is None
    assert Eof().label() == "eof"
    assert ReadError("io error").label() == "transport_read_error"
    assert ReadError("io error").detail() == "io error"
    assert WriteError("flush failed").label() == "transport_write_error"
    assert WriteError("flush failed").detail() == "flush failed"
    assert Forced().label() == "forced"
    assert LivenessDeadline().label() == "liveness_deadline"


def test_disconnect_cause_defaults_for_payloadless_variants() -> None:
    # Payload-less variants inherit the None defaults.
    assert Eof().close_code() is None
    assert Eof().detail() is None
    assert Forced().close_code() is None
    assert Forced().detail() is None
    assert LivenessDeadline().detail() is None


def test_disconnect_cause_variants_are_frozen() -> None:
    cf = CloseFrame(1000)
    with pytest.raises(dataclasses.FrozenInstanceError):
        cf.code = 1001  # type: ignore[misc]


def test_disconnect_cause_dispatch_via_isinstance() -> None:
    causes: list[DisconnectCause] = [
        CloseFrame(1001),
        Eof(),
        ReadError("x"),
        WriteError("y"),
        Forced(),
        LivenessDeadline(),
    ]
    # The open family is iterable as a heterogeneous list and discriminable
    # via isinstance (the Rust ``match`` equivalent).
    assert isinstance(causes[0], CloseFrame)
    assert sum(1 for c in causes if isinstance(c, (ReadError, WriteError))) == 2


# ===========================================================================
# OutageInfo (lines 202-211).
# ===========================================================================
def test_outage_info_construction_with_and_without_prev_connection() -> None:
    info = OutageInfo(
        cause=CloseFrame(1001),
        prev_connection_id=ConnectionId("conn-1"),
        prev_connection_duration_ms=12345,
        last_inbound_mono=1.0,
        detect_ms=200,
        since_last_probe_monotonic_ms=50,
        since_last_probe_wall_ms=50,
        clock_jump_ms=0,
    )
    assert info.cause.close_code() == 1001
    assert info.prev_connection_id == ConnectionId("conn-1")
    assert info.detect_ms == 200

    # prev_connection_id is Optional (first-ever connect has no prior conn).
    info2 = OutageInfo(
        cause=Eof(),
        prev_connection_id=None,
        prev_connection_duration_ms=0,
        last_inbound_mono=0.0,
        detect_ms=0,
        since_last_probe_monotonic_ms=0,
        since_last_probe_wall_ms=0,
        clock_jump_ms=0,
    )
    assert info2.prev_connection_id is None


# ===========================================================================
# DeadlineCallError (lines 212-225).
# ===========================================================================
def test_timed_out_converts_to_network_error() -> None:
    err = TimedOut(timeout=30.0).to_client_error()
    assert isinstance(err, NetworkError)
    assert "30.0s" in str(err)


def test_other_error_returns_wrapped_error_verbatim() -> None:
    inner = NetworkError("boom")
    err = OtherError(error=inner).to_client_error()
    assert err is inner


def test_deadline_call_error_dispatch_via_isinstance() -> None:
    errors: list[DeadlineCallError] = [TimedOut(timeout=5.0), OtherError(error=NetworkError("x"))]
    assert isinstance(errors[0], TimedOut)
    assert isinstance(errors[1], OtherError)


# ===========================================================================
# waiter_guard RAII scope guard (lines 226-234).
# ===========================================================================
class _FakeDemux:
    """Records ``take_response_waiter`` calls; stands in for :class:`Demux`."""

    def __init__(self) -> None:
        self.taken: list[str] = []

    def take_response_waiter(self, request_id: str) -> None:
        self.taken.append(request_id)


def test_waiter_guard_drains_on_normal_exit() -> None:
    demux = _FakeDemux()
    with waiter_guard(demux, "req-1"):  # type: ignore[arg-type]
        pass
    assert demux.taken == ["req-1"]


def test_waiter_guard_drains_on_exception() -> None:
    demux = _FakeDemux()
    with pytest.raises(RuntimeError, match="boom"):
        with waiter_guard(demux, "req-2"):  # type: ignore[arg-type]
            raise RuntimeError("boom")
    assert demux.taken == ["req-2"]


# ===========================================================================
# ConnectionTuning (lines 293-307).
# ===========================================================================
def test_connection_tuning_defaults_all_none() -> None:
    tuning = ConnectionTuning()
    assert tuning.ws_ping_interval is None
    assert tuning.ws_liveness_deadline is None
    assert tuning.reconnect_backoff is None


# ===========================================================================
# ConnKey pool dedup (lines 310-324).
# ===========================================================================
def test_connkey_equality_and_hashing_by_url_and_principal() -> None:
    p1 = PrincipalKey(fingerprint="fp-1")
    p1_dup = PrincipalKey(fingerprint="fp-1")
    p2 = PrincipalKey(fingerprint="fp-2")

    same_a = ConnKey("ws://hub", p1)
    same_b = ConnKey("ws://hub", p1_dup)  # same url + fingerprint.
    diff_principal = ConnKey("ws://hub", p2)
    diff_url = ConnKey("ws://other", p1)

    assert same_a == same_b
    assert hash(same_a) == hash(same_b)
    assert same_a != diff_principal
    assert same_a != diff_url

    # Usable as a dict key (pool dedup).
    pool = {same_a: "conn-handle"}
    assert pool[same_b] == "conn-handle"


def test_connkey_is_frozen() -> None:
    key = ConnKey("ws://hub", PrincipalKey(fingerprint="fp-1"))
    with pytest.raises(dataclasses.FrozenInstanceError):
        key.url = "ws://other"  # type: ignore[misc]


# ===========================================================================
# ReconnectEvent (lines 327-336).
# ===========================================================================
def test_reconnect_event_construction() -> None:
    ev = ReconnectEvent(
        connection_id=ConnectionId("conn-2"),
        sessions_replayed=3,
        attempt=2,
    )
    assert ev.connection_id == ConnectionId("conn-2")
    assert ev.sessions_replayed == 3
    assert ev.attempt == 2
