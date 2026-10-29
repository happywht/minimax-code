"""DAO — session_checkpoints (R310, workspace snapshot layer).

CRUD for the ``session_checkpoints`` table. Each row is one workspace
checkpoint: a git-stash ref for tracked changes plus the metadata
needed to rewind (branch, file lists, an untracked-snapshot flag).

The on-disk untracked-file copy lives outside the database (under
``<data_dir>/checkpoints/<id>/``; see
:class:`minimax_code.workspace.checkpoint.CheckpointManager`) — this DAO
owns only the row, mirroring how :class:`NotificationDAO` owns the
notification row while delivery state lives elsewhere.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any

from ._base import (
    now_iso,
    row_to_dict,
)

logger = logging.getLogger(__name__)


class CheckpointDAO:
    """Async DAO for the ``session_checkpoints`` table."""

    def __init__(self, db) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    async def create(
        self,
        *,
        session_id: str,
        label: str,
        message: str = "",
        git_stash_ref: str | None = None,
        branch: str | None = None,
        tracked_files: list[str] | None = None,
        untracked_files: list[str] | None = None,
        has_untracked_snapshot: bool = False,
        checkpoint_id: str | None = None,
        created_at: str | None = None,
        source_root: str | None = None,
    ) -> dict[str, Any]:
        row_id = checkpoint_id or f"ckpt_{uuid.uuid4().hex[:12]}"
        timestamp = created_at or now_iso()
        sql = (
            "INSERT INTO session_checkpoints "
            "(id, session_id, label, message, git_stash_ref, branch, "
            " tracked_files, untracked_files, has_untracked_snapshot, created_at, source_root) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        params = (
            row_id,
            session_id,
            label,
            message,
            git_stash_ref,
            branch,
            json.dumps(tracked_files or []),
            json.dumps(untracked_files or []),
            1 if has_untracked_snapshot else 0,
            timestamp,
            source_root,
        )
        async with self._db.transaction() as conn:
            await conn.execute(sql, params)
        row = await self._db.fetchone(
            "SELECT * FROM session_checkpoints WHERE id = ?", (row_id,)
        )
        return _hydrate(row)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    async def get(self, checkpoint_id: str) -> dict[str, Any] | None:
        row = await self._db.fetchone(
            "SELECT * FROM session_checkpoints WHERE id = ?", (checkpoint_id,)
        )
        return _hydrate(row) if row else None

    async def rebind_source(self, checkpoint_id: str, *, session_id: str,
                            previous_source: str | None, git_stash_ref: str,
                            source_root: str) -> bool:
        """Update only a still-matching checkpoint after its destination was verified."""
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                "UPDATE session_checkpoints SET source_root=? WHERE id=? AND session_id=? "
                "AND source_root IS ? AND git_stash_ref=?",
                (source_root, checkpoint_id, session_id, previous_source, git_stash_ref),
            )
            return cursor.rowcount == 1

    async def list_by_session(
        self,
        *,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        sql = (
            "SELECT * FROM session_checkpoints WHERE session_id = ? "
            "ORDER BY created_at DESC LIMIT ? OFFSET ?"
        )
        rows = await self._db.fetchall(sql, (session_id, limit, offset))
        return [_hydrate(r) for r in rows]

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete(self, checkpoint_id: str) -> bool:
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM session_checkpoints WHERE id = ?", (checkpoint_id,)
            )
            return cur.rowcount > 0

    async def delete_by_session(self, session_id: str) -> int:
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM session_checkpoints WHERE session_id = ?", (session_id,)
            )
            return cur.rowcount


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _hydrate(row: Any) -> dict[str, Any]:
    d = row_to_dict(row)
    if d is None:
        return {"id": "unknown"}
    # JSON columns -> lists; tolerate NULL / malformed payloads by
    # falling back to an empty list rather than crashing the handler.
    for key in ("tracked_files", "untracked_files"):
        raw = d.get(key)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                d[key] = parsed if isinstance(parsed, list) else []
            except (ValueError, TypeError):
                d[key] = []
        elif raw is None:
            d[key] = []
        # already a list (e.g. injected in tests) — leave as-is
    if "has_untracked_snapshot" in d:
        d["has_untracked_snapshot"] = bool(d["has_untracked_snapshot"])
    return d


__all__ = ["CheckpointDAO"]
