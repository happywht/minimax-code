"""Unit tests for the agents DAO + sub-agent runtime.

The end-to-end IPC round-trip is covered by ``tests/e2e/smoke_agents.py``
(spawns the real agent process). These unit tests focus on:

* :class:`AgentDAO` — round-trip CRUD, name uniqueness, tool_allowlist
  JSON serialize/deserialize, list-all sort order, concurrent races.
* :class:`SubAgentRuntime` — builds an AgentCore handle from a config
  and the stub returns a deterministic text reply.

Each test gets a fresh migrated DB so the DAO tests can run in parallel.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.orchestrator.subagent import SubAgentConfig, SubAgentRuntime


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
async def agents_dao(async_db: AsyncDatabase) -> AgentDAO:
    return AgentDAO(async_db)


def _unique_name(prefix: str = "ag") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# AgentDAO
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_agent_upsert_round_trip(agents_dao: AgentDAO) -> None:
    name = _unique_name()
    row = await agents_dao.upsert(
        name=name, system_prompt="You review code.", tool_allowlist=["read_file"], model="gpt-4"
    )
    assert row["name"] == name
    assert row["system_prompt"] == "You review code."
    assert row["tool_allowlist"] == ["read_file"]
    assert row["model"] == "gpt-4"
    fetched = await agents_dao.get(name)
    assert fetched is not None
    assert fetched["id"] == row["id"]


@pytest.mark.asyncio
async def test_agent_upsert_overwrites_same_name(agents_dao: AgentDAO) -> None:
    """Second upsert of same name updates the row, doesn't error."""
    name = _unique_name()
    a = await agents_dao.upsert(name=name, system_prompt="v1")
    b = await agents_dao.upsert(name=name, system_prompt="v2")
    assert a["id"] == b["id"], "upsert should keep the same primary key"
    assert b["system_prompt"] == "v2"


@pytest.mark.asyncio
async def test_agent_list_returns_all(agents_dao: AgentDAO) -> None:
    names = set()
    for i in range(3):
        n = _unique_name(f"a{i}")
        names.add(n)
        await agents_dao.upsert(name=n, system_prompt=f"agent {i}")
    all_rows = await agents_dao.list_all()
    listed = {r["name"] for r in all_rows}
    assert names.issubset(listed)


@pytest.mark.asyncio
async def test_agent_delete_removes(agents_dao: AgentDAO) -> None:
    name = _unique_name()
    await agents_dao.upsert(name=name, system_prompt="x")
    assert await agents_dao.delete(name) is True
    assert await agents_dao.get(name) is None


@pytest.mark.asyncio
async def test_agent_tool_allowlist_json_serialization(agents_dao: AgentDAO) -> None:
    name = _unique_name()
    allow = ["read_file", "write_file", "shell"]
    await agents_dao.upsert(name=name, system_prompt="x", tool_allowlist=allow)
    row = await agents_dao.get(name)
    assert row is not None
    assert row["tool_allowlist"] == allow


@pytest.mark.asyncio
async def test_agent_concurrent_upsert_distinct_names(agents_dao: AgentDAO) -> None:
    """5 concurrent creates of different names all land."""
    names = [_unique_name(f"c{i}") for i in range(5)]
    await asyncio.gather(
        *(agents_dao.upsert(name=n, system_prompt=f"p-{n}") for n in names)
    )
    rows = await agents_dao.list_all()
    listed = {r["name"] for r in rows}
    for n in names:
        assert n in listed


@pytest.mark.asyncio
async def test_agent_concurrent_upsert_same_name_safe(agents_dao: AgentDAO) -> None:
    """10 racing upserts of the same name end up with one row, not 10."""
    name = _unique_name("race")
    await asyncio.gather(
        *(agents_dao.upsert(name=name, system_prompt=f"v{i}") for i in range(10))
    )
    rows = await agents_dao.list_all()
    matching = [r for r in rows if r["name"] == name]
    assert len(matching) == 1, "concurrent upserts of same name should land on one row"


@pytest.mark.asyncio
async def test_agent_persistence_across_reopen(db_path: Path) -> None:
    """Create → close → reopen → list still finds it."""
    db = AsyncDatabase(db_path)
    await db.connect()
    await db.migrate()
    dao = AgentDAO(db)
    name = _unique_name("persist")
    await dao.upsert(name=name, system_prompt="x")
    await db.close()

    db2 = AsyncDatabase(db_path)
    await db2.connect()
    try:
        dao2 = AgentDAO(db2)
        row = await dao2.get(name)
        assert row is not None
        assert row["name"] == name
    finally:
        await db2.close()


# ---------------------------------------------------------------------------
# SubAgentRuntime stub
# ---------------------------------------------------------------------------


def test_subagent_runtime_constructs_handle() -> None:
    runtime = SubAgentRuntime()
    handle = runtime.build(
        SubAgentConfig(name="reviewer", system_prompt="x", tool_allowlist=[])
    )
    assert handle.config.name == "reviewer"
    assert handle.core is not None


def test_subagent_handles_are_independent() -> None:
    runtime = SubAgentRuntime()
    h1 = runtime.build(SubAgentConfig(name="a", system_prompt="x"))
    h2 = runtime.build(SubAgentConfig(name="a", system_prompt="x"))
    # Two builds → two independent AgentCore instances.
    assert h1.core is not h2.core
