"""Tests for :class:`CodebaseIndexer`."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from minimax_code import app
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


@pytest.mark.asyncio
async def test_index_chunks_large_files(store: CodebaseStore, workspace: Path) -> None:
    workspace.mkdir()
    # Create a file with 120 lines, which should span multiple 50-line chunks.
    lines = [f"def func_{i}():\n    return {i}\n" for i in range(40)]
    (workspace / "big.py").write_text("".join(lines))

    indexer = CodebaseIndexer(workspace, store)
    await indexer.build_index()
    while indexer.progress.status.value == "indexing":
        await asyncio.sleep(0.05)

    chunks = await store.get_file_chunks("big.py")
    # 40 function blocks * 2 lines = 80 lines; with 50-line windows and 5-line
    # overlap we expect at least 2 chunks.
    assert len(chunks) >= 2
    # Each chunk's line range should be consistent with its content.
    for chunk in chunks:
        content_lines = chunk["content"].splitlines()
        expected_len = chunk["end_line"] - chunk["start_line"] + 1
        assert len(content_lines) == expected_len
        meta = chunk.get("metadata") or {}
        assert meta.get("language") == "py"
        # Symbols in metadata should fall within the chunk line range.
        for sym in meta.get("symbols", []):
            assert chunk["start_line"] <= sym["line"] <= chunk["end_line"]


@pytest.mark.asyncio
async def test_index_symbols_filtered_to_chunks(store: CodebaseStore, workspace: Path) -> None:
    workspace.mkdir()
    source = "\n".join([f"def f{i}():\n    pass" for i in range(60)])
    (workspace / "funcs.py").write_text(source)

    indexer = CodebaseIndexer(workspace, store)
    await indexer.build_index()
    while indexer.progress.status.value == "indexing":
        await asyncio.sleep(0.05)

    chunks = await store.get_file_chunks("funcs.py")
    first = min(chunks, key=lambda c: c["start_line"])
    last = max(chunks, key=lambda c: c["start_line"])
    first_meta = (first.get("metadata") or {}).get("symbols", [])
    last_meta = (last.get("metadata") or {}).get("symbols", [])
    # Symbols are line-scoped to their chunk.
    assert all(first["start_line"] <= s["line"] <= first["end_line"] for s in first_meta)
    assert all(last["start_line"] <= s["line"] <= last["end_line"] for s in last_meta)


@pytest.mark.asyncio
async def test_incremental_index_skips_unchanged_files(store: CodebaseStore, workspace: Path) -> None:
    workspace.mkdir()
    (workspace / "a.py").write_text("def a():\n    pass\n")
    (workspace / "b.py").write_text("def b():\n    pass\n")

    indexer = CodebaseIndexer(workspace, store)
    await indexer.build_index()
    while indexer.progress.status.value == "indexing":
        await asyncio.sleep(0.05)

    first_state = await store.get_file_index_state()
    assert set(first_state.keys()) == {"a.py", "b.py"}

    # Rebuild without changes — no files should be re-indexed.
    await indexer.build_index()
    while indexer.progress.status.value == "indexing":
        await asyncio.sleep(0.05)
    assert indexer.progress.processed == 0

    # Modify one file and add another; only changed/new files are processed.
    (workspace / "a.py").write_text("def a():\n    return 1\n")
    (workspace / "c.py").write_text("def c():\n    pass\n")
    # Ensure mtime actually changes on filesystems with coarse resolution.
    await asyncio.sleep(0.05)

    await indexer.build_index()
    while indexer.progress.status.value == "indexing":
        await asyncio.sleep(0.05)
    # a.py changed, c.py new, b.py unchanged.
    assert indexer.progress.processed == 2

    # Delete a file and verify its index records are removed.
    (workspace / "b.py").unlink()
    await indexer.build_index()
    while indexer.progress.status.value == "indexing":
        await asyncio.sleep(0.05)

    files = await store.list_files()
    assert "b.py" not in files
    state = await store.get_file_index_state()
    assert "b.py" not in state


@pytest.mark.asyncio
async def test_auto_index_trigger(store: CodebaseStore, workspace: Path) -> None:
    workspace.mkdir()
    (workspace / "x.py").write_text("def x():\n    pass\n")

    indexer = CodebaseIndexer(workspace, store)
    app.set_codebase_indexer(indexer)
    try:
        # Use a very short delay so the test remains fast.
        app._schedule_codebase_index_build(delay_s=0.01)
        # Wait for the background task to start and finish.
        for _ in range(100):
            if indexer.progress.status.value == "done":
                break
            await asyncio.sleep(0.01)

        assert indexer.progress.status.value == "done"
        assert indexer.progress.processed == 1
        files = await store.list_files()
        assert files == ["x.py"]
    finally:
        app.set_codebase_indexer(None)
