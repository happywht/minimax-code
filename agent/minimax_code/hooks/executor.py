"""Hook executor — runs hook subprocesses and parses decisions.

Fail-open contract: any failure (spawn error, timeout, non-zero exit,
garbage stdout) is recorded in :class:`HookExecutionResult` but never
raised. The caller (agent loop, R7) decides what to do with
``result.ok`` and ``result.decision``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from dataclasses import dataclass

from .types import HookConfig, HookContext, HookDecision

logger = logging.getLogger(__name__)


@dataclass
class HookExecutionResult:
    """Outcome of running one hook. Fields are always populated (fail-open)."""

    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    decision: HookDecision | None = None
    timed_out: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        """True iff the process exited 0 within the timeout, no spawn error."""
        return self.exit_code == 0 and not self.timed_out and self.error is None


class HookExecutor:
    """Runs hooks as child processes. Always fail-open."""

    async def run(self, hook: HookConfig, context: HookContext) -> HookExecutionResult:
        env = dict(os.environ)
        if hook.env:
            env.update(hook.env)
        stdin_payload = (
            context.model_dump_json().encode("utf-8") if hook.pass_stdin else None
        )
        try:
            proc = await asyncio.create_subprocess_exec(
                *hook.command,
                stdin=asyncio.subprocess.PIPE if stdin_payload is not None else None,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
        except (OSError, ValueError) as exc:
            label = hook.command[0] if hook.command else "<empty>"
            logger.warning("hook %s failed to spawn: %s", label, exc)
            return HookExecutionResult(error=f"spawn failed: {exc}")
        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(input=stdin_payload),
                timeout=hook.timeout,
            )
        except TimeoutError:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()
            logger.warning("hook %s timed out after %ss", hook.command, hook.timeout)
            return HookExecutionResult(timed_out=True)
        result = HookExecutionResult(
            exit_code=proc.returncode,
            stdout=stdout_b.decode("utf-8", errors="replace"),
            stderr=stderr_b.decode("utf-8", errors="replace"),
        )
        result.decision = _parse_decision(result.stdout)
        if result.exit_code != 0:
            logger.info(
                "hook %s exited %d (stderr: %s)",
                hook.command,
                result.exit_code,
                result.stderr.strip()[:200],
            )
        return result


def _parse_decision(stdout: str) -> HookDecision | None:
    """Best-effort JSON decision parse. ``None`` if absent or unparseable."""
    text = stdout.strip()
    if not text:
        return None
    # Tolerate leading non-JSON log lines: take the last JSON object.
    candidate = text
    if not text.startswith(("{", "[")):
        idx = text.rfind("\n{")
        if idx == -1:
            return None
        candidate = text[idx + 1 :]
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return HookDecision.model_validate(data)
    except Exception:  # noqa: BLE001 — treat garbage as "no decision"
        return None


__all__ = ["HookExecutionResult", "HookExecutor"]
