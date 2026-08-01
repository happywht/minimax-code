"""Codebase navigation tool — go-to-definition / find-references.

Uses the ``xai_codebase_graph`` scope-graph index built from the workspace.
This provides LSP-like navigation without requiring an LSP server.
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolResult


class NavigateCodebaseTool(Tool):
    """Navigate the indexed codebase: definitions and references by position."""

    name = "navigate_codebase"
    description = (
        "Navigate the indexed codebase using the scope graph. Supports "
        "go-to-definition and find-references either by symbol name or by "
        "file position. Use this when the user asks 'where is this defined' "
        "or 'where is this used' with a specific file location."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["definition", "references"],
                "description": "Whether to find definitions or references.",
            },
            "symbol": {
                "type": "string",
                "description": "Symbol name to look up (alternative to file_path + line + column).",
            },
            "file_path": {
                "type": "string",
                "description": "Relative file path for position-based navigation.",
            },
            "line": {
                "type": "integer",
                "description": "1-indexed line number for position-based navigation.",
            },
            "column": {
                "type": "integer",
                "description": "1-indexed column number for position-based navigation.",
            },
            "include_definition": {
                "type": "boolean",
                "default": False,
                "description": "For references-by-symbol, include the definition site(s).",
            },
        },
        "required": ["operation"],
        "additionalProperties": False,
    }

    def __init__(self, graph_index: Any) -> None:
        super().__init__()
        self._graph_index = graph_index

    async def run(self, **kwargs: Any) -> ToolResult:
        operation = kwargs.get("operation")
        if operation not in ("definition", "references"):
            return ToolResult.fail("'operation' must be 'definition' or 'references'")

        from ...codebase.graph_index import CodebaseGraphIndex

        if not isinstance(self._graph_index, CodebaseGraphIndex):
            return ToolResult.fail("codebase graph index is not available")

        symbol = kwargs.get("symbol")
        file_path = kwargs.get("file_path")
        line = kwargs.get("line")
        column = kwargs.get("column")

        try:
            if symbol is not None and isinstance(symbol, str) and symbol.strip():
                if operation == "definition":
                    result = await self._graph_index.find_definitions(symbol)
                else:
                    result = await self._graph_index.find_references(
                        symbol,
                        include_definition=bool(kwargs.get("include_definition", False)),
                    )
            elif (
                isinstance(file_path, str)
                and isinstance(line, int)
                and isinstance(column, int)
            ):
                if operation == "definition":
                    result = await self._graph_index.goto_definition(
                        file_path, line, column
                    )
                else:
                    return ToolResult.fail(
                        "position-based references are not yet supported; use 'symbol'"
                    )
            else:
                return ToolResult.fail(
                    "provide either 'symbol' or 'file_path' + 'line' + 'column'"
                )
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"codebase navigation failed: {exc}")

        return ToolResult.ok(output=result)


__all__ = ["NavigateCodebaseTool"]
