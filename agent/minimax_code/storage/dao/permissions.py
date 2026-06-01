"""DAO — permission rules.

A *permission rule* maps a tool-name pattern (glob-ish; the auth
layer's matcher is in ``minimax_code.auth.permissions``) to one of
three actions: ``allow``, ``deny``, or ``ask`` (i.e. prompt the user
every time). Rules are stored in insertion order — the matcher
iterates top-down and the first hit wins.
"""

from __future__ import annotations

import logging
from typing import Any

from ._base import apply_pagination, now_iso, row_to_dict

logger = logging.getLogger(__name__)


_VALID_ACTIONS: frozenset[str] = frozenset({"allow", "deny", "ask"})


# ---------------------------------------------------------------------------
# Async DAO
# ---------------------------------------------------------------------------


class PermissionRulesDAO:
    """Async DAO for the ``permission_rules`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def create(
        self,
        *,
        id: str,
        tool_pattern: str,
        action: str,
        scope: str = "global",
        created_at: str | None = None,
    ) -> dict[str, Any]:
        if action not in _VALID_ACTIONS:
            raise ValueError(
                f"action must be one of {sorted(_VALID_ACTIONS)}, got {action!r}"
            )
        sql = (
            "INSERT INTO permission_rules "
            "(id, tool_pattern, action, scope, created_at) "
            "VALUES (?, ?, ?, ?, ?)"
        )
        params = (
            id,
            tool_pattern,
            action,
            scope,
            created_at or now_iso(),
        )
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        row = await self._db.fetchone(
            "SELECT * FROM permission_rules WHERE id = ?", (id,)
        )
        return _hydrate(row)

    async def get(self, rule_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM permission_rules WHERE id = ?", (rule_id,)
        )
        return _hydrate(row)

    async def list(
        self,
        *,
        action: str | None = None,
        tool_pattern_prefix: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> list[dict[str, Any]]:
        where, params = _filters(action=action, prefix=tool_pattern_prefix)
        sql = (
            f"SELECT * FROM permission_rules {where} "
            f"ORDER BY created_at ASC, id ASC"
        )
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    async def delete(self, rule_id: str) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM permission_rules WHERE id = ?", (rule_id,)
            )
            return cur.rowcount > 0

    async def delete_for_pattern(self, tool_pattern: str) -> int:
        """Bulk-delete every rule with a given tool pattern.

        Used when the user clears a saved authorization.
        """
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM permission_rules WHERE tool_pattern = ?", (tool_pattern,)
            )
            return cur.rowcount

    async def count(self, *, action: str | None = None) -> int:
        where, params = _filters(action=action, prefix=None)
        row = await self._db.fetchone(
            f"SELECT COUNT(*) AS n FROM permission_rules {where}", tuple(params)
        )
        return int(row["n"]) if row else 0


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any] | None:
    return row_to_dict(row)


def _filters(
    *, action: str | None, prefix: str | None
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if action is not None:
        if action not in _VALID_ACTIONS:
            raise ValueError(f"action must be one of {sorted(_VALID_ACTIONS)}")
        clauses.append("action = ?")
        params.append(action)
    if prefix is not None:
        clauses.append("tool_pattern LIKE ?")
        params.append(f"{prefix}%")
    if clauses:
        return "WHERE " + " AND ".join(clauses), params
    return "", params


def create_sync(db, **fields) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    if fields["action"] not in _VALID_ACTIONS:
        raise ValueError("invalid action")
    sql = (
        "INSERT INTO permission_rules "
        "(id, tool_pattern, action, scope, created_at) "
        "VALUES (?, ?, ?, ?, ?)"
    )
    params = (
        fields["id"],
        fields["tool_pattern"],
        fields["action"],
        fields.get("scope", "global"),
        fields.get("created_at") or now_iso(),
    )
    with db.transaction() as conn:
        conn.execute(sql, params)
    row = db.fetchone("SELECT * FROM permission_rules WHERE id = ?", (fields["id"],))
    return _hydrate(row)


__all__ = ["PermissionRulesDAO", "create_sync"]
