"""Repo-Map indexer — builds a compressed symbol tree of the workspace.

The indexer walks the workspace directory tree, extracts symbols from
source files using language-specific parsers, and compresses the result
into a text block that fits within a configurable token budget (default
2000 tokens). This block is injected into the LLM's system prompt so
the model has deep awareness of the project structure.

Caching
-------
Results are cached in memory. A :class:`FileChangeCache` tracks mtime
changes so only modified files are re-scanned on subsequent calls. The
caller can explicitly invalidate paths (e.g. after an edit).

Compression
-----------
If the raw output exceeds the token budget, compression is applied:
1. Truncate symbol children deeper than 2 levels.
2. Remove inner functions (keep module + top-level class/function).
3. Drop files with the deepest paths first.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path

from ..compaction import estimate_tokens
from .cache import FileChangeCache
from .parsers import PARSERS, SymbolNode

logger = logging.getLogger(__name__)

# Source extensions we know how to parse.
_SOURCE_EXTENSIONS = set(PARSERS.keys())

# Directories to always skip.
_SKIP_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".tox", "dist", "build", ".eggs", ".minimax",
    ".hg", ".svn", "vendor", ".next", ".nuxt", "coverage", ".coverage",
    "htmlcov", ".terraform", ".serverless",
}

# Files to always skip.
_SKIP_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "uv.lock", "poetry.lock", "go.sum",
    ".DS_Store", "Thumbs.db",
}

# Max files to scan (prevents huge repos from stalling).
_MAX_FILES = 500

# Binary file sniff size.
_BINARY_SNIFF = 8192


@dataclass
class FileOutline:
    """Symbol outline for a single source file."""

    path: str
    language: str
    symbols: list[SymbolNode] = field(default_factory=list)


class RepoMapIndexer:
    """Walk the workspace, extract symbols, compress to a repo-map string.

    Parameters
    ----------
    workspace:
        Root directory to index.
    max_tokens:
        Maximum estimated tokens for the generated map string.
    """

    def __init__(self, workspace: Path, *, max_tokens: int = 2000) -> None:
        self._workspace = workspace
        self._max_tokens = max_tokens
        self._cache = FileChangeCache(workspace)
        self._outlines: list[FileOutline] = []
        self._last_map: str | None = None
        self._dirty = True  # Force initial build.

    async def build_map(self) -> str:
        """Build (or return cached) repo-map string.

        Returns a text block suitable for injection into the LLM's
        system prompt, kept within ``max_tokens`` estimated tokens.
        """
        if not self._dirty and self._last_map is not None:
            # Check if any files have changed since last build.
            if not self._any_stale():
                return self._last_map

        # Walk and parse all source files.
        outlines = self._scan_all()
        self._outlines = outlines
        self._dirty = False

        # Render and compress to fit within budget.
        raw = self._render(outlines)
        compressed = self._compress(raw, self._max_tokens)

        self._last_map = compressed
        return compressed

    def invalidate(self, changed_paths: list[str]) -> None:
        """Mark specific files as stale. Next ``build_map()`` will re-scan."""
        self._cache.invalidate(changed_paths)
        self._dirty = True

    def invalidate_all(self) -> None:
        """Force a full rebuild on next ``build_map()``."""
        self._cache.clear()
        self._dirty = True

    # ------------------------------------------------------------------
    # Internal: scanning
    # ------------------------------------------------------------------

    def _scan_all(self) -> list[FileOutline]:
        """Walk workspace and produce outlines for all source files."""
        outlines: list[FileOutline] = []
        count = 0

        for dirpath, dirnames, filenames in os.walk(self._workspace):
            # Prune skip directories.
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

            for name in filenames:
                if name in _SKIP_FILES:
                    continue
                if count >= _MAX_FILES:
                    logger.info("repo-map: hit file limit (%d), stopping scan", _MAX_FILES)
                    return outlines

                full = Path(dirpath) / name
                ext = full.suffix.lower()
                if ext not in _SOURCE_EXTENSIONS:
                    continue

                # Skip binary files.
                try:
                    data = full.read_bytes()
                except OSError:
                    continue
                if b"\x00" in data[:_BINARY_SNIFF]:
                    continue

                # Parse if stale (or never seen).
                if self._cache.is_stale(full):
                    try:
                        text = data.decode("utf-8")
                    except UnicodeDecodeError:
                        continue
                    parser = PARSERS[ext]
                    symbols = parser(text, name)
                    rel = self._rel_path(full)
                    lang = ext.lstrip(".")
                    outlines.append(FileOutline(path=rel, language=lang, symbols=symbols))
                    self._cache.update(full)
                else:
                    # File unchanged; keep existing outline if available.
                    existing = self._find_outline(self._rel_path(full))
                    if existing:
                        outlines.append(existing)

                count += 1

        # Sort by path for deterministic output.
        outlines.sort(key=lambda o: o.path)
        return outlines

    def _any_stale(self) -> bool:
        """Quick check: have any tracked files changed?"""
        for outline in self._outlines:
            full = self._workspace / outline.path
            if self._cache.is_stale(full):
                return True
        return False

    def _find_outline(self, rel_path: str) -> FileOutline | None:
        """Find an existing outline by relative path."""
        for o in self._outlines:
            if o.path == rel_path:
                return o
        return None

    # ------------------------------------------------------------------
    # Internal: rendering
    # ------------------------------------------------------------------

    def _render(self, outlines: list[FileOutline]) -> str:
        """Render outlines into a text block."""
        if not outlines:
            return ""

        lines: list[str] = []
        total_tokens = 0
        file_count = 0

        for outline in outlines:
            entry_lines = self._render_outline(outline)
            entry_text = "\n".join(entry_lines) + "\n"
            entry_tokens = estimate_tokens(entry_text)

            if total_tokens + entry_tokens > self._max_tokens * 1.5:
                # Stop adding files — we're well over budget.
                break

            lines.extend(entry_lines)
            lines.append("")
            total_tokens += entry_tokens
            file_count += 1

        if not lines:
            return ""

        header = f"repo-map ({file_count} files, ~{total_tokens // 1000}k tokens)\n"
        omitted = len(outlines) - file_count
        if omitted > 0:
            footer = f"\n... ({omitted} files omitted for brevity)"
        else:
            footer = ""

        return header + "\n".join(lines) + footer

    def _render_outline(self, outline: FileOutline, depth: int = 0) -> list[str]:
        """Render a single file outline as indented lines."""
        lines: list[str] = [outline.path]
        for sym in outline.symbols:
            self._render_symbol(sym, lines, depth + 1)
        return lines

    def _render_symbol(self, sym: SymbolNode, lines: list[str], depth: int) -> None:
        """Render a symbol and its children."""
        indent = "  " * depth
        suffix = f" ({sym.kind})" if sym.kind not in ("class", "function", "method") else ""
        lines.append(f"{indent}{sym.name}{suffix}")
        for child in sym.children:
            self._render_symbol(child, lines, depth + 1)

    # ------------------------------------------------------------------
    # Internal: compression
    # ------------------------------------------------------------------

    def _compress(self, text: str, budget: int) -> str:
        """Compress the repo-map text to fit within token budget."""
        tokens = estimate_tokens(text)
        if tokens <= budget:
            return text

        # Strategy 1: Truncate children deeper than 2 levels.
        compressed = self._truncate_depth(self._outlines, max_depth=2)
        rendered = self._render(compressed)
        if estimate_tokens(rendered) <= budget:
            return rendered

        # Strategy 2: Remove inner functions (keep class + top-level func).
        stripped = self._strip_inner_functions(compressed)
        rendered = self._render(stripped)
        if estimate_tokens(rendered) <= budget:
            return rendered

        # Strategy 3: Drop files with deepest paths.
        truncated = self._drop_deep_files(stripped, budget)
        rendered = self._render(truncated)
        return rendered

    def _truncate_depth(
        self, outlines: list[FileOutline], max_depth: int
    ) -> list[FileOutline]:
        """Return outlines with symbol trees truncated to *max_depth*."""
        result = []
        for o in outlines:
            truncated_symbols = [
                self._truncate_sym(s, max_depth, 0) for s in o.symbols
            ]
            result.append(FileOutline(
                path=o.path, language=o.language, symbols=truncated_symbols,
            ))
        return result

    def _truncate_sym(self, sym: SymbolNode, max_depth: int, current: int) -> SymbolNode:
        """Recursively truncate symbol children beyond *max_depth*."""
        if current >= max_depth:
            return SymbolNode(name=sym.name, kind=sym.kind, line=sym.line, children=[])
        return SymbolNode(
            name=sym.name,
            kind=sym.kind,
            line=sym.line,
            children=[self._truncate_sym(c, max_depth, current + 1) for c in sym.children],
        )

    def _strip_inner_functions(self, outlines: list[FileOutline]) -> list[FileOutline]:
        """Remove function-type symbols from inside classes (keep methods)."""
        result = []
        for o in outlines:
            filtered = []
            for sym in o.symbols:
                if sym.kind == "function" and any(
                    s.kind == "class" for s in o.symbols
                ):
                    # Skip top-level functions if there are classes (likely helpers).
                    continue
                filtered.append(sym)
            result.append(FileOutline(
                path=o.path, language=o.language, symbols=filtered,
            ))
        return result

    def _drop_deep_files(self, outlines: list[FileOutline], budget: int) -> list[FileOutline]:
        """Drop files with deepest paths until we fit the budget."""
        # Sort by path depth (shallower = more important).
        sorted_outlines = sorted(outlines, key=lambda o: o.path.count("/"))

        result: list[FileOutline] = []
        for outline in sorted_outlines:
            result.append(outline)
            rendered = self._render(result)
            if estimate_tokens(rendered) <= budget:
                break
            # Over budget — remove last and try next shallower file.

        return result

    # ------------------------------------------------------------------
    # Internal: helpers
    # ------------------------------------------------------------------

    def _rel_path(self, path: Path) -> str:
        """Get path relative to workspace, forward slashes."""
        try:
            rel = str(path.resolve().relative_to(self._workspace.resolve()))
        except ValueError:
            rel = str(path)
        return rel.replace("\\", "/")


__all__ = ["FileOutline", "RepoMapIndexer"]
