"""Pre-edit file backup manager.

Native file tools use ``backup_verified`` and block existing-file mutations
when a reviewed baseline cannot be copied and verified. These copies and their
metadata are retained in the workspace's ``.minimax/backups`` directory.

The legacy ``backup`` API remains best-effort and prunes its older copies; it
is separate from the verified backups used by those tools.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ..file_mutations import _file_identity

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
            # Session-scoped root first (per-project workspace); env/CWD
            # fallback keeps the legacy process-wide behaviour.
            from ..workspace_ctx import current_root, env_or_cwd_root

            root = current_root()
            self._workspace = root if root is not None else env_or_cwd_root()

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


    def backup_verified(self, file_path: Path, *, expected_sha256: str) -> dict[str, Any]:
        """Copy and verify the exact baseline or raise without modifying it."""
        workspace = self._workspace.resolve(strict=True)
        root = workspace
        for component in (".minimax", "backups"):
            root = root / component
            try:
                root.mkdir()
            except FileExistsError:
                pass
            info = root.lstat()
            if not stat.S_ISDIR(info.st_mode) or root.resolve(strict=True) != root:
                raise OSError("Backup directory is not a safe local directory")

        before = file_path.lstat()
        if not stat.S_ISREG(before.st_mode):
            raise OSError("Backup source is not a regular file")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
        ts = datetime.now(tz=UTC).strftime("%Y%m%d_%H%M%S%f")
        # Do not flatten a pathname: different paths can flatten identically.
        name = hashlib.sha256(os.fsencode(str(file_path))).hexdigest()[:16]
        dest = root / f"{ts}_{name}_{uuid.uuid4().hex}.bak"
        manifest = dest.with_suffix(".json")
        created = False
        manifest_created = False
        try:
            source_fd = os.open(file_path, flags)
            with os.fdopen(source_fd, "rb") as source:
                opened = os.fstat(source.fileno())
                if _file_identity(before) != _file_identity(opened):
                    raise OSError("Backup source changed before copying")
                digest = hashlib.sha256()
                backup_fd = os.open(dest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(backup_fd, "wb") as target:
                    created = True
                    while chunk := source.read(65536):
                        target.write(chunk)
                        digest.update(chunk)
                    target.flush()
                    os.fsync(target.fileno())
                after = os.fstat(source.fileno())
                if (_file_identity(opened) != _file_identity(after)
                        or _file_identity(after) != _file_identity(file_path.lstat())
                        or digest.hexdigest() != expected_sha256):
                    raise OSError("Backup source changed or does not match the reviewed baseline")
            with dest.open("rb") as check:
                if hashlib.file_digest(check, "sha256").hexdigest() != expected_sha256:
                    raise OSError("Backup verification failed")
            metadata = {"backup_path": str(dest), "original_path": str(file_path),
                        "timestamp": ts, "sha256": expected_sha256}
            manifest_fd = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(manifest_fd, "w", encoding="utf-8") as record:
                manifest_created = True
                json.dump(metadata, record, ensure_ascii=False)
                record.flush()
                os.fsync(record.fileno())
            if os.name != "nt":
                directory_fd = os.open(root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            return {**metadata, "manifest_path": str(manifest)}
        except BaseException:
            if manifest_created:
                try:
                    manifest.unlink()
                except OSError:
                    pass
            if created:
                try:
                    dest.unlink()
                except OSError:
                    pass
            raise


__all__ = ["BackupManager"]
