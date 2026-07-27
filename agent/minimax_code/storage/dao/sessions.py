"""DAO — session storage.

A *session* is a single conversation thread with the agent. It owns
its title, the model in use, an optional system prompt, and the
archive flag (set when the user tucks the session away in the
sidebar). Message bodies live in :mod:`messages`; we only keep
metadata here.

Usage
-----

    from minimax_code.storage.db import AsyncDatabase, Database
    from minimax_code.storage.dao.sessions import SessionsDAO

    db = AsyncDatabase("/tmp/data.db")
    await db.connect()
    await db.migrate()

    dao = SessionsDAO(db)
    sid = await dao.create(title="hello", model="gpt-4o")
    sess = await dao.get(sid)
    sessions = await dao.list(limit=20, archived=False)

For the synchronous variant (used by the background scheduler and
the test-suite fixture), use :func:`create_sync` /
:func:`get_sync` / :func:`list_sync` / etc. on a :class:`Database`
instance.
"""

from __future__ import annotations

import logging
from typing import Any

from ._base import (
    apply_pagination,
    loads_json,
    now_iso,
    parse_order_by,
    row_to_dict,
)

logger = logging.getLogger(__name__)


# Columns safe to sort by. Anything outside this list is rejected.
_SORTABLE: tuple[str, ...] = ("created_at", "updated_at", "title", "id")


# ---------------------------------------------------------------------------
# Async DAO
# ---------------------------------------------------------------------------


class SessionsDAO:
    """Async DAO for the ``sessions`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def create(
        self,
        *,
        id: str,
        title: str = "",
        created_at: str | None = None,
        updated_at: str | None = None,
        archived: bool = False,
        model: str | None = None,
        system_prompt: str | None = None,
        workspace_mode: str = "local",
        workspace_path: str | None = None,
        worktree_branch: str | None = None,
        base_branch: str | None = None,
        project_id: str | None = None,
    ) -> dict[str, Any]:
        """Insert a new session row; returns the persisted dict.

        ``created_at`` / ``updated_at`` default to "now" if not
        supplied — the agent rarely wants to back-date them.
        """
        now = now_iso()
        sql = (
            "INSERT INTO sessions "
            "(id, title, created_at, updated_at, archived, model, system_prompt, "
            "workspace_mode, workspace_path, worktree_branch, base_branch, project_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            id,
            title,
            created_at or now,
            updated_at or now,
            1 if archived else 0,
            model,
            system_prompt,
            workspace_mode,
            workspace_path,
            worktree_branch,
            base_branch,
            project_id or "inbox",
        )
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        row = await self._db.fetchone(
            "SELECT * FROM sessions WHERE id = ?", (id,)
        )
        return _hydrate(row)

    async def get(self, session_id: str) -> dict[str, Any] | None:
        """Fetch a single session by primary key, or ``None``."""
        row = await self._db.fetchone(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        return _hydrate(row)

    async def update(
        self,
        session_id: str,
        *,
        title: str | None = None,
        archived: bool | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        workspace_mode: str | None = None,
        workspace_path: str | None = None,
        worktree_branch: str | None = None,
        base_branch: str | None = None,
        project_id: str | None = None,
        touch_updated: bool = True,
    ) -> dict[str, Any] | None:
        """Patch one or more fields; returns the updated row.

        Setting ``archived=True`` is the canonical "archive" action.
        Pass ``touch_updated=False`` to update without bumping
        ``updated_at`` (rare; useful in tests).
        """
        sets: list[str] = []
        params: list[Any] = []
        if title is not None:
            sets.append("title = ?")
            params.append(title)
        if archived is not None:
            sets.append("archived = ?")
            params.append(1 if archived else 0)
        if model is not None:
            sets.append("model = ?")
            params.append(model)
        if system_prompt is not None:
            sets.append("system_prompt = ?")
            params.append(system_prompt)
        if workspace_mode is not None:
            sets.append("workspace_mode = ?")
            params.append(workspace_mode)
        if workspace_path is not None:
            sets.append("workspace_path = ?")
            params.append(workspace_path)
        if worktree_branch is not None:
            sets.append("worktree_branch = ?")
            params.append(worktree_branch)
        if base_branch is not None:
            sets.append("base_branch = ?")
            params.append(base_branch)
        if project_id is not None:
            sets.append("project_id = ?")
            params.append(project_id)
        if touch_updated:
            sets.append("updated_at = ?")
            params.append(now_iso())
        if not sets:
            return await self.get(session_id)
        params.append(session_id)
        sql = f"UPDATE sessions SET {', '.join(sets)} WHERE id = ?"
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        return await self.get(session_id)

    async def delete(self, session_id: str) -> bool:
        """Hard-delete a session. Returns True if a row was removed.

        Cascades to ``messages`` and ``tasks`` via FK ON DELETE CASCADE.
        """
        async with self._db.transaction() as conn:
            cur = await conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            return cur.rowcount > 0

    async def archive(self, session_id: str) -> dict[str, Any] | None:
        return await self.update(session_id, archived=True)

    async def unarchive(self, session_id: str) -> dict[str, Any] | None:
        return await self.update(session_id, archived=False)

    async def set_archived(
        self, session_id: str, archived: bool
    ) -> dict[str, Any] | None:
        """Flip the ``archived`` flag explicitly.

        This is a thin convenience wrapper around
        :meth:`update` that returns the row so the IPC layer can
        echo the new state in its reply. Returns ``None`` if the
        session id does not exist.
        """
        return await self.update(session_id, archived=bool(archived))

    async def get_messages(
        self,
        session_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` recent messages for a session.

        Performs a single JOIN-style query against the
        ``messages`` table — we use the FK on ``session_id`` and
        order by ``created_at DESC`` so the caller (the chat UI)
        gets the tail of the conversation without an extra
        round-trip. ``tool_calls`` is decoded back to a Python
        object so the JSON-RPC reply stays valid JSON.

        The list is returned newest-first; the UI can reverse it
        for display.

        ``limit`` is clamped to ``[1, 1000]`` — calling with 0
        would return an empty list (not what callers want) and
        a 100k cap protects against accidental unbounded reads.
        """
        effective_limit = max(1, min(int(limit or 1), 1000))
        # JOIN is implicit (messages.session_id = ?); we keep the
        # WHERE shape so the index ``idx_messages_session_created``
        # is still hit. The right-side ``created_at DESC`` gives
        # us the most recent ``limit`` messages in one pass.
        sql = (
            "SELECT * FROM messages "
            "WHERE session_id = ? "
            "ORDER BY created_at DESC, id DESC "
            "LIMIT ?"
        )
        rows = await self._db.fetchall(sql, (session_id, effective_limit))
        out: list[dict[str, Any]] = []
        for r in rows:
            d = row_to_dict(r)
            if d is None:
                continue
            d["tool_calls"] = loads_json(d.get("tool_calls"))
            out.append(d)
        return out

    async def touch(self, session_id: str) -> dict[str, Any] | None:
        """Bump ``updated_at`` (called on every new message)."""
        return await self.update(session_id, touch_updated=True)

    async def list(
        self,
        *,
        archived: bool | None = None,
        project_id: str | None = None,
        limit: int | None = None,
        offset: int | None = None,
        order_by: str | None = None,
        search: str | None = None,
    ) -> list[dict[str, Any]]:
        """List sessions with optional filters + pagination.

        ``search`` is a case-insensitive substring match on
        ``title``; it's deliberately simple to keep the index
        useful. Empty string is treated as "no filter".
        """
        where: list[str] = []
        params: list[Any] = []
        if archived is not None:
            where.append("archived = ?")
            params.append(1 if archived else 0)
        if project_id is not None:
            where.append("project_id = ?")
            params.append(project_id)
        if search:
            where.append("title LIKE ? COLLATE NOCASE")
            params.append(f"%{search}%")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        sql = f"SELECT * FROM sessions {where_sql} ORDER BY {parse_order_by(order_by, _SORTABLE)}"
        sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
        rows = await self._db.fetchall(sql, tuple(params))
        return [_hydrate(r) for r in rows]

    async def count(
        self,
        *,
        archived: bool | None = None,
        project_id: str | None = None,
        search: str | None = None,
    ) -> int:
        """Return the row count matching the same filters as :meth:`list`.

        ``search`` is a case-insensitive substring match on
        ``title`` — same semantics as :meth:`list`. Pass the same
        arguments to :meth:`count` that you pass to :meth:`list`
        to get a meaningful "total" for the ``session.list`` IPC
        reply. Without this, the handler's ``total`` would only
        reflect the archive filter, not the search filter — a
        subtle but real bug for the sidebar's "search box" UI.
        """
        where: list[str] = []
        params: list[Any] = []
        if archived is not None:
            where.append("archived = ?")
            params.append(1 if archived else 0)
        if project_id is not None:
            where.append("project_id = ?")
            params.append(project_id)
        if search:
            where.append("title LIKE ? COLLATE NOCASE")
            params.append(f"%{search}%")
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        row = await self._db.fetchone(
            f"SELECT COUNT(*) AS n FROM sessions {where_sql}", tuple(params)
        )
        return int(row["n"]) if row else 0

    async def stats(self) -> dict[str, int]:
        """Return aggregate counts for the session/message dashboard.

        Runs three cheap ``COUNT(*)`` queries so the frontend can
        show a single-number summary without dragging rows across
        the wire.
        """
        total_row = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM sessions"
        )
        archived_row = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM sessions WHERE archived = 1"
        )
        messages_row = await self._db.fetchone(
            "SELECT COUNT(*) AS n FROM messages"
        )
        return {
            "total_sessions": int(total_row["n"]) if total_row else 0,
            "archived_sessions": int(archived_row["n"]) if archived_row else 0,
            "total_messages": int(messages_row["n"]) if messages_row else 0,
        }


# ---------------------------------------------------------------------------
# Sync helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any] | None:
    d = row_to_dict(row)
    if d is None:
        return None
    d["archived"] = bool(d.get("archived", 0))
    d["workspace_mode"] = d.get("workspace_mode") or "local"
    return d


def create_sync(db, **fields) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    """Sync variant of :meth:`SessionsDAO.create`."""
    now = now_iso()
    sql = (
        "INSERT INTO sessions "
        "(id, title, created_at, updated_at, archived, model, system_prompt, "
        "workspace_mode, workspace_path, worktree_branch, base_branch, project_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    params = (
        fields["id"],
        fields.get("title", ""),
        fields.get("created_at") or now,
        fields.get("updated_at") or now,
        1 if fields.get("archived") else 0,
        fields.get("model"),
        fields.get("system_prompt"),
        fields.get("workspace_mode", "local"),
        fields.get("workspace_path"),
        fields.get("worktree_branch"),
        fields.get("base_branch"),
        fields.get("project_id") or "inbox",
    )
    with db.transaction() as conn:
        conn.execute(sql, params)
    row = db.fetchone("SELECT * FROM sessions WHERE id = ?", (fields["id"],))
    return _hydrate(row)


def get_sync(db, session_id: str) -> dict[str, Any] | None:  # type: ignore[no-untyped-def]
    row = db.fetchone("SELECT * FROM sessions WHERE id = ?", (session_id,))
    return _hydrate(row)


def list_sync(
    db,  # type: ignore[no-untyped-def]
    *,
    archived: bool | None = None,
    project_id: str | None = None,
    limit: int | None = None,
    offset: int | None = None,
    order_by: str | None = None,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if archived is not None:
        clauses.append("archived = ?")
        params.append(1 if archived else 0)
    if project_id is not None:
        clauses.append("project_id = ?")
        params.append(project_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM sessions {where} ORDER BY {parse_order_by(order_by, _SORTABLE)}"
    sql, params = apply_pagination(sql, params, limit=limit, offset=offset)
    rows = db.fetchall(sql, tuple(params))
    return [_hydrate(r) for r in rows]


def delete_sync(db, session_id: str) -> bool:  # type: ignore[no-untyped-def]
    with db.transaction() as conn:
        cur = conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
        return cur.rowcount > 0


__all__ = [
    "SessionsDAO",
    "create_sync",
    "delete_sync",
    "get_sync",
    "list_sync",
]
