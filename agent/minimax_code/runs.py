"""Service-owned native execution tasks, independent of RPC connection lifetime.

The session lease includes setup, tool execution, persistence and cleanup. The
service is scoped to the IPC server (one transport per process). Durable queue
admission and cross-engine scheduling will build on this ownership boundary.
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
    task: asyncio.Task[Any] | None = None
    cancel_callback: Callable[[], None] | None = None
    cancel_requested: bool = False
    record: Any = None
    message_id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:8]}")
    result: dict[str, Any] | None = None


class RunService:
    def __init__(self) -> None:
        self._jobs: dict[str, list[RunJob]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._closing = False

    def submit(self, session_id: str, operation: Callable[[RunJob], Awaitable[Any]]) -> RunJob:
        """Atomically enqueue before the first await; same-session jobs are FIFO."""
        if self._closing:
            raise RuntimeError("Run service is shutting down")
        queue = self._jobs.setdefault(session_id, [])
        if len(queue) >= 32:
            raise RuntimeError("Session queue is full (32 pending tasks)")
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        job = RunJob(session_id)
        queue.append(job)

        async def drive() -> Any:
            try:
                async with lock:
                    if job.cancel_requested:
                        raise asyncio.CancelledError
                    return await operation(job)
            finally:
                self._remove(job)

        job.task = asyncio.create_task(drive(), name=f"native-run:{session_id}")
        job.task.add_done_callback(lambda task: self._finished(job, task))
        return job

    def _remove(self, job: RunJob) -> None:
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
            if job.cancel_requested or job.task is None or job.task.done():
                continue
            job.cancel_requested = True
            if job.cancel_callback is not None:
                try:
                    job.cancel_callback()
                except Exception:
                    logger.exception("Run cancellation callback failed")
            job.task.cancel()
        return len(jobs)

    async def shutdown(self) -> None:
        """Drain cancellation/child-process cleanup before closing the database."""
        self._closing = True
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
