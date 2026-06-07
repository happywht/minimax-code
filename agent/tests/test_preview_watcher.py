"""Tests for the file-system watcher (v0.6.0).

Covers:
  - WatcherEvent serialization
  - _classify_change mapping
  - watch_workspace emits events on file changes
  - Ignored directories are skipped
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from minimax_code.preview.watcher import (
    WatcherEvent,
    _classify_change,
    watch_workspace,
    DEFAULT_IGNORE_PATTERNS,
)


class TestWatcherEvent:
    """Test WatcherEvent data class."""

    def test_to_dict(self) -> None:
        evt = WatcherEvent(kind="changed", path="index.html")
        d = evt.to_dict()
        assert d == {"kind": "changed", "path": "index.html"}

    def test_created_event(self) -> None:
        evt = WatcherEvent(kind="created", path="new_file.py")
        assert evt.kind == "created"
        assert evt.path == "new_file.py"

    def test_deleted_event(self) -> None:
        evt = WatcherEvent(kind="deleted", path="old_file.py")
        d = evt.to_dict()
        assert d["kind"] == "deleted"


class TestClassifyChange:
    """Test the change-type classification helper."""

    def test_added(self) -> None:
        assert _classify_change("Change.added") == "created"

    def test_modified(self) -> None:
        assert _classify_change("Change.modified") == "changed"

    def test_deleted(self) -> None:
        assert _classify_change("Change.deleted") == "deleted"

    def test_unknown_defaults_to_changed(self) -> None:
        assert _classify_change("something_else") == "changed"


class TestDefaultIgnorePatterns:
    """Verify the default ignore set includes common noise dirs."""

    def test_git_ignored(self) -> None:
        assert ".git" in DEFAULT_IGNORE_PATTERNS

    def test_node_modules_ignored(self) -> None:
        assert "node_modules" in DEFAULT_IGNORE_PATTERNS

    def test_pycache_ignored(self) -> None:
        assert "__pycache__" in DEFAULT_IGNORE_PATTERNS


class TestWatchWorkspaceIntegration:
    """Integration test: real file system change triggers an event."""

    @pytest.mark.asyncio
    async def test_file_create_emits_event(self, tmp_path: Path) -> None:
        """Creating a file should emit a watcher event."""
        queue: asyncio.Queue[WatcherEvent] = asyncio.Queue(maxsize=10)

        # Start the watcher in the background.
        task = asyncio.create_task(watch_workspace(tmp_path, queue))

        # Give the watcher time to start.
        await asyncio.sleep(0.3)

        # Create a file.
        (tmp_path / "test_watcher.txt").write_text("hello", encoding="utf-8")

        # Wait for the event.
        try:
            event = await asyncio.wait_for(queue.get(), timeout=5.0)
            assert event.path == "test_watcher.txt"
            assert event.kind in ("created", "changed")
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    @pytest.mark.asyncio
    async def test_ignored_dir_skipped(self, tmp_path: Path) -> None:
        """Files created in ignored directories should NOT emit events."""
        queue: asyncio.Queue[WatcherEvent] = asyncio.Queue(maxsize=10)
        node_modules = tmp_path / "node_modules"
        node_modules.mkdir()

        task = asyncio.create_task(watch_workspace(tmp_path, queue))
        await asyncio.sleep(0.3)

        # Create a file inside node_modules (should be ignored).
        (node_modules / "package.json").write_text("{}", encoding="utf-8")

        # Also create a file outside (should be detected).
        (tmp_path / "real_file.txt").write_text("data", encoding="utf-8")

        try:
            event = await asyncio.wait_for(queue.get(), timeout=5.0)
            # The event should be for real_file.txt, NOT package.json.
            assert event.path == "real_file.txt"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
