"""Durable spawn compatibility: aliases, validation, cancellation and events."""
from __future__ import annotations

import asyncio

import pytest
from test_agent_execution import agent_env as agent_env
from test_agent_execution import invoke
from test_run_service import Context
from test_skill_execution import skill_env as skill_env

from minimax_code.runs import get_run_service


async def test_spawn_uses_durable_server_id_and_emits_real_run_events(agent_env, monkeypatch):
    from minimax_code.orchestrator.subagent import SubAgentRuntime
    async def forbidden(*args, **kwargs):
        pytest.fail("Public root must not invoke another Runtime/AgentCore loop")
    monkeypatch.setattr(SubAgentRuntime, "invoke", forbidden)
    state = agent_env
    ctx = await invoke(state, "agent.spawn_subagent", run_id="client-association", display_name="Worker card")
    ctx.reply_error.assert_not_awaited()
    result = ctx.reply.await_args.args[0]
    assert result["run_id"] == result["agent_run_id"] != "client-association"
    assert result["client_run_id"] == "client-association" and result["display_name"] == "Worker card"
    assert result["session_id"] != "one" and result["parent_session_id"] == "one"
    events = [call.args[0] for call in ctx.emit.await_args_list]
    assert "run.created" in events and "run.completed" in events and "agent.message_chunk" in events


@pytest.mark.parametrize("missing", ["name", "session_id", "idempotency_key"])
async def test_spawn_rejects_missing_explicit_intent(agent_env, missing):
    state = agent_env
    params = {"name":"worker","request":"inspect","session_id":"one","idempotency_key":"key"}
    params.pop(missing)
    ctx = Context(state.server)
    await state.server._handlers["agent.spawn_subagent"](params, ctx)
    assert ctx.reply_error.await_args.args[0] == -32602 and not state.calls


async def test_spawn_rejects_unknown_agent_without_creating_fake_run(agent_env):
    ctx = await invoke(agent_env, "agent.spawn_subagent", name="missing")
    assert ctx.reply_error.await_args.args[0] == -32602
    assert not await agent_env.dao.list_runs() and not agent_env.calls


async def test_spawn_accepts_registry_id_and_parent_source_alias(agent_env):
    state = agent_env
    ctx = Context(state.server)
    await state.server._handlers["agent.spawn_subagent"]({"name":state.agent["id"],"request":"inspect",
        "parent_session_id":"one","idempotency_key":"legacy-id"}, ctx)
    ctx.reply_error.assert_not_awaited()
    assert ctx.reply.await_args.args[0]["agent_id"] == state.agent["id"]
    assert ctx.reply.await_args.args[0]["agent"] == "worker"


async def test_cancel_compatibility_is_request_ack_then_durable_terminal(agent_env):
    state = agent_env
    state.release.clear()
    ctx = await invoke(state, "agent.spawn_subagent", admission_only=True)
    run = ctx.reply.await_args.args[0]["run"]
    await asyncio.wait_for(state.entered.wait(),5)
    cancel = Context(state.server)
    await state.server._handlers["agent.cancel_subagent"]({"run_id":run["id"]},cancel)
    assert cancel.reply.await_args.args[0]["cancel_requested"]
    job = get_run_service(state.server).get_job(run["id"])
    await asyncio.gather(job.task,return_exceptions=True)
    assert (await state.dao.get_run(run["id"]))["status"] == "cancelled"
    assert (await state.dao.result_payload(run["id"]))["partial"]
