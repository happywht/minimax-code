"""Public chat never downgrades durable restricted or internal-child authority."""

from __future__ import annotations

import copy
import json
from unittest.mock import AsyncMock, Mock

import pytest
from test_agent_execution import agent_env as agent_env
from test_agent_execution import invoke as invoke_agent
from test_agent_execution import skill_env as skill_env
from test_host_session_gate import internal_profile, public_profile
from test_run_service import Context
from test_run_service import native as native

from minimax_code import app
from minimax_code.agent_execution import AgentExecutionSpec
from minimax_code.ipc import builtins, handlers_runs, host_session_gate
from minimax_code.ipc.handler_utils import HandlerError
from minimax_code.model_snapshot import ModelSnapshot
from minimax_code.process_binding import HOST_TARGET
from minimax_code.runs import get_run_service
from minimax_code.skill_execution import _digest

pytestmark = pytest.mark.security


async def history(state, metadata, *, status="completed", session_id="one"):
    return await state.dao.create_run(
        session_id=session_id, mode="execute", status=status, metadata=metadata)


async def resources(state):
    return [
        (await state.db.fetchone(f"SELECT COUNT(*) AS n FROM {table}"))["n"]
        for table in ("agent_runs", "agent_run_steps", "messages", "budget_families")
    ]


async def invoke(state, method, *, run_id=None, session_id="one", **changes):
    ctx = Context(state.server)
    if "run.start" not in state.server._handlers:
        handlers_runs.register_run_handlers(state.server)
    if method == "agent.send_message":
        await builtins.handle_agent_send_message(
            {"session_id": session_id, "content": "do work", **changes}, ctx)
    elif method == "agent.continue_run":
        await builtins.handle_agent_continue_run(
            {"session_id": session_id, **({"run_id": run_id} if run_id else {}), **changes}, ctx)
    elif method == "run.start":
        await state.server._handlers[method](
            {"session_id": session_id, "content": "do work", "idempotency_key": "new",
             **changes}, ctx)
    else:
        await state.server._handlers[method](
            {"run_id": run_id, "idempotency_key": "recovery", "reviewed": True,
             **changes}, ctx)
    return ctx


def assert_denied(ctx, reason):
    ctx.reply.assert_not_awaited()
    assert ctx.reply_error.await_args.args == (
        -32000, "Host execution is unavailable for this session", {"reason": reason})


@pytest.mark.parametrize("authority", ["agent_execution", "process_execution_owner"])
@pytest.mark.parametrize("status", ["queued", "running", "completed", "failed", "cancelled"])
@pytest.mark.parametrize("method,engine", [
    ("agent.send_message", None), ("run.start", "native"), ("run.start", "codex"),
])
async def test_chat_and_both_engines_deny_all_restricted_history_before_resources(
    native, monkeypatch, authority, status, method, engine,
):
    profile = (public_profile(native.root, session_id="one")
               if authority == "agent_execution" else internal_profile())
    await history(native, {authority: profile}, status=status)
    before = await resources(native)
    capture = Mock(side_effect=AssertionError("Model capture forbidden"))
    codex = AsyncMock(side_effect=AssertionError("Codex dispatch forbidden"))
    monkeypatch.setattr(ModelSnapshot, "capture", capture)
    monkeypatch.setattr(handlers_runs, "_start_codex_run", codex)
    ctx = await invoke(native, method, **({"engine": engine} if engine else {}))
    assert_denied(ctx, "restricted_execution_session")
    assert await resources(native) == before and not native.cores
    capture.assert_not_called()
    codex.assert_not_awaited()


@pytest.mark.parametrize("damage", [
    "bad_json", "duplicate", "list", "null_public", "null_internal", "dual", "unknown",
])
@pytest.mark.parametrize("method", [
    "agent.send_message", "run.start", "agent.continue_run", "run.resume",
])
async def test_raw_corrupt_history_denies_handlers_without_model_or_admission(
    native, monkeypatch, damage, method,
):
    run = await history(native, {"truncated": True}, status="cancelled")
    metadata = {
        "bad_json": "{broken", "duplicate": '{"process_execution_owner":{},'
        '"process_execution_owner":null}', "list": "[]",
        "null_public": '{"agent_execution":null}',
        "null_internal": '{"process_execution_owner":null}',
        "dual": json.dumps({"agent_execution": None, "process_execution_owner": None}),
        "unknown": json.dumps({"process_execution_owner": {**internal_profile(), "version": 99}}),
    }[damage]
    await native.db.execute("UPDATE agent_runs SET metadata=? WHERE id=?", (metadata, run["id"]))
    before = await resources(native)
    capture = Mock(side_effect=AssertionError("Model capture forbidden"))
    monkeypatch.setattr(ModelSnapshot, "capture", capture)
    ctx = await invoke(native, method, run_id=run["id"])
    assert_denied(ctx, "session_execution_binding_invalid")
    assert await resources(native) == before and not native.cores
    assert (await native.db.fetchone("SELECT metadata FROM agent_runs WHERE id=?",
                                    (run["id"],)))["metadata"] == metadata
    capture.assert_not_called()


@pytest.mark.parametrize("method,status", [
    ("agent.continue_run", "completed"), ("agent.continue_run", "cancelled"),
    ("run.resume", "cancelled"), ("run.resume", "failed"),
])
@pytest.mark.parametrize("target", [None, HOST_TARGET])
@pytest.mark.parametrize("explicit", [False, True])
async def test_internal_child_recovery_explicitly_denied_before_source_marker(
    native, monkeypatch, method, status, target, explicit,
):
    profile = internal_profile(**({"target": target} if target else {}))
    run = await history(native, {"process_execution_owner": profile, "truncated": True},
                        status=status)
    before = await resources(native)
    capture = Mock(side_effect=AssertionError("Model capture forbidden"))
    monkeypatch.setattr(ModelSnapshot, "capture", capture)
    selected = run["id"] if explicit or method == "run.resume" else None
    ctx = await invoke(native, method, run_id=selected)
    assert_denied(ctx, "internal_process_recovery_unsupported")
    assert await resources(native) == before and not native.cores
    assert "continued" not in (await native.dao.get_run(run["id"]))["metadata"]
    capture.assert_not_called()


@pytest.mark.parametrize("method", [
    "agent.send_message", "run.start", "run.resume", "agent.continue_run",
])
async def test_database_gate_errors_never_fall_back_to_host(native, monkeypatch, method):
    run = await history(native, {}, status="cancelled")
    before = await resources(native)
    monkeypatch.setattr(app, "ensure_db", AsyncMock(side_effect=OSError("database offline")))
    capture = Mock(side_effect=AssertionError("Model capture forbidden"))
    monkeypatch.setattr(ModelSnapshot, "capture", capture)
    ctx = await invoke(native, method, run_id=run["id"])
    assert_denied(ctx, "session_execution_binding_unavailable")
    assert await resources(native) == before and not native.cores
    capture.assert_not_called()


@pytest.mark.parametrize("version", [1, 2, 3])
async def test_host_profile_history_and_source_session_keep_native_chat(native, version):
    await history(native, {"agent_execution": public_profile(
        native.root, session_id="one", target=HOST_TARGET, version=version)})
    native.release.set()
    ctx = await invoke(native, "agent.send_message")
    ctx.reply_error.assert_not_awaited()
    assert ctx.reply.await_args.args[0]["text"] == "done"
    # A source reference is not an execution binding and does not lock "two".
    restricted = public_profile(native.root, session_id="one")
    restricted["source_session_id"] = "two"
    restricted["digest"] = _digest({k: v for k, v in restricted.items() if k != "digest"})
    await history(native, {"agent_execution": restricted})
    source = await invoke(native, "agent.send_message", session_id="two")
    source.reply_error.assert_not_awaited()
    assert source.reply.await_args.args[0]["text"] == "done"


@pytest.mark.parametrize("phase", ["queued", "budget"])
async def test_dispatch_recheck_persists_failure_and_never_enters_model_loop(
    native, monkeypatch, phase,
):
    if phase == "queued":
        original = get_run_service(native.server).submit

        def hold(session_id, operation, **kwargs):
            async def race(job):
                await history(native, {"process_execution_owner": internal_profile()})
                await operation(job)
            return original(session_id, race, **kwargs)

        monkeypatch.setattr(get_run_service(native.server), "submit", hold)
    else:
        from minimax_code.budget_ledger import BudgetHandle

        original = BudgetHandle.for_run

        async def acquire(*args, **kwargs):
            handle = await original(*args, **kwargs)
            await history(native, {"process_execution_owner": internal_profile()})
            return handle

        monkeypatch.setattr(BudgetHandle, "for_run", acquire)
    ctx = await invoke(native, "agent.send_message")
    assert_denied(ctx, "restricted_execution_session")
    runs = await native.dao.list_runs(session_id="one")
    failed = next(run for run in runs if run["status"] == "failed")
    receipt = await native.dao.result_payload(failed["id"])
    assert receipt["error"]["data"]["reason"] == "restricted_execution_session"
    assert not native.cores


async def test_codex_dispatch_helper_rechecks_restricted_history(native, monkeypatch):
    from minimax_code.engines import codex_service
    from minimax_code.ipc.protocol import RunStartParams

    await history(native, {"process_execution_owner": internal_profile()})
    dispatch = AsyncMock()
    monkeypatch.setattr(codex_service, "start_codex_run", dispatch)
    with pytest.raises(HandlerError) as caught:
        await handlers_runs._start_codex_run(RunStartParams(
            session_id="one", engine="codex", content="work", idempotency_key="once"),
            Context(native.server))
    assert caught.value.data["reason"] == "restricted_execution_session"
    dispatch.assert_not_awaited()


async def test_trusted_profile_is_an_actual_spec_and_still_validates_all_history(native):
    data = public_profile(native.root, session_id="one")
    spec = AgentExecutionSpec(data)
    # A server-owned capture may start a new execution session without history.
    await host_session_gate.require_chat_session("one", internal_agent=spec)
    with pytest.raises(HandlerError):
        await host_session_gate.require_chat_session("one", internal_agent=True)
    await history(native, {"agent_execution": data})
    await host_session_gate.require_chat_session("one", internal_agent=spec)
    await history(native, {"process_execution_owner": internal_profile()})
    with pytest.raises(HandlerError) as caught:
        await host_session_gate.require_chat_session("one", internal_agent=spec)
    assert caught.value.data["reason"] == "internal_process_recovery_unsupported"


@pytest.mark.parametrize("field,value", [
    ("execution_target", HOST_TARGET), ("model_snapshot", {"model": "changed"}),
    ("model_provider_fingerprint", "changed"), ("allowlist", ["write_file"]),
    ("tool_identity", "changed"), ("permission_rules", [{"action": "allow"}]),
    ("intent", {"request": "changed"}), ("source_session_id", "two"),
])
async def test_trusted_profile_cannot_ignore_frozen_durable_authority(native, field, value):
    data = public_profile(native.root, session_id="one")
    await history(native, {"agent_execution": data})
    changed = copy.deepcopy(data)
    changed[field] = value
    if field == "execution_target":
        from minimax_code.process_types import canonical_hash
        changed["execution_target_hash"] = canonical_hash(value)
        changed["execution_installation_id"] = None
    changed["digest"] = _digest({key: value for key, value in changed.items() if key != "digest"})
    # The constructor rejects invalid L1 tools/intent immediately. Mutate an
    # owned valid spec to exercise the gate's independent snapshot validation.
    spec = AgentExecutionSpec(copy.deepcopy(data))
    spec.data.update(changed)
    with pytest.raises(HandlerError) as caught:
        await host_session_gate.require_chat_session("one", internal_agent=spec)
    assert caught.value.data["reason"] == "session_execution_binding_invalid"


async def test_trusted_public_host_preparation_transition_preserves_frozen_profile(native):
    data = public_profile(native.root, session_id="one", target=HOST_TARGET)
    prepared = {**data, "prepared": True, "directory_identity": [1, 3]}
    prepared["digest"] = _digest({key: value for key, value in prepared.items() if key != "digest"})
    await history(native, {"agent_execution": prepared})
    await host_session_gate.require_chat_session("one", internal_agent=AgentExecutionSpec(data))
    await host_session_gate.require_chat_session("one", internal_agent=AgentExecutionSpec(prepared))
    changed = {**prepared, "directory_identity": [1, 4]}
    changed["digest"] = _digest({key: value for key, value in changed.items() if key != "digest"})
    with pytest.raises(HandlerError):
        await host_session_gate.require_chat_session("one", internal_agent=AgentExecutionSpec(changed))


async def test_trusted_host_profile_accepts_unbound_terminal_tracking(native):
    await history(native, {})
    spec = AgentExecutionSpec(public_profile(native.root, session_id="one", target=HOST_TARGET))
    await history(native, {"agent_execution": spec.snapshot()})
    await host_session_gate.require_chat_session("one", internal_agent=spec)


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("other", ["unbound", "internal", "foreign", "malformed", "dual"])
async def test_trusted_profile_checks_all_mixed_history_in_either_order(native, reverse, other):
    data = public_profile(native.root, session_id="one")
    foreign = {**data, "intent": {"request": "other"}}
    foreign["digest"] = _digest({k: v for k, v in foreign.items() if k != "digest"})
    other_metadata = {
        "unbound": {}, "internal": {"process_execution_owner": internal_profile()},
        "foreign": {"agent_execution": foreign}, "malformed": {"agent_execution": None},
        "dual": {"agent_execution": data, "process_execution_owner": None},
    }[other]
    rows = [{"agent_execution": data}, {}, other_metadata]
    for metadata in reversed(rows) if reverse else rows:
        await history(native, metadata)
    if other == "unbound":
        await host_session_gate.require_chat_session("one", internal_agent=AgentExecutionSpec(data))
        # The ordinary host entry still sees the restricted binding.
        with pytest.raises(HandlerError):
            await host_session_gate.require_chat_session("one")
    else:
        with pytest.raises(HandlerError) as caught:
            await host_session_gate.require_chat_session("one", internal_agent=AgentExecutionSpec(data))
        assert caught.value.data["reason"] in {
            "internal_process_recovery_unsupported", "session_execution_binding_invalid"}


@pytest.mark.parametrize("version", [1, 2, 3])
@pytest.mark.parametrize("recovery", ["continue", "resume"])
async def test_actual_host_agent_recovery_accepts_real_terminal_tracking(
    agent_env, monkeypatch, version, recovery,
):
    from test_agent_execution import tool_response
    from test_budget_transport import response

    from minimax_code.ipc import handlers_terminal

    state = agent_env
    capture = AgentExecutionSpec.capture.__func__

    async def legacy(cls, *args, **kwargs):
        spec, model = await capture(cls, *args, **kwargs)
        data = spec.snapshot()
        if version < 3:
            data["version"] = version
            for key in ("execution_target", "execution_target_hash", "execution_installation_id"):
                data.pop(key)
        if version == 1:
            data.pop("skill_bindings")
        data["digest"] = _digest({k: v for k, v in data.items() if k != "digest"})
        return cls(data, spec.tools), model

    monkeypatch.setattr(AgentExecutionSpec, "capture", classmethod(legacy))
    await state.agents.upsert(name="worker", system_prompt="Saved block",
                              tool_allowlist=["read_file"], max_iterations=1)
    state.wire = lambda _: tool_response("read_file", {"path": "hello.txt"})
    initial = await invoke_agent(state)
    initial.reply_error.assert_not_awaited()
    receipt = initial.reply.await_args.args[0]
    assert receipt["truncated"]
    terminal = await handlers_terminal.start_terminal_command(
        command="echo tracked", cwd=receipt["workspace"]["path"], timeout_s=5,
        chat_session_id=receipt["session_id"], emit=AsyncMock())
    await terminal.task
    assert terminal.status == "completed"
    assert "agent_execution" not in (await state.dao.get_run(terminal.run_id))["metadata"]
    state.wire = lambda _: response()
    ctx = Context(state.server)
    if recovery == "continue":
        await builtins.handle_agent_continue_run(
            {"session_id": receipt["session_id"], "run_id": receipt["run_id"]}, ctx)
        ctx.reply_error.assert_not_awaited()
        result = ctx.reply.await_args.args[0]
    else:
        await state.dao.update_run_status(receipt["run_id"], status="cancelled")
        await state.server._handlers["run.resume"](
            {"run_id": receipt["run_id"], "idempotency_key": "terminal-recovery",
             "reviewed": True}, ctx)
        ctx.reply_error.assert_not_awaited()
        run = ctx.reply.await_args.args[0]["run"]
        job = get_run_service(state.server).get_job(run["id"])
        await get_run_service(state.server).wait(job)
        result = await state.dao.result_payload(run["id"])
    assert result["status"] == "completed"
    assert result["workspace"]["path"] == receipt["workspace"]["path"]
