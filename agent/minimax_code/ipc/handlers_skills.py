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


class _HandlerError(Exception):
    """Internal sentinel — handlers raise it with a JSON-RPC code."""

    def __init__(self, code: int, message: str, data: Any = None) -> None:
        self.code = code
        self.message = message
        self.data = data


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
            _check_params(params, expected_keys=set())
            enabled_only = bool((params or {}).get("enabled_only", False)) if params else False
            search = (params or {}).get("search") if params else None
            skills = runtime_obj.registry.list(
                enabled=True if enabled_only else None, search=search
            )
            await ctx.reply({"skills": [s.manifest() for s in skills]})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("skill.list failed")
            await ctx.reply_error(INTERNAL_ERROR, f"skill.list failed: {exc}")

    async def handle_skill_get(params: Any, ctx: Context) -> None:
        try:
            runtime_obj = await runtime_factory()
            _check_params(params, expected_keys={"skill_id"})
            skill_id = str(params["skill_id"])
            skill = runtime_obj.registry.get(skill_id)
            payload = skill.manifest()
            payload["body"] = skill.body
            payload["instructions"] = skill.instructions
            available = runtime_obj.registry._available_tools
            payload["missing_tools"] = skill.missing_tools(available)
            await ctx.reply({"skill": payload})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except KeyError:
            await ctx.reply_error(INVALID_PARAMS, f"unknown skill_id: {params.get('skill_id')!r}")
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("skill.get failed")
            await ctx.reply_error(INTERNAL_ERROR, f"skill.get failed: {exc}")

    async def handle_skill_enable(params: Any, ctx: Context) -> None:
        try:
            runtime_obj = await runtime_factory()
            _check_params(params, expected_keys={"skill_id"})
            skill_id = str(params["skill_id"])
            skill = await runtime_obj.registry.enable(skill_id)
            await ctx.reply({"skill": skill.manifest()})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except KeyError:
            await ctx.reply_error(INVALID_PARAMS, f"unknown skill_id: {params.get('skill_id')!r}")
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("skill.enable failed")
            await ctx.reply_error(INTERNAL_ERROR, f"skill.enable failed: {exc}")

    async def handle_skill_disable(params: Any, ctx: Context) -> None:
        try:
            runtime_obj = await runtime_factory()
            _check_params(params, expected_keys={"skill_id"})
            skill_id = str(params["skill_id"])
            skill = await runtime_obj.registry.disable(skill_id)
            await ctx.reply({"skill": skill.manifest()})
        except _HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except KeyError:
            await ctx.reply_error(INVALID_PARAMS, f"unknown skill_id: {params.get('skill_id')!r}")
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("skill.disable failed")
            await ctx.reply_error(INTERNAL_ERROR, f"skill.disable failed: {exc}")

    async def handle_skill_invoke(params: Any, ctx: Context) -> None:
        try:
            runtime_obj = await runtime_factory()
            _check_params(params, expected_keys={"skill_id", "request"})
            skill_id = str(params["skill_id"])
            request = str(params["request"])
            session_id = str(params.get("session_id") or f"skill_{uuid.uuid4().hex[:8]}")
            model = params.get("model")
            max_iterations = params.get("max_iterations")

            # Skill-specific message id so the UI can correlate chunks.
            message_id = f"msg_{uuid.uuid4().hex[:8]}"

            async def _on_chunk(delta: str, done: bool) -> None:
                await ctx.emit(
                    "agent.message_chunk",
                    {
                        "session_id": session_id,
                        "message_id": message_id,
                        "delta": delta,
                        "done": done,
                        "skill_id": skill_id,
                    },
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
            await ctx.reply(
                {
                    "session_id": session_id,
                    "message_id": message_id,
                    "skill_id": skill_id,
                    "text": result.final_text,
                    "iterations": result.iterations,
                    "tool_calls": result.tool_calls,
                    "cancelled": result.cancelled,
                    "truncated": result.truncated,
                }
            )
        except _HandlerError as exc:
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


def _check_params(params: Any, *, expected_keys: set[str]) -> None:
    """Validate the JSON-RPC params shape; raise :class:`_HandlerError` on bad input.

    Rules
    -----
    * ``params`` may be ``None`` (notification-style) only when
      ``expected_keys`` is empty.
    * Otherwise ``params`` must be a dict containing at least
      the keys in ``expected_keys``.
    """
    if not expected_keys:
        return
    if params is None or not isinstance(params, dict):
        raise _HandlerError(INVALID_PARAMS, "params must be a JSON object with the required keys")
    missing = expected_keys - set(params.keys())
    if missing:
        raise _HandlerError(
            INVALID_PARAMS,
            f"missing required param(s): {sorted(missing)}",
        )
    for key in expected_keys:
        if params[key] is None or (isinstance(params[key], str) and not params[key].strip()):
            raise _HandlerError(INVALID_PARAMS, f"param {key!r} must be a non-empty value")


__all__ = ["register_skill_handlers"]
