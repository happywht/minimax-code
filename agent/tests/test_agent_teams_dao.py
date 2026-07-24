"""Tests for AgentTeamDAO — CRUD + enable/disable + JSON hydration."""

from __future__ import annotations

from pathlib import Path

import pytest

from minimax_code.storage.dao.agent_teams import AgentTeamDAO
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
async def dao(async_db: AsyncDatabase) -> AgentTeamDAO:
    return AgentTeamDAO(async_db)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _create_sample(dao: AgentTeamDAO, name: str = "fullstack-review", **kw):
    """Create a team with sensible defaults, forwarding overrides."""
    defaults = dict(
        name=name,
        description="Full-stack review team",
        icon="Users",
        color="#6366f1",
        agents=["coder", "reviewer"],
        orchestration_mode="parallel",
    )
    defaults.update(kw)
    return await dao.create(**defaults)


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_returns_full_record(dao: AgentTeamDAO) -> None:
    team = await _create_sample(dao)
    assert team["name"] == "fullstack-review"
    assert team["agents"] == ["coder", "reviewer"]
    assert team["orchestration_mode"] == "parallel"
    assert team["enabled"] is True
    assert team["id"].startswith("team_")
    assert team["created_at"]


@pytest.mark.asyncio
async def test_get_by_id(dao: AgentTeamDAO) -> None:
    created = await _create_sample(dao)
    fetched = await dao.get(created["id"])
    assert fetched is not None
    assert fetched["name"] == "fullstack-review"


@pytest.mark.asyncio
async def test_get_by_name(dao: AgentTeamDAO) -> None:
    await _create_sample(dao)
    fetched = await dao.get_by_name("fullstack-review")
    assert fetched is not None
    assert fetched["color"] == "#6366f1"


@pytest.mark.asyncio
async def test_get_nonexistent_returns_none(dao: AgentTeamDAO) -> None:
    assert await dao.get("team_nope") is None
    assert await dao.get_by_name("nope") is None


@pytest.mark.asyncio
async def test_list_all_empty(dao: AgentTeamDAO) -> None:
    result = await dao.list_all()
    assert result == []


@pytest.mark.asyncio
async def test_list_all_returns_multiple(dao: AgentTeamDAO) -> None:
    await _create_sample(dao, name="alpha")
    await _create_sample(dao, name="beta")
    teams = await dao.list_all()
    names = {t["name"] for t in teams}
    assert names == {"alpha", "beta"}


@pytest.mark.asyncio
async def test_update_fields(dao: AgentTeamDAO) -> None:
    await _create_sample(dao)
    updated = await dao.update(
        "fullstack-review",
        description="Updated desc",
        color="#f43f5e",
        agents=["coder"],
        orchestration_mode="sequential",
    )
    assert updated is not None
    assert updated["description"] == "Updated desc"
    assert updated["color"] == "#f43f5e"
    assert updated["agents"] == ["coder"]
    assert updated["orchestration_mode"] == "sequential"


@pytest.mark.asyncio
async def test_update_nonexistent_returns_none(dao: AgentTeamDAO) -> None:
    result = await dao.update("ghost", description="nope")
    assert result is None


@pytest.mark.asyncio
async def test_disable_and_enable(dao: AgentTeamDAO) -> None:
    await _create_sample(dao)
    disabled = await dao.set_enabled("fullstack-review", False)
    assert disabled is not None
    assert disabled["enabled"] is False

    enabled = await dao.set_enabled("fullstack-review", True)
    assert enabled is not None
    assert enabled["enabled"] is True


@pytest.mark.asyncio
async def test_delete(dao: AgentTeamDAO) -> None:
    await _create_sample(dao)
    assert await dao.delete("fullstack-review") is True
    assert await dao.get_by_name("fullstack-review") is None


@pytest.mark.asyncio
async def test_delete_nonexistent_returns_false(dao: AgentTeamDAO) -> None:
    assert await dao.delete("ghost") is False


@pytest.mark.asyncio
async def test_invalid_orchestration_mode_raises(dao: AgentTeamDAO) -> None:
    with pytest.raises(ValueError, match="orchestration_mode"):
        await dao.create(
            name="bad-mode",
            orchestration_mode="invalid",
        )


@pytest.mark.asyncio
async def test_agents_json_serialization(dao: AgentTeamDAO) -> None:
    """Agents list should survive a JSON round-trip."""
    created = await _create_sample(dao, agents=["a", "b", "c"])
    assert created["agents"] == ["a", "b", "c"]
    fetched = await dao.get_by_name("fullstack-review")
    assert fetched["agents"] == ["a", "b", "c"]


@pytest.mark.asyncio
async def test_empty_agents_list(dao: AgentTeamDAO) -> None:
    created = await _create_sample(dao, agents=[])
    assert created["agents"] == []
