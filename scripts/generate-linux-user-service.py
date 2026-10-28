#!/usr/bin/env python3
"""Generate a systemd user unit for an already prepared MiniMax Code install.

Writes only to stdout. It does not install, enable, start, or modify a service.
"""
from __future__ import annotations

import argparse
import os
import stat
import sys
from pathlib import Path


def _absolute_existing(raw: str, kind: str) -> str:
    if not raw or any(ord(c) < 32 or ord(c) == 127 for c in raw) or not os.path.isabs(raw):
        raise ValueError(f"{kind} must be an absolute path without control characters")
    # Preserve the venv interpreter symlink: resolving it would discard its environment.
    path = Path(os.path.abspath(raw))
    if kind == "python":
        if not path.is_file() or (os.name != "nt" and not os.access(path, os.X_OK)):
            raise ValueError("python must be an existing executable file")
    elif not path.is_dir():
        raise ValueError(f"{kind} must be an existing directory")
    if kind == "data" and os.name != "nt":
        info = path.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError("data directory must be owned by the current user and private (0700)")
    return str(path)


def _quote(value: str) -> str:
    # systemd expands specifiers even inside quotes. The first ExecStart
    # word is an executable path, where environment expansion is not applied.
    value = value.replace("%", "%%")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def generate(*, python: str, workspace: str, data: str, web_dist: str, port: int) -> str:
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    python = _absolute_existing(python, "python")
    workspace = _absolute_existing(workspace, "workspace")
    data = _absolute_existing(data, "data")
    web_dist = _absolute_existing(web_dist, "web_dist")
    # WorkingDirectory= is a path value, not a shell-style quoted word.
    # systemd strips surrounding whitespace, so refuse an ambiguous final space.
    if workspace[-1].isspace() or (os.name != "nt" and "\\" in workspace):
        raise ValueError("workspace path must not end in whitespace or contain a backslash")
    if not (Path(web_dist) / "index.html").is_file():
        raise ValueError("web_dist must contain a built index.html")
    environment = {
        "MINIMAX_CODE_DATA_DIR": data,
        "MINIMAX_CODE_WORKSPACE": workspace,
        "MINIMAX_CODE_WEB_DIST": web_dist,
        "MINIMAX_CODE_NO_DB": "0",
        "PYTHONUNBUFFERED": "1",
    }
    env_lines = "\n".join(f"Environment={_quote(f'{key}={value}')}" for key, value in environment.items())
    return f"""# Generated for an already prepared installation; do not put credentials in this unit.
[Unit]
Description=MiniMax Code user agent
After=network.target

[Service]
Type=simple
WorkingDirectory={workspace.replace('%', '%%')}
{env_lines}
ExecStart={_quote(python)} -m minimax_code --http --http-host 127.0.0.1 --http-port {port}
Restart=on-failure
RestartSec=5s
TimeoutStopSec=60s
KillMode=control-group
UMask=0077

[Install]
WantedBy=default.target
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", required=True, help="absolute path to installed venv Python")
    parser.add_argument("--workspace", required=True, help="existing default project directory")
    parser.add_argument("--data", required=True, help="existing private user data directory")
    parser.add_argument("--web-dist", required=True, help="existing built frontend directory")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    try:
        unit = generate(python=args.python, workspace=args.workspace, data=args.data,
                        web_dist=args.web_dist, port=args.port)
    except (OSError, ValueError) as exc:
        print(f"Cannot generate unit: {exc}", file=sys.stderr)
        return 2
    sys.stdout.write(unit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
