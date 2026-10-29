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


def _atomic_replace(target: Path, write: Callable[[BinaryIO], object], *, baseline_path: Path,
                    baseline_sha256: str | None) -> None:
    require_write_access()
    target = Path(target)
    lock_key = os.path.normcase(os.path.abspath(target))
    with _WRITE_LOCKS[hash(lock_key) % len(_WRITE_LOCKS)]:
        _atomic_replace_locked(target, write, baseline_path=baseline_path,
                               baseline_sha256=baseline_sha256)


def _atomic_replace_locked(target: Path, write: Callable[[BinaryIO], object], *,
                           baseline_path: Path, baseline_sha256: str | None) -> None:
    parent = target.parent.resolve()
    if parent != target.parent or target.is_symlink():
        raise OSError("Write target changed to a symbolic link; re-read before writing")
    target_existed = target.exists()
    mode = stat.S_IMODE(target.stat().st_mode) if target_existed else None
    fd, temporary = tempfile.mkstemp(prefix=".minimax-write-", dir=parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            write(stream)
            stream.flush()
            os.fsync(stream.fileno())
        current = None
        if baseline_path.exists():
            with baseline_path.open("rb") as source:
                current = hashlib.file_digest(source, "sha256").hexdigest()
        if current != baseline_sha256:
            raise OSError("File changed during write preparation; re-read before retrying")
        if target.is_symlink() or target.parent.resolve() != parent:
            raise OSError("Write target changed during preparation")
        if mode is not None:
            os.chmod(temporary, mode)
        if target_existed and baseline_path == target:
            os.replace(temporary, target)
        else:
            # A different baseline means an overlay's first write: the
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


def atomic_append_text(target: Path, content: str, *, baseline_path: Path,
                       baseline_sha256: str | None) -> None:
    def write(stream: BinaryIO) -> None:
        if baseline_sha256 is not None:
            with baseline_path.open("rb") as source:
                shutil.copyfileobj(source, stream, 65536)
        stream.write(content.encode("utf-8"))

    _atomic_replace(target, write, baseline_path=baseline_path, baseline_sha256=baseline_sha256)


def atomic_copy_file(source: Path, target: Path, *, baseline_sha256: str | None,
                     expected_source_sha256: str | None = None) -> None:
    def write(stream: BinaryIO) -> None:
        with source.open("rb") as incoming:
            digest = hashlib.sha256()
            while chunk := incoming.read(65536):
                stream.write(chunk)
                digest.update(chunk)
            if expected_source_sha256 is not None and digest.hexdigest() != expected_source_sha256:
                raise OSError("Source changed during collection; preserve it for manual review")

    _atomic_replace(target, write, baseline_path=target, baseline_sha256=baseline_sha256)
