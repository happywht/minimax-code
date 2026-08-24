"""Regression tests for migration 026 — ``projects.root_path`` (v1.3.0).

Covers the upgrade path, idempotent replay, DAO round-trips (including
the tri-state ``update(root_path=...)`` semantics), and the ``project.*``
handler validation that ``root_path`` must reference an existing
directory.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from minimax_code.app import set_projects_dao
from minimax_code.ipc.client import IPCClient
from minimax_code.storage.dao.projects import ProjectsDAO, create_sync
from minimax_code.storage.db import (
    AsyncDatabase,
    Database,
    make_temp_database_path,
)
from minimax_code.storage.migrations import discover_migrations


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return make_temp_database_path(tmp_path)


@pytest.fixture
def sync_db(db_path: Path):
    db = Database(db_path)
    db.migrate()
    try:
        yield db
    finally:
        db.close()


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


def _pid() -> str:
    return f"proj_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# Migration upgrade + idempotency
# ---------------------------------------------------------------------------


def test_migration_026_in_registry() -> None:
    versions = {v for v, _run in discover_migrations(applied=[])}
    assert 26 in versions


def test_migration_026_adds_column_with_empty_default(sync_db: Database) -> None:
    cols = {row[1] for row in sync_db.fetchall("PRAGMA table_info(projects)")}
    assert "root_path" in cols
    row = sync_db.fetchone("SELECT root_path FROM projects WHERE id = 'inbox'")
    assert row is not None
    assert row[0] == ""


def test_migration_026_replays_idempotently(sync_db: Database) -> None:
    run_026 = dict(discover_migrations(applied=set()))[26]
    conn = sync_db._conn  # type: ignore[attr-defined]
    run_026(conn)
    cols = {row[1] for row in sync_db.fetchall("PRAGMA table_info(projects)")}
    assert "root_path" in cols


# ---------------------------------------------------------------------------
# DAO round-trips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_dao_create_persists_root_path(
    projects_dao: ProjectsDAO, tmp_path: Path
) -> None:
    pid = _pid()
    row = await projects_dao.create(id=pid, name="Rooted", root_path=str(tmp_path))
    assert row["root_path"] == str(tmp_path)

    fetched = await projects_dao.get(pid)
    assert fetched is not None
    assert fetched["root_path"] == str(tmp_path)


@pytest.mark.asyncio
async def test_dao_root_path_defaults_to_empty(projects_dao: ProjectsDAO) -> None:
    pid = _pid()
    row = await projects_dao.create(id=pid, name="Legacy")
    assert row["root_path"] == ""
    inbox = await projects_dao.get("inbox")
    assert inbox is not None
    assert inbox["root_path"] == ""


@pytest.mark.asyncio
async def test_dao_update_root_path_tri_state(projects_dao: ProjectsDAO, tmp_path: Path) -> None:
    pid = _pid()
    await projects_dao.create(id=pid, name="Tri", root_path=str(tmp_path))

    # None → untouched
    row = await projects_dao.update(pid, name="Renamed")
    assert row is not None
    assert row["root_path"] == str(tmp_path)

    # "" → cleared
    row = await projects_dao.update(pid, root_path="")
    assert row is not None
    assert row["root_path"] == ""

    # new value → rebound
    other = tmp_path / "other"
    other.mkdir()
    row = await projects_dao.update(pid, root_path=str(other))
    assert row is not None
    assert row["root_path"] == str(other)


@pytest.mark.asyncio
async def test_hydrate_defaults_missing_root_path_column(projects_dao: ProjectsDAO) -> None:
    """Rows shaped before 026 (no root_path key) hydrate defensively."""
    from minimax_code.storage.dao.projects import _hydrate

    d = _hydrate({"id": "x", "name": "y", "archived": 0})
    assert d is not None
    assert d["root_path"] == ""


def test_create_sync_persists_root_path(sync_db: Database, tmp_path: Path) -> None:
    row = create_sync(sync_db, id=_pid(), name="SyncRoot", root_path=str(tmp_path))
    assert row["root_path"] == str(tmp_path)


# ---------------------------------------------------------------------------
# Handler validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ipc_project_create_with_root_path(projects_dao: ProjectsDAO, tmp_path: Path) -> None:
    set_projects_dao(projects_dao)
    try:
        client = IPCClient()
        reply = await client.request(
            "project.create", {"name": "Rooted", "root_path": str(tmp_path)}
        )
        assert reply["project"]["root_path"] == str(tmp_path)
    finally:
        set_projects_dao(None)


@pytest.mark.asyncio
async def test_ipc_project_create_rejects_missing_directory(projects_dao: ProjectsDAO) -> None:
    set_projects_dao(projects_dao)
    try:
        client = IPCClient()
        with pytest.raises(RuntimeError, match="root_path"):
            await client.request(
                "project.create", {"name": "Bad", "root_path": "Z:/definitely/not/here"}
            )
    finally:
        set_projects_dao(None)


@pytest.mark.asyncio
async def test_ipc_project_update_sets_and_clears_root(
    projects_dao: ProjectsDAO, tmp_path: Path
) -> None:
    pid = _pid()
    await projects_dao.create(id=pid, name="Upd")
    set_projects_dao(projects_dao)
    try:
        client = IPCClient()
        reply = await client.request(
            "project.update", {"project_id": pid, "root_path": str(tmp_path)}
        )
        assert reply["project"]["root_path"] == str(tmp_path)

        cleared = await client.request(
            "project.update", {"project_id": pid, "root_path": ""}
        )
        assert cleared["project"]["root_path"] == ""
    finally:
        set_projects_dao(None)


@pytest.mark.asyncio
async def test_ipc_project_create_normalizes_relative_root(
    projects_dao: ProjectsDAO, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    set_projects_dao(projects_dao)
    try:
        client = IPCClient()
        reply = await client.request(
            "project.create", {"name": "Rel", "root_path": "."}
        )
        assert reply["project"]["root_path"] == str(tmp_path)
    finally:
        set_projects_dao(None)


@pytest.mark.asyncio
async def test_ipc_project_update_ignores_none_root(
    projects_dao: ProjectsDAO, tmp_path: Path
) -> None:
    pid = _pid()
    await projects_dao.create(id=pid, name="Keep", root_path=str(tmp_path))
    set_projects_dao(projects_dao)
    try:
        client = IPCClient()
        reply = await client.request(
            "project.update", {"project_id": pid, "root_path": None, "name": "Kept"}
        )
        assert reply["project"]["root_path"] == str(tmp_path)
    finally:
        set_projects_dao(None)
