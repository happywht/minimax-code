"""Partial, irreversible maintenance drain for service-owned producers.

This is an integration primitive, not an upgrade gate: independent writers
and direct handler calls remain outside the RPC admission boundary. A fresh
process is needed to resume drained components.
"""
from __future__ import annotations

import asyncio
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

_rpc_context: ContextVar[tuple[object, ...]] = ContextVar("maintenance_rpc_context", default=())


class AdmissionClosed(RuntimeError):
    """RPC admission has been closed for maintenance."""


# These handlers only resolve existing work or report status. No other method
# is admitted after the fence, including newly registered handlers.
_MAINTENANCE_CONTROLS = frozenset({
    "permission.resolve", "run.cancel", "run.get", "run.list", "run.steps", "run.events",
    "terminal.stop", "terminal.read", "terminal.list", "ping", "status",
})


class RPCAdmission:
    """Single-event-loop admission fence; closing and counting never await."""

    def __init__(self) -> None:
        self.closed = False
        self._active = 0
        self._ordinary = 0
        self._controls_closed = False
        self._ordinary_empty = asyncio.Event()
        self._ordinary_empty.set()
        self._empty = asyncio.Event()
        self._empty.set()

    @contextmanager
    def enter(self, method: str, *, request: bool) -> Iterator[None]:
        control = self.closed and request and method in _MAINTENANCE_CONTROLS
        if self.closed and (not control or self._controls_closed):
            raise AdmissionClosed("RPC admission closed for maintenance")
        self._active += 1
        self._empty.clear()
        if not control:
            self._ordinary += 1
            self._ordinary_empty.clear()
        token = _rpc_context.set((*_rpc_context.get(), self))
        try:
            yield
        finally:
            _rpc_context.reset(token)
            self._active -= 1
            if not control:
                self._ordinary -= 1
                if not self._ordinary:
                    self._ordinary_empty.set()
            if not self._active:
                self._empty.set()

    def close(self) -> None:
        self.closed = True

    def close_controls(self) -> None:
        self._controls_closed = True

    async def wait_ordinary_empty(self) -> None:
        await self._ordinary_empty.wait()

    def in_handler_context(self) -> bool:
        return self in _rpc_context.get()

    async def wait_empty(self) -> None:
        await self._empty.wait()


class MaintenanceIncomplete(RuntimeError):
    """A supported producer could not be fenced and drained."""


@dataclass(frozen=True)
class MaintenanceResult:
    drained: tuple[str, ...]
    upgrade_ready: bool = False
    blockers: tuple[str, ...] = (
        "direct handler calls and other unaudited entry points are not fenced",
        "uncovered producer paths may require controls beyond the audited RPC allowlist",
        "stdio processes requests serially and cannot receive controls during a long handler",
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
        admission = getattr(server, "_rpc_admission", None)
        if admission is not None:
            await admission.wait_ordinary_empty()
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
    finally:
        admission = getattr(server, "_rpc_admission", None)
        if admission is not None:
            admission.close_controls()
            await admission.wait_empty()
    return MaintenanceResult(drained=("scheduler", "runs", "local terminal tasks and main processes", "codebase index"))


async def drain_for_maintenance(server: Any) -> MaintenanceResult:
    """Join one retained drain; cancelling a caller does not cancel the drain.

    The result reports only the named producers, never permission to upgrade.
    A failure is retained for all later callers; no automatic restart is attempted.
    """
    admission = getattr(server, "_rpc_admission", None)
    # Waiting for an admitted caller (or a child task inheriting its context)
    # would deadlock or permit that caller to mutate after the barrier.
    if admission is not None:
        if admission.in_handler_context():
            raise MaintenanceIncomplete("Maintenance cannot be initiated from an admitted RPC handler")
        admission.close()
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
