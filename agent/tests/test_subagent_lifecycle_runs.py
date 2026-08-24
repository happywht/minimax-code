"""v1.4.0 — tool-path sub-agent lifecycle: run rows + progress events.

Commit 2 of the sub-agent lifecycle track. ``spawn_subagent`` now
persists an ``agent_runs`` row (``mode='subagent'``, migration 028),
routes ``agent.subagent_progress`` events back to the parent session
via the module-level route table, and registers its core in
``_ACTIVE_RUNS`` so ``agent.cancel_subagent`` reaches it.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.tools.subagents import SpawnSubagentTool
from minimax_code.orchestrator.subagent import (
    SubAgentRuntime,
    pop_subagent_emit,
    register_subagent_emit,
    set_parent_session,
    set_subagent_runtime,
)
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.storage.migrations import discover_migrations


def _migration_028_run() -> Any:
    """Resolve migration 028's ``run`` via the discovery pipeline."""
    for version, run in discover_migrations(applied=set()):
        if version == 28:
            return run
    raise AssertionError("migration 028 not discovered")


# ---------------------------------------------------------------------------
# Migrations
# ---------------------------------------------------------------------------


def _create_legacy_agent_runs(conn: sqlite3.Connection) -> None:
    """Rebuild the pre-028 schema (mode CHECK without 'subagent')."""
    conn.execute("DROP TABLE IF EXISTS agent_runs")
    conn.execute("DROP TABLE IF EXISTS agent_runs_new")
    conn.execute(
        """
        CREATE TABLE agent_runs (
            id              TEXT PRIMARY KEY,
            session_id      TEXT NOT NULL,
            mode            TEXT NOT NULL DEFAULT 'chat'
                            CHECK (mode IN ('chat', 'plan', 'execute', 'team')),
            status          TEXT NOT NULL DEFAULT 'running',
            title           TEXT NOT NULL DEFAULT '',
            user_message_id TEXT,
            assistant_message_id TEXT,
            created_at      TEXT NOT NULL,
            started_at      TEXT,
            completed_at    TEXT,
            error           TEXT,
            metadata        JSON
        )
        """
    )


def _table_sql(conn: sqlite3.Connection) -> str:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_runs'"
    ).fetchone()
    return (row[0] or "").lower() if row else ""


def test_migration_028_widens_mode_check():
    conn = sqlite3.connect(":memory:")
    try:
        _create_legacy_agent_runs(conn)
        conn.execute(
            "INSERT INTO agent_runs (id, session_id, mode, status, created_at) "
            "VALUES ('r1', 's1', 'team', 'completed', '2026-01-01')"
        )
        before = conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]

        _migration_028_run()(conn)

        sql = _table_sql(conn)
        assert "'subagent'" in sql
        assert "'team'" in sql  # previous widening preserved
        # Rows survive the four-step rebuild.
        assert conn.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0] == before
        kept = conn.execute("SELECT mode FROM agent_runs WHERE id='r1'").fetchone()
        assert kept[0] == "team"
        # No leftover temp table.
        leftover = conn.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE name='agent_runs_new'"
        ).fetchone()[0]
        assert leftover == 0
        # Regression guard: the rebuilt table must keep a covering index for
        # the hot "by session" query. Migration 021 left same-named ``_new``
        # indexes on the OLD table, so reusing those names with IF NOT
        # EXISTS silently skipped creation and the DROP TABLE deleted the
        # only index — a bare table scan (caught by test_index_audit).
        idx_rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='agent_runs'"
        ).fetchall()
        assert idx_rows, "agent_runs lost all indexes in the rebuild"
    finally:
        conn.close()


def test_migration_028_guard_is_idempotent():
    conn = sqlite3.connect(":memory:")
    try:
        _create_legacy_agent_runs(conn)
        _migration_028_run()(conn)
        sql_after_first = _table_sql(conn)

        # Second run must be a no-op (guard sees 'subagent').
        _migration_028_run()(conn)
        assert _table_sql(conn) == sql_after_first
    finally:
        conn.close()


def test_migration_028_skips_when_table_missing():
    conn = sqlite3.connect(":memory:")
    try:
        _migration_028_run()(conn)  # no agent_runs table — must not raise
        assert "agent_runs" not in _table_sql(conn)
    finally:
        conn.close()


@pytest.mark.asyncio
async def test_fresh_database_carries_subagent_mode(tmp_path: Path):
    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    try:
        await db.migrate()
        row = await db.fetchone(
            "SELECT sql FROM sqlite_master WHERE type='table' AND name='agent_runs'"
        )
        assert row is not None and "'subagent'" in str(row[0]).lower()
    finally:
        await db.close()


# ---------------------------------------------------------------------------
# Live-database fixtures for the tool path
# ---------------------------------------------------------------------------


@pytest.fixture
async def live_db(tmp_path: Path):
    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    yield db
    await db.close()


@pytest.fixture
def app_db(live_db: AsyncDatabase, monkeypatch: pytest.MonkeyPatch):
    """Point the app-level singletons at the temp database."""
    from minimax_code import app
    from minimax_code.storage.dao.sessions import SessionsDAO

    monkeypatch.setattr(app, "_DB_SINGLETON", live_db)
    app.set_sessions_dao(SessionsDAO(live_db))
    yield live_db
    app.set_sessions_dao(None)


@pytest.fixture
def stub_runtime():
    prev = set_subagent_runtime(SubAgentRuntime())
    yield SubAgentRuntime()
    set_subagent_runtime(prev)


async def _seed_agent(db: AsyncDatabase) -> str:
    dao = AgentDAO(db)
    name = f"lifecycle_{uuid.uuid4().hex[:8]}"
    await dao.upsert(name=name, system_prompt="you are a test agent")
    return name


# ---------------------------------------------------------------------------
# Run-row persistence
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_persists_subagent_run_row(app_db, stub_runtime):
    name = await _seed_agent(app_db)
    parent = f"parent_{uuid.uuid4().hex[:8]}"
    token = set_parent_session(parent)
    try:
        tool = SpawnSubagentTool()
        out = await tool.run(agent_name=name, prompt="do the thing")
    finally:
        from minimax_code.orchestrator.subagent import reset_parent_session

        reset_parent_session(token)

    assert out.success
    run_id = out.output["run_id"]
    assert run_id.startswith("run_")
    assert out.output["parent_session_id"] == parent

    runs_dao = AgentRunsDAO(app_db)
    row = await runs_dao.get_run(run_id)
    assert row is not None
    assert row["mode"] == "subagent"
    assert row["status"] == "completed"
    meta = row["metadata"]
    assert meta["parent_session_id"] == parent
    assert meta["agent_name"] == name
    assert meta["source"] == "tool"
    assert meta["prompt"] == "do the thing"

    # The synthetic sub-session got a placeholder row (FK precondition).
    sessions = await app_db.fetchall(
        "SELECT id FROM sessions WHERE id = ?", (out.output["session_id"],)
    )
    assert len(sessions) == 1

    # Parent session history is NOT polluted — the sub-agent ran in its
    # own synthetic session.
    parent_msgs = await app_db.fetchall(
        "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?", (parent,)
    )
    assert parent_msgs[0]["n"] == 0


@pytest.mark.asyncio
async def test_spawn_without_parent_still_persists(app_db, stub_runtime):
    """No parent session (CLI / stdio): run row stored with null parent."""
    name = await _seed_agent(app_db)
    tool = SpawnSubagentTool()
    out = await tool.run(agent_name=name, prompt="orphan run")
    assert out.success
    row = await AgentRunsDAO(app_db).get_run(out.output["run_id"])
    assert row is not None
    assert row["status"] == "completed"
    assert row["metadata"]["parent_session_id"] is None


# ---------------------------------------------------------------------------
# Event routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_emits_started_then_completed(app_db, stub_runtime):
    name = await _seed_agent(app_db)
    parent = f"parent_{uuid.uuid4().hex[:8]}"
    events: list[tuple[str, dict[str, Any]]] = []

    async def fake_emit(event: str, payload: dict[str, Any]) -> None:
        events.append((event, payload))

    register_subagent_emit(parent, fake_emit)
    token = set_parent_session(parent)
    try:
        tool = SpawnSubagentTool()
        out = await tool.run(agent_name=name, prompt="emit please")
    finally:
        from minimax_code.orchestrator.subagent import reset_parent_session

        reset_parent_session(token)
        pop_subagent_emit(parent)

    assert out.success
    assert events, "expected progress events through the route"
    assert all(ev == "agent.subagent_progress" for ev, _ in events)
    statuses = [p["status"] for _, p in events]
    assert statuses[0] == "started"
    assert statuses[-1] == "completed"

    run_id = out.output["run_id"]
    for _, payload in events:
        # Wire shape parity with handlers_agents._emit_subagent_progress.
        assert payload["run_id"] == run_id
        assert payload["parent_session_id"] == parent
        assert 0.0 <= payload["progress"] <= 1.0
        assert isinstance(payload["summary"], str)
    final = events[-1][1]
    assert final["progress"] == 1.0
    assert isinstance(final.get("text"), str)


@pytest.mark.asyncio
async def test_spawn_without_route_is_silent(app_db, stub_runtime):
    """No emit registered (stdio / CLI): the run completes normally."""
    name = await _seed_agent(app_db)
    token = set_parent_session(f"noroute_{uuid.uuid4().hex[:8]}")
    try:
        tool = SpawnSubagentTool()
        out = await tool.run(agent_name=name, prompt="quiet run")
    finally:
        from minimax_code.orchestrator.subagent import reset_parent_session

        reset_parent_session(token)
    assert out.success
    # Persistence still happened — only the event push was skipped.
    row = await AgentRunsDAO(app_db).get_run(out.output["run_id"])
    assert row is not None and row["status"] == "completed"


# ---------------------------------------------------------------------------
# Cancel channel
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_registers_active_run_during_invoke(app_db, stub_runtime):
    """While invoke is in flight the core sits in ``_ACTIVE_RUNS`` keyed
    by the run id — the exact table ``agent.cancel_subagent`` reads."""
    from minimax_code.ipc.builtins import _ACTIVE_RUNS
    from minimax_code.orchestrator.subagent import SubAgentRuntime as RT

    seen: dict[str, Any] = {}

    class InspectingRuntime(RT):
        async def invoke(self, handle, *, session_id, request):  # type: ignore[override]
            for rid, entry in list(_ACTIVE_RUNS.items()):
                if entry.get("type") == "subagent":
                    seen[rid] = entry
            return await super().invoke(handle, session_id=session_id, request=request)

    name = await _seed_agent(app_db)
    prev = set_subagent_runtime(InspectingRuntime())
    try:
        tool = SpawnSubagentTool()
        out = await tool.run(agent_name=name, prompt="inspect me")
    finally:
        set_subagent_runtime(prev)

    assert out.success
    run_id = out.output["run_id"]
    assert run_id in seen, "run must be registered in _ACTIVE_RUNS during invoke"
    assert seen[run_id]["core"] is not None
    # After the run finishes the entry is popped (no leak).
    assert run_id not in _ACTIVE_RUNS


# ---------------------------------------------------------------------------
# list_runs mode filter
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_runs_mode_filter(app_db):
    dao = AgentRunsDAO(app_db)
    from minimax_code.storage.dao.sessions import SessionsDAO

    sessions = SessionsDAO(app_db)
    await sessions.create(id="sess_a", title="A")

    await dao.create_run(session_id="sess_a", mode="subagent", title="sub run")
    await dao.create_run(session_id="sess_a", mode="chat", title="chat run")

    only_sub = await dao.list_runs(session_id="sess_a", mode="subagent")
    assert [r["title"] for r in only_sub] == ["sub run"]
    only_chat = await dao.list_runs(session_id="sess_a", mode="chat")
    assert [r["title"] for r in only_chat] == ["chat run"]
    everything = await dao.list_runs(session_id="sess_a")
    assert len(everything) == 2  # no mode filter — unchanged legacy behaviour

    with pytest.raises(ValueError):
        await dao.list_runs(session_id="sess_a", mode="bogus")
