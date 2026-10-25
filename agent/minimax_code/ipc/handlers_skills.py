"""JSON-RPC handlers for the ``skill.*`` namespace.

Wire-up
-------

The application bootstrap (:mod:`minimax_code.app`) calls
:func:`register_skill_handlers` with a runtime reference.
Runtime resolution is lazy. Invocation first checks persisted intent so retries
can replay an accepted result without consulting changed live configuration.

Streamed output (``skill.invoke``) is forwarded to the
frontend as ``agent.message_chunk`` /
``agent.tool_call`` / ``agent.tool_result`` / ``agent.status``
events — the same envelope the regular agent loop uses. The
response is the durable skill receipt, or an admission envelope when requested.
"""

from __future__ import annotations

import logging
from typing import Any

from .handler_utils import HandlerError, check_params
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
)
from .server import Context

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_skill_handlers(
    server: Any,
    runtime: Any = None,
) -> None:
    """Register the ``skill.*`` handlers on ``server``.

    Parameters
    ----------
    server:
        The :class:`~minimax_code.ipc.server.IPCServer` instance.
    runtime:
        Optional pre-built :class:`SkillRuntime`. When ``None``,
        handlers build the runtime lazily via
        :func:`minimax_code.app.init_runtime` on the first call.
    """
    # We bind the runtime (or the lazy getter) into a closure
    # so the handlers can be written as plain coroutines.
    runtime_factory = _make_runtime_factory(runtime)

    async def handle_skill_list(params: Any, ctx: Context) -> None:
        try:
            runtime_obj = await runtime_factory()
            check_params(params, expected_keys=set())
            enabled_only = bool((params or {}).get("enabled_only", False)) if params else False
            search = (params or {}).get("search") if params else None
            skills = runtime_obj.registry.list(
                enabled=True if enabled_only else None, search=search
            )
            await ctx.reply({"skills": [s.manifest() for s in skills]})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("skill.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "skill.list failed")

    async def handle_skill_get(params: Any, ctx: Context) -> None:
        try:
            runtime_obj = await runtime_factory()
            check_params(params, expected_keys={"skill_id"})
            skill_id = str(params["skill_id"])
            skill = runtime_obj.registry.get(skill_id)
            payload = skill.manifest()
            payload["body"] = skill.body
            payload["instructions"] = skill.instructions
            available = runtime_obj.registry._available_tools
            payload["missing_tools"] = skill.missing_tools(available)
            await ctx.reply({"skill": payload})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except KeyError:
            await ctx.reply_error(INVALID_PARAMS, f"unknown skill_id: {params.get('skill_id')!r}")
        except Exception:  # pragma: no cover — defensive
            logger.exception("skill.get failed")
            await ctx.reply_error(INTERNAL_ERROR, "skill.get failed")

    async def handle_skill_enable(params: Any, ctx: Context) -> None:
        try:
            runtime_obj = await runtime_factory()
            check_params(params, expected_keys={"skill_id"})
            skill_id = str(params["skill_id"])
            skill = await runtime_obj.registry.enable(skill_id)
            await ctx.reply({"skill": skill.manifest()})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except KeyError:
            await ctx.reply_error(INVALID_PARAMS, f"unknown skill_id: {params.get('skill_id')!r}")
        except Exception:  # pragma: no cover — defensive
            logger.exception("skill.enable failed")
            await ctx.reply_error(INTERNAL_ERROR, "skill.enable failed")

    async def handle_skill_disable(params: Any, ctx: Context) -> None:
        try:
            runtime_obj = await runtime_factory()
            check_params(params, expected_keys={"skill_id"})
            skill_id = str(params["skill_id"])
            skill = await runtime_obj.registry.disable(skill_id)
            await ctx.reply({"skill": skill.manifest()})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except KeyError:
            await ctx.reply_error(INVALID_PARAMS, f"unknown skill_id: {params.get('skill_id')!r}")
        except Exception:  # pragma: no cover — defensive
            logger.exception("skill.disable failed")
            await ctx.reply_error(INTERNAL_ERROR, "skill.disable failed")

    async def handle_skill_install(params: Any, ctx: Context) -> None:
        try:
            runtime_obj = await runtime_factory()
            check_params(params, expected_keys={"content"})
            content = params["content"]
            if not isinstance(content, str):
                raise HandlerError(INVALID_PARAMS, "content must be a string")
            skill = await runtime_obj.registry.install_from_text(
                content,
                replace=bool(params.get("replace", False)),
            )
            await ctx.reply({"skill": skill.manifest()})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except (FileExistsError, ValueError, RuntimeError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("skill.install failed")
            await ctx.reply_error(INTERNAL_ERROR, "skill.install failed")

    async def handle_skill_uninstall(params: Any, ctx: Context) -> None:
        try:
            runtime_obj = await runtime_factory()
            check_params(params, expected_keys={"skill_id"})
            removed = await runtime_obj.registry.uninstall_custom(str(params["skill_id"]))
            await ctx.reply({"ok": True, "skill_id": removed.skill_id})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except KeyError:
            await ctx.reply_error(INVALID_PARAMS, f"unknown skill_id: {params.get('skill_id')!r}")
        except (ValueError, RuntimeError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:
            logger.exception("skill.uninstall failed")
            await ctx.reply_error(INTERNAL_ERROR, "skill.uninstall failed")

    async def handle_skill_invoke(params: Any, ctx: Context) -> None:
        from pydantic import ValidationError

        from ..app import get_db, init_runtime
        from ..runs import get_run_service
        from ..skill_execution import SkillExecutionSpec, canonical_request
        from ..storage.dao.runs import AgentRunsDAO
        from ..workspace_ctx import resolve_root_for_session
        from .builtins import _reply_for_run, handle_agent_send_message
        from .protocol import SkillInvokeParams

        try:
            parsed = SkillInvokeParams.model_validate(params)
            values = parsed.model_dump()
            request = canonical_request(values)
            db = get_db()
            if db is None:
                await init_runtime()
                db = get_db()
            if db is None:
                raise RuntimeError("Skill admission requires durable storage")
            dao, service = AgentRunsDAO(db), get_run_service(ctx.server)
            # Replay needs only persisted intent and receipt. Live skill/model
            # settings may be disabled, removed or changed after acceptance.
            existing = await dao.find_request(parsed.session_id, parsed.idempotency_key, request)
            if existing is not None:
                await _reply_for_run(ctx, dao, service, existing,
                    await service.job_for_run(existing["id"]), created=False, wait=not parsed.admission_only)
                return
            session = await db.fetchone("SELECT * FROM sessions WHERE id=?", (parsed.session_id,))
            if session is None or session["engine"] != "native":
                raise ValueError("skill.invoke requires an existing explicit Native session")
            workspace = str(await resolve_root_for_session(parsed.session_id))
            runtime_obj = await runtime_factory()
            spec, model = await SkillExecutionSpec.capture(runtime_obj, db, ctx.server, values,
                                                          workspace, session["project_id"])
            await handle_agent_send_message(
                {**request, "idempotency_key": parsed.idempotency_key,
                 "budget_tokens": parsed.budget_tokens}, ctx, wait=not parsed.admission_only,
                internal_skill=spec, skill_model=model, skill_runtime=runtime_obj)
        except (ValidationError, ValueError, KeyError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception as exc:
            logger.exception("skill.invoke failed")
            await ctx.reply_error(INTERNAL_ERROR, str(exc))

    server.register("skill.list", handle_skill_list)
    server.register("skill.get", handle_skill_get)
    server.register("skill.enable", handle_skill_enable)
    server.register("skill.disable", handle_skill_disable)
    server.register("skill.install", handle_skill_install)
    server.register("skill.uninstall", handle_skill_uninstall)
    server.register("skill.invoke", handle_skill_invoke)



def _make_runtime_factory(runtime: Any | None) -> Any:
    """Return an async factory that yields a runtime, building it lazily if needed.

    * If ``runtime`` is supplied (test path), the factory just
      returns it on every call.
    * Otherwise the factory calls
      :func:`minimax_code.app.init_runtime` to build the
      process-wide singleton on first use.
    """
    if runtime is not None:
        async def _factory() -> Any:
            return runtime
        return _factory

    async def _factory() -> Any:
        # Lazy import: avoids a circular dependency at module
        # import time (handlers_skills is imported by app, not
        # the other way round).
        from ..app import init_runtime

        return await init_runtime()

    return _factory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

__all__ = ["register_skill_handlers"]
