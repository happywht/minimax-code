"""Lease-bound installation bootstrap with an immutable non-overwriting marker."""
from __future__ import annotations

import json
import os
import stat
import sys
import uuid
from dataclasses import fields
from pathlib import Path

from .process_types import InstallationIdentity, ProcessBoundaryError, canonical_hash

MARKER_NAME = ".process-installation.json"


def require_private_directory(path: Path, *, uid: int) -> Path:
    path = Path(path)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or path.is_symlink():
        raise ProcessBoundaryError("Execution directory must be a real private directory")
    if sys.platform == "linux" and (info.st_uid != uid or info.st_mode & 0o077):
        raise ProcessBoundaryError("Execution directory must belong to the service UID with mode 0700")
    return path.resolve(strict=True)


def _secure_file(path: Path, *, uid: int, links: int = 1):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != links:
        raise ProcessBoundaryError("Execution marker must be a regular file with its exact link count")
    if sys.platform == "linux" and (info.st_uid != uid or info.st_mode & 0o077):
        raise ProcessBoundaryError("Execution marker must be private and belong to the service UID")
    return info


def read_installation_marker(data_dir: Path, *, uid: int) -> dict | None:
    path = data_dir / MARKER_NAME
    try:
        info = _secure_file(path, uid=uid)
    except FileNotFoundError:
        return None
    if info.st_size > 8192:
        raise ProcessBoundaryError("Execution marker is oversized")
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        raise ProcessBoundaryError("Execution marker is unreadable or corrupt") from exc
    if not isinstance(result, dict):
        raise ProcessBoundaryError("Execution marker has an incompatible shape")
    return result


def _fsync_directory(path: Path):
    if sys.platform == "linux":
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _temporary_marker(data_dir, identity):
    return data_dir / (".process-installation-" + identity.bootstrap_nonce + ".tmp")


def _finish_pending_publication(data_dir: Path, identity: InstallationIdentity):
    """Recover the sole two-link window of our non-overwriting publication.

    The durable pending nonce identifies the exact private temporary file. No
    payload may exist in this phase. An unrelated link is never removed.
    """
    marker = data_dir / MARKER_NAME
    temporary = _temporary_marker(data_dir, identity)
    try:
        info = marker.lstat()
    except FileNotFoundError:
        return
    if info.st_nlink != 2:
        return
    _secure_file(marker, uid=identity.uid, links=2)
    temporary_info = _secure_file(temporary, uid=identity.uid, links=2)
    if (info.st_dev, info.st_ino) != (temporary_info.st_dev, temporary_info.st_ino):
        raise ProcessBoundaryError("Pending marker links do not match the bootstrap temporary file")
    if json.loads(marker.read_text()) != identity.marker():
        raise ProcessBoundaryError("Pending marker content differs from its durable identity")
    temporary.unlink()
    _fsync_directory(data_dir)


def publish_installation_marker(data_dir: Path, identity: InstallationIdentity):
    temporary = _temporary_marker(data_dir, identity)
    marker = data_dir / MARKER_NAME
    # A crash during the first write may leave a partial one-link private
    # temporary file. Pending/no resources is proved by bootstrap before here.
    if temporary.exists() or temporary.is_symlink():
        _secure_file(temporary, uid=identity.uid)
        temporary.unlink()
    raw = json.dumps(identity.marker(), sort_keys=True, separators=(",", ":"),
                     ensure_ascii=False, allow_nan=False).encode()
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            if count <= 0:
                raise OSError("Incomplete execution marker write")
            view = view[count:]
        os.fsync(fd)
    finally:
        os.close(fd)
    # link() is atomic and never replaces an existing marker. Keep unexpected
    # conflicts, including their temporary evidence, for explicit review.
    try:
        os.link(temporary, marker)
    except FileExistsError as exc:
        raise ProcessBoundaryError("Execution marker publication conflict; marker was not replaced") from exc
    temporary.unlink()
    _fsync_directory(data_dir)
    if read_installation_marker(data_dir, uid=identity.uid) != identity.marker():
        raise ProcessBoundaryError("Published execution marker readback differs")


def _from_row(row):
    return InstallationIdentity(**{item.name: row[item.name] for item in fields(InstallationIdentity)})


async def bootstrap_installation(dao, *, controller, data_dir: Path, lease) -> InstallationIdentity:
    """No implicit lease acquisition, no adoption after missing/corrupt identity."""
    data_dir = data_dir.resolve(strict=True)
    lease.assert_owned(data_dir, dao.db.path)
    runtime = await controller.info()
    require_private_directory(data_dir, uid=runtime["uid"])
    await dao.enable_durable_ownership()
    location = canonical_hash({"host": runtime["host_fingerprint"], "uid": runtime["uid"],
        "graph_root": runtime["graph_root"], "data_dir": str(data_dir)})
    directory = data_dir.stat()
    current = {"uid": runtime["uid"], "host_fingerprint": runtime["host_fingerprint"],
        "store_fingerprint": runtime["store_fingerprint"], "location_fingerprint": location,
        "data_dir": str(data_dir), "data_dir_device": str(directory.st_dev),
        "data_dir_inode": str(directory.st_ino)}
    row = await dao.installation()
    processes = await dao.db.fetchone("SELECT COUNT(*) AS n FROM process_executions")
    same_location = []
    for cid in await controller.inventory():
        inspected = await controller.inspect(cid)
        if inspected is None:
            raise ProcessBoundaryError("Runtime inventory changed during installation bootstrap")
        labels = (inspected.get("Config") or {}).get("Labels") or {}
        if (labels.get("ai.minimax-code.product") == "minimax-code"
                and labels.get("ai.minimax-code.location") == location):
            same_location.append(cid)
    if row is None:
        marker = read_installation_marker(data_dir, uid=runtime["uid"])
        if marker is not None or processes["n"] or same_location:
            raise ProcessBoundaryError("Installation evidence exists without its database identity; review required")
        identity = InstallationIdentity(install_id=uuid.uuid4().hex,
            bootstrap_nonce=uuid.uuid4().hex, **current)
        await dao.begin_installation(identity)
        row = await dao.installation()
    else:
        identity = _from_row(row)
        if any(getattr(identity, key) != value for key, value in current.items()):
            raise ProcessBoundaryError("Installation host, store, UID, or data directory identity changed")
        if row["marker_hash"] != identity.marker_hash:
            raise ProcessBoundaryError("Installation database marker hash is corrupt")
    if row["phase"] == "bootstrap_pending":
        if processes["n"] or same_location:
            raise ProcessBoundaryError("Pending installation unexpectedly has process resources")
        _finish_pending_publication(data_dir, identity)
        marker = read_installation_marker(data_dir, uid=identity.uid)
        if marker is None:
            publish_installation_marker(data_dir, identity)
        elif marker != identity.marker():
            raise ProcessBoundaryError("Pending installation marker mismatch")
        await dao.activate_installation(identity)
    elif row["phase"] == "active":
        if read_installation_marker(data_dir, uid=identity.uid) != identity.marker():
            raise ProcessBoundaryError("Active installation marker is missing or mismatched; never recreated")
    else:
        raise ProcessBoundaryError("Unknown installation bootstrap phase")
    await dao.assert_installation(identity)
    return identity
