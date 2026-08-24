"""Persistent storage for codebase chunks + FTS5 + vector search.

v1.3.0 — every chunk carries a ``root`` dimension (the resolved
workspace root it was indexed from, ``""`` for the legacy process-wide
default). All read/write methods accept a ``root`` keyword so callers
stay inside their own shard; ``None`` on the aggregate helpers means
"no root filter" (whole-index views for status pages and exports).
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from ..storage.dao._base import dumps_json, loads_json, now_iso, row_to_dict

try:
    from sqlite_vec import serialize_float32

    _HAS_SQLITE_VEC = True
except Exception:  # pragma: no cover
    serialize_float32 = None  # type: ignore[assignment]
    _HAS_SQLITE_VEC = False

logger = logging.getLogger(__name__)


class CodebaseStore:
    """DAO-like store for ``codebase_chunks`` and its FTS5 index."""

    def __init__(self, db: Any) -> None:  # type: ignore[no-untyped-def]
        self._db = db

    async def save_chunk(
        self,
        *,
        file_path: str,
        start_line: int,
        end_line: int,
        content: str,
        metadata: dict[str, Any] | None = None,
        chunk_id: str | None = None,
        root: str = "",
    ) -> dict[str, Any]:
        """Insert or replace a single chunk and return the hydrated row."""
        cid = chunk_id or str(uuid.uuid4())
        now = now_iso()
        sql = (
            "INSERT OR REPLACE INTO codebase_chunks "
            "(id, file_path, start_line, end_line, content, metadata, "
            "created_at, updated_at, root) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        async with self._db.transaction() as conn:
            await conn.execute(
                sql,
                (cid, file_path, start_line, end_line, content,
                 dumps_json(metadata), now, now, root),
            )
        row = await self._db.fetchone(
            "SELECT rowid, * FROM codebase_chunks WHERE id = ?",
            (cid,),
        )
        return self._hydrate(row)

    async def delete_chunks_for_file(self, file_path: str, *, root: str = "") -> int:
        """Remove all chunks + embeddings belonging to ``file_path`` in *root*.

        Returns the number of chunks deleted.
        """
        async with self._db.transaction() as conn:
            rows = await conn.execute(
                "SELECT rowid FROM codebase_chunks WHERE file_path = ? AND root = ?",
                (file_path, root),
            )
            rowids = [r["rowid"] for r in await rows.fetchall()]
            for rid in rowids:
                await conn.execute(
                    "DELETE FROM codebase_chunks_vec WHERE rowid = ?",
                    (rid,),
                )
            cur = await conn.execute(
                "DELETE FROM codebase_chunks WHERE file_path = ? AND root = ?",
                (file_path, root),
            )
            return cur.rowcount

    async def clear(self, root: str | None = None) -> int:
        """Remove chunks, embeddings, and file metadata.

        ``root=None`` wipes the whole index (legacy behaviour, used by
        full exports); a string — including the default shard ``""`` —
        removes only that root's data, leaving other shards untouched.
        Returns the number of chunks deleted.
        """
        async with self._db.transaction() as conn:
            if root is None:
                await conn.execute("DELETE FROM codebase_chunks_vec")
                await conn.execute("DELETE FROM codebase_file_meta")
                cur = await conn.execute("DELETE FROM codebase_chunks")
                return cur.rowcount
            rows = await conn.execute(
                "SELECT rowid FROM codebase_chunks WHERE root = ?",
                (root,),
            )
            rowids = [r["rowid"] for r in await rows.fetchall()]
            for rid in rowids:
                await conn.execute(
                    "DELETE FROM codebase_chunks_vec WHERE rowid = ?",
                    (rid,),
                )
            await conn.execute(
                "DELETE FROM codebase_file_meta WHERE root = ?",
                (root,),
            )
            cur = await conn.execute(
                "DELETE FROM codebase_chunks WHERE root = ?",
                (root,),
            )
            return cur.rowcount

    async def search(
        self,
        query: str,
        *,
        file_pattern: str | None = None,
        limit: int = 20,
        offset: int = 0,
        root: str = "",
    ) -> list[dict[str, Any]]:
        """Keyword search over content and file paths using FTS5.

        Results are ranked by the FTS5 ``rank`` helper and joined back to
        the main table for full metadata, filtered to *root*'s shard.
        """
        if not query or not query.strip():
            return []
        params: list[Any] = []
        file_where = ""
        if file_pattern:
            file_where = "AND c.file_path LIKE ?"
            params.append(file_pattern)

        sql = (
            "SELECT c.rowid, c.*, rank "
            "FROM codebase_chunks_fts fts "
            "JOIN codebase_chunks c ON c.rowid = fts.rowid "
            f"WHERE codebase_chunks_fts MATCH ? AND c.root = ? {file_where} "
            "ORDER BY rank DESC "
            "LIMIT ? OFFSET ?"
        )
        params = [query, root, *params, limit, offset]
        rows = await self._db.fetchall(sql, tuple(params))
        return [self._hydrate(r) for r in rows]

    async def get_file_chunks(
        self, file_path: str, *, root: str = ""
    ) -> list[dict[str, Any]]:
        """Return all chunks for a single file in *root*, ordered by start line."""
        rows = await self._db.fetchall(
            "SELECT * FROM codebase_chunks WHERE file_path = ? AND root = ? "
            "ORDER BY start_line",
            (file_path, root),
        )
        return [self._hydrate(r) for r in rows]

    async def get_stats(self, *, root: str | None = None) -> dict[str, Any]:
        """Return aggregate indexing statistics.

        ``root=None`` reports across every shard; a string reports one.
        """
        where = "WHERE root = ?" if root is not None else ""
        params: tuple[Any, ...] = (root,) if root is not None else ()
        total_row = await self._db.fetchone(
            f"SELECT COUNT(*) AS total FROM codebase_chunks {where}",
            params,
        )
        files_row = await self._db.fetchone(
            f"SELECT COUNT(DISTINCT file_path) AS files FROM codebase_chunks {where}",
            params,
        )
        latest_row = await self._db.fetchone(
            f"SELECT MAX(updated_at) AS latest FROM codebase_chunks {where}",
            params,
        )
        return {
            "total_chunks": total_row["total"] if total_row else 0,
            "total_files": files_row["files"] if files_row else 0,
            "latest_updated_at": latest_row["latest"] if latest_row else None,
        }

    def _hydrate(self, row: Any) -> dict[str, Any] | None:  # type: ignore[no-untyped-def]
        d = row_to_dict(row)
        if d is None:
            return None
        d["metadata"] = loads_json(d.get("metadata"))
        return d

    # ------------------------------------------------------------------
    # Vector embeddings (sqlite-vec)
    # ------------------------------------------------------------------

    async def save_embedding(self, rowid: int, embedding: list[float]) -> None:
        """Upsert the embedding for a chunk rowid."""
        if not _HAS_SQLITE_VEC or serialize_float32 is None:
            return
        async with self._db.transaction() as conn:
            await conn.execute(
                "INSERT OR REPLACE INTO codebase_chunks_vec (rowid, embedding) VALUES (?, ?)",
                (rowid, serialize_float32(embedding)),
            )

    async def delete_embedding(self, rowid: int) -> None:
        """Remove the embedding for a single chunk rowid."""
        async with self._db.transaction() as conn:
            await conn.execute(
                "DELETE FROM codebase_chunks_vec WHERE rowid = ?",
                (rowid,),
            )

    async def search_vectors(
        self,
        query_embedding: list[float],
        *,
        limit: int = 20,
        root: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return the closest chunks by vector distance.

        Results include the chunk rowid and the raw distance value
        (smaller is better). When *root* is given, candidates whose
        rowid belongs to another shard are filtered out afterwards (the
        vec0 table has no root column; rowids are joined back to
        ``codebase_chunks``), so a post-filter may yield fewer than
        ``limit`` rows — callers use these for re-ranking, not as a
        standalone result set.
        """
        if not _HAS_SQLITE_VEC or serialize_float32 is None:
            return []
        sql = (
            "SELECT rowid, distance "
            "FROM codebase_chunks_vec "
            "WHERE embedding MATCH ? "
            "ORDER BY distance "
            "LIMIT ?"
        )
        rows = await self._db.fetchall(
            sql,
            (serialize_float32(query_embedding), limit),
        )
        results = [{"rowid": r["rowid"], "distance": r["distance"]} for r in rows]
        if root is None or not results:
            return results
        rowids = [r["rowid"] for r in results]
        placeholders = ",".join("?" * len(rowids))
        root_rows = await self._db.fetchall(
            f"SELECT rowid FROM codebase_chunks "
            f"WHERE rowid IN ({placeholders}) AND root = ?",
            (*rowids, root),
        )
        allowed = {r["rowid"] for r in root_rows}
        return [r for r in results if r["rowid"] in allowed]

    async def clear_embeddings(self) -> None:
        """Remove all stored embeddings."""
        async with self._db.transaction() as conn:
            await conn.execute("DELETE FROM codebase_chunks_vec")

    async def get_chunk(
        self, chunk_id: str, *, root: str | None = None
    ) -> dict[str, Any] | None:
        """Fetch a single chunk by id (optionally constrained to *root*)."""
        row = await self._db.fetchone(
            "SELECT rowid, * FROM codebase_chunks WHERE id = ?",
            (chunk_id,),
        )
        hydrated = self._hydrate(row)
        if hydrated is not None and root is not None and hydrated.get("root") != root:
            return None
        return hydrated

    async def get_chunk_by_rowid(self, rowid: int) -> dict[str, Any] | None:
        """Fetch a single chunk by SQLite rowid."""
        row = await self._db.fetchone(
            "SELECT rowid, * FROM codebase_chunks WHERE rowid = ?",
            (rowid,),
        )
        return self._hydrate(row)

    async def list_files(
        self, *, limit: int = 1000, root: str | None = None
    ) -> list[str]:
        """Return distinct indexed file paths, most recently updated first.

        ``root=None`` lists across every shard; a string lists one.
        """
        where = "WHERE root = ?" if root is not None else ""
        params: tuple[Any, ...] = (root,) if root is not None else ()
        rows = await self._db.fetchall(
            f"SELECT DISTINCT file_path FROM codebase_chunks {where} "
            "ORDER BY updated_at DESC LIMIT ?",
            (*params, limit),
        )
        return [r["file_path"] for r in rows]

    # ------------------------------------------------------------------
    # File-level metadata for incremental indexing
    # ------------------------------------------------------------------

    async def get_file_index_state(
        self, *, root: str = ""
    ) -> dict[str, dict[str, Any]]:
        """Return the last-known mtime/size for every indexed file in *root*."""
        rows = await self._db.fetchall(
            "SELECT file_path, mtime, size, indexed_at FROM codebase_file_meta "
            "WHERE root = ?",
            (root,),
        )
        return {
            r["file_path"]: {
                "mtime": r["mtime"],
                "size": r["size"],
                "indexed_at": r["indexed_at"],
            }
            for r in rows
        }

    async def save_file_index_state(
        self,
        file_path: str,
        *,
        mtime: float,
        size: int,
        root: str = "",
    ) -> None:
        """Upsert the index state for a single file within *root*."""
        now = now_iso()
        async with self._db.transaction() as conn:
            await conn.execute(
                (
                    "INSERT OR REPLACE INTO codebase_file_meta "
                    "(root, file_path, mtime, size, indexed_at) VALUES (?, ?, ?, ?, ?)"
                ),
                (root, file_path, mtime, size, now),
            )

    async def delete_file_index_state(self, file_path: str, *, root: str = "") -> int:
        """Remove the index state for a deleted file within *root*."""
        async with self._db.transaction() as conn:
            cur = await conn.execute(
                "DELETE FROM codebase_file_meta WHERE file_path = ? AND root = ?",
                (file_path, root),
            )
            return cur.rowcount

    async def clear_file_index_state(self, root: str | None = None) -> int:
        """Remove file-level index state (all roots, or one shard)."""
        async with self._db.transaction() as conn:
            if root is None:
                cur = await conn.execute("DELETE FROM codebase_file_meta")
            else:
                cur = await conn.execute(
                    "DELETE FROM codebase_file_meta WHERE root = ?",
                    (root,),
                )
            return cur.rowcount
