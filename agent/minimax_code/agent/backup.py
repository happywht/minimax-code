"""Pre-edit file backup manager.

Before the agent overwrites or edits a file, the :class:`BackupManager`
creates a timestamped copy in ``.minimax/backups/`` within the workspace.
Backups are best-effort — a failure is logged but never blocks the edit.

Each file keeps at most ``_MAX_BACKUPS_PER_FILE`` backups; the oldest
is automatically pruned when the limit is exceeded.
"""

from __future__ import annotations

import logging
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_BACKUP_DIR = ".minimax/backups"
_MAX_BACKUPS_PER_FILE = 10


class BackupManager:
    """Manages pre-edit file backups inside ``.minimax/backups/``.

    Parameters
    ----------
    workspace:
        The workspace root path. If ``None``, falls back to
        ``MINIMAX_CODE_WORKSPACE`` env var or CWD.
    """

    def __init__(self, workspace: Path | None = None) -> None:
        if workspace is not None:
            self._workspace = workspace
        else:
            env = os.environ.get("MINIMAX_CODE_WORKSPACE")
            self._workspace = Path(env) if env else Path.cwd()

    @property
    def backup_root(self) -> Path:
        """Root directory for all backups."""
        return self._workspace / _BACKUP_DIR

    def backup(self, file_path: Path) -> dict[str, Any] | None:
        """Create a timestamped backup of *file_path*.

        Returns metadata dict ``{backup_path, original_path, timestamp}``
        or ``None`` if the file does not exist or backup fails.
        Backup failures are logged but **never** raise.
        """
        if not file_path.exists():
            return None

        try:
            # Build backup path: .minimax/backups/20260607_143052_src_main.py
            ts = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S%f")
            # Flatten subdirectories to avoid deep nesting in backup dir.
            rel = self._relative_path(file_path)
            flat_name = rel.replace(os.sep, "_").replace("/", "_")
            backup_name = f"{ts}_{flat_name}"
            dest = self.backup_root / backup_name

            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(file_path), str(dest))

            # Prune old backups for this file.
            self._prune(file_path)

            return {
                "backup_path": str(dest),
                "original_path": str(file_path),
                "timestamp": ts,
            }
        except Exception as exc:
            # Best-effort: log and move on, never block the edit.
            logger.warning("backup failed for %s: %s", file_path, exc)
            return None

    def _prune(self, file_path: Path) -> None:
        """Keep at most ``_MAX_BACKUPS_PER_FILE`` backups, delete oldest."""
        rel = self._relative_path(file_path)
        flat_name = rel.replace(os.sep, "_").replace("/", "_")

        try:
            backup_dir = self.backup_root
            if not backup_dir.exists():
                return

            # Find all backups for this file (matching suffix).
            candidates: list[Path] = []
            for entry in backup_dir.iterdir():
                if not entry.is_file():
                    continue
                name = entry.name
                # Format: TIMESTAMP_FLATNAME
                if name.endswith(flat_name):
                    candidates.append(entry)

            if len(candidates) <= _MAX_BACKUPS_PER_FILE:
                return

            # Sort by name (timestamps are lexicographically sortable).
            candidates.sort(key=lambda p: p.name)
            # Delete oldest, keeping the newest _MAX_BACKUPS_PER_FILE.
            to_delete = candidates[:-_MAX_BACKUPS_PER_FILE]
            for old in to_delete:
                try:
                    old.unlink()
                except OSError:
                    pass  # Best-effort pruning.
        except Exception as exc:
            logger.debug("prune failed for %s: %s", file_path, exc)

    def _relative_path(self, file_path: Path) -> str:
        """Get path relative to workspace, or fall back to filename."""
        try:
            return str(file_path.resolve().relative_to(self._workspace.resolve()))
        except ValueError:
            return file_path.name


__all__ = ["BackupManager"]
