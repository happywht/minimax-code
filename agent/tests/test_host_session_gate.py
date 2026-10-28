"""Durable session history fences public terminals and runner discovery probes."""

from __future__ import annotations

import copy
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from minimax_code import app
from minimax_code.agent.tools.terminal import ExecCommandTool
from minimax_code.agent_execution import AgentExecutionSpec
from minimax_code.config import Config
from minimax_code.ipc import handlers_runner, handlers_terminal
from minimax_code.ipc.handler_utils import HandlerError
from minimax_code.ipc.host_session_gate import require_host_session
from minimax_code.ipc.protocol import INVALID_PARAMS
from minimax_code.ipc.server import IPCServer
from minimax_code.process_binding import HOST_TARGET
from minimax_code.process_context import current_process_target
from minimax_code.process_types import ExecutionTargetSpec, canonical_hash
from minimax_code.skill_bindings import tool_identity
from minimax_code.skill_execution import _digest
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase

pytestmark = pytest.mark.security
TARGET = ExecutionTargetSpec("linux-podman-l1", "sha256:" + "a" * 64).to_dict()


def public_profile(root, *, version=3, target=TARGET, session_id="execution"):
    bindings = {"version": 1, "skills": []}
    bindings["digest"] = _digest(bindings)
    data = {
        "version": version, "agent_id": "worker", "agent": "worker",
        "instructions": "Inspect the workspace", "agent_instructions": "Inspect the workspace",
        "skill_bindings": bindings, "model_snapshot": {"model": "fixture-model"},
        "permission_rules": [], "workspace": str(root / "copy"),
        "source_workspace": str(root), "source_identity": [1, 2], "workspace_id": "copy",
        "project_id": "inbox", "source_session_id": "source",
        "execution_session_id": session_id, "intent": {}, "max_iterations": 3,
        "system_prompt_extra": "", "prepared": False, "directory_identity": None,
        "mock": True, "model_provider_fingerprint": None, "allowlist": [],
        "temperature": None, "tool_identity": _digest([]), "verified_models": ["fixture-model"],
    }
    if version == 1:
        data.pop("skill_bindings")
    if version == 3:
        data.update(execution_target=copy.deepcopy(target),
                    execution_target_hash=canonical_hash(target),
                    execution_installation_id="installation" if target["mode"] == "podman" else None)
        if target["mode"] == "podman":
            data.update(allowlist=[ExecCommandTool.name],
                        tool_identity=tool_identity([ExecCommandTool()]),
                        intent={"method": "agent.invoke"},
                        hooks_policy="disabled", permission_policy_version=1)
    data["digest"] = _digest(data)
    AgentExecutionSpec(data)
    return data


def internal_profile(*, target=TARGET):
    return {
        "version": 1, "parent_run_id": "parent", "execution_target": copy.deepcopy(target),
        "execution_target_hash": canonical_hash(target), "prepared": False,
        "execution_installation_id": "installation" if target["mode"] == "podman" else None,
    }


@pytest.fixture
async def state(tmp_path, monkeypatch):
    db = AsyncDatabase(tmp_path / "state.db")
    await db.connect()
    await db.migrate()
    sessions = SessionsDAO(db)
    for session_id in ("source", "execution"):
        await sessions.create(id=session_id, workspace_path=str(tmp_path))
    monkeypatch.setattr(app, "_DB_SINGLETON", db)
    monkeypatch.setattr(app, "get_sessions_dao", lambda: sessions)
    monkeypatch.setattr(app, "get_projects_dao", lambda: None)
    monkeypatch.setattr(app, "ensure_db", AsyncMock(return_value=db))
    handlers_terminal._SESSIONS.clear()
    server = IPCServer(config=Config.from_env(), stdin=sys.stdin, stdout=sys.stdout)
    handlers_terminal.register_terminal_handlers(server)
    handlers_runner.register_runner_handlers(server)
    yield SimpleNamespace(db=db, root=tmp_path, server=server, dao=AgentRunsDAO(db))
    for session in handlers_terminal._SESSIONS.values():
        if session.task:
            await session.task
    handlers_terminal._SESSIONS.clear()
    await db.close()


async def history(state, metadata, *, status="completed", session_id="execution"):
    return await state.dao.create_run(session_id=session_id, mode="execute", status=status,
                                      metadata=metadata)


async def invoke(state, method, **params):
    ctx = SimpleNamespace(reply=AsyncMock(), reply_error=AsyncMock(), emit=AsyncMock())
    await state.server._handlers[method](
        {"session_id": "execution", "command": "echo forbidden", "cwd": str(state.root),
         **params}, ctx,
    )
    return ctx


async def count_resources(state):
    counts = []
    for table in ("agent_runs", "agent_run_steps"):
        counts.append((await state.db.fetchone(f"SELECT COUNT(*) AS n FROM {table}"))["n"])
    return counts


@pytest.mark.parametrize("authority", ["agent_execution", "process_execution_owner"])
@pytest.mark.parametrize("status", ["queued", "running", "completed", "failed", "cancelled"])
@pytest.mark.parametrize("method", ["terminal.start", "runner.start"])
async def test_restricted_history_denies_before_any_resources_or_probe(
    state, monkeypatch, authority, status, method,
):
    profile = public_profile(state.root) if authority == "agent_execution" else internal_profile()
    await history(state, {authority: profile}, status=status)
    counts = await count_resources(state)
    spawn = AsyncMock(side_effect=AssertionError("Host spawn forbidden"))
    probe = AsyncMock(side_effect=AssertionError("Discovery forbidden"))
    monkeypatch.setattr(handlers_terminal, "spawn_shell", spawn)
    monkeypatch.setattr(handlers_runner, "_runner_catalog", probe)
    assert current_process_target() is None
    ctx = await invoke(state, method, runner_id="codex-cli", host_allowed=True,
                       execution_target=HOST_TARGET)
    ctx.reply.assert_not_awaited()
    assert ctx.reply_error.await_args.args == (
        -32000, "Host execution is unavailable for this session",
        {"reason": "restricted_execution_session"},
    )
    assert not handlers_terminal._SESSIONS
    assert await count_resources(state) == counts
    spawn.assert_not_awaited()
    probe.assert_not_awaited()


@pytest.mark.parametrize("damage", [
    "bad_json", "duplicate_keys", "metadata_list", "null_public", "null_internal", "both",
    "version", "bool_version", "missing_target", "null_target", "target_hash", "digest",
    "legacy_target", "legacy_incomplete", "wrong_session", "internal_version",
])
async def test_corrupt_and_ambiguous_history_fails_closed(state, damage):
    profile = public_profile(state.root)
    metadata = {"agent_execution": profile}
    raw = None
    if damage == "bad_json":
        raw = "{broken"
    elif damage == "duplicate_keys":
        raw = '{"agent_execution":{},"agent_execution":null}'
    elif damage == "metadata_list":
        raw = "[]"
    elif damage == "null_public":
        metadata["agent_execution"] = None
    elif damage == "null_internal":
        metadata = {"process_execution_owner": None}
    elif damage == "both":
        metadata["process_execution_owner"] = None
    elif damage == "internal_version":
        metadata = {"process_execution_owner": {**internal_profile(), "version": 999}}
    elif damage == "legacy_target":
        profile["version"] = 2
    elif damage == "legacy_incomplete":
        metadata["agent_execution"] = {"version": 1}
    elif damage == "wrong_session":
        profile["execution_session_id"] = "source"
    elif damage == "version":
        profile["version"] = 999
    elif damage == "bool_version":
        profile["version"] = True
    elif damage == "missing_target":
        profile.pop("execution_target")
    elif damage == "null_target":
        profile["execution_target"] = None
    elif damage == "target_hash":
        profile["execution_target_hash"] = "bad"
    if damage == "digest":
        profile["digest"] = "bad"
    else:
        profile["digest"] = _digest({k: v for k, v in profile.items() if k != "digest"})
    run = await history(state, metadata)
    if raw is not None:
        await state.db.execute("UPDATE agent_runs SET metadata=? WHERE id=?", (raw, run["id"]))
    with pytest.raises(HandlerError) as error:
        await require_host_session("execution")
    assert error.value.code == -32000
    assert error.value.data == {"reason": "session_execution_binding_invalid"}


async def test_all_history_is_checked_after_newer_host_runs_and_agent_changes(state):
    await history(state, {"agent_execution": public_profile(state.root)})
    for _ in range(40):
        await history(state, {"agent_execution": public_profile(state.root, target=HOST_TARGET)})
    with pytest.raises(HandlerError) as error:
        await require_host_session("execution")
    assert error.value.data["reason"] == "restricted_execution_session"
    await require_host_session("source")


@pytest.mark.parametrize("version", [1, 2, 3])
async def test_wellformed_host_history_executes_and_source_stays_host_capable(state, version):
    await history(state, {"agent_execution": public_profile(state.root, version=version,
                                                            target=HOST_TARGET)})
    await require_host_session("execution")
    host_ctx = await invoke(state, "terminal.start", command="echo host-compatible")
    host_ctx.reply_error.assert_not_awaited()
    host_terminal = handlers_terminal._SESSIONS[
        host_ctx.reply.await_args.args[0]["session"]["id"]
    ]
    await host_terminal.task
    assert host_terminal.status == "completed"
    await history(state, {"agent_execution": public_profile(state.root)})
    assert current_process_target() is None
    # The real host process belongs to the source, never the restricted execution session.
    marker = state.root / "source-ran.txt"
    command = f'"{sys.executable}" -c "from pathlib import Path; Path(r\'{marker}\').write_text(\'ok\')"'
    ctx = await invoke(state, "terminal.start", session_id="source", command=command)
    ctx.reply_error.assert_not_awaited()
    terminal = handlers_terminal._SESSIONS[ctx.reply.await_args.args[0]["session"]["id"]]
    await terminal.task
    assert terminal.status == "completed" and marker.read_text() == "ok"


@pytest.mark.parametrize("failure", ["init", "query"])
@pytest.mark.parametrize("method", ["terminal.start", "runner.start"])
async def test_database_unavailable_never_uses_optional_tracking_fallback(
    state, monkeypatch, failure, method,
):
    if failure == "init":
        monkeypatch.setattr(app, "ensure_db", AsyncMock(side_effect=OSError("unavailable")))
    else:
        await state.db.execute("ALTER TABLE agent_runs RENAME TO inaccessible_runs")
    tracking = AsyncMock(return_value=None)
    spawn = AsyncMock()
    catalog = AsyncMock()
    monkeypatch.setattr(handlers_terminal, "_get_runs_dao", tracking)
    monkeypatch.setattr(handlers_terminal, "spawn_shell", spawn)
    monkeypatch.setattr(handlers_runner, "_runner_catalog", catalog)
    ctx = await invoke(state, method, runner_id="codex-cli")
    assert ctx.reply_error.await_args.args[0] == -32000
    assert ctx.reply_error.await_args.args[2]["reason"] == "session_execution_binding_unavailable"
    assert not handlers_terminal._SESSIONS
    for operation in (tracking, spawn, catalog):
        operation.assert_not_awaited()


async def test_shared_starter_cannot_bypass_gate(state):
    await history(state, {"process_execution_owner": internal_profile()})
    with pytest.raises(HandlerError):
        await handlers_terminal.start_terminal_command(command="echo forbidden", cwd=str(state.root),
            timeout_s=5, chat_session_id="execution", emit=AsyncMock())
    assert not handlers_terminal._SESSIONS


async def test_recheck_before_tracking_when_session_restricted_after_registration(state, monkeypatch):
    spawn = AsyncMock()
    tracking = AsyncMock()
    monkeypatch.setattr(handlers_terminal, "spawn_shell", spawn)
    monkeypatch.setattr(handlers_terminal, "_create_run_tracking", tracking)
    original = handlers_terminal.require_host_session
    calls = 0

    async def gate(session_id):
        nonlocal calls
        await original(session_id)
        calls += 1
        if calls == 1:
            await history(state, {"agent_execution": public_profile(state.root)})

    monkeypatch.setattr(handlers_terminal, "require_host_session", gate)
    session = await handlers_terminal.start_terminal_command(command="echo forbidden",
        cwd=str(state.root), timeout_s=5, chat_session_id="execution", emit=AsyncMock())
    await session.task
    assert session.status == "failed" and "restricted_execution_session" in session.error
    tracking.assert_not_awaited()
    spawn.assert_not_awaited()


async def test_recheck_after_tracking_prevents_dispatch_race(state, monkeypatch):
    original = handlers_terminal._create_run_tracking

    async def tracking(session, emit):
        await original(session, emit)
        await history(state, {"agent_execution": public_profile(state.root)})

    monkeypatch.setattr(handlers_terminal, "_create_run_tracking", tracking)
    spawn = AsyncMock()
    monkeypatch.setattr(handlers_terminal, "spawn_shell", spawn)
    session = await handlers_terminal.start_terminal_command(command="echo forbidden",
        cwd=str(state.root), timeout_s=5, chat_session_id="execution", emit=AsyncMock())
    await session.task
    assert session.status == "failed" and "restricted_execution_session" in session.error
    assert (await state.dao.get_run(session.run_id))["status"] == "failed"
    spawn.assert_not_awaited()


async def test_runner_rechecks_each_probe_at_dispatch(state, monkeypatch):
    spawn = AsyncMock()
    monkeypatch.setattr(handlers_runner, "spawn_exec", spawn)
    monkeypatch.setattr(handlers_runner, "spawn_shell", spawn)
    # Simulates restriction committed after catalog admission and before a probe starts.
    await history(state, {"agent_execution": public_profile(state.root)})
    with pytest.raises(HandlerError):
        await handlers_runner._run_probe("codex.exe", ["--version"], chat_session_id="execution")
    spawn.assert_not_awaited()


@pytest.mark.parametrize("params", [{"session_id": "execution"}, {"ignored": True}, [], "", 0, False])
async def test_runner_list_rejects_scoping_and_unknown_params_before_probes(state, monkeypatch, params):
    await history(state, {"agent_execution": public_profile(state.root)})
    catalog = AsyncMock()
    monkeypatch.setattr(handlers_runner, "_runner_catalog", catalog)
    ctx = SimpleNamespace(reply=AsyncMock(), reply_error=AsyncMock())
    await state.server._handlers["runner.list"](params, ctx)
    ctx.reply.assert_not_awaited()
    assert ctx.reply_error.await_args.args[0] == INVALID_PARAMS
    catalog.assert_not_awaited()


@pytest.mark.parametrize("params", [None, {}])
async def test_runner_list_keeps_anonymous_global_inventory(state, monkeypatch, params):
    catalog = AsyncMock(return_value=[{"id": "native", "available": True}])
    monkeypatch.setattr(handlers_runner, "_runner_catalog", catalog)
    ctx = SimpleNamespace(reply=AsyncMock(), reply_error=AsyncMock())
    await state.server._handlers["runner.list"](params, ctx)
    ctx.reply_error.assert_not_awaited()
    assert ctx.reply.await_args.args[0] == {"runners": [{"id": "native", "available": True}]}
    catalog.assert_awaited_once_with()
