"""Headless verification tool (P0-5) + check_subagent enhancements (P1-3).

P0-5 — verify_subagent
----------------------

A spawned sub-agent can leave syntactically broken files on disk and
``report_completion`` happily declares success. The user is then left
debugging their own agent's output. This tool runs a *headless*
verification command in the workspace (e.g. ``python -m py_compile <file>``,
``node --check <file>``, ``pytest <test>``, ``python -c "import ast; ast.parse(open('<file>').read())"``)
and returns pass/fail with the captured stderr.

The verifier is intentionally shell-style — operators want to wire
whatever fits the deliverable, not learn a new declarative mini-DSL.
The execution is sandboxed to ``workspace_ctx.current_root()`` (the
sub-agent's overlay view) and the command runs via
:mod:`subprocess.run` with a hard wall-clock timeout (default 30s).

P1-3 — check_subagent richer state
----------------------------------

``check_subagent`` already returned the progress ledger (v1.5.2);
:func:`build_subagent_status` unifies the projection for *every*
code path (running / waiting_deps / finished / failed) and adds
wall-clock ``elapsed_s`` and the ``error`` fact. The output is a
strict superset of the legacy ``_snapshot`` shape — the full
``tool_calls`` list and the conditional ``files_written`` manifest
ride along so the v1.5.0 five-point propagation contract holds.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

from .base import Tool, ToolResult, register_tool

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# P1-3 — sub-agent run telemetry aggregator
# ---------------------------------------------------------------------------


def build_subagent_status(
    run_id: str,
    *,
    started_at: float | None = None,
    task_result: dict[str, Any] | None = None,
    deps_pending: list[str] | None = None,
) -> dict[str, Any]:
    """Compose the unified ``check_subagent`` / ``wait_subagent`` payload.

    One projection for every code path (running, waiting on DAG deps,
    finished, failed) so check/wait envelopes compare apples-to-apples.
    Calling convention: ``task_result`` non-None means the run reached a
    terminal state and its envelope drives the final status — callers
    must not pass ``task_result`` for an in-flight run. A failed run is
    reported as ``status='completed'`` plus a non-empty ``error`` key.
    Each branch is fail-open: missing inputs become empty values rather
    than raising.
    """
    status = "running"
    if task_result is not None:
        status = "cancelled" if task_result.get("cancelled") else "completed"
    elif deps_pending:
        status = "waiting_deps"
    payload: dict[str, Any] = {
        "run_id": run_id,
        "status": status,
        "deps_pending": deps_pending or [],
    }
    if started_at is not None:
        payload["elapsed_s"] = round(time.time() - started_at, 3)
    if task_result is not None:
        # Surface the key run-level facts the coordinator needs to
        # decide whether to keep waiting, escalate, or merge. The full
        # tool_calls list and the conditional files_written manifest
        # are load-bearing (legacy _snapshot contract + v1.5.0
        # collect propagation) — see the module docstring.
        payload["iterations"] = task_result.get("iterations", 0)
        payload["tool_calls"] = task_result.get("tool_calls", [])
        payload["text"] = task_result.get("text", "")
        payload["usage"] = task_result.get("usage") or {}
        payload["cancelled"] = bool(task_result.get("cancelled", False))
        payload["partial"] = bool(task_result.get("partial", False))
        payload["truncated"] = bool(task_result.get("truncated", False))
        payload["reported"] = bool(task_result.get("reported", False))
        payload["stub"] = bool(task_result.get("stub", True))
        err = task_result.get("error")
        if err:
            payload["error"] = err
        if task_result.get("files_written") is not None:
            payload["files_written"] = task_result["files_written"]
    # Always overlay the progress ledger if it exists.
    from .artifacts import progress_summary

    progress = progress_summary(run_id)
    if progress is not None:
        payload["progress"] = progress
    return payload


# ---------------------------------------------------------------------------
# P0-5 — verify_subagent
# ---------------------------------------------------------------------------


def _workspace_root() -> Path | None:
    from ...workspace_ctx import current_root

    return current_root()


async def _kill_process_tree(proc: asyncio.subprocess.Process) -> None:
    """Kill the shell *and* its children.

    On Windows a bare ``proc.kill()`` only kills the wrapper shell; the
    wrapped child inherits the stdout/stderr pipe handles, so
    ``proc.wait()`` then blocks until the orphan exits on its own (a
    ``sleep(5)`` verifier held the wall clock for its full 5s in the
    regression test). Windows uses ``taskkill /F /T`` (same remedy as
    ``terminal.py``); POSIX kills the process group when the spawn
    opened a new session, falling back to a single kill.
    """
    if proc.returncode is not None:
        return
    if os.name == "nt":
        killer = await asyncio.create_subprocess_exec(
            "taskkill",
            "/F",
            "/T",
            "/PID",
            str(proc.pid),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        with contextlib.suppress(Exception):
            await killer.wait()
    else:
        with contextlib.suppress(Exception):
            os.killpg(os.getpgid(proc.pid), 9)
        with contextlib.suppress(Exception):
            proc.kill()


@register_tool
class VerifySubagentTool(Tool):
    """Run a headless verification command and return pass/fail.

    P0-5: a sub-agent can declare ``report_completion`` with syntactically
    broken files. ``verify_subagent`` runs a shell command (Python syntax
    check, Node --check, pytest, etc.) inside the workspace root and
    surfaces stderr on failure. The run is bounded by ``timeout_s`` so
    a hung verifier cannot hang the parent run.
    """

    name = "verify_subagent"
    description = (
        "Headless verification of a sub-agent's deliverables (P0-5). "
        "Runs ``command`` via subprocess inside the workspace root and "
        "returns pass/fail plus captured stdout/stderr. Use to syntax-check "
        "JS/Python, run pytest, or any other shell-runnable check. "
        "The command is sandboxed to the workspace (paths are resolved "
        "relative to the workspace root) and timed out at ``timeout_s``."
    )
    parameters = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "Shell command to run. Use the file path you want to "
                    "check, e.g. 'python -m py_compile app.py' or "
                    "'node --check app.js'."
                ),
            },
            "cwd": {
                "type": "string",
                "description": (
                    "Working directory (relative to workspace root). "
                    "Defaults to the workspace root itself."
                ),
            },
            "timeout_s": {
                "type": "number",
                "minimum": 1,
                "maximum": 600,
                "description": "Hard wall-clock timeout in seconds (default 30).",
                "default": 30,
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    async def run(
        self,
        command: str,
        cwd: str | None = None,
        timeout_s: float = 30,
    ) -> ToolResult:
        if not isinstance(command, str) or not command.strip():
            return ToolResult.fail("command is required and must be a non-empty string")
        if len(command) > 4_000:
            # 4 KB cap is plenty for any realistic verifier; bounds the
            # blast radius of a malformed prompt.
            return ToolResult.fail("command is too long (max 4000 chars)")
        root = _workspace_root()
        if root is None:
            return ToolResult.fail("no workspace root is active — verifier unavailable")
        workdir = (root / (cwd or ".")).resolve() if cwd else root
        # is_relative_to, not startswith — the string check lets
        # "C:\ws-evil" sneak past a "C:\ws" root.
        if not workdir.is_relative_to(root):
            return ToolResult.fail("cwd escapes workspace root")
        if not workdir.is_dir():
            return ToolResult.fail(f"cwd is not a directory: {workdir}")
        try:
            timeout = max(1.0, min(float(timeout_s), 600.0))
        except (TypeError, ValueError):
            timeout = 30.0
        run_id = f"verify_{uuid.uuid4().hex[:8]}"
        try:
            spawn_kwargs: dict[str, Any] = {}
            # New session on POSIX so _kill_process_tree can signal the
            # whole group; the flag is a no-op on Windows.
            if os.name != "nt":
                spawn_kwargs["start_new_session"] = True
            proc = await asyncio.create_subprocess_shell(
                command,
                cwd=str(workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                **spawn_kwargs,
            )
        except Exception as exc:
            return ToolResult.fail(f"failed to start verifier: {exc}")
        start = time.time()
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
        except TimeoutError:
            with contextlib.suppress(Exception):
                await _kill_process_tree(proc)
            # Reap the killed process — an un-awaited child can leave a
            # zombie / hold the pipe handles open on Windows.
            with contextlib.suppress(Exception):
                await proc.wait()
            return ToolResult.fail(
                f"verifier timed out after {timeout}s",
                output={
                    "verify_run_id": run_id,
                    "exit_code": None,
                    "passed": False,
                    "elapsed_s": round(time.time() - start, 3),
                    "timed_out": True,
                },
            )
        elapsed = time.time() - start
        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")
        return ToolResult.ok(
            {
                "verify_run_id": run_id,
                "exit_code": int(proc.returncode) if proc.returncode is not None else -1,
                "passed": proc.returncode == 0,
                "stdout": stdout[:20_000],  # cap so a verbose build doesn't blow the message
                "stderr": stderr[:20_000],
                "command": command,
                "cwd": str(workdir),
                "elapsed_s": round(elapsed, 3),
            }
        )


__all__ = [
    "VerifySubagentTool",
    "build_subagent_status",
]
