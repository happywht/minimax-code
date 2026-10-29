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
            self.llm = kwargs["llm"]
            self.budget = None  # real AgentCore always sets this; receipt reads it
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


async def test_continuation_retries_join_one_worker_and_replay_after_restart(native, monkeypatch):
    from minimax_code.model_snapshot import ModelSnapshot

    source = await native.dao.create_run(session_id="one", mode="plan", status="completed",
        metadata={"truncated": True, "model_snapshot": ModelSnapshot.capture().metadata(),
                  "workspace_snapshot": str(native.root)})
    params = {"session_id": "one", "run_id": source["id"]}
    first, retry = Context(native.server), Context(native.server)
    a = asyncio.create_task(builtins.handle_agent_continue_run(params, first))
    await asyncio.wait_for(native.started.get(), 2)
    b = asyncio.create_task(builtins.handle_agent_continue_run(params, retry))
    await asyncio.sleep(0)
    a.cancel()  # lost client connection must not duplicate or stop the continuation
    with pytest.raises(asyncio.CancelledError):
        await a
    native.release.set()
    await asyncio.wait_for(b, 3)
    retry.reply_error.assert_not_awaited()
    receipt = retry.reply.call_args.args[0]
    rows = await native.dao.list_runs(session_id="one")
    assert len(rows) == 2 and len(native.cores) == 1
    child = await native.dao.get_run(receipt["run_id"])
    assert child["resumed_from_id"] == source["id"] and child["mode"] == "plan"
    assert child["metadata"]["model_snapshot"] == source["metadata"]["model_snapshot"]
    assert (await native.dao.get_run(source["id"]))["metadata"]["continued"] is True
    # A fresh service has no in-memory worker; the stored receipt is sufficient.
    monkeypatch.setattr(ModelSnapshot, "capture", lambda: (_ for _ in ()).throw(RuntimeError("provider offline")))
    restarted = Context(IPCServer(Config(), stdin=io.StringIO(), stdout=io.StringIO()))
    await builtins.handle_agent_continue_run(params, restarted)
    restarted.reply_error.assert_not_awaited()
    assert restarted.reply.call_args.args[0] == receipt
    assert len(native.cores) == 1


@pytest.mark.parametrize("changed", ["model", "workspace"])
async def test_continuation_rejects_changed_context_before_marking_source(native, changed):
    from minimax_code.model_snapshot import ModelSnapshot

    metadata = {"truncated": True, "model_snapshot": ModelSnapshot.capture().metadata(),
                "workspace_snapshot": str(native.root)}
    if changed == "model":
        metadata["model_snapshot"]["model"] = "original-model"
    else:
        metadata["workspace_snapshot"] = str(native.root / "original-workspace")
    source = await native.dao.create_run(session_id="one", status="completed", metadata=metadata)
    ctx = Context(native.server)
    await builtins.handle_agent_continue_run({"session_id": "one", "run_id": source["id"]}, ctx)
    ctx.reply.assert_not_awaited()
    ctx.reply_error.assert_awaited_once()
    assert len(await native.dao.list_runs()) == 1 and native.cores == []
    assert "continued" not in (await native.dao.get_run(source["id"]))["metadata"]


async def test_continuation_budget_admission_and_receipt_replay(native, monkeypatch):
    from minimax_code.model_snapshot import ModelSnapshot
    from minimax_code.storage.dao.messages import MessagesDAO

    source = await native.dao.create_run(session_id="one", status="completed",
        metadata={"truncated": True, "model_snapshot": ModelSnapshot.capture().metadata(),
                  "workspace_snapshot": str(native.root), "budget": {"limit_tokens": 500}})
    await MessagesDAO(native.db).create(id="spent", session_id="one", role="assistant",
                                       content="prior work", tokens_in=40, tokens_out=60)
    params = {"session_id": "one", "run_id": source["id"]}
    monkeypatch.setenv("MINIMAX_CODE_RUN_BUDGET_TOKENS", "500")
    # Actual governed sends, covered by real SDK tests, enforce the ledger.
    # This mock core makes no paid send and must not treat message sums as
    # an authoritative host baseline during continuation admission.
    monkeypatch.setenv("MINIMAX_CODE_DAILY_BUDGET_TOKENS", "1000")
    accepted = Context(native.server)
    task = asyncio.create_task(builtins.handle_agent_continue_run(params, accepted))
    await asyncio.wait_for(native.started.get(), 2)
    core = native.cores[0]
    assert core.config.run_budget_tokens == 500
    assert core.config.daily_budget_tokens == 1000
    assert core.config.daily_budget_baseline == 0
    native.release.set()
    await asyncio.wait_for(task, 3)
    accepted.reply_error.assert_not_awaited()
    receipt = accepted.reply.call_args.args[0]
    child = await native.dao.get_run(receipt["run_id"])
    assert child["metadata"]["budget"] == {
        "limit_tokens": 500, "daily_limit": 1000, "daily_baseline": 0}
    source = await native.dao.get_run(source["id"])
    assert child["metadata"]["budget_family_id"] == source["metadata"]["budget_family_id"]

    # Reading the existing receipt must not recheck today's cap or provider.
    monkeypatch.setenv("MINIMAX_CODE_DAILY_BUDGET_TOKENS", "1")
    monkeypatch.setattr(ModelSnapshot, "capture", lambda: (_ for _ in ()).throw(RuntimeError("offline")))
    retry = Context(IPCServer(Config(), stdin=io.StringIO(), stdout=io.StringIO()))
    await builtins.handle_agent_continue_run(params, retry)
    retry.reply_error.assert_not_awaited()
    assert retry.reply.call_args.args[0] == receipt
    assert len(await native.dao.list_runs()) == 2 and len(native.cores) == 1


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
    assert sorted(row["status"] for row in await native.dao.list_runs(session_id="one")) == ["queued", "running"]
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
    assert not getattr(native.server, "_permission_gaters", {})


async def test_setup_exception_releases_session_routes_and_allows_next_run(native, monkeypatch):
    monkeypatch.setattr(builtins, "_build_system_prompt_extra", AsyncMock(side_effect=RuntimeError("setup failed")))
    failed = await send(native)
    assert failed.reply_error.call_args.args[0] == -32603
    assert failed.reply_error.call_args.args[1] == "setup failed"
    assert not getattr(native.server, "_permission_gaters", {})
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


async def test_queued_native_request_keeps_model_captured_at_admission(native, monkeypatch):
    from minimax_code import app

    selected = {"client": SimpleNamespace(default_model="first", mock=True, provider_id="p1"),
                "effort": "low"}
    monkeypatch.setattr(app, "get_subagent_llm", lambda: selected["client"])
    monkeypatch.setattr(app, "get_reasoning_effort_override", lambda: selected["effort"])
    first = asyncio.create_task(send(native, content="first"))
    await asyncio.wait_for(native.started.get(), 2)
    selected.update(client=SimpleNamespace(default_model="queued", mock=True, provider_id="p2"), effort="high")
    expected = selected["client"]
    admitted = asyncio.Event()
    service = get_run_service(native.server)
    submit = service.submit

    def observed_submit(*args, **kwargs):
        job = submit(*args, **kwargs)
        admitted.set()
        return job

    monkeypatch.setattr(service, "submit", observed_submit)
    queued = asyncio.create_task(send(native, content="queued"))
    await asyncio.wait_for(admitted.wait(), 2)
    selected.update(client=SimpleNamespace(default_model="other", mock=True, provider_id="p3"), effort=None)
    other = asyncio.create_task(send(native, "two", "other"))
    assert await asyncio.wait_for(native.started.get(), 2) == ("two", "other")
    native.release.set()
    await asyncio.wait_for(asyncio.gather(first, queued, other), 3)
    assert [core.config.model for core in native.cores] == ["first", "other", "queued"]
    assert [core.config.reasoning_effort for core in native.cores] == ["low", None, "high"]
    assert native.cores[-1].llm is expected
    runs = await native.dao.list_runs(session_id="one")
    row = next(row for row in runs if row["title"] == "queued")
    assert row["metadata"]["model_snapshot"]["model"] == "queued"
    assert row["metadata"]["model_snapshot"]["provider_id"] == "p2"


async def run_rpc(native, method, params):
    from minimax_code.ipc.handlers_runs import register_run_handlers

    if "run.start" not in native.server._handlers:
        register_run_handlers(native.server)
    ctx = Context(native.server)
    await native.server._handlers[method](params, ctx)
    return ctx


async def test_duplicate_waiters_and_completed_retry_share_one_execution(native, monkeypatch):
    from minimax_code import app

    params = {"session_id": "one", "content": "write once", "idempotency_key": "same"}
    first, second = Context(native.server), Context(native.server)
    a = asyncio.create_task(builtins.handle_agent_send_message(params, first))
    await asyncio.wait_for(native.started.get(), 2)
    b = asyncio.create_task(builtins.handle_agent_send_message(params, second))
    native.release.set()
    await asyncio.wait_for(asyncio.gather(a, b), 3)
    assert first.reply.call_args.args[0] == second.reply.call_args.args[0]
    assert len(native.cores) == 1
    monkeypatch.setattr(app, "get_subagent_llm", lambda: None)
    retry = Context(native.server)
    await builtins.handle_agent_send_message(params, retry)
    assert retry.reply.call_args.args[0] == first.reply.call_args.args[0]
    conflict = Context(native.server)
    await builtins.handle_agent_send_message({**params, "content": "different"}, conflict)
    assert conflict.reply_error.call_args.args[0] == -32602
    assert len(native.cores) == 1


async def test_start_returns_receipt_and_targeted_cancel_preserves_other_work(native):
    first = await run_rpc(native, "run.start", {"session_id": "one", "content": "first", "idempotency_key": "first"})
    await asyncio.wait_for(native.started.get(), 2)
    a = first.reply.call_args.args[0]["run"]
    second = await run_rpc(native, "run.start", {"session_id": "one", "content": "second", "idempotency_key": "second"})
    b = second.reply.call_args.args[0]["run"]
    assert b["status"] == "queued"
    job = get_run_service(native.server).get_job(b["id"])
    cancelled = await run_rpc(native, "run.cancel", {"run_id": b["id"]})
    assert cancelled.reply.call_args.args[0]["requested"] is True
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(get_run_service(native.server).wait(job), 2)
    status = await run_rpc(native, "run.get", {"run_id": b["id"]})
    receipt = status.reply.call_args.args[0]
    assert receipt["run"]["status"] == "cancelled" and receipt["result"]["cancelled"] is True
    assert (await native.dao.get_run(a["id"]))["status"] == "running"
    native.release.set()
    await get_run_service(native.server).wait(get_run_service(native.server).get_job(a["id"]))
    assert len(native.cores) == 1


async def test_disconnect_during_admission_does_not_leave_a_stranded_queue_record(native, monkeypatch):
    committed, release = asyncio.Event(), asyncio.Event()
    original = AgentRunsDAO.admit_run

    async def delayed(self, **kwargs):
        result = await original(self, **kwargs)
        committed.set()
        await release.wait()
        return result

    monkeypatch.setattr(AgentRunsDAO, "admit_run", delayed)
    waiter = asyncio.create_task(send(native))
    await asyncio.wait_for(committed.wait(), 2)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    release.set()
    await asyncio.wait_for(native.started.get(), 2)
    native.release.set()
    job = get_run_service(native.server)._jobs["one"][0]
    await asyncio.wait_for(get_run_service(native.server).wait(job), 2)
    assert (await native.dao.list_runs())[0]["status"] == "completed"


async def test_queued_run_does_not_follow_later_workspace_rebinding(native):
    from minimax_code.workspace_ctx import current_root

    a, b = native.root / "a", native.root / "b"
    a.mkdir()
    b.mkdir()
    sessions = SessionsDAO(native.db)
    await sessions.update("one", workspace_path=str(a))
    roots = []

    async def record_root():
        roots.append(current_root())

    native.tool = record_root
    first = asyncio.create_task(send(native, content="first"))
    await asyncio.wait_for(native.started.get(), 2)
    accepted = await run_rpc(native, "run.start", {"session_id": "one", "content": "queued", "idempotency_key": "queue"})
    run = accepted.reply.call_args.args[0]["run"]
    job = get_run_service(native.server).get_job(run["id"])
    await sessions.update("one", workspace_path=str(b))
    native.release.set()
    await asyncio.wait_for(asyncio.gather(first, get_run_service(native.server).wait(job)), 3)
    assert roots == [a, a]
    assert (await native.dao.get_run(run["id"]))["metadata"]["workspace_snapshot"] == str(a)


async def test_resume_requires_review_and_creates_a_new_linked_run(native):
    from minimax_code.model_snapshot import ModelSnapshot

    old, _ = await native.dao.admit_run(session_id="one", request={"session_id": "one", "mode": "chat", "content": "original task"},
        request_key="old", title="old", metadata={"model_snapshot": ModelSnapshot.capture().metadata(), "workspace_snapshot": str(native.root)})
    await native.dao.claim_run(old["id"])
    await native.dao.create_step(run_id=old["id"], session_id="one", kind="tool_call", title="possibly applied write")
    await native.dao.recover_orphans()
    request = {"run_id": old["id"], "idempotency_key": "resume"}
    denied = await run_rpc(native, "run.resume", request)
    assert denied.reply_error.call_args.args[0] == -32602
    accepted = await run_rpc(native, "run.resume", {**request, "reviewed": True})
    run = accepted.reply.call_args.args[0]["run"]
    assert run["id"] != old["id"] and run["resumed_from_id"] == old["id"]
    _, content = await asyncio.wait_for(native.started.get(), 2)
    assert "original task" in content and "Verify which actions already took effect" in content
    job = get_run_service(native.server).get_job(run["id"])
    native.release.set()
    await asyncio.wait_for(get_run_service(native.server).wait(job), 2)
    assert (await native.dao.get_run(old["id"]))["status"] == "interrupted"
    retry = await run_rpc(native, "run.resume", {**request, "reviewed": True})
    assert retry.reply.call_args.args[0]["created"] is False
    assert len(native.cores) == 1


async def test_durable_cancel_before_worker_first_instruction_persists_result(native, monkeypatch):
    service = get_run_service(native.server)
    submit = service.submit

    def cancel_immediately(*args, **kwargs):
        job = submit(*args, **kwargs)
        assert not job.started
        service.cancel_run(job.run_id)
        return job

    monkeypatch.setattr(service, "submit", cancel_immediately)
    ctx = await send(native)
    assert ctx.reply.call_args.args[0]["cancelled"] is True
    assert (await native.dao.list_runs())[0]["status"] == "cancelled"
    assert native.cores == []


async def test_notification_failure_cannot_change_durable_outcome(native):
    ctx = Context(native.server)
    ctx.emit.side_effect = ConnectionError("observer disconnected")
    native.release.set()
    await builtins.handle_agent_send_message(
        {"session_id": "one", "content": "finish despite disconnection", "idempotency_key": "offline"}, ctx)
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.call_args.args[0]
    assert receipt["text"] == "done"
    assert (await native.dao.get_run(receipt["run_id"]))["status"] == "completed"
    assert await native.dao.result_payload(receipt["run_id"]) == receipt


async def test_shutdown_after_admission_before_scheduling_saves_cancel_receipt(native, monkeypatch):
    service = get_run_service(native.server)

    def closed(*args, **kwargs):
        raise RuntimeError("service closed")

    monkeypatch.setattr(service, "submit", closed)
    params = {"session_id": "one", "content": "never execute", "idempotency_key": "closed"}
    first, retry = Context(native.server), Context(native.server)
    await builtins.handle_agent_send_message(params, first)
    assert first.reply_error.call_args.args[0] == -32004
    await builtins.handle_agent_send_message(params, retry)
    assert retry.reply.call_args.args[0]["cancelled"] is True
    assert len(await native.dao.list_runs()) == 1
    assert native.cores == []


@pytest.mark.parametrize("fault,model_calls,writes", [
    ("history", 0, 0), ("user", 0, 0), ("assistant", 1, 0),
    ("thinking", 0, 0), ("intent", 1, 0), ("result", 1, 1), ("tool", 1, 1),
])
async def test_evidence_failure_stops_native_execution(native, monkeypatch, fault, model_calls, writes):
    from test_agent_core import CountingTool, FakeLLM, _text_response, _tool_call, _tool_response

    from minimax_code import agent, app
    from minimax_code.agent.core import AgentCore
    from minimax_code.agent.tools import ToolRegistry, ToolResult
    from minimax_code.storage.dao.messages import MessagesDAO

    class Write(CountingTool):
        async def run(self, **kwargs):
            self.calls.append(kwargs)
            (native.root / "effect.txt").write_text("applied", encoding="utf-8")
            return ToolResult.ok(output="saved")

    tool = Write()
    registry = ToolRegistry()
    registry.register(tool)
    llm = FakeLLM([_tool_response(_tool_call("echo", {"text": "write"})), _text_response("done")])
    llm.default_model, llm.mock = "test", True
    monkeypatch.setattr(agent, "AgentCore", AgentCore)
    monkeypatch.setattr(app, "get_subagent_llm", lambda: llm)
    monkeypatch.setattr("minimax_code.agent.tools.get_default_registry", lambda: registry)
    if fault == "history":
        monkeypatch.setattr(MessagesDAO, "list_for_session", AsyncMock(side_effect=OSError("read failure")))
    elif fault in {"user", "assistant", "tool"}:
        await native.db.execute(f"""CREATE TRIGGER fail_message BEFORE INSERT ON messages
            WHEN NEW.role = '{fault}' BEGIN SELECT RAISE(FAIL, 'storage failure'); END""")
    else:
        event, kind = {"thinking": ("INSERT", "thought"), "intent": ("INSERT", "tool_call"),
                       "result": ("UPDATE", "tool_call")}[fault]
        condition = " AND NEW.status = 'completed'" if fault == "result" else ""
        await native.db.execute(f"""CREATE TRIGGER fail_step BEFORE {event} ON agent_run_steps
            WHEN NEW.kind = '{kind}'{condition} BEGIN SELECT RAISE(FAIL, 'storage failure'); END""")
    ctx = await send(native)
    ctx.reply.assert_not_awaited()
    error = ctx.reply_error.call_args.args
    assert error[0] == -32004 and error[2]["requires_review"] is True
    assert llm.call_count == model_calls and len(tool.calls) == writes
    assert (native.root / "effect.txt").exists() is bool(writes)
    run = (await native.dao.list_runs())[0]
    assert run["status"] == "failed"
    assert (await native.dao.result_payload(run["id"]))["error"]["code"] == -32004


async def test_fatal_tool_callback_drains_sibling_cleanup_before_releasing_session():
    from minimax_code.agent.core import AgentCore
    from minimax_code.run_errors import RunPersistenceError

    core = AgentCore()
    core._prepare_tool_call = AsyncMock(side_effect=lambda call: call)
    sibling_started, cleaning, cleaned, release = (asyncio.Event() for _ in range(4))

    async def execute(call):
        if call["id"] == "failure":
            await sibling_started.wait()
            raise RunPersistenceError("could not save result")
        sibling_started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            cleaned.set()

    core._execute_tool_call = execute
    batch = asyncio.create_task(core._run_tool_batch([{"id": "failure"}, {"id": "sibling"}]))
    await asyncio.wait_for(cleaning.wait(), 2)
    assert not batch.done() and not cleaned.is_set()
    release.set()
    with pytest.raises(RunPersistenceError):
        await asyncio.wait_for(batch, 2)
    assert cleaned.is_set()


@pytest.mark.parametrize("empty_tool_turn,auto_continue", [(False, False), (True, False), (True, True)])
async def test_stream_history_receipt_and_run_use_the_same_assistant_ids(native, monkeypatch, empty_tool_turn, auto_continue):
    from test_agent_core import CountingTool, FakeLLM, _text_response, _tool_call, _tool_response

    from minimax_code import agent, app
    from minimax_code.agent.core import AgentCore
    from minimax_code.agent.tools import ToolRegistry
    from minimax_code.storage.dao.messages import MessagesDAO

    registry = ToolRegistry()
    registry.register(CountingTool())
    first = _tool_response(_tool_call("echo", {"text": "hello"}))
    if empty_tool_turn:
        first = first[1:]
    llm = FakeLLM([first, _text_response("final answer")])
    llm.default_model, llm.mock = "test", True

    def core(**kwargs):
        if auto_continue:
            kwargs["config"].max_iterations = 1
        return AgentCore(**kwargs)

    monkeypatch.setattr(agent, "AgentCore", core)
    monkeypatch.setattr(app, "get_subagent_llm", lambda: llm)
    monkeypatch.setattr("minimax_code.agent.tools.get_default_registry", lambda: registry)
    monkeypatch.setattr(builtins, "_resolve_auto_continue", lambda: (auto_continue, 2))
    ctx = await send(native)
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.call_args.args[0]
    messages = await MessagesDAO(native.db).list_for_session("one")
    assistants = [m for m in messages if m["role"] == "assistant"]
    chunks = [c.args[1] for c in ctx.emit.call_args_list if c.args[0] == "agent.message_chunk"]
    chat_events = {"agent.message_chunk", "agent.tool_call", "agent.tool_result", "agent.status"}
    emitted = [c for c in ctx.emit.call_args_list if c.args[0] in chat_events]
    assert {c.args[0] for c in emitted} == chat_events
    assert all(c.args[1]["run_id"] == receipt["run_id"] for c in emitted)
    partial_chunks = [c for c in emitted if c.args[0] == "agent.message_chunk" and c.args[1]["delta"]]
    assert partial_chunks
    assert all(c.kwargs["metadata"]["run_id"] == receipt["run_id"] for c in partial_chunks)
    assert len(assistants) == (3 if auto_continue else 2)
    assert {m["id"] for m in assistants} == {chunk["message_id"] for chunk in chunks}
    assert all(m["metadata"]["run_id"] == receipt["run_id"] for m in messages)
    run = await native.dao.get_run(receipt["run_id"])
    assert run["user_message_id"] == messages[0]["id"]
    assert run["assistant_message_id"] == receipt["message_id"] == assistants[-1]["id"]
    assert assistants[-1]["content"] == receipt["text"] == "final answer"


async def test_reviewed_resume_exposes_unknown_tool_outcome_without_inventing_saved_result(native, monkeypatch):
    from test_agent_core import FakeLLM, _text_response, _tool_call

    from minimax_code import agent, app
    from minimax_code.agent.core import AgentCore
    from minimax_code.model_snapshot import ModelSnapshot
    from minimax_code.storage.dao.messages import MessagesDAO

    llm = FakeLLM([_text_response("Reviewed existing files; no repeated write")])
    llm.default_model, llm.mock = "test", True
    monkeypatch.setattr(agent, "AgentCore", AgentCore)
    monkeypatch.setattr(app, "get_subagent_llm", lambda: llm)
    old, _ = await native.dao.admit_run(session_id="one", title="uncertain write",
        request={"session_id": "one", "mode": "chat", "content": "write file"}, request_key="before-crash",
        metadata={"model_snapshot": ModelSnapshot.capture().metadata(), "workspace_snapshot": str(native.root)})
    await native.dao.claim_run(old["id"])
    messages = MessagesDAO(native.db)
    await messages.create(id="old-assistant", session_id="one", role="assistant", run_id=old["id"],
                          tool_calls=[_tool_call("write_file", {"path": "effect.txt"}, "uncertain-call")])
    (native.root / "effect.txt").write_text("already applied", encoding="utf-8")
    await native.dao.recover_orphans()
    ctx = await run_rpc(native, "run.resume", {"run_id": old["id"], "idempotency_key": "reviewed-resume", "reviewed": True})
    ctx.reply_error.assert_not_awaited()
    run = ctx.reply.call_args.args[0]["run"]
    service = get_run_service(native.server)
    job = service.get_job(run["id"])
    if job:
        await service.wait(job)
    assert (await native.dao.get_run(run["id"]))["status"] == "completed"
    projected = next(m for m in llm.messages[0] if m.get("tool_call_id") == "uncertain-call")
    assert "UNKNOWN" in projected["content"]
    assert not any(m["role"] == "tool" for m in await messages.list_for_session("one"))
    assert (native.root / "effect.txt").read_text(encoding="utf-8") == "already applied"

async def test_auto_continue_receipt_retains_prior_block_failed_tool(native, monkeypatch):
    from test_agent_core import CountingTool, FakeLLM, _text_response, _tool_call, _tool_response

    from minimax_code import agent, app
    from minimax_code.agent.core import AgentCore
    from minimax_code.agent.tools import ToolRegistry, ToolResult

    class FailingTool(CountingTool):
        async def run(self, **kwargs):
            return ToolResult.fail("first block command failed")

    registry = ToolRegistry()
    registry.register(FailingTool())
    llm = FakeLLM([_tool_response(_tool_call("echo", {"text": "test"})), _text_response("done")])
    llm.default_model, llm.mock = "test", True

    def core(**kwargs):
        kwargs["config"].max_iterations = 1
        return AgentCore(**kwargs)

    monkeypatch.setattr(agent, "AgentCore", core)
    monkeypatch.setattr(app, "get_subagent_llm", lambda: llm)
    monkeypatch.setattr("minimax_code.agent.tools.get_default_registry", lambda: registry)
    monkeypatch.setattr(builtins, "_resolve_auto_continue", lambda: (True, 2))
    ctx = await send(native)
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.call_args.args[0]
    assert receipt["blocks"] == 2
    assert receipt["verification"]["failed_tool_calls"] == 1
    assert receipt["verification"]["failures"][0]["error"] == "first block command failed"
    assert receipt["verification"]["requires_review"] is True
    assert receipt["verification"]["verdict"] == "failed"
