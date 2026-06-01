"""Application bootstrap — wires IPC handlers to the server.

This module is the seam where the :mod:`minimax_code.ipc` layer
meets the rest of the agent. At startup it:

1. Spins up the storage layer (asyncio DB + migrations).
2. Builds a :class:`~.agent.skills.SkillRuntime` wired to the
   storage, the LLM client, and the global tool registry.
3. Loads the built-in skills + any user skills on disk.
4. Registers the IPC handlers: ``agent.*``, ``skill.*``, and
   ``task.*``.

The runtime is built lazily on the first ``skill.*`` call, so
tests can construct an :class:`IPCServer` without dragging in
storage or the LLM client. The application entry point
(:mod:`minimax_code.__main__`) calls :func:`init_runtime` once
at startup so the first user request is fast.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from .agent.skills import SkillRuntime, bootstrap

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy runtime singleton
# ---------------------------------------------------------------------------


_RUNTIME: SkillRuntime | None = None
_RUNTIME_LOCK = asyncio.Lock()


def get_runtime() -> SkillRuntime | None:
    """Return the process-wide :class:`SkillRuntime`, or ``None`` if not yet built."""
    return _RUNTIME


async def init_runtime(*, skills_root: Path | str | None = None) -> SkillRuntime:
    """Build the runtime once and cache it.

    Subsequent calls return the cached instance. Safe to call
    concurrently — guarded by an :class:`asyncio.Lock`.
    """
    global _RUNTIME
    async with _RUNTIME_LOCK:
        if _RUNTIME is not None:
            return _RUNTIME
        root = Path(skills_root) if skills_root else _default_skills_root()
        db = await _maybe_open_db()
        runtime = await bootstrap(
            skills_root=root,
            db=db,
            llm=None,
            tool_registry=None,
        )
        _RUNTIME = runtime
        return _RUNTIME


def set_runtime(runtime: SkillRuntime | None) -> None:
    """Replace the cached runtime (used by tests that inject a fake one)."""
    global _RUNTIME
    _RUNTIME = runtime


async def _maybe_open_db() -> Any:
    """Open the async database if storage can be initialised.

    As a side effect, this *also* populates the
    :data:`_PROGRESS_TRACKER` singleton once a DB handle is
    available, so the ``task.*`` IPC handlers can find the
    tracker without having to open the DB themselves.
    """
    if os.environ.get("MINIMAX_CODE_NO_DB") == "1":
        return None
    try:
        from .storage.db import AsyncDatabase, default_database_path
        from .storage.dao.tasks import TaskDAO
        from .progress import ProgressTracker
    except Exception:  # pragma: no cover — storage not yet bootstrapped
        logger.debug("storage layer not importable; running with in-memory skill registry")
        return None
    try:
        db = AsyncDatabase(default_database_path())
        await db.connect()
        await db.migrate()
        # Spin up the process-wide progress tracker that backs
        # the ``task.*`` IPC namespace. Doing it here (next to
        # the DB open) keeps the singleton's lifetime tied to
        # the DB's lifetime.
        _set_progress_tracker(ProgressTracker(TaskDAO(db)))
        return db
    except Exception:  # pragma: no cover — defensive
        logger.exception("failed to open storage; running with in-memory skill registry")
        return None


def _default_skills_root() -> Path:
    env = os.environ.get("MINIMAX_CODE_SKILLS_DIR")
    if env:
        return Path(env)
    # agent/minimax_code/app.py  ->  agent/skills/
    # parents[0] = agent/minimax_code/  parents[1] = agent/  parents[2] = repo root
    return Path(__file__).resolve().parents[2] / "agent" / "skills"


# ---------------------------------------------------------------------------
# Progress tracker singleton
# ---------------------------------------------------------------------------


_PROGRESS_TRACKER: Any = None  # type: ignore[no-untyped-def]


def get_progress_tracker() -> Any:
    """Return the process-wide :class:`ProgressTracker`, or ``None``.

    The tracker is created by :func:`_maybe_open_db` the first
    time the storage layer is opened. Tests can replace it via
    :func:`set_progress_tracker`.
    """
    return _PROGRESS_TRACKER


def set_progress_tracker(tracker: Any) -> None:
    """Replace the cached progress tracker (test seam)."""
    global _PROGRESS_TRACKER
    _PROGRESS_TRACKER = tracker


def _set_progress_tracker(tracker: Any) -> None:
    """Internal setter used by :func:`_maybe_open_db`."""
    global _PROGRESS_TRACKER
    _PROGRESS_TRACKER = tracker


# ---------------------------------------------------------------------------
# Handler registration
# ---------------------------------------------------------------------------


def register_app_handlers(server: Any, *, runtime: SkillRuntime | None = None) -> None:
    """Register all application-level JSON-RPC methods on ``server``.

    The ``skill.*`` handlers call :func:`init_runtime` on every
    invocation and cache the resulting runtime in
    :data:`_RUNTIME`. The ``task.*`` handlers look up the
    :class:`ProgressTracker` singleton in
    :data:`_PROGRESS_TRACKER`. Tests can pre-build and inject
    either via the matching ``runtime=`` / ``tracker=`` kwargs
    to skip the lazy path.
    """
    from .ipc.builtins import handle_agent_send_message
    from .ipc.handlers_permissions import register_permission_handlers
    from .ipc.handlers_scheduled import register_scheduled_handlers
    from .ipc.handlers_skills import register_skill_handlers
    from .ipc.handlers_tasks import register_task_handlers

    server.register("agent.send_message", handle_agent_send_message)
    if runtime is not None:
        set_runtime(runtime)
    # The skill handlers build the runtime themselves via
    # :func:`init_runtime` if it's not pre-set. The
    # ``runtime=`` argument is optional and only used as a
    # cache hint.
    register_skill_handlers(server, runtime=runtime)
    # The task handlers resolve the tracker lazily via
    # :func:`get_progress_tracker`, which is itself lazy (it
    # opens the DB on first call). Inject a tracker when you
    # need to bypass the storage layer (e.g. unit tests).
    register_task_handlers(server)
    # The permission handlers lazily open the async DB and build a
    # :class:`~.permissions.PermissionStore` on first call. Tests
    # that pre-built a store can pass it via the ``store=`` kwarg
    # (see :func:`register_permission_handlers`).
    register_permission_handlers(server)
    # The schedule handlers build the JobScheduler lazily via
    # :func:`minimax_code.scheduler.get_scheduler`, which opens
    # the DB on first call. Tests can inject a scheduler with
    # the ``scheduler=`` kwarg to skip the lazy path.
    register_scheduled_handlers(server)
    logger.info(
        "registered application handlers "
        "(1 agent.* + 5 skill.* + 3 task.* + 5 permission.* + 6 schedule.*)"
    )


__all__ = [
    "get_progress_tracker",
    "get_runtime",
    "init_runtime",
    "register_app_handlers",
    "set_progress_tracker",
    "set_runtime",
]
