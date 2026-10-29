"""JSON-RPC handlers for workspace/worktree thread metadata."""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from ..execution import capture_output, require_write_access, spawn_exec
from ..storage.db import ensure_data_dir
from ..workspace_ctx import WorkspaceUnavailableError, require_directory
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
            if base_ref.startswith("-") or "\0" in base_ref:
                raise HandlerError(INVALID_PARAMS, "base_ref must be a Git commit reference, not an option")
            project_id = _string_param(p, "project_id")
            require_write_access()
            repo = await _repository_for_project(project_id, configured_repo)
            revision = (await _run_git(repo, "rev-parse", "--verify", "--end-of-options",
                                       base_ref + "^{commit}")).strip()
            if len(revision) not in {40, 64} or any(c not in "0123456789abcdef" for c in revision):
                raise HandlerError(INVALID_PARAMS, "base_ref did not resolve to a commit")
            root = configured_worktree_root or (ensure_data_dir() / "worktrees")
            root.mkdir(parents=True, exist_ok=True)

            session_id = f"ses_{uuid.uuid4().hex[:8]}"
            worktree_path = (root / session_id).resolve()
            await _run_git(repo, "worktree", "add", "--detach", str(worktree_path), revision)

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
                try:
                    await _remove_worktree(repo, worktree_path)
                except Exception:
                    logger.exception("Preserved worktree after session creation failed: %s", worktree_path)
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
            require_write_access()
            target = Path(path_raw).absolute()
            if target != target.resolve() or target.parent != root:
                raise HandlerError(
                    INVALID_PARAMS,
                    "refusing to remove worktree outside managed worktree root",
                    {"path": str(target), "root": str(root)},
                )
            repo = await _repository_for_project(session.get("project_id"), configured_repo)
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

    async def handle_merge_worktree(params: Any, ctx: Context) -> None:
        """``workspace.merge_worktree`` — commit, validate, then merge (M3).

        Flow: commit the worktree's pending changes → run the project's
        validate command *inside the worktree* (完成验证后再合并) → merge
        the worktree commits into the main checkout with --no-ff.
        Conflicts abort the merge and are reported — never auto-resolved;
        a failing validation stops the merge before it starts.
        """
        try:
            check_params(params, expected_keys={"session_id"})
            session_id = _string_param(params, "session_id")
            if not session_id:
                raise HandlerError(INVALID_PARAMS, "session_id is required")
            commit_message = _string_param(params, "commit_message") or (
                f"Merge worktree session {session_id}")
            run_validate_raw = params.get("run_validate")
            run_validate = True if run_validate_raw is None else bool(run_validate_raw)
            require_write_access()

            sess_dao = await dao_factory()
            session = await sess_dao.get(session_id)
            if session is None:
                raise HandlerError(INVALID_PARAMS, f"unknown session_id: {session_id!r}")
            if session.get("workspace_mode") != "worktree":
                raise HandlerError(INVALID_PARAMS, "session is not a worktree session")
            worktree_raw = session.get("workspace_path")
            if not worktree_raw:
                raise HandlerError(INVALID_PARAMS, "worktree session has no workspace_path")
            worktree = Path(worktree_raw)
            if not worktree.is_dir():
                raise HandlerError(INVALID_PARAMS, f"worktree directory is missing: {worktree}")

            # Only merge a managed session belonging to its bound project.
            root = (configured_worktree_root or (ensure_data_dir() / "worktrees")).resolve()
            target = worktree.absolute()
            if target != target.resolve() or target.parent != root or target.name != session_id:
                raise HandlerError(INVALID_PARAMS, "worktree does not match the managed session path")
            main_repo = await _repository_for_project(session.get("project_id"), configured_repo)
            worktree_common = (await _run_git(
                worktree, "rev-parse", "--path-format=absolute", "--git-common-dir"
            )).strip()
            project_common = (await _run_git(
                main_repo, "rev-parse", "--path-format=absolute", "--git-common-dir"
            )).strip()
            if Path(worktree_common).resolve() != Path(project_common).resolve():
                raise HandlerError(INVALID_PARAMS, "worktree does not belong to the session project")
            if (await _run_git(main_repo, "status", "--porcelain")).strip():
                raise HandlerError(INVALID_PARAMS, "main checkout has uncommitted changes")

            main_branch = (await _run_git(main_repo, "rev-parse", "--abbrev-ref", "HEAD")).strip()
            if main_branch in {"", "HEAD"}:
                raise HandlerError(INVALID_PARAMS,
                                   "main checkout is in detached HEAD; give it a branch before merging")

            # 1) Commit pending work inside the worktree (a dirty tree
            #    cannot be merged).
            status = (await _run_git(worktree, "status", "--porcelain")).strip()
            committed_pending = False
            if status:
                await _run_git(worktree, "add", "-A")
                await _run_git(worktree, "commit", "-m", commit_message)
                committed_pending = True

            head = (await _run_git(worktree, "rev-parse", "HEAD")).strip()

            # Check the actual target branch, not every local branch.
            # merge-base exits 1 when HEAD is not an ancestor.
            proc = await spawn_exec(
                "git", "-C", str(main_repo), "merge-base", "--is-ancestor", head, "HEAD",
                cwd=main_repo, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            )
            _, ancestor_err = await capture_output(proc, timeout=60)
            if proc.returncode == 0 and not committed_pending:
                await ctx.reply({"merged": False, "reason": "already_merged",
                                 "worktree_head": head, "main_branch": main_branch})
                return
            if proc.returncode not in (0, 1):
                raise HandlerError(INVALID_PARAMS, "git merge-base failed",
                                   {"stderr": ancestor_err.decode(errors="replace")})

            # 3) Validate inside the worktree BEFORE merging (M3: 完成
            #    验证后再合并). A failure stops the merge with evidence.
            validation: dict[str, Any] | None = None
            if run_validate and session.get("project_id"):
                from ..app import get_projects_dao
                from ..verification import normalize_validate_command, run_validate_command

                proj_dao = get_projects_dao()
                if proj_dao is not None:
                    project = await proj_dao.get(str(session["project_id"]))
                    command = normalize_validate_command((project or {}).get("validate_command"))
                    if command:
                        validation = await run_validate_command(command, cwd=str(worktree))
                        if not validation.get("ok"):
                            await ctx.reply({"merged": False, "reason": "validation_failed",
                                             "worktree_head": head, "validation": validation})
                            return

            # Git accepts the detached commit directly; no user branch is created or deleted.
            try:
                await _run_git(main_repo, "merge", "--no-ff", head, "-m", commit_message)
            except HandlerError as exc:
                try:
                    conflicts = (await _run_git(main_repo, "diff", "--name-only",
                                                "--diff-filter=U")).splitlines()
                except HandlerError:
                    conflicts = []
                if conflicts:
                    try:
                        await _run_git(main_repo, "merge", "--abort")
                    except HandlerError:
                        logger.warning("merge --abort failed; leaving repo state for inspection", exc_info=True)
                    await ctx.reply({
                        "merged": False, "reason": "conflicts", "conflicts": conflicts,
                        "worktree_head": head, "main_branch": main_branch,
                        "detail": exc.data.get("stderr", "")[:500] if exc.data else "",
                    })
                    return
                raise

            merge_commit = (await _run_git(main_repo, "rev-parse", "HEAD")).strip()
            await ctx.reply({
                "merged": True,
                "worktree_head": head,
                "merge_commit": merge_commit,
                "main_branch": main_branch,
                "committed_pending": committed_pending,
                "validation": validation,
            })
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("workspace.merge_worktree failed")
            await ctx.reply_error(INTERNAL_ERROR, f"workspace.merge_worktree failed: {exc}")

    server.register("workspace.create_worktree_session", handle_create_worktree_session)
    server.register("workspace.list_worktrees", handle_list_worktrees)
    server.register("workspace.delete_worktree", handle_delete_worktree)
    server.register("workspace.merge_worktree", handle_merge_worktree)


def _string_param(params: dict[str, Any], key: str) -> str | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise HandlerError(INVALID_PARAMS, f"{key} must be a string")
    value = value.strip()
    return value or None


async def _repository_for_project(project_id: str | None, fallback: Path | None) -> Path:
    """A selected project's binding takes precedence over the process default."""
    from ..app import get_projects_dao

    path = fallback
    if project_id and project_id != "inbox":
        dao = get_projects_dao()
        if dao is None:
            raise HandlerError(INVALID_PARAMS, "Project storage is unavailable")
        project = await dao.get(project_id)
        if project is None:
            raise HandlerError(INVALID_PARAMS, f"unknown project_id: {project_id!r}")
        if project.get("root_path"):
            path = Path(project["root_path"])
    try:
        return await _git_repo_root(require_directory(path) if path else None)
    except WorkspaceUnavailableError as exc:
        raise HandlerError(INVALID_PARAMS, str(exc)) from exc


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


async def _git_repo_root(cwd: Path | None = None) -> Path:
    proc = await spawn_exec(
        "git",
        "rev-parse",
        "--show-toplevel",
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await capture_output(proc, timeout=30)
    if proc.returncode != 0:
        raise HandlerError(
            INVALID_PARAMS,
            "current project is not inside a Git repository",
            {"stderr": err.decode(errors="replace")},
        )
    return Path(out.decode().strip()).resolve()


async def _run_git(repo: Path, *args: str) -> str:
    proc = await spawn_exec(
        "git",
        "-C",
        str(repo),
        *args,
        cwd=repo,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await capture_output(proc, timeout=60)
    if proc.returncode != 0:
        raise HandlerError(
            INVALID_PARAMS,
            f"git {' '.join(args)} failed",
            {"stderr": err.decode(errors="replace")},
        )
    return out.decode(errors="replace")


async def _remove_worktree(repo: Path, path: Path) -> None:
    if path.exists():
        # Git refuses dirty, untracked, locked or unregistered directories.
        # Never turn that refusal into a recursive filesystem deletion.
        await _run_git(repo, "worktree", "remove", str(path))
