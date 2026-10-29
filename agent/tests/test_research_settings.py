"""Search settings persist independently and retain network guards."""
from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from minimax_code import app
from minimax_code.agent.tools import public_research
from minimax_code.ipc.handlers_runtime import _handle_research_get, _handle_research_set
from minimax_code.research_settings import get_config, resolve_endpoint, set_config


def test_saved_precedence_clear_and_invalid_endpoints(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_CODE_SEARCH_URL", "https://env.example/custom")
    assert get_config(tmp_path)["source"] == "environment"
    assert get_config(tmp_path)["effective_url"] is None
    assert resolve_endpoint(tmp_path) == "https://env.example/custom"
    saved = set_config(tmp_path, "https://search.example/search")
    assert saved["source"] == "settings" and resolve_endpoint(tmp_path) == saved["saved_url"]
    for value in ("http://example.com/search", "https://a:b@example.com/search",
                  "https://example.com/search?api_key=secret", "https://127.0.0.1/search",
                  "https://example.com/secret"):
        with pytest.raises(ValueError):
            set_config(tmp_path, value)
        assert resolve_endpoint(tmp_path) == "https://search.example/search"
    assert set_config(tmp_path, None)["source"] == "environment"
    monkeypatch.setenv("MINIMAX_CODE_SEARCH_URL", "https://example.com/?key=secret")
    assert get_config(tmp_path)["source"] == "environment_invalid"
    with pytest.raises(ValueError):
        resolve_endpoint(tmp_path)


async def test_ipc_and_tool_read_persisted_configuration(tmp_path, monkeypatch):
    db = SimpleNamespace(path=tmp_path / "test.db")
    monkeypatch.setattr(app, "ensure_db", AsyncMock(return_value=db))
    monkeypatch.setattr(app, "get_db", lambda: db)
    monkeypatch.delenv("MINIMAX_CODE_SEARCH_URL", raising=False)
    ctx = SimpleNamespace(reply=AsyncMock(), reply_error=AsyncMock())
    await _handle_research_set({"search_url": "https://search.example/search"}, ctx)
    assert ctx.reply.await_args.args[0]["source"] == "settings"
    ctx.reply.reset_mock()
    await _handle_research_get({}, ctx)
    assert ctx.reply.await_args.args[0]["saved_url"] == "https://search.example/search"
    captured = []
    def fetch(url):
        captured.append(url)
        return url, {"content-type": "application/json"}, json.dumps({"results": []}).encode(), False
    monkeypatch.setattr(public_research, "_fetch", fetch)
    result = await public_research.SearchPublicWebTool().run("topic")
    assert result.success and captured[0].startswith("https://search.example/search?q=topic")
    await _handle_research_set({"search_url": None}, ctx)
    assert (await public_research.SearchPublicWebTool().run("topic")).success is False
    await _handle_research_set({"search_url": "https://search.example/search", "unexpected": 1}, ctx)
    ctx.reply_error.assert_awaited()


def test_corrupt_configuration_is_not_silently_replaced_or_fallen_back(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_CODE_SEARCH_URL", "https://env.example/search")
    (tmp_path / "research-settings.json").write_text("invalid", encoding="utf-8")
    with pytest.raises(ValueError):
        resolve_endpoint(tmp_path)
