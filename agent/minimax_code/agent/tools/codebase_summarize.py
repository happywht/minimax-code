"""Codebase summarisation tool — lets the agent summarise indexed files/dirs.

Like :class:`~minimax_code.agent.tools.codebase_search.SearchCodebaseTool`,
this tool is not auto-registered because it needs a live retriever instance.
"""

from __future__ import annotations

from typing import Any

from .base import Tool, ToolResult


def _format_source(path: str | None, kind: str | None, total_lines: Any) -> str | None:
    """Return a compact source reference for a file or directory summary."""
    if not path:
        return None
    if kind == "file" and isinstance(total_lines, int) and total_lines > 0:
        return f"{path}#L1-{total_lines}"
    return path


class SummarizeCodebaseTool(Tool):
    """Return a structured summary of an indexed file or directory prefix."""

    name = "summarize_codebase"
    description = (
        "Summarise an indexed file or directory from the codebase. Use this "
        "when the user asks what a module does, what symbols it contains, or "
        "for a high-level overview of a path. Returns language, total lines, "
        "extracted symbols, and a short preview snippet."
    )
    parameters = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "description": "Relative file or directory path to summarise (e.g. 'src/auth.py' or 'src/auth').",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
    }

    def __init__(self, retriever: Any) -> None:
        super().__init__()
        self._retriever = retriever

    async def run(self, **kwargs: Any) -> ToolResult:
        path = kwargs.get("path")
        if not isinstance(path, str) or not path:
            return ToolResult.fail("'path' must be a non-empty string")

        from ...codebase import CodebaseRetriever

        if not isinstance(self._retriever, CodebaseRetriever):
            return ToolResult.fail("codebase retriever is not available")

        try:
            result = await self._retriever.summarize(path)
        except Exception as exc:  # noqa: BLE001
            return ToolResult.fail(f"codebase summarise failed: {exc}")

        path = result.get("path")
        kind = result.get("kind")
        return ToolResult.ok(
            output={
                "path": path,
                "kind": kind,
                "language": result.get("language"),
                "total_lines": result.get("total_lines"),
                "symbols": result.get("symbols") or [],
                "snippet": result.get("snippet"),
                "file_count": result.get("file_count"),
                "source": _format_source(path, kind, result.get("total_lines")),
            }
        )


__all__ = ["SummarizeCodebaseTool"]
