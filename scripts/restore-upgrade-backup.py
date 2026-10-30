#!/usr/bin/env python3
"""Restore a manual-upgrade backup into a new directory with the old release.

Never switches, stops or starts a service and never overwrites a data directory.
The operator must independently confirm the source and that writers are stopped.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import stat
import sys
import tempfile
from pathlib import Path


def _load(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    if spec is None or spec.loader is None:
        raise RuntimeError("Required upgrade helper unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


prepare = _load("upgrade_prepare", "prepare-release-upgrade.py")
copy = _load("upgrade_copy", "copy-upgrade-backup.py")


def _json_file(path: Path) -> dict:
    stream, info = copy.open_regular(path)
    with stream:
        raw = stream.read(1024 * 1024 + 1)
        after = os.fstat(stream.fileno())
    if (len(raw) > 1024 * 1024 or len(raw) != info.st_size or
            (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)):
        raise ValueError("Backup metadata changed or is too large")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("Backup metadata must be an object")
    return result


def restore(args: argparse.Namespace) -> Path:
    if not args.confirm_stopped or not args.confirm_trusted_source:
        raise ValueError("Both --confirm-stopped and --confirm-trusted-source are required")
    source = prepare.safe_dir(args.source_transaction, "source transaction")
    release = prepare.safe_dir(args.old_release, "old release")
    destination = Path(args.new_data_dir)
    if (not destination.is_absolute() or destination == destination.parent or
            destination.name in {"", ".", ".."} or destination.exists() or destination.is_symlink()):
        raise ValueError("Restore destination must be a new absolute directory")
    parent = prepare.safe_dir(str(destination.parent), "destination parent")
    if os.name != "nt":
        info = parent.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("Destination parent must be owned by the current user and mode 0700")
    if (source / ".COPY-COMPLETE").is_file() and not (source / ".COPY-COMPLETE").is_symlink():
        if (source / ".COPY-INCOMPLETE").exists() or (source / ".COPY-INCOMPLETE").is_symlink():
            raise ValueError("Copy has an incomplete marker")
        receipt = _json_file(source / "receipt.json")
        state = _json_file(source / "source-state.json")
        if (receipt.get("format") != "minimax-upgrade-backup-copy-v1" or receipt.get("scope") != "data-only"
                or receipt.get("backup_sha256") != state.get("backup_sha256")):
            raise ValueError("Backup copy receipt does not match its source state")
    else:
        if (source / ".COPY-INCOMPLETE").exists() or (source / ".COPY-INCOMPLETE").is_symlink():
            raise ValueError("Incomplete copies cannot be restored")
        state = _json_file(source / "state.json")
    expected = state.get("backup_sha256")
    if (state.get("status") != "backup-ready" or not isinstance(expected, str)
            or not re.fullmatch(r"[0-9a-f]{64}", expected)):
        raise ValueError("A completed manual upgrade backup with a digest is required")
    recorded = state.get("old_release")
    actual = prepare.identity(release)
    if (not isinstance(recorded, dict) or recorded.get("commit") != actual["commit"]
            or recorded.get("version_label") != actual["version_label"]):
        raise ValueError("Old release identity does not match the backup record")
    original_data = Path(state.get("data_dir", ""))
    if not original_data.is_absolute():
        raise ValueError("Recorded original data path is not absolute on this platform")
    recorded_new = state.get("new_release")
    if not isinstance(recorded_new, dict) or not isinstance(recorded_new.get("path"), str):
        raise ValueError("Recorded new release path is missing")
    new_release = Path(recorded_new["path"])
    if not new_release.is_absolute():
        raise ValueError("Recorded new release path is not absolute on this platform")
    for protected in (source, release, original_data.resolve(), new_release.resolve()):
        if prepare.overlapping(destination, protected):
            raise ValueError("New data directory must not overlap backup, old release or original data")
    python = Path(args.old_python)
    if (not python.is_absolute() or not python.is_file()
            or release not in (python.parent.resolve() / python.name).parents):
        raise ValueError("Python must belong to the old release environment")
    if not (release / "agent" / "minimax_code" / "bundle.py").is_file():
        raise ValueError("Old release bundle implementation is missing")

    stage = Path(tempfile.mkdtemp(prefix=".minimax-restore-", dir=parent))
    staged = stage / "data-vault.zip"
    stage_identity = stage.stat()
    print(f"Restore staging: {stage}", flush=True)
    try:
        bundle, before = copy.open_regular(source / "data-vault.zip")
        if before.st_size > 2 * 1024 * 1024 * 1024:
            bundle.close()
            raise ValueError("Backup exceeds the 2 GiB staging limit")
        digest, size = hashlib.sha256(), 0
        with bundle, staged.open("xb") as output:
            while chunk := bundle.read(1024 * 1024):
                output.write(chunk)
                digest.update(chunk)
                size += len(chunk)
                if size > 2 * 1024 * 1024 * 1024:
                    raise ValueError("Backup exceeded the 2 GiB staging limit")
            after = os.fstat(bundle.fileno())
            output.flush()
            os.fsync(output.fileno())
            staged_identity = os.fstat(output.fileno())
        if ((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) !=
                (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or size != before.st_size or digest.hexdigest() != expected):
            raise ValueError("Backup changed or does not match the recorded digest")
        verified = prepare.bundle_command(python, release / "agent", "verify", "--bundle", str(staged))
        if (verified.get("schema_version") != state.get("schema_version") or
                verified.get("format_version") != state.get("format_version") or
                prepare.file_sha256(staged) != expected):
            raise ValueError("Verified backup differs from the recorded format, schema or digest")
        restored = prepare.bundle_command(python, release / "agent", "restore", "--bundle", str(staged),
                                          "--new-data-dir", str(destination))
        if (restored.get("schema_version") != state.get("schema_version") or
                restored.get("format_version") != state.get("format_version") or
                prepare.file_sha256(staged) != expected):
            raise RuntimeError("Restore receipt or staged backup changed; inspect destination before use")
        # Remove only the exact staging file and directory that this invocation owns.
        current = stage.lstat()
        if ((current.st_dev, current.st_ino) != (stage_identity.st_dev, stage_identity.st_ino)
                or not stat.S_ISDIR(current.st_mode) or staged.is_symlink() or not staged.is_file()
                or (staged.lstat().st_dev, staged.lstat().st_ino) !=
                   (staged_identity.st_dev, staged_identity.st_ino) or staged.lstat().st_nlink != 1):
            raise RuntimeError("Staging identity changed; preserve it for inspection")
        staged.unlink()
        stage.rmdir()
        return destination
    except BaseException:
        print(f"Restore not confirmed; inspect destination and retained staging directory {stage}; do not start it.", file=sys.stderr)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-transaction", "old-release", "old-python", "new-data-dir"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--confirm-stopped", action="store_true")
    parser.add_argument("--confirm-trusted-source", action="store_true")
    args = parser.parse_args()
    try:
        target = restore(args)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, KeyboardInterrupt) as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        return 2
    print(f"Restored into {target}. No service was started or switched.")
    print("Review exclusions, external effects, and release compatibility before any startup; hash and manifest do not authenticate the source.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
