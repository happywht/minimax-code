"""Tests for the ``memories`` DAO."""

from __future__ import annotations

from pathlib import Path

import pytest

from minimax_code.memory import MemoriesDAO
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return make_temp_database_path(tmp_path)


@pytest.fixture
async def async_db(db_path: Path) -> AsyncDatabase:
    db = AsyncDatabase(db_path)
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def dao(async_db: AsyncDatabase) -> MemoriesDAO:
    return MemoriesDAO(async_db)


@pytest.mark.asyncio
async def test_create_and_get(dao: MemoriesDAO) -> None:
    memory = await dao.create("I prefer dark mode.")
    assert memory["content"] == "I prefer dark mode."
    assert memory["category"] == "fact"
    assert memory["confidence"] == 1.0
    assert "id" in memory
    assert "created_at" in memory
    assert "updated_at" in memory

    fetched = await dao.get(memory["id"])
    assert fetched is not None
    assert fetched["id"] == memory["id"]
    assert fetched["content"] == memory["content"]


@pytest.mark.asyncio
async def test_get_missing_returns_none(dao: MemoriesDAO) -> None:
    assert await dao.get("does-not-exist") is None


@pytest.mark.asyncio
async def test_create_with_all_fields(dao: MemoriesDAO, async_db: AsyncDatabase) -> None:
    await ProjectsDAO(async_db).create(id="proj-1", name="Project 1")
    await SessionsDAO(async_db).create(id="sess-1", title="Session 1", project_id="proj-1")
    memory = await dao.create(
        "Use TypeScript strict mode.",
        project_id="proj-1",
        session_id="sess-1",
        category="preference",
        confidence=0.95,
        source="session-1",
    )
    assert memory["project_id"] == "proj-1"
    assert memory["session_id"] == "sess-1"
    assert memory["category"] == "preference"
    assert memory["confidence"] == 0.95
    assert memory["source"] == "session-1"


@pytest.mark.asyncio
async def test_create_validates_content(dao: MemoriesDAO) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        await dao.create("")
    with pytest.raises(ValueError, match="non-empty string"):
        await dao.create("   ")


@pytest.mark.asyncio
async def test_create_validates_category(dao: MemoriesDAO) -> None:
    with pytest.raises(ValueError, match="category must be one of"):
        await dao.create("text", category="invalid")


@pytest.mark.asyncio
async def test_create_validates_confidence(dao: MemoriesDAO) -> None:
    with pytest.raises(ValueError, match="confidence must be in"):
        await dao.create("text", confidence=1.5)
    with pytest.raises(ValueError, match="confidence must be in"):
        await dao.create("text", confidence=-0.1)


@pytest.mark.asyncio
async def test_delete(dao: MemoriesDAO) -> None:
    memory = await dao.create("Delete me.")
    assert await dao.delete(memory["id"]) is True
    assert await dao.get(memory["id"]) is None
    assert await dao.delete(memory["id"]) is False


@pytest.mark.asyncio
async def test_list_and_count(dao: MemoriesDAO, async_db: AsyncDatabase) -> None:
    await ProjectsDAO(async_db).create(id="p1", name="Project 1")
    await ProjectsDAO(async_db).create(id="p2", name="Project 2")
    await dao.create("First fact.", project_id="p1", category="fact")
    m2 = await dao.create(
        "Project preference.", project_id="p1", category="preference"
    )
    await dao.create("Other project.", project_id="p2", category="fact")

    all_memories = await dao.list()
    assert len(all_memories) == 3
    assert await dao.count() == 3

    p1 = await dao.list(project_id="p1")
    assert len(p1) == 2
    assert await dao.count(project_id="p1") == 2

    prefs = await dao.list(project_id="p1", category="preference")
    assert len(prefs) == 1
    assert prefs[0]["id"] == m2["id"]

    # Pagination returns one of the p1 memories.
    ordered = await dao.list(project_id="p1", limit=1)
    assert len(ordered) == 1
    assert ordered[0]["project_id"] == "p1"


@pytest.mark.asyncio
async def test_list_pagination(dao: MemoriesDAO) -> None:
    for i in range(5):
        await dao.create(f"Fact {i}.")

    page1 = await dao.list(limit=2, offset=0)
    assert len(page1) == 2
    page2 = await dao.list(limit=2, offset=2)
    assert len(page2) == 2
    page3 = await dao.list(limit=2, offset=4)
    assert len(page3) == 1


@pytest.mark.asyncio
async def test_search(dao: MemoriesDAO) -> None:
    m1 = await dao.create("Use React for the frontend.", category="decision")
    await dao.create("Use Python for the backend.", category="decision")
    await dao.create("A random lesson learned.", category="lesson")

    results = await dao.search("React")
    assert len(results) == 1
    assert results[0]["id"] == m1["id"]

    backend = await dao.search("python")
    assert len(backend) == 1
    assert backend[0]["content"].startswith("Use Python")

    two = await dao.search("the")
    assert len(two) == 2


@pytest.mark.asyncio
async def test_search_with_filters(dao: MemoriesDAO, async_db: AsyncDatabase) -> None:
    await ProjectsDAO(async_db).create(id="p1", name="Project 1")
    await ProjectsDAO(async_db).create(id="p2", name="Project 2")
    await dao.create("Apple pie recipe.", project_id="p1", category="fact")
    await dao.create("Apple stock price.", project_id="p1", category="preference")
    await dao.create("Apple orchard.", project_id="p2", category="fact")

    results = await dao.search("Apple", project_id="p1", category="fact")
    assert len(results) == 1
    assert results[0]["content"] == "Apple pie recipe."


@pytest.mark.asyncio
async def test_count_with_query(dao: MemoriesDAO) -> None:
    await dao.create("foo bar")
    await dao.create("foo baz")
    await dao.create("qux")

    assert await dao.count(query="foo") == 2
    assert await dao.count(query="baz") == 1
    assert await dao.count(query="missing") == 0


@pytest.mark.asyncio
async def test_search_validates_query(dao: MemoriesDAO) -> None:
    with pytest.raises(ValueError, match="non-empty string"):
        await dao.search("")
