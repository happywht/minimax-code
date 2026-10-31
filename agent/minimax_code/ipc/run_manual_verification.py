"""Explicit, bound project verification after a completed coding run."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from pydantic import ValidationError

from ..agent.core import _check_rule
from ..execution import ExecutionPolicy, execution_scope
from ..perm_consent import PermissionGater, register_gater, unregister_gater
from ..run_errors import RunPersistenceError
from ..runs import get_run_service
from ..storage.dao.projects import ProjectsDAO
from ..storage.dao.text_artifacts import ArtifactDAO
from ..verification import normalize_validate_command, resolve_validate_timeout, run_validate_command, verification_evidence
from ..workspace_ctx import resolve_root_for_session
from .handler_utils import HandlerError
from .host_session_gate import require_host_session
from .protocol import INTERNAL_ERROR, INVALID_PARAMS, RunVerifyProjectParams

logger = logging.getLogger(__name__)


async def verify_project(params, ctx):
    from .handlers_permissions import _ensure_permission_store
    from .handlers_runs import _get_dao

    try:
        req = RunVerifyProjectParams.model_validate(params)
        dao = await _get_dao()
        service = get_run_service(ctx.server)

        async def binding():
            await require_host_session(req.session_id)
            session = await dao._db.fetchone("SELECT project_id,engine,archived FROM sessions WHERE id=?", (req.session_id,))
            snapshot = await dao.snapshot(req.source_run_id)
            source = snapshot["run"] if snapshot else None
            metadata = (source or {}).get("metadata") or {}
            if not isinstance(metadata, dict):
                raise ValueError("Invalid source run metadata; review again")
            project = await ProjectsDAO(dao._db).get(req.project_id)
            root = await resolve_root_for_session(req.session_id)
            info = root.stat()
            identity = f"{info.st_dev}:{info.st_ino}"
            model_snapshot = metadata.get("model_snapshot")
            engine = metadata.get("engine") or (model_snapshot.get("engine") if isinstance(model_snapshot, dict) else None)
            if (not session or session["archived"] or session["project_id"] != req.project_id
                    or session["engine"] not in {"native", "codex"} or engine != session["engine"]
                    or not source or source["session_id"] != req.session_id
                    or source["mode"] not in {"chat", "execute"}
                    or source["status"] not in {"completed", "failed", "interrupted", "cancelled", "budget_paused"}
                    or metadata.get("project_id") != req.project_id or metadata.get("workspace_snapshot") != str(root)
                    or metadata.get("manual_project_validation") or metadata.get("manual_team_validation")
                    or snapshot["cursor"] != req.expected_cursor
                    or not project or project.get("archived") or str(root) != req.expected_workspace
                    or identity != req.expected_workspace_identity
                    or normalize_validate_command(project.get("validate_command")) != req.expected_command
                    or await ArtifactDAO(dao._db).service_id() != req.expected_service_id):
                raise ValueError("Run, project, service, workspace or validation command changed; review again")
            permissions = await _ensure_permission_store(ctx.server)
            if permissions is None:
                raise ValueError("Permission policy is unavailable")
            actions = {_check_rule(permissions, "project_validation"), _check_rule(permissions, "exec_command")}
            if "deny" in actions:
                raise ValueError("Project validation is denied by current permission rules")
            return actions

        await binding()
        claim_unconfirmed = False

        async def operation(job):
            nonlocal claim_unconfirmed
            await binding()
            claim_unconfirmed = True
            claimed = await dao.claim_run(job.run_id)
            if not claimed or claimed.get("id") != job.run_id or claimed.get("session_id") != req.session_id or claimed.get("status") != "running":
                raise RunPersistenceError("Validation claim is unconfirmed; inspect before retrying")
            claim_unconfirmed = False
            actions = await binding()
            approved = False
            if "ask" in actions:
                gater = PermissionGater(emit=ctx.emit, run_id=job.run_id, explicit_once=True)
                register_gater(ctx.server, req.session_id, gater)
                try:
                    saved = await dao.update_run_status(job.run_id, status="awaiting_approval")
                    if not saved or saved.get("id") != job.run_id or saved.get("session_id") != req.session_id or saved.get("status") != "awaiting_approval":
                        raise RunPersistenceError("Approval state could not be saved")
                    await ctx.emit("run.updated", {"run": saved})
                    approved = await gater.request_consent(tool="project_validation", args={
                        "command": req.expected_command, "cwd": req.expected_workspace,
                        "scope": "host shell; unrestricted filesystem and network"}, session_id=req.session_id)
                finally:
                    unregister_gater(ctx.server, req.session_id)
                if not approved:
                    raise ValueError("Project validation approval was denied or expired")
                saved = await dao.update_run_status(job.run_id, status="running")
                if not saved or saved.get("id") != job.run_id or saved.get("session_id") != req.session_id or saved.get("status") != "running":
                    raise RunPersistenceError("Validation state could not be saved")
                await ctx.emit("run.updated", {"run": saved})
            actions = await binding()
            if "ask" in actions and not approved:
                raise ValueError("Project validation requires approval")
            device, inode = (int(value) for value in req.expected_workspace_identity.split(":"))
            with execution_scope(ExecutionPolicy(workspace=Path(req.expected_workspace), _workspace_identity=(device, inode))):
                output = await run_validate_command(req.expected_command, cwd=req.expected_workspace,
                                                    timeout_s=resolve_validate_timeout())
            evidence = verification_evidence(configured=True, validation=output)
            try:
                await binding()
            except Exception:
                # Keep the real command output but never claim it validates a
                # workspace or authority that changed during execution.
                evidence.update(verdict="unknown", reason="validation_binding_changed", requires_review=True)
            job.result = {"run_id": job.run_id, "session_id": req.session_id, "source_run_id": req.source_run_id,
                          "verification": evidence}

        async def finalize(job, error):
            if claim_unconfirmed:
                raise RunPersistenceError("Validation claim is unconfirmed; inspect before retrying")
            status = "completed" if error is None and job.result else "interrupted" if isinstance(error, asyncio.CancelledError) else "failed"
            result = job.result or {"run_id": job.run_id, "session_id": req.session_id, "source_run_id": req.source_run_id,
                "verification": {"version": 1, "verdict": "unknown", "reason": "validation_interrupted_or_failed", "requires_review": True}}
            previous = await dao.get_run(job.run_id)
            saved = await dao.update_run_status(job.run_id, status=status,
                error=str(error) if error and not isinstance(error, asyncio.CancelledError) else None,
                result=result, metadata={**(previous.get("metadata") or {}), "verification": result["verification"]})
            if (not saved or saved.get("id") != job.run_id or saved.get("session_id") != req.session_id
                    or saved.get("status") != status or await dao.result_payload(job.run_id) != result):
                raise RunPersistenceError("Validation result is unconfirmed; inspect before retrying")
            await ctx.emit("run.updated", {"run": saved})

        request = {"mode": "execute", "kind": "manual_project_validation", "source_run_id": req.source_run_id,
                   "command": req.expected_command, "cursor": req.expected_cursor, "service_id": req.expected_service_id,
                   "workspace": req.expected_workspace, "workspace_identity": req.expected_workspace_identity}
        run, _, created = await service.admit(dao=dao, session_id=req.session_id, request=request,
            request_key=req.idempotency_key, title="手动项目验证",
            metadata={"manual_project_validation": True, "source_run_id": req.source_run_id, "project_id": req.project_id,
                      "workspace_snapshot": req.expected_workspace, "validate_command": req.expected_command},
            workspace=req.expected_workspace, project_id=req.project_id, operation=operation, finalize=finalize)
        await ctx.reply({"run": run, "created": created, "session_id": req.session_id,
                         "project_id": req.project_id, "service_id": req.expected_service_id})
    except (ValidationError, ValueError, HandlerError) as exc:
        await ctx.reply_error(INVALID_PARAMS, str(exc))
    except Exception:
        logger.exception("manual project validation admission failed")
        await ctx.reply_error(INTERNAL_ERROR, "Validation admission not confirmed; inspect the run list before retrying")
