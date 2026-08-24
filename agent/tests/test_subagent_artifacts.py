"""v1.4.0 — sub-agent artifact protocol: BRIEF.md + read_artifact.

Commit 4 of the sub-agent lifecycle track. Every spawn anchors an
on-disk handoff directory ``<workspace_root>/.minimax/artifacts/<run_id>/``
with a ``BRIEF.md``; the main agent reads it back via the
containment-checked ``read_artifact`` tool.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.tools.artifacts import (
    BRIEF_NAME,
    COMPLETION_NAME,
    REPORT_NAME,
    ReadArtifactTool,
    ReportCompletionTool,
)
from minimax_code.agent.tools.subagents import (
    SpawnSubagentTool,
    _clone_registry_with_report,
)
from minimax_code.orchestrator.subagent import (
    SubAgentRuntime,
    set_subagent_runtime,
)
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.workspace_ctx import reset_current_root, set_current_root


@pytest.fixture
async def app_db(tmp_path: Path):
    from minimax_code import app
    from minimax_code.storage.dao.sessions import SessionsDAO

    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(app, "_DB_SINGLETON", db)
    app.set_sessions_dao(SessionsDAO(db))
    try:
        yield db
    finally:
        monkey.undo()
        app.set_sessions_dao(None)
        await db.close()


@pytest.fixture
def stub_runtime():
    prev = set_subagent_runtime(SubAgentRuntime())
    try:
        yield SubAgentRuntime()
    finally:
        set_subagent_runtime(prev)


@pytest.fixture
def workspace_root(tmp_path: Path):
    """Point the v1.3.0 workspace ContextVar at a temp project root."""
    root = tmp_path / "proj_root"
    root.mkdir()
    token = set_current_root(root)
    try:
        yield root
    finally:
        reset_current_root(token)


async def _seed_agent(db: AsyncDatabase) -> str:
    dao = AgentDAO(db)
    name = f"art_{uuid.uuid4().hex[:8]}"
    await dao.upsert(name=name, system_prompt="you are a test agent")
    return name


# ---------------------------------------------------------------------------
# BRIEF.md anchoring
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_spawn_writes_brief(app_db, stub_runtime, workspace_root):
    name = await _seed_agent(app_db)
    out = await SpawnSubagentTool().run(
        agent_name=name, prompt="review the auth flow", parent_session_id="sess_x"
    )
    assert out.success
    run_id = out.output["run_id"]

    brief = workspace_root / ".minimax" / "artifacts" / run_id / BRIEF_NAME
    assert brief.is_file()
    content = brief.read_text(encoding="utf-8")
    assert name in content
    assert "review the auth flow" in content
    assert run_id in content
    assert "sess_x" in content
    # The envelope points at the directory for follow-up read_artifact calls.
    assert out.output["artifact_dir"] == str(brief.parent)


@pytest.mark.asyncio
async def test_background_spawn_also_writes_brief(app_db, stub_runtime, workspace_root):
    name = await _seed_agent(app_db)
    out = await SpawnSubagentTool().run(
        agent_name=name, prompt="bg brief", wait=False
    )
    assert out.success
    brief = workspace_root / ".minimax" / "artifacts" / out.output["run_id"] / BRIEF_NAME
    assert brief.is_file()
    assert out.output["artifact_dir"] == str(brief.parent)


@pytest.mark.asyncio
async def test_spawn_without_root_skips_artifacts(app_db, stub_runtime):
    """No workspace root: the spawn still works, just without files."""
    name = await _seed_agent(app_db)
    out = await SpawnSubagentTool().run(agent_name=name, prompt="no root")
    assert out.success
    assert out.output["artifact_dir"] is None


# ---------------------------------------------------------------------------
# read_artifact
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_read_artifact_defaults_to_brief(app_db, stub_runtime, workspace_root):
    name = await _seed_agent(app_db)
    spawn = await SpawnSubagentTool().run(agent_name=name, prompt="read me back")
    assert spawn.success
    run_id = spawn.output["run_id"]

    out = await ReadArtifactTool().run(run_id=run_id)
    assert out.success
    assert out.output["rel_path"] == BRIEF_NAME
    assert name in out.output["content"]
    assert out.output["path"].endswith(BRIEF_NAME)


@pytest.mark.asyncio
async def test_read_artifact_named_file(app_db, stub_runtime, workspace_root):
    name = await _seed_agent(app_db)
    spawn = await SpawnSubagentTool().run(agent_name=name, prompt="named read")
    run_id = spawn.output["run_id"]
    target = workspace_root / ".minimax" / "artifacts" / run_id / "NOTES.txt"
    target.write_text("interim notes", encoding="utf-8")

    out = await ReadArtifactTool().run(run_id=run_id, rel_path="NOTES.txt")
    assert out.success
    assert out.output["content"] == "interim notes"


@pytest.mark.asyncio
async def test_read_artifact_traversal_rejected(app_db, stub_runtime, workspace_root):
    name = await _seed_agent(app_db)
    spawn = await SpawnSubagentTool().run(agent_name=name, prompt="guard me")
    run_id = spawn.output["run_id"]
    # A secret OUTSIDE the artifact directory must stay unreachable.
    secret = workspace_root / "SECRET.txt"
    secret.write_text("classified", encoding="utf-8")

    for rel in ("../SECRET.txt", "../../SECRET.txt"):
        out = await ReadArtifactTool().run(run_id=run_id, rel_path=rel)
        assert not out.success
        assert "rejected" in (out.error or "")


@pytest.mark.asyncio
async def test_read_artifact_absolute_escape_rejected(
    app_db, stub_runtime, workspace_root
):
    name = await _seed_agent(app_db)
    spawn = await SpawnSubagentTool().run(agent_name=name, prompt="abs guard")
    run_id = spawn.output["run_id"]
    outside = (workspace_root / "outside.txt").resolve()
    outside.write_text("nope", encoding="utf-8")

    out = await ReadArtifactTool().run(run_id=run_id, rel_path=str(outside))
    assert not out.success


@pytest.mark.asyncio
async def test_read_artifact_unknown_run(app_db, stub_runtime, workspace_root):
    out = await ReadArtifactTool().run(run_id="run_never_spawned")
    assert not out.success
    assert "not found" in (out.error or "")


@pytest.mark.asyncio
async def test_read_artifact_without_root(app_db, stub_runtime):
    out = await ReadArtifactTool().run(run_id="run_anything")
    assert not out.success
    assert "no workspace root" in (out.error or "")


@pytest.mark.asyncio
async def test_read_artifact_empty_run_id(app_db, stub_runtime):
    out = await ReadArtifactTool().run(run_id="   ")
    assert not out.success
    assert "run_id is required" in (out.error or "")


# ---------------------------------------------------------------------------
# report_completion (v1.4.0 commit 5)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_report_completion_writes_dual_handoff(workspace_root):
    tool = ReportCompletionTool("run_report1", agent_name="reviewer")
    out = await tool.run(
        status="completed",
        summary="Auth flow reviewed; two issues found.",
        files=["src/auth.py", "tests/test_auth.py"],
        gaps=["refresh-token rotation untested"],
        next_steps=["add rotation test"],
    )
    assert out.success
    base = workspace_root / ".minimax" / "artifacts" / "run_report1"

    report = json.loads((base / REPORT_NAME).read_text(encoding="utf-8"))
    assert report["run_id"] == "run_report1"
    assert report["agent"] == "reviewer"
    assert report["status"] == "completed"
    assert report["files"] == ["src/auth.py", "tests/test_auth.py"]
    assert report["gaps"] == ["refresh-token rotation untested"]
    assert report["next_steps"] == ["add rotation test"]

    md = (base / COMPLETION_NAME).read_text(encoding="utf-8")
    assert "Auth flow reviewed" in md
    assert "src/auth.py" in md
    assert "## Next steps" in md


@pytest.mark.asyncio
async def test_report_completion_rejects_bad_status(workspace_root):
    tool = ReportCompletionTool("run_report2")
    out = await tool.run(status="weird", summary="x")
    assert not out.success
    assert "status" in (out.error or "")


@pytest.mark.asyncio
async def test_report_completion_skips_without_root():
    tool = ReportCompletionTool("run_report3")
    out = await tool.run(status="partial", summary="no root to anchor")
    assert out.success  # soft protocol — the sub-agent did its part
    assert out.output["skipped"] is True


def test_clone_registry_scopes_injection():
    """The per-run tool reaches the clone; the global registry stays clean."""
    cloned = _clone_registry_with_report("run_report4", "general")
    assert cloned.has("report_completion")
    assert cloned.has("read_file")  # default surface preserved

    from minimax_code.agent.tools.base import get_default_registry

    assert not get_default_registry().has("report_completion")


class _ReportingRuntime(SubAgentRuntime):
    """Mimics a sub-agent that finds and calls its injected tool."""

    async def invoke(self, handle, *, session_id: str, request: str) -> dict[str, Any]:
        tool = handle.core.registry.get("report_completion")
        assert tool is not None, "report_completion must reach the sub-agent"
        report = await tool.run(status="completed", summary="done via injected tool")
        assert report.success
        return {
            "agent": handle.config.name,
            "request": request,
            "session_id": session_id,
            "text": "final answer",
            "cancelled": False,
            "iterations": 1,
            "tool_calls": [],
            "stub": False,
            "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            "truncated": False,
        }


@pytest.mark.asyncio
async def test_spawn_reports_when_subagent_calls_tool(app_db, workspace_root):
    prev = set_subagent_runtime(_ReportingRuntime())
    try:
        dao = AgentDAO(app_db)
        await dao.upsert(name="rep_agent", system_prompt="report your work")
        out = await SpawnSubagentTool().run(agent_name="rep_agent", prompt="do it")
        assert out.success
        assert out.output["reported"] is True
        assert "warning" not in out.output

        base = workspace_root / ".minimax" / "artifacts" / out.output["run_id"]
        assert (base / COMPLETION_NAME).is_file()
        assert (base / REPORT_NAME).is_file()
    finally:
        set_subagent_runtime(prev)


@pytest.mark.asyncio
async def test_spawn_flags_unreported_run(app_db, stub_runtime, workspace_root):
    """The stub runtime never reports — the envelope must say so."""
    name = await _seed_agent(app_db)
    out = await SpawnSubagentTool().run(agent_name=name, prompt="stay silent")
    assert out.success
    assert out.output["reported"] is False
    assert "did not call report_completion" in out.output["warning"]


@pytest.mark.asyncio
async def test_allowlist_agent_sees_report_completion(app_db, workspace_root):
    """allowlist mode: the appended name survives the filtered view."""
    prev = set_subagent_runtime(_ReportingRuntime())
    try:
        dao = AgentDAO(app_db)
        await dao.upsert(
            name="rep_allowlisted",
            system_prompt="narrow agent",
            tool_allowlist=["read_file"],
        )
        out = await SpawnSubagentTool().run(agent_name="rep_allowlisted", prompt="go")
        assert out.success  # invoke's tool lookup + call succeeded
        assert out.output["reported"] is True
    finally:
        set_subagent_runtime(prev)
