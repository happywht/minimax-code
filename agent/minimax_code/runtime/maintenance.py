"""Partial, irreversible maintenance drain for service-owned producers.

This is an integration primitive, not an upgrade gate: RPC transports and other
writers remain live. A fresh process is needed to resume drained components.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


class MaintenanceIncomplete(RuntimeError):
    """A supported producer could not be fenced and drained."""


@dataclass(frozen=True)
class MaintenanceResult:
    drained: tuple[str, ...]
    upgrade_ready: bool = False
    blockers: tuple[str, ...] = (
        "HTTP and stdio request admission and in-flight handlers are not fenced",
        "sub-agent and other independent producer lifecycles are not fenced",
        "terminal descendant processes cannot be confirmed terminated",
        "shared resources and external writers are not exclusively owned",
    )

    def require_upgrade_ready(self) -> None:
        """Fail closed until a complete process-wide barrier exists."""
        raise MaintenanceIncomplete("Global upgrade readiness is not supported: " + "; ".join(self.blockers))


async def _drain(server: Any) -> MaintenanceResult:
    from ..app import begin_codebase_shutdown, drain_codebase_shutdown
    from ..ipc.handlers_terminal import drain_terminal_sessions
    from ..runs import get_run_service
    from ..scheduler import quiesce_scheduler

    try:
        # Close scheduled admissions before the run service. Already admitted
        # runs may still need terminal or index access while they finish, so
        # defer those fences until the scheduler and runs have drained.
        await quiesce_scheduler()
        await get_run_service(server).drain()
        await quiesce_scheduler(wait=True)
        begin_codebase_shutdown()
        results = await asyncio.gather(
            drain_terminal_sessions(),
            drain_codebase_shutdown(),
            return_exceptions=True,
        )
        failures = [item for item in results if isinstance(item, BaseException)]
        if failures:
            raise MaintenanceIncomplete("One or more producer drains failed") from failures[0]
    except MaintenanceIncomplete:
        raise
    except BaseException as exc:
        raise MaintenanceIncomplete("Maintenance fencing failed") from exc
    return MaintenanceResult(drained=("scheduler", "runs", "local terminal tasks and main processes", "codebase index"))


async def drain_for_maintenance(server: Any) -> MaintenanceResult:
    """Join one retained drain; cancelling a caller does not cancel the drain.

    The result reports only the named producers, never permission to upgrade.
    A failure is retained for all later callers; no automatic restart is attempted.
    """
    task = getattr(server, "_maintenance_drain_task", None)
    if task is None:
        task = asyncio.create_task(_drain(server), name="service-maintenance-drain")
        server._maintenance_drain_task = task
        task.add_done_callback(lambda done: None if done.cancelled() else done.exception())
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        if task.cancelled():
            raise MaintenanceIncomplete("The maintenance drain was cancelled") from None
        raise
