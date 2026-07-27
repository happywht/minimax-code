"""Tests for :class:`CodebaseIndexer`."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from minimax_code.codebase import CodebaseIndexer, CodebaseStore
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
async def store(async_db: AsyncDatabase) -> CodebaseStore:
    return CodebaseStore(async_db)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path / "workspace"


@pytest.mark.asyncio
async def test_index_builds_chunks(store: CodebaseStore, workspace: Path) -> None:
    workspace.mkdir()
    (workspace / "main.py").write_text("def main():\n    print('hello')\n")
    (workspace / "auth.py").write_text("def authenticate():\n    return True\n")

    indexer = CodebaseIndexer(workspace, store)
    status = await indexer.build_index()
    assert status["status"] in ("indexing", "done")

    # Wait for background task to finish.
    while indexer.progress.status.value == "indexing":
        await asyncio.sleep(0.05)

    stats = await store.get_stats()
    assert stats["total_files"] == 2
    assert stats["total_chunks"] == 2


@pytest.mark.asyncio
async def test_index_skips_binary_and_unknown_files(store: CodebaseStore, workspace: Path) -> None:
    workspace.mkdir()
    (workspace / "main.py").write_text("x = 1\n")
    (workspace / "binary.dat").write_bytes(b"\x00\x01\x02\x03")
    (workspace / "readme.md").write_text("# Title\n")

    indexer = CodebaseIndexer(workspace, store)
    await indexer.build_index()
    while indexer.progress.status.value == "indexing":
        await asyncio.sleep(0.05)

    files = await store.list_files()
    assert files == ["main.py"]


@pytest.mark.asyncio
async def test_index_invalidate_path(store: CodebaseStore, workspace: Path) -> None:
    workspace.mkdir()
    (workspace / "main.py").write_text("x = 1\n")
    indexer = CodebaseIndexer(workspace, store)
    await indexer.build_index()
    while indexer.progress.status.value == "indexing":
        await asyncio.sleep(0.05)

    await indexer.invalidate_path("main.py")
    files = await store.list_files()
    assert files == []


@pytest.mark.asyncio
async def test_index_status_before_build(store: CodebaseStore, workspace: Path) -> None:
    workspace.mkdir()
    indexer = CodebaseIndexer(workspace, store)
    assert indexer.to_dict()["status"] == "idle"
