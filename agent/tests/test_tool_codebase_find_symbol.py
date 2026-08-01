"""Tests for :class:`FindSymbolCodebaseTool`."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from minimax_code.agent.tools import FindSymbolCodebaseTool
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
async def test_find_symbol_definitions(indexed_store: tuple) -> None:
    store, indexer = indexed_store
    retriever = CodebaseRetriever(store, indexer=indexer)
    tool = FindSymbolCodebaseTool(retriever)

    result = await tool.run(symbol="authenticate_user")
    assert result.success
    output = result.output
    assert output["symbol"] == "authenticate_user"
    assert len(output["definitions"]) == 1
    assert output["definitions"][0]["file_path"] == "auth.py"
    assert output["definitions"][0]["source"] == "auth.py#L1"
    assert output["definitions"][0]["kind"] == "function"


@pytest.mark.asyncio
async def test_find_symbol_kind_filter(indexed_store: tuple) -> None:
    store, indexer = indexed_store
    retriever = CodebaseRetriever(store, indexer=indexer)
    tool = FindSymbolCodebaseTool(retriever)

    result = await tool.run(symbol="authenticate_user", kind="class")
    assert result.success
    assert result.output["definitions"] == []


@pytest.mark.asyncio
async def test_find_symbol_validates_empty_symbol(indexed_store: tuple) -> None:
    store, indexer = indexed_store
    retriever = CodebaseRetriever(store, indexer=indexer)
    tool = FindSymbolCodebaseTool(retriever)

    result = await tool.run(symbol="")
    assert not result.success
    assert "non-empty" in result.error.lower()
