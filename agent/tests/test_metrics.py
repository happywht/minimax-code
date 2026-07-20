"""Tests for ``minimax_code.computer_hub_sdk.metrics`` (R147).

Mirrors grok-build's ``xai-computer-hub-sdk/src/metrics.rs`` (552 lines) --
the SDK crate's 15th leaf. A *feature-gated facade*: the Rust original has no
``#[test]``s (it is a facade + lazily-registered statics); the Python port
swaps compile-time ``cfg(feature)`` for runtime recorder injection, so these
tests pin the facade contract that the Rust ``cfg`` branches asserted by
construction:

* the default recorder is no-op (Rust ``not(feature = "metrics")``) -- all 36
  wrappers run without raising and without depending on ``prometheus_client``;
* an injected :class:`InMemoryRecorder` records every point with its
  fully-qualified metric name + label values + operation type -- the metric
  catalog is locked 1:1 to the Rust 36 functions;
* :func:`set_recorder` returns the previous recorder so a context can restore
  it (test isolation), and :func:`reset_recorder` restores the no-op default.

Python-specific adjustments (no behavior change):

* Rust ``LazyLock<IntCounter>`` + ``register_int_counter!`` -> a runtime
  :class:`InMemoryRecorder` injected via :func:`set_recorder`; the recorder
  owns the registry, the facade owns the catalog.
* Rust ``IntCounterVec.with_label_values(&[reason])`` -> label values travel
  as a tuple parallel to the Rust slice (``("transport",)``); the label
  *names* live in the wrapper docstrings, asserted here only via the value
  tuples.
* Rust has no default-recorder-is-noop test (it is the ``cfg(not)`` branch);
  this suite asserts the Python equivalent explicitly so a future import of
  ``prometheus_client`` cannot silently turn the default into a real recorder.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from minimax_code.computer_hub_sdk import metrics
from minimax_code.computer_hub_sdk.metrics import (
    InMemoryRecorder,
    NoopRecorder,
    set_recorder,
)


@pytest.fixture
def recorder() -> Iterator[InMemoryRecorder]:
    """Install a fresh InMemoryRecorder; restore the previous on teardown."""
    installed = InMemoryRecorder()
    previous = set_recorder(installed)
    yield installed
    set_recorder(previous)


# ---------------------------------------------------------------------------
# Default recorder is no-op: every wrapper runs without raising and without
# any prometheus_client dependency. Mirrors Rust ``#[cfg(not(feature))]``.
# ---------------------------------------------------------------------------
def test_default_recorder_is_noop_and_all_36_wrappers_run() -> None:
    metrics.reset_recorder()
    assert isinstance(metrics.get_recorder(), NoopRecorder)

    # Every one of the 36 wrappers must run without raising under the no-op.
    # Pool / connection.
    metrics.pool_connections_inc()
    metrics.pool_connections_dec()
    metrics.pool_evictions_inc()
    metrics.demux_inbox_depth_set(3)
    # Reconnect.
    metrics.reconnect_succeeded()
    metrics.reconnect_failed("transport")
    metrics.reconnect_cause("eof")
    metrics.reconnect_duration_observe(0.1)
    metrics.reconnect_gap_observe(0.2)
    metrics.reconnect_writer_resume()
    metrics.liveness_deadline_expired()
    metrics.serve_replay_timeout()
    # Call dispatch.
    metrics.call_dispatch_observe(0.001)
    metrics.call_id_collision()
    metrics.tool_call_inflight_inc("chat")
    metrics.tool_call_inflight_dec("chat")
    # Harness / session.
    metrics.harness_connect("ok", "chat")
    metrics.session_event("open")
    metrics.session_op_observe("open", "ok", 0.05)
    metrics.session_soft_rebind()
    metrics.no_handler()
    metrics.hook_send("on_cancel")
    # Hooks / progress / cancel / admission.
    metrics.progress_frame_forwarded()
    metrics.cancel_hook_received()
    metrics.cancel_applied()
    metrics.cancel_pending_tombstoned()
    metrics.cancel_no_target()
    metrics.tool_call_rejected_overloaded()
    metrics.admission_wait_observe(0.002)
    metrics.writer_sink_send_error()
    # Inbox / notification.
    metrics.inbox_full_request_rejected()
    metrics.inbox_full_reject_send_failed()
    metrics.inbox_full_notification_dropped()
    metrics.notif_lagged_recovered()
    metrics.early_notif_buffered(5)
    # Heartbeat.
    metrics.heartbeat_pong_dropped()

    # 36 calls, zero exceptions, zero prometheus_client dependency.


# ---------------------------------------------------------------------------
# Injected recorder: counter inc routes to the fully-qualified metric name.
# ---------------------------------------------------------------------------
def test_counter_inc_routes_to_fully_qualified_name(recorder: InMemoryRecorder) -> None:
    metrics.reconnect_succeeded()
    metrics.pool_evictions_inc()
    metrics.no_handler()

    names = [(op, name) for op, name, _value, _labels in recorder.events]
    assert names == [
        ("inc", "computer_hub_client_reconnects_total"),
        ("inc", "computer_hub_client_pool_evictions_total"),
        ("inc", "hub_sdk_no_handler_total"),
    ]


# ---------------------------------------------------------------------------
# Labeled counter: label values travel parallel to the Rust &[...] slice.
# ---------------------------------------------------------------------------
def test_labeled_counter_records_label_values(recorder: InMemoryRecorder) -> None:
    metrics.reconnect_failed("handshake_auth")
    metrics.harness_connect("fallback", "shell")
    metrics.tool_call_inflight_inc("chat")

    assert recorder.events == [
        ("inc", "computer_hub_client_reconnect_failed_total", 1.0, ("handshake_auth",)),
        ("inc", "hub_harness_connect_total", 1.0, ("fallback", "shell")),
        ("inc", "hub_tool_call_inflight", 1.0, ("chat",)),
    ]


# ---------------------------------------------------------------------------
# Histogram observe routes the sample value (incl. the 2-label session op).
# ---------------------------------------------------------------------------
def test_histogram_observe_routes_value(recorder: InMemoryRecorder) -> None:
    metrics.reconnect_duration_observe(0.42)
    metrics.call_dispatch_observe(0.001)
    metrics.session_op_observe("bind", "error", 0.125)

    assert recorder.events == [
        ("observe", "computer_hub_client_reconnect_duration_seconds", 0.42, ()),
        ("observe", "computer_hub_client_call_dispatch_seconds", 0.001, ()),
        ("observe", "hub_session_op_duration_seconds", 0.125, ("bind", "error")),
    ]


# ---------------------------------------------------------------------------
# Gauge set / inflight inc+dec pair / pool-connections inc+inc+dec.
# ---------------------------------------------------------------------------
def test_gauge_set_routes_value(recorder: InMemoryRecorder) -> None:
    metrics.demux_inbox_depth_set(7)

    assert recorder.events == [
        ("set_gauge", "computer_hub_client_demux_inbox_depth", 7.0, ()),
    ]


def test_tool_call_inflight_inc_dec_pair(recorder: InMemoryRecorder) -> None:
    metrics.tool_call_inflight_inc("shell")
    metrics.tool_call_inflight_dec("shell")

    assert recorder.events == [
        ("inc", "hub_tool_call_inflight", 1.0, ("shell",)),
        ("dec", "hub_tool_call_inflight", -1.0, ("shell",)),
    ]


def test_pool_connections_inc_inc_dec_pair(recorder: InMemoryRecorder) -> None:
    metrics.pool_connections_inc()
    metrics.pool_connections_inc()
    metrics.pool_connections_dec()

    assert recorder.events == [
        ("inc", "computer_hub_client_pool_connections", 1.0, ()),
        ("inc", "computer_hub_client_pool_connections", 1.0, ()),
        ("dec", "computer_hub_client_pool_connections", -1.0, ()),
    ]


# ---------------------------------------------------------------------------
# Counter inc_by routes the amount (early_notif_buffered).
# ---------------------------------------------------------------------------
def test_inc_by_routes_amount(recorder: InMemoryRecorder) -> None:
    metrics.early_notif_buffered(5)
    metrics.early_notif_buffered(3)

    assert recorder.events == [
        ("inc_by", "hub_early_notif_buffered_total", 5.0, ()),
        ("inc_by", "hub_early_notif_buffered_total", 3.0, ()),
    ]


# ---------------------------------------------------------------------------
# set_recorder returns the previous recorder; reset_recorder restores no-op.
# ---------------------------------------------------------------------------
def test_set_recorder_returns_previous_and_restores() -> None:
    original = metrics.get_recorder()
    spy = InMemoryRecorder()

    previous = set_recorder(spy)
    assert previous is original

    metrics.reconnect_succeeded()  # routes to spy, not original
    assert len(spy.events) == 1

    restored = set_recorder(previous)
    assert restored is spy
    assert metrics.get_recorder() is original


def test_reset_recorder_restores_noop_default() -> None:
    set_recorder(InMemoryRecorder())
    assert not isinstance(metrics.get_recorder(), NoopRecorder)

    metrics.reset_recorder()
    assert isinstance(metrics.get_recorder(), NoopRecorder)
