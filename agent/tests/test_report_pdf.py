"""PDF report generation exercises real file bytes and bound agent access."""
from __future__ import annotations

from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from pypdf import PdfReader

from minimax_code.agent.core import AgentCore
from minimax_code.agent.tools import ToolRegistry
from minimax_code.agent.tools.base import ToolResult
from minimax_code.agent.tools.generate_report_docx import register_report_docx_tool
from minimax_code.attachments import AttachmentService
from minimax_code.execution import ExecutionPolicy, execution_scope
from minimax_code.report_pdf import _font_paths, generate_pdf
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase


async def test_pdf_agent_generates_readable_chinese_and_preserves_original(tmp_path):
    if not any(path.is_file() for path in _font_paths()):
        pytest.skip("A CJK TrueType font is needed for the PDF acceptance test")
    db = AsyncDatabase(tmp_path / "data" / "test.db")
    await db.connect()
    await db.migrate()
    original = tmp_path / "报告.pdf"
    original.write_bytes(b"original file")
    await ProjectsDAO(db).create(id="p", name="project", root_path=str(tmp_path))
    await SessionsDAO(db).create(id="s", project_id="p")
    registry = ToolRegistry()
    register_report_docx_tool(registry, db, "s", "p")
    args = {"filename": "报告.pdf", "title": "资料报告", "sections": [{"heading": "第一章", "blocks": [
        {"kind": "paragraph", "text": "中文正文"},
        {"kind": "table", "rows": [["项目", "数量"], ["钢材", "十二"]]},
    ]}]}
    with execution_scope(ExecutionPolicy(read_only=True)):
        assert not (await registry.dispatch("generate_report_pdf", args)).success
    assert not (await registry.dispatch("generate_report_pdf", {**args, "project_id": "p"})).success
    result = await registry.dispatch("generate_report_pdf", args)
    assert result.success, result.error
    assert result.output["quality_status"] == "unverified"
    assert result.output["render_status"] == "not_attempted"
    service = AttachmentService(db, db.path.parent)
    metadata, data = await service.content("p", result.output["attachment_id"])
    assert metadata["parser_status"] == "parsed"
    text = "".join(page.extract_text() for page in PdfReader(BytesIO(data)).pages)
    for value in ("资料报告", "第一章", "中文正文", "项目", "钢材", "十二"):
        assert value in text
    assert original.read_bytes() == b"original file"
    await db.close()


def test_missing_pdf_font_is_actionable(monkeypatch):
    monkeypatch.setattr("minimax_code.report_pdf._font_paths", lambda: [])
    with pytest.raises(ValueError, match="MINIMAX_CODE_PDF_FONT"):
        generate_pdf("report.pdf", "报告", ["正文"])


async def test_pdf_agent_inherits_write_file_deny_and_default_ask():
    class Store:
        def lookup(self, name):
            return {"action": "deny"} if name == "write_file" else None

    class Fake(ToolRegistry):
        async def dispatch(self, name, args):
            return ToolResult.ok()

    call = {"name": "generate_report_pdf", "arguments": {}}
    no_gate = AgentCore(llm=object(), registry=Fake(), permission_store=None)
    assert not (await no_gate._dispatch_tool(call)).success
    gate = type("Gater", (), {"request_consent": AsyncMock(return_value=True)})()
    denied = AgentCore(llm=object(), registry=Fake(), permission_store=Store(), permission_gater=gate)
    assert not (await denied._dispatch_tool(call)).success
