#!/usr/bin/env python3
"""Copy a prepared manual-upgrade backup to a separate private directory.

This never changes the source, restores data, or switches a release. The
recorded hash detects a changed copy; it does not authenticate the producer.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def directory(raw: str, label: str) -> Path:
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir() or path.resolve() != path:
        raise ValueError(f"{label} must be an existing absolute directory without symbolic links")
    return path


def open_regular(path: Path):
    before = path.lstat()
    if not stat.S_ISREG(before.st_mode):
        raise ValueError(f"not a regular file: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(path, flags)
    stream = os.fdopen(fd, "rb")
    after = os.fstat(stream.fileno())
    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
        after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
    ):
        stream.close()
        raise ValueError(f"file changed before opening: {path}")
    return stream, after


def copy_backup(source_raw: str, destination_raw: str) -> Path:
    source = directory(source_raw, "source transaction")
    parent = directory(destination_raw, "destination parent")
    if source == parent or source in parent.parents or parent in source.parents:
        raise ValueError("source transaction and destination parent must not overlap")
    if os.name != "nt":
        info = parent.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("destination parent must belong to the current user and have mode 0700")

    with open_regular(source / "state.json")[0] as state_file:
        state_bytes = state_file.read(1024 * 1024 + 1)
    if len(state_bytes) > 1024 * 1024:
        raise ValueError("source state is too large")
    state = json.loads(state_bytes)
    digest = state.get("backup_sha256") if isinstance(state, dict) else None
    if not isinstance(state, dict) or state.get("status") != "backup-ready" or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("source is not a manual upgrade transaction with a recorded backup digest")

    source_file, original = open_regular(source / "data-vault.zip")
    try:
        target = Path(tempfile.mkdtemp(prefix="minimax-upgrade-copy-", dir=parent))
        print(f"Copy transaction: {target}", flush=True)
        try:
            (target / ".COPY-INCOMPLETE").write_text("Do not use this incomplete copy.\n", encoding="utf-8")
            hasher = hashlib.sha256()
            count = 0
            with (target / "data-vault.zip").open("xb") as output:
                while chunk := source_file.read(1024 * 1024):
                    output.write(chunk)
                    hasher.update(chunk)
                    count += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            after = os.fstat(source_file.fileno())
            if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
                original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns
            ) or count != original.st_size or hasher.hexdigest() != digest:
                raise ValueError("source changed or does not match the recorded backup digest")
            with (target / "source-state.json").open("xb") as output:
                output.write(state_bytes)
                output.flush()
                os.fsync(output.fileno())
            receipt = {"format": "minimax-upgrade-backup-copy-v1", "source_transaction": str(source),
                       "copied_at": datetime.now(UTC).isoformat(), "backup_sha256": digest,
                       "bytes": count, "scope": "data-only", "exclusions": state.get("exclusions"),
                       "notice": state.get("notice")}
            with (target / "receipt.json").open("x", encoding="utf-8") as output:
                json.dump(receipt, output, ensure_ascii=True, indent=2)
                output.flush()
                os.fsync(output.fileno())
            with (target / ".COPY-INCOMPLETE").open("w", encoding="utf-8") as marker:
                marker.write("Copy completed; review receipt and verify with the original release before restore.\n")
                marker.flush()
                os.fsync(marker.fileno())
            os.rename(target / ".COPY-INCOMPLETE", target / ".COPY-COMPLETE")
            return target
        except BaseException:
            print(f"Copy incomplete; retained {target}. Do not use it as a backup.", file=sys.stderr)
            raise
    finally:
        source_file.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-transaction", required=True)
    parser.add_argument("--destination-parent", required=True)
    args = parser.parse_args()
    try:
        target = copy_backup(args.source_transaction, args.destination_parent)
        print(f"Backup copy created: {target}. Retain the original and review the exclusions.")
        print("The digest does not authenticate the producer or prove restore readiness.")
        return 0
    except (OSError, ValueError, UnicodeError, json.JSONDecodeError, KeyboardInterrupt) as exc:
        print(f"Backup copy failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
