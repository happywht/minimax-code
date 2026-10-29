#!/usr/bin/env python3
"""Prepare a versioned code + prebuilt frontend ZIP without user data.

Tracked program files come from a specific Git commit. The explicitly supplied
frontend is an independent input and is not proven to match that commit. No
runtime, dependencies, credentials or user data are collected or installed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
MAX_FILE = 32 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
MAX_FILES = 10000
WEB_EXTENSIONS = {".html", ".js", ".css", ".svg", ".png", ".jpg", ".jpeg", ".webp", ".ico", ".woff", ".woff2", ".ttf"}
GIT_PATHS = ["package.json", "agent/pyproject.toml", "agent/uv.lock", "agent/README.md",
             "agent/minimax_code", "agent/skills", "scripts/start-production.mjs"]
OPTIONAL_PATHS = ["Start-MiniMax-Code.cmd", "scripts/generate-linux-user-service.py", "scripts/install-linux-user-service.py", "scripts/install-release.py"]


def git(*args: str) -> bytes:
    try:
        result = subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ValueError(f"Git unavailable or timed out: {exc}") from exc
    if result.returncode:
        raise ValueError(f"Git could not read the selected commit: {result.stderr.decode('utf-8', 'replace')[:500]}")
    return result.stdout


def permitted(name: str) -> bool:
    if name in set(GIT_PATHS + OPTIONAL_PATHS):
        return True
    if name.startswith("agent/minimax_code/"):
        suffix = PurePosixPath(name).suffix.lower()
        return suffix == ".py" or (name.startswith("agent/minimax_code/assets/") and
                                    (suffix in {".json", ".py"} or PurePosixPath(name).name in {"NOTICE", "COPYRIGHT", "LICENSE-APACHE-2.0"}))
    return name.startswith("agent/skills/") and PurePosixPath(name).name == "SKILL.md"


def prepare(commit: str, web: Path, output_dir: Path) -> Path:
    if commit != "HEAD" and not re.fullmatch(r"[0-9a-fA-F]{40}", commit):
        raise ValueError("commit must be HEAD or a full 40-character commit ID")
    sha = git("rev-parse", "--verify", f"{commit}^{{commit}}").decode("ascii").strip()
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise ValueError("Git returned an invalid commit ID")
    version = json.loads(git("show", f"{sha}:package.json"))["version"]
    python_version = tomllib.loads(git("show", f"{sha}:agent/pyproject.toml").decode("utf-8"))["project"]["version"]
    if not isinstance(version, str) or not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+(?:[-+][A-Za-z0-9.-]+)?", version) or version != python_version:
        raise ValueError("committed frontend and agent version labels differ or are invalid")
    if not web.is_absolute() or web.is_symlink() or not web.is_dir() or web.resolve() != web:
        raise ValueError("frontend directory must be an existing absolute path without symbolic links")
    if not (web / "index.html").is_file() or (web / "index.html").is_symlink():
        raise ValueError("frontend must contain a regular index.html")
    if not output_dir.is_absolute() or output_dir.is_symlink() or not output_dir.is_dir() or output_dir.resolve() != output_dir:
        raise ValueError("output directory must be an existing absolute path without symbolic links")
    target = output_dir / f"minimax-code-{version}-{sha[:12]}.zip"
    if target.exists() or target.is_symlink():
        raise ValueError(f"release already exists: {target}")
    paths = list(GIT_PATHS)
    tree = set(git("ls-tree", "-r", "--name-only", sha).decode("utf-8").splitlines())
    paths.extend(path for path in OPTIONAL_PATHS if path in tree)
    entries: dict[str, dict] = {}
    total = 0
    def write(archive: zipfile.ZipFile, name: str, data: bytes) -> None:
        nonlocal total
        if name in entries or len(data) > MAX_FILE or len(entries) >= MAX_FILES or total + len(data) > MAX_TOTAL:
            raise ValueError("duplicate path or release size/count limit exceeded")
        archive.writestr(name, data)
        entries[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        total += len(data)

    temp_name = None
    process = None
    try:
        with tempfile.NamedTemporaryFile(prefix=".minimax-release-", suffix=".tmp", dir=output_dir, delete=False) as temporary:
            temp_name = temporary.name
        with zipfile.ZipFile(temp_name, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            with tempfile.TemporaryFile() as errors:
                process = subprocess.Popen(["git", "-C", str(ROOT), "archive", "--format=tar", sha, "--", *paths], stdout=subprocess.PIPE, stderr=errors)
                try:
                    with tarfile.open(fileobj=process.stdout, mode="r|") as source:
                        for member in source:
                            name = member.name.rstrip("/")
                            if member.isdir():
                                continue
                            if not member.isfile() or member.size > MAX_FILE or not permitted(name) or PurePosixPath(name).is_absolute() or ".." in PurePosixPath(name).parts:
                                raise ValueError(f"unsupported committed release entry: {name}")
                            file = source.extractfile(member)
                            if file is None:
                                raise ValueError(f"cannot read committed release entry: {name}")
                            data = file.read(MAX_FILE + 1)
                            if len(data) != member.size:
                                raise ValueError(f"invalid committed release entry: {name}")
                            write(archive, name, data)
                    process.stdout.close()
                    if process.wait(timeout=60):
                        errors.seek(0)
                        raise ValueError(f"Git archive failed: {errors.read(500).decode('utf-8', 'replace')}")
                finally:
                    if process.poll() is None:
                        process.kill()
                        process.wait()
            for required in ("package.json", "agent/pyproject.toml", "agent/uv.lock", "agent/minimax_code/__init__.py", "agent/minimax_code/__main__.py", "scripts/start-production.mjs"):
                if required not in entries:
                    raise ValueError(f"required committed file is missing: {required}")
            for root, directories, files in os.walk(web, followlinks=False):
                root_path = Path(root)
                for directory in directories:
                    if (root_path / directory).is_symlink() or directory.startswith("."):
                        raise ValueError("frontend contains a symbolic or hidden directory")
                for filename in files:
                    path = root_path / filename
                    if filename.startswith(".") or path.suffix.lower() not in WEB_EXTENSIONS or path.is_symlink():
                        raise ValueError(f"unsupported frontend file: {path.relative_to(web)}")
                    before = path.lstat()
                    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE:
                        raise ValueError(f"invalid frontend file: {path.relative_to(web)}")
                    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0))
                    with os.fdopen(fd, "rb") as stream:
                        after = os.fstat(stream.fileno())
                        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                            raise ValueError(f"frontend file changed: {path.relative_to(web)}")
                        data = stream.read(MAX_FILE + 1)
                        finished = os.fstat(stream.fileno())
                    if (finished.st_size, finished.st_mtime_ns) != (before.st_size, before.st_mtime_ns):
                        raise ValueError(f"frontend file changed while reading: {path.relative_to(web)}")
                    if len(data) != before.st_size:
                        raise ValueError(f"frontend file changed: {path.relative_to(web)}")
                    write(archive, "web/dist/" + path.relative_to(web).as_posix(), data)
            manifest = {"format": "minimax-code-release-v1", "version_label": version, "commit": sha,
                        "frontend": "user-supplied prebuilt assets; not verified against commit",
                        "excluded": ["user data", "credentials", "runtime", "dependency environments and caches"],
                        "files": entries}
            archive.writestr("RELEASE-MANIFEST.json", json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
        # Hard link creates the final name atomically and never replaces a file.
        os.link(temp_name, target)
        try:
            os.unlink(temp_name)
        except OSError:
            print(f"Release created; temporary link could not be removed: {temp_name}", file=sys.stderr)
        temp_name = None
        return target
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.wait()
        if temp_name is not None:
            try:
                os.unlink(temp_name)
            except FileNotFoundError:
                pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--commit", default="HEAD", help="HEAD or full committed Git SHA")
    parser.add_argument("--web-dist", required=True, type=Path, help="absolute path to prepared frontend")
    parser.add_argument("--output-dir", required=True, type=Path, help="existing absolute output directory")
    args = parser.parse_args()
    try:
        result = prepare(args.commit, args.web_dist, args.output_dir)
        print(f"Prepared {result}; inspect manifest and prepare dependencies separately.")
        return 0
    except (OSError, ValueError, KeyError, UnicodeError, json.JSONDecodeError, tarfile.TarError, subprocess.TimeoutExpired) as exc:
        print(f"Release preparation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
