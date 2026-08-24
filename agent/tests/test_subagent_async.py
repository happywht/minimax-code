"""v1.4.0 — sub-agent async lifecycle: background runs, partials, live events.

Commit 3 of the sub-agent lifecycle track. ``spawn_subagent`` gains a
``wait`` flag (default true): ``wait=False`` hands the full lifecycle to
a background task held by the module-level registry, the wall clock
returns *partial* results cooperatively instead of destroying the run,
and the core's tool callbacks are bridged into
``agent.subagent_progress`` events. ``check_subagent`` / ``wait_subagent``
collect the outcome.
"""

from __future__ import annotations

import asyncio
import gc
import uuid
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.tools.subagents import (
    CheckSubagentTool,
    SpawnSubagentTool,
    WaitSubagentTool,
)
from minimax_code.orchestrator.subagent import (
    SubAgentRuntime,
    get_background_run,
    pop_subagent_emit,
    register_subagent_emit,
    set_parent_session,
    set_subagent_runtime,
)
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path

ENVELOPE_BASE = {
    "iterations": 1,
    "tool_calls": [{"name": "read_file"}],
    "stub": False,
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    "truncated": False,
}


class ScriptedRuntime(SubAgentRuntime):
    """Configurable invoke for lifecycle tests (never touches the LLM).

    ``delay`` sleeps inside invoke; with ``cancel_aware`` the sleep
    polls ``handle.core.cancelled`` and returns a partial envelope the
    moment the wall clock cancels the core — exactly how a real
    ``AgentCore.run`` cooperatively winds down at its checkpoints.
    ``fire_callbacks`` drives the bridged on_tool_call/on_tool_result
    hooks so tests can observe the live-event mirror.
    """

    def __init__(self, *, delay: float = 0.0, cancel_aware: bool = False) -> None:
        super().__init__()
        self._delay = delay
        self._cancel_aware = cancel_aware
        self.fire_callbacks = False

    async def invoke(self, handle, *, session_id: str, request: str) -> dict[str, Any]:
        if self.fire_callbacks and handle.core is not None:
            await handle.core.on_tool_call({"name": "read_file"})
            await handle.core.on_tool_result({"name": "read_file"}, None)
        if self._delay:
            loop = asyncio.get_running_loop()
            deadline = loop.time() + self._delay
            while loop.time() < deadline:
                if (
                    self._cancel_aware
                    and handle.core is not None
                    and handle.core.cancelled
                ):
                    return {
                        "agent": handle.config.name,
                        "request": request,
                        "session_id": session_id,
                        "text": "partial findings so far",
                        "cancelled": True,
                        **ENVELOPE_BASE,
                    }
                await asyncio.sleep(0.01)
        return {
            "agent": handle.config.name,
            "request": request,
            "session_id": session_id,
            "text": "full result text",
            "cancelled": False,
            **ENVELOPE_BASE,
        }


@pytest.fixture
async def app_db(tmp_path: Path):
    from minimax_code import app
    from minimax_code.storage.dao.sessions import SessionsDAO

    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(app, "_DB_SINGLETON", db)
    app.set_sessions_dao(SessionsDAO(db))
    try:
        yield db
    finally:
        monkey.undo()
        app.set_sessions_dao(None)
        await db.close()


@pytest.fixture
def scripted():
    runtime = ScriptedRuntime()
    prev = set_subagent_runtime(runtime)
    try:
        yield runtime
    finally:
        set_subagent_runtime(prev)


async def _seed_agent(db: AsyncDatabase) -> str:
    dao = AgentDAO(db)
    name = f"async_{uuid.uuid4().hex[:8]}"
    await dao.upsert(name=name, system_prompt="you are a test agent")
    return name


async def _drive_to_completion(run_id: str) -> None:
    """Wait until the background task finished and was reaped."""
    task = get_background_run(run_id)
    assert task is not None, "background run must be registered"
    await asyncio.wait_for(asyncio.shield(task), timeout=10)
    await asyncio.sleep(0)  # let the done-callback run its pop


# ---------------------------------------------------------------------------
# Background spawn (wait=False)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wait_false_returns_immediately(app_db, scripted):
    name = await _seed_agent(app_db)
    tool = SpawnSubagentTool()
    out = await tool.run(agent_name=name, prompt="bg work", wait=False)
    assert out.success
    body = out.output
    assert body["status"] == "running"
    assert body["wait"] is False
    assert body["run_id"].startswith("run_")
    assert "hint" in body
    # The run row exists the moment spawn returns (check_subagent race-free).
    row = await AgentRunsDAO(app_db).get_run(body["run_id"])
    assert row is not None and row["status"] == "running"
    await _drive_to_completion(body["run_id"])


@pytest.mark.asyncio
async def test_background_run_completes_and_persists(app_db, scripted):
    name = await _seed_agent(app_db)
    out = await SpawnSubagentTool().run(agent_name=name, prompt="persist me", wait=False)
    run_id = out.output["run_id"]
    await _drive_to_completion(run_id)

    row = await AgentRunsDAO(app_db).get_run(run_id)
    assert row is not None
    assert row["status"] == "completed"
    meta = row["metadata"]
    assert meta["partial"] is False
    assert meta["result_text"] == "full result text"
    # Reaped from the registry — no leak.
    assert get_background_run(run_id) is None


@pytest.mark.asyncio
async def test_background_strong_reference_survives_gc(app_db):
    """Module-level registry holds the task; the GC cannot collect it."""
    name = await _seed_agent(app_db)
    slow = ScriptedRuntime(delay=0.08)
    prev = set_subagent_runtime(slow)
    try:
        tool = SpawnSubagentTool()
        out = await tool.run(agent_name=name, prompt="gc test", wait=False)
        run_id = out.output["run_id"]
        del tool, out
        gc.collect()
        await _drive_to_completion(run_id)
        row = await AgentRunsDAO(app_db).get_run(run_id)
        assert row is not None and row["status"] == "completed"
    finally:
        set_subagent_runtime(prev)


# ---------------------------------------------------------------------------
# check_subagent / wait_subagent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_subagent_running_then_finished(app_db):
    name = await _seed_agent(app_db)
    slow = ScriptedRuntime(delay=0.15)
    prev = set_subagent_runtime(slow)
    try:
        out = await SpawnSubagentTool().run(
            agent_name=name, prompt="poll me", wait=False
        )
        run_id = out.output["run_id"]

        check = await CheckSubagentTool().run(run_id=run_id)
        assert check.success
        assert check.output["status"] == "running"

        await _drive_to_completion(run_id)
        # Task reaped → the check falls through to the persisted row.
        final = await CheckSubagentTool().run(run_id=run_id)
        assert final.success
        assert final.output["status"] == "completed"
        assert final.output["text"] == "full result text"
    finally:
        set_subagent_runtime(prev)


@pytest.mark.asyncio
async def test_check_subagent_unknown_run(app_db):
    out = await CheckSubagentTool().run(run_id="run_does_not_exist")
    assert not out.success
    assert "unknown run_id" in (out.error or "")


@pytest.mark.asyncio
async def test_wait_subagent_collects_result(app_db, scripted):
    name = await _seed_agent(app_db)
    spawn_out = await SpawnSubagentTool().run(
        agent_name=name, prompt="collect me", wait=False
    )
    run_id = spawn_out.output["run_id"]

    out = await WaitSubagentTool().run(run_id=run_id, timeout_s=10)
    assert out.success
    body = out.output
    assert body["status"] == "completed"
    assert body["text"] == "full result text"
    assert body["iterations"] == 1
    assert body["usage"] == {"prompt_tokens": 10, "completion_tokens": 5}


@pytest.mark.asyncio
async def test_wait_subagent_timeout_keeps_run_alive(app_db):
    name = await _seed_agent(app_db)
    slow = ScriptedRuntime(delay=0.4)
    prev = set_subagent_runtime(slow)
    try:
        out = await SpawnSubagentTool().run(
            agent_name=name, prompt="slow work", wait=False
        )
        run_id = out.output["run_id"]

        timed_out = await WaitSubagentTool().run(run_id=run_id, timeout_s=0.05)
        assert timed_out.success
        assert timed_out.output["status"] == "running"
        assert timed_out.output["timeout"] is True

        # The timeout killed only the shield wrapper — the run itself
        # finishes normally and persists.
        await _drive_to_completion(run_id)
        row = await AgentRunsDAO(app_db).get_run(run_id)
        assert row is not None and row["status"] == "completed"
    finally:
        set_subagent_runtime(prev)


# ---------------------------------------------------------------------------
# Wall-clock partial (wait=True)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_wall_clock_timeout_returns_partial(app_db, monkeypatch):
    monkeypatch.setenv("MINIMAX_CODE_SUBAGENT_TIMEOUT_S", "0.1")
    name = await _seed_agent(app_db)
    slow = ScriptedRuntime(delay=2.0, cancel_aware=True)
    prev = set_subagent_runtime(slow)
    try:
        tool = SpawnSubagentTool()
        out = await tool.run(agent_name=name, prompt="long work")  # wait=True
        assert out.success
        body = out.output
        assert body["cancelled"] is True
        assert body["partial"] is True
        assert body["text"] == "partial findings so far"  # partial kept
        assert body["tool_calls"]  # accumulated work kept

        row = await AgentRunsDAO(app_db).get_run(body["run_id"])
        assert row is not None
        assert row["status"] == "cancelled"
        assert row["metadata"]["partial"] is True
        assert row["metadata"]["result_text"] == "partial findings so far"
    finally:
        set_subagent_runtime(prev)


@pytest.mark.asyncio
async def test_wall_clock_partial_event_flow(app_db, monkeypatch):
    monkeypatch.setenv("MINIMAX_CODE_SUBAGENT_TIMEOUT_S", "0.1")
    name = await _seed_agent(app_db)
    slow = ScriptedRuntime(delay=2.0, cancel_aware=True)
    prev = set_subagent_runtime(slow)
    parent = f"parent_{uuid.uuid4().hex[:8]}"
    events: list[tuple[str, dict[str, Any]]] = []

    async def fake_emit(event: str, payload: dict[str, Any]) -> None:
        events.append((event, payload))

    register_subagent_emit(parent, fake_emit)
    token = set_parent_session(parent)
    try:
        tool = SpawnSubagentTool()
        out = await tool.run(agent_name=name, prompt="eventful partial")
        assert out.success
        assert out.output["partial"] is True

        statuses = [p["status"] for _, p in events]
        assert statuses[0] == "started"
        assert statuses[-1] == "cancelled"
        assert events[-1][1]["progress"] == 1.0
        assert events[-1][1].get("text") == "partial findings so far"
    finally:
        from minimax_code.orchestrator.subagent import reset_parent_session

        reset_parent_session(token)
        pop_subagent_emit(parent)
        set_subagent_runtime(prev)


# ---------------------------------------------------------------------------
# Live tool-event bridge
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_realtime_tool_events_bridged(app_db, scripted):
    scripted.fire_callbacks = True
    name = await _seed_agent(app_db)
    parent = f"parent_{uuid.uuid4().hex[:8]}"
    events: list[tuple[str, dict[str, Any]]] = []

    async def fake_emit(event: str, payload: dict[str, Any]) -> None:
        events.append((event, payload))

    register_subagent_emit(parent, fake_emit)
    token = set_parent_session(parent)
    try:
        tool = SpawnSubagentTool()
        out = await tool.run(agent_name=name, prompt="bridge test")
        assert out.success
    finally:
        from minimax_code.orchestrator.subagent import reset_parent_session

        reset_parent_session(token)
        pop_subagent_emit(parent)

    statuses = [p["status"] for _, p in events]
    assert statuses[0] == "started"
    assert "tool_call" in statuses
    assert "tool_result" in statuses
    assert statuses[-1] == "completed"
    # Bridged events carry the wire-shape keys the frontend expects.
    tool_events = [p for _, p in events if p["status"].startswith("tool")]
    assert all(p["parent_session_id"] == parent for p in tool_events)
    assert all(0.0 <= p["progress"] <= 1.0 for p in tool_events)
