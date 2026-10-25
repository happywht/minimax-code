"""Real SDK parsers/transports at governed send boundaries; no paid network."""
from __future__ import annotations

import asyncio
import json
import sqlite3

import httpx
import openai
import pytest
from test_agent_core import CountingTool
from test_run_service import Context, native  # noqa: F401

from minimax_code.agent.core import AgentConfig, AgentCore
from minimax_code.agent.llm import LLMError, MiniMaxClient
from minimax_code.agent.tools import ToolRegistry
from minimax_code.budget import BudgetExhausted
from minimax_code.budget_ledger import BudgetHandle, budget_scope, estimate_request
from minimax_code.storage.dao.budgets import BudgetsDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase


@pytest.fixture
async def governed(tmp_path):
    db = AsyncDatabase(tmp_path / "wire.db")
    await db.connect()
    await db.migrate()
    await SessionsDAO(db).create(id="s")
    runs = AgentRunsDAO(db)
    run, _ = await runs.admit_run(session_id="s", request={"mode": "chat", "content": "work"},
        title="work", metadata={"budget": {"limit_tokens": 100000}, "model_snapshot": {"provider_id": "saved", "model": "wire"}})
    handle = await BudgetHandle.for_run(db, run["id"])
    try:
        yield db, runs, run, handle
    finally:
        await db.close()


def response(*, tool=False, usage=True, partial=False):
    delta = {"tool_calls": [{"index": 0, "id": "tool", "type": "function", "function": {"name": "echo", "arguments": '{"text":"owned"}'}}]} if tool else {"content": "answer"}
    frame = {"id": "response", "object": "chat.completion.chunk", "created": 0,
             "model": "wire", "choices": [{"index": 0, "delta": delta, "finish_reason": None}]}
    frames = [frame]
    if partial:
        frames.append({**frame, "choices": [], "usage": {"prompt_tokens": 9}})
    else:
        frames.append({**frame, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls" if tool else "stop"}]})
        if usage:
            frames.append({**frame, "choices": [], "usage": {"prompt_tokens": 9, "completion_tokens": 11, "total_tokens": 20}})
    body = "".join(f"data: {json.dumps(frame)}\n\n" for frame in frames) + "data: [DONE]\n\n"
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)


def client(handler, *, require_budget=False):
    sdk = openai.AsyncOpenAI(api_key="offline", base_url="https://offline.invalid/v1",
        max_retries=5, http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    return MiniMaxClient(protocol="openai", api_key="offline", mock=False, model="wire",
                         client=sdk, require_budget=require_budget)


async def collect(llm):
    return [chunk async for chunk in llm.stream_chat([{"role": "user", "content": "work"}], max_tokens=32)]


async def test_real_sdk_hidden_retries_disabled_and_unknown_not_blindly_retried(governed):
    _, _, _, handle = governed
    calls = []
    def failure(request):
        calls.append(request)
        return httpx.Response(503, json={"error": {"message": "uncertain"}})
    llm = client(failure)
    try:
        with budget_scope(handle):
            with pytest.raises(LLMError):
                await collect(llm)
            with pytest.raises(BudgetExhausted, match="unresolved_attempt"):
                await collect(llm)
        assert len(calls) == 1
        rows = await handle.dao.attempts(handle.family_id)
        assert len(rows) == 1 and rows[0]["state"] == "unknown"
        assert rows[0]["known_tokens"] is None and rows[0]["estimated_tokens"] > 32
    finally:
        await llm.close()


async def test_success_settles_and_journals_before_dispatching_tools(governed):
    db, _, run, handle = governed
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        return response(tool=len(calls) == 1)
    llm = client(handler)
    class CheckedTool(CountingTool):
        async def run(self, **kwargs):
            attempts = await handle.dao.attempts(handle.family_id)
            assert attempts[0]["state"] == "settled"
            events = await db.fetchall("SELECT * FROM run_events WHERE run_id=? AND type='budget'", (run["id"],))
            assert any(json.loads(row["data"])["state"] == "settled" for row in events)
            return await super().run(**kwargs)
    tool = CheckedTool()
    registry = ToolRegistry()
    registry.register(tool)
    core = AgentCore(llm=llm, registry=registry, config=AgentConfig(model="wire", max_output_tokens=32))
    try:
        with budget_scope(handle):
            result = await core.run(session_id="s", user_message="work")
        assert len(tool.calls) == 1 and result.final_text == "answer"
        assert len(calls) == 2 and all(body["max_tokens"] == 32 for body in calls)
        snapshot = await handle.snapshot()
        assert snapshot["known_tokens"] == 40 and snapshot["reserved_tokens"] == 0
        attempts = await handle.dao.attempts(handle.family_id)
        assert [attempt["estimated_tokens"] for attempt in attempts] == [estimate_request(body) for body in calls]
    finally:
        await llm.close()


@pytest.mark.parametrize("failure_point", ["reserve", "sent", "settle"])
async def test_persistence_failure_stops_network_or_tools_without_refund(governed, failure_point):
    db, _, _, handle = governed
    if failure_point == "reserve":
        sql = "CREATE TRIGGER fail_budget BEFORE INSERT ON budget_attempts BEGIN SELECT RAISE(FAIL,'reserve failed'); END"
    elif failure_point == "sent":
        sql = "CREATE TRIGGER fail_budget BEFORE UPDATE ON budget_attempts WHEN NEW.state='sent' BEGIN SELECT RAISE(FAIL,'sent failed'); END"
    else:
        sql = "CREATE TRIGGER fail_budget BEFORE INSERT ON run_events WHEN NEW.type='budget' AND json_extract(NEW.data,'$.state')='settled' BEGIN SELECT RAISE(FAIL,'settlement journal failed'); END"
    await db.execute(sql)
    calls = []
    def handler(request):
        calls.append(request)
        return response(tool=True)
    llm = client(handler)
    tool, registry = CountingTool(), ToolRegistry()
    registry.register(tool)
    core = AgentCore(llm=llm, registry=registry, config=AgentConfig(model="wire", max_output_tokens=32))
    try:
        with budget_scope(handle), pytest.raises(sqlite3.IntegrityError):
            await core.run(session_id="s", user_message="work")
        assert not tool.calls
        assert len(calls) == int(failure_point == "settle")
        attempts = await handle.dao.attempts(handle.family_id)
        if failure_point == "reserve":
            assert not attempts
        else:
            assert attempts[0]["state"] == ("unknown" if failure_point == "settle" else "released")
            assert ((await handle.snapshot())["reserved_tokens"] > 0) is (failure_point == "settle")
    finally:
        await llm.close()


@pytest.mark.parametrize("mode", ["missing", "partial", "early_close"])
async def test_incomplete_stream_preserves_unknown_before_next_call(governed, mode):
    _, _, _, handle = governed
    llm = client(lambda request: response(usage=mode != "missing", partial=mode == "partial"))
    try:
        with budget_scope(handle):
            if mode == "early_close":
                stream = llm.stream_chat([{"role": "user", "content": "work"}], max_tokens=32)
                await stream.__anext__()
                await stream.aclose()
            else:
                with pytest.raises(BudgetExhausted, match="incomplete"):
                    await collect(llm)
        row = (await handle.dao.attempts(handle.family_id))[0]
        assert row["state"] == "unknown"
        assert row["known_tokens"] == (9 if mode == "partial" else None)
        assert (await handle.snapshot())["unknown_attempts"] == 1
    finally:
        await llm.close()


async def test_paid_service_client_without_unified_run_fails_closed():
    calls = []
    llm = client(lambda request: calls.append(request) or response(), require_budget=True)
    try:
        with pytest.raises(RuntimeError, match="unified run"):
            await collect(llm)
        assert not calls
    finally:
        await llm.close()


async def test_family_shared_by_child_and_recovery_without_reset(governed):
    _, runs, run, handle = governed
    llm = client(lambda request: response())
    try:
        with budget_scope(handle):
            await collect(llm)
            with budget_scope(handle.descendant("actual-child")):
                await asyncio.create_task(collect(llm))
        await runs.update_run_status(run["id"], status="budget_paused")
        recovered, _ = await runs.admit_run(session_id="s", request={"mode": "chat", "content": "recover"},
            title="recover", metadata={"budget": {"limit_tokens": 120000}}, resumed_from_id=run["id"])
        recovery_handle = await BudgetHandle.for_run(handle.dao.db, recovered["id"])
        assert recovery_handle.family_id == handle.family_id
        assert (await recovery_handle.snapshot())["known_tokens"] == 40
        with budget_scope(recovery_handle):
            await collect(llm)
        assert (await recovery_handle.snapshot())["known_tokens"] == 60
        assert [attempt["purpose"] for attempt in await handle.dao.attempts(handle.family_id)] == ["inference", "subagent", "inference"]
        fresh, _ = await runs.admit_run(session_id="s", request={"mode": "chat", "content": "new turn"}, title="new", metadata={})
        assert fresh["metadata"]["budget_family_id"] != handle.family_id
    finally:
        await llm.close()


@pytest.mark.parametrize("boundary", ["reserve", "sent", "settle"])
async def test_cancellation_at_committed_boundary_retains_cleanup_ownership(governed, monkeypatch, boundary):
    _, _, _, handle = governed
    entered, release = asyncio.Event(), asyncio.Event()
    cleanup_entered, cleanup_release = asyncio.Event(), asyncio.Event()
    original = getattr(handle.dao, boundary)
    async def paused(*args, **kwargs):
        result = await original(*args, **kwargs)
        entered.set()
        await release.wait()
        return result
    monkeypatch.setattr(handle.dao, boundary, paused)
    original_failure = handle.dao.fail_attempt
    async def failure(*args, **kwargs):
        cleanup_entered.set()
        await cleanup_release.wait()
        return await original_failure(*args, **kwargs)
    monkeypatch.setattr(handle.dao, "fail_attempt", failure)
    calls = []
    llm = client(lambda request: calls.append(request) or response(tool=True))
    tool, registry = CountingTool(), ToolRegistry()
    registry.register(tool)
    core = AgentCore(llm=llm, registry=registry, config=AgentConfig(model="wire", max_output_tokens=32))
    try:
        with budget_scope(handle):
            owner = asyncio.create_task(core.run(session_id="s", user_message="work"))
        await asyncio.wait_for(entered.wait(), 3)
        owner.cancel()
        if boundary == "settle":
            # Settlement already committed; repeated cancellation must keep
            # waiting on the same retained settlement task.
            await asyncio.sleep(0)
            owner.cancel()
            assert not owner.done()
            release.set()
        await asyncio.wait_for(cleanup_entered.wait(), 3)
        owner.cancel()
        await asyncio.sleep(0)
        assert not owner.done()
        cleanup_release.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(owner, 3)
        assert not tool.calls
        assert len(calls) == int(boundary == "settle")
        row = (await handle.dao.attempts(handle.family_id))[0]
        assert row["state"] == {"reserve": "released", "sent": "unknown", "settle": "settled"}[boundary]
    finally:
        release.set()
        cleanup_release.set()
        await llm.close()


async def test_parent_finalization_cannot_release_live_child_reservation(native, monkeypatch):  # noqa: F811
    from minimax_code.budget_ledger import current_handle
    from minimax_code.ipc import builtins
    entered, release = asyncio.Event(), asyncio.Event()
    children = []
    calls = []
    llm = client(lambda request: calls.append(request) or response())
    original = BudgetsDAO.reserve
    async def reserve_paused(self, **kwargs):
        aid = await original(self, **kwargs)
        entered.set()
        await release.wait()
        return aid
    monkeypatch.setattr(BudgetsDAO, "reserve", reserve_paused)
    async def spawn_child():
        with budget_scope(current_handle().descendant("async-child")):
            children.append(asyncio.create_task(collect(llm)))
        await entered.wait()
    native.tool = spawn_child
    ctx = Context(native.server)
    parent = asyncio.create_task(builtins.handle_agent_send_message({"session_id": "one", "content": "work"}, ctx))
    try:
        await asyncio.wait_for(entered.wait(), 3)
        native.release.set()
        await asyncio.wait_for(parent, 3)
        run = (await native.dao.list_runs(session_id="one"))[0]
        rows = await BudgetsDAO(native.db).attempts(run["metadata"]["budget_family_id"])
        assert rows[0]["state"] == "reserved" and not calls
        with pytest.raises(ValueError):
            await BudgetsDAO(native.db).reconcile(target="attempt", target_id=rows[0]["id"], liability_tokens=0, reason="still active")
        release.set()
        await asyncio.wait_for(children[0], 3)
        assert len(calls) == 1
        rows = await BudgetsDAO(native.db).attempts(run["metadata"]["budget_family_id"])
        assert rows[0]["state"] == "settled"
    finally:
        release.set()
        native.release.set()
        await asyncio.gather(parent, *children, return_exceptions=True)
        await llm.close()


async def test_released_reservation_can_never_cross_send_seam(governed):
    _, _, run, handle = governed
    aid = await handle.dao.reserve(family_id=handle.family_id, run_id=run["id"], provider="wire", model="wire", estimate=50)
    await handle.dao.release(aid, reason="provably unsent")
    with pytest.raises(BudgetExhausted, match="no longer live"):
        await handle.dao.sent(aid)


async def test_paid_skill_endpoint_refuses_unscoped_send_without_false_success(tmp_path):
    from test_skills import _write_skill

    from minimax_code.agent.skills.registry import SkillRegistry
    from minimax_code.agent.skills.runtime import SkillRuntime
    from minimax_code.config import Config
    from minimax_code.ipc.handlers_skills import register_skill_handlers
    from minimax_code.ipc.server import IPCServer
    _write_skill(tmp_path, "budget-skill", tools=[])
    registry = SkillRegistry(skills_root=tmp_path, db=None, auto_persist=False)
    await registry.load_all()
    calls = []
    llm = client(lambda request: calls.append(request) or response(), require_budget=True)
    server = IPCServer(Config())
    register_skill_handlers(server, runtime=SkillRuntime(registry=registry, llm=llm, tool_registry=ToolRegistry()))
    ctx = Context(server)
    try:
        await server._handlers["skill.invoke"]({"skill_id": "budget-skill:budget-skill", "request": "work", "session_id": "s"}, ctx)
        ctx.reply.assert_not_awaited()
        ctx.reply_error.assert_awaited_once()
        assert "idempotency_key" in str(ctx.reply_error.await_args)
        assert not calls
    finally:
        await llm.close()


async def test_governed_child_budget_pause_is_partial_not_completed(governed, monkeypatch, tmp_path):
    from minimax_code.orchestrator.subagent import SubAgentConfig, SubAgentRuntime
    _, _, _, handle = governed
    await handle.dao.db.execute("UPDATE budget_families SET cap_tokens=1 WHERE id=?", (handle.family_id,))
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    calls = []
    llm = client(lambda request: calls.append(request) or response(), require_budget=True)
    runtime = SubAgentRuntime(llm=llm)
    try:
        with budget_scope(handle):
            result = await runtime.invoke(runtime.build(SubAgentConfig(name="child", system_prompt="work")), session_id="s", request="work")
        assert result["budget_exhausted"] and result["partial"]
        assert result["verification"]["requires_review"]
        assert result["workspace"]["state"] == "failed"
        assert not calls
    finally:
        await llm.close()


@pytest.mark.parametrize("failure", [False, True])
async def test_real_anthropic_sdk_cache_usage_retry_and_tool_settlement(governed, failure):
    import anthropic
    db, _, run, handle = governed
    calls = []
    def handler(request):
        calls.append(json.loads(request.content))
        if failure:
            return httpx.Response(503, json={"type": "error", "error": {"type": "overloaded_error", "message": "uncertain"}})
        tool = len(calls) == 1
        events = [
            {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant", "content": [], "model": "wire", "stop_reason": None, "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 0, "cache_read_input_tokens": 4, "cache_creation_input_tokens": 6}}},
            {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "tool", "name": "echo", "input": {}} if tool else {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": '{"text":"owned"}'} if tool else {"type": "text_delta", "text": "answer"}},
            {"type": "content_block_stop", "index": 0},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use" if tool else "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 3}},
            {"type": "message_stop"},
        ]
        body = "".join(f"event: {event['type']}\ndata: {json.dumps(event)}\n\n" for event in events)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=body)
    sdk = anthropic.AsyncAnthropic(api_key="offline", max_retries=5, base_url="https://offline.invalid",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    llm = MiniMaxClient(protocol="anthropic", api_key="offline", mock=False, model="wire", client=sdk, require_budget=True)
    class CheckedTool(CountingTool):
        async def run(self, **kwargs):
            row = (await handle.dao.attempts(handle.family_id))[0]
            assert row["state"] == "settled" and row["known_tokens"] == 23
            usage = json.loads(row["usage"])
            assert usage["prompt_tokens"] == 20 and usage["cached_tokens"] == 4 and usage["cache_creation_tokens"] == 6
            assert await db.fetchone("SELECT 1 FROM run_events WHERE run_id=? AND type='budget' AND json_extract(data,'$.state')='settled'", (run["id"],))
            return await super().run(**kwargs)
    tool, registry = CheckedTool(), ToolRegistry()
    registry.register(tool)
    core = AgentCore(llm=llm, registry=registry, config=AgentConfig(model="wire", max_output_tokens=32))
    try:
        with budget_scope(handle):
            if failure:
                with pytest.raises(LLMError):
                    await collect(llm)
                with pytest.raises(BudgetExhausted, match="unresolved_attempt"):
                    await collect(llm)
            else:
                result = await core.run(session_id="s", user_message="work")
                assert result.final_text == "answer" and len(tool.calls) == 1
        assert len(calls) == (1 if failure else 2)
        assert all(body["max_tokens"] == 32 for body in calls)
        rows = await handle.dao.attempts(handle.family_id)
        assert len(rows) == len(calls)
        assert rows[0]["state"] == ("unknown" if failure else "settled")
    finally:
        await llm.close()


async def test_auto_continue_keeps_actual_sdk_spend_in_original_family(governed):
    from minimax_code.ipc.builtins import _run_with_auto_continue
    _, _, _, handle = governed
    calls = []
    def handler(request):
        calls.append(request)
        return response(tool=len(calls) == 1)
    llm = client(handler)
    registry = ToolRegistry()
    registry.register(CountingTool())
    core = AgentCore(llm=llm, registry=registry, config=AgentConfig(model="wire", max_output_tokens=32,
        max_iterations=1, auto_continue=True, auto_continue_max_blocks=2))
    try:
        with budget_scope(handle):
            result, blocks = await _run_with_auto_continue(core, session_id="s", content="work")
        assert blocks == 2 and result.final_text == "answer"
        assert len(calls) == 2 and (await handle.snapshot())["known_tokens"] == 40
        assert len(await handle.dao.attempts(handle.family_id)) == 2
    finally:
        await llm.close()


async def test_legacy_family_needs_explicit_accounting_and_reconcile_is_not_repeatable(governed):
    _, runs, _, handle = governed
    legacy = await runs.create_run(session_id="s", mode="chat", status="interrupted",
        metadata={"budget": {"limit_tokens": 1000}})
    await handle.dao.db.execute("UPDATE agent_runs SET result=? WHERE id=?", ('{"tokens_total":40}', legacy["id"]))
    resumed, _ = await runs.admit_run(session_id="s", request={"mode": "chat", "content": "recover"}, title="recover",
        metadata={"budget": {"limit_tokens": 2000}}, resumed_from_id=legacy["id"])
    recovery = await BudgetHandle.for_run(handle.dao.db, resumed["id"])
    assert (await recovery.snapshot())["pause_reason"] == "legacy_family_incomplete"
    with pytest.raises(BudgetExhausted, match="legacy_family_incomplete"):
        await recovery.dao.reserve(family_id=recovery.family_id, run_id=resumed["id"], provider="wire", model="wire", estimate=1)
    await recovery.dao.reconcile(target="family", target_id=recovery.family_id, liability_tokens=100, reason="historical authorization")
    with pytest.raises(ValueError):
        await recovery.dao.reconcile(target="family", target_id=recovery.family_id, liability_tokens=40, reason="cannot erase previous authorization")
    state = await recovery.snapshot()
    assert state["known_tokens"] == 40 and state["reserved_tokens"] == 60
    assert state["accounting_resolved"] and not state["usage_complete"]


async def test_budget_rpc_requires_explicit_authorization_and_preserves_nullable_usage(governed, monkeypatch):
    from minimax_code import app
    from minimax_code.config import Config
    from minimax_code.ipc.handlers_budget import register_budget_handlers
    from minimax_code.ipc.server import IPCServer
    db, runs, run, handle = governed
    monkeypatch.setattr(app, "get_db", lambda: db)
    aid = await handle.dao.reserve(family_id=handle.family_id, run_id=run["id"], provider="wire", model="wire", estimate=100)
    await handle.dao.sent(aid)
    await handle.dao.settle(aid, {}, complete=False)
    await runs.update_run_status(run["id"], status="interrupted")
    server = IPCServer(Config())
    register_budget_handlers(server)
    request = {"target": "attempt", "target_id": aid, "liability_tokens": 80, "reason": "offline evidence"}
    denied = Context(server)
    await server._handlers["budget.reconcile"](request, denied)
    denied.reply_error.assert_awaited_once()
    approved = Context(server)
    await server._handlers["budget.reconcile"]({**request, "acknowledged": True}, approved)
    approved.reply.assert_awaited_once_with({"ok": True})
    ctx = Context(server)
    await server._handlers["budget.get"]({"run_id": run["id"]}, ctx)
    state = ctx.reply.await_args.args[0]
    assert state["attempts"][0]["known_tokens"] is None
    assert state["attempts"][0]["provenance"] == "reconciled"
    assert state["budget"]["reserved_tokens"] == 80 and not state["budget"]["usage_complete"]


async def test_budget_rpc_lists_other_family_daily_blocker_with_child_root_fallback(governed, monkeypatch):
    from minimax_code import app
    from minimax_code.config import Config
    from minimax_code.ipc.handlers_budget import register_budget_handlers
    from minimax_code.ipc.server import IPCServer
    db, runs, run, handle = governed
    monkeypatch.setattr(app, "get_db", lambda: db)
    aid = await handle.dao.reserve(family_id=handle.family_id, run_id="orphan-child", provider="wire", model="wire", estimate=100)
    await handle.dao.sent(aid)
    await handle.dao.settle(aid, {}, complete=False)
    fresh, _ = await runs.admit_run(session_id="s", request={"mode": "chat", "content": "fresh"}, title="fresh", metadata={})
    server = IPCServer(Config())
    register_budget_handlers(server)
    ctx = Context(server)
    await server._handlers["budget.get"]({"run_id": fresh["id"]}, ctx)
    state = ctx.reply.await_args.args[0]
    assert state["attempts"] == []
    assert state["day_blockers"] == {"total": 1, "items": [{"attempt_id": aid, "family_id": handle.family_id, "known_tokens": None, "run_id": run["id"], "session_id": "s"}]}


async def test_double_settlement_fault_quarantines_every_handle_until_recovery(governed):
    db, runs, run, handle = governed
    # A sibling can already own a provably unsent reservation when the fault
    # arrives. Both reserve and sent must consult the same DB quarantine.
    waiting = await handle.dao.reserve(family_id=handle.family_id, run_id=run["id"], provider="wire", model="wire", estimate=50)
    await db.execute("CREATE TRIGGER fail_settlements BEFORE INSERT ON run_events WHEN NEW.type='budget' AND json_extract(NEW.data,'$.state') IN ('settled','unknown') BEGIN SELECT RAISE(FAIL,'settlement storage unavailable'); END")
    calls = []
    llm = client(lambda request: calls.append(request) or response())
    try:
        with budget_scope(handle), pytest.raises(sqlite3.IntegrityError):
            await collect(llm)
        fresh, _ = await runs.admit_run(session_id="s", request={"mode": "chat", "content": "fresh"}, title="fresh", metadata={})
        other = await BudgetHandle.for_run(db, fresh["id"])
        with budget_scope(other), pytest.raises(BudgetExhausted, match="recovery is unavailable"):
            await collect(llm)
        with pytest.raises(BudgetExhausted, match="recovery is unavailable"):
            await other.dao.sent(waiting)
        assert len(calls) == 1
        await db.execute("DROP TRIGGER fail_settlements")
        # Removing a transient fault alone cannot reopen paid sends.
        with budget_scope(other), pytest.raises(BudgetExhausted):
            await collect(llm)
        await db.close()
        await db.connect()
        await runs.recover_orphans()
        rows = await handle.dao.attempts(handle.family_id)
        assert rows[0]["state"] == "released" and rows[1]["state"] == "unknown"
        assert rows[1]["known_tokens"] is None
        with budget_scope(handle), pytest.raises(BudgetExhausted, match="unresolved_attempt"):
            await collect(llm)
    finally:
        await llm.close()


async def test_startup_recovery_failure_keeps_service_paid_admission_closed(governed):
    from minimax_code import app
    db, _, run, handle = governed
    aid = await handle.dao.reserve(family_id=handle.family_id, run_id=run["id"], provider="wire", model="wire", estimate=100)
    await handle.dao.sent(aid)
    await db.execute("CREATE TRIGGER fail_recovery BEFORE INSERT ON run_events WHEN NEW.type='budget' AND json_extract(NEW.data,'$.state')='unknown' BEGIN SELECT RAISE(FAIL,'recovery storage unavailable'); END")
    await app._run_crash_recovery(db)
    assert db._budget_governance_error
    calls = []
    llm = client(lambda request: calls.append(request) or response())
    try:
        with budget_scope(handle), pytest.raises(BudgetExhausted, match="recovery is unavailable"):
            await collect(llm)
        assert not calls
        assert (await handle.dao.attempts(handle.family_id))[0]["state"] == "sent"
        await db.execute("DROP TRIGGER fail_recovery")
        await app._run_crash_recovery(db)
        assert db._budget_governance_error is None
        assert (await handle.dao.attempts(handle.family_id))[0]["state"] == "unknown"
    finally:
        await llm.close()
