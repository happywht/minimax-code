"""Agent skill bindings run in the admitted Native core, entirely offline."""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_agent_execution import agent_env as agent_env
from test_agent_execution import invoke, prepare_gate, tool_response
from test_budget_transport import response
from test_skill_execution import Provider, SkillTool
from test_skill_execution import skill_env as skill_env

from minimax_code.agent.skills.loader import Skill
from minimax_code.agent.skills.runtime import SkillToolProvider
from minimax_code.agent.tools import get_default_registry
from minimax_code.agent.tools.base import ToolResult
from minimax_code.agent_execution import AgentExecutionSpec
from minimax_code.runs import get_run_service
from minimax_code.skill_execution import _digest


class OtherTool(SkillTool):
    name = "other_probe"


class OtherProvider(SkillToolProvider):
    def install(self, registry):
        registry.register(OtherTool())
        return {"other_probe"}


async def add_other(state, *, name="other", provider=None, tools=None):
    skill = Skill(
        name=name,
        description="",
        when_to_use="",
        body="Second skill text",
        path=state.root / f"other-{name}",
        version="2",
        tools=["other_probe"] if tools is None else tools,
    )
    await state.runtime.registry.register(skill)
    state.runtime.register_tool_provider(skill, provider or OtherProvider())
    return skill


async def configure(state, skills=None, allowlist=None):
    row = await state.agents.upsert(
        name="worker",
        system_prompt="Agent first",
        skills=[state.skill.skill_id] if skills is None else skills,
        tool_allowlist=allowlist,
        max_iterations=6,
    )
    if allowlist is None:
        async with state.db.transaction() as conn:
            await conn.execute("UPDATE agents SET tool_allowlist=NULL WHERE id=?", (row["id"],))
    return await state.agents.get("worker")


async def test_two_skills_order_actual_provider_tools_and_durable_replay(agent_env):
    state = agent_env
    await state.runtime.registry.register(replace(state.skill, when_to_use="USE-ONLY-SENTINEL"))
    other = await add_other(state)
    await configure(
        state, [state.skill.name, other.skill_id], ["read_file", "skill_probe", "other_probe"]
    )
    state.wire = lambda _: tool_response("other_probe", {}) if len(state.calls) == 1 else response()
    ctx = await invoke(state)
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.await_args.args[0]
    row = await state.dao.get_run(receipt["run_id"])
    saved = row["metadata"]["agent_execution"]
    assert saved["version"] == 2 and saved["skill_bindings"]["version"] == 1
    assert [s["id"] for s in saved["skill_bindings"]["skills"]] == [
        state.skill.skill_id,
        other.skill_id,
    ]
    prompt = state.calls[0]["messages"][0]["content"]
    assert "USE-ONLY-SENTINEL" in prompt
    assert (
        prompt.index("Agent first")
        < prompt.index("Saved skill instructions")
        < prompt.index("Second skill text")
    )
    assert {t["function"]["name"] for t in state.calls[0]["tools"]} == {
        "read_file",
        "skill_probe",
        "other_probe",
    }
    assert any(
        m["role"] == "tool" and "private" in m["content"] for m in state.calls[1]["messages"]
    )
    assert len(state.clients) == 1 and receipt == await state.dao.result_payload(row["id"])
    assert "comments" not in receipt and "https://" not in json.dumps(saved["skill_bindings"])
    await state.runtime.registry.unregister(state.skill.skill_id)
    await state.agents.delete("worker")
    assert (await invoke(state)).reply.await_args.args[0] == receipt and len(state.calls) == 2


@pytest.mark.parametrize(
    "case",
    [
        "unknown",
        "ambiguous",
        "duplicate",
        "canonical_duplicate",
        "disabled",
        "empty",
        "unknown_tool",
        "excluded",
        "baseline_collision",
        "provider_collision",
        "reserved",
    ],
)
async def test_invalid_bindings_refuse_before_admission_or_sdk(agent_env, case):
    state = agent_env
    skills, allow = [state.skill.skill_id], None
    if case == "unknown":
        skills = ["missing"]
    elif case == "ambiguous":
        await add_other(state, name=state.skill.name)
        skills = [state.skill.name]
    elif case == "duplicate":
        skills *= 2
    elif case == "canonical_duplicate":
        skills.append(state.skill.name)
    elif case == "disabled":
        await state.runtime.registry.register(replace(state.skill, enabled=False))
    elif case == "empty":
        skills = [" "]
    elif case == "unknown_tool":
        allow = ["skill_probe", "typo"]
    elif case == "excluded":
        allow = []
    else:

        class CollisionTool(SkillTool):
            name = (
                "read_file"
                if case == "baseline_collision"
                else "report_completion"
                if case == "reserved"
                else "skill_probe"
            )

        class CollisionProvider(SkillToolProvider):
            def install(self, registry):
                registry.register(CollisionTool())
                return {CollisionTool.name}

        other = await add_other(state, provider=CollisionProvider(), tools=[CollisionTool.name])
        skills.append(other.skill_id)
    await configure(state, skills, allow)
    ctx = await invoke(state)
    assert ctx.reply_error.await_count == 1 and not state.calls
    assert not await state.dao.list_runs() and not (state.source / ".minimax").exists()


async def test_exact_id_beats_ambiguous_short_name_and_provider_extra_exports(agent_env):
    state = agent_env
    await add_other(state, name=state.skill.name)

    class ExtraProvider(Provider):
        def install(self, registry):
            return super().install(registry) | OtherProvider().install(registry)

    state.runtime.register_tool_provider(state.skill, ExtraProvider())
    await configure(state)
    receipt = (await invoke(state)).reply.await_args.args[0]
    saved = (await state.dao.get_run(receipt["run_id"]))["metadata"]["agent_execution"]
    assert {"skill_probe", "other_probe", "read_file"} <= set(saved["allowlist"])
    assert saved["skill_bindings"]["skills"][0]["provider_tools"] == ["skill_probe", "other_probe"]


async def test_private_providers_no_mutable_cross_run_leak(agent_env):
    state = agent_env

    class CounterTool(SkillTool):
        def __init__(self):
            self.count = 0

        async def run(self, **kwargs):
            self.count += 1
            return ToolResult(success=True, output=str(self.count))

    class CounterProvider(Provider):
        def __init__(self):
            self.tool = CounterTool()

        def install(self, registry):
            registry.register(self.tool)
            return {self.tool.name}

    provider = CounterProvider()
    state.runtime.register_tool_provider(state.skill, provider)
    await configure(state, allowlist=["skill_probe"])
    seen = set()

    def wire(request):
        body = json.loads(request.content)
        user = next(m["content"] for m in body["messages"] if m["role"] == "user")
        if user not in seen:
            seen.add(user)
            return tool_response("skill_probe", {})
        return response()

    state.wire = wire
    import asyncio

    replies = await asyncio.gather(
        invoke(state, request="first", idempotency_key="first"),
        invoke(state, request="second", idempotency_key="second"),
    )
    assert all(r.reply.await_args.args[0]["status"] == "completed" for r in replies)
    values = [m["content"] for c in state.calls for m in c["messages"] if m["role"] == "tool"]
    assert [json.loads(v)["output"] for v in values] == ["1", "1"] and provider.tool.count == 0
    assert not get_default_registry().has("skill_probe")


@pytest.mark.parametrize(
    "change", ["body", "disabled", "version", "manifest", "provider", "dependencies", "allowlist"]
)
async def test_queued_dependency_validation_and_frozen_body(agent_env, monkeypatch, change):
    state = agent_env
    await configure(state, allowlist=["skill_probe", "read_file"])
    entered, release = await prepare_gate(monkeypatch)
    admitted = (await invoke(state, admission_only=True)).reply.await_args.args[0]["run"]
    await entered.wait()
    if change == "body":
        await state.runtime.registry.register(replace(state.skill, body="New body"))
    elif change == "disabled":
        await state.runtime.registry.register(replace(state.skill, enabled=False))
    elif change == "version":
        await state.runtime.registry.register(replace(state.skill, version="new"))
    elif change == "manifest":
        await state.runtime.registry.register(replace(state.skill, tools=[]))
    elif change == "provider":
        state.runtime.register_tool_provider(state.skill, OtherProvider())
    elif change == "dependencies":
        await configure(state, [])
    else:
        await configure(state, allowlist=["skill_probe"])
    job = get_run_service(state.server).get_job(admitted["id"])
    release.set()
    import asyncio

    result = await asyncio.gather(job.task, return_exceptions=True)
    if change == "body":
        assert not isinstance(result[0], BaseException)
        assert "Saved skill instructions" in state.calls[0]["messages"][0]["content"]
        assert "New body" not in state.calls[0]["messages"][0]["content"]
    else:
        assert isinstance(result[0], ValueError) and not state.calls
        assert (await state.dao.get_run(admitted["id"]))["status"] == "failed"


async def test_profile_versions_are_explicit_and_corruption_fails_closed(agent_env):
    state = agent_env
    await configure(state, [])
    receipt = (await invoke(state)).reply.await_args.args[0]
    data = (await state.dao.get_run(receipt["run_id"]))["metadata"]["agent_execution"]
    AgentExecutionSpec(data)
    for mutate in (
        lambda d: d.pop("skill_bindings"),
        lambda d: d["skill_bindings"].pop("skills"),
        lambda d: d["skill_bindings"].update(version=99),
    ):
        broken = copy.deepcopy(data)
        mutate(broken)
        broken["digest"] = _digest({k: v for k, v in broken.items() if k != "digest"})
        with pytest.raises(ValueError):
            AgentExecutionSpec(broken)
    legacy = {
        k: v for k, v in data.items() if k not in {"agent_instructions", "skill_bindings", "digest"}
    }
    legacy["version"] = 1
    legacy["digest"] = _digest(legacy)
    spec, _ = await AgentExecutionSpec.restore(legacy, state.runtime, state.db)
    assert spec.data["version"] == 1
    await configure(state)
    with pytest.raises(ValueError, match="Legacy"):
        await spec.validate(state.runtime, state.db)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("manifest_tools", [{}]),
        ("provider_tools", [1]),
        ("provider_digest", None),
        ("tool_identity", None),
        ("version", 2),
    ],
)
async def test_damaged_binding_fields_refuse_even_with_matching_digests(agent_env, field, value):
    state = agent_env
    await configure(state)
    receipt = (await invoke(state)).reply.await_args.args[0]
    data = copy.deepcopy(
        (await state.dao.get_run(receipt["run_id"]))["metadata"]["agent_execution"]
    )
    bindings = data["skill_bindings"]
    bindings["skills"][0][field] = value
    bindings["digest"] = _digest({k: v for k, v in bindings.items() if k != "digest"})
    data["digest"] = _digest({k: v for k, v in data.items() if k != "digest"})
    with pytest.raises(ValueError, match="damaged"):
        AgentExecutionSpec(data)


@pytest.fixture
def short_source():
    import shutil
    import tempfile

    source = Path(tempfile.mkdtemp(prefix="as-"))
    try:
        yield source
    finally:
        assert source.resolve().parent == Path(
            tempfile.gettempdir()
        ).resolve() and source.name.startswith("as-")
        shutil.rmtree(source)


async def test_recursive_children_keep_bindings_cap_and_own_receipt_channels(
    agent_env, short_source
):
    state = agent_env
    # Real nested receipt files also need room under Windows MAX_PATH.
    # pytest's descriptive test directory is deliberately not the source.
    source = short_source
    (source / "hello.txt").write_text("source", encoding="utf-8")
    await state.sessions.update("one", workspace_path=str(source))
    other = await add_other(state)
    await configure(
        state,
        [state.skill.skill_id, other.skill_id],
        ["spawn_subagent", "skill_probe", "other_probe"],
    )
    stages = {}

    def wire(request):
        body = json.loads(request.content)
        user = next(m["content"] for m in body["messages"] if m["role"] == "user")
        phase = stages.get(user, 0)
        stages[user] = phase + 1
        if phase == 0 and user in {"Inspect hello.txt", "child"}:
            return tool_response(
                "spawn_subagent",
                {
                    "agent_name": "worker",
                    "prompt": "child" if user == "Inspect hello.txt" else "grandchild",
                    "wait": False,
                },
            )
        if user != "Inspect hello.txt" and phase == (1 if user == "child" else 0):
            return tool_response(
                "report_completion", {"status": "completed", "summary": f"own {user}"}
            )
        return response()

    state.wire = wire
    result = (await invoke(state, budget_tokens=600000)).reply.await_args.args[0]
    attempts = await state.db.fetchall("SELECT * FROM budget_attempts")
    assert len(attempts) == 7 and len({a["run_id"] for a in attempts}) == 3
    root = await state.dao.get_run(result["run_id"])
    family = root["metadata"]["budget_family_id"]
    children = [r for r in await state.dao.list_runs() if r["mode"] == "subagent"]
    assert len(children) == 2, result
    for run in children:
        profile = run["metadata"]["agent_skill_profile"]
        assert [b["id"] for b in profile["skill_bindings"]["skills"]] == [
            state.skill.skill_id,
            other.skill_id,
        ]
        assert set(profile["allowlist"]) == {
            "spawn_subagent",
            "skill_probe",
            "other_probe",
            "report_completion",
            "report_progress",
        }
        assert run["metadata"]["budget_family_id"] == family and run["status"] == "completed"
        assert any(a["run_id"] == run["id"] and a["family_id"] == family for a in attempts)
        payload = run["metadata"]
        # Tool outputs contain the server-bound durable identity, never the
        # ancestor receipt's target. Reports are read from actual disk below.
        reports = list(source.rglob("REPORT.json"))
        assert len(reports) == 2, payload
        report = next(
            json.loads(p.read_text(encoding="utf-8"))
            for p in reports
            if json.loads(p.read_text(encoding="utf-8"))["run_id"] == run["id"]
        )
        assert report["summary"] == f"own {run['metadata']['prompt']}"
        assert payload["result_text"] == "answer"


@pytest.mark.parametrize("case", ["required_skill", "explicit_tool", "persistence"])
async def test_child_binding_rejection_prevents_child_sdk(agent_env, monkeypatch, case):
    from minimax_code.storage.dao.runs import AgentRunsDAO

    state = agent_env
    await configure(state, allowlist=["spawn_subagent", "skill_probe"])
    other = await add_other(state)
    skills = [other.skill_id] if case == "required_skill" else [state.skill.skill_id]
    allow = ["skill_probe", "read_file"] if case == "explicit_tool" else None
    await state.agents.upsert(
        name="child", system_prompt="Child", skills=skills, tool_allowlist=allow
    )
    if case == "persistence":
        original = AgentRunsDAO.create_run

        async def refuse(self, *args, **kwargs):
            if (kwargs.get("metadata") or {}).get("agent_skill_profile"):
                raise OSError("binding admission refused")
            return await original(self, *args, **kwargs)

        monkeypatch.setattr(AgentRunsDAO, "create_run", refuse)
    state.wire = lambda _: (
        tool_response("spawn_subagent", {"agent_name": "child", "prompt": "child", "wait": True})
        if len(state.calls) == 1
        else response()
    )
    receipt = (await invoke(state, budget_tokens=300000)).reply.await_args.args[0]
    assert (
        len(state.calls) == 2 and receipt["verification"]["requires_review"] and receipt["partial"]
    )
    assert not [r for r in await state.dao.list_runs() if r["mode"] == "subagent"]
    assert len(await state.db.fetchall("SELECT * FROM budget_attempts")) == 2


async def test_skill_profile_restart_online_backup_keeps_family_copy_unknown_hold(
    agent_env, monkeypatch
):
    from test_agent_execution import test_actual_restart_snapshot_preserves_pending_or_sent_run

    state = agent_env
    await configure(state, allowlist=["read_file", "skill_probe"])
    # Existing real online SQLite snapshot/reopen/recover/reconcile proof now
    # runs with a v2 binding, rather than manufacturing interrupted metadata.
    await test_actual_restart_snapshot_preserves_pending_or_sent_run(state, monkeypatch, True)


async def test_skills_continue_preserves_saved_body_family_and_copy(agent_env):
    from test_run_service import Context

    from minimax_code.ipc.builtins import handle_agent_continue_run

    state = agent_env
    await configure(state, allowlist=["skill_probe"])
    await state.agents.upsert(name="worker", system_prompt="Agent first", max_iterations=1)
    state.wire = lambda _: tool_response("skill_probe", {})
    first = (await invoke(state)).reply.await_args.args[0]
    old = await state.dao.get_run(first["run_id"])
    assert first["truncated"]
    await state.runtime.registry.register(replace(state.skill, body="Different current body"))
    await state.agents.upsert(name="worker", system_prompt="Different current agent")
    state.wire = lambda _: response()
    ctx = Context(state.server)
    await handle_agent_continue_run(
        {"session_id": first["session_id"], "run_id": first["run_id"]}, ctx
    )
    ctx.reply_error.assert_not_awaited()
    final = ctx.reply.await_args.args[0]
    new = await state.dao.get_run(final["run_id"])
    assert new["metadata"]["budget_family_id"] == old["metadata"]["budget_family_id"]
    assert new["metadata"]["agent_execution"] == old["metadata"]["agent_execution"]
    assert final["workspace"]["path"] == first["workspace"]["path"]
    prompt = state.calls[-1]["messages"][0]["content"]
    assert "Saved skill instructions" in prompt and "Different current body" not in prompt


async def test_permission_revocation_blocks_bound_provider_tool(agent_env, monkeypatch):
    state = agent_env
    await configure(state, allowlist=["skill_probe"])
    entered, release = await prepare_gate(monkeypatch)
    admitted = (await invoke(state, admission_only=True)).reply.await_args.args[0]["run"]
    await entered.wait()
    await state.permissions.upsert(tool_pattern="*", action="deny")
    called = []

    async def forbidden(self, **kwargs):
        called.append(True)
        return ToolResult(success=True, output="forbidden")

    monkeypatch.setattr(SkillTool, "run", forbidden)
    state.wire = lambda _: tool_response("skill_probe", {}) if len(state.calls) == 1 else response()
    release.set()
    await get_run_service(state.server).wait(get_run_service(state.server).get_job(admitted["id"]))
    result = await state.dao.result_payload(admitted["id"])
    assert not called and result["verification"]["requires_review"] and result["partial"]


async def test_bound_skill_os_children_double_cancel_and_late_registration(agent_env, monkeypatch):
    import asyncio
    import sys

    from minimax_code import execution
    from minimax_code.orchestrator.subagent import register_background_run

    state = agent_env
    await configure(state, allowlist=["skill_probe"])
    await state.agents.upsert(name="worker", system_prompt="Agent", max_iterations=1)
    entered, release, done = asyncio.Event(), asyncio.Event(), asyncio.Event()
    processes, tasks = [], []
    original = execution.terminate_tree
    completed = 0

    async def start():
        proc = await execution.spawn_exec(sys.executable, "-c", "import time; time.sleep(60)")
        processes.append(proc)
        await execution.communicate_bounded(proc, timeout=120)

    async def terminate(proc):
        nonlocal completed
        if len(processes) == 1:
            task = asyncio.create_task(start())
            tasks.append(task)
            while len(processes) < 2:
                await asyncio.sleep(0)
            register_background_run("late-bound-skill", task)
        else:
            entered.set()
        await release.wait()
        await original(proc)
        completed += 1
        if completed == 2:
            done.set()

    async def tool(self, **kwargs):
        task = asyncio.create_task(start())
        tasks.append(task)
        register_background_run("bound-skill", task)
        while not processes:
            await asyncio.sleep(0)
        return ToolResult(success=True, output="started")

    monkeypatch.setattr(execution, "terminate_tree", terminate)
    monkeypatch.setattr(SkillTool, "run", tool)
    state.wire = lambda _: tool_response("skill_probe", {})
    run = (await invoke(state, admission_only=True)).reply.await_args.args[0]["run"]
    service = get_run_service(state.server)
    job = service.get_job(run["id"])
    try:
        await asyncio.wait_for(entered.wait(), 8)
        preliminary = await state.dao.result_payload(run["id"])
        sdk = state.clients[0]._transport._client
        assert preliminary["status"] == "running"
        service.cancel_run(run["id"])
        job.task.cancel()
        await asyncio.sleep(0)
        assert not job.task.done() and not done.is_set() and not sdk.is_closed()
        assert (
            service.get_job(run["id"]) is job and state.server._permission_gaters[run["session_id"]]
        )
    finally:
        release.set()
        await asyncio.gather(job.task, return_exceptions=True)
    assert (
        done.is_set()
        and all(t.done() for t in tasks)
        and all(p.returncode is not None for p in processes)
    )
    assert sdk.is_closed() and (await state.dao.result_payload(run["id"]))["partial"]


@pytest.mark.parametrize(
    "change", ["body", "disabled", "version", "provider", "tools_revoked", "model_provider"]
)
async def test_admitted_child_revalidates_before_first_send(agent_env, monkeypatch, change):
    import asyncio

    from minimax_code.agent.tools import subagents
    from minimax_code.storage.dao.providers import ProviderDAO

    state = agent_env
    await configure(state, allowlist=["spawn_subagent", "skill_probe"])
    await state.agents.upsert(
        name="child",
        system_prompt="Child before skill",
        skills=[state.skill.name],
        tool_allowlist=["skill_probe"],
    )
    entered, release = asyncio.Event(), asyncio.Event()

    class Gate:
        async def acquire(self):
            entered.set()
            await release.wait()

        def release(self):
            pass

    monkeypatch.setattr(subagents, "_spawn_semaphore", lambda: Gate())
    state.wire = lambda _: (
        tool_response("spawn_subagent", {"agent_name": "child", "prompt": "child", "wait": True})
        if len(state.calls) == 1
        else response()
    )
    parent = asyncio.create_task(invoke(state, budget_tokens=300000))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        child = next(r for r in await state.dao.list_runs() if r["mode"] == "subagent")
        binding = child["metadata"]["agent_skill_profile"]["skill_bindings"]
        assert binding["skills"][0]["id"] == state.skill.skill_id
        if change == "body":
            await state.runtime.registry.register(
                replace(state.skill, body="Changed after child admission")
            )
        elif change == "disabled":
            await state.runtime.registry.register(replace(state.skill, enabled=False))
        elif change == "version":
            await state.runtime.registry.register(replace(state.skill, version="changed"))
        elif change == "provider":
            state.runtime.register_tool_provider(state.skill, OtherProvider())
        elif change == "tools_revoked":
            await state.agents.upsert(name="child", system_prompt="Child", tool_allowlist=[])
        else:
            await ProviderDAO(state.db).update(state.provider["id"], enabled=False)
    finally:
        release.set()
    receipt = (await parent).reply.await_args.args[0]
    calls = [
        c
        for c in state.calls
        if next(m["content"] for m in c["messages"] if m["role"] == "user") == "child"
    ]
    if change == "body":
        assert len(calls) == 1 and "Saved skill instructions" in calls[0]["messages"][0]["content"]
        assert "Changed after child admission" not in calls[0]["messages"][0]["content"]
    else:
        assert not calls and receipt["verification"]["requires_review"] and receipt["partial"]
        failed = await state.dao.get_run(child["id"])
        assert failed["status"] == "failed" and "UnboundLocalError" not in failed["error"]
        if change == "tools_revoked":
            assert "tool capability was revoked" in failed["error"]
        elif change == "model_provider":
            assert "model provider changed or was revoked" in failed["error"]


async def test_instruction_only_skills_and_explicit_empty_tools(agent_env):
    state = agent_env
    skill = Skill(
        name="instructions",
        description="",
        when_to_use="Only this condition",
        body="No provider necessary",
        path=state.root / "only-text",
        tools=[],
    )
    await state.runtime.registry.register(skill)
    await configure(state, [skill.skill_id], [])
    receipt = (await invoke(state)).reply.await_args.args[0]
    assert receipt["status"] == "completed" and not state.calls[0].get("tools")
    assert "Only this condition" in state.calls[0]["messages"][0]["content"]


@pytest.mark.parametrize("field", ["missing", "damaged"])
async def test_actual_resume_damaged_v2_never_falls_back_to_generic(agent_env, field):
    from test_run_service import Context

    state = agent_env
    await configure(state, allowlist=["skill_probe"])
    await state.agents.upsert(name="worker", system_prompt="Agent", max_iterations=1)
    state.wire = lambda _: tool_response("skill_probe", {})
    receipt = (await invoke(state)).reply.await_args.args[0]
    run = await state.dao.get_run(receipt["run_id"])
    metadata = copy.deepcopy(run["metadata"])
    saved = metadata["agent_execution"]
    if field == "missing":
        saved.pop("skill_bindings")
    else:
        saved["skill_bindings"]["skills"][0].pop("provider_digest")
    saved["digest"] = _digest({k: v for k, v in saved.items() if k != "digest"})
    await state.dao.update_run_status(run["id"], status="interrupted", metadata=metadata)
    before = len(state.calls)
    ctx = Context(state.server)
    await state.server._handlers["run.resume"](
        {"run_id": run["id"], "idempotency_key": "damaged", "reviewed": True}, ctx
    )
    assert ctx.reply_error.await_count == 1 and len(state.calls) == before
    assert len(await state.dao.list_runs()) == 1


@pytest.mark.parametrize("method", ["agent.create", "agent.update"])
@pytest.mark.parametrize("skills", [[""], [" "], [1], "skill", ["same", "same"]])
async def test_crud_rejects_invalid_dependency_declarations(agent_env, method, skills):
    from test_run_service import Context

    state = agent_env
    ctx = Context(state.server)
    await state.server._handlers[method](
        {"name": "worker", "system_prompt": "Agent", "skills": skills}, ctx
    )
    assert ctx.reply_error.await_count == 1 and not state.calls
    assert not (await state.agents.get("worker"))["skills"]


@pytest.mark.parametrize("change", ["disabled", "version", "provider"])
async def test_inherited_provider_tools_keep_revocation_provenance(agent_env, monkeypatch, change):
    import asyncio

    from minimax_code.agent.tools import subagents

    state = agent_env
    await configure(state, allowlist=["spawn_subagent", "skill_probe"])
    await state.agents.upsert(name="child", system_prompt="No declared skills", skills=[])
    entered, release = asyncio.Event(), asyncio.Event()

    class Gate:
        async def acquire(self):
            entered.set()
            await release.wait()

        def release(self):
            pass

    monkeypatch.setattr(subagents, "_spawn_semaphore", lambda: Gate())
    state.wire = lambda _: (
        tool_response("spawn_subagent", {"agent_name": "child", "prompt": "child", "wait": True})
        if len(state.calls) == 1
        else response()
    )
    parent = asyncio.create_task(invoke(state, budget_tokens=300000))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        child = next(r for r in await state.dao.list_runs() if r["mode"] == "subagent")
        profile = child["metadata"]["agent_skill_profile"]
        assert profile["skill_bindings"]["skills"] == []
        assert profile["inherited_tool_bindings"]["skills"][0]["id"] == state.skill.skill_id
        assert "skill_probe" in profile["allowlist"]
        assert "Saved skill instructions" not in profile["instructions"]
        if change == "disabled":
            await state.runtime.registry.register(replace(state.skill, enabled=False))
        elif change == "version":
            await state.runtime.registry.register(replace(state.skill, version="changed"))
        else:
            state.runtime.register_tool_provider(state.skill, OtherProvider())
    finally:
        release.set()
    receipt = (await parent).reply.await_args.args[0]
    assert (
        len(state.calls) == 2 and receipt["partial"] and receipt["verification"]["requires_review"]
    )
    assert (await state.dao.get_run(child["id"]))["status"] == "failed"


async def test_child_new_skill_cannot_alias_an_inherited_provider_name(agent_env):
    state = agent_env
    await configure(state, allowlist=["spawn_subagent", "skill_probe"])
    other = await add_other(state, provider=Provider(), tools=["skill_probe"])
    await state.agents.upsert(name="child", system_prompt="Child", skills=[other.skill_id])
    state.wire = lambda _: (
        tool_response("spawn_subagent", {"agent_name": "child", "prompt": "child", "wait": True})
        if len(state.calls) == 1
        else response()
    )
    receipt = (await invoke(state, budget_tokens=300000)).reply.await_args.args[0]
    assert (
        len(state.calls) == 2 and receipt["partial"] and receipt["verification"]["requires_review"]
    )
    assert not [r for r in await state.dao.list_runs() if r["mode"] == "subagent"]


async def test_none_allowlist_freezes_candidates_without_later_global_additions(
    agent_env, monkeypatch
):
    state = agent_env
    await configure(state)
    entered, release = await prepare_gate(monkeypatch)
    admitted = (await invoke(state, admission_only=True)).reply.await_args.args[0]["run"]
    await entered.wait()
    registry = get_default_registry()
    registry.register(OtherTool())
    try:
        release.set()
        await get_run_service(state.server).wait(
            get_run_service(state.server).get_job(admitted["id"])
        )
        names = {t["function"]["name"] for t in state.calls[0]["tools"]}
        assert "skill_probe" in names and "read_file" in names and "other_probe" not in names
    finally:
        release.set()
        registry.unregister("other_probe")


async def test_unselected_global_tool_does_not_enter_private_registry(agent_env):
    state = agent_env

    class UnselectedTool(SkillTool):
        name = "unselected_global"

        def __deepcopy__(self, memo):
            raise AssertionError("Unselected global service must not be copied")

    registry = get_default_registry()
    registry.register(UnselectedTool())
    try:
        await configure(state, allowlist=["skill_probe"])
        receipt = (await invoke(state)).reply.await_args.args[0]
        assert receipt["status"] == "completed"
        assert {t["function"]["name"] for t in state.calls[0]["tools"]} == {"skill_probe"}
    finally:
        registry.unregister("unselected_global")


@pytest.mark.parametrize("revoked", [False, True])
async def test_known_no_skills_v1_profile_executes_continue_without_upgrade(agent_env, revoked):
    from test_run_service import Context

    from minimax_code.ipc.builtins import handle_agent_continue_run

    state = agent_env
    await configure(state, [], ["read_file"])
    await state.agents.upsert(name="worker", system_prompt="Legacy", max_iterations=1)
    state.wire = lambda _: tool_response("read_file", {"path": "hello.txt"})
    first = (await invoke(state)).reply.await_args.args[0]
    old = await state.dao.get_run(first["run_id"])
    metadata = copy.deepcopy(old["metadata"])
    data = metadata["agent_execution"]
    data.pop("skill_bindings")
    data.pop("agent_instructions")
    data["version"] = 1
    data["digest"] = _digest({k: v for k, v in data.items() if k != "digest"})
    await state.dao.update_run_status(old["id"], status=old["status"], metadata=metadata)
    if revoked:
        await state.agents.upsert(name="worker", system_prompt="Changed", tool_allowlist=[])
    state.wire = lambda _: response()
    before = len(state.calls)
    ctx = Context(state.server)
    await handle_agent_continue_run(
        {"session_id": first["session_id"], "run_id": first["run_id"]}, ctx
    )
    if revoked:
        assert ctx.reply_error.await_count == 1 and len(state.calls) == before
        assert "revoked" in str(ctx.reply_error.await_args)
        return
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.await_args.args[0]
    new = await state.dao.get_run(receipt["run_id"])
    assert receipt["text"] == "answer" and new["metadata"]["agent_execution"]["version"] == 1
    assert new["metadata"]["budget_family_id"] == old["metadata"]["budget_family_id"]


async def test_real_code_review_binding_keeps_agent_model_budget_and_generic_result(agent_env):
    from minimax_code.agent.skills._builtin.code_review import Provider as ReviewProvider
    from minimax_code.agent.skills.loader import load_skill_file

    state = agent_env
    path = Path(__file__).resolve().parents[1] / "skills" / "code-review" / "SKILL.md"
    skill = load_skill_file(path)
    skill = replace(
        skill,
        extra={
            **skill.extra,
            "model": "unverified-skill-model",
            "max_iterations": 1,
            "budget_tokens": 1,
        },
    )
    await state.runtime.registry.register(skill)
    state.runtime.register_tool_provider(skill, ReviewProvider())
    await configure(state, [skill.skill_id], ["read_file", *skill.tools])
    receipt = (await invoke(state)).reply.await_args.args[0]
    saved = (await state.dao.get_run(receipt["run_id"]))["metadata"]["agent_execution"]
    assert state.calls[0]["model"] == "wire" and saved["max_iterations"] == 6
    assert set(skill.tools) <= {t["function"]["name"] for t in state.calls[0]["tools"]}
    assert receipt["text"] == "answer" and "comments" not in receipt and "stats" not in receipt
    assert receipt["budget"]["limit_tokens"] == 100000
