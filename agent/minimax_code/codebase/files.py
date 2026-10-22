"""Bounded workspace discovery shared by text and scope-graph indexes."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from ..workspace_ctx import require_directory

SKIP_DIRS = {
    ".git", ".venv", "node_modules", "__pycache__", ".pytest_cache",
    ".mypy_cache", ".tox", "dist", "build", ".eggs", ".minimax",
    ".hg", ".svn", "vendor", ".next", ".nuxt", "coverage", ".coverage",
    "htmlcov", ".terraform", ".serverless", ".playwright-mcp", ".ruff_cache",
    "grok-build",
}
SKIP_FILES = {
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "uv.lock",
    "poetry.lock", "go.sum", ".ds_store", "thumbs.db",
}


def source_path(workspace: Path, relative: str) -> Path:
    # Lazy import avoids the tool-registry / indexer initialization cycle.
    from ..agent.tools.file_ops import safe_resolve

    root = require_directory(workspace)
    raw = root / relative
    resolved = safe_resolve(relative, workspace=root)
    if raw != resolved:
        raise ValueError("Indexing symbolic links or junctions is not supported")
    return resolved


def discover_sources(workspace: Path, *, supported: Callable[[Path], bool],
                     max_files: int, max_file_size: int) -> list[tuple[str, float, int]]:
    root = require_directory(workspace)
    files: list[tuple[str, float, int]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        directory = Path(dirpath)
        dirnames[:] = sorted(d for d in dirnames if d.casefold() not in SKIP_DIRS
                             and (directory / d).resolve() == directory / d)
        for name in sorted(filenames):
            if len(files) >= max_files:
                return sorted(files)
            if name.casefold() in SKIP_FILES:
                continue
            relative = (directory / name).relative_to(root).as_posix()
            try:
                full = source_path(root, relative)
                if not supported(full):
                    continue
                stat = full.stat()
                if not full.is_file() or stat.st_size > max_file_size:
                    continue
            except (OSError, ValueError):
                continue
            files.append((relative, stat.st_mtime, stat.st_size))
    return sorted(files)


def read_source(workspace: Path, relative: str, *, max_file_size: int) -> bytes:
    target = source_path(workspace, relative)
    with target.open("rb") as stream:
        data = stream.read(max_file_size + 1)
    if len(data) > max_file_size:
        raise ValueError("Source exceeded indexing size limit")
    return data
