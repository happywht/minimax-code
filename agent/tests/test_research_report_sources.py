from __future__ import annotations

from io import BytesIO

import pytest
from docx import Document
from pypdf import PdfReader

from minimax_code.agent.tools import ToolRegistry
from minimax_code.agent.tools.generate_report_docx import register_report_docx_tool
from minimax_code.agent.tools.public_research import register_public_research_tools
from minimax_code.agent.tools.research_sources import ResearchSources
from minimax_code.attachments import AttachmentService
from minimax_code.report_pdf import _font_paths
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase


async def test_read_page_into_real_reports_and_reject_foreign_ids(tmp_path, monkeypatch):
    if not any(path.is_file() for path in _font_paths()):
        pytest.skip("A compatible Chinese font is needed")
    db = AsyncDatabase(tmp_path / "data" / "test.db")
    await db.connect()
    await db.migrate()
    try:
        await ProjectsDAO(db).create(id="p", name="Project", root_path=str(tmp_path))
        await SessionsDAO(db).create(id="s", project_id="p")
        monkeypatch.setattr("minimax_code.agent.tools.public_research._fetch", lambda url: (
            "https://example.org/final", {"content-type": "text/html"},
            ("<title>Captured title</title><p>" + "evidence " * 200 + "</p>").encode(), False))
        registry, sources = ToolRegistry(), ResearchSources()
        register_public_research_tools(registry, sources)
        register_report_docx_tool(registry, db, "s", "p", sources)
        page = await registry.dispatch("read_public_page", {"url": "https://example.org/start"})
        assert page.success and page.output["page_source_id"]
        args = {"filename": "research", "title": "报告", "paragraphs": ["分析 [1]。"],
                "page_source_ids": [page.output["page_source_id"]]}
        result = await registry.dispatch("generate_report_pair", args)
        assert result.success
        service = AttachmentService(db, db.path.parent)
        files = {item["format"]: (await service.content("p", item["attachment_id"]))[1]
                 for item in result.output["results"]}
        docx = "\n".join(p.text for p in Document(BytesIO(files["docx"])).paragraphs)
        pdf = "\n".join(p.extract_text() for p in PdfReader(BytesIO(files["pdf"])).pages)
        for text in (docx, pdf):
            assert "Captured title" in text and "https://example.org/final" in text
            assert page.output["accessed_at"] in text and "evidence" in text
            assert "截断" in text and "未" in text
        other = ToolRegistry()
        register_report_docx_tool(other, db, "s", "p", ResearchSources())
        rejected = await other.dispatch("generate_report_docx", {
            **args, "filename": "foreign.docx"})
        assert not rejected.success and "Unknown page source ID" in rejected.error
    finally:
        await db.close()


def test_page_registry_bounds_and_missing_title():
    sources = ResearchSources()
    page = {"title": "", "url": "https://example.org", "accessed_at": "2026-10-29T00:00:00Z",
            "excerpt": "abc", "truncated": False}
    ids = [sources.remember(page) for _ in range(30)]
    assert sources.remember(page) is None
    assert sources.resolve([ids[0]], [])[0]["title"] == "（页面未提供标题）"
    with pytest.raises(ValueError):
        sources.resolve([ids[0], ids[0]], [])
