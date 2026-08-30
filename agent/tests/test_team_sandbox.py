"""v1.7.0 (R2) — team-path per-member write sandboxing.

The team orchestrator reuses the exact ``spawn_subagent`` sandbox
machinery: deterministic per-member run ids, ContextVar activation with
fail-closed setup, and an orchestrator-driven auto-collect (skip policy)
— between sequential members, before the reviewer consolidates, and once
for every remaining run at the end.

Driving strategy: members run on the stub path (``llm=None``), so the
tests pre-seed sandbox mirror files from an ``agent.team_progress`` hook
— the collect walk is filesystem-based, and the run id is deterministic,
so a pre-seeded tree merges exactly like a member's real writes would.

These tests pin:

* run-id derivation (determinism, reviewer index, name sanitisation)
* default-off leaves no trace; sandbox=True merges pre-seeded writes back
* skip-on-conflict keeps the workspace version + surfaces an advisory
* sequential collects between members; review collects before the reviewer
* ContextVars reset after the run (sequential runs on the caller's task)
* handler param normalisation (explicit > env > false) + reply shape
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.tools.sandbox import SANDBOX_RELPATH
from minimax_code.config import Config
from minimax_code.ipc.handlers_teams import register_team_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.orchestrator.team_orchestrator import (
    TeamOrchestrator,
    _sandbox_run_id,
)
from minimax_code.storage.dao.agent_teams import AgentTeamDAO
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path
from minimax_code.workspace_ctx import (
    current_run_id,
    current_sandbox,
    reset_current_root,
    set_current_root,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def ws_root(tmp_path: Path):
    """Point ``current_root`` at a temp workspace for the whole test."""
    token = set_current_root(tmp_path)
    yield tmp_path
    reset_current_root(token)


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return make_temp_database_path(tmp_path)


@pytest.fixture
async def async_db(db_path: Path):
    db = AsyncDatabase(db_path)
    await db.connect()
    await db.migrate()
    try:
        yield db
    finally:
        await db.close()


@pytest.fixture
def team_dao(async_db: AsyncDatabase) -> AgentTeamDAO:
    return AgentTeamDAO(async_db)


@pytest.fixture
def agent_dao(async_db: AsyncDatabase) -> AgentDAO:
    return AgentDAO(async_db)


async def _seed_team(
    team_dao: AgentTeamDAO,
    agent_dao: AgentDAO,
    mode: str = "parallel",
    *,
    members: tuple[str, ...] = ("writer-a", "writer-b"),
    orchestration_config: dict[str, Any] | None = None,
) -> None:
    for name in members:
        await agent_dao.upsert(
            name=name,
            system_prompt=f"{name} writes files.",
            tool_allowlist=["read_file", "write_file"],
            model=None,
        )
    await team_dao.create(
        name="crew",
        agents=list(members),
        orchestration_mode=mode,
        orchestration_config=orchestration_config,
    )


def _sb_dir(root: Path, run_id: str) -> Path:
    return root / SANDBOX_RELPATH / run_id


def _seed_mirror(root: Path, run_id: str, rel: str, content: str) -> None:
    """Pre-seed a sandbox mirror file (as if the member wrote it)."""
    target = _sb_dir(root, run_id) / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _receipt(root: Path, run_id: str) -> Path:
    return root / SANDBOX_RELPATH / ".collected" / f"{run_id}.json"


# ---------------------------------------------------------------------------
# run-id derivation
# ---------------------------------------------------------------------------


def test_run_id_deterministic_and_sanitized() -> None:
    a = _sandbox_run_id("teamrun_abc123", 0, "writer-a")
    b = _sandbox_run_id("teamrun_abc123", 0, "writer-a")
    assert a == b == "team_teamrun_abc123_00_writer-a"
    # Review re-runs a config at index len(configs) — distinct tree.
    assert _sandbox_run_id("teamrun_abc123", 2, "writer-a") != a
    # Path-hostile agent names flatten to path-safe characters
    # (leading/trailing separators stripped).
    ugly = _sandbox_run_id("teamrun_x", 1, "../we ird/name!!")
    assert ugly == "team_teamrun_x_01_.._we_ird_name"
    assert "/" not in ugly


# ---------------------------------------------------------------------------
# default off / sandboxed parallel collect
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_default_off_leaves_no_sandbox_trace(
    ws_root: Path, team_dao: AgentTeamDAO, agent_dao: AgentDAO,
) -> None:
    await _seed_team(team_dao, agent_dao, mode="parallel")
    orch = TeamOrchestrator(team_dao=team_dao, agent_dao=agent_dao, llm=None)
    result = await orch.run("crew", "do work")

    assert result.success is True
    assert result.sandbox_summary == {}
    assert all(r.run_id is None for r in result.agents_run)
    assert not (ws_root / SANDBOX_RELPATH).exists()


@pytest.mark.asyncio
async def test_sandbox_true_collects_preseeded_writes(
    ws_root: Path, team_dao: AgentTeamDAO, agent_dao: AgentDAO,
) -> None:
    await _seed_team(team_dao, agent_dao, mode="parallel")
    seeded: set[str] = set()

    async def _seed_on_start(event_name: str, payload: Any) -> None:
        if payload.get("status") != "agent_started":
            return
        idx = payload["agents_completed"]
        rel = f"out/{'a' if idx == 0 else 'b'}.txt"
        run_id = _sandbox_run_id(payload["task_id"], idx, payload["agent_name"])
        if run_id not in seeded:
            _seed_mirror(ws_root, run_id, rel, f"from {run_id}")
            seeded.add(run_id)

    orch = TeamOrchestrator(
        team_dao=team_dao, agent_dao=agent_dao, llm=None, emit_event=_seed_on_start,
    )
    result = await orch.run("crew", "do work", sandbox=True)

    assert result.success is True
    assert len(seeded) == 2
    # Both members' writes merged back into the workspace...
    assert (ws_root / "out/a.txt").read_text(encoding="utf-8").startswith("from ")
    assert (ws_root / "out/b.txt").read_text(encoding="utf-8").startswith("from ")
    # ...with receipts kept and trees pruned, all surfaced in the summary.
    summary = result.sandbox_summary
    assert summary["collected"] == 2
    assert summary["merged_files"] == 2
    assert summary["errors"] == 0
    assert summary["skipped_runs"] == []
    for r in result.agents_run:
        assert r.run_id is not None
        assert _receipt(ws_root, r.run_id).exists()
        assert not _sb_dir(ws_root, r.run_id).exists()  # pruned


# ---------------------------------------------------------------------------
# skip-on-conflict
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_collect_skip_conflict_keeps_workspace_and_advises(
    ws_root: Path, team_dao: AgentTeamDAO, agent_dao: AgentDAO,
) -> None:
    await _seed_team(team_dao, agent_dao, mode="parallel",
                     members=("solo",))
    # The workspace already owns a divergent version of the mirrored
    # file (no _base snapshot → "created independently" conflict → skip).
    (ws_root / "conflict.txt").write_text("workspace version", encoding="utf-8")

    async def _seed_on_start(event_name: str, payload: Any) -> None:
        if payload.get("status") != "agent_started":
            return
        run_id = _sandbox_run_id(
            payload["task_id"], payload["agents_completed"], payload["agent_name"],
        )
        _seed_mirror(ws_root, run_id, "conflict.txt", "sandbox version")

    orch = TeamOrchestrator(
        team_dao=team_dao, agent_dao=agent_dao, llm=None, emit_event=_seed_on_start,
    )
    result = await orch.run("crew", "do work", sandbox=True)

    # Skip keeps the workspace version and refuses to prune.
    assert (ws_root / "conflict.txt").read_text(encoding="utf-8") == "workspace version"
    summary = result.sandbox_summary
    assert summary["skipped_runs"] and summary["skipped_files"]
    assert "sandbox collect" in result.merged_text
    # The advisory is not a failure — the run itself still succeeded.
    assert result.success is True


# ---------------------------------------------------------------------------
# sequential: collect between members
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequential_collects_between_members(
    ws_root: Path, team_dao: AgentTeamDAO, agent_dao: AgentDAO,
) -> None:
    await _seed_team(team_dao, agent_dao, mode="sequential")
    checks: dict[str, Any] = {}

    async def _probe(event_name: str, payload: Any) -> None:
        idx = payload.get("agents_completed")
        if payload.get("status") != "agent_started":
            return
        if idx == 0:
            run_id = _sandbox_run_id(payload["task_id"], 0, "writer-a")
            _seed_mirror(ws_root, run_id, "out/a.txt", "from writer-a")
        if idx == 1:
            # Member 2 just started: member 1's collect must already be
            # done — receipt on disk, file visible in the workspace.
            run_id = _sandbox_run_id(payload["task_id"], 0, "writer-a")
            checks["receipt_before_second"] = _receipt(ws_root, run_id).exists()
            checks["file_before_second"] = (
                ws_root / "out/a.txt"
            ).read_text(encoding="utf-8") == "from writer-a"

    orch = TeamOrchestrator(
        team_dao=team_dao, agent_dao=agent_dao, llm=None, emit_event=_probe,
    )
    result = await orch.run("crew", "do work", sandbox=True)

    assert checks == {"receipt_before_second": True, "file_before_second": True}
    # writer-a merged its seeded file; writer-b collected empty (a
    # zero-write collect still counts — receipt written, tree pruned).
    assert result.sandbox_summary["collected"] == 2
    assert result.sandbox_summary["merged_files"] == 1


# ---------------------------------------------------------------------------
# review: collect writers before the reviewer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_collects_writers_before_reviewer(
    ws_root: Path, team_dao: AgentTeamDAO, agent_dao: AgentDAO,
) -> None:
    await _seed_team(team_dao, agent_dao, mode="review",
                     orchestration_config={"review_agent": "writer-b"})
    checks: dict[str, Any] = {}

    async def _probe(event_name: str, payload: Any) -> None:
        idx = payload.get("agents_completed")
        if payload.get("status") != "agent_started":
            return
        if idx == 0:
            run_id = _sandbox_run_id(payload["task_id"], 0, "writer-a")
            _seed_mirror(ws_root, run_id, "review.txt", "writer-a draft")
        if idx == 2:
            # Reviewer starting (index len(configs)): writers' sandbox
            # writes must already live in the workspace.
            checks["writer_merged_before_review"] = (
                ws_root / "review.txt"
            ).read_text(encoding="utf-8") == "writer-a draft"

    orch = TeamOrchestrator(
        team_dao=team_dao, agent_dao=agent_dao, llm=None, emit_event=_probe,
    )
    result = await orch.run("crew", "do work", sandbox=True)

    assert checks == {"writer_merged_before_review": True}
    # Writers collected mid-run + the reviewer swept at the end
    # (empty collects still count).
    assert result.sandbox_summary["collected"] == 3


# ---------------------------------------------------------------------------
# ContextVar hygiene
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_contextvars_reset_after_sandboxed_run(
    ws_root: Path, team_dao: AgentTeamDAO, agent_dao: AgentDAO,
) -> None:
    # Sequential runs members on the caller's own task, so a forgotten
    # reset would leak straight into this test's context.
    await _seed_team(team_dao, agent_dao, mode="sequential")
    orch = TeamOrchestrator(team_dao=team_dao, agent_dao=agent_dao, llm=None)
    await orch.run("crew", "do work", sandbox=True)

    assert current_sandbox() is None
    assert current_run_id() is None


# ---------------------------------------------------------------------------
# handler layer: param normalisation + reply shape
# ---------------------------------------------------------------------------


class _CapturedReply:
    def __init__(self) -> None:
        self.reply_value: Any = None
        self.error_value: Any = None

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(
        self, code: int, message: str, data: Any = None,
    ) -> None:
        self.error_value = {"code": code, "message": message, "data": data}

    async def emit(self, *args: Any, **kwargs: Any) -> None:  # pragma: no cover
        pass


class _StubTeamDAO:
    """Minimal team DAO — enough for ``team.spawn`` to resolve a team."""

    async def get_by_name(self, name: str) -> dict[str, Any]:
        return {
            "name": name,
            "enabled": True,
            "agents": ["writer-a"],
            "orchestration_mode": "parallel",
        }


def _fake_result(**extra: Any) -> Any:
    from types import SimpleNamespace

    base = dict(
        team_name="crew",
        orchestration_mode="parallel",
        merged_text="done",
        agents_run=[
            SimpleNamespace(
                agent_name="writer-a", success=True, text="t", error="",
                iterations=1, tool_calls=[], stub=True,
                run_id="team_teamrun_x_00_writer-a",
            ),
        ],
        conflicts=[],
        task_id="teamrun_x",
        success=True,
        sandbox_summary={},
    )
    base.update(extra)
    return SimpleNamespace(**base)


@pytest.fixture
def spawn_handlers(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    server = IPCServer(Config())
    register_team_handlers(server, dao=_StubTeamDAO())  # type: ignore[arg-type]
    captured: dict[str, Any] = {}

    class _FakeOrch:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def run(self, *args: Any, **kwargs: Any) -> Any:
            captured["args"] = args
            captured["kwargs"] = kwargs
            return captured.get("result") or _fake_result()

    monkeypatch.setattr(
        "minimax_code.orchestrator.team_orchestrator.TeamOrchestrator", _FakeOrch,
    )
    monkeypatch.setattr("minimax_code.app.get_subagent_llm", lambda: None)
    handlers = server._handlers
    handlers["_captured"] = captured  # type: ignore[assignment]
    return handlers


@pytest.mark.asyncio
async def test_handler_env_default_and_explicit_override(
    spawn_handlers: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = spawn_handlers["_captured"]
    monkeypatch.setenv("MINIMAX_CODE_SANDBOX_DEFAULT", "true")

    # No param → env default wins.
    ctx = _CapturedReply()
    await spawn_handlers["team.spawn"](
        {"team_name": "crew", "request": "r"}, ctx,
    )
    assert ctx.error_value is None
    assert captured["kwargs"]["sandbox"] is True

    # Explicit param beats the env default.
    ctx = _CapturedReply()
    await spawn_handlers["team.spawn"](
        {"team_name": "crew", "request": "r", "sandbox": False}, ctx,
    )
    assert ctx.error_value is None
    assert captured["kwargs"]["sandbox"] is False


@pytest.mark.asyncio
async def test_handler_reply_carries_sandbox_fields(
    spawn_handlers: dict[str, Any],
) -> None:
    captured = spawn_handlers["_captured"]
    captured["result"] = _fake_result(
        sandbox_summary={
            "collected": 1, "merged_files": 2, "conflicts": [],
            "errors": 0, "skipped_runs": [], "skipped_files": [],
        },
    )

    ctx = _CapturedReply()
    await spawn_handlers["team.spawn"](
        {"team_name": "crew", "request": "r", "sandbox": True}, ctx,
    )

    assert ctx.error_value is None
    reply = ctx.reply_value
    assert reply["sandbox"] is True
    assert reply["sandbox_summary"]["collected"] == 1
    assert reply["agents_run"][0]["run_id"] == "team_teamrun_x_00_writer-a"


@pytest.mark.asyncio
async def test_handler_unsandboxed_reply_has_no_summary(
    spawn_handlers: dict[str, Any],
) -> None:
    ctx = _CapturedReply()
    await spawn_handlers["team.spawn"](
        {"team_name": "crew", "request": "r"}, ctx,
    )

    assert ctx.error_value is None
    reply = ctx.reply_value
    assert reply["sandbox"] is False
    assert "sandbox_summary" not in reply
    # agent without a sandbox run carries no run_id key at all
    assert "run_id" in reply["agents_run"][0]  # fake always sets one; shape check
    # (real unsandboxed runs omit the key — pinned at the orchestrator
    # layer by test_default_off_leaves_no_sandbox_trace)
    assert json.dumps(reply)  # fully serialisable
