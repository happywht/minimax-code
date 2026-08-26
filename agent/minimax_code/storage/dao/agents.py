"""DAO — sub-agent configuration.

A *sub-agent* is a reusable agent identity: its own system prompt,
an optional list of tool names it's allowed to call, and a
preferred model. The orchestrator (Phase 2) ``spawn``s a sub-agent
by looking up its config here and instantiating an :class:`AgentCore`
with those defaults.

Two surface layers
------------------

* :class:`AgentsDAO` — *low-level* CRUD keyed on the row's ``id``.
  Used by tests and other internal callers that already work with
  the raw id space.

* :class:`AgentDAO` — *high-level* convenience layer used by the
  ``agent.*`` IPC namespace. Keyed on the human-meaningful ``name``
  (the column carries a UNIQUE constraint) and exposes an
  ``upsert(name, system_prompt, tool_allowlist, model)`` flow that
  matches the typical "create-or-update by name" RPC contract.
  The class auto-generates a stable ``id`` and stores the
  ``tool_allowlist`` as JSON transparently.

Both classes share the same ``agents`` table — they are siblings,
not parent/child.
"""

from __future__ import annotations

import logging
import uuid
from sqlite3 import IntegrityError
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
        description: str | None = None,
        icon: str | None = None,
        color: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        team_id: str | None = None,
        skills: list[str] | None = None,
        max_iterations: int | None = None,
        temperature: float | None = None,
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
        # v0.8.0 extended fields
        if description is not None:
            sets.append("description = ?")
            params.append(description)
        if icon is not None:
            sets.append("icon = ?")
            params.append(icon)
        if color is not None:
            sets.append("color = ?")
            params.append(color)
        if category is not None:
            sets.append("category = ?")
            params.append(category)
        if tags is not None:
            sets.append("tags = ?")
            params.append(dumps_json(tags))
        if team_id is not None:
            sets.append("team_id = ?")
            params.append(team_id)
        if skills is not None:
            sets.append("skills = ?")
            params.append(dumps_json(skills))
        if max_iterations is not None:
            sets.append("max_iterations = ?")
            params.append(max_iterations)
        if temperature is not None:
            sets.append("temperature = ?")
            params.append(temperature)
        if not sets:
            return await self.get(agent_id)
        sets.append("updated_at = ?")
        params.append(now_iso())
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
    # v0.8.0 extended columns — safe no-op when columns don't exist yet
    d["tags"] = loads_json(d.get("tags"))
    d["skills"] = loads_json(d.get("skills"))
    d["enabled"] = bool(d.get("enabled", 1))
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


__all__ = ["AgentsDAO", "AgentDAO", "create_sync"]


# ---------------------------------------------------------------------------
# High-level DAO (IPC / orchestrator surface)
# ---------------------------------------------------------------------------


class AgentDAO:
    """High-level DAO for the ``agents`` table — keyed by ``name``.

    This is the convenience layer the ``agent.*`` IPC namespace and
    the :class:`SubAgentRuntime` use. Compared to :class:`AgentsDAO`:

    * :meth:`get` and :meth:`delete` are keyed on the human-meaningful
      ``name`` (which is UNIQUE in the schema) rather than the
      internal ``id`` — that matches the natural RPC contract
      ("`agent.get` `{ name }`" — no id ever leaks to the caller).
    * :meth:`upsert` is a single call that *inserts when missing,
      updates in place when present*. Returns the resulting row.
    * :meth:`list_all` is the only read-list method, returns plain
      dicts with ``tool_allowlist`` already deserialised from JSON.
    """

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    # ---- read ------------------------------------------------------------

    async def list_all(self) -> list[dict[str, Any]]:
        """Return every row, ordered by ``name`` ASC, with allowlist deserialised."""
        rows = await self._db.fetchall(
            "SELECT * FROM agents ORDER BY name ASC"
        )
        return [_hydrate(r) for r in rows]

    async def get(self, name: str) -> dict[str, Any] | None:
        """Look up by ``name``; ``None`` if missing."""
        if not name or not isinstance(name, str):
            raise ValueError(f"name must be a non-empty string, got {name!r}")
        row = await self._db.fetchone(
            "SELECT * FROM agents WHERE name = ?", (name,)
        )
        return _hydrate(row)

    async def get_by_id(self, agent_id: str) -> dict[str, Any] | None:
        """Look up by internal ``id``; ``None`` if missing."""
        if not agent_id or not isinstance(agent_id, str):
            raise ValueError(f"agent_id must be a non-empty string, got {agent_id!r}")
        row = await self._db.fetchone(
            "SELECT * FROM agents WHERE id = ?", (agent_id,)
        )
        return _hydrate(row)

    async def get_by_name_or_id(self, value: str) -> dict[str, Any] | None:
        """Look up a sub-agent by public name first, then internal id.

        The IPC contract historically accepted ``name`` while parts of
        the frontend used ``AgentInfo.id``. Supporting both keeps old
        clients working while new UI paths use ``name`` explicitly.
        """
        found = await self.get(value)
        if found is not None:
            return found
        return await self.get_by_id(value)

    # ---- write -----------------------------------------------------------

    async def upsert(
        self,
        name: str,
        system_prompt: str = "",
        tool_allowlist: list[str] | None = None,
        model: str | None = None,
        *,
        description: str | None = None,
        icon: str | None = None,
        color: str | None = None,
        category: str | None = None,
        tags: list[str] | None = None,
        team_id: str | None = None,
        skills: list[str] | None = None,
        max_iterations: int | None = None,
        temperature: float | None = None,
    ) -> dict[str, Any]:
        """Insert-or-update by ``name``.

        If a row with ``name`` already exists, the supplied
        fields are written (other fields stay as-is — the caller
        sends only what they want to change). If no row exists,
        a new one is inserted with an auto-generated ``id``
        (``agent_<12-hex>``) and the current UTC timestamp.
        The resulting row is always returned so the caller can
        read back the id / created_at.

        ``tool_allowlist`` may be ``None`` (clears the allowlist)
        or a list of strings; non-list inputs raise ``ValueError``.
        """
        if not name or not isinstance(name, str):
            raise ValueError(f"name must be a non-empty string, got {name!r}")
        if not isinstance(system_prompt, str):
            raise ValueError(
                f"system_prompt must be a string, got {type(system_prompt).__name__}"
            )
        if tool_allowlist is not None:
            if not isinstance(tool_allowlist, list) or not all(
                isinstance(x, str) for x in tool_allowlist
            ):
                raise ValueError(
                    "tool_allowlist must be a list of strings (or None)"
                )
        if model is not None and not isinstance(model, str):
            raise ValueError(f"model must be a string or None, got {model!r}")

        existing = await self.get(name)
        if existing is None:
            agent_id = f"agent_{uuid.uuid4().hex[:12]}"
            now = now_iso()
            sql = (
                "INSERT INTO agents "
                "(id, name, system_prompt, tool_allowlist, model, "
                "description, icon, color, category, tags, team_id, "
                "skills, max_iterations, temperature, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )
            params = (
                agent_id,
                name,
                system_prompt,
                dumps_json(tool_allowlist) if tool_allowlist is not None else None,
                model,
                description or "",
                icon or "",
                color or "",
                category or "",
                dumps_json(tags) if tags is not None else None,
                team_id,
                dumps_json(skills) if skills is not None else None,
                # P0-3: current default 100 — a sub-agent needs
                # headroom to call report_completion after writing a
                # non-trivial deliverable (legacy rows were lifted by
                # migration 029).
                max_iterations if max_iterations is not None else 100,
                temperature,
                now,
                now,
            )
            try:
                async with self._db.transaction() as conn:
                    await conn.execute(sql, params)
            except IntegrityError:
                existing = await self.get(name)
                if existing is None:  # pragma: no cover — defensive
                    raise
        if existing is not None:
            sets: list[str] = []
            params: list[Any] = []
            sets.append("system_prompt = ?")
            params.append(system_prompt)
            if tool_allowlist is not None:
                sets.append("tool_allowlist = ?")
                params.append(dumps_json(tool_allowlist))
            if model is not None:
                sets.append("model = ?")
                params.append(model)
            # v0.8.0 extended fields
            if description is not None:
                sets.append("description = ?")
                params.append(description)
            if icon is not None:
                sets.append("icon = ?")
                params.append(icon)
            if color is not None:
                sets.append("color = ?")
                params.append(color)
            if category is not None:
                sets.append("category = ?")
                params.append(category)
            if tags is not None:
                sets.append("tags = ?")
                params.append(dumps_json(tags))
            if team_id is not None:
                sets.append("team_id = ?")
                params.append(team_id)
            if skills is not None:
                sets.append("skills = ?")
                params.append(dumps_json(skills))
            if max_iterations is not None:
                sets.append("max_iterations = ?")
                params.append(max_iterations)
            if temperature is not None:
                sets.append("temperature = ?")
                params.append(temperature)
            sets.append("updated_at = ?")
            params.append(now_iso())
            params.append(name)
            sql = f"UPDATE agents SET {', '.join(sets)} WHERE name = ?"
            async with self._db.transaction() as conn:
                await conn.execute(sql, params)
        return await self.get(name)

    async def delete(self, name: str) -> bool:
        """Delete by ``name``; returns ``True`` if a row was removed."""
        if not name or not isinstance(name, str):
            raise ValueError(f"name must be a non-empty string, got {name!r}")
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM agents WHERE name = ?", (name,)
            )
            return cur.rowcount > 0
