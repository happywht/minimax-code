"""Symbol lookup tool — finds definitions and references in the indexed codebase.

This tool searches the metadata extracted during indexing (symbols per chunk)
rather than doing an on-the-fly parse. It satisfies the v0.11.0 goal of letting
Agent answer "where is X defined / used" without requiring tree-sitter at
runtime. Future iterations can swap the backend for ``xai_codebase_graph``
scope-graph navigation once the tree-sitter grammar bindings are aligned.
"""

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
    """Find where a symbol is defined or referenced in the indexed codebase."""

    name = "find_symbol"
    description = (
        "Find definitions and references of a symbol in the indexed codebase. "
        "Use this when the user asks where a function, class, or variable is "
        "defined or used. Returns file paths, line numbers, and the surrounding "
        "symbol kind."
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

        try:
            search_result = await self._retriever.search(
                query=symbol,
                file_pattern=file_pattern if isinstance(file_pattern, str) else None,
                limit=50,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"symbol search failed: {exc}")

        definitions: list[dict[str, Any]] = []
        references: list[dict[str, Any]] = []
        seen_defs: set[tuple[str, str, int]] = set()
        seen_refs: set[tuple[str, str, int]] = set()

        for match in search_result.get("results", []):
            file_path = match.get("file_path")
            for sym in match.get("symbols", []):
                name = sym.get("name")
                sym_kind = sym.get("kind")
                line = sym.get("line")
                if name != symbol:
                    continue
                if kind is not None and sym_kind != kind:
                    continue
                key = (file_path, name, line)
                # Treat class/function/method/type definitions as definitions;
                # references are not currently extracted, so we fall back to
                # treating any matching symbol occurrence as a reference unless
                # it is a definition kind.
                if sym_kind in {"function", "class", "method", "type", "interface", "enum"}:
                    if key not in seen_defs:
                        seen_defs.add(key)
                        definitions.append(
                            {
                                "file_path": file_path,
                                "line": line,
                                "source": _format_source(file_path, line),
                                "kind": sym_kind,
                            }
                        )
                else:
                    if key not in seen_refs:
                        seen_refs.add(key)
                        references.append(
                            {
                                "file_path": file_path,
                                "line": line,
                                "source": _format_source(file_path, line),
                                "kind": sym_kind,
                            }
                        )

        return ToolResult.ok(
            output={
                "symbol": symbol,
                "definitions": definitions,
                "references": references,
                "total": len(definitions) + len(references),
            },
            definition_count=len(definitions),
            reference_count=len(references),
        )


__all__ = ["FindSymbolCodebaseTool"]
