from __future__ import annotations

from unittest.mock import AsyncMock

from test_run_service import Context, native  # noqa: F401

from minimax_code.agent.tools.research_sources import ResearchSources
from minimax_code.ipc.handlers_runs import register_run_handlers
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO


async def test_research_rpc_lists_full_detail_and_enforces_scope(native, tmp_path, monkeypatch):  # noqa: F811
    from minimax_code import app
    monkeypatch.setattr(app, "ensure_db", AsyncMock(return_value=native.db))
    await ProjectsDAO(native.db).create(id="p1", name="One", root_path=str(tmp_path))
    await ProjectsDAO(native.db).create(id="p2", name="Two", root_path=str(tmp_path))
    await SessionsDAO(native.db).update("one", project_id="p1")
    run = await native.dao.create_run(session_id="one", status="running", metadata={"project_id": "p1"})
    sources = ResearchSources(native.db, "one", "p1", run["id"])
    page = {"title": "Title", "url": "https://example.org/final", "accessed_at": "2026-10-29T00:00:00Z",
            "excerpt": "x" * 2000, "truncated": False, "title_truncated": False, "excerpt_truncated": False}
    source_id = await sources.remember_page(page)
    register_run_handlers(native.server)
    list_handler, detail_handler = native.server._handlers["run.research_list"], native.server._handlers["run.research_get"]
    context = Context(native.server)
    await list_handler({"session_id": "one", "project_id": "p1", "query": "Title"}, context)
    result = context.reply.await_args.args[0]
    assert result["sources"][0]["page_source_id"] == source_id
    assert len(result["sources"][0]["excerpt"]) == 1000
    params = {"session_id": "one", "project_id": "p1", "expected_service_id": result["service_id"], "page_source_id": source_id}
    detail = Context(native.server)
    await detail_handler(params, detail)
    assert detail.reply.await_args.args[0]["source"]["excerpt"] == page["excerpt"]
    for invalid in ({**params, "project_id": "p2"}, {**params, "expected_service_id": "other"}, {**params, "session_id": "two"}):
        rejected = Context(native.server)
        await detail_handler(invalid, rejected)
        assert rejected.reply_error.await_count == 1
    await native.db.execute("DELETE FROM agent_runs WHERE id=?", (run["id"],))
    deleted = Context(native.server)
    await detail_handler(params, deleted)
    assert deleted.reply_error.await_count == 1


async def test_research_rpc_discards_result_if_session_rebound(native, tmp_path, monkeypatch):  # noqa: F811
    from minimax_code import app
    monkeypatch.setattr(app, "ensure_db", AsyncMock(return_value=native.db))
    await ProjectsDAO(native.db).create(id="p1", name="One", root_path=str(tmp_path))
    await ProjectsDAO(native.db).create(id="p2", name="Two", root_path=str(tmp_path))
    await SessionsDAO(native.db).update("one", project_id="p1")
    original = ResearchSources.list_pages
    async def rebind(self, *args):
        result = await original(self, *args)
        await SessionsDAO(native.db).update("one", project_id="p2")
        return result
    monkeypatch.setattr(ResearchSources, "list_pages", rebind)
    register_run_handlers(native.server)
    ctx = Context(native.server)
    await native.server._handlers["run.research_list"]({"session_id": "one", "project_id": "p1"}, ctx)
    assert ctx.reply_error.await_count == 1 and ctx.reply.await_count == 0
