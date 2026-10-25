"""Standalone stdlib helper executed on the remote host via SSH.

Stage next to the destination, serialize cooperating publishers, retain a backup,
and journal publication. No recursive cleanup and no following symbolic links.
This is file consistency, not an OS sandbox against hostile local processes.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def fingerprint(path):
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise ValueError("destination must be a regular file, not a link or directory")
    value = digest(path)
    after = path.lstat()
    def fields(s):
        return [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]

    if fields(before) != fields(after):
        raise ValueError("file changed while reading")
    return {"identity": fields(after), "sha256": value}


def sync_directory(path):
    # Windows cannot fsync directories this way. Production remote hosts are
    # Linux; the Windows branch exists for portable local fault-injection tests.
    if os.name != "nt":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def save(stage, value):
    # A process can die after writing the temporary journal. A unique name
    # prevents that orphan from blocking every later status/commit operation.
    fd, name = tempfile.mkstemp(prefix="manifest-", suffix=".next", dir=stage)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, stage / "manifest.json")
        sync_directory(stage)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def lock(parent, name, *, create=True):
    key = hashlib.sha256(name.encode()).hexdigest()[:24]
    path = parent / (".minimax-upload-lock-" + key)
    flags = (os.O_CREAT if create else 0) | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0)
    if path.is_symlink():
        raise ValueError("upload lock is a symbolic link")
    fd = os.open(path, flags, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("upload lock must be a regular file")
        if os.name == "nt":  # permits the same helper to be exercised in Windows tests
            import msvcrt
            if os.fstat(fd).st_size == 0:
                os.write(fd, b"0")
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def transact(request):
    target = Path(request["target"])
    transfer_id = request["transfer_id"]
    if (not target.is_absolute() or ".." in target.parts or not target.name
            or not re.fullmatch(r"[0-9a-f]{32}", transfer_id)):
        raise ValueError("absolute target and valid transfer id required")
    parent = target.parent.resolve(strict=True)
    action = request["action"]
    if parent != target.parent or (action in {"prepare", "commit"} and target.is_symlink()):
        raise ValueError("upload path must not traverse a symbolic link")
    stage = parent / (".minimax-upload-" + transfer_id)
    if action not in {"prepare", "commit", "status", "abort"}:
        raise ValueError("unknown upload action")
    if action == "prepare" and not re.fullmatch(r"[0-9a-f]{64}", request.get("sha256") or ""):
        raise ValueError("valid sha256 required")
    with lock(parent, target.name, create=action == "prepare"):
        newly_created = False
        if action == "prepare" and not stage.exists():
            baseline = fingerprint(target)
            stage.mkdir(mode=0o700)
            sync_directory(parent)
            save(stage, {"target": str(target), "baseline": baseline,
                         "state": "staged", "sha256": request["sha256"]})
            newly_created = True
        if stage.is_symlink() or not stage.is_dir():
            raise ValueError("upload staging directory is unavailable")
        manifest = stage / "manifest.json"
        if manifest.is_symlink():
            raise ValueError("upload manifest is a symbolic link")
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if data["target"] != str(target):
            raise ValueError("transfer belongs to another destination")
        if action == "prepare" and data["sha256"] != request["sha256"]:
            raise ValueError("transfer id already belongs to different content")
        payload, backup = stage / "payload", stage / "previous"
        # An SSH disconnect may occur after replacement but before its receipt.
        if data["state"] == "publishing":
            try:
                current = fingerprint(target)
            except (OSError, ValueError):
                # An unsafe or unreadable current destination cannot prove a
                # publication; its link is never followed. Keep the backup and
                # uncertain journal available to status callers.
                current = None
            # Content equality alone cannot attribute another writer's file to
            # this transfer. Rename preserves the staged file's inode/mtime,
            # while ctime may change on POSIX rename.
            publication = data.get("publication")
            if (not payload.exists() and current and publication
                    and current["identity"][:4] == publication["identity"][:4]
                    and current["sha256"] == publication["sha256"]):
                data["state"] = "published"
                if action != "status":
                    save(stage, data)
        if action == "commit" and data["state"] == "staged":
            if payload.is_symlink() or not payload.is_file() or digest(payload) != data["sha256"]:
                raise ValueError("uploaded checksum mismatch; destination preserved")
            if fingerprint(target) != data["baseline"]:
                raise ValueError("destination changed during upload; destination preserved")
            if data["baseline"] is not None:
                with target.open("rb") as src, backup.open("xb") as dst:
                    shutil.copyfileobj(src, dst)
                    dst.flush()
                    os.fsync(dst.fileno())
                if digest(backup) != data["baseline"]["sha256"]:
                    raise ValueError("destination changed while backing up")
                data["backup_verified"] = True
            incoming_mode = stat.S_IMODE(payload.stat().st_mode)
            final_mode = (stat.S_IMODE(target.stat().st_mode)
                          if data["baseline"] is not None else incoming_mode)
            # Linux permits fsync on a read-only fd, while Windows needs a
            # writable fd. SCP can preserve a read-only source's mode. Only the
            # owned private payload temporarily gains owner-write on Windows.
            if os.name == "nt":
                os.chmod(payload, incoming_mode | stat.S_IWUSR)
            try:
                with payload.open("r+b" if os.name == "nt" else "rb") as stream:
                    os.chmod(payload, final_mode)
                    os.fsync(stream.fileno())
            finally:
                os.chmod(payload, final_mode)
            publication = fingerprint(payload)
            if publication["sha256"] != data["sha256"]:
                raise ValueError("uploaded checksum changed before publication")
            if fingerprint(target) != data["baseline"] or target.is_symlink() or target.parent.resolve() != parent:
                raise ValueError("destination changed before publication")
            data["state"] = "publishing"
            data["publication"] = publication
            save(stage, data)
            os.replace(payload, target)
            sync_directory(parent)
            sync_directory(stage)
            data["state"] = "published"
            save(stage, data)
        if action == "abort" and data["state"] == "staged":
            # Only owned staging files are removed. Never delete a destination,
            # backup or an uncertain/committed publication.
            if payload.exists() or payload.is_symlink():
                if os.name == "nt" and not payload.is_symlink():
                    os.chmod(payload, stat.S_IMODE(payload.stat().st_mode) | stat.S_IWUSR)
                payload.unlink()
            data["state"] = "aborted"
            save(stage, data)
        return {"state": data["state"], "transfer_id": transfer_id,
                "staged_path": str(payload), "sha256": data["sha256"],
                "newly_created": newly_created,
                "backup_path": str(backup) if data.get("backup_verified") and backup.is_file() else None}


if __name__ == "__main__":
    import sys
    try:
        print(json.dumps(transact(json.loads(sys.argv[1]))))
    except Exception as exc:
        print(json.dumps({"error": str(exc)}))
        sys.exit(1)
