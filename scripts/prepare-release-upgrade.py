#!/usr/bin/env python3
"""Explicitly prepare a stopped-data backup before manually switching releases.

This does not stop or start a service, alter a release, migrate data, or switch
an active version. It uses the old release's own data-bundle implementation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def safe_dir(raw: str, label: str) -> Path:
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir() or path.resolve() != path:
        raise ValueError(f"{label} must be an existing absolute directory without symbolic links")
    return path


def overlapping(a: Path, b: Path) -> bool:
    return a == b or a in b.parents or b in a.parents


def identity(release: Path) -> dict:
    marker, manifest = release / ".INSTALL-COMPLETE", release / "RELEASE-MANIFEST.json"
    if marker.is_symlink() or not marker.is_file() or (release / ".INSTALL-INCOMPLETE").exists():
        raise ValueError(f"release is not marked as completely installed: {release}")
    if manifest.is_symlink() or not manifest.is_file() or manifest.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("release manifest missing or invalid")
    value = json.loads(manifest.read_text(encoding="utf-8"))
    if (not isinstance(value, dict) or value.get("format") != "minimax-code-release-v1"
            or not isinstance(value.get("version_label"), str)
            or not isinstance(value.get("commit"), str) or len(value["commit"]) != 40
            or any(char not in "0123456789abcdef" for char in value["commit"])):
        raise ValueError("release manifest identity is invalid")
    return {"version_label": value["version_label"], "commit": value["commit"], "path": str(release)}


def minimal_environment() -> dict[str, str]:
    allowed = {"PATH", "PATHEXT", "HOME", "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "SYSTEMROOT", "WINDIR", "TEMP", "TMP", "TMPDIR", "LANG", "LC_ALL", "LC_CTYPE"}
    return {key: value for key, value in os.environ.items() if key.upper() in allowed}


def bundle_command(python: Path, agent: Path, *args: str) -> dict:
    # Isolated interpreter with only this release's package path explicitly added.
    bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('minimax_code.bundle',run_name='__main__')"
    try:
        result = subprocess.run([str(python), "-I", "-c", bootstrap, str(agent), *args], cwd=agent,
                                env=minimal_environment(), capture_output=True, text=True, timeout=3600, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"old release bundle command unavailable or timed out: {exc}") from exc
    if result.returncode:
        raise RuntimeError(f"old release bundle {args[0]} failed: {result.stderr[:1000].strip()}")
    try:
        value = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError("old release bundle returned an invalid receipt") from exc
    if not isinstance(value, dict) or value.get("ok") is not True or value.get("operation") != args[0] or value.get("scope") != "data-only":
        raise RuntimeError("old release bundle returned an inconsistent receipt")
    return value


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def prepare(args) -> Path:
    if not args.confirm_stopped:
        raise ValueError("--confirm-stopped is required after stopping the service and quiescing tasks")
    old = safe_dir(args.old_release, "old release")
    new = safe_dir(args.new_release, "new release")
    data = safe_dir(args.data_dir, "data directory")
    backups = safe_dir(args.backup_dir, "backup directory")
    if any(overlapping(a, b) for i, a in enumerate((old, new, data, backups)) for b in (old, new, data, backups)[i + 1:]):
        raise ValueError("release, data and backup directories must not overlap")
    if os.name != "nt":
        info = backups.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("backup directory must belong to the current user and have mode 0700")
    old_id, new_id = identity(old), identity(new)
    if old_id["commit"] == new_id["commit"]:
        raise ValueError("old and new release commits must differ")
    python = Path(args.old_python)
    if not python.is_absolute() or not python.is_file() or not (old in (python.parent.resolve() / python.name).parents):
        raise ValueError("old Python executable must belong to the old release environment")
    if not (old / "agent" / "minimax_code" / "bundle.py").is_file():
        raise ValueError("old release does not include its bundle implementation")
    transaction = Path(tempfile.mkdtemp(prefix="minimax-upgrade-backup-", dir=backups))
    print(f"Backup transaction: {transaction}", flush=True)
    def state(status: str, **extra) -> None:
        content = {"status": status, "old_release": old_id, "new_release": new_id, "data_dir": str(data),
                   "updated_at": datetime.now(UTC).isoformat(), **extra}
        temp = transaction / "state.tmp"
        with temp.open("w", encoding="utf-8") as out:
            json.dump(content, out, ensure_ascii=True, indent=2)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp, transaction / "state.json")
    try:
        state("preparing")
        bundle = transaction / "data-vault.zip"
        created = bundle_command(python, old / "agent", "create", "--data-dir", str(data), "--output", str(bundle))
        state("created", schema_version=created.get("schema_version"))
        verified = bundle_command(python, old / "agent", "verify", "--bundle", str(bundle))
        if (type(created.get("schema_version")) is not int or type(created.get("format_version")) is not int
                or verified.get("schema_version") != created["schema_version"]
                or verified.get("format_version") != created["format_version"]):
            raise RuntimeError("created and verified bundle receipts disagree")
        state("backup-ready", schema_version=verified.get("schema_version"), format_version=verified.get("format_version"),
              backup_sha256=file_sha256(bundle), exclusions=verified.get("exclusions"), notice=verified.get("notice"))
        return transaction
    except BaseException:
        try:
            state("incomplete")
        except Exception:
            pass
        print(f"Backup did not complete; retained {transaction}. Do not switch releases based on it.", file=sys.stderr)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("old-release", "new-release", "old-python", "data-dir", "backup-dir"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--confirm-stopped", action="store_true", help="operator confirms service stopped and tasks quiesced")
    args = parser.parse_args()
    try:
        transaction = prepare(args)
        print(f"Backup ready: {transaction}. Review state.json and exclusions before any manual upgrade.")
        print("No service was started or switched; backup is not a complete machine image or reverse migration.")
        return 0
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError, KeyboardInterrupt) as exc:
        print(f"Cannot prepare upgrade backup: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
