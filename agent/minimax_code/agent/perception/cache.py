"""Simple mtime-based file change cache.

Tracks file modification times so the indexer can skip re-scanning
unchanged files. Entries can be explicitly invalidated when the agent
edits a file.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


class FileChangeCache:
    """Track file modification times to detect changes.

    Parameters
    ----------
    workspace:
        Root directory for relative path computation.
    """

    def __init__(self, workspace: Path) -> None:
        self._workspace = workspace
        self._mtimes: dict[str, float] = {}

    def is_stale(self, path: Path) -> bool:
        """Return ``True`` if *path* has changed since last check.

        Files not yet tracked are considered stale (never seen before).
        """
        key = self._key(path)
        try:
            current = path.stat().st_mtime
        except OSError:
            return True
        previous = self._mtimes.get(key)
        if previous is None:
            return True
        return current != previous

    def update(self, path: Path) -> None:
        """Record current mtime for *path*."""
        key = self._key(path)
        try:
            self._mtimes[key] = path.stat().st_mtime
        except OSError:
            pass

    def invalidate(self, paths: list[str]) -> None:
        """Remove entries so next access triggers re-scan."""
        for p in paths:
            self._mtimes.pop(p, None)
            # Also try with forward-slash normalisation.
            self._mtimes.pop(p.replace("\\", "/"), None)

    def clear(self) -> None:
        """Drop all cached mtimes."""
        self._mtimes.clear()

    def _key(self, path: Path) -> str:
        """Compute cache key (relative path, forward slashes)."""
        try:
            rel = str(path.resolve().relative_to(self._workspace.resolve()))
        except ValueError:
            rel = str(path)
        return rel.replace("\\", "/")
