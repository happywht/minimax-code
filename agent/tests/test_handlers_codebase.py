"""Tests for ``codebase.*`` IPC handlers."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from minimax_code import app
from minimax_code.codebase import CodebaseIndexer, CodebaseStore
from minimax_code.ipc.client import IPCClient
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
def client(tmp_path: Path, async_db: AsyncDatabase) -> IPCClient:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "main.py").write_text("def hello():\n    return 1\n")
    (workspace / "utils.py").write_text("def helper():\n    pass\n")

    store = CodebaseStore(async_db)
    indexer = CodebaseIndexer(workspace, store)
    app.set_codebase_indexer(indexer)
    return IPCClient()


@pytest.mark.asyncio
async def test_codebase_status_idle(client: IPCClient) -> None:
    result = await client.request("codebase.status", {})
    assert result["status"] == "idle"


@pytest.mark.asyncio
async def test_codebase_build_and_search(client: IPCClient) -> None:
    status = await client.request("codebase.build_index", {})
    assert status["status"] in ("indexing", "done")

    indexer = app.get_codebase_indexer()
    while indexer.progress.status.value == "indexing":
        await asyncio.sleep(0.05)

    search = await client.request("codebase.search", {"query": "helper"})
    assert search["total"] == 1
    assert search["results"][0]["file_path"] == "utils.py"


@pytest.mark.asyncio
async def test_codebase_summarize(client: IPCClient) -> None:
    await client.request("codebase.build_index", {})
    indexer = app.get_codebase_indexer()
    while indexer.progress.status.value == "indexing":
        await asyncio.sleep(0.05)

    result = await client.request("codebase.summarize", {"path": "main.py"})
    assert result["path"] == "main.py"
    assert result["kind"] == "file"
    assert result["total_lines"] == 2


@pytest.mark.asyncio
async def test_codebase_search_validates_query(client: IPCClient) -> None:
    with pytest.raises(RuntimeError, match="non-empty string"):
        await client.request("codebase.search", {"query": ""})
