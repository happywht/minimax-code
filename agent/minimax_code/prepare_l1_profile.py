"""Prepare a disabled Linux L1 profile in an existing private data directory.

Run on the service host as its non-root user. This command does not inspect or
start Podman, enable the profile, pull images, or modify an existing file.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import stat
import sys
from pathlib import Path

from .execution_profiles import PODMAN_PROFILE, PROFILE_FILE
from .process_types import ExecutionTargetSpec, ProcessBoundaryError


def install_disabled_profile(data_dir: Path, image_id: str) -> Path:
    """Publish one complete disabled profile without overwriting an existing one."""
    if sys.platform != "linux" or os.getuid() == 0 or os.geteuid() != os.getuid():
        raise ProcessBoundaryError("Run on Linux as the non-root service user")
    if not data_dir.is_absolute():
        raise ProcessBoundaryError("Data directory must be an absolute path")
    # Validate the exact immutable image and the bundled governor policy bytes;
    # do not claim the image exists or that the runtime is available.
    target = ExecutionTargetSpec(profile_id=PODMAN_PROFILE, image_id=image_id)
    info = data_dir.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise ProcessBoundaryError("Data directory must be a real, service-owned 0700 directory")
    directory = os.open(data_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    temp = ".execution-profile-" + secrets.token_hex(16) + ".tmp"
    created = False
    try:
        opened = os.fstat(directory)
        if ((opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or opened.st_uid != os.getuid() or stat.S_IMODE(opened.st_mode) != 0o700):
            raise ProcessBoundaryError("Data directory identity or permissions changed")
        # Only configurable catalog fields belong in this document. The
        # immutable governor remains supplied by the existing runtime.
        document = {"version": 1, "profiles": [{"id": PODMAN_PROFILE,
            "enabled": False, "image_id": target.image_id}]}
        payload = (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o600, dir_fd=directory)
        created = True
        try:
            os.fchmod(fd, 0o600)
            view = memoryview(payload)
            while view:
                count = os.write(fd, view)
                if count <= 0:
                    raise OSError("Incomplete configuration write")
                view = view[count:]
            os.fsync(fd)
        finally:
            os.close(fd)
        current = data_dir.lstat()
        if ((current.st_dev, current.st_ino) != (opened.st_dev, opened.st_ino)
                or not stat.S_ISDIR(current.st_mode) or current.st_uid != os.getuid()
                or stat.S_IMODE(current.st_mode) != 0o700):
            raise ProcessBoundaryError("Data directory changed before publication")
        # A hard-link publication cannot replace an existing file or symlink.
        os.link(temp, PROFILE_FILE, src_dir_fd=directory, dst_dir_fd=directory,
                follow_symlinks=False)
        os.unlink(temp, dir_fd=directory)
        created = False
        os.fsync(directory)
    finally:
        if created:
            try:
                os.unlink(temp, dir_fd=directory)
            except FileNotFoundError:
                pass
        os.close(directory)
    return data_dir / PROFILE_FILE


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, help="existing absolute private service data directory")
    parser.add_argument("--image-id", required=True, help="full local image ID, sha256: plus 64 lowercase hex")
    args = parser.parse_args()
    try:
        result = install_disabled_profile(Path(args.data_dir), args.image_id)
    except (OSError, ValueError, TypeError, ProcessBoundaryError) as exc:
        print(f"Profile preparation failed: {exc}. If publication was interrupted, inspect the data directory before retrying.", file=sys.stderr)
        return 2
    print(f"Prepared disabled profile: {result}")
    print("No container was run; no image or runtime readiness was checked. Restart is needed to reload configuration.")
    print("L1 and unattended execution remain unavailable until their separate acceptance requirements are met.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
