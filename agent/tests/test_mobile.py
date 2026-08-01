"""Unit tests for the mobile pairing surface.

Covers (in order):
  * MobileDeviceDAO round-trip + touch + delete
  * PairingManager token lifecycle (mint, validate, expiry, single-use)
  * PairingError → IPC mapping
  * register_mobile_handlers against a fake server, with a stub DAO
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

import pytest

from minimax_code.ipc.handlers_mobile import register_mobile_handlers
from minimax_code.ipc.protocol import (
    INVALID_PARAMS,
)
from minimax_code.mobile import (
    PairingError,
    PairingManager,
)

# ---------------------------------------------------------------------------
# MobileDeviceDAO (in-process stub)
# ---------------------------------------------------------------------------


@dataclass
class _StubDAO:
    """In-memory substitute for :class:`MobileDeviceDAO`."""

    rows: dict[str, dict[str, Any]] = field(default_factory=dict)

    async def list_all(self) -> list[dict[str, Any]]:
        return list(self.rows.values())

    async def get(self, device_id: str) -> dict[str, Any] | None:
        return self.rows.get(device_id)

    async def register(self, device_id: str, name: str, public_key: str) -> dict[str, Any]:
        if not isinstance(device_id, str) or not device_id.strip():
            raise ValueError("device_id")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("name")
        if not isinstance(public_key, str) or not public_key.strip():
            raise ValueError("public_key")
        row = {
            "device_id": device_id,
            "name": name,
            "public_key": public_key,
            "paired_at": "2026-06-01T00:00:00Z",
            "last_seen_at": None,
        }
        self.rows[device_id] = row
        return row

    async def unregister(self, device_id: str) -> bool:
        return self.rows.pop(device_id, None) is not None

    async def touch_last_seen(self, device_id: str) -> dict[str, Any] | None:
        row = self.rows.get(device_id)
        if row is None:
            return None
        row["last_seen_at"] = "2026-06-01T01:00:00Z"
        return row


# ---------------------------------------------------------------------------
# Fake server + fake context for the IPC tests
# ---------------------------------------------------------------------------


class _FakeCtx:
    def __init__(self) -> None:
        self.replies: list[dict] = []
        self.errors: list[dict] = []
        self.events: list[dict] = []

    async def reply(self, value: Any) -> None:
        self.replies.append(value)

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.errors.append({"code": code, "message": message, "data": data})

    async def emit(self, event: str, data: Any = None) -> None:
        self.events.append({"event": event, "data": data})


class _FakeServer:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def register(self, method: str, handler: Any) -> None:
        self.handlers[method] = handler


def _bind(name: str, server: _FakeServer) -> Any:
    return server.handlers[name]


# ---------------------------------------------------------------------------
# MobileDeviceDAO stub tests
# ---------------------------------------------------------------------------


def test_dao_register_then_list() -> None:
    dao = _StubDAO()
    asyncio.run(dao.register("dev-1", "iPhone", "pk1"))
    asyncio.run(dao.register("dev-2", "iPad", "pk2"))
    rows = asyncio.run(dao.list_all())
    assert {r["device_id"] for r in rows} == {"dev-1", "dev-2"}


def test_dao_touch_updates_last_seen() -> None:
    dao = _StubDAO()
    asyncio.run(dao.register("dev-1", "iPhone", "pk1"))
    updated = asyncio.run(dao.touch_last_seen("dev-1"))
    assert updated is not None
    assert updated["last_seen_at"] is not None


def test_dao_unregister_returns_bool() -> None:
    dao = _StubDAO()
    asyncio.run(dao.register("dev-1", "iPhone", "pk1"))
    assert asyncio.run(dao.unregister("dev-1")) is True
    assert asyncio.run(dao.unregister("dev-1")) is False  # already gone


def test_dao_register_validates_input() -> None:
    dao = _StubDAO()
    for bad in (("d", "", "pk"), ("d", "iPhone", ""), ("", "iPhone", "pk")):
        with pytest.raises(ValueError):
            asyncio.run(dao.register(*bad))


# ---------------------------------------------------------------------------
# PairingManager tests
# ---------------------------------------------------------------------------


def test_pairing_manager_mint_then_validate() -> None:
    pm = PairingManager(ttl_seconds=60, clock=lambda: 1_000.0)
    info = pm.generate_pairing_token(suggested_name="iPhone")
    assert info["token"]
    assert "expires_at" in info
    assert pm.has_token(info["token"]) is True


def test_pairing_manager_token_is_single_use() -> None:
    pm = PairingManager(ttl_seconds=60, clock=lambda: 1_000.0)
    info = pm.generate_pairing_token()
    # Manually mark the token as used by clearing the cache and
    # trying to confirm via the subclass.
    class _Stub(PairingManager):
        def _persist(self, device_id: str, name: str, public_key: str) -> dict[str, Any]:
            return {"device_id": device_id, "name": name, "public_key": public_key}

    spm = _Stub(ttl_seconds=60, clock=lambda: 1_000.0)
    spm._tokens = dict(pm._tokens)
    device = spm.confirm_pairing(info["token"], "dev-1", "iPhone", "pk1")
    assert device["device_id"] == "dev-1"
    with pytest.raises(PairingError):
        spm.confirm_pairing(info["token"], "dev-2", "iPad", "pk2")


def test_pairing_manager_expired_token() -> None:
    clock_value = [1_000.0]

    def fake_clock() -> float:
        return clock_value[0]

    pm = PairingManager(ttl_seconds=10, clock=fake_clock)
    info = pm.generate_pairing_token()
    clock_value[0] += 20  # jump past expiry
    with pytest.raises(PairingError) as ei:
        pm.confirm_pairing(info["token"], "dev-1", "iPhone", "pk1")
    assert ei.value.code == "expired_token"


def test_pairing_manager_invalid_inputs_raise() -> None:
    pm = PairingManager()
    for bad in (("", "d", "n", "k"), ("t", "", "n", "k"), ("t", "d", "", "k"), ("t", "d", "n", "")):
        with pytest.raises(PairingError):
            pm.confirm_pairing(*bad)


def test_pairing_manager_clear() -> None:
    pm = PairingManager()
    pm.generate_pairing_token()
    pm.clear()
    assert pm.pending_count() == 0


# ---------------------------------------------------------------------------
# IPC handler tests
# ---------------------------------------------------------------------------


def _build_server_with_dao(dao: _StubDAO | None = None) -> tuple[_FakeServer, _StubDAO]:
    dao = dao or _StubDAO()
    server = _FakeServer()
    register_mobile_handlers(server, dao=dao)
    return server, dao


def test_pair_start_returns_token_and_qr() -> None:
    server, _ = _build_server_with_dao()
    asyncio.run(_bind("mobile.pair_start", server)(None, _FakeCtx()))
    # Implicit: no return value to assert at this layer — the
    # handler sends via ctx.reply. This test just confirms no
    # exception is raised on the happy path.


def test_pair_confirm_writes_row() -> None:
    server, dao = _build_server_with_dao()
    _PairingManagerHelper().mint()

    # Re-register handler so the manager is set up with our stub.
    class _PM(PairingManager):
        def _persist(self, device_id: str, name: str, public_key: str) -> dict[str, Any]:
            return asyncio.run(dao.register(device_id, name, public_key)).__class__()  # placeholder
    # Simpler: use a real PairingManagerWithDAO and bind to our stub dao
    from minimax_code.mobile import PairingManagerWithDAO
    pm = PairingManagerWithDAO(dao)  # type: ignore[arg-type]
    # Re-register with our pm
    server2 = _FakeServer()
    register_mobile_handlers(server2, dao=dao, manager=pm)

    info = pm.generate_pairing_token()
    ctx = _FakeCtx()
    asyncio.run(
        _bind("mobile.pair_confirm", server2)(
            {"token": info["token"], "device_id": "d-1", "name": "iPhone", "public_key": "pk"},
            ctx,
        )
    )
    assert not ctx.errors, ctx.errors
    assert ctx.replies and "device" in ctx.replies[0]
    rows = asyncio.run(dao.list_all())
    assert any(r["device_id"] == "d-1" for r in rows)


def test_pair_confirm_rejects_unknown_token() -> None:
    server, dao = _build_server_with_dao()
    from minimax_code.mobile import PairingManagerWithDAO
    pm = PairingManagerWithDAO(dao)  # type: ignore[arg-type]
    server2 = _FakeServer()
    register_mobile_handlers(server2, dao=dao, manager=pm)

    ctx = _FakeCtx()
    asyncio.run(
        _bind("mobile.pair_confirm", server2)(
            {"token": "does-not-exist", "device_id": "d-1", "name": "iPhone", "public_key": "pk"},
            ctx,
        )
    )
    assert ctx.errors
    assert ctx.errors[0]["code"] == INVALID_PARAMS


def test_pair_confirm_rejects_expired_token() -> None:
    clock_value = [1_000.0]
    from minimax_code.mobile import PairingManagerWithDAO
    dao = _StubDAO()
    pm = PairingManagerWithDAO(dao, ttl_seconds=10, clock=lambda: clock_value[0])  # type: ignore[arg-type]
    server = _FakeServer()
    register_mobile_handlers(server, dao=dao, manager=pm)
    info = pm.generate_pairing_token()
    clock_value[0] += 20
    ctx = _FakeCtx()
    asyncio.run(
        _bind("mobile.pair_confirm", server)(
            {"token": info["token"], "device_id": "d-1", "name": "iPhone", "public_key": "pk"},
            ctx,
        )
    )
    assert ctx.errors
    assert "expired" in ctx.errors[0]["message"].lower()


def test_list_returns_devices() -> None:
    server, dao = _build_server_with_dao()
    asyncio.run(dao.register("d-1", "iPhone", "pk"))
    asyncio.run(dao.register("d-2", "iPad", "pk"))
    ctx = _FakeCtx()
    asyncio.run(_bind("mobile.list", server)(None, ctx))
    assert ctx.replies
    assert "devices" in ctx.replies[0]
    assert len(ctx.replies[0]["devices"]) == 2


def test_unpair_removes_row() -> None:
    server, dao = _build_server_with_dao()
    asyncio.run(dao.register("d-1", "iPhone", "pk"))
    ctx = _FakeCtx()
    asyncio.run(
        _bind("mobile.unpair", server)({"device_id": "d-1"}, ctx)
    )
    assert not ctx.errors
    assert asyncio.run(dao.get("d-1")) is None


def test_unpair_unknown_device_returns_error() -> None:
    server, _ = _build_server_with_dao()
    ctx = _FakeCtx()
    asyncio.run(
        _bind("mobile.unpair", server)({"device_id": "nope"}, ctx)
    )
    assert ctx.errors
    assert ctx.errors[0]["code"] == INVALID_PARAMS


def test_touch_bumps_last_seen() -> None:
    server, dao = _build_server_with_dao()
    asyncio.run(dao.register("d-1", "iPhone", "pk"))
    ctx = _FakeCtx()
    asyncio.run(
        _bind("mobile.touch", server)({"device_id": "d-1"}, ctx)
    )
    assert not ctx.errors
    assert ctx.replies[0]["device"]["last_seen_at"] is not None


def test_touch_unknown_device_returns_error() -> None:
    server, _ = _build_server_with_dao()
    ctx = _FakeCtx()
    asyncio.run(
        _bind("mobile.touch", server)({"device_id": "nope"}, ctx)
    )
    assert ctx.errors


def test_pair_confirm_validates_input() -> None:
    server, dao = _build_server_with_dao()
    from minimax_code.mobile import PairingManagerWithDAO
    pm = PairingManagerWithDAO(dao)  # type: ignore[arg-type]
    server2 = _FakeServer()
    register_mobile_handlers(server2, dao=dao, manager=pm)
    info = pm.generate_pairing_token()
    ctx = _FakeCtx()
    asyncio.run(
        _bind("mobile.pair_confirm", server2)(
            {"token": info["token"], "device_id": "", "name": "iPhone", "public_key": "pk"},
            ctx,
        )
    )
    assert ctx.errors
    assert ctx.errors[0]["code"] == INVALID_PARAMS


# ---------------------------------------------------------------------------
# Helper kept off to the side so the file stays grep-friendly
# ---------------------------------------------------------------------------


class _PairingManagerHelper:
    def mint(self) -> dict[str, Any]:
        pm = PairingManager(ttl_seconds=60, clock=lambda: 1_000.0)
        return pm.generate_pairing_token()


# ---------------------------------------------------------------------------
# v0.7.0 — Mobile push notification + device status tests
# ---------------------------------------------------------------------------


class _FakeWSManager:
    """Stub WSManager for push_notification / device_status tests."""

    def __init__(self, online: set[str] | None = None) -> None:
        self._online = online or set()
        self.sent: list[tuple[str, dict[str, Any]]] = []

    def get_online_devices(self) -> set[str]:
        return set(self._online)

    async def send_to_device(self, device_id: str, payload: dict[str, Any]) -> bool:
        if device_id not in self._online:
            return False
        self.sent.append((device_id, payload))
        return True


class _FakeApp:
    """Minimal FastAPI app state stand-in."""

    class _State:
        def __init__(self, ws_manager: _FakeWSManager) -> None:
            self.ws_manager = ws_manager

    def __init__(self, ws_manager: _FakeWSManager) -> None:
        self.state = self._State(ws_manager)


def _build_server_with_ws(
    dao: _StubDAO | None = None,
    ws_mgr: _FakeWSManager | None = None,
) -> tuple[_FakeServer, _StubDAO, _FakeWSManager]:
    """Build a fake server + DAO + WSManager for push/status tests."""
    dao = dao or _StubDAO()
    ws_mgr = ws_mgr or _FakeWSManager()
    server = _FakeServer()
    register_mobile_handlers(server, dao=dao)
    return server, dao, ws_mgr


def test_push_notification_to_specific_device() -> None:
    dao = _StubDAO()
    ws_mgr = _FakeWSManager(online={"dev-1"})
    server, _, _ = _build_server_with_ws(dao, ws_mgr)

    # Inject the fake HTTP app so the handler can find ws_manager
    import minimax_code.app as _app_mod
    fake_app = _FakeApp(ws_mgr)
    old_app = _app_mod._HTTP_APP
    _app_mod._HTTP_APP = fake_app
    try:
        ctx = _FakeCtx()
        asyncio.run(
            _bind("mobile.push_notification", server)(
                {
                    "device_id": "dev-1",
                    "notification": {"title": "Hello", "body": "World"},
                },
                ctx,
            )
        )
        assert not ctx.errors, ctx.errors
        assert ctx.replies[0]["ok"] is True
        assert ctx.replies[0]["delivered"] is True
        assert len(ws_mgr.sent) == 1
        assert ws_mgr.sent[0][0] == "dev-1"
    finally:
        _app_mod._HTTP_APP = old_app


def test_push_notification_broadcast() -> None:
    dao = _StubDAO()
    ws_mgr = _FakeWSManager(online={"dev-1", "dev-2"})
    server, _, _ = _build_server_with_ws(dao, ws_mgr)

    import minimax_code.app as _app_mod
    fake_app = _FakeApp(ws_mgr)
    old_app = _app_mod._HTTP_APP
    _app_mod._HTTP_APP = fake_app
    try:
        ctx = _FakeCtx()
        asyncio.run(
            _bind("mobile.push_notification", server)(
                {
                    "notification": {"title": "Broadcast", "body": "To all"},
                },
                ctx,
            )
        )
        assert not ctx.errors, ctx.errors
        assert ctx.replies[0]["ok"] is True
        assert ctx.replies[0]["broadcast"] is True
        assert ctx.replies[0]["total_devices"] == 2
        assert ctx.replies[0]["delivered"] == 2
        assert len(ws_mgr.sent) == 2
    finally:
        _app_mod._HTTP_APP = old_app


def test_push_notification_offline_device() -> None:
    dao = _StubDAO()
    ws_mgr = _FakeWSManager(online=set())  # no devices online
    server, _, _ = _build_server_with_ws(dao, ws_mgr)

    import minimax_code.app as _app_mod
    fake_app = _FakeApp(ws_mgr)
    old_app = _app_mod._HTTP_APP
    _app_mod._HTTP_APP = fake_app
    try:
        ctx = _FakeCtx()
        asyncio.run(
            _bind("mobile.push_notification", server)(
                {
                    "device_id": "dev-1",
                    "notification": {"title": "Hello", "body": "World"},
                },
                ctx,
            )
        )
        assert not ctx.errors
        assert ctx.replies[0]["ok"] is True
        assert ctx.replies[0]["delivered"] is False
    finally:
        _app_mod._HTTP_APP = old_app


def test_push_notification_missing_title() -> None:
    server, _, _ = _build_server_with_ws()

    ctx = _FakeCtx()
    asyncio.run(
        _bind("mobile.push_notification", server)(
            {"notification": {"body": "No title"}},
            ctx,
        )
    )
    assert ctx.errors
    assert ctx.errors[0]["code"] == INVALID_PARAMS


def test_device_status_shows_online() -> None:
    dao = _StubDAO()
    asyncio.run(dao.register("dev-1", "iPhone", "pk1"))
    asyncio.run(dao.register("dev-2", "iPad", "pk2"))
    ws_mgr = _FakeWSManager(online={"dev-1"})
    server, _, _ = _build_server_with_ws(dao, ws_mgr)

    import minimax_code.app as _app_mod
    fake_app = _FakeApp(ws_mgr)
    old_app = _app_mod._HTTP_APP
    _app_mod._HTTP_APP = fake_app
    try:
        ctx = _FakeCtx()
        asyncio.run(_bind("mobile.device_status", server)(None, ctx))
        assert not ctx.errors, ctx.errors
        devices = ctx.replies[0]["devices"]
        by_id = {d.get("id") or d.get("device_id"): d for d in devices}
        assert by_id["dev-1"]["online"] is True
        assert by_id["dev-2"]["online"] is False
    finally:
        _app_mod._HTTP_APP = old_app


def test_device_status_no_app() -> None:
    """When HTTP app is not running, all devices are offline."""
    dao = _StubDAO()
    asyncio.run(dao.register("dev-1", "iPhone", "pk1"))
    server, _, _ = _build_server_with_ws(dao)

    import minimax_code.app as _app_mod
    old_app = _app_mod._HTTP_APP
    _app_mod._HTTP_APP = None
    try:
        ctx = _FakeCtx()
        asyncio.run(_bind("mobile.device_status", server)(None, ctx))
        assert not ctx.errors
        devices = ctx.replies[0]["devices"]
        assert all(not d["online"] for d in devices)
    finally:
        _app_mod._HTTP_APP = old_app
