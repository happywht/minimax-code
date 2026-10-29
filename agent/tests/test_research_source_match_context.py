from __future__ import annotations

from test_run_service import native  # noqa: F401

from minimax_code.agent.tools.research_sources import ResearchSources
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO


async def test_saved_excerpt_match_context_across_sessions(native, tmp_path):  # noqa: F811
    db = native.db
    await ProjectsDAO(db).create(id="research-project", name="Research", root_path=str(tmp_path))
    await ProjectsDAO(db).create(id="other-project", name="Other", root_path=str(tmp_path))
    await SessionsDAO(db).update("one", project_id="research-project")
    await SessionsDAO(db).update("two", project_id="other-project")
    original = ResearchSources(db, "one", "research-project")
    identity = await original._binding()
    excerpt = "前" * 1300 + "NeEdLe" + "后" * 300
    source_id, _ = await original.remember_manual_page({
        "title": "Record", "url": "https://example.org/", "accessed_at": "2026-01-01T00:00:00Z",
        "excerpt": excerpt, "truncated": True, "title_truncated": False, "excerpt_truncated": True,
    }, identity)
    # A later session of the same project can find the saved passage, without a network call.
    await SessionsDAO(db).update("two", project_id="research-project")
    later = ResearchSources(db, "two", "research-project")
    rows = await later.list_pages("needle")
    assert len(rows) == 1 and rows[0]["page_source_id"] == source_id
    match = rows[0]["excerpt_match"]
    assert match == {"text": excerpt[1180:1426], "start": 1180, "end": 1426,
                     "match_start": 1300, "match_end": 1306}
    assert "NeEdLe" not in rows[0]["excerpt"]
    assert "excerpt_match" not in (await later.list_pages("Record"))[0]
    assert "excerpt_match" not in (await later.list_pages())[0]
    await SessionsDAO(db).update("two", project_id="other-project")
    assert await ResearchSources(db, "two", "other-project").list_pages("needle") == []
