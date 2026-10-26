"""Public agent roots use the real Native executor and offline SDK transport."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest
from test_budget_transport import response
from test_run_service import Context
from test_skill_execution import skill_env as skill_env

from minimax_code.ipc.builtins import _build_system_prompt_extra as actual_build_extra
from minimax_code.ipc.handlers_agents import register_agent_handlers
from minimax_code.runs import get_run_service
from minimax_code.storage.dao.agents import AgentDAO


def tool_response(name, args):
    lines = []
    for line in response(tool=True).text.splitlines():
        if not line.startswith("data: {"):
            lines.append(line)
            continue
        frame = json.loads(line[6:])
        for choice in frame["choices"]:
            for call in choice["delta"].get("tool_calls", []):
                call["function"] = {"name": name, "arguments": json.dumps(args)}
        lines.append("data: " + json.dumps(frame))
    return httpx.Response(200, headers={"content-type": "text/event-stream"}, content="\n".join(lines) + "\n")


@pytest.fixture
async def agent_env(skill_env):
    state = skill_env
    state.source = state.root / "source-a"
    state.source.mkdir()
    (state.source / "hello.txt").write_text("source-a", encoding="utf-8")
    await state.sessions.update("one", workspace_path=str(state.source))
    await state.sessions.update("two", workspace_path=str(state.source))
    state.agents = AgentDAO(state.db)
    state.agent = await state.agents.upsert(name="worker", system_prompt="Frozen agent prompt",
                                           tool_allowlist=["read_file"], max_iterations=4)
    register_agent_handlers(state.server)
    return state


async def invoke(state, method="agent.invoke", **changes):
    ctx = Context(state.server)
    await state.server._handlers[method]({"name": "worker", "request": "Inspect hello.txt",
        "session_id": "one", "idempotency_key": "agent-once", "budget_tokens": 100000,
        **changes}, ctx)
    return ctx


@pytest.mark.parametrize("method", ["agent.invoke", "agent.spawn_subagent"])
async def test_actual_agent_root_and_replay_before_registry(agent_env, method):
    state = agent_env
    ctx = await invoke(state, method)
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.await_args.args[0]
    run = await state.dao.get_run(receipt["run_id"])
    assert receipt == await state.dao.result_payload(run["id"])
    assert receipt["text"] == "answer" and receipt["status"] == "completed"
    assert receipt["agent_run_id"] == run["id"] and receipt["session_id"] != "one"
    assert receipt["parent_session_id"] == "one" and receipt["agent"] == "worker"
    root = Path(receipt["workspace"]["path"])
    assert root != state.source and (root / "hello.txt").read_text() == "source-a"
    assert {t["function"]["name"] for t in state.calls[0]["tools"]} == {"read_file"}
    assert "Frozen agent prompt" in state.calls[0]["messages"][0]["content"]
    attempts = await state.db.fetchall("SELECT * FROM budget_attempts")
    assert len(attempts) == 1 and attempts[0]["run_id"] == run["id"]
    assert run["metadata"]["budget_family_id"] == attempts[0]["family_id"]
    await state.agents.delete("worker")
    replay = await invoke(state, method)
    assert replay.reply.await_args.args[0] == receipt and len(state.calls) == 1
    conflict = await invoke(state, method, request="different")
    assert "different agent input" in str(conflict.reply_error.await_args)


async def test_wire_workspace_advisory_names_actual_execution_copy(agent_env, monkeypatch):
    from minimax_code.ipc import builtins

    state = agent_env
    monkeypatch.setattr(builtins, "_build_system_prompt_extra", actual_build_extra)
    receipt = (await invoke(state)).reply.await_args.args[0]
    prompt = state.calls[0]["messages"][0]["content"]
    assert f"Workspace root for this session: {receipt['workspace']['path']}" in prompt
    assert f"Workspace root for this session: {state.source}\n" not in prompt


async def test_concurrent_duplicate_detached_waiter_owns_one_run(agent_env):
    state = agent_env
    state.release.clear()
    first = asyncio.create_task(invoke(state))
    await asyncio.wait_for(state.entered.wait(), 5)
    second = asyncio.create_task(invoke(state))
    first.cancel()
    await asyncio.gather(first, return_exceptions=True)
    state.release.set()
    reply = await second
    reply.reply_error.assert_not_awaited()
    assert len(state.calls) == 1
    assert len(await state.db.fetchall("SELECT * FROM agent_root_admissions")) == 1
    assert len(await state.dao.list_runs()) == 1
    assert not get_run_service(state.server)._by_run


async def test_two_database_connections_atomically_admit_one_execution_session(agent_env):
    from minimax_code.agent_execution import AgentExecutionSpec, canonical_intent
    from minimax_code.storage.dao.runs import AgentRunsDAO
    from minimax_code.storage.db import AsyncDatabase

    state = agent_env
    values = dict(name="worker", request="Inspect hello.txt", session_id="one")
    intent = canonical_intent(values, "agent.invoke")
    source = await state.sessions.get("one")
    other = AsyncDatabase(state.db.path)
    await other.connect()
    try:
        async def admit(dao, suffix):
            spec, _ = await AgentExecutionSpec.capture(state.agent, state.db, state.server,
                values, intent, str(state.source), source["project_id"], f"execution-{suffix}", f"copy-{suffix}")
            return await dao.admit_run(session_id=f"execution-{suffix}", request={"mode": "execute", "content": "Inspect hello.txt"},
                title="agent:worker", metadata={"agent_execution": spec.snapshot()}, request_key="atomic-once")

        results = await asyncio.gather(admit(state.dao, "a"), admit(AgentRunsDAO(other), "b"))
        assert sorted(created for _, created in results) == [False, True]
        assert results[0][0]["id"] == results[1][0]["id"]
        assert len(await state.db.fetchall("SELECT * FROM agent_root_admissions")) == 1
        assert len(await state.db.fetchall("SELECT * FROM sessions WHERE id LIKE 'execution-%'")) == 1
        assert len(await state.db.fetchall("SELECT * FROM budget_families")) == 1
        assert not state.calls and not (state.source / ".minimax").exists()
    finally:
        await other.close()


async def test_source_binding_change_during_capture_cannot_admit(agent_env, monkeypatch):
    from minimax_code.agent_execution import AgentExecutionSpec

    state = agent_env
    original = AgentExecutionSpec.capture.__func__

    async def capture(cls, *args, **kwargs):
        result = await original(cls, *args, **kwargs)
        await state.sessions.update("one", workspace_path=str(state.root))
        return result

    monkeypatch.setattr(AgentExecutionSpec, "capture", classmethod(capture))
    ctx = await invoke(state)
    assert "workspace changed during admission" in str(ctx.reply_error.await_args)
    assert not state.calls and not await state.dao.list_runs()


async def test_changed_provider_route_during_capture_refuses_before_admission(agent_env):
    from minimax_code.storage.dao.providers import ProviderDAO

    state = agent_env
    await ProviderDAO(state.db).update(state.provider["id"], base_url="https://changed.invalid/v1")
    ctx = await invoke(state)
    assert "configuration is changing" in str(ctx.reply_error.await_args)
    assert not state.calls and not await state.dao.list_runs()
    assert not (state.source / ".minimax").exists()


async def test_public_client_run_id_is_only_receipt_association(agent_env):
    state = agent_env
    ctx = await invoke(state, run_id="client-claimed-run")
    receipt = ctx.reply.await_args.args[0]
    assert receipt["run_id"] != "client-claimed-run"
    assert receipt["client_run_id"] == "client-claimed-run"
    replay = await invoke(state, run_id="different-client-association")
    assert replay.reply.await_args.args[0] == receipt and len(state.calls) == 1


async def test_name_precedes_compatibility_agent_id(agent_env):
    state = agent_env
    other = await state.agents.upsert(name="other", system_prompt="Other agent", tool_allowlist=[])
    receipt = (await invoke(state, agent_id=other["id"])).reply.await_args.args[0]
    assert receipt["agent_id"] == state.agent["id"] and receipt["agent"] == "worker"
    assert "Frozen agent prompt" in state.calls[0]["messages"][0]["content"]


async def test_mapping_owners_cannot_be_deleted_to_reexecute_same_key(agent_env):
    import sqlite3

    from minimax_code.ipc.handlers_sessions import register_session_handlers

    state = agent_env
    receipt = (await invoke(state)).reply.await_args.args[0]
    register_session_handlers(state.server, dao=state.sessions)
    for session_id in ("one", receipt["session_id"]):
        ctx = Context(state.server)
        await state.server._handlers["session.delete"]({"session_id":session_id}, ctx)
        assert ctx.reply_error.await_args.args[0] == -32602
        assert "archive it instead" in ctx.reply_error.await_args.args[1]
        assert await state.sessions.get(session_id)
    with pytest.raises(sqlite3.IntegrityError):
        async with state.db.transaction() as conn:
            await conn.execute("DELETE FROM agent_runs WHERE id=?", (receipt["run_id"],))
    replay = await invoke(state)
    assert replay.reply.await_args.args[0] == receipt and len(state.calls) == 1
    assert len(await state.db.fetchall("SELECT * FROM budget_attempts")) == 1


@pytest.mark.parametrize("table", ["sessions", "agent_runs", "agent_root_admissions"])
async def test_root_admission_transaction_rolls_back_before_prepare_or_sdk(agent_env, table):
    state = agent_env
    async with state.db.transaction() as conn:
        await conn.execute(f"CREATE TRIGGER fail_admit BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT, 'storage refusal'); END")
    ctx = await invoke(state)
    assert ctx.reply_error.await_count == 1
    assert not state.calls and not (state.source / ".minimax").exists()
    assert not await state.db.fetchall("SELECT * FROM agent_root_admissions")
    assert not await state.dao.list_runs()
    assert {s["id"] for s in await state.db.fetchall("SELECT id FROM sessions")} == {"one", "two"}


async def test_ready_persistence_failure_retains_original_run_without_sdk(agent_env):
    state = agent_env
    async with state.db.transaction() as conn:
        await conn.execute("CREATE TRIGGER fail_ready BEFORE UPDATE OF workspace_path ON sessions BEGIN SELECT RAISE(ABORT, 'ready refusal'); END")
    ctx = await invoke(state)
    assert ctx.reply_error.await_count == 1 and not state.calls
    mapping = (await state.db.fetchall("SELECT * FROM agent_root_admissions"))[0]
    run = await state.dao.get_run(mapping["run_id"])
    assert run["status"] == "failed" and not run["metadata"]["agent_execution"]["prepared"]
    assert len(list((state.source / ".minimax" / "child-workspaces").iterdir())) == 1
    replay = await invoke(state)
    assert replay.reply_error.await_count == 1 and not state.calls
    assert len(await state.dao.list_runs()) == 1


async def test_submit_failure_retains_atomic_run_for_replay(agent_env, monkeypatch):
    state = agent_env
    service = get_run_service(state.server)
    monkeypatch.setattr(service, "submit", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("submit refused")))
    ctx = await invoke(state)
    assert ctx.reply_error.await_count == 1 and not state.calls
    mapping = (await state.db.fetchall("SELECT * FROM agent_root_admissions"))[0]
    run = await state.dao.get_run(mapping["run_id"])
    assert run["status"] == "cancelled"
    replay = await invoke(state)
    receipt = replay.reply.await_args.args[0]
    assert receipt["run_id"] == receipt["agent_run_id"] == run["id"]
    assert receipt["agent"] == "worker" and receipt["agent_id"] == state.agent["id"]
    assert receipt["status"] == receipt["run_status"] == "cancelled"
    assert receipt["partial"] and receipt["cancelled"] and receipt["parent_session_id"] == "one"
    assert receipt == await state.dao.result_payload(run["id"])
    assert len(await state.dao.list_runs()) == 1


async def test_cancel_prepare_is_original_run_and_never_reprepared(agent_env, monkeypatch):
    from minimax_code.workspace import child

    state = agent_env
    entered, released = asyncio.Event(), asyncio.Event()
    prepares = []
    original = child.prepare_child

    async def prepare(*args):
        prepares.append(args)
        result = await original(*args)
        entered.set()
        await released.wait()
        return result

    monkeypatch.setattr(child, "prepare_child", prepare)
    ctx = await invoke(state, admission_only=True)
    run = ctx.reply.await_args.args[0]["run"]
    await asyncio.wait_for(entered.wait(), 5)
    cancel = Context(state.server)
    await state.server._handlers["agent.cancel_subagent"]({"run_id": run["id"]}, cancel)
    assert cancel.reply.await_args.args[0]["cancel_requested"]
    job = service_job = get_run_service(state.server).get_job(run["id"])
    await asyncio.gather(job.task, return_exceptions=True)
    assert service_job.task.done()
    assert (await state.dao.get_run(run["id"]))["status"] == "cancelled"
    replay = await invoke(state)
    assert replay.reply.await_args.args[0]["run_id"] == run["id"]
    assert not state.calls and len(prepares) == 1


async def test_double_cancel_prepare_retains_actual_process_cleanup(agent_env, monkeypatch):
    import sys

    from minimax_code import execution
    from minimax_code.execution import ExecutionPolicy, execution_scope
    from minimax_code.workspace import child

    state = agent_env
    (state.source / ".git").mkdir()
    entered, release = asyncio.Event(), asyncio.Event()
    processes = []
    terminate = execution.terminate_tree

    async def delayed_terminate(process):
        entered.set()
        await release.wait()
        await terminate(process)

    async def git(root, *args):
        with execution_scope(ExecutionPolicy(workspace=root)):
            process = await execution.spawn_exec(sys.executable, "-c", "import time; time.sleep(60)")
            processes.append(process)
            await execution.communicate_bounded(process, timeout=120)
        return ""

    monkeypatch.setattr(child, "_git", git)
    monkeypatch.setattr(execution, "terminate_tree", delayed_terminate)
    ctx = await invoke(state, admission_only=True)
    run = ctx.reply.await_args.args[0]["run"]
    service = get_run_service(state.server)
    job = service.get_job(run["id"])
    try:
        for _ in range(100):
            if processes:
                break
            await asyncio.sleep(0.01)
        assert len(processes) == 1
        service.cancel_run(run["id"])
        await asyncio.wait_for(entered.wait(), 5)
        job.task.cancel()
        await asyncio.sleep(0)
        assert not job.task.done() and processes[0].returncode is None
        assert service.get_job(run["id"]) is job and not state.calls
    finally:
        release.set()
        await asyncio.gather(job.task, return_exceptions=True)
    assert processes[0].returncode is not None
    assert (await state.dao.get_run(run["id"]))["status"] == "cancelled"
    assert not state.calls
    replay = await invoke(state)
    assert replay.reply.await_args.args[0]["run_id"] == run["id"] and len(processes) == 1


@pytest.mark.parametrize("field", ["family_id", "parent_run_id", "instructions", "internal_agent"])
async def test_public_authority_injection_is_rejected(agent_env, field):
    ctx = await invoke(agent_env, **{field: "forged"})
    assert ctx.reply_error.await_count == 1 and not agent_env.calls


async def test_generic_entry_cannot_reuse_agent_execution_session(agent_env):
    from minimax_code.ipc.builtins import handle_agent_send_message

    state = agent_env
    ctx = await invoke(state)
    sid = ctx.reply.await_args.args[0]["session_id"]
    generic = Context(state.server)
    await handle_agent_send_message({"session_id": sid, "content": "bypass"}, generic)
    assert "saved profile" in str(generic.reply_error.await_args)
    assert len(state.calls) == 1


async def prepare_gate(monkeypatch):
    from minimax_code.agent_execution import AgentExecutionSpec

    entered, release = asyncio.Event(), asyncio.Event()
    original = AgentExecutionSpec.prepare

    async def gate(self, *args):
        entered.set()
        await release.wait()
        return await original(self, *args)

    monkeypatch.setattr(AgentExecutionSpec, "prepare", gate)
    return entered, release


async def test_queued_agent_prompt_tools_model_are_frozen(agent_env, monkeypatch):
    from minimax_code.storage.dao.providers import ProviderDAO

    state = agent_env
    await ProviderDAO(state.db).update(state.provider["id"], models=[{"id": "wire"}, {"id": "agent-wire"}])
    # Agent's explicit verified model takes precedence over the selected parent.
    await state.agents.upsert(name="worker", system_prompt="Frozen agent prompt", model="agent-wire",
                              tool_allowlist=["read_file"])
    entered, release = await prepare_gate(monkeypatch)
    ctx = await invoke(state, admission_only=True)
    run = ctx.reply.await_args.args[0]["run"]
    await entered.wait()
    state.selected.default_model = "unrelated-global"
    await state.agents.upsert(name="worker", system_prompt="changed prompt", model="changed-model",
                              tool_allowlist=["write_file"])
    job = get_run_service(state.server).get_job(run["id"])
    release.set()
    await get_run_service(state.server).wait(job)
    assert state.calls[0]["model"] == "agent-wire"
    assert "Frozen agent prompt" in state.calls[0]["messages"][0]["content"]
    assert "changed prompt" not in state.calls[0]["messages"][0]["content"]
    assert {t["function"]["name"] for t in state.calls[0]["tools"]} == {"read_file"}


@pytest.mark.parametrize("change", ["disabled", "provider", "source", "permissions", "tool"])
async def test_queued_revocations_and_dependencies_fail_closed(agent_env, monkeypatch, change):
    from unittest.mock import AsyncMock

    from minimax_code.agent.tools import get_default_registry
    from minimax_code.storage.dao.providers import ProviderDAO

    state = agent_env
    entered, release = await prepare_gate(monkeypatch)
    ctx = await invoke(state, admission_only=True)
    run = ctx.reply.await_args.args[0]["run"]
    await entered.wait()
    if change == "disabled":
        await state.agents.upsert(name="worker", system_prompt="x", enabled=False)
    elif change == "provider":
        await ProviderDAO(state.db).update(state.provider["id"], base_url="https://changed.invalid")
    elif change == "source":
        state.source.rename(state.source.with_name("old-source"))
        state.source.mkdir()
    elif change == "permissions":
        monkeypatch.setattr("minimax_code.ipc.handlers_permissions._ensure_permission_store", AsyncMock(return_value=None))
    else:
        tool = get_default_registry().get("read_file")
        monkeypatch.setattr(tool, "description", "dependency changed")
    job = get_run_service(state.server).get_job(run["id"])
    release.set()
    await asyncio.gather(job.task, return_exceptions=True)
    row = await state.dao.get_run(run["id"])
    assert row["status"] == "failed" and not state.calls
    assert (await state.dao.result_payload(run["id"]))["partial"]


async def test_permission_revocation_blocks_tool_but_preserves_honest_parent_result(agent_env, monkeypatch):
    state = agent_env
    entered, release = await prepare_gate(monkeypatch)
    ctx = await invoke(state, admission_only=True)
    await entered.wait()
    await state.permissions.upsert(tool_pattern="*", action="deny")
    state.wire = lambda _: tool_response("read_file", {"path": "hello.txt"}) if len(state.calls) == 1 else response()
    job = get_run_service(state.server).get_job(ctx.reply.await_args.args[0]["run"]["id"])
    release.set()
    await get_run_service(state.server).wait(job)
    result = await state.dao.result_payload(job.run_id)
    assert result["partial"] and result["verification"]["failed_tool_calls"] == 1
    assert len(state.calls) == 2


async def test_parallel_public_roots_write_independent_copies_and_families(agent_env):
    state = agent_env
    await state.agents.upsert(name="worker", system_prompt="Write", tool_allowlist=["write_file"])

    def wire(request):
        messages = json.loads(request.content)["messages"]
        if any(message["role"] == "tool" for message in messages):
            return response()
        content = next(message["content"] for message in messages if message["role"] == "user")
        return tool_response("write_file", {"path": "hello.txt", "content": content})

    state.wire = wire
    first, second = await asyncio.gather(invoke(state, request="left"), invoke(state, request="right", idempotency_key="right"))
    first.reply_error.assert_not_awaited()
    second.reply_error.assert_not_awaited()
    a, b = first.reply.await_args.args[0], second.reply.await_args.args[0]
    assert a["session_id"] != b["session_id"] and a["run_id"] != b["run_id"]
    assert Path(a["workspace"]["path"]).joinpath("hello.txt").read_text() == "left"
    assert Path(b["workspace"]["path"]).joinpath("hello.txt").read_text() == "right"
    assert (state.source / "hello.txt").read_text() == "source-a"
    rows = [await state.dao.get_run(x["run_id"]) for x in (a, b)]
    assert rows[0]["metadata"]["budget_family_id"] != rows[1]["metadata"]["budget_family_id"]


async def test_budget_recovery_keeps_profile_copy_and_cumulative_family(agent_env):
    state = agent_env
    ctx = await invoke(state, budget_tokens=1)
    paused = ctx.reply.await_args.args[0]
    assert paused["run_status"] == "budget_paused" and not state.calls
    state.selected.default_model = "unrelated-global"
    resume = Context(state.server)
    await state.server._handlers["run.resume"]({"run_id": paused["run_id"], "idempotency_key": "recover",
        "budget_tokens": 100000, "reviewed": True}, resume)
    resume.reply_error.assert_not_awaited()
    run = resume.reply.await_args.args[0]["run"]
    job = get_run_service(state.server).get_job(run["id"])
    await get_run_service(state.server).wait(job)
    result = await state.dao.result_payload(run["id"])
    assert result["status"] == "completed" and result["workspace"]["path"] == paused["workspace"]["path"]
    original = await state.dao.get_run(paused["run_id"])
    assert run["metadata"]["budget_family_id"] == original["metadata"]["budget_family_id"]
    assert len(list((state.source / ".minimax" / "child-workspaces").iterdir())) == 1
    assert len(state.calls) == 1 and state.calls[0]["model"] == "wire"


async def test_agent_continue_uses_saved_profile_copy_and_family(agent_env):
    from minimax_code.ipc.builtins import handle_agent_continue_run

    state = agent_env
    await state.agents.upsert(name="worker", system_prompt="Saved block", tool_allowlist=["read_file"], max_iterations=1)
    state.wire = lambda _: tool_response("read_file", {"path": "hello.txt"})
    first = await invoke(state)
    receipt = first.reply.await_args.args[0]
    assert receipt["truncated"]
    state.wire = lambda _: response()
    state.selected.default_model = "unrelated-global"
    await state.agents.upsert(name="worker", system_prompt="New instructions", tool_allowlist=["write_file"])
    ctx = Context(state.server)
    await handle_agent_continue_run({"session_id": receipt["session_id"], "run_id": receipt["run_id"]}, ctx)
    ctx.reply_error.assert_not_awaited()
    continued = ctx.reply.await_args.args[0]
    old, new = [await state.dao.get_run(row["run_id"]) for row in (receipt, continued)]
    assert continued["agent_id"] == receipt["agent_id"] and continued["workspace"]["path"] == receipt["workspace"]["path"]
    assert new["metadata"]["budget_family_id"] == old["metadata"]["budget_family_id"]
    assert state.calls[-1]["model"] == "wire" and "Saved block" in state.calls[-1]["messages"][0]["content"]
    assert {tool["function"]["name"] for tool in state.calls[-1]["tools"]} == {"read_file"}


@pytest.mark.parametrize("change", ["missing_profile", "missing_copy", "disabled"])
async def test_reviewed_agent_recovery_never_falls_back_to_generic_chat(agent_env, change):
    state = agent_env
    first = await invoke(state, budget_tokens=1)
    receipt = first.reply.await_args.args[0]
    run = await state.dao.get_run(receipt["run_id"])
    if change == "missing_profile":
        metadata = dict(run["metadata"])
        metadata.pop("agent_execution")
        await state.dao.update_run_status(run["id"], status="budget_paused", metadata=metadata)
    elif change == "missing_copy":
        copy = Path(receipt["workspace"]["path"])
        copy.rename(copy.with_name("retained-unconfirmed"))
    else:
        await state.agents.upsert(name="worker", system_prompt="x", enabled=False)
    ctx = Context(state.server)
    await state.server._handlers["run.resume"]({"run_id":run["id"],"idempotency_key":"invalid-recovery",
        "budget_tokens":100000,"reviewed":True}, ctx)
    assert ctx.reply_error.await_count == 1 and not state.calls


@pytest.mark.parametrize("prepared", [False, True])
async def test_actual_restart_snapshot_preserves_pending_or_sent_run(agent_env, monkeypatch, prepared):
    import sqlite3

    from minimax_code import app
    from minimax_code.config import Config
    from minimax_code.ipc.handlers_runs import register_run_handlers
    from minimax_code.ipc.server import IPCServer
    from minimax_code.storage.dao.budgets import BudgetsDAO
    from minimax_code.storage.dao.runs import AgentRunsDAO
    from minimax_code.storage.dao.sessions import SessionsDAO
    from minimax_code.storage.db import AsyncDatabase

    state = agent_env
    if prepared:
        state.release.clear()
        entered = state.entered
    else:
        entered, _ = await prepare_gate(monkeypatch)
    accepted = await invoke(state, admission_only=True)
    initial = accepted.reply.await_args.args[0]["run"]
    await asyncio.wait_for(entered.wait(), 5)
    path = state.root / "crashed.db"
    target = sqlite3.connect(path)
    try:
        await state.db._conn.backup(target)
    finally:
        target.close()
    # The copied SQLite image is the exact pre-crash state, not a cancelled
    # row rewritten to look interrupted. Shut down only the original service.
    await get_run_service(state.server).shutdown()
    count = len(state.calls)
    reopened = AsyncDatabase(path)
    await reopened.connect()
    dao = AgentRunsDAO(reopened)
    server = IPCServer(Config())
    register_agent_handlers(server)
    register_run_handlers(server)
    monkeypatch.setattr(app, "get_db", lambda: reopened)
    monkeypatch.setattr(app, "get_sessions_dao", lambda: SessionsDAO(reopened))
    try:
        await dao.recover_orphans()
        run = await dao.get_run(initial["id"])
        receipt = await dao.result_payload(run["id"])
        assert run["status"] == receipt["status"] == receipt["run_status"] == "interrupted"
        assert receipt["partial"] and receipt["agent_run_id"] == run["id"]
        assert run["metadata"]["agent_execution"]["prepared"] is prepared
        replay = Context(server)
        await server._handlers["agent.invoke"]({"name": "worker", "request": "Inspect hello.txt",
            "session_id": "one", "idempotency_key": "agent-once", "budget_tokens": 100000}, replay)
        assert replay.reply.await_args.args[0] == receipt and len(state.calls) == count
        attempts = await BudgetsDAO(reopened).attempts(run["metadata"]["budget_family_id"])
        assert len(attempts) == int(prepared)
        if prepared:
            assert attempts[0]["state"] == "unknown"
        resume = Context(server)
        await server._handlers["run.resume"]({"run_id": run["id"], "idempotency_key": "restart",
            "reviewed": True}, resume)
        if not prepared:
            assert "preparation is unconfirmed" in str(resume.reply_error.await_args)
        else:
            held = resume.reply.await_args.args[0]["run"]
            job = get_run_service(server).get_job(held["id"])
            await get_run_service(server).wait(job)
            assert (await dao.get_run(held["id"]))["status"] == "budget_paused"
            assert len(state.calls) == count
            await BudgetsDAO(reopened).reconcile(target="attempt", target_id=attempts[0]["id"],
                liability_tokens=attempts[0]["estimated_tokens"], reason="Explicit offline liability authorization")
            state.release.set()
            resumed = Context(server)
            await server._handlers["run.resume"]({"run_id": held["id"], "idempotency_key": "reconciled",
                "budget_tokens": 300000, "reviewed": True}, resumed)
            final = resumed.reply.await_args.args[0]["run"]
            await get_run_service(server).wait(get_run_service(server).get_job(final["id"]))
            result = await dao.result_payload(final["id"])
            assert result["status"] == "completed" and result["workspace"]["path"] == run["metadata"]["workspace_snapshot"]
            assert final["metadata"]["budget_family_id"] == run["metadata"]["budget_family_id"]
            assert len(state.calls) == count + 1
    finally:
        await get_run_service(server).shutdown()
        await reopened.close()


async def test_actual_recursive_agent_children_have_durable_parents_and_budget_owners(agent_env, tmp_path_factory):
    from minimax_code.storage.dao.budgets import BudgetsDAO

    state = agent_env
    # Three actual copies must remain below the host's Windows path limit;
    # the long descriptive pytest directory name itself exceeded it.
    state.source = tmp_path_factory.mktemp("ar")
    (state.source / "hello.txt").write_text("source-a", encoding="utf-8")
    await state.sessions.update("one", workspace_path=str(state.source))
    await state.agents.upsert(name="worker", system_prompt="Worker", tool_allowlist=["spawn_subagent"], max_iterations=100)
    seen = set()

    def wire(request):
        body = json.loads(request.content)
        user = next(msg["content"] for msg in body["messages"] if msg["role"] == "user")
        if user in {"Inspect hello.txt", "child"} and user not in seen:
            seen.add(user)
            return tool_response("spawn_subagent", {"agent_name": "worker", "prompt": "child" if user == "Inspect hello.txt" else "grandchild", "wait": False})
        return response()

    state.wire = wire
    ctx = await invoke(state, budget_tokens=300000)
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.await_args.args[0]
    root = await state.dao.get_run(receipt["run_id"])
    attempts = await BudgetsDAO(state.db).attempts(root["metadata"]["budget_family_id"])
    assert len(attempts) == 5 and len({row["run_id"] for row in attempts}) == 3
    owners = {row["run_id"]: await state.dao.get_run(row["run_id"]) for row in attempts}
    assert all(owner and owner["metadata"]["budget_family_id"] == root["metadata"]["budget_family_id"] for owner in owners.values())
    sessions = {owner["session_id"] for owner in owners.values()}
    assert len(sessions) == 3 and "one" not in sessions
    for owner in owners.values():
        if owner["mode"] == "subagent":
            parent = owners[owner["metadata"]["parent_run_id"]]
            assert owner["metadata"]["parent_session_id"] == parent["session_id"]
    assert len(receipt["verification"]["children"]) == 2 and receipt["budget"]["known_tokens"] == 100
    assert state.clients[0]._transport._client is None


@pytest.mark.parametrize("supported", [False, True])
async def test_explicit_child_model_is_verified_not_silently_replaced(agent_env, supported):
    from minimax_code.storage.dao.providers import ProviderDAO

    state = agent_env
    await state.agents.upsert(name="worker", system_prompt="Root", tool_allowlist=["spawn_subagent"])
    await state.agents.upsert(name="child", system_prompt="Child", model="child-wire", tool_allowlist=["read_file"])
    if supported:
        await ProviderDAO(state.db).update(state.provider["id"], models=[{"id": "wire"}, {"id": "child-wire"}])
    first = True

    def wire(request):
        nonlocal first
        if first:
            first = False
            return tool_response("spawn_subagent", {"agent_name": "child", "prompt": "child", "wait": True})
        return response()

    state.wire = wire
    ctx = await invoke(state, budget_tokens=300000)
    result = ctx.reply.await_args.args[0]
    calls = [call for call in state.calls if next(m["content"] for m in call["messages"] if m["role"] == "user") == "child"]
    if supported:
        assert len(calls) == 1 and calls[0]["model"] == "child-wire"
    else:
        assert not calls and result["partial"] and result["verification"]["failed_tool_calls"] == 1


async def test_agent_root_double_cancel_retains_real_descendants_until_cleanup(agent_env, monkeypatch):
    import sys

    from minimax_code import execution
    from minimax_code.agent.tools import ReadFileTool, ToolResult
    from minimax_code.orchestrator.subagent import register_background_run

    state = agent_env
    await state.agents.upsert(name="worker", system_prompt="Worker", tool_allowlist=["read_file"], max_iterations=1)
    entered, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    children, processes = [], []
    completed = 0
    original = execution.terminate_tree

    async def terminate(proc):
        nonlocal completed
        if len(processes) == 1:
            started = asyncio.Event()

            async def late():
                process = await execution.spawn_exec(sys.executable, "-c", "import time; time.sleep(60)")
                processes.append(process)
                started.set()
                await execution.communicate_bounded(process, timeout=120)

            task = asyncio.create_task(late())
            children.append(task)
            await started.wait()
            register_background_run("late-agent-grandchild", task)
        else:
            entered.set()
        await release.wait()
        await original(proc)
        completed += 1
        if completed == 2:
            cleaned.set()

    async def tool(self, **kwargs):
        async def child():
            proc = await execution.spawn_exec(sys.executable, "-c", "import time; time.sleep(60)")
            processes.append(proc)
            await execution.communicate_bounded(proc, timeout=120)
        task = asyncio.create_task(child())
        children.append(task)
        register_background_run("agent-process-child", task)
        await asyncio.sleep(0.1)
        return ToolResult(success=True, output="child started")

    monkeypatch.setattr(execution, "terminate_tree", terminate)
    monkeypatch.setattr(ReadFileTool, "run", tool)
    state.wire = lambda _: tool_response("read_file", {"path": "hello.txt"})
    ctx = await invoke(state, admission_only=True)
    run = ctx.reply.await_args.args[0]["run"]
    service = get_run_service(state.server)
    job = service.get_job(run["id"])
    try:
        await asyncio.wait_for(entered.wait(), 8)
        prelim = await state.dao.result_payload(run["id"])
        assert prelim["agent_run_id"] == run["id"] and prelim["status"] == "running"
        assert len(prelim["tool_calls"]) == 1
        sdk = state.clients[0]._transport._client
        service.cancel_run(run["id"])
        await asyncio.sleep(0)
        job.task.cancel()
        await asyncio.sleep(0)
        assert not job.task.done() and not cleaned.is_set() and all(not task.done() for task in children)
        assert not sdk.is_closed() and service.get_job(run["id"]) is job
        assert state.server._permission_gaters[run["session_id"]]
    finally:
        release.set()
        await asyncio.gather(job.task, return_exceptions=True)
    assert cleaned.is_set() and all(task.done() for task in children)
    assert all(proc.returncode is not None for proc in processes) and sdk.is_closed()
    final = await state.dao.result_payload(run["id"])
    assert final["cancelled"] and final["partial"] and final["text"]
    assert (await state.dao.get_run(run["id"]))["status"] == "cancelled"


@pytest.mark.parametrize("phase", ["preliminary", "terminal", "all_terminal"])
@pytest.mark.parametrize("failure", ["error", "missing"])
async def test_profile_receipt_persistence_refusal_never_returns_success(agent_env, monkeypatch, phase, failure):
    from minimax_code.storage.dao.runs import AgentRunsDAO

    state = agent_env
    update, terminate = AgentRunsDAO.update_run_status, AgentRunsDAO.terminate_run

    async def refuse_update(self, run_id, **kwargs):
        result = kwargs.get("result") or {}
        refusing = result.get("agent_id") and (
            (phase == "preliminary" and kwargs["status"] == "running")
            or (phase in {"terminal", "all_terminal"} and kwargs["status"] == "completed"))
        if refusing:
            if failure == "missing":
                return None
            raise OSError("profile storage refused")
        return await update(self, run_id, **kwargs)

    async def refuse_terminate(self, *args, **kwargs):
        if phase == "all_terminal":
            if failure == "missing":
                return None
            raise OSError("failure receipt storage also refused")
        return await terminate(self, *args, **kwargs)

    monkeypatch.setattr(AgentRunsDAO, "update_run_status", refuse_update)
    monkeypatch.setattr(AgentRunsDAO, "terminate_run", refuse_terminate)
    ctx = await invoke(state)
    ctx.reply.assert_not_awaited()
    assert ctx.reply_error.await_count == 1 and len(state.calls) == 1
    run = (await state.dao.list_runs())[0]
    assert run["status"] != "completed"
    assert all(llm._transport._client is None for llm in state.clients)
    if phase != "all_terminal":
        result = await state.dao.result_payload(run["id"])
        assert result["status"] == "failed" and result["partial"]
