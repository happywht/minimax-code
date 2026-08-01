"""Tests for the ``memory.*`` IPC handlers."""

from __future__ import annotations

from pathlib import Path

import pytest

from minimax_code import app
from minimax_code.ipc.client import IPCClient
from minimax_code.ipc.handlers_memory import register_memory_handlers
from minimax_code.storage.dao.projects import ProjectsDAO
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
async def client(async_db: AsyncDatabase) -> IPCClient:
    """Build an IPC client with the memory handlers wired to a temp DB."""
    app._DB_SINGLETON = async_db
    await ProjectsDAO(async_db).create(id="p1", name="Test Project")
    try:
        ipc = IPCClient()
        register_memory_handlers(ipc.server)
        yield ipc
    finally:
        app._DB_SINGLETON = None


@pytest.mark.asyncio
async def test_memory_list_empty(client: IPCClient) -> None:
    result = await client.request("memory.list", {})
    assert result == {"memories": [], "total": 0}


@pytest.mark.asyncio
async def test_memory_add_and_list(client: IPCClient) -> None:
    added = await client.request(
        "memory.add",
        {
            "content": "Prefer tabs over spaces.",
            "project_id": "p1",
            "category": "preference",
            "confidence": 0.9,
            "source": "chat",
        },
    )
    memory = added["memory"]
    assert memory["content"] == "Prefer tabs over spaces."
    assert memory["project_id"] == "p1"
    assert memory["category"] == "preference"
    assert memory["confidence"] == 0.9
    assert memory["source"] == "chat"

    listed = await client.request("memory.list", {"project_id": "p1"})
    assert listed["total"] == 1
    assert len(listed["memories"]) == 1
    assert listed["memories"][0]["id"] == memory["id"]


@pytest.mark.asyncio
async def test_memory_search(client: IPCClient) -> None:
    await client.request("memory.add", {"content": "React is great."})
    await client.request("memory.add", {"content": "Vue is fine too."})

    result = await client.request("memory.search", {"query": "React"})
    assert result["total"] == 1
    assert len(result["memories"]) == 1
    assert result["memories"][0]["content"] == "React is great."


@pytest.mark.asyncio
async def test_memory_delete(client: IPCClient) -> None:
    added = await client.request("memory.add", {"content": "Delete this."})
    memory_id = added["memory"]["id"]

    deleted = await client.request("memory.delete", {"id": memory_id})
    assert deleted == {"ok": True, "id": memory_id}

    listed = await client.request("memory.list", {})
    assert listed["total"] == 0

    missing = await client.request("memory.delete", {"id": memory_id})
    assert missing == {"ok": False, "id": memory_id}


@pytest.mark.asyncio
async def test_memory_extract(client: IPCClient) -> None:
    result = await client.request(
        "memory.extract",
        {"text": "First sentence. Second sentence! Third?"},
    )
    facts = result["facts"]
    assert len(facts) == 3
    assert facts[0] == {"content": "First sentence.", "category": "fact", "confidence": 0.8}
    assert facts[1] == {"content": "Second sentence!", "category": "fact", "confidence": 0.8}
    assert facts[2] == {"content": "Third?", "category": "fact", "confidence": 0.8}


@pytest.mark.asyncio
async def test_memory_add_rejects_invalid_category(client: IPCClient) -> None:
    with pytest.raises(RuntimeError, match="category must be one of"):
        await client.request(
            "memory.add",
            {"content": "x", "category": "not-a-category"},
        )


@pytest.mark.asyncio
async def test_memory_search_rejects_empty_query(client: IPCClient) -> None:
    with pytest.raises(RuntimeError, match="non-empty string"):
        await client.request("memory.search", {"query": ""})
