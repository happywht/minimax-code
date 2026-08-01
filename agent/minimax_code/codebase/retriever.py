"""High-level retrieval API over the codebase index."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from .embedder import CodebaseEmbedder, get_default_embedder
from .indexer import CodebaseIndexer
from .store import CodebaseStore

logger = logging.getLogger(__name__)

_DEFAULT_SNIPPET_RADIUS = 120


@dataclass
class SearchResult:
    """One code search hit, ready to send to the frontend."""

    chunk_id: str
    file_path: str
    start_line: int
    end_line: int
    snippet: str
    language: str
    rank: float
    symbols: list[dict[str, Any]]


@dataclass
class SummaryResult:
    """Local summary for a file or directory prefix."""

    path: str
    kind: str  # "file" | "directory"
    language: str | None
    total_lines: int
    symbols: list[dict[str, Any]]
    snippet: str
    file_count: int


class CodebaseRetriever:
    """Wraps the store with snippet generation and hybrid search."""

    def __init__(
        self,
        store: CodebaseStore,
        indexer: CodebaseIndexer | None = None,
        embedder: CodebaseEmbedder | None = None,
        *,
        vector_weight: float = 0.3,
    ) -> None:
        self._store = store
        self._indexer = indexer
        self._embedder = embedder or get_default_embedder()
        self._vector_weight = max(0.0, min(1.0, vector_weight))

    async def search(
        self,
        query: str,
        *,
        file_pattern: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Hybrid search the codebase and return snippets.

        Combines FTS5 keyword ranking with sqlite-vec vector similarity.
        ``file_pattern`` is a SQL ``LIKE`` pattern (``%`` wildcards).
        """
        # 1. Keyword results from FTS5.
        fts_rows = await self._store.search(
            query=query,
            file_pattern=file_pattern,
            limit=limit * 2,
            offset=0,
        )

        # 2. Vector results (if the extension is available).
        vector_rows: list[dict[str, Any]] = []
        try:
            query_embedding = (await self._embedder.embed([query]))[0]
            vector_rows = await self._store.search_vectors(
                query_embedding,
                limit=limit * 2,
            )
        except Exception:  # noqa: BLE001
            logger.debug("vector search unavailable for query %r", query, exc_info=True)

        # 3. Build a rowid -> distance map for vector results.
        vec_distance_by_rowid: dict[int, float] = {}
        min_vec_distance: float | None = None
        for vr in vector_rows:
            distance = vr["distance"]
            vec_distance_by_rowid[vr["rowid"]] = distance
            if min_vec_distance is None or distance < min_vec_distance:
                min_vec_distance = distance

        # 4. Normalize and combine scores for FTS rows only.
        # We use vector similarity to re-rank keyword hits rather than
        # introducing pure vector hits, which keeps results interpretable
        # even with lightweight embedders.
        max_fts_rank = max((row.get("rank", 0.0) or 0.0 for row in fts_rows), default=0.0)
        max_vec_score = 0.0
        if min_vec_distance is not None:
            # Convert L2 distance on unit vectors to a [0, 1] similarity.
            max_vec_score = max(
                0.0,
                1.0 - min_vec_distance / 2.0,
            )

        scored: list[tuple[float, int, dict[str, Any]]] = []
        for row in fts_rows:
            rid = row["rowid"]
            fts_score = 0.0
            if max_fts_rank > 0:
                fts_score = (row.get("rank", 0.0) or 0.0) / max_fts_rank

            vec_score = 0.0
            distance = vec_distance_by_rowid.get(rid)
            if distance is not None:
                vec_score = max(0.0, 1.0 - distance / 2.0)
                if max_vec_score > 0:
                    vec_score = vec_score / max_vec_score

            hybrid_score = (
                (1 - self._vector_weight) * fts_score
                + self._vector_weight * vec_score
            )
            scored.append((hybrid_score, rid, row))

        scored.sort(key=lambda x: x[0], reverse=True)

        # 5. Build result objects, honouring offset/limit.
        results: list[SearchResult] = []
        for _, _, row in scored[offset : offset + limit]:
            metadata = row.get("metadata") or {}
            content = row.get("content") or ""
            snippet = _build_snippet(content, query)
            results.append(
                SearchResult(
                    chunk_id=row["id"],
                    file_path=row["file_path"],
                    start_line=row["start_line"],
                    end_line=row["end_line"],
                    snippet=snippet,
                    language=metadata.get("language"),
                    rank=row.get("rank", 0.0),
                    symbols=metadata.get("symbols") or [],
                )
            )
        return {
            "query": query,
            "file_pattern": file_pattern,
            "total": len(results),
            "results": [r.__dict__ for r in results],
        }

    async def summarize(self, path: str) -> dict[str, Any]:
        """Return a structured summary for a file or directory prefix."""
        chunks = await self._store.get_file_chunks(path)
        if chunks:
            return self._summarize_file(path, chunks[0])

        # Directory prefix: gather files whose path starts with the prefix.
        rows = await self._store.list_files(limit=1000)
        matching = [f for f in rows if f.startswith(path)]
        if not matching:
            return SummaryResult(
                path=path,
                kind="directory",
                language=None,
                total_lines=0,
                symbols=[],
                snippet="No indexed files found.",
                file_count=0,
            ).__dict__
        total_lines = 0
        symbols: list[dict[str, Any]] = []
        for file_path in matching[:20]:
            file_chunks = await self._store.get_file_chunks(file_path)
            if file_chunks:
                meta = file_chunks[0].get("metadata") or {}
                total_lines += meta.get("total_lines", 0)
                symbols.extend(meta.get("symbols", []))
        return SummaryResult(
            path=path,
            kind="directory",
            language=None,
            total_lines=total_lines,
            symbols=symbols[:50],
            snippet=f"Directory contains {len(matching)} indexed file(s).",
            file_count=len(matching),
        ).__dict__

    def _summarize_file(self, path: str, chunk: dict[str, Any]) -> dict[str, Any]:
        metadata = chunk.get("metadata") or {}
        content = chunk.get("content") or ""
        lines = content.splitlines()
        preview_lines = min(20, len(lines))
        snippet = "\n".join(lines[:preview_lines])
        return SummaryResult(
            path=path,
            kind="file",
            language=metadata.get("language"),
            total_lines=metadata.get("total_lines", 0),
            symbols=metadata.get("symbols") or [],
            snippet=snippet,
            file_count=1,
        ).__dict__


def _build_snippet(content: str, query: str, radius: int = _DEFAULT_SNIPPET_RADIUS) -> str:
    """Extract a short snippet around the first query term match."""
    if not content:
        return ""
    lowered = content.lower()
    terms = [t.lower() for t in re.split(r"\W+", query) if t.strip()]
    if not terms:
        return content[: radius * 2]
    pos = -1
    for term in terms:
        pos = lowered.find(term)
        if pos != -1:
            break
    if pos == -1:
        return content[: radius * 2]
    start = max(0, pos - radius)
    end = min(len(content), pos + radius)
    snippet = content[start:end]
    if start > 0:
        snippet = "…" + snippet
    if end < len(content):
        snippet = snippet + "…"
    return snippet
