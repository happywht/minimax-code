from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from minimax_code import app
from minimax_code.ipc.protocol import NativeModelSnapshot
from minimax_code.model_snapshot import ModelSnapshot
from minimax_code.orchestrator.subagent import (
    SubAgentConfig,
    SubAgentConfigError,
    SubAgentRuntime,
    get_subagent_runtime,
    subagent_runtime_scope,
)
from minimax_code.storage.dao.model_prefs import ModelPrefsDAO
from minimax_code.storage.dao.providers import ProviderDAO
from minimax_code.storage.db import AsyncDatabase


@pytest.fixture(autouse=True)
def restore_model_globals(monkeypatch):
    monkeypatch.setattr(app, "_SUBAGENT_LLM", None)
    monkeypatch.setattr(app, "_REASONING_EFFORT_OVERRIDE", None)
    monkeypatch.setattr(app, "_MODEL_REBUILD_GENERATION", 0)
    monkeypatch.setattr("minimax_code.orchestrator.subagent._SUBAGENT_RUNTIME", None)


async def test_async_load_does_not_publish_partial_model_effort(monkeypatch, tmp_path):
    db = AsyncDatabase(tmp_path / "model.db")
    await db.connect()
    await db.migrate()
    try:
        old = SimpleNamespace(default_model="old", reasoning_effort_override="low")
        app._set_subagent_llm(old)
        await ModelPrefsDAO(db).set_reasoning_effort("high")
        entered, release = asyncio.Event(), asyncio.Event()
        original = ProviderDAO.get

        async def delayed(self, provider_id):
            entered.set()
            await release.wait()
            return await original(self, provider_id)

        monkeypatch.setattr(ProviderDAO, "get", delayed)
        loading = asyncio.create_task(app._rebuild_subagent_llm(db))
        await asyncio.wait_for(entered.wait(), 2)
        assert app.get_subagent_llm() is old
        assert app.get_reasoning_effort_override() == "low"
        release.set()
        client = await asyncio.wait_for(loading, 2)
        assert app.get_reasoning_effort_override() == "low"
        app._set_subagent_llm(client)
        assert app.get_subagent_llm() is client
        assert app.get_reasoning_effort_override() == "high"
        await client.close()
    finally:
        await db.close()


async def test_stale_async_rebuild_cannot_replace_newer_selection(monkeypatch):
    entered, release = asyncio.Event(), asyncio.Event()
    old = SimpleNamespace(default_model="old", reasoning_effort_override="low", close=AsyncMock())
    new = SimpleNamespace(default_model="new", reasoning_effort_override="high", close=AsyncMock())
    calls = 0

    async def load(db):
        nonlocal calls
        calls += 1
        if calls == 1:
            entered.set()
            await release.wait()
            return old
        return new

    monkeypatch.setattr(app, "get_db", lambda: object())
    monkeypatch.setattr(app, "_rebuild_subagent_llm", load)
    first = asyncio.create_task(app.rebuild_subagent_llm())
    await asyncio.wait_for(entered.wait(), 2)
    await app.rebuild_subagent_llm()
    release.set()
    await asyncio.wait_for(first, 2)
    assert app.get_subagent_llm() is new
    assert app.get_reasoning_effort_override() == "high"
    old.close.assert_awaited_once()
    new.close.assert_not_awaited()


async def test_child_task_inherits_parent_model_after_global_switch():
    parent_client = SimpleNamespace(default_model="parent", api_key="NEVER_SERIALIZE",
                                    base_url="https://secret.invalid", protocol="openai",
                                    provider_id="parent-provider", reasoning_effort_override="high")
    app._set_subagent_llm(parent_client)
    snapshot = ModelSnapshot.capture()
    runtime = SubAgentRuntime(llm=snapshot.client, reasoning_effort=snapshot.reasoning_effort)
    release = asyncio.Event()

    async def child():
        await release.wait()
        handle = get_subagent_runtime().build(SubAgentConfig(name="worker", system_prompt="task"))
        return handle.core

    with subagent_runtime_scope(runtime):
        child_task = asyncio.create_task(child())
    other = SimpleNamespace(default_model="other", reasoning_effort_override="low")
    app._set_subagent_llm(other)
    release.set()
    core = await asyncio.wait_for(child_task, 2)
    assert core.llm is parent_client
    assert core.config.model == "parent"
    assert core.config.reasoning_effort == "high"
    assert get_subagent_runtime()._llm is other
    assert NativeModelSnapshot.model_validate(snapshot.metadata()).model == "parent"
    metadata = json.dumps(snapshot.metadata())
    assert "parent-provider" in metadata and "NEVER_SERIALIZE" not in metadata
    assert "secret.invalid" not in metadata


@pytest.mark.parametrize("unavailable", ["missing", "disabled", "read-error", "empty-endpoint"])
async def test_unavailable_provider_never_falls_back(monkeypatch, tmp_path, unavailable):
    db = AsyncDatabase(tmp_path / "model.db")
    await db.connect()
    await db.migrate()
    try:
        async def get(self, provider_id):
            if unavailable == "read-error":
                raise RuntimeError("private database failure")
            if unavailable == "empty-endpoint":
                return {"enabled": 1, "base_url": "", "protocol": "openai"}
            return None if unavailable == "missing" else {"enabled": 0}

        monkeypatch.setattr(ProviderDAO, "get", get)
        def forbidden():
            pytest.fail("must not read fallback provider credentials")
        monkeypatch.setattr("minimax_code.secrets.get_api_key", forbidden)
        client = await app._rebuild_subagent_llm(db)
        app._set_subagent_llm(client)
        with pytest.raises(RuntimeError, match="provider|configuration"):
            ModelSnapshot.capture()
        with pytest.raises(SubAgentConfigError, match="provider|configuration"):
            get_subagent_runtime().build(SubAgentConfig(name="worker", system_prompt="task"))
        with pytest.raises(RuntimeError, match="provider|configuration"):
            await client.chat([{"role": "user", "content": "test"}])
        await client.close()
    finally:
        await db.close()


async def test_missing_third_party_key_does_not_use_default_provider_secret(monkeypatch, tmp_path):
    db = AsyncDatabase(tmp_path / "model.db")
    await db.connect()
    await db.migrate()
    try:
        provider = await ProviderDAO(db).create(name="custom", protocol="openai",
                                              base_url="https://custom.invalid", models=[{"id": "custom-model"}])
        await ModelPrefsDAO(db).set_current("custom-model", provider["id"])
        monkeypatch.setattr("minimax_code.secrets.get_provider_key", lambda provider_id: None)
        monkeypatch.setattr("minimax_code.secrets.get_api_key",
                            lambda: pytest.fail("must not read another provider's secret"))
        client = await app._rebuild_subagent_llm(db)
        assert client.api_key == "" and client.mock
        assert client.default_model == "custom-model"
        assert client.provider_id == provider["id"]
        await client.close()
    finally:
        await db.close()
