"""DAO — agent team templates.

A *team* is a named group of agents with an orchestration mode
(parallel, sequential, round-robin, vote, or review). The team row
stores the agent membership as a JSON array of agent names; the runtime
resolves them to live :class:`SubAgentConfig` objects at spawn time.

The surface follows the same pattern as :class:`AgentDAO`:
keyed on the human-meaningful ``name`` (UNIQUE), with full CRUD
and enable/disable toggles.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ._base import apply_pagination, dumps_json, loads_json, now_iso, row_to_dict

logger = logging.getLogger(__name__)


_SORTABLE: tuple[str, ...] = ("name", "created_at", "updated_at")

_VALID_MODES: tuple[str, ...] = (
    "parallel",
    "sequential",
    "round-robin",
    "vote",
    "review",
)


class AgentTeamDAO:
    """Async DAO for the ``agent_teams`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    # ---- read ------------------------------------------------------------

    async def list_all(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        """Return every team row, ordered by ``name`` ASC."""
        sql = "SELECT * FROM agent_teams ORDER BY name ASC"
        sql, params = apply_pagination(sql, [], limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    async def get(self, team_id: str) -> dict[str, Any] | None:
        """Look up by ``id``; ``None`` if missing."""
        row = await self._db.fetchone(
            "SELECT * FROM agent_teams WHERE id = ?", (team_id,)
        )
        return _hydrate(row)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        """Look up by ``name``; ``None`` if missing."""
        if not name or not isinstance(name, str):
            raise ValueError(f"name must be a non-empty string, got {name!r}")
        row = await self._db.fetchone(
            "SELECT * FROM agent_teams WHERE name = ?", (name,)
        )
        return _hydrate(row)

    # ---- write -----------------------------------------------------------

    async def create(
        self,
        *,
        name: str,
        description: str = "",
        icon: str = "",
        color: str = "",
        agents: list[str] | None = None,
        orchestration_mode: str = "parallel",
        orchestration_config: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Insert a new team row and return it."""
        if not name or not isinstance(name, str):
            raise ValueError(f"name must be a non-empty string, got {name!r}")
        if orchestration_mode not in _VALID_MODES:
            raise ValueError(
                f"orchestration_mode must be one of {_VALID_MODES}, "
                f"got {orchestration_mode!r}"
            )
        team_id = f"team_{uuid.uuid4().hex[:10]}"
        now = now_iso()
        sql = (
            "INSERT INTO agent_teams "
            "(id, name, description, icon, color, agents, orchestration_mode, "
            "orchestration_config, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            team_id,
            name,
            description,
            icon,
            color,
            dumps_json(agents or []),
            orchestration_mode,
            dumps_json(orchestration_config),
            1 if enabled else 0,
            now,
            now,
        )
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        row = await self._db.fetchone(
            "SELECT * FROM agent_teams WHERE id = ?", (team_id,)
        )
        return _hydrate(row)

    async def update(
        self,
        name: str,
        *,
        description: str | None = None,
        icon: str | None = None,
        color: str | None = None,
        agents: list[str] | None = None,
        orchestration_mode: str | None = None,
        orchestration_config: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update fields on the team identified by ``name``.

        Only supplied fields are updated; ``None`` means "don't touch".
        ``agents`` and ``orchestration_config`` are serialised as JSON.
        ``updated_at`` is always bumped.
        """
        if not name or not isinstance(name, str):
            raise ValueError(f"name must be a non-empty string, got {name!r}")
        sets: list[str] = []
        params: list[Any] = []
        if description is not None:
            sets.append("description = ?")
            params.append(description)
        if icon is not None:
            sets.append("icon = ?")
            params.append(icon)
        if color is not None:
            sets.append("color = ?")
            params.append(color)
        if agents is not None:
            sets.append("agents = ?")
            params.append(dumps_json(agents))
        if orchestration_mode is not None:
            if orchestration_mode not in _VALID_MODES:
                raise ValueError(
                    f"orchestration_mode must be one of {_VALID_MODES}, "
                    f"got {orchestration_mode!r}"
                )
            sets.append("orchestration_mode = ?")
            params.append(orchestration_mode)
        if orchestration_config is not None:
            sets.append("orchestration_config = ?")
            params.append(dumps_json(orchestration_config))
        if not sets:
            return await self.get_by_name(name)
        sets.append("updated_at = ?")
        params.append(now_iso())
        params.append(name)
        sql = f"UPDATE agent_teams SET {', '.join(sets)} WHERE name = ?"
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        return await self.get_by_name(name)

    async def set_enabled(self, name: str, enabled: bool) -> dict[str, Any] | None:
        """Toggle the ``enabled`` flag and bump ``updated_at``."""
        if not name or not isinstance(name, str):
            raise ValueError(f"name must be a non-empty string, got {name!r}")
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE agent_teams SET enabled = ?, updated_at = ? WHERE name = ?",
                (1 if enabled else 0, now_iso(), name),
            )
        return await self.get_by_name(name)

    async def delete(self, name: str) -> bool:
        """Delete by ``name``; returns ``True`` if a row was removed."""
        if not name or not isinstance(name, str):
            raise ValueError(f"name must be a non-empty string, got {name!r}")
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM agent_teams WHERE name = ?", (name,)
            )
            return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Hydration
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any] | None:
    d = row_to_dict(row)
    if d is None:
        return None
    # JSON columns
    d["agents"] = loads_json(d.get("agents")) or []
    d["orchestration_config"] = loads_json(d.get("orchestration_config"))
    # Integer → bool
    d["enabled"] = bool(d.get("enabled", 1))
    return d


__all__ = ["AgentTeamDAO"]
