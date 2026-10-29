#!/usr/bin/env python3
"""Prepare or explicitly install a new systemd user service for a prepared release.

By default only print the unit. --install creates a new file and reloads the
user manager; --enable and --start are independent explicit options. Existing
units and data are never replaced. Run on the Linux machine that owns the service.
"""
from __future__ import annotations

import argparse
import importlib.util
import os
import re
import stat
import subprocess
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("unit_generator", Path(__file__).with_name("generate-linux-user-service.py"))
generator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(generator)


def command(*args: str) -> str:
    try:
        result = subprocess.run(["systemctl", "--user", *args], capture_output=True, text=True,
                                timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"systemctl unavailable or timed out: {exc}") from exc
    if result.returncode:
        raise RuntimeError(f"systemctl {' '.join(args[:2])} failed ({result.returncode}): {result.stderr[:500].strip()}")
    return result.stdout


def properties(unit: str) -> dict[str, str]:
    output = command("show", unit, "--all", "--property=LoadState", "--property=FragmentPath",
                     "--property=ActiveState", "--property=UnitFileState", "--property=DropInPaths")
    values = dict(line.split("=", 1) for line in output.splitlines() if "=" in line)
    if not {"LoadState", "FragmentPath", "ActiveState", "UnitFileState", "DropInPaths"} <= values.keys():
        raise RuntimeError("systemd did not return the required unit properties")
    return values


def install(args, content: str) -> None:
    unit_dir = Path(args.unit_dir)
    if not unit_dir.is_absolute() or unit_dir.is_symlink() or not unit_dir.is_dir() or unit_dir.resolve() != unit_dir:
        raise ValueError("unit directory must be an existing absolute directory without symbolic links")
    info = unit_dir.stat()
    if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o022:
        raise ValueError("unit directory must belong to the current user and not be group/world writable")
    # Restrict installation to the user manager's principal configuration directory.
    config_home = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    expected = config_home / "systemd" / "user"
    if not expected.is_absolute() or unit_dir != expected:
        raise ValueError(f"unit directory must be the current user manager directory: {expected}")
    before = properties(args.unit)
    if (before["LoadState"] != "not-found" or before["FragmentPath"] or before["DropInPaths"]
            or before["ActiveState"] not in {"inactive", "failed"}
            or before["UnitFileState"] not in {"", "not-found"}):
        raise ValueError("a unit with this name already exists, is loaded, or has custom configuration")
    directory = os.open(unit_dir, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    created = False
    try:
        actual = os.fstat(directory)
        if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError("unit directory changed before installation")
        fd = os.open(args.unit, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
        created = True
        with os.fdopen(fd, "wb") as stream:
            stream.write(content.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.fsync(directory)
        current = unit_dir.stat()
        if (current.st_dev, current.st_ino) != (actual.st_dev, actual.st_ino):
            raise RuntimeError("unit directory changed after file creation")
        print(f"Created new unit: {unit_dir / args.unit}", flush=True)
        command("daemon-reload")
        print("User manager reloaded.", flush=True)
        loaded = properties(args.unit)
        if (loaded["LoadState"] != "loaded" or loaded["FragmentPath"] != str(unit_dir / args.unit)
                or loaded["DropInPaths"] or loaded["ActiveState"] not in {"inactive", "failed"}):
            raise RuntimeError("loaded unit does not match the new file or has unexpected state/drop-ins")
        if args.enable:
            command("enable", args.unit)
            print("Unit enabled for the user manager.", flush=True)
        if args.start:
            command("start", args.unit)
            state = properties(args.unit)
            print(f"Start requested; systemd state: {state['ActiveState']}. This does not establish application readiness.", flush=True)
            if state["ActiveState"] != "active":
                raise RuntimeError("service did not remain active; inspect systemd status and journal")
    except Exception:
        if created:
            print(f"Installation stopped after creating {unit_dir / args.unit}; file and any completed steps are retained. Inspect the unit and systemd state before changing anything.", file=sys.stderr)
        raise
    finally:
        os.close(directory)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--unit", default="minimax-code.service", help="new user unit name")
    parser.add_argument("--unit-dir", default=str(Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"))
    parser.add_argument("--python", required=True, help="absolute path to prepared venv Python")
    parser.add_argument("--workspace", required=True, help="existing default workspace")
    parser.add_argument("--data", required=True, help="existing private data directory")
    parser.add_argument("--web-dist", required=True, help="existing prepared frontend")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--install", action="store_true", help="create a new unit file and reload user manager")
    parser.add_argument("--enable", action="store_true", help="also enable the newly installed unit")
    parser.add_argument("--start", action="store_true", help="also start the newly installed unit")
    args = parser.parse_args()
    if not sys.platform.startswith("linux"):
        parser.error("this command requires Linux")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*\.service", args.unit):
        parser.error("unit must be a simple .service name")
    if (args.enable or args.start) and not args.install:
        parser.error("--enable and --start require --install")
    try:
        content = generator.generate(python=args.python, workspace=args.workspace, data=args.data,
                                     web_dist=args.web_dist, port=args.port)
        if args.install:
            print(f"Prepared Python: {args.python}; frontend: {args.web_dist}. Installed version is not verified.", flush=True)
            install(args, content)
        else:
            print(content, end="")
            print("Preview only: no file or service changes. Use --install explicitly after review.", file=sys.stderr)
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"Cannot prepare/install user service: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
