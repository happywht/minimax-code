"""Tests for message-level DAO and IPC operations."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from minimax_code.ipc.client import IPCClient
from minimax_code.storage.dao.messages import MessagesDAO
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
async def sessions_dao(async_db: AsyncDatabase) -> SessionsDAO:
    return SessionsDAO(async_db)


@pytest.fixture
async def messages_dao(async_db: AsyncDatabase) -> MessagesDAO:
    return MessagesDAO(async_db)


@pytest.mark.asyncio
async def test_update_content_persists(
    messages_dao: MessagesDAO, sessions_dao: SessionsDAO
) -> None:
    sid = "ses_update_1"
    await sessions_dao.create(id=sid, title="t")
    msg = await messages_dao.create(id="msg_u1", session_id=sid, role="user", content="hello")
    updated = await messages_dao.update(msg["id"], content="hello world")
    assert updated is not None
    assert updated["content"] == "hello world"
    fetched = await messages_dao.get(msg["id"])
    assert fetched is not None
    assert fetched["content"] == "hello world"


@pytest.mark.asyncio
async def test_delete_removes_row(
    messages_dao: MessagesDAO, sessions_dao: SessionsDAO
) -> None:
    sid = "ses_delete_1"
    await sessions_dao.create(id=sid, title="t")
    msg = await messages_dao.create(id="msg_d1", session_id=sid, role="user", content="x")
    assert await messages_dao.delete(msg["id"]) is True
    assert await messages_dao.get(msg["id"]) is None


@pytest.mark.asyncio
async def test_update_unknown_returns_none(messages_dao: MessagesDAO) -> None:
    assert await messages_dao.update("msg_nope", content="x") is None


@pytest.mark.asyncio
async def test_delete_unknown_returns_false(messages_dao: MessagesDAO) -> None:
    assert await messages_dao.delete("msg_nope") is False


@pytest.mark.asyncio
async def test_message_ipc_update_round_trip(
    async_db: AsyncDatabase,
    messages_dao: MessagesDAO,
    sessions_dao: SessionsDAO,
) -> None:
    sid = "ses_ipc_u"
    await sessions_dao.create(id=sid, title="t")
    msg = await messages_dao.create(id="msg_ipc_u", session_id=sid, role="user", content="a")

    async def fake_init_runtime() -> object:
        return object()

    with (
        patch("minimax_code.app.init_runtime", side_effect=fake_init_runtime),
        patch("minimax_code.app.get_db", return_value=async_db),
    ):
        client = IPCClient()
        reply = await client.request(
            "message.update", {"message_id": msg["id"], "content": "b"}
        )
        assert reply["ok"] is True
        assert reply["message"]["content"] == "b"


@pytest.mark.asyncio
async def test_message_ipc_delete_round_trip(
    async_db: AsyncDatabase,
    messages_dao: MessagesDAO,
    sessions_dao: SessionsDAO,
) -> None:
    sid = "ses_ipc_d"
    await sessions_dao.create(id=sid, title="t")
    msg = await messages_dao.create(id="msg_ipc_d", session_id=sid, role="user", content="x")

    async def fake_init_runtime() -> object:
        return object()

    with (
        patch("minimax_code.app.init_runtime", side_effect=fake_init_runtime),
        patch("minimax_code.app.get_db", return_value=async_db),
    ):
        client = IPCClient()
        reply = await client.request("message.delete", {"message_id": msg["id"]})
        assert reply["ok"] is True
        assert reply["message_id"] == msg["id"]
        assert await messages_dao.get(msg["id"]) is None


@pytest.mark.asyncio
async def test_message_ipc_unknown_id_raises(
    async_db: AsyncDatabase,
) -> None:
    async def fake_init_runtime() -> object:
        return object()

    with (
        patch("minimax_code.app.init_runtime", side_effect=fake_init_runtime),
        patch("minimax_code.app.get_db", return_value=async_db),
    ):
        client = IPCClient()
        with pytest.raises(RuntimeError):
            await client.request("message.update", {"message_id": "msg_nope", "content": "x"})
        with pytest.raises(RuntimeError):
            await client.request("message.delete", {"message_id": "msg_nope"})
