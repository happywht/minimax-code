"""Per-project workspace root resolution (v1.3.0).

Historically every session shared one process-wide workspace root
(``MINIMAX_CODE_WORKSPACE`` env or process CWD).  This module adds the
"which root does *this* conversation run against?" dimension:

* :func:`resolve_root_for_session` walks the priority chain
  ``session.workspace_path`` (git-worktree sessions) → ``project.root_path``
  (set on the project, directory must exist) → :func:`env_or_cwd_root`.
* The resolved root is published on a :class:`contextvars.ContextVar` for
  the duration of an agent run (:func:`set_current_root` /
  :func:`session_root_scope`).  ``file_ops._default_workspace`` reads it,
  so every path-sensitive tool (read/write/list/edit/exec/backup)
  inherits the session's root with zero signature changes.

Concurrency: each JSON-RPC request runs in its own asyncio task and
``ContextVar`` state is copied into child tasks (subagents, team members)
on ``create_task`` — children inherit the parent run's root and their own
``set`` calls never leak back into the parent.

Import graph: this module must stay import-cheap and free of any
``agent.tools`` import (tools import *it*).  Runtime DAO access is lazily
imported from ``.app`` inside functions — the same pattern
``handlers_projects`` uses.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

#: The active workspace root for the current task, or ``None`` when the
#: process-wide default (:func:`env_or_cwd_root`) applies.
_current_root: ContextVar[Path | None] = ContextVar(
    "minimax_workspace_root", default=None
)


# ---------------------------------------------------------------------------
# Process-wide fallback root
# ---------------------------------------------------------------------------


def env_or_cwd_root() -> Path:
    """The process-wide workspace root: ``MINIMAX_CODE_WORKSPACE`` or CWD.

    This is the single authoritative definition of the legacy default —
    ``file_ops._default_workspace`` delegates here when no session root
    is set, so the two can never drift apart.
    """
    raw = os.environ.get("MINIMAX_CODE_WORKSPACE") or os.getcwd()
    return Path(raw).expanduser().resolve()


# ---------------------------------------------------------------------------
# ContextVar accessors
# ---------------------------------------------------------------------------


def current_root() -> Path | None:
    """The session-scoped root, or ``None`` if the default applies."""
    return _current_root.get()


def set_current_root(root: Path) -> Token[Path | None]:
    """Publish *root* for the current task; returns the reset token."""
    return _current_root.set(Path(root).resolve())


def reset_current_root(token: Token[Path | None]) -> None:
    """Undo a previous :func:`set_current_root` (call in ``finally``)."""
    _current_root.reset(token)


# ---------------------------------------------------------------------------
# Resolution chain
# ---------------------------------------------------------------------------


async def _project_root(project_id: str | None) -> Path | None:
    """The project's ``root_path`` if set and the directory exists.

    Returns ``None`` for unknown projects, empty roots, DAO unavailability,
    or a root pointing at a missing directory (the run falls back to the
    process root with a warning rather than exploding the session).
    """
    if not project_id:
        return None
    dao = _get_projects_dao()
    if dao is None:
        return None
    try:
        project = await dao.get(project_id)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("project lookup failed for %s: %s", project_id, exc)
        return None
    if not project:
        return None
    raw = str(project.get("root_path") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_dir():
        logger.warning(
            "project %s root_path %s is not a directory — falling back to "
            "the process workspace",
            project_id,
            raw,
        )
        return None
    return path.resolve()


async def resolve_root_for_session(session_id: str | None) -> Path:
    """Resolve the workspace root a session's agent run should use.

    Priority: ``session.workspace_path`` (worktree sessions) →
    ``project.root_path`` → :func:`env_or_cwd_root`.  Never raises — a
    broken chain degrades to the process root.
    """
    if session_id:
        session = await _get_session(session_id)
        if session:
            ws_raw = str(session.get("workspace_path") or "").strip()
            if ws_raw and Path(ws_raw).expanduser().is_dir():
                return Path(ws_raw).expanduser().resolve()
            root = await _project_root(
                str(session.get("project_id") or "").strip() or None
            )
            if root is not None:
                return root
    return env_or_cwd_root()


async def resolve_root_for_project_id(project_id: str | None) -> Path:
    """Resolve a root directly from a project id (git/codebase/terminal).

    Projects without a root resolve to the process-wide default, so
    callers can treat the return value as always valid.
    """
    root = await _project_root(project_id)
    return root if root is not None else env_or_cwd_root()


@asynccontextmanager
async def session_root_scope(session_id: str | None) -> AsyncIterator[Path]:
    """Run a block with the session's root published on the ContextVar.

    Convenience wrapper for top-level handlers that drive an agent run
    outside ``agent.send_message`` (``agent.invoke``, ``teams.spawn``):
    resolves the root, sets it for the block, and always resets.
    """
    root = await resolve_root_for_session(session_id)
    token = set_current_root(root)
    try:
        yield root
    finally:
        reset_current_root(token)


# ---------------------------------------------------------------------------
# Runtime accessors (lazy to keep the import graph acyclic)
# ---------------------------------------------------------------------------


def _get_projects_dao() -> Any | None:
    try:
        from .app import get_projects_dao

        return get_projects_dao()
    except Exception:  # pragma: no cover — defensive
        return None


async def _get_session(session_id: str) -> Any | None:
    try:
        from .app import get_sessions_dao

        dao = get_sessions_dao()
        if dao is None:
            return None
        return await dao.get(session_id)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("session lookup failed for %s: %s", session_id, exc)
        return None


__all__ = [
    "current_root",
    "env_or_cwd_root",
    "reset_current_root",
    "resolve_root_for_project_id",
    "resolve_root_for_session",
    "session_root_scope",
    "set_current_root",
]
