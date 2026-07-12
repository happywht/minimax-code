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
import os
import re
import sys
import time
from collections.abc import Mapping
from typing import Any

from .base import Tool, ToolResult, register_tool
from .file_ops import PathSecurityError, safe_resolve

# ---------------------------------------------------------------------------
# Resource limits
# ---------------------------------------------------------------------------

_HARD_TIMEOUT_CAP = 600  # 10 minutes
_MAX_OUTPUT_BYTES = 5 * 1024 * 1024  # 5 MiB
_MAX_ARGS = 100  # Maximum number of arguments
_MAX_ARG_LEN = 4096  # Maximum length of a single argument
_PROCESS_SEMAPHORE = asyncio.Semaphore(10)  # Max concurrent children

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


def _is_dangerous_cmd(cmd: list[str]) -> str | None:
    """Return a reason string if *cmd* is dangerous, else ``None``.

    The check is case-insensitive on the base command (index 0) and
    case-insensitive substring match on args.
    """
    if not cmd:
        return None
    base = cmd[0].lower()
    # Normalize path: extract just the executable name.
    if "/" in base or "\\" in base:
        base = base.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    # Remove extension on Windows.
    if sys.platform == "win32" and "." in base:
        base = base.rsplit(".", 1)[0]

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


@register_tool
class ExecCommandTool(Tool):
    name = "exec_command"
    description = (
        "Run a shell command and return its stdout, stderr, and exit code. "
        "The command is executed via asyncio.create_subprocess_exec (no "
        "shell interpolation); arguments are passed as a list. Use 'cwd' "
        "to scope the working directory and 'env' to inject extra "
        "environment variables. Default timeout is 30 seconds; hard cap "
        "is 10 minutes. Output is capped at 5 MiB per stream."
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
            cwd = safe_resolve(cwd_arg) if cwd_arg else None
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
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
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
            },
            error=err_msg,
            metadata={
                "exit_code": exit_code,
                "duration_s": duration,
                "truncated_stdout": truncated_stdout,
                "truncated_stderr": truncated_stderr,
            },
        )


__all__ = ["ExecCommandTool", "_build_safe_env", "_is_dangerous_cmd"]
