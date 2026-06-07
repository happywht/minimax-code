"""Unit tests for the SQLite storage layer.

We exercise every DAO on both the sync and async paths (WAL mode
lets us share the same file), the migration runner, the FK + UNIQUE
constraints the schema relies on, and a small concurrency probe.

Tests are organized into "scenarios" — each scenario groups a few
related ``test_*`` functions and shares a single database fixture
where possible. Each test is self-contained and re-creates the
data it needs; we don't rely on test ordering.
"""

from __future__ import annotations

import asyncio
import sqlite3
import threading
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest

from minimax_code.storage.dao.agents import AgentsDAO
from minimax_code.storage.dao.messages import MessagesDAO
from minimax_code.storage.dao.mobile_devices import MobileDevicesDAO
from minimax_code.storage.dao.permissions import PermissionRulesDAO
from minimax_code.storage.dao.scheduled_jobs import ScheduledJobsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.dao.skills import SkillsDAO
from minimax_code.storage.dao.tasks import TasksDAO
from minimax_code.storage.db import (
    AsyncDatabase,
    Database,
    default_data_dir,
    default_database_path,
    make_temp_database_path,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Fresh temp path for each test."""
    return make_temp_database_path(tmp_path)


@pytest.fixture
def sync_db(db_path: Path) -> Iterator[Database]:
    """A migrated synchronous Database, auto-closed."""
    db = Database(db_path)
    db.migrate()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture
async def async_db(db_path: Path) -> AsyncDatabase:
    """A migrated async Database."""
    db = AsyncDatabase(db_path)
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def id_factory() -> callable:
    """Generate prefixed unique IDs so test output is greppable."""

    def _make(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:10]}"

    return _make


# ---------------------------------------------------------------------------
# Path resolution / connection lifecycle
# ---------------------------------------------------------------------------


def test_default_paths_are_windows_roaming_appdata() -> None:
    """On Windows, default_data_dir() must point at %APPDATA%\\MiniMaxCode."""
    if not _is_windows():
        pytest.skip("Windows-specific path test")
    assert str(default_data_dir()).endswith("MiniMaxCode")
    assert "AppData" in str(default_data_dir())
    assert str(default_database_path()).endswith("data.db")
    assert default_database_path().parent == default_data_dir()


def test_make_temp_database_path_is_unique(tmp_path: Path) -> None:
    p1 = make_temp_database_path(tmp_path)
    p2 = make_temp_database_path(tmp_path)
    assert p1 != p2
    assert p1.parent == tmp_path


def test_database_creates_parent_dirs(tmp_path: Path) -> None:
    nested = tmp_path / "a" / "b" / "c" / "data.db"
    db = Database(nested)
    try:
        assert nested.parent.is_dir()
        db.migrate()
        assert nested.exists()
    finally:
        db.close()


def test_sync_migrate_is_idempotent(sync_db: Database) -> None:
    """Re-running migrate() must apply zero new versions."""
    assert sync_db.migrate() == []
    assert sync_db.applied_versions() == {1, 2, 3, 4, 5, 6, 7, 8, 9}


@pytest.mark.asyncio
async def test_async_migrate_is_idempotent(async_db: AsyncDatabase) -> None:
    assert await async_db.migrate() == []
    assert await async_db.applied_versions() == {1, 2, 3, 4, 5, 6, 7, 8, 9}


@pytest.mark.asyncio
async def test_async_and_sync_share_the_same_file(db_path: Path) -> None:
    """Apply migrations with async, then write a row via sync, then read via async."""
    adb = AsyncDatabase(db_path)
    await adb.connect()
    await adb.migrate()
    # write via sync
    sdb = Database(db_path)
    try:
        sdb.execute(
            "INSERT INTO sessions (id, title, created_at, updated_at, archived) "
            "VALUES (?, ?, ?, ?, ?)",
            ("ses_x", "shared", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", 0),
        )
    finally:
        sdb.close()
    # read via async
    row = await adb.fetchone("SELECT * FROM sessions WHERE id = ?", ("ses_x",))
    assert row is not None
    assert row["title"] == "shared"
    await adb.close()


# ---------------------------------------------------------------------------
# Sessions DAO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sessions_crud(async_db: AsyncDatabase, id_factory) -> None:
    dao = SessionsDAO(async_db)
    sid = id_factory("ses")
    created = await dao.create(id=sid, title="hi", model="gpt-4o")
    assert created["id"] == sid
    assert created["title"] == "hi"
    assert created["model"] == "gpt-4o"
    assert created["archived"] is False

    # get
    fetched = await dao.get(sid)
    assert fetched is not None and fetched["title"] == "hi"

    # update
    await dao.update(sid, title="hi 2", model="gpt-4o-mini")
    after = await dao.get(sid)
    assert after["title"] == "hi 2"
    assert after["model"] == "gpt-4o-mini"
    # updated_at should be different
    assert after["updated_at"] >= created["updated_at"]

    # archive / unarchive
    assert (await dao.archive(sid))["archived"] is True
    assert (await dao.unarchive(sid))["archived"] is False

    # delete
    assert await dao.delete(sid) is True
    assert await dao.get(sid) is None
    # second delete returns False
    assert await dao.delete(sid) is False


@pytest.mark.asyncio
async def test_sessions_list_filter_pagination(async_db: AsyncDatabase, id_factory) -> None:
    dao = SessionsDAO(async_db)
    for i in range(5):
        await dao.create(id=id_factory("ses"), title=f"t{i}", archived=(i % 2 == 0))

    all_s = await dao.list()
    assert len(all_s) == 5
    archived = await dao.list(archived=True)
    assert len(archived) == 3  # i=0,2,4
    assert all(s["archived"] for s in archived)
    active = await dao.list(archived=False)
    assert len(active) == 2

    # pagination
    page1 = await dao.list(limit=2, offset=0, order_by="title ASC")
    page2 = await dao.list(limit=2, offset=2, order_by="title ASC")
    assert page1[0]["title"] == "t0"
    assert page2[0]["title"] == "t2"
    assert len(page1) == 2 and len(page2) == 2

    # search
    matches = await dao.list(search="t3")
    assert len(matches) == 1 and matches[0]["title"] == "t3"

    # count
    assert await dao.count() == 5
    assert await dao.count(archived=True) == 3


# ---------------------------------------------------------------------------
# Messages DAO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_crud_with_tool_calls_and_json(async_db: AsyncDatabase, id_factory) -> None:
    sdao = SessionsDAO(async_db)
    mdao = MessagesDAO(async_db)
    sid = id_factory("ses")
    await sdao.create(id=sid, title="m")

    mid_user = id_factory("msg")
    m_user = await mdao.create(
        id=mid_user, session_id=sid, role="user", content="hello"
    )
    assert m_user["role"] == "user"
    assert m_user["content"] == "hello"
    assert m_user["tool_calls"] is None

    # assistant with tool_calls
    tool_calls = [
        {
            "id": "call_1",
            "type": "function",
            "function": {"name": "list_directory", "arguments": '{"path":"."}'},
        }
    ]
    mid_asst = id_factory("msg")
    m_asst = await mdao.create(
        id=mid_asst,
        session_id=sid,
        role="assistant",
        content="",
        tool_calls=tool_calls,
        parent_id=mid_user,
        tokens_in=5,
        tokens_out=10,
    )
    assert m_asst["tool_calls"] == tool_calls
    assert m_asst["parent_id"] == mid_user
    assert m_asst["tokens_in"] == 5
    assert m_asst["tokens_out"] == 10

    # tool result message
    mid_tool = id_factory("msg")
    m_tool = await mdao.create(
        id=mid_tool,
        session_id=sid,
        role="tool",
        content="file1.py\nfile2.py",
        tool_call_id="call_1",
    )
    assert m_tool["role"] == "tool"
    assert m_tool["tool_call_id"] == "call_1"

    # list_for_session
    msgs = await mdao.list_for_session(sid)
    assert [m["id"] for m in msgs] == [mid_user, mid_asst, mid_tool]
    assert await mdao.count_for_session(sid) == 3

    # role filter
    tools = await mdao.list_for_session(sid, role="tool")
    assert len(tools) == 1 and tools[0]["id"] == mid_tool

    # token totals
    totals = await mdao.token_totals_for_session(sid)
    assert totals == {"tokens_in": 5, "tokens_out": 10}


@pytest.mark.asyncio
async def test_messages_invalid_role_rejected(async_db: AsyncDatabase, id_factory) -> None:
    sdao = SessionsDAO(async_db)
    mdao = MessagesDAO(async_db)
    sid = id_factory("ses")
    await sdao.create(id=sid, title="m")
    with pytest.raises(ValueError, match="role"):
        await mdao.create(id=id_factory("msg"), session_id=sid, role="bot")


@pytest.mark.asyncio
async def test_messages_cascade_on_session_delete(
    async_db: AsyncDatabase, id_factory
) -> None:
    """Deleting a session must cascade to its messages and tasks."""
    sdao = SessionsDAO(async_db)
    mdao = MessagesDAO(async_db)
    tdao = TasksDAO(async_db)
    sid = id_factory("ses")
    await sdao.create(id=sid, title="x")
    for _ in range(3):
        await mdao.create(
            id=id_factory("msg"), session_id=sid, role="user", content="hi"
        )
    await tdao.create(id=id_factory("task"), session_id=sid, title="t1")
    assert await mdao.count_for_session(sid) == 3
    assert await tdao.count(session_id=sid) == 1

    await sdao.delete(sid)
    assert await mdao.count_for_session(sid) == 0
    assert await tdao.count(session_id=sid) == 0


# ---------------------------------------------------------------------------
# Tasks DAO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_tasks_status_transitions(async_db: AsyncDatabase, id_factory) -> None:
    sdao = SessionsDAO(async_db)
    tdao = TasksDAO(async_db)
    sid = id_factory("ses")
    await sdao.create(id=sid, title="x")
    tid = id_factory("task")
    await tdao.create(id=tid, session_id=sid, title="build")

    pending = await tdao.get(tid)
    assert pending["status"] == "pending"
    assert pending["started_at"] is None
    assert pending["completed_at"] is None

    # start -> running, started_at auto-filled
    started = await tdao.update_status(tid, status="running", progress=10)
    assert started["status"] == "running"
    assert started["started_at"] is not None
    assert started["progress"] == 10

    # progress update
    p = await tdao.update_progress(tid, 50)
    assert p["progress"] == 50

    # finish -> completed, completed_at auto-filled
    done = await tdao.update_status(tid, status="completed", progress=100)
    assert done["status"] == "completed"
    assert done["completed_at"] is not None
    assert done["progress"] == 100

    # filter
    completed = await tdao.list(status="completed")
    assert len(completed) == 1
    assert completed[0]["id"] == tid


@pytest.mark.asyncio
async def test_tasks_invalid_status_rejected(async_db: AsyncDatabase, id_factory) -> None:
    sdao = SessionsDAO(async_db)
    tdao = TasksDAO(async_db)
    sid = id_factory("ses")
    await sdao.create(id=sid, title="x")
    with pytest.raises(ValueError):
        await tdao.create(id=id_factory("task"), session_id=sid, title="t", status="bogus")
    with pytest.raises(ValueError):
        await tdao.create(
            id=id_factory("task"), session_id=sid, title="t", progress=150
        )


# ---------------------------------------------------------------------------
# Skills DAO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_skills_unique_name_upsert(async_db: AsyncDatabase, id_factory) -> None:
    dao = SkillsDAO(async_db)
    first = await dao.upsert(
        id=id_factory("skl"),
        name="commit-helper",
        path="/skills/commit-helper",
        description="drafts commits",
    )
    assert first["enabled"] is True

    # Upsert by same name updates instead of creating a duplicate.
    updated = await dao.upsert(
        id=id_factory("skl"),  # different id — should be ignored on conflict
        name="commit-helper",
        path="/skills/commit-helper",
        version="0.2.0",
        description="drafts commits better",
        enabled=False,
    )
    assert updated["id"] == first["id"]  # same row id
    assert updated["version"] == "0.2.0"
    assert updated["description"] == "drafts commits better"
    assert updated["enabled"] is False
    # only one row
    assert await dao.count() == 1

    # enable / disable
    assert (await dao.enable("commit-helper"))["enabled"] is True
    assert (await dao.disable("commit-helper"))["enabled"] is False


@pytest.mark.asyncio
async def test_skills_unique_name_db_constraint(
    async_db: AsyncDatabase, id_factory
) -> None:
    """Bypassing the DAO must still fail with a UNIQUE error."""
    dao = SkillsDAO(async_db)
    await dao.upsert(
        id=id_factory("skl"), name="dedup", path="/a", description=""
    )
    with pytest.raises(sqlite3.IntegrityError):
        await async_db.execute(
            "INSERT INTO skills (id, name, path, description, when_to_use, "
            "enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (id_factory("skl"), "dedup", "/b", "", "", 1, "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )


# ---------------------------------------------------------------------------
# Scheduled jobs DAO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scheduled_jobs_due_query(async_db: AsyncDatabase, id_factory) -> None:
    dao = ScheduledJobsDAO(async_db)
    # due: enabled, next_run_at in the past
    await dao.create(
        id=id_factory("job"),
        name="j1",
        cron_expr="* * * * *",
        next_run_at="2020-01-01T00:00:00Z",
        enabled=True,
        payload={"kind": "noop"},
    )
    # not due: future
    await dao.create(
        id=id_factory("job"),
        name="j2",
        cron_expr="* * * * *",
        next_run_at="2999-01-01T00:00:00Z",
        enabled=True,
    )
    # not due: disabled even if past
    await dao.create(
        id=id_factory("job"),
        name="j3",
        cron_expr="* * * * *",
        next_run_at="2020-01-01T00:00:00Z",
        enabled=False,
    )
    due = await dao.due()
    assert len(due) == 1
    assert due[0]["name"] == "j1"
    assert due[0]["payload"] == {"kind": "noop"}


@pytest.mark.asyncio
async def test_scheduled_jobs_record_run(async_db: AsyncDatabase, id_factory) -> None:
    dao = ScheduledJobsDAO(async_db)
    jid = id_factory("job")
    await dao.create(id=jid, name="j", cron_expr="* * * * *")
    out = await dao.record_run(jid, last_run_at="2026-01-01T00:00:00Z", next_run_at="2026-01-01T00:01:00Z")
    assert out["last_run_at"] == "2026-01-01T00:00:00Z"
    assert out["next_run_at"] == "2026-01-01T00:01:00Z"


# ---------------------------------------------------------------------------
# Agents DAO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agents_unique_name_constraint(async_db: AsyncDatabase, id_factory) -> None:
    dao = AgentsDAO(async_db)
    await dao.create(id=id_factory("ag"), name="explore", system_prompt="you explore")
    with pytest.raises(sqlite3.IntegrityError):
        await async_db.execute(
            "INSERT INTO agents (id, name, system_prompt, tool_allowlist, model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (id_factory("ag"), "explore", "", None, None, "2026-01-01T00:00:00Z"),
        )


@pytest.mark.asyncio
async def test_agents_update_tool_allowlist(async_db: AsyncDatabase, id_factory) -> None:
    dao = AgentsDAO(async_db)
    aid = id_factory("ag")
    await dao.create(id=aid, name="build", system_prompt="", tool_allowlist=["file_ops", "terminal"])
    out = await dao.update(aid, tool_allowlist=["terminal", "search"])
    assert out["tool_allowlist"] == ["terminal", "search"]


# ---------------------------------------------------------------------------
# Permission rules DAO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_permission_rules_action_constraint(async_db: AsyncDatabase, id_factory) -> None:
    dao = PermissionRulesDAO(async_db)
    with pytest.raises(ValueError):
        await dao.create(id=id_factory("pr"), tool_pattern="*", action="permit")
    # ok
    await dao.create(id=id_factory("pr"), tool_pattern="file_ops:*", action="allow", scope="global")


@pytest.mark.asyncio
async def test_permission_rules_delete_for_pattern(async_db: AsyncDatabase, id_factory) -> None:
    dao = PermissionRulesDAO(async_db)
    for i in range(3):
        await dao.create(id=id_factory("pr"), tool_pattern=f"tool_{i}:*", action="allow")
    deleted = await dao.delete_for_pattern("tool_1:*")
    assert deleted == 1
    assert await dao.count() == 2


# ---------------------------------------------------------------------------
# Mobile devices DAO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_mobile_devices_unique_device_id(async_db: AsyncDatabase, id_factory) -> None:
    dao = MobileDevicesDAO(async_db)
    first = await dao.upsert(
        id=id_factory("dev"), device_id="phone-abc", name="My Phone", public_key="PUBKEY1"
    )
    second = await dao.upsert(
        id=id_factory("dev"),  # different row id
        device_id="phone-abc",  # same natural key
        name="My Renamed Phone",
        public_key="PUBKEY2",
        last_seen_at="2026-01-01T00:00:00Z",
    )
    assert first["id"] == second["id"]  # same row
    assert second["name"] == "My Renamed Phone"
    assert second["public_key"] == "PUBKEY2"
    assert second["last_seen_at"] == "2026-01-01T00:00:00Z"
    assert await dao.count() == 1

    # touch updates last_seen_at
    touched = await dao.touch("phone-abc")
    assert touched is not None
    assert touched["last_seen_at"] is not None


# ---------------------------------------------------------------------------
# Indexes — query plans must use them
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_messages_index_used_for_session_listing(async_db: AsyncDatabase, id_factory) -> None:
    """The session+created_at index must be selected by the planner."""
    sdao = SessionsDAO(async_db)
    mdao = MessagesDAO(async_db)
    sid = id_factory("ses")
    await sdao.create(id=sid, title="i")
    for i in range(50):
        await mdao.create(
            id=id_factory("msg"), session_id=sid, role="user", content=f"m{i}"
        )
    # ANALYZE first so the planner has stats
    await async_db.execute("ANALYZE")
    plan_rows = await async_db.fetchall(
        "EXPLAIN QUERY PLAN SELECT * FROM messages WHERE session_id = ? "
        "ORDER BY created_at ASC",
        (sid,),
    )
    # EXPLAIN QUERY PLAN returns rows with columns (id, parent, notused, detail);
    # we only care about the ``detail`` column.
    plan_text = " ".join(str(r["detail"]) for r in plan_rows)
    # The plan should reference our index, not "SCAN TABLE messages".
    assert "USING INDEX" in plan_text or "USING COVERING INDEX" in plan_text or "idx_messages_session_created" in plan_text


@pytest.mark.asyncio
async def test_sessions_index_used_for_listing(async_db: AsyncDatabase) -> None:
    sdao = SessionsDAO(async_db)
    for i in range(20):
        await sdao.create(id=f"ses_idx_{i:03d}", title=f"s{i}")
    await async_db.execute("ANALYZE")
    rows = await async_db.fetchall(
        "EXPLAIN QUERY PLAN SELECT * FROM sessions WHERE archived = 0 "
        "ORDER BY updated_at DESC"
    )
    plan_text = " ".join(str(r["detail"]) for r in rows)
    assert "idx_sessions_archived_updated" in plan_text or "idx_sessions_updated" in plan_text


# ---------------------------------------------------------------------------
# Concurrency — multiple writers, no corruption
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_concurrent_writers_no_lost_rows(async_db: AsyncDatabase, id_factory) -> None:
    """Fire 50 concurrent inserts from many tasks; expect 50 rows."""
    sdao = SessionsDAO(async_db)
    n_writers = 8
    n_each = 25  # 200 total sessions

    async def writer(prefix: str) -> list[str]:
        ids = []
        for i in range(n_each):
            sid = id_factory(f"ses_{prefix}_{i}")
            await sdao.create(id=sid, title=f"{prefix}-{i}")
            ids.append(sid)
        return ids

    results = await asyncio.gather(*(writer(f"w{i}") for i in range(n_writers)))
    all_ids = [i for sub in results for i in sub]
    assert len(all_ids) == n_writers * n_each
    count = await sdao.count()
    assert count == n_writers * n_each


@pytest.mark.asyncio
async def test_sync_thread_safety(db_path: Path, id_factory) -> None:
    """Sync Database serializes writers; no SQLite 'database is locked' errors."""
    sdb = Database(db_path)
    sdb.migrate()

    from minimax_code.storage.dao import create_session_sync

    errors: list[BaseException] = []

    def worker(prefix: str) -> None:
        try:
            for i in range(20):
                create_session_sync(
                    sdb, id=id_factory(f"thread_{prefix}_{i}"), title=f"{prefix}-{i}"
                )
        except BaseException as exc:  # pragma: no cover — failure path
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(f"t{i}",)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    row = sdb.fetchone("SELECT COUNT(*) AS n FROM sessions")
    assert int(row["n"]) == 4 * 20
    sdb.close()


# ---------------------------------------------------------------------------
# Sync DAO helpers
# ---------------------------------------------------------------------------


def test_sync_session_dao_helpers(sync_db: Database, id_factory) -> None:
    from minimax_code.storage.dao.sessions import (
        create_sync,
        delete_sync,
        get_sync,
        list_sync,
    )

    sid = id_factory("ses")
    create_sync(sync_db, id=sid, title="sync")
    assert get_sync(sync_db, sid)["title"] == "sync"
    listing = list_sync(sync_db, order_by="title ASC", limit=5)
    assert any(s["id"] == sid for s in listing)
    assert delete_sync(sync_db, sid) is True
    assert get_sync(sync_db, sid) is None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _is_windows() -> bool:
    import sys

    return sys.platform == "win32"
