"""Copy a completed SQLite backup or data-vault bundle to a new directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def copy_backup(source: Path, destination_parent: Path) -> Path:
    if not source.is_absolute() or not destination_parent.is_absolute():
        raise ValueError("source and destination parent must be absolute paths")
    if source.suffix.lower() not in {".db", ".zip"}:
        raise ValueError("source must be a completed .db backup or .zip bundle")
    before = source.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("source must be a regular file, not a link")
    if destination_parent.is_symlink() or not destination_parent.is_dir():
        raise ValueError("destination parent must be an existing directory, not a link")
    if source.suffix.lower() == ".db" and any(
        Path(str(source) + suffix).exists() for suffix in ("-wal", "-shm", "-journal")
    ):
        raise ValueError("database has SQLite sidecars; copy a completed standalone snapshot")

    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    with os.fdopen(os.open(source, flags), "rb") as input_file:
        opened = os.fstat(input_file.fileno())
        identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
        if identity(before) != identity(opened):
            raise ValueError("source changed while opening")
        target = Path(tempfile.mkdtemp(prefix="minimax-backup-copy-", dir=destination_parent))
        print(f"Copy directory: {target}", flush=True)
        try:
            marker = target / ".COPY-INCOMPLETE"
            marker.write_text("Copy incomplete. Do not use.\n", encoding="utf-8")
            digest = hashlib.sha256()
            count = 0
            with (target / source.name).open("xb") as output:
                while chunk := input_file.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                    count += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            if identity(os.fstat(input_file.fileno())) != identity(opened) or count != opened.st_size:
                raise ValueError("source changed during copying")
            receipt = {"format": "minimax-backup-copy-v1", "source": str(source),
                       "file": source.name, "bytes": count, "sha256": digest.hexdigest(),
                       "copied_at": datetime.now(UTC).isoformat()}
            with (target / "receipt.json").open("x", encoding="utf-8") as output:
                json.dump(receipt, output, ensure_ascii=False, indent=2)
                output.flush()
                os.fsync(output.fileno())
            with (target / ".COPY-COMPLETE").open("x", encoding="utf-8") as completed:
                completed.write("Copy completed. Use only if .COPY-INCOMPLETE is absent.\n")
                completed.flush()
                os.fsync(completed.fileno())
            marker.unlink()
            return target
        except BaseException:
            print(f"Copy incomplete; retained {target}. Do not use it as a backup.", file=sys.stderr)
            raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--destination-parent", required=True, type=Path)
    args = parser.parse_args()
    try:
        target = copy_backup(args.source, args.destination_parent)
    except (OSError, ValueError) as exc:
        print(f"Backup copy failed: {exc}", file=sys.stderr)
        return 1
    print(f"Backup copy created: {target}. Keep the original and review the receipt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
