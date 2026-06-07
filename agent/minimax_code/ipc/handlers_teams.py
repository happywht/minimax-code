"""JSON-RPC handlers for the ``team.*`` namespace.

Manages agent team templates — named groups of agents with an
orchestration mode (parallel, sequential, or round-robin).

Methods:
    team.list    — list all teams
    team.create  — create a new team
    team.get     — get a team by name
    team.update  — update a team's fields
    team.delete  — delete a team
    team.enable  — enable a team
    team.disable — disable a team

v0.8.0 — Enterprise Multi-Agent.
"""

from __future__ import annotations

import logging
from typing import Any

from .protocol import INTERNAL_ERROR, INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_team_handlers(server: Any, *, dao: Any = None) -> None:
    """Register the ``team.*`` handlers on ``server``.

    Parameters
    ----------
    server:
        The :class:`~minimax_code.ipc.server.IPCServer` instance.
    dao:
        Optional pre-built :class:`AgentTeamDAO`. When ``None``,
        handlers resolve the DAO lazily on first call.
    """
    dao_factory = _make_dao_factory(dao)

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
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("team.list failed")
            await ctx.reply_error(INTERNAL_ERROR, f"team.list failed: {exc}")

    # ------------------------------------------------------------------- get

    async def handle_team_get(params: Any, ctx: Context) -> None:
        try:
            team_dao = await dao_factory()
            if team_dao is None:
                await ctx.reply_error(INTERNAL_ERROR, "storage unavailable")
                return
            _check_params(params, expected_keys={"name"})
            name = str(params["name"])
            team = await team_dao.get_by_name(name)
            if team is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown team name: {name!r}"
                )
                return
            await ctx.reply({"team": team})
        except Exception as exc:
            if isinstance(exc, _ParamError):
                await ctx.reply_error(exc.code, exc.message)
            else:
                logger.exception("team.get failed")
                await ctx.reply_error(INTERNAL_ERROR, f"team.get failed: {exc}")

    # ---------------------------------------------------------------- create

    async def handle_team_create(params: Any, ctx: Context) -> None:
        try:
            team_dao = await dao_factory()
            if team_dao is None:
                await ctx.reply_error(INTERNAL_ERROR, "storage unavailable")
                return
            _check_params(params, expected_keys={"name"})
            name = str(params["name"])
            description = str(params.get("description", ""))
            icon = str(params.get("icon", ""))
            color = str(params.get("color", ""))
            agents = params.get("agents")
            if agents is not None and not isinstance(agents, list):
                raise _ParamError(
                    INVALID_PARAMS, "agents must be a list of strings"
                )
            orchestration_mode = str(
                params.get("orchestration_mode", "parallel")
            )
            team = await team_dao.create(
                name=name,
                description=description,
                icon=icon,
                color=color,
                agents=agents if isinstance(agents, list) else [],
                orchestration_mode=orchestration_mode,
            )
            await ctx.reply({"team": team})
        except _ParamError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("team.create failed")
            await ctx.reply_error(INTERNAL_ERROR, f"team.create failed: {exc}")

    # ---------------------------------------------------------------- update

    async def handle_team_update(params: Any, ctx: Context) -> None:
        try:
            team_dao = await dao_factory()
            if team_dao is None:
                await ctx.reply_error(INTERNAL_ERROR, "storage unavailable")
                return
            _check_params(params, expected_keys={"name"})
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
                    raise _ParamError(
                        INVALID_PARAMS, "agents must be a list of strings"
                    )
                updates["agents"] = agents
            if "orchestration_mode" in params:
                updates["orchestration_mode"] = str(params["orchestration_mode"])
            team = await team_dao.update(name, **updates)
            if team is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown team name: {name!r}"
                )
                return
            await ctx.reply({"team": team})
        except _ParamError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("team.update failed")
            await ctx.reply_error(INTERNAL_ERROR, f"team.update failed: {exc}")

    # ---------------------------------------------------------------- delete

    async def handle_team_delete(params: Any, ctx: Context) -> None:
        try:
            team_dao = await dao_factory()
            if team_dao is None:
                await ctx.reply_error(INTERNAL_ERROR, "storage unavailable")
                return
            _check_params(params, expected_keys={"name"})
            name = str(params["name"])
            ok = await team_dao.delete(name)
            if not ok:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown team name: {name!r}"
                )
                return
            await ctx.reply({"ok": True, "name": name})
        except _ParamError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except ValueError as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("team.delete failed")
            await ctx.reply_error(INTERNAL_ERROR, f"team.delete failed: {exc}")

    # --------------------------------------------------------------- enable

    async def handle_team_enable(params: Any, ctx: Context) -> None:
        try:
            team_dao = await dao_factory()
            if team_dao is None:
                await ctx.reply_error(INTERNAL_ERROR, "storage unavailable")
                return
            _check_params(params, expected_keys={"name"})
            name = str(params["name"])
            team = await team_dao.set_enabled(name, True)
            if team is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown team name: {name!r}"
                )
                return
            await ctx.reply({"team": team})
        except _ParamError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("team.enable failed")
            await ctx.reply_error(INTERNAL_ERROR, f"team.enable failed: {exc}")

    # -------------------------------------------------------------- disable

    async def handle_team_disable(params: Any, ctx: Context) -> None:
        try:
            team_dao = await dao_factory()
            if team_dao is None:
                await ctx.reply_error(INTERNAL_ERROR, "storage unavailable")
                return
            _check_params(params, expected_keys={"name"})
            name = str(params["name"])
            team = await team_dao.set_enabled(name, False)
            if team is None:
                await ctx.reply_error(
                    INVALID_PARAMS, f"unknown team name: {name!r}"
                )
                return
            await ctx.reply({"team": team})
        except _ParamError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:  # pragma: no cover — defensive
            logger.exception("team.disable failed")
            await ctx.reply_error(INTERNAL_ERROR, f"team.disable failed: {exc}")

    # --------------------------------------------------------------- spawn

    async def handle_team_spawn(params: Any, ctx: Context) -> None:
        """Run all agents in a team and return the merged result.

        Emits ``agent.team_progress`` events during execution.
        """
        try:
            _check_params(params, expected_keys={"team_name", "request"})
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

            # Build agent DAO
            agent_dao = await _make_agent_dao()

            # Build emit callback from server context.
            # IPCServer has no ``emit()`` method — we mirror what
            # ``Context.emit()`` does: create an Event envelope,
            # write it to stdout, then fan out to WS listeners.
            async def _emit(event_name: str, payload: Any) -> None:
                try:
                    from .protocol import Event

                    env = Event(event=event_name, data=payload)
                    await server._send(env.to_bytes())
                    server.notify(env.model_dump(exclude_none=True))
                except Exception:
                    logger.warning("Failed to emit %s", event_name, exc_info=True)

            from ..orchestrator.team_orchestrator import TeamOrchestrator

            orch = TeamOrchestrator(
                team_dao=team_dao,
                agent_dao=agent_dao,
                emit_event=_emit,
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
        except _ParamError as exc:
            await ctx.reply_error(exc.code, exc.message)
        except Exception as exc:
            logger.exception("team.spawn failed")
            await ctx.reply_error(INTERNAL_ERROR, f"team.spawn failed: {exc}")

    server.register("team.list", handle_team_list)
    server.register("team.get", handle_team_get)
    server.register("team.create", handle_team_create)
    server.register("team.update", handle_team_update)
    server.register("team.delete", handle_team_delete)
    server.register("team.enable", handle_team_enable)
    server.register("team.disable", handle_team_disable)
    server.register("team.spawn", handle_team_spawn)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _ParamError(Exception):
    """Internal sentinel for parameter validation errors."""

    def __init__(self, code: int, message: str) -> None:
        self.code = code
        self.message = message


def _check_params(params: Any, *, expected_keys: set[str]) -> None:
    """Validate JSON-RPC params; raise :class:`_ParamError` on failure."""
    if not expected_keys:
        return
    if params is None or not isinstance(params, dict):
        raise _ParamError(
            INVALID_PARAMS,
            "params must be a JSON object with the required keys",
        )
    missing = expected_keys - set(params.keys())
    if missing:
        raise _ParamError(
            INVALID_PARAMS,
            f"missing required param(s): {sorted(missing)}",
        )
    for key in expected_keys:
        if params[key] is None or (
            isinstance(params[key], str) and not params[key].strip()
        ):
            raise _ParamError(
                INVALID_PARAMS, f"param {key!r} must be a non-empty value"
            )


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


async def _make_agent_dao() -> Any:
    """Lazily build an :class:`AgentDAO` from the process-wide DB singleton."""
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


__all__ = ["register_team_handlers"]
