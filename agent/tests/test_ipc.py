"""Smoke tests for the JSON-RPC over stdio bridge."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from minimax_code import __version__
from minimax_code.ipc.client import IPCClient
from minimax_code.ipc.protocol import (
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    Request,
)
from minimax_code.ipc.server import IPCServer


@pytest.fixture
async def isolated_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> AsyncIterator[None]:
    """Keep the chat integration test isolated from the live product DB."""
    monkeypatch.setattr("minimax_code.secrets.get_api_key", lambda: None)
    monkeypatch.delenv("MINIMAX_API_KEY", raising=False)
    monkeypatch.setenv("MINIMAX_CODE_DATA_DIR", str(tmp_path))
    yield

    import minimax_code.app as app_module
    from minimax_code.app import (
        get_db,
        set_progress_tracker,
        set_runtime,
        set_sessions_dao,
        set_subagent_llm,
    )

    db = get_db()
    if db is not None:
        await db.close()
    app_module._DB_SINGLETON = None
    app_module._PROVIDER_DAO_SINGLETON = None
    set_runtime(None)
    set_progress_tracker(None)
    set_sessions_dao(None)
    set_subagent_llm(None)


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
    assert result["version"] == __version__
    assert result["python"].startswith("3.")


@pytest.mark.asyncio
async def test_unknown_method_returns_error_envelope() -> None:
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
async def test_agent_send_message_streams_hello(
    isolated_runtime: None,
) -> None:
    client = IPCClient()
    reply = await client.request(
        "agent.send_message", {"content": "hello", "session_id": None}
    )
    # The in-process request completes after all stream events are emitted.
    # Drain the queue with a short idle window instead of waiting 30 seconds
    # for an arbitrary event count that a normal response never reaches.
    events = await client.collect_events(50, timeout=0.2)
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


@pytest.mark.asyncio
async def test_agent_send_message_persists_token_usage(
    isolated_runtime: None,
) -> None:
    """v1.1.3 — the persisted assistant row carries real usage.

    Before this, regular completions hit the DB with metadata=NULL and
    tokens_in=tokens_out=0, so the context indicator showed 0 after any
    session reload and session.stats summed zeroes.
    """
    client = IPCClient()
    reply = await client.request(
        "agent.send_message", {"content": "hello", "session_id": None}
    )
    session_id = reply["session_id"]

    from minimax_code.app import get_db

    db = get_db()
    assert db is not None
    row = await db.fetchone(
        "SELECT tokens_in, tokens_out, metadata FROM messages "
        "WHERE session_id = ? AND role = 'assistant' "
        "ORDER BY id DESC LIMIT 1",
        (session_id,),
    )
    assert row is not None, "expected a persisted assistant row"
    assert int(row["tokens_in"]) >= 1
    assert int(row["tokens_out"]) >= 1
    meta = json.loads(row["metadata"]) if row["metadata"] else None
    assert isinstance(meta, dict)
    assert int(meta.get("tokens_in", 0)) >= 1
    assert "thinking_count" in meta
