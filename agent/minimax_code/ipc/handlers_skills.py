"""JSON-RPC handlers for the ``skill.*`` namespace.

Wire-up
-------

The application bootstrap (:mod:`minimax_code.app`) calls
:func:`register_skill_handlers` with a runtime reference.
The handlers each call :func:`_ensure_runtime` on entry so the
runtime is built lazily on the first ``skill.*`` request.

Streamed output (``skill.invoke``) is forwarded to the
frontend as ``agent.message_chunk`` /
``agent.tool_call`` / ``agent.tool_result`` / ``agent.status``
events — the same envelope the regular agent loop uses. The
*response* to the request itself is a small summary envelope
(success, skill_id, final text, iterations); the actual text
arrives via the streaming events.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from .handler_utils import HandlerError, check_params
from .protocol import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    NOT_IMPLEMENTED,
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
        try:
            runtime_obj = await runtime_factory()
            check_params(params, expected_keys={"skill_id", "request"})
            skill_id = str(params["skill_id"])
            request = str(params["request"])
            session_id = str(params.get("session_id") or f"skill_{uuid.uuid4().hex[:8]}")
            model = params.get("model")
            max_iterations = params.get("max_iterations")

            # v0.4.0 — Code Review with real LLM support.
            # When the caller passes a ``diff`` in params, the
            # CodeReviewPanel is asking for a diff review. If a real
            # LLM is available (non-mock), we route through the full
            # skill runtime so the model can use run_linter /
            # find_complex_functions and produce a high-quality
            # review. In mock mode we fall back to the deterministic
            # diff walker for zero-dependency operation.
            diff_param = params.get("diff")
            if (
                isinstance(diff_param, str)
                and diff_param
                and skill_id == "code-review:code-review"
            ):
                from ..agent.skills._builtin.code_review import _diff_stats, _review_diff

                # Always compute stats for UI compatibility.
                stats = _diff_stats(diff_param)

                # Decide path: real LLM vs mock fallback.
                llm = getattr(runtime_obj, "llm", None)
                use_real_llm = llm is not None and not getattr(llm, "mock", True)

                if use_real_llm:
                    # Real LLM path: craft a review request and
                    # invoke the skill through the runtime.
                    review_request = (
                        "Review the following unified diff. Identify bugs, "
                        "style issues, and complexity hot-spots. Use "
                        "`run_linter` and `find_complex_functions` on the "
                        "changed files when applicable.\n\n"
                        f"```diff\n{diff_param}\n```"
                    )
                    message_id = f"msg_{uuid.uuid4().hex[:8]}"

                    async def _on_chunk(
                        delta: str, done: bool, metadata: dict | None = None
                    ) -> None:
                        await ctx.emit(
                            "agent.message_chunk",
                            {
                                "session_id": session_id,
                                "message_id": message_id,
                                "delta": delta,
                                "done": done,
                                "skill_id": skill_id,
                            },
                            metadata=metadata,
                        )

                    async def _on_tool_call(call: dict[str, Any]) -> None:
                        await ctx.emit(
                            "agent.tool_call",
                            {
                                "session_id": session_id,
                                "tool_call_id": call.get("id", ""),
                                "name": call.get("name", ""),
                                "args": call.get("args", {}),
                                "skill_id": skill_id,
                            },
                        )

                    async def _on_tool_result(
                        call: dict[str, Any], result: Any
                    ) -> None:
                        result_payload = (
                            result.to_dict()
                            if hasattr(result, "to_dict")
                            else result
                        )
                        await ctx.emit(
                            "agent.tool_result",
                            {
                                "session_id": session_id,
                                "tool_call_id": call.get("id", ""),
                                "name": call.get("name", ""),
                                "result": result_payload,
                                "skill_id": skill_id,
                            },
                        )

                    async def _on_status(
                        status: str, detail: dict[str, Any]
                    ) -> None:
                        await ctx.emit(
                            "agent.status",
                            {
                                "session_id": session_id,
                                "status": status,
                                "detail": {**detail, "skill_id": skill_id},
                            },
                        )

                    result = await runtime_obj.invoke(
                        skill_id,
                        request=review_request,
                        session_id=session_id,
                        on_chunk=_on_chunk,
                        on_tool_call=_on_tool_call,
                        on_tool_result=_on_tool_result,
                        on_status=_on_status,
                        model=model,
                        max_iterations=max_iterations,
                    )
                    await ctx.reply(
                        {
                            "session_id": session_id,
                            "message_id": message_id,
                            "skill_id": skill_id,
                            "text": result.final_text,
                            # Canonical output channel for the frontend —
                            # mirrors ``text`` so callers read one key.
                            "output": result.final_text,
                            "iterations": result.iterations,
                            "tool_calls": result.tool_calls,
                            "cancelled": result.cancelled,
                            "truncated": result.truncated,
                            "comments": [],
                            "stats": stats,
                        }
                    )
                    return
                else:
                    # Mock fallback: deterministic diff walker.
                    review = _review_diff(diff_param)
                    message_id = f"msg_{uuid.uuid4().hex[:8]}"
                    await ctx.emit(
                        "agent.message_chunk",
                        {
                            "session_id": session_id,
                            "message_id": message_id,
                            "delta": review["text"],
                            "done": True,
                            "skill_id": skill_id,
                        },
                    )
                    await ctx.reply(
                        {
                            "session_id": session_id,
                            "message_id": message_id,
                            "skill_id": skill_id,
                            "text": review["text"],
                            "output": review["text"],
                            "iterations": review.get("iterations", 0),
                            "tool_calls": review.get("tool_calls", 0),
                            "cancelled": False,
                            "truncated": False,
                            "comments": review.get("comments", []),
                            "stats": stats,
                        }
                    )
                    return

            # Skill-specific message id so the UI can correlate chunks.
            message_id = f"msg_{uuid.uuid4().hex[:8]}"

            async def _on_chunk(
                delta: str, done: bool, metadata: dict | None = None
            ) -> None:
                # Skill invocations don't surface a per-turn
                # thinking count (the skill runtime is itself the
                # "think"), so the metadata channel is unused
                # here. We still accept the kwarg so the callback
                # matches the v0.3.0 :data:`ChunkCallback` shape
                # and could be plumbed through in the future.
                await ctx.emit(
                    "agent.message_chunk",
                    {
                        "session_id": session_id,
                        "message_id": message_id,
                        "delta": delta,
                        "done": done,
                        "skill_id": skill_id,
                    },
                    metadata=metadata,
                )

            async def _on_tool_call(call: dict[str, Any]) -> None:
                await ctx.emit(
                    "agent.tool_call",
                    {
                        "session_id": session_id,
                        "tool_call_id": call.get("id", ""),
                        "name": call.get("name", ""),
                        "args": call.get("args", {}),
                        "skill_id": skill_id,
                    },
                )

            async def _on_tool_result(call: dict[str, Any], result: Any) -> None:
                result_payload = (
                    result.to_dict() if hasattr(result, "to_dict") else result
                )
                await ctx.emit(
                    "agent.tool_result",
                    {
                        "session_id": session_id,
                        "tool_call_id": call.get("id", ""),
                        "name": call.get("name", ""),
                        "result": result_payload,
                        "skill_id": skill_id,
                    },
                )

            async def _on_status(status: str, detail: dict[str, Any]) -> None:
                await ctx.emit(
                    "agent.status",
                    {
                        "session_id": session_id,
                        "status": status,
                        "detail": {**detail, "skill_id": skill_id},
                    },
                )

            result = await runtime_obj.invoke(
                skill_id,
                request=request,
                session_id=session_id,
                on_chunk=_on_chunk,
                on_tool_call=_on_tool_call,
                on_tool_result=_on_tool_result,
                on_status=_on_status,
                model=model,
                max_iterations=max_iterations,
            )
            await _persist_invoke_turn(session_id, skill_id, request, result)
            await ctx.reply(
                {
                    "session_id": session_id,
                    "message_id": message_id,
                    "skill_id": skill_id,
                    "text": result.final_text,
                    # Canonical output channel — the generic path used to
                    # reply without it while the frontend read ``output``,
                    # so every skill result rendered as ``undefined``.
                    "output": result.final_text,
                    "iterations": result.iterations,
                    "tool_calls": result.tool_calls,
                    "cancelled": result.cancelled,
                    "truncated": result.truncated,
                }
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except KeyError as exc:
            await ctx.reply_error(INVALID_PARAMS, f"unknown skill_id: {exc.args[0]!r}")
        except Exception as exc:
            logger.exception("skill.invoke failed")
            code = NOT_IMPLEMENTED if "not implemented" in str(exc).lower() else INTERNAL_ERROR
            await ctx.reply_error(code, str(exc))

    server.register("skill.list", handle_skill_list)
    server.register("skill.get", handle_skill_get)
    server.register("skill.enable", handle_skill_enable)
    server.register("skill.disable", handle_skill_disable)
    server.register("skill.install", handle_skill_install)
    server.register("skill.uninstall", handle_skill_uninstall)
    server.register("skill.invoke", handle_skill_invoke)


async def _persist_invoke_turn(
    session_id: str, skill_id: str, request: str, result: Any
) -> None:
    """Best-effort persistence of a ``skill.invoke`` turn (v1.2.0).

    Skill output used to live only in the event stream — reload the app
    and whatever a skill produced (a code review, a digest) was gone.
    Now the turn is written to the messages ledger when — and only
    when — the caller supplied a ``session_id`` that actually exists.
    The fallback ``skill_*`` id names no session row and would violate
    the messages→sessions FK, so it is skipped.

    Persistence is an enhancement, never a failure mode: any error here
    is logged and swallowed because the IPC reply already carries the
    full result.
    """
    if not session_id or session_id.startswith("skill_"):
        return
    try:
        from ..app import get_db
        from ..storage.dao.messages import MessagesDAO
        from ..storage.dao.sessions import SessionsDAO

        db = get_db()
        if db is None:
            return
        if await SessionsDAO(db).get(session_id) is None:
            return
        dao = MessagesDAO(db)
        meta: dict[str, Any] = {"source": "skill_invoke", "skill_id": skill_id}
        await dao.create(
            id=f"msg_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            role="user",
            content=request,
            metadata=meta,
        )
        await dao.create(
            id=f"msg_{uuid.uuid4().hex[:12]}",
            session_id=session_id,
            role="assistant",
            content=result.final_text,
            metadata={**meta, "iterations": result.iterations},
        )
    except Exception:
        logger.warning("skill.invoke persist skipped", exc_info=True)

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
