"""Codebase indexer — builds local searchable chunks from workspace files."""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..agent.perception.parsers import PARSERS, SymbolNode
from .store import CodebaseStore

logger = logging.getLogger(__name__)

_SOURCE_EXTENSIONS = set(PARSERS.keys())

# Directories to always skip (matches perception indexer + common build dirs).
_SKIP_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".tox", "dist", "build", ".eggs", ".minimax",
    ".hg", ".svn", "vendor", ".next", ".nuxt", "coverage", ".coverage",
    "htmlcov", ".terraform", ".serverless", ".playwright-mcp", ".ruff_cache",
    "grok-build",
}

_SKIP_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
    "uv.lock", "poetry.lock", "go.sum",
    ".DS_Store", "Thumbs.db",
}

_MAX_FILES = 500
_MAX_FILE_SIZE = 2 * 1024 * 1024  # 2 MB
_BINARY_SNIFF = 8192


class IndexStatus(StrEnum):
    """Lifecycle status of an indexing run."""

    IDLE = "idle"
    INDEXING = "indexing"
    DONE = "done"
    ERROR = "error"


@dataclass
class IndexProgress:
    """Current indexing progress snapshot."""

    status: IndexStatus = IndexStatus.IDLE
    processed: int = 0
    total: int = 0
    message: str = ""
    error: str | None = None


class CodebaseIndexer:
    """Walk a workspace, parse source files, and persist searchable chunks.

    The indexer is intentionally lightweight: no external vector model,
    just FTS5 over file contents + extracted symbol metadata.
    """

    def __init__(
        self,
        workspace: Path | str,
        store: CodebaseStore,
        *,
        max_files: int = _MAX_FILES,
        max_file_size: int = _MAX_FILE_SIZE,
    ) -> None:
        self._workspace = Path(workspace).expanduser().resolve()
        self._store = store
        self._max_files = max_files
        self._max_file_size = max_file_size
        self._progress = IndexProgress()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[Any] | None = None

    @property
    def progress(self) -> IndexProgress:
        """Return a copy of the current progress."""
        return IndexProgress(
            status=self._progress.status,
            processed=self._progress.processed,
            total=self._progress.total,
            message=self._progress.message,
            error=self._progress.error,
        )

    def to_dict(self) -> dict[str, Any]:
        """Snapshot suitable for ``codebase.status`` responses."""
        p = self.progress
        percent = 0
        if p.total > 0:
            percent = int((p.processed / p.total) * 100)
        return {
            "status": p.status.value,
            "processed": p.processed,
            "total": p.total,
            "percent": percent,
            "message": p.message,
            "error": p.error,
        }

    async def build_index(self, *, force: bool = False) -> dict[str, Any]:
        """Trigger a full index build.

        If an index run is already active, returns its current status.
        Otherwise starts a background task and returns the initial status.
        """
        async with self._lock:
            if self._progress.status == IndexStatus.INDEXING:
                return self.to_dict()
            if self._task is not None and not self._task.done():
                return self.to_dict()
            self._progress = IndexProgress(status=IndexStatus.INDEXING)
            self._task = asyncio.create_task(self._run_build(force=force))
        return self.to_dict()

    async def _run_build(self, *, force: bool) -> None:
        """Actual indexing work executed in a background task."""
        try:
            files = await self._discover_files()
            self._progress.total = len(files)
            self._progress.processed = 0
            self._progress.message = f"Indexing {len(files)} files"

            if force:
                await self._store.clear()

            for rel_path in files:
                await self._index_file(rel_path)
                self._progress.processed += 1
                self._progress.message = f"Indexed {rel_path}"

            self._progress.status = IndexStatus.DONE
            self._progress.message = f"Indexed {self._progress.processed} files"
        except Exception as exc:  # noqa: BLE001
            logger.exception("codebase index build failed")
            self._progress.status = IndexStatus.ERROR
            self._progress.error = str(exc)
            self._progress.message = "Index build failed"

    async def _discover_files(self) -> list[str]:
        """Return a sorted list of relative source-file paths to index."""
        return await asyncio.to_thread(self._sync_discover_files)

    def _sync_discover_files(self) -> list[str]:
        """Synchronous file discovery (runs in thread pool)."""
        files: list[str] = []
        count = 0
        for dirpath, dirnames, filenames in os.walk(self._workspace):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                if name in _SKIP_FILES:
                    continue
                if count >= self._max_files:
                    logger.info("codebase: hit file limit (%d)", self._max_files)
                    files.sort()
                    return files
                full = Path(dirpath) / name
                ext = full.suffix.lower()
                if ext not in _SOURCE_EXTENSIONS:
                    continue
                try:
                    st = full.stat()
                except OSError:
                    continue
                if st.st_size > self._max_file_size:
                    continue
                files.append(self._rel_path(full))
                count += 1
        files.sort()
        return files

    async def _index_file(self, rel_path: str) -> None:
        """Read, parse, and store chunks for a single file."""
        full = self._workspace / rel_path
        try:
            data = await asyncio.to_thread(full.read_bytes)
        except OSError:
            return
        if b"\x00" in data[:_BINARY_SNIFF]:
            return
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return

        ext = full.suffix.lower()
        parser = PARSERS.get(ext)
        symbols: list[SymbolNode] = parser(text, full.name) if parser else []

        # Delete old chunks for this file then insert a single file-level chunk.
        await self._store.delete_chunks_for_file(rel_path)
        lines = text.splitlines()
        metadata = {
            "language": ext.lstrip("."),
            "total_lines": len(lines),
            "symbols": [_symbol_to_dict(s) for s in symbols],
        }
        await self._store.save_chunk(
            file_path=rel_path,
            start_line=1,
            end_line=max(1, len(lines)),
            content=text,
            metadata=metadata,
        )

    def _rel_path(self, full: Path) -> str:
        try:
            return full.relative_to(self._workspace).as_posix()
        except ValueError:
            return full.as_posix()

    async def invalidate_path(self, rel_path: str) -> None:
        """Remove a file from the index (e.g. after deletion)."""
        await self._store.delete_chunks_for_file(rel_path)


def _symbol_to_dict(node: SymbolNode) -> dict[str, Any]:
    return {
        "name": node.name,
        "kind": node.kind,
        "line": node.line,
        "children": [_symbol_to_dict(c) for c in node.children],
    }
