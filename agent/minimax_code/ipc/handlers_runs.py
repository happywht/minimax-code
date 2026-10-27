"""JSON-RPC handlers for the ``run.*`` timeline namespace."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import ValidationError

from .handler_utils import HandlerError, check_params
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
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
            from .host_session_gate import require_public_recovery

            await require_public_recovery(None, request.run_id)
            run = await dao.get_run(request.run_id)
            if run is None or run["status"] not in {"interrupted", "failed", "cancelled", "budget_paused"}:
                raise HandlerError(INVALID_PARAMS, "Only interrupted, failed, cancelled or budget-paused runs can be resumed")
            previous = await dao.request_payload(request.run_id)
            metadata = run.get("metadata") or {}
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
                if run["status"] == "budget_paused" and request.budget_tokens is None:
                    raise HandlerError(INVALID_PARAMS, "Pass budget_tokens to authorize a raised recovery cap")
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
                internal_schedule=saved_schedule, internal_skill=saved_skill, internal_agent=saved_agent)
        except (ValidationError, ValueError) as exc:
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


async def _start_codex_run(request: RunStartParams, ctx: Context, **options: Any) -> None:
    from .host_session_gate import require_chat_session

    await require_chat_session(request.session_id)
    from ..engines.codex_service import start_codex_run
    await start_codex_run(request, ctx, **options)


__all__ = ["register_run_handlers"]
