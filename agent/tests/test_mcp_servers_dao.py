"""Tests for :class:`McpServersDAO`."""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from minimax_code.storage.dao.mcp_servers import McpServersDAO
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
async def dao(async_db: AsyncDatabase) -> McpServersDAO:
    return McpServersDAO(async_db)


@pytest.fixture
def id_factory() -> Any:
    def _make(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:10]}"

    return _make


@pytest.mark.asyncio
async def test_create_and_get(dao: McpServersDAO, id_factory: Any) -> None:
    sid = id_factory("mcp")
    row = await dao.create(
        id=sid,
        name="Filesystem",
        transport="stdio",
        command=["npx", "@modelcontextprotocol/server-filesystem", "."],
        env={"NODE_PATH": "/usr/local/lib"},
        enabled=True,
    )
    assert row["id"] == sid
    assert row["name"] == "Filesystem"
    assert row["transport"] == "stdio"
    assert row["command"] == ["npx", "@modelcontextprotocol/server-filesystem", "."]
    assert row["env"] == {"NODE_PATH": "/usr/local/lib"}
    assert row["enabled"] is True

    fetched = await dao.get(sid)
    assert fetched is not None
    assert fetched["name"] == "Filesystem"


@pytest.mark.asyncio
async def test_update_command_and_enabled(dao: McpServersDAO, id_factory: Any) -> None:
    sid = id_factory("mcp")
    await dao.create(id=sid, name="Git", transport="stdio", command=["npx", "@modelcontextprotocol/server-git"])
    updated = await dao.update(sid, command=["python", "-m", "mcp_server_git"], enabled=False)
    assert updated is not None
    assert updated["command"] == ["python", "-m", "mcp_server_git"]
    assert updated["enabled"] is False


@pytest.mark.asyncio
async def test_delete(dao: McpServersDAO, id_factory: Any) -> None:
    sid = id_factory("mcp")
    await dao.create(id=sid, name="DeleteMe", transport="stdio", command=["echo"])
    assert await dao.delete(sid) is True
    assert await dao.get(sid) is None
    assert await dao.delete(sid) is False


@pytest.mark.asyncio
async def test_list_filters_enabled(dao: McpServersDAO, id_factory: Any) -> None:
    enabled_id = id_factory("mcp")
    disabled_id = id_factory("mcp")
    await dao.create(id=enabled_id, name="Enabled", transport="stdio", command=["echo"], enabled=True)
    await dao.create(id=disabled_id, name="Disabled", transport="stdio", command=["echo"], enabled=False)
    enabled_rows = await dao.list(enabled=True)
    assert len(enabled_rows) == 1
    assert enabled_rows[0]["name"] == "Enabled"
