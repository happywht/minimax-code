"""Tests for :class:`SearchCodebaseTool`."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from minimax_code.agent.tools import SearchCodebaseTool
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
    (workspace / "main.py").write_text("def main() -> None:\n    print('hello')\n")

    from minimax_code.codebase.store import CodebaseStore

    store = CodebaseStore(async_db)
    indexer = CodebaseIndexer(workspace, store)
    await indexer.build_index()
    while indexer.progress.status.value == "indexing":
        await asyncio.sleep(0.05)
    return store, indexer


@pytest.mark.asyncio
async def test_search_codebase_tool_returns_matches(indexed_store: tuple) -> None:
    store, indexer = indexed_store
    retriever = CodebaseRetriever(store, indexer=indexer)
    tool = SearchCodebaseTool(retriever)

    result = await tool.run(query="authenticate")
    assert result.success
    output = result.output
    assert output["query"] == "authenticate"
    assert output["match_count"] == 1
    assert output["matches"][0]["file_path"] == "auth.py"
    assert output["matches"][0]["source"] == "auth.py#L1-2"


@pytest.mark.asyncio
async def test_search_codebase_tool_respects_file_pattern(indexed_store: tuple) -> None:
    store, indexer = indexed_store
    retriever = CodebaseRetriever(store, indexer=indexer)
    tool = SearchCodebaseTool(retriever)

    result = await tool.run(query="def", file_pattern="auth%")
    assert result.success
    assert len(result.output["matches"]) == 1
    assert result.output["matches"][0]["file_path"] == "auth.py"


@pytest.mark.asyncio
async def test_search_codebase_tool_validates_empty_query(indexed_store: tuple) -> None:
    store, indexer = indexed_store
    retriever = CodebaseRetriever(store, indexer=indexer)
    tool = SearchCodebaseTool(retriever)

    result = await tool.run(query="")
    assert not result.success
    assert "non-empty" in result.error.lower()


@pytest.mark.asyncio
async def test_search_codebase_tool_no_matches(indexed_store: tuple) -> None:
    store, indexer = indexed_store
    retriever = CodebaseRetriever(store, indexer=indexer)
    tool = SearchCodebaseTool(retriever)

    result = await tool.run(query="xyzzy_not_found")
    assert result.success
    assert result.output["match_count"] == 0
    assert "matches" in result.output


def test_search_codebase_tool_registry_shape(indexed_store: tuple) -> None:
    """The tool must expose the LLM function-calling schema."""
    store, indexer = indexed_store
    retriever = CodebaseRetriever(store, indexer=indexer)
    tool = SearchCodebaseTool(retriever)

    assert tool.name == "search_codebase"
    assert "query" in tool.parameters["required"]
    assert "file_pattern" in tool.parameters["properties"]
    assert "limit" in tool.parameters["properties"]


def test_registry_accepts_search_codebase(indexed_store: tuple) -> None:
    """Cloning the default registry and adding the codebase tool works."""
    from minimax_code.agent.tools import ToolRegistry, get_default_registry

    store, indexer = indexed_store
    retriever = CodebaseRetriever(store, indexer=indexer)

    registry = ToolRegistry()
    for tool in get_default_registry().list():
        registry.register(tool)
    registry.register(SearchCodebaseTool(retriever))

    names = registry.names()
    assert "search_codebase" in names
    functions = registry.to_llm_functions()
    assert any(f["function"]["name"] == "search_codebase" for f in functions)
