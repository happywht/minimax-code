"""Terminal execution tool — runs a shell command with hard timeouts.

The implementation uses :func:`asyncio.create_subprocess_exec` (not
``shell``) so we can pass argv directly and avoid shell-injection
risks. ``cwd`` is constrained to the workspace via
:func:`minimax_code.agent.tools.file_ops.safe_resolve`.

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
import shlex
import time
from collections.abc import Mapping
from typing import Any

from .base import Tool, ToolResult, register_tool
from .file_ops import PathSecurityError, safe_resolve

# Maximum command wall time, even if the caller asks for more.
_HARD_TIMEOUT_CAP = 600  # 10 minutes
# Maximum bytes captured per stream.
_MAX_OUTPUT_BYTES = 5 * 1024 * 1024  # 5 MiB
# Disallow obviously dangerous commands outright.
_DENY_PREFIXES = (
    "rm -rf /",
    "rm -fr /",
    "format ",
    "del /f /s /q c:\\",
    "rd /s /q c:\\",
    "shutdown",
    "halt",
    "poweroff",
)


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
        if not isinstance(cmd, list) or not cmd or not all(isjoin := [isinstance(x, str) for x in cmd]) or not all(isjoin):
            return ToolResult.fail("'cmd' must be a non-empty list of strings")

        # Surface dangerous commands early.
        joined_for_check = " ".join(shlex.quote(part) for part in cmd).lower()
        for bad in _DENY_PREFIXES:
            if joined_for_check.startswith(bad):
                return ToolResult.fail(f"refusing to run dangerous command: {cmd!r}")

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

        # Build child env. Inherit parent by default; PYTHONUNBUFFERED
        # is set so any Python subprocess prints immediately.
        child_env = os.environ.copy()
        child_env.setdefault("PYTHONUNBUFFERED", "1")
        child_env.setdefault("PYTHONIOENCODING", "utf-8")
        child_env.update(extra_env)

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


__all__ = ["ExecCommandTool"]
