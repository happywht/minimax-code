"""Evidence-backed report source text in both generated file types."""
from __future__ import annotations

from io import BytesIO

import pytest
from docx import Document
from pypdf import PdfReader

from minimax_code.agent.tools import ToolRegistry
from minimax_code.agent.tools.generate_report_docx import register_report_docx_tool
from minimax_code.attachments import AttachmentService
from minimax_code.ipc.protocol import AttachmentGenerateDocxParams
from minimax_code.report_pdf import _font_paths
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase


async def test_pair_keeps_sources_and_order_in_actual_files(tmp_path):
    if not any(path.is_file() for path in _font_paths()):
        pytest.skip("A compatible Chinese font is needed")
    db = AsyncDatabase(tmp_path / "data" / "test.db")
    await db.connect()
    await db.migrate()
    await ProjectsDAO(db).create(id="p", name="Project", root_path=str(tmp_path))
    await SessionsDAO(db).create(id="s", project_id="p")
    registry = ToolRegistry()
    register_report_docx_tool(registry, db, "s", "p")
    sources = [{"title": "研究机构", "url": "https://example.org/p?a=1&b=2", "accessed_at": "2026-10-29"},
               {"title": "公开材料", "url": "https://example.com/report"}]
    result = await registry.dispatch("generate_report_pair", {"filename": "来源报告", "title": "报告",
        "paragraphs": ["结论 [1]，补充 [2]。"], "sources": sources})
    assert result.success and result.output["status"] == "complete"
    service = AttachmentService(db, db.path.parent)
    files = {item["format"]: (await service.content("p", item["attachment_id"]))[1]
             for item in result.output["results"]}
    paragraphs = [p.text for p in Document(BytesIO(files["docx"])).paragraphs]
    assert paragraphs[-6:] == ["参考来源（由提供者填写，未自动核验）", "[1] 研究机构",
        "网址：https://example.org/p?a=1&b=2", "访问时间：2026-10-29", "[2] 公开材料", "网址：https://example.com/report"]
    pdf = "".join(page.extract_text() for page in PdfReader(BytesIO(files["pdf"])).pages)
    assert "https://example.org/p?a=1&b=2" in pdf and "[1] 研究机构" in pdf
    assert "2026-10-29" in pdf and "[2] 公开材料" in pdf
    await db.close()


@pytest.mark.parametrize("source", [
    {"title": "x", "url": "javascript:alert(1)"},
    {"title": "x", "url": "https://user:pass@example.org/"},
    {"title": "x", "url": "https://example.org/a b"},
    {"title": "x", "url": "https://example.org", "accessed_at": "yesterday"},
    {"title": "x", "url": "https://example.org", "accessed_at": "2026-10-29T08:00:00"},
])
def test_invalid_source_rejected(source):
    with pytest.raises(ValueError):
        AttachmentGenerateDocxParams.model_validate({"project_id": "p", "expected_service_id": "svc_" + "0" * 32,
            "filename": "report.docx", "title": "Report", "paragraphs": ["text"], "sources": [source]})


def test_sources_are_optional_and_bounded():
    values = {"project_id": "p", "expected_service_id": "svc_" + "0" * 32,
              "filename": "report.docx", "title": "Report", "paragraphs": ["text"]}
    assert AttachmentGenerateDocxParams.model_validate(values).sources == []
    with pytest.raises(ValueError):
        AttachmentGenerateDocxParams.model_validate({**values, "sources": [{"title": "x", "url": "https://example.org"}] * 31})
