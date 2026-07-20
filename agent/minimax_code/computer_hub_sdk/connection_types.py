"""Connection types layer (R150, SDK leaf 18a).

Forward-port of grok-build ``xai-computer-hub-sdk/src/connection.rs`` lines
58-347: the pure-data foundation the connection actor builds on -- constants,
the resolve functions, health/clock-jump tracking, disconnect classification,
deadline-call error, the waiter RAII guard, pool dedup key, tuning knobs,
reconnect event payload, and the three callback type aliases.

The actor itself -- ``HubConnection`` / ``HubConnectionInner`` (lines 360+),
the ``ConnectedExit`` / ``WriterControl<S>`` state machine (961-1382), and the
1310-line ``#[cfg(test)]`` block -- is a later leaf (R151+). This module has
no network / async-actor dependency, so it ports cleanly as pure data + a
thread-safe health tracker.

tokio -> asyncio / Rust -> Python adaptations (no behavior change):

* ``tokio::time::Instant`` (monotonic) -> ``time.monotonic()``.
* ``std::time::SystemTime`` (wall) -> ``time.time()``.
* ``std::time::Duration`` -> ``float`` seconds.
* ``parking_lot::Mutex`` -> ``threading.Lock`` (the health tracker is touched
  from both the reader task and the liveness probe; a sync lock mirrors the
  Rust ``Mutex`` without crossing the async boundary).
* ``Arc<[Duration]>`` shared backoff -> ``tuple[float, ...]`` (immutable, so
  aliasing is safe without refcounting).
* ``Arc<Mutex<Option<String>>>`` write-error slot -> :class:`WriteErrorSlot`
  (thread-safe optional string).
* ``struct WaiterGuard<'a>`` + ``impl Drop`` -> :func:`waiter_guard`
  contextmanager (the Rust RAII scope guard is a ``with`` block in Python).
* ``enum DisconnectCause`` / ``enum DeadlineCallError`` (tagged unions) ->
  open frozen-dataclass families dispatched via ``isinstance`` / ``.label()``
  (Python has no sum-type enum that carries per-variant fields ergonomically).

YAGNI boundary: ``WriteErrorSlot`` is defined here (the type is part of the
foundation) but is only wired into the writer task in the connection-actor
leaf (R151+); the three callback aliases are typed but the dispatch points
live in the actor.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING

from minimax_code.computer_hub_sdk.auth import PrincipalKey
from minimax_code.computer_hub_sdk.error import ClientError, NetworkError
from minimax_code.tool_protocol.ids import ConnectionId

if TYPE_CHECKING:
    # ``Demux`` / ``RequestId`` are only needed at the ``waiter_guard`` call
    # site (R149 / R106), kept out of runtime imports to avoid a cycle.
    from minimax_code.computer_hub_sdk.demux import Demux
    from minimax_code.tool_protocol.ids import RequestId

__all__ = [
    # Constants.
    "OUTBOUND_BUFFER",
    "RECONNECT_BACKOFF_MS",
    "RECONNECT_ATTEMPT_MIN_BUDGET",
    "DEFAULT_WS_PING_INTERVAL",
    "SERVE_ATTEMPT_TIMEOUT",
    "SERVE_MAX_ATTEMPTS",
    "CLOCK_PROBE_INTERVAL",
    "CLOCK_JUMP_ACCUM_MIN_MS",
    "CLOCK_JUMP_REPORT_MIN_MS",
    # Resolve functions.
    "reconnect_attempt_budget",
    "default_reconnect_backoff",
    "resolve_reconnect_backoff",
    "resolve_ws_ping_interval",
    "resolve_ws_liveness_deadline",
    # Health tracking.
    "HealthState",
    "HealthSnapshot",
    "ConnHealth",
    "WriteErrorSlot",
    # Disconnect classification.
    "DisconnectCause",
    "CloseFrame",
    "Eof",
    "ReadError",
    "WriteError",
    "Forced",
    "LivenessDeadline",
    "OutageInfo",
    # Deadline call error.
    "DeadlineCallError",
    "TimedOut",
    "OtherError",
    # Waiter RAII guard.
    "waiter_guard",
    # Tuning + dedup + event + callbacks.
    "ConnectionTuning",
    "ConnKey",
    "ReconnectEvent",
    "ReconnectCallback",
    "DisconnectCallback",
    "ConnectCallback",
]


# ===========================================================================
# Constants (lines 55-78).
# ===========================================================================
# Outbound mpsc bound. Picked to match the server's per-actor outbound buffer
# so a single-process roundtrip never dead-blocks on sender capacity.
OUTBOUND_BUFFER = 256
# Backoff schedule (seconds) for reconnect attempts. The last value is reused
# for any further attempts so the cap is 10s.
RECONNECT_BACKOFF_MS: tuple[float, ...] = (0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)
# Floor for the per-attempt reconnect budget: a small liveness override must
# not shrink it below what a WAN handshake + session replay needs.
RECONNECT_ATTEMPT_MIN_BUDGET = 30.0
# Default WebSocket keepalive ping cadence (seconds).
DEFAULT_WS_PING_INTERVAL = 30.0
# Per-attempt serve (session replay) budget + retry cap.
SERVE_ATTEMPT_TIMEOUT = 30.0
SERVE_MAX_ATTEMPTS = 3
# Clock-jump probe cadence + accumulation/report thresholds (ms).
CLOCK_PROBE_INTERVAL = 5.0
CLOCK_JUMP_ACCUM_MIN_MS = 100
CLOCK_JUMP_REPORT_MIN_MS = 2000


# ===========================================================================
# Resolve functions (lines 68-286).
# ===========================================================================
def reconnect_attempt_budget(liveness_deadline: float) -> float:
    """Per-attempt reconnect budget: the liveness deadline, floored.

    Liveness tuning bounds detection, not connection establishment -- a tiny
    liveness deadline must not shrink the per-attempt budget below what a WAN
    handshake + session replay needs, or the retry loop would livelock.
    """
    return max(liveness_deadline, RECONNECT_ATTEMPT_MIN_BUDGET)


def default_reconnect_backoff() -> tuple[float, ...]:
    """Process-wide default reconnect schedule (materialised from const)."""
    return RECONNECT_BACKOFF_MS


def resolve_reconnect_backoff(configured: tuple[float, ...] | None) -> tuple[float, ...]:
    """Resolve a backoff schedule, falling back to the built-in table.

    ``None`` *or* an empty tuple (degenerate) both fall back to the default.
    """
    if configured is not None and len(configured) > 0:
        return configured
    return default_reconnect_backoff()


def resolve_ws_ping_interval(configured: float | None) -> float:
    """Resolve the keepalive ping cadence, clamping unset *or zero* to default.

    A zero period would panic the equivalent of ``tokio::time::interval``;
    ``Duration::ZERO`` (e.g. via ``with_ws_ping_interval(0)``) must never reach
    the writer task.
    """
    if configured is not None and configured > 0:
        return configured
    return DEFAULT_WS_PING_INTERVAL


def resolve_ws_liveness_deadline(configured: float | None, ping_interval: float) -> float:
    """Resolve the inbound-liveness deadline, clamping unset *or zero* to 2.5x ping.

    2.5x tolerates a fully lost/coalesced pong plus scheduling jitter before
    declaring death (a healthy connection delivers >=1 inbound frame per ping
    period). Still detects a silently dead transport within ~1-2 keepalive
    cycles instead of TCP-retransmission timescales. Explicit overrides are
    honored verbatim.
    """
    if configured is not None and configured > 0:
        return configured
    return ping_interval * 5 / 2


# ===========================================================================
# Health / clock-jump tracking (lines 80-167).
# ===========================================================================
@dataclass
class HealthState:
    """Mutable per-connection health state (guarded by :class:`ConnHealth`)."""

    last_inbound_mono: float
    mono_ref: float
    wall_ref: float
    clock_jump_accum_ms: int


@dataclass
class HealthSnapshot:
    """Point-in-time read of connection health.

    ``since_last_probe_monotonic_ms`` is elapsed since the last probe window
    rolled (the most recent inbound frame or 5s clock probe) -- NOT since
    connection start. Healthy traffic keeps it small (<= ~5s); the meaningful
    freeze signal in this snapshot is ``clock_jump_ms``.
    """

    last_inbound_mono: float
    since_last_probe_monotonic_ms: int
    since_last_probe_wall_ms: int
    clock_jump_ms: int


class ConnHealth:
    """Thread-safe connection health tracker.

    Mirrors ``ConnHealth`` (parking_lot::Mutex -> threading.Lock). The clock-
    jump detector compares monotonic elapsed vs wall elapsed per probe window;
    a sustained wall excess (VM pause, GC stall, host sleep) accumulates and,
    once it crosses :data:`CLOCK_JUMP_REPORT_MIN_MS`, surfaces in the snapshot.
    """

    __slots__ = ("_state", "_lock")

    def __init__(self) -> None:
        self._state = self._fresh_state()
        self._lock = threading.Lock()

    @staticmethod
    def _fresh_state() -> HealthState:
        now_mono = time.monotonic()
        return HealthState(
            last_inbound_mono=now_mono,
            mono_ref=now_mono,
            wall_ref=time.time(),
            clock_jump_accum_ms=0,
        )

    @staticmethod
    def _deltas(state: HealthState) -> tuple[int, int]:
        """Monotonic + wall ms elapsed since the last probe window rolled."""
        mono_ms = int((time.monotonic() - state.mono_ref) * 1000)
        wall_ms = int((time.time() - state.wall_ref) * 1000)
        if wall_ms < 0:  # clock moved backwards -> saturating unwrap_or(0).
            wall_ms = 0
        return mono_ms, wall_ms

    @staticmethod
    def _roll(state: HealthState) -> None:
        """Roll the probe window, accumulating wall-vs-mono excess."""
        mono_ms, wall_ms = ConnHealth._deltas(state)
        excess = wall_ms - mono_ms if wall_ms > mono_ms else 0
        if excess >= CLOCK_JUMP_ACCUM_MIN_MS:
            state.clock_jump_accum_ms += excess
        state.mono_ref = time.monotonic()
        state.wall_ref = time.time()

    def record_inbound(self) -> None:
        """Record an inbound frame: roll the window, stamp last_inbound."""
        with self._lock:
            self._roll(self._state)
            self._state.last_inbound_mono = time.monotonic()

    def refresh_clock(self) -> None:
        """Roll the probe window without an inbound stamp (the 5s clock probe)."""
        with self._lock:
            self._roll(self._state)

    def snapshot(self) -> HealthSnapshot:
        """Read-only point-in-time snapshot (no window roll)."""
        with self._lock:
            state = self._state
            mono_ms, wall_ms = self._deltas(state)
            excess = wall_ms - mono_ms if wall_ms > mono_ms else 0
            accumulated = state.clock_jump_accum_ms + (
                excess if excess >= CLOCK_JUMP_ACCUM_MIN_MS else 0
            )
            jump = accumulated if accumulated >= CLOCK_JUMP_REPORT_MIN_MS else 0
            return HealthSnapshot(
                last_inbound_mono=state.last_inbound_mono,
                since_last_probe_monotonic_ms=mono_ms,
                since_last_probe_wall_ms=wall_ms,
                clock_jump_ms=jump,
            )

    def reset(self) -> None:
        """Reset to a fresh state (on reconnect)."""
        with self._lock:
            self._state = self._fresh_state()


class WriteErrorSlot:
    """Thread-safe shared optional error string (Arc<Mutex<Option<String>>>).

    The reader/actor probes this on reconnect to classify a write-side failure
    into a :class:`WriteError` disconnect cause. Wired in the connection-actor
    leaf (R151+); the type belongs to the foundation here.
    """

    __slots__ = ("_value", "_lock")

    def __init__(self) -> None:
        self._value: str | None = None
        self._lock = threading.Lock()

    def get(self) -> str | None:
        with self._lock:
            return self._value

    def set(self, value: str) -> None:
        with self._lock:
            self._value = value

    def clear(self) -> None:
        with self._lock:
            self._value = None


# ===========================================================================
# Disconnect classification (lines 168-211).
# ===========================================================================
class DisconnectCause:
    """Tag base for the 6 Rust-enum disconnect causes.

    Rust models this as a single enum with payload-carrying variants; Python
    mirrors it as an open family of frozen dataclasses dispatched via
    ``isinstance`` (or :meth:`label`). ``close_code`` / ``detail`` default to
    ``None``; payload-carrying variants override.
    """

    __slots__ = ()

    def label(self) -> str:
        """Stable lowercase tag for metrics / logging."""
        raise NotImplementedError

    def close_code(self) -> int | None:
        """The WS close code, if this was a CloseFrame."""
        return None

    def detail(self) -> str | None:
        """The transport error detail string, if Read/WriteError."""
        return None


@dataclass(frozen=True)
class CloseFrame(DisconnectCause):
    """Server-sent WS close frame (optionally carries a close code)."""

    code: int | None

    def label(self) -> str:
        return "close_frame"

    def close_code(self) -> int | None:
        return self.code


@dataclass(frozen=True)
class Eof(DisconnectCause):
    """Clean EOF on the underlying stream."""

    def label(self) -> str:
        return "eof"


@dataclass(frozen=True)
class ReadError(DisconnectCause):
    """Reader-side transport error (carries the detail string)."""

    detail_str: str

    def label(self) -> str:
        return "transport_read_error"

    def detail(self) -> str | None:
        return self.detail_str


@dataclass(frozen=True)
class WriteError(DisconnectCause):
    """Writer-side transport error (carries the detail string)."""

    detail_str: str

    def label(self) -> str:
        return "transport_write_error"

    def detail(self) -> str | None:
        return self.detail_str


@dataclass(frozen=True)
class Forced(DisconnectCause):
    """Locally forced disconnect (shutdown / pool evict)."""

    def label(self) -> str:
        return "forced"


@dataclass(frozen=True)
class LivenessDeadline(DisconnectCause):
    """No inbound frame within the liveness deadline (silently dead transport)."""

    def label(self) -> str:
        return "liveness_deadline"


@dataclass
class OutageInfo:
    """Per-outage record gathered at disconnect, fed to observability.

    Carries the cause, the prior connection's id + duration, the health
    snapshot at detection, and the detection latency -- enough to attribute
    the outage without re-reading the wire.
    """

    cause: DisconnectCause
    prev_connection_id: ConnectionId | None
    prev_connection_duration_ms: int
    last_inbound_mono: float
    detect_ms: int
    since_last_probe_monotonic_ms: int
    since_last_probe_wall_ms: int
    clock_jump_ms: int


# ===========================================================================
# Deadline call error (lines 212-225).
# ===========================================================================
class DeadlineCallError:
    """Two-variant error from a deadline-bounded call (Rust enum).

    ``impl From<DeadlineCallError> for ClientError`` becomes
    :meth:`to_client_error`.
    """

    __slots__ = ()

    def to_client_error(self) -> ClientError:
        raise NotImplementedError


@dataclass(frozen=True)
class TimedOut(DeadlineCallError):
    """The call exceeded its deadline."""

    timeout: float

    def to_client_error(self) -> ClientError:
        # Rust formats ``{timeout:?}`` -> "30s"; Python has no Duration Debug,
        # so the message carries seconds. The message text is informational.
        return NetworkError(f"request timed out after {self.timeout}s")


@dataclass(frozen=True)
class OtherError(DeadlineCallError):
    """A non-timeout ClientError surfaced from the call."""

    error: ClientError

    def to_client_error(self) -> ClientError:
        return self.error


# ===========================================================================
# Waiter RAII guard (lines 226-234).
# ===========================================================================
@contextmanager
def waiter_guard(demux: Demux, request_id: RequestId) -> Iterator[None]:
    """RAII scope guard that takes (drains) the response waiter on exit.

    Mirrors ``struct WaiterGuard<'a>`` + ``impl Drop``: the guard binds a
    ``Demux`` + ``RequestId`` pair and, on scope exit, removes the parked
    waiter so a late response cannot resolve a future the caller already
    abandoned. Use as ``with waiter_guard(demux, rid): ...``.
    """
    try:
        yield
    finally:
        demux.take_response_waiter(request_id)


# ===========================================================================
# Tuning + pool dedup + reconnect event + callbacks (lines 293-347).
# ===========================================================================
@dataclass
class ConnectionTuning:
    """Optional, default-preserving connection-tuning knobs.

    ``None`` (the default) on every knob reproduces the historical hardcoded
    behaviour; the resolve functions clamp ``None`` *or* zero to the built-in
    defaults so a degenerate override cannot reach the writer task.
    """

    ws_ping_interval: float | None = None
    ws_liveness_deadline: float | None = None
    reconnect_backoff: tuple[float, ...] | None = None


@dataclass(frozen=True)
class ConnKey:
    """Pool dedup key. Two connections pool together iff ``(url, principal)`` match."""

    url: str
    principal: PrincipalKey


@dataclass
class ReconnectEvent:
    """Reconnect-callback payload (dispatched once per successful reconnect)."""

    connection_id: ConnectionId
    sessions_replayed: int
    attempt: int


# Boxed callbacks -> plain Callables (Python needs no Send + Sync + 'static).
ReconnectCallback = Callable[[ReconnectEvent], None]
DisconnectCallback = Callable[[], None]
ConnectCallback = Callable[[], None]
