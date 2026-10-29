from __future__ import annotations

from unittest.mock import AsyncMock

from test_run_service import native  # noqa: F401

from minimax_code.agent.tools.base import ToolResult
from minimax_code.agent.tools.generate_report_docx import GenerateReportDocxTool
from minimax_code.agent.tools.generate_table_xlsx import GenerateTableXlsxTool
from minimax_code.ipc.builtins import _RunRecorder
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO


async def test_real_report_receipt_is_bound_to_its_run(native, tmp_path, monkeypatch):  # noqa: F811
    await ProjectsDAO(native.db).create(id="p1", name="P", root_path=str(tmp_path))
    await SessionsDAO(native.db).update("one", project_id="p1")
    run = await native.dao.create_run(session_id="one", status="running", metadata={"project_id": "p1", "model_snapshot": {"engine": "native"}})
    tool = GenerateReportDocxTool(native.db, "one", "p1")
    result = await tool.run(filename="result.docx", title="Report", paragraphs=["Hello"])
    assert result.success
    recorder = _RunRecorder(dao=native.dao, db=native.db, emit=AsyncMock(), session_id="one",
                            title="task", assistant_message_id="message", project_id="p1", run_id=run["id"])
    call = {"name": "generate_report_docx", "id": "call_1"}
    await recorder.tool_result(call, result)
    steps = await native.dao.list_steps(run["id"])
    associations = [step for step in steps if (step["payload"] or {}).get("source") == "run_attachment_v1"]
    assert len(associations) == 1
    assert associations[0]["payload"]["attachment_id"] == result.output["attachment_id"]
    assert associations[0]["payload"]["sha256"] == result.output["sha256"]
    await recorder.tool_result(call, result)
    assert len([step for step in await native.dao.list_steps(run["id"])
                if (step["payload"] or {}).get("source") == "run_attachment_v1"]) == 1
    partial = ToolResult.fail("partial", output={"status": "partial", "results": [
        {"format": "docx", "status": "saved", **result.output},
        {"format": "pdf", "status": "unconfirmed"}]})
    await recorder.tool_result({"name": "generate_report_pair", "id": "pair"}, partial)
    assert len([step for step in await native.dao.list_steps(run["id"])
                if (step["payload"] or {}).get("source") == "run_attachment_v1"]) == 2
    from minimax_code.ipc.handler_utils import HandlerError
    monkeypatch.setattr("minimax_code.ipc.host_session_gate.require_host_session", AsyncMock(side_effect=HandlerError(-32000, "restricted")))
    await recorder.tool_result({"name": "generate_report_docx", "id": "restricted"}, result)
    assert len([step for step in await native.dao.list_steps(run["id"])
                if (step["payload"] or {}).get("source") == "run_attachment_v1"]) == 2


async def test_foreign_or_unconfirmed_results_do_not_gain_association(native, tmp_path):  # noqa: F811
    await ProjectsDAO(native.db).create(id="p1", name="P", root_path=str(tmp_path))
    await ProjectsDAO(native.db).create(id="p2", name="Other", root_path=str(tmp_path))
    await SessionsDAO(native.db).update("one", project_id="p1")
    await SessionsDAO(native.db).update("two", project_id="p2")
    run = await native.dao.create_run(session_id="one", status="running", metadata={"project_id": "p1", "model_snapshot": {"engine": "native"}})
    foreign = await GenerateReportDocxTool(native.db, "two", "p2").run(filename="other.docx", title="Other", paragraphs=["x"])
    assert foreign.success
    recorder = _RunRecorder(dao=native.dao, db=native.db, emit=AsyncMock(), session_id="one",
                            title="task", assistant_message_id="message", project_id="p1", run_id=run["id"])
    await recorder.tool_result({"name": "generate_report_docx", "id": "one"}, foreign)
    await recorder.tool_result({"name": "generate_report_docx", "id": "two"}, ToolResult.fail("unknown", output=foreign.output))
    await recorder.tool_result({"name": "read_public_page", "id": "three"}, foreign)
    assert not [step for step in await native.dao.list_steps(run["id"])
                if (step["payload"] or {}).get("source") == "run_attachment_v1"]


async def test_real_table_delivery_is_linked(native, tmp_path):  # noqa: F811
    await ProjectsDAO(native.db).create(id="p1", name="P", root_path=str(tmp_path))
    await SessionsDAO(native.db).update("one", project_id="p1")
    run = await native.dao.create_run(session_id="one", status="running", metadata={"project_id": "p1", "model_snapshot": {"engine": "native"}})
    tool = GenerateTableXlsxTool(native.db, "one", "p1")
    result = await tool.run("table.xlsx", "table_key", {"sheet_name": "Data",
        "columns": [{"name": "Value", "kind": "number"}], "rows": [[1]]})
    assert result.success
    recorder = _RunRecorder(dao=native.dao, db=native.db, emit=AsyncMock(), session_id="one",
                            title="task", assistant_message_id="message", project_id="p1", run_id=run["id"])
    await recorder.tool_result({"name": "generate_table_xlsx", "id": "table"}, result)
    evidence = [step for step in await native.dao.list_steps(run["id"])
                if (step["payload"] or {}).get("source") == "run_attachment_v1"]
    assert len(evidence) == 1 and evidence[0]["payload"]["attachment_id"] == result.output["attachment_id"]


async def test_association_failure_does_not_fail_tool_result(native, monkeypatch, caplog):  # noqa: F811
    run = await native.dao.create_run(session_id="one", status="running")
    recorder = _RunRecorder(dao=native.dao, db=native.db, emit=AsyncMock(), session_id="one",
                            title="task", assistant_message_id="message", project_id="p1", run_id=run["id"])
    monkeypatch.setattr("minimax_code.run_attachments.record_tool_attachments", AsyncMock(side_effect=RuntimeError("index failed")))
    result = ToolResult.ok({"attachment_id": "att_example", "sha256": "abc"})
    await recorder.tool_result({"name": "generate_report_docx", "id": "call"}, result)
    steps = await native.dao.list_steps(run["id"])
    assert len(steps) == 1 and steps[0]["status"] == "completed" and steps[0]["payload"]["success"] is True
    assert not any((step["payload"] or {}).get("source") == "run_attachment_v1" for step in steps)
    assert "Could not record task attachment association" in caplog.text


async def test_association_notification_failure_does_not_fail_result(native, monkeypatch, caplog):  # noqa: F811
    run = await native.dao.create_run(session_id="one", status="running")
    async def emit(_event, data):
        if (data["step"].get("payload") or {}).get("source") == "run_attachment_v1":
            raise RuntimeError("offline")
    recorder = _RunRecorder(dao=native.dao, db=native.db, emit=emit, session_id="one",
                            title="task", assistant_message_id="message", project_id="p1", run_id=run["id"])
    artifact = {"payload": {"source": "run_attachment_v1"}}
    monkeypatch.setattr("minimax_code.run_attachments.record_tool_attachments", AsyncMock(return_value=[artifact]))
    await recorder.tool_result({"name": "generate_report_docx", "id": "call"}, ToolResult.ok({}))
    assert "Could not notify task attachment association" in caplog.text
    assert (await native.dao.list_steps(run["id"]))[0]["status"] == "completed"
