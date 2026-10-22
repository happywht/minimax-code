from __future__ import annotations

import asyncio
import importlib
import sqlite3

import pytest

from minimax_code.storage.dao.runs import AgentRunsDAO, RequestConflictError
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase, Database
from minimax_code.storage.migrations import discover_migrations, ensure_migration_table


@pytest.fixture
async def dao(tmp_path):
    db = AsyncDatabase(tmp_path / "runs.db")
    await db.connect()
    await db.migrate()
    await SessionsDAO(db).create(id="s", title="session")
    try:
        yield AgentRunsDAO(db)
    finally:
        await db.close()


async def admit(dao, key="request", content="work", **kwargs):
    return await dao.admit_run(session_id="s", request={"mode": "chat", "content": content},
                               title=content, metadata={"model_snapshot": {"model": "a"}},
                               request_key=key, **kwargs)


async def test_message_and_run_link_roll_back_together_if_journal_fails(dao):
    from minimax_code.storage.dao.messages import MessagesDAO

    run, _ = await admit(dao)
    await dao.claim_run(run["id"])
    await dao._db.execute("""CREATE TRIGGER fail_journal BEFORE INSERT ON run_events
        BEGIN SELECT RAISE(FAIL, 'journal unavailable'); END""")
    messages = MessagesDAO(dao._db)
    with pytest.raises(sqlite3.IntegrityError):
        await messages.create(id="user", session_id="s", role="user", run_id=run["id"])
    assert await messages.get("user") is None
    assert (await dao.get_run(run["id"]))["user_message_id"] is None


async def test_message_link_cannot_cross_session_or_append_to_finished_run(dao):
    from minimax_code.storage.dao.messages import MessagesDAO

    run, _ = await admit(dao)
    await SessionsDAO(dao._db).create(id="other")
    messages = MessagesDAO(dao._db)
    with pytest.raises(ValueError, match="session"):
        await messages.create(id="wrong", session_id="other", role="user", run_id=run["id"])
    assert await messages.get("wrong") is None
    await dao.terminate_run(run["id"], status="cancelled", error="stop")
    with pytest.raises(ValueError, match="terminated"):
        await messages.create(id="late", session_id="s", role="assistant", run_id=run["id"])
    assert await messages.get("late") is None


async def test_concurrent_duplicate_admission_commits_one_request_and_event(dao):
    results = await asyncio.gather(*(admit(dao) for _ in range(12)))
    assert sum(created for _, created in results) == 1
    assert len({run["id"] for run, _ in results}) == 1
    run = results[0][0]
    assert run["status"] == "queued" and run["started_at"] is None
    assert (await dao.events(run["id"]))["next_seq"] == 1
    assert "request" not in run and "result" not in run
    assert (await dao.request_payload(run["id"]))["content"] == "work"
    with pytest.raises(RequestConflictError):
        await admit(dao, content="different")
    assert (await dao.events(run["id"]))["next_seq"] == 1


async def test_admission_and_claim_roll_back_if_event_cannot_be_written(dao):
    await dao._db.execute("""CREATE TRIGGER fail_journal BEFORE INSERT ON run_events
        BEGIN SELECT RAISE(FAIL, 'journal unavailable'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        await admit(dao)
    assert await dao.list_runs() == []
    await dao._db.execute("DROP TRIGGER fail_journal")
    run, _ = await admit(dao)
    await dao._db.execute("""CREATE TRIGGER fail_journal BEFORE INSERT ON run_events
        BEGIN SELECT RAISE(FAIL, 'journal unavailable'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        await dao.claim_run(run["id"])
    assert (await dao.get_run(run["id"]))["status"] == "queued"


async def test_claim_once_and_completion_receipt_is_atomic(dao):
    run, _ = await admit(dao)
    claims = await asyncio.gather(*(dao.claim_run(run["id"]) for _ in range(4)))
    assert sum(claim is not None for claim in claims) == 1
    receipt = {"text": "complete", "run_id": run["id"]}
    await dao.update_run_status(run["id"], status="completed", result=receipt)
    assert await dao.result_payload(run["id"]) == receipt
    retry, created = await admit(dao)
    assert not created and retry["status"] == "completed"
    events = (await dao.events(run["id"]))["events"]
    assert [event["type"] for event in events] == ["created", "status", "status"]
    assert "result" not in events[-1]["data"]["run"]


async def test_restart_marks_admitted_work_interrupted_without_executing_it(dao):
    queued, _ = await admit(dao, "queued")
    running, _ = await admit(dao, "running")
    await dao.claim_run(running["id"])
    step = await dao.create_step(run_id=running["id"], session_id="s", kind="tool_call")
    assert (await dao.recover_orphans())["recovered"] == 2
    assert (await dao.get_run(queued["id"]))["status"] == "interrupted"
    assert (await dao.get_run(running["id"]))["status"] == "interrupted"
    assert (await dao.get_step(step["id"]))["status"] == "failed"
    for run, expected in ((queued, False), (running, True)):
        event = (await dao.events(run["id"]))["events"][-1]
        assert event["data"]["step"]["payload"]["requires_review"] is expected
        assert await dao.result_payload(run["id"]) is None
    assert (await dao.recover_orphans())["recovered"] == 0
    assert (await admit(dao, "queued"))[1] is False


def old_database(path, version=30):
    db = Database(path)
    ensure_migration_table(db.raw_connection)
    for v, run in discover_migrations([]):
        if v > version:
            break
        # No live records yet; bootstrap only the pre-upgrade schema.
        run(db.raw_connection)
        db.execute("INSERT INTO schema_migrations VALUES (?, 'before')", (v,))
    db.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES ('s','test','t','t')")
    db.execute("INSERT INTO agent_runs(id,session_id,created_at) VALUES ('r','s','t')")
    db.execute("INSERT INTO agent_run_steps(id,run_id,session_id,kind,started_at) VALUES ('step','r','s','thought','t')")
    if version >= 30:
        db.execute("INSERT INTO run_events VALUES ('event','r',1,'created','t','{}')")
    return db


@pytest.mark.parametrize("old_version", [20, 27, 30])
def test_upgrade_preserves_child_rows_and_foreign_keys(tmp_path, old_version):
    with old_database(tmp_path / "upgrade.db", old_version) as db:
        db.migrate()
        assert db.fetchone("SELECT COUNT(*) FROM agent_run_steps")[0] == 1
        assert db.fetchone("SELECT COUNT(*) FROM run_events")[0] == (1 if old_version == 30 else 0)
        assert db.fetchone("PRAGMA foreign_keys")[0] == 1
        assert db.fetchall("PRAGMA foreign_key_check") == []
        db.execute("UPDATE agent_runs SET status='queued' WHERE id='r'")
        db.execute("DELETE FROM agent_runs WHERE id='r'")
        assert db.fetchone("SELECT COUNT(*) FROM agent_run_steps")[0] == 0
        assert db.fetchone("SELECT COUNT(*) FROM run_events")[0] == 0


def test_migration_rebuild_preserves_custom_index_and_trigger(tmp_path):
    with old_database(tmp_path / "upgrade.db") as db:
        db.execute("CREATE INDEX custom_run_title ON agent_runs(title)")
        db.execute("CREATE TABLE test_audit(value TEXT)")
        db.execute("""CREATE TRIGGER custom_run_update AFTER UPDATE ON agent_runs
            BEGIN INSERT INTO test_audit VALUES (NEW.status); END""")
        db.migrate()
        db.execute("UPDATE agent_runs SET status='interrupted' WHERE id='r'")
        assert db.fetchone("SELECT value FROM test_audit")[0] == "interrupted"
        assert db.fetchone("SELECT name FROM sqlite_master WHERE name='custom_run_title'")


@pytest.mark.parametrize("fault", ["after-drop", "bad-reference"])
def test_rebuild_failure_rolls_back_and_restores_fk_enforcement(tmp_path, monkeypatch, fault):
    migration = importlib.import_module("minimax_code.storage.migrations.031_durable_run_admission")
    with old_database(tmp_path / "upgrade.db") as db:
        def fail(conn):
            migration.run(conn)
            if fault == "after-drop":
                raise RuntimeError("fault after rebuild")
            conn.execute("UPDATE agent_run_steps SET run_id='missing'")

        fail.requires_foreign_keys_off = True
        monkeypatch.setattr("minimax_code.storage.db.discover_migrations", lambda applied: [(31, fail)])
        with pytest.raises((RuntimeError, sqlite3.IntegrityError)):
            db.migrate()
        assert 31 not in db.applied_versions()
        assert db.fetchone("PRAGMA foreign_keys")[0] == 1
        assert db.fetchone("SELECT run_id FROM agent_run_steps")[0] == "r"
        assert db.fetchone("SELECT COUNT(*) FROM run_events")[0] == 1
        assert "request_key" not in [r[1] for r in db.fetchall("PRAGMA table_info(agent_runs)")]
