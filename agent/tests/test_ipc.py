"""Smoke tests for the JSON-RPC over stdio bridge."""

from __future__ import annotations

import asyncio
import json

import pytest

from minimax_code.ipc.client import IPCClient
from minimax_code.ipc.protocol import (
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    Request,
)
from minimax_code.ipc.server import IPCServer, Context
from minimax_code.app import register_app_handlers


@pytest.mark.asyncio
async def test_ping_round_trip() -> None:
    client = IPCClient()
    result = await client.request("ping")
    assert result["pong"] > 0
    assert "uptime_s" in result
    assert result["server"] == "minimax-code-agent"


@pytest.mark.asyncio
async def test_status() -> None:
    client = IPCClient()
    result = await client.request("status")
    assert result["agent"] == "minimax-code-agent"
    assert result["version"] == "0.7.0"
    assert result["python"].startswith("3.")


@pytest.mark.asyncio
async def test_unknown_method_returns_error_envelope() -> None:
    client = IPCClient()
    # Manually craft a request to an unknown method and verify the
    # server emits a JSON-RPC error envelope.
    from minimax_code.config import Config

    out = []
    server = IPCServer(
        config=Config.from_env(),
        stdin=__import__("io").StringIO(),
        stdout=__import__("io").StringIO(),
    )
    original_send = server._send

    async def capture(payload: bytes) -> None:
        await original_send(payload)
        out.append(server.stdout.getvalue().splitlines()[-1])

    server._send = capture  # type: ignore[assignment]
    req = Request(id="bad-1", method="does.not.exist", params=None)
    await server._handle_line(req.to_line() + "\n")
    assert out, "expected an error envelope"
    obj = json.loads(out[-1])
    assert obj["id"] == "bad-1"
    assert obj["error"]["code"] == METHOD_NOT_FOUND


@pytest.mark.asyncio
async def test_parse_error_envelope() -> None:
    client = IPCClient()
    # Forge a raw bad-JSON message.
    from minimax_code.config import Config

    captured = []
    server = IPCServer(
        config=Config.from_env(),
        stdin=__import__("io").StringIO(),
        stdout=__import__("io").StringIO(),
    )
    original_send = server._send

    async def capture(payload: bytes) -> None:
        await original_send(payload)
        captured.append(server.stdout.getvalue().splitlines()[-1])

    server._send = capture  # type: ignore[assignment]
    await server._handle_line("{not json}\n")
    assert captured
    obj = json.loads(captured[-1])
    assert obj["error"]["code"] == PARSE_ERROR


@pytest.mark.asyncio
async def test_agent_send_message_streams_hello(monkeypatch: pytest.MonkeyPatch) -> None:
    # Force mock mode so the test works regardless of keyring contents.
    monkeypatch.setattr("minimax_code.secrets.get_api_key", lambda: None)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)

    client = IPCClient()
    events_task = asyncio.create_task(client.collect_events(50, timeout=30.0))
    reply = await client.request(
        "agent.send_message", {"content": "hello", "session_id": None}
    )
    events = await events_task
    # In mock mode the text starts with "[mock]"; with a real API
    # key configured the LLM returns a genuine response.  Both paths
    # are valid — we only assert that we got a non-empty reply.
    assert reply["text"], "expected non-empty assistant reply"
    assert reply["session_id"].startswith("ses_")
    # Events include message_chunks and possibly status events.
    chunks = [e for e in events if e["event"] == "agent.message_chunk"]
    assert chunks, "expected at least one message_chunk event"
    # The final message_chunk must have done=True.
    final = [e for e in chunks if e["data"].get("done") is True]
    assert final, "expected a final done event"
