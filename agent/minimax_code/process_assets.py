"""Frozen reviewed policy bytes published outside every payload mount."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from .process_types import ProcessBoundaryError

SECCOMP_SHA256 = "41353e75c367b32482d0367ba5632ea6f27fba0b390b68418401549ab8cd545f"
ASSET_ROOT = Path(__file__).with_name("assets") / "linux_l1"


def governor_identity():
    from .pid1_guard import GUARD_KIND, GUARD_SHA256, GUARD_SOURCE
    if hashlib.sha256(GUARD_SOURCE.encode("utf-8")).hexdigest() != GUARD_SHA256:
        raise ProcessBoundaryError("Fixed PID1 guard source identity differs")
    policy_bytes()
    return {"kind": GUARD_KIND, "guard_sha256": GUARD_SHA256, "seccomp_sha256": SECCOMP_SHA256}


def policy_bytes():
    raw = (ASSET_ROOT / "seccomp-pid1-v1.json").read_bytes()
    if hashlib.sha256(raw).hexdigest() != SECCOMP_SHA256:
        raise ProcessBoundaryError("Frozen seccomp policy asset identity differs")
    return raw


def verify_private_policy(path: Path, *, uid: int):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != uid
            or stat.S_IMODE(info.st_mode) != 0o600 or info.st_size > 128 * 1024
            or hashlib.sha256(path.read_bytes()).hexdigest() != SECCOMP_SHA256):
        raise ProcessBoundaryError("Private seccomp policy ownership or bytes differ")
    return info.st_dev, info.st_ino


def install_private_policy(directory: Path, *, uid: int):
    from .process_installation import require_private_directory
    require_private_directory(directory, uid=uid)
    raw = policy_bytes()
    path = directory / ("seccomp-" + SECCOMP_SHA256 + ".json")
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        verify_private_policy(path, uid=uid)
        return path
    try:
        view = memoryview(raw)
        while view:
            written = os.write(fd, view)
            if not written:
                raise OSError("Incomplete seccomp policy publication")
            view = view[written:]
        os.fsync(fd)
    finally:
        os.close(fd)
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(parent)
    finally:
        os.close(parent)
    verify_private_policy(path, uid=uid)
    return path
