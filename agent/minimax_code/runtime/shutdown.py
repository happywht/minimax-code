"""Retained, cancel-once teardown shared by HTTP and stdio services."""
from __future__ import annotations

import asyncio
from typing import Any


class ShutdownIncomplete(RuntimeError):
    """Resources or live producers could not be confirmed closed."""


async def _drain_tasks(tasks) -> None:
    pending = {task for task in tasks if task is not None and not task.done()}
    for task in pending:
        if not task.cancelling():
            task.cancel()
    results = await asyncio.gather(*pending, return_exceptions=True)
    failures = [result for result in results
                if isinstance(result, BaseException) and not isinstance(result, asyncio.CancelledError)]
    if failures:
        raise ShutdownIncomplete("Producer cleanup failed") from failures[0]


async def _shutdown(server: Any) -> None:
    from ..app import (
        begin_codebase_shutdown,
        drain_codebase_shutdown,
        get_db,
        get_execution_profiles,
        get_mcp_registry,
        get_subagent_llm,
    )
    from ..ipc.builtins import _ACTIVE_RUNS
    from ..ipc.handlers_terminal import _SESSIONS
    from ..orchestrator.subagent import _BACKGROUND_RUNS
    from ..runs import RunShutdownError, get_run_service
    from ..scheduler import quiesce_scheduler

    # Fence auto and explicit index builds before any shutdown await.
    try:
        begin_codebase_shutdown()
    except Exception as exc:
        raise ShutdownIncomplete("Codebase indexer cannot be fenced") from exc
    # Stop new scheduled admissions before draining the owners of actual work.
    await quiesce_scheduler()
    try:
        await get_run_service(server).shutdown()
    except RunShutdownError as exc:
        raise ShutdownIncomplete("Run cleanup ended with an error") from exc
    # A cancelling child may register its own descendant before it yields.
    while True:
        tasks = [*_BACKGROUND_RUNS.values(), *(s.task for s in _SESSIONS.values())]
        pending = [task for task in tasks if task is not None and not task.done()]
        if not pending:
            break
        await _drain_tasks(pending)

    # Core.cancel() is merely a request. An unmatched core has no awaitable
    # owner here; clearing its registry cannot prove that its writes stopped.
    if _ACTIVE_RUNS:
        raise ShutdownIncomplete("Active agent work has no confirmed drained owner")
    if any(s.process is not None and s.process.returncode is None for s in _SESSIONS.values()):
        raise ShutdownIncomplete("A terminal process is still running")
    await quiesce_scheduler(wait=True)
    try:
        await drain_codebase_shutdown()
    except BaseException as exc:
        raise ShutdownIncomplete("Codebase index cleanup was not confirmed") from exc
    catalog = get_execution_profiles()
    if catalog is not None:
        try:
            await catalog.close()
        except Exception as exc:
            # SQLite and lease must survive unknown container responsibility.
            raise ShutdownIncomplete("Restricted process cleanup remains unconfirmed") from exc

    # Attempt every independent resource closure. Preserve the failed outcome
    # even if the rest close successfully; the CLI must retain its dirty marker.
    failures: list[BaseException] = []
    tunnels = getattr(server, "_host_tunnel_manager", None)
    if tunnels is not None:
        try:
            await tunnels.close()
        except Exception as exc:
            failures.append(exc)
    registry = get_mcp_registry()
    if registry is not None:
        for item in registry.list_servers():
            try:
                await registry.remove_server(item["name"])
            except Exception as exc:
                failures.append(exc)
    llm = get_subagent_llm()
    if llm is not None and hasattr(llm, "close"):
        try:
            await llm.close()
        except Exception as exc:
            failures.append(exc)
    db = get_db()
    if db is not None and hasattr(db, "close"):
        try:
            await db.close()
        except Exception as exc:
            failures.append(exc)
    if failures:
        raise ShutdownIncomplete(f"{len(failures)} service resources failed to close") from failures[0]


async def shutdown_runtime(server: Any) -> None:
    """All callers await one teardown; repeated cancellation cannot cancel it.

    A cancelled caller still waits for teardown before propagating cancellation.
    No timeout is interpreted as proof of completion. Runtime restart in the
    same process is not supported; start a fresh service process.
    """
    task = getattr(server, "_runtime_shutdown_task", None)
    if task is None:
        task = asyncio.create_task(_shutdown(server), name="service-runtime-shutdown")
        server._runtime_shutdown_task = task
    cancelled = False
    while True:
        try:
            await asyncio.shield(task)
            break
        except asyncio.CancelledError:
            if task.cancelled():
                raise ShutdownIncomplete("The retained shutdown task was cancelled") from None
            cancelled = True
    if cancelled:
        raise asyncio.CancelledError
