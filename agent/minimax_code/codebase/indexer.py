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
from .embedder import CodebaseEmbedder, get_default_embedder
from .graph_index import CodebaseGraphIndex
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

# Chunking strategy: line-based windows with a small overlap so symbols that
# sit near a boundary are still discoverable from either adjacent chunk.
_CHUNK_SIZE = 50
_CHUNK_OVERLAP = 5


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
        embedder: CodebaseEmbedder | None = None,
    ) -> None:
        self._workspace = Path(workspace).expanduser().resolve()
        self._store = store
        self._max_files = max_files
        self._max_file_size = max_file_size
        self._embedder = embedder or get_default_embedder()
        self._graph_index = CodebaseGraphIndex(self._workspace)
        self._progress = IndexProgress()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[Any] | None = None

    @property
    def embedder(self) -> CodebaseEmbedder:
        """Return the embedder used by this indexer."""
        return self._embedder

    @property
    def graph_index(self) -> CodebaseGraphIndex:
        """Return the scope-graph index for this workspace."""
        return self._graph_index

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
        """Actual indexing work executed in a background task.

        When ``force`` is False we only re-index files whose mtime or size
        has changed since the last run, and remove records for deleted files.
        """
        try:
            current_files = await self._discover_files()
            self._progress.total = len(current_files)
            self._progress.processed = 0

            if force:
                await self._store.clear()
                await self._store.clear_file_index_state()
                files_to_index = current_files
                self._progress.message = f"Indexing {len(files_to_index)} files"
            else:
                previous_state = await self._store.get_file_index_state()
                current_paths = {f[0] for f in current_files}
                deleted_paths = set(previous_state.keys()) - current_paths

                files_to_index = []
                for rel_path, mtime, size in current_files:
                    prev = previous_state.get(rel_path)
                    if prev is None or prev["mtime"] != mtime or prev["size"] != size:
                        files_to_index.append((rel_path, mtime, size))

                for rel_path in deleted_paths:
                    await self._store.delete_chunks_for_file(rel_path)
                    await self._store.delete_file_index_state(rel_path)

                unchanged = len(current_files) - len(files_to_index)
                self._progress.total = len(files_to_index) + len(deleted_paths)
                self._progress.message = (
                    f"Indexing {len(files_to_index)} changed files, "
                    f"{unchanged} unchanged, {len(deleted_paths)} deleted"
                )

            for rel_path, mtime, size in files_to_index:
                await self._index_file(rel_path, mtime=mtime, size=size)
                self._progress.processed += 1
                self._progress.message = f"Indexed {rel_path}"

            self._progress.status = IndexStatus.DONE
            self._progress.message = f"Indexed {self._progress.processed} files"
        except Exception as exc:  # noqa: BLE001
            logger.exception("codebase index build failed")
            self._progress.status = IndexStatus.ERROR
            self._progress.error = str(exc)
            self._progress.message = "Index build failed"

    async def _discover_files(self) -> list[tuple[str, float, int]]:
        """Return a sorted list of (rel_path, mtime, size) tuples to index."""
        return await asyncio.to_thread(self._sync_discover_files)

    def _sync_discover_files(self) -> list[tuple[str, float, int]]:
        """Synchronous file discovery (runs in thread pool)."""
        files: list[tuple[str, float, int]] = []
        count = 0
        for dirpath, dirnames, filenames in os.walk(self._workspace):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
            for name in filenames:
                if name in _SKIP_FILES:
                    continue
                if count >= self._max_files:
                    logger.info("codebase: hit file limit (%d)", self._max_files)
                    files.sort(key=lambda x: x[0])
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
                files.append((self._rel_path(full), st.st_mtime, st.st_size))
                count += 1
        files.sort(key=lambda x: x[0])
        return files

    async def _index_file(self, rel_path: str, *, mtime: float, size: int) -> None:
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

        # Delete old chunks for this file then insert fresh line windows.
        await self._store.delete_chunks_for_file(rel_path)
        lines = text.splitlines()
        total_lines = len(lines)
        language = ext.lstrip(".")
        all_symbols = [_symbol_to_dict(s) for s in symbols]

        saved_chunks: list[dict[str, Any]] = []
        start = 0
        while start < total_lines:
            end = min(start + _CHUNK_SIZE, total_lines)
            # Pull in a few extra lines as context for the next window so
            # symbols near chunk boundaries remain searchable from either
            # side. The last chunk simply runs to the end of the file.
            if end < total_lines:
                overlap_end = min(end + _CHUNK_OVERLAP, total_lines)
                chunk_lines = lines[start:overlap_end]
                next_start = end
                chunk_end_line = overlap_end
            else:
                chunk_lines = lines[start:end]
                next_start = total_lines
                chunk_end_line = end

            chunk_text = "\n".join(chunk_lines)
            chunk_symbols = _symbols_in_range(all_symbols, start + 1, chunk_end_line)
            row = await self._save_chunk(
                rel_path,
                start_line=start + 1,
                end_line=chunk_end_line,
                content=chunk_text,
                language=language,
                total_lines=total_lines,
                symbols=chunk_symbols,
            )
            saved_chunks.append(row)
            start = next_start

        # Compute and persist embeddings for every chunk of this file.
        if saved_chunks:
            try:
                texts = [c["content"] for c in saved_chunks]
                embeddings = await self._embedder.embed(texts)
                for chunk, embedding in zip(saved_chunks, embeddings, strict=True):
                    await self._store.save_embedding(chunk["rowid"], embedding)
            except Exception:  # noqa: BLE001
                logger.exception("failed to embed chunks for %s", rel_path)

        await self._store.save_file_index_state(rel_path, mtime=mtime, size=size)

    async def _save_chunk(
        self,
        rel_path: str,
        *,
        start_line: int,
        end_line: int,
        content: str,
        language: str,
        total_lines: int,
        symbols: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Persist a single chunk with normalized metadata."""
        metadata = {
            "language": language,
            "total_lines": total_lines,
            "symbols": symbols,
        }
        return await self._store.save_chunk(
            file_path=rel_path,
            start_line=start_line,
            end_line=end_line,
            content=content,
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
        await self._store.delete_file_index_state(rel_path)


def _symbol_to_dict(node: SymbolNode) -> dict[str, Any]:
    return {
        "name": node.name,
        "kind": node.kind,
        "line": node.line,
        "children": [_symbol_to_dict(c) for c in node.children],
    }


def _symbols_in_range(
    symbols: list[dict[str, Any]], start_line: int, end_line: int
) -> list[dict[str, Any]]:
    """Return symbols (with children filtered) whose line falls inside the window.

    A symbol is kept if its own line is within ``[start_line, end_line]``;
    children are recursively filtered the same way. This keeps chunk metadata
    small and relevant for retrieval.
    """
    out: list[dict[str, Any]] = []
    for symbol in symbols:
        line = symbol.get("line", 0)
        if start_line <= line <= end_line:
            filtered = dict(symbol)
            filtered["children"] = _symbols_in_range(
                symbol.get("children", []), start_line, end_line
            )
            out.append(filtered)
        else:
            # The symbol itself is outside the window, but a child might still
            # fall inside (e.g., nested class with methods spanning chunks).
            out.extend(_symbols_in_range(symbol.get("children", []), start_line, end_line))
    return out
