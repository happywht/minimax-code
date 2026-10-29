from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from test_interjection import _EchoTool, _ScriptedLLM, _tool_call
from test_run_service import Context, native  # noqa: F401

from minimax_code.agent.core import AgentConfig, AgentCore
from minimax_code.agent.llm import StreamChunk
from minimax_code.agent.tools import ToolRegistry
from minimax_code.ipc.builtins import handle_agent_send_message
from minimax_code.ipc.handlers_runs import register_run_handlers
from minimax_code.run_steer import SteerController
from minimax_code.runs import RunJob, get_run_service


async def test_native_steer_retries_and_is_closed_if_final_answer_arrives(native):  # noqa: F811
    register_run_handlers(native.server)
    start = Context(native.server)
    await handle_agent_send_message({"session_id": "one", "content": "first"}, start, wait=False)
    await asyncio.wait_for(native.started.get(), 3)
    run = start.reply.await_args.args[0]["run"]
    request = {"run_id": run["id"], "session_id": "one", "idempotency_key": "a", "text": "extra"}
    handler = native.server._handlers["run.steer"]
    first, duplicate, wrong = Context(native.server), Context(native.server), Context(native.server)
    await handler(request, first)
    assert first.reply.await_args.args[0]["step"]["status"] == "pending"
    await handler(request, duplicate)
    assert duplicate.reply.await_args.args[0]["step"]["id"] == first.reply.await_args.args[0]["step"]["id"]
    await handler({**request, "session_id": "two"}, wrong)
    assert wrong.reply_error.await_count == 1
    native.release.set()
    job = get_run_service(native.server).get_job(run["id"])
    await get_run_service(native.server).wait(job)
    retry = Context(native.server)
    await handler(request, retry)
    assert retry.reply.await_args.args[0]["step"]["status"] == "failed"


async def test_tool_batch_finishes_before_steer_enters_next_model_context(native):  # noqa: F811
    run = await native.dao.create_run(session_id="one", status="running")
    job = RunJob("one", run_id=run["id"], dao=native.dao)
    controller = SteerController(job, AsyncMock())
    llm = _ScriptedLLM([[StreamChunk(tool_call_deltas=[_tool_call("echo", {"text": "hi"})],
                                     finish_reason="tool_calls")],
                        [StreamChunk(delta="done", finish_reason="stop")]])
    registry = ToolRegistry()
    registry.register(_EchoTool())
    core = AgentCore(llm=llm, registry=registry, config=AgentConfig())
    core.on_safe_interjections = controller.drain
    async def on_call(_call):
        await controller.accept("key", "extra")
    core.on_tool_call = on_call
    await core.run(session_id="one", user_message="start")
    assert all("extra" not in str(message) for message in llm.captured[0])
    assert any("extra" in str(message) for message in llm.captured[1])
    assert (await native.dao.list_steps(run["id"]))[0]["status"] == "completed"


async def test_cancel_closes_pending_input_and_rejects_new_input(native):  # noqa: F811
    register_run_handlers(native.server)
    ctx = Context(native.server)
    await handle_agent_send_message({"session_id": "one", "content": "work"}, ctx, wait=False)
    await asyncio.wait_for(native.started.get(), 3)
    run = ctx.reply.await_args.args[0]["run"]
    handler = native.server._handlers["run.steer"]
    request = {"run_id": run["id"], "session_id": "one", "idempotency_key": "cancel", "text": "extra"}
    accepted = Context(native.server)
    await handler(request, accepted)
    service = get_run_service(native.server)
    job = service.get_job(run["id"])
    service.cancel_run(run["id"])
    try:
        await service.wait(job)
    except asyncio.CancelledError:
        pass
    step = await native.dao.get_step(accepted.reply.await_args.args[0]["step"]["id"])
    assert step["status"] in {"failed", "cancelled"}
    rejected = Context(native.server)
    await handler({**request, "idempotency_key": "new"}, rejected)
    assert rejected.reply_error.await_count == 1


async def test_restart_recovery_marks_pending_input_unconfirmed(native):  # noqa: F811
    run = await native.dao.create_run(session_id="one", status="running")
    controller = SteerController(RunJob("one", run_id=run["id"], dao=native.dao), AsyncMock())
    step = await controller.accept("key", "extra")
    await native.dao.recover_orphans()
    recovered = await native.dao.get_step(step["id"])
    assert recovered["status"] == "failed"
    assert recovered["payload"]["text"] == "extra"
