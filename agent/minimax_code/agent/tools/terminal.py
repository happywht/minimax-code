"""Terminal execution tool — runs a shell command with hard timeouts.

The implementation uses :func:`asyncio.create_subprocess_exec` (not
``shell``) so we can pass argv directly and avoid shell-injection
risks. ``cwd`` is constrained to the workspace via
:func:`minimax_code.agent.tools.file_ops.safe_resolve`.

v0.5.0 hardening
----------------
* Environment variable whitelist (``_build_safe_env``) instead of
  ``os.environ.copy()`` — API keys, SSH keys, and cloud credentials
  are never leaked into child processes.
* Expanded command deny list (``_DENY_COMMANDS``) — blocks shell
  escapes (``bash -c``, ``cmd /c``), eval invocations (``python -c``,
  ``node -e``), privilege escalation (``sudo``, ``runas``), and
  destructive commands (``rm -rf /``, ``format``).
* Concurrency limit (``_PROCESS_SEMAPHORE``) — at most 10 child
  processes at once.
* Argument count and length limits — ``_MAX_ARGS``, ``_MAX_ARG_LEN``.

v1.6.0 sandbox escape detection
-------------------------------
A sandboxed sub-agent (``spawn_subagent(sandbox=True)``) can still
shell out and write the *shared workspace* directly — exec has no
write-redirection layer. Those writes are invisible to
``collect_subagent``'s three-way compare, so without this detector
they silently bypass the merge. When (and only when) the caller runs
inside a sandbox, the tool snapshots the workspace before/after the
child, diffs the two, subtracts writes the fs-bus attributes to other
actors, and reports the remainder as an advisory
``sandbox_escape`` block. Main-agent calls pay zero overhead: the
snapshot walk never runs outside a sandbox.

Streaming model
---------------
The tool buffers stdout / stderr line-by-line and returns a
:class:`ToolResult` containing the joined text. We do **not**
push incremental lines back to the agent loop because the
function-call API surface is request/response. If a UI wants
live tail it should call ``run_streaming`` directly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import signal
import subprocess
import sys
import time
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...workspace_ctx import current_run_id, current_sandbox
from .base import Tool, ToolResult, register_tool
from .file_ops import PathSecurityError, _default_workspace, safe_resolve

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------

_HARD_TIMEOUT_CAP = 600  # 10 minutes
_MAX_OUTPUT_BYTES = 5 * 1024 * 1024  # 5 MiB
_MAX_ARGS = 100  # Maximum number of arguments
_MAX_ARG_LEN = 4096  # Maximum length of a single argument
_PROCESS_SEMAPHORE = asyncio.Semaphore(10)  # Max concurrent children


# ---------------------------------------------------------------------------
# Process-tree control (v1.2.2)
# ---------------------------------------------------------------------------

# SIGKILL is POSIX-only in the signal module; on Windows the tree kill
# goes through ``taskkill /F /T`` and never reads the signal value, so
# the conventional 9 is a safe stand-in.
_SIGKILL = getattr(signal, "SIGKILL", 9)


def _child_spawn_kwargs() -> dict[str, Any]:
    """Platform kwargs so the child leads its own process group/session.

    This is what makes killing the *whole* tree possible: POSIX
    ``killpg`` requires the child to own its process group, otherwise
    a group kill would take the agent process down with it.
    """
    if sys.platform == "win32":
        # ``taskkill /T`` walks the tree by parent-PID — no group flag
        # needed (CREATE_NEW_PROCESS_GROUP only gates Ctrl+C delivery).
        return {}
    return {"start_new_session": True}


async def _signal_process_tree(
    proc: asyncio.subprocess.Process, sig: int
) -> None:
    """Deliver *sig* to *proc* and every descendant, not just the child.

    ``proc.kill()`` reaches only the direct child — a command that
    spawned its own children (build tool, dev server) used to leave
    them orphaned, still holding ports and locks in the workspace.

    Windows maps every signal to ``taskkill /F /T /PID`` (force-kill
    the tree); SIGTERM-vs-SIGKILL nuance doesn't exist there and
    ``Process.terminate()`` was already a hard TerminateProcess.
    POSIX sends the signal to the child's process group. Both fall
    back to a plain ``proc.kill()`` when the tree-wide call fails.
    """
    if proc.returncode is not None:
        return
    if sys.platform == "win32":
        rc = await asyncio.to_thread(
            subprocess.call,
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        if rc != 0:
            try:
                proc.kill()
            except (ProcessLookupError, OSError):
                pass
        return
    try:
        killpg = getattr(os, "killpg", None)
        getpgid = getattr(os, "getpgid", None)
        if killpg is None or getpgid is None:  # pragma: no cover — non-POSIX
            raise OSError("killpg/getpgid unavailable on this platform")
        killpg(getpgid(proc.pid), sig)
    except (ProcessLookupError, PermissionError, OSError):
        try:
            proc.kill()
        except (ProcessLookupError, OSError):
            pass

# ---------------------------------------------------------------------------
# Environment variable sanitisation
# ---------------------------------------------------------------------------

# Keys that are always safe to pass through (case-insensitive match).
_SAFE_ENV_WHITELIST: frozenset[str] = frozenset({
    "PATH", "HOME", "USERPROFILE", "HOMEPATH", "HOMEDRIVE",
    "TEMP", "TMP", "TMPDIR",
    "LANG", "LC_ALL", "LC_CTYPE",
    "PYTHONPATH", "PYTHONUNBUFFERED", "PYTHONIOENCODING",
    "GOPATH", "GOROOT",
    "CARGO_HOME", "RUSTUP_HOME",
    "NODE_PATH",
    "JAVA_HOME",
    "EDITOR", "VISUAL",
    "PAGER", "TERM",
    "SYSTEMROOT",  # Windows: required for subprocesses
    "COMSPEC",  # Windows: cmd.exe path
    "PROCESSOR_ARCHITECTURE",
    "PROGRAMFILES", "PROGRAMFILES(X86)", "PROGRAMDATA",
    "LOCALAPPDATA", "APPDATA",
    "USERDNSDOMAIN",
    "MINIMAX_CODE_DATA_DIR",  # our own data dir (not secret)
})

# Prefixes that are *never* safe — even if not in the whitelist,
# matching one of these is an instant rejection.
_BLOCKED_ENV_PREFIXES: tuple[str, ...] = (
    "MINIMAX_API_KEY",
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "AWS_",
    "AZURE_",
    "GOOGLE_API_KEY",
    "GEMINI_API_KEY",
    "SSH_",
    "SECRET_",
    "TOKEN",
    "PASSWORD",
    "PASSWD",
    "PRIVATE_KEY",
    "DATABASE_URL",
    "REDIS_URL",
    "MONGO",
    "POSTGRES",
    "KUBERNETES_SERVICE_HOST",
)


def _build_safe_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build a sanitized environment for child processes.

    Only whitelisted keys are inherited from the parent. User-provided
    ``extra`` vars are always passed through (they are explicit opt-in),
    but any that match a blocked prefix are silently dropped.
    """
    safe: dict[str, str] = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if any(upper.startswith(pfx) for pfx in _BLOCKED_ENV_PREFIXES):
            continue
        if upper in _SAFE_ENV_WHITELIST:
            safe[key] = value
    # Always set these for reliable subprocess output.
    safe.setdefault("PYTHONUNBUFFERED", "1")
    safe.setdefault("PYTHONIOENCODING", "utf-8")
    # Merge user-provided extras — but still drop blocked ones.
    if extra:
        for key, value in extra.items():
            upper = key.upper()
            if not any(upper.startswith(pfx) for pfx in _BLOCKED_ENV_PREFIXES):
                safe[key] = value
    return safe


# ---------------------------------------------------------------------------
# Command deny list
# ---------------------------------------------------------------------------

# Each entry maps a base command to a list of dangerous flag patterns.
# If *any* arg matches a listed regex for the base command, the command
# is rejected. If the list is empty, the base command itself is blocked
# regardless of arguments.
#
# The check is case-insensitive on Windows.
_DENY_COMMANDS: dict[str, list[str]] = {
    # Shell escapes — allow the shell binary itself but block "-c"/"-command"
    "bash": ["-c", "--command"],
    "sh": ["-c"],
    "zsh": ["-c"],
    "fish": ["-c"],
    "cmd": ["/c", "/C", "-c"],
    "powershell": ["-command", "-Command", "-c", "-enc", "-encodedcommand"],
    "pwsh": ["-command", "-Command", "-c", "-enc", "-encodedcommand"],
    # Eval invocations
    "python": ["-c"],
    "python3": ["-c"],
    "node": ["-e", "--eval", "-c", "--print"],
    "perl": ["-e", "-E"],
    "ruby": ["-e"],
    "php": ["-r"],
    # Privilege escalation — always blocked regardless of args
    "sudo": [],
    "su": [],
    "runas": [],
    # Destructive — always blocked regardless of args
    "format": [],
    "shutdown": [],
    "halt": [],
    "poweroff": [],
    "reboot": [],
    "init": ["0", "6"],
}

# Commands where the danger is arg-level: block if an arg matches,
# but allow the base command with other args (e.g. `python script.py`).
_ARG_LEVEL_DANGER: frozenset[str] = frozenset({
    "bash", "sh", "zsh", "fish", "cmd", "powershell", "pwsh",
    "python", "python3", "node", "perl", "ruby", "php", "init",
})

# Commands that are always blocked (empty deny list = block unconditionally).
_UNCONDITIONAL_BLOCK: frozenset[str] = frozenset({
    k for k, v in _DENY_COMMANDS.items() if not v
})

# Windows executable extensions stripped from the base command on every
# platform — an agent running on Linux must still recognise the danger in a
# Windows-style argv (``C:\Python312\python.exe -c ...``) it was asked to
# run. Other dotted names (``python3.12``) are left alone off-Windows.
_WIN_EXEC_EXTS: frozenset[str] = frozenset({".exe", ".bat", ".cmd", ".com", ".ps1"})


def _strip_executable_name(base: str) -> str:
    """Lowercased basename of *base* with a Windows-style extension removed.

    Known Windows executable extensions are stripped on every platform; on
    Windows any other trailing ``.ext`` is stripped too (original behaviour).
    """
    if "/" in base or "\\" in base:
        base = base.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    stem, dot, ext = base.rpartition(".")
    if dot and (f".{ext}" in _WIN_EXEC_EXTS or sys.platform == "win32"):
        return stem
    return base


def _is_dangerous_cmd(cmd: list[str]) -> str | None:
    """Return a reason string if *cmd* is dangerous, else ``None``.

    The check is case-insensitive on the base command (index 0) and
    case-insensitive substring match on args.
    """
    if not cmd:
        return None
    base = _strip_executable_name(cmd[0].lower())

    # Unconditional block — e.g. sudo, shutdown.
    if base in _UNCONDITIONAL_BLOCK:
        return f"'{cmd[0]}' is blocked (privilege escalation / destructive)"

    if base in {"rm", "rmdir"}:
        lowered = [arg.lower() for arg in cmd[1:]]
        recursive_force = any("r" in arg and "f" in arg for arg in lowered if arg.startswith("-"))
        root_target = any(
            arg in {"/", "\\"} or re.fullmatch(r"[a-z]:[\\/]?", arg) is not None
            for arg in lowered
        )
        if recursive_force and root_target:
            return f"'{cmd[0]}' is blocked (destructive root deletion)"

    # Arg-level checks.
    if base in _DENY_COMMANDS:
        patterns = _DENY_COMMANDS[base]
        if base in _ARG_LEVEL_DANGER:
            for arg in cmd[1:]:
                arg_lower = arg.lower()
                for pat in patterns:
                    if _matches_deny_arg(arg_lower, pat.lower()):
                        return (
                            f"'{cmd[0]} {arg}' is blocked "
                            f"(shell escape / eval pattern)"
                        )
    return None


def _matches_deny_arg(arg: str, pattern: str) -> bool:
    """Return true when *arg* is the dangerous flag itself.

    This intentionally avoids substring matching: ``--no-color``
    contains ``-c`` but is not a shell escape. Long options still
    match their ``--flag=value`` form.
    """
    if arg == pattern:
        return True
    if pattern.startswith("--") or pattern.startswith("-"):
        return arg.startswith(pattern + "=")
    return False


# ---------------------------------------------------------------------------
# Sandbox escape detection (v1.6.0)
# ---------------------------------------------------------------------------

# Narrow exclude set for the escape walk. Unlike glob's broad default
# table we deliberately keep build artifacts visible — a sandboxed exec
# dropping files into ``build/`` or ``dist/`` is exactly the escape form
# this detector exists to surface. Only provenance-free noise is pruned.
_ESCAPE_EXCLUDE_DIRS: frozenset[str] = frozenset({
    ".git", ".hg", ".svn",
    ".venv", "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".minimax",  # sandboxes / backups / artifacts — our own machinery
})

# Report cap: enough for the sub-agent (and the main agent's change-notes
# feed) to act on, small enough to keep the tool payload bounded.
_ESCAPE_MAX_REPORT = 50

# fs-bus causes that attribute a workspace write to a known tool. Events
# with these causes and a *different* run id are subtracted from the raw
# diff — a concurrent run's (or the main agent's) legitimate write landing
# inside our exec window must not be misreported as an escape.
_ATTRIBUTED_CAUSES: frozenset[str] = frozenset({
    "write_file", "edit_file", "append_file", "collect_subagent",
})

# Diff class → the closed FsEventKind union (created/modified/removed).
# An illegal kind string would be swallowed by the bus's fail-open and
# silently vanish, so the mapping is explicit.
_ESCAPE_KIND_MAP: dict[str, str] = {
    "added": "created",
    "modified": "modified",
    "removed": "removed",
}

# The windowed attribution lookback. The bus buffer is 500 events deep;
# beyond that we accept a rare false positive over paying for persistence.
_ESCAPE_BUS_LOOKBACK = 500


def _normkey(rel_posix: str) -> str:
    """Canonical snapshot key: normcase (case-folding on Windows) with
    forward slashes preserved — plain ``os.path.normcase`` flips ``/`` to
    ``\\`` there, which would leak into the human-facing report."""
    return os.path.normcase(rel_posix).replace(os.sep, "/")


def _scan_workspace(root: Path) -> dict[str, tuple[int, int]]:
    """Snapshot every regular file under *root* as ``{key: (mtime_ns, size)}``.

    Keys are :func:`_normkey` of the forward-slash relative path so the
    pre/post diff is stable across platforms and separators (the project's
    established normcase-key convention, cf. ``file_ops._INFLIGHT_WRITES``).
    Excluded directory names are pruned in-place during the walk; stat
    failures are skipped (a racy delete mid-walk must not break the call).
    Pure sync — callers wrap in ``asyncio.to_thread`` to keep the loop live.
    """
    snap: dict[str, tuple[int, int]] = {}
    for dirpath, dirnames, filenames in os.walk(root, topdown=True):
        dirnames[:] = [d for d in dirnames if d not in _ESCAPE_EXCLUDE_DIRS]
        for name in filenames:
            full = os.path.join(dirpath, name)
            try:
                st = os.stat(full)
            except OSError:
                continue
            rel = os.path.relpath(full, root).replace(os.sep, "/")
            snap[_normkey(rel)] = (st.st_mtime_ns, st.st_size)
    return snap


def _diff_snapshots(
    pre: dict[str, tuple[int, int]],
    post: dict[str, tuple[int, int]],
) -> list[tuple[str, str]]:
    """Classify snapshot deltas as ``[(change, rel_posix)]`` sorted by path.

    ``change`` is one of ``added`` / ``modified`` / ``removed``. The
    ``(mtime_ns, size)`` pair is a cheap fingerprint: a rewrite landing in
    the same nanosecond tick with the same size is invisible — a documented
    TOCTOU residual; content-hashing every file on every exec is not worth
    the walk cost for an advisory signal.
    """
    out: list[tuple[str, str]] = []
    for key, stat in post.items():
        old = pre.get(key)
        if old is None:
            out.append(("added", key))
        elif old != stat:
            out.append(("modified", key))
    for key in pre:
        if key not in post:
            out.append(("removed", key))
    out.sort(key=lambda item: item[1])
    return out


def _bus_watermark() -> int:
    """The fs bus's highest buffered seq (0 when the bus is off or empty).

    Taken right after the pre-walk so the attribution window covers
    [watermark, post-walk]: everything attributed in that span is a known
    tool write, and everything left in the diff is the exec's own doing.
    """
    try:
        from ...app import ensure_fs_bus

        bus = ensure_fs_bus()
        if bus is None:
            return 0
        events = bus.recent(limit=1)
        return events[-1].seq if events else 0
    except Exception:  # noqa: BLE001 — advisory path must never raise
        return 0


def _attributed_paths(root: Path, watermark: int, own_run: str | None) -> set[str]:
    """Normcase-rel paths other actors wrote into the workspace since *watermark*.

    Subtracted from the raw diff so concurrent runs' and the main agent's
    tool writes are not misreported. Only ``_ATTRIBUTED_CAUSES`` events
    count; the sub-agent's own run id is excluded — its tool writes are
    sandbox-redirected and pruned from the walk anyway, but the guard keeps
    the semantics explicit.
    """
    try:
        from ...app import ensure_fs_bus

        bus = ensure_fs_bus()
        if bus is None:
            return set()
        attributed: set[str] = set()
        for event in bus.recent(limit=_ESCAPE_BUS_LOOKBACK):
            if event.seq <= watermark:
                continue
            if event.cause not in _ATTRIBUTED_CAUSES:
                continue
            attrs = dict(event.attributes)
            if own_run is not None and attrs.get("run_id") == own_run:
                continue
            for path in event.paths:
                try:
                    rel = os.path.relpath(path, root)
                except ValueError:
                    continue  # different drive — not under this root
                if rel.startswith(".."):
                    continue
                attributed.add(_normkey(rel.replace(os.sep, "/")))
        return attributed
    except Exception:  # noqa: BLE001 — advisory path must never raise
        return set()


def _emit_escape(
    changed: list[tuple[str, str]], root: Path, run_id: str | None
) -> None:
    """Mirror unattributed exec-window changes onto the fs bus (fail-open).

    The main agent's v1.5.1 change-notes poll picks these up under cause
    ``exec_command_sandbox_escape``; kinds go through ``_ESCAPE_KIND_MAP``
    onto the closed FsEventKind union so nothing is silently dropped.
    """
    try:
        from ...app import ensure_fs_bus

        bus = ensure_fs_bus()
        if bus is None:
            return
        by_kind: dict[str, list[str]] = {}
        for change, rel in changed:
            abs_path = str(root.joinpath(*rel.split("/")))
            by_kind.setdefault(_ESCAPE_KIND_MAP[change], []).append(abs_path)
        for kind, paths in by_kind.items():
            bus.emit(
                kind,
                paths,
                "exec_command_sandbox_escape",
                **({"run_id": run_id} if run_id else {}),
            )
    except Exception:  # noqa: BLE001 — advisory path must never raise
        logger.debug("sandbox_escape fs_bus emit failed", exc_info=True)


@register_tool
class ExecCommandTool(Tool):
    name = "exec_command"
    description = (
        "Run a shell command and return its stdout, stderr, and exit code. "
        "The command is executed via asyncio.create_subprocess_exec (no "
        "shell interpolation); arguments are passed as a list. Use 'cwd' "
        "to scope the working directory and 'env' to inject extra "
        "environment variables. Default timeout is 30 seconds; hard cap "
        "is 10 minutes. Output is capped at 5 MiB per stream. NOTE: in a "
        "sandboxed run, files written by the child land in the shared "
        "workspace directly (not the sandbox) and will NOT be merged by "
        "collect_subagent — write deliverables with write_file instead; "
        "any workspace writes detected here come back as a sandbox_escape "
        "warning."
    )
    parameters = {
        "type": "object",
        "properties": {
            "cmd": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Command argv, e.g. ['git', 'status'].",
            },
            "cwd": {"type": "string", "description": "Working directory (must be inside the workspace)."},
            "env": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Extra environment variables to merge into the child env.",
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "maximum": _HARD_TIMEOUT_CAP,
                "default": 30,
                "description": "Wall-clock timeout in seconds.",
            },
        },
        "required": ["cmd"],
        "additionalProperties": False,
    }

    async def run(self, **kwargs: Any) -> ToolResult:
        cmd = kwargs.get("cmd")
        if not isinstance(cmd, list) or not cmd:
            return ToolResult.fail("'cmd' must be a non-empty list of strings")
        if not all(isinstance(x, str) for x in cmd):
            return ToolResult.fail("'cmd' must be a non-empty list of strings")

        # Argument count and length limits.
        if len(cmd) > _MAX_ARGS:
            return ToolResult.fail(
                f"too many arguments ({len(cmd)} > {_MAX_ARGS})"
            )
        for i, arg in enumerate(cmd):
            if len(arg) > _MAX_ARG_LEN:
                return ToolResult.fail(
                    f"argument {i} exceeds max length ({len(arg)} > {_MAX_ARG_LEN})"
                )

        # Dangerous command check (expanded deny list).
        reason = _is_dangerous_cmd(cmd)
        if reason:
            return ToolResult.fail(f"refusing to run dangerous command: {reason}")

        cwd_arg = kwargs.get("cwd")
        try:
            cwd = safe_resolve(cwd_arg) if cwd_arg else _default_workspace()
        except PathSecurityError as exc:
            return ToolResult.fail(str(exc))

        timeout = int(kwargs.get("timeout") or 30)
        if timeout > _HARD_TIMEOUT_CAP:
            timeout = _HARD_TIMEOUT_CAP

        env_arg: Mapping[str, Any] | None = kwargs.get("env")
        if env_arg is not None and not isinstance(env_arg, dict):
            return ToolResult.fail("'env' must be a JSON object of string→string")
        extra_env = {str(k): str(v) for k, v in (env_arg or {}).items()}

        # Build sanitized child environment.
        child_env = _build_safe_env(extra_env)

        # v1.6.0 — sandbox escape detection setup. Sandbox runs only;
        # the main agent's exec calls skip all of this. The pre-walk sits
        # inside the semaphore so writes landing while we *queue* for a
        # slot are in the "before" snapshot, not misread as escapes.
        escape_root = _default_workspace() if current_sandbox() is not None else None
        pre_snapshot: dict[str, tuple[int, int]] | None = None
        watermark = 0
        if escape_root is not None:
            try:
                pre_snapshot = await asyncio.to_thread(_scan_workspace, escape_root)
                watermark = _bus_watermark()
            except Exception:  # noqa: BLE001 — advisory must never break exec
                logger.debug("escape pre-walk failed", exc_info=True)
                pre_snapshot = None

        # Acquire concurrency semaphore.
        async with _PROCESS_SEMAPHORE:
            started = time.monotonic()
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=str(cwd) if cwd else None,
                    env=child_env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    **_child_spawn_kwargs(),
                )
            except FileNotFoundError as exc:
                return ToolResult.fail(f"command not found: {exc}")
            except PermissionError as exc:
                return ToolResult.fail(f"permission denied: {exc}")
            except OSError as exc:
                return ToolResult.fail(f"failed to spawn: {exc}")

            try:
                stdout_b, stderr_b = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                timed_out = False
            except TimeoutError:
                timed_out = True
                # v1.2.2: kill the whole tree — grandchildren used to
                # survive a plain proc.kill() and stay orphaned.
                await _signal_process_tree(proc, _SIGKILL)
                try:
                    stdout_b, stderr_b = await proc.communicate()
                except Exception:  # pragma: no cover — defensive
                    stdout_b, stderr_b = b"", b""

        truncated_stdout = False
        truncated_stderr = False
        if len(stdout_b) > _MAX_OUTPUT_BYTES:
            stdout_b = stdout_b[:_MAX_OUTPUT_BYTES]
            truncated_stdout = True
        if len(stderr_b) > _MAX_OUTPUT_BYTES:
            stderr_b = stderr_b[:_MAX_OUTPUT_BYTES]
            truncated_stderr = True

        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")
        exit_code = proc.returncode
        duration = time.monotonic() - started

        # v1.6.0 — post-walk at the convergence point: both the timeout
        # and the normal return path flow through here, so an escape
        # during a killed command is reported too (the tree kill may race
        # a straggler write — that write happened, it must be surfaced).
        escape_info: dict[str, Any] | None = None
        if pre_snapshot is not None and escape_root is not None:
            try:
                post_snapshot = await asyncio.to_thread(_scan_workspace, escape_root)
                own_run = current_run_id()
                attributed = _attributed_paths(escape_root, watermark, own_run)
                raw_diff = _diff_snapshots(pre_snapshot, post_snapshot)
                changed = [(c, p) for c, p in raw_diff if p not in attributed]
                if changed:
                    escape_info = {
                        "changed": [
                            f"{change}:{rel}"
                            for change, rel in changed[:_ESCAPE_MAX_REPORT]
                        ],
                        "changed_count": len(changed),
                        "changed_truncated": len(changed) > _ESCAPE_MAX_REPORT,
                        "warning": (
                            "exec_command wrote the shared workspace while "
                            "this run is sandboxed — these changes bypass "
                            "collect_subagent. Re-write deliverables with "
                            "write_file before reporting completion."
                        ),
                    }
                    _emit_escape(changed, escape_root, own_run)
            except Exception:  # noqa: BLE001 — advisory must never break exec
                logger.debug("escape post-walk failed", exc_info=True)

        if timed_out:
            return ToolResult.fail(
                f"command timed out after {timeout}s (killed)",
                output={
                    "stdout": stdout,
                    "stderr": stderr,
                    "exit_code": -1,
                    "timed_out": True,
                    "duration_s": duration,
                    "truncated_stdout": truncated_stdout,
                    "truncated_stderr": truncated_stderr,
                    **({"sandbox_escape": escape_info} if escape_info else {}),
                },
                exit_code=-1,
                timed_out=True,
                duration_s=duration,
            )

        success = exit_code == 0
        err_msg = None if success else f"command exited with code {exit_code}"
        return ToolResult(
            success=success,
            output={
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
                "duration_s": duration,
                "truncated_stdout": truncated_stdout,
                "truncated_stderr": truncated_stderr,
                **({"sandbox_escape": escape_info} if escape_info else {}),
            },
            error=err_msg,
            metadata={
                "exit_code": exit_code,
                "duration_s": duration,
                "truncated_stdout": truncated_stdout,
                "truncated_stderr": truncated_stderr,
            },
        )


__all__ = [
    "ExecCommandTool",
    "_build_safe_env",
    "_is_dangerous_cmd",
    "_child_spawn_kwargs",
    "_signal_process_tree",
    "_SIGKILL",
    "_ESCAPE_EXCLUDE_DIRS",
    "_scan_workspace",
    "_diff_snapshots",
    "_bus_watermark",
    "_attributed_paths",
    "_emit_escape",
]
