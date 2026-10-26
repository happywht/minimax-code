"""Agent configuration CRUD and durable public Native root admission.

Both invoke surfaces resolve server-owned profiles and reuse RunService's
single Native executor. Replays need only saved intent/receipts; no runtime
stub, private AgentCore loop or mutable global model is invoked by this layer.
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
            from ..app import ensure_execution_profiles
            catalog = await ensure_execution_profiles(agent_dao._db)
            await ctx.reply({"agents": agents, "execution_profiles": catalog.cards()})
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
            enabled = params.get("enabled")
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
                from .protocol import AgentSkillsConfig
                upsert_kwargs["skills"] = AgentSkillsConfig(skills=skills).skills
            if max_iterations is not None:
                upsert_kwargs["max_iterations"] = int(max_iterations)
            if temperature is not None:
                upsert_kwargs["temperature"] = float(temperature)
            if enabled is not None:
                upsert_kwargs["enabled"] = bool(enabled)
            if "execution_profile_id" in params:
                from .protocol import AgentExecutionProfileConfig
                upsert_kwargs["execution_profile_id"] = AgentExecutionProfileConfig(
                    execution_profile_id=params["execution_profile_id"]).execution_profile_id
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
                if skills is not None and not isinstance(skills, list):
                    raise HandlerError(INVALID_PARAMS, "skills must be a list of strings")
                from .protocol import AgentSkillsConfig
                upsert_kwargs["skills"] = AgentSkillsConfig(skills=skills).skills
            if "max_iterations" in params:
                upsert_kwargs["max_iterations"] = int(params["max_iterations"])
            if "temperature" in params:
                upsert_kwargs["temperature"] = float(params["temperature"])
            if "enabled" in params:
                # Enable/disable toggle from the settings UI. Before this
                # branch the key was silently dropped (DAO had no enabled
                # write path either) — the toggle reported success but the
                # row never changed.
                upsert_kwargs["enabled"] = bool(params["enabled"])
            if "execution_profile_id" in params:
                from .protocol import AgentExecutionProfileConfig
                upsert_kwargs["execution_profile_id"] = AgentExecutionProfileConfig(
                    execution_profile_id=params["execution_profile_id"]).execution_profile_id
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

    async def _invoke_root(params: Any, ctx: Context, method: str) -> None:
        from pydantic import ValidationError

        from ..agent_execution import AgentExecutionSpec, canonical_intent
        from ..app import get_db, init_runtime
        from ..runs import get_run_service
        from ..storage.dao.runs import AgentRunsDAO
        from ..workspace_ctx import resolve_root_for_session
        from .builtins import _reply_for_run, handle_agent_send_message
        from .protocol import AgentInvokeParams

        try:
            values = dict(params) if isinstance(params, dict) else params
            if isinstance(values, dict) and not values.get("session_id") and values.get("parent_session_id"):
                values["session_id"] = values["parent_session_id"]
            parsed = AgentInvokeParams.model_validate(values)
            values = parsed.model_dump()
            if parsed.parent_session_id and parsed.parent_session_id != parsed.session_id:
                raise ValueError("parent_session_id must identify the explicit source session")
            intent = canonical_intent(values, method)
            db = get_db()
            if db is None:
                await init_runtime()
                db = get_db()
            if db is None:
                raise RuntimeError("Agent admission requires durable storage")
            runs, service = AgentRunsDAO(db), get_run_service(ctx.server)
            existing = await runs.find_agent_root(parsed.session_id, parsed.idempotency_key, intent)
            if existing is not None:
                await _reply_for_run(ctx, runs, service, existing,
                    await service.job_for_run(existing["id"]), created=False, wait=not parsed.admission_only)
                return
            source = await db.fetchone("SELECT * FROM sessions WHERE id=?", (parsed.session_id,))
            if source is None or source["engine"] != "native":
                raise ValueError("Agent invocation requires an existing explicit Native source session")
            workspace = str(await resolve_root_for_session(parsed.session_id))
            agent_dao = await dao_factory()
            if agent_dao is None:
                raise RuntimeError("Agent registry storage is unavailable")
            row = await agent_dao.get_by_name_or_id(parsed.name)
            if row is None and parsed.agent_id:
                row = await agent_dao.get_by_name_or_id(parsed.agent_id)
            if row is None:
                raise ValueError("Agent is unavailable")
            session_id, workspace_id = f"ses_{uuid.uuid4().hex}", f"agent_{uuid.uuid4().hex[:16]}"
            spec, model = await AgentExecutionSpec.capture(row, db, ctx.server, values, intent,
                workspace, source["project_id"], session_id, workspace_id)
            await handle_agent_send_message(
                {"session_id": session_id, "mode": "execute", "content": parsed.request,
                 "agent_intent": intent, "idempotency_key": parsed.idempotency_key,
                 "budget_tokens": parsed.budget_tokens}, ctx, wait=not parsed.admission_only,
                internal_agent=spec, skill_model=model)
        except (ValidationError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception as exc:
            logger.exception("%s failed", method)
            await ctx.reply_error(INTERNAL_ERROR, str(exc))

    async def handle_agent_invoke(params: Any, ctx: Context) -> None:
        await _invoke_root(params, ctx, "agent.invoke")

    async def handle_agent_spawn_subagent(params: Any, ctx: Context) -> None:
        await _invoke_root(params, ctx, "agent.spawn_subagent")

    async def handle_agent_cancel_subagent(params: Any, ctx: Context) -> None:
        from ..app import get_db
        from ..runs import get_run_service
        from ..storage.dao.runs import AgentRunsDAO

        if not isinstance(params, dict) or not isinstance(params.get("run_id"), str):
            await ctx.reply_error(INVALID_PARAMS, "run_id is required")
            return
        db = get_db()
        run = await AgentRunsDAO(db).get_run(params["run_id"]) if db is not None else None
        if run is None:
            await ctx.reply({"ok": True, "cancelled": False, "cancel_requested": False,
                             "run_id": params["run_id"], "note": "No matching durable agent run"})
            return
        if "agent_execution" in (run.get("metadata") or {}):
            requested = get_run_service(ctx.server).cancel_run(run["id"])
        elif (run.get("metadata") or {}).get("source") == "tool":
            # Internal children retain their owner's existing lifetime. This
            # compatibility signal never declares teardown or terminal state.
            from .builtins import _ACTIVE_RUNS
            entry = _ACTIVE_RUNS.get(run["id"])
            requested = bool(entry and entry.get("core"))
            if requested:
                entry["core"].cancel()
        else:
            await ctx.reply_error(INVALID_PARAMS, "Unknown durable public agent run")
            return
        await ctx.reply({"ok": True, "cancelled": requested, "cancel_requested": requested,
                         "run_id": run["id"], "run_status": run["status"]})

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

def _config_from_row(config_row: dict[str, Any]) -> Any:
    """Build a full :class:`SubAgentConfig` from a persisted DAO row.

    v1.2.2 regression: ``agent.invoke`` / ``agent.spawn_subagent``
    used to construct the config with only five fields, silently
    dropping the persisted ``max_iterations`` / ``temperature`` /
    ``skills`` (…) — a sub-agent saved with a 200-iteration budget
    actually ran at the default 50. Fields absent or NULL in the row
    keep their dataclass defaults.
    """
    from ..orchestrator import SubAgentConfig

    kwargs: dict[str, Any] = {}
    if config_row.get("max_iterations") is not None:
        kwargs["max_iterations"] = int(config_row["max_iterations"])
    if config_row.get("temperature") is not None:
        kwargs["temperature"] = float(config_row["temperature"])
    if config_row.get("skills"):
        kwargs["skills"] = list(config_row["skills"])
    if config_row.get("tags"):
        kwargs["tags"] = list(config_row["tags"])
    return SubAgentConfig(
        name=str(config_row["name"]),
        system_prompt=config_row.get("system_prompt") or "",
        tool_allowlist=config_row.get("tool_allowlist"),
        model=config_row.get("model"),
        id=config_row.get("id"),
        description=str(config_row.get("description") or ""),
        enabled=bool(config_row.get("enabled", True)),
        icon=str(config_row.get("icon") or ""),
        color=str(config_row.get("color") or ""),
        category=str(config_row.get("category") or ""),
        team_id=config_row.get("team_id"),
        execution_profile_id=config_row.get("execution_profile_id"),
        **kwargs,
    )

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
