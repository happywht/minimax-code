"""File-system watcher using watchfiles.

Emits change events to an ``asyncio.Queue`` that the SSE endpoint
drains. Designed to be started as a background ``asyncio.Task`` that
cancels cleanly on shutdown.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Default ignore patterns — we skip these directories entirely.
DEFAULT_IGNORE_PATTERNS: set[str] = {
    "node_modules",
    ".git",
    "__pycache__",
    ".venv",
    "venv",
    ".tox",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".next",
    ".nuxt",
    "target",
}


class WatcherEvent:
    """A single file-change event pushed to the SSE bus."""

    __slots__ = ("kind", "path")

    def __init__(self, kind: str, path: str) -> None:
        self.kind = kind  # "created" | "changed" | "deleted"
        self.path = path

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "path": self.path}


def _classify_change(change_type: Any) -> str:
    """Map a watchfiles ``Change`` enum to a human-friendly string."""
    # watchfiles uses (added, modified, deleted) tuples.
    name = str(change_type)
    if "added" in name or "created" in name:
        return "created"
    if "deleted" in name or "removed" in name:
        return "deleted"
    return "changed"


def _is_ignored_path(path: str, ignore_patterns: set[str]) -> bool:
    """Return whether any complete path segment is ignored."""
    ignored = {pattern.casefold() for pattern in ignore_patterns}
    return any(part.casefold() in ignored for part in Path(path).parts)


async def watch_workspace(
    workspace: Path,
    event_queue: asyncio.Queue[WatcherEvent],
    *,
    ignore_patterns: set[str] | None = None,
) -> None:
    """Watch *workspace* for file changes and push events to *event_queue*.

    Runs until cancelled.  Uses ``watchfiles.awatch`` under the hood,
    which uses OS-native file-system events (inotify / ReadDirectoryChangesW).

    Parameters
    ----------
    workspace:
        Root directory to watch.
    event_queue:
        ``asyncio.Queue`` that receives :class:`WatcherEvent` instances.
    ignore_patterns:
        Directory names to skip. Defaults to :data:`DEFAULT_IGNORE_PATTERNS`.
    """
    try:
        from watchfiles import awatch
    except ImportError:
        logger.warning(
            "watchfiles not installed — live-preview file watching disabled. "
            "Install with: pip install watchfiles"
        )
        return

    ignores = ignore_patterns or DEFAULT_IGNORE_PATTERNS

    async for changes in awatch(
        str(workspace),
        watch_filter=lambda change, path: not _is_ignored_path(path, ignores),
    ):
        for change_type, path in changes:
            event = WatcherEvent(
                kind=_classify_change(change_type),
                path=str(Path(path).relative_to(workspace)),
            )
            try:
                event_queue.put_nowait(event)
            except asyncio.QueueFull:
                logger.debug("watcher event queue full — dropping %s", event.path)
