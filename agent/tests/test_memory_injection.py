"""Tests for long-term memory injection into the system prompt."""

from __future__ import annotations

from pathlib import Path

import pytest

from minimax_code import app
from minimax_code.memory import MemoriesDAO, MemoryInjector, build_memory_context
from minimax_code.storage.dao.projects import ProjectsDAO
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
