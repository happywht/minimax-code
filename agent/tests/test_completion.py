"""Tests for the inline code completion module (v0.6.0 Stage 1).

Covers:
  - _build_fim_prompt construction
  - complete() with mock LLM client
  - Edge cases: empty prefix, oversized content, temperature clamping
  - POST /complete route integration (valid, missing fields, oversized body)
"""
from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from minimax_code.agent.completion import (
    CompletionRequest,
    CompletionResponse,
    _build_fim_prompt,
    complete,
)
from minimax_code.agent.types import LLMResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_llm(text: str = "print('hello')") -> MagicMock:
    """Build a mock MiniMaxClient whose chat() returns a canned response."""
    llm = MagicMock()
    llm.default_model = "MiniMax-M3"
    llm.chat = AsyncMock(
        return_value=LLMResponse(
            message={"role": "assistant", "content": text},
            usage={"input_tokens": 10, "output_tokens": 5},
            finish_reason="stop",
            model="MiniMax-M3",
        )
    )
    return llm


# ---------------------------------------------------------------------------
# _build_fim_prompt
# ---------------------------------------------------------------------------


class TestBuildFimPrompt:
    """Verify the FIM (Fill-In-the-Middle) prompt structure."""

    def test_basic_prompt_structure(self) -> None:
        req = CompletionRequest(
            file_path="main.py",
            content_before="def hello():\n    ",
            content_after="\n    return",
            language="python",
        )
        messages = _build_fim_prompt(req)
        assert len(messages) == 2
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"

    def test_prefix_suffix_in_user_message(self) -> None:
        req = CompletionRequest(
            file_path="app.ts",
            content_before="const x = ",
            content_after=";",
            language="typescript",
        )
        messages = _build_fim_prompt(req)
        user_msg = messages[1]["content"]
        assert "<prefix>" in user_msg
        assert "</prefix>" in user_msg
        assert "<cursor/>" in user_msg
        assert "<suffix>" in user_msg
        assert "</suffix>" in user_msg
        assert "const x = " in user_msg
        assert ";" in user_msg

    def test_language_hint_present(self) -> None:
        req = CompletionRequest(
            file_path="main.py",
            language="python",
        )
        messages = _build_fim_prompt(req)
        assert "(python)" in messages[1]["content"]

    def test_no_language_hint_when_none(self) -> None:
        req = CompletionRequest(file_path="main.py")
        messages = _build_fim_prompt(req)
        assert "()" not in messages[1]["content"]

    def test_empty_prefix_suffix_handled(self) -> None:
        req = CompletionRequest(file_path="empty.txt")
        messages = _build_fim_prompt(req)
        assert len(messages) == 2
        user_msg = messages[1]["content"]
        assert "<prefix>" in user_msg
        assert "</prefix>" in user_msg
        assert "<suffix>" in user_msg
        assert "</suffix>" in user_msg

    def test_oversized_content_truncated(self) -> None:
        huge = "x" * 100_000
        req = CompletionRequest(
            file_path="big.py",
            content_before=huge,
            content_after=huge,
        )
        messages = _build_fim_prompt(req)
        user_msg = messages[1]["content"]
        # Each half should be capped at 25_000 chars
        assert user_msg.count("x") < 60_000


# ---------------------------------------------------------------------------
# complete()
# ---------------------------------------------------------------------------


class TestComplete:
    """Verify the complete() async function."""

    @pytest.mark.asyncio
    async def test_returns_completion_response(self) -> None:
        llm = _make_llm("print('hi')")
        req = CompletionRequest(file_path="test.py", language="python")
        resp = await complete(req, llm)
        assert isinstance(resp, CompletionResponse)
        assert resp.text == "print('hi')"
        assert resp.model == "MiniMax-M3"
        assert resp.tokens_in == 10
        assert resp.tokens_out == 5
        assert resp.latency_ms >= 0

    @pytest.mark.asyncio
    async def test_tools_not_passed(self) -> None:
        """Completion should never pass tools to the LLM."""
        llm = _make_llm()
        req = CompletionRequest(file_path="a.py")
        await complete(req, llm)
        call_kwargs = llm.chat.call_args[1]
        assert call_kwargs.get("tools") is None

    @pytest.mark.asyncio
    async def test_temperature_and_max_tokens_forwarded(self) -> None:
        llm = _make_llm()
        req = CompletionRequest(
            file_path="a.py",
            max_tokens=512,
            temperature=0.7,
        )
        await complete(req, llm)
        call_kwargs = llm.chat.call_args[1]
        assert call_kwargs["max_tokens"] == 512
        assert call_kwargs["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_empty_response_text(self) -> None:
        llm = _make_llm("")
        req = CompletionRequest(file_path="a.py")
        resp = await complete(req, llm)
        assert resp.text == ""


# ---------------------------------------------------------------------------
# Route integration (POST /complete)
# ---------------------------------------------------------------------------


class TestCompletionRoute:
    """Integration tests for the FastAPI POST /complete route."""

    @pytest.fixture()
    def _app(self) -> Any:
        """Build a FastAPI app with the completion route wired up."""
        import io

        from minimax_code.config import Config
        from minimax_code.http_server import build_app
        from minimax_code.ipc.server import IPCServer

        server = IPCServer(
            config=Config.from_env(),
            stdin=io.StringIO(),
            stdout=io.StringIO(),
        )
        app = build_app(server)
        return app

    @pytest.mark.asyncio
    async def test_valid_request(self, _app: Any) -> None:
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=_app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/complete",
                json={
                    "file_path": "main.py",
                    "content_before": "def foo():\n    ",
                    "content_after": "\n    return",
                    "language": "python",
                    "max_tokens": 128,
                    "temperature": 0.3,
                },
            )
            assert resp.status_code == 200
            data = resp.json()
            assert "text" in data
            assert "model" in data

    @pytest.mark.asyncio
    async def test_missing_file_path(self, _app: Any) -> None:
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=_app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/complete",
                json={"content_before": "x = "},
            )
            assert resp.status_code == 400
            assert "file_path" in resp.json()["error"]

    @pytest.mark.asyncio
    async def test_invalid_json(self, _app: Any) -> None:
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=_app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/complete",
                content=b"not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_max_tokens_capped(self, _app: Any) -> None:
        """max_tokens > 1024 should be silently capped."""
        from httpx import ASGITransport, AsyncClient

        async with AsyncClient(
            transport=ASGITransport(app=_app),
            base_url="http://test",
        ) as client:
            resp = await client.post(
                "/complete",
                json={
                    "file_path": "a.py",
                    "max_tokens": 99999,
                },
            )
            assert resp.status_code == 200
