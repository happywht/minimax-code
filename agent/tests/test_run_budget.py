"""M2 budget tests — per-run and daily token budgets, budget_paused lifecycle.

Two layers, mirroring the send-message pipeline:

- **core gate** (real ``AgentCore`` + scripted ``FakeLLM``): the
  pre-call projection pauses the turn before a breaking call, usage
  accumulates into the shared :class:`BudgetState`, and the published
  ContextVar hands the same pot to sub-agents.
- **durable wiring** (the ``native`` fixture drives
  ``handle_agent_send_message`` end to end with a stub core):
  ``budget_paused`` lands in the run row + receipt, ``run.resume``
  demands a raised cap, the daily baseline refuses admission, and boot
  recovery closes paused runs without a review flag.

Timing follows ``test_run_service``: the handler coroutine is a task,
the stub core signals ``started`` at the top of ``run()``, and
``release`` lets the turn finish — a plain ``await`` would deadlock on
``wait=True``.
"""

from __future__ import annotations

import asyncio
import io
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from minimax_code.agent.types import StreamChunk
from minimax_code.budget import (
    BudgetState,
    current_budget,
    daily_budget_limit,
    projected_call_tokens,
    publish_current_budget,
    reset_current_budget,
    resolve_run_budget,
)
from minimax_code.config import Config
from minimax_code.ipc import builtins
from minimax_code.ipc.handlers_runs import register_run_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.runs import get_run_service
from minimax_code.storage.dao.messages import MessagesDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase

# ---------------------------------------------------------------------------
# BudgetState unit behaviour
# ---------------------------------------------------------------------------


def test_budget_state_records_usage_and_projection() -> None:
    state = BudgetState(limit_tokens=500)
    assert state.would_exceed(projected_call_tokens(None, 400)) is False
    state.record({"prompt_tokens": 300, "completion_tokens": 100})
    assert state.used_tokens == 400
    # Next call projected at the last prompt (300) + output cap (400)
    # would land at 700 > 500 — pause before spending.
    assert state.would_exceed(projected_call_tokens(300, 400)) is True


def test_budget_state_daily_dimension_uses_baseline() -> None:
    state = BudgetState(daily_limit=1000, daily_baseline=900)
    assert state.would_exceed(projected_call_tokens(None, 50)) is False
    assert state.would_exceed(projected_call_tokens(None, 200)) is True


def test_budget_state_snapshot_roundtrip() -> None:
    state = BudgetState(limit_tokens=99, daily_limit=1000, daily_baseline=5)
    state.record({"total_tokens": 42})
    state.pauses += 1
    snap = state.snapshot()
    assert snap == {"limit_tokens": 99, "daily_limit": 1000,
                    "used_tokens": 42, "pauses": 1}


def test_resolve_run_budget_priority(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIMAX_CODE_RUN_BUDGET_TOKENS", raising=False)
    monkeypatch.delenv("MINIMAX_CODE_SCHEDULED_BUDGET_TOKENS", raising=False)
    # explicit request wins over everything; 0 disables.
    assert resolve_run_budget(777) == 777
    assert resolve_run_budget(0) is None
    assert resolve_run_budget(None) is None
    # env default applies without a request.
    monkeypatch.setenv("MINIMAX_CODE_RUN_BUDGET_TOKENS", "250")
    assert resolve_run_budget(None) == 250
    # scheduled floor overrides the plain default for unattended jobs.
    monkeypatch.setenv("MINIMAX_CODE_SCHEDULED_BUDGET_TOKENS", "90")
    assert resolve_run_budget(None, scheduled=True) == 90
    assert resolve_run_budget(None, scheduled=False) == 250


def test_daily_budget_limit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIMAX_CODE_DAILY_BUDGET_TOKENS", raising=False)
    assert daily_budget_limit() is None
    monkeypatch.setenv("MINIMAX_CODE_DAILY_BUDGET_TOKENS", "4000")
    assert daily_budget_limit() == 4000
    monkeypatch.setenv("MINIMAX_CODE_DAILY_BUDGET_TOKENS", "0")
    assert daily_budget_limit() is None


def test_contextvar_publish_scopes_budget() -> None:
    assert current_budget() is None
    state = BudgetState(limit_tokens=10)
    token = publish_current_budget(state)
    try:
        assert current_budget() is state
    finally:
        reset_current_budget(token)
    assert current_budget() is None


# ---------------------------------------------------------------------------
# Core gate — real AgentCore, scripted LLM
# ---------------------------------------------------------------------------


class ScriptedLLM:
    """Queued StreamChunk responses plus per-response usage dicts."""

    def __init__(self, responses: list[tuple[list[StreamChunk], dict[str, int]]]) -> None:
        self._queue = list(responses)
        self.call_count = 0
        self.mock = True

    async def stream_chat(self, messages: Any, **_: Any):  # noqa: ANN401
        self.call_count += 1
        chunks, usage = (self._queue.pop(0) if self._queue else
                         ([StreamChunk(delta="done", finish_reason="stop")], {}))
        for chunk in chunks:
            chunk.usage = usage
            yield chunk


def _core(llm: ScriptedLLM, **config_kwargs: Any):  # noqa: ANN401
    from minimax_code.agent.core import AgentConfig, AgentCore

    config = AgentConfig(max_output_tokens=100, **config_kwargs)
    return AgentCore(llm=llm, config=config)


async def test_core_pauses_before_first_call_when_budget_spent() -> None:
    llm = ScriptedLLM([])
    core = _core(llm, run_budget_tokens=10)
    assert core.budget is not None
    core.budget.record({"total_tokens": 50})  # already over the cap
    result = await core.run(session_id="s", user_message="go")
    assert result.budget_exhausted is True
    assert result.cancelled is False
    assert result.truncated is False
    assert llm.call_count == 0
    assert "Paused" in result.final_text
    assert core.budget.pauses == 1


async def test_core_completes_and_accumulates_usage_under_budget() -> None:
    llm = ScriptedLLM([
        ([StreamChunk(delta="hello", finish_reason="stop")],
         {"prompt_tokens": 30, "completion_tokens": 20}),
    ])
    core = _core(llm, run_budget_tokens=1000)
    result = await core.run(session_id="s", user_message="go")
    assert result.budget_exhausted is False
    assert core.budget is not None and core.budget.used_tokens == 50
    assert llm.call_count == 1


async def test_core_daily_baseline_pauses_before_first_call() -> None:
    llm = ScriptedLLM([])
    core = _core(llm, daily_budget_tokens=200, daily_budget_baseline=190)
    result = await core.run(session_id="s", user_message="go")
    assert result.budget_exhausted is True
    assert llm.call_count == 0


async def test_core_publishes_budget_scope_and_resets() -> None:
    llm = ScriptedLLM([
        ([StreamChunk(delta="ok", finish_reason="stop")], {"total_tokens": 5}),
    ])
    core = _core(llm)
    assert core.budget is None
    core.budget = BudgetState(limit_tokens=100)
    await core.run(session_id="s", user_message="go")
    assert current_budget() is None  # scope closed after the turn


# ---------------------------------------------------------------------------
# Durable wiring — end to end through handle_agent_send_message
# ---------------------------------------------------------------------------


class Context:
    def __init__(self, server):
        self.server = server
        self.reply = AsyncMock()
        self.reply_error = AsyncMock()
        self.emit = AsyncMock()


@pytest.fixture
async def native(monkeypatch, tmp_path):
    from minimax_code import agent, app

    db = AsyncDatabase(tmp_path / "budget.db")
    await db.connect()
    await db.migrate()
    sessions = SessionsDAO(db)
    await sessions.create(id="one", title="one", workspace_path=str(tmp_path))
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    monkeypatch.delenv("MINIMAX_CODE_DAILY_BUDGET_TOKENS", raising=False)
    monkeypatch.delenv("MINIMAX_CODE_RUN_BUDGET_TOKENS", raising=False)
    monkeypatch.setattr(app, "init_runtime", AsyncMock())
    monkeypatch.setattr(app, "get_db", lambda: db)
    monkeypatch.setattr(app, "get_sessions_dao", lambda: sessions)
    monkeypatch.setattr(app, "ensure_codebase_indexer", lambda *args: None)
    monkeypatch.setattr(app, "get_codebase_indexer", lambda: None)
    monkeypatch.setattr(app, "ensure_hook_manager", lambda: None)
    monkeypatch.setattr(app, "ensure_telemetry_engine", lambda: None)
    monkeypatch.setattr(app, "get_subagent_llm", lambda: SimpleNamespace(default_model="test", mock=True))
    monkeypatch.setattr(builtins, "_build_system_prompt_extra", AsyncMock(return_value=None))
    monkeypatch.setattr("minimax_code.ipc.handlers_permissions._ensure_permission_store", AsyncMock(return_value=None))
    server = IPCServer(Config(), stdin=io.StringIO(), stdout=io.StringIO())
    register_run_handlers(server)
    cores: list[Any] = []
    started: asyncio.Queue[tuple[str, str]] = asyncio.Queue()
    release = asyncio.Event()

    class Core:
        def __init__(self, **kwargs):
            self.config = kwargs["config"]
            self.llm = kwargs["llm"]
            self.budget = None  # real AgentCore always sets this; receipt reads it
            self.cancelled = False
            cores.append(self)

        def cancel(self):
            self.cancelled = True

        async def run(self, *, session_id, user_message):
            started.put_nowait((session_id, user_message))
            await release.wait()
            if native.exhausted:
                return SimpleNamespace(final_text="Paused", iterations=1, usage={},
                                       truncated=False, cancelled=False, compactions=0,
                                       budget_exhausted=True,
                                       tool_calls=[], tool_results=[])
            return SimpleNamespace(final_text="done", iterations=1, usage={},
                                   truncated=False, cancelled=False, compactions=0,
                                   budget_exhausted=False,
                                   tool_calls=native.tool_calls,
                                   tool_results=native.tool_results)

    monkeypatch.setattr(agent, "AgentCore", Core)
    native = SimpleNamespace(db=db, dao=AgentRunsDAO(db), server=server, cores=cores,
                             started=started, release=release, exhausted=True, root=tmp_path,
                             tool_calls=[], tool_results=[])
    try:
        yield native
    finally:
        await get_run_service(server).shutdown()
        await db.close()


async def _send(native, params: dict[str, Any]) -> tuple[Context, asyncio.Task]:
    """Drive one send-message turn to completion (stub core gates on release)."""
    ctx = Context(native.server)
    task = asyncio.create_task(builtins.handle_agent_send_message(params, ctx))
    await asyncio.wait_for(native.started.get(), 3)
    native.release.set()
    await asyncio.wait_for(task, 3)
    native.release.clear()  # the next run must gate on release again
    return ctx, task


async def _drain_background_runs(native) -> None:
    """Let released background jobs (wait=False resumes) finish."""
    native.release.set()
    service = get_run_service(native.server)
    for _ in range(200):
        pending = [job for queue in service._jobs.values() for job in queue]
        if not pending:
            break
        await asyncio.sleep(0.01)
    native.release.clear()


async def test_budget_exhausted_run_lands_budget_paused_status(native) -> None:
    await _send(native, {"session_id": "one", "content": "burn tokens"})
    runs = await native.dao.list_runs(session_id="one")
    assert runs[0]["status"] == "budget_paused"
    assert runs[0]["metadata"]["budget_exhausted"] is True
    # The receipt the RPC waiter saved carries the explicit flag too.
    receipt = await native.dao.result_payload(runs[0]["id"])
    assert receipt["budget_paused"] is True


async def test_resume_budget_paused_demands_raised_cap_then_resumes(native) -> None:
    await _send(native, {"session_id": "one", "content": "burn tokens"})
    paused = (await native.dao.list_runs(session_id="one"))[0]

    # Without a raised cap the pause is exactly what the caller asked
    # for — resuming as-is would immediately re-pause, so refuse.
    blocked = Context(native.server)
    await native.server._handlers["run.resume"](
        {"run_id": paused["id"], "idempotency_key": "resume-1"}, blocked)
    blocked.reply_error.assert_awaited_once()
    assert "budget" in str(blocked.reply_error.await_args.args[1]).lower()

    # Raising the cap resumes without the review gate — a budget pause
    # is a clean stop at a call boundary, not an unknown interruption.
    resumed = Context(native.server)
    resume_task = asyncio.create_task(native.server._handlers["run.resume"](
        {"run_id": paused["id"], "idempotency_key": "resume-2",
         "budget_tokens": 9999}, resumed))
    session_id, message = await asyncio.wait_for(native.started.get(), 3)
    assert session_id == "one"
    assert "Budget recovery context" in message
    native.exhausted = False
    await _drain_background_runs(native)
    await asyncio.wait_for(resume_task, 3)
    runs = await native.dao.list_runs(session_id="one")
    statuses = [row["status"] for row in runs]
    assert statuses.count("budget_paused") == 1
    assert statuses.count("completed") == 1
    resumed_run = next(row for row in runs if row["status"] == "completed")
    assert resumed_run["resumed_from_id"] == paused["id"]


async def test_resume_interrupted_inherits_saved_budget_cap(native, monkeypatch) -> None:
    # A crashed run resumed with reviewed=True picks up the cap saved in
    # its admission metadata instead of silently running unlimited.
    run = await native.dao.create_run(
        session_id="one", mode="chat", status="interrupted",
        title="crashed", metadata={"model_snapshot": {"provider_id": None, "model": "m"},
                                   "workspace_snapshot": str(native.root),
                                   "budget": {"limit_tokens": 321, "daily_limit": None,
                                              "daily_baseline": 0}})
    await native.db.execute(
        "UPDATE agent_runs SET request = ?, request_key = 'k', started_at = '2026-01-01T00:00:00' WHERE id = ?",
        ('{"session_id": "one", "mode": "chat", "content": "orig"}', run["id"]),
    )
    monkeypatch.setattr(builtins, "ModelSnapshot",
                        SimpleNamespace(capture=lambda: SimpleNamespace(
                            metadata=lambda: {"provider_id": None, "model": "m"},
                            client=SimpleNamespace(mock=True), reasoning_effort=None,
                            context_window=None, model="m")))
    ctx = Context(native.server)
    resume_task = asyncio.create_task(native.server._handlers["run.resume"](
        {"run_id": run["id"], "idempotency_key": "resume-k", "reviewed": True}, ctx))
    await asyncio.wait_for(native.started.get(), 3)
    native.exhausted = False
    await _drain_background_runs(native)
    await asyncio.wait_for(resume_task, 3)
    assert 321 in [core.config.run_budget_tokens for core in native.cores]


async def test_daily_budget_is_attached_at_admission_not_message_sum(native, monkeypatch) -> None:
    monkeypatch.setenv("MINIMAX_CODE_DAILY_BUDGET_TOKENS", "10")
    messages = MessagesDAO(native.db)
    await messages.create(id="m1", session_id="one", role="assistant", content="spent",
                          tokens_in=40, tokens_out=60)
    # Historical message sums are not an authoritative spend ledger. Actual
    # request-boundary tests cover atomic daily refusal before SDK entry.
    await _send(native, {"session_id": "one", "content": "more"})
    run = (await native.dao.list_runs(session_id="one"))[0]
    assert run["metadata"]["budget"]["daily_limit"] == 10


async def test_budget_param_flows_into_admission_metadata(native) -> None:
    native.exhausted = False
    await _send(native, {"session_id": "one", "content": "bounded", "budget_tokens": 5000})
    run = (await native.dao.list_runs(session_id="one"))[0]
    assert run["metadata"]["budget"]["limit_tokens"] == 5000


async def test_recover_orphans_closes_budget_paused_runs_without_review_flag(native) -> None:
    run = await native.dao.create_run(
        session_id="one", mode="chat", status="budget_paused", title="paused")
    result = await native.dao.recover_orphans(reason="restarted")
    assert run["id"] in result["run_ids"]
    recovered = await native.dao.get_run(run["id"])
    assert recovered["status"] == "interrupted"
    steps = await native.dao.list_steps(run["id"])
    payload = steps[-1]["payload"]
    assert payload["requires_review"] is False
    assert payload["previous_status"] == "budget_paused"


async def test_recovered_budget_pause_still_requires_explicit_budget(native) -> None:
    await _send(native, {"session_id": "one", "content": "burn tokens"})
    paused = (await native.dao.list_runs(session_id="one"))[0]
    await native.dao.recover_orphans(reason="restarted")

    blocked = Context(native.server)
    await native.server._handlers["run.resume"](
        {"run_id": paused["id"], "idempotency_key": "restart-no-budget", "reviewed": True},
        blocked,
    )
    blocked.reply_error.assert_awaited_once()
    assert "budget" in blocked.reply_error.await_args.args[1].lower()

    resumed = Context(native.server)
    task = asyncio.create_task(native.server._handlers["run.resume"](
        {"run_id": paused["id"], "idempotency_key": "restart-with-budget",
         "budget_tokens": 9999}, resumed))
    _, message = await asyncio.wait_for(native.started.get(), 3)
    assert "Budget recovery context" in message
    native.exhausted = False
    await _drain_background_runs(native)
    await asyncio.wait_for(task, 3)
    resumed.reply_error.assert_not_awaited()


async def test_recovered_pause_with_unfinished_tool_requires_review(native) -> None:
    await _send(native, {"session_id": "one", "content": "burn tokens"})
    paused = (await native.dao.list_runs(session_id="one"))[0]
    await native.dao.create_step(run_id=paused["id"], session_id="one", kind="tool_call",
                                 payload={"args": {"path": "possibly-written"}})
    await native.dao.recover_orphans(reason="restarted")
    steps = await native.dao.list_steps(paused["id"])
    assert steps[-1]["payload"]["requires_review"] is True

    ctx = Context(native.server)
    await native.server._handlers["run.resume"](
        {"run_id": paused["id"], "idempotency_key": "unknown-effect", "budget_tokens": 9999},
        ctx,
    )
    ctx.reply_error.assert_awaited_once()
    assert "review" in ctx.reply_error.await_args.args[1].lower()
    reviewed = Context(native.server)
    await native.server._handlers["run.resume"](
        {"run_id": paused["id"], "idempotency_key": "reviewed-no-budget", "reviewed": True},
        reviewed,
    )
    reviewed.reply_error.assert_awaited_once()
    assert "budget" in reviewed.reply_error.await_args.args[1].lower()


async def test_receipt_carries_verification_evidence(native) -> None:
    """M2 acceptance semantics: failed tool calls must not melt into a
    plain "completed" — the receipt and the run row both carry the
    failure evidence so a caller can demand review."""
    native.exhausted = False
    native.tool_calls = [{"name": "write_file", "id": "t1"},
                         {"name": "exec_command", "id": "t2"}]
    native.tool_results = [SimpleNamespace(success=True, error=None),
                           SimpleNamespace(success=False, error="exit code 1: tests failed")]
    await _send(native, {"session_id": "one", "content": "do the thing"})
    run = (await native.dao.list_runs(session_id="one"))[0]
    receipt = await native.dao.result_payload(run["id"])
    assert receipt["verification"]["tool_calls"] == 2
    assert receipt["verification"]["failed_tool_calls"] == 1
    assert receipt["verification"]["requires_review"] is True
    assert receipt["verification"]["failures"] == [
        {"tool": "exec_command", "error": "exit code 1: tests failed"}]
    # The durable run row mirrors the same evidence.
    assert run["metadata"]["verification"]["failed_tool_calls"] == 1
    assert run["status"] == "completed"  # status stays; evidence separates


async def test_clean_receipt_requires_no_review(native) -> None:
    native.exhausted = False
    native.tool_calls = [{"name": "read_file", "id": "t1"}]
    native.tool_results = [SimpleNamespace(success=True, error=None)]
    await _send(native, {"session_id": "one", "content": "just read"})
    run = (await native.dao.list_runs(session_id="one"))[0]
    receipt = await native.dao.result_payload(run["id"])
    assert receipt["verification"]["failed_tool_calls"] == 0
    assert receipt["verification"]["requires_review"] is False
