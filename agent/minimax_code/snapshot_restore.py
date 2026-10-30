"""Place a standalone SQLite snapshot in a new data directory, without opening it."""

from __future__ import annotations

import argparse
import hashlib
import os
import stat
import sys
from pathlib import Path


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def restore_snapshot(source: Path, destination: Path) -> tuple[int, str]:
    if not source.is_absolute() or not destination.is_absolute():
        raise ValueError("source and new data directory must be absolute paths")
    if source.suffix.lower() != ".db":
        raise ValueError("source must be a standalone .db snapshot")
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("source must be a regular file, not a link")
    if any(os.path.lexists(str(source) + suffix) for suffix in ("-wal", "-shm", "-journal")):
        raise ValueError("source has SQLite sidecars; use a completed standalone snapshot")
    if destination.parent.is_symlink() or not destination.parent.is_dir():
        raise ValueError("new data directory's parent must be an existing directory, not a link")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(source, flags), "rb") as input_file:
        opened = os.fstat(input_file.fileno())
        if _identity(before) != _identity(opened):
            raise ValueError("source changed while opening")
        os.mkdir(destination, 0o700)  # Exclusive: an existing directory is never replaced.
        print(f"New data directory: {destination}", flush=True)
        try:
            marker = destination / ".RESTORE-INCOMPLETE"
            with marker.open("x", encoding="utf-8") as status:
                status.write("Restore incomplete. Do not start this data directory.\n")
                status.flush()
                os.fsync(status.fileno())
            partial = destination / ".data.db.restore-partial"
            digest = hashlib.sha256()
            count = 0
            with partial.open("xb") as output:
                while chunk := input_file.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                    count += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            if _identity(os.fstat(input_file.fileno())) != _identity(opened) or count != opened.st_size:
                raise ValueError("source changed during restore")
            # A hard link creates data.db exclusively, without replacing a file
            # even if another actor unexpectedly creates it first.
            os.link(partial, destination / "data.db")
            partial.unlink()
            marker.unlink()
            return count, digest.hexdigest()
        except BaseException:
            print(f"Restore incomplete; retained {destination}. Do not start it.", file=sys.stderr)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--new-data-dir", required=True, type=Path)
    args = parser.parse_args()
    try:
        count, digest = restore_snapshot(args.snapshot, args.new_data_dir)
    except (OSError, ValueError) as exc:
        print(f"Snapshot restore failed: {exc}", file=sys.stderr)
        return 1
    print(f"Copied {count} bytes to {args.new_data_dir / 'data.db'} (SHA-256 {digest}).")
    print("Review version compatibility, missing files and external effects before starting.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
