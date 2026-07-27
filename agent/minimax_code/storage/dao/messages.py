"""DAO — message storage.

A *message* is one chat turn in a session: either from the user, the
assistant, a system prompt, or a tool result. We keep the body as
plain text (the LLM is happy with that) and stash structured
``tool_calls`` as a JSON column for round-tripping OpenAI-style
function-calling payloads.

Parent / child linkage supports branching chat trees — the agent
core may fork a session and replay an earlier turn with a new user
message.
"""

from __future__ import annotations

import logging
from typing import Any

from ._base import (
    apply_pagination,
    dumps_json,
    loads_json,
    now_iso,
    parse_order_by,
    row_to_dict,
)

logger = logging.getLogger(__name__)


_SORTABLE: tuple[str, ...] = ("created_at", "id")
_VALID_ROLES: frozenset[str] = frozenset({"system", "user", "assistant", "tool"})


# ---------------------------------------------------------------------------
# Async DAO
# ---------------------------------------------------------------------------


class MessagesDAO:
    """Async DAO for the ``messages`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def create(
        self,
        *,
        id: str,
        session_id: str,
        role: str,
        content: str = "",
        tool_calls: list[dict[str, Any]] | None = None,
        tool_call_id: str | None = None,
        parent_id: str | None = None,
        created_at: str | None = None,
        tokens_in: int = 0,
        tokens_out: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if role not in _VALID_ROLES:
            raise ValueError(
                f"role must be one of {sorted(_VALID_ROLES)}, got {role!r}"
            )
        sql = (
            "INSERT INTO messages "
            "(id, session_id, role, content, tool_calls, tool_call_id, "
            " parent_id, created_at, tokens_in, tokens_out, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            id,
            session_id,
            role,
            content,
            dumps_json(tool_calls) if tool_calls is not None else None,
            tool_call_id,
            parent_id,
            created_at or now_iso(),
            tokens_in,
            tokens_out,
            dumps_json(metadata) if metadata is not None else None,
        )
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        row = await self._db.fetchone("SELECT * FROM messages WHERE id = ?", (id,))
        return _hydrate(row)

    async def get(self, message_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM messages WHERE id = ?", (message_id,)
        )
        return _hydrate(row)

    async def list_for_session(
        self,
        session_id: str,
        *,
        limit: int | None = None,
        offset: int | None = None,
        order_by: str | None = None,
        before: str | None = None,
        role: str | None = None,
    ) -> list[dict[str, Any]]:
        """List messages belonging to ``session_id``.

        ``before`` (an ISO timestamp) lets the UI page backwards
        through history cheaply. ``role`` is an exact-match filter
        ("show me only assistant turns").
        """
        where = ["session_id = ?"]
        params: list[Any] = [session_id]
        if before is not None:
            where.append("created_at < ?")
            params.append(before)
        if role is not None:
            where.append("role = ?")
            params.append(role)
        sql = f"SELECT * FROM messages WHERE {' AND '.join(where)} ORDER BY {parse_order_by(order_by, _SORTABLE, default='created_at ASC')}"
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    async def count_for_session(self, session_id: str) -> int:
        row = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM messages WHERE session_id = ?", (session_id,)
        )
        return int(row["n"]) if row else 0

    async def token_totals_for_session(self, session_id: str) -> dict[str, int]:
        """Return ``{tokens_in, tokens_out}`` summed across the session.

        Computed in SQL so we don't have to drag every row back to
        Python just to add integers. Used by the cost / budget UI.
        """
        row = await self._db.fetchone(
            "SELECT COALESCE(SUM(tokens_in), 0) AS tin, "
            "       COALESCE(SUM(tokens_out), 0) AS tout "
            "FROM messages WHERE session_id = ?",
            (session_id,),
        )
        return {
            "tokens_in": int(row["tin"]) if row else 0,
            "tokens_out": int(row["tout"]) if row else 0,
        }

    async def update(
        self,
        message_id: str,
        *,
        content: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Patch a message's content and/or metadata.

        Returns the updated row, or ``None`` if the id does not exist.
        Used by the frontend's inline message editor.
        """
        sets: list[str] = []
        params: list[Any] = []
        if content is not None:
            sets.append("content = ?")
            params.append(content)
        if metadata is not None:
            sets.append("metadata = ?")
            params.append(dumps_json(metadata))
        if not sets:
            return await self.get(message_id)
        params.append(message_id)
        sql = f"UPDATE messages SET {', '.join(sets)} WHERE id = ?"
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        return await self.get(message_id)

    async def delete(self, message_id: str) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute("DELETE FROM messages WHERE id = ?", (message_id,))
            return cur.rowcount > 0

    async def delete_for_session(self, session_id: str) -> int:
        """Bulk-delete every message in a session.

        Mostly used by tests; the agent usually relies on the
        ``ON DELETE CASCADE`` from the FK constraint when a session
        is hard-deleted.
        """
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM messages WHERE session_id = ?", (session_id,)
            )
            return cur.rowcount


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any] | None:
    d = row_to_dict(row)
    if d is None:
        return None
    d["tool_calls"] = loads_json(d.get("tool_calls"))
    d["metadata"] = loads_json(d.get("metadata"))
    return d


def create_sync(db, **fields) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    if fields["role"] not in _VALID_ROLES:
        raise ValueError(f"role must be one of {sorted(_VALID_ROLES)}")
    sql = (
        "INSERT INTO messages "
        "(id, session_id, role, content, tool_calls, tool_call_id, "
        " parent_id, created_at, tokens_in, tokens_out, metadata) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    md = fields.get("metadata")
    params = (
        fields["id"],
        fields["session_id"],
        fields["role"],
        fields.get("content", ""),
        dumps_json(fields["tool_calls"]) if fields.get("tool_calls") is not None else None,
        fields.get("tool_call_id"),
        fields.get("parent_id"),
        fields.get("created_at") or now_iso(),
        fields.get("tokens_in", 0),
        fields.get("tokens_out", 0),
        dumps_json(md) if md is not None else None,
    )
    with db.transaction() as conn:
        conn.execute(sql, params)
    row = db.fetchone("SELECT * FROM messages WHERE id = ?", (fields["id"],))
    return _hydrate(row)


def list_for_session_sync(
    db, session_id: str, *, limit: int | None = None, offset: int | None = None
) -> list[dict[str, Any]]:  # type: ignore[no-untyped-def]
    sql = f"SELECT * FROM messages WHERE session_id = ? ORDER BY {parse_order_by(None, _SORTABLE, default='created_at ASC')}"
    sql, params = apply_pagination(sql, [session_id], limit=limit, offset=offset)
    rows = db.fetchall(sql, tuple(params))
    return [_hydrate(r) for r in rows]


__all__ = [
    "MessagesDAO",
    "create_sync",
    "list_for_session_sync",
]
