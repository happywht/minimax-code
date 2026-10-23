"""Project validation commands — the M3 acceptance executor (v1.6.2).

A project may declare the command that proves its code is good (e.g.
``pytest -x -q``). When a run modified files inside the project, the
send-message pipeline executes that command before completing and
attaches the result to the run's ``verification`` acceptance evidence:

``{"command": ..., "exit_code": 0, "ok": true, "timed_out": false,
   "stdout": "...", "stderr": "...", "truncated": false}``

A non-zero exit is a *validation result*, not a pipeline failure — the
run still completes, but ``verification.requires_review`` is forced so
the verbal "done" can never mask a failing project (M3: 完成可验收的
编程闭环).

Failure model mirrors ``self_evolution.payload._run_command``:
infrastructure errors (spawn failure / timeout) produce ``ok=False``
with an ``error`` key; the command's own exit code is evidence, not an
exception.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from .execution import communicate_bounded, spawn_shell

logger = logging.getLogger(__name__)

# Same posture as the scheduler command payload: the ceiling keeps the
# kill inside the caller's own timeout budget instead of leaking the
# child behind a cancelled ``communicate()``.
_DEFAULT_VALIDATE_TIMEOUT_S = 600.0
_VALIDATE_TIMEOUT_CEILING_S = 900.0
# Per-stream cap — the acceptance evidence carries a tail, not a log.
_VALIDATE_OUTPUT_CAP = 20_000


def coerce_validate_timeout(value: Any) -> float:
    """Normalise a ``timeout_s`` knob into a safe float."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return _DEFAULT_VALIDATE_TIMEOUT_S
    if value <= 0:
        return _DEFAULT_VALIDATE_TIMEOUT_S
    return min(float(value), _VALIDATE_TIMEOUT_CEILING_S)


def normalize_validate_command(value: Any) -> str:
    """Return the trimmed command, or ``""`` when absent/invalid."""
    if not isinstance(value, str):
        return ""
    return value.strip()


async def run_validate_command(
    command: str, *, cwd: str, timeout_s: float = _DEFAULT_VALIDATE_TIMEOUT_S
) -> dict[str, Any]:
    """Execute a project's validate command and return structured evidence."""
    try:
        proc = await spawn_shell(
            command,
            cwd=cwd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        return {"command": command, "ok": False,
                "error": f"validate command failed to spawn: {exc}"}

    timed_out = False
    try:
        captured = await communicate_bounded(proc, timeout=timeout_s, limit=_VALIDATE_OUTPUT_CAP)
        stdout_b, stderr_b = captured.stdout, captured.stderr
        stdout_trunc, stderr_trunc = captured.stdout_truncated, captured.stderr_truncated
    except TimeoutError:
        timed_out = True
        stdout_b = stderr_b = b""
        stdout_trunc = stderr_trunc = False

    evidence: dict[str, Any] = {
        "command": command,
        "exit_code": proc.returncode,
        "ok": proc.returncode == 0 and not timed_out,
        "timed_out": timed_out,
        "stdout": stdout_b.decode("utf-8", errors="replace"),
        "stderr": stderr_b.decode("utf-8", errors="replace"),
        "stdout_truncated": stdout_trunc,
        "stderr_truncated": stderr_trunc,
    }
    if timed_out:
        evidence["error"] = f"validate command timed out after {timeout_s}s"
    return evidence


def run_touched_files(bus: Any, run_id: str) -> bool:
    """True when the fs-bus recorded any write attributed to this run.

    File tools publish with ``run_id`` in the event attributes (v1.5.1),
    so a run that only read/searched never triggers the validation
    command — pure Q&A turns stay cheap.
    """
    if bus is None or not run_id:
        return False
    try:
        for event in bus.recent(limit=500):
            attrs = dict(getattr(event, "attributes", ()) or ())
            if attrs.get("run_id") == run_id:
                return True
    except Exception:  # noqa: BLE001 — advisory probe, never fatal
        logger.debug("run_touched_files probe failed", exc_info=True)
    return False


def resolve_validate_timeout() -> float:
    """Operator knob for the per-run validation timeout (default 600s)."""
    import os

    raw = os.environ.get("MINIMAX_CODE_VALIDATE_TIMEOUT_S", "").strip()
    if not raw:
        return _DEFAULT_VALIDATE_TIMEOUT_S
    try:
        return coerce_validate_timeout(float(raw))
    except ValueError:
        logger.warning("MINIMAX_CODE_VALIDATE_TIMEOUT_S=%r is not a number; using default", raw)
        return _DEFAULT_VALIDATE_TIMEOUT_S


__all__ = [
    "coerce_validate_timeout",
    "normalize_validate_command",
    "resolve_validate_timeout",
    "run_touched_files",
    "run_validate_command",
]
