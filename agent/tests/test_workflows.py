"""Tests for workflow engine + DAO + IPC handlers.

v0.7.0 — Workflow Engine (Stage 3).
"""

from __future__ import annotations

import os
import sys

import pytest

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from minimax_code.config import Config
from minimax_code.ipc.server import IPCServer
from minimax_code.storage.dao.workflows import WorkflowDAO
from minimax_code.storage.db import AsyncDatabase
from minimax_code.workflow import get_workflow_engine


@pytest.fixture
async def db(tmp_path):
    """In-memory database with all migrations applied."""
    d = AsyncDatabase(str(tmp_path / "test.db"))
    await d.connect()
    await d.migrate()
    yield d
    await d.close()


@pytest.fixture
def dao(db):
    return WorkflowDAO(db)


@pytest.fixture
def engine():
    """Fresh engine per test (reset singleton)."""
    import minimax_code.workflow as _wf
    _wf._ENGINE = None
    return get_workflow_engine()


@pytest.fixture
def server():
    s = IPCServer(config=Config.from_env(), stdin=sys.stdin, stdout=sys.stdout)
    from minimax_code.app import register_app_handlers
    register_app_handlers(s)
    return s


# ===========================================================================
# WorkflowDAO tests
# ===========================================================================


class TestWorkflowDAO:
    @pytest.mark.asyncio
    async def test_create_and_get(self, dao):
        wf = await dao.create(name="Test WF", trigger_type="webhook")
        assert wf["name"] == "Test WF"
        assert wf["trigger_type"] == "webhook"
        assert wf["enabled"] is True
        assert wf["run_count"] == 0
        assert wf["id"].startswith("wf_")

        fetched = await dao.get(wf["id"])
        assert fetched is not None
        assert fetched["name"] == "Test WF"

    @pytest.mark.asyncio
    async def test_create_with_steps(self, dao):
        steps = [
            {"type": "condition", "if": {"field": "branch", "op": "eq", "value": "main"}, "then_step": 1},
            {"type": "action", "action_type": "notify", "config": {"title": "Push on main"}},
        ]
        wf = await dao.create(name="PR WF", trigger_type="webhook", steps=steps, trigger_config={"repo": "test"})
        assert wf["steps"] == steps
        assert wf["trigger_config"] == {"repo": "test"}

    @pytest.mark.asyncio
    async def test_list_all(self, dao):
        await dao.create(name="WF1", trigger_type="webhook")
        await dao.create(name="WF2", trigger_type="schedule")
        await dao.create(name="WF3", trigger_type="webhook")

        entries, total = await dao.list_all()
        assert total == 3
        assert len(entries) == 3

    @pytest.mark.asyncio
    async def test_list_filter_trigger_type(self, dao):
        await dao.create(name="WF1", trigger_type="webhook")
        await dao.create(name="WF2", trigger_type="schedule")

        entries, total = await dao.list_all(trigger_type="webhook")
        assert total == 1
        assert entries[0]["trigger_type"] == "webhook"

    @pytest.mark.asyncio
    async def test_list_enabled_only(self, dao):
        wf = await dao.create(name="WF1", trigger_type="webhook")
        await dao.disable(wf["id"])

        entries, total = await dao.list_all(enabled_only=True)
        assert total == 0

    @pytest.mark.asyncio
    async def test_update(self, dao):
        wf = await dao.create(name="Old", trigger_type="webhook")
        updated = await dao.update(wf["id"], name="New", description="updated desc")
        assert updated is not None
        assert updated["name"] == "New"
        assert updated["description"] == "updated desc"

    @pytest.mark.asyncio
    async def test_update_steps(self, dao):
        wf = await dao.create(name="WF", trigger_type="webhook")
        new_steps = [{"type": "action", "action_type": "notify", "config": {}}]
        updated = await dao.update(wf["id"], steps=new_steps)
        assert updated["steps"] == new_steps

    @pytest.mark.asyncio
    async def test_enable_disable(self, dao):
        wf = await dao.create(name="WF", trigger_type="webhook")
        assert wf["enabled"] is True

        disabled = await dao.disable(wf["id"])
        assert disabled["enabled"] is False

        enabled = await dao.enable(wf["id"])
        assert enabled["enabled"] is True

    @pytest.mark.asyncio
    async def test_increment_run(self, dao):
        wf = await dao.create(name="WF", trigger_type="webhook")
        assert wf["run_count"] == 0

        await dao.increment_run(wf["id"])
        fetched = await dao.get(wf["id"])
        assert fetched["run_count"] == 1
        assert fetched["last_run_at"] is not None

    @pytest.mark.asyncio
    async def test_delete(self, dao):
        wf = await dao.create(name="WF", trigger_type="webhook")
        ok = await dao.delete(wf["id"])
        assert ok is True
        assert await dao.get(wf["id"]) is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent(self, dao):
        ok = await dao.delete("wf_nonexistent")
        assert ok is False

    @pytest.mark.asyncio
    async def test_list_by_trigger(self, dao):
        await dao.create(name="W1", trigger_type="webhook")
        await dao.create(name="W2", trigger_type="schedule")
        await dao.create(name="W3", trigger_type="webhook")

        webhook_wfs = await dao.list_by_trigger("webhook")
        assert len(webhook_wfs) == 2


# ===========================================================================
# WorkflowEngine tests
# ===========================================================================


class TestWorkflowEngine:
    @pytest.mark.asyncio
    async def test_evaluate_trigger_match_type(self, engine):
        wf = {"trigger_type": "webhook", "trigger_config": {}}
        assert await engine.evaluate_trigger(wf, {"trigger_type": "webhook"}) is True
        assert await engine.evaluate_trigger(wf, {"trigger_type": "schedule"}) is False

    @pytest.mark.asyncio
    async def test_evaluate_trigger_with_config(self, engine):
        wf = {"trigger_type": "webhook", "trigger_config": {"repo": "my-repo"}}
        assert await engine.evaluate_trigger(wf, {"trigger_type": "webhook", "repo": "my-repo"}) is True
        assert await engine.evaluate_trigger(wf, {"trigger_type": "webhook", "repo": "other"}) is False
        assert await engine.evaluate_trigger(wf, {"trigger_type": "webhook"}) is False

    @pytest.mark.asyncio
    async def test_evaluate_trigger_contains(self, engine):
        wf = {"trigger_type": "webhook", "trigger_config": {"repo": "~my-org"}}
        assert await engine.evaluate_trigger(wf, {"trigger_type": "webhook", "repo": "my-org/my-repo"}) is True
        assert await engine.evaluate_trigger(wf, {"trigger_type": "webhook", "repo": "other/repo"}) is False

    @pytest.mark.asyncio
    async def test_run_empty_workflow(self, engine):
        result = await engine.run_workflow({"steps": []}, {})
        assert result == {"steps_completed": 0, "steps_failed": 0}

    @pytest.mark.asyncio
    async def test_run_action_notify(self, engine):
        """Action steps complete even when NotificationManager is unavailable (graceful fallback)."""
        wf = {
            "id": "wf_test",
            "name": "Test",
            "steps": [
                {"type": "action", "action_type": "notify", "config": {"title": "Hello", "body": "World"}},
            ],
        }
        result = await engine.run_workflow(wf, {})
        assert result["steps_completed"] == 1
        assert result["steps_failed"] == 0

    @pytest.mark.asyncio
    async def test_run_condition_eq(self, engine):
        wf = {
            "id": "wf_test",
            "name": "Test",
            "steps": [
                {"type": "condition", "if": {"field": "branch", "op": "eq", "value": "main"}, "then_step": 1, "else_step": 2},
                {"type": "action", "action_type": "notify", "config": {"title": "On main"}},
                {"type": "action", "action_type": "notify", "config": {"title": "Not main"}},
            ],
        }
        result = await engine.run_workflow(wf, {"branch": "main"})
        # Steps: condition → skip to step 1 → notify → step 2 → notify
        assert result["steps_completed"] == 2

    @pytest.mark.asyncio
    async def test_run_condition_branch_else(self, engine):
        wf = {
            "id": "wf_test",
            "name": "Test",
            "steps": [
                {"type": "condition", "if": {"field": "branch", "op": "eq", "value": "main"}, "then_step": 1, "else_step": 2},
                {"type": "action", "action_type": "notify", "config": {"title": "On main"}},
                {"type": "action", "action_type": "notify", "config": {"title": "Not main"}},
            ],
        }
        result = await engine.run_workflow(wf, {"branch": "dev"})
        assert result["steps_completed"] == 1  # only the else-step

    @pytest.mark.asyncio
    async def test_run_unknown_step_type(self, engine):
        wf = {
            "id": "wf_test",
            "name": "Test",
            "steps": [{"type": "unknown_type"}],
        }
        result = await engine.run_workflow(wf, {})
        assert result["steps_failed"] == 1

    @pytest.mark.asyncio
    async def test_singleton(self, engine):
        e2 = get_workflow_engine()
        assert e2 is engine


# ===========================================================================
# Workflow IPC handler tests
# ===========================================================================


class TestWorkflowIPC:
    @pytest.mark.asyncio
    async def test_workflow_create_and_list(self, server, db):
        # Inject db so handler's lazy DAO can find it
        from minimax_code import app as _app
        _app._DB_SINGLETON = db

        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 1,
            "method": "workflow.create",
            "params": {"name": "My Workflow", "trigger_type": "webhook"},
        })
        assert "result" in resp
        wf = resp["result"]
        assert wf["name"] == "My Workflow"
        assert wf["trigger_type"] == "webhook"

        resp2 = await server.handle_request({
            "jsonrpc": "2.0", "id": 2,
            "method": "workflow.list",
            "params": {},
        })
        entries = resp2["result"]["entries"]
        assert len(entries) == 1

        # cleanup
        _app._DB_SINGLETON = None

    @pytest.mark.asyncio
    async def test_workflow_enable_disable(self, server, db):
        from minimax_code import app as _app
        _app._DB_SINGLETON = db

        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 1,
            "method": "workflow.create",
            "params": {"name": "WF", "trigger_type": "schedule"},
        })
        wf_id = resp["result"]["id"]

        resp2 = await server.handle_request({
            "jsonrpc": "2.0", "id": 2,
            "method": "workflow.disable",
            "params": {"id": wf_id},
        })
        assert resp2["result"]["enabled"] is False

        resp3 = await server.handle_request({
            "jsonrpc": "2.0", "id": 3,
            "method": "workflow.enable",
            "params": {"id": wf_id},
        })
        assert resp3["result"]["enabled"] is True

        _app._DB_SINGLETON = None

    @pytest.mark.asyncio
    async def test_workflow_trigger(self, server, db):
        from minimax_code import app as _app
        _app._DB_SINGLETON = db

        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 1,
            "method": "workflow.create",
            "params": {"name": "WF", "trigger_type": "webhook", "steps": []},
        })
        wf_id = resp["result"]["id"]

        resp2 = await server.handle_request({
            "jsonrpc": "2.0", "id": 2,
            "method": "workflow.trigger",
            "params": {"id": wf_id, "context": {"branch": "main"}},
        })
        assert resp2["result"]["ok"] is True

        _app._DB_SINGLETON = None

    @pytest.mark.asyncio
    async def test_workflow_delete(self, server, db):
        from minimax_code import app as _app
        _app._DB_SINGLETON = db

        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 1,
            "method": "workflow.create",
            "params": {"name": "WF", "trigger_type": "webhook"},
        })
        wf_id = resp["result"]["id"]

        resp2 = await server.handle_request({
            "jsonrpc": "2.0", "id": 2,
            "method": "workflow.delete",
            "params": {"id": wf_id},
        })
        assert resp2["result"]["deleted"] is True

        _app._DB_SINGLETON = None

    @pytest.mark.asyncio
    async def test_workflow_create_missing_name(self, server, db):
        from minimax_code import app as _app
        _app._DB_SINGLETON = db

        resp = await server.handle_request({
            "jsonrpc": "2.0", "id": 1,
            "method": "workflow.create",
            "params": {"trigger_type": "webhook"},
        })
        assert "error" in resp

        _app._DB_SINGLETON = None
