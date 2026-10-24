"""M4-A tests — engine registry, engine.* namespace, AppServer contract.

- ``engine.list`` / ``engine.status``: native always available; codex
  probed live; unknown ids rejected.
- ``run.start`` pins engines at admission: unknown / unavailable /
  not-yet-wired engines are *named* and rejected — never a silent
  fallback to native.
- ``AppServerConnection`` speaks the Codex App Server protocol
  (line-delimited JSON-RPC) against a scripted fake: initialize
  handshake, id-matched responses, notification dispatch, and a
  server-originated approval request round trip.
"""

from __future__ import annotations

import asyncio
import io
import json
import sys
from unittest.mock import AsyncMock

import pytest

from minimax_code.config import Config
from minimax_code.engines import list_engines
from minimax_code.engines.codex import AppServerConnection
from minimax_code.ipc.handlers_engines import register_engine_handlers
from minimax_code.ipc.handlers_runs import register_run_handlers
from minimax_code.ipc.server import IPCServer

# ---------------------------------------------------------------------------
# engine.* namespace
# ---------------------------------------------------------------------------


class Ctx:
    def __init__(self, server):
        self.server = server
        self.reply = AsyncMock()
        self.reply_error = AsyncMock()


@pytest.fixture
async def server():
    srv = IPCServer(Config(), stdin=io.StringIO(), stdout=io.StringIO())
    register_engine_handlers(srv)
    register_run_handlers(srv)
    try:
        yield srv
    finally:
        # Drain background jobs — a leaked codex run task would hang the
        # event loop on teardown.
        from minimax_code.runs import get_run_service

        await get_run_service(srv).shutdown()


async def test_engine_list_includes_native_and_codex(server) -> None:
    ctx = Ctx(server)
    await server._handlers["engine.list"]({}, ctx)
    engines = ctx.reply.await_args.args[0]["engines"]
    ids = [e["id"] for e in engines]
    assert ids == ["native", "codex"]
    native = engines[0]
    assert native["available"] is True
    assert native["capabilities"]["streaming"] is True
    assert ctx.reply.await_args.args[0]["default"] == "native"


async def test_engine_status_reports_reason_for_unavailable(server, monkeypatch) -> None:
    import minimax_code.engines.codex as codex_mod

    async def _fake_probe(timeout_s: float = 5.0):
        from minimax_code.engines.base import EngineInfo

        return EngineInfo(id="codex", name="Codex", available=False,
                          unavailable_reason="codex CLI not found on PATH")
    monkeypatch.setattr(codex_mod, "_probe_codex_async", _fake_probe)

    ctx = Ctx(server)
    await server._handlers["engine.status"]({"engine_id": "codex"}, ctx)
    card = ctx.reply.await_args.args[0]["engine"]
    assert card["available"] is False
    assert "codex CLI not found" in card["unavailable_reason"]

    bad = Ctx(server)
    await server._handlers["engine.status"]({"engine_id": "nope"}, bad)
    bad.reply_error.assert_awaited_once()


async def test_run_start_codex_admits_and_names_failures(server, monkeypatch, tmp_path) -> None:
    """engine=codex admits durably and drives through the App Server.

    The driver is pointed at a scripted fake codex; the run completes
    with the fake's streamed answer, and a DEAD codex command names the
    failure in the run row — never a silent fallback to native.
    """
    from minimax_code import app
    from minimax_code.storage.dao.sessions import SessionsDAO
    from minimax_code.storage.db import AsyncDatabase

    db = AsyncDatabase(tmp_path / "codex.db")
    await db.connect()
    await db.migrate()
    sessions = SessionsDAO(db)
    monkeypatch.setattr(app, "init_runtime", AsyncMock())
    monkeypatch.setattr(app, "get_db", lambda: db)
    monkeypatch.setattr(app, "get_sessions_dao", lambda: sessions)

    fake = tmp_path / "fake_codex.py"
    fake.write_text(FAKE_CODEX, encoding="utf-8")
    monkeypatch.setenv("MINIMAX_CODE_CODEX_CMD",
                       json.dumps([sys.executable, str(fake)]))

    ctx = Ctx(server)
    await server._handlers["run.start"]({
        "session_id": "ses_codex", "content": "run the tests",
        "idempotency_key": "k1", "engine": "codex",
    }, ctx)
    if ctx.reply.await_args is None:
        pytest.fail(f'run.start failed: {ctx.reply_error.await_args_list}')
    created = ctx.reply.await_args.args[0]
    assert created["created"] is True
    run_id = created["run"]["id"]
    row = {"status": "queued"}
    for _ in range(300):
        await asyncio.sleep(0.02)
        row = await ctx_reply_run(server, run_id)
        if row["status"] in {"completed", "failed", "cancelled"}:
            break
    assert row["status"] == "completed", row
    receipt = row["result"]
    assert receipt["engine"] == "codex"
    assert "running tests" in receipt["text"]

    # A dead codex command names the failure in the run row.
    monkeypatch.setenv("MINIMAX_CODE_CODEX_CMD",
                       json.dumps([sys.executable, "-c", "import sys; sys.exit(3)"]))
    ctx2 = Ctx(server)
    await server._handlers["run.start"]({
        "session_id": "ses_codex", "content": "again",
        "idempotency_key": "k2", "engine": "codex",
    }, ctx2)
    run_id2 = ctx2.reply.await_args.args[0]["run"]["id"]
    row2 = {"status": "queued"}
    for _ in range(700):
        await asyncio.sleep(0.05)
        row2 = (await ctx_reply_run(server, run_id2))
        if row2["status"] in {"completed", "failed", "cancelled"}:
            break
    assert row2["status"] == "failed"

    # Unknown engine ids are rejected at the protocol layer (Literal),
    # before any engine lookup happens.
    ctx3 = Ctx(server)
    await server._handlers["run.start"]({
        "session_id": "ses_x", "content": "hi",
        "idempotency_key": "k3", "engine": "bogus",
    }, ctx3)
    assert "run.start requires" in str(ctx3.reply_error.await_args.args[1])
    await db.close()


async def ctx_reply_run(server, run_id):
    from minimax_code.app import get_db
    from minimax_code.storage.dao.runs import AgentRunsDAO

    dao = AgentRunsDAO(get_db())
    run = await dao.get_run(run_id)
    result = await dao.result_payload(run_id)
    return {**run, "result": result}


# ---------------------------------------------------------------------------
# AppServerConnection — protocol contract against a scripted fake
# ---------------------------------------------------------------------------

FAKE_CODEX = '''\
import json, sys
for raw in sys.stdin:
    req = json.loads(raw)
    method = req.get("method", "")
    rid = req.get("id")
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": rid,
                          "result": {"userAgent": {"version": "0.163.0"}}}), flush=True)
        continue
    if method == "thread/start":
        print(json.dumps({"jsonrpc": "2.0", "method": "thread/started",
                          "params": {"thread_id": "th_1"}}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "id": rid,
                          "result": {"thread_id": "th_1"}}), flush=True)
        continue
    if method == "turn/start":
        print(json.dumps({"jsonrpc": "2.0", "id": rid,
                          "result": {"turn_id": "turn_1"}}), flush=True)
        # A server-originated APPROVAL request — the adapter must answer.
        print(json.dumps({"jsonrpc": "2.0", "id": "srv-1",
                          "method": "execCommandApproval",
                          "params": {"conversationId": "th_1",
                                     "callId": "c1", "command": ["pytest"]}}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "method": "item/agentMessage/delta",
                          "params": {"delta": "running tests"}}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "method": "turn/completed",
                          "params": {"thread_id": "th_1", "turn_id": "turn_1"}}), flush=True)
        continue
    if rid is not None:
        print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {}}), flush=True)
'''


async def test_appserver_connection_contract(tmp_path) -> None:
    fake = tmp_path / "fake_codex.py"
    fake.write_text(FAKE_CODEX, encoding="utf-8")

    conn = AppServerConnection(cwd=tmp_path, codex_cmd=[sys.executable, str(fake)])
    await conn.start()
    try:
        notifications: list[dict] = []
        approvals: list[dict] = []
        conn.on_notification = notifications.append

        def on_server_request(req: dict) -> dict:
            approvals.append(req)
            return {"decision": "approved"}  # sync short-circuit reply

        conn.on_server_request = on_server_request

        init = await conn.request("initialize", {"clientInfo": {"name": "t", "version": "0"}},
                                  timeout=10)
        assert init["userAgent"]["version"] == "0.163.0"

        thread = await conn.request("thread/start", {"cwd": str(tmp_path)}, timeout=10)
        await asyncio.sleep(0.1)  # let the notification stream land
        assert thread["thread_id"] == "th_1"
        assert any(n["method"] == "thread/started" for n in notifications)

        await conn.request("turn/start", {"threadId": "th_1", "input": "run tests"},
                           timeout=10)
        await asyncio.sleep(0.2)
        assert approvals and approvals[0]["method"] == "execCommandApproval"
        deltas = [n for n in notifications if n["method"] == "item/agentMessage/delta"]
        assert deltas and deltas[0]["params"]["delta"] == "running tests"
        assert any(n["method"] == "turn/completed" for n in notifications)
    finally:
        await conn.close()


async def test_appserver_async_approval_round_trip(tmp_path) -> None:
    """The human-in-the-loop approval path: the handler returns a
    coroutine (it waits on a human), and the RESPONSE must be written
    back to the server when it settles — a broken reply path means the
    real Codex waits 300s and denies every command."""

    fake_lines: list[str] = []

    fake_src = """
import json, sys
for raw in sys.stdin:
    req = json.loads(raw)
    rid = req.get("id")
    method = req.get("method", "")
    if method == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": rid,
                          "result": {"userAgent": {"version": "0.163.0"}}}), flush=True)
    elif method == "turn/start":
        print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"turn_id": "t1"}}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "id": "srv-9",
                          "method": "execCommandApproval",
                          "params": {"conversationId": "th", "callId": "c"}}), flush=True)
        # wait for the approval response line, then finish
        resp = json.loads(sys.stdin.readline())
        print(json.dumps({"jsonrpc": "2.0", "method": "approval_echo",
                          "params": resp}), flush=True)
        print(json.dumps({"jsonrpc": "2.0", "method": "turn/completed",
                          "params": {}}), flush=True)
    elif rid is not None:
        print(json.dumps({"jsonrpc": "2.0", "id": rid, "result": {}}), flush=True)
"""
    fake = tmp_path / "fake_codex_async.py"
    fake.write_text(fake_src, encoding="utf-8")

    conn = AppServerConnection(cwd=tmp_path, codex_cmd=[sys.executable, str(fake)])
    await conn.start()
    try:
        notifications: list[dict] = []
        conn.on_notification = notifications.append

        async def human_handler(req: dict) -> dict:
            await asyncio.sleep(0.2)  # simulating the human thinking
            return {"decision": "approved"}

        conn.on_server_request = human_handler
        await conn.request("initialize", {"clientInfo": {"name": "t", "version": "0"}}, timeout=10)
        await conn.request("turn/start", {"threadId": "th", "input": "x"}, timeout=10)
        for _ in range(100):
            if any(n["method"] == "turn/completed" for n in notifications):
                break
            await asyncio.sleep(0.05)
        echoes = [n for n in notifications if n["method"] == "approval_echo"]
        assert echoes, "the approval response must reach the server"
        assert echoes[0]["params"].get("result", {}).get("decision") == "approved"
    finally:
        await conn.close()


async def test_engine_cards_via_list_engines() -> None:
    cards = await list_engines()
    assert [c.id for c in cards] == ["native", "codex"]
    assert cards[0].available is True
