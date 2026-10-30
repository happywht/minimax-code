#!/usr/bin/env python3
"""Restore a manual or Linux user-service upgrade backup with the old release.

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
evidence = _load("service_evidence", "service-upgrade-evidence.py")


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
    if getattr(args, "linux_service_transaction", False):
        if sys.platform != "linux":
            raise ValueError("Linux service transactions must be restored on Linux")
        if not getattr(args, "confirm_old_unit", False):
            raise ValueError("Review old.service and explicitly supply --confirm-old-unit")
        state = _json_file(source / "state.json")
        # Earlier service transactions contain neither a backup digest nor a
        # release manifest identity. Refuse early phases and require the operator
        # to identify and trust the exact backup and old unit independently.
        if state.get("state") not in {"stopped", "backup-verified", "switching-unit", "unit-switched",
                                      "starting", "healthy", "failed-before-switch", "failed-during-or-after-switch"}:
            raise ValueError("The transaction did not reach a potentially usable stopped backup")
        unit, unit_info = copy.open_regular(source / "old.service")
        with unit:
            unit_bytes = unit.read(65537)
            unit_after = os.fstat(unit.fileno())
        if (not unit_bytes or len(unit_bytes) > 65536 or len(unit_bytes) != unit_info.st_size
                or (unit_info.st_dev, unit_info.st_ino, unit_info.st_size, unit_info.st_mtime_ns) !=
                   (unit_after.st_dev, unit_after.st_ino, unit_after.st_size, unit_after.st_mtime_ns)):
            raise ValueError("Recorded old unit is missing, oversized or changed")
        recorded_old = state.get("old_release")
        if not isinstance(recorded_old, str) or not Path(recorded_old).is_absolute() or Path(recorded_old) != release:
            raise ValueError("Old release path differs from the Linux transaction")
        saved_evidence = state.get("backup_evidence")
        recorded_source = None
        expected_size = None
        if "backup_evidence" in state:
            if (not isinstance(saved_evidence, dict) or saved_evidence.get("version") != 1
                    or not isinstance(saved_evidence.get("backup_sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", saved_evidence["backup_sha256"])
                    or type(saved_evidence.get("bytes")) is not int
                    or not 0 < saved_evidence["bytes"] <= 2 * 1024 * 1024 * 1024
                    or type(saved_evidence.get("schema_version")) is not int
                    or type(saved_evidence.get("format_version")) is not int
                    or not isinstance(saved_evidence.get("old_source"), dict)):
                raise ValueError("Recorded backup evidence is invalid")
            expected = saved_evidence["backup_sha256"]
            expected_schema, expected_format = saved_evidence["schema_version"], saved_evidence["format_version"]
            expected_size = saved_evidence["bytes"]
            recorded_source = saved_evidence["old_source"]
            if getattr(args, "expected_sha256", None) is not None and args.expected_sha256 != expected:
                raise ValueError("Supplied digest differs from recorded backup evidence")
        else:
            if not isinstance(getattr(args, "expected_sha256", None), str) or not re.fullmatch(r"[0-9a-f]{64}", args.expected_sha256):
                raise ValueError("Legacy Linux transactions require independently recorded --expected-sha256")
            expected = args.expected_sha256
            expected_schema, expected_format = None, None
        data_value = state.get("data")
        if not isinstance(data_value, str):
            raise ValueError("Recorded original data path is missing")
        original_data = Path(data_value)
        new_value = state.get("new_release")
        if not isinstance(new_value, str):
            raise ValueError("Recorded new release path is missing")
        new_release = Path(new_value)
    else:
        recorded_source = None
        expected_size = None
        if getattr(args, "expected_sha256", None) is not None or getattr(args, "confirm_old_unit", False):
            raise ValueError("Linux transaction flags require --linux-service-transaction")
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
        data_value = state.get("data_dir")
        if not isinstance(data_value, str):
            raise ValueError("Recorded original data path is missing")
        original_data = Path(data_value)
        recorded_new = state.get("new_release")
        if not isinstance(recorded_new, dict) or not isinstance(recorded_new.get("path"), str):
            raise ValueError("Recorded new release path is missing")
        new_release = Path(recorded_new["path"])
        expected_schema, expected_format = state.get("schema_version"), state.get("format_version")
        if type(expected_schema) is not int or type(expected_format) is not int:
            raise ValueError("Backup schema and format must be recorded")
    if not original_data.is_absolute() or not new_release.is_absolute():
        raise ValueError("Recorded data and new release paths must be absolute on this platform")
    for protected in (source, release, original_data.resolve(), new_release.resolve()):
        if prepare.overlapping(destination, protected):
            raise ValueError("New data directory must not overlap backup, old release or original data")
    python = Path(args.old_python)
    if (not python.is_absolute() or not python.is_file()
            or release not in (python.parent.resolve() / python.name).parents):
        raise ValueError("Python must belong to the old release environment")
    if not (release / "agent" / "minimax_code" / "bundle.py").is_file():
        raise ValueError("Old release bundle implementation is missing")
    if recorded_source is not None and evidence.source_identity(release, python) != recorded_source:
        raise ValueError("Old release source or Python path differs from recorded backup evidence")

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
                or size != before.st_size or (expected_size is not None and size != expected_size)
                or digest.hexdigest() != expected):
            raise ValueError("Backup changed or does not match the recorded digest")
        verified = prepare.bundle_command(python, release / "agent", "verify", "--bundle", str(staged))
        if (type(verified.get("schema_version")) is not int or type(verified.get("format_version")) is not int
                or (expected_schema is not None and verified["schema_version"] != expected_schema)
                or (expected_format is not None and verified["format_version"] != expected_format)
                or prepare.file_sha256(staged) != expected
                or (recorded_source is not None and evidence.source_identity(release, python) != recorded_source)):
            raise ValueError("Verified backup differs from the recorded format, schema or digest")
        restored = prepare.bundle_command(python, release / "agent", "restore", "--bundle", str(staged),
                                          "--new-data-dir", str(destination))
        if (restored.get("schema_version") != verified["schema_version"] or
                restored.get("format_version") != verified["format_version"] or
                prepare.file_sha256(staged) != expected or
                (recorded_source is not None and evidence.source_identity(release, python) != recorded_source)):
            raise RuntimeError("Restore receipt, source or staged backup changed; inspect destination before use")
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
    parser.add_argument("--linux-service-transaction", action="store_true", help="restore a Linux user-service upgrade transaction")
    parser.add_argument("--expected-sha256", help="independently recorded SHA-256; required for legacy Linux transactions")
    parser.add_argument("--confirm-old-unit", action="store_true", help="operator reviewed old.service and selected its matching old release and Python")
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
