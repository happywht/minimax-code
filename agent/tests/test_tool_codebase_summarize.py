"""Tests for :class:`SummarizeCodebaseTool`."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from minimax_code.agent.tools import SummarizeCodebaseTool
from minimax_code.codebase import CodebaseIndexer, CodebaseRetriever
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
async def indexed_store(async_db: AsyncDatabase, tmp_path: Path) -> tuple:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "auth.py").write_text(
        "def authenticate_user(token: str) -> bool:\n    return True\n"
    )

    from minimax_code.codebase.store import CodebaseStore

    store = CodebaseStore(async_db)
    indexer = CodebaseIndexer(workspace, store)
    await indexer.build_index()
    while indexer.progress.status.value == "indexing":
        await asyncio.sleep(0.05)
    return store, indexer


@pytest.mark.asyncio
async def test_summarize_codebase_file(indexed_store: tuple) -> None:
    store, indexer = indexed_store
    retriever = CodebaseRetriever(store, indexer=indexer)
    tool = SummarizeCodebaseTool(retriever)

    result = await tool.run(path="auth.py")
    assert result.success
    output = result.output
    assert output["path"] == "auth.py"
    assert output["kind"] == "file"
    assert output["total_lines"] == 2
    assert output["source"] == "auth.py#L1-2"
    assert any(s["name"] == "authenticate_user" for s in output["symbols"])


@pytest.mark.asyncio
async def test_summarize_codebase_tool_validates_empty_path(indexed_store: tuple) -> None:
    store, indexer = indexed_store
    retriever = CodebaseRetriever(store, indexer=indexer)
    tool = SummarizeCodebaseTool(retriever)

    result = await tool.run(path="")
    assert not result.success
    assert "non-empty" in result.error.lower()


@pytest.mark.asyncio
async def test_summarize_codebase_missing_path(indexed_store: tuple) -> None:
    store, indexer = indexed_store
    retriever = CodebaseRetriever(store, indexer=indexer)
    tool = SummarizeCodebaseTool(retriever)

    result = await tool.run(path="nonexistent.py")
    assert result.success
    assert result.output["kind"] == "directory"
    assert result.output["total_lines"] == 0
