from __future__ import annotations

from types import SimpleNamespace

import pytest

from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path


@pytest.fixture
async def async_db(tmp_path):
    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def id_factory():
    def _make(prefix: str) -> str:
        import uuid

        return f"{prefix}_{uuid.uuid4().hex[:10]}"

    return _make


@pytest.mark.asyncio
async def test_agent_runs_dao_lifecycle(async_db, id_factory) -> None:
    sessions = SessionsDAO(async_db)
    dao = AgentRunsDAO(async_db)
    sid = id_factory("ses")
    await sessions.create(id=sid, title="timeline")

    run = await dao.create_run(session_id=sid, title="Fix tests")
    assert run["session_id"] == sid
    assert run["status"] == "running"
    assert run["metadata"] is None

    step = await dao.create_step(
        run_id=run["id"],
        session_id=sid,
        kind="tool_call",
        title="exec_command",
        summary='{"cmd":["pytest"]}',
        tool_call_id="call_1",
        tool_name="exec_command",
        payload={"args": {"cmd": ["pytest"]}},
    )
    assert step["ordinal"] == 1
    assert step["payload"]["args"]["cmd"] == ["pytest"]

    done_step = await dao.complete_step(
        step["id"],
        summary="Completed",
        payload={"exit_code": 0},
    )
    assert done_step["status"] == "completed"
    assert done_step["completed_at"] is not None
    assert done_step["duration_ms"] is not None

    final = await dao.update_run_status(
        run["id"],
        status="completed",
        metadata={"iterations": 1},
    )
    assert final["status"] == "completed"
    assert final["metadata"]["iterations"] == 1

    runs = await dao.list_runs(session_id=sid)
    steps = await dao.list_steps(run["id"])
    assert [r["id"] for r in runs] == [run["id"]]
    assert [s["id"] for s in steps] == [step["id"]]


@pytest.mark.asyncio
async def test_run_handlers_list_and_steps(monkeypatch, tmp_path, id_factory) -> None:
    from minimax_code import app as app_module
    from minimax_code.ipc.client import IPCClient

    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    monkeypatch.setattr(app_module, "_DB_SINGLETON", db)
    try:
        sessions = SessionsDAO(db)
        dao = AgentRunsDAO(db)
        sid = id_factory("ses")
        await sessions.create(id=sid, title="timeline")
        run = await dao.create_run(session_id=sid, title="History")
        await dao.create_step(
            run_id=run["id"],
            session_id=sid,
            kind="thought",
            status="completed",
            title="Thinking",
            ordinal=1,
        )

        client = IPCClient()
        listed = await client.request("run.list", {"session_id": sid})
        assert listed["runs"][0]["id"] == run["id"]

        detail = await client.request("run.steps", {"run_id": run["id"]})
        assert detail["run"]["id"] == run["id"]
        assert detail["steps"][0]["title"] == "Thinking"
    finally:
        monkeypatch.setattr(app_module, "_DB_SINGLETON", None)
        await db.close()


@pytest.mark.asyncio
async def test_run_recorder_tool_call_is_idempotent_per_tool_call_id(
    async_db, id_factory
) -> None:
    """A duplicate tool_call event for the same tool_call_id must not create
    a second step — the duplicate used to orphan the first step in "running"
    state forever on the timeline (spinner + red pair on exec_command)."""
    from minimax_code.ipc.builtins import _RunRecorder

    sessions = SessionsDAO(async_db)
    dao = AgentRunsDAO(async_db)
    sid = id_factory("ses")
    await sessions.create(id=sid, title="dup guard")

    events: list[str] = []

    async def _emit(event: str, _payload: dict) -> None:
        events.append(event)

    recorder = _RunRecorder(
        dao=dao,
        db=async_db,
        emit=_emit,
        session_id=sid,
        title="test run",
        assistant_message_id=None,
    )
    await recorder.create()

    call = {"id": "call_dup_1", "name": "exec_command", "args": {"cmd": ["ls"]}}
    await recorder.tool_call(call)
    await recorder.tool_call(call)  # duplicate — must be a no-op

    steps = await dao.list_steps(recorder.run_id)
    tool_steps = [s for s in steps if s["kind"] == "tool_call"]
    assert len(tool_steps) == 1, "duplicate tool_call event must not add a step"
    assert events.count("run.step.started") == 1
    # And tool_result completes THE one step (no orphan left "running").
    await recorder.tool_result(
        call,
        SimpleNamespace(success=True, output="ok", error=None, metadata=None),
    )
    steps = await dao.list_steps(recorder.run_id)
    tool_steps = [s for s in steps if s["kind"] == "tool_call"]
    assert len(tool_steps) == 1
    assert tool_steps[0]["status"] == "completed"
