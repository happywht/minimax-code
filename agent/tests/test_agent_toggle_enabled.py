"""Regression tests for the agent enable/disable toggle (settings UI bug).

Field report: clicking "停用" (disable) on a sub-agent in the settings
page did nothing. Root cause — the ``enabled`` key was dropped at every
layer of the write path:

1. ``handle_agent_update`` had ``if`` branches for eight extended fields
   (description/icon/color/category/tags/skills/max_iterations/
   temperature) but none for ``enabled``;
2. ``AgentDAO.upsert`` had no ``enabled`` parameter at all — no INSERT
   column, no UPDATE SET.

So ``agent.update {name, enabled: false}`` replied **success** with the
row unchanged (verified by probing a live 8765 instance). The mock
backend (``mock.ts``) already implemented the intended behaviour — the
real backend just never caught up.

A companion gap: ``TeamOrchestrator._resolve_agents`` ignored member
``enabled`` — a disabled agent still ran with its team while the
spawn_subagent tool path rejected it. Both paths now skip disabled
members with a warning.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from minimax_code.config import Config
from minimax_code.ipc.handlers_agents import register_agent_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.orchestrator.team_orchestrator import TeamOrchestrator
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path

# ── Fixtures ──────────────────────────────────────────────────────────────────


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
async def dao(async_db: AsyncDatabase) -> AgentDAO:
    return AgentDAO(async_db)


@pytest.fixture
def handlers(dao: AgentDAO) -> dict[str, Any]:
    """Registered agent.* handlers over an isolated temp database."""
    server = IPCServer(
        config=Config.from_env(),
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )
    register_agent_handlers(server, dao=dao)
    return server._handlers


class _FakeContext:
    """Stand-in for :class:`Context` that captures reply / error."""

    def __init__(self) -> None:
        self.reply_value: dict[str, Any] | None = None
        self.error_value: dict[str, Any] | None = None

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.error_value = {"code": code, "message": message, "data": data}

    async def emit(self, event: str, data: Any) -> None:  # pragma: no cover
        pass


def _unique(prefix: str = "ag") -> str:
    import uuid

    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ── DAO layer ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_insert_with_enabled_false(dao: AgentDAO) -> None:
    """INSERT path must honour an explicit enabled=False."""
    name = _unique()
    row = await dao.upsert(name=name, system_prompt="x", enabled=False)
    assert row["enabled"] is False


@pytest.mark.asyncio
async def test_upsert_update_flips_enabled(dao: AgentDAO) -> None:
    """UPDATE path must flip enabled both ways."""
    name = _unique()
    await dao.upsert(name=name, system_prompt="x")
    off = await dao.upsert(name=name, system_prompt="x", enabled=False)
    assert off["enabled"] is False
    on = await dao.upsert(name=name, system_prompt="x", enabled=True)
    assert on["enabled"] is True


@pytest.mark.asyncio
async def test_upsert_update_none_leaves_enabled(dao: AgentDAO) -> None:
    """enabled=None on update means "don't touch" — a disabled row stays
    disabled when other fields are written."""
    name = _unique()
    await dao.upsert(name=name, system_prompt="x", enabled=False)
    row = await dao.upsert(name=name, system_prompt="y")
    assert row["enabled"] is False
    assert row["system_prompt"] == "y"


# ── Handler layer (agent.update / agent.create) ──────────────────────────────


@pytest.mark.asyncio
async def test_update_disable_round_trip(
    handlers: dict[str, Any], dao: AgentDAO
) -> None:
    """The settings toggle: agent.update {name, enabled: false} must
    persist and echo the flip. Before the fix this replied success with
    enabled still true."""
    name = _unique()
    await dao.upsert(name=name, system_prompt="x")

    ctx = _FakeContext()
    await handlers["agent.update"]({"name": name, "enabled": False}, ctx)
    assert ctx.error_value is None, ctx.error_value
    assert ctx.reply_value is not None
    assert ctx.reply_value["agent"]["enabled"] is False

    # ...and re-enable
    ctx2 = _FakeContext()
    await handlers["agent.update"]({"name": name, "enabled": True}, ctx2)
    assert ctx2.reply_value["agent"]["enabled"] is True

    # persisted, not just echoed
    row = await dao.get(name)
    assert row is not None and row["enabled"] is True


@pytest.mark.asyncio
async def test_update_without_enabled_keeps_state(
    handlers: dict[str, Any], dao: AgentDAO
) -> None:
    """An update touching other fields must not resurrect a disabled row."""
    name = _unique()
    await dao.upsert(name=name, system_prompt="x", enabled=False)

    ctx = _FakeContext()
    await handlers["agent.update"]({"name": name, "system_prompt": "y"}, ctx)
    assert ctx.error_value is None, ctx.error_value
    assert ctx.reply_value["agent"]["enabled"] is False
    assert ctx.reply_value["agent"]["system_prompt"] == "y"


@pytest.mark.asyncio
async def test_create_with_enabled_false(
    handlers: dict[str, Any], dao: AgentDAO
) -> None:
    """agent.create accepts enabled for symmetry (default stays true)."""
    name = _unique()
    ctx = _FakeContext()
    await handlers["agent.create"](
        {"name": name, "system_prompt": "x", "enabled": False}, ctx
    )
    assert ctx.error_value is None, ctx.error_value
    assert ctx.reply_value["agent"]["enabled"] is False


# ── Team resolution: disabled members are skipped ────────────────────────────


@pytest.mark.asyncio
async def test_resolve_agents_skips_disabled(dao: AgentDAO) -> None:
    """A disabled member must not resolve — same skip semantics as a
    deleted member (which the orchestrator already skipped)."""
    await dao.upsert(name="t-on", system_prompt="on")
    await dao.upsert(name="t-off", system_prompt="off", enabled=False)

    orch = TeamOrchestrator(agent_dao=dao)
    configs = await orch._resolve_agents(["t-on", "t-off"])
    assert [c.name for c in configs] == ["t-on"]


@pytest.mark.asyncio
async def test_resolve_agents_all_disabled_returns_empty(dao: AgentDAO) -> None:
    """All members disabled → empty list → the run path reports
    "no valid agents found" (existing failure envelope, no crash)."""
    await dao.upsert(name="t-a", system_prompt="a", enabled=False)
    await dao.upsert(name="t-b", system_prompt="b", enabled=False)

    orch = TeamOrchestrator(agent_dao=dao)
    assert await orch._resolve_agents(["t-a", "t-b"]) == []


@pytest.mark.asyncio
async def test_resolve_agents_enabled_members_unaffected(dao: AgentDAO) -> None:
    """Regression pin: the default-enabled happy path resolves everyone."""
    await dao.upsert(name="t-x", system_prompt="x")
    await dao.upsert(name="t-y", system_prompt="y")

    orch = TeamOrchestrator(agent_dao=dao)
    configs = await orch._resolve_agents(["t-x", "t-y"])
    assert sorted(c.name for c in configs) == ["t-x", "t-y"]
