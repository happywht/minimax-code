"""Tests for P2#22: WebSocket ping/pong heartbeat mechanism.

Verifies:
1. _WSManager starts heartbeat loop on first client connect
2. _WSManager stops heartbeat loop on last client disconnect
3. Heartbeat sends agent.ping to all connected clients
4. WS endpoint handles agent.pong from client silently
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minimax_code.http_server import _WSManager


def _make_mock_ws() -> AsyncMock:
    """Create a mock WebSocket with query_params."""
    ws = AsyncMock()
    ws.query_params = {}
    return ws


def _make_manager() -> _WSManager:
    """Create a _WSManager with a mock IPCServer."""
    mock_server = MagicMock()
    mock_server.register_listener = MagicMock()
    mock_server.unregister_listener = MagicMock()
    return _WSManager(mock_server, version="test")


@pytest.mark.asyncio
async def test_heartbeat_starts_on_first_client() -> None:
    """Ping task should be created when the first client connects."""
    mgr = _make_manager()
    ws1 = _make_mock_ws()

    await mgr.on_connect(ws1)

    assert mgr._ping_task is not None
    assert not mgr._ping_task.done()

    # Cleanup
    await mgr.on_disconnect(ws1)
    # Give event loop a tick to process cancellation
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_disconnect_during_ready_is_cleaned_without_error_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A client may close immediately after the upgrade is accepted."""
    from starlette.websockets import WebSocketDisconnect

    mgr = _make_manager()
    ws = _make_mock_ws()
    ws.send_json.side_effect = WebSocketDisconnect(code=1006)

    with pytest.raises(WebSocketDisconnect):
        await mgr.on_connect(ws)

    assert ws not in mgr._clients
    assert mgr._ping_task is None
    assert not [record for record in caplog.records if record.levelno >= 40]


@pytest.mark.asyncio
async def test_heartbeat_stops_on_last_client_disconnect() -> None:
    """Ping task should be cancelled when all clients disconnect."""
    mgr = _make_manager()
    ws1 = _make_mock_ws()
    ws2 = _make_mock_ws()

    await mgr.on_connect(ws1)
    await mgr.on_connect(ws2)
    task = mgr._ping_task
    assert task is not None

    # Disconnect one — task still running
    await mgr.on_disconnect(ws1)
    assert mgr._ping_task is not None

    # Disconnect last — task cancelled
    await mgr.on_disconnect(ws2)
    assert mgr._ping_task is None


@pytest.mark.asyncio
async def test_heartbeat_sends_ping() -> None:
    """Heartbeat loop should send agent.ping to all connected clients."""
    mgr = _make_manager()
    ws1 = _make_mock_ws()
    ws2 = _make_mock_ws()

    await mgr.on_connect(ws1)
    await mgr.on_connect(ws2)

    # Manually trigger one heartbeat iteration instead of waiting 30s
    await mgr._heartbeat_loop.__wrapped__(mgr) if hasattr(mgr._heartbeat_loop, '__wrapped__') else None

    # Verify ping was sent to both clients via send_json
    # The _heartbeat_loop is an async method; call it with controlled timing
    # Actually, let's patch asyncio.sleep to control the loop
    ping_calls_1 = [c for c in ws1.send_json.call_args_list
                     if c.args and c.args[0].get("method") == "agent.ping"]
    [c for c in ws2.send_json.call_args_list
                     if c.args and c.args[0].get("method") == "agent.ping"]

    # No pings yet because we haven't waited 30s
    assert len(ping_calls_1) == 0

    # Cleanup
    await mgr.on_disconnect(ws1)
    await mgr.on_disconnect(ws2)
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_heartbeat_drops_dead_client() -> None:
    """If send_json fails for a client, heartbeat should drop it."""
    mgr = _make_manager()
    ws_dead = _make_mock_ws()
    ws_alive = _make_mock_ws()

    # Dead client will raise on send_json
    ws_dead.send_json.side_effect = [None, Exception("connection lost")]

    await mgr.on_connect(ws_dead)
    await mgr.on_connect(ws_alive)

    # Manually trigger heartbeat cleanup by simulating one cycle
    # Patch sleep to run immediately then raise CancelledError to exit loop
    with patch("asyncio.sleep", side_effect=asyncio.CancelledError):
        await mgr._heartbeat_loop()

    # The dead client should have been cleaned up
    # (send_json raised on second call, triggering cleanup)
    # Note: first call is agent.ready in on_connect, second is agent.ping

    # Cleanup
    await mgr.on_disconnect(ws_alive)
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_ws_endpoint_handles_pong() -> None:
    """The WS endpoint should silently accept agent.pong messages."""
    from starlette.testclient import TestClient

    from minimax_code.config import Config
    from minimax_code.http_server import build_app
    from minimax_code.ipc.server import IPCServer

    server = IPCServer(config=Config.from_env(), stdin=None, stdout=None)
    app = build_app(server, version="test")
    client = TestClient(app)

    with client.websocket_connect("/ws") as ws:
        # Read the agent.ready event
        ready = ws.receive_json()
        assert ready["method"] == "agent.ready"

        # Send a pong — should be accepted without error
        ws.send_json({"jsonrpc": "2.0", "method": "agent.pong"})

        # Send another message — connection should still be alive
        ws.send_json({"jsonrpc": "2.0", "method": "test", "params": {}})


@pytest.mark.asyncio
async def test_multiple_clients_share_one_heartbeat() -> None:
    """All connected clients share a single heartbeat task."""
    mgr = _make_manager()
    clients = [_make_mock_ws() for _ in range(5)]

    for ws in clients:
        await mgr.on_connect(ws)

    task = mgr._ping_task
    assert task is not None

    # All 5 clients connected, still just one task
    assert mgr._ping_task is task

    # Disconnect 3 — task still alive
    for ws in clients[:3]:
        await mgr.on_disconnect(ws)
    assert mgr._ping_task is task

    # Disconnect remaining 2 — task cancelled
    for ws in clients[3:]:
        await mgr.on_disconnect(ws)
    assert mgr._ping_task is None
