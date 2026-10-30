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
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar

from ..execution import communicate_bounded, spawn_exec, terminate_tree
from .types import HookConfig, HookContext, HookDecision

logger = logging.getLogger(__name__)
_hook_cancel: ContextVar[asyncio.Event | None] = ContextVar("hook_cancel", default=None)


@contextmanager
def hook_cancellation_scope(event: asyncio.Event):
    token = _hook_cancel.set(event)
    try:
        yield
    finally:
        _hook_cancel.reset(token)


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
        cancel_event = _hook_cancel.get()
        if cancel_event is not None and cancel_event.is_set():
            return HookExecutionResult(error="hook cancelled before start")
        stdin_payload = (
            context.model_dump_json().encode("utf-8") if hook.pass_stdin else None
        )
        try:
            proc = await spawn_exec(
                *hook.command,
                stdin=asyncio.subprocess.PIPE if stdin_payload is not None else asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=hook.env,
                cwd=context.cwd,
            )
        except (OSError, ValueError) as exc:
            label = hook.command[0] if hook.command else "<empty>"
            logger.warning("hook %s failed to spawn: %s", label, exc)
            return HookExecutionResult(error=f"spawn failed: {exc}")
        capture_started = False

        async def capture():
            nonlocal capture_started
            capture_started = True
            return await communicate_bounded(proc, input=stdin_payload, timeout=hook.timeout, limit=64 * 1024)

        try:
            if cancel_event is None:
                captured = await capture()
            else:
                owned = asyncio.create_task(capture())
                cancelled = asyncio.create_task(cancel_event.wait())
                try:
                    done, _ = await asyncio.wait((owned, cancelled), return_when=asyncio.FIRST_COMPLETED)
                    if cancelled in done:
                        return HookExecutionResult(error="hook cancelled; side effects may have occurred")
                    captured = owned.result()
                finally:
                    for task in (owned, cancelled):
                        if not task.done():
                            task.cancel()
                    async def cleanup():
                        await asyncio.gather(owned, cancelled, return_exceptions=True)
                        if not capture_started:
                            await terminate_tree(proc)
                    from ..budget_ledger import owned_cleanup
                    await owned_cleanup(cleanup())
            stdout_b, stderr_b = captured.stdout, captured.stderr
        except TimeoutError:
            logger.warning("hook %s timed out after %ss", hook.command, hook.timeout)
            return HookExecutionResult(timed_out=True)
        if captured.stdout_truncated or captured.stderr_truncated:
            return HookExecutionResult(exit_code=proc.returncode, error="hook output limit exceeded")
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
