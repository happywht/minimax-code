"""Durable cursors must survive missed notifications, failed writes and restart."""

from __future__ import annotations

import asyncio
import sqlite3

import pytest

from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase


@pytest.fixture
async def database(tmp_path):
    db = AsyncDatabase(tmp_path / "events.db")
    await db.migrate()
    await SessionsDAO(db).create(id="session")
    try:
        yield db
    finally:
        await db.close()


async def reject_events(database):
    await database.execute("""CREATE TRIGGER reject_event BEFORE INSERT ON run_events
        BEGIN SELECT RAISE(ABORT, 'event disk fault'); END""")


async def test_replay_survives_restart_and_missing_broadcast(database):
    from minimax_code.ipc.builtins import _RunRecorder

    async def dropped_notification(*args):
        raise ConnectionError("browser disconnected")

    dao = AgentRunsDAO(database)
    recorder = _RunRecorder(dao=dao, db=database, emit=dropped_notification,
                            session_id="session", title="lost notification",
                            assistant_message_id="assistant")
    with pytest.raises(ConnectionError):
        await recorder.create()
    first = await dao.snapshot(recorder.run_id)
    assert first["cursor"] == 1
    step = await dao.create_step(run_id=recorder.run_id, session_id="session", kind="tool_call")
    await dao.complete_step(step["id"], payload={"exit_code": 0})
    await dao.update_run_status(recorder.run_id, status="completed")
    await database.close()
    reopened = AsyncDatabase(database.path)
    try:
        page = await AgentRunsDAO(reopened).events(recorder.run_id, after_seq=first["cursor"], limit=2)
        assert [item["seq"] for item in page["events"]] == [2, 3]
        assert page["has_more"]
        last = await AgentRunsDAO(reopened).events(recorder.run_id, after_seq=page["next_seq"])
        assert last["events"][0]["data"]["run"]["status"] == "completed"
        assert not last["has_more"]
        assert len({event["id"] for event in page["events"] + last["events"]}) == 3
    finally:
        await reopened.close()


async def test_concurrent_steps_have_distinct_ordinals_and_event_sequences(database):
    dao = AgentRunsDAO(database)
    run = await dao.create_run(session_id="session")
    steps = await asyncio.gather(*(dao.create_step(
        run_id=run["id"], session_id="session", kind="thought", title=str(i)
    ) for i in range(20)))
    assert sorted(step["ordinal"] for step in steps) == list(range(1, 21))
    page = await dao.events(run["id"])
    assert [event["seq"] for event in page["events"]] == list(range(1, 22))
    assert (await dao.snapshot(run["id"]))["cursor"] == 21


@pytest.mark.parametrize("operation", ["create", "status", "step", "complete_step", "recover"])
async def test_event_insert_failure_rolls_back_corresponding_state(database, operation):
    dao = AgentRunsDAO(database)
    run = await dao.create_run(id="existing", session_id="session")
    step = await dao.create_step(run_id=run["id"], session_id="session", kind="tool_call")
    before = await dao.snapshot(run["id"])
    await reject_events(database)
    with pytest.raises(sqlite3.IntegrityError, match="event disk fault"):
        if operation == "create":
            await dao.create_run(id="rejected", session_id="session")
        elif operation == "status":
            await dao.update_run_status(run["id"], status="completed")
        elif operation == "step":
            await dao.create_step(run_id=run["id"], session_id="session", kind="final")
        elif operation == "complete_step":
            await dao.complete_step(step["id"])
        else:
            await dao.recover_orphans()
    assert await dao.get_run("rejected") is None
    assert await dao.snapshot(run["id"]) == before


async def test_cancelled_write_rolls_back_state_and_does_not_advance_cursor(database, monkeypatch):
    from minimax_code.storage.dao import runs

    entered = asyncio.Event()

    async def suspended_event(*args):
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(runs, "_append_event", suspended_event)
    dao = AgentRunsDAO(database)
    task = asyncio.create_task(dao.create_run(id="cancelled-write", session_id="session"))
    await asyncio.wait_for(entered.wait(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await dao.get_run("cancelled-write") is None
    assert await database.fetchall("SELECT * FROM run_events") == []


async def test_snapshot_waits_for_committed_state_and_cursor(database, monkeypatch):
    from minimax_code.storage.dao import runs

    dao = AgentRunsDAO(database)
    run = await dao.create_run(session_id="session")
    original = runs._append_event
    entered, release = asyncio.Event(), asyncio.Event()

    async def suspended_event(*args):
        entered.set()
        await release.wait()
        await original(*args)

    monkeypatch.setattr(runs, "_append_event", suspended_event)
    writer = asyncio.create_task(dao.update_run_status(run["id"], status="completed"))
    await asyncio.wait_for(entered.wait(), 2)
    reader = asyncio.create_task(dao.snapshot(run["id"]))
    await asyncio.sleep(0)
    assert not reader.done()
    release.set()
    await writer
    snapshot = await reader
    assert snapshot["run"]["status"] == "completed"
    assert snapshot["cursor"] == 2


async def test_recovery_closes_unknown_steps_once_and_retains_evidence(database):
    dao = AgentRunsDAO(database)
    run = await dao.create_run(session_id="session")
    step = await dao.create_step(run_id=run["id"], session_id="session", kind="tool_call",
                                 payload={"args": {"path": "report.txt"}})
    assert (await dao.recover_orphans())["recovered"] == 1
    snapshot = await dao.snapshot(run["id"])
    recovered_step = next(item for item in snapshot["steps"] if item["id"] == step["id"])
    assert recovered_step["status"] == "failed"
    assert recovered_step["payload"]["args"]["path"] == "report.txt"
    event = (await dao.events(run["id"]))["events"][-1]
    assert event["type"] == "recovered"
    assert event["data"]["step"]["payload"]["requires_review"] is True
    assert (await dao.recover_orphans())["recovered"] == 0
    assert await dao.snapshot(run["id"]) == snapshot


async def test_event_scope_validation_and_cascade(database):
    dao = AgentRunsDAO(database)
    first = await dao.create_run(session_id="session")
    await SessionsDAO(database).create(id="other")
    second = await dao.create_run(session_id="other")
    with pytest.raises(ValueError, match="session must match"):
        await dao.create_step(run_id=first["id"], session_id="other", kind="thought")
    for after, limit in [(-1, 1), (True, 1), (1.5, 1), (2, 1), (0, 501), (0, False)]:
        with pytest.raises(ValueError):
            await dao.events(first["id"], after_seq=after, limit=limit)
    assert await dao.events("missing") is None
    assert await dao.snapshot("missing") is None
    assert await dao.events(first["id"], after_seq=1) == {
        "events": [], "next_seq": 1, "has_more": False}
    await database.execute("DELETE FROM sessions WHERE id = 'session'")
    rows = await database.fetchall("SELECT run_id FROM run_events")
    assert [row["run_id"] for row in rows] == [second["id"]]


async def test_events_rpc_and_snapshot_cursor(database, monkeypatch):
    from minimax_code import app
    from minimax_code.ipc.client import IPCClient

    monkeypatch.setattr(app, "_DB_SINGLETON", database)
    run = await AgentRunsDAO(database).create_run(session_id="session")
    client = IPCClient()
    snapshot = await client.request("run.steps", {"run_id": run["id"]})
    assert snapshot["cursor"] == 1
    events = await client.request("run.events", {"run_id": run["id"]})
    assert events["events"][0]["data"]["run"] == snapshot["run"]
    for params in [{"run_id": run["id"], "limit": True}, {"run_id": run["id"], "after_seq": "0"},
                   {"run_id": run["id"], "extra": 1}, {"run_id": "missing"}]:
        with pytest.raises(Exception, match="invalid run.events|unknown run_id"):
            await client.request("run.events", params)


async def test_export_import_roundtrip_and_legacy_import_clear_stale_events(database):
    from minimax_code.ipc.handlers_data import dump_database, restore_database

    dao = AgentRunsDAO(database)
    run = await dao.create_run(session_id="session")
    before = await dao.events(run["id"])
    envelope = await dump_database(database)
    await dao.update_run_status(run["id"], status="completed")
    with pytest.raises(ValueError, match="unsupported on Schema037"):
        await restore_database(database, envelope)
    assert (await dao.get_run(run["id"]))["status"] == "completed"
    assert (await dao.events(run["id"]))["next_seq"] == before["next_seq"] + 1
    # Partial old exports must not leave events describing pre-import state.
    del envelope["tables"]["run_events"]
    envelope["schema_version"] = 29
    with pytest.raises(ValueError, match="unsupported on Schema037"):
        await restore_database(database, envelope)
    assert (await dao.get_run(run["id"]))["status"] == "completed"
