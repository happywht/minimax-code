"""Tests for TeamOrchestrator — parallel/sequential/round-robin + conflict detection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from minimax_code.orchestrator.team_orchestrator import (
    AgentRunResult,
    TeamOrchestrator,
)
from minimax_code.storage.dao.agent_teams import AgentTeamDAO
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
async def team_dao(async_db: AsyncDatabase) -> AgentTeamDAO:
    return AgentTeamDAO(async_db)


@pytest.fixture
async def agent_dao(async_db: AsyncDatabase) -> AgentDAO:
    return AgentDAO(async_db)


@pytest.fixture
def emitted_events() -> list[dict[str, Any]]:
    return []


@pytest.fixture
def make_orchestrator(
    team_dao: AgentTeamDAO,
    agent_dao: AgentDAO,
    emitted_events: list[dict[str, Any]],
) -> TeamOrchestrator:
    """Factory fixture — build orchestrator with captured event emitter."""

    async def _emit(name: str, payload: Any) -> None:
        emitted_events.append({"event": name, "payload": payload})

    def _build() -> TeamOrchestrator:
        return TeamOrchestrator(
            team_dao=team_dao,
            agent_dao=agent_dao,
            emit_event=_emit,
        )

    return _build  # type: ignore[return-value]


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _seed_team_and_agents(
    team_dao: AgentTeamDAO,
    agent_dao: AgentDAO,
    mode: str = "parallel",
) -> None:
    """Create a team 'review' with 2 member agents."""
    await agent_dao.upsert(
        name="coder",
        system_prompt="Write code.",
        tool_allowlist=["read_file", "write_file"],
        model=None,
    )
    await agent_dao.upsert(
        name="reviewer",
        system_prompt="Review code.",
        tool_allowlist=["read_file"],
        model=None,
    )
    await team_dao.create(
        name="review",
        agents=["coder", "reviewer"],
        orchestration_mode=mode,
    )


# ── Tests: parallel mode ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parallel_returns_both_results(
    make_orchestrator: TeamOrchestrator,
    team_dao: AgentTeamDAO,
    agent_dao: AgentDAO,
) -> None:
    await _seed_team_and_agents(team_dao, agent_dao, mode="parallel")
    orch = make_orchestrator()
    result = await orch.run("review", "Write a hello world function")

    assert result.success is True
    assert result.orchestration_mode == "parallel"
    assert len(result.agents_run) == 2
    assert result.merged_text  # contains output from both agents
    names = {r.agent_name for r in result.agents_run}
    assert names == {"coder", "reviewer"}


@pytest.mark.asyncio
async def test_parallel_emits_progress_events(
    make_orchestrator: TeamOrchestrator,
    team_dao: AgentTeamDAO,
    agent_dao: AgentDAO,
    emitted_events: list[dict[str, Any]],
) -> None:
    await _seed_team_and_agents(team_dao, agent_dao)
    orch = make_orchestrator()
    await orch.run("review", "test request")

    statuses = [e["payload"]["status"] for e in emitted_events]
    assert "started" in statuses
    assert "completed" in statuses


# ── Tests: sequential mode ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sequential_returns_both_results(
    make_orchestrator: TeamOrchestrator,
    team_dao: AgentTeamDAO,
    agent_dao: AgentDAO,
) -> None:
    await _seed_team_and_agents(team_dao, agent_dao, mode="sequential")
    orch = make_orchestrator()
    result = await orch.run("review", "Write and review a function")

    assert result.success is True
    assert result.orchestration_mode == "sequential"
    assert len(result.agents_run) == 2


# ── Tests: round-robin mode ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_round_robin_returns_results(
    make_orchestrator: TeamOrchestrator,
    team_dao: AgentTeamDAO,
    agent_dao: AgentDAO,
) -> None:
    await _seed_team_and_agents(team_dao, agent_dao, mode="round-robin")
    orch = make_orchestrator()
    result = await orch.run("review", "Distribute tasks")

    assert result.success is True
    assert result.orchestration_mode == "round-robin"
    assert len(result.agents_run) == 2


# ── Tests: error cases ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unknown_team_returns_error(
    make_orchestrator: TeamOrchestrator,
) -> None:
    orch = make_orchestrator()
    result = await orch.run("ghost", "request")
    assert result.success is False
    assert "unknown team" in result.merged_text.lower()


@pytest.mark.asyncio
async def test_disabled_team_returns_error(
    make_orchestrator: TeamOrchestrator,
    team_dao: AgentTeamDAO,
    agent_dao: AgentDAO,
) -> None:
    await _seed_team_and_agents(team_dao, agent_dao)
    await team_dao.set_enabled("review", False)
    orch = make_orchestrator()
    result = await orch.run("review", "request")
    assert result.success is False
    assert "disabled" in result.merged_text.lower()


@pytest.mark.asyncio
async def test_no_agents_in_team(
    make_orchestrator: TeamOrchestrator,
    team_dao: AgentTeamDAO,
) -> None:
    await team_dao.create(name="empty", agents=[])
    orch = make_orchestrator()
    result = await orch.run("empty", "request")
    assert result.success is True  # not an error, just empty
    assert "no agents" in result.merged_text.lower()


@pytest.mark.asyncio
async def test_no_dao_returns_error() -> None:
    orch = TeamOrchestrator()  # no DAOs
    result = await orch.run("any", "request")
    assert result.success is False
    assert "dao not available" in result.merged_text.lower()


# ── Tests: conflict detection ────────────────────────────────────────────────


def test_detect_conflicts_concurrent_write() -> None:
    results = [
        AgentRunResult(
            agent_name="coder",
            success=True,
            tool_calls=[
                {"name": "write_file", "args": {"path": "src/main.py"}},
            ],
        ),
        AgentRunResult(
            agent_name="reviewer",
            success=True,
            tool_calls=[
                {"name": "write_file", "args": {"path": "src/main.py"}},
            ],
        ),
    ]
    conflicts = TeamOrchestrator._detect_conflicts(results)
    assert len(conflicts) == 1
    assert conflicts[0].file_path == "src/main.py"
    assert set(conflicts[0].agents) == {"coder", "reviewer"}


def test_detect_conflicts_no_overlap() -> None:
    results = [
        AgentRunResult(
            agent_name="coder",
            success=True,
            tool_calls=[
                {"name": "write_file", "args": {"path": "src/a.py"}},
            ],
        ),
        AgentRunResult(
            agent_name="reviewer",
            success=True,
            tool_calls=[
                {"name": "write_file", "args": {"path": "src/b.py"}},
            ],
        ),
    ]
    conflicts = TeamOrchestrator._detect_conflicts(results)
    assert len(conflicts) == 0


def test_detect_conflicts_edit_and_write() -> None:
    results = [
        AgentRunResult(
            agent_name="coder",
            success=True,
            tool_calls=[
                {"name": "edit_file", "args": {"file_path": "README.md"}},
            ],
        ),
        AgentRunResult(
            agent_name="fixer",
            success=True,
            tool_calls=[
                {"name": "write_file", "args": {"path": "README.md"}},
            ],
        ),
    ]
    conflicts = TeamOrchestrator._detect_conflicts(results)
    assert len(conflicts) == 1


def test_detect_conflicts_read_only_no_conflict() -> None:
    results = [
        AgentRunResult(
            agent_name="coder",
            success=True,
            tool_calls=[
                {"name": "read_file", "args": {"path": "src/main.py"}},
            ],
        ),
        AgentRunResult(
            agent_name="reviewer",
            success=True,
            tool_calls=[
                {"name": "read_file", "args": {"path": "src/main.py"}},
            ],
        ),
    ]
    conflicts = TeamOrchestrator._detect_conflicts(results)
    assert len(conflicts) == 0
