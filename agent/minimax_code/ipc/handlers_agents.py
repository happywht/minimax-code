"""JSON-RPC handlers for the ``agent.*`` (sub-agent) namespace.

This namespace is the IPC seam the frontend uses to manage and
invoke *sub-agents* — separately-configured agent identities
(system prompt + tool allowlist + model) that the primary agent
can dispatch work to. The handlers do two jobs:

* **Config CRUD** — ``agent.list`` / ``agent.get`` / ``agent.create``
  / ``agent.update`` / ``agent.delete`` round-trip rows in the
  ``agents`` table via :class:`AgentDAO`. All five are pure
  storage reads / writes; no LLM involvement.
* **Invoke** — ``agent.invoke`` builds a
  :class:`~minimax_code.orchestrator.subagent.SubAgentHandle` from
  the named config, runs it through the
  :class:`SubAgentRuntime`, emits a streamed ``agent.message_chunk``
  event for the response, and writes a single progress row to the
  ``tasks`` table (start → 50 → complete) so the rest of the system
  sees a real task lifecycle.

The runtime is **real LLM** by default — a process-wide
:class:`MiniMaxClient` is built at app boot (in
:func:`minimax_code.app._maybe_open_db`) and injected into the
runtime singleton, so :meth:`SubAgentRuntime.invoke` forwards
to :meth:`AgentCore.run` and the wire response carries
``stub=False`` plus the model's actual text. When the runtime
singleton isn't set (e.g. a test that never bootstrapped the
app, or a process running without storage), the runtime falls
back to a deterministic stub envelope (``stub=True``) so the
IPC layer can still exercise the full event flow. No child
process is spawned in either path.

Wire-up
-------

:func:`register_agent_handlers` is called from
:func:`minimax_code.app.register_app_handlers` after the storage
layer is migrated. The DAO is built lazily on the first ``agent.*``
request — same lazy-resolve pattern used by
:mod:`handlers_sessions` and :mod:`handlers_tasks`.

Schema
------

``agent.list``     -> ``{ agents: [...] }``
``agent.get``      -> ``{ agent: {...} }``
``agent.create``   -> ``{ agent: {...} }``
``agent.update``   -> ``{ agent: {...} }``
``agent.delete``   -> ``{ ok: true }``
``agent.invoke``   -> streamed ``agent.message_chunk`` events +
                      final ``{ agent, request, session_id, text, task_id,
                                iterations, tool_calls, stub }``
                      (``stub=False`` when the runtime has an LLM,
                      ``stub=True`` when it falls back to the
                      deterministic stub envelope.)
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from .handler_utils import HandlerError, check_params
from .protocol import INTERNAL_ERROR, INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_agent_handlers(server: Any, *, dao: Any = None) -> None:
    """Register the ``agent.*`` handlers on ``server``.

    Parameters
    ----------
    server:
        The :class:`~minimax_code.ipc.server.IPCServer` instance.
    dao:
        Optional pre-built :class:`AgentDAO`. When ``None``,
        handlers resolve the singleton (lazily building it on
        first call) via :func:`_make_dao_factory`.
    """
    dao_factory = _make_dao_factory(dao)
    using_injected_dao = dao is not None
    # The runtime is process-wide — one instance handles every
    # ``agent.invoke`` call. We resolve it lazily on every
    # invocation rather than capturing at registration time, so
    # that the singleton set up by :func:`minimax_code.app._maybe_open_db`
    # (which runs after the handler registration in the current
    # boot order) is picked up correctly. When the singleton
    # hasn't been built yet (e.g. a test that skipped the app
    # init), we fall back to a default ``SubAgentRuntime()`` so
    # handlers stay usable.
    from ..orchestrator import SubAgentRuntime, get_subagent_runtime

    def _resolve_runtime() -> Any:
        """Return the current process-wide runtime, or build a default one.

        Resolved lazily on every :meth:`handle_agent_invoke` call
        so the LLM-injected runtime set by
        :func:`minimax_code.app._maybe_open_db` is picked up
        even when the DB opens *after* handler registration
        (the current boot order).
        """
        return get_subagent_runtime() or SubAgentRuntime()

    # ------------------------------------------------------------------ list

    async def handle_agent_list(params: Any, ctx: Context) -> None:
        try:
            agent_dao = await dao_factory()
            if agent_dao is None:
                raise HandlerError(
                    INTERNAL_ERROR,
                    "storage layer is not available; agent registry disabled",
                )
            check_params(params, expected_keys=set())
            agents = await agent_dao.list_all()
            await ctx.reply({"agents": agents})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover — defensive
            logger.exception("agent.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "agent.list failed")

    # ------------------------------------------------------------------- get

    async def handle_agent_get(params: Any, ctx: Context) -> None:
        try:
            agent_dao = await dao_factory()
            if agent_dao is None:
                raise HandlerError(
                    INTERNAL_ERROR,
                    "storage layer is not available; agent registry disabled",
                )
            check_params(params, expected_keys={"name"})
            name = str(params["name"])
            agent = await agent_dao.get(name)
            if agent is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown agent name: {name!r}"
                )
                return
            await ctx.reply({"agent": agent})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:  # pragma: no cover — defensive
            logger.exception("agent.get failed")
            await ctx.reply_error(INTERNAL_ERROR, "agent.get failed")

    # ---------------------------------------------------------------- create

    async def handle_agent_create(params: Any, ctx: Context) -> None:
        try:
            agent_dao = await dao_factory()
            if agent_dao is None:
                raise HandlerError(
                    INTERNAL_ERROR,
                    "storage layer is not available; agent registry disabled",
                )
            check_params(params, expected_keys={"name", "system_prompt"})
            name = str(params["name"])
            system_prompt = str(params["system_prompt"])
            tool_allowlist = _normalise_allowlist(params.get("tool_allowlist"))
            model = params.get("model")
            if model is not None:
                model = str(model)
            # v0.8.0 extended fields
            description = params.get("description")
            icon = params.get("icon")
            color = params.get("color")
            category = params.get("category")
            tags = params.get("tags")
            skills = params.get("skills")
            max_iterations = params.get("max_iterations")
            temperature = params.get("temperature")
            # Build kwargs for extended upsert
            upsert_kwargs: dict[str, Any] = {}
            if description is not None:
                upsert_kwargs["description"] = str(description)
            if icon is not None:
                upsert_kwargs["icon"] = str(icon)
            if color is not None:
                upsert_kwargs["color"] = str(color)
            if category is not None:
                upsert_kwargs["category"] = str(category)
            if tags is not None:
                if not isinstance(tags, list):
                    raise HandlerError(INVALID_PARAMS, "tags must be a list of strings")
                upsert_kwargs["tags"] = tags
            if skills is not None:
                if not isinstance(skills, list):
                    raise HandlerError(INVALID_PARAMS, "skills must be a list of strings")
                upsert_kwargs["skills"] = skills
            if max_iterations is not None:
                upsert_kwargs["max_iterations"] = int(max_iterations)
            if temperature is not None:
                upsert_kwargs["temperature"] = float(temperature)
            agent = await agent_dao.upsert(
                name=name,
                system_prompt=system_prompt,
                tool_allowlist=tool_allowlist,
                model=model,
                **upsert_kwargs,
            )
            await ctx.reply({"agent": agent})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:  # pragma: no cover — defensive
            logger.exception("agent.create failed")
            await ctx.reply_error(INTERNAL_ERROR, "agent.create failed")

    # ---------------------------------------------------------------- update

    async def handle_agent_update(params: Any, ctx: Context) -> None:
        try:
            agent_dao = await dao_factory()
            if agent_dao is None:
                raise HandlerError(
                    INTERNAL_ERROR,
                    "storage layer is not available; agent registry disabled",
                )
            check_params(params, expected_keys={"name"})
            name = str(params["name"])
            existing = await agent_dao.get(name)
            if existing is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown agent name: {name!r}"
                )
                return
            # Read each optional field — missing means "don't touch".
            new_prompt = params.get("system_prompt", existing["system_prompt"])
            if "tool_allowlist" in params:
                new_allowlist = _normalise_allowlist(params["tool_allowlist"])
            else:
                new_allowlist = existing.get("tool_allowlist")
            if "model" in params:
                new_model = params["model"]
                new_model = str(new_model) if new_model is not None else None
            else:
                new_model = existing.get("model")
            # v0.8.0 extended fields for update
            upsert_kwargs: dict[str, Any] = {}
            if "description" in params:
                upsert_kwargs["description"] = str(params["description"])
            if "icon" in params:
                upsert_kwargs["icon"] = str(params["icon"])
            if "color" in params:
                upsert_kwargs["color"] = str(params["color"])
            if "category" in params:
                upsert_kwargs["category"] = str(params["category"])
            if "tags" in params:
                tags = params["tags"]
                if not isinstance(tags, list):
                    raise HandlerError(INVALID_PARAMS, "tags must be a list of strings")
                upsert_kwargs["tags"] = tags
            if "skills" in params:
                skills = params["skills"]
                if not isinstance(skills, list):
                    raise HandlerError(INVALID_PARAMS, "skills must be a list of strings")
                upsert_kwargs["skills"] = skills
            if "max_iterations" in params:
                upsert_kwargs["max_iterations"] = int(params["max_iterations"])
            if "temperature" in params:
                upsert_kwargs["temperature"] = float(params["temperature"])
            agent = await agent_dao.upsert(
                name=name,
                system_prompt=str(new_prompt),
                tool_allowlist=new_allowlist,
                model=new_model,
                **upsert_kwargs,
            )
            await ctx.reply({"agent": agent})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:  # pragma: no cover — defensive
            logger.exception("agent.update failed")
            await ctx.reply_error(INTERNAL_ERROR, "agent.update failed")

    # ---------------------------------------------------------------- delete

    async def handle_agent_delete(params: Any, ctx: Context) -> None:
        try:
            agent_dao = await dao_factory()
            if agent_dao is None:
                raise HandlerError(
                    INTERNAL_ERROR,
                    "storage layer is not available; agent registry disabled",
                )
            check_params(params, expected_keys={"name"})
            name = str(params["name"])
            ok = await agent_dao.delete(name)
            if not ok:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown agent name: {name!r}"
                )
                return
            await ctx.reply({"ok": True, "name": name})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:  # pragma: no cover — defensive
            logger.exception("agent.delete failed")
            await ctx.reply_error(INTERNAL_ERROR, "agent.delete failed")

    # ---------------------------------------------------------------- invoke

    async def handle_agent_invoke(params: Any, ctx: Context) -> None:
        """Stream a sub-agent invocation.

        Params: ``{"name": "...", "request": "...", "session_id"?: "..."}``
        Reply:  ``{"agent": ..., "request": ..., "session_id": ...,
                  "text": ..., "task_id": ..., "iterations": N, "stub": <bool>}``

        When the runtime singleton (set by
        :func:`minimax_code.app._maybe_open_db`) carries a
        :class:`MiniMaxClient`, ``runtime.invoke`` returns the
        model's actual final text and ``stub=False``. When no
        LLM is injected (e.g. a test path), the deterministic
        stub envelope is returned (``stub=True``).

        Side effects
        ------------

        1. Emits a ``agent.message_chunk`` event with the final
           text (so the frontend sees a stream, not just a
           reply). Marks ``done=True`` on the final emission.
        2. Writes a single progress row through
           :func:`minimax_code.app.get_progress_tracker` — start
           at 0, bump to 50 mid-stream, complete at 100 — so the
           ``task.*`` namespace and the ``ProgressPanel`` see a
           real task lifecycle.
        """
        try:
            agent_dao = await dao_factory()
            if agent_dao is None:
                raise HandlerError(
                    INTERNAL_ERROR,
                    "storage layer is not available; agent registry disabled",
                )
            check_params(params, expected_keys={"name", "request"})
            name = str(params["name"])
            requested_agent_id = (
                str(params["agent_id"])
                if params.get("agent_id") and str(params["agent_id"]).strip()
                else None
            )
            request = str(params["request"])
            session_id = params.get("session_id")
            if session_id is None or not str(session_id).strip():
                # Sub-agent invocations don't need to live inside a
                # real chat session, but the ``tasks`` table has a
                # NOT-NULL FK to ``sessions.id``, so we mint a
                # placeholder. The corresponding row is created
                # lazily by the tracker via ``get_session``.
                from ..orchestrator import make_session_id

                session_id = make_session_id("subagent")
            session_id = str(session_id)

            if hasattr(agent_dao, "get_by_name_or_id"):
                config_row = await agent_dao.get_by_name_or_id(name)
                if config_row is None and requested_agent_id:
                    config_row = await agent_dao.get_by_name_or_id(requested_agent_id)
            else:
                config_row = await agent_dao.get(name)
                if config_row is None and requested_agent_id:
                    config_row = await agent_dao.get(requested_agent_id)
            if config_row is None:
                error = f"unknown agent name or id: {name!r}"
                await ctx.reply_error(INVALID_PARAMS, error)
                return
            name = str(config_row["name"])

            from ..orchestrator import SubAgentConfig

            config = SubAgentConfig(
                name=config_row["name"],
                system_prompt=config_row.get("system_prompt") or "",
                tool_allowlist=config_row.get("tool_allowlist"),
                model=config_row.get("model"),
                id=config_row.get("id"),
            )
            # Resolve the runtime lazily — the singleton is set
            # by :func:`minimax_code.app._maybe_open_db`, which
            # may not have run yet at handler-registration time
            # (the boot order registers handlers first, then
            # opens the DB on the first ``skill.*`` call).
            runtime = _resolve_runtime()
            handle = runtime.build(config)

            # Ensure a sessions row exists so the FK in ``tasks``
            # is satisfied. We do this through the same path the
            # ``task.start`` handler uses, but inline here so we
            # don't need a separate request.
            if not using_injected_dao:
                await _ensure_session(session_id, title=f"subagent:{name}")

            # 1. Start a progress task. The tracker writes the row
            # and emits ``agent.status { status: started }`` for us.
            tracker = await _get_tracker()
            task_id: str | None = None
            message_id = f"msg_{uuid.uuid4().hex[:8]}"
            if tracker is not None:
                task_id = await tracker.start_task(
                    session_id,
                    f"subagent:{name}",
                    emit=ctx.emit,
                )

            # 2. Run the sub-agent. When the runtime has an LLM
            # injected, this forwards to AgentCore.run and
            # returns the model's actual final text (and sets
            # ``stub=False``). When the runtime is unconfigured
            # (no LLM), the deterministic stub envelope is
            # returned (``stub=True``).
            result = await runtime.invoke(
                handle, session_id=session_id, request=request
            )
            is_stub = bool(result.get("stub", True))
            final_text = result.get("text", "")

            # 3. Stream a single agent.message_chunk with the
            # final text so the frontend sees a stream. The
            # streamed delta and the final reply text are kept
            # in sync (both come from ``final_text``).
            await ctx.emit(
                "agent.message_chunk",
                {
                    "session_id": session_id,
                    "message_id": message_id,
                    "delta": final_text,
                    "done": False,
                    "agent": name,
                },
            )
            # 4. Bump the progress to 50 (mid-stream).
            if tracker is not None and task_id is not None:
                await tracker.update(
                    task_id,
                    50,
                    message=("stub: half-way through" if is_stub else "half-way through"),
                    emit=ctx.emit,
                )
            # 5. Final chunk marker.
            await ctx.emit(
                "agent.message_chunk",
                {
                    "session_id": session_id,
                    "message_id": message_id,
                    "delta": "",
                    "done": True,
                    "agent": name,
                },
            )
            # 6. Mark the task complete.
            if tracker is not None and task_id is not None:
                await tracker.complete(task_id, emit=ctx.emit)
            await ctx.reply(
                {
                    "agent": name,
                    "request": request,
                    "session_id": session_id,
                    "text": final_text,
                    "iterations": result.get("iterations", 0),
                    "tool_calls": result.get("tool_calls", []),
                    "task_id": task_id,
                    "message_id": message_id,
                    "stub": is_stub,
                }
            )
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:  # pragma: no cover — defensive
            logger.exception("agent.invoke failed")
            await ctx.reply_error(INTERNAL_ERROR, "agent.invoke failed")

    # ----------------------------------------------------------------- spawn
    #
    # v0.3.0 §2: ``agent.spawn_subagent`` is the chat-flow entry point
    # for spawning a sub-agent from a user message. It is similar to
    # ``agent.invoke`` but emits the ``agent.subagent_progress`` event
    # stream (status, progress 0..1, human summary) so the frontend
    # can render a live progress panel and a result card once the
    # run reaches ``completed`` | ``failed``.
    #
    # Params (all optional except ``name`` and ``request``):
    #     name:                 sub-agent name (FK to ``agents`` row)
    #     request:              the prompt to send
    #     parent_session_id?:   session the spawn was triggered from
    #     context_message_id?:  the user message that triggered the spawn
    #     display_name?:        override the agent name shown in the UI
    #
    # Reply:
    #     { agent_run_id, agent_id, text, status, iterations, tool_calls,
    #       task_id, message_id, parent_session_id, context_message_id,
    #       display_name, stub }
    #
    # Side effects (per stage of the run):
    #     1. emit "agent.subagent_progress" { status: "started",     progress: 0.05 }
    #     2. emit "agent.subagent_progress" { status: "thinking",    progress: 0.30 }
    #     3. emit "agent.subagent_progress" { status: "tool_call",   progress: 0.55 } (when tool calls present)
    #     4. emit "agent.subagent_progress" { status: "tool_result", progress: 0.70 } (when tool calls present)
    #     5. emit "agent.subagent_progress" { status: "completed",   progress: 1.0,  text: ... }
    #
    # On any exception the handler emits a final ``failed`` event
    # before replying with the error envelope.

    async def handle_agent_spawn_subagent(params: Any, ctx: Context) -> None:
        # Prefer the client-supplied run_id (avoids orphaned optimistic
        # UI rows); fall back to a server-generated one when absent.
        run_id = (
            str(params["run_id"])
            if isinstance(params, dict)
            and params.get("run_id")
            and str(params["run_id"]).strip()
            else f"run_{uuid.uuid4().hex[:12]}"
        )
        message_id = f"msg_{uuid.uuid4().hex[:8]}"
        name = "unknown"
        parent_session_id: str | None = None
        context_message_id: str | None = None
        try:
            agent_dao = await dao_factory()
            if agent_dao is None:
                raise HandlerError(
                    INTERNAL_ERROR,
                    "storage layer is not available; agent registry disabled",
                )
            check_params(params, expected_keys={"name", "request"})
            name = str(params["name"])
            request = str(params["request"])
            parent_session_id = (
                str(params["parent_session_id"])
                if params.get("parent_session_id")
                else None
            )
            context_message_id = (
                str(params["context_message_id"])
                if params.get("context_message_id")
                else None
            )
            display_name = (
                str(params["display_name"]) if params.get("display_name") else None
            )

            requested_agent_id = (
                str(params["agent_id"])
                if params.get("agent_id") and str(params["agent_id"]).strip()
                else None
            )
            if hasattr(agent_dao, "get_by_name_or_id"):
                config_row = await agent_dao.get_by_name_or_id(name)
                if config_row is None and requested_agent_id:
                    config_row = await agent_dao.get_by_name_or_id(requested_agent_id)
            else:
                config_row = await agent_dao.get(name)
                if config_row is None and requested_agent_id:
                    config_row = await agent_dao.get(requested_agent_id)
            if config_row is None:
                error = f"unknown agent name or id: {name!r}"
                await _emit_subagent_progress(
                    ctx,
                    run_id=run_id,
                    agent_id=name,
                    parent_session_id=parent_session_id,
                    context_message_id=context_message_id,
                    status="failed",
                    progress=1.0,
                    summary="failed",
                    error=error,
                )
                await ctx.reply_error(
                    INVALID_PARAMS, error
                )
                return
            name = str(config_row["name"])

            from ..orchestrator import SubAgentConfig, make_session_id

            config = SubAgentConfig(
                name=config_row["name"],
                system_prompt=config_row.get("system_prompt") or "",
                tool_allowlist=config_row.get("tool_allowlist"),
                model=config_row.get("model"),
                id=config_row.get("id"),
            )
            runtime = _resolve_runtime()
            handle = runtime.build(config)

            # Sub-agent run needs a session row (FK constraint). We
            # anchor the synthetic session to the parent's id when
            # provided so the run shows up alongside the parent
            # chat in the UI; otherwise we mint a placeholder.
            sub_session_id = (
                str(parent_session_id)
                if parent_session_id
                else make_session_id("subagent")
            )
            if not using_injected_dao:
                await _ensure_session(sub_session_id, title=f"subagent:{name}")

            # 1. started.
            await _emit_subagent_progress(
                ctx,
                run_id=run_id,
                agent_id=name,
                parent_session_id=parent_session_id,
                context_message_id=context_message_id,
                status="started",
                progress=0.05,
                summary=f"starting {name}",
            )
            # 2. thinking.
            await _emit_subagent_progress(
                ctx,
                run_id=run_id,
                agent_id=name,
                parent_session_id=parent_session_id,
                context_message_id=context_message_id,
                status="thinking",
                progress=0.3,
                summary=f"thinking about: {str(request)[:60]!s}",
            )

            # Drive the sub-agent. ``runtime.invoke`` either forwards
            # to ``AgentCore.run`` (real LLM) or returns the
            # deterministic stub envelope. The wire shape is the same.
            # Register the core so ``agent.cancel_subagent`` can find it.
            from .builtins import _ACTIVE_RUNS
            if hasattr(handle, "core") and handle.core is not None:
                _ACTIVE_RUNS[run_id] = {"core": handle.core, "type": "subagent"}
            try:
                result = await runtime.invoke(
                    handle, session_id=sub_session_id, request=request
                )
            finally:
                _ACTIVE_RUNS.pop(run_id, None)
            is_stub = bool(result.get("stub", True))
            final_text = result.get("text", "")
            tool_calls = list(result.get("tool_calls", []))

            # 3 + 4. tool_call / tool_result stages when the LLM
            # actually invoked tools; the stub path has an empty
            # list and we skip these stages.
            if tool_calls:
                await _emit_subagent_progress(
                    ctx,
                    run_id=run_id,
                    agent_id=name,
                    parent_session_id=parent_session_id,
                    context_message_id=context_message_id,
                    status="tool_call",
                    progress=0.55,
                    summary=f"called {len(tool_calls)} tool(s): {', '.join(str(t.get('name', '?')) for t in tool_calls[:3])}",
                )
                await _emit_subagent_progress(
                    ctx,
                    run_id=run_id,
                    agent_id=name,
                    parent_session_id=parent_session_id,
                    context_message_id=context_message_id,
                    status="tool_result",
                    progress=0.7,
                    summary=f"got {len(tool_calls)} result(s)",
                )

            # 5. completed.
            await _emit_subagent_progress(
                ctx,
                run_id=run_id,
                agent_id=name,
                parent_session_id=parent_session_id,
                context_message_id=context_message_id,
                status="completed",
                progress=1.0,
                summary="done",
                text=final_text,
            )

            await ctx.reply(
                {
                    "agent_run_id": run_id,
                    "agent_id": name,
                    "text": final_text,
                    "iterations": result.get("iterations", 0),
                    "tool_calls": tool_calls,
                    "task_id": None,
                    "message_id": message_id,
                    "parent_session_id": parent_session_id,
                    "context_message_id": context_message_id,
                    "display_name": display_name,
                    "stub": is_stub,
                }
            )
        except HandlerError as exc:
            # Best-effort: surface the failure on the progress stream
            # so the UI flips the row to ``failed`` and the user
            # sees an error rather than a hung "running" pill.
            try:
                await _emit_subagent_progress(
                    ctx,
                    run_id=run_id,
                    agent_id=name,
                    parent_session_id=parent_session_id,
                    context_message_id=context_message_id,
                    status="failed",
                    progress=1.0,
                    summary="failed",
                    error=exc.message,
                )
            except Exception:  # pragma: no cover — defensive
                logger.debug("failed to emit subagent_progress failure event")
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("agent.spawn_subagent failed")
            try:
                await _emit_subagent_progress(
                    ctx,
                    run_id=run_id,
                    agent_id=name,
                    parent_session_id=parent_session_id,
                    context_message_id=context_message_id,
                    status="failed",
                    progress=1.0,
                    summary="failed",
                    error=str(exc),
                )
            except Exception:  # pragma: no cover — defensive
                logger.debug("failed to emit subagent_progress failure event")
            await ctx.reply_error(
                INTERNAL_ERROR, f"agent.spawn_subagent failed: {exc}"
            )

    # ---------------------------------------------------------------- cancel sub
    async def handle_agent_cancel_subagent(params: Any, ctx: Context) -> None:
        """Cancel a running sub-agent by ``run_id``.

        Looks up the :class:`AgentCore` in :data:`_ACTIVE_RUNS`
        (registered by ``handle_agent_spawn_subagent``) and signals
        cooperative cancellation.  Emits a ``failed`` subagent_progress
        event so the frontend row transitions to ``failed``.
        """
        run_id = (
            params.get("run_id") if isinstance(params, dict) else None
        )
        if not run_id:
            await ctx.reply_error(-32602, "run_id is required")
            return
        from .builtins import _ACTIVE_RUNS

        entry = _ACTIVE_RUNS.pop(str(run_id), None)
        if entry and entry.get("core"):
            entry["core"].cancel()
            # Notify the UI that this run was cancelled.
            await _emit_subagent_progress(
                ctx,
                run_id=str(run_id),
                agent_id="",
                parent_session_id=None,
                context_message_id=None,
                status="failed",
                progress=1.0,
                summary="cancelled",
                error="cancelled by user",
            )
            await ctx.reply({"ok": True, "cancelled": True})
        else:
            await ctx.reply({"ok": True, "cancelled": False, "note": "no active run"})

    server.register("agent.list", handle_agent_list)
    server.register("agent.get", handle_agent_get)
    server.register("agent.create", handle_agent_create)
    server.register("agent.update", handle_agent_update)
    server.register("agent.delete", handle_agent_delete)
    server.register("agent.invoke", handle_agent_invoke)
    server.register("agent.spawn_subagent", handle_agent_spawn_subagent)
    server.register("agent.cancel_subagent", handle_agent_cancel_subagent)

# ---------------------------------------------------------------------------
# Factory + helpers
# ---------------------------------------------------------------------------

def _make_dao_factory(dao: Any | None) -> Any:
    """Return an async factory yielding an :class:`AgentDAO`.

    Mirrors :func:`handlers_sessions._make_dao_factory` /
    :func:`handlers_scheduled._make_scheduler_factory` /
    :func:`handlers_tasks._get_tracker`: the test path uses
    explicit ``dao=`` injection, the runtime path lazily resolves
    the singleton on first call.
    """
    if dao is not None:

        async def _factory() -> Any:
            return dao

        return _factory

    async def _factory() -> Any:
        # Lazy import: avoid a circular dep at module load time.
        from ..app import ensure_db
        from ..storage.dao.agents import AgentDAO
        try:
            db = await ensure_db()
            if db is None:
                return None
            return AgentDAO(db)
        except Exception:  # pragma: no cover — defensive
            logger.exception("failed to open agents DAO")
            return None

    return _factory

async def _get_tracker() -> Any:
    """Resolve the progress tracker singleton; ``None`` if unavailable.

    Mirrors the lazy-resolve helper in :mod:`handlers_tasks`. Used
    by ``agent.invoke`` to write the start / update / complete
    progress events for the stubbed response.
    """
    try:
        from ..app import get_progress_tracker, init_runtime

        existing = get_progress_tracker()
        if existing is not None:
            return existing
        try:
            await init_runtime()
        except Exception:
            pass
        return get_progress_tracker()
    except Exception:  # pragma: no cover — defensive
        return None

async def _ensure_session(session_id: str, *, title: str) -> None:
    """Best-effort: insert a placeholder ``sessions`` row.

    The ``tasks`` table has a NOT-NULL FK to ``sessions.id``. The
    PoC sub-agent invocation flow may arrive with a freshly-minted
    synthetic ``session_id`` that has no row yet. We insert one
    here so the tracker doesn't choke with a foreign-key error.
    Real client flows (where the user has an actual chat session)
    will already have the row.

    Failures are swallowed: if the sessions DAO isn't available the
    tracker will surface a clearer error on its own and the user
    gets a useful message.
    """
    try:
        from ..app import get_sessions_dao, init_runtime

        dao = get_sessions_dao()
        if dao is None:
            try:
                await init_runtime()
            except Exception:
                pass
            dao = get_sessions_dao()
        if dao is None:
            return
        existing = await dao.get(session_id)
        if existing is not None:
            return
        await dao.create(
            id=session_id, title=title, system_prompt="", model=None
        )
    except Exception:  # pragma: no cover — defensive
        logger.debug("ensure_session(%s) failed; continuing", session_id)

async def _emit_subagent_progress(
    ctx: Any,
    *,
    run_id: str,
    agent_id: str,
    parent_session_id: str | None,
    context_message_id: str | None,
    status: str,
    progress: float,
    summary: str,
    text: str | None = None,
    error: str | None = None,
) -> None:
    """Push a single ``agent.subagent_progress`` event.

    See ``docs/v0.3.0-design.md`` §2 for the wire shape. The
    ``received_at`` field is added by the receiver (frontend) so
    wall-clock skew between the agent and the webview is irrelevant.
    """
    payload: dict[str, Any] = {
        "run_id": run_id,
        "agent_id": agent_id,
        "parent_session_id": parent_session_id,
        "context_message_id": context_message_id,
        "status": status,
        "progress": max(0.0, min(1.0, float(progress))),
        "summary": summary,
    }
    if text is not None:
        payload["text"] = text
    if error is not None:
        payload["error"] = error
    await ctx.emit("agent.subagent_progress", payload)

def _normalise_allowlist(value: Any) -> list[str] | None:
    """Coerce the IPC ``tool_allowlist`` field into a list of strings.

    Returns ``None`` if the caller sent ``None`` / an empty list
    / didn't include the key (the DAO treats ``None`` as "leave
    as-is" for the update path and "no allowlist" for the create
    path).

    Raises :class:`ValueError` on a non-list, non-string input.
    """
    if value is None:
        return None
    if isinstance(value, str):
        # Convenience: a single string is treated as a one-item
        # list. The frontend almost always sends an array, but a
        # manual caller from the dev console might not.
        return [value] if value.strip() else None
    if not isinstance(value, list):
        raise ValueError(
            f"tool_allowlist must be a list of strings (got {type(value).__name__})"
        )
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise ValueError(
                f"tool_allowlist items must be strings (got {type(item).__name__})"
            )
        if item.strip():
            out.append(item)
    return out or None

__all__ = ["register_agent_handlers"]
