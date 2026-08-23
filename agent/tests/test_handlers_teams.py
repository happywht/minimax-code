"""Tests for team.* IPC handlers — stub DAO + real DB integration."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from minimax_code.config import Config
from minimax_code.ipc.handlers_agents import register_agent_handlers
from minimax_code.ipc.handlers_teams import register_team_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.storage.dao.agent_teams import AgentTeamDAO
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path

# ── Fakes ─────────────────────────────────────────────────────────────────────


class _CapturedReply:
    """Minimal Context substitute that captures reply / reply_error / emit.

    ``emit`` mirrors the real ``Context.emit`` signature (event, data,
    keyword-only metadata) so handlers can pass it straight through as an
    ``emit_event`` callback — captured tuples stay available for assertions.
    """

    def __init__(self) -> None:
        self.reply_value: dict[str, Any] | None = None
        self.error_value: dict[str, Any] | None = None
        self.emitted: list[tuple[str, Any]] = []

    async def reply(self, value: Any) -> None:
        self.reply_value = value

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.error_value = {"code": code, "message": message, "data": data}

    async def emit(
        self,
        event: str,
        data: Any = None,
        *,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        payload = {**data, "metadata": metadata} if metadata and isinstance(data, dict) else data
        self.emitted.append((event, payload))


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
            "orchestration_config": kw.get("orchestration_config"),
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
def real_handlers(async_db: AsyncDatabase) -> dict[str, Any]:
    """Handlers wired to real DAOs on a temp database."""
    server = IPCServer(Config())
    register_agent_handlers(server, dao=AgentDAO(async_db))
    register_team_handlers(
        server,
        dao=AgentTeamDAO(async_db),
        agent_dao=AgentDAO(async_db),
        runs_dao=AgentRunsDAO(async_db),
    )
    return server._handlers


# ── Tests: stub DAO ───────────────────────────────────────────────────────────


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
async def test_team_create_with_orchestration_config(
    handlers: dict[str, Any],
) -> None:
    ctx = _CapturedReply()
    await handlers["team.create"](
        {
            "name": "config-team",
            "orchestration_mode": "review",
            "orchestration_config": {"review_agent": "coder"},
        },
        ctx,
    )
    assert ctx.error_value is None, ctx.error_value
    team = ctx.reply_value["team"]
    assert team["orchestration_mode"] == "review"
    assert team["orchestration_config"] == {"review_agent": "coder"}


@pytest.mark.asyncio
async def test_team_create_rejects_bad_orchestration_config(
    handlers: dict[str, Any],
) -> None:
    ctx = _CapturedReply()
    await handlers["team.create"](
        {
            "name": "bad-config",
            "orchestration_config": "not-an-object",
        },
        ctx,
    )
    assert ctx.error_value is not None
    assert "orchestration_config must be a JSON object" in ctx.error_value["message"]


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


# ── Tests: real DB integration ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_team_spawn_and_run_get(real_handlers: dict[str, Any]) -> None:
    # Seed agents and a review-mode team.
    await real_handlers["agent.create"](
        {
            "name": "coder",
            "system_prompt": "Write code.",
            "tool_allowlist": ["read_file", "write_file"],
        },
        _CapturedReply(),
    )
    await real_handlers["agent.create"](
        {
            "name": "reviewer",
            "system_prompt": "Review code.",
            "tool_allowlist": ["read_file"],
        },
        _CapturedReply(),
    )
    ctx_create = _CapturedReply()
    await real_handlers["team.create"](
        {
            "name": "review",
            "agents": ["coder", "reviewer"],
            "orchestration_mode": "review",
        },
        ctx_create,
    )
    assert ctx_create.reply_value is not None

    ctx_spawn = _CapturedReply()
    await real_handlers["team.spawn"](
        {"team_name": "review", "request": "Write a hello world function"},
        ctx_spawn,
    )
    assert ctx_spawn.error_value is None, ctx_spawn.error_value
    assert ctx_spawn.reply_value is not None
    task_id = ctx_spawn.reply_value["task_id"]
    assert ctx_spawn.reply_value["orchestration_mode"] == "review"
    assert ctx_spawn.reply_value["success"] is True

    ctx_get = _CapturedReply()
    await real_handlers["team.run.get"]({"task_id": task_id}, ctx_get)
    assert ctx_get.error_value is None, ctx_get.error_value
    assert ctx_get.reply_value is not None
    run = ctx_get.reply_value["run"]
    result = ctx_get.reply_value["result"]
    assert run["id"] == task_id
    assert run["mode"] == "team"
    assert result["team_name"] == "review"
    assert result["orchestration_mode"] == "review"
    assert result["success"] is True
    assert len(result["agents_run"]) == 3  # 2 writers + 1 reviewer


@pytest.mark.asyncio
async def test_team_run_get_unknown_task(real_handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await real_handlers["team.run.get"]({"task_id": "teamrun_noexist"}, ctx)
    assert ctx.error_value is not None
    assert "unknown team run" in ctx.error_value["message"]


# ── Tests: LLM wiring (v1.2.2 regression) ─────────────────────────────────────


@pytest.mark.asyncio
async def test_team_spawn_injects_llm_singleton(
    real_handlers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v1.2.2 regression: ``team.spawn`` must wire the process-wide
    LLM singleton into :class:`TeamOrchestrator`.

    Before the fix the handler omitted ``llm=`` when constructing the
    orchestrator, so ``TeamOrchestrator._llm`` was permanently ``None``
    and every member SubAgentRuntime returned the canned
    ``stub: agent xxx would handle...`` envelope — team runs in a real
    deployment never produced an actual answer. The SubAgentRuntime
    layer already had real-vs-stub coverage (``test_agents.py``); this
    test pins the *handler-level* wiring, which is the link the old
    tests missed (they all ran with ``_SUBAGENT_LLM = None``).
    """

    class _FakeLLM:
        """Minimal ``stream_chat`` stand-in (mirrors test_agents._FakeLLM)."""

        def __init__(self) -> None:
            self.calls = 0

        async def stream_chat(  # type: ignore[no-untyped-def]
            self, messages, **_kwargs
        ):
            from minimax_code.agent.llm import StreamChunk

            self.calls += 1
            yield StreamChunk(delta=f"[{messages[0]['content'][:12]}] team reply")
            yield StreamChunk(
                finish_reason="stop",
                usage={"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            )

    fake = _FakeLLM()
    monkeypatch.setattr("minimax_code.app._SUBAGENT_LLM", fake)

    await real_handlers["agent.create"](
        {
            "name": "solo",
            "system_prompt": "Do things.",
            "tool_allowlist": ["read_file"],
        },
        _CapturedReply(),
    )
    ctx_create = _CapturedReply()
    await real_handlers["team.create"](
        {"name": "solo-team", "agents": ["solo"], "orchestration_mode": "parallel"},
        ctx_create,
    )
    assert ctx_create.reply_value is not None

    ctx_spawn = _CapturedReply()
    await real_handlers["team.spawn"](
        {"team_name": "solo-team", "request": "say hi"}, ctx_spawn
    )
    assert ctx_spawn.error_value is None, ctx_spawn.error_value
    result = ctx_spawn.reply_value
    assert result is not None
    assert result["success"] is True
    agents_run = result["agents_run"]
    assert len(agents_run) == 1
    assert agents_run[0]["stub"] is False, (
        "team.spawn lost its LLM wiring — the member fell back to the "
        "stub path (handler forgot llm=get_subagent_llm()?)"
    )
    assert not agents_run[0]["text"].startswith("stub:")
    assert fake.calls >= 1, "the member never drove the injected LLM"


@pytest.mark.asyncio
async def test_team_spawn_without_llm_keeps_stub_path(
    real_handlers: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backward-compat: no LLM singleton (no db / boot not run) →
    members keep the deterministic stub envelope instead of erroring."""
    monkeypatch.setattr("minimax_code.app._SUBAGENT_LLM", None)

    await real_handlers["agent.create"](
        {
            "name": "loner",
            "system_prompt": "Do things.",
            "tool_allowlist": ["read_file"],
        },
        _CapturedReply(),
    )
    ctx_create = _CapturedReply()
    await real_handlers["team.create"](
        {"name": "loner-team", "agents": ["loner"], "orchestration_mode": "parallel"},
        ctx_create,
    )
    assert ctx_create.reply_value is not None

    ctx_spawn = _CapturedReply()
    await real_handlers["team.spawn"](
        {"team_name": "loner-team", "request": "say hi"}, ctx_spawn
    )
    assert ctx_spawn.error_value is None, ctx_spawn.error_value
    result = ctx_spawn.reply_value
    assert result is not None
    assert result["success"] is True
    assert result["agents_run"][0]["stub"] is True
