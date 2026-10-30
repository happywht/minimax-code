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
_current_root: ContextVar[tuple[Path, int, int] | None] = ContextVar(
    "minimax_workspace_root", default=None
)


class WorkspaceUnavailableError(ValueError):
    """An explicitly bound workspace cannot safely be used."""


def require_directory(path: Path | str) -> Path:
    resolved = Path(path).expanduser().resolve()
    if not resolved.is_dir():
        raise WorkspaceUnavailableError(
            f"Workspace directory is unavailable: {resolved}. Rebind the project before running."
        )
    return resolved


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
    return require_directory(raw)


# ---------------------------------------------------------------------------
# ContextVar accessors
# ---------------------------------------------------------------------------


def current_root() -> Path | None:
    """Return the bound root only while it still resolves to the same directory.

    Direct team runs can have a session root without an execution policy.
    Revalidate that root on each use so a deleted directory or a replacement
    symlink cannot silently redirect later file or process operations.
    """
    binding = _current_root.get()
    if binding is None:
        return None
    root, device, inode = binding
    resolved = require_directory(root)
    info = resolved.stat()
    if resolved != root or (info.st_dev, info.st_ino) != (device, inode):
        raise WorkspaceUnavailableError(
            "Session workspace has changed; rebind before continuing."
        )
    return resolved


def set_current_root(root: Path) -> Token[tuple[Path, int, int] | None]:
    """Publish *root* for the current task; returns the reset token."""
    resolved = require_directory(root)
    info = resolved.stat()
    return _current_root.set((resolved, info.st_dev, info.st_ino))


def reset_current_root(token: Token[tuple[Path, int, int] | None]) -> None:
    """Undo a previous :func:`set_current_root` (call in ``finally``)."""
    _current_root.reset(token)


#: The per-run sandbox directory (v1.5.0), or ``None`` when tool I/O goes
#: straight to the workspace. Set once at the top of a sandboxed sub-agent
#: run by ``subagents._drive_run``; inherited by every child task of that
#: run via the ContextVar copy-on-create_task semantics, and never leaks
#: back into the parent.
_current_sandbox: ContextVar[Path | None] = ContextVar(
    "minimax_sandbox_dir", default=None
)


def current_sandbox() -> Path | None:
    """The sandbox dir for the current run, or ``None`` (direct I/O)."""
    return _current_sandbox.get()


def set_sandbox(root: Path | None) -> Token[Path | None]:
    """Publish *root* as the active sandbox; returns the reset token."""
    return _current_sandbox.set(Path(root).resolve() if root is not None else None)


def reset_sandbox(token: Token[Path | None]) -> None:
    """Undo a previous :func:`set_sandbox` (call in ``finally``)."""
    _current_sandbox.reset(token)


#: The owning run id for the current task (v1.5.1), or ``None`` for the
#: main agent (which runs outside any run scope). Set once at the top of
#: a sub-agent drive by ``subagents._drive_run``; the write tools read
#: it to attribute file writes (fs-bus events) and to consult/populate
#: the in-flight write registry (cross-run concurrent-writer warnings).
_current_run_id: ContextVar[str | None] = ContextVar(
    "minimax_run_id", default=None
)


def current_run_id() -> str | None:
    """The sub-agent run id owning this task, or ``None`` (main agent)."""
    return _current_run_id.get()


def set_current_run_id(run_id: str | None) -> Token[str | None]:
    """Publish *run_id* for the current task; returns the reset token."""
    return _current_run_id.set(run_id)


def reset_current_run_id(token: Token[str | None]) -> None:
    """Undo a previous :func:`set_current_run_id` (call in ``finally``)."""
    _current_run_id.reset(token)


# ---------------------------------------------------------------------------
# Resolution chain
# ---------------------------------------------------------------------------


async def _project_root(project_id: str | None) -> Path | None:
    """Return an optional unbound root; fail closed for an invalid binding."""
    if not project_id:
        return None
    dao = _get_projects_dao()
    if dao is None:
        raise WorkspaceUnavailableError("Project storage is unavailable; cannot verify workspace.")
    try:
        project = await dao.get(project_id)
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("project lookup failed for %s: %s", project_id, exc)
        raise WorkspaceUnavailableError("Project lookup failed; cannot verify workspace.") from exc
    if not project:
        raise WorkspaceUnavailableError(f"Project {project_id!r} no longer exists. Rebind the session.")
    raw = str(project.get("root_path") or "").strip()
    if not raw:
        return None
    return require_directory(raw)


async def resolve_root_for_session(session_id: str | None) -> Path:
    """Resolve the workspace root a session's agent run should use.

    Priority: ``session.workspace_path`` (worktree sessions) →
    ``project.root_path`` → :func:`env_or_cwd_root`. An invalid explicit
    binding raises instead of silently selecting a different directory.
    """
    if session_id:
        session = await _get_session(session_id)
        if not session:
            raise WorkspaceUnavailableError(f"Session {session_id!r} no longer exists.")
        if session:
            ws_raw = str(session.get("workspace_path") or "").strip()
            if ws_raw:
                return require_directory(ws_raw)
            root = await _project_root(
                str(session.get("project_id") or "").strip() or None
            )
            if root is not None:
                return root
    return env_or_cwd_root()


async def resolve_root_for_project_id(project_id: str | None) -> Path:
    """Resolve a root directly from a project id (git/codebase/terminal).

    Unbound projects use the process-wide default. Invalid bindings raise.
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
            raise WorkspaceUnavailableError("Session storage is unavailable; cannot verify workspace.")
        return await dao.get(session_id)
    except WorkspaceUnavailableError:
        raise
    except Exception as exc:  # pragma: no cover — defensive
        logger.warning("session lookup failed for %s: %s", session_id, exc)
        raise WorkspaceUnavailableError("Session lookup failed; cannot verify workspace.") from exc


__all__ = [
    "current_root",
    "current_run_id",
    "current_sandbox",
    "env_or_cwd_root",
    "reset_current_root",
    "reset_current_run_id",
    "reset_sandbox",
    "resolve_root_for_project_id",
    "resolve_root_for_session",
    "session_root_scope",
    "set_current_root",
    "set_current_run_id",
    "set_sandbox",
]
