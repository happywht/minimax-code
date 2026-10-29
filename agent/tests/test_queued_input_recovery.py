from __future__ import annotations

import asyncio

from test_run_service import Context, native  # noqa: F401

from minimax_code.ipc.builtins import handle_agent_send_message
from minimax_code.ipc.handlers_runs import register_run_handlers
from minimax_code.runs import get_run_service


async def test_queued_input_is_durable_and_executes_in_order(native):  # noqa: F811
    register_run_handlers(native.server)
    first = Context(native.server)
    await handle_agent_send_message({"session_id": "one", "content": "first",
                                     "idempotency_key": "first"}, first, wait=False)
    assert await asyncio.wait_for(native.started.get(), 3) == ("one", "first")
    second = Context(native.server)
    await handle_agent_send_message({"session_id": "one", "content": "second full text",
                                     "idempotency_key": "second"}, second, wait=False)
    run = second.reply.await_args.args[0]["run"]
    assert run["status"] == "queued"
    ctx = Context(native.server)
    await native.server._handlers["run.steps"]({"run_id": run["id"]}, ctx)
    assert ctx.reply.await_args.args[0]["queued_input"] == {"text": "second full text", "truncated": False}
    retry = Context(native.server)
    await handle_agent_send_message({"session_id": "one", "content": "second full text",
                                     "idempotency_key": "second"}, retry, wait=False)
    assert retry.reply.await_args.args[0]["run"]["id"] == run["id"]
    native.release.set()
    assert await asyncio.wait_for(native.started.get(), 3) == ("one", "second full text")
    job = get_run_service(native.server).get_job(run["id"])
    if job:
        await get_run_service(native.server).wait(job)
    ctx = Context(native.server)
    await native.server._handlers["run.steps"]({"run_id": run["id"]}, ctx)
    assert ctx.reply.await_args.args[0]["queued_input"] is None
