"""Service-owned native execution tasks, independent of RPC connection lifetime.

The session lease includes setup, tool execution, persistence and cleanup. The
service is scoped to the IPC server (one transport per process). Native requests
are durably admitted before scheduling; cross-engine scheduling remains separate.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(eq=False)
class RunJob:
    session_id: str
    run_id: str | None = None
    dao: Any = None
    started: bool = False
    task: asyncio.Task[Any] | None = None
    cancel_callback: Callable[[], None] | None = None
    cancel_requested: bool = False
    record: Any = None
    message_id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:8]}")
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    workspace: str | None = None
    project_id: str | None = None
    # M2 — resolved budget for this run (None = unlimited) and the
    # day's already-spent total captured at admission. Execution reads
    # them off the job; resume overrides come in through the same path.
    budget_tokens: int | None = None
    daily_baseline: int = 0
    daily_budget_limit: int | None = None
    # M3 — the project's acceptance command, snapshotted at admission so
    # a mid-run rebind cannot swap what the run will be validated with.
    validate_command: str = ""


class RunService:
    def __init__(self) -> None:
        self._jobs: dict[str, list[RunJob]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._closing = False
        self._by_run: dict[str, RunJob] = {}
        self._admissions: set[asyncio.Task[Any]] = set()
        self._admission_lock = asyncio.Lock()

    def submit(
        self, session_id: str, operation: Callable[[RunJob], Awaitable[Any]], *,
        job: RunJob | None = None,
        finalize: Callable[[RunJob, BaseException | None], Awaitable[None]] | None = None,
    ) -> RunJob:
        """Atomically enqueue before the first await; same-session jobs are FIFO."""
        if self._closing:
            raise RuntimeError("Run service is shutting down")
        queue = self._jobs.setdefault(session_id, [])
        if job is None and len(queue) >= 32:
            raise RuntimeError("Session queue is full (32 pending tasks)")
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        job = job or RunJob(session_id)
        queue.append(job)
        if job.run_id:
            self._by_run[job.run_id] = job

        async def drive() -> Any:
            job.started = True
            finalized = False
            try:
                async with lock:
                    error = None
                    try:
                        if job.cancel_requested:
                            raise asyncio.CancelledError
                        return await operation(job)
                    except BaseException as exc:
                        error = exc
                        raise
                    finally:
                        # Persistence remains inside the session lease.
                        finalized = True
                        if finalize:
                            await finalize(job, error)
            except BaseException as exc:
                if not finalized and finalize:
                    await finalize(job, exc)
                raise
            finally:
                self._remove(job)

        job.task = asyncio.create_task(drive(), name=f"native-run:{session_id}")
        job.task.add_done_callback(lambda task: self._finished(job, task))
        return job

    async def admit(
        self, *, dao: Any, session_id: str, request: dict[str, Any], title: str,
        metadata: dict[str, Any], request_key: str | None, workspace: str,
        project_id: str | None, operation: Callable[[RunJob], Awaitable[Any]],
        finalize: Callable[[RunJob, BaseException | None], Awaitable[None]],
        resumed_from_id: str | None = None,
        budget_tokens: int | None = None, daily_baseline: int = 0,
        daily_budget_limit: int | None = None,
        validate_command: str = "",
    ) -> tuple[dict[str, Any], RunJob | None, bool]:
        async def accept():
            async with self._admission_lock:
                if self._closing:
                    raise RuntimeError("Run service is shutting down")
                run, created = await dao.admit_run(
                    session_id=session_id, request=request, title=title, metadata=metadata,
                    request_key=request_key, resumed_from_id=resumed_from_id)
                if not created:
                    return run, self._by_run.get(run["id"]), False
                job = RunJob(session_id, run_id=run["id"], dao=dao,
                             message_id=run["assistant_message_id"], workspace=workspace,
                             project_id=project_id, budget_tokens=budget_tokens,
                             daily_baseline=daily_baseline,
                             daily_budget_limit=daily_budget_limit,
                             validate_command=validate_command)
                try:
                    self.submit(session_id, operation, job=job, finalize=finalize)
                except BaseException:
                    await dao.terminate_run(run["id"], status="cancelled",
                                            error="Service closed before execution started",
                                            result={"session_id": session_id,
                                                    "message_id": job.message_id,
                                                    "run_id": job.run_id, "text": "",
                                                    "cancelled": True})
                    raise
                return run, job, True

        task = asyncio.create_task(accept(), name=f"admit-run:{session_id}")
        self._admissions.add(task)
        def finished(task):
            self._admissions.discard(task)
            if not task.cancelled():
                task.exception()  # detached RPC waiters must not leak exceptions
        task.add_done_callback(finished)
        return await asyncio.shield(task)

    def get_job(self, run_id: str) -> RunJob | None:
        return self._by_run.get(run_id)

    async def job_for_run(self, run_id: str) -> RunJob | None:
        async with self._admission_lock:
            return self.get_job(run_id)

    def _remove(self, job: RunJob) -> None:
        if job.run_id and self._by_run.get(job.run_id) is job:
            self._by_run.pop(job.run_id, None)
        queue = self._jobs.get(job.session_id)
        if queue and job in queue:
            queue.remove(job)
            if not queue:
                self._jobs.pop(job.session_id, None)
                self._locks.pop(job.session_id, None)

    def _finished(self, job: RunJob, task: asyncio.Task[Any]) -> None:
        # A task cancelled before its first instruction never enters finally.
        self._remove(job)
        if not task.cancelled():
            error = task.exception()
            if error is not None:
                logger.error("Service-owned run failed for %s", job.session_id, exc_info=error)

    async def wait(self, job: RunJob) -> Any:
        assert job.task is not None
        return await asyncio.shield(job.task)

    def cancel_session(self, session_id: str) -> int:
        """Request stop for active and queued work; retain leases until teardown."""
        jobs = list(self._jobs.get(session_id, []))
        for job in jobs:
            self._cancel(job)
        return len(jobs)

    def cancel_run(self, run_id: str) -> bool:
        job = self.get_job(run_id)
        return self._cancel(job) if job is not None else False

    def _cancel(self, job: RunJob) -> bool:
        if job.cancel_requested or job.task is None or job.task.done():
            return False
        job.cancel_requested = True
        if job.cancel_callback is not None:
            try:
                job.cancel_callback()
            except Exception:
                logger.exception("Run cancellation callback failed")
        # A not-yet-started task must enter drive() to persist cancellation.
        if job.started:
            job.task.cancel()
        return True

    async def shutdown(self) -> None:
        """Drain cancellation/child-process cleanup before closing the database."""
        self._closing = True
        await asyncio.gather(*self._admissions, return_exceptions=True)
        jobs = [job for queue in self._jobs.values() for job in queue]
        for session_id in list(self._jobs):
            self.cancel_session(session_id)
        await asyncio.gather(*(job.task for job in jobs if job.task), return_exceptions=True)


def get_run_service(server: Any) -> RunService:
    service = getattr(server, "_run_service", None)
    if not isinstance(service, RunService):
        service = RunService()
        server._run_service = service
    return service
