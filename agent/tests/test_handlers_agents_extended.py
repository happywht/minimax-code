"""Tests for v0.8.0 extended fields in agent.create / agent.update handlers."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from minimax_code.config import Config
from minimax_code.ipc.handlers_agents import register_agent_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.storage.dao.agents import AgentDAO
from minimax_code.storage.db import AsyncDatabase, make_temp_database_path

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
def handlers(async_db: AsyncDatabase) -> dict[str, Any]:
    dao = AgentDAO(async_db)
    server = IPCServer(Config())
    register_agent_handlers(server, dao=dao)
    return server._handlers


# ── Tests: agent.create with extended fields ─────────────────────────────────


@pytest.mark.asyncio
async def test_create_with_v08_fields(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["agent.create"](
        {
            "name": "reviewer",
            "system_prompt": "Review code.",
            "description": "Security reviewer",
            "icon": "ShieldAlert",
            "color": "#f43f5e",
            "category": "review",
            "tags": ["security"],
            "skills": ["code-review"],
            "max_iterations": 16,
            "temperature": 0.5,
        },
        ctx,
    )
    assert ctx.error_value is None, ctx.error_value
    agent = ctx.reply_value["agent"]
    assert agent["name"] == "reviewer"
    assert agent["description"] == "Security reviewer"
    assert agent["icon"] == "ShieldAlert"
    assert agent["color"] == "#f43f5e"
    assert agent["category"] == "review"
    assert agent["tags"] == ["security"]
    assert agent["skills"] == ["code-review"]
    assert agent["max_iterations"] == 16
    assert agent["temperature"] == 0.5


@pytest.mark.asyncio
async def test_create_minimal_uses_defaults(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["agent.create"](
        {"name": "basic", "system_prompt": "Do stuff."},
        ctx,
    )
    assert ctx.error_value is None, ctx.error_value
    agent = ctx.reply_value["agent"]
    assert agent["name"] == "basic"
    # Defaults from migration + _hydrate (loads_json on NULL → None)
    assert agent["tags"] is None or agent["tags"] == []
    assert agent["skills"] is None or agent["skills"] == []
    assert agent["enabled"] is True


@pytest.mark.asyncio
async def test_create_rejects_invalid_tags(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["agent.create"](
        {
            "name": "bad-tags",
            "system_prompt": "test",
            "tags": "not-a-list",
        },
        ctx,
    )
    assert ctx.error_value is not None
    assert "tags must be a list" in ctx.error_value["message"]


@pytest.mark.asyncio
async def test_create_rejects_invalid_skills(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["agent.create"](
        {
            "name": "bad-skills",
            "system_prompt": "test",
            "skills": "not-a-list",
        },
        ctx,
    )
    assert ctx.error_value is not None
    assert "skills must be a list" in ctx.error_value["message"]


# ── Tests: agent.update with extended fields ──────────────────────────────────


@pytest.mark.asyncio
async def test_update_extended_fields(handlers: dict[str, Any]) -> None:
    # create first
    ctx_create = _CapturedReply()
    await handlers["agent.create"](
        {"name": "updatable", "system_prompt": "v1"},
        ctx_create,
    )
    assert ctx_create.error_value is None

    # update with v0.8.0 fields
    ctx_update = _CapturedReply()
    await handlers["agent.update"](
        {
            "name": "updatable",
            "description": "Updated desc",
            "icon": "Zap",
            "color": "#eab308",
            "category": "perf",
            "tags": ["performance"],
            "skills": ["perf-check"],
            "max_iterations": 12,
            "temperature": 0.3,
        },
        ctx_update,
    )
    assert ctx_update.error_value is None, ctx_update.error_value
    agent = ctx_update.reply_value["agent"]
    assert agent["description"] == "Updated desc"
    assert agent["icon"] == "Zap"
    assert agent["tags"] == ["performance"]
    assert agent["skills"] == ["perf-check"]
    assert agent["max_iterations"] == 12
    assert agent["temperature"] == 0.3


@pytest.mark.asyncio
async def test_update_preserves_untouched_fields(handlers: dict[str, Any]) -> None:
    # create with extended fields
    ctx_create = _CapturedReply()
    await handlers["agent.create"](
        {
            "name": "preserve-test",
            "system_prompt": "original prompt",
            "description": "original desc",
            "icon": "Bot",
            "tags": ["original"],
        },
        ctx_create,
    )
    assert ctx_create.error_value is None

    # update only one field
    ctx_update = _CapturedReply()
    await handlers["agent.update"](
        {"name": "preserve-test", "icon": "Zap"},
        ctx_update,
    )
    assert ctx_update.error_value is None
    agent = ctx_update.reply_value["agent"]
    # Icon changed
    assert agent["icon"] == "Zap"
    # Others preserved
    assert agent["description"] == "original desc"
    assert agent["tags"] == ["original"]
    assert agent["system_prompt"] == "original prompt"


@pytest.mark.asyncio
async def test_update_unknown_agent_returns_error(handlers: dict[str, Any]) -> None:
    ctx = _CapturedReply()
    await handlers["agent.update"](
        {"name": "ghost", "description": "nope"},
        ctx,
    )
    assert ctx.error_value is not None
    assert "unknown" in ctx.error_value["message"]
