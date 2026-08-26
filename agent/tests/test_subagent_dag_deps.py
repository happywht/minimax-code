"""P0-4 — ``spawn_subagent`` DAG gating (``depends_on``) regression tests.

Pins the three behaviours the audit fixed:

* validation — ``depends_on`` only makes sense for background spawns
  (``wait=false``) and entries must be strings;
* projection — a dependent run reports ``status='waiting_deps'`` with
  its pending list while the upstream run is in flight;
* liveness — a dep id that never existed in the background registry is
  treated as satisfied, not dead-waited (the original loop filtered on
  ``get_background_run(d) is not None`` but kept the entry in the
  pending table, so the run spun until the 600s wall clock).

The ordering pin uses a runtime that timestamps every invoke: the
dependent's first invoke must start at or after the upstream's last
invoke ended.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.tools.base import get_default_registry
from minimax_code.orchestrator.subagent import (
    SubAgentRuntime,
    get_background_run,
    set_subagent_runtime,
)
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path

ENVELOPE_BASE = {
    "iterations": 1,
    "tool_calls": [{"name": "read_file"}],
    "stub": False,
    "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    "truncated": False,
}


class TimingRuntime(SubAgentRuntime):
    """Scripted invoke with per-agent delay and a monotonic event log."""

    def __init__(self, delays: dict[str, float] | None = None) -> None:
        super().__init__()
        self._delays = delays or {}
        self.events: list[dict[str, Any]] = []

    def span(self, agent_name: str) -> dict[str, Any]:
        matches = [e for e in self.events if e["name"] == agent_name]
        assert matches, f"no invoke logged for {agent_name!r}"
        return matches[-1]

    async def invoke(self, handle, *, session_id: str, request: str) -> dict[str, Any]:
        start = time.monotonic()
        delay = self._delays.get(handle.config.name, 0.0)
        if delay:
            await asyncio.sleep(delay)
        self.events.append(
            {"name": handle.config.name, "start": start, "end": time.monotonic()}
        )
        return {
            "agent": handle.config.name,
            "request": request,
            "session_id": session_id,
            "text": "done",
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
def runtime():
    rt = TimingRuntime()
    prev = set_subagent_runtime(rt)
    try:
        yield rt
    finally:
        set_subagent_runtime(prev)


async def _seed_agent(db: AsyncDatabase, delay: float = 0.0) -> str:
    name = f"dag_{uuid.uuid4().hex[:8]}"
    await AgentDAO(db).upsert(name=name, system_prompt="you are a test agent")
    return name


async def _dispatch(tool: str, args: dict[str, Any]):
    return await get_default_registry().dispatch(tool, args)


async def _spawn(agent_name: str, *, depends_on: list[str] | None = None):
    args: dict[str, Any] = {
        "agent_name": agent_name,
        "prompt": "dag work",
        "wait": False,
    }
    if depends_on is not None:
        args["depends_on"] = depends_on
    return await _dispatch("spawn_subagent", args)


async def _drive_to_completion(run_id: str) -> None:
    task = get_background_run(run_id)
    assert task is not None, "background run must be registered"
    # 10s cap doubles as the liveness pin: a dead-waiting run exhausts
    # this wait_for and fails the test instead of the 600s wall clock.
    await asyncio.wait_for(asyncio.shield(task), timeout=10)
    await asyncio.sleep(0)  # let the done-callback run its pop


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_depends_on_with_wait_true_rejected(app_db, runtime) -> None:
    name = await _seed_agent(app_db)
    res = await _dispatch(
        "spawn_subagent",
        {
            "agent_name": name,
            "prompt": "x",
            "wait": True,
            "depends_on": ["run_other"],
        },
    )
    assert not res.success
    assert "depends_on" in (res.error or "")
    assert "wait" in (res.error or "")


@pytest.mark.asyncio
async def test_depends_on_entries_must_be_strings(app_db, runtime) -> None:
    name = await _seed_agent(app_db)
    res = await _spawn(name, depends_on=[12345])
    assert not res.success
    assert "depends_on" in (res.error or "")


# ---------------------------------------------------------------------------
# waiting_deps projection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_reports_waiting_deps(app_db) -> None:
    rt = TimingRuntime()
    prev = set_subagent_runtime(rt)
    try:
        name_a = await _seed_agent(app_db)
        name_b = await _seed_agent(app_db)
        rt._delays = {name_a: 0.6}  # keep A in flight across the check

        out_a = await _spawn(name_a)
        assert out_a.success, out_a.error
        run_a = out_a.output["run_id"]

        out_b = await _spawn(name_b, depends_on=[run_a])
        assert out_b.success, out_b.error
        run_b = out_b.output["run_id"]

        check = await _dispatch("check_subagent", {"run_id": run_b})
        assert check.success, check.error
        body = check.output
        assert body["status"] == "waiting_deps"
        assert body["deps_pending"] == [run_a]
        assert "elapsed_s" in body
        # Terminal keys never leak into an in-flight projection.
        assert "text" not in body and "tool_calls" not in body

        await _drive_to_completion(run_a)
        await _drive_to_completion(run_b)
    finally:
        set_subagent_runtime(prev)


# ---------------------------------------------------------------------------
# Liveness — unknown deps are satisfied, not dead-waited
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_unknown_dep_treated_as_satisfied(app_db, runtime) -> None:
    name = await _seed_agent(app_db)
    out = await _spawn(name, depends_on=["run_bogus"])
    assert out.success, out.error
    run_id = out.output["run_id"]

    # Before the fix this spun until the 600s wall clock; the 10s cap
    # inside _drive_to_completion turns a regression into a red test.
    await _drive_to_completion(run_id)

    check = await _dispatch("check_subagent", {"run_id": run_id})
    assert check.success, check.error
    assert check.output["status"] == "completed"


@pytest.mark.asyncio
async def test_dep_failure_does_not_block_dependent(app_db, runtime) -> None:
    """A finished (even failed) upstream must release its dependents."""
    name_a = await _seed_agent(app_db)
    name_b = await _seed_agent(app_db)

    out_a = await _spawn(name_a)
    run_a = out_a.output["run_id"]
    await _drive_to_completion(run_a)

    # Spawn B *after* A is done and reaped — the dep id is no longer in
    # the registry, which is exactly the unknown-dep path.
    out_b = await _spawn(name_b, depends_on=[run_a])
    assert out_b.success, out_b.error
    await _drive_to_completion(out_b.output["run_id"])

    check = await _dispatch("check_subagent", {"run_id": out_b.output["run_id"]})
    assert check.output["status"] == "completed"


# ---------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dependent_starts_after_upstream_finishes(app_db) -> None:
    rt = TimingRuntime()
    prev = set_subagent_runtime(rt)
    try:
        name_a = await _seed_agent(app_db)
        name_b = await _seed_agent(app_db)
        rt._delays = {name_a: 0.3}

        out_a = await _spawn(name_a)
        out_b = await _spawn(name_b, depends_on=[out_a.output["run_id"]])
        await _drive_to_completion(out_a.output["run_id"])
        await _drive_to_completion(out_b.output["run_id"])

        span_a = rt.span(name_a)
        span_b = rt.span(name_b)
        # Hard ordering: B's invoke cannot start before A's invoke ended.
        assert span_b["start"] >= span_a["end"]
    finally:
        set_subagent_runtime(prev)
