"""Codebase RAG tool — lets the agent search the indexed workspace.

This tool bridges the v0.11.0 Milestone 2 ``codebase.*`` index with the
agent loop. It is intentionally *not* auto-registered via ``@register_tool``
because it needs a live :class:`~minimax_code.codebase.CodebaseRetriever`
instance; the IPC layer injects it into a per-core registry clone.
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolResult

_MAX_RESULTS = 20


def _format_source(
    file_path: str | None,
    start_line: Any | None,
    end_line: Any | None,
) -> str | None:
    """Return a compact source reference for a code snippet."""
    if not file_path:
        return None
    if isinstance(start_line, int) and isinstance(end_line, int):
        return f"{file_path}#L{start_line}-{end_line}"
    return file_path

_HARD_LIMIT = 50


class SearchCodebaseTool(Tool):
    """Search the indexed codebase for semantically relevant code snippets."""

    name = "search_codebase"
    description = (
        "Search the indexed codebase for code snippets, definitions, or usage "
        "examples matching a natural-language or keyword query. Use this tool "
        "when the user asks about project structure, where a function/class is "
        "defined, how a module works, or what patterns exist in the code. "
        "Returns file paths, line ranges, ranked snippets, and extracted symbols."
    )
    parameters = {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": "Natural-language or keyword query to search for.",
            },
            "file_pattern": {
                "type": "string",
                "description": "Optional SQL LIKE pattern to filter by file path (e.g. 'src/%').",
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": _HARD_LIMIT,
                "default": _MAX_RESULTS,
                "description": f"Maximum number of snippets to return (1..{_HARD_LIMIT}).",
            },
        },
        "required": ["query"],
        "additionalProperties": False,
    }

    def __init__(self, retriever: Any) -> None:
        super().__init__()
        self._retriever = retriever

    async def run(self, **kwargs: Any) -> ToolResult:
        query = kwargs.get("query")
        if not isinstance(query, str) or not query.strip():
            return ToolResult.fail("'query' must be a non-empty string")

        file_pattern = kwargs.get("file_pattern")
        if file_pattern is not None and not isinstance(file_pattern, str):
            return ToolResult.fail("'file_pattern' must be a string")

        limit = int(kwargs.get("limit") or _MAX_RESULTS)
        limit = max(1, min(limit, _HARD_LIMIT))

        from ...codebase import CodebaseRetriever

        if not isinstance(self._retriever, CodebaseRetriever):
            return ToolResult.fail("codebase retriever is not available")

        try:
            result = await self._retriever.search(
                query=query,
                file_pattern=file_pattern or None,
                limit=limit,
                offset=0,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"codebase search failed: {exc}")

        results = result.get("results") or []
        if not results:
            return ToolResult.ok(
                output={
                    "query": query,
                    "match_count": 0,
                    "matches": [],
                    "note": "No indexed matches found. Try a different query or run codebase.build_index.",
                },
                match_count=0,
            )

        # Trim payload to keep the LLM context small.
        matches: list[dict[str, Any]] = []
        for r in results:
            file_path = r.get("file_path")
            matches.append(
                {
                    "file_path": file_path,
                    "start_line": r.get("start_line"),
                    "end_line": r.get("end_line"),
                    "source": _format_source(
                        file_path, r.get("start_line"), r.get("end_line")
                    ),
                    "snippet": r.get("snippet"),
                    "symbols": r.get("symbols") or [],
                    "rank": r.get("rank"),
                }
            )

        return ToolResult.ok(
            output={
                "query": query,
                "match_count": len(matches),
                "matches": matches,
            },
            match_count=len(matches),
        )


__all__ = ["SearchCodebaseTool"]
