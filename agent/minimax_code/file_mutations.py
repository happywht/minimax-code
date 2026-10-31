"""Atomic replacement with a last-moment baseline check for native file tools."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

from .execution import require_write_access


# Stripes bound memory use while serializing all native mutations of the same
# path in this process. External writers do not participate in these locks.
_WRITE_LOCKS = tuple(threading.Lock() for _ in range(128))


def _file_identity(info: os.stat_result) -> tuple[int, ...]:
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, info.st_ctime_ns)


def _same_open_file(path_info: os.stat_result, handle_info: os.stat_result) -> bool:
    # Windows can report creation time for a path and change time for an open
    # handle as st_ctime. Compare ctime within each source, not across them.
    path_id = _file_identity(path_info)
    handle_id = _file_identity(handle_info)
    return path_id[:-1] == handle_id[:-1] if os.name == "nt" else path_id == handle_id


def _baseline_state(path: Path) -> tuple[str | None, tuple[int, ...] | None]:
    """Hash one regular file without following its final symlink.

    Verify the open descriptor and directory entry still refer to the same
    unchanged file after reading. This is a conflict check, not an OS CAS.
    """
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None, None
    if not stat.S_ISREG(before.st_mode):
        raise OSError("Baseline is not a regular file; re-read before writing")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags)
    with os.fdopen(fd, "rb") as source:
        opened = os.fstat(source.fileno())
        if not stat.S_ISREG(opened.st_mode) or not _same_open_file(before, opened):
            raise OSError("Baseline file was replaced; re-read before writing")
        digest = hashlib.file_digest(source, "sha256").hexdigest()
        after = os.fstat(source.fileno())
        path_after = path.lstat()
        if (_file_identity(opened) != _file_identity(after)
                or _file_identity(before) != _file_identity(path_after)
                or not _same_open_file(path_after, after)):
            raise OSError("Baseline file changed while reading; re-read before writing")
    return digest, _file_identity(path_after)


def _atomic_replace(target: Path, write: Callable[[BinaryIO], object], *, baseline_path: Path,
                    baseline_sha256: str | None, new_mode: Callable[[], int] | None = None) -> None:
    require_write_access()
    target = Path(target)
    lock_key = os.path.normcase(os.path.abspath(target))
    with _WRITE_LOCKS[hash(lock_key) % len(_WRITE_LOCKS)]:
        _atomic_replace_locked(target, write, baseline_path=baseline_path,
                               baseline_sha256=baseline_sha256, new_mode=new_mode)


def _atomic_replace_locked(target: Path, write: Callable[[BinaryIO], object], *,
                           baseline_path: Path, baseline_sha256: str | None,
                           new_mode: Callable[[], int] | None = None) -> None:
    parent = target.parent.resolve()
    if parent != target.parent or target.is_symlink():
        raise OSError("Write target changed to a symbolic link; re-read before writing")
    try:
        target_stat = target.lstat()
    except FileNotFoundError:
        target_stat = None
    if target_stat is not None and not stat.S_ISREG(target_stat.st_mode):
        raise OSError("Write target is not a regular file")
    target_existed = target_stat is not None
    mode = stat.S_IMODE(target_stat.st_mode) if target_stat is not None else None
    fd, temporary = tempfile.mkstemp(prefix=".minimax-write-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        current, baseline_identity = _baseline_state(baseline_path)
        if current != baseline_sha256:
            raise OSError("File changed during write preparation; re-read before retrying")
        if (baseline_path == target and target_stat is not None
                and baseline_identity != _file_identity(target_stat)):
            raise OSError("File identity or metadata changed during write preparation; re-read before retrying")
        if target.is_symlink() or target.parent.resolve() != parent:
            raise OSError("Write target changed during preparation")
        if mode is not None:
            os.chmod(temporary, mode)
        elif new_mode is not None:
            os.chmod(temporary, new_mode())
        if target_existed and baseline_path == target and baseline_sha256 is not None:
            os.replace(temporary, target)
        else:
            # A missing baseline or a different baseline means a new file
            # or an overlay's first write: the
            # destination was absent when the read view was chosen. Even if
            # it appeared before target_existed was sampled, do not replace
            # that other writer's mirror. Linking also protects new files
            # created after the target_existed check.
            os.link(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_write_text(target: Path, content: str, *, baseline_path: Path,
                      baseline_sha256: str | None) -> None:
    _atomic_replace(target, lambda stream: stream.write(content.encode("utf-8")),
                    baseline_path=baseline_path, baseline_sha256=baseline_sha256)


def atomic_create_bytes(target: Path, content: bytes) -> None:
    """Publish bytes only if the destination is still absent."""
    _atomic_replace(target, lambda stream: stream.write(content),
                    baseline_path=target, baseline_sha256=None)


def atomic_append_text(target: Path, content: str, *, baseline_path: Path,
                       baseline_sha256: str | None) -> None:
    def write(stream: BinaryIO) -> None:
        if baseline_sha256 is not None:
            before = baseline_path.lstat()
            if not stat.S_ISREG(before.st_mode):
                raise OSError("Baseline is not a regular file; re-read before writing")
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
            fd = os.open(baseline_path, flags)
            with os.fdopen(fd, "rb") as source:
                opened = os.fstat(source.fileno())
                if not _same_open_file(before, opened):
                    raise OSError("Baseline file was replaced; re-read before writing")
                shutil.copyfileobj(source, stream, 65536)
                after = os.fstat(source.fileno())
                path_after = baseline_path.lstat()
                if (_file_identity(opened) != _file_identity(after)
                        or _file_identity(before) != _file_identity(path_after)
                        or not _same_open_file(path_after, after)):
                    raise OSError("Baseline file changed while copying; re-read before writing")
        stream.write(content.encode("utf-8"))

    _atomic_replace(target, write, baseline_path=baseline_path, baseline_sha256=baseline_sha256)


def atomic_copy_file(source: Path, target: Path, *, baseline_sha256: str | None,
                     expected_source_sha256: str | None = None,
                     expected_owner_execute: bool | None = None) -> None:
    source_mode = 0

    def write(stream: BinaryIO) -> None:
        nonlocal source_mode
        with source.open("rb") as incoming:
            before = os.fstat(incoming.fileno())
            path_before = source.lstat() if expected_owner_execute is not None else None
            if not stat.S_ISREG(before.st_mode):
                raise OSError("Source is not a regular file")
            if path_before is not None and not _same_open_file(path_before, before):
                raise OSError("Source identity or permissions changed during collection")
            digest = hashlib.sha256()
            while chunk := incoming.read(65536):
                stream.write(chunk)
                digest.update(chunk)
            if expected_source_sha256 is not None and digest.hexdigest() != expected_source_sha256:
                raise OSError("Source changed during collection; preserve it for manual review")
            if expected_owner_execute is not None:
                after = os.fstat(incoming.fileno())
                path_after = source.lstat()
                if (_file_identity(before) != _file_identity(after)
                        or _file_identity(path_before) != _file_identity(path_after)
                        or not _same_open_file(path_after, after)):
                    raise OSError("Source identity or permissions changed during collection")
                if bool(before.st_mode & stat.S_IXUSR) != expected_owner_execute:
                    raise OSError("Source executable permission changed after completion")
                source_mode = before.st_mode

    _atomic_replace(target, write, baseline_path=target, baseline_sha256=baseline_sha256,
                    new_mode=(lambda: 0o600 | (source_mode & stat.S_IXUSR)) if expected_owner_execute is not None else None)
