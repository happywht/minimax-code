"""Tests for team.* IPC handlers — stub DAO + _CapturedReply context."""

from __future__ import annotations

from typing import Any

import pytest

from minimax_code.config import Config
from minimax_code.ipc.handlers_teams import register_team_handlers
from minimax_code.ipc.server import IPCServer

# ── Fakes ─────────────────────────────────────────────────────────────────────


class _CapturedReply:
    """Minimal Context substitute that captures reply / reply_error."""

    def __init__(self) -> None:
        self.reply_value: dict[str, Any] | None = None
        self.error_value: dict[str, Any] | None = None

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.error_value = {"code": code, "message": message, "data": data}


class _StubDAO:
    """In-memory stub for AgentTeamDAO used by handler tests."""

    def __init__(self) -> None:
        self._teams: dict[str, dict[str, Any]] = {}
        self._counter = 0

    async def list_all(self) -> list[dict[str, Any]]:
        return list(self._teams.values())

    async def get_by_name(self, name: str) -> dict[str, Any] | None:
        return self._teams.get(name)

    async def create(self, **kw: Any) -> dict[str, Any]:
        name = kw["name"]
        if name in self._teams:
            raise ValueError(f"duplicate team name: {name}")
        self._counter += 1
        team = {
            "id": f"team_{self._counter:010d}",
            "name": name,
            "description": kw.get("description", ""),
            "icon": kw.get("icon", ""),
            "color": kw.get("color", ""),
            "agents": kw.get("agents", []),
            "orchestration_mode": kw.get("orchestration_mode", "parallel"),
            "enabled": True,
            "created_at": "2026-06-07T00:00:00",
            "updated_at": "2026-06-07T00:00:00",
        }
        self._teams[name] = team
        return team

    async def update(self, name: str, **kw: Any) -> dict[str, Any] | None:
        team = self._teams.get(name)
        if team is None:
            return None
        team.update(kw)
        return team

    async def set_enabled(self, name: str, val: bool) -> dict[str, Any] | None:
        team = self._teams.get(name)
        if team is None:
            return None
        team["enabled"] = val
        return team

    async def delete(self, name: str) -> bool:
        return self._teams.pop(name, None) is not None


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def stub_dao() -> _StubDAO:
    return _StubDAO()


@pytest.fixture
def handlers(stub_dao: _StubDAO) -> dict[str, Any]:
    server = IPCServer(Config())
    register_team_handlers(server, dao=stub_dao)
    return server._handlers


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_list_empty(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["team.list"](None, ctx)
    assert ctx.reply_value == {"teams": []}


@pytest.mark.asyncio
async def test_team_create_and_get(
    handlers: dict[str, Any], stub_dao: _StubDAO
) -> None:
    ctx = _CapturedReply()
    await handlers["team.create"](
        {"name": "alpha", "description": "Team Alpha", "agents": ["a1", "a2"]},
        ctx,
    )
    assert ctx.reply_value is not None
    team = ctx.reply_value["team"]
    assert team["name"] == "alpha"
    assert team["agents"] == ["a1", "a2"]

    # get by name
    ctx2 = _CapturedReply()
    await handlers["team.get"]({"name": "alpha"}, ctx2)
    assert ctx2.reply_value["team"]["name"] == "alpha"


@pytest.mark.asyncio
async def test_team_create_missing_name(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["team.create"]({}, ctx)
    assert ctx.error_value is not None
    assert "missing" in ctx.error_value["message"].lower()


@pytest.mark.asyncio
async def test_team_create_empty_name(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["team.create"]({"name": "  "}, ctx)
    assert ctx.error_value is not None
    assert "non-empty" in ctx.error_value["message"].lower()


@pytest.mark.asyncio
async def test_team_create_invalid_agents(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["team.create"]({"name": "bad", "agents": "not-a-list"}, ctx)
    assert ctx.error_value is not None
    assert "agents must be a list" in ctx.error_value["message"]


@pytest.mark.asyncio
async def test_team_update(handlers: dict[str, Any], stub_dao: _StubDAO) -> None:
    # create first
    await stub_dao.create(name="beta", agents=["x"])
    ctx = _CapturedReply()
    await handlers["team.update"](
        {"name": "beta", "description": "updated", "color": "#ff0000"},
        ctx,
    )
    assert ctx.reply_value is not None
    assert ctx.reply_value["team"]["description"] == "updated"
    assert ctx.reply_value["team"]["color"] == "#ff0000"


@pytest.mark.asyncio
async def test_team_update_unknown_name(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["team.update"]({"name": "ghost"}, ctx)
    assert ctx.error_value is not None
    assert "unknown" in ctx.error_value["message"]


@pytest.mark.asyncio
async def test_team_enable_disable(
    handlers: dict[str, Any], stub_dao: _StubDAO
) -> None:
    await stub_dao.create(name="gamma")

    ctx_off = _CapturedReply()
    await handlers["team.disable"]({"name": "gamma"}, ctx_off)
    assert ctx_off.reply_value["team"]["enabled"] is False

    ctx_on = _CapturedReply()
    await handlers["team.enable"]({"name": "gamma"}, ctx_on)
    assert ctx_on.reply_value["team"]["enabled"] is True


@pytest.mark.asyncio
async def test_team_delete(
    handlers: dict[str, Any], stub_dao: _StubDAO
) -> None:
    await stub_dao.create(name="delta")
    ctx = _CapturedReply()
    await handlers["team.delete"]({"name": "delta"}, ctx)
    assert ctx.reply_value == {"ok": True, "name": "delta"}

    # verify gone
    ctx2 = _CapturedReply()
    await handlers["team.get"]({"name": "delta"}, ctx2)
    assert ctx2.error_value is not None
    assert "unknown" in ctx2.error_value["message"]


@pytest.mark.asyncio
async def test_team_delete_unknown(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["team.delete"]({"name": "ghost"}, ctx)
    assert ctx.error_value is not None
    assert "unknown" in ctx.error_value["message"]


@pytest.mark.asyncio
async def test_team_list_after_creates(
    handlers: dict[str, Any], stub_dao: _StubDAO
) -> None:
    await stub_dao.create(name="t1")
    await stub_dao.create(name="t2")
    ctx = _CapturedReply()
    await handlers["team.list"](None, ctx)
    names = {t["name"] for t in ctx.reply_value["teams"]}
    assert names == {"t1", "t2"}
