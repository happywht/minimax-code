from __future__ import annotations

import asyncio
import io
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from minimax_code.config import Config
from minimax_code.ipc import builtins
from minimax_code.ipc.server import IPCServer
from minimax_code.runs import RunService, get_run_service
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase


class Context:
    def __init__(self, server):
        self.server = server
        self.reply = AsyncMock()
        self.reply_error = AsyncMock()
        self.emit = AsyncMock()


@pytest.fixture
async def native(monkeypatch, tmp_path):
    from minimax_code import agent, app

    db = AsyncDatabase(tmp_path / "runs.db")
    await db.connect()
    await db.migrate()
    sessions = SessionsDAO(db)
    for sid in ("one", "two"):
        await sessions.create(id=sid, title=sid, workspace_path=str(tmp_path))
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    monkeypatch.setattr(app, "init_runtime", AsyncMock())
    monkeypatch.setattr(app, "get_db", lambda: db)
    monkeypatch.setattr(app, "get_sessions_dao", lambda: sessions)
    monkeypatch.setattr(app, "ensure_codebase_indexer", lambda *args: None)
    monkeypatch.setattr(app, "get_codebase_indexer", lambda: None)
    monkeypatch.setattr(app, "ensure_hook_manager", lambda: None)
    monkeypatch.setattr(app, "ensure_telemetry_engine", lambda: None)
    monkeypatch.setattr(app, "get_subagent_llm", lambda: SimpleNamespace(default_model="test", mock=True))
    monkeypatch.setattr(builtins, "_build_system_prompt_extra", AsyncMock(return_value=None))
    monkeypatch.setattr("minimax_code.ipc.handlers_permissions._ensure_permission_store", AsyncMock(return_value=None))
    server = IPCServer(Config(), stdin=io.StringIO(), stdout=io.StringIO())
    cores = []
    started = asyncio.Queue()
    release = asyncio.Event()

    class Core:
        def __init__(self, **kwargs):
            self.config = kwargs["config"]
            self.cancelled = False
            cores.append(self)

        def cancel(self):
            self.cancelled = True

        async def run(self, *, session_id, user_message):
            started.put_nowait((session_id, user_message))
            if state.tool:
                await state.tool()
            await release.wait()
            return SimpleNamespace(final_text="done", iterations=1, usage={}, truncated=False,
                                   cancelled=False, compactions=0)

    monkeypatch.setattr(agent, "AgentCore", Core)
    state = SimpleNamespace(db=db, dao=AgentRunsDAO(db), server=server, cores=cores,
                            started=started, release=release, root=tmp_path, tool=None)
    try:
        yield state
    finally:
        await get_run_service(server).shutdown()
        await db.close()


async def send(state, session="one", content="work"):
    ctx = Context(state.server)
    await builtins.handle_agent_send_message({"session_id": session, "content": content}, ctx)
    return ctx


async def test_disconnected_waiter_does_not_cancel_native_run(native):
    task = asyncio.create_task(send(native))
    await asyncio.wait_for(native.started.get(), 2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not native.cores[0].cancelled
    jobs = get_run_service(native.server)._jobs["one"]
    assert len(jobs) == 1 and not jobs[0].task.done()
    native.release.set()
    await asyncio.wait_for(get_run_service(native.server).wait(jobs[0]), 2)
    rows = await native.dao.list_runs(session_id="one")
    assert len(rows) == 1 and rows[0]["status"] == "completed"


async def test_cancelled_http_request_leaves_accepted_native_job_running(native, monkeypatch):
    import httpx

    from minimax_code.http_server import build_app

    monkeypatch.setenv("MINIMAX_CODE_AUTH_TOKEN", "test-run-service-token")
    native.server.register("agent.send_message", builtins.handle_agent_send_message)
    transport = httpx.ASGITransport(app=build_app(native.server))
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        request = asyncio.create_task(client.post("/rpc", headers={"Authorization": "Bearer test-run-service-token"},
            json={"jsonrpc": "2.0", "id": 1, "method": "agent.send_message",
                  "params": {"session_id": "one", "content": "detached HTTP"}}))
        await asyncio.wait_for(native.started.get(), 3)
        request.cancel()
        with pytest.raises(asyncio.CancelledError):
            await request
        job = get_run_service(native.server)._jobs["one"][0]
        assert not job.task.done()
        native.release.set()
        await asyncio.wait_for(get_run_service(native.server).wait(job), 3)
        assert (await native.dao.list_runs())[0]["status"] == "completed"


async def test_same_session_serializes_setup_and_persistence_but_other_sessions_run(native):
    first = asyncio.create_task(send(native, content="first"))
    assert await asyncio.wait_for(native.started.get(), 3) == ("one", "first")
    second = asyncio.create_task(send(native, content="second"))
    other = asyncio.create_task(send(native, "two", "other"))
    assert await asyncio.wait_for(native.started.get(), 2) == ("two", "other")
    assert len(await native.dao.list_runs(session_id="one")) == 1
    native.release.set()
    await asyncio.wait_for(asyncio.gather(first, second, other), 3)
    assert await asyncio.wait_for(native.started.get(), 3) == ("one", "second")
    assert all(row["status"] == "completed" for row in await native.dao.list_runs())
    assert get_run_service(native.server)._jobs == {}


async def test_cancel_keeps_lease_until_cleanup_and_does_not_revive_queued_work():
    service = RunService()
    started = asyncio.Event()
    cleaning = asyncio.Event()
    release = asyncio.Event()
    order = []

    async def first(job):
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            order.append("cleaned")

    async def later(job):
        order.append("later")

    a = service.submit("s", first)
    b = service.submit("s", later)
    await started.wait()
    assert service.cancel_session("s") == 2
    await cleaning.wait()
    c = service.submit("s", later)
    await asyncio.sleep(0)
    assert order == []
    release.set()
    await asyncio.gather(service.wait(a), service.wait(b), service.wait(c), return_exceptions=True)
    assert order == ["cleaned", "later"]
    await service.shutdown()


async def test_cancel_native_persists_steps_and_repeated_cancel_is_idempotent(native):
    task = asyncio.create_task(send(native))
    await asyncio.wait_for(native.started.get(), 3)
    run = (await native.dao.list_runs(session_id="one"))[0]
    step = await native.dao.create_step(run_id=run["id"], session_id="one", kind="tool_call", title="running")
    cancel = Context(native.server)
    await builtins.handle_agent_cancel({"session_id": "one"}, cancel)
    await builtins.handle_agent_cancel({"session_id": "one"}, cancel)
    ctx = await asyncio.wait_for(task, 2)
    assert ctx.reply.call_args.args[0]["cancelled"] is True
    assert native.cores[0].cancelled
    assert (await native.dao.get_run(run["id"]))["status"] == "cancelled"
    assert (await native.dao.get_step(step["id"]))["status"] == "cancelled"
    events = await native.dao.events(run["id"])
    assert [event["type"] for event in events["events"]][-2:] == ["step_updated", "status"]
    assert "one" not in builtins._ACTIVE_RUNS


async def test_shutdown_drains_before_storage_closes(native):
    task = asyncio.create_task(send(native))
    await asyncio.wait_for(native.started.get(), 3)
    service = get_run_service(native.server)
    await service.shutdown()
    await task
    assert (await native.dao.list_runs())[0]["status"] == "cancelled"
    assert not service._jobs
    with pytest.raises(RuntimeError, match="shutting down"):
        service.submit("one", AsyncMock())


async def test_native_cancel_reaps_a_real_process_before_recording_cancelled(native):
    from minimax_code.execution import capture_output, spawn_exec

    process = None
    spawned = asyncio.Event()

    async def tool():
        nonlocal process
        process = await spawn_exec(sys.executable, "-c", "import time; time.sleep(60)",
                                   stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        spawned.set()
        await capture_output(process, timeout=90)

    native.tool = tool
    task = asyncio.create_task(send(native))
    await asyncio.wait_for(spawned.wait(), 5)
    await builtins.handle_agent_cancel({"session_id": "one"}, Context(native.server))
    await asyncio.wait_for(task, 5)
    assert process.returncode is not None
    assert (await native.dao.list_runs())[0]["status"] == "cancelled"


async def test_cancellation_reply_names_the_latest_assistant_segment(native):
    emitted = asyncio.Event()

    async def tool():
        core = native.cores[0]
        await core.on_chunk("before tool", False)
        await core.on_tool_call({"id": "call", "name": "read_file", "args": {"path": "a"}})
        await core.on_chunk("after tool", False)
        emitted.set()

    native.tool = tool
    task = asyncio.create_task(send(native))
    await asyncio.wait_for(emitted.wait(), 2)
    job = get_run_service(native.server)._jobs["one"][0]
    latest_id = job.message_id
    assert latest_id != job.record.assistant_message_id
    await builtins.handle_agent_cancel({"session_id": "one"}, Context(native.server))
    ctx = await task
    assert ctx.reply.call_args.args[0]["message_id"] == latest_id


async def test_storage_failure_prevents_model_and_hook_execution(native):
    await native.db.execute("""CREATE TRIGGER fail_run BEFORE INSERT ON agent_runs
        BEGIN SELECT RAISE(FAIL, 'disk unavailable'); END""")
    ctx = await send(native)
    assert ctx.reply_error.call_args.args[0] == -32004
    assert native.started.empty()
    assert await native.dao.list_runs() == []
    assert "one" not in builtins._ACTIVE_RUNS
    assert not native.server._permission_gaters


async def test_setup_exception_releases_session_routes_and_allows_next_run(native, monkeypatch):
    monkeypatch.setattr(builtins, "_build_system_prompt_extra", AsyncMock(side_effect=RuntimeError("setup failed")))
    with pytest.raises(RuntimeError, match="setup failed"):
        await send(native)
    assert not native.server._permission_gaters
    assert not get_run_service(native.server)._jobs
    monkeypatch.setattr(builtins, "_build_system_prompt_extra", AsyncMock(return_value=None))
    native.release.set()
    ctx = await send(native)
    ctx.reply_error.assert_not_awaited()
    assert ctx.reply.call_args.args[0]["text"] == "done"


async def test_committed_completion_wins_over_cancel_during_optional_memory(native, monkeypatch):
    extracting = asyncio.Event()

    async def memory(self, result):
        extracting.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(builtins._RunRecorder, "_persist_memories", memory)
    native.release.set()
    task = asyncio.create_task(send(native))
    await asyncio.wait_for(extracting.wait(), 2)
    await builtins.handle_agent_cancel({"session_id": "one"}, Context(native.server))
    ctx = await asyncio.wait_for(task, 2)
    assert ctx.reply.call_args.args[0]["cancelled"] is False
    assert ctx.reply.call_args.args[0]["text"] == "done"
    assert (await native.dao.list_runs())[0]["status"] == "completed"


async def test_termination_rolls_back_when_event_persistence_fails(native):
    run = await native.dao.create_run(session_id="one")
    step = await native.dao.create_step(run_id=run["id"], session_id="one", kind="thought")
    await native.db.execute("""CREATE TRIGGER fail_event BEFORE INSERT ON run_events
        BEGIN SELECT RAISE(FAIL, 'disk unavailable'); END""")
    with pytest.raises(Exception, match="disk unavailable"):
        await native.dao.terminate_run(run["id"], status="cancelled", error="stop")
    assert (await native.dao.get_run(run["id"]))["status"] == "running"
    assert (await native.dao.get_step(step["id"]))["status"] == "running"


async def test_cancelling_before_first_task_instruction_cleans_queue():
    service = RunService()
    operation = AsyncMock()
    job = service.submit("s", operation)
    service.cancel_session("s")
    with pytest.raises(asyncio.CancelledError):
        await service.wait(job)
    operation.assert_not_awaited()
    assert not service._jobs and not service._locks
