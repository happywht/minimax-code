"""Tests for ``minimax_code.computer_hub_sdk.demux`` (R149).

Mirrors grok-build's ``xai-computer-hub-sdk/src/demux.rs`` (973 lines) test
suite -- the SDK crate's 17th leaf. The 22 Rust ``#[tokio::test]`` cases pin
the inbound frame demultiplexer's routing + correlation contract; each maps
1:1 to a Python ``async def`` / ``def`` test below.

Rust test -> Python test mapping
-------------------------------

Response correlation (4):
* (1) ``response_route_matches_waiter`` ->
  :func:`test_response_route_matches_waiter`.
* (2) ``fail_calls_for_session_resolves_only_matching_call_waiters`` ->
  :func:`test_fail_calls_for_session_resolves_only_matching_call_waiters`.
* (3) ``fail_calls_for_session_is_idempotent_after_resolution`` ->
  :func:`test_fail_calls_for_session_is_idempotent_after_resolution`.
* (4) ``short_circuit_then_late_response_is_unrouted`` ->
  :func:`test_short_circuit_then_late_response_is_unrouted`.

Session routing (4):
* (5) ``session_route_pushes_to_inbox`` ->
  :func:`test_session_route_pushes_to_inbox`.
* (6) ``reverse_hook_request_routes_to_inbox_as_request`` ->
  :func:`test_reverse_hook_request_routes_to_inbox_as_request`.
* (7) ``notification_classified_without_id`` ->
  :func:`test_notification_classified_without_id`.
* (8) ``unknown_session_returns_unknown_session`` ->
  :func:`test_unknown_session_returns_unknown_session`.

Unrouted + inbox-full (7):
* (9) ``unknown_request_id_returns_unrouted`` ->
  :func:`test_unknown_request_id_returns_unrouted`.
* (10) ``full_inbox_returns_inbox_full_without_blocking`` ->
  :func:`test_full_inbox_returns_inbox_full_without_blocking`.
* (11) ``dropped_receiver_returns_session_dropped`` ->
  :func:`test_dropped_receiver_returns_session_dropped`.
* (12) ``inbox_full_request_synthesizes_overloaded_response_onto_outbound`` ->
  :func:`test_inbox_full_request_synthesizes_overloaded_response_onto_outbound`.
* (13) ``inbox_full_request_with_malformed_id_still_emits_overloaded_response`` ->
  :func:`test_inbox_full_request_with_malformed_id_still_emits_overloaded_response`.
* (14) ``inbox_full_notification_is_dropped_without_outbound_response`` ->
  :func:`test_inbox_full_notification_is_dropped_without_outbound_response`.
* (15) ``inbox_full_request_without_outbound_does_not_panic`` ->
  :func:`test_inbox_full_request_without_outbound_does_not_panic`.

Progress correlation (6):
* (16) ``progress_route_pushes_to_progress_waiter`` ->
  :func:`test_progress_route_pushes_to_progress_waiter`.
* (17) ``progress_with_no_waiter_returns_unknown_progress`` ->
  :func:`test_progress_with_no_waiter_returns_unknown_progress`.
* (18) ``dropped_progress_receiver_returns_progress_dropped`` ->
  :func:`test_dropped_progress_receiver_returns_progress_dropped`.
* (19) ``unregister_progress_waiter_returns_sender_when_present`` ->
  :func:`test_unregister_progress_waiter_returns_sender_when_present`.
* (20) ``try_register_progress_waiter_rejects_collision_and_preserves_existing`` ->
  :func:`test_try_register_progress_waiter_rejects_collision_and_preserves_existing`.
* (21) ``full_progress_channel_returns_progress_full_without_blocking`` ->
  :func:`test_full_progress_channel_returns_progress_full_without_blocking`.
* (22) ``drain_progress_removes_all_waiters_and_drops_senders`` ->
  :func:`test_drain_progress_removes_all_waiters_and_drops_senders`.

Python-specific adaptation (no behavior change):

* Rust test (6) ``reverse_hook_request_routes_to_inbox_as_request`` uses
  ``crate::harness::PERMISSION_REQUEST_KIND`` + ``HookFrame::custom_request``
  + ``Method::Hook.as_wire_str()`` -- types defined in ``harness.rs`` (a later
  leaf). The demux's classification under test ("a session-scoped frame with
  an ``id`` routes as Request") is identical for any method string, so the
  Python test substitutes a generic ``"permission_request"`` method (the
  harness-specific frame type is exercised when ``harness.rs`` is ported).
* Rust ``drop(rx)`` (receiver drop) -> :meth:`_SinkRx.close_channel`; Rust
  ``drop(tx)`` for ``drain_progress`` -> :meth:`_Sink.close` (the channel's
  ``closed`` flag is the Python drop-equivalent, since Python has no drop
  detector).
* Rust ``tokio::select!`` / ``timeout`` futures are not needed -- the mpsc
  pair's ``recv`` returns ``None`` once closed AND drained, so the tests await
  it directly.
"""

from __future__ import annotations

import asyncio
import json

from minimax_code.computer_hub_sdk.demux import (
    Demux,
    InboundFrame,
    RouteOutcome,
    mpsc_channel,
)
from minimax_code.computer_hub_sdk.error import NetworkError
from minimax_code.tool_protocol.ids import RequestId, SessionId, ToolCallId


def _future() -> asyncio.Future:
    """Create a fresh unresolved future (the oneshot equivalent, R149)."""
    return asyncio.get_running_loop().create_future()


# ===========================================================================
# Response correlation -- Rust tests 1-4.
# ===========================================================================
async def test_response_route_matches_waiter() -> None:
    # (1) A response carrying `result` resolves the parked waiter by id; the
    # decoded JsonRpcResponse is the fulfilled value.
    demux = Demux.new()
    fut = _future()
    demux.register_response_waiter(RequestId("r1"), fut)
    outcome = demux.route(
        {"jsonrpc": "2.0", "id": "r1", "result": {"outcome": "bound"}}
    )
    assert outcome == RouteOutcome.Response
    resp = await fut
    assert resp.id.to_wire() == "r1"


async def test_fail_calls_for_session_resolves_only_matching_call_waiters() -> None:
    # (2) fail_calls_for_session resolves only the session's in-flight tool.call
    # waiters; another session's call + a non-call (turn hook) waiter stay parked.
    demux = Demux.new()
    s1, s2 = SessionId("s1"), SessionId("s2")
    fa, fb, fc, fhook = _future(), _future(), _future(), _future()
    demux.register_call_response_waiter(RequestId("a"), s1, fa)
    demux.register_call_response_waiter(RequestId("b"), s1, fb)
    demux.register_call_response_waiter(RequestId("c"), s2, fc)
    demux.register_response_waiter(RequestId("hook"), fhook)

    resolved = demux.fail_calls_for_session(s1, lambda: NetworkError("gone"))
    assert resolved == 2
    # s1's calls resolved with NetworkError.
    assert fa.done() and isinstance(fa.exception(), NetworkError)
    assert fb.done() and isinstance(fb.exception(), NetworkError)
    # s2's call + the turn hook are still parked.
    assert not fc.done()
    assert not fhook.done()
    # take_response_waiter returns the parked future for c / hook, None for a/b.
    assert demux.take_response_waiter(RequestId("a")) is None
    assert demux.take_response_waiter(RequestId("b")) is None
    assert demux.take_response_waiter(RequestId("c")) is fc
    assert demux.take_response_waiter(RequestId("hook")) is fhook


async def test_fail_calls_for_session_is_idempotent_after_resolution() -> None:
    # (3) A call waiter already taken (resolved elsewhere) is not re-failed.
    demux = Demux.new()
    s1 = SessionId("s1")
    fa = _future()
    demux.register_call_response_waiter(RequestId("a"), s1, fa)
    # Simulate a prior resolution: take (and drop) the waiter.
    taken = demux.take_response_waiter(RequestId("a"))
    assert taken is fa
    # fail_calls finds nothing to resolve -> 0.
    assert demux.fail_calls_for_session(s1, lambda: NetworkError("gone")) == 0


async def test_short_circuit_then_late_response_is_unrouted() -> None:
    # (4) After fail_calls_for_session short-circuits a call, a late response
    # for the same id finds no waiter -> Unrouted (no double-resolve).
    demux = Demux.new()
    s1 = SessionId("s1")
    fa = _future()
    demux.register_call_response_waiter(RequestId("a"), s1, fa)
    assert demux.fail_calls_for_session(s1, lambda: NetworkError("gone")) == 1
    assert fa.done() and isinstance(fa.exception(), NetworkError)
    outcome = demux.route({"jsonrpc": "2.0", "id": "a", "result": {}})
    assert outcome == RouteOutcome.Unrouted


# ===========================================================================
# Session routing -- Rust tests 5-8.
# ===========================================================================
async def test_session_route_pushes_to_inbox() -> None:
    # (5) A session-scoped request lands in the inbox as InboundFrame::Request.
    demux = Demux.new()
    tx, rx = mpsc_channel(4)
    sid = SessionId("s1")
    assert demux.register_session_inbox(sid, tx) is None
    frame = {"id": "x", "session_id": "s1", "method": "tool_call_request"}
    assert demux.route(frame) == RouteOutcome.Session
    recv = await rx.recv()
    assert recv is not None
    assert recv.is_request is True
    assert recv.value == frame


async def test_reverse_hook_request_routes_to_inbox_as_request() -> None:
    # (6) Rust uses harness::PERMISSION_REQUEST_KIND + HookFrame::custom_request
    # + Method::Hook.as_wire_str() (harness.rs leaf). The demux classification
    # under test -- "a session-scoped frame with an id -> Request" -- is
    # method-agnostic, so a generic permission_request method stands in.
    demux = Demux.new()
    tx, rx = mpsc_channel(4)
    demux.register_session_inbox(SessionId("s1"), tx)
    frame = {"id": "h1", "session_id": "s1", "method": "permission_request"}
    assert demux.route(frame) == RouteOutcome.Session
    recv = await rx.recv()
    assert recv is not None
    assert recv.is_request is True


async def test_notification_classified_without_id() -> None:
    # (7) A session-scoped frame WITHOUT an id is a Notification (is_request=False).
    demux = Demux.new()
    tx, rx = mpsc_channel(4)
    demux.register_session_inbox(SessionId("s1"), tx)
    frame = {"session_id": "s1", "method": "tool_event", "params": {}}
    assert demux.route(frame) == RouteOutcome.Session
    recv = await rx.recv()
    assert recv is not None
    assert recv.is_request is False


def test_unknown_session_returns_unknown_session() -> None:
    # (8) A session-scoped frame for an unregistered session -> UnknownSession.
    demux = Demux.new()
    outcome = demux.route({"session_id": "ghost", "method": "x"})
    assert outcome == RouteOutcome.UnknownSession


# ===========================================================================
# Unrouted + inbox-full -- Rust tests 9-15.
# ===========================================================================
def test_unknown_request_id_returns_unrouted() -> None:
    # (9) A response for an unknown request id -> Unrouted (no waiter parked).
    demux = Demux.new()
    outcome = demux.route({"jsonrpc": "2.0", "id": "nope", "result": {}})
    assert outcome == RouteOutcome.Unrouted


def test_full_inbox_returns_inbox_full_without_blocking() -> None:
    # (10) A second frame into a capacity-1 inbox -> InboxFull (non-blocking).
    demux = Demux.new()
    tx, _rx = mpsc_channel(1)
    demux.register_session_inbox(SessionId("s1"), tx)
    first = demux.route({"id": "a", "session_id": "s1", "method": "tool_call_request"})
    assert first == RouteOutcome.Session
    second = demux.route({"id": "b", "session_id": "s1", "method": "tool_call_request"})
    assert second == RouteOutcome.InboxFull


async def test_dropped_receiver_returns_session_dropped() -> None:
    # (11) A dropped inbox receiver -> SessionDropped + the stale binding pruned.
    demux = Demux.new()
    tx, rx = mpsc_channel(4)
    sid = SessionId("s1")
    demux.register_session_inbox(sid, tx)
    rx.close_channel()  # drop(rx)
    outcome = demux.route({"id": "x", "session_id": "s1", "method": "tool_call_request"})
    assert outcome == RouteOutcome.SessionDropped
    assert len(demux._sessions) == 0  # stale binding removed


def test_inbox_full_request_synthesizes_overloaded_response_onto_outbound() -> None:
    # (12) A Request that overflows the inbox synthesizes the shared -32016
    # tool_busy rejection onto the outbound sink; the wire carries id /
    # session_id / error.code / error.data.{code,retryable}.
    out_tx, out_rx = mpsc_channel(8)
    demux = Demux.new().with_outbound(out_tx)
    tx, _rx = mpsc_channel(1)
    demux.register_session_inbox(SessionId("busy"), tx)
    demux.route({"id": "a", "session_id": "busy", "method": "tool_call_request"})
    outcome = demux.route({"id": "b", "session_id": "busy", "method": "tool_call_request"})
    assert outcome == RouteOutcome.InboxFull
    wire_text = out_rx.try_recv()
    assert wire_text is not None
    wire = json.loads(wire_text)
    assert wire["id"] == "b"
    assert wire["session_id"] == "busy"
    assert wire["error"]["code"] == -32016
    assert wire["error"]["data"]["code"] == "tool_busy"
    assert wire["error"]["data"]["retryable"] is True
    # Exactly one rejection emitted.
    assert out_rx.try_recv() is None


def test_inbox_full_request_with_malformed_id_still_emits_overloaded_response() -> None:
    # (13) A Request whose id fails to deserialize is echoed back as its raw
    # compact JSON text (Rust Value::to_string == json.dumps compact).
    out_tx, out_rx = mpsc_channel(8)
    demux = Demux.new().with_outbound(out_tx)
    tx, _rx = mpsc_channel(1)
    demux.register_session_inbox(SessionId("busy"), tx)
    demux.route({"id": "a", "session_id": "busy", "method": "tool_call_request"})
    outcome = demux.route(
        {"id": {"nested": 1}, "session_id": "busy", "method": "tool_call_request"}
    )
    assert outcome == RouteOutcome.InboxFull
    wire_text = out_rx.try_recv()
    assert wire_text is not None
    wire = json.loads(wire_text)
    assert wire["id"] == '{"nested":1}'  # compact JSON echo
    assert wire["error"]["code"] == -32016
    assert wire["error"]["data"]["code"] == "tool_busy"


def test_inbox_full_notification_is_dropped_without_outbound_response() -> None:
    # (14) A Notification that overflows the inbox is dropped silently -- no
    # outbound response (Notifications are fire-and-forget).
    out_tx, out_rx = mpsc_channel(8)
    demux = Demux.new().with_outbound(out_tx)
    tx, _rx = mpsc_channel(1)
    demux.register_session_inbox(SessionId("busy"), tx)
    demux.route({"id": "a", "session_id": "busy", "method": "tool_call_request"})
    outcome = demux.route({"session_id": "busy", "method": "tool_event"})
    assert outcome == RouteOutcome.InboxFull
    assert out_rx.try_recv() is None  # no outbound response


def test_inbox_full_request_without_outbound_does_not_panic() -> None:
    # (15) A bare Demux (no outbound sink) still reports InboxFull cleanly for a
    # Request -- the rejection is simply not synthesized anywhere.
    demux = Demux.new()
    tx, _rx = mpsc_channel(1)
    demux.register_session_inbox(SessionId("busy"), tx)
    demux.route({"id": "a", "session_id": "busy", "method": "tool_call_request"})
    outcome = demux.route({"id": "b", "session_id": "busy", "method": "tool_call_request"})
    assert outcome == RouteOutcome.InboxFull


# ===========================================================================
# Progress correlation -- Rust tests 16-22.
# ===========================================================================
async def test_progress_route_pushes_to_progress_waiter() -> None:
    # (16) A tool_call_progress notification routes by params.tool_call_id to
    # the call's progress stream; the decoded ToolCallProgressFrame carries
    # tool_call_id / kind / body verbatim.
    from minimax_code.tool_protocol.frames import ToolCallProgressFrame

    demux = Demux.new()
    call_id = ToolCallId("call-1")
    tx, rx = mpsc_channel(4)
    ok, rejected = demux.try_register_progress_waiter(call_id, tx)
    assert ok is True and rejected is None
    frame = {
        "method": "tool_call_progress",
        "params": {
            "tool_call_id": "call-1",
            "kind": "log_chunk",
            "body": {"text": "hello"},
        },
    }
    assert demux.route(frame) == RouteOutcome.Progress
    recv = await rx.recv()
    assert recv is not None
    assert recv.tool_call_id == "call-1"
    assert recv.kind == "log_chunk"
    assert recv.body == {"text": "hello"}


def test_progress_with_no_waiter_returns_unknown_progress() -> None:
    # (17) A progress frame with no parked waiter -> UnknownProgress.
    demux = Demux.new()
    outcome = demux.route(
        {
            "method": "tool_call_progress",
            "params": {"tool_call_id": "orphan", "kind": "chunk", "body": {}},
        }
    )
    assert outcome == RouteOutcome.UnknownProgress


async def test_dropped_progress_receiver_returns_progress_dropped() -> None:
    # (18) A dropped progress receiver -> ProgressDropped + the waiter pruned.
    from minimax_code.tool_protocol.frames import ToolCallProgressFrame

    demux = Demux.new()
    call_id = ToolCallId("call-1")
    tx, rx = mpsc_channel(4)
    demux.try_register_progress_waiter(call_id, tx)
    rx.close_channel()  # drop(rx)
    outcome = demux.route(
        {
            "method": "tool_call_progress",
            "params": {"tool_call_id": "call-1", "kind": "chunk", "body": {}},
        }
    )
    assert outcome == RouteOutcome.ProgressDropped
    assert len(demux._progress) == 0


def test_unregister_progress_waiter_returns_sender_when_present() -> None:
    # (19) unregister_progress_waiter returns the sender once, None thereafter.
    from minimax_code.tool_protocol.frames import ToolCallProgressFrame

    demux = Demux.new()
    call_id = ToolCallId("call-1")
    tx, _rx = mpsc_channel(4)
    demux.try_register_progress_waiter(call_id, tx)
    assert demux.unregister_progress_waiter(call_id) is tx
    assert demux.unregister_progress_waiter(call_id) is None


async def test_try_register_progress_waiter_rejects_collision_and_preserves_existing() -> None:
    # (20) A second registration for the same tool_call_id is rejected; the
    # existing channel is untouched (a later progress frame still routes to it).
    from minimax_code.tool_protocol.frames import ToolCallProgressFrame

    demux = Demux.new()
    call_id = ToolCallId("call-1")
    tx_first, rx_first = mpsc_channel(4)
    tx_second, _rx_second = mpsc_channel(4)
    ok1, rej1 = demux.try_register_progress_waiter(call_id, tx_first)
    assert ok1 is True and rej1 is None
    ok2, rej2 = demux.try_register_progress_waiter(call_id, tx_second)
    # Collision: rejected, handed back the rejected sender.
    assert ok2 is False and rej2 is tx_second
    # Drop the rejected sender (caller responsibility) -- no effect on the live channel.
    # The live channel still receives the progress frame.
    assert demux.route(
        {
            "method": "tool_call_progress",
            "params": {
                "tool_call_id": "call-1",
                "kind": "log_chunk",
                "body": {"text": "alive"},
            },
        }
    ) == RouteOutcome.Progress
    recv = await rx_first.recv()
    assert recv is not None
    assert recv.body == {"text": "alive"}


def test_full_progress_channel_returns_progress_full_without_blocking() -> None:
    # (21) A second progress frame into a capacity-1 channel -> ProgressFull.
    from minimax_code.tool_protocol.frames import ToolCallProgressFrame

    demux = Demux.new()
    call_id = ToolCallId("call-1")
    tx, _rx = mpsc_channel(1)
    demux.try_register_progress_waiter(call_id, tx)
    params = {"tool_call_id": "call-1", "kind": "chunk", "body": {}}
    first = demux.route({"method": "tool_call_progress", "params": params})
    assert first == RouteOutcome.Progress
    second = demux.route({"method": "tool_call_progress", "params": params})
    assert second == RouteOutcome.ProgressFull


async def test_drain_progress_removes_all_waiters_and_drops_senders() -> None:
    # (22) drain_progress drops every parked sender; each channel closes so the
    # receiver's recv returns None once drained.
    from minimax_code.tool_protocol.frames import ToolCallProgressFrame

    demux = Demux.new()
    call_a, call_b = ToolCallId("a"), ToolCallId("b")
    txa, rxa = mpsc_channel(4)
    txb, rxb = mpsc_channel(4)
    demux.try_register_progress_waiter(call_a, txa)
    demux.try_register_progress_waiter(call_b, txb)
    assert len(demux._progress) == 2
    demux.drain_progress()
    assert len(demux._progress) == 0
    # Senders dropped (closed) -> receivers recv None.
    assert await rxa.recv() is None
    assert await rxb.recv() is None
