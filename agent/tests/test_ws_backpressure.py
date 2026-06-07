"""Tests for P2#23: WebSocket backpressure via bounded per-client queues.

Verifies:
1. Each client gets a bounded send queue on connect
2. Queue is cleaned up on disconnect
3. When queue is full, oldest items are dropped (not blocked)
4. Sender task sends items in order
5. Sender task stops on sentinel / cancellation
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from minimax_code.http_server import _WSManager


def _make_mock_ws() -> AsyncMock:
    """Create a mock WebSocket with query_params."""
    ws = AsyncMock()
    ws.query_params = {}
    return ws


def _make_manager(*, max_depth: int = 256) -> _WSManager:
    """Create a _WSManager with a mock IPCServer."""
    mock_server = MagicMock()
    mock_server.register_listener = MagicMock()
    mock_server.unregister_listener = MagicMock()
    mgr = _WSManager(mock_server, version="test")
    mgr._max_queue_depth = max_depth
    return mgr


@pytest.mark.asyncio
async def test_sender_started_on_connect() -> None:
    """Per-client sender queue + task created on connect."""
    mgr = _make_manager()
    ws = _make_mock_ws()

    await mgr.on_connect(ws)

    ws_id = id(ws)
    assert ws_id in mgr._send_queues
    assert ws_id in mgr._sender_tasks
    assert not mgr._sender_tasks[ws_id].done()

    await mgr.on_disconnect(ws)
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_sender_stopped_on_disconnect() -> None:
    """Per-client sender queue + task removed on disconnect."""
    mgr = _make_manager()
    ws = _make_mock_ws()

    await mgr.on_connect(ws)
    ws_id = id(ws)

    await mgr.on_disconnect(ws)
    await asyncio.sleep(0.05)

    assert ws_id not in mgr._send_queues
    assert ws_id not in mgr._sender_tasks


@pytest.mark.asyncio
async def test_enqueue_sends_in_order() -> None:
    """Items are delivered to the client in FIFO order."""
    mgr = _make_manager()
    ws = _make_mock_ws()
    # Track actual send_json calls
    sent_items: list[dict] = []
    ws.send_json.side_effect = lambda p: sent_items.append(p)

    await mgr.on_connect(ws)

    # Enqueue several items
    for i in range(5):
        mgr._enqueue(ws, {"idx": i})

    # Give the sender task time to process
    await asyncio.sleep(0.2)

    # Items should be sent in order (plus the initial agent.ready)
    data_items = [s for s in sent_items if "idx" in s]
    assert [d["idx"] for d in data_items] == [0, 1, 2, 3, 4]

    await mgr.on_disconnect(ws)
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_backpressure_drops_oldest() -> None:
    """When queue is full, oldest items are dropped to make room."""
    mgr = _make_manager(max_depth=4)
    ws = _make_mock_ws()

    await mgr.on_connect(ws)

    # Stop the sender task so items accumulate in the queue
    ws_id = id(ws)
    task = mgr._sender_tasks.get(ws_id)
    if task:
        task.cancel()
        await asyncio.sleep(0.05)

    # Now enqueue more items than the queue can hold
    for i in range(8):
        mgr._enqueue(ws, {"idx": i})

    # Queue should have at most max_depth items
    queue = mgr._send_queues.get(ws_id)
    assert queue is not None
    assert queue.qsize() <= mgr._max_queue_depth

    # The remaining items should be the newest ones (drop oldest)
    items: list[int] = []
    while not queue.empty():
        item = queue.get_nowait()
        if item is not None and "idx" in item:
            items.append(item["idx"])
    # With depth 4, we should have items 4-7 (last 4)
    assert items == [4, 5, 6, 7]

    await mgr.on_disconnect(ws)
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_enqueue_nonexistent_client() -> None:
    """Enqueuing for a non-tracked client is a no-op."""
    mgr = _make_manager()
    ws = _make_mock_ws()

    # Should not raise
    mgr._enqueue(ws, {"test": True})


@pytest.mark.asyncio
async def test_sender_stops_on_send_failure() -> None:
    """Sender task exits when send_json raises."""
    mgr = _make_manager()
    ws = _make_mock_ws()
    call_count = 0

    async def _failing_send(payload):
        nonlocal call_count
        call_count += 1
        if call_count > 1:
            raise ConnectionError("client disconnected")

    ws.send_json.side_effect = _failing_send

    await mgr.on_connect(ws)

    # Enqueue items — sender should stop after the first failure
    mgr._enqueue(ws, {"first": True})
    mgr._enqueue(ws, {"second": True})
    mgr._enqueue(ws, {"third": True})

    await asyncio.sleep(0.2)

    # Sender should have stopped; only first + agent.ready sent
    ws_id = id(ws)
    task = mgr._sender_tasks.get(ws_id)
    if task:
        assert task.done()

    await mgr.on_disconnect(ws)
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_multiple_clients_independent_queues() -> None:
    """Each client has its own independent send queue."""
    mgr = _make_manager()
    ws1 = _make_mock_ws()
    ws2 = _make_mock_ws()

    await mgr.on_connect(ws1)
    await mgr.on_connect(ws2)

    assert id(ws1) in mgr._send_queues
    assert id(ws2) in mgr._send_queues
    assert mgr._send_queues[id(ws1)] is not mgr._send_queues[id(ws2)]

    # Disconnect one — other still has its queue
    await mgr.on_disconnect(ws1)
    await asyncio.sleep(0.05)
    assert id(ws1) not in mgr._send_queues
    assert id(ws2) in mgr._send_queues

    await mgr.on_disconnect(ws2)
    await asyncio.sleep(0.05)
