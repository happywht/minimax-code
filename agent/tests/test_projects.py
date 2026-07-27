"""Tests for the project organization surface.

Covers:

* :class:`ProjectsDAO` CRUD, archive, and delete semantics.
* ``project.*`` IPC handlers round-trip.
* Session creation under a project and deletion moving sessions back
  to the inbox.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from minimax_code.app import set_projects_dao, set_sessions_dao
from minimax_code.ipc.client import IPCClient
from minimax_code.storage.dao.projects import ProjectsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
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
def id_factory() -> Any:
    def _make(prefix: str) -> str:
        return f"{prefix}_{uuid.uuid4().hex[:10]}"

    return _make


@pytest.fixture
async def projects_dao(async_db: AsyncDatabase) -> ProjectsDAO:
    dao = ProjectsDAO(async_db)
    await dao.ensure_inbox()
    return dao


@pytest.fixture
async def sessions_dao(async_db: AsyncDatabase) -> SessionsDAO:
    return SessionsDAO(async_db)


@pytest.mark.asyncio
async def test_inbox_is_created_by_ensure_inbox(projects_dao: ProjectsDAO) -> None:
    inbox = await projects_dao.ensure_inbox()
    assert inbox["id"] == "inbox"
    assert inbox["name"] == "收件箱"


@pytest.mark.asyncio
async def test_create_and_get_project(projects_dao: ProjectsDAO, id_factory: Any) -> None:
    pid = id_factory("proj")
    row = await projects_dao.create(id=pid, name="Test Project", description="desc")
    assert row["id"] == pid
    assert row["name"] == "Test Project"
    assert row["description"] == "desc"
    assert row["archived"] is False

    fetched = await projects_dao.get(pid)
    assert fetched is not None
    assert fetched["name"] == "Test Project"


@pytest.mark.asyncio
async def test_update_project_name(projects_dao: ProjectsDAO, id_factory: Any) -> None:
    pid = id_factory("proj")
    await projects_dao.create(id=pid, name="Old")
    row = await projects_dao.update(pid, name="New")
    assert row is not None
    assert row["name"] == "New"


@pytest.mark.asyncio
async def test_cannot_update_inbox(projects_dao: ProjectsDAO) -> None:
    with pytest.raises(ValueError):
        await projects_dao.update("inbox", name="Nope")


@pytest.mark.asyncio
async def test_archive_and_unarchive_project(
    projects_dao: ProjectsDAO, id_factory: Any
) -> None:
    pid = id_factory("proj")
    await projects_dao.create(id=pid, name="Archive Me")
    row = await projects_dao.set_archived(pid, True)
    assert row is not None
    assert row["archived"] is True
    row = await projects_dao.set_archived(pid, False)
    assert row is not None
    assert row["archived"] is False


@pytest.mark.asyncio
async def test_delete_project_moves_sessions_to_inbox(
    projects_dao: ProjectsDAO,
    sessions_dao: SessionsDAO,
    id_factory: Any,
) -> None:
    pid = id_factory("proj")
    await projects_dao.create(id=pid, name="Temp")
    sid = id_factory("ses")
    await sessions_dao.create(id=sid, title="session in project", project_id=pid)

    ok = await projects_dao.delete(pid)
    assert ok is True

    session = await sessions_dao.get(sid)
    assert session is not None
    assert session["project_id"] == "inbox"

    assert await projects_dao.get(pid) is None


@pytest.mark.asyncio
async def test_cannot_delete_inbox(projects_dao: ProjectsDAO) -> None:
    await projects_dao.ensure_inbox()
    assert await projects_dao.delete("inbox") is False


@pytest.mark.asyncio
async def test_list_projects_filters_archived(
    projects_dao: ProjectsDAO, id_factory: Any
) -> None:
    active = id_factory("proj")
    archived = id_factory("proj")
    await projects_dao.create(id=active, name="Active")
    await projects_dao.create(id=archived, name="Archived")
    await projects_dao.set_archived(archived, True)

    active_rows = await projects_dao.list(archived=False)
    assert any(p["id"] == active for p in active_rows)
    assert not any(p["id"] == archived for p in active_rows)

    archived_rows = await projects_dao.list(archived=True)
    assert any(p["id"] == archived for p in archived_rows)


@pytest.mark.asyncio
async def test_session_create_defaults_to_inbox(
    sessions_dao: SessionsDAO,
) -> None:
    row = await sessions_dao.create(id="ses_default", title="no project")
    assert row["project_id"] == "inbox"


@pytest.mark.asyncio
async def test_session_list_filters_by_project(
    sessions_dao: SessionsDAO,
    projects_dao: ProjectsDAO,
    id_factory: Any,
) -> None:
    pid = id_factory("proj")
    await projects_dao.create(id=pid, name="P")
    await sessions_dao.create(id="ses_inbox", title="inbox")
    await sessions_dao.create(id="ses_proj", title="in project", project_id=pid)

    inbox_rows = await sessions_dao.list(project_id="inbox")
    assert len(inbox_rows) == 1
    assert inbox_rows[0]["id"] == "ses_inbox"

    proj_rows = await sessions_dao.list(project_id=pid)
    assert len(proj_rows) == 1
    assert proj_rows[0]["id"] == "ses_proj"


@pytest.mark.asyncio
async def test_ipc_project_create_and_list(
    projects_dao: ProjectsDAO,
) -> None:
    set_projects_dao(projects_dao)
    try:
        client = IPCClient()
        reply = await client.request("project.create", {"name": "IPC Project"})
        assert reply["project"]["name"] == "IPC Project"
        assert reply["project"]["id"].startswith("proj_")

        listed = await client.request("project.list", {})
        assert any(p["name"] == "IPC Project" for p in listed["projects"])
    finally:
        set_projects_dao(None)


@pytest.mark.asyncio
async def test_ipc_project_archive_delete_moves_sessions(
    projects_dao: ProjectsDAO,
    sessions_dao: SessionsDAO,
    id_factory: Any,
) -> None:
    pid = id_factory("proj")
    await projects_dao.create(id=pid, name="To Delete")
    sid = id_factory("ses")
    await sessions_dao.create(id=sid, title="task", project_id=pid)

    set_projects_dao(projects_dao)
    set_sessions_dao(sessions_dao)
    try:
        client = IPCClient()
        archived = await client.request("project.archive", {"project_id": pid})
        assert archived["project"]["archived"] is True

        deleted = await client.request("project.delete", {"project_id": pid})
        assert deleted["ok"] is True

        session_reply = await client.request("session.get", {"session_id": sid})
        assert session_reply["session"]["project_id"] == "inbox"
    finally:
        set_projects_dao(None)
        set_sessions_dao(None)
