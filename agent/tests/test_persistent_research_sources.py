from __future__ import annotations

import sqlite3
import zipfile
from io import BytesIO

from docx import Document
from test_run_service import native  # noqa: F401

from minimax_code.agent.tools import ToolRegistry
from minimax_code.agent.tools.generate_report_docx import register_report_docx_tool
from minimax_code.agent.tools.public_research import register_public_research_tools
from minimax_code.agent.tools.research_sources import ResearchSources
from minimax_code.attachments import AttachmentService
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO


async def test_source_survives_new_task_and_is_project_scoped(native, tmp_path, monkeypatch):  # noqa: F811
    db = native.db
    await ProjectsDAO(db).create(id="p1", name="One", root_path=str(tmp_path))
    await ProjectsDAO(db).create(id="p2", name="Two", root_path=str(tmp_path))
    await SessionsDAO(db).update("one", project_id="p1")
    await SessionsDAO(db).update("two", project_id="p2")
    run1 = await native.dao.create_run(session_id="one", status="running", metadata={"project_id": "p1"})
    monkeypatch.setattr("minimax_code.agent.tools.public_research._fetch", lambda url: (
        "https://example.org/final", {"content-type": "text/html"},
        ("<title>Document</title><p>" + "source " * 200 + "</p>").encode(), False))
    first = ResearchSources(db, "one", "p1", run1["id"])
    registry = ToolRegistry()
    register_public_research_tools(registry, first)
    page = await registry.dispatch("read_public_page", {"url": "https://example.org/start"})
    assert page.success
    source_id = page.output["page_source_id"]
    saved = await native.dao.get_step(source_id)
    assert saved["payload"]["page"]["excerpt"] == page.output["excerpt"]
    assert saved["payload"]["page"]["accessed_at"] == page.output["accessed_at"]

    # Verify the existing offline bundle carries the source journal unchanged.
    from minimax_code.bundle import create_bundle
    data_dir = tmp_path / "backup-source"
    data_dir.mkdir()
    snapshot = sqlite3.connect(data_dir / "data.db")
    try:
        await db.backup(snapshot)
    finally:
        snapshot.close()
    archive = tmp_path / "sources.zip"
    create_bundle(data_dir, archive)
    with zipfile.ZipFile(archive) as bundle:
        (tmp_path / "bundled.db").write_bytes(bundle.read("data.db"))
    with sqlite3.connect(tmp_path / "bundled.db") as bundled:
        assert bundled.execute("SELECT payload FROM agent_run_steps WHERE id=?", (source_id,)).fetchone()

    run2 = await native.dao.create_run(session_id="one", status="running", metadata={"project_id": "p1"})
    second = ResearchSources(db, "one", "p1", run2["id"])
    later = ToolRegistry()
    register_public_research_tools(later, second)
    register_report_docx_tool(later, db, "one", "p1", second)
    listed = await later.dispatch("list_research_sources", {"query": "Document"})
    assert listed.success and listed.output["sources"][0]["page_source_id"] == source_id
    report = await later.dispatch("generate_report_docx", {"filename": "result.docx", "title": "Report",
        "paragraphs": ["Evidence [1]"], "page_source_ids": [source_id]})
    assert report.success
    content = (await AttachmentService(db, db.path.parent).content("p1", report.output["attachment_id"]))[1]
    text = "\n".join(p.text for p in Document(BytesIO(content)).paragraphs)
    assert "https://example.org/final" in text and page.output["accessed_at"] in text
    assert "Document" in text and "source" in text and "截断" in text

    foreign_run = await native.dao.create_run(session_id="two", status="running", metadata={"project_id": "p2"})
    foreign = ResearchSources(db, "two", "p2", foreign_run["id"])
    assert await foreign.list_pages() == []
    try:
        await foreign.resolve_pages([source_id], [])
        raise AssertionError("cross-project source was exposed")
    except ValueError:
        pass

    identity = (await db.fetchone("SELECT service_id FROM artifact_service WHERE singleton=1"))["service_id"]
    await db.execute("UPDATE artifact_service SET service_id=? WHERE singleton=1", ("svc_" + "f" * 32,))
    try:
        assert await second.list_pages() == []
        try:
            await second.resolve_pages([source_id], [])
            raise AssertionError("cross-service source was exposed")
        except ValueError:
            pass
    finally:
        await db.execute("UPDATE artifact_service SET service_id=? WHERE singleton=1", (identity,))
