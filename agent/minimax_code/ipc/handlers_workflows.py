"""IPC handlers — ``workflow.*`` namespace.

Provides CRUD + enable/disable + manual trigger over JSON-RPC 2.0.

Methods
-------
workflow.list          Paginated list with filters
workflow.create        Create a new workflow
workflow.update        Update workflow fields
workflow.delete        Delete a workflow
workflow.enable        Enable a workflow
workflow.disable       Disable a workflow
workflow.trigger       Manually trigger a workflow run
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from typing import Any

from ..ipc.server import Context
from ..storage.dao.workflows import WorkflowDAO
from ..workflow import get_workflow_engine
from .handler_utils import HandlerError

logger = logging.getLogger(__name__)


def _skill_step_key(wid: str, key: str, index: int) -> str:
    # Index zero preserves the key used by earlier single-step workflows.
    parts = [wid, key] if index == 0 else [wid, key, index]
    return "workflow:" + hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


async def _chain_scope(db: Any, session_id: Any, service_id: Any, project_id: Any) -> None:
    from ..storage.dao.text_artifacts import ArtifactDAO
    if not isinstance(session_id, str) or not session_id.strip() or not isinstance(service_id, str) or not service_id.strip():
        raise HandlerError(-32602, "Explicit session and service identity are required")
    if project_id is not None and not isinstance(project_id, str):
        raise HandlerError(-32602, "Invalid project identity")
    session = await db.fetchone("SELECT engine, project_id FROM sessions WHERE id=?", (session_id,))
    if not session or session["engine"] != "native" or session["project_id"] != project_id or await ArtifactDAO(db).service_id() != service_id:
        raise HandlerError(-32602, "Service, Native session or project binding changed")


async def _chain_status(db: Any, wid: str, key: str, session_id: str) -> list[dict[str, Any]]:
    from ..storage.dao.runs import AgentRunsDAO
    row = await db.fetchone("SELECT steps_json FROM workflow_skill_chains WHERE workflow_id=? AND trigger_key=?", (wid, key))
    if row is None:
        raise HandlerError(-32002, "Workflow skill chain not found")
    dao = AgentRunsDAO(db)
    results = []
    for index, step in enumerate(json.loads(row["steps_json"])):
        run_row = await db.fetchone("SELECT id FROM agent_runs WHERE session_id=? AND request_key=?", (session_id, _skill_step_key(wid, key, index)))
        run = await dao.get_run(run_row["id"]) if run_row else None
        receipt = await dao.result_payload(run["id"]) if run and run["status"] == "completed" else None
        results.append({"index": index, "skill_id": step["config"]["skill_id"], "run": run,
                        "continuation_blocked_reason": _continuation_blocked_reason(run, receipt)})
    return results


def _continuation_blocked_reason(run: Any, receipt: Any) -> str | None:
    if not run:
        return "尚未接单"
    if run.get("status") != "completed":
        return f"运行状态为 {run.get('status', '未知')}"
    metadata = run.get("metadata")
    if not isinstance(metadata, dict) or not isinstance(receipt, dict):
        return "完成回执或运行信息不可用，需要核对"
    if metadata.get("truncated") or receipt.get("truncated") or receipt.get("partial"):
        return "输出被截断或结果不完整，需要核对"
    if (run.get("error") or metadata.get("budget_exhausted") or receipt.get("error")
            or receipt.get("ok") is False or receipt.get("cancelled")
            or receipt.get("budget_paused") or receipt.get("budget_exhausted")
            or receipt.get("run_status", "completed") != "completed"
            or receipt.get("status", "completed") != "completed"):
        return "运行回执显示失败或未正常完成，需要核对"
    for verification in (metadata.get("verification"), receipt.get("verification")):
        if verification is not None and not isinstance(verification, dict):
            return "验证信息无效，需要核对"
        if verification and (verification.get("requires_review") or verification.get("failed_tool_calls")
                             or verification.get("verdict") == "failed"):
            return "运行验证失败或需要核对"
    return None

# ---------------------------------------------------------------------------
# Lazy DAO factory (same pattern as handlers_notifications.py)
# ---------------------------------------------------------------------------

_DAO_ATTR = "_workflow_dao"
_DAO_LOCK_ATTR = "_workflow_dao_lock"
_FACTORY_ATTR = "_workflow_dao_factory"

def _make_workflow_dao_factory(server: Any) -> Any:
    factory = getattr(server, _FACTORY_ATTR, None)
    if factory is not None:
        return factory

    async def _factory() -> WorkflowDAO:
        dao = getattr(server, _DAO_ATTR, None)
        if dao is not None:
            return dao
        lock: asyncio.Lock = getattr(server, _DAO_LOCK_ATTR, asyncio.Lock())
        setattr(server, _DAO_LOCK_ATTR, lock)
        async with lock:
            dao = getattr(server, _DAO_ATTR, None)
            if dao is not None:
                return dao
            # Use the process-wide DB singleton — never open a
            # second connection (avoids connection leaks and
            # ``AsyncDatabase()`` without a path).
            from ..app import get_db

            db = get_db()
            if db is None:
                raise HandlerError(
                    -32004, "storage not initialised"
                )
            dao = WorkflowDAO(db)
            setattr(server, _DAO_ATTR, dao)
            return dao

    setattr(server, _FACTORY_ATTR, _factory)
    return _factory

async def _ensure_dao(ctx: Context) -> WorkflowDAO:
    factory = _make_workflow_dao_factory(ctx.server)
    return await factory()

# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

async def handle_workflow_list(params: dict[str, Any], ctx: Context) -> dict[str, Any]:
    dao = await _ensure_dao(ctx)
    limit = int(params.get("limit", 100))
    offset = int(params.get("offset", 0))
    trigger_type = params.get("trigger_type")
    enabled_only = bool(params.get("enabled_only", False))

    entries, total = await dao.list_all(
        limit=limit,
        offset=offset,
        trigger_type=trigger_type,
        enabled_only=enabled_only,
    )
    return {"entries": entries, "total": total}

async def handle_workflow_create(params: dict[str, Any], ctx: Context) -> dict[str, Any]:
    dao = await _ensure_dao(ctx)
    name = params.get("name")
    if not name:
        raise HandlerError(-32001, "Missing 'name'")
    trigger_type = params.get("trigger_type")
    if not trigger_type:
        raise HandlerError(-32001, "Missing 'trigger_type'")

    trigger_config = params.get("trigger_config")
    if isinstance(trigger_config, str):
        try:
            trigger_config = json.loads(trigger_config)
        except json.JSONDecodeError:
            trigger_config = {}

    steps = params.get("steps")
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except json.JSONDecodeError:
            steps = []

    entry = await dao.create(
        name=name,
        description=params.get("description", ""),
        trigger_type=trigger_type,
        trigger_config=trigger_config,
        steps=steps,
        enabled=bool(params.get("enabled", True)),
    )
    return entry

async def handle_workflow_update(params: dict[str, Any], ctx: Context) -> dict[str, Any]:
    dao = await _ensure_dao(ctx)
    wid = params.get("id")
    if not wid:
        raise HandlerError(-32001, "Missing 'id'")

    trigger_config = params.get("trigger_config")
    if isinstance(trigger_config, str):
        try:
            trigger_config = json.loads(trigger_config)
        except json.JSONDecodeError:
            trigger_config = None

    steps = params.get("steps")
    if isinstance(steps, str):
        try:
            steps = json.loads(steps)
        except json.JSONDecodeError:
            steps = None

    entry = await dao.update(
        wid,
        name=params.get("name"),
        description=params.get("description"),
        trigger_config=trigger_config,
        steps=steps,
    )
    if entry is None:
        raise HandlerError(-32002, f"Workflow '{wid}' not found")
    return entry

async def handle_workflow_delete(params: dict[str, Any], ctx: Context) -> dict[str, Any]:
    dao = await _ensure_dao(ctx)
    wid = params.get("id")
    if not wid:
        raise HandlerError(-32001, "Missing 'id'")
    ok = await dao.delete(wid)
    if not ok:
        raise HandlerError(-32002, f"Workflow '{wid}' not found")
    return {"deleted": True}

async def handle_workflow_enable(params: dict[str, Any], ctx: Context) -> dict[str, Any]:
    dao = await _ensure_dao(ctx)
    wid = params.get("id")
    if not wid:
        raise HandlerError(-32001, "Missing 'id'")
    entry = await dao.enable(wid)
    if entry is None:
        raise HandlerError(-32002, f"Workflow '{wid}' not found")
    return entry

async def handle_workflow_disable(params: dict[str, Any], ctx: Context) -> dict[str, Any]:
    dao = await _ensure_dao(ctx)
    wid = params.get("id")
    if not wid:
        raise HandlerError(-32001, "Missing 'id'")
    entry = await dao.disable(wid)
    if entry is None:
        raise HandlerError(-32002, f"Workflow '{wid}' not found")
    return entry

async def handle_workflow_trigger(params: dict[str, Any], ctx: Context) -> dict[str, Any]:
    """Manually trigger a workflow run."""
    dao = await _ensure_dao(ctx)
    wid = params.get("id")
    if not wid:
        raise HandlerError(-32001, "Missing 'id'")
    status = params.get("skill_status")
    if status is not None:
        required = {"idempotency_key", "session_id", "expected_service_id", "expected_project_id"}
        if not isinstance(status, dict) or set(status) != required or not isinstance(status["idempotency_key"], str) or not status["idempotency_key"].strip():
            raise HandlerError(-32602, "Explicit chain identity is required")
        db = dao._db
        await _chain_scope(db, status["session_id"], status["expected_service_id"], status["expected_project_id"])
        chain = await db.fetchone("SELECT session_id,service_id,project_id,steps_json FROM workflow_skill_chains WHERE workflow_id=? AND trigger_key=?", (wid, status["idempotency_key"]))
        if not chain or (chain["session_id"], chain["service_id"], chain["project_id"]) != (status["session_id"], status["expected_service_id"], status["expected_project_id"]):
            raise HandlerError(-32002, "Workflow skill chain not found in this scope")
        current_workflow = await dao.get(wid)
        current_steps = json.dumps(current_workflow.get("steps") or [], sort_keys=True, ensure_ascii=False, separators=(",", ":")) if current_workflow else None
        return {"skill_chain": await _chain_status(db, wid, status["idempotency_key"], status["session_id"]),
                "definition_matches": current_steps == chain["steps_json"]}
    workflow = await dao.get(wid)
    if workflow is None:
        raise HandlerError(-32002, f"Workflow '{wid}' not found")

    steps = workflow.get("steps") or []
    skill_steps = [s for s in steps if s.get("type") == "action" and s.get("action_type") == "run-skill"]
    if skill_steps or params.get("skill_execution") is not None:
        if not steps or len(steps) != len(skill_steps) or any(not isinstance((s.get("config") or {}).get("skill_id"), str) or not s["config"]["skill_id"].strip() for s in steps):
            raise HandlerError(-32602, "Only linear workflows containing exclusively run-skill steps are supported")
        intent = params.get("skill_execution")
        if not isinstance(intent, dict):
            raise HandlerError(-32602, "Explicit skill execution inputs are required")
        required = {"skill_id", "session_id", "request", "budget_tokens", "idempotency_key", "expected_service_id", "expected_project_id"}
        if set(intent) not in (required, required | {"step_index"}):
            raise HandlerError(-32602, "Skill execution inputs are incomplete or contain unknown fields")
        index = intent.get("step_index", 0)
        if type(index) is not int or index < 0 or index >= len(steps):
            raise HandlerError(-32602, "Invalid skill step index")
        skill_id = (skill_steps[index].get("config") or {}).get("skill_id")
        if not isinstance(skill_id, str) or not skill_id.strip() or intent["skill_id"] != skill_id:
            raise HandlerError(-32602, "Workflow skill changed or is missing; inspect the original run before retrying")
        key = intent["idempotency_key"]
        budget = intent["budget_tokens"]
        if not isinstance(key, str) or not key.strip() or len(key) > 128:
            raise HandlerError(-32602, "A non-empty idempotency key of at most 128 characters is required")
        if type(budget) is not int or budget <= 0:
            raise HandlerError(-32602, "A positive integer token budget is required")
        if not isinstance(intent["expected_service_id"], str) or not intent["expected_service_id"].strip():
            raise HandlerError(-32602, "Expected service identity is required")
        if intent["expected_project_id"] is not None and not isinstance(intent["expected_project_id"], str):
            raise HandlerError(-32602, "Expected project identity is invalid")

        from pydantic import ValidationError
        from .protocol import SkillInvokeParams
        skill_params = {k: v for k, v in intent.items() if k != "step_index"}
        skill_params.update(idempotency_key=_skill_step_key(wid, key, index), admission_only=True)
        try:
            SkillInvokeParams.model_validate(skill_params)
        except ValidationError as exc:
            raise HandlerError(-32602, str(exc)) from exc
        db = dao._db
        await _chain_scope(db, intent["session_id"], intent["expected_service_id"], intent["expected_project_id"])
        snapshot = json.dumps(steps, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        from ..storage.dao._base import now_iso
        async with db.transaction() as conn:
            async with conn.execute("SELECT * FROM workflow_skill_chains WHERE workflow_id=? AND trigger_key=?", (wid, key)) as cursor:
                chain = await cursor.fetchone()
            if chain is None:
                if index != 0:
                    raise HandlerError(-32602, "Start the chain with step zero")
                if len(steps) > 1:
                    async with conn.execute("SELECT id FROM agent_runs WHERE session_id=? AND request_key=?", (intent["session_id"], _skill_step_key(wid, key, 0))) as cursor:
                        if await cursor.fetchone():
                            raise HandlerError(-32602, "Legacy run exists for this key; inspect it and use a new key for a chain")
                await conn.execute("INSERT INTO workflow_skill_chains VALUES (?,?,?,?,?,?,?)", (wid, key, intent["session_id"], intent["expected_service_id"], intent["expected_project_id"], snapshot, now_iso()))
            elif (chain["session_id"], chain["service_id"], chain["project_id"], chain["steps_json"]) != (intent["session_id"], intent["expected_service_id"], intent["expected_project_id"], snapshot):
                raise HandlerError(-32602, "Chain binding or workflow steps changed; inspect the original chain")
        previous = await _chain_status(db, wid, key, intent["session_id"])
        for item in previous[:index]:
            if item["continuation_blocked_reason"]:
                raise HandlerError(-32602, f"步骤 {item['index']} 不能继续：{item['continuation_blocked_reason']}")
        handler = ctx.server._handlers.get("skill.invoke")
        if handler is None:
            raise HandlerError(-32004, "Skill admission is unavailable")

        class CaptureContext:
            server = ctx.server
            request_id = ctx.request_id

            async def reply(self, value: Any) -> None:
                self.result = value

            async def reply_error(self, code: int, message: str, data: Any = None) -> None:
                self.error = (code, message, data)

            async def emit(self, *args: Any, **kwargs: Any) -> None:
                await ctx.emit(*args, **kwargs)

            async def notify(self, *args: Any, **kwargs: Any) -> None:
                await ctx.notify(*args, **kwargs)

        capture = CaptureContext()
        await handler(skill_params, capture)
        if hasattr(capture, "error"):
            raise HandlerError(*capture.error)
        if not hasattr(capture, "result"):
            raise HandlerError(-32603, "Skill admission result is unconfirmed; inspect the run before retrying")
        return {"ok": True, "steps_completed": 0, "steps_failed": 0, "errors": [],
                "skill_admission": capture.result}

    context = params.get("context") or {}
    engine = get_workflow_engine()
    result = await engine.run_workflow(workflow, context)
    await dao.increment_run(wid)
    return {"ok": result["steps_failed"] == 0, **result}

# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_workflow_handlers(server: Any) -> None:
    server.register("workflow.list", handle_workflow_list)
    server.register("workflow.create", handle_workflow_create)
    server.register("workflow.update", handle_workflow_update)
    server.register("workflow.delete", handle_workflow_delete)
    server.register("workflow.enable", handle_workflow_enable)
    server.register("workflow.disable", handle_workflow_disable)
    server.register("workflow.trigger", handle_workflow_trigger)
    logger.info("Registered workflow.* handlers")

__all__ = ["register_workflow_handlers"]
