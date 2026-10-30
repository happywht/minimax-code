"""Project validation commands — the M3 acceptance executor (v1.6.2).

A project may declare the command that proves its code is good (e.g.
``pytest -x -q``). When a write-enabled run completes normally, the
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

from .execution import ProcessTimeoutError, communicate_bounded, spawn_shell

logger = logging.getLogger(__name__)


def verification_evidence(*, status: str = "completed", engine: str = "native",
                          mode: str = "chat", configured: bool = False,
                          truncated: bool = False, base: dict[str, Any] | None = None,
                          validation: dict[str, Any] | None = None,
                          codex_validation_enabled: bool = False) -> dict[str, Any]:
    """Acceptance truth is separate from execution status; absent proof never passes."""
    evidence = {"tool_calls": 0, "failed_tool_calls": 0, "failures": [], **(base or {})}
    evidence.pop("validation", None)
    if status != "completed":
        verdict, reason = "unknown", f"execution_{status}"
    elif truncated:
        verdict, reason = "unknown", "execution_truncated"
    elif mode == "plan":
        verdict, reason = "not_applicable", "plan_mode"
    elif engine == "codex" and not codex_validation_enabled:
        verdict, reason = "unknown", "engine_validation_not_supported"
    elif not configured:
        verdict, reason = "unknown", "not_configured"
    elif validation is None:
        verdict, reason = "unknown", "validation_missing"
    else:
        evidence["validation"] = validation
        if validation.get("timed_out"):
            verdict, reason = "unknown", "validation_timeout"
        elif validation.get("exit_code") is None:
            verdict, reason = "unknown", "validation_spawn_failed"
        elif validation.get("ok") is True and validation.get("exit_code") == 0:
            verdict, reason = "passed", "validation_passed"
        else:
            verdict, reason = "failed", "validation_failed"
    if evidence.get("failed_tool_calls") and status == "completed":
        verdict, reason = "failed", "failed_tool_calls"
    review = (bool(evidence.get("requires_review")) or bool(evidence.get("failed_tool_calls")) or truncated
              or status in {"failed", "cancelled", "interrupted", "budget_paused"}
              or verdict == "failed")
    # Missing/failed infrastructure for a configured validator requires review.
    review = review or (configured and verdict == "unknown" and reason.startswith("validation_"))
    evidence.update(version=1, verdict=verdict, reason=reason, requires_review=review)
    return evidence


async def verify_native_result(*, status: str, mode: str, command: str, workspace: str,
                               truncated: bool, base: dict[str, Any]) -> dict[str, Any]:
    validation = None
    if status == "completed" and mode != "plan" and not truncated and command:
        try:
            from .workspace_ctx import require_directory
            if not workspace:
                raise ValueError("Validation workspace is missing")
            root = str(require_directory(workspace))
            validation = await run_validate_command(command, cwd=root,
                                                   timeout_s=resolve_validate_timeout())
        except Exception as exc:
            logger.exception("validator infrastructure failed")
            validation = {"command": command, "ok": False, "error": str(exc)}
    return verification_evidence(status=status, mode=mode, configured=bool(command),
                                 truncated=truncated, base=base, validation=validation)


def task_outcome(result: Any) -> str:
    """Project a settled receipt without equating 'returned' with 'completed'."""
    if not isinstance(result, dict):
        return "completed"
    status = result.get("run_status")
    if status in {"budget_paused", "cancelled", "interrupted", "failed"}:
        return status
    if status in {"queued", "planning", "running", "awaiting_approval", "waiting_input"}:
        return "running"
    if status is None:
        if result.get("cancelled"):
            return "cancelled"
        if result.get("budget_paused"):
            return "budget_paused"
    if result.get("error"):
        return "failed"
    if result.get("run_id") and status != "completed":
        return "requires_review"
    if status == "completed" and (result.get("verification") or {}).get("requires_review"):
        return "requires_review"
    if result.get("ok") is False:
        return "failed"
    if (result.get("verification") or {}).get("requires_review") or result.get("truncated"):
        return "requires_review"
    return "completed"


def durable_task_result(run: dict[str, Any], receipt: Any) -> dict[str, Any]:
    """Project a persisted run without treating an unconfirmed receipt as success."""
    receipt = receipt if isinstance(receipt, dict) else {}
    metadata = run.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    proofs = [receipt.get("verification"), metadata.get("verification")]

    def valid(proof: Any) -> bool:
        if not isinstance(proof, dict) or proof.get("version") != 1:
            return False
        if (not isinstance(proof.get("verdict"), str)
                or proof.get("verdict") not in {"passed", "failed", "unknown", "not_applicable"}
                or not isinstance(proof.get("reason"), str)
                or not isinstance(proof.get("requires_review"), bool)):
            return False
        if proof["verdict"] == "passed":
            validation = proof.get("validation")
            return (isinstance(validation, dict) and validation.get("ok") is True
                    and type(validation.get("exit_code")) is int and validation["exit_code"] == 0
                    and validation.get("timed_out") is False)
        return True

    valid_proofs = [proof for proof in proofs if valid(proof)]
    evidence = dict(valid_proofs[0]) if valid_proofs else {}
    if valid_proofs:
        evidence["requires_review"] = any(proof["requires_review"] for proof in valid_proofs)
    reason = None
    if run.get("status") == "completed":
        identity_matches = (receipt.get("run_id") == run.get("id")
                            and receipt.get("session_id") == run.get("session_id")
                            and receipt.get("run_status") == "completed")
        contradictory = len(valid_proofs) == 2 and any(
            valid_proofs[0].get(key) != valid_proofs[1].get(key)
            for key in ("verdict", "reason", "requires_review")
        )
        if (not identity_matches or not valid(receipt.get("verification"))
                or (proofs[1] is not None and not valid(proofs[1])) or contradictory
                or receipt.get("cancelled") is True or receipt.get("budget_paused") is True):
            evidence = {"version": 1, "verdict": "unknown", "reason": "receipt_unconfirmed",
                        "requires_review": True}
            reason = "Completed run receipt or verification evidence is missing, invalid or inconsistent"
    result = {**receipt, "run_id": run.get("id"), "run_status": run.get("status"),
              "verification": evidence}
    if reason:
        result["review_reason"] = reason
    if run.get("error"):
        result["error"] = run["error"]
    return result

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
    except ProcessTimeoutError as exc:
        timed_out = True
        stdout_b, stderr_b = exc.output.stdout, exc.output.stderr
        stdout_trunc, stderr_trunc = exc.output.stdout_truncated, exc.output.stderr_truncated

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

    File tools publish with ``run_id`` in the event attributes (v1.5.1).
    This is advisory telemetry only; validation must not depend on this
    bounded event buffer retaining a write.
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
