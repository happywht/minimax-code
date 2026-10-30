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
    workflow = await dao.get(wid)
    if workflow is None:
        raise HandlerError(-32002, f"Workflow '{wid}' not found")

    steps = workflow.get("steps") or []
    skill_steps = [s for s in steps if s.get("type") == "action" and s.get("action_type") == "run-skill"]
    if skill_steps or params.get("skill_execution") is not None:
        if len(steps) != 1 or len(skill_steps) != 1:
            raise HandlerError(-32602, "Skill workflows must contain exactly one run-skill step")
        intent = params.get("skill_execution")
        if not isinstance(intent, dict):
            raise HandlerError(-32602, "Explicit skill execution inputs are required")
        required = {"skill_id", "session_id", "request", "budget_tokens", "idempotency_key", "expected_service_id", "expected_project_id"}
        if set(intent) != required:
            raise HandlerError(-32602, "Skill execution inputs are incomplete or contain unknown fields")
        skill_id = (skill_steps[0].get("config") or {}).get("skill_id")
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

        # Namespace a user's trigger key by workflow. Stable across retries;
        # the skill admission layer rejects a changed request under that key.
        digest = hashlib.sha256(json.dumps([wid, key], ensure_ascii=False).encode()).hexdigest()
        skill_params = {**intent, "idempotency_key": f"workflow:{digest}", "admission_only": True}
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
