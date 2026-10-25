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
    LLM_ERROR,
    RunEventsParams,
    RunIdParams,
    RunResumeParams,
    RunStartParams,
)
from .server import Context

logger = logging.getLogger(__name__)


async def _get_dao() -> Any:
    from ..app import get_db, init_runtime
    from ..storage.dao.runs import AgentRunsDAO

    db = get_db()
    if db is None:
        try:
            await init_runtime()
        except Exception:
            pass
        db = get_db()
    if db is None:
        raise HandlerError(INTERNAL_ERROR, "storage layer is not available")
    return AgentRunsDAO(db)


def register_run_handlers(server: Any) -> None:
    """Register run timeline read APIs."""

    async def handle_run_list(params: Any, ctx: Context) -> None:
        try:
            if params is None:
                params = {}
            check_params(params, expected_keys=set())
            dao = await _get_dao()
            session_id = params.get("session_id")
            status = params.get("status")
            # v1.4.0: optional mode filter — 'subagent' surfaces tool-path
            # sub-agent runs (see migration 028) without post-filtering.
            mode = params.get("mode")
            limit = params.get("limit", 20)
            offset = params.get("offset", 0)
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
            dao = await _get_dao()
            snapshot = await dao.snapshot(run_id)
            if snapshot is None:
                await ctx.reply_error(INVALID_PARAMS, f"unknown run_id: {run_id!r}")
                return
            await ctx.reply(snapshot)
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:
            logger.exception("run.steps failed")
            await ctx.reply_error(INTERNAL_ERROR, "run.steps failed")

    async def handle_run_events(params: Any, ctx: Context) -> None:
        try:
            request = RunEventsParams.model_validate(params)
            dao = await _get_dao()
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
        # M4 — engines are pinned at admission and never silently fall
        # back. "codex" runs on the Codex App Server adapter; an
        # unavailable engine names its reason instead of degrading.
        if request.engine != "native":
            from ..engines import get_engine

            engine = get_engine(request.engine)
            if engine is None:
                await ctx.reply_error(INVALID_PARAMS, f"unknown engine: {request.engine!r}")
                return
            info = engine.info()
            if asyncio.iscoroutine(info):
                info = await info
            if not info.available:
                await ctx.reply_error(
                    INVALID_PARAMS,
                    f"engine {request.engine!r} is unavailable: {info.unavailable_reason}",
                    {"engine": info.to_dict()},
                )
                return
            await _start_codex_run(request, ctx, engine_info=info.to_dict())
            return
        await handle_agent_send_message(request.model_dump(exclude={"engine"}), ctx, wait=False)

    async def handle_run_get(params: Any, ctx: Context) -> None:
        try:
            request = RunIdParams.model_validate(params)
            dao = await _get_dao()
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
            dao = await _get_dao()
            run = await dao.get_run(request.run_id)
            if run is None:
                raise HandlerError(INVALID_PARAMS, "unknown run_id")
            requested = get_run_service(ctx.server).cancel_run(request.run_id)
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
            run = await dao.get_run(request.run_id)
            if run is None or run["status"] not in {"interrupted", "failed", "cancelled", "budget_paused"}:
                raise HandlerError(INVALID_PARAMS, "Only interrupted, failed, cancelled or budget-paused runs can be resumed")
            previous = await dao.request_payload(request.run_id)
            metadata = run.get("metadata") or {}
            if previous is None or not metadata.get("model_snapshot") or not metadata.get("workspace_snapshot"):
                raise HandlerError(INVALID_PARAMS, "This legacy run has no saved request/context; start a new task with a handoff")
            # A budget pause is a clean stop at a call boundary — no
            # uncertain writes, so the review gate only applies to the
            # other resumable statuses.
            if run["status"] != "budget_paused" and run.get("started_at") and not request.reviewed:
                raise HandlerError(INVALID_PARAMS, "Review the prior steps and workspace before resuming; their effects will not be automatically replayed")
            # M2 — budget: explicit override wins; otherwise inherit the
            # original cap so a plain resume cannot silently exceed what
            # the caller first authorised.
            saved_budget = (metadata.get("budget") or {}).get("limit_tokens")
            budget_override = request.budget_tokens if request.budget_tokens is not None else saved_budget
            if run["status"] == "budget_paused" and request.budget_tokens is None:
                raise HandlerError(
                    INVALID_PARAMS,
                    "This run paused on its token budget; pass budget_tokens to raise the cap before resuming",
                )
            note = (
                f"\n\n[Runtime recovery context: prior run {run['id']} ended as {run['status']}. "
                "Continue the original task after inspecting the existing workspace and prior conversation. "
                "Verify which actions already took effect before repeating a write. No tool step has been automatically retried.]"
            )
            if run["status"] == "budget_paused":
                note = (
                    f"\n\n[Budget recovery context: prior run {run['id']} paused at a model-call "
                    f"boundary with its token budget exhausted. The workspace reflects only confirmed "
                    "effects; continue the task with the raised budget.]"
                )
            content = previous["content"]
            if isinstance(content, list):
                content = [*content, {"type": "text", "text": note}]
            else:
                content += note
            await handle_agent_send_message(
                {**previous, "content": content, "idempotency_key": request.idempotency_key,
                 "budget_tokens": budget_override},
                ctx, wait=False, resumed_from_id=run["id"],
                required_model=metadata["model_snapshot"], required_workspace=metadata["workspace_snapshot"])
        except ValidationError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)

    server.register("run.list", handle_run_list)
    server.register("run.steps", handle_run_steps)
    server.register("run.events", handle_run_events)
    server.register("run.start", handle_run_start)
    server.register("run.get", handle_run_get)
    server.register("run.cancel", handle_run_cancel)
    server.register("run.resume", handle_run_resume)


async def _start_codex_run(request: RunStartParams, ctx: Context, *,
                           engine_info: dict[str, Any],
                           resume_thread_id: str | None = None) -> None:
    """Durably admit and drive one Codex-engine run (M4-B).

    Mirrors the native admission path: idempotency keys, budget
    resolution and the RunService lease all behave identically — only
    the turn driver differs (Codex App Server instead of AgentCore).
    """
    from ..budget import daily_budget_limit, resolve_run_budget
    from ..engines.codex_run import CodexApprovalGate, drive_codex_run
    from ..perm_consent import register_gater, unregister_gater
    from ..runs import get_run_service
    from .builtins import _notify_run

    if not isinstance(request.content, str) or not request.content.strip():
        await ctx.reply_error(INVALID_PARAMS, "codex runs require string content")
        return
    session_id = request.session_id
    title = request.content.strip()[:32]
    try:
        from ..app import get_db, get_sessions_dao, init_runtime
        from ..storage.dao.runs import AgentRunsDAO

        await init_runtime()
        db = get_db()
        if db is None:
            raise RuntimeError("database not initialized")
        dao = AgentRunsDAO(db)
        # agent_runs carries an FK on sessions — mirror the native path
        # and auto-create the session row on first use.
        sessions = get_sessions_dao()
        if sessions is not None and await sessions.get(session_id) is None:
            try:
                await sessions.create(id=session_id, title=f"codex:{title}",
                                      system_prompt="", model=None)
            except Exception:
                if await sessions.get(session_id) is None:
                    raise
    except Exception as exc:
        await ctx.reply_error(-32004, f"Cannot start task: {exc}")
        return

    session_id = request.session_id
    service = get_run_service(ctx.server)
    run_budget = resolve_run_budget(request.budget_tokens)
    daily_limit = daily_budget_limit()
    daily_baseline = 0
    if daily_limit is not None:
        try:
            row = await db.fetchone(
                "SELECT COALESCE(SUM(tokens_in + tokens_out), 0) AS n FROM messages "
                "WHERE date(created_at) = date('now', 'localtime')")
            daily_baseline = int(row["n"] or 0) if row else 0
        except Exception:
            daily_baseline = 0
        if daily_baseline >= daily_limit:
            await ctx.reply_error(
                LLM_ERROR,
                f"Daily token budget exhausted ({daily_baseline} spent of {daily_limit})",
                {"daily_budget": {"limit": daily_limit, "spent": daily_baseline}},
            )
            return

    request_payload = {"session_id": session_id, "mode": request.mode,
                       "content": request.content, "engine": "codex"}
    title = request.content.strip()[:32]

    async def operation(job: Any) -> None:
        emit = _codex_emit_factory(ctx, job)
        # M4 — approvals resolve through the SAME permission.resolve RPC
        # the native gater uses; without this registration the human's
        # answer never reaches Codex and every request times out denied.
        approval_gate = CodexApprovalGate()
        register_gater(ctx.server, session_id, approval_gate)

        async def _run() -> None:
            await dao.update_run_status(job.run_id, status="running")
            result = await drive_codex_run(
                job, emit=emit, prompt=request.content,
                resume_thread_id=resume_thread_id,
                approval_gate=approval_gate, mode=request.mode)
            job.result = result
            status = ("cancelled" if result.get("cancelled")
                      else "budget_paused" if result.get("budget_paused")
                      else result.get("run_status", "completed"))
            stored = await dao.get_run(job.run_id)
            metadata = dict((stored or {}).get("metadata") or {})
            metadata.update(codex_thread_id=result.get("codex_thread_id"), engine="codex")
            if result.get("budget") is not None:
                metadata["budget"] = result["budget"]
            await dao.update_run_status(
                job.run_id, status=status, result=result,
                error=str(result["error"]) if result.get("error") else None,
                metadata=metadata,
            )
            await _notify_run(ctx, "run.completed",
                              {"run": await dao.get_run(job.run_id)})

        task = asyncio.create_task(_run(), name=f"codex-run:{job.run_id}")
        try:
            await task
        except BaseException as exc:
            job.error = {"code": -32603, "message": str(exc) or type(exc).__name__}
            raise
        finally:
            unregister_gater(ctx.server, session_id)

    async def finalize(job: Any, error: BaseException | None) -> None:
        current = await dao.get_run(job.run_id)
        if current and current["status"] in {"completed", "failed", "cancelled",
                                             "interrupted", "budget_paused"}:
            return
        cancelled = isinstance(error, asyncio.CancelledError) or job.cancel_requested
        reason = "Cancelled by user or service shutdown" if cancelled \
            else str(error or "Codex run ended without a confirmed result")
        receipt = ({"session_id": session_id, "run_id": job.run_id,
                    "text": "", "cancelled": True} if cancelled
                   else {"error": {"code": -32603, "message": reason}})
        run = await dao.terminate_run(job.run_id,
                                      status="cancelled" if cancelled else "failed",
                                      error=reason, result=receipt)
        await _notify_run(ctx, "run.completed", {"run": run})

    # Keep the same fail-closed directory resolution as Native.
    from ..workspace_ctx import resolve_root_for_session

    try:
        codex_workspace = str(await resolve_root_for_session(session_id))
    except Exception as exc:
        await ctx.reply_error(-32004, f"Cannot resolve Codex workspace: {exc}")
        return
    run, job, created = await service.admit(
        dao=dao, session_id=session_id, request=request_payload,
        request_key=request.idempotency_key, title=title,
        metadata={"engine": "codex",
                  "budget": {"limit_tokens": run_budget, "daily_limit": daily_limit,
                             "daily_baseline": daily_baseline}},
        workspace=codex_workspace, project_id=None,
        operation=operation, finalize=finalize,
        resumed_from_id=None, budget_tokens=run_budget,
        daily_baseline=daily_baseline, daily_budget_limit=daily_limit)
    if created:
        await _notify_run(ctx, "run.created", {"run": run})
    await ctx.reply({"run": run, "created": created})


def _codex_emit_factory(ctx: Context, job: Any):
    """Wrap ctx.emit so notification failures never kill a Codex run."""
    from .builtins import _notify_run

    async def emit(event: str, data: dict[str, Any]) -> None:
        await _notify_run(ctx, event, {**data, "session_id": job.session_id,
                                       "run_id": job.run_id})
    return emit


__all__ = ["register_run_handlers"]
