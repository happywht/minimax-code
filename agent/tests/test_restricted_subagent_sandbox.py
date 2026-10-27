"""Trusted restricted child copies never inherit legacy file overlays.

The controller and mount boundary are controlled offline seams. Real SQLite,
copy manifests, file tools and process owner admission remain in this path;
this suite does not enable the public restricted subagent tool class.
"""
from __future__ import annotations

import asyncio
import copy
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_process_ownership import IDENTITY, TARGET
from test_process_ownership import ownership as ownership
from test_process_supervisor import supervisor as supervisor

from minimax_code import app
from minimax_code.agent.core import AgentCore, AgentRunResult
from minimax_code.agent.tools.base import ToolRegistry
from minimax_code.agent.tools.file_ops import ReadFileTool, WriteFileTool
from minimax_code.agent.tools.sandbox import CollectSubagentTool
from minimax_code.agent.tools.subagents import (
    SANDBOX_PROTOCOL_PROMPT,
    SpawnSubagentTool,
    _drive_run,
)
from minimax_code.agent.tools.terminal import ExecCommandTool
from minimax_code.budget_ledger import BudgetHandle, budget_scope
from minimax_code.execution import ExecutionPolicy, execution_scope, spawn_exec
from minimax_code.orchestrator.subagent import SubAgentConfig, SubAgentRuntime
from minimax_code.process_children import owner_snapshot
from minimax_code.process_context import current_process_target, required_target_scope
from minimax_code.process_types import ProcessBoundaryError
from minimax_code.run_ownership import owned_runtime
from minimax_code.runs import RunService
from minimax_code.skill_bindings import SkillBindings
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.workspace.child import load_child
from minimax_code.workspace_ctx import (
    current_root,
    current_sandbox,
    reset_current_root,
    reset_current_run_id,
    reset_sandbox,
    set_current_root,
    set_current_run_id,
    set_sandbox,
)


@pytest.fixture
async def restricted_child(supervisor, monkeypatch, tmp_path):
    manager, controller, processes, owner = supervisor
    db = processes.db
    root = Path(owner.workspace)
    (root / "input.txt").write_text("parent", encoding="utf-8")
    overlay = tmp_path / "inherited-overlay"
    overlay.mkdir()
    registry = ToolRegistry()
    for tool in (ReadFileTool(), WriteFileTool(), ExecCommandTool()):
        registry.register(tool)
    agents = AgentDAO(db)
    row = await agents.upsert(name="copy-worker", tool_allowlist=list(registry.names()),
        execution_profile_id="linux-podman-l1")
    state = SimpleNamespace(db=db, manager=manager, controller=controller, root=root, overlay=overlay,
        owner=owner, agents=agents, row=row, handles=[], entered=asyncio.Event(), behavior=None, revoked=False)

    class Catalog:
        supervisor = manager
        installation_id = IDENTITY.install_id
        data_dir = db.path.parent

        def resolve(self, profile, *, parent_target):
            if state.revoked or profile not in (None, "linux-podman-l1") or parent_target != TARGET.to_dict():
                raise ProcessBoundaryError("Test profile was revoked")
            return TARGET.to_dict()

        def assert_compatible(self, target, installation_id):
            if state.revoked or target != TARGET.to_dict() or installation_id != self.installation_id:
                raise ProcessBoundaryError("Test profile or installation was revoked")

        def assert_extension_boundary(self):
            return None

    catalog = Catalog()
    monkeypatch.setattr(app, "get_db", lambda: db)
    monkeypatch.setattr(app, "get_execution_profiles", lambda: catalog)
    monkeypatch.setattr(app, "get_sessions_dao", lambda: SessionsDAO(db))

    async def initialized():
        return None

    monkeypatch.setattr(app, "init_runtime", initialized)
    # Windows has no Linux mount table. Mount hardening has its own tests;
    # this seam only admits the actual test directories outside service data.
    def workspace_boundary(path):
        assert Path(path).is_dir() and not Path(path).is_relative_to(db.path.parent)

    monkeypatch.setattr("minimax_code.workspace.child._validate_restricted_mounts", workspace_boundary)
    monkeypatch.setattr(manager, "_validate_workspace", workspace_boundary)
    runtime = SubAgentRuntime(llm=SimpleNamespace())
    build = runtime.build

    def recorded(*args, **kwargs):
        handle = build(*args, **kwargs)
        state.handles.append(handle)
        return handle

    monkeypatch.setattr(runtime, "build", recorded)
    monkeypatch.setattr("minimax_code.orchestrator.subagent.get_subagent_runtime", lambda: runtime)
    monkeypatch.setattr("minimax_code.orchestrator.get_subagent_runtime", lambda: runtime)

    async def core_run(core, **kwargs):
        state.entered.set()
        assert current_sandbox() is None
        assert current_process_target().owner.run_id != owner.run_id
        if state.behavior is not None:
            return await state.behavior(core)
        result = await core.registry.dispatch("write_file", {"path": "created.txt", "content": "child"})
        assert result.success, result.error
        return AgentRunResult("done", 1)

    monkeypatch.setattr(AgentCore, "run", core_run)
    service = RunService()
    context = {"execution_target": TARGET.to_dict(), "execution_installation_id": IDENTITY.install_id,
        "registry": registry, "tool_bindings": SkillBindings.capture(None, None).data,
        "skill_runtime": None, "context": {}, "mock": True, "permissions": None, "gater": None,
        "run_service": service}
    with ExitStack() as scopes:
        root_token, run_token, sandbox_token = set_current_root(root), set_current_run_id("root"), set_sandbox(overlay)
        context_token = owned_runtime.set(context)
        scopes.callback(reset_current_root, root_token)
        scopes.callback(reset_current_run_id, run_token)
        scopes.callback(reset_sandbox, sandbox_token)
        scopes.callback(owned_runtime.reset, context_token)
        scopes.enter_context(execution_scope(ExecutionPolicy(root)))
        scopes.enter_context(budget_scope(await BudgetHandle.for_run(db, "root")))
        scopes.enter_context(required_target_scope(TARGET, owner))
        yield state


def test_explicit_empty_sandbox_scope_resets_parent_overlay(tmp_path):
    parent = set_sandbox(tmp_path)
    try:
        cleared = set_sandbox(None)
        try:
            assert current_sandbox() is None
        finally:
            reset_sandbox(cleared)
        assert current_sandbox() == tmp_path
    finally:
        reset_sandbox(parent)


@pytest.mark.parametrize("value", [0, 1, "true", "false", [], {}])
async def test_spawn_overlay_flag_requires_a_json_boolean(value, monkeypatch):
    async def forbidden():
        pytest.fail("Malformed flags must be rejected before agent lookup")

    monkeypatch.setattr("minimax_code.agent.tools.subagents._agent_dao", forbidden)
    result = await SpawnSubagentTool().run("worker", "work", sandbox=value)
    assert not result.success and "boolean" in result.error


async def test_explicit_overlay_is_rejected_before_any_spawn_effect(restricted_child, monkeypatch):
    state = restricted_child
    before_runs = await AgentRunsDAO(state.db).list_runs()
    before_sessions = await state.db.fetchall("SELECT id FROM sessions")
    effects = []

    def forbidden(*args, **kwargs):
        effects.append("filesystem side effect")
        raise AssertionError("Restricted overlay request must fail before writes")

    monkeypatch.setattr(Path, "mkdir", forbidden)
    monkeypatch.setattr("minimax_code.agent.tools.artifacts.write_brief", forbidden)
    result = await SpawnSubagentTool().run("copy-worker", "write", sandbox=True)
    assert not result.success and "incompatible legacy overlay" in result.error
    assert not effects and not state.handles
    assert await AgentRunsDAO(state.db).list_runs() == before_runs
    assert await state.db.fetchall("SELECT id FROM sessions") == before_sessions
    assert current_sandbox() == state.overlay


@pytest.mark.parametrize("sandbox", [None, False])
async def test_restricted_env_default_uses_actual_copy_manifest(restricted_child, monkeypatch, sandbox):
    state = restricted_child
    monkeypatch.setenv("MINIMAX_CODE_SANDBOX_DEFAULT", "true")
    result = await SpawnSubagentTool().run("copy-worker", "write", sandbox=sandbox)
    assert result.success, result.error
    output = result.output
    assert output["sandbox"] is False
    assert SANDBOX_PROTOCOL_PROMPT not in state.handles[0].config.system_prompt
    child = load_child(state.root, output["workspace"]["run_id"])
    assert child.record["restricted"] is True and child.record["kind"] == "directory"
    assert output["workspace"]["collection_required"] is True
    assert output["files_written"] == output["workspace"]["files_written"] == child.record["changes"] == ["created.txt"]
    assert (child.root / "created.txt").read_text() == "child"
    assert not (state.root / "created.txt").exists()
    assert not (state.root / ".minimax" / "sandboxes").exists()
    assert not list(state.overlay.iterdir())
    saved = await AgentRunsDAO(state.db).get_run(output["run_id"])
    assert saved["metadata"]["sandbox"] is False
    assert saved["metadata"]["files_written"] == child.record["changes"]
    assert saved["metadata"]["process_execution_owner"]["copy_binding"]["kind"] == "directory"
    assert current_sandbox() == state.overlay


async def test_collection_uses_the_owned_copy_when_overlay_flag_is_false(restricted_child):
    state = restricted_child
    spawned = await SpawnSubagentTool().run("copy-worker", "write", sandbox=False)
    assert spawned.success and spawned.output["sandbox"] is False
    # Collection is a parent workspace operation, never an overlay operation.
    token = set_sandbox(None)
    try:
        collected = await CollectSubagentTool().run(run_id=spawned.output["run_id"])
    finally:
        reset_sandbox(token)
    assert collected.success, collected.error
    assert collected.output["complete"] is True
    assert [item["path"] for item in collected.output["merged"]] == ["created.txt"]
    assert (state.root / "created.txt").read_text() == "child"
    child = load_child(state.root, spawned.output["workspace"]["run_id"])
    assert child.record["state"] == "collected"
    assert not (state.root / ".minimax" / "sandboxes").exists()


async def direct_handle(state):
    runtime = SubAgentRuntime(llm=SimpleNamespace())
    handle = runtime.build(SubAgentConfig(name=state.row["name"], id=state.row["id"],
        execution_profile_id="linux-podman-l1", tool_allowlist=state.row["tool_allowlist"]),
        registry=owned_runtime.get()["registry"])
    dao = AgentRunsDAO(state.db)
    await dao.create_run(id="direct-child", session_id="session", status="running", mode="subagent",
        metadata={"budget_family_id": "family", "process_execution_owner": owner_snapshot(
            TARGET.to_dict(), IDENTITY.install_id, parent_run_id="root")})
    return runtime, handle


@pytest.mark.parametrize("failure", ["success", "exception", "cancel"])
async def test_direct_runtime_clears_inherited_overlay_and_always_restores(restricted_child, failure):
    state = restricted_child

    async def behavior(core):
        assert current_sandbox() is None
        if failure == "exception":
            raise RuntimeError("controlled child failure")
        if failure == "cancel":
            raise asyncio.CancelledError
        return AgentRunResult("done", 1)

    state.behavior = behavior
    runtime, handle = await direct_handle(state)
    token = set_current_run_id("direct-child")
    try:
        if failure == "success":
            result = await runtime.invoke(handle, session_id="session", request="work")
            assert result["workspace"]["kind"] == "directory"
        else:
            with pytest.raises(RuntimeError if failure == "exception" else asyncio.CancelledError):
                await runtime.invoke(handle, session_id="session", request="work")
        assert current_sandbox() == state.overlay
        assert current_root() == state.root
        assert current_process_target().owner == state.owner
    finally:
        reset_current_run_id(token)


async def test_file_tool_and_process_owner_use_the_same_independent_copy(restricted_child, monkeypatch):
    state = restricted_child
    observed = []
    create = state.controller.create

    async def process_create(row, argv, **kwargs):
        root = Path(row["workspace"])
        assert root == current_root() and root != state.root
        assert (root / "from-file-tool.txt").read_text() == "visible to process"
        assert current_sandbox() is None
        (root / "from-process.txt").write_text("process result")
        observed.append((row["run_id"], str(root)))
        await create(row, argv, **kwargs)

    monkeypatch.setattr(state.controller, "create", process_create)

    async def behavior(core):
        result = await core.registry.dispatch("write_file", {"path": "from-file-tool.txt", "content": "visible to process"})
        assert result.success, result.error
        process = await spawn_exec("controlled-process")
        assert await process.wait() == 42
        return AgentRunResult("verified", 1)

    state.behavior = behavior
    result = await SpawnSubagentTool().run("copy-worker", "write and verify", sandbox=False)
    assert result.success, result.error
    assert observed == [(result.output["run_id"], result.output["workspace"]["path"])]
    assert result.output["files_written"] == ["from-file-tool.txt", "from-process.txt"]
    assert not await state.manager.dao.active()
    assert not list(state.overlay.iterdir())


@pytest.mark.parametrize("sandbox", [True, "false", None, 0])
async def test_bad_queued_overlay_metadata_never_invokes_child(restricted_child, sandbox):
    state = restricted_child
    runtime, handle = await direct_handle(state)
    with pytest.raises(ProcessBoundaryError, match="legacy file overlay"):
        await _drive_run(runtime=runtime, handle=handle, run_id="direct-child", sub_session="session",
            prompt="work", row=state.row, parent="session", emit=None,
            run_dao=AgentRunsDAO(state.db), run_metadata={"sandbox": sandbox})
    assert not state.entered.is_set() and current_sandbox() == state.overlay
    assert (await AgentRunsDAO(state.db).get_run("direct-child"))["status"] == "failed"


@pytest.mark.parametrize("damage", ["parent-ended", "child-ended", "family", "install", "source", "revoked", "agent-profile"])
async def test_direct_runtime_rechecks_active_frozen_lineage_before_copy(restricted_child, damage):
    state = restricted_child
    runtime, handle = await direct_handle(state)
    runs = AgentRunsDAO(state.db)
    if damage == "parent-ended":
        await runs.update_run_status("root", status="completed")
    elif damage == "child-ended":
        await runs.update_run_status("direct-child", status="completed")
    elif damage in {"family", "install"}:
        row = await runs.get_run("direct-child")
        metadata = copy.deepcopy(row["metadata"])
        if damage == "family":
            metadata["budget_family_id"] = "other"
        else:
            metadata["process_execution_owner"]["execution_installation_id"] = "foreign"
        await runs.update_run_status("direct-child", status="running", metadata=metadata)
    elif damage == "revoked":
        state.revoked = True
    elif damage == "agent-profile":
        await state.agents.upsert(name="copy-worker", execution_profile_id="native-host")
    token = set_current_run_id("direct-child")
    root_token = set_current_root(state.overlay) if damage == "source" else None
    try:
        with pytest.raises(ProcessBoundaryError):
            await runtime.invoke(handle, session_id="session", request="work")
        assert not state.entered.is_set()
        assert not (state.root / ".minimax").exists()
        assert current_sandbox() == state.overlay
    finally:
        if root_token is not None:
            reset_current_root(root_token)
        reset_current_run_id(token)


async def test_actual_task_cancellation_restores_direct_child_context(restricted_child):
    state = restricted_child
    runtime, handle = await direct_handle(state)
    restored = []

    async def behavior(core):
        await asyncio.Event().wait()

    state.behavior = behavior

    async def invoke():
        token = set_current_run_id("direct-child")
        try:
            await runtime.invoke(handle, session_id="session", request="wait")
        finally:
            restored.append((current_sandbox(), current_root(), current_process_target().owner))
            reset_current_run_id(token)

    task = asyncio.create_task(invoke())
    await asyncio.wait_for(state.entered.wait(), 3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert restored == [(state.overlay, state.root, state.owner)]
    assert load_child(state.root, "direct-child").record["state"] == "cancelled"


@pytest.mark.parametrize("failure", [False, True])
async def test_drive_run_scope_clears_and_resets_inherited_overlay(restricted_child, failure):
    state = restricted_child
    runtime, handle = await direct_handle(state)
    dao = AgentRunsDAO(state.db)
    metadata = (await dao.get_run("direct-child"))["metadata"]
    metadata["sandbox"] = False
    if failure:
        async def behavior(core):
            raise RuntimeError("controlled drive failure")
        state.behavior = behavior
    args = dict(runtime=runtime, handle=handle, run_id="direct-child", sub_session="session",
        prompt="work", row=state.row, parent="session", emit=None, run_dao=dao, run_metadata=metadata)
    if failure:
        with pytest.raises(RuntimeError, match="controlled drive failure"):
            await _drive_run(**args)
    else:
        result = await _drive_run(**args)
        assert result["files_written"] == ["created.txt"]
    assert current_sandbox() == state.overlay and current_root() == state.root


@pytest.mark.parametrize("damage", ["revoked", "missing-frozen-target"])
async def test_queued_target_must_still_match_its_frozen_handle(restricted_child, damage):
    state = restricted_child
    runtime, handle = await direct_handle(state)
    if damage == "revoked":
        state.revoked = True
    else:
        handle.execution_target = None
    with pytest.raises(ProcessBoundaryError):
        await _drive_run(runtime=runtime, handle=handle, run_id="direct-child", sub_session="session",
            prompt="work", row=state.row, parent="session", emit=None,
            run_dao=AgentRunsDAO(state.db), run_metadata={"sandbox": False})
    assert not state.entered.is_set() and not (state.root / ".minimax").exists()
    assert current_sandbox() == state.overlay


async def test_restricted_runtime_cannot_report_stub_completion(restricted_child):
    state = restricted_child
    runtime, handle = await direct_handle(state)
    runtime._llm = None
    with pytest.raises(ProcessBoundaryError, match="stub completion is unavailable"):
        await runtime.invoke(handle, session_id="session", request="work")
    assert not state.entered.is_set() and not (state.root / ".minimax").exists()


@pytest.mark.parametrize("damage", ["inactive-parent", "invalid-config"])
async def test_parent_and_config_are_checked_before_spawn_artifacts(restricted_child, monkeypatch, damage):
    state = restricted_child
    if damage == "inactive-parent":
        await AgentRunsDAO(state.db).update_run_status("root", status="completed")
    else:
        await state.agents.upsert(name="copy-worker", tool_allowlist=["unknown-tool"])
    effects = []

    def forbidden(*args, **kwargs):
        effects.append("brief")
        raise AssertionError("Invalid admission reached a brief write")

    monkeypatch.setattr("minimax_code.agent.tools.artifacts.write_brief", forbidden)
    before_runs = len(await AgentRunsDAO(state.db).list_runs())
    before_sessions = len(await state.db.fetchall("SELECT id FROM sessions"))
    with pytest.raises((ProcessBoundaryError, ValueError)):
        await SpawnSubagentTool().run("copy-worker", "work", sandbox=False)
    assert not effects and not state.entered.is_set()
    assert len(await AgentRunsDAO(state.db).list_runs()) == before_runs
    assert len(await state.db.fetchall("SELECT id FROM sessions")) == before_sessions
    assert not (state.root / ".minimax").exists()


@pytest.mark.parametrize("boundary", ["copy", "owner"])
async def test_parent_ending_during_preparation_blocks_file_core(restricted_child, monkeypatch, boundary):
    from minimax_code import process_children
    from minimax_code.workspace import child as child_module

    state = restricted_child
    runtime, handle = await direct_handle(state)
    target = child_module if boundary == "copy" else process_children
    name = "prepare_child" if boundary == "copy" else "prepare_child_owner"
    original = getattr(target, name)

    async def ended_parent(*args, **kwargs):
        result = await original(*args, **kwargs)
        await AgentRunsDAO(state.db).update_run_status("root", status="completed")
        return result

    monkeypatch.setattr(target, name, ended_parent)
    token = set_current_run_id("direct-child")
    try:
        with pytest.raises(ProcessBoundaryError, match="inactive or changed"):
            await runtime.invoke(handle, session_id="session", request="work")
    finally:
        reset_current_run_id(token)
    assert not state.entered.is_set()
    child = load_child(state.root, "direct-child")
    assert child.record["state"] == "failed"
    assert not (child.root / "created.txt").exists()
    assert current_sandbox() == state.overlay and current_root() == state.root
