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


async def push_file(
    host: Any,
    local_path: Path,
    remote_path: str,
    *,
    timeout: float = 300.0,
    runner: Any = None,
) -> SshResult:
    """Upload one file via scp, then verify sha256 on both ends (M5)."""
    argv = _scp_args(host, str(local_path), f"{_target(host)}:{remote_path}")
    result = await _run_scp(argv, timeout=timeout, runner=runner)
    if not result.ok:
        return result
    local_sha = _sha256_file(local_path)
    remote_sha = await remote_sha256(host, remote_path, runner=runner)
    if remote_sha is None or remote_sha != local_sha:
        return SshResult(
            ok=False, exit_code=0, stdout=result.stdout,
            stderr=f"sha256 mismatch after upload: local={local_sha} remote={remote_sha}")
    return result


async def fetch_file(
    host: Any,
    remote_path: str,
    local_path: Path,
    *,
    timeout: float = 300.0,
    runner: Any = None,
) -> SshResult:
    """Download one remote file via scp, then verify sha256 on both ends."""
    argv = _scp_args(host, f"{_target(host)}:{remote_path}", str(local_path))
    result = await _run_scp(argv, timeout=timeout, runner=runner)
    if not result.ok:
        return result
    if not local_path.exists():
        return SshResult(ok=False, exit_code=0, stdout=result.stdout,
                         stderr="local file missing after download")
    local_sha = _sha256_file(local_path)
    remote_sha = await remote_sha256(host, remote_path, runner=runner)
    if remote_sha is None or remote_sha != local_sha:
        return SshResult(ok=False, exit_code=0, stdout=result.stdout,
                         stderr=f"sha256 mismatch after download: local={local_sha} remote={remote_sha}")
    return result


async def remote_sha256(host: Any, remote_path: str, *, runner: Any = None) -> str | None:
    """Remote ``sha256sum`` of one file, or None when it cannot be read."""
    result = await run_ssh(host, f"sha256sum {shlex.quote(remote_path)}",
                           timeout=30.0, runner=runner)
    if not result.ok:
        return None
    first = result.stdout.strip().split(" ", 1)
    return first[0] if first and first[0] else None


def _sha256_file(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _target(host: Any) -> str:
    username = host.get("username") or ""
    return f"{username}@{host['hostname']}" if username else str(host["hostname"])


def _scp_args(host: Any, source: str, dest: str) -> list[str]:
    args = ["scp", "-P", str(int(host.get("port") or 22)),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new"]
    if host.get("identity_file"):
        args += ["-i", host["identity_file"]]
    args += [source, dest]
    return args


async def _run_scp(argv: list[str], *, timeout: float,
                   runner: Any = None) -> SshResult:
    if runner is not None:
        code, out, err = await runner(argv)
        to_text = lambda b: b.decode("utf-8", errors="replace") if isinstance(b, bytes) else str(b)  # noqa: E731
        return SshResult(ok=code == 0, exit_code=code,
                         stdout=to_text(out), stderr=to_text(err))
    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return SshResult(ok=False, exit_code=None, stdout="",
                         stderr=f"scp client not available: {exc}")
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
           "push_file", "fetch_file", "remote_sha256",
           "quote_remote", "resolve_identity_file"]
