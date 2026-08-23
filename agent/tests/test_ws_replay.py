"""Tests for the v0.13.0 WS resume mechanism (roadmap R13).

The broadcast path stamps every event with a monotonic ``seq`` and
appends it to a bounded history ring. A client reconnecting with
``GET /ws?since=<seq>`` gets the missed events replayed after
``agent.ready`` — so a dropped WebSocket no longer means silently
lost stream events.

Verifies:
1. ``_on_event`` stamps sequential ``seq`` values onto broadcasts.
2. The history ring retains the newest events up to its capacity.
3. ``on_connect`` without ``since`` replays nothing.
4. ``on_connect`` with ``since=N`` replays exactly the events with
   ``seq > N``, in order, through the client's sender queue.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from minimax_code.http_server import _WSManager


def _make_mock_ws(query: dict[str, str] | None = None) -> AsyncMock:
    """Create a mock WebSocket with query_params."""
    ws = AsyncMock()
    ws.query_params = query or {}
    return ws


def _make_manager() -> _WSManager:
    """Create a _WSManager with a mock IPCServer."""
    mock_server = MagicMock()
    mock_server.register_listener = MagicMock()
    mock_server.unregister_listener = MagicMock()
    return _WSManager(mock_server, version="test")


async def _drain_sender(delay: float = 0.05) -> None:
    """Give per-client sender tasks a tick to drain their queues."""
    await asyncio.sleep(delay)


@pytest.mark.asyncio
async def test_on_event_stamps_monotonic_seq() -> None:
    mgr = _make_manager()
    ws = _make_mock_ws()
    await mgr.on_connect(ws)

    mgr._on_event({"event": "agent.message_chunk", "data": {"text": "a"}})
    mgr._on_event({"event": "agent.status", "data": {"status": "thinking"}})

    await _drain_sender()
    payloads = [c.args[0] for c in ws.send_json.call_args_list]
    broadcasts = [p for p in payloads if p.get("method") == "agent.message_chunk" or p.get("method") == "agent.status"]
    assert [p["seq"] for p in broadcasts] == [1, 2]
    assert broadcasts[0]["method"] == "agent.message_chunk"
    assert broadcasts[1]["method"] == "agent.status"

    await mgr.on_disconnect(ws)
    await _drain_sender()


@pytest.mark.asyncio
async def test_history_ring_is_bounded() -> None:
    mgr = _make_manager()
    for i in range(600):
        mgr._on_event({"event": "agent.status", "data": {"i": i}})

    seqs = [seq for seq, _ in mgr._history]
    assert len(mgr._history) == 512
    # Newest 512 retained: 89..600
    assert seqs[0] == 89
    assert seqs[-1] == 600


@pytest.mark.asyncio
async def test_connect_without_since_replays_nothing() -> None:
    mgr = _make_manager()
    for i in range(3):
        mgr._on_event({"event": "agent.status", "data": {"i": i}})

    ws = _make_mock_ws()
    await mgr.on_connect(ws)
    await _drain_sender()

    payloads = [c.args[0] for c in ws.send_json.call_args_list]
    methods = [p["method"] for p in payloads]
    assert methods == ["agent.ready"], f"expected only agent.ready, got {methods}"

    await mgr.on_disconnect(ws)
    await _drain_sender()


@pytest.mark.asyncio
async def test_connect_with_since_replays_missed_events_in_order() -> None:
    mgr = _make_manager()
    # Client saw seq 2; events 3..5 happened while disconnected.
    for i in range(5):
        mgr._on_event({"event": "agent.status", "data": {"i": i}})

    ws = _make_mock_ws(query={"since": "2"})
    await mgr.on_connect(ws)
    await _drain_sender()

    payloads = [c.args[0] for c in ws.send_json.call_args_list]
    methods = [p["method"] for p in payloads]
    assert methods[0] == "agent.ready"
    replays = payloads[1:]
    assert [p["seq"] for p in replays] == [3, 4, 5]
    assert [p["params"]["i"] for p in replays] == [2, 3, 4]

    await mgr.on_disconnect(ws)
    await _drain_sender()


@pytest.mark.asyncio
async def test_since_zero_replays_everything_retained() -> None:
    mgr = _make_manager()
    mgr._on_event({"event": "agent.status", "data": {"i": 0}})
    mgr._on_event({"event": "agent.status", "data": {"i": 1}})

    # A client that never saw any event may ask with since=0.
    ws = _make_mock_ws(query={"since": "0"})
    await mgr.on_connect(ws)
    await _drain_sender()

    payloads = [c.args[0] for c in ws.send_json.call_args_list]
    assert [p["seq"] for p in payloads[1:]] == [1, 2]

    await mgr.on_disconnect(ws)
    await _drain_sender()


@pytest.mark.asyncio
async def test_since_beyond_history_replays_nothing() -> None:
    mgr = _make_manager()
    mgr._on_event({"event": "agent.status", "data": {"i": 0}})

    # Client's last seq is ahead of the ring (server restarted) —
    # replay nothing. v1.2.2: the client detects this epoch reset via
    # the ``next_seq`` anchor on the ready frame (see tests below),
    # drops its stale cursor, and reconnects without ``?since=``.
    ws = _make_mock_ws(query={"since": "9999"})
    await mgr.on_connect(ws)
    await _drain_sender()

    payloads = [c.args[0] for c in ws.send_json.call_args_list]
    assert [p["method"] for p in payloads] == ["agent.ready"]

    await mgr.on_disconnect(ws)
    await _drain_sender()


# ---------------------------------------------------------------------------
# v1.2.2 — ready-frame ``next_seq`` anchor (epoch-reset detection)
# ---------------------------------------------------------------------------


def _ready_payload(payloads: list[dict]) -> dict:
    ready = [p for p in payloads if p.get("method") == "agent.ready"]
    assert ready, "no agent.ready frame was sent"
    return ready[0]


@pytest.mark.asyncio
async def test_ready_frame_carries_next_seq_anchor() -> None:
    """The ready frame advertises the seq the next broadcast will
    carry — the anchor clients compare their watermark against."""
    mgr = _make_manager()

    ws = _make_mock_ws()
    await mgr.on_connect(ws)
    await _drain_sender()
    ready = _ready_payload([c.args[0] for c in ws.send_json.call_args_list])
    assert ready["params"]["next_seq"] == 1, "fresh process must anchor at 1"
    await mgr.on_disconnect(ws)
    await _drain_sender()

    for i in range(5):
        mgr._on_event({"event": "agent.status", "data": {"i": i}})

    ws2 = _make_mock_ws()
    await mgr.on_connect(ws2)
    await _drain_sender()
    ready2 = _ready_payload([c.args[0] for c in ws2.send_json.call_args_list])
    assert ready2["params"]["next_seq"] == 6, "anchor must be last seq + 1"
    await mgr.on_disconnect(ws2)
    await _drain_sender()


@pytest.mark.asyncio
async def test_ready_anchor_exposes_epoch_reset_to_stale_client() -> None:
    """The v1.2.2 regression: a client holding the *previous*
    process's watermark (47) reconnects after an agent restart. The
    fresh process anchors at ``next_seq=1`` — 1 <= 47 tells the client
    its ``?since=47`` cursor points into a dead epoch."""
    old_mgr = _make_manager()  # simulates the pre-restart process
    for _ in range(47):
        old_mgr._on_event({"event": "agent.status", "data": {}})

    restarted = _make_manager()  # fresh process: counter back at 0
    ws = _make_mock_ws(query={"since": "47"})
    await restarted.on_connect(ws)
    await _drain_sender()

    payloads = [c.args[0] for c in ws.send_json.call_args_list]
    ready = _ready_payload(payloads)
    assert ready["params"]["next_seq"] == 1
    # And the replay the stale cursor asked for is empty — exactly
    # what the client uses the anchor to recover from.
    assert [p["method"] for p in payloads] == ["agent.ready"]

    await restarted.on_disconnect(ws)
    await _drain_sender()
