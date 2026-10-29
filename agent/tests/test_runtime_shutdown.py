"""Teardown orders real task completion before closing shared resources."""
from __future__ import annotations

import asyncio
import shlex
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from minimax_code.runtime.shutdown import ShutdownIncomplete, shutdown_runtime


@pytest.fixture
def lifetime(monkeypatch):
    from minimax_code import app, scheduler
    from minimax_code.ipc import builtins, handlers_terminal
    from minimax_code.orchestrator import subagent
    from minimax_code.runs import RunService

    order = []
    async def quiesce(*, wait=False):
        order.append("cron-drain" if wait else "cron-fence")
    async def close_db():
        order.append("db-close")
    async def close_llm():
        order.append("llm-close")
    db = SimpleNamespace(close=AsyncMock(side_effect=close_db))
    llm = SimpleNamespace(close=AsyncMock(side_effect=close_llm))
    monkeypatch.setattr(app, "_CODEBASE_INDEX_CLOSING", False)
    monkeypatch.setattr(app, "_CODEBASE_INDEXERS", {})
    monkeypatch.setattr(app, "_CODEBASE_INDEXER", None)
    monkeypatch.setattr(app, "_CODEBASE_AUTO_TASKS", set())
    monkeypatch.setattr(app, "get_db", lambda: db)
    monkeypatch.setattr(app, "get_subagent_llm", lambda: llm)
    monkeypatch.setattr(app, "get_mcp_registry", lambda: None)
    monkeypatch.setattr(scheduler, "quiesce_scheduler", quiesce)
    monkeypatch.setattr(builtins, "_ACTIVE_RUNS", {})
    monkeypatch.setattr(handlers_terminal, "_SESSIONS", {})
    monkeypatch.setattr(subagent, "_BACKGROUND_RUNS", {})
    return SimpleNamespace(server=SimpleNamespace(_run_service=RunService()), order=order,
                           db=db, llm=llm, active=builtins._ACTIVE_RUNS,
                           terminals=handlers_terminal._SESSIONS,
                           children=subagent._BACKGROUND_RUNS)


async def test_repeated_cancel_waits_same_terminal_cleanup_before_database(lifetime):
    state = lifetime
    entered, release = asyncio.Event(), asyncio.Event()
    async def terminal():
        try:
            await asyncio.Event().wait()
        finally:
            entered.set()
            await release.wait()
            state.order.append("terminal-clean")
    task = asyncio.create_task(terminal())
    await asyncio.sleep(0)
    state.terminals["terminal"] = SimpleNamespace(task=task, process=None)
    first = asyncio.create_task(shutdown_runtime(state.server))
    await entered.wait()
    first.cancel()
    first.cancel()
    second = asyncio.create_task(shutdown_runtime(state.server))
    await asyncio.sleep(0)
    first.cancel()
    assert task.cancelling() == 1
    assert state.db.close.await_count == 0
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await first
    await second
    await shutdown_runtime(state.server)
    assert state.order == ["cron-fence", "terminal-clean", "cron-drain", "llm-close", "db-close"]
    assert state.db.close.await_count == 1


async def test_shutdown_closes_owned_host_tunnels(lifetime):
    close = AsyncMock()
    lifetime.server._host_tunnel_manager = SimpleNamespace(close=close)
    await shutdown_runtime(lifetime.server)
    close.assert_awaited_once()


async def test_late_registered_descendant_is_drained(lifetime):
    state = lifetime
    async def child():
        try:
            await asyncio.Event().wait()
        finally:
            state.order.append("child-clean")
    async def parent():
        try:
            await asyncio.Event().wait()
        finally:
            task = asyncio.create_task(child())
            state.children["late"] = task
            await asyncio.sleep(0)
    state.children["parent"] = asyncio.create_task(parent())
    await asyncio.sleep(0)
    await shutdown_runtime(state.server)
    assert state.order.index("child-clean") < state.order.index("db-close")


async def test_unmatched_core_never_cleared_or_inferred_clean(lifetime):
    state = lifetime
    state.active["orphan"] = object()
    with pytest.raises(ShutdownIncomplete, match="owner"):
        await shutdown_runtime(state.server)
    assert "orphan" in state.active
    assert state.db.close.await_count == 0
    assert state.llm.close.await_count == 0


async def test_producer_cleanup_error_does_not_close_database(lifetime):
    async def broken():
        try:
            await asyncio.Event().wait()
        finally:
            raise RuntimeError("cleanup failed")
    task = asyncio.create_task(broken())
    await asyncio.sleep(0)
    lifetime.terminals["bad"] = SimpleNamespace(task=task, process=None)
    with pytest.raises(ShutdownIncomplete, match="Producer cleanup"):
        await shutdown_runtime(lifetime.server)
    assert lifetime.db.close.await_count == 0


async def test_mcp_failure_attempts_every_connection_and_remains_unclean(lifetime, monkeypatch):
    from minimax_code import app
    async def remove(name):
        lifetime.order.append(name)
        if name == "bad-mcp":
            raise RuntimeError("MCP close failed")
    registry = SimpleNamespace(list_servers=lambda: [{"name": "bad-mcp"}, {"name": "good-mcp"}],
                               remove_server=remove)
    monkeypatch.setattr(app, "get_mcp_registry", lambda: registry)
    with pytest.raises(ShutdownIncomplete, match="resources"):
        await shutdown_runtime(lifetime.server)
    assert "good-mcp" in lifetime.order
    assert lifetime.db.close.await_count == 1
    with pytest.raises(ShutdownIncomplete):
        await shutdown_runtime(lifetime.server)
    assert lifetime.db.close.await_count == 1


async def test_real_terminal_is_stopped_before_database_close(lifetime, tmp_path):
    from minimax_code.execution import terminate_tree
    from minimax_code.ipc.handlers_terminal import start_terminal_command

    args = [sys.executable, "-c", "import time;print('READY',flush=True);time.sleep(45)"]
    command = subprocess.list2cmdline(args) if sys.platform == "win32" else shlex.join(args)
    session = await start_terminal_command(command=command, cwd=str(tmp_path), timeout_s=60,
                                           chat_session_id=None, emit=AsyncMock())
    try:
        async with asyncio.timeout(15):
            while not any("READY" in chunk.text for chunk in session.chunks):
                assert not session.task.done(), session.error
                await asyncio.sleep(0.02)
        await shutdown_runtime(lifetime.server)
        assert session.task.done()
        assert session.process.returncode is not None
        assert session.status == "cancelled"
        assert lifetime.db.close.await_count == 1
    finally:
        if session.process is not None:
            await terminate_tree(session.process)
        if not session.task.done():
            session.task.cancel()
        await asyncio.gather(session.task, return_exceptions=True)


async def test_run_cleanup_error_is_not_inferred_clean(lifetime):
    async def operation(_job):
        await asyncio.Event().wait()
    async def finalize(_job, _error):
        raise RuntimeError("run persistence failed")
    job = lifetime.server._run_service.submit("session", operation, finalize=finalize)
    await asyncio.sleep(0)
    with pytest.raises(ShutdownIncomplete, match="Run cleanup"):
        await shutdown_runtime(lifetime.server)
    assert job.task.done()
    assert lifetime.db.close.await_count == 0


@pytest.mark.parametrize("persist_failure", [False, True])
async def test_inflight_admission_is_drained_and_persistence_errors_propagate(lifetime, persist_failure):
    from minimax_code.runs import RunServiceClosing

    entered, release = asyncio.Event(), asyncio.Event()
    async def admit(**_kwargs):
        entered.set()
        await release.wait()
        return {"id": "run", "assistant_message_id": "message"}, True
    async def terminate(*_args, **_kwargs):
        if persist_failure:
            raise RuntimeError("terminal receipt persistence failed")
    dao = SimpleNamespace(admit_run=admit, terminate_run=AsyncMock(side_effect=terminate))
    service = lifetime.server._run_service
    request = asyncio.create_task(service.admit(
        dao=dao, session_id="pending", request={}, title="test", metadata={}, request_key="key",
        workspace="test", project_id=None, operation=AsyncMock(), finalize=AsyncMock()))
    await entered.wait()
    closing = asyncio.create_task(shutdown_runtime(lifetime.server))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert service._closing
    release.set()
    if persist_failure:
        with pytest.raises(RuntimeError, match="persistence"):
            await request
        with pytest.raises(ShutdownIncomplete, match="Run cleanup"):
            await closing
        assert lifetime.db.close.await_count == 0
    else:
        with pytest.raises(RunServiceClosing):
            await request
        await closing
        assert lifetime.db.close.await_count == 1
    assert dao.terminate_run.await_count == 1


async def test_run_failure_does_not_abandon_other_owner_cleanup(lifetime):
    service = lifetime.server._run_service
    finished = asyncio.Event()
    async def operation(_job):
        await asyncio.Event().wait()
    async def bad_finalize(_job, _error):
        raise RuntimeError("bad cleanup")
    async def good_finalize(_job, _error):
        await asyncio.sleep(0.01)
        finished.set()
    service.submit("bad", operation, finalize=bad_finalize)
    service.submit("good", operation, finalize=good_finalize)
    await asyncio.sleep(0)
    with pytest.raises(ShutdownIncomplete):
        await shutdown_runtime(lifetime.server)
    assert finished.is_set()
    assert lifetime.db.close.await_count == 0


async def test_job_removed_during_admission_drain_still_reports_cleanup_failure(lifetime):
    from minimax_code.runs import RunServiceClosing

    entered, release, fail_job = asyncio.Event(), asyncio.Event(), asyncio.Event()
    service = lifetime.server._run_service
    async def operation(_job):
        await fail_job.wait()
    async def bad_finalize(_job, _error):
        raise RuntimeError("cleanup failed while admission was draining")
    old_job = service.submit("old", operation, finalize=bad_finalize)
    async def admit(**_kwargs):
        entered.set()
        await release.wait()
        return {"id": "new", "assistant_message_id": "message"}, True
    dao = SimpleNamespace(admit_run=admit, terminate_run=AsyncMock())
    request = asyncio.create_task(service.admit(
        dao=dao, session_id="pending", request={}, title="test", metadata={}, request_key="key",
        workspace="test", project_id=None, operation=AsyncMock(), finalize=AsyncMock()))
    await entered.wait()
    closing = asyncio.create_task(shutdown_runtime(lifetime.server))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert service._closing
    fail_job.set()
    with pytest.raises(RuntimeError, match="while admission"):
        await old_job.task
    assert "old" not in service._jobs
    release.set()
    with pytest.raises(RunServiceClosing):
        await request
    with pytest.raises(ShutdownIncomplete):
        await closing
    assert lifetime.db.close.await_count == 0


async def test_historical_failed_task_does_not_poison_later_shutdown(lifetime):
    async def operation(_job):
        raise RuntimeError("already handled historical failure")
    service = lifetime.server._run_service
    old = service.submit("history", operation, finalize=AsyncMock())
    with pytest.raises(RuntimeError, match="historical"):
        await old.task
    await asyncio.sleep(0)
    assert not service._jobs
    await shutdown_runtime(lifetime.server)
    assert lifetime.db.close.await_count == 1
    await shutdown_runtime(lifetime.server)
    assert lifetime.db.close.await_count == 1


async def test_shutdown_cancels_delayed_auto_index_before_db_close(lifetime, monkeypatch):
    from minimax_code import app

    attempted = []
    monkeypatch.delenv("MINIMAX_CODE_DISABLE_AUTO_INDEX", raising=False)
    monkeypatch.setattr(app, "get_codebase_indexer", lambda: attempted.append(True))
    app._schedule_codebase_index_build(delay_s=60)
    assert len(app._CODEBASE_AUTO_TASKS) == 1
    await shutdown_runtime(lifetime.server)
    assert not attempted
    assert not app._CODEBASE_AUTO_TASKS
    assert lifetime.db.close.await_count == 1
    app._schedule_codebase_index_build(delay_s=0)
    assert not app._CODEBASE_AUTO_TASKS


async def test_shutdown_fences_new_builds_and_waits_for_active_index(lifetime, monkeypatch, tmp_path):
    from minimax_code import app
    from minimax_code.codebase.indexer import CodebaseIndexer

    indexer = CodebaseIndexer(tmp_path, object())
    app._CODEBASE_INDEXERS["workspace"] = indexer
    started, release = asyncio.Event(), asyncio.Event()
    async def build(*, force):
        started.set()
        await release.wait()
    monkeypatch.setattr(indexer, "_run_build", build)
    await indexer.build_index()
    await started.wait()
    closing = asyncio.create_task(shutdown_runtime(lifetime.server))
    while not indexer._closing:
        await asyncio.sleep(0)
    with pytest.raises(RuntimeError, match="closing"):
        await indexer.build_index()
    assert app.ensure_codebase_indexer(tmp_path / "other") is None
    assert lifetime.db.close.await_count == 0
    release.set()
    await closing
    assert lifetime.db.close.await_count == 1
    assert app.ensure_codebase_indexer(tmp_path) is None
    with pytest.raises(RuntimeError, match="closing"):
        await indexer.build_index()


async def test_shutdown_fences_build_already_waiting_for_index_lock(lifetime, tmp_path):
    from minimax_code import app
    from minimax_code.codebase.indexer import CodebaseIndexer

    indexer = CodebaseIndexer(tmp_path, object())
    app._CODEBASE_INDEXERS["workspace"] = indexer
    await indexer._lock.acquire()
    build = asyncio.create_task(indexer.build_index())
    await asyncio.sleep(0)
    closing = asyncio.create_task(shutdown_runtime(lifetime.server))
    while not indexer._closing:
        await asyncio.sleep(0)
    indexer._lock.release()
    with pytest.raises(RuntimeError, match="closing"):
        await build
    await closing
    assert indexer._task is None
    assert lifetime.db.close.await_count == 1


async def test_cancelled_index_build_does_not_confirm_shutdown(lifetime, monkeypatch, tmp_path):
    from minimax_code import app
    from minimax_code.codebase.indexer import CodebaseIndexer

    indexer = CodebaseIndexer(tmp_path, object())
    app._CODEBASE_INDEXERS["workspace"] = indexer
    started = asyncio.Event()
    async def build(*, force):
        started.set()
        await asyncio.Event().wait()
    monkeypatch.setattr(indexer, "_run_build", build)
    await indexer.build_index()
    await started.wait()
    indexer._task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await indexer._task
    with pytest.raises(ShutdownIncomplete, match="index cleanup"):
        await shutdown_runtime(lifetime.server)
    assert lifetime.db.close.await_count == 0


async def test_shutdown_accepts_indexer_with_lifecycle_protocol(lifetime):
    from minimax_code import app

    events = []
    class Indexer:
        def begin_close(self):
            events.append("fence")
        async def close(self):
            assert lifetime.db.close.await_count == 0
            events.append("drained")
    indexer = Indexer()
    app._CODEBASE_INDEXERS["fake"] = indexer
    app._CODEBASE_INDEXER = indexer
    await shutdown_runtime(lifetime.server)
    assert events == ["fence", "drained"]
    assert lifetime.db.close.await_count == 1


async def test_shutdown_rejects_indexer_without_lifecycle_protocol(lifetime):
    from minimax_code import app

    app._CODEBASE_INDEXERS["fake"] = object()
    with pytest.raises(ShutdownIncomplete, match="cannot be fenced"):
        await shutdown_runtime(lifetime.server)
    assert lifetime.db.close.await_count == 0
    assert app._CODEBASE_INDEX_CLOSING
