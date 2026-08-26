"""v1.5.2 MINIMAX_CODE_SANDBOX_DEFAULT — process-wide sandbox default.

Round-4 field report asked for sandbox-default-on; the call stays
opt-in (a silent default-on turns the single-agent fast path into a
trap: uncollected outputs and unpruned sandboxes), but operators running
parallel-write workloads can flip one env instead of teaching every
caller ``sandbox=true``. Precedence: explicit arg > env > false.

Pinned at both levels: the helper's truthy spellings, and a real
``spawn_subagent`` dispatch proving the omission (``sandbox`` absent)
inherits the env.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from minimax_code.agent.tools import get_default_registry
from minimax_code.agent.tools.subagents import _sandbox_default
from minimax_code.orchestrator.subagent import SubAgentRuntime, set_subagent_runtime
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.workspace_ctx import reset_current_root, set_current_root


@pytest.fixture
def workspace_root(tmp_path: Path):
    root = tmp_path / "proj_root"
    root.mkdir()
    token = set_current_root(root)
    try:
        yield root
    finally:
        reset_current_root(token)


@pytest.fixture
def stub_runtime():
    prev = set_subagent_runtime(SubAgentRuntime())
    try:
        yield SubAgentRuntime()
    finally:
        set_subagent_runtime(prev)


@pytest.fixture
async def app_db(tmp_path: Path):
    from minimax_code import app
    from minimax_code.storage.dao.sessions import SessionsDAO

    db = AsyncDatabase(make_temp_database_path(tmp_path))
    await db.connect()
    await db.migrate()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(app, "_DB_SINGLETON", db)
    app.set_sessions_dao(SessionsDAO(db))
    try:
        yield db
    finally:
        monkey.undo()
        app.set_sessions_dao(None)
        await db.close()


async def _seed_agent(db: AsyncDatabase) -> str:
    dao = AgentDAO(db)
    name = f"sbe_{uuid.uuid4().hex[:8]}"
    await dao.upsert(name=name, system_prompt="you are a test agent")
    return name


# ---------------------------------------------------------------------------
# Helper truth table
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", False),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("1", True),
        ("true", True),
        ("YES", True),
        (" On ", True),
    ],
)
def test_sandbox_default_truthy_spellings(
    monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("MINIMAX_CODE_SANDBOX_DEFAULT", raw)
    assert _sandbox_default() is expected


def test_sandbox_default_unset_is_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MINIMAX_CODE_SANDBOX_DEFAULT", raising=False)
    assert _sandbox_default() is False


# ---------------------------------------------------------------------------
# Behavioral: an omitted arg inherits the env through a real dispatch
# ---------------------------------------------------------------------------


async def test_spawn_omitting_sandbox_inherits_env_on(
    workspace_root: Path, stub_runtime: SubAgentRuntime, app_db: AsyncDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIMAX_CODE_SANDBOX_DEFAULT", "true")
    name = await _seed_agent(app_db)

    res = await get_default_registry().dispatch(
        "spawn_subagent", {"agent_name": name, "prompt": "say hi", "wait": True}
    )
    assert res.success, res.error
    run_id = res.output["run_id"]
    assert res.output["sandbox"] is True
    # Fail-closed directory creation really happened.
    assert (workspace_root / ".minimax" / "sandboxes" / run_id).is_dir()


async def test_spawn_explicit_false_beats_env_on(
    workspace_root: Path, stub_runtime: SubAgentRuntime, app_db: AsyncDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MINIMAX_CODE_SANDBOX_DEFAULT", "true")
    name = await _seed_agent(app_db)

    res = await get_default_registry().dispatch(
        "spawn_subagent",
        {"agent_name": name, "prompt": "say hi", "wait": True, "sandbox": False},
    )
    assert res.success, res.error
    assert res.output["sandbox"] is False
    assert not (workspace_root / ".minimax" / "sandboxes").exists()


async def test_spawn_explicit_true_survives_env_off(
    workspace_root: Path, stub_runtime: SubAgentRuntime, app_db: AsyncDatabase,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    name = await _seed_agent(app_db)

    res = await get_default_registry().dispatch(
        "spawn_subagent",
        {"agent_name": name, "prompt": "say hi", "wait": True, "sandbox": True},
    )
    assert res.success, res.error
    assert res.output["sandbox"] is True
