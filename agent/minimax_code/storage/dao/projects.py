"""DAO — project organization.

A *project* is a user-defined folder that groups sessions (tasks).
There is one reserved project, ``inbox`` (收件箱), which collects
sessions that are not explicitly assigned to another project.
"""

from __future__ import annotations

import logging
from typing import Any

from ._base import apply_pagination, now_iso, parse_order_by, row_to_dict

logger = logging.getLogger(__name__)

INBOX_ID = "inbox"
INBOX_NAME = "收件箱"
INBOX_DESCRIPTION = "未归类任务默认目录"

_SORTABLE: tuple[str, ...] = ("name", "created_at", "updated_at")


class ProjectsDAO:
    """Async DAO for the ``projects`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def ensure_inbox(self) -> dict[str, Any]:
        """Create the reserved inbox project if it does not exist."""
        existing = await self.get(INBOX_ID)
        if existing:
            return existing
        now = now_iso()
        sql = (
            "INSERT INTO projects (id, name, description, archived, created_at, updated_at) "
            "VALUES (?, ?, ?, 0, ?, ?)"
        )
        async with self._db.transaction() as conn:
            await conn.execute(sql, (INBOX_ID, INBOX_NAME, INBOX_DESCRIPTION, now, now))
        row = await self._db.fetchone("SELECT * FROM projects WHERE id = ?", (INBOX_ID,))
        return _hydrate(row)

    async def create(
        self,
        *,
        id: str,
        name: str,
        description: str = "",
        archived: bool = False,
        root_path: str = "",
    ) -> dict[str, Any]:
        """Insert a new project and return the persisted row."""
        now = now_iso()
        sql = (
            "INSERT INTO projects (id, name, description, archived, root_path, "
            "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        async with self._db.transaction() as conn:
            await conn.execute(
                sql,
                (id, name, description, 1 if archived else 0, root_path, now, now),
            )
        row = await self._db.fetchone("SELECT * FROM projects WHERE id = ?", (id,))
        return _hydrate(row)

    async def get(self, project_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        return _hydrate(row)

    async def update(
        self,
        project_id: str,
        *,
        name: str | None = None,
        description: str | None = None,
        root_path: str | None = None,
    ) -> dict[str, Any] | None:
        """Rename, re-describe, or re-root a project.

        ``root_path`` follows tri-state semantics: ``None`` leaves it
        untouched, ``""`` clears it back to the process-wide workspace,
        and a non-empty string binds the project to that directory.
        """
        if project_id == INBOX_ID:
            raise ValueError("cannot update the reserved inbox project")
        sets: list[str] = []
        params: list[Any] = []
        if name is not None:
            sets.append("name = ?")
            params.append(name)
        if description is not None:
            sets.append("description = ?")
            params.append(description)
        if root_path is not None:
            sets.append("root_path = ?")
            params.append(root_path)
        if not sets:
            return await self.get(project_id)
        sets.append("updated_at = ?")
        params.append(now_iso())
        params.append(project_id)
        sql = f"UPDATE projects SET {', '.join(sets)} WHERE id = ?"
        async with self._db.transaction() as conn:
            await conn.execute(sql, tuple(params))
        return await self.get(project_id)

    async def delete(self, project_id: str) -> bool:
        """Delete a project and move its sessions back to the inbox.

        Returns ``True`` if a row was removed. The inbox project itself
        cannot be deleted.
        """
        if project_id == INBOX_ID:
            return False
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE sessions SET project_id = ? WHERE project_id = ?",
                (INBOX_ID, project_id),
            )
            cur = await conn.execute("DELETE FROM projects WHERE id = ?", (project_id,))
            return cur.rowcount > 0

    async def set_archived(self, project_id: str, archived: bool) -> dict[str, Any] | None:
        """Archive or unarchive a project."""
        if project_id == INBOX_ID:
            raise ValueError("cannot archive the reserved inbox project")
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE projects SET archived = ?, updated_at = ? WHERE id = ?",
                (1 if archived else 0, now_iso(), project_id),
            )
        return await self.get(project_id)

    async def list(
        self,
        *,
        archived: bool | None = None,
        limit: int | None = None,
        offset: int | None = None,
        order_by: str | None = None,
    ) -> list[dict[str, Any]]:
        """List projects, optionally filtering by archived flag."""
        where: list[str] = []
        params: list[Any] = []
        if archived is not None:
            where.append("archived = ?")
            params.append(1 if archived else 0)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"SELECT * FROM projects {where_sql} ORDER BY {parse_order_by(order_by, _SORTABLE)}"
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any] | None:
    d = row_to_dict(row)
    if d is None:
        return None
    d["archived"] = bool(d.get("archived", 0))
    # Defensive: rows read before migration 026 (or hand-built dicts) lack the column.
    d.setdefault("root_path", "")
    return d


def create_sync(db, **fields) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Sync variant of :meth:`ProjectsDAO.create`."""
    now = now_iso()
    sql = (
        "INSERT INTO projects (id, name, description, archived, root_path, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)"
    )
    with db.transaction() as conn:
        conn.execute(
            sql,
            (
                fields["id"],
                fields["name"],
                fields.get("description", ""),
                1 if fields.get("archived") else 0,
                fields.get("root_path", ""),
                now,
                now,
            ),
        )
    row = db.fetchone("SELECT * FROM projects WHERE id = ?", (fields["id"],))
    return _hydrate(row)


def get_sync(db, project_id: str) -> dict[str, Any] | None:
    row = db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
    return _hydrate(row)


def list_sync(
    db,  # type: ignore[no-untyped-def]
    *,
    archived: bool | None = None,
    limit: int | None = None,
    offset: int | None = None,
    order_by: str | None = None,
) -> list[dict[str, Any]]:
    where = ""
    params: list[Any] = []
    if archived is not None:
        where = "WHERE archived = ?"
        params.append(1 if archived else 0)
    sql = f"SELECT * FROM projects {where} ORDER BY {parse_order_by(order_by, _SORTABLE)}"
    sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
    rows = db.fetchall(sql, tuple(params))
    return [_hydrate(r) for r in rows]


def ensure_inbox_sync(db) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    row = db.fetchone("SELECT * FROM projects WHERE id = ?", (INBOX_ID,))
    if row:
        return _hydrate(row)
    now = now_iso()
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO projects (id, name, description, archived, created_at, updated_at) "
            "VALUES (?, ?, ?, 0, ?, ?)",
            (INBOX_ID, INBOX_NAME, INBOX_DESCRIPTION, now, now),
        )
    row = db.fetchone("SELECT * FROM projects WHERE id = ?", (INBOX_ID,))
    return _hydrate(row)


__all__ = [
    "ProjectsDAO",
    "INBOX_ID",
    "INBOX_NAME",
    "create_sync",
    "get_sync",
    "list_sync",
    "ensure_inbox_sync",
]
