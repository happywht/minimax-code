"""JSON-RPC handlers for the ``team.*`` namespace.

Manages agent team templates — named groups of agents with an
orchestration mode (parallel, sequential, round-robin, vote, or review).

Methods:
    team.list      — list all teams
    team.create    — create a new team
    team.get       — get a team by name
    team.update    — update a team's fields
    team.delete    — delete a team
    team.enable    — enable a team
    team.disable   — disable a team
    team.spawn     — run all agents in a team
    team.run.get   — query a persisted team run by task_id

v0.11.0 — Agent Studio orchestration enhancements.
"""

from __future__ import annotations

import logging
from typing import Any

from .handler_utils import HandlerError, check_params
from .protocol import INTERNAL_ERROR, INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_team_handlers(
    server: Any,
    *,
    dao: Any = None,
    agent_dao: Any = None,
    runs_dao: Any = None,
) -> None:
    """Register the ``team.*`` handlers on ``server``.

    Parameters
    ----------
    server:
        The :class:`~minimax_code.ipc.server.IPCServer` instance.
    dao:
        Optional pre-built :class:`AgentTeamDAO`. When ``None``,
        handlers resolve the DAO lazily on first call.
    agent_dao:
        Optional pre-built :class:`AgentDAO` for ``team.spawn``. When
        ``None``, the handler resolves it lazily via the process-wide
        DB singleton.
    runs_dao:
        Optional pre-built :class:`AgentRunsDAO` for ``team.run.get``.
        When ``None``, the handler resolves it lazily via the
        process-wide DB singleton.
    """
    dao_factory = _make_dao_factory(dao)
    agent_dao_factory = _make_agent_dao_factory(agent_dao)
    runs_dao_factory = _make_runs_dao_factory(runs_dao)

    # ------------------------------------------------------------------ list

    async def handle_team_list(params: Any, ctx: Context) -> None:
        try:
            team_dao = await dao_factory()
            if team_dao is None:
                await ctx.reply_error(
                    INTERNAL_ERROR,
                    "storage layer is not available; team registry disabled",
                )
                return
            teams = await team_dao.list_all()
            await ctx.reply({"teams": teams})
        except Exception:  # pragma: no cover — defensive
            logger.exception("team.list failed")
            await ctx.reply_error(INTERNAL_ERROR, "team.list failed")

    # ------------------------------------------------------------------- get

    async def handle_team_get(params: Any, ctx: Context) -> None:
        try:
            team_dao = await dao_factory()
            if team_dao is None:
                await ctx.reply_error(INTERNAL_ERROR, "storage unavailable")
                return
            check_params(params, expected_keys={"name"})
            name = str(params["name"])
            team = await team_dao.get_by_name(name)
            if team is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown team name: {name!r}"
                )
                return
            await ctx.reply({"team": team})
        except Exception as exc:
            if isinstance(exc, HandlerError):
                await ctx.reply_error(exc.code, exc.message)
            else:
                logger.exception("team.get failed")
                await ctx.reply_error(INTERNAL_ERROR, "team.get failed")

    # ---------------------------------------------------------------- create

    async def handle_team_create(params: Any, ctx: Context) -> None:
        try:
            team_dao = await dao_factory()
            if team_dao is None:
                await ctx.reply_error(INTERNAL_ERROR, "storage unavailable")
                return
            check_params(params, expected_keys={"name"})
            name = str(params["name"]).strip()
            if not name:
                raise HandlerError(INVALID_PARAMS, "name must be a non-empty string")
            description = str(params.get("description", ""))
            icon = str(params.get("icon", ""))
            color = str(params.get("color", ""))
            agents = params.get("agents")
            if agents is not None and not isinstance(agents, list):
                raise HandlerError(
                    INVALID_PARAMS, "agents must be a list of strings"
                )
            orchestration_mode = str(
                params.get("orchestration_mode", "parallel")
            )
            orchestration_config = params.get("orchestration_config")
            if orchestration_config is not None and not isinstance(orchestration_config, dict):
                raise HandlerError(
                    INVALID_PARAMS, "orchestration_config must be a JSON object"
                )
            team = await team_dao.create(
                name=name,
                description=description,
                icon=icon,
                color=color,
                agents=agents if isinstance(agents, list) else [],
                orchestration_mode=orchestration_mode,
                orchestration_config=orchestration_config,
            )
            await ctx.reply({"team": team})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:  # pragma: no cover — defensive
            logger.exception("team.create failed")
            await ctx.reply_error(INTERNAL_ERROR, "team.create failed")

    # ---------------------------------------------------------------- update

    async def handle_team_update(params: Any, ctx: Context) -> None:
        try:
            team_dao = await dao_factory()
            if team_dao is None:
                await ctx.reply_error(INTERNAL_ERROR, "storage unavailable")
                return
            check_params(params, expected_keys={"name"})
            name = str(params["name"])
            # All fields optional on update
            updates: dict[str, Any] = {}
            if "description" in params:
                updates["description"] = str(params["description"])
            if "icon" in params:
                updates["icon"] = str(params["icon"])
            if "color" in params:
                updates["color"] = str(params["color"])
            if "agents" in params:
                agents = params["agents"]
                if not isinstance(agents, list):
                    raise HandlerError(
                        INVALID_PARAMS, "agents must be a list of strings"
                    )
                updates["agents"] = agents
            if "orchestration_mode" in params:
                updates["orchestration_mode"] = str(params["orchestration_mode"])
            if "orchestration_config" in params:
                orchestration_config = params["orchestration_config"]
                if orchestration_config is not None and not isinstance(orchestration_config, dict):
                    raise HandlerError(
                        INVALID_PARAMS, "orchestration_config must be a JSON object"
                    )
                updates["orchestration_config"] = orchestration_config
            team = await team_dao.update(name, **updates)
            if team is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown team name: {name!r}"
                )
                return
            await ctx.reply({"team": team})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:  # pragma: no cover — defensive
            logger.exception("team.update failed")
            await ctx.reply_error(INTERNAL_ERROR, "team.update failed")

    # ---------------------------------------------------------------- delete

    async def handle_team_delete(params: Any, ctx: Context) -> None:
        try:
            team_dao = await dao_factory()
            if team_dao is None:
                await ctx.reply_error(INTERNAL_ERROR, "storage unavailable")
                return
            check_params(params, expected_keys={"name"})
            name = str(params["name"])
            ok = await team_dao.delete(name)
            if not ok:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown team name: {name!r}"
                )
                return
            await ctx.reply({"ok": True, "name": name})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception:  # pragma: no cover — defensive
            logger.exception("team.delete failed")
            await ctx.reply_error(INTERNAL_ERROR, "team.delete failed")

    # --------------------------------------------------------------- enable

    async def handle_team_enable(params: Any, ctx: Context) -> None:
        try:
            team_dao = await dao_factory()
            if team_dao is None:
                await ctx.reply_error(INTERNAL_ERROR, "storage unavailable")
                return
            check_params(params, expected_keys={"name"})
            name = str(params["name"])
            team = await team_dao.set_enabled(name, True)
            if team is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown team name: {name!r}"
                )
                return
            await ctx.reply({"team": team})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception:  # pragma: no cover — defensive
            logger.exception("team.enable failed")
            await ctx.reply_error(INTERNAL_ERROR, "team.enable failed")

    # -------------------------------------------------------------- disable

    async def handle_team_disable(params: Any, ctx: Context) -> None:
        try:
            team_dao = await dao_factory()
            if team_dao is None:
                await ctx.reply_error(INTERNAL_ERROR, "storage unavailable")
                return
            check_params(params, expected_keys={"name"})
            name = str(params["name"])
            team = await team_dao.set_enabled(name, False)
            if team is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown team name: {name!r}"
                )
                return
            await ctx.reply({"team": team})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception:  # pragma: no cover — defensive
            logger.exception("team.disable failed")
            await ctx.reply_error(INTERNAL_ERROR, "team.disable failed")

    # --------------------------------------------------------------- spawn

    async def handle_team_spawn(params: Any, ctx: Context) -> None:
        """Run all agents in a team and return the merged result.

        Emits ``agent.team_progress`` events during execution and
        persists the result to ``agent_runs`` under ``task_id``.
        """
        try:
            check_params(params, expected_keys={"team_name", "request"})
            team_name = str(params["team_name"])
            request = str(params["request"])
            session_id = params.get("session_id")
            if session_id is not None:
                session_id = str(session_id)
            parent_session_id = params.get("parent_session_id")
            if parent_session_id is not None:
                parent_session_id = str(parent_session_id)

            # Resolve DAOs lazily
            team_dao = await dao_factory()
            if team_dao is None:
                await ctx.reply_error(
                    INTERNAL_ERROR, "storage unavailable"
                )
                return

            agent_dao_instance = await agent_dao_factory()

            # v1.2.0: reuse ``ctx.emit`` directly instead of hand-mirroring
            # its envelope dance (Event + stdout + WS fan-out) here. The
            # hand-rolled copy had already drifted once (no metadata support)
            # and bought nothing — the handler owns a live Context.
            # v1.2.2: inject the process-wide LLM singleton. The factory
            # below used to omit ``llm=``, leaving TeamOrchestrator._llm
            # permanently None — every member SubAgentRuntime then fell
            # back to the canned "stub: agent xxx would handle..." path,
            # so team runs never produced a real answer. None (no db /
            # boot not yet run) keeps the legacy stub path for tests.
            from ..app import get_subagent_llm
            from ..orchestrator.team_orchestrator import TeamOrchestrator

            orch = TeamOrchestrator(
                team_dao=team_dao,
                agent_dao=agent_dao_instance,
                llm=get_subagent_llm(),
                emit_event=ctx.emit,
            )
            result = await orch.run(
                team_name,
                request,
                session_id=session_id,
                parent_session_id=parent_session_id,
            )
            await ctx.reply({
                "team_name": result.team_name,
                "orchestration_mode": result.orchestration_mode,
                "merged_text": result.merged_text,
                "agents_run": [
                    {
                        "agent_name": r.agent_name,
                        "success": r.success,
                        "text": r.text,
                        "error": r.error,
                        "iterations": r.iterations,
                        "stub": r.stub,
                    }
                    for r in result.agents_run
                ],
                "conflicts": [
                    {
                        "file_path": c.file_path,
                        "agents": c.agents,
                        "conflict_type": c.conflict_type,
                    }
                    for c in result.conflicts
                ],
                "task_id": result.task_id,
                "success": result.success,
            })
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception:
            logger.exception("team.spawn failed")
            await ctx.reply_error(INTERNAL_ERROR, "team.spawn failed")

    # ------------------------------------------------------------ run.get

    async def handle_team_run_get(params: Any, ctx: Context) -> None:
        """Query a persisted team run by ``task_id``."""
        try:
            check_params(params, expected_keys={"task_id"})
            task_id = str(params["task_id"])

            runs_dao_instance = await runs_dao_factory()
            if runs_dao_instance is None:
                await ctx.reply_error(INTERNAL_ERROR, "storage unavailable")
                return

            run = await runs_dao_instance.get_run(task_id)
            if run is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown team run: {task_id!r}"
                )
                return

            metadata = run.get("metadata") or {}
            team_result = metadata.get("team_result") or {}
            await ctx.reply({
                "run": run,
                "result": {
                    "team_name": team_result.get("team_name", ""),
                    "orchestration_mode": team_result.get(
                        "orchestration_mode", ""
                    ),
                    "merged_text": team_result.get("merged_text", ""),
                    "agents_run": team_result.get("agents_run", []),
                    "conflicts": team_result.get("conflicts", []),
                    "success": team_result.get("success", False),
                },
            })
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception:
            logger.exception("team.run.get failed")
            await ctx.reply_error(INTERNAL_ERROR, "team.run.get failed")

    server.register("team.list", handle_team_list)
    server.register("team.get", handle_team_get)
    server.register("team.create", handle_team_create)
    server.register("team.update", handle_team_update)
    server.register("team.delete", handle_team_delete)
    server.register("team.enable", handle_team_enable)
    server.register("team.disable", handle_team_disable)
    server.register("team.spawn", handle_team_spawn)
    server.register("team.run.get", handle_team_run_get)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_dao_factory(dao: Any | None) -> Any:
    """Return an async factory yielding an :class:`AgentTeamDAO`."""
    if dao is not None:

        async def _factory() -> Any:
            return dao

        return _factory

    async def _factory() -> Any:
        try:
            from ..app import get_db
            from ..storage.dao.agent_teams import AgentTeamDAO

            db = get_db()
            if db is None:
                logger.warning("storage not initialised; team DAO unavailable")
                return None
            return AgentTeamDAO(db)
        except Exception:  # pragma: no cover — defensive
            logger.exception("failed to open team DAO")
            return None

    return _factory


def _make_agent_dao_factory(agent_dao: Any | None) -> Any:
    """Return an async factory yielding an :class:`AgentDAO}."""
    if agent_dao is not None:

        async def _factory() -> Any:
            return agent_dao

        return _factory

    async def _factory() -> Any:
        try:
            from ..app import get_db
            from ..storage.dao.agents import AgentDAO

            db = get_db()
            if db is None:
                logger.warning("storage not initialised; agent DAO unavailable")
                return None
            return AgentDAO(db)
        except Exception:  # pragma: no cover — defensive
            logger.exception("failed to build agent DAO for team.spawn")
            return None

    return _factory


def _make_runs_dao_factory(runs_dao: Any | None) -> Any:
    """Return an async factory yielding an :class:`AgentRunsDAO}."""
    if runs_dao is not None:

        async def _factory() -> Any:
            return runs_dao

        return _factory

    async def _factory() -> Any:
        try:
            from ..app import get_db
            from ..storage.dao.runs import AgentRunsDAO

            db = get_db()
            if db is None:
                logger.warning("storage not initialised; runs DAO unavailable")
                return None
            return AgentRunsDAO(db)
        except Exception:  # pragma: no cover — defensive
            logger.exception("failed to build runs DAO for team.run.get")
            return None

    return _factory


__all__ = ["register_team_handlers"]
