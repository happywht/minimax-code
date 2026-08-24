"""v1.4.0 — sub-agent artifact protocol: BRIEF.md + read_artifact.

Commit 4 of the sub-agent lifecycle track. Every spawn anchors an
on-disk handoff directory ``<workspace_root>/.minimax/artifacts/<run_id>/``
with a ``BRIEF.md``; the main agent reads it back via the
containment-checked ``read_artifact`` tool.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from minimax_code.agent.tools.artifacts import BRIEF_NAME, ReadArtifactTool
from minimax_code.agent.tools.subagents import SpawnSubagentTool
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
