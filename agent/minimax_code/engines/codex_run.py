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
) -> dict[str, Any]:
    """Drive one Codex turn; returns the reply envelope (receipt).

    ``job`` is a :class:`~minimax_code.runs.RunJob` — workspace, budget
    knobs and cancel_callback all come from it. ``emit(event, data)``
    pushes wire events; exceptions propagate to the caller (the run
    finalizer marks the run failed).
    """
    budget: BudgetState | None = None
    if getattr(job, "budget_tokens", None) or getattr(job, "daily_budget_limit", None):
        budget = BudgetState(
            limit_tokens=getattr(job, "budget_tokens", None),
            daily_limit=getattr(job, "daily_budget_limit", None),
            daily_baseline=getattr(job, "daily_baseline", 0),
        )

    import os

    cmd_raw = os.environ.get("MINIMAX_CODE_CODEX_CMD", "").strip()
    codex_cmd = json.loads(cmd_raw) if cmd_raw else codex_cmd

    gate = CodexApprovalGate()
    conn = AppServerConnection(
        cwd=Path(job.workspace) if job.workspace else None,
        codex_cmd=codex_cmd,
    )
    await conn.start()

    text_parts: list[str] = []
    usage: dict[str, int] = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
    turn_done: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    turn_id: str | None = None
    thread_id: str | None = None
    cancelled = False

    def _emit_chunk(delta: str, done: bool) -> None:
        try:
            asyncio.create_task(emit("agent.message_chunk", {
                "session_id": job.session_id,
                "run_id": job.run_id,
                "message_id": job.message_id,
                "delta": delta,
                "done": done,
            }))
        except Exception:  # noqa: BLE001 — streaming is advisory
            logger.debug("codex chunk emit failed", exc_info=True)

    def on_notification(n: dict[str, Any]) -> None:
        nonlocal turn_id, thread_id, cancelled
        method = n.get("method", "")
        params = n.get("params") or {}
        if method == "item/agentMessage/delta":
            delta = str(params.get("delta") or params.get("text") or "")
            if delta:
                text_parts.append(delta)
                _emit_chunk(delta, False)
        elif method == "thread/tokenUsage/updated":
            info = (params.get("token_usage") or params.get("usage") or params) or {}
            total = int(info.get("total_tokens") or info.get("total") or 0)
            usage["prompt_tokens"] = int(info.get("input_tokens") or info.get("prompt_tokens") or 0)
            usage["completion_tokens"] = int(info.get("output_tokens") or info.get("completion_tokens") or 0)
            usage["total_tokens"] = total or (usage["prompt_tokens"] + usage["completion_tokens"])
            if budget is not None:
                budget.record(usage)
                if budget.would_exceed(0) and turn_id and not turn_done.done():
                    logger.warning("codex run %s: budget exhausted; interrupting", job.run_id)
                    asyncio.create_task(conn.request("turn/interrupt", {
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
            if not turn_done.done():
                turn_done.set_exception(RuntimeError(str(params)))

    def on_server_request(req: dict[str, Any]) -> Any:
        method = req.get("method", "")
        if method not in _APPROVAL_METHODS:
            return {}  # unhandled server requests default-allow nothing
        request_id = f"codex-{req['id']}"
        params = req.get("params") or {}
        fut: asyncio.Future[bool] = asyncio.get_running_loop().create_future()
        gate._pending[request_id] = fut
        try:
            asyncio.create_task(emit("permission.request", {
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
            return {"decision": "approved" if allowed else "denied"}

        return _await_decision()

    conn.on_notification = on_notification
    conn.on_server_request = on_server_request
    if getattr(job, "cancel_callback", None) is not None:
        def _on_cancel() -> None:
            nonlocal cancelled
            cancelled = True
            if turn_id and thread_id and not turn_done.done():
                asyncio.create_task(conn.request("turn/interrupt", {
                    "threadId": thread_id, "turnId": turn_id}, timeout=10))
        job.cancel_callback = _on_cancel

    try:
        init = await conn.initialize()
        logger.info("codex app-server initialized: %s",
                    json_safe(init)[:120])

        # Thread lifecycle — resume the stored external session when the
        # caller supplies one (M4: 后续继续使用原会话).
        if resume_thread_id:
            result = await conn.request("thread/resume", {
                "thread_id": resume_thread_id, "cwd": job.workspace,
            }, timeout=60)
        else:
            result = await conn.request("thread/start", {
                "cwd": job.workspace,
            }, timeout=60)
        thread_id = (result or {}).get("thread_id") or thread_id

        turn = await conn.request("turn/start", {
            "threadId": thread_id,
            "input": [{"type": "text", "text": prompt}]
            if isinstance(prompt, str) else prompt,
            "cwd": job.workspace,
        }, timeout=60)
        turn_id = (turn or {}).get("turn_id") or (turn or {}).get("id")

        await asyncio.wait_for(turn_done, timeout=None)
        _emit_chunk("", True)

        if cancelled:
            return _receipt(job, "", usage, cancelled=True, thread_id=thread_id)
        if budget is not None and budget.would_exceed(0):
            return _receipt(job, "".join(text_parts), usage,
                            budget_paused=True, thread_id=thread_id,
                            budget=budget.snapshot())
        return _receipt(job, "".join(text_parts), usage, thread_id=thread_id,
                        budget=budget.snapshot() if budget else None)
    finally:
        await conn.close()


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
        "tokens_in": usage["prompt_tokens"],
        "tokens_out": usage["completion_tokens"],
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
