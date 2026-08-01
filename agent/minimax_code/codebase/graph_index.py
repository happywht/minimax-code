"""Scope-graph index wrapper over ``xai_codebase_graph``.

This module bridges the v0.11.0 lightweight codebase RAG index with the richer
``xai_codebase_graph`` scope graph. It is built lazily from the same workspace
and provides go-to-definition / go-to-references / symbol-at-position queries.

The graph index is kept in memory and rebuilt on demand. It is fail-open: if
tree-sitter is unavailable or a file cannot be parsed, the operation returns an
empty result rather than crashing the agent.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from ..xai_codebase_graph import Navigator
from ..xai_codebase_graph.languages import LanguageRegistry
from ..xai_codebase_graph.languages.registry import default_language_registry
from ..xai_codebase_graph.manager.builder import IndexBuilder
from ..xai_codebase_graph.navigation import NavigationError

logger = logging.getLogger(__name__)


class CodebaseGraphIndex:
    """In-memory scope-graph index for a workspace.

    Wraps a :class:`Navigator` and rebuilds it lazily when the workspace changes.
    All methods are async-friendly: the synchronous tree-sitter build runs in a
    worker thread via :func:`asyncio.to_thread`.
    """

    def __init__(
        self,
        workspace: Path | str,
        *,
        registry: LanguageRegistry | None = None,
    ) -> None:
        self._workspace = Path(workspace).expanduser().resolve()
        self._registry = registry or default_language_registry()
        self._navigator: Navigator | None = None

    async def _ensure_built(self) -> Navigator | None:
        if self._navigator is not None:
            return self._navigator
        try:
            index = await self._build_index()
        except Exception:  # noqa: BLE001
            logger.exception("codebase graph index build failed")
            return None
        if index is None:
            return None
        self._navigator = Navigator(index, self._registry)
        return self._navigator

    async def _build_index(self) -> Any:
        import asyncio

        builder = IndexBuilder.with_registry(self._registry)
        return await asyncio.to_thread(builder.build, self._workspace)

    async def find_definitions(self, symbol: str) -> dict[str, Any]:
        """Return definition locations for ``symbol``.

        Returns a dict with ``symbol`` and ``locations`` (list of
        ``{file_path, line}``). Never raises.
        """
        navigator = await self._ensure_built()
        if navigator is None:
            return {"symbol": symbol, "locations": []}
        try:
            result = navigator.goto_definition_by_name(symbol)
        except NavigationError as exc:
            logger.debug("find_definitions failed: %s", exc)
            return {"symbol": symbol, "locations": []}
        return {
            "symbol": result.symbol,
            "locations": [
                {"file_path": loc.path, "line": loc.line}
                for loc in result.locations
            ],
        }

    async def find_references(
        self,
        symbol: str,
        *,
        include_definition: bool = False,
    ) -> dict[str, Any]:
        """Return reference locations for ``symbol``.

        Returns a dict with ``symbol`` and ``locations`` (list of
        ``{file_path, line, symbol?}``). Never raises.
        """
        navigator = await self._ensure_built()
        if navigator is None:
            return {"symbol": symbol, "locations": []}
        try:
            result = navigator.goto_references_by_name(
                symbol,
                include_definition=include_definition,
            )
        except NavigationError as exc:
            logger.debug("find_references failed: %s", exc)
            return {"symbol": symbol, "locations": []}
        return {
            "symbol": result.symbol,
            "locations": [
                {
                    "file_path": loc.path,
                    "line": loc.line,
                    "symbol": loc.symbol,
                }
                for loc in result.locations
            ],
        }

    async def goto_definition(
        self,
        file_path: str,
        line: int,
        column: int,
    ) -> dict[str, Any]:
        """Go to definition for the symbol at ``file_path:line:column``.

        ``line`` and ``column`` are 1-indexed. Never raises.
        """
        navigator = await self._ensure_built()
        if navigator is None:
            return {"symbol": None, "locations": []}
        full_path = str(self._workspace / file_path)
        try:
            result = navigator.goto_definition(full_path, line, column)
        except NavigationError as exc:
            logger.debug("goto_definition failed: %s", exc)
            return {"symbol": None, "locations": []}
        return {
            "symbol": result.symbol,
            "locations": [
                {"file_path": loc.path, "line": loc.line}
                for loc in result.locations
            ],
        }

    def invalidate(self) -> None:
        """Drop the cached navigator so the next call rebuilds."""
        self._navigator = None


__all__ = ["CodebaseGraphIndex"]
