"""Exec-only L1 admission/restore/dispatch; all services are offline test seams."""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from test_agent_execution import agent_env as agent_env
from test_agent_execution import invoke
from test_process_ownership import IDENTITY, TARGET
from test_skill_execution import skill_env as skill_env

from minimax_code import app
from minimax_code.agent.tools.base import Tool, ToolRegistry, ToolResult
from minimax_code.agent.tools.file_ops import ReadFileTool, WriteFileTool
from minimax_code.agent.tools.terminal import ExecCommandTool
from minimax_code.agent_execution import AgentExecutionSpec, canonical_intent
from minimax_code.execution_profiles import ExecutionProfileCatalog
from minimax_code.process_binding import load_execution_binding
from minimax_code.process_boundary import assert_supported_tools
from minimax_code.process_context import required_target_scope
from minimax_code.process_types import ProcessBoundaryError
from minimax_code.skill_bindings import SkillBindings, tool_identity
from minimax_code.skill_execution import _digest


@pytest.fixture
async def restricted_env(agent_env, monkeypatch):
    state = agent_env
    state.agent = await state.agents.upsert("worker", execution_profile_id="linux-podman-l1",
        tool_allowlist=["exec_command"])
    # Real catalog resolution; controlled availability is not a production grant.
    catalog = ExecutionProfileCatalog(data_dir=state.root / "service-data")
    catalog._targets["linux-podman-l1"] = TARGET.to_dict()
    catalog._manager = SimpleNamespace(identity=IDENTITY, _protected_roots=())
    monkeypatch.setattr(catalog, "_availability", lambda: (True, None))
    catalog.prepare_service = AsyncMock()
    monkeypatch.setattr(app, "ensure_execution_profiles", AsyncMock(return_value=catalog))
    monkeypatch.setattr(app, "get_execution_profiles", lambda: catalog)
    state.catalog = catalog
    return state


async def capture(state, method="agent.invoke"):
    values = dict(name="worker", request="Run a container command", session_id="one")
    source = await state.sessions.get("one")
    return await AgentExecutionSpec.capture(state.agent, state.db, state.server, values,
        canonical_intent(values, method), str(state.source), source["project_id"], "exec-session", "exec-copy")


def redigest(data):
    data["digest"] = _digest({key: value for key, value in data.items() if key != "digest"})
    return data


def forbidden(*args, **kwargs):
    pytest.fail("Rejected L1 request reached runtime/model/dependency/dispatch effects")


async def test_exec_only_snapshot_roundtrips_to_unprepared_durable_binding(restricted_env):
    spec, _ = await capture(restricted_env)
    restored = AgentExecutionSpec(spec.snapshot())
    binding = load_execution_binding({"id": "root", "metadata": {"agent_execution": restored.snapshot()}})
    assert binding.target == TARGET.to_dict() and binding.owner is None
    assert restored.data["allowlist"] == ["exec_command"]
    assert restored.data["skill_bindings"]["skills"] == []
    assert [type(tool) for tool in spec.tools] == [ExecCommandTool]


async def test_supported_prepared_exec_spec_restores_without_expanding_tools(restricted_env):
    state = restricted_env
    spec, _ = await capture(state)
    run, _ = await state.dao.admit_run(session_id=spec.data["execution_session_id"],
        request={"mode": "execute", "content": "controlled"}, title="Exec-only agent",
        metadata={"agent_execution": spec.snapshot()}, request_key="supported-exec")
    with required_target_scope(TARGET):
        prepared = await spec.prepare(state.db, run["id"])
    restored, model = await AgentExecutionSpec.restore(prepared.snapshot(), state.runtime, state.db)
    saved = await state.dao.get_run(run["id"])
    binding = load_execution_binding(saved, family_root_run_id=run["id"])
    assert binding.owner.run_id == run["id"] and binding.owner.workspace == restored.data["workspace"]
    assert [type(tool) for tool in restored.tools] == [ExecCommandTool]
    assert restored.snapshot() == prepared.snapshot() and model.model == spec.data["model_snapshot"]["model"]
    assert not state.calls


@pytest.mark.parametrize("skills", [["text-only"], ["missing"], ["probe"]])
async def test_any_configured_skills_rejected_before_runtime_initialization(restricted_env, monkeypatch, skills):
    state = restricted_env
    state.agent = await state.agents.upsert("worker", skills=skills)
    monkeypatch.setattr(app, "init_runtime", forbidden)
    monkeypatch.setattr(SkillBindings, "capture", forbidden)
    with pytest.raises(ProcessBoundaryError, match="does not support skills"):
        await capture(state)
    assert not state.calls and not await state.dao.list_runs()
    assert not (state.source / ".minimax").exists()


@pytest.mark.parametrize("names", [None, [], ["read_file"], ["write_file", "exec_command"]])
async def test_unaccepted_tool_sets_rejected_before_model_capture(restricted_env, monkeypatch, names):
    from minimax_code.model_snapshot import ModelSnapshot

    state = restricted_env
    state.agent = await state.agents.upsert("worker", tool_allowlist=names)
    if names is None:
        await state.db.execute("UPDATE agents SET tool_allowlist=NULL WHERE name='worker'")
        state.agent = await state.agents.get("worker")
    monkeypatch.setattr(ModelSnapshot, "capture", forbidden)
    with pytest.raises((ProcessBoundaryError, ValueError)):
        await capture(state)
    assert not state.calls and not await state.dao.list_runs()


@pytest.mark.parametrize("damage", ["read", "broad", "empty", "identity", "skill", "alias", "hooks"])
async def test_old_broad_or_changed_spec_rejected_before_restore_and_owner_projection(
    restricted_env, monkeypatch, damage,
):
    from minimax_code import secrets

    state = restricted_env
    spec, _ = await capture(state)
    data = spec.snapshot()
    data["prepared"] = True
    if damage == "read":
        data.update(allowlist=["read_file"], tool_identity=tool_identity([ReadFileTool()]))
    elif damage == "broad":
        data.update(allowlist=["read_file", "write_file", "exec_command"],
            tool_identity=tool_identity([ReadFileTool(), WriteFileTool(), ExecCommandTool()]))
    elif damage == "empty":
        data.update(allowlist=[], tool_identity=tool_identity([]))
    elif damage == "identity":
        data["tool_identity"] = "foreign"
    elif damage == "skill":
        data["skill_bindings"]["skills"].append(dict(id="text-only", version="1", instructions="text",
            manifest_tools=[], provider_tools=[], provider_digest=_digest(None), tool_identity=tool_identity([])))
        redigest(data["skill_bindings"])
    elif damage == "alias":
        data["intent"]["method"] = "agent.spawn_subagent"
    else:
        data["hooks_policy"] = "enabled"
    redigest(data)
    monkeypatch.setattr(secrets, "get_provider_key", forbidden)
    monkeypatch.setattr(SkillBindings, "validate", forbidden)
    with pytest.raises(ValueError):
        AgentExecutionSpec(data)
    with pytest.raises(ValueError):
        await AgentExecutionSpec.restore(data, state.runtime, state.db)
    with pytest.raises(ProcessBoundaryError):
        load_execution_binding({"id": "root", "metadata": {"agent_execution": data}})
    assert not state.calls


class Impostor(Tool):
    name = "exec_command"

    async def run(self, **kwargs):
        forbidden()


class SubclassExec(ExecCommandTool):
    async def run(self, **kwargs):
        forbidden()


@pytest.mark.parametrize("tool_class", [Impostor, SubclassExec])
async def test_impostor_selected_by_name_cannot_reach_model_capture(restricted_env, monkeypatch, tool_class):
    from minimax_code.agent import tools
    from minimax_code.model_snapshot import ModelSnapshot

    registry = ToolRegistry()
    registry.register(tool_class())
    monkeypatch.setattr(tools, "get_default_registry", lambda: registry)
    monkeypatch.setattr(ModelSnapshot, "capture", forbidden)
    with pytest.raises(ProcessBoundaryError):
        await capture(restricted_env)
    assert not restricted_env.calls and not await restricted_env.dao.list_runs()


@pytest.mark.parametrize("tool", [Impostor(), SubclassExec(), ReadFileTool(), WriteFileTool()])
async def test_exact_actual_class_is_checked_at_admission_and_dispatch(restricted_env, tool):
    with pytest.raises(ProcessBoundaryError):
        assert_supported_tools([tool])
    registry = ToolRegistry()
    registry.register(tool)
    args = {"cmd": ["never-run"]} if isinstance(tool, ExecCommandTool) else (
        {"path": "not-read", "content": "not-written"} if type(tool) is WriteFileTool else
        {"path": "not-read"} if type(tool) is ReadFileTool else {})
    with required_target_scope(TARGET):
        result = await registry.dispatch(tool.name, args)
    assert not result.success and "L1" in result.error


async def test_exact_exec_dispatch_keeps_shared_boundary(restricted_env):
    tool = ExecCommandTool()
    tool.run = AsyncMock(return_value=ToolResult.ok("controlled payload"))
    registry = ToolRegistry()
    registry.register(tool)
    with required_target_scope(TARGET):
        result = await registry.dispatch("exec_command", {"cmd": ["controlled"]})
    assert result.success
    tool.run.assert_awaited_once_with(cmd=["controlled"])


async def test_alias_new_restricted_root_refused_before_capture(restricted_env, monkeypatch):
    monkeypatch.setattr(AgentExecutionSpec, "capture", forbidden)
    ctx = await invoke(restricted_env, "agent.spawn_subagent")
    assert "require agent.invoke" in str(ctx.reply_error.await_args)
    assert not restricted_env.calls and not await restricted_env.dao.list_runs()
    assert not (restricted_env.source / ".minimax").exists()


@pytest.mark.parametrize("method", ["agent.invoke", "agent.spawn_subagent"])
async def test_old_active_broad_snapshot_cannot_join_idempotent_execution(
    restricted_env, monkeypatch, method,
):
    from minimax_code.ipc import builtins
    from minimax_code.storage.dao.runs import AgentRunsDAO

    state = restricted_env
    spec, _ = await capture(state)
    data = spec.snapshot()
    data.update(allowlist=["read_file"], tool_identity=tool_identity([ReadFileTool()]))
    data["intent"]["method"] = method
    redigest(data)
    existing = {"id": "legacy-broad", "status": "running", "metadata": {"agent_execution": data}}
    if method == "agent.spawn_subagent":
        # The frozen old target controls alias rejection even if config changed.
        await state.agents.upsert("worker", execution_profile_id="native-host")
    monkeypatch.setattr(AgentRunsDAO, "find_agent_root", AsyncMock(return_value=existing))
    monkeypatch.setattr(builtins, "_reply_for_run", forbidden)
    monkeypatch.setattr(AgentExecutionSpec, "capture", forbidden)
    ctx = await invoke(state, method)
    assert ctx.reply_error.await_count == 1 and not state.calls


async def test_historical_terminal_broad_receipt_is_read_only(restricted_env, monkeypatch):
    from minimax_code.ipc import builtins
    from minimax_code.storage.dao.runs import AgentRunsDAO

    state = restricted_env
    spec, _ = await capture(state)
    data = spec.snapshot()
    data.update(allowlist=["read_file"], tool_identity=tool_identity([ReadFileTool()]))
    redigest(data)
    existing = {"id": "historical", "status": "completed", "metadata": {"agent_execution": data}}
    monkeypatch.setattr(AgentRunsDAO, "find_agent_root", AsyncMock(return_value=existing))
    reply = AsyncMock()
    monkeypatch.setattr(builtins, "_reply_for_run", reply)
    monkeypatch.setattr(AgentExecutionSpec, "capture", forbidden)
    await invoke(state)
    reply.assert_awaited_once()
    assert not state.calls
