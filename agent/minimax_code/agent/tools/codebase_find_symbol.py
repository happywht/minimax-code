"""Find exact declarations in the existing indexed symbol metadata."""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolResult


def _format_source(file_path: str | None, line: Any) -> str | None:
    """Return a compact source reference for a symbol location."""
    if not file_path:
        return None
    if isinstance(line, int):
        return f"{file_path}#L{line}"
    return file_path


class FindSymbolCodebaseTool(Tool):
    """Find where a symbol is declared in the indexed codebase."""

    name = "find_symbol"
    description = (
        "Find exact symbol declarations in the indexed codebase, including nested "
        "symbols. Returns file paths, line numbers and kinds from the index "
        "snapshot, which may be stale. This does not find usage references; use "
        "search_files or navigate_codebase for usages."
    )
    parameters = {
        "type": "object",
        "properties": {
            "symbol": {
                "type": "string",
                "description": "Symbol name to look up (e.g. 'authenticate_user').",
            },
            "kind": {
                "type": "string",
                "description": "Optional symbol kind filter (e.g. 'function', 'class', 'variable').",
            },
            "file_pattern": {
                "type": "string",
                "description": "Optional SQL LIKE pattern to filter by file path (e.g. 'src/%').",
            },
        },
        "required": ["symbol"],
        "additionalProperties": False,
    }

    def __init__(self, retriever: Any) -> None:
        super().__init__()
        self._retriever = retriever

    async def run(self, **kwargs: Any) -> ToolResult:
        symbol = kwargs.get("symbol")
        if not isinstance(symbol, str) or not symbol.strip():
            return ToolResult.fail("'symbol' must be a non-empty string")

        from ...codebase import CodebaseRetriever

        if not isinstance(self._retriever, CodebaseRetriever):
            return ToolResult.fail("codebase retriever is not available")

        kind = kwargs.get("kind")
        file_pattern = kwargs.get("file_pattern")
        if kind is not None and not isinstance(kind, str):
            return ToolResult.fail("'kind' must be a string")
        if file_pattern is not None and not isinstance(file_pattern, str):
            return ToolResult.fail("'file_pattern' must be a string")

        max_results = 100
        try:
            rows = await self._retriever.find_symbols(
                symbol, kind=kind, file_pattern=file_pattern, limit=max_results + 1
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"symbol search failed: {exc}")

        definitions = [
            {
                "file_path": row["file_path"],
                "line": row["line"],
                "source": _format_source(row["file_path"], row["line"]),
                "kind": row["kind"],
            }
            for row in rows[:max_results]
        ]
        return ToolResult.ok(
            output={
                "symbol": symbol,
                "definitions": definitions,
                "references": [],
                "total": len(definitions),
                "truncated": len(rows) > max_results,
                "note": (
                    "Results are indexed declarations and may be stale. Usage references "
                    "are not extracted; use search_files or navigate_codebase for usages."
                ),
            },
            definition_count=len(definitions),
            reference_count=0,
        )


__all__ = ["FindSymbolCodebaseTool"]
