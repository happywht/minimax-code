"""Bounded identities for Linux upgrade backup and restore, not signatures."""
from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path


def digest_file(path: Path, maximum: int) -> tuple[str, int]:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > maximum:
        raise ValueError(f"Expected bounded regular file: {path}")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
    with os.fdopen(fd, "rb") as stream:
        opened = os.fstat(stream.fileno())
        expected = (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if expected != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns):
            raise ValueError("Identity changed while opening")
        digest, size = hashlib.sha256(), 0
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            if size > maximum:
                raise ValueError("File exceeded size limit")
            digest.update(chunk)
        final = os.fstat(stream.fileno())
        if expected != (final.st_dev, final.st_ino, final.st_size, final.st_mtime_ns, final.st_ctime_ns) or size != info.st_size:
            raise ValueError("File changed while hashing")
    return digest.hexdigest(), size


def source_identity(release: Path, python: Path) -> dict:
    """Identify selected paths and selected source files, not the full environment."""
    bundle_hash, _ = digest_file(release / "agent" / "minimax_code" / "bundle.py", 32 * 1024 * 1024)
    project = release / "agent" / "pyproject.toml"
    project_hash, _ = digest_file(project, 4 * 1024 * 1024)
    import tomllib
    project_data = tomllib.loads(project.read_text(encoding="utf-8"))
    project_section = project_data.get("project")
    if not isinstance(project_section, dict):
        raise ValueError("Old release project metadata is invalid")
    version = project_section.get("version")
    if not isinstance(version, str) or not version or len(version) > 128:
        raise ValueError("Old release project version is unavailable")
    if digest_file(project, 4 * 1024 * 1024)[0] != project_hash:
        raise ValueError("Project metadata changed")
    marker = release / ".INSTALL-COMPLETE"
    manifest_path = release / "RELEASE-MANIFEST.json"
    manifest = None
    if marker.exists() or marker.is_symlink() or manifest_path.exists() or manifest_path.is_symlink():
        if marker.is_symlink() or not marker.is_file() or (release / ".INSTALL-INCOMPLETE").exists():
            raise ValueError("Old release is not marked complete")
        digest, _ = digest_file(manifest_path, 4 * 1024 * 1024)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if (not isinstance(manifest, dict) or manifest.get("format") != "minimax-code-release-v1"
                or not isinstance(manifest.get("version_label"), str)
                or not isinstance(manifest.get("commit"), str) or len(manifest["commit"]) != 40
                or any(c not in "0123456789abcdef" for c in manifest["commit"])):
            raise ValueError("Old release manifest is invalid")
        if digest_file(manifest_path, 4 * 1024 * 1024)[0] != digest:
            raise ValueError("Old release manifest changed")
        manifest = {"version_label": manifest["version_label"], "commit": manifest["commit"], "sha256": digest}
    return {"release_path": str(release), "python_path": str(python), "project_version": version,
            "pyproject_sha256": project_hash, "bundle_sha256": bundle_hash, "release_manifest": manifest}
