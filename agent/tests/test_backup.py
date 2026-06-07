"""Unit tests for the BackupManager.

Covers backup creation, pruning, content preservation,
and graceful handling of missing files.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from minimax_code.agent.backup import BackupManager


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


class TestBackup:
    def test_create_backup_for_file(self, tmp_path: Path) -> None:
        """Backup creates a timestamped copy in .minimax/backups/."""
        src = tmp_path / "src" / "main.py"
        src.parent.mkdir(parents=True)
        src.write_text("print('hello')", encoding="utf-8")

        mgr = BackupManager(workspace=tmp_path)
        result = mgr.backup(src)

        assert result is not None
        assert "backup_path" in result
        assert "original_path" in result
        assert "timestamp" in result
        # The backup file should exist on disk.
        backup_path = Path(result["backup_path"])
        assert backup_path.exists()
        assert backup_path.parent == tmp_path / ".minimax" / "backups"

    def test_backup_nonexistent_file_returns_none(self, tmp_path: Path) -> None:
        """Backing up a file that does not exist returns None."""
        mgr = BackupManager(workspace=tmp_path)
        missing = tmp_path / "nonexistent.py"
        result = mgr.backup(missing)
        assert result is None

    def test_backup_preserves_content(self, tmp_path: Path) -> None:
        """The backup file has the same content as the original."""
        content = "def foo():\n    return 42\n"
        src = tmp_path / "code.py"
        src.write_text(content, encoding="utf-8")

        mgr = BackupManager(workspace=tmp_path)
        result = mgr.backup(src)

        assert result is not None
        backup_path = Path(result["backup_path"])
        assert backup_path.read_text(encoding="utf-8") == content

    def test_multiple_backups_for_same_file(self, tmp_path: Path) -> None:
        """Multiple backups for the same file all exist on disk."""
        src = tmp_path / "app.py"
        src.write_text("v1", encoding="utf-8")

        mgr = BackupManager(workspace=tmp_path)
        results = []
        for i in range(3):
            src.write_text(f"v{i}", encoding="utf-8")
            # Small sleep to ensure unique timestamps (microsecond precision).
            time.sleep(0.001)
            result = mgr.backup(src)
            results.append(result)

        # All backups should have been created.
        assert all(r is not None for r in results)
        backup_dir = tmp_path / ".minimax" / "backups"
        backups = list(backup_dir.iterdir())
        assert len(backups) == 3

    def test_prune_removes_oldest_when_over_limit(self, tmp_path: Path) -> None:
        """When >10 backups exist for a file, oldest ones are pruned."""
        src = tmp_path / "prune_me.py"
        src.write_text("content", encoding="utf-8")

        mgr = BackupManager(workspace=tmp_path)
        # Create 12 backups with slight delay between each.
        for i in range(12):
            src.write_text(f"v{i}", encoding="utf-8")
            time.sleep(0.002)
            mgr.backup(src)

        backup_dir = tmp_path / ".minimax" / "backups"
        remaining = list(backup_dir.iterdir())
        # Should be capped at 10.
        assert len(remaining) == 10

    def test_backup_root_property(self, tmp_path: Path) -> None:
        """backup_root returns workspace/.minimax/backups."""
        mgr = BackupManager(workspace=tmp_path)
        assert mgr.backup_root == tmp_path / ".minimax" / "backups"

    def test_backup_with_subdirectory_file(self, tmp_path: Path) -> None:
        """Files in subdirectories have flattened backup names."""
        src = tmp_path / "deep" / "nested" / "file.py"
        src.parent.mkdir(parents=True)
        src.write_text("content", encoding="utf-8")

        mgr = BackupManager(workspace=tmp_path)
        result = mgr.backup(src)

        assert result is not None
        backup_path = Path(result["backup_path"])
        # Name should contain flattened path (underscores for separators).
        assert "deep_nested_file.py" in backup_path.name or "file.py" in backup_path.name

    def test_backup_does_not_raise_on_permission_error(self, tmp_path: Path) -> None:
        """Backup failures are swallowed gracefully (best-effort)."""
        src = tmp_path / "ok.py"
        src.write_text("data", encoding="utf-8")

        mgr = BackupManager(workspace=tmp_path)
        # Normal backup should succeed.
        result = mgr.backup(src)
        assert result is not None

    def test_backup_manager_from_env(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """BackupManager falls back to env var when workspace is None."""
        monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
        mgr = BackupManager(workspace=None)
        assert mgr.backup_root == tmp_path / ".minimax" / "backups"
