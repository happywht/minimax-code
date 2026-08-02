"""Tests for long-term memory injection into the system prompt."""

from __future__ import annotations

import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from minimax_code import app
from minimax_code.ipc.builtins import _RunRecorder
from minimax_code.memory import MemoriesDAO, MemoryInjector, build_memory_context
from minimax_code.storage.dao.messages import MessagesDAO
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path


@pytest.fixture
async def async_db(tmp_path: Path) -> AsyncDatabase:
    db_path = make_temp_database_path(tmp_path)
    db = AsyncDatabase(db_path)
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_build_memory_context_includes_project_and_session_memories(
    async_db: AsyncDatabase,
) -> None:
    """Project-wide memories and session-specific memories are both included."""
    await ProjectsDAO(async_db).create(id="p1", name="Test Project")
    await SessionsDAO(async_db).create(id="s1", title="Test Session")
    dao = MemoriesDAO(async_db)
    project = await dao.create(
        content="Project-wide preference",
        project_id="p1",
        category="preference",
    )
    session = await dao.create(
        content="Session-specific fact",
        session_id="s1",
        category="fact",
    )
    # A memory for another project/session should not appear.
    await ProjectsDAO(async_db).create(id="p2", name="Other Project")
    await dao.create(content="Other project", project_id="p2")

    ctx = await build_memory_context(project_id="p1", session_id="s1", dao=dao)

    assert "## Relevant memories" in ctx
    assert project["content"] in ctx
    assert session["content"] in ctx
    assert "Other project" not in ctx


@pytest.mark.asyncio
async def test_build_memory_context_empty_when_no_matches(
    async_db: AsyncDatabase,
) -> None:
    await ProjectsDAO(async_db).create(id="p1", name="Test Project")
    dao = MemoriesDAO(async_db)
    await dao.create(content="Project memory", project_id="p1")
    ctx = await build_memory_context(project_id="missing", dao=dao)
    assert ctx == ""


@pytest.mark.asyncio
async def test_build_memory_context_empty_without_dao() -> None:
    assert await build_memory_context(dao=None) == ""


@pytest.mark.asyncio
async def test_memory_injector_build_context(async_db: AsyncDatabase) -> None:
    app._DB_SINGLETON = async_db
    try:
        await ProjectsDAO(async_db).create(id="p1", name="Test Project")
        injector = MemoryInjector(MemoriesDAO(async_db))
        await MemoriesDAO(async_db).create(content="Recall me", project_id="p1")
        ctx = await injector.build_context(project_id="p1", session_id=None, query=None)
        assert "Recall me" in ctx
    finally:
        app._DB_SINGLETON = None


@pytest.mark.asyncio
async def test_run_recorder_extracts_and_persists_memories(async_db: AsyncDatabase) -> None:
    """Assistant replies are auto-extracted into memories and counted in metadata."""
    await ProjectsDAO(async_db).create(id="p1", name="Test Project")
    await SessionsDAO(async_db).create(id="s1", title="Test Session")
    msg = await MessagesDAO(async_db).create(
        id=f"msg_{uuid.uuid4().hex[:12]}",
        session_id="s1",
        role="assistant",
        content="",
    )

    async def _emit(_event: str, _payload: dict[str, object]) -> None:
        return None

    recorder = _RunRecorder(
        dao=AgentRunsDAO(async_db),
        db=async_db,
        emit=_emit,
        session_id="s1",
        title="test run",
        assistant_message_id=msg["id"],
        project_id="p1",
    )
    await recorder.create()

    result = SimpleNamespace(
        final_text="Use TypeScript for the frontend. Prefer functional components.",
        iterations=1,
        usage=None,
        truncated=False,
        cancelled=False,
    )
    await recorder.complete(result)

    memories = await MemoriesDAO(async_db).list(session_id="s1")
    assert len(memories) == 2
    contents = {m["content"] for m in memories}
    assert "Use TypeScript for the frontend." in contents
    assert "Prefer functional components." in contents
    for m in memories:
        assert m["project_id"] == "p1"
        assert m["session_id"] == "s1"
        assert m["category"] == "fact"
        assert m["source"] == "chat"

    updated = await MessagesDAO(async_db).get(msg["id"])
    assert updated["metadata"]["memory_count"] == 2
