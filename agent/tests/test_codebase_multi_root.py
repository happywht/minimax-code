"""v1.3.0 regression tests — per-root codebase index sharding.

Pins the whole vertical of Commit 4:

1. **Migration 027** — legacy single-shard schema upgrades to the
   ``root``-dimensioned one with data preserved, and replays cleanly.
2. **Store layer** — every read/write honours the ``root`` shard key;
   ``clear``/``clear_file_index_state`` with a string never touches
   other shards while ``None`` wipes everything.
3. **Indexer isolation** — A/B double-root builds stay invisible to
   each other's searches, and an incremental rebuild of A no longer
   deletes B's chunks/file-meta (the pre-1.3.0 diff saw the whole
   table and wiped the "missing" files).
4. ``ensure_codebase_indexer`` — bucket cache keyed by resolved root,
   default root keeps the legacy ``root_key=''`` shard and syncs the
   legacy single slot, plus the canonical-env fix for
   ``_default_codebase_root``.
5. **Handler routing** — ``codebase.*`` params with a ``project_id``
   build/search that project's tree; without one they fall back to the
   default root exactly as before.
"""

from __future__ import annotations

import asyncio
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

from minimax_code import app as app_mod
from minimax_code.app import ensure_codebase_indexer, set_projects_dao
from minimax_code.codebase import CodebaseIndexer, CodebaseRetriever, CodebaseStore
from minimax_code.codebase.indexer import IndexStatus
from minimax_code.codebase.store import _HAS_SQLITE_VEC
from minimax_code.config import Config
from minimax_code.ipc.handlers_codebase import register_codebase_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.storage.migrations import discover_migrations

# ---------------------------------------------------------------------------
# Fixtures & helpers
# ---------------------------------------------------------------------------


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
async def projects_dao(async_db: AsyncDatabase) -> ProjectsDAO:
    dao = ProjectsDAO(async_db)
    await dao.ensure_inbox()
    return dao


@pytest.fixture(autouse=True)
def _clean_daos():
    """Keep the workspace_ctx projects DAO singleton test-local."""
    yield
    set_projects_dao(None)


@pytest.fixture(autouse=True)
def _no_workspace_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIMAX_CODE_WORKSPACE", raising=False)
    monkeypatch.delenv("MINIMAX_CODE_WORKSPACE_DIR", raising=False)


@pytest.fixture(autouse=True)
def _clean_codebase_cache():
    """Snapshot/restore the per-root codebase cache around each test."""
    prev_buckets = dict(app_mod._CODEBASE_INDEXERS)
    prev_legacy = app_mod._CODEBASE_INDEXER
    yield
    app_mod._CODEBASE_INDEXERS.clear()
    app_mod._CODEBASE_INDEXERS.update(prev_buckets)
    app_mod._CODEBASE_INDEXER = prev_legacy


class _FakeContext:
    """Stand-in for :class:`Context` that captures reply / error."""

    def __init__(self, server: Any = None) -> None:
        self.reply_value: dict[str, Any] | None = None
        self.error_value: dict[str, Any] | None = None
        self.server = server

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.error_value = {"code": code, "message": message, "data": data}


def make_workspace(tmp_path: Path, name: str, files: dict[str, str]) -> Path:
    """Materialise a tiny source tree under ``tmp_path / name``."""
    ws = tmp_path / name
    ws.mkdir()
    for rel, content in files.items():
        target = ws / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    return ws


async def _wait_index_done(indexer: CodebaseIndexer, timeout_s: float = 15.0) -> None:
    """Poll an indexer's background build until DONE/ERROR."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        p = indexer.progress
        if p.status == IndexStatus.DONE:
            assert p.error is None
            return
        if p.status == IndexStatus.ERROR:
            pytest.fail(f"index build failed: {p.error}")
        await asyncio.sleep(0.05)
    pytest.fail("index build did not finish in time")


def _pid() -> str:
    return f"proj_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# 1. Migration 027 — upgrade + idempotency
# ---------------------------------------------------------------------------


def test_migration_027_in_registry() -> None:
    versions = {v for v, _run in discover_migrations(applied=set())}
    assert 27 in versions


def test_migration_027_upgrades_legacy_schema(tmp_path: Path) -> None:
    """A pre-1.3.0 single-shard database upgrades with data preserved."""
    conn = sqlite3.connect(tmp_path / "legacy.db")
    conn.execute(
        "CREATE TABLE codebase_chunks ("
        "id TEXT PRIMARY KEY, file_path TEXT NOT NULL, "
        "start_line INTEGER NOT NULL, end_line INTEGER NOT NULL, "
        "content TEXT NOT NULL, metadata TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO codebase_chunks "
        "VALUES ('c1', 'src/a.py', 1, 10, 'alpha content', NULL, "
        "'2026-01-01', '2026-01-01')"
    )
    conn.execute(
        "CREATE TABLE codebase_file_meta ("
        "file_path TEXT PRIMARY KEY, mtime REAL NOT NULL, "
        "size INTEGER NOT NULL, indexed_at TEXT NOT NULL)"
    )
    conn.execute(
        "INSERT INTO codebase_file_meta VALUES ('src/a.py', 1.5, 10, '2026-01-01')"
    )
    conn.commit()

    run_027 = dict(discover_migrations(applied=set()))[27]
    run_027(conn)
    conn.commit()

    chunk_cols = {row[1] for row in conn.execute("PRAGMA table_info(codebase_chunks)")}
    assert "root" in chunk_cols
    row = conn.execute("SELECT root FROM codebase_chunks WHERE id = 'c1'").fetchone()
    assert row is not None and row[0] == ""

    meta_cols = {row[1] for row in conn.execute("PRAGMA table_info(codebase_file_meta)")}
    assert "root" in meta_cols
    pk = {row[1] for row in conn.execute("PRAGMA table_info(codebase_file_meta)") if row[5]}
    assert pk == {"root", "file_path"}, "file_meta must key on (root, file_path)"
    meta = conn.execute(
        "SELECT root, file_path, mtime FROM codebase_file_meta"
    ).fetchone()
    assert meta == ("", "src/a.py", 1.5)

    # Replay is a no-op with data intact.
    run_027(conn)
    conn.commit()
    count = conn.execute("SELECT COUNT(*) FROM codebase_file_meta").fetchone()[0]
    assert count == 1
    conn.close()


def test_migration_027_applied_by_full_migrate(db_path: Path) -> None:
    """The migrations registry wires 027 into the normal upgrade path."""
    from minimax_code.storage.db import Database

    db = Database(db_path)
    try:
        db.migrate()
        cols = {row[1] for row in db.fetchall("PRAGMA table_info(codebase_file_meta)")}
        assert "root" in cols
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 2. Store layer — per-root reads/writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_store_save_and_search_are_root_scoped(async_db: AsyncDatabase) -> None:
    store = CodebaseStore(async_db)
    await store.save_chunk(
        file_path="src/shared.py", start_line=1, end_line=10,
        content="unicorn marker alpha", root="A",
    )
    await store.save_chunk(
        file_path="src/shared.py", start_line=1, end_line=10,
        content="different text beta", root="B",
    )

    a_rows = await store.search("unicorn", root="A")
    assert len(a_rows) == 1
    assert a_rows[0]["root"] == "A"
    assert await store.search("unicorn", root="B") == []

    b_rows = await store.search("beta", root="B")
    assert len(b_rows) == 1
    assert await store.search("beta", root="A") == []


@pytest.mark.asyncio
async def test_store_delete_scoped_to_root(async_db: AsyncDatabase) -> None:
    store = CodebaseStore(async_db)
    for root in ("A", "B"):
        await store.save_chunk(
            file_path="src/x.py", start_line=1, end_line=5,
            content="payload", root=root,
        )
    deleted = await store.delete_chunks_for_file("src/x.py", root="A")
    assert deleted == 1
    assert await store.search("payload", root="A") == []
    assert len(await store.search("payload", root="B")) == 1


@pytest.mark.asyncio
async def test_store_stats_and_list_files_by_root(async_db: AsyncDatabase) -> None:
    store = CodebaseStore(async_db)
    await store.save_chunk(
        file_path="a.py", start_line=1, end_line=5, content="one", root="A"
    )
    await store.save_chunk(
        file_path="b.py", start_line=1, end_line=5, content="two", root="B"
    )
    await store.save_chunk(
        file_path="c.py", start_line=1, end_line=5, content="three", root="B"
    )

    assert (await store.get_stats(root="A"))["total_chunks"] == 1
    assert (await store.get_stats(root="B"))["total_chunks"] == 2
    agg = await store.get_stats()  # None → whole-index view
    assert agg["total_chunks"] == 3
    assert agg["total_files"] == 3

    assert await store.list_files(root="A") == ["a.py"]
    assert sorted(await store.list_files(root="B")) == ["b.py", "c.py"]
    assert len(await store.list_files()) == 3


@pytest.mark.asyncio
async def test_store_get_chunk_root_guard(async_db: AsyncDatabase) -> None:
    store = CodebaseStore(async_db)
    row = await store.save_chunk(
        file_path="a.py", start_line=1, end_line=5, content="x", root="A"
    )
    assert await store.get_chunk(row["id"], root="A") is not None
    assert await store.get_chunk(row["id"], root="B") is None
    assert await store.get_chunk(row["id"]) is not None  # None → unfiltered


@pytest.mark.asyncio
async def test_store_file_meta_per_root(async_db: AsyncDatabase) -> None:
    store = CodebaseStore(async_db)
    await store.save_file_index_state("f.py", mtime=1.0, size=10, root="A")
    await store.save_file_index_state("f.py", mtime=2.0, size=20, root="B")

    state_a = await store.get_file_index_state(root="A")
    assert list(state_a) == ["f.py"] and state_a["f.py"]["mtime"] == 1.0
    state_b = await store.get_file_index_state(root="B")
    assert state_b["f.py"]["mtime"] == 2.0

    await store.delete_file_index_state("f.py", root="A")
    assert await store.get_file_index_state(root="A") == {}
    assert "f.py" in await store.get_file_index_state(root="B")

    await store.clear_file_index_state("A")
    assert await store.get_file_index_state(root="B") != {}
    await store.clear_file_index_state()  # None → all shards
    assert await store.get_file_index_state(root="B") == {}


@pytest.mark.asyncio
async def test_store_clear_root_vs_all(async_db: AsyncDatabase) -> None:
    store = CodebaseStore(async_db)
    for root in ("A", "B"):
        await store.save_chunk(
            file_path="x.py", start_line=1, end_line=5, content="keep", root=root
        )
        await store.save_file_index_state("x.py", mtime=1.0, size=5, root=root)

    deleted = await store.clear("A")
    assert deleted == 1
    assert await store.search("keep", root="A") == []
    assert len(await store.search("keep", root="B")) == 1
    assert await store.get_file_index_state(root="B") != {}

    wiped = await store.clear(None)
    assert wiped == 1
    assert await store.search("keep", root="B") == []
    assert await store.get_file_index_state(root="B") == {}


@pytest.mark.skipif(not _HAS_SQLITE_VEC, reason="sqlite-vec not available")
@pytest.mark.asyncio
async def test_store_vector_search_root_filter(async_db: AsyncDatabase) -> None:
    store = CodebaseStore(async_db)
    row_a = await store.save_chunk(
        file_path="a.py", start_line=1, end_line=5, content="vec", root="A"
    )
    row_b = await store.save_chunk(
        file_path="b.py", start_line=1, end_line=5, content="vec", root="B"
    )
    vec = [0.1] * 128
    await store.save_embedding(row_a["rowid"], vec)
    await store.save_embedding(row_b["rowid"], vec)

    hits_a = await store.search_vectors(vec, limit=10, root="A")
    assert [h["rowid"] for h in hits_a] == [row_a["rowid"]]
    hits_b = await store.search_vectors(vec, limit=10, root="B")
    assert [h["rowid"] for h in hits_b] == [row_b["rowid"]]
    # Unfiltered keeps both (re-ranking callers may ask across shards).
    assert len(await store.search_vectors(vec, limit=10)) == 2


# ---------------------------------------------------------------------------
# 3. Indexer isolation — the v1.3.0 headline fix
# ---------------------------------------------------------------------------


def _indexer_for(ws: Path, db: AsyncDatabase) -> CodebaseIndexer:
    return CodebaseIndexer(
        ws, CodebaseStore(db), root_key=str(ws.resolve())
    )


@pytest.mark.asyncio
async def test_double_root_builds_search_isolated(
    async_db: AsyncDatabase, tmp_path: Path
) -> None:
    ws_a = make_workspace(tmp_path, "projA", {"app_a.py": "def find_me_a():\n    return 'unicorn alpha'\n"})
    ws_b = make_workspace(tmp_path, "projB", {"app_b.py": "def find_me_b():\n    return 'unicorn bravo'\n"})

    ix_a = _indexer_for(ws_a, async_db)
    ix_b = _indexer_for(ws_b, async_db)
    await ix_a.build_index(force=True)
    await ix_b.build_index(force=True)
    await _wait_index_done(ix_a)
    await _wait_index_done(ix_b)

    r_a = CodebaseRetriever(ix_a._store, indexer=ix_a, embedder=ix_a.embedder, root=ix_a.root_key)
    r_b = CodebaseRetriever(ix_b._store, indexer=ix_b, embedder=ix_b.embedder, root=ix_b.root_key)

    res_a = await r_a.search(query="unicorn")
    assert res_a["total"] == 1
    assert res_a["results"][0]["file_path"] == "app_a.py"

    res_b = await r_b.search(query="unicorn")
    assert res_b["total"] == 1
    assert res_b["results"][0]["file_path"] == "app_b.py"


@pytest.mark.asyncio
async def test_incremental_build_does_not_delete_other_root(
    async_db: AsyncDatabase, tmp_path: Path
) -> None:
    """The pre-1.3.0 incremental diff saw the whole file-meta table and
    wiped every other root's chunks as 'deleted'. Must not regress."""
    ws_a = make_workspace(tmp_path, "projA", {"a1.py": "value = 'alpha-one'\n"})
    ws_b = make_workspace(tmp_path, "projB", {"b1.py": "value = 'bravo-one'\n"})

    ix_a = _indexer_for(ws_a, async_db)
    ix_b = _indexer_for(ws_b, async_db)
    await ix_a.build_index(force=True)
    await _wait_index_done(ix_a)
    await ix_b.build_index(force=True)
    await _wait_index_done(ix_b)

    stats_b_before = await ix_b._store.get_stats(root=ix_b.root_key)
    meta_b_before = await ix_b._store.get_file_index_state(root=ix_b.root_key)
    assert stats_b_before["total_chunks"] > 0

    # New file lands in A; incremental (force=False) rebuild.
    (ws_a / "a2.py").write_text("value = 'alpha-two'\n", encoding="utf-8")
    await ix_a.build_index(force=False)
    await _wait_index_done(ix_a)

    # B's shard is untouched — same chunks, same file-meta rows.
    assert await ix_b._store.get_stats(root=ix_b.root_key) == stats_b_before
    assert await ix_b._store.get_file_index_state(root=ix_b.root_key) == meta_b_before

    # A picked up the new file; the old one survived the incremental pass.
    files_a = sorted(await ix_a._store.list_files(root=ix_a.root_key))
    assert files_a == ["a1.py", "a2.py"]


# ---------------------------------------------------------------------------
# 4. ensure_codebase_indexer — bucket cache + default-root env fix
# ---------------------------------------------------------------------------


def test_default_root_env_priority(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    ws1 = (tmp_path / "ws1").resolve()
    ws2 = (tmp_path / "ws2").resolve()

    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(ws1))
    assert app_mod._default_codebase_root() == ws1

    # Canonical spelling wins over the deprecated one.
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE_DIR", str(ws2))
    assert app_mod._default_codebase_root() == ws1

    # Legacy spelling alone still resolves (with a deprecation warning).
    monkeypatch.delenv("MINIMAX_CODE_WORKSPACE")
    with pytest.warns(DeprecationWarning):
        assert app_mod._default_codebase_root() == ws2

    # Neither set → process CWD (the historical fallback).
    monkeypatch.delenv("MINIMAX_CODE_WORKSPACE_DIR")
    assert app_mod._default_codebase_root() == Path.cwd().resolve()


@pytest.mark.asyncio
async def test_ensure_codebase_indexer_buckets_by_root(
    async_db: AsyncDatabase, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(app_mod, "get_db", lambda: async_db)
    ra, rb = tmp_path / "ra", tmp_path / "rb"
    ra.mkdir()
    rb.mkdir()

    ix_a1 = ensure_codebase_indexer(ra)
    ix_a2 = ensure_codebase_indexer(ra)
    ix_b = ensure_codebase_indexer(rb)

    assert ix_a1 is not None
    assert ix_a2 is ix_a1, "same root must reuse the cached indexer"
    assert ix_b is not ix_a1, "different root must get its own indexer"
    assert ix_a1.root_key == str(ra.resolve())
    assert ix_b.root_key == str(rb.resolve())


@pytest.mark.asyncio
async def test_ensure_codebase_indexer_default_root_keeps_legacy_shard(
    async_db: AsyncDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(app_mod, "get_db", lambda: async_db)

    ix_default = ensure_codebase_indexer(None)
    assert ix_default is not None
    assert ix_default.root_key == "", "default root must keep the legacy shard"
    assert app_mod.get_codebase_indexer() is ix_default
    assert app_mod._CODEBASE_INDEXER is ix_default, "legacy single slot stays in sync"

    # A cached second call returns the identical object.
    assert ensure_codebase_indexer(None) is ix_default


@pytest.mark.asyncio
async def test_ensure_codebase_indexer_cache_hit_resyncs_legacy_slot(
    async_db: AsyncDatabase, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cache hit must refresh a cleared legacy slot (regression).

    Full-suite ordering: earlier tests can leave bucket entries behind
    while the legacy single slot is reset to ``None`` — the cache-hit
    path used to return early without re-syncing the slot, making
    ``get_codebase_indexer()`` wrongly report the root as unavailable.
    """
    monkeypatch.setattr(app_mod, "get_db", lambda: async_db)
    ix = ensure_codebase_indexer(None)
    assert ix is not None

    # Simulate a test seam clearing only the legacy slot.
    app_mod._CODEBASE_INDEXER = None
    assert ensure_codebase_indexer(None) is ix
    assert app_mod._CODEBASE_INDEXER is ix, "cache hit must resync the legacy slot"


# ---------------------------------------------------------------------------
# 5. Handler routing — project_id in codebase.* params
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_handlers_route_build_and_search_by_project_id(
    async_db: AsyncDatabase,
    projects_dao: ProjectsDAO,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(app_mod, "get_db", lambda: async_db)
    ws_a = make_workspace(
        tmp_path, "projA", {"app_a.py": "def find_me_a():\n    return 'unicorn alpha'\n"}
    )
    pid = _pid()
    await projects_dao.create(id=pid, name="A", root_path=str(ws_a))
    set_projects_dao(projects_dao)

    server = IPCServer(Config())
    register_codebase_handlers(server)
    handlers = server._handlers

    ctx = _FakeContext()
    await handlers["codebase.build_index"]({"project_id": pid, "force": True}, ctx)
    assert ctx.error_value is None, ctx.error_value
    ix = app_mod._CODEBASE_INDEXERS[str(ws_a.resolve())]
    await _wait_index_done(ix)

    ctx2 = _FakeContext()
    await handlers["codebase.search"]({"query": "unicorn", "project_id": pid}, ctx2)
    assert ctx2.error_value is None, ctx2.error_value
    assert ctx2.reply_value is not None
    assert ctx2.reply_value["total"] == 1
    assert ctx2.reply_value["results"][0]["file_path"] == "app_a.py"

    # Default root: same DB, different shard — no cross-root leak.
    ix_default = ensure_codebase_indexer(None)
    assert ix_default is not None
    ctx3 = _FakeContext()
    await handlers["codebase.search"]({"query": "unicorn"}, ctx3)
    assert ctx3.error_value is None, ctx3.error_value
    assert ctx3.reply_value is not None
    assert ctx3.reply_value["total"] == 0


@pytest.mark.asyncio
async def test_handlers_status_reports_project_shard(
    async_db: AsyncDatabase,
    projects_dao: ProjectsDAO,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(app_mod, "get_db", lambda: async_db)
    ws_a = make_workspace(tmp_path, "projA", {"s.py": "x = 1\n"})
    pid = _pid()
    await projects_dao.create(id=pid, name="A", root_path=str(ws_a))
    set_projects_dao(projects_dao)

    server = IPCServer(Config())
    register_codebase_handlers(server)

    ctx = _FakeContext()
    await server._handlers["codebase.status"]({"project_id": pid}, ctx)
    assert ctx.error_value is None, ctx.error_value
    assert ctx.reply_value is not None
    assert ctx.reply_value["stats"]["total_chunks"] == 0  # built lazily, not yet
