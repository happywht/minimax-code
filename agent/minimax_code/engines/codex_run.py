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

from ..budget import BudgetExhausted, BudgetState
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
    job: Any, *, emit: Any, prompt: str, resume_thread_id: str | None = None,
    codex_cmd: list[str] | None = None, approval_gate: CodexApprovalGate | None = None,
    mode: str = "chat", conversation: Any = None,
) -> dict[str, Any]:
    """One ordered turn. Persist each event before display and drain on every exit."""
    import os
    import shutil

    from ..workspace_ctx import require_directory

    workspace = str(require_directory(job.workspace))
    if mode not in {"chat", "plan", "execute"}:
        raise ValueError(f"Unsupported Codex execution mode: {mode}")
    budget = BudgetState(limit_tokens=getattr(job, "budget_tokens", None),
                         daily_limit=getattr(job, "daily_budget_limit", None),
                         daily_baseline=getattr(job, "daily_baseline", 0))
    bounded = budget.limit_tokens is not None or budget.daily_limit is not None
    cmd_raw = os.environ.get("MINIMAX_CODE_CODEX_CMD", "").strip()
    if cmd_raw:
        codex_cmd = json.loads(cmd_raw)
    elif codex_cmd is None or codex_cmd[0] == "codex":
        codex_cmd = [shutil.which("codex") or "codex", "app-server"]
    gate = approval_gate or CodexApprovalGate()
    background_tasks: set[asyncio.Task[Any]] = set()

    def spawn_notify(coro: Any) -> None:
        task = asyncio.create_task(coro)
        background_tasks.add(task)
        task.add_done_callback(background_tasks.discard)

    conn = AppServerConnection(cwd=Path(workspace), codex_cmd=codex_cmd)
    text_parts: list[str] = []
    usage: dict[str, int] = {}
    watermark = {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0}
    uncertain = False
    ledger_exceeded = False
    turn_done: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
    turn_ready = asyncio.Event()
    queue: asyncio.Queue[Any] = asyncio.Queue()
    early: list[dict[str, Any]] = []
    turn_id: str | None = None
    thread_id: str | None = None
    cancelled = False
    turn_requested = False
    closing = False
    event_errors: list[BaseException] = []

    async def interrupt() -> None:
        if turn_id and thread_id:
            await conn.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=10)

    async def process(n: dict[str, Any]) -> None:
        nonlocal uncertain, watermark, ledger_exceeded
        if "exit_error" in n:
            raise RuntimeError(n["exit_error"])
        method, params = n.get("method", ""), n.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("Invalid Codex notification params")
        tid = params.get("threadId") or params.get("thread_id")
        external_turn = params.get("turnId") or (params.get("turn") or {}).get("id")
        relevant = method in {"item/agentMessage/delta", "thread/tokenUsage/updated", "turn/completed", "error"}
        if relevant and ((tid and tid != thread_id) or (external_turn and external_turn != turn_id)):
            return
        if conversation and relevant and method != "error" and (not tid or not external_turn):
            raise ValueError("Invalid Codex notification: missing thread/turn identity")
        if method == "item/agentMessage/delta":
            delta = str(params.get("delta") or "")
            if conversation:
                await conversation.delta(delta)
            text_parts.append(delta)
            await emit("agent.message_chunk", {"session_id": job.session_id, "run_id": job.run_id,
                       "message_id": job.message_id, "delta": delta, "done": False,
                       "metadata": {"run_id": job.run_id, "engine": "codex", "external_turn_id": turn_id}})
        elif method == "thread/tokenUsage/updated":
            info = (params.get("tokenUsage") or {}).get("total") or {}
            if conversation:
                certain = await conversation.record_usage(info)
                observed = conversation.usage or {}
                usage.update(prompt_tokens=observed.get("inputTokens"),
                             completion_tokens=observed.get("outputTokens"),
                             total_tokens=observed.get("totalTokens"))
                uncertain = not certain
                budget.used_tokens = int(observed.get("totalTokens") or 0)
                if conversation.budget_handle is not None:
                    ledger_exceeded = await conversation.budget_exceeded()
            else:
                keys = ("inputTokens", "outputTokens", "totalTokens")
                if not all(type(info.get(k)) is int and info[k] >= 0 for k in keys):
                    raise ValueError("Invalid Codex token usage notification")
                if any(info[k] < watermark[k] for k in keys):
                    uncertain = True
                elif not uncertain:
                    watermark = {k: info[k] for k in keys}
                    usage.update(prompt_tokens=info["inputTokens"], completion_tokens=info["outputTokens"],
                                 total_tokens=info["totalTokens"])
                    budget.used_tokens = info["totalTokens"]
            if not closing and (ledger_exceeded or (bounded and (uncertain or budget.would_exceed(1)))):
                spawn_notify(interrupt())
        elif method == "turn/completed":
            if not turn_done.done():
                turn_done.set_result(params)
        elif method == "runtime/status":
            if conversation:
                await conversation._save(params["status"])
                await emit("run.updated", {"run": await job.dao.get_run(job.run_id)})
        elif method == "item/reasoning/summaryTextDelta":
            await emit("agent.status", {"session_id": job.session_id, "run_id": job.run_id, "status": "thinking"})
        elif method == "error" and not params.get("willRetry", False):
            raise RuntimeError(str(params))

    async def consume() -> None:
        await turn_ready.wait()
        while True:
            n = await queue.get()
            try:
                if n is None:
                    return
                await process(n)
            except Exception as exc:
                event_errors.append(exc)
                if not turn_done.done():
                    turn_done.set_exception(RuntimeError(f"Invalid Codex notification: {exc}"))
            finally:
                queue.task_done()

    def receive_notification(n: dict[str, Any]) -> None:
        if not turn_ready.is_set():
            early.append(n)
        queue.put_nowait(n)

    def on_server_request(req: dict[str, Any]) -> Any:
        method = req.get("method", "")
        if method not in _APPROVAL_METHODS:
            return {}  # unhandled server requests default-allow nothing
        request_id = f"codex:{job.run_id}:{req['id']}"
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
            queue.put_nowait({"method": "runtime/status", "params": {"status": "awaiting_approval"}})
            try:
                allowed = await asyncio.wait_for(fut, timeout=300)
            except TimeoutError:
                allowed = False
            finally:
                gate._pending.pop(request_id, None)
                if not turn_done.done():
                    queue.put_nowait({"method": "runtime/status", "params": {"status": "running"}})
            if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                return {"decision": "accept" if allowed else "decline"}
            return {"decision": "approved" if allowed else "denied"}

        return _await_decision()

    conn.on_notification = receive_notification
    conn.on_server_request = on_server_request
    conn.on_exited = lambda: queue.put_nowait({"exit_error": "codex app-server exited mid-turn"})
    def on_cancel() -> None:
        nonlocal cancelled
        cancelled = True
        spawn_notify(interrupt())
    job.cancel_callback = on_cancel
    worker = asyncio.create_task(consume(), name=f"codex-events:{job.run_id}")
    result_receipt = None
    failure: BaseException | None = None
    try:
        await conn.start()
        await conn.initialize()
        cwd_params = {"cwd": workspace, "sandbox": "read-only" if mode == "plan" else "workspace-write",
                      "approvalPolicy": "never" if mode == "plan" else "on-request", "approvalsReviewer": "user"}
        if conversation:
            cwd_params.update({k: conversation.state[k] for k in ("model", "modelProvider", "reasoningEffort")
                               if k in conversation.state})
        result = await conn.request("thread/resume" if resume_thread_id else "thread/start",
                     {**cwd_params, **({"threadId": resume_thread_id} if resume_thread_id else {})}, timeout=60)
        thread_id = _extract(result, "thread", "id", "thread_id")
        if not thread_id or (resume_thread_id and thread_id != resume_thread_id):
            raise ValueError("Codex returned a missing or mismatched thread ID")
        if conversation:
            await conversation.bind(result)
            await conversation.begin_attempt()
        turn_requested = True
        turn = await conn.request("turn/start", {
            "threadId": thread_id, "input": [{"type": "text", "text": prompt}], "cwd": workspace,
            "approvalPolicy": cwd_params["approvalPolicy"], "approvalsReviewer": "user",
            "sandboxPolicy": ({"type": "readOnly", "networkAccess": False} if mode == "plan"
                else {"type": "workspaceWrite", "writableRoots": [workspace], "networkAccess": False,
                      "excludeTmpdirEnvVar": True, "excludeSlashTmp": True}),
        }, timeout=60)
        turn_id = _extract(turn, "turn", "id", "turn_id") or _extract(turn, None, "id")
        if not isinstance(turn_id, str) or not turn_id:
            raise ValueError("Codex returned a missing turn ID")
        if conversation:
            await conversation.start_turn(turn_id)
        turn_ready.set()
        completed = await turn_done
        await queue.join()
        if event_errors:
            raise event_errors[0]
        terminal = completed.get("turn") or {}
        status = terminal.get("status")
        if status not in {"completed", "failed", "interrupted"}:
            raise RuntimeError(f"Codex returned an unrecognized terminal status: {status!r}")
        if cancelled:
            status = "cancelled"
        elif status != "failed" and (ledger_exceeded or (conversation is not None and (not usage or uncertain)) or (bounded and (not usage or uncertain or budget.would_exceed(1)))):
            status = "budget_paused"
        result_receipt = _receipt(job, "".join(text_parts), usage, thread_id=thread_id,
                           cancelled=status == "cancelled", budget_paused=status == "budget_paused",
                           budget=budget.snapshot() if bounded else None)
        result_receipt.update(run_status=status, external_turn_id=turn_id,
                              usage_certainty=conversation.certainty if conversation else ("uncertain" if uncertain else "observed" if usage else "unknown"))
        if terminal.get("error") or status in {"failed", "interrupted"}:
            result_receipt["error"] = terminal.get("error") or {"message": f"Codex turn {status}"}
        return result_receipt
    except BudgetExhausted as exc:
        result_receipt = _receipt(job, "".join(text_parts), usage, thread_id=thread_id,
                                  budget_paused=True, budget=None)
        result_receipt.update(run_status="budget_paused", budget_pause_reason=str(exc))
        return result_receipt
    except BaseException as exc:
        failure = exc
        raise
    finally:
        job.cancel_callback = None
        closing = True
        cleanup_error: BaseException | None = None
        try:
            # Producer stays alive while interrupt settles. Its final notifications
            # are still consumed and persisted, including replies to turn/interrupt.
            try:
                if not turn_ready.is_set() and conversation and thread_id and turn_requested:
                    prior = set(conversation.state.get("known_turn_ids") or [])
                    candidates = {p.get("turnId") or (p.get("turn") or {}).get("id")
                                  for n in early if isinstance((p := n.get("params")), dict)
                                  and p.get("threadId") == thread_id}
                    candidates.discard(None)
                    candidates -= prior
                    if len(candidates) == 1:
                        turn_id = next(iter(candidates))
                        await conversation.start_turn(turn_id)
            except BaseException as exc:
                cleanup_error = exc
            finally:
                turn_ready.set()
            if failure is not None and turn_id and thread_id and not cancelled:
                spawn_notify(interrupt())
            if background_tasks:
                await asyncio.gather(*list(background_tasks), return_exceptions=True)
        finally:
            # Stop the producer BEFORE the final queue drain. No received event can
            # be orphaned behind the consumer's sentinel or terminal DB write.
            try:
                await conn.close()
                await queue.join()
                status = ("cancelled" if isinstance(failure, asyncio.CancelledError) or cancelled else
                          "failed" if failure is not None or cleanup_error or event_errors else
                          (result_receipt or {}).get("run_status", "failed"))
                if status not in {"failed", "cancelled"} and (ledger_exceeded or (bounded and (
                        not usage or uncertain or budget.would_exceed(1)))):
                    status = "budget_paused"
                final = _receipt(job, "".join(text_parts), usage, thread_id=thread_id,
                                 cancelled=status == "cancelled", budget_paused=status == "budget_paused",
                                 budget=budget.snapshot() if bounded else None)
                final.update(run_status=status, external_turn_id=turn_id,
                    usage_certainty=conversation.certainty if conversation else (
                        "uncertain" if uncertain else "observed" if usage else "unknown"))
                reason = failure or cleanup_error or (event_errors[0] if event_errors else None)
                if reason is None and (result_receipt or {}).get("error"):
                    reason = RuntimeError(str(result_receipt["error"]))
                if reason or status in {"failed", "interrupted"}:
                    final["error"] = {"message": str(reason) if reason else f"Codex turn {status}"}
                if conversation and conversation.bound:
                    final = await conversation.finish(status, receipt=final,
                        error=(str(reason) or type(reason).__name__) if reason else
                              f"Codex turn {status}" if status in {"failed", "interrupted"} else None)
                if result_receipt is not None:
                    result_receipt.clear()
                    result_receipt.update(final)
                await emit("agent.message_chunk", {"session_id": job.session_id, "run_id": job.run_id,
                           "message_id": job.message_id, "delta": "", "done": True,
                           "metadata": {"run_id": job.run_id, "engine": "codex", "external_turn_id": turn_id}})
            finally:
                queue.put_nowait(None)
                await worker
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
