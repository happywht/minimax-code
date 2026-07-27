"""High-level retrieval API over the codebase index."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

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
    """Wraps the store with snippet generation and summarisation helpers."""

    def __init__(self, store: CodebaseStore, indexer: CodebaseIndexer | None = None) -> None:
        self._store = store
        self._indexer = indexer

    async def search(
        self,
        query: str,
        *,
        file_pattern: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> dict[str, Any]:
        """Search the codebase and return snippets.

        ``file_pattern`` is a SQL ``LIKE`` pattern (``%`` wildcards).
        """
        rows = await self._store.search(
            query=query,
            file_pattern=file_pattern,
            limit=limit,
            offset=offset,
        )
        results: list[SearchResult] = []
        for row in rows:
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
