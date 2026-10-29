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
import uuid
from dataclasses import dataclass
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
_APPROVAL_TIMEOUT_S = 300.0


@dataclass
class _CodexApproval:
    future: asyncio.Future[bool]
    payload: dict[str, Any]
    deadline: float


class CodexApprovalGate:
    """Duck-typed gater: pending approval requests resolvable via the
    same ``permission.resolve`` RPC the native PermissionGater uses."""

    def __init__(self, *, session_id: str | None = None, run_id: str | None = None) -> None:
        self.session_id = session_id
        self.run_id = run_id
        self.closed = False
        self._pending: dict[str, _CodexApproval] = {}

    def pending_ids(self) -> list[str]:
        return list(self._pending)

    def has_pending(self, request_id: str) -> bool:
        return request_id in self._pending

    def resolve(self, request_id: str, decision: bool) -> bool:
        item = self._pending.get(request_id)
        if (self.closed or item is None or item.future.done()
                or asyncio.get_running_loop().time() >= item.deadline):
            return False
        item.future.set_result(bool(decision))
        return True

    async def wait(self, request_id: str) -> bool:
        item = self._pending.get(request_id)
        if item is None:
            return False
        return await item.future

    def live_requests(self, session_id: str, run_id: str) -> list[dict[str, Any]]:
        if self.closed or self.session_id != session_id or self.run_id != run_id:
            return []
        now = asyncio.get_running_loop().time()
        return [{**item.payload, "remaining_ms": max(0, int((item.deadline - now) * 1000))}
                for item in self._pending.values() if not item.future.done() and item.deadline > now]

    def close_all(self) -> list[str]:
        if self.closed:
            return []
        self.closed = True
        ids = []
        for request_id, item in self._pending.items():
            ids.append(request_id)
            if not item.future.done():
                item.future.set_result(False)
        return ids


async def drive_codex_run(
    job: Any, *, emit: Any, prompt: str, resume_thread_id: str | None = None,
    codex_cmd: list[str] | None = None, approval_gate: CodexApprovalGate | None = None,
    mode: str = "chat", conversation: Any = None, post_turn: Any = None,
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
    if gate.session_id is None and gate.run_id is None:
        gate.session_id, gate.run_id = job.session_id, job.run_id
    if gate.session_id != job.session_id or gate.run_id != job.run_id:
        raise ValueError("Codex approval gate belongs to a different run")
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
    budget_interrupt_requested = False
    budget_interrupt_failed = False
    event_errors: list[BaseException] = []
    # File-change approvals identify an item but do not include its diff.
    # Retain bounded previews keyed by the full external identity, so an
    # approval can never display changes from another thread or turn.
    file_previews: dict[tuple[str, str, str], dict[str, Any]] = {}
    # Only items belonging to this turn are exposed. Map external IDs to
    # run-scoped IDs so reconnects and concurrent sessions cannot collide.
    tool_items: dict[str, dict[str, Any]] = {}

    async def tool_event(method: str, params: dict[str, Any]) -> None:
        if params.get("threadId") != thread_id or params.get("turnId") != turn_id:
            return
        item = params.get("item")
        if not isinstance(item, dict) or item.get("type") not in {"commandExecution", "fileChange"}:
            return
        item_id = item.get("id")
        if not isinstance(item_id, str) or not item_id:
            raise ValueError("Codex tool item is missing its ID")
        entry = tool_items.get(item_id)
        if entry is None:
            kind = item["type"]
            name = "codex.commandExecution" if kind == "commandExecution" else "codex.fileChange"
            args: dict[str, Any] = {}
            if kind == "commandExecution":
                for key in ("command", "cwd"):
                    if isinstance(item.get(key), str):
                        args[key] = item[key][:4000]
            else:
                preview = file_previews.get((thread_id, turn_id, item_id))
                args["changes"] = preview or {"changes": [], "unavailable": True}
            call_id = f"codex:{job.run_id}:{uuid.uuid4().hex}"
            entry = {"kind": kind, "name": name, "args": args, "id": call_id,
                     "step_id": None, "completed": False}
            tool_items[item_id] = entry
            if conversation:
                step = await job.dao.create_step(run_id=job.run_id, session_id=job.session_id,
                    kind="tool_call", title=name, summary=json.dumps(args, ensure_ascii=False)[:300],
                    tool_call_id=call_id, tool_name=name, payload={"args": args})
                entry["step_id"] = step["id"]
                await emit("run.step.started", {"run_id": job.run_id, "step": step})
            await emit("agent.tool_call", {"session_id": job.session_id, "run_id": job.run_id,
                "message_id": job.message_id, "tool_call_id": call_id, "name": name, "args": args})
        elif entry["kind"] != item["type"]:
            raise ValueError("Codex item type changed within a turn")
        if method != "item/completed" or entry["completed"]:
            return
        status = item.get("status")
        # Unknown or absent statuses must never be reported as successful.
        success = status == "completed"
        if entry["kind"] == "commandExecution":
            output = item.get("aggregatedOutput")
            output = output if isinstance(output, str) else ""
            result = {"status": status, "exit_code": item.get("exitCode"),
                      "output": output[:16000], "truncated": len(output) > 16000}
            if type(item.get("exitCode")) is int and item["exitCode"] != 0:
                success = False
        else:
            result = {"status": status, **(file_previews.get((thread_id, turn_id, item_id))
                                             or {"changes": [], "unavailable": True})}
        error = None if success else f"Codex item outcome: {status or 'unknown'}"
        if not success and result.get("exit_code") is not None:
            error += f" (exit code {result['exit_code']})"
        await finish_tool(entry, result, error)

    async def finish_tool(entry: dict[str, Any], result: dict[str, Any], error: str | None) -> None:
        if entry["completed"]:
            return
        output = json.dumps(result, ensure_ascii=False)
        if conversation:
            parent, observation = await job.dao.record_tool_result(
                run_id=job.run_id, session_id=job.session_id, parent_id=entry["step_id"],
                tool_call_id=entry["id"], tool_name=entry["name"], success=error is None,
                summary=(error or output)[:600], error=error,
                payload={"success": error is None, "output_preview": output[:1200], "result": result})
            if parent:
                await emit("run.step.completed", {"run_id": job.run_id, "step": parent})
            await emit("run.step.completed", {"run_id": job.run_id, "step": observation})
        entry["completed"] = True
        await emit("agent.tool_result", {"session_id": job.session_id, "run_id": job.run_id,
            "message_id": job.message_id, "tool_call_id": entry["id"], "name": entry["name"],
            "result": output, "error": error})

    async def interrupt() -> None:
        if turn_id and thread_id:
            await conn.request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id}, timeout=10)

    async def interrupt_cancelled_turn() -> None:
        try:
            await interrupt()
        except Exception as exc:
            if not turn_done.done():
                turn_done.set_exception(RuntimeError(f"Codex cancellation interrupt failed; turn outcome unknown: {exc}"))

    async def interrupt_budget_turn() -> None:
        nonlocal budget_interrupt_failed
        try:
            await interrupt()
        except Exception as exc:
            # A terminal notification already establishes the turn outcome.
            if turn_done.done():
                return
            budget_interrupt_failed = True
            error = RuntimeError(f"Codex budget interrupt failed; turn outcome unknown: {exc}")
            event_errors.append(error)
            turn_done.set_exception(error)

    def close_approvals() -> None:
        for request_id in gate.close_all():
            spawn_notify(emit("permission.closed", {"request_id": request_id,
                         "session_id": job.session_id, "run_id": job.run_id}))

    async def process(n: dict[str, Any]) -> None:
        nonlocal uncertain, watermark, ledger_exceeded, budget_interrupt_requested
        if "exit_error" in n:
            raise RuntimeError(n["exit_error"])
        method, params = n.get("method", ""), n.get("params") or {}
        if not isinstance(params, dict):
            raise ValueError("Invalid Codex notification params")
        tid = params.get("threadId") or params.get("thread_id")
        external_turn = params.get("turnId") or (params.get("turn") or {}).get("id")
        relevant = method in {"item/agentMessage/delta", "thread/tokenUsage/updated", "turn/completed", "turn/plan/updated", "error"}
        if relevant and ((tid and tid != thread_id) or (external_turn and external_turn != turn_id)):
            return
        if conversation and relevant and method != "error" and (not tid or not external_turn):
            raise ValueError("Invalid Codex notification: missing thread/turn identity")
        if method in {"item/started", "item/completed"}:
            await tool_event(method, params)
        elif method == "item/agentMessage/delta":
            delta = str(params.get("delta") or "")
            if conversation:
                await conversation.delta(delta)
            text_parts.append(delta)
            seq = job.live_stream.append(job.message_id, delta, False) if hasattr(job, "live_stream") else None
            await emit("agent.message_chunk", {"session_id": job.session_id, "run_id": job.run_id,
                       "message_id": job.message_id, "delta": delta, "done": False,
                       **({"stream_seq": seq} if seq is not None else {"stream_unavailable": True,
                           "stream_event_seq": job.live_stream.event_seq} if hasattr(job, "live_stream") else {}),
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
            if (not closing and not budget_interrupt_requested
                    and (ledger_exceeded or (bounded and (uncertain or budget.would_exceed(1))))):
                budget_interrupt_requested = True
                spawn_notify(interrupt_budget_turn())
        elif method == "turn/plan/updated":
            steps, explanation = params.get("plan"), params.get("explanation")
            if (not isinstance(steps, list) or (explanation is not None and not isinstance(explanation, str))
                    or any(not isinstance(step, dict) or not isinstance(step.get("step"), str)
                           or step.get("status") not in {"pending", "inProgress", "completed"} for step in steps)):
                raise ValueError("Invalid Codex plan notification")
            truncated = len(steps) > 100 or (explanation is not None and len(explanation) > 4000)
            bounded_steps = []
            for step in steps[:100]:
                truncated |= len(step["step"]) > 2000
                bounded_steps.append({"step": step["step"][:2000], "status": step["status"]})
            plan = {"explanation": explanation[:4000] if explanation is not None else None,
                    "steps": bounded_steps, "truncated": truncated}
            if conversation:
                await conversation.update_plan(plan)
                await emit("run.updated", {"run": await job.dao.get_run(job.run_id)})
        elif method == "turn/completed":
            close_approvals()
            if not turn_done.done():
                turn_done.set_result(params)
        elif method == "runtime/status":
            if conversation:
                await conversation._save(params["status"])
                conversation.runtime_status = params["status"]
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
                close_approvals()
                if not turn_done.done():
                    turn_done.set_exception(RuntimeError(f"Invalid Codex notification: {exc}"))
            finally:
                queue.task_done()

    def receive_notification(n: dict[str, Any]) -> None:
        if n.get("method") in {"item/started", "item/completed"}:
            params = n.get("params")
            if isinstance(params, dict):
                item = params.get("item")
                tid, turn = params.get("threadId"), params.get("turnId")
                if (isinstance(item, dict) and item.get("type") == "fileChange"
                        and all(isinstance(v, str) and v for v in (tid, turn, item.get("id")))
                        and isinstance(item.get("changes"), list)):
                    changes = item["changes"]
                    remaining = 16000
                    preview = []
                    truncated = len(changes) > 20
                    for change in changes[:20]:
                        if not isinstance(change, dict) or not isinstance(change.get("path"), str) or not isinstance(change.get("diff"), str):
                            truncated = True
                            continue
                        path, diff = change["path"][:1000], change["diff"][:remaining]
                        truncated |= len(path) < len(change["path"]) or len(diff) < len(change["diff"])
                        remaining -= len(diff)
                        preview.append({"path": path, "diff": diff})
                    key = (tid, turn, item["id"])
                    if key not in file_previews and len(file_previews) >= 100:
                        file_previews.pop(next(iter(file_previews)))
                    file_previews[key] = {"changes": preview, "truncated": truncated}
        if not turn_ready.is_set():
            early.append(n)
        queue.put_nowait(n)

    def on_server_request(req: dict[str, Any]) -> Any:
        method = req.get("method", "")
        if method not in _APPROVAL_METHODS:
            return {}  # unhandled server requests default-allow nothing
        params = req.get("params") or {}
        if method == "item/permissions/requestApproval":
            # Do not translate a boolean consent into an additional permission
            # profile. Unsupported grants are explicitly empty.
            return {"permissions": {}, "scope": "turn"}
        if mode == "plan":
            # A plan cannot grant an escape from its read-only policy.
            return {"decision": "decline"} if method.startswith("item/") else {"decision": "denied"}
        async def _await_decision() -> dict[str, Any]:
            await turn_ready.wait()
            rejected = {"decision": "decline"} if method.startswith("item/") else {"decision": "denied"}
            if (closing or cancelled or gate.closed or turn_done.done() or not isinstance(params, dict)
                    or ((params.get("threadId") or params.get("thread_id"))
                        and (params.get("threadId") or params.get("thread_id")) != thread_id)
                    or ((params.get("turnId") or params.get("turn_id"))
                        and (params.get("turnId") or params.get("turn_id")) != turn_id)):
                return rejected
            loop = asyncio.get_running_loop()
            request_id = f"codex:{job.run_id}:{uuid.uuid4().hex[:12]}:{req['id']}"
            fut: asyncio.Future[bool] = loop.create_future()
            deadline = loop.time() + _APPROVAL_TIMEOUT_S
            args = {k: params.get(k) for k in ("command", "cwd", "reason", "grantRoot",
                    "networkApprovalContext", "kind") if params.get(k) is not None}
            if method == "item/fileChange/requestApproval":
                item_id = params.get("itemId")
                preview = (file_previews.get((thread_id, turn_id, item_id))
                           if isinstance(item_id, str) and params.get("threadId") == thread_id
                           and params.get("turnId") == turn_id else None)
                args["file_change_preview"] = preview or {"changes": [], "unavailable": True}
            payload = {"request_id": request_id, "session_id": job.session_id, "run_id": job.run_id,
                       "tool": f"codex:{method}", "args": args,
                       "remaining_ms": max(0, int((deadline - loop.time()) * 1000))}
            gate._pending[request_id] = _CodexApproval(fut, payload, deadline)
            spawn_notify(emit("permission.request", payload))
            queue.put_nowait({"method": "runtime/status", "params": {"status": "awaiting_approval"}})
            try:
                allowed = await asyncio.wait_for(fut, timeout=max(0, deadline - loop.time()))
            except TimeoutError:
                allowed = False
            finally:
                gate._pending.pop(request_id, None)
                await emit("permission.closed", {"request_id": request_id,
                           "session_id": job.session_id, "run_id": job.run_id})
                if not turn_done.done() and not gate.closed and not gate._pending:
                    queue.put_nowait({"method": "runtime/status", "params": {"status": "running"}})
            if closing or cancelled or gate.closed or turn_done.done():
                allowed = False
            if method in {"item/commandExecution/requestApproval", "item/fileChange/requestApproval"}:
                return {"decision": "accept" if allowed else "decline"}
            return {"decision": "approved" if allowed else "denied"}

        return _await_decision()

    conn.on_notification = receive_notification
    conn.on_server_request = on_server_request
    def on_exited() -> None:
        close_approvals()
        queue.put_nowait({"exit_error": "codex app-server exited mid-turn"})
    conn.on_exited = on_exited
    def on_cancel() -> None:
        nonlocal cancelled
        if cancelled or turn_done.done():
            return
        cancelled = True
        close_approvals()
        # Before the turn is ready its identity may not exist yet. The
        # startup path sends the deferred interrupt once it is available.
        if turn_ready.is_set():
            spawn_notify(interrupt_cancelled_turn())
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
        from ..run_steer import CodexSteerController
        job.steer_controller = CodexSteerController(
            job, emit, conn, thread_id, turn_id,
            lambda: not closing and not cancelled and not turn_done.done() and not budget_interrupt_requested)
        if cancelled:
            spawn_notify(interrupt_cancelled_turn())
        completed = await turn_done
        await queue.join()
        if event_errors:
            raise event_errors[0]
        terminal = completed.get("turn") or {}
        status = terminal.get("status")
        if status not in {"completed", "failed", "interrupted"}:
            raise RuntimeError(f"Codex returned an unrecognized terminal status: {status!r}")
        if cancelled and status == "interrupted":
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
        if job.steer_controller is not None:
            job.steer_controller.close()
            job.steer_controller = None
        close_approvals()
        cleanup_error: BaseException | None = None
        validation_cancel: asyncio.CancelledError | None = None
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
            if failure is not None and turn_id and thread_id and not cancelled and not budget_interrupt_failed:
                spawn_notify(interrupt())
            if background_tasks:
                await asyncio.gather(*list(background_tasks), return_exceptions=True)
        finally:
            # Stop the producer BEFORE the final queue drain. No received event can
            # be orphaned behind the consumer's sentinel or terminal DB write.
            try:
                await conn.close()
                await queue.join()
                for entry in tool_items.values():
                    if not entry["completed"]:
                        await finish_tool(entry, {"status": "unknown"},
                                          "Codex item outcome unknown: no completion received")
                status = ("cancelled" if isinstance(failure, asyncio.CancelledError) else
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
                    if post_turn is not None and reason is None:
                        try:
                            final = await post_turn(job, conversation, final)
                        except asyncio.CancelledError as exc:
                            validation_cancel = exc
                            status = "cancelled"
                            final.update(run_status=status, cancelled=True, budget_paused=False)
                            from ..verification import verification_evidence
                            final["verification"] = verification_evidence(status=status, engine="codex")
                        except Exception as exc:
                            status = "failed"
                            reason = exc
                            final.update(run_status=status, error={"message": str(exc)})
                            from ..verification import verification_evidence
                            final["verification"] = verification_evidence(status=status, engine="codex")
                    final = await conversation.finish(status, receipt=final,
                        error=(str(reason) or type(reason).__name__) if reason else
                              f"Codex turn {status}" if status in {"failed", "interrupted"} else None)
                if result_receipt is not None:
                    result_receipt.clear()
                    result_receipt.update(final)
                seq = job.live_stream.append(job.message_id, "", True) if hasattr(job, "live_stream") else None
                await emit("agent.message_chunk", {"session_id": job.session_id, "run_id": job.run_id,
                           "message_id": job.message_id, "delta": "", "done": True,
                           **({"stream_seq": seq} if seq is not None else {"stream_unavailable": True,
                               "stream_event_seq": job.live_stream.event_seq} if hasattr(job, "live_stream") else {}),
                           "metadata": {"run_id": job.run_id, "engine": "codex", "external_turn_id": turn_id}})
            finally:
                queue.put_nowait(None)
                await worker
                if turn_done.done() and not turn_done.cancelled():
                    turn_done.exception()
                elif not turn_done.done():
                    turn_done.cancel()
        if validation_cancel is not None:
            raise validation_cancel


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
