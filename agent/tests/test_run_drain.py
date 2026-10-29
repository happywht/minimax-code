from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from minimax_code.runs import RunService, RunServiceClosing, RunShutdownError


def admission(service, dao, session="session", operation=None, finalize=None):
    return service.admit(
        dao=dao, session_id=session, request={}, title="test", metadata={}, request_key=None,
        workspace="test", project_id=None, operation=operation or AsyncMock(),
        finalize=finalize or AsyncMock())


async def test_drain_fences_work_but_completes_inflight_admission_and_cleanup():
    service = RunService()
    entered, release_db, started, release_run, cleanup, release_cleanup = (
        asyncio.Event() for _ in range(6))

    async def admit_run(**_kwargs):
        entered.set()
        await release_db.wait()
        return {"id": "run", "assistant_message_id": "message"}, True

    async def operation(_job):
        started.set()
        await release_run.wait()

    async def finalize(_job, error):
        assert error is None
        cleanup.set()
        await release_cleanup.wait()

    dao = SimpleNamespace(admit_run=admit_run, terminate_run=AsyncMock())
    first = asyncio.create_task(admission(service, dao, operation=operation, finalize=finalize))
    await entered.wait()
    queued = asyncio.create_task(admission(service, dao, session="queued"))
    await asyncio.sleep(0)
    draining = asyncio.create_task(service.drain())
    await asyncio.sleep(0)
    with pytest.raises(RunServiceClosing):
        service.submit("direct", AsyncMock())
    later = asyncio.create_task(admission(service, dao, session="later"))
    release_db.set()
    _, job, created = await first
    assert created and job is not None
    with pytest.raises(RunServiceClosing):
        await queued
    with pytest.raises(RunServiceClosing):
        await later
    await started.wait()
    assert not draining.done() and not job.cancel_requested
    release_run.set()
    await cleanup.wait()
    assert not draining.done()
    release_cleanup.set()
    await draining
    dao.terminate_run.assert_not_awaited()
    await service.drain()


async def test_cancelled_drain_waiter_does_not_cancel_run_or_other_waiters():
    service = RunService()
    started, release = asyncio.Event(), asyncio.Event()
    async def operation(_job):
        started.set()
        await release.wait()
    job = service.submit("session", operation)
    await started.wait()
    first = asyncio.create_task(service.drain())
    await asyncio.sleep(0)
    second = asyncio.create_task(service.drain())
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    assert not job.task.done() and not job.cancel_requested
    release.set()
    await second
    await service.drain()


async def test_drain_reports_failure_even_if_job_finishes_while_admission_waits():
    service = RunService()
    fail, entered, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def operation(_job):
        await fail.wait()
        raise ValueError("run failed")
    job = service.submit("existing", operation)
    async def admit_run(**_kwargs):
        entered.set()
        await release.wait()
        raise ValueError("admission failed")
    request = asyncio.create_task(admission(service, SimpleNamespace(admit_run=admit_run)))
    await entered.wait()
    draining = asyncio.create_task(service.drain())
    await asyncio.sleep(0)
    fail.set()
    with pytest.raises(ValueError, match="run failed"):
        await job.task
    release.set()
    with pytest.raises(ValueError, match="admission failed"):
        await request
    with pytest.raises(RunShutdownError, match="drain"):
        await draining
    with pytest.raises(RunShutdownError, match="drain"):
        await service.drain()


async def test_shutdown_still_cancels_during_drain():
    service = RunService()
    entered, finalized = asyncio.Event(), asyncio.Event()
    async def operation(_job):
        entered.set()
        await asyncio.Event().wait()
    async def finalize(_job, error):
        assert isinstance(error, asyncio.CancelledError)
        finalized.set()
    job = service.submit("session", operation, finalize=finalize)
    await entered.wait()
    draining = asyncio.create_task(service.drain())
    await asyncio.sleep(0)
    await service.shutdown()
    await draining
    assert job.cancel_requested and finalized.is_set()

async def test_drain_rejects_uncontrolled_cancellation_before_job_starts():
    service = RunService()
    job = service.submit("session", AsyncMock(), finalize=AsyncMock())
    job.task.cancel()
    with pytest.raises(RunShutdownError, match="drain"):
        await service.drain()


async def test_drain_waits_for_all_cleanup_and_reports_finalize_failure():
    service = RunService()
    release, entered, finished = asyncio.Event(), asyncio.Event(), asyncio.Event()
    async def wait_operation(_job):
        entered.set()
        await release.wait()
    async def good_finalize(_job, _error):
        finished.set()
    async def bad_finalize(_job, _error):
        raise RuntimeError("finalize failed")
    service.submit("good", wait_operation, finalize=good_finalize)
    service.submit("bad", wait_operation, finalize=bad_finalize)
    await entered.wait()
    draining = asyncio.create_task(service.drain())
    await asyncio.sleep(0)
    assert not draining.done()
    release.set()
    with pytest.raises(RunShutdownError, match="drain"):
        await draining
    assert finished.is_set()

async def test_drain_reports_interrupted_cancellation_cleanup():
    service = RunService()
    entered, cleaning = asyncio.Event(), asyncio.Event()
    async def operation(_job):
        entered.set()
        await asyncio.Event().wait()
    async def finalize(_job, _error):
        cleaning.set()
        await asyncio.Event().wait()
    job = service.submit("session", operation, finalize=finalize)
    await entered.wait()
    draining = asyncio.create_task(service.drain())
    await asyncio.sleep(0)
    assert service.cancel_session("session") == 1
    await cleaning.wait()
    job.task.cancel()
    with pytest.raises(RunShutdownError, match="drain"):
        await draining

async def test_drain_preserves_admission_already_committed_to_sqlite(tmp_path):
    from minimax_code.storage.dao.runs import AgentRunsDAO
    from minimax_code.storage.dao.sessions import SessionsDAO
    from minimax_code.storage.db import AsyncDatabase

    db = AsyncDatabase(tmp_path / "drain.db")
    await db.connect()
    await db.migrate()
    await SessionsDAO(db).create(id="session", title="session")
    real_dao = AgentRunsDAO(db)
    committed, release, ran = asyncio.Event(), asyncio.Event(), asyncio.Event()

    async def admit_run(**kwargs):
        result = await real_dao.admit_run(**kwargs)
        committed.set()
        await release.wait()
        return result

    async def operation(job):
        await real_dao.claim_run(job.run_id)
        ran.set()

    async def finalize(job, error):
        assert error is None
        await real_dao.update_run_status(job.run_id, status="completed")

    dao = SimpleNamespace(admit_run=admit_run, terminate_run=real_dao.terminate_run)
    service = RunService()
    try:
        request = asyncio.create_task(admission(service, dao, operation=operation, finalize=finalize))
        await committed.wait()
        draining = asyncio.create_task(service.drain())
        await asyncio.sleep(0)
        release.set()
        run, job, created = await request
        await draining
        assert created and job is not None and ran.is_set()
        assert (await real_dao.get_run(run["id"]))["status"] == "completed"
    finally:
        await db.close()
