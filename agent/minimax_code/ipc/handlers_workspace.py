"""JSON-RPC handlers for workspace/worktree thread metadata."""

from __future__ import annotations

import asyncio
import logging
import shutil
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..storage.db import ensure_data_dir
from .handler_utils import HandlerError, check_params
from .protocol import INTERNAL_ERROR, INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)

_DEFAULT_TITLE_FMT = "Worktree 任务 — %Y-%m-%d %H:%M"


def register_workspace_handlers(
    server: Any,
    *,
    dao: Any = None,
    repo_root: Path | None = None,
    worktree_root: Path | None = None,
) -> None:
    dao_factory = _make_dao_factory(dao)
    configured_repo = repo_root
    configured_worktree_root = worktree_root

    async def handle_create_worktree_session(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys=set())
            p = params or {}
            if not isinstance(p, dict):
                raise HandlerError(INVALID_PARAMS, "params must be an object")
            title = _string_param(p, "title") or datetime.now().strftime(_DEFAULT_TITLE_FMT)
            base_ref = _string_param(p, "base_ref") or "HEAD"
            project_id = _string_param(p, "project_id")
            if project_id and project_id != "inbox":
                await _validate_project_id(project_id)
            repo = configured_repo or await _git_repo_root()
            root = configured_worktree_root or (ensure_data_dir() / "worktrees")
            root.mkdir(parents=True, exist_ok=True)

            session_id = f"ses_{uuid.uuid4().hex[:8]}"
            worktree_path = (root / session_id).resolve()
            await _run_git(repo, "worktree", "add", "--detach", str(worktree_path), base_ref)

            sess_dao = await dao_factory()
            try:
                row = await sess_dao.create(
                    id=session_id,
                    title=title,
                    workspace_mode="worktree",
                    workspace_path=str(worktree_path),
                    base_branch=base_ref,
                    project_id=project_id or "inbox",
                )
            except Exception:
                await _remove_worktree(repo, worktree_path)
                raise

            await ctx.reply(
                {
                    "session_id": row["id"],
                    "session": row,
                    "worktree_path": row.get("workspace_path"),
                    "base_branch": row.get("base_branch"),
                }
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("workspace.create_worktree_session failed")
            await ctx.reply_error(
                INTERNAL_ERROR,
                f"workspace.create_worktree_session failed: {exc}",
            )

    async def handle_list_worktrees(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys=set())
            sess_dao = await dao_factory()
            sessions = await sess_dao.list(limit=500)
            await ctx.reply(
                {
                    "sessions": [
                        s for s in sessions if s.get("workspace_mode") == "worktree"
                    ]
                }
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("workspace.list_worktrees failed")
            await ctx.reply_error(INTERNAL_ERROR, f"workspace.list_worktrees failed: {exc}")

    async def handle_delete_worktree(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            sess_dao = await dao_factory()
            session = await sess_dao.get(session_id)
            if session is None:
                raise HandlerError(INVALID_PARAMS, f"unknown session_id: {session_id!r}")
            if session.get("workspace_mode") != "worktree":
                raise HandlerError(INVALID_PARAMS, "session is not a worktree session")
            path_raw = session.get("workspace_path")
            if not path_raw:
                raise HandlerError(INVALID_PARAMS, "worktree session has no workspace_path")
            root = (configured_worktree_root or (ensure_data_dir() / "worktrees")).resolve()
            target = Path(path_raw).resolve()
            if root not in (target, *target.parents):
                raise HandlerError(
                    INVALID_PARAMS,
                    "refusing to remove worktree outside managed worktree root",
                    {"path": str(target), "root": str(root)},
                )
            repo = configured_repo or await _git_repo_root()
            await _remove_worktree(repo, target)
            row = await sess_dao.update(
                session_id,
                workspace_mode="local",
                workspace_path="",
                worktree_branch="",
                base_branch="",
            )
            await ctx.reply({"ok": True, "session": row})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("workspace.delete_worktree failed")
            await ctx.reply_error(INTERNAL_ERROR, f"workspace.delete_worktree failed: {exc}")

    server.register("workspace.create_worktree_session", handle_create_worktree_session)
    server.register("workspace.list_worktrees", handle_list_worktrees)
    server.register("workspace.delete_worktree", handle_delete_worktree)


def _string_param(params: dict[str, Any], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HandlerError(INVALID_PARAMS, f"{key} must be a string")
    value = value.strip()
    return value or None


async def _validate_project_id(project_id: str) -> None:
    """Reject a ``project_id`` that no project row backs (v1.3.0).

    A worktree session filed under a ghost project would silently
    vanish from every project-filtered list, so the id is checked
    before the worktree is created — failing fast also keeps the
    ``worktree add`` from leaving an orphaned checkout behind.
    """
    from ..app import get_projects_dao

    dao = get_projects_dao()
    if dao is None:  # pragma: no cover - runtime not initialised
        return
    if await dao.get(project_id) is None:
        raise HandlerError(INVALID_PARAMS, f"unknown project_id: {project_id!r}")


def _make_dao_factory(dao: Any | None) -> Any:
    if dao is not None:

        async def _factory() -> Any:
            return dao

        return _factory

    async def _factory() -> Any:
        from ..app import get_sessions_dao, init_runtime

        existing = get_sessions_dao()
        if existing is not None:
            return existing
        await init_runtime()
        return get_sessions_dao()

    return _factory


async def _git_repo_root() -> Path:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "rev-parse",
        "--show-toplevel",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise HandlerError(
            INVALID_PARAMS,
            "current project is not inside a Git repository",
            {"stderr": err.decode(errors="replace")},
        )
    return Path(out.decode().strip()).resolve()


async def _run_git(repo: Path, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        "-C",
        str(repo),
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await proc.communicate()
    if proc.returncode != 0:
        raise HandlerError(
            INVALID_PARAMS,
            f"git {' '.join(args)} failed",
            {"stderr": err.decode(errors="replace")},
        )
    return out.decode(errors="replace")


async def _remove_worktree(repo: Path, path: Path) -> None:
    if path.exists():
        try:
            await _run_git(repo, "worktree", "remove", "--force", str(path))
        except HandlerError:
            shutil.rmtree(path, ignore_errors=True)
