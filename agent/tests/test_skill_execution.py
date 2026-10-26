"""Durable skill handler + actual AgentCore/SQLite/SDK replay, offline only."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_budget_transport import client, response
from test_run_service import Context

from minimax_code.agent.skills.loader import Skill
from minimax_code.agent.skills.registry import SkillRegistry
from minimax_code.agent.skills.runtime import SkillRuntime, SkillToolProvider
from minimax_code.agent.tools import ToolRegistry
from minimax_code.agent.tools.base import Tool, ToolResult
from minimax_code.config import Config
from minimax_code.ipc.handlers_runs import register_run_handlers
from minimax_code.ipc.handlers_skills import register_skill_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.model_snapshot import ModelSnapshot
from minimax_code.permissions import PermissionStore
from minimax_code.runs import get_run_service
from minimax_code.storage.dao.budgets import BudgetsDAO
from minimax_code.storage.dao.permissions import PermissionRuleDAO
from minimax_code.storage.dao.providers import ProviderDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase


class SkillTool(Tool):
    name = "skill_probe"
    description = "Selected skill provider tool"
    parameters = {"type": "object", "properties": {}}

    async def run(self, **kwargs):
        return ToolResult(success=True, output="private")


class Provider(SkillToolProvider):
    def install(self, registry):
        registry.register(SkillTool())
        return {"skill_probe"}


@pytest.fixture
async def skill_env(tmp_path, monkeypatch):
    from minimax_code import app, skill_execution
    from minimax_code.ipc import builtins

    db = AsyncDatabase(tmp_path / "skills.db")
    await db.connect()
    await db.migrate()
    sessions = SessionsDAO(db)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    for sid in ("one", "two"):
        await sessions.create(id=sid, workspace_path=str(workspace))
    provider = await ProviderDAO(db).create(
        name="offline",
        protocol="openai",
        base_url="https://offline.invalid/v1",
        models=[{"id": "wire"}],
    )
    selected = client(lambda request: pytest.fail("Shared client used"), require_budget=True)
    selected.provider_id = provider["id"]
    selected.base_url = provider["base_url"]
    registry = SkillRegistry(skills_root=tmp_path, auto_persist=False)
    skill = Skill(
        name="probe",
        description="",
        when_to_use="",
        body="Saved skill instructions",
        path=tmp_path / "probe",
        version="1",
        tools=["skill_probe"],
    )
    registry._skills[skill.skill_id] = skill
    runtime = SkillRuntime(registry=registry, llm=selected, tool_registry=ToolRegistry())
    runtime.register_tool_provider(skill, Provider())
    permissions = PermissionStore(PermissionRuleDAO(db))
    await permissions.warm()
    await permissions.upsert(tool_pattern="*", action="allow")
    server = IPCServer(Config())
    state = SimpleNamespace(
        db=db,
        sessions=sessions,
        dao=AgentRunsDAO(db),
        server=server,
        runtime=runtime,
        skill=skill,
        selected=selected,
        provider=provider,
        permissions=permissions,
        calls=[],
        clients=[],
        wire=None,
        entered=asyncio.Event(),
        release=asyncio.Event(),
        root=tmp_path,
    )
    state.release.set()

    async def handler(request):
        state.calls.append(json.loads(request.content))
        state.entered.set()
        await state.release.wait()
        return state.wire(request) if state.wire else response()

    def private(model):
        llm = client(handler, require_budget=True)
        llm.provider_id = model.provider_id
        llm.base_url = model.client.base_url
        state.clients.append(llm)
        return ModelSnapshot(
            llm,
            model.model,
            model.reasoning_effort,
            model.context_window,
            model.provider_id,
            "openai",
        )

    monkeypatch.setattr(app, "init_runtime", AsyncMock(return_value=runtime))
    monkeypatch.setattr(app, "get_db", lambda: db)
    monkeypatch.setattr(app, "get_sessions_dao", lambda: sessions)
    monkeypatch.setattr(app, "get_subagent_llm", lambda: selected)
    monkeypatch.setattr("minimax_code.secrets.get_provider_key", lambda *_: "offline")
    monkeypatch.setattr(app, "ensure_codebase_indexer", lambda *args: None)
    monkeypatch.setattr(app, "get_codebase_indexer", lambda: None)
    monkeypatch.setattr(
        app, "ensure_hook_manager", lambda: pytest.fail("Skill hooks must be disabled")
    )
    monkeypatch.setattr(app, "ensure_telemetry_engine", lambda: None)
    monkeypatch.setattr(
        builtins, "_build_system_prompt_extra", AsyncMock(return_value="Frozen context")
    )
    monkeypatch.setattr(builtins, "_baseline_git_workspace", AsyncMock(return_value=False))
    monkeypatch.setattr(skill_execution, "_private_model", private)
    monkeypatch.setattr(
        "minimax_code.ipc.handlers_permissions._ensure_permission_store",
        AsyncMock(return_value=permissions),
    )
    register_skill_handlers(server, runtime=runtime)
    register_run_handlers(server)
    try:
        yield state
    finally:
        state.release.set()
        await get_run_service(server).shutdown()
        for llm in state.clients:
            await llm.close()
        await selected.close()
        await db.close()


async def invoke(state, **changes):
    params = {
        "skill_id": state.skill.skill_id,
        "request": "Inspect this workspace",
        "session_id": "one",
        "idempotency_key": "skill-once",
        "budget_tokens": 100000,
        **changes,
    }
    ctx = Context(state.server)
    await state.server._handlers["skill.invoke"](params, ctx)
    return ctx


@pytest.mark.parametrize(
    "change", [{"base_url": "https://changed.invalid/v1"}, {"protocol": "anthropic"}]
)
async def test_capture_refuses_old_client_new_provider_configuration_window(skill_env, change):
    state = skill_env
    # provider.update has committed but rebuild_subagent_llm has not yet swapped
    # the global client. No admission, client allocation or request may follow.
    await ProviderDAO(state.db).update(state.provider["id"], **change)
    ctx = await invoke(state)
    ctx.reply.assert_not_awaited()
    assert "configuration is changing" in str(ctx.reply_error.await_args)
    assert not state.clients and not state.calls
    assert not await state.dao.list_runs(session_id="one")


@pytest.mark.parametrize("profile", [None, {}, "missing"])
async def test_malformed_or_missing_skill_profile_never_resumes_as_generic_chat(skill_env, profile):
    from minimax_code.ipc.builtins import handle_agent_continue_run

    state = skill_env
    completed = await invoke(state)
    run_id = completed.reply.await_args.args[0]["run_id"]
    row = await state.dao.get_run(run_id)
    metadata = {**row["metadata"], "truncated": True}
    if profile == "missing":
        metadata.pop("skill_execution")
    else:
        metadata["skill_execution"] = profile
    await state.dao.update_run_status(run_id, status="failed", metadata=metadata)
    resume = Context(state.server)
    await state.server._handlers["run.resume"](
        {"run_id": run_id, "idempotency_key": "invalid-profile", "reviewed": True}, resume
    )
    assert "start a new task" in str(resume.reply_error.await_args)
    await state.dao.update_run_status(run_id, status="completed", metadata=metadata)
    continuation = Context(state.server)
    await handle_agent_continue_run({"session_id": "one", "run_id": run_id}, continuation)
    assert "start a new task" in str(continuation.reply_error.await_args)
    assert len(state.calls) == 1


async def test_actual_handler_sdk_settlement_receipt_and_replay_before_registry(
    skill_env, monkeypatch
):
    state = skill_env
    ctx = await invoke(state)
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.await_args.args[0]
    run = await state.dao.get_run(receipt["run_id"])
    assert receipt == await state.dao.result_payload(run["id"])
    assert receipt["text"] == receipt["output"] == "answer"
    assert receipt["skill_id"] == state.skill.skill_id and receipt["status"] == "completed"
    assert (
        run["metadata"]["skill_execution"]["instructions"]
        == "# Skill: probe (v1)\n\nSaved skill instructions"
    )
    assert len(state.calls) == 1
    assert "Saved skill instructions" in state.calls[0]["messages"][0]["content"]
    names = {item["function"]["name"] for item in state.calls[0]["tools"]}
    assert {"read_file", "skill_probe"}.issubset(names)
    assert not state.runtime._default_tool_registry.has("skill_probe")
    attempts = await BudgetsDAO(state.db).attempts(run["metadata"]["budget_family_id"])
    assert len(attempts) == 1 and attempts[0]["state"] == "settled"
    assert attempts[0]["known_tokens"] == 20
    assert await state.db.fetchall("SELECT * FROM messages WHERE session_id='one'")
    assert await state.dao.list_steps(run_id=run["id"])
    monkeypatch.setattr(
        state.runtime.registry, "get", lambda *args: pytest.fail("Replay resolved registry")
    )
    retry = await invoke(state)
    assert retry.reply.await_args.args[0] == receipt and len(state.calls) == 1
    conflict = await invoke(state, request="Different intent")
    assert "different input" in str(conflict.reply_error.await_args)


async def test_concurrent_duplicate_detached_waiter_one_attempt(skill_env):
    state = skill_env
    state.release.clear()
    first = asyncio.create_task(invoke(state))
    await asyncio.wait_for(state.entered.wait(), 3)
    second = asyncio.create_task(invoke(state))
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    state.release.set()
    ctx = await asyncio.wait_for(second, 3)
    assert ctx.reply.await_args.args[0]["status"] == "completed"
    assert len(state.calls) == 1
    replay = await invoke(state, admission_only=True)
    assert replay.reply.await_args.args[0]["created"] is False
    assert len(await state.dao.list_runs(session_id="one")) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"request": {}},
        {"request": " "},
        {"instructions": "injected"},
        {"family_id": "injected"},
        {"session_id": "missing"},
        {"idempotency_key": ""},
        {"max_iterations": False},
    ],
)
async def test_public_intent_validation_prevents_model_work(skill_env, change):
    ctx = await invoke(skill_env, **change)
    ctx.reply.assert_not_awaited()
    ctx.reply_error.assert_awaited_once()
    assert not skill_env.calls


async def queue_blocker(state):
    entered, release = asyncio.Event(), asyncio.Event()

    async def operation(job):
        entered.set()
        await release.wait()

    blocker = get_run_service(state.server).submit("one", operation)
    await entered.wait()
    return blocker, release


async def test_queued_model_instructions_context_frozen(skill_env):
    state = skill_env
    blocker, release = await queue_blocker(state)
    ctx = await invoke(state, admission_only=True)
    run = ctx.reply.await_args.args[0]["run"]
    state.selected.default_model = "new-global-model"
    state.skill.body = "Changed live instructions"
    release.set()
    service = get_run_service(state.server)
    await service.wait(blocker)
    await service.wait(service.get_job(run["id"]))
    assert state.calls[0]["model"] == "wire"
    prompt = state.calls[0]["messages"][0]["content"]
    assert "Saved skill instructions" in prompt and "Changed live instructions" not in prompt
    assert "Frozen context" in prompt


@pytest.mark.parametrize("change", ["disabled", "version", "tools", "provider", "permission"])
async def test_queued_dependency_changes_fail_closed(skill_env, monkeypatch, change):
    state = skill_env
    blocker, release = await queue_blocker(state)
    ctx = await invoke(state, admission_only=True)
    run = ctx.reply.await_args.args[0]["run"]
    if change == "disabled":
        state.skill.enabled = False
    elif change == "version":
        state.skill.version = "2"
    elif change == "tools":
        state.skill.tools = []
    elif change == "provider":
        await ProviderDAO(state.db).update(state.provider["id"], base_url="https://changed.invalid")
    else:
        monkeypatch.setattr(
            "minimax_code.ipc.handlers_permissions._ensure_permission_store",
            AsyncMock(return_value=None),
        )
    release.set()
    service = get_run_service(state.server)
    await service.wait(blocker)
    job = service.get_job(run["id"])
    await asyncio.gather(service.wait(job), return_exceptions=True)
    receipt = await state.dao.result_payload(run["id"])
    assert (await state.dao.get_run(run["id"]))["status"] == "failed"
    assert receipt["skill_id"] == state.skill.skill_id and receipt["partial"]
    assert receipt["status"] == "failed" and not state.calls


def tool_response():
    result = response(tool=True)
    return type(result)(
        200,
        headers={"content-type": "text/event-stream"},
        content=result.content.replace(b'"name": "echo"', b'"name": "skill_probe"'),
    )


async def test_queued_permission_revocation_reaches_normal_tool_gate(skill_env, monkeypatch):
    state = skill_env
    called = []

    async def tool_run(self, **kwargs):
        called.append(True)
        return ToolResult(success=True, output="should not run")

    monkeypatch.setattr(SkillTool, "run", tool_run)
    blocker, release = await queue_blocker(state)
    ctx = await invoke(state, admission_only=True)
    run = ctx.reply.await_args.args[0]["run"]
    await state.permissions.upsert(tool_pattern="*", action="deny")
    state.wire = lambda _: tool_response() if len(state.calls) == 1 else response()
    release.set()
    service = get_run_service(state.server)
    await service.wait(blocker)
    await service.wait(service.get_job(run["id"]))
    receipt = await state.dao.result_payload(run["id"])
    assert not called and len(state.calls) == 2
    assert receipt["verification"]["failed_tool_calls"] == 1 and receipt["partial"]


async def test_resume_restores_profile_family_and_rejects_changed_dependencies(skill_env):
    state = skill_env
    ctx = await invoke(state, budget_tokens=1)
    receipt = ctx.reply.await_args.args[0]
    source = await state.dao.get_run(receipt["run_id"])
    assert source["status"] == "budget_paused" and not state.calls
    state.selected.default_model = "unrelated-global"
    resume = Context(state.server)
    await state.server._handlers["run.resume"](
        {"run_id": source["id"], "idempotency_key": "resume-skill", "budget_tokens": 100000}, resume
    )
    resumed = resume.reply.await_args.args[0]["run"]
    service = get_run_service(state.server)
    await service.wait(service.get_job(resumed["id"]))
    result = await state.dao.result_payload(resumed["id"])
    assert result["skill_id"] == state.skill.skill_id and result["output"] == "answer"
    assert resumed["metadata"]["budget_family_id"] == source["metadata"]["budget_family_id"]
    assert "Saved skill instructions" in state.calls[0]["messages"][0]["content"]
    # A second interrupted profile cannot silently become unrestricted chat.
    await state.dao.update_run_status(resumed["id"], status="interrupted")
    state.skill.version = "2"
    refused = Context(state.server)
    await state.server._handlers["run.resume"](
        {"run_id": resumed["id"], "idempotency_key": "resume-changed", "reviewed": True}, refused
    )
    refused.reply.assert_not_awaited()
    assert "start a new task" in str(refused.reply_error.await_args)
    assert len(state.calls) == 1


async def test_skill_child_cleanup_retains_client_gater_and_lease_through_double_cancel(
    skill_env, monkeypatch
):
    import sys

    from minimax_code import execution
    from minimax_code.orchestrator.subagent import register_background_run
    from minimax_code.run_ownership import owned_runtime

    state = skill_env
    entered, release, cleaned = asyncio.Event(), asyncio.Event(), asyncio.Event()
    children = []
    processes = []
    cleanup_count = 0
    original = execution.terminate_tree

    async def termination(proc):
        nonlocal cleanup_count
        # Register a real grandchild after the owner has already begun cancelling.
        # It must join that same retained drain and receive cancellation once.
        if len(processes) == 1:
            started = asyncio.Event()

            async def late_child():
                late = await execution.spawn_exec(
                    sys.executable, "-c", "import time; time.sleep(60)"
                )
                processes.append(late)
                started.set()
                await execution.communicate_bounded(late, timeout=120)

            task = asyncio.create_task(late_child())
            children.append(task)
            await started.wait()
            register_background_run("late-grandchild", task)
        else:
            entered.set()
        await release.wait()
        await original(proc)
        cleanup_count += 1
        if cleanup_count == 2:
            cleaned.set()

    monkeypatch.setattr(execution, "terminate_tree", termination)

    async def tool_run(self, **kwargs):
        assert owned_runtime.get() is not None

        async def child():
            proc = await execution.spawn_exec(sys.executable, "-c", "import time; time.sleep(60)")
            processes.append(proc)
            await execution.communicate_bounded(proc, timeout=120)

        task = asyncio.create_task(child())
        register_background_run("owned-child", task)
        children.append(task)
        # Ensure real process ownership is established before root completes.
        await asyncio.sleep(0.1)
        return ToolResult(success=True, output="child started")

    monkeypatch.setattr(SkillTool, "run", tool_run)
    state.wire = lambda _: tool_response() if len(state.calls) == 1 else response()
    ctx = await invoke(state, admission_only=True, max_iterations=1)
    run = ctx.reply.await_args.args[0]["run"]
    service = get_run_service(state.server)
    job = service.get_job(run["id"])
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert job.result["text"]
        preliminary = await state.dao.result_payload(run["id"])
        assert preliminary["skill_id"] == state.skill.skill_id and preliminary["tool_calls"] == 1
        assert preliminary["output"] == preliminary["text"] and preliminary["status"] == "running"
        service.cancel_run(run["id"])
        await asyncio.sleep(0)
        job.task.cancel()
        await asyncio.sleep(0)
        assert (
            not job.task.done()
            and all(not task.done() for task in children)
            and not cleaned.is_set()
        )
        assert service.get_job(run["id"]) is job
        sdk = state.clients[0]._transport._client
        assert not sdk.is_closed()
        assert state.server._permission_gaters["one"]
    finally:
        release.set()
        await asyncio.gather(job.task, return_exceptions=True)
    assert cleaned.is_set() and all(task.done() for task in children)
    assert len(processes) == 2 and all(proc.returncode is not None for proc in processes)
    assert sdk.is_closed()
    receipt = await state.dao.result_payload(run["id"])
    assert receipt["cancelled"] and receipt["skill_id"] == state.skill.skill_id
    assert receipt["text"] and receipt["verification"]["requires_review"]
    assert receipt["tool_calls"] == 1 and len(receipt["verification"]["children"]) == 2


async def test_two_concurrent_skills_have_private_provider_surfaces_and_clients(skill_env):
    state = skill_env

    class OtherTool(SkillTool):
        name = "other_probe"

    class OtherProvider(Provider):
        def install(self, registry):
            registry.register(OtherTool())
            return {"other_probe"}

    other = Skill(
        name="other",
        description="",
        when_to_use="",
        body="Other instructions",
        path=state.root / "other",
        version="1",
        tools=["other_probe"],
    )
    state.runtime.registry._skills[other.skill_id] = other
    state.runtime.register_tool_provider(other, OtherProvider())
    state.release.clear()
    first = asyncio.create_task(invoke(state, request="first"))
    second = asyncio.create_task(
        invoke(state, skill_id=other.skill_id, session_id="two", request="second")
    )
    try:
        async with asyncio.timeout(5):
            while len(state.calls) != 2:
                await asyncio.sleep(0.01)
        assert len(state.clients) == 2 and state.clients[0] is not state.clients[1]
        for call in state.calls:
            names = {item["function"]["name"] for item in call["tools"]}
            if call["messages"][-1]["content"] == "first":
                assert "skill_probe" in names and "other_probe" not in names
            else:
                assert "other_probe" in names and "skill_probe" not in names
            assert "read_file" in names
    finally:
        state.release.set()
    replies = await asyncio.gather(first, second)
    families = [
        (await state.dao.get_run(ctx.reply.await_args.args[0]["run_id"]))["metadata"][
            "budget_family_id"
        ]
        for ctx in replies
    ]
    assert len(set(families)) == 2
    assert not state.runtime._default_tool_registry.has("skill_probe")
    assert not state.runtime._default_tool_registry.has("other_probe")


def sdk_tool(name, args):
    import httpx

    frame = {
        "id": "tool-response",
        "object": "chat.completion.chunk",
        "created": 0,
        "model": "wire",
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "tc-" + name,
                            "type": "function",
                            "function": {"name": name, "arguments": json.dumps(args)},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ],
    }
    ending = {**frame, "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]}
    usage = {
        **frame,
        "choices": [],
        "usage": {"prompt_tokens": 9, "completion_tokens": 11, "total_tokens": 20},
    }
    return httpx.Response(
        200,
        headers={"content-type": "text/event-stream"},
        content="".join(f"data: {json.dumps(item)}\n\n" for item in (frame, ending, usage))
        + "data: [DONE]\n\n",
    )


async def test_actual_recursive_background_subagents_share_family_and_are_drained(skill_env):
    from minimax_code.storage.dao.agents import AgentsDAO

    state = skill_env
    await AgentsDAO(state.db).create(id="worker", name="worker", model="wire")
    seen = set()

    def wire(request):
        body = json.loads(request.content)
        user = next(msg["content"] for msg in body["messages"] if msg["role"] == "user")
        if user not in seen and user in {"Inspect this workspace", "child"}:
            seen.add(user)
            return sdk_tool(
                "spawn_subagent",
                {
                    "agent_name": "worker",
                    "prompt": "child" if user == "Inspect this workspace" else "grandchild",
                    "wait": False,
                },
            )
        return response()

    state.wire = wire
    ctx = await invoke(state, budget_tokens=300000)
    ctx.reply_error.assert_not_awaited()
    result = ctx.reply.await_args.args[0]
    root = await state.dao.get_run(result["run_id"])
    attempts = await BudgetsDAO(state.db).attempts(root["metadata"]["budget_family_id"])
    assert len(attempts) == 5 and all(attempt["state"] == "settled" for attempt in attempts), [
        next(m["content"] for m in reversed(call["messages"]) if m["role"] == "user")
        for call in state.calls
    ]
    assert len({attempt["run_id"] for attempt in attempts}) == 3
    for attempt in attempts:
        owner = await state.dao.get_run(attempt["run_id"])
        assert owner is not None, attempt["run_id"]
        assert owner["metadata"]["budget_family_id"] == root["metadata"]["budget_family_id"]
        if owner["mode"] == "subagent":
            assert owner["session_id"] != root["session_id"]
            assert owner["metadata"]["prompt"] in {"child", "grandchild"}
    assert result["budget"]["known_tokens"] == 100
    assert len(result["verification"]["children"]) == 2
    children = await state.db.fetchall("SELECT status FROM agent_runs WHERE mode='subagent'")
    assert len(children) == 2 and all(row["status"] == "completed" for row in children)
    assert state.clients[0]._transport._client is None  # closed only after both descendants


async def test_actual_background_child_budget_failure_is_partial_evidence(skill_env):
    from minimax_code.storage.dao.agents import AgentsDAO

    state = skill_env
    await AgentsDAO(state.db).create(id="worker", name="worker", model="wire")
    first = True

    def wire(request):
        nonlocal first
        if first:
            first = False
            return sdk_tool(
                "spawn_subagent", {"agent_name": "worker", "prompt": "child", "wait": False}
            )
        return response()

    state.wire = wire
    ctx = await invoke(state, budget_tokens=55000)
    ctx.reply_error.assert_not_awaited()
    result = ctx.reply.await_args.args[0]
    children = result["verification"]["children"]
    assert len(children) == 1 and children[0]["requires_review"], result
    assert result["partial"]
    rows = await state.db.fetchall("SELECT status,metadata FROM agent_runs WHERE mode='subagent'")
    assert len(rows) == 1 and rows[0]["status"] == "budget_paused"
    assert json.loads(rows[0]["metadata"])["budget_exhausted"]


@pytest.mark.parametrize(
    "failure", ["session_error", "session_missing", "run_error", "run_missing"]
)
@pytest.mark.parametrize("wait", [False, True])
async def test_owned_child_admission_failure_prevents_child_sdk(
    skill_env, monkeypatch, failure, wait
):
    from minimax_code import app
    from minimax_code.storage.dao.agents import AgentsDAO

    state = skill_env
    await AgentsDAO(state.db).create(id="worker", name="worker", model="wire")
    original_session = SessionsDAO.create
    original_run = AgentRunsDAO.create_run

    async def create_session(self, **kwargs):
        if kwargs["id"] not in {"one", "two"} and failure == "session_error":
            raise OSError("child session admission refused")
        return await original_session(self, **kwargs)

    async def create_run(self, **kwargs):
        if kwargs.get("mode") == "subagent":
            if failure == "run_error":
                raise OSError("child run admission refused")
            if failure == "run_missing":
                return None
        return await original_run(self, **kwargs)

    monkeypatch.setattr(SessionsDAO, "create", create_session)
    monkeypatch.setattr(AgentRunsDAO, "create_run", create_run)

    def wire(request):
        if len(state.calls) == 1:
            if failure == "session_missing":
                monkeypatch.setattr(app, "get_sessions_dao", lambda: None)
            return sdk_tool(
                "spawn_subagent", {"agent_name": "worker", "prompt": "child", "wait": wait}
            )
        return response()

    state.wire = wire
    ctx = await invoke(state, budget_tokens=300000)
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.await_args.args[0]
    assert len(state.calls) == 2
    assert all(
        next(m["content"] for m in call["messages"] if m["role"] == "user")
        == "Inspect this workspace"
        for call in state.calls
    )
    assert receipt["partial"] and receipt["verification"]["failed_tool_calls"] == 1
    assert "durably admitted" in receipt["verification"]["failures"][0]["error"]
    run = await state.dao.get_run(receipt["run_id"])
    attempts = await BudgetsDAO(state.db).attempts(run["metadata"]["budget_family_id"])
    assert len(attempts) == 2 and {attempt["run_id"] for attempt in attempts} == {run["id"]}
    assert not await state.db.fetchall("SELECT id FROM agent_runs WHERE mode='subagent'")


@pytest.mark.parametrize("failure", ["error", "missing"])
@pytest.mark.parametrize("wait", [False, True])
async def test_owned_child_terminal_persistence_failure_is_not_success_evidence(
    skill_env, monkeypatch, failure, wait
):
    from minimax_code.storage.dao.agents import AgentsDAO

    state = skill_env
    await AgentsDAO(state.db).create(id="worker", name="worker", model="wire")
    update = AgentRunsDAO.update_run_status

    async def refuse_terminal(self, run_id, **kwargs):
        row = await self.get_run(run_id)
        if row and row["mode"] == "subagent" and kwargs["status"] == "completed":
            if failure == "missing":
                return None
            raise OSError("child terminal storage refused")
        return await update(self, run_id, **kwargs)

    monkeypatch.setattr(AgentRunsDAO, "update_run_status", refuse_terminal)
    state.wire = lambda _: (
        sdk_tool("spawn_subagent", {"agent_name": "worker", "prompt": "child", "wait": wait})
        if len(state.calls) == 1
        else response()
    )
    ctx = await invoke(state, budget_tokens=300000)
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.await_args.args[0]
    assert len(state.calls) == 3
    assert receipt["partial"] and receipt["verification"]["requires_review"]
    if wait:
        assert receipt["verification"]["failed_tool_calls"] == 1
        assert (
            "completion could not be persisted" in receipt["verification"]["failures"][0]["error"]
        )
        child_row = (await state.dao.list_runs(mode="subagent"))[0]
    else:
        child = receipt["verification"]["children"][0]
        assert child["requires_review"] and "completion could not be persisted" in child["reason"]
        child_row = await state.dao.get_run(child["run_id"])
    assert child_row["status"] == "running"  # no fabricated durable terminal evidence
    assert await state.dao.result_payload(child_row["id"]) is None
    assert await state.dao.result_payload(receipt["run_id"]) == receipt


@pytest.mark.parametrize("preliminary", [False, True])
@pytest.mark.parametrize("malformed", [False, True])
async def test_orphan_skill_receipt_projection_and_replay_without_live_resolution(
    skill_env, monkeypatch, preliminary, malformed
):
    from minimax_code import app

    state = skill_env
    completed = await invoke(state)
    original = await state.dao.get_run(completed.reply.await_args.args[0]["run_id"])
    profile = (
        {"skill_id": state.skill.skill_id} if malformed else original["metadata"]["skill_execution"]
    )
    from minimax_code.skill_execution import canonical_request

    request = canonical_request(
        {
            "skill_id": state.skill.skill_id,
            "request": "Inspect this workspace",
            "session_id": "one",
            "budget_tokens": 100000,
        }
    )
    row, _ = await state.dao.admit_run(
        session_id="one",
        request=request,
        title="orphan",
        request_key="orphan",
        metadata={"skill_execution": profile},
    )
    saved = {
        **completed.reply.await_args.args[0],
        "run_id": row["id"],
        "text": "saved partial text",
        "output": "saved partial text",
        "usage": {"total_tokens": 20},
    }
    await state.dao.update_run_status(
        row["id"], status="running", **({"result": saved} if preliminary else {})
    )
    monkeypatch.setattr(
        app, "init_runtime", AsyncMock(side_effect=AssertionError("Recovery resolved live skill"))
    )
    count = len(state.calls)
    await state.dao.recover_orphans()
    snapshot = await state.dao.result_payload(row["id"])
    assert snapshot["status"] == snapshot["run_status"] == "interrupted"
    assert snapshot["partial"] and snapshot["skill_id"] == state.skill.skill_id
    assert snapshot["output"] == ("saved partial text" if preliminary else "")
    if preliminary:
        assert snapshot["usage"] == {"total_tokens": 20}
    replay = await invoke(state, idempotency_key="orphan")
    if malformed:
        replay.reply_error.assert_awaited_once_with(
            snapshot["error"]["code"], snapshot["error"]["message"], None
        )
    else:
        replay.reply.assert_awaited_once_with(snapshot)
    assert len(state.calls) == count


@pytest.mark.parametrize("where", ["admission", "receipt"])
async def test_storage_refusal_never_returns_unpersisted_success(skill_env, monkeypatch, where):
    state = skill_env
    if where == "admission":

        async def refusal(*args, **kwargs):
            raise OSError("admission storage refused")

        monkeypatch.setattr(AgentRunsDAO, "admit_run", refusal)
    else:
        original = AgentRunsDAO.update_run_status

        async def refusal(self, run_id, **kwargs):
            if kwargs.get("result", {}).get("skill_id") and kwargs["status"] == "running":
                raise OSError("receipt storage refused")
            return await original(self, run_id, **kwargs)

        monkeypatch.setattr(AgentRunsDAO, "update_run_status", refusal)
    ctx = await invoke(state)
    ctx.reply.assert_not_awaited()
    ctx.reply_error.assert_awaited_once()
    if where == "admission":
        assert not state.calls and not await state.dao.list_runs(session_id="one")
    else:
        run = (await state.dao.list_runs(session_id="one"))[0]
        receipt = await state.dao.result_payload(run["id"])
        assert run["status"] == receipt["status"] == "failed"
        assert receipt["partial"] and receipt["skill_id"] == state.skill.skill_id
        attempts = await BudgetsDAO(state.db).attempts(run["metadata"]["budget_family_id"])
        assert attempts[0]["state"] == "settled" and attempts[0]["known_tokens"] == 20
    assert all(llm._transport._client is None for llm in state.clients)


async def test_restart_retains_skill_profile_unknown_liability_and_reviewed_resume(
    skill_env, monkeypatch
):
    from minimax_code import app
    from minimax_code.ipc.handlers_budget import register_budget_handlers

    state = skill_env
    state.release.clear()
    ctx = await invoke(state, admission_only=True)
    source = ctx.reply.await_args.args[0]["run"]
    await asyncio.wait_for(state.entered.wait(), 3)
    service = get_run_service(state.server)
    job = service.get_job(source["id"])
    service.cancel_run(source["id"])
    await asyncio.gather(job.task, return_exceptions=True)
    attempts = await BudgetsDAO(state.db).attempts(source["metadata"]["budget_family_id"])
    assert attempts[0]["state"] == "unknown"
    # Simulate an interrupted persisted owner after an abrupt service death.
    await state.dao.update_run_status(source["id"], status="running")
    dbpath = state.root / "skills.db"
    await state.db.close()
    reopened = AsyncDatabase(dbpath)
    await reopened.connect()
    monkeypatch.setattr(app, "get_db", lambda: reopened)
    monkeypatch.setattr(app, "get_sessions_dao", lambda: SessionsDAO(reopened))
    state.db, state.dao = reopened, AgentRunsDAO(reopened)
    try:
        await state.dao.recover_orphans()
        persisted = await state.dao.get_run(source["id"])
        result = await state.dao.result_payload(source["id"])
        assert persisted["status"] == result["status"] == result["run_status"] == "interrupted"
        assert result["skill_id"] == state.skill.skill_id and result["partial"]
        assert persisted["metadata"]["skill_execution"] == source["metadata"]["skill_execution"]
        state.release.set()
        unreviewed = Context(state.server)
        await state.server._handlers["run.resume"](
            {"run_id": source["id"], "idempotency_key": "unreviewed"}, unreviewed
        )
        assert "Review" in str(unreviewed.reply_error.await_args)
        held = Context(state.server)
        await state.server._handlers["run.resume"](
            {"run_id": source["id"], "idempotency_key": "reviewed-held", "reviewed": True}, held
        )
        paused = held.reply.await_args.args[0]["run"]
        await service.wait(service.get_job(paused["id"]))
        assert (await state.dao.get_run(paused["id"]))["status"] == "budget_paused"
        assert len(state.calls) == 1
        register_budget_handlers(state.server)
        reconcile = Context(state.server)
        await state.server._handlers["budget.reconcile"](
            {
                "target": "attempt",
                "target_id": attempts[0]["id"],
                "liability_tokens": attempts[0]["estimated_tokens"],
                "reason": "Offline explicit accounting authorization",
                "acknowledged": True,
            },
            reconcile,
        )
        reconcile.reply_error.assert_not_awaited()
        resumed = Context(state.server)
        await state.server._handlers["run.resume"](
            {
                "run_id": paused["id"],
                "idempotency_key": "reconciled-skill",
                "budget_tokens": 300000,
                "reviewed": True,
            },
            resumed,
        )
        run = resumed.reply.await_args.args[0]["run"]
        await service.wait(service.get_job(run["id"]))
        receipt = await state.dao.result_payload(run["id"])
        assert receipt["skill_id"] == state.skill.skill_id and receipt["status"] == "completed"
        assert run["metadata"]["budget_family_id"] == source["metadata"]["budget_family_id"]
        assert len(state.calls) == 2
    finally:
        await reopened.close()


async def test_owned_child_failure_with_failed_failure_write_remains_review_evidence(
    skill_env, monkeypatch
):
    from minimax_code.orchestrator.subagent import SubAgentRuntime
    from minimax_code.storage.dao.agents import AgentsDAO

    state = skill_env
    await AgentsDAO(state.db).create(id="worker", name="worker", model="wire")
    update = AgentRunsDAO.update_run_status

    async def refuse_failure(self, run_id, **kwargs):
        row = await self.get_run(run_id)
        if row and row["mode"] == "subagent" and kwargs["status"] == "failed":
            raise OSError("failed child evidence write also refused")
        return await update(self, run_id, **kwargs)

    monkeypatch.setattr(AgentRunsDAO, "update_run_status", refuse_failure)
    monkeypatch.setattr(
        SubAgentRuntime, "invoke", AsyncMock(side_effect=RuntimeError("child runtime failed"))
    )

    def wire(request):
        if len(state.calls) == 1:
            return sdk_tool(
                "spawn_subagent", {"agent_name": "worker", "prompt": "child", "wait": False}
            )
        return response()

    state.wire = wire
    ctx = await invoke(state, budget_tokens=300000)
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.await_args.args[0]
    assert receipt["partial"] and receipt["verification"]["requires_review"]
    child = receipt["verification"]["children"][0]
    assert child["requires_review"] and "child runtime failed" in child["reason"]
    assert (await state.dao.get_run(child["run_id"]))["status"] == "running"
    assert len(state.calls) == 2  # no further child send/tool after failure


@pytest.mark.parametrize("failure", ["missing", "session", "family", "handle"])
async def test_owned_child_launch_rechecks_persisted_owner_before_sdk(
    skill_env, monkeypatch, failure
):
    from minimax_code.budget_ledger import budget_scope
    from minimax_code.orchestrator.subagent import SubAgentRuntime
    from minimax_code.storage.dao.agents import AgentsDAO
    from minimax_code.workspace import child
    from minimax_code.workspace_ctx import current_run_id

    state = skill_env
    await AgentsDAO(state.db).create(id="worker", name="worker", model="wire")
    prepare = child.prepare_child

    async def prepare_then_lose_owner(*args, **kwargs):
        workspace = await prepare(*args, **kwargs)
        owner_id = current_run_id()
        if failure == "missing":
            await state.db.execute("DELETE FROM agent_runs WHERE id=?", (owner_id,))
        elif failure == "session":
            await state.db.execute("UPDATE agent_runs SET session_id='two' WHERE id=?", (owner_id,))
        elif failure == "family":
            row = await state.dao.get_run(owner_id)
            await state.dao.update_run_status(
                owner_id,
                status="running",
                metadata={**row["metadata"], "budget_family_id": "other-family"},
            )
        return workspace

    monkeypatch.setattr(child, "prepare_child", prepare_then_lose_owner)
    original_invoke = SubAgentRuntime.invoke

    async def invoke_without_handle(self, *args, **kwargs):
        if failure == "handle":
            with budget_scope(None):
                return await original_invoke(self, *args, **kwargs)
        return await original_invoke(self, *args, **kwargs)

    monkeypatch.setattr(SubAgentRuntime, "invoke", invoke_without_handle)
    state.wire = lambda _: (
        sdk_tool("spawn_subagent", {"agent_name": "worker", "prompt": "child", "wait": False})
        if len(state.calls) == 1
        else response()
    )
    ctx = await invoke(state, budget_tokens=300000)
    receipt = ctx.reply.await_args.args[0]
    assert len(state.calls) == 2
    assert receipt["partial"] and receipt["verification"]["children"][0]["requires_review"]
    root = await state.dao.get_run(receipt["run_id"])
    attempts = await BudgetsDAO(state.db).attempts(root["metadata"]["budget_family_id"])
    assert {attempt["run_id"] for attempt in attempts} == {root["id"]}


async def test_restore_rechecks_provider_row_used_to_build_private_configuration(
    skill_env, monkeypatch
):
    from minimax_code.skill_execution import SkillExecutionSpec

    state = skill_env
    completed = await invoke(state)
    row = await state.dao.get_run(completed.reply.await_args.args[0]["run_id"])
    profile = row["metadata"]["skill_execution"]
    get = ProviderDAO.get
    calls = 0

    async def changing(self, provider_id):
        nonlocal calls
        calls += 1
        provider = await get(self, provider_id)
        return {**provider, "base_url": "https://changed.invalid/v1"} if calls == 2 else provider

    monkeypatch.setattr(ProviderDAO, "get", changing)
    with pytest.raises(ValueError, match="changed during restoration"):
        await SkillExecutionSpec.restore(profile, state.runtime, state.db)
    assert len(state.clients) == len(state.calls) == 1
    assert "https://" not in json.dumps(profile)
