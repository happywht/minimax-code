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
import base64
import json
import os
import re
import shlex
import tempfile
import uuid
import zlib
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..execution import process_environment


def ssh_environment() -> dict[str, str]:
    """Filter service credentials while retaining SSH agent authentication."""
    extra = {key: value for key, value in os.environ.items()
             if key.upper() == "SSH_AUTH_SOCK"}
    return process_environment(extra)


@dataclass
class SshResult:
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    transfer_id: str | None = None
    publication_state: str | None = None
    backup_path: str | None = None
    newly_created: bool = False


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
            env=ssh_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return SshResult(ok=False, exit_code=None, stdout="",
                         stderr=f"ssh client not available: {exc}")
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        await _stop_transfer_process(proc)
        return SshResult(ok=False, exit_code=None, stdout="", stderr="",
                         timed_out=True)
    except asyncio.CancelledError:
        await _stop_transfer_process(proc)
        raise
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
    transfer_id: str | None = None,
) -> SshResult:
    """Stage, verify, then atomically publish a remote upload with a backup."""
    from ..execution import require_write_access

    require_write_access()
    target = PurePosixPath(remote_path)
    if not target.is_absolute() or ".." in target.parts or not target.name:
        return SshResult(False, None, "", "remote_path must be an absolute file path")
    remote_path = str(target)
    local_path = Path(local_path).expanduser().absolute()
    local_sha = _sha256_file(local_path)
    transfer_id = transfer_id if transfer_id is not None else uuid.uuid4().hex
    if not isinstance(transfer_id, str) or not re.fullmatch(r"[0-9a-f]{32}", transfer_id):
        return SshResult(False, None, "", "transfer_id must be 32 lowercase hex characters")

    async def action(name):
        return await upload_action(host, remote_path, transfer_id, name,
                                   sha256=local_sha, runner=runner)

    prepared = await action("prepare")
    if not prepared.ok:
        return prepared
    if not prepared.newly_created:
        # A duplicate request may be racing the first caller's SCP. Never
        # overwrite its payload, recommit it, or abort its owned staging.
        prepared.ok = prepared.publication_state == "published"
        if not prepared.ok:
            prepared.stderr = "transfer already exists; inspect its status or use a new transfer_id"
        return prepared
    staged = target.parent / (".minimax-upload-" + transfer_id) / "payload"
    committing = False
    try:
        result = await _run_scp(
            _scp_args(host, str(local_path), f"{_target(host)}:{staged}", sftp_only=True),
            timeout=timeout, runner=runner)
        if not result.ok:
            result.transfer_id = transfer_id
            result.publication_state = "not_published"
            return result
        committing = True
        committed = await action("commit")
        if committed.ok and committed.publication_state == "published":
            return committed
        # Never retry a write after losing its reply. Query the journal instead.
        observed = await action("status")
        if observed.ok and observed.publication_state == "published":
            return observed
        committed.ok = False
        committed.publication_state = (observed.publication_state if observed.ok else "unknown")
        if observed.ok and observed.publication_state == "staged":
            committing = False
        committed.stderr = committed.stderr or "upload publication not confirmed; inspect transfer journal"
        return committed
    finally:
        if not committing:
            # Cleanup is limited to this transfer's uncommitted payload. Losing
            # cleanup connectivity leaves staging, never an overwritten target.
            with suppress(Exception):
                await action("abort")


async def upload_action(host: Any, remote_path: str, transfer_id: str, action: str, *,
                        sha256: str | None = None, runner: Any = None) -> SshResult:
    """Run the standalone journal helper; paths are data, never shell syntax."""
    from ..execution import require_write_access
    from . import upload_transaction

    if action not in {"prepare", "commit", "status", "abort"}:
        raise ValueError("unknown upload action")
    if action != "status":
        require_write_access()
    source = Path(upload_transaction.__file__).read_bytes()
    packed = base64.b64encode(zlib.compress(source)).decode("ascii")
    bootstrap = f"import base64,zlib;exec(zlib.decompress(base64.b64decode('{packed}')))"
    payload = json.dumps({"target": remote_path, "transfer_id": transfer_id,
                          "action": action, "sha256": sha256})
    command = shlex.join(["python3", "-c", bootstrap, payload])
    result = await run_ssh(host, command, timeout=30, runner=runner)
    result.transfer_id = transfer_id
    result.publication_state = "unknown"
    if not result.ok:
        result.stderr = result.stderr or result.stdout
        return result
    try:
        data = json.loads(result.stdout)
        if data.get("transfer_id") != transfer_id:
            raise ValueError("transfer receipt id mismatch")
        state = data.get("state")
        if state not in {"staged", "publishing", "published", "aborted"}:
            raise ValueError("invalid transfer state")
        result.publication_state = state
        result.backup_path = data.get("backup_path")
        result.newly_created = data.get("newly_created") is True
    except (ValueError, TypeError, AttributeError) as exc:
        result.ok = False
        result.stderr = f"invalid upload receipt: {exc}"
    return result


async def fetch_file(
    host: Any,
    remote_path: str,
    local_path: Path,
    *,
    timeout: float = 300.0,
    runner: Any = None,
) -> SshResult:
    """Verify a staged download and publish only to an unused destination."""
    from ..execution import require_write_access

    require_write_access()
    # Resolve the parent, not the destination: following a final symlink would
    # erase the evidence that this is an unsafe replacement target.
    local_path = Path(local_path).expanduser().absolute()
    if local_path.is_symlink() or local_path.parent.resolve() != local_path.parent:
        return SshResult(False, None, "", "download destination must not be a symbolic link")
    if local_path.exists():
        return SshResult(False, None, "", "download destination already exists; choose a new path")
    with tempfile.TemporaryDirectory(prefix=".minimax-download-", dir=local_path.parent) as tmp:
        staged = Path(tmp) / "payload"
        argv = _scp_args(host, f"{_target(host)}:{remote_path}", str(staged), sftp_only=True)
        result = await _run_scp(argv, timeout=timeout, runner=runner)
        if not result.ok:
            return result
        if not staged.is_file() or staged.is_symlink():
            return SshResult(False, None, "", "staged file missing after download")
        local_sha = _sha256_file(staged)
        remote_sha = await remote_sha256(host, remote_path, runner=runner)
        if remote_sha is None or remote_sha != local_sha:
            return SshResult(False, None, result.stdout,
                             f"sha256 mismatch after download: local={local_sha} remote={remote_sha}")
        try:
            # The staging directory is on the same filesystem. Linking is an
            # atomic create-if-absent, including if another process creates
            # the destination while the transfer is running.
            os.link(staged, local_path)
        except OSError as exc:
            return SshResult(False, None, result.stdout, f"download not published: {exc}")
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


def _scp_args(host: Any, source: str, dest: str, *, sftp_only: bool = False) -> list[str]:
    args = ["scp", "-P", str(int(host.get("port") or 22)),
            "-o", "BatchMode=yes",
            "-o", "StrictHostKeyChecking=accept-new"]
    if sftp_only:
        # Legacy SCP interprets remote paths through a shell. Force SFTP so
        # user-supplied parent names stay literal, and never fall back to SCP.
        args.append("-s")
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
            env=ssh_environment(),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except FileNotFoundError as exc:
        return SshResult(ok=False, exit_code=None, stdout="",
                         stderr=f"scp client not available: {exc}")
    try:
        out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        await _stop_transfer_process(proc)
        return SshResult(ok=False, exit_code=None, stdout="", stderr="",
                         timed_out=True)
    except asyncio.CancelledError:
        await _stop_transfer_process(proc)
        raise
    return SshResult(
        ok=proc.returncode == 0,
        exit_code=proc.returncode,
        stdout=out_b.decode("utf-8", errors="replace"),
        stderr=err_b.decode("utf-8", errors="replace"),
    )


async def _stop_transfer_process(proc: asyncio.subprocess.Process) -> None:
    """Reap the local SSH/SCP transport; this does not prove remote job exit."""
    if proc.returncode is None:
        with suppress(ProcessLookupError):
            proc.kill()
    await proc.wait()


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
