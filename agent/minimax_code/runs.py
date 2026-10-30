"""Service-owned native execution tasks, independent of RPC connection lifetime.

The session lease includes setup, tool execution, persistence and cleanup. The
service is scoped to the IPC server (one transport per process). Native requests
are durably admitted before scheduling; cross-engine scheduling remains separate.
"""

from __future__ import annotations

import asyncio
import logging
import os
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .live_stream import LiveStream

logger = logging.getLogger(__name__)
_WORKSPACE_RUNTIME_ID = uuid.uuid4().hex


def _workspace_identity(path: str) -> dict[str, Any]:
    from .workspace_ctx import require_directory

    root = require_directory(path)
    if str(root) != path:
        raise ValueError("Workspace path changed; start a new task after reviewing its binding")
    info = root.stat()
    if not info.st_ino:
        raise ValueError("Workspace directory identity is unavailable; cannot safely queue this task")
    return {"version": 1, "device": info.st_dev, "inode": info.st_ino,
            "runtime": _WORKSPACE_RUNTIME_ID}


class RunServiceClosing(RuntimeError):
    """Admission was safely rejected because service shutdown has started."""


class RunShutdownError(RuntimeError):
    """An admission or run did not confirm successful teardown."""


def _workspace_overlaps(first: str, second: str) -> bool:
    """Compare canonical roots, including roots nested inside another root."""
    left = os.path.normcase(str(Path(first).resolve()))
    right = os.path.normcase(str(Path(second).resolve()))
    try:
        common = os.path.commonpath((left, right))
    except ValueError:  # Different Windows drives.
        return False
    return common == left or common == right


def validate_recovery_retry(run: dict[str, Any], source_id: str | None,
                            budget_tokens: int | None) -> None:
    """An idempotent recovery must retain its source and admitted family cap."""
    from .storage.dao.runs import RequestConflictError

    saved_source = run.get("resumed_from_id")
    if saved_source != source_id:
        raise RequestConflictError("idempotency_key belongs to a different recovery source")
    if source_id is not None:
        metadata = run.get("metadata")
        budget = metadata.get("budget") if isinstance(metadata, dict) else None
        if not isinstance(budget, dict) or "limit_tokens" not in budget:
            raise RequestConflictError("Existing recovery budget is unavailable; inspect the run")
        if budget["limit_tokens"] != budget_tokens:
            raise RequestConflictError("idempotency_key belongs to a different cumulative budget")


@dataclass(eq=False)
class RunJob:
    session_id: str
    run_id: str | None = None
    dao: Any = None
    started: bool = False
    operation_started: bool = False
    task: asyncio.Task[Any] | None = None
    cancel_callback: Callable[[], None] | None = None
    cancel_requested: bool = False
    finalizing: bool = False
    cleanup_completed: bool = False
    record: Any = None
    message_id: str = field(default_factory=lambda: f"msg_{uuid.uuid4().hex[:8]}")
    result: dict[str, Any] | None = None
    live_stream: LiveStream = field(default_factory=LiveStream)
    error: dict[str, Any] | None = None
    workspace: str | None = None
    workspace_identity: dict[str, Any] | None = None
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
    capture_file_changes: bool = False
    steer_controller: Any = None

    def verify_workspace(self) -> None:
        if not self.workspace or not self.workspace_identity or _workspace_identity(self.workspace) != self.workspace_identity:
            raise ValueError("Accepted workspace directory changed or its identity is unavailable; start a new task after review")


class RunService:
    def __init__(self) -> None:
        self._jobs: dict[str, list[RunJob]] = {}
        self._locks: dict[str, asyncio.Lock] = {}
        self._closing = False
        self._by_run: dict[str, RunJob] = {}
        self._admissions: set[asyncio.Task[Any]] = set()
        self._admission_lock = asyncio.Lock()
        self._accepted_admissions: set[asyncio.Task[Any]] = set()
        self._draining = False
        self._shutdown_started = False
        self._drain_task: asyncio.Task[None] | None = None
        self._drain_jobs: set[RunJob] = set()

    def submit(
        self, session_id: str, operation: Callable[[RunJob], Awaitable[Any]], *,
        job: RunJob | None = None,
        finalize: Callable[[RunJob, BaseException | None], Awaitable[None]] | None = None,
    ) -> RunJob:
        return self._submit(session_id, operation, job=job, finalize=finalize)

    def _submit(
        self, session_id: str, operation: Callable[[RunJob], Awaitable[Any]], *,
        job: RunJob | None = None,
        finalize: Callable[[RunJob, BaseException | None], Awaitable[None]] | None = None,
        _owned_child: bool = False,
    ) -> RunJob:
        """Atomically enqueue before the first await; same-session jobs are FIFO."""
        accepted = asyncio.current_task() in self._accepted_admissions
        if self._closing and not ((accepted or _owned_child) and self._draining and not self._shutdown_started):
            raise RunServiceClosing("Run service is shutting down")
        queue = self._jobs.setdefault(session_id, [])
        if job is None and len(queue) >= 32:
            raise RuntimeError("Session queue is full (32 pending tasks)")
        lock = self._locks.setdefault(session_id, asyncio.Lock())
        job = job or RunJob(session_id)
        queue.append(job)
        if self._draining:
            self._drain_jobs.add(job)
        if job.run_id:
            self._by_run[job.run_id] = job

        async def drive() -> Any:
            finalized = False
            try:
                async with lock:
                    # Waiting jobs must not be task-cancelled: their finalizer
                    # writes session state and must run under this same lease.
                    job.started = True
                    error = None
                    try:
                        if job.cancel_requested:
                            raise asyncio.CancelledError
                        if job.workspace_identity is not None:
                            job.verify_workspace()
                        job.operation_started = True
                        baseline = None
                        if job.capture_file_changes:
                            from .run_file_changes import capture
                            baseline = await capture(job)
                        try:
                            return await operation(job)
                        finally:
                            # Once the operation has ended, cancellation must not
                            # turn its outcome into a cancellation while optional
                            # observation or durable finalization is in progress.
                            job.finalizing = True
                            if job.capture_file_changes:
                                from .run_file_changes import save_comparison
                                await save_comparison(job, baseline)
                    except BaseException as exc:
                        error = exc
                        raise
                    finally:
                        # Persistence remains inside the session lease.
                        finalized = True
                        job.finalizing = True
                        if finalize:
                            await finalize(job, error)
                        job.cleanup_completed = True
            except BaseException as exc:
                if not finalized:
                    job.finalizing = True
                    if finalize:
                        await finalize(job, exc)
                    job.cleanup_completed = True
                raise
            finally:
                self._remove(job)

        job.task = asyncio.create_task(drive(), name=f"native-run:{session_id}")
        job.task.add_done_callback(lambda task: self._finished(job, task))
        return job

    def submit_owned_child(self, session_id: str, operation: Callable[[RunJob], Awaitable[Any]],
                           *, job: RunJob) -> RunJob:
        """Submit a restricted child only while its bound parent job is live."""
        from .process_context import current_process_target
        from .run_ownership import owned_runtime

        target = current_process_target()
        context = owned_runtime.get()
        parent = self._by_run.get(target.owner.run_id) if target and target.owner else None
        if (not context or context.get("run_service") is not self or parent is None
                or parent.task is None or parent.task.done() or not job.run_id
                or parent.cancel_requested or parent.finalizing
                or job.run_id == parent.run_id):
            raise RunServiceClosing("Restricted child has no live service-owned parent")

        entered_operation = False

        async def guarded_operation(child: RunJob) -> Any:
            nonlocal entered_operation
            # A child may wait for its own session lease while its parent is
            # cancelled. Do not begin its effects after that cancellation.
            if parent.cancel_requested:
                raise asyncio.CancelledError
            entered_operation = True
            return await operation(child)

        async def finalize(child: RunJob, error: BaseException | None) -> None:
            if not entered_operation and child.dao is not None:
                from .budget_ledger import owned_cleanup

                saved = await owned_cleanup(child.dao.update_run_status(
                    child.run_id, status="cancelled",
                    error="Child execution cancelled before it started"))
                if saved is None:
                    raise RuntimeError("Could not persist cancelled child run")

        return self._submit(session_id, guarded_operation, job=job, finalize=finalize,
                            _owned_child=True)

    async def admit(
        self, *, dao: Any, session_id: str, request: dict[str, Any], title: str,
        metadata: dict[str, Any], request_key: str | None, workspace: str,
        project_id: str | None, operation: Callable[[RunJob], Awaitable[Any]],
        finalize: Callable[[RunJob, BaseException | None], Awaitable[None]],
        resumed_from_id: str | None = None,
        continuation: bool = False,
        budget_tokens: int | None = None, daily_baseline: int = 0,
        daily_budget_limit: int | None = None,
        validate_command: str = "",
    ) -> tuple[dict[str, Any], RunJob | None, bool]:
        async def accept():
            async with self._admission_lock:
                if self._closing:
                    raise RunServiceClosing("Run service is shutting down")
                current = asyncio.current_task()
                assert current is not None
                self._accepted_admissions.add(current)
                try:
                    # Idempotent retries must find their original run before
                    # checking other sessions' workspace reservations.
                    if request_key is not None:
                        if (not isinstance(request_key, str) or not request_key.strip()
                                or len(request_key) > 128):
                            raise ValueError("idempotency_key must be a non-empty string of at most 128 characters")
                        profile = metadata.get("agent_execution")
                        if profile is not None and not resumed_from_id:
                            existing = await dao.find_agent_root(
                                profile["source_session_id"], request_key, profile["intent"])
                        else:
                            existing = await dao.find_request(session_id, request_key, request)
                        if existing is not None:
                            validate_recovery_retry(existing, resumed_from_id, budget_tokens)
                            return existing, self._by_run.get(existing["id"]), False

                    # Reservations cover queued jobs as well as execution and
                    # finalization. Reject rather than wait: a parent can await
                    # a child, so a workspace lock could otherwise deadlock.
                    for other_session, jobs in self._jobs.items():
                        if other_session == session_id:
                            continue
                        for other in jobs:
                            if other.workspace and _workspace_overlaps(workspace, other.workspace):
                                raise ValueError(
                                    "Workspace overlaps an active or queued run in another session; "
                                    "wait for it to finish or use an independent workspace")
                    identity = _workspace_identity(workspace)
                    bound_metadata = {**metadata, "workspace_identity": identity}
                    run, created = await dao.admit_run(
                        session_id=session_id, request=request, title=title, metadata=bound_metadata,
                        request_key=request_key, resumed_from_id=resumed_from_id,
                        **({"continuation": True} if continuation else {}))
                    if not created:
                        validate_recovery_retry(run, resumed_from_id, budget_tokens)
                        return run, self._by_run.get(run["id"]), False
                    job = RunJob(session_id, run_id=run["id"], dao=dao,
                                 message_id=run["assistant_message_id"], workspace=workspace,
                                 workspace_identity=identity,
                                 project_id=project_id, budget_tokens=budget_tokens,
                                 daily_baseline=daily_baseline,
                                 daily_budget_limit=daily_budget_limit,
                                 validate_command=validate_command,
                                 capture_file_changes=request.get("capture_file_changes") is True)
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
                finally:
                    self._accepted_admissions.discard(current)

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

    async def cancel_session(self, session_id: str) -> tuple[int, list[dict[str, Any]]]:
        """Fence admitted work before confirming durable queued cancellations."""
        async def cancel_admitted() -> tuple[int, list[dict[str, Any]]]:
            # Include admissions already committing, but release this global lock
            # before any cancellation persistence or session cleanup can wait.
            async with self._admission_lock:
                jobs = list(self._jobs.get(session_id, []))
                queued = {job: not job.operation_started for job in jobs}
                for job in jobs:
                    self._cancel(job)

            updates = []
            failures = []
            for job in jobs:
                if not job.cancel_requested or not job.run_id or job.dao is None:
                    continue
                # A previously queued cancellation can already be finalizing;
                # retry its durable fence too if an earlier write failed.
                try:
                    run = await job.dao.request_queued_cancel(job.run_id)
                    if run is None:
                        run = await job.dao.get_run(job.run_id)
                        if queued[job] and (run is None or run.get("status") != "cancelled"):
                            raise RuntimeError(f"Could not confirm queued cancellation for {job.run_id}")
                    if run is not None:
                        updates.append(run)
                except Exception as exc:
                    failures.append(exc)
            if failures:
                raise RuntimeError("Could not confirm all session cancellations") from failures[0]
            return len(jobs), updates

        # A disconnected caller must not interrupt persistence halfway through.
        task = asyncio.create_task(cancel_admitted(), name=f"cancel-session:{session_id}")
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(task)

    def cancel_run(self, run_id: str) -> bool:
        job = self.get_job(run_id)
        return self._cancel(job) if job is not None else False

    async def cancel_run_durable(self, run_id: str) -> bool:
        """Cancel a run and persist the fence when it has not begun execution."""
        job = self.get_job(run_id)
        if job is None:
            return False
        queued = not job.operation_started
        # Fence the queued job before yielding to persistence.
        requested = self._cancel(job)

        async def cancel_admitted() -> bool:
            if queued and job.cancel_requested and job.dao is not None:
                run = await job.dao.request_queued_cancel(run_id)
                if run is None:
                    run = await job.dao.get_run(run_id)
                    if run is None or run.get("status") != "cancelled":
                        raise RuntimeError(f"Could not confirm queued cancellation for {run_id}")
            elif queued and job.cancel_requested:
                raise RuntimeError(f"Cannot persist queued cancellation for {run_id}")
            return requested

        # Persist even if the caller disconnects while SQLite is committing.
        task = asyncio.create_task(cancel_admitted(), name=f"cancel-run:{run_id}")
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
        return await asyncio.shield(task)

    def _cancel(self, job: RunJob) -> bool:
        # Once execution has ended, interrupting terminal persistence can leave
        # the durable run active after its service-owned task has disappeared.
        if job.finalizing or job.cancel_requested or job.task is None or job.task.done():
            return False
        job.cancel_requested = True
        if job.cancel_callback is not None:
            try:
                job.cancel_callback()
            except Exception:
                logger.exception("Run cancellation callback failed")
        # A queued task must acquire the session lease before it persists its
        # cancellation. Only interrupt a task that already owns the lease.
        if job.started:
            job.task.cancel()
        return True

    async def drain(self) -> None:
        """Fence new work and wait for accepted RunService jobs without cancelling them.

        Only admissions already inside the database operation cross the fence.
        This does not stop other service owners or establish process-wide quiescence.
        Cancelling a waiter leaves the shared drain running; failures remain observable
        by subsequent callers. A separate shutdown may still cancel running work.
        """
        if self._drain_task is None:
            self._closing = True
            self._draining = True
            jobs = {job for queue in self._jobs.values() for job in queue}
            self._drain_jobs.update(jobs)
            admissions = list(self._admissions)

            async def finish_drain() -> None:
                results = await asyncio.gather(*admissions, return_exceptions=True)
                failures = []
                for result in results:
                    if isinstance(result, BaseException):
                        if not isinstance(result, RunServiceClosing):
                            failures.append(result)
                    elif result[1] is not None:
                        jobs.add(result[1])
                self._drain_jobs.update(jobs)
                seen: set[RunJob] = set()
                while pending := self._drain_jobs - seen:
                    seen.update(pending)
                    active_jobs = [job for job in pending if job.task]
                    results = await asyncio.gather(
                        *(job.task for job in active_jobs), return_exceptions=True)
                    for job, value in zip(active_jobs, results, strict=True):
                        if isinstance(value, asyncio.CancelledError):
                            if not job.cancel_requested or not job.cleanup_completed:
                                failures.append(value)
                        elif isinstance(value, BaseException):
                            failures.append(value)
                if failures:
                    raise RunShutdownError("Run drain ended with an error") from failures[0]

            self._drain_task = asyncio.create_task(finish_drain(), name="run-service-drain")
            # Retrieve errors even if every waiter disconnects.
            self._drain_task.add_done_callback(
                lambda task: None if task.cancelled() else task.exception())
        await asyncio.shield(self._drain_task)

    async def shutdown(self) -> None:
        """Drain cancellation/child-process cleanup before closing the database."""
        self._shutdown_started = True
        self._closing = True
        # A job can finish/remove itself while admissions are being awaited.
        # Retain both sides of that await so its cleanup error is not lost.
        jobs = {job for queue in self._jobs.values() for job in queue}
        admissions = await asyncio.gather(*list(self._admissions), return_exceptions=True)
        jobs.update(job for queue in self._jobs.values() for job in queue)
        for job in jobs:
            self._cancel(job)
        results = await asyncio.gather(*(job.task for job in jobs if job.task), return_exceptions=True)
        failures = [value for value in admissions
                    if isinstance(value, BaseException)
                    and not isinstance(value, (asyncio.CancelledError, RunServiceClosing))]
        failures.extend(value for value in results if isinstance(value, BaseException)
                        and not isinstance(value, asyncio.CancelledError))
        if failures:
            raise RunShutdownError("Run cleanup ended with an error") from failures[0]


def get_run_service(server: Any) -> RunService:
    service = getattr(server, "_run_service", None)
    if not isinstance(service, RunService):
        service = RunService()
        server._run_service = service
    return service
