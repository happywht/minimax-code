"""Tests for ``minimax_code.computer_hub_sdk.connection`` (R151-R152).

Mirrors the ACTOR-STRUCTURE (R151) + NETWORK-OUTBOUND (R152) halves of
grok-build's ``xai-computer-hub-sdk/src/connection.rs`` lines 348-766 -- the
SDK crate's 18th leaf (18b: the ``HubConnection`` handle +
``HubConnectionInner`` shared state + ``ConnectionConfig`` + the pure-logic
accessors; 18c: the four network-outbound methods ``send_outbound`` /
``call_request`` / ``call_request_with_timeout`` / ``call_request_with_deadline``
-- lines 671-766). The remaining network-bound surface (``connect`` /
``serve``), the ``run_writer`` / ``run_reader_actor`` / ``open_socket`` /
``run_handshake`` spawn pipeline, and the ``WriterControl<S>`` state machine
(lines 806-1382) are later leaves (R153+); this leaf defines the type +
accessors + outbound path but not the socket / framing layer, so tests build
an inner directly with real :class:`_Sink` channels and exercise the accessors
without a socket (R152 fulfils the oneshot response-waiter via a recording
demux stand-in, matching Rust's ``oneshot::channel`` with an ``asyncio.Future``).

Rust test -> Python test mapping
--------------------------------

* Rust ``impl Debug for HubConnection`` (363-369) -> the ``__repr__`` test.
* Rust ``key`` / ``kind`` / ``actor_id`` / ``demux`` (596-636) -> the
  identity-accessor tests. ``actor_id`` uses :func:`id` (Rust
  ``Arc::as_ptr as usize``).
* Rust ``connection_id`` (612, async tokio::Mutex) -> the sync slot test (the
  Python ``threading.Lock`` read needs no await under the GIL).
* Rust ``supports`` (625, ``Option<bool>`` tri-state) -> the unknown / true /
  false tests across empty vs populated ``hello_capabilities``.
* Rust ``force_reconnect`` / ``request_shutdown`` (640, 657, ``let _ =``) ->
  the signal-sent + silent-on-closed tests (verified via channel buffer size,
  since the payload is ``None`` and indistinguishable from an empty drain).
* Rust ``await_shutdown`` (644, ``CancellationToken::cancelled().await``) ->
  the async test resolving once the shutdown ``Event`` is set.
* Rust ``track_session`` / ``untrack_session`` / ``bound_session_count`` (663,
  668, 798) -> the refcount tests (distinct-key count, multi-borrow, remove-
  at-zero).
* Rust ``try_send_outbound`` (771) -> the ok / full->``BackpressureError`` /
  closed->``NetworkError`` tests.
* Rust ``send_outbound`` (748-766, ``try_send`` ok / ``Full`` -> bounded
  ``timeout(250ms, send)`` / ``Closed`` -> ``NetworkError``) -> the R152
  ok / full-recovers / full-times-out->``BackpressureError`` / closed tests.
* Rust ``call_request`` (671-682, serde + ``oneshot`` + ``WaiterGuard`` +
  outbound + ``rx.await``) -> the R152 returns / propagates-client-error /
  drains-waiter-on-send-failure tests.
* Rust ``call_request_with_deadline`` (693-746, ``timeout(t, rx)`` match ->
  ``TimedOut`` / ``OtherError``) -> the R152 success / times-out /
  wraps-send-failure tests.
* Rust ``call_request_with_timeout`` (684-691, ``DeadlineCallError`` ->
  ``ClientError``) -> the R152 wraps test.
* Rust ``try_alloc_request_id`` (789, ``fetch_add`` + ``RequestId::new``) ->
  the ``c0`` / ``c1`` / ``c2`` monotonic test.
* Rust ``impl Drop for HubConnection`` (853) -> the ``__del__`` stop-signal
  test.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import time
import weakref
from collections.abc import AsyncIterator, Callable
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from minimax_code.computer_hub_sdk.auth import AuthCredential, PrincipalKey
from minimax_code.computer_hub_sdk.connection import (
    ConnectedExit,
    ConnectionConfig,
    HubConnection,
    HubConnectionInner,
    Pause,
    Resume,
    SocketClosed,
    Stop,
    TerminalClose,
    WriterControl,
    WsBinary,
    WsClose,
    WsFrameReceived,
    WsInbound,
    WsPing,
    WsPong,
    WsRaw,
    WsReadError,
    WsText,
    _AtomicCounter,
    _ConnectionIdSlot,
    _EarlyNotifSlot,
    _HelloCaps,
    _Interval,
    _resolve_role_query,
    backoff_for,
    classify_stream_end,
    drain_reconnect_signals,
    exit_for_close_code,
    fire_on_disconnect,
    host_is_loopback,
    now_unix_millis,
    open_socket,
    reconnect_and_replay,
    route_or_pong,
    run_handshake,
    run_reader_actor,
    run_reader_phase,
    run_writer,
)
from minimax_code.computer_hub_sdk.connection_types import (
    CloseFrame,
    ConnectionTuning,
    ConnHealth,
    ConnKey,
    Eof,
    Forced,
    LivenessDeadline,
    OtherError,
    OutageInfo,
    ReadError,
    TimedOut,
    WriteError,
    WriteErrorSlot,
)
from minimax_code.computer_hub_sdk.demux import Demux, _Sink, mpsc_channel
from minimax_code.computer_hub_sdk.error import (
    BackpressureError,
    ClientError,
    Closed,
    HandshakeAuthFailed,
    InsecureScheme,
    InvalidConfig,
    NetworkError,
    ProtocolError,
    SerdeError,
)
from minimax_code.computer_hub_sdk.refcount import RefCountedSet
from minimax_code.tool_protocol.connection import ConnectionKind
from minimax_code.tool_protocol.envelope import (
    JsonRpcError,
    JsonRpcIdString,
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcVersion,
)
from minimax_code.tool_protocol.frames import ServeParams
from minimax_code.tool_protocol.handshake import HelloAckMsg
from minimax_code.tool_protocol.ids import ConnectionId, RequestId, ServerId, SessionId, ToolId
from minimax_code.tool_protocol.methods import Method

if TYPE_CHECKING:
    from minimax_code.computer_hub_sdk.connection_types import ReconnectEvent


# ===========================================================================
# Test fakes + helpers.
# ===========================================================================
class _FakeAuth:
    """Minimal stand-in for the ``AuthProvider`` Protocol.

    The accessors under test do not call into the credential, so the methods
    return inert values; the type exists only to satisfy the inner's
    ``credential`` field.
    """

    def current(self) -> None:
        return None

    def principal_key(self) -> PrincipalKey:
        return PrincipalKey(fingerprint="fp-1")

    def identity(self) -> None:
        return None


def _noop_reconnect(_event: ReconnectEvent) -> None:
    """ReconnectCallback stand-in (avoids E731 lambda assignment)."""


def _noop() -> None:
    """Nullary callback stand-in (avoids E731 lambda assignment)."""


def _make_inner(**overrides: object) -> SimpleNamespace:
    """Build a ``HubConnectionInner`` wired to fresh real ``_Sink`` channels.

    Returns a namespace carrying the inner plus every sink / sink-rx pair so a
    test can drive the channel state (close, inspect buffer size) without
    touching private demux internals.
    """
    capacity = int(overrides.pop("outbound_capacity", 8))  # type: ignore[arg-type]
    hello_caps = overrides.pop("hello_caps", None)
    early = overrides.pop("early_notif_rx", None)
    allow_insecure = overrides.pop("allow_insecure_ws", False)
    demux_override = overrides.pop("demux", None)

    outbound_tx, outbound_rx = mpsc_channel(capacity)
    stop_tx, stop_rx = mpsc_channel(8)
    reconnect_tx, reconnect_rx = mpsc_channel(8)

    kwargs: dict[str, object] = dict(
        key=ConnKey("ws://hub", PrincipalKey(fingerprint="fp-1")),
        kind=ConnectionKind.ToolServer,
        credential=_FakeAuth(),
        reconnect_backoff=(1.0, 2.0),
        outbound_tx=outbound_tx,
        demux=demux_override if demux_override is not None else Demux(),
        bound_sessions=RefCountedSet(),
        stop_tx=stop_tx,
        reconnect_tx=reconnect_tx,
        allow_insecure_ws=allow_insecure,  # type: ignore[arg-type]
    )
    if hello_caps is not None:
        kwargs["hello_capabilities"] = _HelloCaps(hello_caps)  # type: ignore[arg-type]
    if early is not None:
        kwargs["early_notif_rx"] = early

    inner = HubConnectionInner(**kwargs)  # type: ignore[arg-type]
    return SimpleNamespace(
        inner=inner,
        outbound_tx=outbound_tx,
        outbound_rx=outbound_rx,
        stop_tx=stop_tx,
        stop_rx=stop_rx,
        reconnect_tx=reconnect_tx,
        reconnect_rx=reconnect_rx,
    )


# ===========================================================================
# ConnectionConfig (lines 376-423).
# ===========================================================================
def test_connection_config_defaults() -> None:
    cfg = ConnectionConfig(
        url="ws://hub",
        credential=_FakeAuth(),
        kind=ConnectionKind.ToolServer,
    )
    assert cfg.url == "ws://hub"
    assert cfg.kind == ConnectionKind.ToolServer
    assert cfg.on_reconnect is None
    assert cfg.on_disconnect is None
    assert cfg.on_connect is None
    assert cfg.server_id is None
    assert cfg.server_description is None
    assert cfg.server_metadata is None
    assert cfg.outbound_buffer is None
    assert isinstance(cfg.tuning, ConnectionTuning)
    assert cfg.alpha_test_key is None
    assert cfg.allow_insecure_ws is False
    assert cfg.on_fatal is None


def test_connection_config_full() -> None:
    cfg = ConnectionConfig(
        url="wss://hub",
        credential=_FakeAuth(),
        kind=ConnectionKind.Harness,
        on_reconnect=_noop_reconnect,
        on_disconnect=_noop,
        on_connect=_noop,
        server_id=ServerId("srv-1"),
        server_description="a one-line description",
        server_metadata={"k": "v"},
        outbound_buffer=128,
        tuning=ConnectionTuning(ws_ping_interval=15.0),
        alpha_test_key="key",
        allow_insecure_ws=True,
    )
    assert cfg.kind == ConnectionKind.Harness
    assert cfg.server_id == ServerId("srv-1")
    assert cfg.server_metadata == {"k": "v"}
    assert cfg.outbound_buffer == 128
    assert cfg.tuning.ws_ping_interval == 15.0
    assert cfg.alpha_test_key == "key"
    assert cfg.allow_insecure_ws is True
    assert cfg.on_reconnect is _noop_reconnect


# ===========================================================================
# HubConnectionInner construction (lines 435-482).
# ===========================================================================
def test_inner_defaults_runtime_state() -> None:
    ctx = _make_inner()
    inner = ctx.inner
    assert isinstance(inner.connection_id, _ConnectionIdSlot)
    assert isinstance(inner.hello_capabilities, _HelloCaps)
    assert isinstance(inner.next_request_id, _AtomicCounter)
    assert isinstance(inner.shutdown, asyncio.Event)
    assert isinstance(inner.health, ConnHealth)
    assert inner.connection_id.get() is None
    assert inner.hello_capabilities.is_empty()
    assert inner.allow_insecure_ws is False
    assert inner.reconnect_backoff == (1.0, 2.0)


def test_inner_optional_overrides() -> None:
    health = ConnHealth()
    counter = _AtomicCounter(100)
    inner = HubConnectionInner(
        key=ConnKey("ws://x", PrincipalKey(fingerprint="fp")),
        kind=ConnectionKind.Harness,
        credential=_FakeAuth(),
        reconnect_backoff=(5.0,),
        outbound_tx=mpsc_channel(4)[0],
        demux=Demux(),
        bound_sessions=RefCountedSet(),
        stop_tx=mpsc_channel(4)[0],
        reconnect_tx=mpsc_channel(4)[0],
        health=health,
        next_request_id=counter,
        allow_insecure_ws=True,
    )
    assert inner.health is health
    assert inner.next_request_id is counter
    assert inner.allow_insecure_ws is True


# ===========================================================================
# Slot helpers (interior-mutability primitives).
# ===========================================================================
def test_atomic_counter_fetch_add_returns_previous() -> None:
    counter = _AtomicCounter()
    assert counter.fetch_add() == 0
    assert counter.fetch_add() == 1
    assert counter.fetch_add(5) == 2
    assert counter.fetch_add() == 7


def test_hello_caps_replace_snapshot_clear() -> None:
    caps = _HelloCaps()
    assert caps.is_empty()
    caps.replace(["a", "b"])
    assert not caps.is_empty()
    assert caps.contains("a")
    assert not caps.contains("c")
    assert caps.snapshot() == ["a", "b"]
    caps.clear()
    assert caps.is_empty()


def test_connection_id_slot_lifecycle() -> None:
    slot = _ConnectionIdSlot()
    assert slot.get() is None
    slot.set(ConnectionId("c1"))
    assert slot.get() == ConnectionId("c1")
    slot.clear()
    assert slot.get() is None


def test_early_notif_slot_take_set() -> None:
    slot = _EarlyNotifSlot()
    assert slot.take() is None
    queue: asyncio.Queue = asyncio.Queue()
    slot.set(queue)
    assert slot.get() is queue
    assert slot.take() is queue
    assert slot.take() is None


# ===========================================================================
# HubConnection identity accessors (lines 596-636).
# ===========================================================================
def test_key_kind_demux_and_repr() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert conn.key() == ConnKey("ws://hub", PrincipalKey(fingerprint="fp-1"))
    assert conn.kind() == ConnectionKind.ToolServer
    assert conn.demux() is ctx.inner.demux
    rendered = repr(conn)
    assert "HubConnection" in rendered
    assert "ws://hub" in rendered


def test_actor_id_stable_and_matches_inner() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert conn.actor_id() == id(ctx.inner)
    # Stable across calls (identity does not change within the instance life).
    assert conn.actor_id() == conn.actor_id()


def test_connection_id_none_then_set() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert conn.connection_id() is None
    ctx.inner.connection_id.set(ConnectionId("conn-1"))
    assert conn.connection_id() == ConnectionId("conn-1")


def test_supports_unknown_when_caps_empty() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    # Empty capability list -> None (indistinguishable from a pre-field server).
    assert conn.supports("session_attach_server") is None


def test_supports_true_false_when_caps_populated() -> None:
    ctx = _make_inner(hello_caps=["session_attach_server", "tools_changed"])
    conn = HubConnection(ctx.inner)
    assert conn.supports("session_attach_server") is True
    assert conn.supports("tools_changed") is True
    assert conn.supports("nonexistent") is False


def test_take_early_notifications_drains_once() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert conn.take_early_notifications() is None
    queue: asyncio.Queue = asyncio.Queue()
    ctx.inner.early_notif_rx.set(queue)
    assert conn.take_early_notifications() is queue
    # Drained: a second take returns None.
    assert conn.take_early_notifications() is None


# ===========================================================================
# Shutdown / reconnect signalling (lines 640-659, 853-857).
# ===========================================================================
def _buffer_size(rx: object) -> int:
    """Items pending in a ``_SinkRx``'s channel (None payload is ambiguous)."""
    return rx._channel.buffer.qsize()  # type: ignore[attr-defined]


def test_force_reconnect_enqueues_signal() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert _buffer_size(ctx.reconnect_rx) == 0
    conn.force_reconnect()
    assert _buffer_size(ctx.reconnect_rx) == 1


def test_force_reconnect_silent_on_closed_channel() -> None:
    ctx = _make_inner()
    ctx.reconnect_rx.close_channel()
    conn = HubConnection(ctx.inner)
    # Closed channel: the ``let _ =`` discard swallows _MpscClosed silently.
    conn.force_reconnect()


def test_request_shutdown_enqueues_signal() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert _buffer_size(ctx.stop_rx) == 0
    conn.request_shutdown()
    assert _buffer_size(ctx.stop_rx) == 1


async def test_await_shutdown_resolves_once_event_set() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    # Pre-set so wait() returns promptly (Event has persistent semantics).
    ctx.inner.shutdown.set()
    await asyncio.wait_for(conn.await_shutdown(), timeout=1.0)


def test_del_enqueues_stop_signal() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert _buffer_size(ctx.stop_rx) == 0
    conn.__del__()  # explicit, mirroring the Rust Drop path.
    assert _buffer_size(ctx.stop_rx) == 1


# ===========================================================================
# Session tracking (lines 663-670, 798-800).
# ===========================================================================
def test_track_untrack_session_refcount() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    s1 = SessionId("s1")
    s2 = SessionId("s2")
    assert conn.bound_session_count() == 0
    # s1 borrowed twice, s2 once -> 2 distinct keys.
    conn.track_session(s1)
    conn.track_session(s1)
    conn.track_session(s2)
    assert conn.bound_session_count() == 2
    # First untrack of s1 leaves refcount 1 -> still tracked.
    conn.untrack_session(s1)
    assert conn.bound_session_count() == 2
    # Second untrack of s1 -> removed.
    conn.untrack_session(s1)
    assert conn.bound_session_count() == 1
    conn.untrack_session(s2)
    assert conn.bound_session_count() == 0


# ===========================================================================
# Outbound + request-id allocation (lines 771-795).
# ===========================================================================
def test_try_send_outbound_ok() -> None:
    ctx = _make_inner(outbound_capacity=4)
    conn = HubConnection(ctx.inner)
    conn.try_send_outbound("frame-1")
    conn.try_send_outbound("frame-2")
    assert _buffer_size(ctx.outbound_rx) == 2


def test_try_send_outbound_full_raises_backpressure() -> None:
    ctx = _make_inner(outbound_capacity=2)
    conn = HubConnection(ctx.inner)
    conn.try_send_outbound("f1")
    conn.try_send_outbound("f2")
    with pytest.raises(BackpressureError):
        conn.try_send_outbound("f3")


def test_try_send_outbound_closed_raises_network() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    ctx.outbound_rx.close_channel()
    with pytest.raises(NetworkError):
        conn.try_send_outbound("f1")


def test_try_alloc_request_id_monotonic_c_prefix() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    assert conn.try_alloc_request_id() == RequestId("c0")
    assert conn.try_alloc_request_id() == RequestId("c1")
    assert conn.try_alloc_request_id() == RequestId("c2")


# ===========================================================================
# Network-outbound methods (R152, lines 671-766).
# ===========================================================================
class _RecordingDemux:
    """Minimal Demux stand-in exposing response-waiter register/take.

    ``call_request`` / ``call_request_with_deadline`` touch the demux only via
    ``register_response_waiter`` + ``take_response_waiter`` (the latter through
    :func:`waiter_guard`), so a real :class:`Demux` is unnecessary: this fake
    parks the future in a dict the test fulfils (``set_result`` /
    ``set_exception``) on its own schedule, exercising every await arm of the
    Rust ``rx.await`` / ``timeout(t, rx)`` translation.
    """

    def __init__(self) -> None:
        self.waiters: dict[str, asyncio.Future] = {}

    def register_response_waiter(self, request_id: str, waiter: asyncio.Future) -> None:
        self.waiters[request_id] = waiter

    def take_response_waiter(self, request_id: str) -> asyncio.Future | None:
        return self.waiters.pop(request_id, None)


def _make_request(request_id: RequestId) -> JsonRpcRequest[dict[str, object]]:
    """Minimal JSON-RPC request with the given id (avoids E731 lambda)."""
    return JsonRpcRequest(
        jsonrpc=JsonRpcVersion(),
        id=JsonRpcIdString.from_request_id(request_id),
        method="ping",
        params={},
    )


def _make_ok_response(request_id: RequestId) -> JsonRpcResponse[dict[str, object]]:
    """Minimal success response with the given id."""
    return JsonRpcResponse.ok(
        JsonRpcIdString.from_request_id(request_id),
        result={"ok": True},
    )


# --- send_outbound (lines 748-766) -----------------------------------------
async def test_send_outbound_ok() -> None:
    ctx = _make_inner(outbound_capacity=4)
    conn = HubConnection(ctx.inner)
    await conn.send_outbound("frame-1")
    assert _buffer_size(ctx.outbound_rx) == 1


async def test_send_outbound_full_recovers_after_bounded_wait() -> None:
    ctx = _make_inner(outbound_capacity=1)
    conn = HubConnection(ctx.inner)
    # Fill the single slot; the next send_outbound must await capacity.
    conn.try_send_outbound("first")
    assert _buffer_size(ctx.outbound_rx) == 1

    async def drain() -> None:
        await asyncio.sleep(0.01)  # let send_outbound park on the full buffer
        await ctx.outbound_rx.recv()

    asyncio.create_task(drain())
    await conn.send_outbound("second")  # recovers once `drain` frees a slot.
    assert _buffer_size(ctx.outbound_rx) == 1


async def test_send_outbound_full_times_out_raises_backpressure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import minimax_code.computer_hub_sdk.connection as conn_mod

    monkeypatch.setattr(conn_mod, "_OUTBOUND_BACKOFF_WAIT_S", 0.02)
    ctx = _make_inner(outbound_capacity=1)
    conn = HubConnection(ctx.inner)
    conn.try_send_outbound("first")  # fill the single slot.
    with pytest.raises(BackpressureError):
        await conn.send_outbound("second")  # no drainer -> bounded wait elapses.


async def test_send_outbound_closed_raises_network() -> None:
    ctx = _make_inner()
    conn = HubConnection(ctx.inner)
    ctx.outbound_rx.close_channel()
    with pytest.raises(NetworkError):
        await conn.send_outbound("frame")


# --- call_request (lines 671-682) ------------------------------------------
async def test_call_request_returns_response_and_drains_waiter() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")
    expected = _make_ok_response(rid)

    async def fulfiller() -> None:
        await asyncio.sleep(0.01)
        demux.waiters[rid].set_result(expected)

    asyncio.create_task(fulfiller())
    resp = await conn.call_request(rid, _make_request(rid))
    assert resp == expected
    # waiter_guard took the waiter on scope exit -> no leak.
    assert rid not in demux.waiters


async def test_call_request_propagates_client_error_from_waiter() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")

    async def fulfiller() -> None:
        await asyncio.sleep(0.01)
        demux.waiters[rid].set_exception(NetworkError("server gone"))

    asyncio.create_task(fulfiller())
    with pytest.raises(NetworkError, match="server gone"):
        await conn.call_request(rid, _make_request(rid))
    assert rid not in demux.waiters


async def test_call_request_drains_waiter_on_send_failure() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")
    ctx.outbound_rx.close_channel()  # send_outbound will raise NetworkError.
    with pytest.raises(NetworkError):
        await conn.call_request(rid, _make_request(rid))
    # waiter registered then drained by waiter_guard despite the send failure.
    assert rid not in demux.waiters


# --- call_request_with_deadline (lines 693-746) ----------------------------
async def test_call_request_with_deadline_success() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")
    expected = _make_ok_response(rid)

    async def fulfiller() -> None:
        await asyncio.sleep(0.01)
        demux.waiters[rid].set_result(expected)

    asyncio.create_task(fulfiller())
    resp = await conn._call_request_with_deadline(rid, _make_request(rid), timeout=1.0)
    assert resp == expected
    assert rid not in demux.waiters


async def test_call_request_with_deadline_times_out() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")
    # No fulfiller: the deadline elapses before the future resolves. The
    # outcome is a TimedOut value (Rust enum), not a raised exception.
    outcome = await conn._call_request_with_deadline(rid, _make_request(rid), timeout=0.02)
    assert isinstance(outcome, TimedOut)
    assert outcome.timeout == 0.02
    assert rid not in demux.waiters


async def test_call_request_with_deadline_wraps_send_failure_as_other() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")
    ctx.outbound_rx.close_channel()  # send_outbound raises NetworkError.
    outcome = await conn._call_request_with_deadline(rid, _make_request(rid), timeout=1.0)
    assert isinstance(outcome, OtherError)
    assert isinstance(outcome.error, NetworkError)
    assert rid not in demux.waiters


# --- call_request_with_timeout (lines 684-691) -----------------------------
async def test_call_request_with_timeout_wraps_timed_out_as_client_error() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")
    # No fulfiller -> TimedOut -> to_client_error -> NetworkError.
    with pytest.raises(NetworkError):
        await conn.call_request_with_timeout(rid, _make_request(rid), timeout=0.02)
    assert rid not in demux.waiters


async def test_call_request_serializes_compact_json_to_outbound() -> None:
    import json as _json

    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")

    async def fulfiller() -> None:
        await asyncio.sleep(0.01)
        demux.waiters[rid].set_result(_make_ok_response(rid))

    asyncio.create_task(fulfiller())
    await conn.call_request(rid, _make_request(rid))
    frame = await ctx.outbound_rx.recv()
    decoded = _json.loads(frame)
    assert decoded["jsonrpc"] == "2.0"
    assert decoded["id"] == "c0"
    assert decoded["method"] == "ping"
    assert decoded["params"] == {}
    # Compact separators: no whitespace after ',' or ':'.
    assert ", " not in frame
    assert ": " not in frame


# ===========================================================================
# serve (R153, lines 806-851).
# ===========================================================================
def _make_serve_params() -> ServeParams:
    """Minimal ServeParams (empty tool snapshot) for serve() exercises."""
    return ServeParams(tools=[])


def _make_serve_ok(
    request_id: RequestId, wire: dict[str, object]
) -> JsonRpcResponse[dict[str, object]]:
    """Success response carrying a ServeResult wire payload for ``request_id``."""
    return JsonRpcResponse.ok(
        JsonRpcIdString.from_request_id(request_id), result=wire
    )


async def test_serve_returns_deserialized_result() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")
    wire: dict[str, object] = {"accepted": 2, "added": ["t1", "t2"], "removed": []}

    async def fulfiller() -> None:
        await asyncio.sleep(0.01)
        demux.waiters[rid].set_result(_make_serve_ok(rid, wire))

    asyncio.create_task(fulfiller())
    result = await conn.serve(SessionId("s1"), _make_serve_params())
    assert result.accepted == 2
    assert result.added == [ToolId("t1"), ToolId("t2")]
    assert result.removed == []
    # Single attempt consumed only c0; the waiter slot is drained.
    assert rid not in demux.waiters


async def test_serve_propagates_jsonrpc_error() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")

    async def fulfiller() -> None:
        await asyncio.sleep(0.01)
        demux.waiters[rid].set_result(
            JsonRpcResponse.err(
                JsonRpcIdString.from_request_id(rid),
                JsonRpcError(code=-32001, message="serve rejected"),
            )
        )

    asyncio.create_task(fulfiller())
    with pytest.raises(ClientError, match="serve rejected"):
        await conn.serve(SessionId("s1"), _make_serve_params())
    # JSON-RPC error aborts at once: no retry budget, no reconnect.
    assert _buffer_size(ctx.reconnect_rx) == 0


async def test_serve_raises_other_error_immediately() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    # Closed outbound channel: send_outbound raises NetworkError, which
    # _call_request_with_deadline wraps as OtherError; serve surfaces it
    # immediately (Rust ``Err(Other(e)) => return Err(e)``) without retry.
    ctx.outbound_rx.close_channel()
    with pytest.raises(NetworkError):
        await conn.serve(SessionId("s1"), _make_serve_params())
    # Immediate surface: no reconnect signal enqueued.
    assert _buffer_size(ctx.reconnect_rx) == 0


async def test_serve_raises_serde_error_on_malformed_result() -> None:
    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    rid = RequestId("c0")

    async def fulfiller() -> None:
        await asyncio.sleep(0.01)
        demux.waiters[rid].set_result(
            _make_serve_ok(rid, {"accepted": "not-a-number"})
        )

    asyncio.create_task(fulfiller())
    with pytest.raises(SerdeError):
        await conn.serve(SessionId("s1"), _make_serve_params())


async def test_serve_force_reconnects_after_all_timeouts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import minimax_code.computer_hub_sdk.connection as conn_mod

    monkeypatch.setattr(conn_mod, "SERVE_ATTEMPT_TIMEOUT", 0.02)
    calls: list[int] = []

    def _count() -> None:
        calls.append(1)

    monkeypatch.setattr(conn_mod, "serve_replay_timeout", _count)

    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    # No fulfiller: every bounded attempt elapses its deadline.
    with pytest.raises(NetworkError):
        await conn.serve(SessionId("s1"), _make_serve_params())
    # Each timeout bumps the replay counter; SERVE_MAX_ATTEMPTS == 3.
    assert len(calls) == 3
    # All attempts timed out -> exactly one force_reconnect signal.
    assert _buffer_size(ctx.reconnect_rx) == 1
    # All three waiter slots (c0/c1/c2) drained by waiter_guard.
    assert demux.waiters == {}


async def test_serve_retries_then_succeeds_after_one_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import minimax_code.computer_hub_sdk.connection as conn_mod

    monkeypatch.setattr(conn_mod, "SERVE_ATTEMPT_TIMEOUT", 0.05)
    calls: list[int] = []

    def _count() -> None:
        calls.append(1)

    monkeypatch.setattr(conn_mod, "serve_replay_timeout", _count)

    demux = _RecordingDemux()
    ctx = _make_inner(demux=demux)
    conn = HubConnection(ctx.inner)
    second = RequestId("c1")
    wire: dict[str, object] = {"accepted": 1, "added": ["t9"], "removed": []}

    async def fulfiller() -> None:
        # Let c0 time out (never fulfil it); fulfil c1 once registered.
        while second not in demux.waiters:
            await asyncio.sleep(0.005)
        demux.waiters[second].set_result(_make_serve_ok(second, wire))

    asyncio.create_task(fulfiller())
    result = await conn.serve(SessionId("s1"), _make_serve_params())
    assert result.accepted == 1
    assert result.added == [ToolId("t9")]
    # First attempt timed out (1 replay), second succeeded -> no reconnect.
    assert len(calls) == 1
    assert _buffer_size(ctx.reconnect_rx) == 0


# ===========================================================================
# Steady-state control layer (R154, connection.rs 961-1036).
# ===========================================================================
# ConnectedExit / exit_for_close_code classify how the reader's steady-state
# loop terminates; WriterControl<S> is the writer flow-control signal across a
# reconnect; now_unix_millis stamps wall-clock instants. Pure-logic support for
# the spawn pipeline (run_reader_actor / run_writer, R155+); no socket here.
def test_connected_exit_variants_classify_via_isinstance() -> None:
    # The three Rust-enum variants are an open family discriminable via
    # isinstance (the Rust ``match`` equivalent).
    exits: list[ConnectedExit] = [
        Stop(),
        SocketClosed(CloseFrame(1000)),
        TerminalClose(4100),
    ]
    assert isinstance(exits[0], Stop)
    assert isinstance(exits[1], SocketClosed)
    assert isinstance(exits[2], TerminalClose)
    # The cause payload survives on the SocketClosed arm.
    assert isinstance(exits[1].cause, CloseFrame)
    assert exits[1].cause.close_code() == 1000
    # The terminal code survives on the TerminalClose arm.
    assert exits[2].code == 4100


def test_connected_exit_variants_are_frozen() -> None:
    tc = TerminalClose(4100)
    with pytest.raises(dataclasses.FrozenInstanceError):
        tc.code = 4101  # type: ignore[misc]
    sc = SocketClosed(CloseFrame(1000))
    with pytest.raises(dataclasses.FrozenInstanceError):
        sc.cause = CloseFrame(1001)  # type: ignore[misc]


def test_exit_for_close_code_terminal_band_is_terminal_close() -> None:
    # Boundaries: 4100 and 4199 are terminal; 4099 and 4200 are not.
    assert isinstance(exit_for_close_code(4100), TerminalClose)
    assert isinstance(exit_for_close_code(4199), TerminalClose)
    mid = exit_for_close_code(4150)
    assert isinstance(mid, TerminalClose)
    assert mid.code == 4150
    # Just outside the band -> SocketClosed (reconnect driver runs).
    assert isinstance(exit_for_close_code(4200), SocketClosed)
    assert isinstance(exit_for_close_code(4099), SocketClosed)


def test_exit_for_close_code_none_and_normal_route_to_reconnect() -> None:
    # None (close without a code) -> SocketClosed(CloseFrame(None)) -> reconnect.
    none_exit = exit_for_close_code(None)
    assert isinstance(none_exit, SocketClosed)
    assert none_exit.cause.close_code() is None
    # A normal 1000 close -> SocketClosed(CloseFrame(1000)) -> reconnect.
    normal = exit_for_close_code(1000)
    assert isinstance(normal, SocketClosed)
    assert normal.cause.close_code() == 1000


def test_now_unix_millis_is_monotonic_non_negative() -> None:
    first = now_unix_millis()
    second = now_unix_millis()
    # Wall clock ms since epoch: a 2026+ timestamp is well into the trillions.
    assert first > 1_700_000_000_000  # ~2023 epoch-ms floor.
    # Two successive reads are non-decreasing (wall clock can stall, not jump
    # backwards within a single tight loop under normal conditions).
    assert second >= first


def test_writer_control_variants_classify_via_isinstance() -> None:
    # Pause: payload-less signal (socket dead, stop draining).
    pause: WriterControl[str] = Pause()
    assert isinstance(pause, Pause)
    assert isinstance(pause, WriterControl)
    # Resume: carries the fresh sink (reconnected, resume draining).
    resume: WriterControl[str] = Resume(sink="ws-split-sink")
    assert isinstance(resume, Resume)
    assert isinstance(resume, WriterControl)
    # The sink payload survives on the Resume arm and is opaque to the base.
    assert resume.sink == "ws-split-sink"
    # Pause and Resume are distinct arms.
    assert not isinstance(pause, Resume)
    assert not isinstance(resume, Pause)


def test_writer_control_resume_is_frozen() -> None:
    resume = Resume(sink="sink-1")
    with pytest.raises(dataclasses.FrozenInstanceError):
        resume.sink = "sink-2"  # type: ignore[misc]


# ===========================================================================
# Spawn-pipeline pure-logic helpers (R155, SDK leaf 18f -- connection.rs
# 861-869 + 980-994 + 1015-1022 + 1080-1084).
#
# These exercise the four zero-socket helpers the spawn pipeline factors out
# (host_is_loopback / route_or_pong / classify_stream_end / fire_on_disconnect)
# without a live transport. ``route_or_pong`` needs a demux with a ``route``
# method, so a local recorder stands in -- the shared ``_RecordingDemux`` only
# exposes the response-waiter register/take surface the call-request tests
# need, and widening it would cross an iteration boundary.
# ===========================================================================
class _RouteRecorder:
    """Demux stand-in exposing only ``route`` (R155 route_or_pong tests).

    Records every routed frame so a test can assert the demux was reached (and
    with what payload) without depending on the full :class:`Demux` surface.
    """

    def __init__(self) -> None:
        self.routed: list[dict[str, object]] = []

    def route(self, frame: dict[str, object]) -> None:
        self.routed.append(frame)


def test_host_is_loopback_canonical_names_true() -> None:
    # urlparse lower-cases ``.hostname``, so the case-insensitive ``localhost``
    # match holds for any-cased input (mirrors Rust ``eq_ignore_ascii_case``).
    assert host_is_loopback("ws://127.0.0.1:8765") is True
    assert host_is_loopback("ws://[::1]:8765") is True
    assert host_is_loopback("ws://localhost:8765") is True
    assert host_is_loopback("ws://LOCALHOST:8765") is True


def test_host_is_loopback_other_hosts_false() -> None:
    assert host_is_loopback("ws://hub:8765") is False
    assert host_is_loopback("ws://example.com") is False
    assert host_is_loopback("ws://192.168.1.1:8765") is False


def test_host_is_loopback_hostless_or_unparseable_false() -> None:
    # No scheme -> urlparse sees a bare path, ``.hostname`` is None.
    assert host_is_loopback("not a url") is False
    assert host_is_loopback("") is False


def test_route_or_pong_ping_answers_with_pong_wire() -> None:
    demux = _RouteRecorder()
    inner = _make_inner(demux=demux).inner
    text = json.dumps({"method": Method.Ping.as_wire_str()})
    out = route_or_pong(inner, text)
    assert out is not None
    decoded = json.loads(out)
    assert decoded["method"] == Method.Pong.as_wire_str()
    assert isinstance(decoded["ts_ms"], int)
    # A ping is answered inline; the demux is never reached.
    assert demux.routed == []


def test_route_or_pong_other_method_routes_and_returns_none() -> None:
    demux = _RouteRecorder()
    inner = _make_inner(demux=demux).inner
    frame = {"method": "some_notification", "params": {"x": 1}}
    out = route_or_pong(inner, json.dumps(frame))
    assert out is None
    assert demux.routed == [frame]


def test_route_or_pong_non_dict_json_dropped_not_routed() -> None:
    demux = _RouteRecorder()
    inner = _make_inner(demux=demux).inner
    # A bare JSON array / string / number / null maps to Rust's terminal
    # ``Unrouted`` (Python Demux.route is dict-only, so non-dict is dropped).
    assert route_or_pong(inner, json.dumps([1, 2, 3])) is None
    assert route_or_pong(inner, json.dumps("plain string")) is None
    assert route_or_pong(inner, json.dumps(42)) is None
    assert route_or_pong(inner, "null") is None
    assert demux.routed == []


def test_route_or_pong_unparseable_text_discarded_not_routed() -> None:
    demux = _RouteRecorder()
    inner = _make_inner(demux=demux).inner
    assert route_or_pong(inner, "{not valid json") is None
    assert demux.routed == []


def test_classify_stream_end_write_error_takes_priority() -> None:
    inner = _make_inner().inner
    inner.writer_error.set("writer boom")
    cause = classify_stream_end(inner, read_error="reader boom")
    assert isinstance(cause, WriteError)
    assert cause.detail() == "writer boom"


def test_classify_stream_end_read_error_when_no_write_error() -> None:
    inner = _make_inner().inner
    cause = classify_stream_end(inner, read_error="reader boom")
    assert isinstance(cause, ReadError)
    assert cause.detail() == "reader boom"


def test_classify_stream_end_clean_eof_when_neither_error() -> None:
    inner = _make_inner().inner
    cause = classify_stream_end(inner, read_error=None)
    assert isinstance(cause, Eof)
    assert cause.detail() is None


def test_classify_stream_end_take_drains_slot_once() -> None:
    inner = _make_inner().inner
    inner.writer_error.set("first")
    first = classify_stream_end(inner, read_error=None)
    assert isinstance(first, WriteError)
    assert first.detail() == "first"
    # take() drained the slot under lock -> a fresh classification falls
    # through to Eof (the write-side failure fires at most once per reconnect).
    second = classify_stream_end(inner, read_error=None)
    assert isinstance(second, Eof)


def test_fire_on_disconnect_invokes_registered_callback() -> None:
    calls: list[str] = []

    def cb() -> None:
        calls.append("disconnected")

    inner = _make_inner().inner
    inner.on_disconnect = cb
    fire_on_disconnect(inner)
    assert calls == ["disconnected"]


def test_fire_on_disconnect_none_callback_is_noop() -> None:
    inner = _make_inner().inner  # on_disconnect defaults to None
    # Must not raise -- the reader task calls this unconditionally on exit.
    fire_on_disconnect(inner)


# ===========================================================================
# Spawn-pipeline reconnect pure-logic helpers (R156, SDK leaf 18g -- backoff_for
# 1377-1382 + drain_reconnect_signals 1243-1245).
#
# The reconnect driver (``run_reader_actor``, R157+) factors out two zero-socket
# helpers: the backoff-schedule lookup and the reconnect-signal drain. Both are
# pure logic, so they exercise without a live transport. The drain tests build
# a real ``mpsc_channel`` (unit signals) and inspect the underlying buffer
# directly, because ``_SinkRx.try_recv`` cannot tell a queued ``None`` signal
# from "empty".
# ===========================================================================
_DEFAULT_BACKOFF = (0.1, 0.2, 0.5, 1.0, 2.0, 5.0, 10.0)  # == RECONNECT_BACKOFF_MS


def test_backoff_for_follows_schedule() -> None:
    # attempt 1 -> first slot (100ms), monotonically through the schedule.
    assert backoff_for(1, _DEFAULT_BACKOFF) == 0.1
    assert backoff_for(2, _DEFAULT_BACKOFF) == 0.2
    assert backoff_for(7, _DEFAULT_BACKOFF) == 10.0


def test_backoff_for_caps_at_last_slot() -> None:
    # Past the schedule's tail the last slot (10s) is reused forever -- the
    # schedule's final value is the cap.
    assert backoff_for(8, _DEFAULT_BACKOFF) == 10.0
    assert backoff_for(1000, _DEFAULT_BACKOFF) == 10.0


def test_backoff_for_zero_attempt_uses_first_slot() -> None:
    # Rust ``u32::saturating_sub(1)`` on attempt 0 -> idx 0 -> first slot.
    assert backoff_for(0, _DEFAULT_BACKOFF) == 0.1


def test_backoff_for_honors_custom_schedule() -> None:
    custom = (0.005, 0.015)
    assert backoff_for(1, custom) == 0.005
    assert backoff_for(2, custom) == 0.015
    # attempt past the tail -> last custom slot.
    assert backoff_for(99, custom) == 0.015


def test_backoff_for_empty_schedule_is_zero_not_panic() -> None:
    # A degenerate empty tuning override collapses to no delay rather than
    # aborting the reconnect loop (Rust ``unwrap_or(Duration::ZERO)``).
    assert backoff_for(1, ()) == 0.0
    assert backoff_for(0, ()) == 0.0
    assert backoff_for(1000, ()) == 0.0


def test_drain_reconnect_signals_clears_queued() -> None:
    tx, rx = mpsc_channel(8)
    for _ in range(5):
        tx.try_send(None)
    assert rx._channel.buffer.qsize() == 5
    drain_reconnect_signals(rx)
    # Inspect the underlying buffer directly: every queued signal is ``None``,
    # so try_recv (None == empty) cannot verify the drain -- qsize can.
    assert rx._channel.buffer.qsize() == 0
    # And a fresh signal still lands after the drain.
    tx.try_send(None)
    assert rx._channel.buffer.qsize() == 1


def test_drain_reconnect_signals_empty_noop() -> None:
    tx, rx = mpsc_channel(8)
    # Draining an empty queue is a no-op (loop exits on the first QueueEmpty).
    drain_reconnect_signals(rx)
    assert rx._channel.buffer.qsize() == 0
    tx.try_send(None)
    assert rx._channel.buffer.qsize() == 1


# ===========================================================================
# Spawn-pipeline writer task tests (R157, SDK leaf 18h -- connection.rs
# 1714-1940). Mirrors the Rust ``run_writer`` in-memory-sink tests: the
# writer task is generic over the sink, so a recording sink stands in for the
# live WebSocket with no socket on the wire.
# ===========================================================================
_TEST_PING_NEVER = 3600.0


class _RecordingSink:
    """In-memory sink: records text frames, counts pings, fails on demand.

    Mirrors Rust ``RecordingSink`` (connection.rs 1632-1697) -- an
    ``UnboundedSink<Message>`` for the writer task to drain onto, with a
    ``fail`` flag that makes the next ``send_*`` raise so a send-error path
    can be exercised without a real transport. ``WriterSink`` (Protocol)
    provides exactly the two methods this class implements.
    """

    __slots__ = ("recorded", "pings", "fail")

    def __init__(self) -> None:
        self.recorded: list[str] = []
        self.pings: int = 0
        self.fail: bool = False

    async def send_text(self, text: str) -> None:
        if self.fail:
            raise OSError("sink dead")
        self.recorded.append(text)

    async def send_ping(self) -> None:
        if self.fail:
            raise OSError("sink dead")
        self.pings += 1


async def _wait_until(predicate: Callable[[], bool], label: str) -> None:
    """Poll ``predicate`` every 5ms until it is true or 2s elapse.

    Mirrors Rust ``wait_until`` (connection.rs 1704-1712): the writer task
    runs concurrently, so the test side polls an observable (recorded frames,
    ping count, error slot) until the expected state materialises rather than
    sleeping a fixed duration.
    """
    for _ in range(400):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError(f"timed out waiting for: {label}")


def _idle_write_error_slot() -> WriteErrorSlot:
    """Fresh error slot (Rust ``idle_write_error_slot``, connection.rs 1699)."""
    return WriteErrorSlot()


async def test_writer_drains_outbound_while_live() -> None:
    # A live writer drains queued text frames in arrival order.
    sink = _RecordingSink()
    out_tx, out_rx = mpsc_channel(8)
    ctl_tx, ctl_rx = mpsc_channel(8)
    stop_tx, stop_rx = mpsc_channel(8)
    writer = asyncio.ensure_future(
        run_writer(sink, out_rx, ctl_rx, stop_rx, _TEST_PING_NEVER, _idle_write_error_slot())
    )
    try:
        out_tx.try_send("a")
        out_tx.try_send("b")
        await _wait_until(lambda: len(sink.recorded) == 2, "two frames drained")
        assert sink.recorded == ["a", "b"]
        assert sink.pings == 0
    finally:
        stop_tx.try_send(None)
        await asyncio.wait_for(writer, timeout=2.0)


async def test_writer_exits_on_stop_signal() -> None:
    # A stop signal returns the writer cleanly.
    sink = _RecordingSink()
    _, out_rx = mpsc_channel(8)
    _, ctl_rx = mpsc_channel(8)
    stop_tx, stop_rx = mpsc_channel(8)
    writer = asyncio.ensure_future(
        run_writer(sink, out_rx, ctl_rx, stop_rx, _TEST_PING_NEVER, _idle_write_error_slot())
    )
    stop_tx.try_send(None)
    await asyncio.wait_for(writer, timeout=2.0)


async def test_writer_exits_when_outbound_channel_closes() -> None:
    # Dropping the last outbound sender (Rust ``drop(out_tx)``) is mirrored by
    # an explicit channel close: recv() returns None on an empty closed
    # channel, which the writer treats as a clean exit.
    sink = _RecordingSink()
    out_tx, out_rx = mpsc_channel(8)
    _, ctl_rx = mpsc_channel(8)
    _, stop_rx = mpsc_channel(8)
    writer = asyncio.ensure_future(
        run_writer(sink, out_rx, ctl_rx, stop_rx, _TEST_PING_NEVER, _idle_write_error_slot())
    )
    out_tx.close()
    await asyncio.wait_for(writer, timeout=2.0)


async def test_writer_send_error_pauses_until_resume_without_multi_frame_loss() -> None:
    # A send failure pauses the writer; queued frames are held until a fresh
    # sink is supplied via Resume, so a transient transport outage does not
    # drop multi-frame bursts. The single failing frame itself is lost (it was
    # already pulled off the channel when the send raised); the burst queued
    # behind it is preserved and flushed in order on Resume.
    sink = _RecordingSink()
    out_tx, out_rx = mpsc_channel(64)
    ctl_tx, ctl_rx = mpsc_channel(8)
    stop_tx, stop_rx = mpsc_channel(8)
    slot = _idle_write_error_slot()
    writer = asyncio.ensure_future(
        run_writer(sink, out_rx, ctl_rx, stop_rx, _TEST_PING_NEVER, slot)
    )
    try:
        out_tx.try_send("ok")
        await _wait_until(lambda: len(sink.recorded) == 1, "first frame drained")
        assert sink.recorded == ["ok"]
        # Flip the sink to failing and queue a burst; none of it lands on the
        # dead sink.
        sink.fail = True
        out_tx.try_send("lost")
        out_tx.try_send("kept1")
        out_tx.try_send("kept2")
        await _wait_until(lambda: slot.get() is not None, "write error recorded")
        assert "sink dead" in (slot.get() or "")
        await asyncio.sleep(0.05)
        assert sink.recorded == ["ok"]
        # Resume on a fresh sink; the post-failure frames flush in order.
        fresh = _RecordingSink()
        ctl_tx.try_send(Resume(fresh))
        await _wait_until(lambda: len(fresh.recorded) == 2, "two frames recovered")
        assert fresh.recorded == ["kept1", "kept2"]
    finally:
        stop_tx.try_send(None)
        await asyncio.wait_for(writer, timeout=2.0)


async def test_writer_resume_discards_stale_write_error() -> None:
    # Resume clears a stale write error so the reader does not attribute an
    # outage that was already recovered by the sink swap.
    sink = _RecordingSink()
    _, out_rx = mpsc_channel(8)
    ctl_tx, ctl_rx = mpsc_channel(8)
    stop_tx, stop_rx = mpsc_channel(8)
    slot = _idle_write_error_slot()
    writer = asyncio.ensure_future(
        run_writer(sink, out_rx, ctl_rx, stop_rx, _TEST_PING_NEVER, slot)
    )
    try:
        ctl_tx.try_send(Pause())
        await asyncio.sleep(0.02)
        slot.set("stale")
        assert slot.get() == "stale"
        ctl_tx.try_send(Resume(_RecordingSink()))
        await _wait_until(lambda: slot.get() is None, "stale error cleared on resume")
    finally:
        stop_tx.try_send(None)
        await asyncio.wait_for(writer, timeout=2.0)


# ===========================================================================
# Spawn-pipeline writer task ping/buffer/control-close tests (R158, SDK leaf
# 18h test completion -- connection.rs 1744-1845 + 1982-2000). Closes the
# run_writer coverage deferred from R157: the keepalive ping cadence, the ping
# re-arm after Resume, Pause buffering + Resume flush, and the control-channel
# close exit path. No production change -- run_writer landed in R157.
# ===========================================================================
async def test_writer_honors_custom_ping_interval() -> None:
    # A small ping_period fires send_ping on that cadence -- the writer's
    # keepalive arm is wired to asyncio.sleep(ping_period) per loop.
    sink = _RecordingSink()
    _, out_rx = mpsc_channel(4)
    _, ctl_rx = mpsc_channel(2)
    stop_tx, stop_rx = mpsc_channel(1)
    writer = asyncio.ensure_future(
        run_writer(sink, out_rx, ctl_rx, stop_rx, 0.02, _idle_write_error_slot())
    )
    try:
        await _wait_until(
            lambda: sink.pings >= 3,
            "three keepalive pings at the configured cadence",
        )
        assert sink.pings >= 3
    finally:
        stop_tx.try_send(None)
        await asyncio.wait_for(writer, timeout=2.0)


async def test_writer_re_arms_custom_ping_interval_after_resume() -> None:
    # After Pause + Resume(fresh), the keepalive cadence restarts on the fresh
    # sink -- the ping arm is rebuilt each loop, so Resume naturally re-arms it
    # without any special-case logic in run_writer.
    dead = _RecordingSink()
    _, out_rx = mpsc_channel(4)
    ctl_tx, ctl_rx = mpsc_channel(2)
    stop_tx, stop_rx = mpsc_channel(1)
    writer = asyncio.ensure_future(
        run_writer(dead, out_rx, ctl_rx, stop_rx, 0.02, _idle_write_error_slot())
    )
    try:
        ctl_tx.try_send(Pause())
        fresh = _RecordingSink()
        ctl_tx.try_send(Resume(fresh))
        await _wait_until(
            lambda: fresh.pings >= 3,
            "keepalive pings resume on the configured cadence after Resume",
        )
        assert fresh.pings >= 3
    finally:
        stop_tx.try_send(None)
        await asyncio.wait_for(writer, timeout=2.0)


async def test_writer_buffers_during_pause_and_flushes_on_resume() -> None:
    # While paused the writer holds queued frames (live=False skips the
    # outbound arm); on Resume they flush to the fresh sink, in order, and
    # nothing ever lands on the dead sink.
    dead = _RecordingSink()
    out_tx, out_rx = mpsc_channel(16)
    ctl_tx, ctl_rx = mpsc_channel(2)
    stop_tx, stop_rx = mpsc_channel(1)
    writer = asyncio.ensure_future(
        run_writer(dead, out_rx, ctl_rx, stop_rx, _TEST_PING_NEVER, _idle_write_error_slot())
    )
    try:
        ctl_tx.try_send(Pause())
        await asyncio.sleep(0.02)  # let the writer process Pause
        for frame in ("g1", "g2", "g3"):
            out_tx.try_send(frame)
        await asyncio.sleep(0.05)  # give the writer a chance to (wrongly) drain
        assert dead.recorded == [], "paused writer must not drain onto the dead sink"
        fresh = _RecordingSink()
        ctl_tx.try_send(Resume(fresh))
        await _wait_until(
            lambda: len(fresh.recorded) == 3, "buffered frames flush after resume"
        )
        assert fresh.recorded == ["g1", "g2", "g3"]
        assert dead.recorded == [], "no frame must ever reach the dead sink"
    finally:
        stop_tx.try_send(None)
        await asyncio.wait_for(writer, timeout=2.0)


async def test_writer_exits_when_control_channel_closes() -> None:
    # Dropping the last control sender (Rust ``drop(ctl_tx)``) is mirrored by
    # an explicit channel close: recv() returns None on an empty closed
    # channel, which the writer treats as a clean exit.
    sink = _RecordingSink()
    _, out_rx = mpsc_channel(4)
    ctl_tx, ctl_rx = mpsc_channel(2)
    _, stop_rx = mpsc_channel(1)
    writer = asyncio.ensure_future(
        run_writer(sink, out_rx, ctl_rx, stop_rx, _TEST_PING_NEVER, _idle_write_error_slot())
    )
    ctl_tx.close()
    await asyncio.wait_for(writer, timeout=2.0)


# ===========================================================================
# _Interval (tokio::time::interval port) tests (R159, SDK leaf 18i prep --
# run_reader_phase clock-probe timing infrastructure). _Interval exists
# because Python's asyncio.sleep is a one-shot future: recreating it every
# loop iteration resets the timeline and a clock_probe co-scheduled with a
# busy inbound stream would never fire.
# ===========================================================================
async def test_interval_first_tick_is_immediate() -> None:
    # Mirrors tokio::time::interval's first tick().await resolving at once.
    interval = _Interval(0.05)
    loop = asyncio.get_running_loop()
    t0 = loop.time()
    await interval.tick()
    assert loop.time() - t0 < 0.01


async def test_interval_subsequent_tick_waits_period() -> None:
    interval = _Interval(0.05)
    loop = asyncio.get_running_loop()
    await interval.tick()  # immediate first tick
    t0 = loop.time()
    await interval.tick()
    elapsed = loop.time() - t0
    # Widen the band: Windows' ~15.6ms timer grain can make asyncio.sleep(0.05)
    # return ~3ms early. The assertion still rejects an immediate return (the
    # regression this guards), just not platform scheduling jitter.
    assert 0.04 <= elapsed < 0.10


async def test_interval_keeps_global_timeline_across_loops() -> None:
    # The raison d'etre of _Interval: a body taking time between ticks must
    # NOT push the next tick out by that body time. Grid-aligned ticks keep
    # the gap ~= period even with a body; a naive per-iteration
    # asyncio.sleep(period) would add body on top (gap ~= period + body).
    period = 0.04
    body = 0.03
    interval = _Interval(period)
    loop = asyncio.get_running_loop()
    await interval.tick()  # immediate first tick
    stamps = [loop.time()]
    for _ in range(3):
        await asyncio.sleep(body)
        await interval.tick()
        stamps.append(loop.time())
    gaps = [stamps[i + 1] - stamps[i] for i in range(len(stamps) - 1)]
    for gap in gaps:
        # Windows timer granularity + scheduler jitter makes the tight
        # (0.8*period, period+0.5*body) window flaky under load. Keep the
        # semantics check: gaps must stay roughly one period apart and not
        # accumulate the full body time.
        assert period * 0.5 <= gap < period + body


async def test_interval_missed_tick_resets_to_now() -> None:
    # Delay semantics: if the loop is busy past the boundary, the next tick
    # resolves immediately and last_tick snaps to now (no Burst catch-up).
    interval = _Interval(0.05)
    loop = asyncio.get_running_loop()
    await interval.tick()
    await asyncio.sleep(0.2)  # miss ~4 boundaries
    t0 = loop.time()
    await interval.tick()
    elapsed = loop.time() - t0
    assert elapsed < 0.01  # immediate (missed -> reset, not another period wait)


async def test_interval_aligns_consecutive_ticks_to_grid() -> None:
    # Back-to-back ticks land ~period apart on the global grid. The period is
    # kept >= 100 ms so the Windows ~15 ms timer granularity does not wash the
    # alignment signal out (the production probe runs at CLOCK_PROBE_INTERVAL).
    interval = _Interval(0.1)
    loop = asyncio.get_running_loop()
    await interval.tick()
    stamps = [loop.time()]
    for _ in range(2):
        await interval.tick()
        stamps.append(loop.time())
    gap0 = stamps[1] - stamps[0]
    gap1 = stamps[2] - stamps[1]
    assert 0.075 <= gap0 < 0.16
    assert 0.075 <= gap1 < 0.16


# ===========================================================================
# run_reader_phase (tokio biased select! reader steady-state loop) tests (R160,
# SDK leaf 18j -- connection.rs 1259-1299). The loop is generic over the inbound
# stream, so every biased-select arm is exercised against a fake iterator with
# no live transport, mirroring run_writer's test granularity.
# ===========================================================================
_TEST_READER_DEADLINE = 0.2  # liveness window; > Windows ~15ms timer granularity.


class _ScriptedStream:
    """Async iterator yielding a fixed list then ``StopAsyncIteration``.

    An optional per-frame delay simulates real transport pacing so the liveness
    deadline's cross-round rearm can be exercised: a delay near (but under) the
    deadline exposes a failure to rearm -- without rearm the original deadline
    trips during the paced feed, while a correct rearm starts a fresh window on
    each frame and the stream ends cleanly.
    """

    def __init__(self, items: list[WsInbound], delay: float = 0.0) -> None:
        self._items = list(items)
        self._i = 0
        self._delay = delay

    def __aiter__(self) -> _ScriptedStream:
        return self

    async def __anext__(self) -> WsInbound:
        if self._i >= len(self._items):
            raise StopAsyncIteration
        if self._delay > 0:
            await asyncio.sleep(self._delay)
        item = self._items[self._i]
        self._i += 1
        return item


class _StuckStream:
    """Async iterator that never yields (a silently dead transport)."""

    def __aiter__(self) -> _StuckStream:
        return self

    async def __anext__(self) -> WsInbound:
        # Block until cancelled; the reader's deadline arm fires first and the
        # pending stream task is reaped (cancelled) on the way out.
        await asyncio.sleep(3600)
        raise StopAsyncIteration


async def test_reader_text_ping_frame_answers_with_pong_on_outbound() -> None:
    # A JSON-RPC ``ping`` text frame is answered inline: route_or_pong returns
    # the pong wire string, which the reader pushes onto outbound_tx. The stream
    # then ends cleanly -> SocketClosed(Eof).
    h = _make_inner()
    text = json.dumps({"method": Method.Ping.as_wire_str()})
    stream = _ScriptedStream([WsFrameReceived(WsText(text))])
    reader = asyncio.ensure_future(
        run_reader_phase(h.inner, stream, h.stop_rx, h.reconnect_rx, _TEST_READER_DEADLINE)
    )
    exit_val = await asyncio.wait_for(reader, timeout=2.0)
    assert isinstance(exit_val, SocketClosed)
    assert isinstance(exit_val.cause, Eof)
    pong = await asyncio.wait_for(h.outbound_rx.recv(), timeout=1.0)
    assert pong is not None
    decoded = json.loads(pong)
    assert decoded["method"] == Method.Pong.as_wire_str()
    assert isinstance(decoded["ts_ms"], int)


async def test_reader_text_other_frame_routes_to_demux_no_pong() -> None:
    # A non-ping JSON object is handed to demux.route and emits no pong.
    demux = _RouteRecorder()
    h = _make_inner(demux=demux)
    text = json.dumps({"jsonrpc": "2.0", "method": "notify", "params": {}})
    stream = _ScriptedStream([WsFrameReceived(WsText(text))])
    reader = asyncio.ensure_future(
        run_reader_phase(h.inner, stream, h.stop_rx, h.reconnect_rx, _TEST_READER_DEADLINE)
    )
    exit_val = await asyncio.wait_for(reader, timeout=2.0)
    assert isinstance(exit_val, SocketClosed)
    assert isinstance(exit_val.cause, Eof)
    assert [f["method"] for f in demux.routed] == ["notify"]
    # No pong was emitted: the outbound channel stayed empty.
    assert h.outbound_rx.try_recv() is None


async def test_reader_returns_stop_when_stop_channel_fires() -> None:
    # The stop arm is the highest-priority biased-select branch.
    h = _make_inner()
    stream = _StuckStream()
    reader = asyncio.ensure_future(
        run_reader_phase(h.inner, stream, h.stop_rx, h.reconnect_rx, _TEST_READER_DEADLINE)
    )
    h.stop_tx.try_send(None)
    exit_val = await asyncio.wait_for(reader, timeout=2.0)
    assert isinstance(exit_val, Stop)


async def test_reader_returns_forced_when_reconnect_channel_fires() -> None:
    # A reconnect signal drops the current socket (Forced): below stop in
    # priority, above any inbound stream / clock / deadline arm.
    h = _make_inner()
    stream = _StuckStream()
    reader = asyncio.ensure_future(
        run_reader_phase(h.inner, stream, h.stop_rx, h.reconnect_rx, _TEST_READER_DEADLINE)
    )
    h.reconnect_tx.try_send(None)
    exit_val = await asyncio.wait_for(reader, timeout=2.0)
    assert isinstance(exit_val, SocketClosed)
    assert isinstance(exit_val.cause, Forced)


async def test_reader_ignores_ws_ping_frame_and_continues_to_next() -> None:
    # A WS-layer ping is ignored (tungstenite auto-acks); the loop continues to
    # the next frame, which here ends the stream. Each ignored frame still
    # rearms the deadline and records inbound health; no JSON-RPC pong is sent.
    h = _make_inner()
    stream = _ScriptedStream([WsFrameReceived(WsPing()), WsFrameReceived(WsPing())])
    reader = asyncio.ensure_future(
        run_reader_phase(h.inner, stream, h.stop_rx, h.reconnect_rx, _TEST_READER_DEADLINE)
    )
    exit_val = await asyncio.wait_for(reader, timeout=2.0)
    assert isinstance(exit_val, SocketClosed)
    assert isinstance(exit_val.cause, Eof)
    assert h.outbound_rx.try_recv() is None


async def test_reader_ignores_pong_and_raw_frames() -> None:
    h = _make_inner()
    stream = _ScriptedStream([WsFrameReceived(WsPong()), WsFrameReceived(WsRaw())])
    reader = asyncio.ensure_future(
        run_reader_phase(h.inner, stream, h.stop_rx, h.reconnect_rx, _TEST_READER_DEADLINE)
    )
    exit_val = await asyncio.wait_for(reader, timeout=2.0)
    assert isinstance(exit_val, SocketClosed)
    assert isinstance(exit_val.cause, Eof)


async def test_reader_logs_and_ignores_binary_frame() -> None:
    # The protocol is text-only; a binary frame is logged and ignored.
    h = _make_inner()
    stream = _ScriptedStream([WsFrameReceived(WsBinary())])
    reader = asyncio.ensure_future(
        run_reader_phase(h.inner, stream, h.stop_rx, h.reconnect_rx, _TEST_READER_DEADLINE)
    )
    exit_val = await asyncio.wait_for(reader, timeout=2.0)
    assert isinstance(exit_val, SocketClosed)
    assert isinstance(exit_val.cause, Eof)


async def test_reader_exits_on_server_close_frame() -> None:
    # A server-initiated close frame is classified via exit_for_close_code.
    h = _make_inner()
    stream = _ScriptedStream([WsFrameReceived(WsClose(1000))])
    reader = asyncio.ensure_future(
        run_reader_phase(h.inner, stream, h.stop_rx, h.reconnect_rx, _TEST_READER_DEADLINE)
    )
    exit_val = await asyncio.wait_for(reader, timeout=2.0)
    assert exit_val == exit_for_close_code(1000)


async def test_reader_exits_on_stream_end_as_eof() -> None:
    # An empty stream -> WsStreamEnd -> classify_stream_end(inner, None); with
    # no write error in the slot, the cause is Eof.
    h = _make_inner()
    stream = _ScriptedStream([])
    reader = asyncio.ensure_future(
        run_reader_phase(h.inner, stream, h.stop_rx, h.reconnect_rx, _TEST_READER_DEADLINE)
    )
    exit_val = await asyncio.wait_for(reader, timeout=2.0)
    assert isinstance(exit_val, SocketClosed)
    assert isinstance(exit_val.cause, Eof)


async def test_reader_exits_on_read_error_as_transport_read_error() -> None:
    # A transport read error carries its detail into ReadError (the writer_error
    # slot is empty here, so the read-error detail wins).
    h = _make_inner()
    stream = _ScriptedStream([WsReadError("boom")])
    reader = asyncio.ensure_future(
        run_reader_phase(h.inner, stream, h.stop_rx, h.reconnect_rx, _TEST_READER_DEADLINE)
    )
    exit_val = await asyncio.wait_for(reader, timeout=2.0)
    assert isinstance(exit_val, SocketClosed)
    assert isinstance(exit_val.cause, ReadError)
    assert exit_val.cause.detail_str == "boom"


async def test_reader_deadline_rearmed_each_frame_does_not_trip() -> None:
    # Core correctness (Rust pin! + reset on each inbound frame): a paced feed
    # whose per-frame delay is under the liveness deadline must NOT trip the
    # deadline. Each frame rearms the window (cancel old + fresh sleep), so the
    # deadline never elapses and the stream ends cleanly as Eof. A naive
    # per-iteration rebuild (or no rearm) would let the original deadline elapse
    # mid-feed and the loop would wrongly return LivenessDeadline instead.
    # Delay/deadline picked so the no-rearm regression trips on frame 2 (deadline
    # 0.5s elapses before the 0.6s second frame) while a correct rearm keeps the
    # window ahead of every frame; both margins are >> the ~15ms timer grain.
    h = _make_inner()
    feed = [WsFrameReceived(WsPing()) for _ in range(3)]
    stream = _ScriptedStream(feed, delay=0.3)
    reader = asyncio.ensure_future(
        run_reader_phase(h.inner, stream, h.stop_rx, h.reconnect_rx, 0.5)
    )
    exit_val = await asyncio.wait_for(reader, timeout=3.0)
    assert isinstance(exit_val, SocketClosed)
    assert isinstance(exit_val.cause, Eof)  # NOT LivenessDeadline.


async def test_reader_declares_liveness_deadline_when_stream_stuck() -> None:
    # No inbound frame within the liveness window -> SocketClosed(Liveness).
    h = _make_inner()
    stream = _StuckStream()
    reader = asyncio.ensure_future(
        run_reader_phase(h.inner, stream, h.stop_rx, h.reconnect_rx, _TEST_READER_DEADLINE)
    )
    exit_val = await asyncio.wait_for(reader, timeout=2.0)
    assert isinstance(exit_val, SocketClosed)
    assert isinstance(exit_val.cause, LivenessDeadline)


# ===========================================================================
# run_reader_actor (R161, SDK leaf 18k -- connection.rs 1088-1242). The actor
# is pure orchestration: it consumes run_reader_phase's ConnectedExit and drives
# the reconnect lifecycle. The real reconnect (open a fresh socket + replay
# sessions) is a network-bound leaf (R162+), so the actor takes a ReconnectFn
# Protocol -- its entire exit dispatch, outage bookkeeping, biased
# backoff/reconnect select, writer Pause/Resume handshake, and the
# fatal-vs-transient retry state machine are unit-testable with a scripted
# reconnect_fn + a minimal fake writer, no live transport.
# ===========================================================================
class _ScriptedReconnect:
    """ReconnectFn stand-in: replays a scripted list of outcomes per call.

    Each entry is either a ``(sink, stream)`` tuple (a successful reconnect
    yielding a fresh sink + inbound stream) or a ``BaseException`` to raise (a
    failed attempt -- ``HandshakeAuthFailed`` is fatal, every other error
    including ``TimeoutError`` is transient). Records every call so a test can
    assert url / attempt-index / cumulative-backoff bookkeeping.
    """

    def __init__(self, outcomes: list[Any]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[tuple[str, int, float]] = []

    async def __call__(
        self,
        inner: HubConnectionInner,
        url: str,
        attempt: int,
        outage: OutageInfo,
        backoff_total: float,
    ) -> tuple[Any, AsyncIterator[WsInbound]]:
        self.calls.append((url, attempt, backoff_total))
        outcome = self._outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome  # type: ignore[return-value]


def _spawn_writer() -> tuple[_Sink[Any], _Sink[None], asyncio.Task[None], list[Any]]:
    """Spawn a minimal fake writer recording Pause/Resume, exiting on stop.

    Isolates ``run_reader_actor`` from the real ``run_writer`` (which needs a
    live WriterSink handle). Mirrors the writer's biased ``stop > ctl`` select
    so the actor's Pause/Resume handshake and the teardown ``writer_stop_tx`` /
    ``await writer_handle`` are exercised without a socket. ``controls`` is the
    observable list of received WriterControl signals.
    """
    ctl_tx, ctl_rx = mpsc_channel(8)
    stop_tx, stop_rx = mpsc_channel(8)
    controls: list[Any] = []

    async def fake_writer() -> None:
        while True:
            stop_task = asyncio.ensure_future(stop_rx.recv())
            ctl_task = asyncio.ensure_future(ctl_rx.recv())
            done, pending = await asyncio.wait(
                (stop_task, ctl_task),
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            if stop_task in done:
                return
            if ctl_task in done:
                ctl = ctl_task.result()
                if ctl is None:  # writer_ctl_tx.close() during teardown.
                    return
                controls.append(ctl)

    return ctl_tx, stop_tx, asyncio.ensure_future(fake_writer()), controls


class _FakePool:
    """Connection-pool stand-in recording forget_if invocations.

    Defined without ``__slots__`` so it is weakref-able -- the actor's
    ``on_fatal`` slot holds a ``weakref.ref`` to the pool (the pool <-> connection
    edge is not an ownership cycle).
    """

    def __init__(self) -> None:
        self.forget_calls: list[tuple[ConnKey, Callable[[Any], bool]]] = []

    def forget_if(self, key: ConnKey, predicate: Callable[[Any], bool]) -> bool:
        self.forget_calls.append((key, predicate))
        return True


async def test_actor_stop_exits_and_runs_cleanup() -> None:
    # The Stop exit (handle-requested shutdown) breaks to cleanup without
    # reconnecting; the finally block stops the writer, drains waiters as
    # NetworkError, and sets the shutdown event.
    h = _make_inner()
    stream = _StuckStream()
    ctl_tx, wstop_tx, writer_task, _ = _spawn_writer()
    reconnect_fn = _ScriptedReconnect([])
    actor = asyncio.ensure_future(
        run_reader_actor(
            h.inner, stream, h.stop_rx, h.reconnect_rx,
            ctl_tx, wstop_tx, writer_task,
            "ws://hub", _TEST_READER_DEADLINE, reconnect_fn,
        )
    )
    h.stop_tx.try_send(None)
    await asyncio.wait_for(actor, timeout=2.0)
    assert reconnect_fn.calls == []  # Stop exit never reconnects.
    assert writer_task.done()
    assert h.inner.shutdown.is_set()


async def test_actor_terminal_close_drains_as_closed_and_skips_reconnect() -> None:
    # A terminal close code (4100-4199) drains waiters as Closed and exits
    # WITHOUT reconnecting (the peer signalled a permanent condition).
    h = _make_inner()
    stream = _ScriptedStream([WsFrameReceived(WsClose(4101))])
    ctl_tx, wstop_tx, writer_task, _ = _spawn_writer()
    reconnect_fn = _ScriptedReconnect([])
    actor = asyncio.ensure_future(
        run_reader_actor(
            h.inner, stream, h.stop_rx, h.reconnect_rx,
            ctl_tx, wstop_tx, writer_task,
            "ws://hub", _TEST_READER_DEADLINE, reconnect_fn,
        )
    )
    await asyncio.wait_for(actor, timeout=2.0)
    assert reconnect_fn.calls == []  # terminal close never reconnects.
    assert h.inner.shutdown.is_set()


async def test_actor_socket_closed_pauses_reconnects_resumes() -> None:
    # SocketClosed -> Pause the writer -> reconnect -> Resume(fresh sink) ->
    # re-enter the reader on the new stream. The second phase returns
    # TerminalClose so the actor exits without further reconnect.
    new_sink = object()
    new_stream = _ScriptedStream([WsFrameReceived(WsClose(4101))])
    reconnect_fn = _ScriptedReconnect([(new_sink, new_stream)])
    h = _make_inner()
    h.inner.reconnect_backoff = (0.01, 0.01)
    stream = _ScriptedStream([WsReadError("boom")])  # 1st phase: SocketClosed.
    ctl_tx, wstop_tx, writer_task, controls = _spawn_writer()
    actor = asyncio.ensure_future(
        run_reader_actor(
            h.inner, stream, h.stop_rx, h.reconnect_rx,
            ctl_tx, wstop_tx, writer_task,
            "ws://hub", _TEST_READER_DEADLINE, reconnect_fn,
        )
    )
    await asyncio.wait_for(actor, timeout=2.0)
    assert reconnect_fn.calls == [("ws://hub", 1, 0.01)]
    assert any(isinstance(c, Pause) for c in controls)
    assert any(isinstance(c, Resume) and c.sink is new_sink for c in controls)
    assert h.inner.shutdown.is_set()


async def test_actor_handshake_auth_failure_evicts_pool_and_stops() -> None:
    # A HandshakeAuthFailed outcome is fatal: drain as AuthError, evict this
    # connection from the pool via on_fatal, and stop (no retry).
    pool = _FakePool()
    h = _make_inner()
    h.inner.reconnect_backoff = (0.01, 0.01)
    h.inner.on_fatal = weakref.ref(pool)
    stream = _ScriptedStream([WsReadError("boom")])  # SocketClosed -> reconnect.
    reconnect_fn = _ScriptedReconnect([HandshakeAuthFailed(401)])
    ctl_tx, wstop_tx, writer_task, _ = _spawn_writer()
    actor = asyncio.ensure_future(
        run_reader_actor(
            h.inner, stream, h.stop_rx, h.reconnect_rx,
            ctl_tx, wstop_tx, writer_task,
            "ws://hub", _TEST_READER_DEADLINE, reconnect_fn,
        )
    )
    await asyncio.wait_for(actor, timeout=2.0)
    assert reconnect_fn.calls == [("ws://hub", 1, 0.01)]
    assert len(pool.forget_calls) == 1
    key, _predicate = pool.forget_calls[0]
    assert key == h.inner.key
    assert h.inner.shutdown.is_set()


async def test_actor_transport_error_retries_then_succeeds() -> None:
    # A non-auth error (NetworkError) is transient: retry after backoff. The
    # second attempt succeeds -> Resume -> reader resumes on the new stream.
    new_sink = object()
    new_stream = _ScriptedStream([WsFrameReceived(WsClose(4101))])
    reconnect_fn = _ScriptedReconnect([NetworkError("transport down"), (new_sink, new_stream)])
    h = _make_inner()
    h.inner.reconnect_backoff = (0.01, 0.01)
    stream = _ScriptedStream([WsReadError("boom")])
    ctl_tx, wstop_tx, writer_task, controls = _spawn_writer()
    actor = asyncio.ensure_future(
        run_reader_actor(
            h.inner, stream, h.stop_rx, h.reconnect_rx,
            ctl_tx, wstop_tx, writer_task,
            "ws://hub", _TEST_READER_DEADLINE, reconnect_fn,
        )
    )
    await asyncio.wait_for(actor, timeout=3.0)
    assert [a for _url, a, _bt in reconnect_fn.calls] == [1, 2]
    assert any(isinstance(c, Resume) and c.sink is new_sink for c in controls)


async def test_actor_reconnect_timeout_treated_as_transport_error() -> None:
    # A TimeoutError raised by the reconnect attempt is transient: it is NOT
    # fatal (not a HandshakeAuthFailed), so the loop retries. The fn raises
    # TimeoutError directly rather than hanging, so the test does not wait the
    # 30s per-attempt min budget (reconnect_attempt_budget floors at 30s).
    new_sink = object()
    new_stream = _ScriptedStream([WsFrameReceived(WsClose(4101))])
    reconnect_fn = _ScriptedReconnect([TimeoutError(), (new_sink, new_stream)])
    h = _make_inner()
    h.inner.reconnect_backoff = (0.01, 0.01)
    stream = _ScriptedStream([WsReadError("boom")])
    ctl_tx, wstop_tx, writer_task, controls = _spawn_writer()
    actor = asyncio.ensure_future(
        run_reader_actor(
            h.inner, stream, h.stop_rx, h.reconnect_rx,
            ctl_tx, wstop_tx, writer_task,
            "ws://hub", _TEST_READER_DEADLINE, reconnect_fn,
        )
    )
    await asyncio.wait_for(actor, timeout=3.0)
    assert [a for _url, a, _bt in reconnect_fn.calls] == [1, 2]
    assert any(isinstance(c, Resume) and c.sink is new_sink for c in controls)


async def test_actor_stop_during_backoff_preempts_reconnect() -> None:
    # A stop signal during the inter-attempt backoff sleep preempts the
    # reconnect loop (biased stop > backoff-sleep) -- reconnect_fn is never
    # called. A long backoff widens the preemption window.
    h = _make_inner()
    h.inner.reconnect_backoff = (0.5, 0.5)
    stream = _ScriptedStream([WsReadError("boom")])  # SocketClosed -> backoff.
    reconnect_fn = _ScriptedReconnect([])  # must NOT be called.
    ctl_tx, wstop_tx, writer_task, controls = _spawn_writer()
    actor = asyncio.ensure_future(
        run_reader_actor(
            h.inner, stream, h.stop_rx, h.reconnect_rx,
            ctl_tx, wstop_tx, writer_task,
            "ws://hub", _TEST_READER_DEADLINE, reconnect_fn,
        )
    )
    # Wait until the Pause handshake lands -> the actor has entered the
    # reconnect loop's backoff sleep.
    await _wait_until(
        lambda: any(isinstance(c, Pause) for c in controls), "writer paused"
    )
    h.stop_tx.try_send(None)
    await asyncio.wait_for(actor, timeout=2.0)
    assert reconnect_fn.calls == []  # pre-empted before the reconnect attempt.
    assert h.inner.shutdown.is_set()


async def test_actor_stop_during_reconnect_attempt_preempts() -> None:
    # A stop signal during an in-flight reconnect attempt preempts it (biased
    # stop > reconnect attempt) even though the 30s min attempt budget has not
    # elapsed. The hanging reconnect_fn is cancelled via the biased select.
    h = _make_inner()
    h.inner.reconnect_backoff = (0.01, 0.01)
    stream = _ScriptedStream([WsReadError("boom")])
    entered = asyncio.Event()

    async def hang_reconnect(
        inner: HubConnectionInner,
        url: str,
        attempt: int,
        outage: OutageInfo,
        backoff_total: float,
    ) -> tuple[Any, AsyncIterator[WsInbound]]:
        entered.set()
        await asyncio.sleep(3600)  # stop preempts well before the 30s budget.
        raise AssertionError("unreachable")  # pragma: no cover

    ctl_tx, wstop_tx, writer_task, _ = _spawn_writer()
    actor = asyncio.ensure_future(
        run_reader_actor(
            h.inner, stream, h.stop_rx, h.reconnect_rx,
            ctl_tx, wstop_tx, writer_task,
            "ws://hub", _TEST_READER_DEADLINE, hang_reconnect,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    h.stop_tx.try_send(None)
    await asyncio.wait_for(actor, timeout=2.0)
    assert h.inner.shutdown.is_set()


async def test_actor_writer_pause_channel_closed_breaks_actor() -> None:
    # If the writer-control channel is closed when the actor tries to send the
    # SocketClosed Pause, _send_writer_ctl returns False and the actor breaks
    # to cleanup without entering the reconnect loop.
    h = _make_inner()
    stream = _ScriptedStream([WsReadError("boom")])  # SocketClosed -> Pause.
    ctl_tx, wstop_tx, writer_task, _ = _spawn_writer()
    reconnect_fn = _ScriptedReconnect([])  # must NOT be called.
    ctl_tx.close()  # pre-close -> the Pause send fails -> break.
    actor = asyncio.ensure_future(
        run_reader_actor(
            h.inner, stream, h.stop_rx, h.reconnect_rx,
            ctl_tx, wstop_tx, writer_task,
            "ws://hub", _TEST_READER_DEADLINE, reconnect_fn,
        )
    )
    await asyncio.wait_for(actor, timeout=2.0)
    assert reconnect_fn.calls == []
    assert h.inner.shutdown.is_set()
    assert writer_task.done()


# ===========================================================================
# open_socket + _resolve_role_query network primitives (R162, SDK leaf 19a).
# ===========================================================================
# Forward-port of connection.rs:877-931. ``open_socket`` is the socket-opening
# orchestrator: plaintext-safety gate -> role-query normalisation -> header
# injection -> dependency-injected dialer. The raw ``connect_async`` is injected
# via :class:`WebSocketDial` (a Protocol), so the whole leaf is unit-tested
# without a live transport -- a scripted ``_RecordingDial`` records the
# (url, headers) the orchestrator built and returns a sentinel stream, or
# raises an HTTP-reject-shaped exception to exercise the error classifier.
# ``host_is_loopback`` (861-869) is reused from R155 and already tested there
# (lines 995-1014); these tests exercise the orchestrator that builds on it.
class _RecordingDial:
    """Scripted ``WebSocketDial`` stand-in (R162).

    Records every ``(url, headers)`` pair the orchestrator dialled with and
    returns ``result`` (a sentinel stream). If ``exc`` is set it is raised
    instead, shaped like an HTTP-reject so
    :meth:`ClientError.from_handshake_error` can classify it (a ``status``
    attribute of 401/403 -> ``HandshakeAuthFailed``; otherwise ``NetworkError``).
    """

    def __init__(self, *, result: Any = None, exc: BaseException | None = None) -> None:
        self.result = result if result is not None else SimpleNamespace(name="ws-stream")
        self.exc = exc
        self.calls: list[tuple[str, list[tuple[str, str]]]] = []

    async def __call__(self, url: str, headers: list[tuple[str, str]]) -> Any:
        self.calls.append((url, list(headers)))
        if self.exc is not None:
            raise self.exc
        return self.result


class _HttpReject(Exception):
    """Exception carrying an HTTP status (the ``.status`` dialer contract)."""

    def __init__(self, status: int, message: str = "") -> None:
        super().__init__(message)
        self.status = status


# -- _resolve_role_query (connection.rs:899-913) ----------------------------
def test_resolve_role_query_appends_role_when_absent() -> None:
    # No role query -> role=<kind.value> appended; host / port / path preserved.
    out = _resolve_role_query("ws://hub:8080/path", ConnectionKind.Harness)
    assert out == "ws://hub:8080/path?role=harness"


def test_resolve_role_query_preserves_matching_role() -> None:
    # A pre-existing role that agrees with kind is left untouched (idempotent).
    out = _resolve_role_query("ws://hub?role=tool_server", ConnectionKind.ToolServer)
    assert out == "ws://hub?role=tool_server"


def test_resolve_role_query_conflict_raises_invalid_config() -> None:
    # A pre-existing role that disagrees with kind is a hard config error --
    # a harness URL cannot be opened as a tool-server and vice versa.
    with pytest.raises(InvalidConfig):
        _resolve_role_query("ws://hub?role=harness", ConnectionKind.ToolServer)


def test_resolve_role_query_preserves_other_params_and_fragment() -> None:
    # Other query pairs (order kept) and the fragment survive the append.
    out = _resolve_role_query("ws://hub:8080/p?a=1&b=2#frag", ConnectionKind.Harness)
    assert out == "ws://hub:8080/p?a=1&b=2&role=harness#frag"


def test_resolve_role_query_empty_role_observed_and_conflicts() -> None:
    # ``role=`` (empty value) is observed verbatim -- the parse_qsl (NOT
    # parse_qs) gate: parse_qs would drop the blank value and silently append a
    # fresh role, masking a misconfigured URL. The empty string disagrees with
    # kind.value, so it must raise.
    with pytest.raises(InvalidConfig):
        _resolve_role_query("ws://hub?role=", ConnectionKind.Harness)


# -- open_socket plaintext-safety gate (connection.rs:877-889) --------------
async def test_open_socket_plaintext_remote_refused() -> None:
    # ws:// to a non-loopback host without allow_insecure_ws -> InsecureScheme
    # (the bearer would cross the network in cleartext). Dial is never called.
    dial = _RecordingDial()
    with pytest.raises(InsecureScheme) as exc_info:
        await open_socket(
            "ws://hub.example.com",
            AuthCredential.bearer("tok"),
            ConnectionKind.Harness,
            None,
            False,
            dial,
        )
    assert exc_info.value.url == "ws://hub.example.com"
    assert dial.calls == []


async def test_open_socket_plaintext_loopback_exempt() -> None:
    # ws:// to loopback (127.0.0.1) is the dev / local-proxy exception: no
    # InsecureScheme, the dialer runs and its stream is returned verbatim.
    dial = _RecordingDial()
    ws = await open_socket(
        "ws://127.0.0.1:8080",
        AuthCredential.bearer("tok"),
        ConnectionKind.Harness,
        None,
        False,
        dial,
    )
    assert ws is dial.result
    assert len(dial.calls) == 1


async def test_open_socket_plaintext_remote_allow_insecure_warns_then_dials() -> None:
    # With allow_insecure_ws=true the plaintext gate is downgraded to a warning
    # and the dialer runs against the remote host.
    dial = _RecordingDial()
    ws = await open_socket(
        "ws://hub.example.com",
        AuthCredential.bearer("tok"),
        ConnectionKind.Harness,
        None,
        True,
        dial,
    )
    assert ws is dial.result
    assert len(dial.calls) == 1


async def test_open_socket_wss_remote_is_not_plaintext() -> None:
    # wss:// (TLS) is never plaintext, regardless of host -- the gate is a
    # no-op and the dialer runs without allow_insecure_ws.
    dial = _RecordingDial()
    await open_socket(
        "wss://hub.example.com",
        AuthCredential.bearer("tok"),
        ConnectionKind.Harness,
        None,
        False,
        dial,
    )
    assert len(dial.calls) == 1


# -- open_socket role-query + header injection (connection.rs:899-924) ------
async def test_open_socket_injects_role_query_for_harness() -> None:
    # The dialer receives the role-normalised URL (role=harness appended).
    dial = _RecordingDial()
    await open_socket(
        "wss://hub",
        AuthCredential.bearer("tok"),
        ConnectionKind.Harness,
        None,
        False,
        dial,
    )
    assert dial.calls[0][0] == "wss://hub?role=harness"


async def test_open_socket_preserves_matching_role_query() -> None:
    # A pre-existing matching role is preserved verbatim (no double append).
    dial = _RecordingDial()
    await open_socket(
        "wss://hub?role=tool_server",
        AuthCredential.bearer("tok"),
        ConnectionKind.ToolServer,
        None,
        False,
        dial,
    )
    assert dial.calls[0][0] == "wss://hub?role=tool_server"


async def test_open_socket_conflicting_role_query_raises() -> None:
    # Role mismatch surfaces before the dialer is reached; dial never called.
    dial = _RecordingDial()
    with pytest.raises(InvalidConfig):
        await open_socket(
            "wss://hub?role=harness",
            AuthCredential.bearer("tok"),
            ConnectionKind.ToolServer,
            None,
            False,
            dial,
        )
    assert dial.calls == []


async def test_open_socket_injects_bearer_upgrade_header() -> None:
    # BearerCredential.upgrade_headers -> ("authorization", "Bearer <tok>")
    # reaches the dialer's header bundle.
    dial = _RecordingDial()
    await open_socket(
        "wss://hub",
        AuthCredential.bearer("secret-token"),
        ConnectionKind.Harness,
        None,
        False,
        dial,
    )
    headers = dict(dial.calls[0][1])
    assert headers["authorization"] == "Bearer secret-token"


async def test_open_socket_attaches_traceparent_to_header_bundle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # attach_trace_to_http_request (R131) is called on the SAME mutable header
    # mapping the dialer later receives, so an active traceparent rides the
    # upgrade. Spy on the bound name in the connection module to verify the
    # handoff without depending on a live span context.
    seen: dict[str, object] = {}

    def fake_attach(headers: dict[str, str]) -> bool:
        seen["called"] = True
        seen["is_dict"] = isinstance(headers, dict)
        # R131 contract: writes the W3C traceparent into the mapping it got.
        headers["traceparent"] = "00-..-..-01"
        return True

    monkeypatch.setattr(
        "minimax_code.computer_hub_sdk.connection.attach_trace_to_http_request",
        fake_attach,
    )
    dial = _RecordingDial()
    await open_socket(
        "wss://hub",
        AuthCredential.bearer("tok"),
        ConnectionKind.Harness,
        None,
        False,
        dial,
    )
    assert seen.get("called") is True
    assert seen.get("is_dict") is True
    # The traceparent the spy wrote into the shared mapping reaches the dialer.
    headers = dict(dial.calls[0][1])
    assert headers["traceparent"] == "00-..-..-01"


# -- open_socket dialer error classification (connection.rs:925-931) --------
async def test_open_socket_dial_401_raises_handshake_auth_failed() -> None:
    # A 401 on the HTTP upgrade is non-retryable: replaying the same credential
    # is rejected identically, so it surfaces as HandshakeAuthFailed(status=401)
    # rather than a transient NetworkError.
    dial = _RecordingDial(exc=_HttpReject(401, "unauthorized"))
    with pytest.raises(HandshakeAuthFailed) as exc_info:
        await open_socket(
            "wss://hub",
            AuthCredential.bearer("tok"),
            ConnectionKind.Harness,
            None,
            False,
            dial,
        )
    assert exc_info.value.status == 401


async def test_open_socket_dial_403_raises_handshake_auth_failed() -> None:
    # 403 forbidden is the other auth-reject status -> HandshakeAuthFailed.
    dial = _RecordingDial(exc=_HttpReject(403, "forbidden"))
    with pytest.raises(HandshakeAuthFailed) as exc_info:
        await open_socket(
            "wss://hub",
            AuthCredential.bearer("tok"),
            ConnectionKind.Harness,
            None,
            False,
            dial,
        )
    assert exc_info.value.status == 403


async def test_open_socket_dial_transport_error_raises_network_error() -> None:
    # Any non-auth dialer failure (connection refused, TLS error, ...) carries
    # no ``status`` attribute, so from_handshake_error collapses it to a
    # transient NetworkError (retryable on the next reconnect attempt).
    dial = _RecordingDial(exc=ConnectionError("refused"))
    with pytest.raises(NetworkError):
        await open_socket(
            "wss://hub",
            AuthCredential.bearer("tok"),
            ConnectionKind.Harness,
            None,
            False,
            dial,
        )


# -- open_socket API-parity no-ops (connection.rs:923) ----------------------
async def test_open_socket_alpha_test_key_accepted_but_ignored() -> None:
    # alpha_test_key is accepted for API parity with the Rust signature but
    # unused (Rust: ``let _ = alpha_test_key;``) -- it must not affect the dial
    # URL, headers, or flow. Two calls differing only in the key produce
    # identical dial invocations.
    dial_a = _RecordingDial()
    dial_b = _RecordingDial()
    await open_socket(
        "wss://hub",
        AuthCredential.bearer("tok"),
        ConnectionKind.Harness,
        "alpha-key-xyz",
        False,
        dial_a,
    )
    await open_socket(
        "wss://hub",
        AuthCredential.bearer("tok"),
        ConnectionKind.Harness,
        None,
        False,
        dial_b,
    )
    assert dial_a.calls == dial_b.calls


async def test_open_socket_returns_dialer_stream_verbatim() -> None:
    # The dialer's return value (library-dependent stream object) is returned
    # as-is -- open_socket does not wrap / inspect it. Same identity.
    sentinel = SimpleNamespace(name="raw-ws")
    dial = _RecordingDial(result=sentinel)
    ws = await open_socket(
        "wss://hub",
        AuthCredential.bearer("tok"),
        ConnectionKind.Harness,
        None,
        False,
        dial,
    )
    assert ws is sentinel


# ---------------------------------------------------------------------------
# run_handshake -- handshake orchestrator bridge (R163, SDK leaf 19b).
# ---------------------------------------------------------------------------
class _RecordingHandshake:
    """Spy capturing run_handshake's send_hello delegation (R163).

    Replaces the module-level ``send_hello`` reference so the orchestrator's
    contract is tested in isolation: every arg threaded through verbatim, the
    ack repackaged into a ``(sink, stream, ack)`` triple, and any ClientError
    subclass propagated untouched. The sink/stream are opaque sentinels
    (run_handshake never inspects them -- it only forwards them), so this
    suite does NOT re-exercise send_hello's own frame arms (already covered
    exhaustively in test_computer_hub_sdk_handshake.py); it asserts only the
    thin orchestrator's pass-through semantics.
    """

    def __init__(
        self, *, ack: HelloAckMsg | None = None, exc: BaseException | None = None
    ) -> None:
        self.captured: dict[str, Any] = {}
        self._ack = ack
        self._exc = exc

    async def __call__(
        self,
        sink: Any,
        stream: Any,
        kind: ConnectionKind,
        server_id: ServerId | None = None,
        description: str | None = None,
        metadata: Any = None,
    ) -> HelloAckMsg:
        self.captured = {
            "sink": sink,
            "stream": stream,
            "kind": kind,
            "server_id": server_id,
            "description": description,
            "metadata": metadata,
        }
        if self._exc is not None:
            raise self._exc
        assert self._ack is not None  # pacify type checker; set in __init__.
        return self._ack


def _hello_ack_wire() -> dict[str, Any]:
    """A well-formed hello_ack wire dict (PROTOCOL_VERSION supported)."""
    return {
        "connection_id": "conn-1",
        "user_id": "user-7",
        "computer_hub_version": "hub-0.1.0",
        "supported_protocol_versions": ["1.0.0"],
    }


async def test_run_handshake_threads_sink_stream_kind_to_send_hello(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The three positional args (sink, stream, kind) are forwarded verbatim;
    # run_handshake never inspects them, only threads them through.
    spy = _RecordingHandshake(ack=HelloAckMsg.from_wire(_hello_ack_wire()))
    monkeypatch.setattr("minimax_code.computer_hub_sdk.connection.send_hello", spy)
    sink = object()
    stream = object()
    await run_handshake(sink, stream, ConnectionKind.ToolServer)
    assert spy.captured["sink"] is sink
    assert spy.captured["stream"] is stream
    assert spy.captured["kind"] is ConnectionKind.ToolServer


async def test_run_handshake_defaults_optional_fields_to_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _RecordingHandshake(ack=HelloAckMsg.from_wire(_hello_ack_wire()))
    monkeypatch.setattr("minimax_code.computer_hub_sdk.connection.send_hello", spy)
    await run_handshake(object(), object(), ConnectionKind.Harness)
    assert spy.captured["server_id"] is None
    assert spy.captured["description"] is None
    assert spy.captured["metadata"] is None


async def test_run_handshake_threads_optional_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _RecordingHandshake(ack=HelloAckMsg.from_wire(_hello_ack_wire()))
    monkeypatch.setattr("minimax_code.computer_hub_sdk.connection.send_hello", spy)
    metadata = {"runner": "gha"}
    await run_handshake(
        object(),
        object(),
        ConnectionKind.ToolServer,
        "srv-9",  # type: ignore[arg-type]
        "ci-runner",
        metadata,
    )
    assert spy.captured["server_id"] == "srv-9"
    assert spy.captured["description"] == "ci-runner"
    assert spy.captured["metadata"] is metadata


async def test_run_handshake_returns_sink_stream_ack_triple(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The orchestrator repackages send_hello's ack into a (sink, stream, ack)
    # triple; the sink/stream are the SAME objects passed in (mirrors Rust
    # returning the re-borrowed &mut pair), and the ack is send_hello's return.
    ack = HelloAckMsg.from_wire(_hello_ack_wire())
    spy = _RecordingHandshake(ack=ack)
    monkeypatch.setattr("minimax_code.computer_hub_sdk.connection.send_hello", spy)
    sink = object()
    stream = object()
    result_sink, result_stream, result_ack = await run_handshake(
        sink, stream, ConnectionKind.ToolServer
    )
    assert result_sink is sink
    assert result_stream is stream
    assert result_ack is ack


async def test_run_handshake_propagates_protocol_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _RecordingHandshake(exc=ProtocolError("version mismatch"))
    monkeypatch.setattr("minimax_code.computer_hub_sdk.connection.send_hello", spy)
    with pytest.raises(ProtocolError, match="version mismatch"):
        await run_handshake(object(), object(), ConnectionKind.ToolServer)


async def test_run_handshake_propagates_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _RecordingHandshake(exc=Closed("server closed during handshake"))
    monkeypatch.setattr("minimax_code.computer_hub_sdk.connection.send_hello", spy)
    with pytest.raises(Closed, match="server closed"):
        await run_handshake(object(), object(), ConnectionKind.ToolServer)


async def test_run_handshake_propagates_network_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _RecordingHandshake(exc=NetworkError("hello send failed"))
    monkeypatch.setattr("minimax_code.computer_hub_sdk.connection.send_hello", spy)
    with pytest.raises(NetworkError, match="hello send"):
        await run_handshake(object(), object(), ConnectionKind.ToolServer)


async def test_run_handshake_propagates_serde_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spy = _RecordingHandshake(exc=SerdeError("hello serialize failed"))
    monkeypatch.setattr("minimax_code.computer_hub_sdk.connection.send_hello", spy)
    with pytest.raises(SerdeError, match="serialize"):
        await run_handshake(object(), object(), ConnectionKind.ToolServer)


# ---------------------------------------------------------------------------
# reconnect_and_replay -- reconnect orchestration finale (R164, SDK leaf 19c).
# ---------------------------------------------------------------------------
_CRED_SENTINEL: Any = object()  # yielded by _CrededAuth.current().
_DIAL_SENTINEL: Any = object()  # opaque WebSocketDial threaded into open_socket.
_WS_SENTINEL: Any = object()  # opaque raw ws returned by the open_socket spy.


class _CrededAuth:
    """AuthProvider stand-in whose ``current()`` yields a sentinel (R164).

    Asserts that reconnect_and_replay reads the FRESH credential
    (``inner.credential.current()``) right before opening the socket, rather
    than reusing a stale one captured earlier.
    """

    def current(self) -> Any:
        return _CRED_SENTINEL

    def principal_key(self) -> PrincipalKey:
        return PrincipalKey(fingerprint="fp-1")

    def identity(self) -> None:
        return None


class _OpenSocketSpy:
    """Spy capturing reconnect_and_replay's open_socket delegation (R164).

    Replaces the module-level ``open_socket`` reference so the orchestrator's
    socket-opening contract is tested in isolation: every arg threaded through
    verbatim and a ws sentinel returned. The plaintext/loopback/header gates
    (already covered by the R162 open_socket suite) are not re-exercised here.
    """

    def __init__(
        self, *, ws: Any = _WS_SENTINEL, exc: BaseException | None = None
    ) -> None:
        self._ws = ws
        self._exc = exc
        self.captured: dict[str, Any] = {}

    async def __call__(
        self,
        url: str,
        credential: Any,
        kind: ConnectionKind,
        alpha_test_key: str | None,
        allow_insecure_ws: bool,
        dial: Any,
    ) -> Any:
        self.captured = {
            "url": url,
            "credential": credential,
            "kind": kind,
            "alpha_test_key": alpha_test_key,
            "allow_insecure_ws": allow_insecure_ws,
            "dial": dial,
        }
        if self._exc is not None:
            raise self._exc
        return self._ws


class _SplitSpy:
    """Spy capturing reconnect_and_replay's split_ws delegation (R164).

    The real ``ws.split()`` (tokio_tungstenite) has no Python-WS analog, so the
    sink/stream split is dependency-injected; this spy records the raw ws handed
    to the splitter and returns the test's pre-built sink/stream pair.
    """

    def __init__(self, sink: Any, stream: Any) -> None:
        self._sink = sink
        self._stream = stream
        self.captured_ws: Any = None

    def __call__(self, ws: Any) -> tuple[Any, Any]:
        self.captured_ws = ws
        return self._sink, self._stream


class _ReplaySink:
    """Fake HandshakeSink recording ``send_text`` invocations (R164).

    Only ``send_text`` is exercised by the session-replay loop (the handshake
    itself is short-circuited by the ``_RecordingHandshake`` spy); the optional
    ``send_exc`` models a transport write failure that the best-effort replay
    must swallow without aborting the reconnect.
    """

    def __init__(self, *, send_exc: BaseException | None = None) -> None:
        self.sent: list[str] = []
        self._send_exc = send_exc

    async def send_text(self, text: str) -> None:
        if self._send_exc is not None:
            raise self._send_exc
        self.sent.append(text)


class _ReplayStream:
    """Fake HandshakeStream: one scripted exception then end (R164).

    ``asyncio.wait_for(stream.__anext__(), 5)`` runs once per replayed session;
    the reply is discarded, so the stream only needs to raise a scripted error
    (swallowed) and then stop. A real ``asyncio.TimeoutError`` models the 5s
    budget elapsing without paying the wall-clock cost (the exception fires
    immediately from ``__anext__``, so ``wait_for`` propagates at once). Both
    ``StopAsyncIteration`` and ``asyncio.TimeoutError`` are ``Exception``
    subclasses, so the orchestrator's ``except Exception`` arm catches either.
    """

    def __init__(self, *, exc: BaseException | None = None) -> None:
        self._exc = exc

    def __aiter__(self) -> _ReplayStream:
        return self

    async def __anext__(self) -> Any:
        if self._exc is not None:
            raise self._exc
        raise StopAsyncIteration


class _ReconnectRecorder:
    """Captures on_reconnect callback invocations (R164, avoids E731 lambda)."""

    def __init__(self) -> None:
        self.events: list[Any] = []

    def __call__(self, event: Any) -> None:
        self.events.append(event)


def _replay_ack_wire(
    *, connection_id: str = "conn-7", capabilities: list[str] | None = None
) -> dict[str, Any]:
    """A hello_ack wire dict for the reconnect suite (R164).

    Unlike the shared ``_hello_ack_wire`` (R163, capability-free), this helper
    optionally carries ``capabilities`` so the cap-replace branch can be
    exercised with a non-empty list.
    """
    wire: dict[str, Any] = {
        "connection_id": connection_id,
        "user_id": "user-7",
        "computer_hub_version": "hub-0.1.0",
        "supported_protocol_versions": ["1.0.0"],
    }
    if capabilities is not None:
        wire["capabilities"] = capabilities
    return wire


def _replay_outage(*, cause: Any | None = None) -> OutageInfo:
    """A minimal OutageInfo for reconnect_and_replay (R164).

    Only ``cause`` (for the metric label) and ``last_inbound_mono`` (for the
    silent-gap sample) are read by the orchestrator; the remaining fields ride
    along verbatim into the info log line and are not asserted on.
    """
    return OutageInfo(
        cause=cause if cause is not None else Eof(),
        prev_connection_id=None,
        prev_connection_duration_ms=1_000,
        last_inbound_mono=time.monotonic() - 1.0,
        detect_ms=42,
        since_last_probe_monotonic_ms=5,
        since_last_probe_wall_ms=6,
        clock_jump_ms=0,
    )


def _replay_inner(
    *,
    kind: ConnectionKind,
    sessions: tuple[str, ...] = (),
    on_reconnect: Callable[[Any], None] | None = None,
    credential: Any | None = None,
    server_id: ServerId | None = None,
    server_description: str | None = None,
    server_metadata: Any = None,
) -> HubConnectionInner:
    """Build a HubConnectionInner wired to fresh channels (R164).

    ``_make_inner`` hard-codes kind/credential/bound_sessions for the writer /
    reader suites; reconnect_and_replay needs Harness kind, populated sessions,
    a sentinel credential, and an on_reconnect callback, so this dedicated
    builder constructs the inner directly with mock channels.
    """
    outbound_tx, _ = mpsc_channel(8)
    stop_tx, _ = mpsc_channel(8)
    reconnect_tx, _ = mpsc_channel(8)
    bound: RefCountedSet = RefCountedSet()
    for sid in sessions:
        bound.increment(SessionId(sid))
    return HubConnectionInner(
        key=ConnKey("ws://hub", PrincipalKey(fingerprint="fp-1")),
        kind=kind,
        credential=credential if credential is not None else _FakeAuth(),
        reconnect_backoff=(1.0, 2.0),
        outbound_tx=outbound_tx,
        demux=Demux(),
        bound_sessions=bound,
        stop_tx=stop_tx,
        reconnect_tx=reconnect_tx,
        on_reconnect=on_reconnect,
        server_id=server_id,
        server_description=server_description,
        server_metadata=server_metadata,
    )


async def _drive_replay(
    monkeypatch: pytest.MonkeyPatch,
    inner: HubConnectionInner,
    *,
    ack: HelloAckMsg,
    sink: Any,
    stream: Any,
    ws: Any = _WS_SENTINEL,
    attempt: int = 3,
) -> SimpleNamespace:
    """Wire the open_socket + send_hello spies and run reconnect_and_replay (R164).

    Returns a namespace carrying the returned ``(sink, stream)`` pair plus every
    spy (open_socket, split_ws, handshake) so a test can assert on the exact
    arg threading without re-wiring the monkeypatch boilerplate each time.
    """
    open_spy = _OpenSocketSpy(ws=ws)
    handshake_spy = _RecordingHandshake(ack=ack)
    split_spy = _SplitSpy(sink=sink, stream=stream)
    monkeypatch.setattr("minimax_code.computer_hub_sdk.connection.open_socket", open_spy)
    monkeypatch.setattr("minimax_code.computer_hub_sdk.connection.send_hello", handshake_spy)
    sink_out, stream_out = await reconnect_and_replay(
        inner,
        "wss://hub/agent",
        attempt,
        _replay_outage(),
        2.0,
        dial=_DIAL_SENTINEL,
        split_ws=split_spy,
    )
    return SimpleNamespace(
        sink=sink_out,
        stream=stream_out,
        open_spy=open_spy,
        split_spy=split_spy,
        handshake_spy=handshake_spy,
    )


async def test_reconnect_and_replay_threads_fresh_credential_and_open_socket_args(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fresh credential, url, kind, and the injected dial are all threaded
    # into open_socket verbatim; alpha_test_key/allow_insecure_ws default off.
    ack = HelloAckMsg.from_wire(_replay_ack_wire())
    inner = _replay_inner(
        kind=ConnectionKind.ToolServer,
        credential=_CrededAuth(),
        server_id=ServerId("srv-1"),
        server_description="ci",
        server_metadata={"k": "v"},
    )
    res = await _drive_replay(
        monkeypatch, inner, ack=ack, sink=_ReplaySink(), stream=_ReplayStream()
    )
    cap = res.open_spy.captured
    assert cap["url"] == "wss://hub/agent"
    assert cap["credential"] is _CRED_SENTINEL
    assert cap["kind"] is ConnectionKind.ToolServer
    assert cap["alpha_test_key"] is None
    assert cap["allow_insecure_ws"] is False
    assert cap["dial"] is _DIAL_SENTINEL


async def test_reconnect_and_replay_splits_ws_and_threads_pair_to_handshake(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The raw ws goes to split_ws; the resulting (sink, stream) pair + kind flow
    # into run_handshake untouched.
    ws = object()
    ack = HelloAckMsg.from_wire(_replay_ack_wire())
    inner = _replay_inner(kind=ConnectionKind.Harness)
    sink = _ReplaySink()
    stream = _ReplayStream()
    res = await _drive_replay(monkeypatch, inner, ack=ack, sink=sink, stream=stream, ws=ws)
    assert res.split_spy.captured_ws is ws
    hcap = res.handshake_spy.captured
    assert hcap["sink"] is sink
    assert hcap["stream"] is stream
    assert hcap["kind"] is ConnectionKind.Harness


async def test_reconnect_and_replay_tool_server_skips_session_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Only the harness role replays sessions; a tool server with bound sessions
    # sends nothing on reconnect.
    ack = HelloAckMsg.from_wire(_replay_ack_wire())
    inner = _replay_inner(kind=ConnectionKind.ToolServer, sessions=("s1", "s2"))
    sink = _ReplaySink()
    await _drive_replay(monkeypatch, inner, ack=ack, sink=sink, stream=_ReplayStream())
    assert sink.sent == []


async def test_reconnect_and_replay_harness_replays_each_bound_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Every still-bound session gets one session_open frame; the stream reply is
    # discarded (StopAsyncIteration swallowed) so neither session blocks.
    ack = HelloAckMsg.from_wire(_replay_ack_wire())
    inner = _replay_inner(kind=ConnectionKind.Harness, sessions=("s1", "s2"))
    sink = _ReplaySink()
    await _drive_replay(monkeypatch, inner, ack=ack, sink=sink, stream=_ReplayStream())
    assert len(sink.sent) == 2
    sids = {json.loads(msg)["session_id"] for msg in sink.sent}
    assert sids == {"s1", "s2"}


async def test_reconnect_and_replay_session_open_request_is_well_formed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The replay frame is a JSON-RPC 2.0 session_open request: fresh uuid id,
    # the bound session_id, and resume=False with no last_seq (fresh open).
    ack = HelloAckMsg.from_wire(_replay_ack_wire())
    inner = _replay_inner(kind=ConnectionKind.Harness, sessions=("s1",))
    sink = _ReplaySink()
    await _drive_replay(monkeypatch, inner, ack=ack, sink=sink, stream=_ReplayStream())
    payload = json.loads(sink.sent[0])
    assert payload["jsonrpc"] == "2.0"
    assert payload["method"] == "session_open"
    assert payload["session_id"] == "s1"
    assert payload["params"] == {"resume": False}
    assert isinstance(payload["id"], str)


async def test_reconnect_and_replay_swallows_send_failure_during_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A transport write failure on one session_open must not abort the reconnect
    # (best-effort replay); the (sink, stream) pair is still returned.
    ack = HelloAckMsg.from_wire(_replay_ack_wire())
    inner = _replay_inner(kind=ConnectionKind.Harness, sessions=("s1",))
    sink = _ReplaySink(send_exc=RuntimeError("write broken"))
    res = await _drive_replay(
        monkeypatch, inner, ack=ack, sink=sink, stream=_ReplayStream()
    )
    assert res.sink is sink


async def test_reconnect_and_replay_swallows_recv_timeout_during_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The 5s recv budget elapsing (asyncio.TimeoutError) on the discarded reply
    # is swallowed per session; multiple sessions each pay the cost independently.
    ack = HelloAckMsg.from_wire(_replay_ack_wire())
    inner = _replay_inner(kind=ConnectionKind.Harness, sessions=("s1", "s2"))
    sink = _ReplaySink()
    stream = _ReplayStream(exc=TimeoutError())
    res = await _drive_replay(monkeypatch, inner, ack=ack, sink=sink, stream=stream)
    assert res.stream is stream


async def test_reconnect_and_replay_updates_connection_id_and_capabilities(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The fresh ack's connection_id replaces the slot and its capabilities
    # replace the hello caps (a None caps list collapses to empty).
    ack = HelloAckMsg.from_wire(
        _replay_ack_wire(connection_id="conn-42", capabilities=["streaming", "telemetry"])
    )
    inner = _replay_inner(kind=ConnectionKind.ToolServer)
    await _drive_replay(
        monkeypatch, inner, ack=ack, sink=_ReplaySink(), stream=_ReplayStream()
    )
    assert inner.connection_id.get() == ConnectionId("conn-42")
    assert inner.hello_capabilities.snapshot() == ["streaming", "telemetry"]


async def test_reconnect_and_replay_dispatches_on_reconnect_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # On a successful reconnect the on_reconnect callback fires once with the
    # fresh connection id, the session count, and the attempt number.
    ack = HelloAckMsg.from_wire(_replay_ack_wire(connection_id="conn-9"))
    recorder = _ReconnectRecorder()
    inner = _replay_inner(
        kind=ConnectionKind.Harness,
        sessions=("s1", "s2"),
        on_reconnect=recorder,
    )
    sink = _ReplaySink()
    res = await _drive_replay(
        monkeypatch, inner, ack=ack, sink=sink, stream=_ReplayStream(), attempt=5
    )
    assert len(recorder.events) == 1
    event = recorder.events[0]
    assert event.connection_id == ConnectionId("conn-9")
    assert event.sessions_replayed == 2
    assert event.attempt == 5
    # The returned pair is the split -> handshake passthrough (same objects).
    assert res.sink is sink


async def test_reconnect_and_replay_skips_callback_when_on_reconnect_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With no on_reconnect callback wired, the dispatch branch is skipped
    # entirely (the `if inner.on_reconnect is not None` guard holds).
    ack = HelloAckMsg.from_wire(_replay_ack_wire())
    inner = _replay_inner(kind=ConnectionKind.ToolServer)  # on_reconnect=None.
    res = await _drive_replay(
        monkeypatch, inner, ack=ack, sink=_ReplaySink(), stream=_ReplayStream()
    )
    assert res.sink is not None
    assert res.stream is not None
