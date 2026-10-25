"""Codex run driver — one engine turn through the Codex App Server (M4-B).

Drives a single task via ``thread/start|resume`` + ``turn/start`` and
bridges the Codex event stream onto MiniMax Code's wire surface:

- ``item/agentMessage/delta``        → ``agent.message_chunk``
- ``item/reasoning/summaryTextDelta``→ ``agent.status`` (thinking)
- ``thread/tokenUsage/updated``      → budget accounting (M2 semantics:
  a pre-set cap interrupts the turn at the next accounting point)
- ``turn/completed`` / ``error``     → receipt
- ``execCommandApproval`` and friends→ ``permission.request`` events,
  resolved through the session's permission registry (the same
  ``permission.resolve`` RPC the native gater uses)

External session continuity (M4: 保存外部会话 ID): the Codex thread id
is stored in the run's metadata under ``codex_thread_id``; a resumed
run passes it back via ``thread/resume`` so Codex continues its own
session instead of starting a new one.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

from ..budget import BudgetState
from .codex import AppServerConnection

logger = logging.getLogger(__name__)

# Server-originated requests that mean "may I run/change something?".
_APPROVAL_METHODS = frozenset({
    "execCommandApproval", "applyPatchApproval", "fileChangeApproval",
    "item/commandExecution/requestApproval", "item/fileChange/requestApproval",
    "item/permissions/requestApproval",
})


class CodexApprovalGate:
    """Duck-typed gater: pending approval requests resolvable via the
    same ``permission.resolve`` RPC the native PermissionGater uses."""

    def __init__(self) -> None:
        self._pending: dict[str, asyncio.Future[bool]] = {}

    def pending_ids(self) -> list[str]:
        return list(self._pending)

    def has_pending(self, request_id: str) -> bool:
        return request_id in self._pending

    def resolve(self, request_id: str, decision: bool) -> bool:
        fut = self._pending.get(request_id)
        if fut is None or fut.done():
            return False
        fut.set_result(bool(decision))
        return True

    async def wait(self, request_id: str) -> bool:
        fut = self._pending.get(request_id)
        if fut is None:
            return False
        return await fut


async def drive_codex_run(
    job: Any,
    *,
    emit: Any,
    prompt: str,
    resume_thread_id: str | None = None,
    codex_cmd: list[str] | None = None,
    approval_gate: CodexApprovalGate | None = None,
    mode: str = "chat",
) -> dict[str, Any]:
    """Drive one Codex turn; returns the reply envelope (receipt).

    ``job`` is a :class:`~minimax_code.runs.RunJob` — workspace, budget
    knobs and cancel_callback all come from it. ``emit(event, data)``
    pushes wire events; exceptions propagate to the caller (the run
    finalizer marks the run failed).
    """
    from ..workspace_ctx import require_directory

    # Revalidate the admitted directory after any queue wait. Never let the
    # child process select a different working directory implicitly.
    workspace = str(require_directory(job.workspace))
    if mode not in {"chat", "plan", "execute"}:
        raise ValueError(f"Unsupported Codex execution mode: {mode}")
    budget: BudgetState | None = None
    if getattr(job, "budget_tokens", None) or getattr(job, "daily_budget_limit", None):
        budget = BudgetState(
            limit_tokens=getattr(job, "budget_tokens", None),
            daily_limit=getattr(job, "daily_budget_limit", None),
            daily_baseline=getattr(job, "daily_baseline", 0),
        )

    import os
    import shutil

    cmd_raw = os.environ.get("MINIMAX_CODE_CODEX_CMD", "").strip()
    if cmd_raw:
        codex_cmd = json.loads(cmd_raw)
    elif codex_cmd is None or codex_cmd[0] in ("codex",):
        # Windows: bare "codex" is an npm .cmd shim — CreateProcess
        # needs the resolved absolute path.
        exe = shutil.which("codex")
        base = [exe] if exe else ["codex"]
        codex_cmd = [*base, "app-server"]

    gate = approval_gate or CodexApprovalGate()
    # Strong refs for fire-and-forget emits — a bare create_task can be
    # garbage-collected before it ever runs (the task would silently
    # vanish, and with it every permission.request).
    background_tasks: set[asyncio.Task[None]] = set()

    def spawn_notify(coro: Any) -> None:
        task = asyncio.create_task(coro)
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

    conn = AppServerConnection(
        cwd=Path(workspace),
        codex_cmd=codex_cmd,
    )
    await conn.start()

    text_parts: list[str] = []
    usage: dict[str, int] = {}
    accounted_total = 0
    turn_done: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    turn_id: str | None = None
    thread_id: str | None = None
    cancelled = False

    def _emit_chunk(delta: str, done: bool) -> None:
        try:
            spawn_notify(emit("agent.message_chunk", {
                "session_id": job.session_id,
                "run_id": job.run_id,
                "message_id": job.message_id,
                "delta": delta,
                "done": done,
            }))
        except Exception:  # noqa: BLE001 — streaming is advisory
            logger.debug("codex chunk emit failed", exc_info=True)

    def on_notification(n: dict[str, Any]) -> None:
        nonlocal turn_id, thread_id, cancelled, accounted_total
        method = n.get("method", "")
        params = n.get("params") or {}
        if method == "item/agentMessage/delta":
            delta = str(params.get("delta") or params.get("text") or "")
            if delta:
                text_parts.append(delta)
                _emit_chunk(delta, False)
        elif method == "thread/tokenUsage/updated":
            # 0.163.0 reports cumulative thread totals in camelCase. Charge
            # only the increase; replayed notifications must not spend twice.
            info = (params.get("tokenUsage") or {}).get("total") or {}
            values = [info.get(key) for key in ("inputTokens", "outputTokens", "totalTokens")]
            if not all(type(value) is int and value >= 0 for value in values):
                if not turn_done.done():
                    turn_done.set_exception(ValueError("Invalid Codex token usage notification"))
                return
            inputs, outputs, total = values
            if total < accounted_total:
                return  # delayed older cumulative notification
            delta = total - accounted_total
            accounted_total = total
            usage.update(prompt_tokens=inputs, completion_tokens=outputs, total_tokens=total)
            if budget is not None:
                budget.record({"total_tokens": delta})
                if budget.would_exceed(1) and turn_id and not turn_done.done():
                    logger.warning("codex run %s: budget exhausted; interrupting", job.run_id)
                    spawn_notify(conn.request("turn/interrupt", {
                        "threadId": thread_id, "turnId": turn_id}, timeout=10))
        elif method == "turn/completed":
            if not turn_done.done():
                turn_done.set_result(params)
        elif method == "thread/status/changed":
            # The thread id arrives here for thread/start as well.
            tid = params.get("thread_id") or params.get("threadId")
            if tid:
                thread_id = tid
        elif method == "error":
            if not params.get("willRetry", False) and not turn_done.done():
                turn_done.set_exception(RuntimeError(str(params)))

    def on_server_request(req: dict[str, Any]) -> Any:
        method = req.get("method", "")
        if method not in _APPROVAL_METHODS:
            return {}  # unhandled server requests default-allow nothing
        request_id = f"codex-{req['id']}"
        params = req.get("params") or {}
        if method == "item/permissions/requestApproval":
            # Do not translate a boolean consent into an additional permission
            # profile. Unsupported grants are explicitly empty.
            return {"permissions": {}, "scope": "turn"}
        if mode == "plan":
            # A plan cannot grant an escape from its read-only policy.
            return {"decision": "decline"} if method.startswith("item/") else {"decision": "denied"}
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        gate._pending[request_id] = fut
        try:
            spawn_notify(emit("permission.request", {
                "request_id": request_id,
                "session_id": job.session_id,
                "run_id": job.run_id,
                "tool": f"codex:{method}",
                "args": {k: params.get(k) for k in ("command", "cwd", "reason")
                         if params.get(k) is not None},
                "timeout_s": 300,
            }))
        except Exception:  # noqa: BLE001
            logger.debug("approval emit failed", exc_info=True)

        async def _await_decision() -> dict[str, Any]:
            try:
                allowed = await asyncio.wait_for(fut, timeout=300)
            except TimeoutError:
                allowed = False
            finally:
                gate._pending.pop(request_id, None)
            if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                return {"decision": "accept" if allowed else "decline"}
            return {"decision": "approved" if allowed else "denied"}

        return _await_decision()

    def receive_notification(n: dict[str, Any]) -> None:
        try:
            on_notification(n)
        except Exception as exc:
            # A malformed notification must fail the owning run, not kill the
            # reader task and leave the turn waiting forever.
            if not turn_done.done():
                turn_done.set_exception(RuntimeError(f"Invalid Codex notification: {exc}"))

    conn.on_notification = receive_notification
    conn.on_server_request = on_server_request

    def _on_server_exited() -> None:
        if not turn_done.done():
            turn_done.set_exception(RuntimeError("codex app-server exited mid-turn"))
    conn.on_exited = _on_server_exited
    def _on_cancel() -> None:
        nonlocal cancelled
        cancelled = True
        if turn_id and thread_id and not turn_done.done():
            spawn_notify(conn.request("turn/interrupt", {
                "threadId": thread_id, "turnId": turn_id}, timeout=10))
    job.cancel_callback = _on_cancel

    try:
        init = await conn.initialize()
        logger.info("codex app-server initialized: %s",
                    json_safe(init)[:120])

        # Thread lifecycle — resume the stored external session when the
        # caller supplies one (M4: 后续继续使用原会话).
        cwd_params = {"cwd": workspace, "sandbox": "read-only" if mode == "plan" else "workspace-write",
                      "approvalPolicy": "never" if mode == "plan" else "on-request",
                      "approvalsReviewer": "user"}
        if resume_thread_id:
            result = await conn.request("thread/resume", {
                "threadId": resume_thread_id, **cwd_params,
            }, timeout=60)
        else:
            result = await conn.request("thread/start", cwd_params, timeout=60)
        thread_id = _extract(result, "thread", "id", "thread_id") or thread_id

        turn = await conn.request("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}]
            if isinstance(prompt, str) else prompt,
            "cwd": workspace,
            "approvalPolicy": cwd_params["approvalPolicy"],
            "approvalsReviewer": "user",
            "sandboxPolicy": ({"type": "readOnly", "networkAccess": False} if mode == "plan"
                              else {"type": "workspaceWrite", "writableRoots": [workspace],
                                    "networkAccess": False, "excludeTmpdirEnvVar": True,
                                    "excludeSlashTmp": True}),
        }, timeout=60)
        turn_id = _extract(turn, "turn", "id", "turn_id") or _extract(turn, None, "id")

        completed = await turn_done
        terminal = completed.get("turn") or {}
        status = terminal.get("status")
        if status not in {"completed", "failed", "interrupted"}:
            raise RuntimeError(f"Codex returned an unrecognized terminal status: {status!r}")
        _emit_chunk("", True)

        if cancelled:
            return _receipt(job, "", usage, cancelled=True, thread_id=thread_id)
        if status == "failed":
            receipt = _receipt(job, "".join(text_parts), usage, thread_id=thread_id,
                               budget=budget.snapshot() if budget else None)
            receipt["error"] = terminal.get("error") or {"message": "Codex turn failed"}
            receipt["run_status"] = "failed"
            return receipt
        if budget is not None and (not usage or budget.would_exceed(1)):
            return _receipt(job, "".join(text_parts), usage,
                            budget_paused=True, thread_id=thread_id,
                            budget=budget.snapshot())
        receipt = _receipt(job, "".join(text_parts), usage, thread_id=thread_id,
                           budget=budget.snapshot() if budget else None)
        if status == "interrupted":
            receipt["run_status"] = "interrupted"
            receipt["error"] = {"message": "Codex turn was interrupted"}
        return receipt
    finally:
        job.cancel_callback = None
        # Deliver queued stream events before the owning run publishes its end.
        if background_tasks:
            await asyncio.gather(*background_tasks, return_exceptions=True)
        await conn.close()
        # Initialization and terminal waits can fail concurrently on EOF.
        # Retrieve the latter exception even when initialization raised first.
        if turn_done.done() and not turn_done.cancelled():
            turn_done.exception()
        elif not turn_done.done():
            turn_done.cancel()


def _extract(payload: Any, wrapper: str | None, *keys: str) -> Any:
    """Pull an id out of a Codex response (nested ``thread``/``turn``
    objects or flat fields — the wire shape drifted across versions)."""
    data = payload if isinstance(payload, dict) else {}
    source = data.get(wrapper) if wrapper else None
    for candidate in (source if isinstance(source, dict) else {}, data):
        for key in keys:
            value = candidate.get(key)
            if value:
                return value
    return None


def _receipt(job: Any, text: str, usage: dict[str, int], *, thread_id: str | None,
             cancelled: bool = False, budget_paused: bool = False,
             budget: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "session_id": job.session_id,
        "message_id": job.message_id,
        "run_id": job.run_id,
        "text": text,
        "iterations": None,
        "cancelled": cancelled,
        "budget_paused": budget_paused,
        "budget": budget,
        "truncated": False,
        "stub": False,
        "tokens_in": usage.get("prompt_tokens"),
        "tokens_out": usage.get("completion_tokens"),
        "engine": "codex",
        "codex_thread_id": thread_id,
        "verification": None,
    }


def json_safe(obj: Any) -> str:
    try:
        return json_dumps(obj)
    except Exception:
        return repr(obj)


def json_dumps(obj: Any) -> str:
    import json

    return json.dumps(obj, ensure_ascii=False, default=str)


__all__ = ["drive_codex_run", "CodexApprovalGate"]
