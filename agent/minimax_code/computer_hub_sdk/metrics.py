"""Feature-equivalent Prometheus metrics facade for the SDK (R147).

Fusion of grok-build's ``xai-computer-hub-sdk/src/metrics.rs`` (552 lines) --
the SDK crate's 15th leaf (after R133 error / R134 handshake / R135 refcount /
R136 donate_pump / R137 trace_donate / R138 connection_borrow / R139 auth /
R140 observability / R141 cancel / R142 admission / R143 pool / R144
notification / R145 oidc_provider / R146 metric_donate).

A *feature-gated facade*: the Rust original has two ``cfg`` branches.
``#[cfg(feature = "metrics")]`` records each call to a lazily-registered
Prometheus counter / gauge / histogram. ``#[cfg(not(feature = "metrics"))]``
(the default) compiles every helper to an empty body so the SDK carries zero
Prometheus dependency. The 36 ``pub(crate) use inner::*`` re-exports let call
sites stay ``crate::metrics::reconnect_succeeded()`` regardless of the feature.

MiniMax Code has no ``prometheus_client`` dependency (confirmed R146) and no
cargo feature gate. The facade's *value* is the **metric catalog** -- 36 named
instrumentation points across pool / reconnect / call / demux / session /
cancel / admission / inbox / notification subsystems -- plus the no-op
default. Python preserves both by swapping compile-time gating for **runtime
recorder injection**: a module-level recorder defaults to a no-op (the Rust
``not(feature = "metrics")`` equivalent) and :func:`set_recorder` swaps in a
real one (the Rust ``feature = "metrics"`` equivalent). The 36 typed wrappers
map 1:1 to the Rust functions, preserving every metric name, label arity, and
operation type, so a future recorder that binds to ``prometheus_client`` (or
the OTLP donation pump, R146) lights up all 36 points with no call-site edits.

What migrates vs what does NOT (YAGNI boundary)
-----------------------------------------------

MIGRATED (transport-agnostic facade + catalog):

* :class:`MetricsRecorder` -- the 5-method protocol
  (``inc`` / ``inc_by`` / ``dec`` / ``set_gauge`` / ``observe``) covering every
  Prometheus operation the 36 points use. Protocol, not a concrete prometheus
  type, so it has no ``prometheus_client`` dependency.
* :class:`NoopRecorder` -- the default sink; every method is a no-op (Rust
  ``not(feature = "metrics")`` equivalent -- zero overhead, zero dependency).
* :class:`InMemoryRecorder` -- a list-recording sink for tests / diagnostics.
* :func:`set_recorder` / :func:`get_recorder` / :func:`reset_recorder` -- the
  runtime injection API (Rust ``cfg(feature = "metrics")`` equivalent).
  ``set`` returns the previous recorder so a context can restore it (test
  isolation).
* 36 typed facade wrappers, each mapping 1:1 to a Rust function and carrying
  its fully-qualified Prometheus metric name + label arity in the docstring:

    - Pool / connection (4): pool_connections_inc / _dec / pool_evictions_inc
      / demux_inbox_depth_set
    - Reconnect (8): reconnect_succeeded / reconnect_failed / reconnect_cause
      / reconnect_duration_observe / reconnect_gap_observe /
      reconnect_writer_resume / liveness_deadline_expired /
      serve_replay_timeout
    - Call dispatch (4): call_dispatch_observe / call_id_collision /
      tool_call_inflight_inc / tool_call_inflight_dec
    - Harness / session (6): harness_connect (pub) / session_event /
      session_op_observe / session_soft_rebind / no_handler / hook_send
    - Hooks / progress / cancel / admission (8): progress_frame_forwarded /
      cancel_hook_received / cancel_applied / cancel_pending_tombstoned /
      cancel_no_target / tool_call_rejected_overloaded /
      admission_wait_observe / writer_sink_send_error
    - Inbox / notification (5): inbox_full_request_rejected /
      inbox_full_reject_send_failed / inbox_full_notification_dropped /
      notif_lagged_recovered / early_notif_buffered
    - Heartbeat (1): heartbeat_pong_dropped

NOT MIGRATED (framework glue with no Python equivalent):

* ``register_int_counter!`` / ``register_int_gauge!`` / ``register_histogram!``
  / ``register_*_vec!`` macro registration -- needs ``prometheus_client`` and a
  process registry. The recorder injection replaces this: a bound recorder
  owns its own registry.
* ``exponential_buckets(0.01, 2.0, 14)`` -- the histogram bucket layouts. A
  real recorder supplies them; the facade carries no bucket config.
* ``LazyLock<IntCounter>`` / ``LazyLock<Histogram>`` static singletons -- Python
  has no equivalent lazy-static registry; the module-level recorder singleton
  is the closest shape (one ``_RECORDER`` instead of 36 ``LazyLock``s).
* ``#[cfg(feature = "metrics")]`` compile-time gating -- replaced by runtime
  recorder injection (default :class:`NoopRecorder`).

Python-specific adaptations (no behavior change)
------------------------------------------------

* ``pub(crate) fn`` + ``pub use inner::harness_connect`` -> module-level
  functions (Python has no visibility-gated re-exports; ``harness_connect`` is
  simply exported in ``__all__`` alongside the crate-internal ones).
* ``IntCounter.inc()`` / ``.inc_by(n)`` -> ``recorder.inc(name)`` /
  ``recorder.inc_by(name, n)``.
* ``IntGauge.inc()`` / ``.dec()`` / ``.set(n)`` -> ``recorder.inc(name)`` /
  ``recorder.dec(name)`` / ``recorder.set_gauge(name, n)``.
* ``Histogram.observe(secs)`` -> ``recorder.observe(name, secs)``.
* ``IntCounterVec.with_label_values(&[reason]).inc()`` ->
  ``recorder.inc(name, (reason,))`` -- labels travel as a tuple parallel to
  the Rust ``&[...]`` slice; the label *names* (e.g. ``"reason"``) live in the
  wrapper's docstring (the recorder binds them when it owns a real registry).
* ``LazyLock<...>::new(register!...)`` -> the recorder owns registration
  lazily on first use (:class:`NoopRecorder` does nothing; a future
  PrometheusRecorder would lazily register on first ``inc``/``observe``).
"""

from __future__ import annotations

from typing import Protocol

__all__ = [
    # Recorder seam (the feature-gate equivalent).
    "MetricsRecorder",
    "NoopRecorder",
    "InMemoryRecorder",
    "set_recorder",
    "get_recorder",
    "reset_recorder",
    # Pool / connection.
    "pool_connections_inc",
    "pool_connections_dec",
    "pool_evictions_inc",
    "demux_inbox_depth_set",
    # Reconnect.
    "reconnect_succeeded",
    "reconnect_failed",
    "reconnect_cause",
    "reconnect_duration_observe",
    "reconnect_gap_observe",
    "reconnect_writer_resume",
    "liveness_deadline_expired",
    "serve_replay_timeout",
    # Call dispatch.
    "call_dispatch_observe",
    "call_id_collision",
    "tool_call_inflight_inc",
    "tool_call_inflight_dec",
    # Harness / session.
    "harness_connect",
    "session_event",
    "session_op_observe",
    "session_soft_rebind",
    "no_handler",
    "hook_send",
    # Hooks / progress / cancel / admission.
    "progress_frame_forwarded",
    "cancel_hook_received",
    "cancel_applied",
    "cancel_pending_tombstoned",
    "cancel_no_target",
    "tool_call_rejected_overloaded",
    "admission_wait_observe",
    "writer_sink_send_error",
    # Inbox / notification.
    "inbox_full_request_rejected",
    "inbox_full_reject_send_failed",
    "inbox_full_notification_dropped",
    "notif_lagged_recovered",
    "early_notif_buffered",
    # Heartbeat.
    "heartbeat_pong_dropped",
]


# ---------------------------------------------------------------------------
# Recorder seam.
#
# Default is NoopRecorder (Rust ``not(feature = "metrics")`` equivalent):
# every metric point is a no-op, zero dependencies, zero overhead. A caller
# that wants real recording injects via set_recorder (Rust
# ``feature = "metrics"`` equivalent).
# ---------------------------------------------------------------------------
class MetricsRecorder(Protocol):
    """The 5-method sink every SDK metric point routes through (R147).

    Covers every Prometheus operation the 36 instrumentation points use:
    ``inc`` (counter +1 / gauge +1), ``inc_by`` (counter +n), ``dec`` (gauge
    -1), ``set_gauge`` (gauge absolute), ``observe`` (histogram sample). A
    bound recorder owns its own registry and label-name binding; the facade
    passes only the fully-qualified metric name, the label *values* (parallel
    to Rust's ``&[...]`` slice), and the numeric operand.
    """

    def inc(self, name: str, labels: tuple[str, ...] = ()) -> None: ...
    def inc_by(self, name: str, amount: int, labels: tuple[str, ...] = ()) -> None: ...
    def dec(self, name: str, labels: tuple[str, ...] = ()) -> None: ...
    def set_gauge(self, name: str, value: int, labels: tuple[str, ...] = ()) -> None: ...
    def observe(self, name: str, value: float, labels: tuple[str, ...] = ()) -> None: ...


class NoopRecorder:
    """Default recorder: every metric point is a no-op (R147).

    The Python equivalent of Rust's ``#[cfg(not(feature = "metrics"))]`` -- all
    36 helpers compile to empty bodies, the SDK carries zero Prometheus
    dependency. The methods are pass-throughs so the call site pays only the
    function-call overhead.
    """

    __slots__ = ()

    def inc(self, name: str, labels: tuple[str, ...] = ()) -> None:
        ...

    def inc_by(self, name: str, amount: int, labels: tuple[str, ...] = ()) -> None:
        ...

    def dec(self, name: str, labels: tuple[str, ...] = ()) -> None:
        ...

    def set_gauge(self, name: str, value: int, labels: tuple[str, ...] = ()) -> None:
        ...

    def observe(self, name: str, value: float, labels: tuple[str, ...] = ()) -> None:
        ...


class InMemoryRecorder:
    """List-recording sink for tests / diagnostics (R147).

    Records every call as ``(op, name, value, labels)`` so a test can assert
    which points fired, with which label values, and in what order. Not
    thread-safe beyond the GIL; for diagnostics only, never production.
    """

    __slots__ = ("events",)

    def __init__(self) -> None:
        self.events: list[tuple[str, str, float, tuple[str, ...]]] = []

    def inc(self, name: str, labels: tuple[str, ...] = ()) -> None:
        self.events.append(("inc", name, 1.0, labels))

    def inc_by(self, name: str, amount: int, labels: tuple[str, ...] = ()) -> None:
        self.events.append(("inc_by", name, float(amount), labels))

    def dec(self, name: str, labels: tuple[str, ...] = ()) -> None:
        self.events.append(("dec", name, -1.0, labels))

    def set_gauge(self, name: str, value: int, labels: tuple[str, ...] = ()) -> None:
        self.events.append(("set_gauge", name, float(value), labels))

    def observe(self, name: str, value: float, labels: tuple[str, ...] = ()) -> None:
        self.events.append(("observe", name, value, labels))


# Module-level recorder singleton. Defaults to NoopRecorder; swap via
# set_recorder. Mirrors the 36 ``LazyLock`` statics collapsed to one sink.
_RECORDER: MetricsRecorder = NoopRecorder()


def get_recorder() -> MetricsRecorder:
    """Return the active recorder (default :class:`NoopRecorder`, R147)."""
    return _RECORDER


def set_recorder(recorder: MetricsRecorder) -> MetricsRecorder:
    """Install ``recorder`` as the active sink; return the previous one (R147).

    Returning the previous recorder lets a caller (typically a test) restore
    it in a ``finally`` -- the Rust ``cfg(feature = "metrics")`` equivalent
    applied at runtime. The default is :class:`NoopRecorder`, so the SDK is
    zero-dependency until a real recorder is injected.
    """
    global _RECORDER
    previous = _RECORDER
    _RECORDER = recorder
    return previous


def reset_recorder() -> None:
    """Restore the :class:`NoopRecorder` default (R147)."""
    global _RECORDER
    _RECORDER = NoopRecorder()


# ---------------------------------------------------------------------------
# Pool / connection.
# ---------------------------------------------------------------------------
def pool_connections_inc() -> None:
    """``computer_hub_client_pool_connections`` (gauge, +1) -- R147.

    Active pooled connections in the SDK connection pool.
    """
    _RECORDER.inc("computer_hub_client_pool_connections")


def pool_connections_dec() -> None:
    """``computer_hub_client_pool_connections`` (gauge, -1) -- R147."""
    _RECORDER.dec("computer_hub_client_pool_connections")


def pool_evictions_inc() -> None:
    """``computer_hub_client_pool_evictions_total`` (counter, +1) -- R147.

    Pooled connections closed by the idle reaper (unused past the idle TTL).
    """
    _RECORDER.inc("computer_hub_client_pool_evictions_total")


def demux_inbox_depth_set(depth: int) -> None:
    """``computer_hub_client_demux_inbox_depth`` (gauge, set) -- R147.

    Number of session inboxes registered in the inbound demux.
    """
    _RECORDER.set_gauge("computer_hub_client_demux_inbox_depth", depth)


# ---------------------------------------------------------------------------
# Reconnect.
# ---------------------------------------------------------------------------
def reconnect_succeeded() -> None:
    """``computer_hub_client_reconnects_total`` (counter, +1) -- R147."""
    _RECORDER.inc("computer_hub_client_reconnects_total")


def reconnect_failed(reason: str) -> None:
    """``computer_hub_client_reconnect_failed_total`` (counter, +1, label ``reason``) -- R147.

    ``reason`` is ``handshake_auth`` (fatal 401/403) or ``transport``
    (retryable).
    """
    _RECORDER.inc("computer_hub_client_reconnect_failed_total", (reason,))


def reconnect_cause(cause: str) -> None:
    """``computer_hub_client_reconnects_by_cause_total`` (counter, +1, label ``cause``) -- R147.

    Successful reconnects by disconnect cause of the previous connection
    (close_frame / eof / transport_read_error / transport_write_error /
    forced).
    """
    _RECORDER.inc("computer_hub_client_reconnects_by_cause_total", (cause,))


def reconnect_duration_observe(secs: float) -> None:
    """``computer_hub_client_reconnect_duration_seconds`` (histogram) -- R147."""
    _RECORDER.observe("computer_hub_client_reconnect_duration_seconds", secs)


def reconnect_gap_observe(secs: float) -> None:
    """``computer_hub_client_reconnect_gap_seconds`` (histogram) -- R147."""
    _RECORDER.observe("computer_hub_client_reconnect_gap_seconds", secs)


def reconnect_writer_resume() -> None:
    """``computer_hub_client_reconnect_writer_resume_total`` (counter, +1) -- R147."""
    _RECORDER.inc("computer_hub_client_reconnect_writer_resume_total")


def liveness_deadline_expired() -> None:
    """``computer_hub_client_liveness_deadline_expired_total`` (counter, +1) -- R147."""
    _RECORDER.inc("computer_hub_client_liveness_deadline_expired_total")


def serve_replay_timeout() -> None:
    """``computer_hub_client_serve_replay_timeout_total`` (counter, +1) -- R147."""
    _RECORDER.inc("computer_hub_client_serve_replay_timeout_total")


# ---------------------------------------------------------------------------
# Call dispatch.
# ---------------------------------------------------------------------------
def call_dispatch_observe(secs: float) -> None:
    """``computer_hub_client_call_dispatch_seconds`` (histogram) -- R147."""
    _RECORDER.observe("computer_hub_client_call_dispatch_seconds", secs)


def call_id_collision() -> None:
    """``computer_hub_client_call_id_collisions_total`` (counter, +1) -- R147."""
    _RECORDER.inc("computer_hub_client_call_id_collisions_total")


def tool_call_inflight_inc(scope: str) -> None:
    """``hub_tool_call_inflight`` (gauge, +1, label ``scope``) -- R147."""
    _RECORDER.inc("hub_tool_call_inflight", (scope,))


def tool_call_inflight_dec(scope: str) -> None:
    """``hub_tool_call_inflight`` (gauge, -1, label ``scope``) -- R147."""
    _RECORDER.dec("hub_tool_call_inflight", (scope,))


# ---------------------------------------------------------------------------
# Harness / session.
# ---------------------------------------------------------------------------
def harness_connect(status: str, sampler: str) -> None:
    """``hub_harness_connect_total`` (counter, +1, labels ``status``+``sampler``) -- R147.

    Hub connection attempts by outcome (``ok`` / ``error`` / ``fallback``) and
    sampler (``chat`` / ``shell``). Public so callers outside the SDK (e.g. the
    agentic sampler's ``AgentBuilder::build_harness()``) can emit
    ``status="fallback"`` when the server connection fails and the builder
    falls back to a local-only harness.
    """
    _RECORDER.inc("hub_harness_connect_total", (status, sampler))


def session_event(event_type: str) -> None:
    """``hub_session_event_total`` (counter, +1, label ``event_type``) -- R147."""
    _RECORDER.inc("hub_session_event_total", (event_type,))


def session_op_observe(op: str, status: str, secs: float) -> None:
    """``hub_session_op_duration_seconds`` (histogram, labels ``op``+``status``) -- R147.

    ``op`` is ``open`` or ``bind``; ``status`` is ``ok`` or ``error``.
    """
    _RECORDER.observe("hub_session_op_duration_seconds", secs, (op, status))


def session_soft_rebind() -> None:
    """``hub_session_soft_rebind_total`` (counter, +1) -- R147."""
    _RECORDER.inc("hub_session_soft_rebind_total")


def no_handler() -> None:
    """``hub_sdk_no_handler_total`` (counter, +1) -- R147."""
    _RECORDER.inc("hub_sdk_no_handler_total")


def hook_send(hook_type: str) -> None:
    """``hub_hook_send_total`` (counter, +1, label ``hook_type``) -- R147."""
    _RECORDER.inc("hub_hook_send_total", (hook_type,))


# ---------------------------------------------------------------------------
# Hooks / progress / cancel / admission.
# ---------------------------------------------------------------------------
def progress_frame_forwarded() -> None:
    """``hub_progress_frames_forwarded_total`` (counter, +1) -- R147."""
    _RECORDER.inc("hub_progress_frames_forwarded_total")


def cancel_hook_received() -> None:
    """``hub_cancel_hook_received_total`` (counter, +1) -- R147."""
    _RECORDER.inc("hub_cancel_hook_received_total")


def cancel_applied() -> None:
    """``hub_cancel_applied_total`` (counter, +1) -- R147."""
    _RECORDER.inc("hub_cancel_applied_total")


def cancel_pending_tombstoned() -> None:
    """``hub_cancel_pending_tombstoned_total`` (counter, +1) -- R147."""
    _RECORDER.inc("hub_cancel_pending_tombstoned_total")


def cancel_no_target() -> None:
    """``hub_cancel_no_target_total`` (counter, +1) -- R147."""
    _RECORDER.inc("hub_cancel_no_target_total")


def tool_call_rejected_overloaded() -> None:
    """``hub_tool_call_rejected_overloaded_total`` (counter, +1) -- R147."""
    _RECORDER.inc("hub_tool_call_rejected_overloaded_total")


def admission_wait_observe(secs: float) -> None:
    """``hub_tool_call_admission_wait_seconds`` (histogram) -- R147."""
    _RECORDER.observe("hub_tool_call_admission_wait_seconds", secs)


def writer_sink_send_error() -> None:
    """``computer_hub_client_writer_sink_send_errors_total`` (counter, +1) -- R147."""
    _RECORDER.inc("computer_hub_client_writer_sink_send_errors_total")


# ---------------------------------------------------------------------------
# Inbox / notification.
# ---------------------------------------------------------------------------
def inbox_full_request_rejected() -> None:
    """``hub_inbox_full_request_rejected_total`` (counter, +1) -- R147."""
    _RECORDER.inc("hub_inbox_full_request_rejected_total")


def inbox_full_reject_send_failed() -> None:
    """``hub_inbox_full_reject_send_failed_total`` (counter, +1) -- R147."""
    _RECORDER.inc("hub_inbox_full_reject_send_failed_total")


def inbox_full_notification_dropped() -> None:
    """``hub_inbox_full_notification_dropped_total`` (counter, +1) -- R147."""
    _RECORDER.inc("hub_inbox_full_notification_dropped_total")


def notif_lagged_recovered() -> None:
    """``hub_notif_lagged_recovered_total`` (counter, +1) -- R147."""
    _RECORDER.inc("hub_notif_lagged_recovered_total")


def early_notif_buffered(frames: int) -> None:
    """``hub_early_notif_buffered_total`` (counter, +n) -- R147."""
    _RECORDER.inc_by("hub_early_notif_buffered_total", frames)


# ---------------------------------------------------------------------------
# Heartbeat.
# ---------------------------------------------------------------------------
def heartbeat_pong_dropped() -> None:
    """``computer_hub_client_heartbeat_pong_dropped_total`` (counter, +1) -- R147."""
    _RECORDER.inc("computer_hub_client_heartbeat_pong_dropped_total")
