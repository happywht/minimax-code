"""RPC maintenance admission covers both transports and in-flight handlers."""
import asyncio
import io
import json

import pytest

from minimax_code.config import Config
from minimax_code.ipc.server import IPCServer
from minimax_code.runtime.maintenance import MaintenanceIncomplete, drain_for_maintenance


def server():
    return IPCServer(Config(), stdin=io.StringIO(), stdout=io.StringIO())


def fake_producers(monkeypatch):
    from minimax_code import app, runs, scheduler
    from minimax_code.ipc import handlers_terminal

    async def noop(*args, **kwargs):
        pass

    class Service:
        drain = staticmethod(noop)

    monkeypatch.setattr(scheduler, "quiesce_scheduler", noop)
    monkeypatch.setattr(runs, "get_run_service", lambda server: Service())
    monkeypatch.setattr(app, "begin_codebase_shutdown", lambda: None)
    monkeypatch.setattr(app, "drain_codebase_shutdown", noop)
    monkeypatch.setattr(handlers_terminal, "drain_terminal_sessions", noop)


async def test_drain_waits_for_notification_and_closes_both_transports(monkeypatch):
    fake_producers(monkeypatch)
    rpc = server()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def work(params, ctx):
        calls.append(params)
        entered.set()
        await release.wait()
        return "done"

    rpc.register("work", work)
    active = asyncio.create_task(rpc.handle_request({"method": "work", "params": "old"}))
    await entered.wait()
    drain = asyncio.create_task(drain_for_maintenance(rpc))
    await asyncio.sleep(0)
    assert rpc._rpc_admission.closed
    assert not drain.done()
    rejected = await rpc.handle_request({"id": 1, "method": "work", "params": "http"})
    assert rejected["error"]["code"] == -32000
    await rpc._handle_line(json.dumps({"id": 2, "method": "work", "params": "stdio"}))
    assert json.loads(rpc.stdout.getvalue())["error"]["code"] == -32000
    await rpc._handle_line(json.dumps({"method": "work", "params": "notification"}))
    assert calls == ["old"]
    release.set()
    await active
    assert not (await drain).upgrade_ready


async def test_cancellation_releases_admission_and_waiter_does_not_reopen(monkeypatch):
    fake_producers(monkeypatch)
    rpc = server()
    entered = asyncio.Event()

    async def work(params, ctx):
        entered.set()
        await asyncio.Event().wait()

    rpc.register("work", work)
    active = asyncio.create_task(rpc.handle_request({"id": 1, "method": "work"}))
    await entered.wait()
    waiter = asyncio.create_task(drain_for_maintenance(rpc))
    await asyncio.sleep(0)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    assert rpc._rpc_admission.closed
    active.cancel()
    with pytest.raises(asyncio.CancelledError):
        await active
    assert not (await drain_for_maintenance(rpc)).upgrade_ready


async def test_handler_and_child_cannot_start_self_deadlocking_drain():
    rpc = server()

    async def work(params, ctx):
        with pytest.raises(MaintenanceIncomplete, match="admitted RPC handler"):
            await drain_for_maintenance(rpc)
        child = asyncio.create_task(drain_for_maintenance(rpc))
        with pytest.raises(MaintenanceIncomplete, match="admitted RPC handler"):
            await child
        return "ok"

    rpc.register("work", work)
    response = await rpc.handle_request({"id": 1, "method": "work"})
    assert response["result"] == "ok"
    assert not rpc._rpc_admission.closed


async def test_nested_call_rejected_after_close():
    rpc = server()
    entered, release = asyncio.Event(), asyncio.Event()

    async def outer(params, ctx):
        entered.set()
        await release.wait()
        return await rpc.handle_request({"id": 2, "method": "inner"})

    async def inner(params, ctx):
        pytest.fail("nested call must not enter after closure")

    rpc.register("outer", outer)
    rpc.register("inner", inner)
    active = asyncio.create_task(rpc.handle_request({"id": 1, "method": "outer"}))
    await entered.wait()
    rpc._rpc_admission.close()
    release.set()
    response = await active
    assert response["result"]["error"]["code"] == -32000
    await asyncio.wait_for(rpc._rpc_admission.wait_empty(), 1)

async def test_controls_can_release_work_and_are_drained_before_return(monkeypatch):
    fake_producers(monkeypatch)
    rpc = server()
    entered, release, control_entered, control_release = (asyncio.Event() for _ in range(4))

    async def work(params, ctx):
        entered.set()
        await release.wait()

    async def resolve(params, ctx):
        control_entered.set()
        release.set()
        await control_release.wait()
        return "resolved"

    rpc.register("work", work)
    rpc.register("permission.resolve", resolve)
    active = asyncio.create_task(rpc.handle_request({"id": 1, "method": "work"}))
    await entered.wait()
    first = asyncio.create_task(drain_for_maintenance(rpc))
    await asyncio.sleep(0)
    second = asyncio.create_task(drain_for_maintenance(rpc))
    control = asyncio.create_task(rpc.handle_request({"id": 2, "method": "permission.resolve"}))
    await control_entered.wait()
    await active
    # The drain cannot return while the accepted control is still running.
    await asyncio.sleep(0)
    assert not first.done()
    control_release.set()
    assert (await control)["result"] == "resolved"
    assert await first is await second
    rejected = await rpc.handle_request({"id": 3, "method": "permission.resolve"})
    assert rejected["error"]["code"] == -32000

async def test_maintenance_status_does_not_initialize_runtime(monkeypatch):
    from minimax_code import app
    from minimax_code.ipc.handlers_runs import register_run_handlers
    from minimax_code.ipc.handlers_terminal import register_terminal_handlers

    rpc = server()
    register_run_handlers(rpc)
    register_terminal_handlers(rpc)
    monkeypatch.setattr(app, "get_db", lambda: None)

    async def forbidden():
        pytest.fail("maintenance read/cancel must not initialize the runtime")

    monkeypatch.setattr(app, "init_runtime", forbidden)
    rpc._rpc_admission.close()
    for method, params in [("run.list", {}), ("run.get", {"run_id": "x"}),
                           ("run.cancel", {"run_id": "x"})]:
        response = await rpc.handle_request({"id": 1, "method": method, "params": params})
        assert response["error"]["message"] == "storage layer is not available"
    response = await rpc.handle_request({"id": 2, "method": "terminal.list", "params": {}})
    assert "sessions" in response["result"]

async def test_preexisting_status_initialization_finishes_before_producer_drain(monkeypatch):
    from minimax_code import app
    from minimax_code.ipc.handlers_runs import register_run_handlers

    fake_producers(monkeypatch)
    rpc = server()
    register_run_handlers(rpc)
    initializing, release, shutdown_started = (asyncio.Event() for _ in range(3))
    monkeypatch.setattr(app, "get_db", lambda: None)

    async def init():
        initializing.set()
        await release.wait()

    monkeypatch.setattr(app, "init_runtime", init)
    monkeypatch.setattr(app, "begin_codebase_shutdown", shutdown_started.set)
    query = asyncio.create_task(rpc.handle_request({"id": 1, "method": "run.list", "params": {}}))
    await initializing.wait()
    drain = asyncio.create_task(drain_for_maintenance(rpc))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert rpc._rpc_admission.closed
    assert not shutdown_started.is_set()
    release.set()
    response = await query
    assert response["error"]["message"] == "storage layer is not available"
    await drain
    assert shutdown_started.is_set()
