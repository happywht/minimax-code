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

    async def _delete_worktree(params: Any, ctx: Context) -> None:
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

    async def handle_delete_worktree(params: Any, ctx: Context) -> None:
        """Fence new admissions while checking and removing the bound worktree."""
        from ..runs import get_run_service

        try:
            check_params(params, expected_keys={"session_id"})
            session_id = str(params["session_id"])
            service = get_run_service(ctx.server)
            async with service._admission_lock:
                sess_dao = await dao_factory()
                session = await sess_dao.get(session_id)
                if not session:
                    raise HandlerError(INVALID_PARAMS, "Session is unavailable")
                if params.get("for_session_delete") is True:
                    table = await sess_dao._db.fetchone("SELECT name FROM sqlite_master WHERE type='table' AND name='agent_root_admissions'")
                    if table and await sess_dao._db.fetchone(
                            "SELECT run_id FROM agent_root_admissions WHERE source_session_id=? OR execution_session_id=? LIMIT 1",
                            (session_id, session_id)):
                        raise HandlerError(INVALID_PARAMS, "Session retains durable Agent history; archive it instead")
                workspace = session.get("workspace_path")
                if any(job.task is not None and not job.task.done() and
                       (job.session_id == session_id or (workspace and job.workspace and
                        Path(job.workspace).is_relative_to(Path(workspace))))
                       for jobs in service._jobs.values() for job in jobs):
                    raise HandlerError(INVALID_PARAMS, "Worktree is used by a queued or active run; stop it and wait for cleanup")
                active = await sess_dao._db.fetchone(
                    "SELECT id FROM agent_runs WHERE session_id=? AND status IN "
                    "('queued','planning','running','awaiting_approval','waiting_input') LIMIT 1", (session_id,))
                if active:
                    raise HandlerError(INVALID_PARAMS, "Session has an unfinished run; review its status before cleanup")
                await _delete_worktree(params, ctx)
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:
            logger.exception("workspace.delete_worktree admission check failed")
            await ctx.reply_error(INTERNAL_ERROR, "Unable to confirm worktree is unused")

    async def handle_review_commits(params: Any, ctx: Context) -> None:
        """Read committed source changes relative to the current target merge base."""
        try:
            from ..app import ensure_db
            from ..storage.dao.text_artifacts import ArtifactDAO
            from .host_session_gate import require_host_session
            from .protocol import WorkspaceReviewCommitsParams

            req = WorkspaceReviewCommitsParams.model_validate(params)
            db = await ensure_db()
            dao = await dao_factory()

            async def binding():
                await require_host_session(req.session_id)
                session = await dao.get(req.session_id)
                from ..app import get_projects_dao
                projects = get_projects_dao()
                project = await projects.get(req.project_id) if projects is not None else None
                if (not session or session.get("archived") or session.get("project_id") != req.project_id
                        or session.get("workspace_mode") != "worktree" or not session.get("workspace_path")
                        or not project or not project.get("root_path") or not Path(project["root_path"]).is_absolute()
                        or await ArtifactDAO(db).service_id() != req.expected_service_id):
                    raise HandlerError(INVALID_PARAMS, "Service, project or worktree session changed")
                root = (configured_worktree_root or (ensure_data_dir() / "worktrees")).resolve()
                source = Path(session["workspace_path"]).absolute()
                if source != source.resolve() or source.parent != root or source.name != req.session_id or not source.is_dir():
                    raise HandlerError(INVALID_PARAMS, "Managed worktree directory is unavailable or changed")
                target = await _repository_for_project(req.project_id, configured_repo)
                source_stat, target_stat = source.stat(), target.stat()
                return source, target, (source_stat.st_dev, source_stat.st_ino), (target_stat.st_dev, target_stat.st_ino)

            source, target, source_id, target_id = await binding()
            common_source = (await _run_git(source, "rev-parse", "--path-format=absolute", "--git-common-dir")).strip()
            common_target = (await _run_git(target, "rev-parse", "--path-format=absolute", "--git-common-dir")).strip()
            if Path(common_source).resolve() != Path(common_target).resolve():
                raise HandlerError(INVALID_PARAMS, "Worktree is not part of the target project's Git repository")
            try:
                branch = (await _run_git(target, "symbolic-ref", "--quiet", "--short", "HEAD")).strip()
            except HandlerError as exc:
                raise HandlerError(INVALID_PARAMS, "Target project HEAD is detached or its branch is unavailable") from exc
            source_head = (await _run_git(source, "rev-parse", "--verify", "HEAD^{commit}")).strip()
            target_head = (await _run_git(target, "rev-parse", "--verify", "HEAD^{commit}")).strip()
            proc = await spawn_exec("git", "-C", str(target), "merge-base", target_head, source_head,
                                    cwd=target, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            out, _ = await capture_output(proc, timeout=60)
            if proc.returncode == 1:
                raise HandlerError(INVALID_PARAMS, "No common Git ancestor exists between the worktree and target")
            if proc.returncode != 0:
                raise HandlerError(INVALID_PARAMS, "Could not determine the Git comparison base")
            base = out.decode("ascii", errors="strict").strip()
            if any(len(value) not in {40, 64} or any(ch not in "0123456789abcdef" for ch in value)
                   for value in (base, source_head, target_head)):
                raise HandlerError(INVALID_PARAMS, "Git returned an invalid comparison commit")
            diff = await _run_git(target, "diff", "--no-color", "--no-ext-diff", "--no-textconv", "-M", base, source_head, "--")
            logs = (await _run_git(target, "log", "--no-decorate", "--max-count=101", "--format=%H%x09%s", f"{target_head}..{source_head}", "--")).splitlines()
            current = await binding()
            if (current != (source, target, source_id, target_id)
                    or (await _run_git(source, "rev-parse", "--verify", "HEAD^{commit}")).strip() != source_head
                    or (await _run_git(target, "rev-parse", "--verify", "HEAD^{commit}")).strip() != target_head
                    or (await _run_git(target, "symbolic-ref", "--quiet", "--short", "HEAD")).strip() != branch):
                raise HandlerError(INVALID_PARAMS, "Worktree or target changed during review; refresh")
            await ctx.reply({"session_id": req.session_id, "project_id": req.project_id, "service_id": req.expected_service_id,
                "workspace": str(source), "target": str(target), "target_branch": branch, "target_head": target_head,
                "source_head": source_head, "merge_base": base, "diff": diff, "commits": logs[:100],
                "commits_truncated": len(logs) > 100})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:
            from pydantic import ValidationError
            if isinstance(exc, (ValidationError, ValueError, OSError)):
                await ctx.reply_error(INVALID_PARAMS, str(exc))
            else:
                logger.exception("workspace.review_commits failed")
                await ctx.reply_error(INTERNAL_ERROR, "Could not review committed worktree changes")

    async def _merge_worktree(params: Any, ctx: Context, source_reserved: Path,
                              target_reserved: Path, verify_source: Any, verify_target: Any) -> None:
        """``workspace.merge_worktree`` — commit, validate, then merge (M3).

        Flow: commit the worktree's pending changes → run the project's
        validate command *inside the worktree* (完成验证后再合并) → merge
        the worktree commits into the main checkout with --no-ff.
        Conflicts leave the merge state for inspection — never auto-resolved;
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
            if worktree.resolve() != source_reserved or main_repo != target_reserved:
                raise HandlerError(INVALID_PARAMS, "Workspace binding changed; review again")
            verify_source()
            verify_target()
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
            main_head = (await _run_git(main_repo, "rev-parse", "--verify", "HEAD^{commit}")).strip()

            # The reviewed path merges only the exact committed source and
            # target shown to the user. Never stage unreviewed worktree edits.
            review = params.get("review")
            if review is not None:
                from ..app import ensure_db
                from ..storage.dao.text_artifacts import ArtifactDAO
                from .host_session_gate import require_host_session

                review_keys = {"service_id", "project_id", "source_head", "target_head", "target_branch"}
                if (not isinstance(review, dict) or set(review) != review_keys
                        or any(not isinstance(review[k], str) or not review[k] for k in review_keys)):
                    raise HandlerError(INVALID_PARAMS, "Invalid worktree review binding")
                await require_host_session(session_id)
                db = await ensure_db()
                if (session.get("archived") or await ArtifactDAO(db).service_id() != review["service_id"]
                        or session.get("project_id") != review["project_id"]
                        or main_head != review["target_head"] or main_branch != review["target_branch"]
                        or (await _run_git(worktree, "rev-parse", "--verify", "HEAD^{commit}")).strip() != review["source_head"]):
                    raise HandlerError(INVALID_PARAMS, "Worktree review changed; review again before merging")

            # 1) Commit pending work inside the worktree (a dirty tree
            #    cannot be merged).
            status = (await _run_git(worktree, "status", "--porcelain")).strip()
            committed_pending = False
            if status and review is not None:
                raise HandlerError(INVALID_PARAMS, "Worktree has uncommitted changes; review and handle them before merging")
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
            command = ""
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

            # Validation can run arbitrary project commands and take a long time.
            # Do not merge a stale source or target if either changed while it ran.
            # This is a preflight, not a lock against external Git or file writers.
            source_head = (await _run_git(worktree, "rev-parse", "--verify", "HEAD^{commit}")).strip()
            source_status = await _run_git(worktree, "status", "--porcelain")
            if source_head != head or source_status.strip():
                await ctx.reply({"merged": False, "reason": "worktree_changed",
                                 "worktree_head": head, "current_worktree_head": source_head,
                                 "validation": validation})
                return
            current_branch = (await _run_git(main_repo, "rev-parse", "--abbrev-ref", "HEAD")).strip()
            current_head = (await _run_git(main_repo, "rev-parse", "--verify", "HEAD^{commit}")).strip()
            current_status = await _run_git(main_repo, "status", "--porcelain")
            if current_branch != main_branch or current_head != main_head or current_status.strip():
                await ctx.reply({"merged": False, "reason": "main_checkout_changed",
                                 "main_branch": main_branch, "current_main_branch": current_branch,
                                 "main_head": main_head, "current_main_head": current_head,
                                 "worktree_head": head, "validation": validation})
                return

            if review is not None:
                await require_host_session(session_id)
                current_session = await sess_dao.get(session_id)
                if (not current_session or current_session.get("archived")
                        or current_session.get("workspace_mode") != "worktree"
                        or current_session.get("workspace_path") != worktree_raw
                        or current_session.get("project_id") != review["project_id"]
                        or await ArtifactDAO(db).service_id() != review["service_id"]):
                    raise HandlerError(INVALID_PARAMS, "Worktree binding changed; review again before merging")

            # Reservations coordinate in-process task admission, not external writers.
            verify_source()
            verify_target()
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
                    await ctx.reply({
                        "merged": False, "reason": "conflicts", "conflicts": conflicts,
                        "worktree_head": head, "main_branch": main_branch,
                        "merge_in_progress": True,
                        "notice": "Merge state was preserved; inspect concurrent edits before resolving or aborting it.",
                        "detail": exc.data.get("stderr", "")[:500] if exc.data else "",
                    })
                    return
                raise

            merge_commit = (await _run_git(main_repo, "rev-parse", "HEAD")).strip()
            # A successful worktree check does not validate the combined tree.
            # Do not roll back a completed merge, even if the command fails or
            # changes files: those changes may belong to the user.
            post_merge: dict[str, Any] = {"status": "skipped", "reason": "not_configured"}
            if not run_validate:
                post_merge["reason"] = "disabled"
            elif command:
                post_merge = {"status": "unknown", "reason": "checkout_changed"}
                try:
                    async def target_matches() -> bool:
                        branch = (await _run_git(main_repo, "rev-parse", "--abbrev-ref", "HEAD")).strip()
                        current = (await _run_git(main_repo, "rev-parse", "--verify", "HEAD^{commit}")).strip()
                        parents = (await _run_git(main_repo, "rev-list", "--parents", "-n", "1", current)).split()
                        status = await _run_git(main_repo, "status", "--porcelain")
                        return (branch == main_branch and current == merge_commit
                                and parents == [merge_commit, main_head, head] and not status.strip())

                    if await target_matches():
                        evidence = await run_validate_command(command, cwd=str(main_repo))
                        post_merge["validation"] = evidence
                        if await target_matches():
                            if evidence.get("ok") is True and evidence.get("exit_code") == 0:
                                post_merge.update(status="passed", reason="validation_passed")
                            elif evidence.get("timed_out") or evidence.get("exit_code") is None:
                                post_merge.update(status="unknown", reason="validation_incomplete")
                            else:
                                post_merge.update(status="failed", reason="validation_failed")
                except Exception:
                    logger.exception("Post-merge validation could not be confirmed")
                    post_merge.update(status="unknown", reason="validation_error")
            await ctx.reply({
                "merged": True,
                "worktree_head": head,
                "merge_commit": merge_commit,
                "main_branch": main_branch,
                "committed_pending": committed_pending,
                "validation": validation,
                "post_merge_validation": post_merge,
            })
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("workspace.merge_worktree failed; inspect repository state")
            await ctx.reply_error(INTERNAL_ERROR, f"Merge outcome may be uncertain; inspect repository state: {exc}")

    async def handle_merge_worktree(params: Any, ctx: Context) -> None:
        """Reserve both checkouts until merge and post-merge validation finish."""
        from ..runs import _workspace_identity, _workspace_overlaps, get_run_service

        try:
            check_params(params, expected_keys={"session_id"})
            session_id = _string_param(params, "session_id")
            if not session_id:
                raise HandlerError(INVALID_PARAMS, "session_id is required")
            sess_dao = await dao_factory()
            session = await sess_dao.get(session_id)
            if not session or session.get("workspace_mode") != "worktree" or not session.get("workspace_path"):
                raise HandlerError(INVALID_PARAMS, "Worktree session is unavailable")
            source = require_directory(Path(session["workspace_path"])).resolve()
            target = await _repository_for_project(session.get("project_id"), configured_repo)
            if source == target:
                raise HandlerError(INVALID_PARAMS, "Source and target workspaces must differ")
            service = get_run_service(ctx.server)
            if _workspace_overlaps(str(source), str(target)):
                # One reservation covers nested roots; independently pin both identities.
                source_identity = _workspace_identity(str(source))
                target_identity = _workspace_identity(str(target))
                covering = target if source.is_relative_to(target) else source
                async with service.workspace_maintenance(str(covering)) as verify_covering:
                    def verify_source() -> None:
                        verify_covering()
                        if _workspace_identity(str(source)) != source_identity:
                            raise ValueError("Source workspace changed during merge")

                    def verify_target() -> None:
                        verify_covering()
                        if _workspace_identity(str(target)) != target_identity:
                            raise ValueError("Target workspace changed during merge")

                    await _merge_worktree(params, ctx, source, target, verify_source, verify_target)
            else:
                async with service.workspace_maintenance(str(source)) as verify_source:
                    async with service.workspace_maintenance(str(target)) as verify_target:
                        await _merge_worktree(params, ctx, source, target, verify_source, verify_target)
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except (ValueError, WorkspaceUnavailableError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("workspace.merge_worktree reservation failed")
            await ctx.reply_error(INTERNAL_ERROR, "Unable to reserve workspaces for merge")

    server.register("workspace.create_worktree_session", handle_create_worktree_session)
    server.register("workspace.list_worktrees", handle_list_worktrees)
    server.register("workspace.review_commits", handle_review_commits)
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
