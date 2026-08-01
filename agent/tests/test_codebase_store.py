"""Tests for :class:`CodebaseStore`."""

from __future__ import annotations

from pathlib import Path

import pytest

from minimax_code.codebase.store import CodebaseStore
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


@pytest.mark.asyncio
async def test_save_and_get_chunk(store: CodebaseStore) -> None:
    row = await store.save_chunk(
        file_path="src/main.py",
        start_line=1,
        end_line=10,
        content="def main():\n    pass\n",
        metadata={"language": "py", "total_lines": 2},
    )
    assert row["file_path"] == "src/main.py"
    assert row["metadata"]["language"] == "py"

    fetched = await store.get_chunk(row["id"])
    assert fetched is not None
    assert fetched["content"] == "def main():\n    pass\n"


@pytest.mark.asyncio
async def test_delete_chunks_for_file(store: CodebaseStore) -> None:
    await store.save_chunk(
        file_path="src/a.py",
        start_line=1,
        end_line=1,
        content="x = 1",
    )
    await store.save_chunk(
        file_path="src/b.py",
        start_line=1,
        end_line=1,
        content="y = 2",
    )
    deleted = await store.delete_chunks_for_file("src/a.py")
    assert deleted == 1
    files = await store.list_files()
    assert files == ["src/b.py"]


@pytest.mark.asyncio
async def test_fts_search(store: CodebaseStore) -> None:
    await store.save_chunk(
        file_path="src/auth.py",
        start_line=1,
        end_line=5,
        content="def authenticate_user(token: str) -> bool:\n    return True\n",
    )
    await store.save_chunk(
        file_path="src/main.py",
        start_line=1,
        end_line=5,
        content="def main() -> None:\n    print('hello')\n",
    )
    results = await store.search("authenticate")
    assert len(results) == 1
    assert results[0]["file_path"] == "src/auth.py"


@pytest.mark.asyncio
async def test_search_with_file_pattern(store: CodebaseStore) -> None:
    await store.save_chunk(
        file_path="src/auth.py",
        start_line=1,
        end_line=5,
        content="def authenticate_user(token: str) -> bool:\n    return True\n",
    )
    await store.save_chunk(
        file_path="tests/test_auth.py",
        start_line=1,
        end_line=5,
        content="def test_authenticate():\n    pass\n",
    )
    results = await store.search("authenticate", file_pattern="src/%")
    assert len(results) == 1
    assert results[0]["file_path"].startswith("src/")


@pytest.mark.asyncio
async def test_clear(store: CodebaseStore) -> None:
    await store.save_chunk(file_path="src/x.py", start_line=1, end_line=1, content="x = 1")
    removed = await store.clear()
    assert removed == 1
    stats = await store.get_stats()
    assert stats["total_chunks"] == 0


@pytest.mark.asyncio
async def test_save_and_search_vectors(store: CodebaseStore) -> None:
    row = await store.save_chunk(
        file_path="src/auth.py",
        start_line=1,
        end_line=2,
        content="def authenticate_user(token: str) -> bool:\n    return True\n",
    )
    await store.save_embedding(row["rowid"], [0.1] * 128)

    results = await store.search_vectors([0.1] * 128, limit=5)
    assert len(results) == 1
    assert results[0]["rowid"] == row["rowid"]
    assert results[0]["distance"] < 1.0


@pytest.mark.asyncio
async def test_delete_chunks_for_file_removes_embeddings(store: CodebaseStore) -> None:
    row = await store.save_chunk(
        file_path="src/auth.py",
        start_line=1,
        end_line=1,
        content="x = 1",
    )
    await store.save_embedding(row["rowid"], [0.1] * 128)

    await store.delete_chunks_for_file("src/auth.py")
    results = await store.search_vectors([0.1] * 128, limit=5)
    assert results == []
