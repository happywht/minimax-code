"""Tests for v0.7.0 Notification infrastructure.

Coverage:

* :class:`TestNotificationDAO` — CRUD round-trip, list filters,
  mark_read, mark_all_read, purge, count_unread
* :class:`TestNotificationIPC` — drives the ``notification.*`` handlers
  in-process via ``IPCServer``
* :class:`TestNotificationManager` — notify() writes DB + pushes event
* :class:`TestPermissionResolved` — verify ``permission.resolved`` is
  emitted after resolve (the bug fix)
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

import sys

from minimax_code.config import Config
from minimax_code.ipc.server import IPCServer
from minimax_code.notifications import (
    NotificationManager,
    get_notification_manager,
    set_notification_manager,
)
from minimax_code.storage.dao.notifications import NotificationDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path


# ---------------------------------------------------------------------------
# Fixtures
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
def notif_dao(async_db: AsyncDatabase) -> NotificationDAO:
    return NotificationDAO(async_db)


# ---------------------------------------------------------------------------
# DAO
# ---------------------------------------------------------------------------


class TestNotificationDAO:
    @pytest.mark.asyncio
    async def test_create_and_get(self, notif_dao: NotificationDAO) -> None:
        entry = await notif_dao.create(type="info", title="Hello")
        assert entry["id"].startswith("ntf_")
        assert entry["type"] == "info"
        assert entry["title"] == "Hello"
        assert entry["read"] is False

        got = await notif_dao.get(entry["id"])
        assert got is not None
        assert got["id"] == entry["id"]

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, notif_dao: NotificationDAO) -> None:
        assert await notif_dao.get("ntf_no_such") is None

    @pytest.mark.asyncio
    async def test_list_unread(self, notif_dao: NotificationDAO) -> None:
        await notif_dao.create(type="info", title="A")
        await notif_dao.create(type="warning", title="B")
        unread = await notif_dao.list_unread()
        assert len(unread) == 2

    @pytest.mark.asyncio
    async def test_list_all_with_filters(self, notif_dao: NotificationDAO) -> None:
        await notif_dao.create(type="info", title="X", source="webhook")
        await notif_dao.create(type="error", title="Y", source="system")
        await notif_dao.create(type="info", title="Z", source="agent")

        entries, total = await notif_dao.list_all(type="info")
        assert total == 2
        assert len(entries) == 2

        entries2, total2 = await notif_dao.list_all(source="system")
        assert total2 == 1

        entries3, total3 = await notif_dao.list_all(unread_only=True)
        assert total3 == 3

    @pytest.mark.asyncio
    async def test_list_all_pagination(self, notif_dao: NotificationDAO) -> None:
        for i in range(5):
            await notif_dao.create(type="info", title=f"N{i}")
        entries, total = await notif_dao.list_all(limit=2, offset=0)
        assert total == 5
        assert len(entries) == 2

    @pytest.mark.asyncio
    async def test_count_unread(self, notif_dao: NotificationDAO) -> None:
        assert await notif_dao.count_unread() == 0
        await notif_dao.create(type="info", title="A")
        await notif_dao.create(type="info", title="B")
        assert await notif_dao.count_unread() == 2

    @pytest.mark.asyncio
    async def test_mark_read(self, notif_dao: NotificationDAO) -> None:
        entry = await notif_dao.create(type="info", title="Test")
        assert entry["read"] is False

        updated = await notif_dao.mark_read(entry["id"])
        assert updated is not None
        assert updated["read"] is True
        assert updated["read_at"] is not None

        assert await notif_dao.count_unread() == 0

    @pytest.mark.asyncio
    async def test_mark_read_nonexistent(self, notif_dao: NotificationDAO) -> None:
        result = await notif_dao.mark_read("ntf_nope")
        assert result is None

    @pytest.mark.asyncio
    async def test_mark_all_read(self, notif_dao: NotificationDAO) -> None:
        await notif_dao.create(type="info", title="A")
        await notif_dao.create(type="info", title="B")
        count = await notif_dao.mark_all_read()
        assert count == 2
        assert await notif_dao.count_unread() == 0

    @pytest.mark.asyncio
    async def test_delete(self, notif_dao: NotificationDAO) -> None:
        entry = await notif_dao.create(type="info", title="Del")
        assert await notif_dao.delete(entry["id"]) is True
        assert await notif_dao.get(entry["id"]) is None
        assert await notif_dao.delete("ntf_nope") is False

    @pytest.mark.asyncio
    async def test_purge_read_only(self, notif_dao: NotificationDAO) -> None:
        e1 = await notif_dao.create(type="info", title="Read")
        await notif_dao.mark_read(e1["id"])
        await notif_dao.create(type="info", title="Unread")
        count = await notif_dao.purge(read_only=True)
        assert count == 1
        _, total = await notif_dao.list_all()
        assert total == 1

    @pytest.mark.asyncio
    async def test_purge_before_time(self, notif_dao: NotificationDAO) -> None:
        await notif_dao.create(type="info", title="Old")
        # 'Old' is created now; purge before year 2030 should delete it
        count = await notif_dao.purge(before_iso="2030-01-01T00:00:00Z")
        assert count == 1


# ---------------------------------------------------------------------------
# IPC handlers
# ---------------------------------------------------------------------------


class TestNotificationIPC:
    @pytest.fixture
    def server(self, async_db: AsyncDatabase) -> IPCServer:
        cfg = Config.from_env()
        srv = IPCServer(config=cfg, stdin=sys.stdin, stdout=sys.stdout)
        # Inject the DAO directly so handlers skip the lazy factory
        setattr(srv, "_notification_dao", NotificationDAO(async_db))
        from minimax_code.ipc.handlers_notifications import register_notification_handlers
        register_notification_handlers(srv)
        return srv

    async def _call(self, server: IPCServer, method: str, params: dict[str, Any]) -> Any:
        """Call a handler through handle_request (real dispatch path)."""
        resp = await server.handle_request({
            "jsonrpc": "2.0",
            "id": 1,
            "method": method,
            "params": params,
        })
        assert resp is not None
        assert "result" in resp
        return resp["result"]

    @pytest.mark.asyncio
    async def test_list(self, server: IPCServer) -> None:
        dao: NotificationDAO = getattr(server, "_notification_dao")
        await dao.create(type="info", title="IPC Test")
        result = await self._call(server, "notification.list", {})
        assert result["total"] == 1
        assert len(result["entries"]) == 1

    @pytest.mark.asyncio
    async def test_mark_read(self, server: IPCServer) -> None:
        dao: NotificationDAO = getattr(server, "_notification_dao")
        entry = await dao.create(type="info", title="MR")
        result = await self._call(server, "notification.mark_read", {"id": entry["id"]})
        assert result["read"] is True

    @pytest.mark.asyncio
    async def test_mark_all_read(self, server: IPCServer) -> None:
        dao: NotificationDAO = getattr(server, "_notification_dao")
        await dao.create(type="info", title="A")
        await dao.create(type="info", title="B")
        result = await self._call(server, "notification.mark_all_read", {})
        assert result["marked"] == 2

    @pytest.mark.asyncio
    async def test_delete(self, server: IPCServer) -> None:
        dao: NotificationDAO = getattr(server, "_notification_dao")
        entry = await dao.create(type="info", title="Del")
        result = await self._call(server, "notification.delete", {"id": entry["id"]})
        assert result["deleted"] is True

    @pytest.mark.asyncio
    async def test_purge(self, server: IPCServer) -> None:
        dao: NotificationDAO = getattr(server, "_notification_dao")
        await dao.create(type="info", title="Old")
        result = await self._call(server, "notification.purge", {"before_iso": "2030-01-01T00:00:00Z"})
        assert result["purged"] == 1


# ---------------------------------------------------------------------------
# NotificationManager
# ---------------------------------------------------------------------------


class TestNotificationManager:
    @pytest.mark.asyncio
    async def test_notify_creates_and_pushes(
        self, notif_dao: NotificationDAO
    ) -> None:
        cfg = Config.from_env()
        server = IPCServer(config=cfg, stdin=sys.stdin, stdout=sys.stdout)
        events: list[dict[str, Any]] = []
        server.register_listener(lambda env: events.append(env))

        mgr = NotificationManager(notif_dao, server)
        entry = await mgr.notify(type="info", title="Push test")
        assert entry["id"].startswith("ntf_")
        assert entry["read"] is False

        # Should have pushed a notification.new event
        assert len(events) == 1
        assert events[0]["event"] == "notification.new"
        assert events[0]["data"]["id"] == entry["id"]

    @pytest.mark.asyncio
    async def test_singleton(self, notif_dao: NotificationDAO) -> None:
        cfg = Config.from_env()
        server = IPCServer(config=cfg, stdin=sys.stdin, stdout=sys.stdout)
        mgr = NotificationManager(notif_dao, server)
        set_notification_manager(mgr)
        assert get_notification_manager() is mgr
        # Reset
        set_notification_manager(None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Permission resolved bug fix
# ---------------------------------------------------------------------------


class TestPermissionResolvedEvent:
    @pytest.mark.asyncio
    async def test_resolve_emits_event(self, async_db: AsyncDatabase) -> None:
        """Verify that ``permission.resolve`` emits ``permission.resolved``.

        This is the bug fix for the v0.7.0 — before this, the frontend
        subscribed to ``permission.resolved`` but the backend never
        emitted it.
        """
        from minimax_code.ipc.handlers_permissions import register_permission_handlers
        from minimax_code.permissions import PermissionStore

        cfg = Config.from_env()
        server = IPCServer(config=cfg, stdin=sys.stdin, stdout=sys.stdout)
        events: list[dict[str, Any]] = []
        server.register_listener(lambda env: events.append(env))

        store = PermissionStore(async_db)
        register_permission_handlers(server, store=store)

        # Simulate the gater being stashed on the server
        from unittest.mock import MagicMock
        gater = MagicMock()
        gater.resolve = MagicMock(return_value=True)
        setattr(server, "_permission_gater", gater)

        # Resolve a fake request via handle_request (real dispatch path)
        resp = await server.handle_request({
            "jsonrpc": "2.0",
            "id": 42,
            "method": "permission.resolve",
            "params": {
                "request_id": "test-req-1",
                "decision": "allow",
            },
        })
        assert resp is not None
        assert "result" in resp
        assert resp["result"]["ok"] is True

        # Should have emitted the resolved event
        assert len(events) == 1
        assert events[0]["event"] == "permission.resolved"
        assert events[0]["data"]["request_id"] == "test-req-1"
        assert events[0]["data"]["decision"] == "allow"
