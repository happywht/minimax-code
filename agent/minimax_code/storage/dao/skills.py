"""DAO — skill registry.

A *skill* is a packaged behavior the agent can opt into: a
``SKILL.md`` plus optional tool scripts. The loader scans the
``skills/`` directory on disk and we mirror the manifest here so
the UI can list them, the agent can ``enable`` / ``disable`` them
without re-reading the filesystem, and ``when_to_use`` is queryable.
"""

from __future__ import annotations

import logging
from typing import Any

from ._base import apply_pagination, now_iso, parse_order_by, row_to_dict

logger = logging.getLogger(__name__)


_SORTABLE: tuple[str, ...] = ("name", "created_at", "updated_at", "version")


# ---------------------------------------------------------------------------
# Async DAO
# ---------------------------------------------------------------------------


class SkillsDAO:
    """Async DAO for the ``skills`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def upsert(
        self,
        *,
        id: str,
        name: str,
        path: str,
        version: str = "0.0.0",
        description: str = "",
        when_to_use: str = "",
        enabled: bool = True,
    ) -> dict[str, Any]:
        """Insert a skill, or update existing fields by ``name``.

        We use ``name`` (not ``id``) as the natural key for "this is
        the same skill on disk" — two ``SKILL.md`` files with the
        same ``name`` would conflict anyway. ``id`` is the
        database-side primary key.
        """
        now = now_iso()
        sql_select = "SELECT id FROM skills WHERE name = ?"
        row = await self._db.fetchone(sql_select, (name,))
        if row is None:
            sql = (
                "INSERT INTO skills "
                "(id, name, version, path, description, when_to_use, enabled, "
                " created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
            )
            params = (
                id,
                name,
                version,
                path,
                description,
                when_to_use,
                1 if enabled else 0,
                now,
                now,
            )
            async with self._db.transaction() as conn:
                await conn.execute(sql, params)
        else:
            sql = (
                "UPDATE skills SET version = ?, path = ?, description = ?, "
                "when_to_use = ?, enabled = ?, updated_at = ? WHERE name = ?"
            )
            params = (
                version,
                path,
                description,
                when_to_use,
                1 if enabled else 0,
                now,
                name,
            )
            async with self._db.transaction() as conn:
                await conn.execute(sql, params)
        out = await self._db.fetchone("SELECT * FROM skills WHERE name = ?", (name,))
        return _hydrate(out)

    async def get(self, skill_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM skills WHERE id = ?", (skill_id,))
        return _hydrate(row)

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM skills WHERE name = ?", (name,))
        return _hydrate(row)

    async def enable(self, name: str) -> dict[str, Any] | None:
        return await self._set_enabled(name, True)

    async def disable(self, name: str) -> dict[str, Any] | None:
        return await self._set_enabled(name, False)

    async def _set_enabled(self, name: str, enabled: bool) -> dict[str, Any] | None:
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE skills SET enabled = ?, updated_at = ? WHERE name = ?",
                (1 if enabled else 0, now_iso(), name),
            )
        row = await self._db.fetchone("SELECT * FROM skills WHERE name = ?", (name,))
        return _hydrate(row)

    async def list(
        self,
        *,
        enabled: bool | None = None,
        search: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        where, params = _build_filters(enabled=enabled, search=search)
        sql = f"SELECT * FROM skills {where} ORDER BY {parse_order_by(order_by, _SORTABLE)}"
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    async def count(self, *, enabled: bool | None = None) -> int:
        where, params = _build_filters(enabled=enabled, search=None)
        row = await self._db.fetchone(
            f"SELECT COUNT(*) AS n FROM skills {where}", tuple(params)
        )
        return int(row["n"]) if row else 0

    async def delete(self, skill_id: str) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute("DELETE FROM skills WHERE id = ?", (skill_id,))
            return cur.rowcount > 0


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any] | None:
    d = row_to_dict(row)
    if d is None:
        return None
    d["enabled"] = bool(d.get("enabled", 0))
    return d


def _build_filters(
    *, enabled: bool | None, search: str | None
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if enabled is not None:
        clauses.append("enabled = ?")
        params.append(1 if enabled else 0)
    if search:
        clauses.append("(name LIKE ? COLLATE NOCASE OR description LIKE ? COLLATE NOCASE)")
        like = f"%{search}%"
        params.append(like)
        params.append(like)
    if clauses:
        return "WHERE " + " AND ".join(clauses), params
    return "", params


def upsert_sync(db, **fields) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    now = now_iso()
    row = db.fetchone("SELECT id FROM skills WHERE name = ?", (fields["name"],))
    if row is None:
        sql = (
            "INSERT INTO skills "
            "(id, name, version, path, description, when_to_use, enabled, "
            " created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            fields["id"],
            fields["name"],
            fields.get("version", "0.0.0"),
            fields["path"],
            fields.get("description", ""),
            fields.get("when_to_use", ""),
            1 if fields.get("enabled", True) else 0,
            now,
            now,
        )
    else:
        sql = (
            "UPDATE skills SET version = ?, path = ?, description = ?, "
            "when_to_use = ?, enabled = ?, updated_at = ? WHERE name = ?"
        )
        params = (
            fields.get("version", "0.0.0"),
            fields["path"],
            fields.get("description", ""),
            fields.get("when_to_use", ""),
            1 if fields.get("enabled", True) else 0,
            now,
            fields["name"],
        )
    with db.transaction() as conn:
        conn.execute(sql, params)
    out = db.fetchone("SELECT * FROM skills WHERE name = ?", (fields["name"],))
    return _hydrate(out)


def list_sync(
    db,  # type: ignore[no-untyped-def]
    *,
    enabled: bool | None = None,
    limit: int | None = None,
    offset: int | None = None,
) -> list[dict[str, Any]]:
    where, params = _build_filters(enabled=enabled, search=None)
    sql = f"SELECT * FROM skills {where} ORDER BY {parse_order_by(None, _SORTABLE)}"
    sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
    rows = db.fetchall(sql, tuple(params))
    return [_hydrate(r) for r in rows]


__all__ = [
    "SkillsDAO",
    "list_sync",
    "upsert_sync",
]
