"""DAO — sub-agent configuration.

A *sub-agent* is a reusable agent identity: its own system prompt,
an optional list of tool names it's allowed to call, and a
preferred model. The orchestrator (Phase 2) ``spawn``s a sub-agent
by looking up its config here and instantiating an :class:`AgentCore`
with those defaults.
"""

from __future__ import annotations

import logging
from typing import Any

from ._base import apply_pagination, dumps_json, loads_json, now_iso, row_to_dict

logger = logging.getLogger(__name__)


_SORTABLE: tuple[str, ...] = ("name", "created_at")


# ---------------------------------------------------------------------------
# Async DAO
# ---------------------------------------------------------------------------


class AgentsDAO:
    """Async DAO for the ``agents`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def create(
        self,
        *,
        id: str,
        name: str,
        system_prompt: str = "",
        tool_allowlist: list[str] | None = None,
        model: str | None = None,
        created_at: str | None = None,
    ) -> dict[str, Any]:
        sql = (
            "INSERT INTO agents "
            "(id, name, system_prompt, tool_allowlist, model, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)"
        )
        params = (
            id,
            name,
            system_prompt,
            dumps_json(tool_allowlist) if tool_allowlist is not None else None,
            model,
            created_at or now_iso(),
        )
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        row = await self._db.fetchone("SELECT * FROM agents WHERE id = ?", (id,))
        return _hydrate(row)

    async def get(self, agent_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM agents WHERE id = ?", (agent_id,))
        return _hydrate(row)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM agents WHERE name = ?", (name,))
        return _hydrate(row)

    async def list(
        self,
        *,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM agents ORDER BY name ASC"
        sql, params = apply_pagination(sql, [], limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    async def update(
        self,
        agent_id: str,
        *,
        system_prompt: str | None = None,
        tool_allowlist: list[str] | None = None,
        model: str | None = None,
    ) -> dict[str, Any] | None:
        sets: list[str] = []
        params: list[Any] = []
        if system_prompt is not None:
            sets.append("system_prompt = ?")
            params.append(system_prompt)
        if tool_allowlist is not None:
            sets.append("tool_allowlist = ?")
            params.append(dumps_json(tool_allowlist))
        if model is not None:
            sets.append("model = ?")
            params.append(model)
        if not sets:
            return await self.get(agent_id)
        params.append(agent_id)
        sql = f"UPDATE agents SET {', '.join(sets)} WHERE id = ?"
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        return await self.get(agent_id)

    async def delete(self, agent_id: str) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute("DELETE FROM agents WHERE id = ?", (agent_id,))
            return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any] | None:
    d = row_to_dict(row)
    if d is None:
        return None
    d["tool_allowlist"] = loads_json(d.get("tool_allowlist"))
    return d


def create_sync(db, **fields) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    sql = (
        "INSERT INTO agents "
        "(id, name, system_prompt, tool_allowlist, model, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)"
    )
    params = (
        fields["id"],
        fields["name"],
        fields.get("system_prompt", ""),
        dumps_json(fields.get("tool_allowlist"))
        if fields.get("tool_allowlist") is not None
        else None,
        fields.get("model"),
        fields.get("created_at") or now_iso(),
    )
    with db.transaction() as conn:
        conn.execute(sql, params)
    row = db.fetchone("SELECT * FROM agents WHERE id = ?", (fields["id"],))
    return _hydrate(row)


__all__ = ["AgentsDAO", "create_sync"]
