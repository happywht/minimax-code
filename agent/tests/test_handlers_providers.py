"""Regression tests for provider credential lifecycle handlers."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from minimax_code import app, secrets
from minimax_code.ipc.client import IPCClient


class FakeProviderDAO:
    def __init__(self) -> None:
        self.provider = {
            "id": "custom-openai",
            "name": "Custom OpenAI",
            "protocol": "openai",
            "base_url": "https://example.test/v1",
            "models": [],
            "enabled": True,
        }
        self.updates: list[tuple[str, dict[str, object]]] = []

    async def get(self, provider_id: str):
        return self.provider.copy() if provider_id == self.provider["id"] else None

    async def update(self, provider_id: str, **kwargs):
        self.updates.append((provider_id, kwargs))
        self.provider.update(kwargs)
        return self.provider.copy()


def make_client(dao: FakeProviderDAO) -> IPCClient:
    client = IPCClient()
    client.server._provider_dao = dao
    client.server._provider_dao_lock = asyncio.Lock()
    return client


@pytest.mark.asyncio
async def test_set_provider_key_rebuilds_active_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dao = FakeProviderDAO()
    client = make_client(dao)
    set_key = MagicMock()
    rebuild = AsyncMock()
    monkeypatch.setattr(secrets, "set_provider_key", set_key)
    monkeypatch.setattr(app, "rebuild_subagent_llm", rebuild)

    result = await client.request(
        "provider.set_api_key",
        {"provider_id": "custom-openai", "api_key": "sk-custom"},
    )

    set_key.assert_called_once_with("custom-openai", "sk-custom")
    rebuild.assert_awaited_once_with()
    assert dao.updates == [("custom-openai", {"api_key_set": 1})]
    assert result["api_key_configured"] is True


@pytest.mark.asyncio
async def test_clear_provider_key_rebuilds_active_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dao = FakeProviderDAO()
    client = make_client(dao)
    clear_key = MagicMock()
    rebuild = AsyncMock()
    monkeypatch.setattr(secrets, "clear_provider_key", clear_key)
    monkeypatch.setattr(app, "rebuild_subagent_llm", rebuild)

    result = await client.request(
        "provider.clear_api_key",
        {"provider_id": "custom-openai"},
    )

    clear_key.assert_called_once_with("custom-openai")
    rebuild.assert_awaited_once_with()
    assert dao.updates == [("custom-openai", {"api_key_set": 0})]
    assert result["api_key_configured"] is False
