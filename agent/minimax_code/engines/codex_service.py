"""Codex admission and RunService integration; conversations own durable event writes."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from ..ipc.handler_utils import HandlerError
from ..storage.dao.runs import AgentRunsDAO
from .codex_persistence import CodexConversation


async def start_codex_run(request: Any, ctx: Any, *, resumed_from_id: str | None = None,
                         reviewed: bool = False, expected_snapshot: dict[str, Any] | None = None) -> None:
    from ..app import get_db, get_sessions_dao, init_runtime
    from ..budget import daily_budget_limit, resolve_run_budget
    from ..ipc.builtins import _notify_run
    from ..perm_consent import register_gater, unregister_gater
    from ..runs import get_run_service
    from ..workspace_ctx import resolve_root_for_session
    from . import get_engine
    from .codex_run import CodexApprovalGate, drive_codex_run

    if not isinstance(request.content, str) or not request.content.strip():
        raise HandlerError(-32602, "Codex supports text only; attachments and multimodal input are unsupported")
    await init_runtime()
    db = get_db()
    if db is None:
        raise HandlerError(-32004, "Database unavailable")
    sessions = get_sessions_dao()
    session = await sessions.get(request.session_id)
    if session is None:
        session = await sessions.create(id=request.session_id, title=request.content.strip()[:32], engine="codex")
    if session.get("engine") != "codex":
        raise HandlerError(-32602, "Codex requires a pinned Codex session")
    payload = {"session_id": request.session_id, "engine": "codex", "mode": request.mode,
               "content": request.content, "budget_tokens": request.budget_tokens,
               "resumed_from_id": resumed_from_id}
    if request.capture_file_changes:
        payload["capture_file_changes"] = True
    dao, service = AgentRunsDAO(db), get_run_service(ctx.server)
    existing = await dao.find_request(request.session_id, request.idempotency_key, payload)
    if existing is not None:
        await ctx.reply({"run": existing, "created": False})
        return
    info = await get_engine("codex").info()
    if not info.available:
        raise HandlerError(-32602, f"Codex engine unavailable: {info.unavailable_reason}", {"engine": info.to_dict()})
    workspace = str(await resolve_root_for_session(request.session_id))
    project_id = session.get("project_id")
    validate_command = ""
    if project_id:
        from ..storage.dao.projects import ProjectsDAO
        from ..verification import normalize_validate_command

        project = await ProjectsDAO(db).get(str(project_id))
        if project is None:
            raise HandlerError(-32602, "Project is unavailable; rebind the session before running")
        validate_command = normalize_validate_command(project.get("validate_command"))
    state = session.get("engine_state") or {}
    if state.get("handoff_required"):
        raise HandlerError(-32602, "Ambiguous legacy Codex session; create a new session with a handoff")
    if state.get("workspace") and state["workspace"] != workspace:
        raise HandlerError(-32602, "Codex workspace changed; restore its binding or start a new session")
    if expected_snapshot and (session.get("engine_thread_id") != expected_snapshot.get("codex_thread_id")
                              or workspace != expected_snapshot.get("workspace_snapshot")):
        raise HandlerError(-32602, "Codex recovery thread or workspace binding differs from the original run")
    budget = resolve_run_budget(request.budget_tokens)
    daily = daily_budget_limit()
    baseline = 0  # The shared ledger reads current daily liabilities atomically.

    async def emit(event: str, data: dict[str, Any]) -> None:
        await _notify_run(ctx, event, data)

    async def validate(job: Any, conversation: CodexConversation, receipt: dict[str, Any]) -> dict[str, Any]:
        from ..execution import ExecutionPolicy, execution_scope
        from ..perm_consent import PermissionGater, register_gater
        from ..verification import (
            resolve_validate_timeout,
            run_validate_command,
            verification_evidence,
        )

        status = receipt["run_status"]
        evidence = verification_evidence(status=status, engine="codex", mode=request.mode,
            configured=bool(job.validate_command), codex_validation_enabled=True)
        receipt["verification"] = evidence
        if status != "completed" or request.mode == "plan" or not job.validate_command:
            return receipt

        async def still_bound() -> bool:
            from ..storage.dao.projects import ProjectsDAO
            current = await sessions.get(job.session_id)
            return (current is not None and current.get("project_id") == job.project_id
                    and (not job.project_id or await ProjectsDAO(db).get(str(job.project_id)) is not None)
                    and str(await resolve_root_for_session(job.session_id)) == job.workspace)

        def unverified(reason: str) -> dict[str, Any]:
            receipt["verification"] = {**evidence, "verdict": "unknown", "reason": reason, "requires_review": True}
            return receipt

        try:
            if not await still_bound():
                return unverified("validation_workspace_changed")
        except (OSError, ValueError, RuntimeError):
            return unverified("validation_workspace_changed")
        gater = PermissionGater(emit=emit, run_id=job.run_id, explicit_once=True)
        register_gater(ctx.server, job.session_id, gater)
        job.result = {**receipt, "run_status": "awaiting_approval"}
        await conversation._save("awaiting_approval", message=True, result={**receipt, "run_status": "awaiting_approval"})
        await emit("run.updated", {"run": await dao.get_run(job.run_id)})
        job.cancel_callback = gater.cancel_all
        try:
            allowed = await gater.request_consent(tool="project_validation", args={
                "command": job.validate_command, "cwd": job.workspace,
                "scope": "host shell; unrestricted filesystem and network"}, session_id=job.session_id)
        finally:
            job.cancel_callback = None
        if not allowed:
            return unverified("validation_denied")
        try:
            if job.cancel_requested:
                raise asyncio.CancelledError
            if not await still_bound():
                return unverified("validation_workspace_changed")
        except (OSError, ValueError, RuntimeError):
            return unverified("validation_workspace_changed")
        await conversation._save("running", message=True, result={**receipt, "run_status": "running"})
        await emit("run.updated", {"run": await dao.get_run(job.run_id)})
        with execution_scope(ExecutionPolicy(workspace=Path(job.workspace))):
            result = await run_validate_command(job.validate_command, cwd=job.workspace, timeout_s=resolve_validate_timeout())
        if job.cancel_requested:
            raise asyncio.CancelledError
        receipt["verification"] = verification_evidence(status=status, engine="codex", mode=request.mode,
            configured=True, validation=result, codex_validation_enabled=True)
        return receipt

    async def operation(job: Any) -> None:
        from ..budget_ledger import BudgetHandle
        conversation = CodexConversation(db, job)
        conversation.budget_handle = await BudgetHandle.for_run(db, job.run_id, daily_limit=daily, enforcement="reactive")
        job.codex_conversation = conversation
        # Re-read after waiting for the previous job, never capture an empty thread ID at admission.
        await conversation.load()
        current_session = await sessions.get(job.session_id)
        current_workspace = str(await resolve_root_for_session(job.session_id))
        latest_session = await sessions.get(job.session_id)
        if (not current_session or not latest_session
                or current_session.get("engine") != "codex" or latest_session.get("engine") != "codex"
                or current_session.get("project_id") != job.project_id
                or latest_session.get("project_id") != job.project_id
                or current_workspace != job.workspace):
            raise ValueError("Codex session project, engine or workspace changed while queued; start a new task")
        if expected_snapshot and conversation.thread_id != expected_snapshot["codex_thread_id"]:
            raise ValueError("Codex recovery thread binding changed while queued")
        # A queued task must not bypass an earlier task's uncertain outcome.
        others = await dao.list_runs(session_id=job.session_id, limit=1000)
        for other in others:
            md = other.get("metadata") or {}
            if (other["id"] != job.run_id and other.get("started_at") and md.get("engine") == "codex"
                    and other["status"] in {"failed", "interrupted", "cancelled", "budget_paused"}
                    and not md.get("recovery_child_id")):
                raise ValueError("An earlier Codex task needs review before this queued input can execute")
        claimed = await dao.claim_run(job.run_id)
        if claimed is None:
            raise RuntimeError("Codex run could not be claimed")
        if conversation.thread_id:
            await conversation._save("running")  # Capture known identity even if thread/resume fails.
        gate = CodexApprovalGate(session_id=job.session_id, run_id=job.run_id)
        register_gater(ctx.server, job.session_id, gate)
        try:
            result = await drive_codex_run(job, emit=emit, prompt=request.content,
                resume_thread_id=conversation.thread_id, conversation=conversation,
                approval_gate=gate, mode=request.mode, post_turn=validate)
            job.result = result
        finally:
            unregister_gater(ctx.server, job.session_id)

    async def finalize(job: Any, error: BaseException | None) -> None:
        run = await dao.get_run(job.run_id)
        if run["status"] not in {"completed", "failed", "cancelled", "interrupted", "budget_paused"}:
            status = "cancelled" if isinstance(error, asyncio.CancelledError) or job.cancel_requested else "failed"
            result = {"session_id": job.session_id, "run_id": job.run_id, "message_id": job.message_id,
                      "text": "", "engine": "codex", **(job.result or {}),
                      "run_status": status, "cancelled": status == "cancelled"}
            from ..verification import verification_evidence
            result["verification"] = verification_evidence(status=status, engine="codex")
            run = await dao.terminate_run(job.run_id, status=status,
                error=str(error or "Codex run ended without confirmed result"), result=result)
        await _notify_run(ctx, "run.completed", {"run": run})

    run, job, created = await service.admit(dao=dao, session_id=request.session_id, request=payload,
        request_key=request.idempotency_key, title=request.content.strip()[:32],
        metadata={"engine": "codex", "engine_card": info.to_dict(), "reviewed": reviewed,
                  "workspace_snapshot": workspace,
                  "project_id": project_id,
                  "validate_command": validate_command,
                  "budget": {"limit_tokens": budget, "daily_limit": daily, "daily_baseline": baseline}},
        workspace=workspace, project_id=project_id, operation=operation, finalize=finalize,
        resumed_from_id=resumed_from_id, budget_tokens=budget, daily_baseline=baseline, daily_budget_limit=daily,
        validate_command=validate_command)
    if created:
        await _notify_run(ctx, "run.created", {"run": run})
    await ctx.reply({"run": run, "created": created})
