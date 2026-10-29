"""JSON-RPC handlers for the ``run.*`` timeline namespace."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from pydantic import ValidationError

from .handler_utils import HandlerError, check_params
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    RunChildListParams,
    RunChildWorkspaceParams,
    RunEventsParams,
    RunIdParams,
    RunOutputClearParams,
    RunOutputInventoryParams,
    RunOutputPageParams,
    RunResearchGetParams,
    RunProjectSummaryParams,
    RunResearchSearchParams,
    RunResearchReadParams,
    RunResearchListParams,
    RunResumeParams,
    RunStartParams,
    RunSteerParams,
    RunVerifyCollectedParams,
)
from .server import Context

logger = logging.getLogger(__name__)


async def _get_dao(*, initialize: bool = True) -> Any:
    from ..app import get_db, init_runtime
    from ..storage.dao.runs import AgentRunsDAO

    db = get_db()
    if db is None and initialize:
        try:
            await init_runtime()
        except Exception:
            pass
        db = get_db()
    if db is None:
        raise HandlerError(INTERNAL_ERROR, "storage layer is not available")
    return AgentRunsDAO(db)


async def _get_read_control_dao(ctx: Context) -> Any:
    # A maintenance status/cancel request must not initialize the runtime.
    admission = getattr(ctx.server, "_rpc_admission", None)
    if admission is not None and admission.closed:
        return await _get_dao(initialize=False)
    return await _get_dao()


def register_run_handlers(server: Any) -> None:
    """Register run timeline read APIs."""

    child_review_lock = asyncio.Lock()

    async def handle_output_inventory(params: Any, ctx: Context) -> None:
        from ..tool_outputs import inventory
        from .host_session_gate import require_host_session
        try:
            req = RunOutputInventoryParams.model_validate(params)
            await require_host_session(req.session_id)
            dao = await _get_read_control_dao(ctx)
            result = await inventory(dao._db, req.session_id, req.offset)
            await require_host_session(req.session_id)
            await ctx.reply(result)
        except (ValidationError, ValueError, HandlerError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("tool output inventory failed")
            await ctx.reply_error(INTERNAL_ERROR, "Output inventory unavailable")

    async def handle_output_clear(params: Any, ctx: Context) -> None:
        from ..tool_outputs import clear
        from .host_session_gate import require_host_session
        try:
            req = RunOutputClearParams.model_validate(params)
            await require_host_session(req.session_id)
            service = getattr(ctx.server, "_run_service", None)
            job = service.get_job(req.run_id) if service is not None else None
            if job is not None and job.task is not None and not job.task.done():
                raise ValueError("Run cleanup is still active")
            dao = await _get_dao()
            result = await clear(dao._db, run_id=req.run_id, session_id=req.session_id,
                ref=req.output_ref, service_id=req.expected_service_id, expected_sha256=req.expected_sha256)
            try:
                await ctx.emit("run.output_cleared", {"run_id": req.run_id, "session_id": req.session_id,
                    "output_ref": req.output_ref, "service_id": req.expected_service_id,
                    "cleared_at": result["cleared_at"]})
            except Exception:
                logger.warning("Output cleared but notification failed", exc_info=True)
            await ctx.reply({**result, "run_id": req.run_id, "session_id": req.session_id,
                             "output_ref": req.output_ref, "service_id": req.expected_service_id})
        except (ValidationError, ValueError, HandlerError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("tool output clear failed")
            await ctx.reply_error(INTERNAL_ERROR, "Could not clear captured output")

    async def handle_output_page(params: Any, ctx: Context) -> None:
        from ..tool_outputs import read
        from .host_session_gate import require_host_session

        try:
            req = RunOutputPageParams.model_validate(params)
            await require_host_session(req.session_id)
            dao = await _get_read_control_dao(ctx)
            page = await read(dao._db, run_id=req.run_id, session_id=req.session_id,
                ref=req.output_ref, service_id=req.expected_service_id, offset=req.offset, limit=req.limit)
            await require_host_session(req.session_id)
            await ctx.reply(page)
        except (ValidationError, ValueError, HandlerError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("tool output page failed")
            await ctx.reply_error(INTERNAL_ERROR, "Captured output is unavailable")

    async def handle_child_list(params: Any, ctx: Context) -> None:
        from ..orchestrator.subagent import get_background_run
        from ..storage.dao.runs import _hydrate_run
        from ..storage.dao.text_artifacts import ArtifactDAO

        try:
            req = RunChildListParams.model_validate(params)
            dao = await _get_read_control_dao(ctx)

            async def binding():
                session = await dao._db.fetchone("SELECT project_id FROM sessions WHERE id=?", (req.session_id,))
                if not session:
                    raise ValueError("Unknown parent session")
                identity = await ArtifactDAO(dao._db).service_id()
                return session["project_id"], identity

            project, identity = await binding()
            rows = await dao._db.fetchall("""SELECT c.* FROM agent_runs c JOIN agent_runs p
                ON p.id = CASE WHEN json_valid(c.metadata) THEN json_extract(c.metadata,'$.parent_run_id') END
                WHERE c.mode='subagent' AND p.session_id=?
                AND CASE WHEN json_valid(c.metadata) THEN json_extract(c.metadata,'$.parent_session_id') END=?
                AND CASE WHEN json_valid(p.metadata) THEN json_extract(p.metadata,'$.project_id') END IS ?
                ORDER BY c.created_at DESC,c.id DESC LIMIT 100""", (req.session_id, req.session_id, project))
            children = []
            for row in rows:
                run = _hydrate_run(row)
                meta = run.get("metadata") or {}
                if not isinstance(meta, dict):
                    continue
                task = get_background_run(run["id"])
                live = task is not None and not task.done()
                children.append({"run_id": run["id"], "session_id": run["session_id"],
                    "parent_run_id": meta["parent_run_id"], "status": run["status"], "live": live,
                    "agent_id": str(meta.get("agent_id") or ""), "agent_name": str(meta.get("agent_name") or ""),
                    "prompt": str(meta.get("prompt") or "")[:2000], "text": str(meta.get("result_text") or "")[:4000],
                    "text_truncated": len(str(meta.get("result_text") or "")) > 4000,
                    "partial": bool(meta.get("partial")), "error": run.get("error"),
                    "created_at": run["created_at"], "completed_at": run.get("completed_at")})
            if await binding() != (project, identity):
                raise ValueError("Session project or service changed during recovery")
            await ctx.reply({"session_id": req.session_id, "project_id": project,
                             "service_id": identity, "children": children, "limit": 100})
        except (ValidationError, ValueError, HandlerError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("child run recovery failed")
            await ctx.reply_error(INTERNAL_ERROR, "Could not recover child runs")

    async def handle_child_workspace(params: Any, ctx: Context) -> None:
        from ..execution import ExecutionPolicy, execution_scope
        from ..orchestrator.subagent import get_background_run
        from ..storage.dao.text_artifacts import ArtifactDAO
        from ..workspace.child import collect_child, load_child
        from ..workspace.child_review import preview
        from ..workspace_ctx import require_directory, resolve_root_for_session
        from .host_session_gate import require_host_session

        try:
            req = RunChildWorkspaceParams.model_validate(params)
            dao = await _get_dao()
            team_collection: dict[str, Any] | None = None
            parent_run_id: str | None = None

            async def binding():
                nonlocal team_collection, parent_run_id
                await require_host_session(req.session_id)
                if req.team_run_id:
                    from ..orchestrator.team_control import is_active
                    from ..runs import get_run_service
                    team = await dao.get_run(req.team_run_id)
                    meta = (team or {}).get("metadata") or {}
                    result = meta.get("team_result") if isinstance(meta, dict) else None
                    agents = result.get("agents_run") if isinstance(result, dict) else None
                    session = await dao._db.fetchone("SELECT project_id FROM sessions WHERE id=?", (req.session_id,))
                    recorded = isinstance(agents, list) and any(isinstance(a, dict) and isinstance(a.get("workspace"), dict)
                        and a["workspace"].get("run_id") == req.run_id for a in agents)
                    steps = await dao.list_steps(req.team_run_id) if team else []
                    if team and not recorded:
                        recorded = any(isinstance(step.get("payload"), dict)
                            and step["payload"].get("kind") == "team_member_v1"
                            and step["payload"].get("outcome") in {"completed", "incomplete"}
                            and isinstance(step["payload"].get("workspace"), dict)
                            and step["payload"]["workspace"].get("run_id") == req.run_id for step in steps)
                    if (not team or team["mode"] != "team" or team["session_id"] != req.session_id
                            or team["status"] not in {"completed", "failed", "interrupted"} or not session
                            or not recorded or "project_id" not in meta
                            or meta["project_id"] != session["project_id"]):
                        raise ValueError("Team member or project binding is unavailable or changed")
                    if team["status"] == "interrupted":
                        completed = [step for step in steps if step.get("status") == "completed"
                            and isinstance(step.get("payload"), dict)
                            and step["payload"].get("kind") == "team_member_v1"
                            and step["payload"].get("outcome") == "completed"
                            and step["payload"].get("stub") is False
                            and isinstance(step["payload"].get("workspace"), dict)
                            and step["payload"]["workspace"].get("run_id") == req.run_id
                            and step["payload"]["workspace"].get("state") == "completed"]
                        stop = meta.get("team_stop") or {}
                        job = get_run_service(ctx.server).get_job(req.team_run_id)
                        active = is_active(req.team_run_id) or bool(job and job.task and not job.task.done())
                        eligible = len(completed) == 1 and stop.get("version") == 1 and stop.get("tracked_tasks_drained") is True and not active
                        team_collection = {"eligible": eligible,
                            "step_id": completed[0]["id"] if len(completed) == 1 else None,
                            "reason": None if eligible else "成员终态、受管清理证据或团队停止状态不可确认"}
                        if req.action == "collect" and (not eligible or not req.confirmed_team_stopped
                                or req.expected_team_step_id != team_collection["step_id"]):
                            raise ValueError("Interrupted team requires confirmed stop and an unchanged completed member record")
                    root = require_directory(await resolve_root_for_session(req.session_id))
                    if meta.get("workspace_snapshot") != str(root):
                        raise ValueError("Team workspace binding changed")
                    identity = await ArtifactDAO(dao._db).service_id()
                    return root, session["project_id"], identity
                child_run = await dao.get_run(req.run_id)
                meta = (child_run or {}).get("metadata") or {}
                parent_id = meta.get("parent_run_id") if isinstance(meta, dict) else None
                parent_run_id = parent_id if isinstance(parent_id, str) else None
                parent = await dao.get_run(parent_id) if isinstance(parent_id, str) else None
                parent_meta = (parent or {}).get("metadata") or {}
                session = await dao._db.fetchone("SELECT project_id FROM sessions WHERE id=?", (req.session_id,))
                if (not child_run or child_run["mode"] != "subagent"
                        or meta.get("parent_session_id") != req.session_id or not parent
                        or parent["session_id"] != req.session_id or not session
                        or not isinstance(parent_meta, dict) or "project_id" not in parent_meta
                        or parent_meta["project_id"] != session["project_id"]):
                    raise ValueError("Child run or project binding is unavailable or changed")
                await require_host_session(child_run["session_id"])
                root = require_directory(await resolve_root_for_session(req.session_id))
                if parent_meta.get("workspace_snapshot") != str(root):
                    raise ValueError("Parent run workspace binding changed")
                identity = await ArtifactDAO(dao._db).service_id()
                return root, session["project_id"], identity

            async with child_review_lock:
                root, project_id, identity = await binding()
                if req.action == "collect" and (req.expected_service_id != identity or not req.revision):
                    raise ValueError("Review this workspace again before collection")
                if get_background_run(req.run_id) is not None:
                    raise ValueError("Child is still running")
                child = load_child(root, req.run_id)
                if child is None:
                    raise ValueError("No independent workspace is available for this run")
                if req.action == "collect" and team_collection is not None and child.record.get("state") != "completed":
                    raise ValueError("Interrupted team member workspace is not confirmed completed")
                reviewed = await asyncio.to_thread(preview, child)
                if await binding() != (root, project_id, identity):
                    raise ValueError("Session, project or service changed during review")
                if team_collection is not None and reviewed["state"] not in {"completed", "collected"}:
                    team_collection = {**team_collection, "eligible": False,
                                       "reason": "成员工作区不再处于已完成状态"}
                if req.action == "collect":
                    if reviewed["revision"] != req.revision:
                        raise ValueError("Workspace changed since review; refresh before collection")
                    if any(item["conflict"] for item in reviewed["files"]):
                        raise ValueError("Conflicts must be resolved before collection")
                    # Cancellation must not leave an unobserved writer running.
                    def collect():
                        with execution_scope(ExecutionPolicy(workspace=root)):
                            return collect_child(child, on_conflict="fail")
                    task = asyncio.create_task(asyncio.to_thread(collect))
                    try:
                        report = await asyncio.shield(task)
                    except asyncio.CancelledError:
                        await task
                        raise
                    await ctx.reply({"report": report, "session_id": req.session_id,
                                     "project_id": project_id, "service_id": identity})
                else:
                    command = None
                    if project_id:
                        from ..storage.dao.projects import ProjectsDAO
                        from ..verification import normalize_validate_command
                        project = await ProjectsDAO(dao._db).get(project_id)
                        command = normalize_validate_command((project or {}).get("validate_command"))
                    await ctx.reply({**reviewed, "team_collection": team_collection, "parent_run_id": parent_run_id, "validate_command": command, "session_id": req.session_id,
                                     "project_id": project_id, "service_id": identity})
        except (ValidationError, ValueError, HandlerError, OSError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("child workspace review failed")
            await ctx.reply_error(INTERNAL_ERROR, "Child workspace review or collection failed; some files may have been collected")

    async def handle_verify_collected(params: Any, ctx: Context) -> None:
        from pathlib import Path
        from ..execution import ExecutionPolicy, execution_scope
        from ..runs import get_run_service
        from ..storage.dao.projects import ProjectsDAO
        from ..storage.dao.text_artifacts import ArtifactDAO
        from ..verification import normalize_validate_command, resolve_validate_timeout, run_validate_command, verification_evidence
        from ..workspace.child import load_child
        from ..workspace.child_review import preview
        from ..workspace_ctx import resolve_root_for_session
        from .handlers_permissions import _ensure_permission_store
        from .host_session_gate import require_host_session

        try:
            req = RunVerifyCollectedParams.model_validate(params)
            dao = await _get_dao()
            service = get_run_service(ctx.server)

            async def binding():
                await require_host_session(req.session_id)
                if bool(req.team_run_id) == bool(req.parent_run_id):
                    raise ValueError("Provide exactly one team or parent run")
                session = await dao._db.fetchone("SELECT project_id FROM sessions WHERE id=?", (req.session_id,))
                if not session or not session["project_id"]:
                    raise ValueError("Session project is unavailable")
                if req.team_run_id:
                    owner = await dao.get_run(req.team_run_id)
                    metadata = (owner or {}).get("metadata") or {}
                    result = metadata.get("team_result") if isinstance(metadata, dict) else None
                    agents = result.get("agents_run") if isinstance(result, dict) else None
                    if (not owner or owner["mode"] != "team" or owner["session_id"] != req.session_id
                            or owner["status"] not in {"completed", "failed"} or not isinstance(agents, list)
                            or not any(isinstance(a, dict) and isinstance(a.get("workspace"), dict)
                                       and a["workspace"].get("run_id") == req.child_run_id for a in agents)):
                        raise ValueError("Team or member binding changed")
                else:
                    from ..orchestrator.subagent import get_background_run
                    child_run = await dao.get_run(req.child_run_id)
                    child_meta = (child_run or {}).get("metadata") or {}
                    owner = await dao.get_run(req.parent_run_id)
                    metadata = (owner or {}).get("metadata") or {}
                    if (not child_run or child_run["mode"] != "subagent"
                            or child_meta.get("parent_session_id") != req.session_id
                            or child_meta.get("parent_run_id") != req.parent_run_id
                            or not owner or owner["session_id"] != req.session_id
                            or owner["status"] not in {"completed", "failed", "interrupted", "cancelled", "budget_paused"}
                            or get_background_run(req.child_run_id) is not None):
                        raise ValueError("Child or parent run binding changed or remains active")
                    await require_host_session(child_run["session_id"])
                if not isinstance(metadata, dict) or metadata.get("project_id") != session["project_id"]:
                    raise ValueError("Run project binding changed")
                root = await resolve_root_for_session(req.session_id)
                identity = await ArtifactDAO(dao._db).service_id()
                project = await ProjectsDAO(dao._db).get(session["project_id"])
                command = normalize_validate_command((project or {}).get("validate_command"))
                if (identity != req.expected_service_id or str(root) != metadata.get("workspace_snapshot")
                        or command != req.expected_command):
                    raise ValueError("Service, workspace or validation command changed; review again")
                child = load_child(root, req.child_run_id)
                if child is None:
                    raise ValueError("Collected workspace is unavailable")
                review = await asyncio.to_thread(preview, child)
                if (review["state"] != "collected" or review["revision"] != req.revision
                        or not isinstance(review.get("receipt"), dict)
                        or review["receipt"].get("complete") is not True):
                    raise ValueError("Collected workspace or parent files changed; review again")
                permissions = await _ensure_permission_store(ctx.server)
                if permissions.is_denied("project_validation") or permissions.is_denied("exec_command"):
                    raise ValueError("Project validation is denied by the current permission rules")
                return str(root), session["project_id"]

            workspace, project_id = await binding()
            claim_unconfirmed = False
            async def operation(job):
                nonlocal claim_unconfirmed
                from ..run_errors import RunPersistenceError

                if await binding() != (workspace, project_id):
                    raise ValueError("Project binding changed while queued")
                # Do not dispatch a potentially side-effecting command unless
                # this exact queued run has been durably claimed once.
                claim_unconfirmed = True
                claimed = await dao.claim_run(job.run_id)
                if (not claimed or claimed.get("id") != job.run_id
                        or claimed.get("session_id") != req.session_id
                        or claimed.get("status") != "running"):
                    raise RunPersistenceError("Validation run claim could not be confirmed")
                claim_unconfirmed = False
                with execution_scope(ExecutionPolicy(workspace=Path(workspace))):
                    output = await run_validate_command(req.expected_command, cwd=workspace,
                                                        timeout_s=resolve_validate_timeout())
                evidence = verification_evidence(configured=True, validation=output)
                job.result = {"run_id": job.run_id, "session_id": req.session_id,
                              "team_run_id": req.team_run_id, "parent_run_id": req.parent_run_id, "child_run_id": req.child_run_id,
                              "verification": evidence}

            async def finalize(job, error):
                from ..run_errors import RunPersistenceError

                # An uncertain claim must not be overwritten with a fabricated
                # terminal state, even if the database becomes available again.
                if claim_unconfirmed:
                    raise RunPersistenceError("Validation run claim is unconfirmed; inspect it before retrying")
                status = "completed" if error is None and job.result else "interrupted" if isinstance(error, asyncio.CancelledError) else "failed"
                result = job.result or {"run_id": job.run_id, "session_id": req.session_id,
                    "team_run_id": req.team_run_id, "parent_run_id": req.parent_run_id, "child_run_id": req.child_run_id,
                    "verification": {"verdict": "unknown", "reason": "validation_interrupted_or_failed", "requires_review": True}}
                saved = await dao.update_run_status(job.run_id, status=status,
                    error=str(error) if error and not isinstance(error, asyncio.CancelledError) else None, result=result)
                if (not saved or saved.get("id") != job.run_id
                        or saved.get("session_id") != req.session_id or saved.get("status") != status
                        or await dao.result_payload(job.run_id) != result):
                    raise RunPersistenceError("Validation result could not be confirmed; inspect the run before retrying")
                await ctx.emit("run.updated", {"run": saved})

            request = {"mode": "execute", "kind": "team_collection_validation", "team_run_id": req.team_run_id,
                       "child_run_id": req.child_run_id, "command": req.expected_command,
                       "revision": req.revision, "service_id": req.expected_service_id}
            if req.parent_run_id:
                request["parent_run_id"] = req.parent_run_id
            run, _, _ = await service.admit(dao=dao, session_id=req.session_id, request=request,
                request_key=req.idempotency_key, title="子任务收集后的项目验证",
                metadata={"manual_team_validation": True, "team_run_id": req.team_run_id,
                          "parent_run_id": req.parent_run_id, "child_run_id": req.child_run_id, "project_id": project_id,
                          "workspace_snapshot": workspace, "validate_command": req.expected_command},
                workspace=workspace, project_id=project_id, operation=operation, finalize=finalize)
            await ctx.reply({"run": run, "session_id": req.session_id, "project_id": project_id,
                             "service_id": req.expected_service_id})
        except (ValidationError, ValueError, HandlerError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("manual team validation admission failed")
            await ctx.reply_error(INTERNAL_ERROR, "Could not start validation; inspect the run list before retrying")

    async def handle_run_list(params: Any, ctx: Context) -> None:
        try:
            if params is None:
                params = {}
            check_params(params, expected_keys=set())
            dao = await _get_read_control_dao(ctx)
            session_id = params.get("session_id")
            status = params.get("status")
            # v1.4.0: optional mode filter — 'subagent' surfaces tool-path
            # sub-agent runs (see migration 028) without post-filtering.
            mode = params.get("mode")
            limit = params.get("limit", 20)
            offset = params.get("offset", 0)
            recovery = params.get("include_active", False)
            if not isinstance(recovery, bool):
                raise ValueError("include_active must be a boolean")
            if recovery:
                if not isinstance(session_id, str) or not session_id or status or mode or offset:
                    raise ValueError("include_active requires a session and no filters or offset")
                await ctx.reply(await dao.list_recovery_runs(session_id, limit=int(limit)))
                return
            runs = await dao.list_runs(
                session_id=str(session_id) if session_id else None,
                status=str(status) if status else None,
                mode=str(mode) if mode else None,
                limit=int(limit) if limit else 20,
                offset=int(offset) if offset else 0,
                order_by="created_at DESC",
            )
            await ctx.reply({"runs": runs})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except (TypeError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, f"invalid run.list params: {exc}")
        except Exception:
            logger.exception("run.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "run.list failed")

    async def handle_run_steps(params: Any, ctx: Context) -> None:
        try:
            check_params(params, expected_keys={"run_id"})
            run_id = str(params["run_id"])
            dao = await _get_read_control_dao(ctx)
            snapshot = await dao.snapshot(run_id)
            if snapshot is None:
                await ctx.reply_error(INVALID_PARAMS, f"unknown run_id: {run_id!r}")
                return
            snapshot["pending_ask_user"] = None
            snapshot["pending_native_permissions"] = []
            snapshot["pending_codex_permissions"] = []
            snapshot["queued_input"] = None
            if not snapshot["run"].get("started_at"):
                saved = await dao.request_payload(run_id)
                content = saved.get("content") if isinstance(saved, dict) else None
                if isinstance(content, str):
                    snapshot["queued_input"] = {"text": content[:8000], "truncated": len(content) > 8000}
            service = getattr(ctx.server, "_run_service", None)
            job = service.get_job(run_id) if service is not None else None
            snapshot["live_stream"] = (job.live_stream.snapshot()
                if job is not None and job.started and job.session_id == snapshot["run"]["session_id"] else None)
            from ..engines.codex_run import CodexApprovalGate
            from ..perm_consent import PermissionGater

            session_id = snapshot["run"]["session_id"]
            registry = getattr(ctx.server, "_permission_gaters", {})
            gater = registry.get(session_id) if isinstance(registry, dict) else None
            if isinstance(gater, PermissionGater):
                snapshot["pending_native_permissions"] = gater.live_requests(session_id, run_id)
            elif isinstance(gater, CodexApprovalGate):
                snapshot["pending_codex_permissions"] = gater.live_requests(session_id, run_id)
            if isinstance(registry, dict):
                for candidate in registry.values():
                    if isinstance(candidate, PermissionGater) and candidate is not gater and candidate.run_id == run_id:
                        snapshot["pending_native_permissions"].extend(candidate.live_requests(session_id, run_id))
            live = getattr(ctx.server, "_live_ask_users", {}).get(run_id)
            if live:
                event, future, deadline = live
                if (future is not None and not future.done()
                        and event.get("session_id") == snapshot["run"]["session_id"]
                        and deadline > asyncio.get_running_loop().time()):
                    snapshot["pending_ask_user"] = {**event, "remaining_ms": max(0, int(
                        (deadline - asyncio.get_running_loop().time()) * 1000))}
            await ctx.reply(snapshot)
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:
            logger.exception("run.steps failed")
            await ctx.reply_error(INTERNAL_ERROR, "run.steps failed")

    async def handle_run_events(params: Any, ctx: Context) -> None:
        try:
            request = RunEventsParams.model_validate(params)
            dao = await _get_read_control_dao(ctx)
            result = await dao.events(**request.model_dump())
            if result is None:
                raise HandlerError(INVALID_PARAMS, "unknown run_id")
            await ctx.reply(result)
        except ValidationError:
            await ctx.reply_error(INVALID_PARAMS, "invalid run.events params")
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("run.events failed")
            await ctx.reply_error(INTERNAL_ERROR, "run.events failed")

    async def handle_run_start(params: Any, ctx: Context) -> None:
        from .builtins import handle_agent_send_message

        try:
            request = RunStartParams.model_validate(params)
        except ValidationError:
            await ctx.reply_error(INVALID_PARAMS, "run.start requires a session, input and idempotency key; only native is currently supported")
            return
        try:
            from ..app import get_db, init_runtime
            from .host_session_gate import require_chat_session

            await init_runtime()
            await require_chat_session(request.session_id)
            db = get_db()
            session = await db.fetchone("SELECT engine FROM sessions WHERE id=?", (request.session_id,)) if db else None
            pinned = session["engine"] if session else (request.engine or "native")
            if request.engine is not None and request.engine != pinned:
                raise HandlerError(INVALID_PARAMS, "Requested engine differs from the session's pinned engine")
            if pinned == "codex":
                await _start_codex_run(request, ctx)
            else:
                await handle_agent_send_message(request.model_dump(exclude={"engine"}), ctx, wait=False)
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except (ValueError, RuntimeError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))

    async def handle_run_get(params: Any, ctx: Context) -> None:
        try:
            request = RunIdParams.model_validate(params)
            dao = await _get_read_control_dao(ctx)
            run = await dao.get_run(request.run_id)
            if run is None:
                raise HandlerError(INVALID_PARAMS, "unknown run_id")
            await ctx.reply({"run": run, "result": await dao.result_payload(request.run_id)})
        except ValidationError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)

    async def handle_run_cancel(params: Any, ctx: Context) -> None:
        from ..runs import get_run_service

        try:
            request = RunIdParams.model_validate(params)
            dao = await _get_read_control_dao(ctx)
            run = await dao.get_run(request.run_id)
            if run is None:
                raise HandlerError(INVALID_PARAMS, "unknown run_id")
            service = get_run_service(ctx.server)
            # Wait for an admission already committing before looking up its job.
            # The service fences queued work before yielding and shields the durable
            # cancellation from an RPC disconnect.
            job = await service.job_for_run(request.run_id)
            queued_job = job is not None and not job.operation_started
            requested = await service.cancel_run_durable(request.run_id)
            if run.get("mode") == "team" and not requested:
                from ..orchestrator.team_control import cancel as cancel_team
                requested = cancel_team(request.run_id)
            if queued_job and job.cancel_requested:
                from .builtins import _notify_run
                run = await dao.get_run(request.run_id)
                if run is not None:
                    await _notify_run(ctx, "run.updated", {"run": run})
            await ctx.reply({"ok": True, "requested": requested, "run": run})
        except ValidationError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)

    async def handle_run_resume(params: Any, ctx: Context) -> None:
        from .builtins import handle_agent_send_message

        try:
            request = RunResumeParams.model_validate(params)
            dao = await _get_dao()
            from .host_session_gate import require_public_recovery

            await require_public_recovery(None, request.run_id)
            run = await dao.get_run(request.run_id)
            if run is None or run["status"] not in {"interrupted", "failed", "cancelled", "budget_paused"}:
                raise HandlerError(INVALID_PARAMS, "Only interrupted, failed, cancelled or budget-paused runs can be resumed")
            if run.get("mode") == "team":
                raise HandlerError(INVALID_PARAMS, "Team runs cannot be resumed; review their results and start a new run explicitly")
            if (run.get("metadata") or {}).get("manual_team_validation"):
                raise HandlerError(INVALID_PARAMS, "Review validation results before starting a new explicit validation")
            bound = {"expected_service_id", "expected_session_id", "expected_project_id", "expected_cursor"}
            if bound & request.model_fields_set:
                if not bound <= request.model_fields_set or not request.expected_service_id or not request.expected_session_id or request.expected_cursor is None:
                    raise HandlerError(INVALID_PARAMS, "Recovery review binding is incomplete")
                from ..storage.dao.text_artifacts import ArtifactDAO
                identity = await ArtifactDAO(dao._db).service_id()
                source = await dao.snapshot(request.run_id)
                session = await dao._db.fetchone("SELECT project_id FROM sessions WHERE id=?", (run["session_id"],))
                if (identity != request.expected_service_id or source is None or source["cursor"] != request.expected_cursor
                        or source["run"]["session_id"] != request.expected_session_id or source["run"]["status"] != run["status"]
                        or not session or session["project_id"] != request.expected_project_id
                        or (source["run"].get("metadata") or {}).get("project_id") != request.expected_project_id):
                    raise HandlerError(INVALID_PARAMS, "Recovery review changed; inspect the run again")
            previous = await dao.request_payload(request.run_id)
            metadata = run.get("metadata") or {}
            budget_paused = run["status"] == "budget_paused"
            recovery_requires_review = False
            if run["status"] == "interrupted":
                # Startup closes paused runs as interrupted, but records the
                # previous status in the recovery step. Preserve its budget
                # boundary semantics across restarts, including older records.
                for step in reversed(await dao.list_steps(run["id"])):
                    payload = step.get("payload") or {}
                    if isinstance(payload, dict) and "recovered_at" in payload:
                        budget_paused = payload.get("previous_status") == "budget_paused"
                        recovery_requires_review = payload.get("requires_review") is not False
                        break
            if metadata.get("engine") == "codex":
                from .host_session_gate import require_chat_session

                await require_chat_session(run["session_id"])
                from ..engines.codex import SUPPORTED_PROTOCOL_PIN
                if previous is None or not metadata.get("codex_thread_id") or not metadata.get("workspace_snapshot"):
                    raise HandlerError(INVALID_PARAMS, "Codex recovery has no saved thread/context; create a new session with a handoff")
                if metadata.get("codex_protocol") != SUPPORTED_PROTOCOL_PIN:
                    raise HandlerError(INVALID_PARAMS, "Codex recovery protocol is incompatible")
                if run.get("started_at") and not request.reviewed:
                    raise HandlerError(INVALID_PARAMS, "Review Codex's prior actions and workspace before recovery, including budget interruption")
                saved = (metadata.get("budget") or {}).get("limit_tokens")
                prompt = (f"[Recovery of Codex run {run['id']} ending as {run['status']}. "
                          "Inspect the existing workspace and conversation. Determine which side effects already occurred "
                          "and verify them before taking further action. Continue only the unfinished work; "
                          "do not blindly replay the original instruction or repeat unconfirmed writes.]")
                recovery = RunStartParams(session_id=run["session_id"], engine="codex", mode=run["mode"],
                    content=prompt, idempotency_key=request.idempotency_key,
                    budget_tokens=request.budget_tokens if request.budget_tokens is not None else saved)
                await _start_codex_run(recovery, ctx, resumed_from_id=run["id"],
                                       reviewed=request.reviewed, expected_snapshot=metadata)
                return
            if previous is None or not metadata.get("model_snapshot") or not metadata.get("workspace_snapshot"):
                raise HandlerError(INVALID_PARAMS, "This legacy run has no saved request/context; start a new task with a handoff")
            # A budget pause is a clean stop at a call boundary — no
            # uncertain writes, so the review gate only applies to the
            # other resumable statuses.
            if (not budget_paused or recovery_requires_review) and run.get("started_at") and not request.reviewed:
                raise HandlerError(INVALID_PARAMS, "Review the prior steps and workspace before resuming; their effects will not be automatically replayed")
            # M2 — budget: explicit override wins; otherwise inherit the
            # original cap so a plain resume cannot silently exceed what
            # the caller first authorised.
            saved_budget = (metadata.get("budget") or {}).get("limit_tokens")
            budget_override = request.budget_tokens if request.budget_tokens is not None else saved_budget
            note = (
                f"\n\n[Runtime recovery context: prior run {run['id']} ended as {run['status']}. "
                "Continue the original task after inspecting the existing workspace and prior conversation. "
                "Verify which actions already took effect before repeating a write. No tool step has been automatically retried.]"
            )
            if budget_paused and not recovery_requires_review:
                note = (
                    f"\n\n[Budget recovery context: prior run {run['id']} paused at a model-call "
                    f"boundary. The workspace reflects only confirmed effects; continue the task "
                    "under the authorized cumulative budget.]"
                )
            content = previous["content"]
            if isinstance(content, list):
                content = [*content, {"type": "text", "text": note}]
            else:
                content += note
            saved_schedule = metadata.get("schedule")
            saved_skill = None
            if "skill_execution" in metadata or "skill_intent" in previous:
                from ..skill_execution import SkillExecutionSpec
                if "skill_intent" not in previous:
                    raise HandlerError(INVALID_PARAMS, "Saved skill intent is missing; start a new task")
                saved_skill = SkillExecutionSpec(metadata.get("skill_execution"))
            saved_agent = None
            if "agent_execution" in metadata or "agent_intent" in previous:
                from ..agent_execution import AgentExecutionSpec
                if "agent_intent" not in previous:
                    raise HandlerError(INVALID_PARAMS, "Saved agent intent is missing; start a new task")
                saved_agent = AgentExecutionSpec(metadata.get("agent_execution"))
                if not saved_agent.data["prepared"]:
                    raise HandlerError(INVALID_PARAMS, "Agent workspace preparation is unconfirmed; inspect it and start a new task")
                if not request.reviewed:
                    raise HandlerError(INVALID_PARAMS, "Review the agent workspace and prior actions before recovery")
            if saved_schedule and budget_override is not None:
                saved_schedule = {**saved_schedule, "context": {**saved_schedule["context"], "budget_tokens": budget_override}}
            await handle_agent_send_message(
                {**previous, "content": content, "idempotency_key": request.idempotency_key,
                 "budget_tokens": budget_override},
                ctx, wait=False, resumed_from_id=run["id"],
                required_model=metadata["model_snapshot"], required_workspace=metadata["workspace_snapshot"],
                required_permission_rules=metadata.get("permission_rules_snapshot"),
                internal_schedule=saved_schedule, internal_skill=saved_skill, internal_agent=saved_agent)
        except (ValidationError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)

    async def handle_run_steer(params: Any, ctx: Context) -> None:
        from ..run_steer import step_id
        from ..runs import get_run_service
        from .host_session_gate import require_chat_session

        try:
            request = RunSteerParams.model_validate(params)
            if not request.text.strip() or not request.idempotency_key.strip():
                raise ValueError("Input and idempotency key must not be blank")
            await require_chat_session(request.session_id)
            dao = await _get_dao()
            run = await dao.get_run(request.run_id)
            if not run or run["session_id"] != request.session_id:
                raise ValueError("Run and session do not match")
            metadata = run.get("metadata") or {}
            if metadata.get("engine") == "codex" or (metadata.get("model_snapshot") or {}).get("engine") != "native":
                raise ValueError("Only Native runs support in-flight input")
            session = await dao._db.fetchone("SELECT engine, project_id FROM sessions WHERE id=?", (request.session_id,))
            if (not session or session["engine"] != "native" or "project_id" not in metadata
                    or metadata["project_id"] != session["project_id"]):
                raise ValueError("Run session or project binding changed")
            existing = await dao.get_step(step_id(request.run_id, request.idempotency_key))
            if existing:
                if (existing.get("payload") or {}).get("text") != request.text:
                    raise ValueError("Idempotency key belongs to different input")
                await ctx.reply({"step": existing})
                return
            job = get_run_service(ctx.server).get_job(request.run_id)
            controller = job.steer_controller if job and job.session_id == request.session_id else None
            if controller is None:
                raise ValueError("This run is not accepting in-flight input")
            await ctx.reply({"step": await controller.accept(request.idempotency_key, request.text)})
        except (ValidationError, ValueError, HandlerError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("run.steer failed")
            await ctx.reply_error(INTERNAL_ERROR, "Could not record in-flight input")

    def research_handler(model, *, detail=False):
        async def handle(params, ctx):
            from ..agent.tools.research_sources import ResearchSources
            from .host_session_gate import require_host_session
            try:
                request = model.model_validate(params)
                await require_host_session(request.session_id)
                dao = await _get_dao()
                sources = ResearchSources(dao._db, request.session_id, request.project_id)
                identity = await sources._binding()
                if detail:
                    if request.expected_service_id != identity:
                        raise ValueError("Research service identity changed")
                    result = {"source": await sources.get_page(request.page_source_id)}
                else:
                    result = {"sources": await sources.list_pages(request.query, 20, request.offset)}
                if await sources._binding() != identity:
                    raise ValueError("Research service or project binding changed")
                await require_host_session(request.session_id)
                await ctx.reply({**result, "session_id": request.session_id,
                                 "project_id": request.project_id, "service_id": identity})
            except (ValidationError, ValueError, HandlerError) as exc:
                await ctx.reply_error(INVALID_PARAMS, str(exc))
            except Exception:
                logger.exception("research source query failed")
                await ctx.reply_error(INTERNAL_ERROR, "Research source query failed")
        return handle

    async def handle_project_summary(params, ctx):
        from ..runs import get_run_service
        from ..storage.dao.text_artifacts import ArtifactDAO
        from .host_session_gate import require_host_session
        try:
            request = RunProjectSummaryParams.model_validate(params)
            dao = await _get_read_control_dao(ctx)
            service = ArtifactDAO(dao._db)
            async def check_scope():
                project = await dao._db.fetchone("SELECT id FROM projects WHERE id=?", (request.project_id,))
                if not project or await service.service_id() != request.expected_service_id:
                    raise ValueError("Project or service identity changed")
            await check_scope()
            base = """FROM agent_runs r JOIN sessions s ON s.id=r.session_id
                WHERE s.project_id=? AND s.archived=0 AND r.mode!='subagent'
                AND json_valid(r.metadata) AND json_extract(r.metadata,'$.project_id')=?
                AND COALESCE(json_extract(r.metadata,'$.manual_research'),0)!=1"""
            fields = "r.id,r.session_id,r.title,r.status,r.created_at,r.completed_at,s.title AS session_title"
            live_service = get_run_service(ctx.server)
            if request.run_id and (request.history_query is not None or request.history_offset):
                raise ValueError("History search cannot be combined with run detail")
            if request.history_query is not None:
                # LIKE metacharacters are escaped so the query is a literal substring.
                term = request.history_query.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
                pattern = f"%{term}%"
                rows = await dao._db.fetchall(
                    f"SELECT {fields} {base} AND r.status IN ('completed','failed','cancelled','interrupted') "
                    "AND (r.title LIKE ? ESCAPE '\\' OR s.title LIKE ? ESCAPE '\\' OR r.id LIKE ? ESCAPE '\\') "
                    "ORDER BY COALESCE(r.completed_at,r.created_at) DESC,r.id DESC LIMIT 26 OFFSET ?",
                    (request.project_id, request.project_id, pattern, pattern, pattern, request.history_offset),
                )
                items = []
                allowed_sessions = {}
                for row in rows[:25]:
                    sid = row["session_id"]
                    if sid not in allowed_sessions:
                        try:
                            await require_host_session(sid)
                            allowed_sessions[sid] = True
                        except HandlerError:
                            allowed_sessions[sid] = False
                    if allowed_sessions[sid]:
                        items.append({**dict(row), "live": None})
                await check_scope()
                await ctx.reply({"project_id": request.project_id, "service_id": request.expected_service_id,
                    "history": {"items": items, "offset": request.history_offset, "has_more": len(rows) > 25}})
                return
            if request.run_id:
                row = await dao._db.fetchone(f"SELECT {fields} {base} AND r.id=?", (request.project_id, request.project_id, request.run_id))
                if not row:
                    raise ValueError("Run is unavailable in this project")
                await require_host_session(row["session_id"])
                receipt = await dao.result_payload(request.run_id)
                text = receipt.get("text") if isinstance(receipt, dict) and receipt.get("run_id") == request.run_id and receipt.get("session_id") == row["session_id"] else None
                if not isinstance(text, str):
                    text = None
                await check_scope()
                row2 = await dao._db.fetchone(f"SELECT {fields} {base} AND r.id=?", (request.project_id, request.project_id, request.run_id))
                if not row2 or row2["session_id"] != row["session_id"]:
                    raise ValueError("Run project association changed")
                await ctx.reply({"project_id": request.project_id, "service_id": request.expected_service_id,
                    "detail": {**dict(row2), "text": text[:8000] if text is not None else None,
                               "text_truncated": text is not None and len(text) > 8000}})
                return
            groups = {"attention": ("awaiting_approval", "waiting_input", "budget_paused", "interrupted"),
                      "active": ("queued", "planning", "running"),
                      "recent": ("completed", "failed", "cancelled", "interrupted")}
            result = {}
            allowed_sessions = {}
            for group, statuses in groups.items():
                placeholders = ",".join("?" for _ in statuses)
                ordering = "COALESCE(r.completed_at,r.created_at)" if group == "recent" else "r.created_at"
                rows = await dao._db.fetchall(f"SELECT {fields} {base} AND r.status IN ({placeholders}) ORDER BY {ordering} DESC,r.id DESC LIMIT 26", (request.project_id, request.project_id, *statuses))
                items = []
                for row in rows[:25]:
                    sid = row["session_id"]
                    if sid not in allowed_sessions:
                        try:
                            await require_host_session(sid)
                            allowed_sessions[sid] = True
                        except HandlerError:
                            allowed_sessions[sid] = False
                    if not allowed_sessions[sid]:
                        continue
                    job = live_service.get_job(row["id"])
                    item = dict(row)
                    item["live"] = bool(job and job.session_id == row["session_id"]) if row["status"] in {"queued", "planning", "running", "awaiting_approval", "waiting_input"} else None
                    items.append(item)
                result[group] = {"items": items, "truncated": len(rows) > 25}
            await check_scope()
            await ctx.reply({"project_id": request.project_id, "service_id": request.expected_service_id, "groups": result})
        except (ValidationError, ValueError, HandlerError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("run project summary failed")
            await ctx.reply_error(INTERNAL_ERROR, "Project run summary unavailable")

    server.register("run.project_summary", handle_project_summary)
    def manual_research_handler(model, *, read=False):
        async def handle(params, ctx):
            from ..agent.core import _check_rule
            from ..agent.tools.public_research import ReadPublicPageTool, SearchPublicWebTool
            from ..agent.tools.research_sources import ResearchSources
            from .handlers_permissions import _ensure_permission_store
            from .host_session_gate import require_host_session
            try:
                request = model.model_validate(params)
                await require_host_session(request.session_id)
                dao = await _get_dao()
                sources = ResearchSources(dao._db, request.session_id, request.project_id)
                if await sources._binding() != request.expected_service_id:
                    raise ValueError("Research service identity changed")
                permissions = await _ensure_permission_store(ctx.server)
                name = "read_public_page" if read else "search_public_web"
                if "deny" in {_check_rule(permissions, "network"), _check_rule(permissions, name)}:
                    raise ValueError("Network access is denied by permission policy")
                # The explicit UI action authorizes this one request; it does not
                # change permission rules or authorize future Agent tool calls.
                result = await (ReadPublicPageTool().run(request.url) if read else SearchPublicWebTool().run(request.query))
                if not result.success:
                    raise ValueError(result.error or "Research request failed")
                if await sources._binding() != request.expected_service_id:
                    raise ValueError("Research service or project binding changed")
                await require_host_session(request.session_id)
                payload = result.output
                if read:
                    page_id, run_id = await sources.remember_manual_page(payload, request.expected_service_id)
                    payload = {**payload, "page_source_id": page_id, "run_id": run_id}
                await ctx.reply({"session_id": request.session_id, "project_id": request.project_id,
                    "service_id": request.expected_service_id, "result": payload})
            except (ValidationError, ValueError, HandlerError) as exc:
                await ctx.reply_error(INVALID_PARAMS, str(exc))
            except Exception:
                logger.exception("manual research failed")
                await ctx.reply_error(INTERNAL_ERROR, "Research operation failed")
        return handle

    server.register("run.research_search", manual_research_handler(RunResearchSearchParams))
    server.register("run.research_read", manual_research_handler(RunResearchReadParams, read=True))
    server.register("run.list", handle_run_list)
    server.register("run.steps", handle_run_steps)
    server.register("run.events", handle_run_events)
    server.register("run.start", handle_run_start)
    server.register("run.get", handle_run_get)
    server.register("run.cancel", handle_run_cancel)
    server.register("run.resume", handle_run_resume)
    server.register("run.steer", handle_run_steer)
    server.register("run.research_list", research_handler(RunResearchListParams))
    server.register("run.research_get", research_handler(RunResearchGetParams, detail=True))
    server.register("run.child_workspace", handle_child_workspace)
    server.register("run.verify_collected", handle_verify_collected)
    server.register("run.child_list", handle_child_list)
    server.register("run.output_page", handle_output_page)
    server.register("run.output_inventory", handle_output_inventory)
    server.register("run.output_clear", handle_output_clear)


async def _start_codex_run(request: RunStartParams, ctx: Context, **options: Any) -> None:
    from .host_session_gate import require_chat_session

    await require_chat_session(request.session_id)
    from ..engines.codex_service import start_codex_run
    await start_codex_run(request, ctx, **options)


__all__ = ["register_run_handlers"]
