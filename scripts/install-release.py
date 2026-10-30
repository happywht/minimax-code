#!/usr/bin/env python3
"""Explicitly extract a prepared release into a new, isolated version directory.

The embedded hashes detect corruption; they are not a signature and cannot
establish who produced the package. No dependencies or services are modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import sys
import unicodedata
import zipfile
from pathlib import Path, PurePosixPath

MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_FILES = 10000
MAX_MANIFEST = 4 * 1024 * 1024
WEB_EXTENSIONS = {".html", ".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2", ".ttf"}
EXACT = {"package.json", "agent/pyproject.toml", "agent/uv.lock", "agent/README.md", "scripts/start-production.mjs",
         "Start-MiniMax-Code.cmd", "scripts/generate-linux-user-service.py", "scripts/install-linux-user-service.py", "scripts/install-release.py", "scripts/start-release.mjs", "scripts/start-release.ps1", "Start-Installed-Release.cmd", "scripts/diagnose-release.ps1", "Diagnose-Installed-Release.cmd", "scripts/prepare-release-upgrade.py", "scripts/copy-upgrade-backup.py", "scripts/restore-snapshot.ps1", "Restore-Snapshot.cmd", "scripts/copy-backup.ps1", "Copy-Backup.cmd", "scripts/data-vault-backup.ps1", "Data-Vault-Backup.cmd"}
REQUIRED = {"package.json", "agent/pyproject.toml", "agent/uv.lock", "agent/minimax_code/__init__.py",
            "agent/minimax_code/__main__.py", "scripts/start-production.mjs", "web/dist/index.html"}
RESERVED = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}


def safe_name(name: str) -> bool:
    if not name or name.startswith("/") or "\\" in name or "\x00" in name or len(name) > 1024:
        return False
    parts = name.split("/")
    for part in parts:
        if (not part or part in {".", ".."} or part.startswith(".") or part.endswith((".", " "))
                or any(ord(c) < 32 or ord(c) == 127 or c in '<>:"|?*' for c in part)
                or part.split(".", 1)[0].upper() in RESERVED):
            return False
    return not PurePosixPath(name).is_absolute()


def permitted(name: str) -> bool:
    if name in EXACT:
        return True
    if name.startswith("agent/minimax_code/"):
        suffix = PurePosixPath(name).suffix.lower()
        return suffix == ".py" or (name.startswith("agent/minimax_code/assets/") and
            (suffix in {".json", ".py"} or PurePosixPath(name).name in {"NOTICE", "COPYRIGHT", "LICENSE-APACHE-2.0"}))
    if name.startswith("agent/skills/"):
        return PurePosixPath(name).name == "SKILL.md"
    return name.startswith("web/dist/") and PurePosixPath(name).suffix.lower() in WEB_EXTENSIONS


def no_duplicates(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("manifest contains duplicate JSON keys")
        result[key] = value
    return result


def safe_file(info: zipfile.ZipInfo) -> bool:
    mode = (info.external_attr >> 16) & 0xFFFF
    kind = stat.S_IFMT(mode)
    return (not info.is_dir() and kind in {0, stat.S_IFREG} and not (info.flag_bits & 1)
            and info.compress_type in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED})


def verify_entry(archive: zipfile.ZipFile, info: zipfile.ZipInfo, expected: dict) -> None:
    if type(expected) is not dict or set(expected) != {"sha256", "bytes"} or type(expected["bytes"]) is not int:
        raise ValueError(f"invalid manifest record: {info.filename}")
    size, digest = expected["bytes"], expected["sha256"]
    if size < 0 or size > MAX_FILE or size != info.file_size or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError(f"invalid size or digest: {info.filename}")
    hasher, count = hashlib.sha256(), 0
    with archive.open(info) as source:
        while chunk := source.read(1024 * 1024):
            count += len(chunk)
            if count > size:
                raise ValueError(f"entry exceeds declared size: {info.filename}")
            hasher.update(chunk)
    if count != size or hasher.hexdigest() != digest:
        raise ValueError(f"entry digest mismatch: {info.filename}")


def install(source_path: Path, output_root: Path) -> Path:
    if not source_path.is_absolute() or source_path.is_symlink() or source_path.resolve() != source_path or not source_path.is_file():
        raise ValueError("archive must be an existing absolute regular path without symbolic links")
    if not output_root.is_absolute() or output_root.is_symlink() or output_root.resolve() != output_root or not output_root.is_dir():
        raise ValueError("destination parent must be an existing absolute directory without symbolic links")
    source_info = source_path.stat()
    if not stat.S_ISREG(source_info.st_mode) or source_info.st_size > MAX_TOTAL * 2:
        raise ValueError("archive is not a regular file or exceeds the archive size limit")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    fd = os.open(source_path, flags)
    destination = None
    try:
        with os.fdopen(fd, "rb") as stream, zipfile.ZipFile(stream) as archive:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns) != (source_info.st_dev, source_info.st_ino, source_info.st_size, source_info.st_mtime_ns):
                raise ValueError("archive changed before opening")
            infos = archive.infolist()
            if len(infos) > MAX_FILES + 1:
                raise ValueError("archive has too many entries")
            index = {}
            canonical = set()
            for info in infos:
                name = info.filename
                folded = unicodedata.normalize("NFC", name).casefold()
                if not safe_name(name) or not safe_file(info) or folded in canonical:
                    raise ValueError(f"unsafe, duplicate or unsupported archive entry: {name}")
                canonical.add(folded)
                index[name] = info
            for name in index:
                parts = name.split("/")
                for length in range(1, len(parts)):
                    if unicodedata.normalize("NFC", "/".join(parts[:length])).casefold() in canonical:
                        raise ValueError("archive contains file/directory path collision")
            info = index.pop("RELEASE-MANIFEST.json", None)
            if info is None or info.file_size > MAX_MANIFEST:
                raise ValueError("release manifest is missing or too large")
            manifest_bytes = archive.read(info)
            manifest = json.loads(manifest_bytes, object_pairs_hook=no_duplicates)
            if not isinstance(manifest, dict) or manifest.get("format") != "minimax-code-release-v1":
                raise ValueError("unsupported release manifest")
            version, commit, files = manifest.get("version_label"), manifest.get("commit"), manifest.get("files")
            if (not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version)
                    or not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit)
                    or not isinstance(files, dict) or set(files) != set(index) or not REQUIRED <= set(files)):
                raise ValueError("release identity or file list is invalid")
            if any(not permitted(name) for name in index):
                raise ValueError("archive contains files outside the release allowlist")
            total = 0
            for name, entry in index.items():
                verify_entry(archive, entry, files[name])
                total += entry.file_size
                if total > MAX_TOTAL:
                    raise ValueError("release content exceeds size limit")
            end = os.fstat(stream.fileno())
            if (end.st_size, end.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
                raise ValueError("archive changed during verification")
            destination = output_root / f"minimax-code-{version}-{commit[:12]}"
            if not safe_name(destination.name):
                raise ValueError("release version cannot form a safe directory name")
            os.mkdir(destination, 0o700)  # exclusive: never replace an existing release
            marker = destination / ".INSTALL-INCOMPLETE"
            with marker.open("x", encoding="utf-8") as flag:
                flag.write("Installation in progress; do not run this release.\n")
            for name, entry in index.items():
                target = destination.joinpath(*name.split("/"))
                target.parent.mkdir(parents=True, exist_ok=True)
                for ancestor in (target.parent, *target.parent.parents):
                    if ancestor == destination.parent:
                        break
                    if ancestor.is_symlink():
                        raise ValueError("destination contains a symbolic link")
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
                out_fd = os.open(target, flags, 0o600)
                digest, count = hashlib.sha256(), 0
                with os.fdopen(out_fd, "wb") as output, archive.open(entry) as input_file:
                    while chunk := input_file.read(1024 * 1024):
                        count += len(chunk)
                        if count > files[name]["bytes"]:
                            raise ValueError(f"entry changed during extraction: {name}")
                        output.write(chunk)
                        digest.update(chunk)
                    output.flush()
                    os.fsync(output.fileno())
                if count != files[name]["bytes"] or digest.hexdigest() != files[name]["sha256"]:
                    raise ValueError(f"entry changed during extraction: {name}")
            with (destination / "RELEASE-MANIFEST.json").open("xb") as output:
                output.write(manifest_bytes)
                output.flush()
                os.fsync(output.fileno())
            end = os.fstat(stream.fileno())
            if (end.st_size, end.st_mtime_ns) != (opened.st_size, opened.st_mtime_ns):
                raise ValueError("archive changed during extraction")
            with marker.open("w", encoding="utf-8") as flag:
                flag.write("Release files extracted and checked against the package manifest; publisher and dependencies are not verified.\n")
                flag.flush()
                os.fsync(flag.fileno())
            os.rename(marker, destination / ".INSTALL-COMPLETE")
            return destination
    except Exception:
        if destination is not None:
            print(f"Installation incomplete; retained {destination}. Do not run it; inspect and remove it manually if appropriate.", file=sys.stderr)
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path, help="absolute path to prepared release ZIP")
    parser.add_argument("--destination-parent", required=True, type=Path, help="existing trusted absolute parent directory")
    args = parser.parse_args()
    try:
        path = install(args.archive, args.destination_parent)
        print(f"Installed release files in {path}. Prepare runtime and dependencies separately; no service was changed.")
        print("Manifest hashes detect corruption, not publisher identity or application readiness.")
        return 0
    except (OSError, ValueError, KeyError, UnicodeError, json.JSONDecodeError, zipfile.BadZipFile, EOFError, RuntimeError) as exc:
        print(f"Release installation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
