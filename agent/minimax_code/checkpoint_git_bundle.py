"""Offline, bounded capture and isolated validation of checkpoint Git objects."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
import tempfile
import threading
import time
from pathlib import Path

from .text_artifacts import _identity, _regular, _safe_open

TIMEOUT = 60
OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")
MAX_OBJECTS = 1_000_000


def _env(home: str) -> dict[str, str]:
    allowed = {"PATH", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE"}
    env = {key: value for key, value in os.environ.items() if key.upper() in allowed}
    env.update({"HOME": home, "TMP": home, "TEMP": home, "LC_ALL": "C",
                "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
                "GIT_TERMINAL_PROMPT": "0", "GIT_NO_REPLACE_OBJECTS": "1",
                "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": "",
                "GIT_CONFIG_COUNT": "2", "GIT_CONFIG_KEY_0": "core.hooksPath",
                "GIT_CONFIG_VALUE_0": os.devnull, "GIT_CONFIG_KEY_1": "core.fsmonitor",
                "GIT_CONFIG_VALUE_1": "false"})
    return env


def _validate_object_store(common: Path) -> None:
    """Reject links, alternates and partial stores before invoking Git."""
    objects = common / "objects"
    with _safe_open(objects, directory=True):
        count = 0
        deadline = time.monotonic() + TIMEOUT
        def fail(exc):
            raise ValueError("Cannot inspect Git object storage") from exc
        for directory, subdirs, files in os.walk(objects, followlinks=False, onerror=fail):
            for name in (*subdirs, *files):
                count += 1
                if count > MAX_OBJECTS * 2 or time.monotonic() > deadline:
                    raise ValueError("Git object storage exceeds inspection limit")
                info = (Path(directory) / name).lstat()
                if (stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400
                        or not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode))
                        or stat.S_ISREG(info.st_mode) and info.st_nlink != 1):
                    raise ValueError("Git object storage contains links")
        if ((common / "shallow").exists() or (objects / "info" / "alternates").exists()
                or any((objects / "pack").glob("*.promisor"))):
            raise ValueError("Shallow, alternate, or partial Git object storage is unsupported")


def _git(args, *, cwd: Path, env, stdin=None, output=False):
    try:
        result = subprocess.run(["git", "--no-optional-locks", *args], cwd=cwd, env=env,
                                input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE if output else subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, timeout=TIMEOUT, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError("Git command unavailable or timed out") from exc
    if result.returncode != 0 or (output and len(result.stdout) > 4096):
        raise ValueError("Git could not verify checkpoint objects offline")
    return result.stdout if output else b""


def _run_pack(cwd: Path, env, oid: str, output, limit: int):
    try:
        proc = subprocess.Popen(["git", "--no-optional-locks", "pack-objects", "--threads=1", "--stdout", "--revs"],
                                cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise ValueError("Git pack command unavailable") from exc
    errors = []

    def read():
        total = 0
        try:
            while chunk := proc.stdout.read(65536):
                total += len(chunk)
                if total > limit:
                    errors.append("Git checkpoint pack exceeds bundle limit")
                    proc.kill()
                    return
                output.write(chunk)
        except (OSError, ValueError):
            errors.append("Git pack stream failed")
            proc.kill()

    worker = threading.Thread(target=read, daemon=True)
    worker.start()
    try:
        proc.stdin.write((oid + "\n").encode("ascii"))
        proc.stdin.close()
        proc.wait(timeout=TIMEOUT)
    except (OSError, subprocess.TimeoutExpired) as exc:
        proc.kill()
        proc.wait()
        raise ValueError("Git pack failed or timed out") from exc
    finally:
        worker.join(timeout=TIMEOUT)
        proc.stdout.close()
    if worker.is_alive() or errors or proc.returncode:
        raise ValueError(errors[0] if errors else "Git pack failed")


def _object_ids(args, *, cwd: Path, env) -> set[str]:
    try:
        proc = subprocess.Popen(["git", "--no-optional-locks", *args], cwd=cwd, env=env,
                                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL)
    except OSError as exc:
        raise ValueError("Git object enumeration failed") from exc
    timer = threading.Timer(TIMEOUT, proc.kill)
    timer.start()
    ids = set()
    try:
        for line in proc.stdout:
            if len(line) > 65 or not OID.fullmatch(line.decode("ascii").strip()):
                raise ValueError("Invalid Git object enumeration")
            ids.add(line.decode("ascii").strip())
            if len(ids) > MAX_OBJECTS:
                raise ValueError("Too many checkpoint Git objects")
        if proc.wait() != 0:
            raise ValueError("Git object enumeration failed or timed out")
    finally:
        timer.cancel()
        if proc.poll() is None:
            proc.kill()
            proc.wait()
        proc.stdout.close()
    return ids


def validate_pack(pack: Path, oid: str) -> str:
    if not OID.fullmatch(oid):
        raise ValueError("Invalid checkpoint Git object ID")
    with tempfile.TemporaryDirectory(prefix="checkpoint-git-verify-") as temp:
        root = Path(temp).absolute()
        env = _env(temp)
        _git(["init", "--bare", f"--object-format={'sha1' if len(oid) == 40 else 'sha256'}", "repo"], cwd=root, env=env)
        repo = root / "repo"
        try:
            with _safe_open(pack) as fd, os.fdopen(os.dup(fd), "rb") as source:
                subprocess.run(["git", "--no-optional-locks", "index-pack", "--stdin", "--strict"],
                               cwd=repo, env=env, stdin=source, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=TIMEOUT, check=True)
        except (OSError, subprocess.SubprocessError) as exc:
            raise ValueError("Invalid checkpoint Git pack") from exc
        if _git(["cat-file", "-t", oid], cwd=repo, env=env, output=True).strip() != b"commit":
            raise ValueError("Checkpoint Git object is not a commit")
        _git(["fsck", "--full", "--no-reflogs", oid], cwd=repo, env=env)
        reachable = _object_ids(["rev-list", "--objects", "--no-object-names", oid], cwd=repo, env=env)
        packed = _object_ids(["cat-file", "--batch-all-objects", "--batch-check=%(objectname)"], cwd=repo, env=env)
        if reachable != packed:
            raise ValueError("Checkpoint Git pack contains objects outside its closure")
        parent = _git(["rev-parse", "--verify", oid + "^1"], cwd=repo, env=env, output=True).decode().strip()
        _git(["rev-parse", "--verify", oid + "^2"], cwd=repo, env=env)
        return parent


def import_pack(pack: Path, oid: str, source: str, target: str, limit: int, expected: dict) -> str:
    """Import objects only after strict isolated validation and explicit caller confirmation.

    No refs, index or worktree files are intentionally modified. Failure or interruption
    may leave unreachable objects. External Git processes and GC are not locked out.
    """
    root = Path(target)
    if not root.is_absolute() or root.resolve() != root or str(root) != source:
        raise ValueError("Target does not match the recorded checkpoint repository")
    with tempfile.TemporaryDirectory(prefix="checkpoint-git-import-") as temp:
        temporary = Path(temp).absolute() / "snapshot.pack"
        with _safe_open(pack) as fd, temporary.open("xb") as out:
            before = os.fstat(fd)
            _regular(before)
            digest = hashlib.sha256()
            total = 0
            while chunk := os.read(fd, 65536):
                total += len(chunk)
                if total > limit:
                    raise ValueError("Checkpoint Git pack exceeds limit")
                digest.update(chunk)
                out.write(chunk)
            if (_identity(before) != _identity(os.fstat(fd))
                    or _identity(before)[:4] != _identity(pack.lstat())[:4]):
                raise ValueError("Checkpoint Git pack changed while reading")
        if total != expected["size_bytes"] or digest.hexdigest() != expected["sha256"]:
            raise ValueError("Checkpoint Git pack does not match its saved metadata")
        parent = validate_pack(temporary, oid)
        env = _env(temp)
        with _safe_open(root, directory=True) as root_fd:
            before = root.stat() if root_fd is None else os.fstat(root_fd)
            top = _git(["rev-parse", "--show-toplevel"], cwd=root, env=env, output=True).decode().strip()
            if Path(top).resolve() != root:
                raise ValueError("Target is not the recorded repository root")
            common = Path(_git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=root, env=env, output=True).decode().strip())
            with _safe_open(common, directory=True), _safe_open(common / "objects", directory=True):
                common_info = common.stat()
                identity = {"worktree_device": before.st_dev, "worktree_inode": before.st_ino,
                            "git_device": common_info.st_dev, "git_inode": common_info.st_ino}
                if identity != expected["repository"]:
                    raise ValueError("Target repository identity differs from the captured repository")
                _validate_object_store(common)
                probe = subprocess.run(["git", "config", "--get-regexp", r"^(extensions\.partialclone|remote\..*\.promisor)$"], cwd=root, env=env,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT, check=False)
                if probe.returncode != 1:
                    raise ValueError("Partial clone configuration is unsupported")
                head = _git(["rev-parse", "--verify", "HEAD"], cwd=root, env=env, output=True).decode().strip()
                if head != parent:
                    raise ValueError("Target HEAD does not match the checkpoint base commit")
                _git(["cat-file", "-e", parent + "^{commit}"], cwd=root, env=env)
                if _identity(before)[:4] != _identity(root.lstat())[:4]:
                    raise ValueError("Target repository changed before import")
                if _identity(common_info)[:4] != _identity(common.lstat())[:4]:
                    raise ValueError("Git common directory changed before import")
                try:
                    with temporary.open("rb") as incoming:
                        subprocess.run(["git", "--no-optional-locks", f"--git-dir={common}", "index-pack", "--stdin", "--strict"], cwd=root, env=env,
                                       stdin=incoming, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT, check=True)
                except (OSError, subprocess.SubprocessError) as exc:
                    raise ValueError("Git import failed; unreachable objects may remain") from exc
                _git(["cat-file", "-e", oid + "^{commit}"], cwd=root, env=env)
        return digest.hexdigest()


def capture_pack(source: str, oid: str, output, limit: int) -> dict[str, int]:
    if not isinstance(source, str) or not OID.fullmatch(oid):
        raise ValueError("Checkpoint Git source or object is unknown")
    root = Path(source)
    if not root.is_absolute() or root.resolve() != root or not root.is_dir():
        raise ValueError("Checkpoint repository path is unavailable or contains links")
    with tempfile.TemporaryDirectory(prefix="checkpoint-git-env-") as temp, _safe_open(root, directory=True) as root_fd:
        env = _env(temp)
        before = root.stat() if root_fd is None else os.fstat(root_fd)
        top = _git(["rev-parse", "--show-toplevel"], cwd=root, env=env, output=True).decode().strip()
        if Path(top).resolve() != root:
            raise ValueError("Checkpoint source is not the repository root")
        common = Path(_git(["rev-parse", "--path-format=absolute", "--git-common-dir"], cwd=root, env=env, output=True).decode().strip())
        with _safe_open(common, directory=True) as common_fd:
            common_before = common.stat() if common_fd is None else os.fstat(common_fd)
            _validate_object_store(common)
            try:
                probe = subprocess.run(["git", "config", "--get-regexp", r"^(extensions\.partialclone|remote\..*\.promisor)$"], cwd=root, env=env,
                                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=TIMEOUT, check=False)
            except (OSError, subprocess.TimeoutExpired) as exc:
                raise ValueError("Cannot inspect Git configuration") from exc
            if probe.returncode != 1:
                raise ValueError("Partial clone configuration is unsupported")
            if _git(["cat-file", "-t", oid], cwd=root, env=env, output=True).strip() != b"commit":
                raise ValueError("Checkpoint Git object is not a commit")
            _run_pack(root, env, oid, output, limit)
            if (_identity(before) != _identity(root.stat() if root_fd is None else os.fstat(root_fd))
                    or _identity(common_before) != _identity(common.stat() if common_fd is None else os.fstat(common_fd))
                    or _identity(before)[:4] != _identity(root.lstat())[:4]
                    or _identity(common_before)[:4] != _identity(common.lstat())[:4]):
                raise ValueError("Checkpoint repository changed while capturing")
            identity = {"worktree_device": before.st_dev, "worktree_inode": before.st_ino,
                        "git_device": common_before.st_dev, "git_inode": common_before.st_ino}
            if not identity["worktree_inode"] or not identity["git_inode"]:
                raise ValueError("Stable checkpoint repository identity is unavailable")
            return identity
