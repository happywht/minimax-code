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

    # ---- write -----------------------------------------------------------

    async def upsert(
        self,
        name: str,
        system_prompt: str = "",
        tool_allowlist: list[str] | None = None,
        model: str | None = None,
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
            sql = (
                "INSERT INTO agents "
                "(id, name, system_prompt, tool_allowlist, model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)"
            )
            params = (
                agent_id,
                name,
                system_prompt,
                dumps_json(tool_allowlist) if tool_allowlist is not None else None,
                model,
                now_iso(),
            )
            async with self._db.transaction() as conn:
                await conn.execute(sql, params)
        else:
            sets: list[str] = []
            params: list[Any] = []
            # Always overwrite system_prompt on upsert (it's a
            # field the caller is always expected to send, even
            # if it's the empty string).
            sets.append("system_prompt = ?")
            params.append(system_prompt)
            if tool_allowlist is not None:
                sets.append("tool_allowlist = ?")
                params.append(dumps_json(tool_allowlist))
            if model is not None:
                sets.append("model = ?")
                params.append(model)
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
