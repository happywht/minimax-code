"""Tests for v0.8.0 extended fields in AgentDAO — upsert/update/_hydrate."""

from __future__ import annotations

from pathlib import Path

import pytest

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


# ── Helpers ───────────────────────────────────────────────────────────────────


def _unique() -> str:
    import uuid

    return f"agent_{uuid.uuid4().hex[:8]}"


# ── Tests ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_upsert_with_extended_fields(dao: AgentDAO) -> None:
    """Upsert should accept all v0.8.0 keyword fields and return them hydrated."""
    name = _unique()
    row = await dao.upsert(
        name=name,
        system_prompt="You review code.",
        tool_allowlist=["read_file", "write_file"],
        model="gpt-4",
        description="Security reviewer agent",
        icon="ShieldAlert",
        color="#f43f5e",
        category="review",
        tags=["security", "review"],
        team_id="fullstack-review",
        skills=["code-review", "security-scan"],
        max_iterations=16,
        temperature=0.7,
    )
    assert row["name"] == name
    assert row["description"] == "Security reviewer agent"
    assert row["icon"] == "ShieldAlert"
    assert row["color"] == "#f43f5e"
    assert row["category"] == "review"
    assert row["tags"] == ["security", "review"]
    assert row["team_id"] == "fullstack-review"
    assert row["skills"] == ["code-review", "security-scan"]
    assert row["max_iterations"] == 16
    assert row["temperature"] == 0.7
    assert row["enabled"] is True


@pytest.mark.asyncio
async def test_upsert_minimal_defaults(dao: AgentDAO) -> None:
    """Minimal upsert (name only) should use sensible defaults."""
    name = _unique()
    row = await dao.upsert(
        name=name,
        system_prompt="Minimal agent",
        tool_allowlist=[],
        model=None,
    )
    assert row["tags"] is None or row["tags"] == []  # loads_json on NULL
    assert row["skills"] is None or row["skills"] == []
    assert row["enabled"] is True
    assert row["max_iterations"] == 8  # default
    assert row["temperature"] is None  # default


@pytest.mark.asyncio
async def test_hydrate_enabled_bool(dao: AgentDAO) -> None:
    """_hydrate must convert enabled from int to bool."""
    name = _unique()
    row = await dao.upsert(
        name=name,
        system_prompt="test",
        tool_allowlist=[],
        model=None,
    )
    assert row["enabled"] is True
    assert isinstance(row["enabled"], bool)


@pytest.mark.asyncio
async def test_upsert_updates_extended_fields(dao: AgentDAO) -> None:
    """Second upsert should update v0.8.0 fields on an existing agent."""
    name = _unique()
    await dao.upsert(
        name=name,
        system_prompt="test",
        tool_allowlist=[],
        model=None,
    )
    # Second upsert with extended fields — should UPDATE, not INSERT duplicate
    updated = await dao.upsert(
        name=name,
        system_prompt="test",
        tool_allowlist=[],
        model=None,
        description="Updated description",
        icon="Zap",
        color="#eab308",
        category="perf",
        tags=["performance"],
        skills=["perf-check"],
        max_iterations=12,
        temperature=0.3,
    )
    assert updated is not None
    assert updated["description"] == "Updated description"
    assert updated["icon"] == "Zap"
    assert updated["tags"] == ["performance"]
    assert updated["skills"] == ["perf-check"]
    assert updated["max_iterations"] == 12
    assert updated["temperature"] == 0.3


@pytest.mark.asyncio
async def test_upsert_idempotent(dao: AgentDAO) -> None:
    """Second upsert with same name should update, not duplicate."""
    name = _unique()
    await dao.upsert(
        name=name,
        system_prompt="v1",
        tool_allowlist=["read_file"],
        model="gpt-4",
    )
    row = await dao.upsert(
        name=name,
        system_prompt="v2",
        tool_allowlist=["read_file", "write_file"],
        model="gpt-4-turbo",
        description="v2 desc",
    )
    assert row["system_prompt"] == "v2"
    assert row["tool_allowlist"] == ["read_file", "write_file"]
    assert row["description"] == "v2 desc"

    # Should be only one agent with this name
    all_agents = await dao.list_all()
    names = [a["name"] for a in all_agents]
    assert names.count(name) == 1
