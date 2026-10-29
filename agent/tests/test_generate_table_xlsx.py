"""Ordinary-chat XLSX generation and binding, without model or network calls."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from minimax_code.agent.core import AgentCore
from minimax_code.agent.tools import ToolRegistry
from minimax_code.agent.tools.base import ToolResult
from minimax_code.agent.tools.generate_table_xlsx import register_table_xlsx_tool
from minimax_code.execution import ExecutionPolicy, execution_scope
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase


@pytest.fixture
async def table_env(tmp_path):
    db = AsyncDatabase(tmp_path / "data" / "test.db")
    await db.connect()
    await db.migrate()
    await ProjectsDAO(db).create(id="p", name="Project", root_path=str(tmp_path))
    await ProjectsDAO(db).create(id="other", name="Other", root_path=str(tmp_path))
    sessions = SessionsDAO(db)
    await sessions.create(id="s", project_id="p")
    registry = ToolRegistry()
    register_table_xlsx_tool(registry, db, "s", "p")
    yield db, sessions, registry
    await db.close()


def args():
    return {"filename": "report.xlsx", "request_key": "intent_1", "spec": {
        "sheet_name": "数据", "columns": [{"name": "编号", "kind": "text"},
                                          {"name": "数量", "kind": "number"}],
        "rows": [["00001", 2], ["=literal", 3]]}}


async def test_xlsx_tool_real_generation_retry_and_project_binding(table_env):
    db, sessions, registry = table_env
    with execution_scope(ExecutionPolicy(read_only=True)):
        assert not (await registry.dispatch("generate_table_xlsx", args())).success
    assert not (await registry.dispatch("generate_table_xlsx", {**args(), "project_id": "other"})).success
    first = await registry.dispatch("generate_table_xlsx", args())
    assert first.success and first.output["structure_status"] == "passed"
    assert first.output["business_quality"] == "unverified"
    replay = await registry.dispatch("generate_table_xlsx", args())
    assert replay.success and replay.output["delivery_id"] == first.output["delivery_id"]
    changed = args()
    changed["spec"]["rows"] = [["other", 1]]
    assert not (await registry.dispatch("generate_table_xlsx", changed)).success
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM office_deliveries"))["n"] == 1
    await sessions.update("s", project_id="other")
    assert not (await registry.dispatch("generate_table_xlsx", args())).success


async def test_rebinding_during_generation_cannot_commit(table_env, monkeypatch):
    from minimax_code import attachment_parser

    db, sessions, registry = table_env
    original = attachment_parser.inspect_xlsx

    async def rebind(data):
        parsed = await original(data)
        await sessions.update("s", project_id="other")
        return parsed

    monkeypatch.setattr(attachment_parser, "inspect_xlsx", rebind)
    assert not (await registry.dispatch("generate_table_xlsx", args())).success
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM office_deliveries"))["n"] == 0
    assert (await db.fetchone("SELECT COUNT(*) AS n FROM attachments"))["n"] == 0


@pytest.mark.parametrize(("rules", "has_gater", "consent", "permitted"), [
    ({}, False, False, False), ({}, True, False, False), ({}, True, True, True),
    ({"write_file": "deny", "generate_table_xlsx": "allow"}, True, True, False),
    ({"write_file": "ask", "generate_table_xlsx": "allow"}, True, False, False),
])
async def test_write_permissions(table_env, rules, has_gater, consent, permitted):
    _, _, registry = table_env
    tool = registry.get("generate_table_xlsx")
    tool.run = AsyncMock(return_value=ToolResult.ok())

    class Store:
        def lookup(self, name):
            return {"action": rules[name]} if name in rules else None

    gater = type("Gater", (), {"request_consent": AsyncMock(return_value=consent)})() if has_gater else None
    core = AgentCore(llm=object(), registry=registry, permission_store=Store(), permission_gater=gater)
    result = await core._dispatch_tool({"name": "generate_table_xlsx", "arguments": args()})
    assert result.success is permitted
    assert tool.run.await_count == (1 if permitted else 0)
