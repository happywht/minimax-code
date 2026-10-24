"""SSH execution for remote Linux hosts (M5).

Thin async wrapper over the system ``ssh`` client. Design rules:

- **BatchMode** — never prompt; an unreachable host or a missing key
  fails fast with a structured error instead of hanging the loop.
- **ConnectTimeout** bounded (default 10s) so a dead machine is
  detected in seconds.
- The runner is *injectable* — tests swap it for a fake and assert the
  exact argv (host key policy, identity file, port) without a network.
"""

from __future__ import annotations

import asyncio
import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass
class SshResult:
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False


def build_ssh_args(
    *,
    hostname: str,
    username: str,
    port: int = 22,
    identity_file: str | None = None,
    connect_timeout: float = 10.0,
    remote_command: str | None = None,
) -> list[str]:
    """Assemble the ssh argv (unit-tested without any network)."""
    args = [
        "ssh",
        "-p", str(port),
        "-o", "BatchMode=yes",
        "-o", f"ConnectTimeout={int(connect_timeout)}",
        "-o", "StrictHostKeyChecking=accept-new",
    ]
    if identity_file:
        args += ["-i", str(identity_file)]
    target = f"{username}@{hostname}" if username else hostname
    args.append(target)
    if remote_command:
        args.append(remote_command)
    return args


async def run_ssh(
    host: Any,
    remote_command: str,
    *,
    timeout: float = 60.0,
    connect_timeout: float = 10.0,
    runner: Any = None,
) -> SshResult:
    """Execute ``remote_command`` on the host row's machine.

    ``runner`` overrides the process spawn for tests: it receives the
    argv list and returns ``(returncode, stdout_bytes, stderr_bytes)``.
    """
    argv = build_ssh_args(
        hostname=host["hostname"],
        username=host["username"],
        port=int(host.get("port") or 22),
        identity_file=host.get("identity_file") or None,
        connect_timeout=connect_timeout,
        remote_command=remote_command,
    )
    if runner is not None:
        code, out, err = await runner(argv)
        return SshResult(ok=code == 0, exit_code=code,
                         stdout=out.decode("utf-8", errors="replace") if isinstance(out, bytes) else str(out),
                         stderr=err.decode("utf-8", errors="replace") if isinstance(err, bytes) else str(err))
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return SshResult(ok=False, exit_code=None, stdout="",
                         stderr=f"ssh client not available: {exc}")
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        return SshResult(ok=False, exit_code=None, stdout="", stderr="",
                         timed_out=True)
    return SshResult(
        ok=proc.returncode == 0,
        exit_code=proc.returncode,
        stdout=out_b.decode("utf-8", errors="replace"),
        stderr=err_b.decode("utf-8", errors="replace"),
    )


async def probe_host(host: Any, *, runner: Any = None) -> dict[str, Any]:
    """Connection diagnosis + version handshake (M5: 连接诊断、版本握手).

    One round trip: OS line plus a marker, then the agent/codex/ssh
    versions when present. The probe result is what ``host.probe``
    persists.
    """
    marker = "minimax-host-probe"
    result = await run_ssh(
        host,
        f"uname -sr && echo {marker} && (python3 --version 2>&1 || true) "
        f"&& (codex --version 2>/dev/null || echo codex-missing)",
        timeout=30.0,
        runner=runner,
    )
    info: dict[str, Any] = {
        "ok": result.ok,
        "timed_out": result.timed_out,
        "agent_version": "",
        "os": "",
        "python_version": "",
    }
    if not result.ok:
        info["error"] = (result.stderr or result.stdout or "ssh failed")[:300]
        return info
    sections = result.stdout.split(marker)
    info["os"] = sections[0].strip()
    rest = [line.strip() for line in (sections[1].splitlines() if len(sections) > 1 else []) if line.strip()]
    if rest:
        info["python_version"] = rest[0]
    if len(rest) > 1:
        info["agent_version"] = rest[1]
        info["codex_present"] = rest[1] != "codex-missing"
    return info


def quote_remote(command: str) -> str:
    """Quote a local command for safe execution through an ssh layer."""
    return shlex.quote(command)


def resolve_identity_file(path: str | None) -> Path | None:
    if not path:
        return None
    return Path(path).expanduser()


__all__ = ["SshResult", "build_ssh_args", "run_ssh", "probe_host",
           "quote_remote", "resolve_identity_file"]
