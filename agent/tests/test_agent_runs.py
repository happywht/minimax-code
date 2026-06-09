from __future__ import annotations

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
