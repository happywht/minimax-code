"""Tests for multimodal content handling in builtins.send_message (v0.6.0).

Covers:
  - String content backward compatibility
  - List content (ContentPart[]) accepted
  - Empty list rejected
  - Title extraction from multimodal content
"""
from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minimax_code.config import Config
from minimax_code.ipc.builtins import handle_agent_send_message
from minimax_code.ipc.protocol import Response
from minimax_code.ipc.server import IPCServer


def _make_server() -> IPCServer:
    return IPCServer(
        config=Config.from_env(),
        stdin=io.StringIO(),
        stdout=io.StringIO(),
    )


class _FakeCtx:
    """Minimal Context double for testing handlers."""

    def __init__(self, server: IPCServer) -> None:
        self.server = server
        self._replied: list[dict[str, Any]] = []
        self._errors: list[tuple[int, str]] = []
        self._emitted: list[tuple[str, Any]] = []

    async def reply(self, data: Any) -> None:
        self._replied.append(data)

    async def reply_error(self, code: int, message: str) -> None:
        self._errors.append((code, message))

    async def emit(self, event: str, data: Any, **kw: Any) -> None:
        self._emitted.append((event, data))


class TestSendMessageMultimodal:
    """Test multimodal content handling in agent.send_message."""

    @pytest.mark.asyncio
    async def test_string_content_backward_compat(self) -> None:
        """String content should still work without error."""
        server = _make_server()
        ctx = _FakeCtx(server)

        # Patch the lazy imports inside the handler
        mock_llm_mod = MagicMock()
        mock_llm_cls = MagicMock()
        mock_llm = MagicMock()
        mock_llm_cls.return_value = mock_llm
        mock_llm_mod.MiniMaxClient = mock_llm_cls

        mock_core_cls = MagicMock()
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.final_text = "hello"
        mock_result.iterations = 1
        mock_result.usage = {}
        mock_core.run = AsyncMock(return_value=mock_result)
        mock_core_cls.return_value = mock_core

        mock_agent_mod = MagicMock()
        mock_agent_mod.AgentCore = mock_core_cls
        mock_agent_mod.MiniMaxClient = mock_llm_cls
        mock_agent_mod.AgentConfig = MagicMock()

        mock_app_mod = MagicMock()
        mock_app_mod.get_sessions_dao = MagicMock(return_value=None)
        mock_app_mod.init_runtime = AsyncMock()

        mock_msg_dao = MagicMock()
        mock_msg_dao_mod = MagicMock()
        mock_msg_dao_mod.MessagesDAO = MagicMock(return_value=None)

        mock_db_mod = MagicMock()
        mock_db_mod.AsyncDatabase = MagicMock(return_value=None)

        mock_bpe = AsyncMock(return_value=None)

        with patch.dict("sys.modules", {
            "minimax_code.agent": mock_agent_mod,
            "minimax_code.agent.core": mock_agent_mod,
            "minimax_code.app": mock_app_mod,
            "minimax_code.storage.dao.messages": mock_msg_dao_mod,
            "minimax_code.storage.db": mock_db_mod,
        }), patch("minimax_code.ipc.builtins._build_system_prompt_extra", mock_bpe):
            await handle_agent_send_message(
                {"content": "Hello world", "session_id": "test-ses-1"},
                ctx,
            )

        # Should not have reply_error
        assert len(ctx._errors) == 0

    @pytest.mark.asyncio
    async def test_list_content_accepted(self) -> None:
        """List content (multimodal) should be accepted."""
        server = _make_server()
        ctx = _FakeCtx(server)

        multimodal_content = [
            {"type": "text", "text": "Describe this image"},
            {"type": "image", "media_type": "image/png", "data": "iVBORw0KGgo="},
        ]

        mock_agent_mod = MagicMock()
        mock_llm_cls = MagicMock()
        mock_llm = MagicMock()
        mock_llm_cls.return_value = mock_llm
        mock_core_cls = MagicMock()
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.final_text = "I see a diagram"
        mock_result.iterations = 1
        mock_result.usage = {}
        mock_core.run = AsyncMock(return_value=mock_result)
        mock_core_cls.return_value = mock_core
        mock_agent_mod.AgentCore = mock_core_cls
        mock_agent_mod.MiniMaxClient = mock_llm_cls
        mock_agent_mod.AgentConfig = MagicMock()

        mock_app_mod = MagicMock()
        mock_app_mod.get_sessions_dao = MagicMock(return_value=None)
        mock_app_mod.init_runtime = AsyncMock()

        mock_msg_dao_mod = MagicMock()
        mock_msg_dao_mod.MessagesDAO = MagicMock(return_value=None)

        mock_db_mod = MagicMock()
        mock_db_mod.AsyncDatabase = MagicMock(return_value=None)

        mock_bpe = AsyncMock(return_value=None)

        with patch.dict("sys.modules", {
            "minimax_code.agent": mock_agent_mod,
            "minimax_code.agent.core": mock_agent_mod,
            "minimax_code.app": mock_app_mod,
            "minimax_code.storage.dao.messages": mock_msg_dao_mod,
            "minimax_code.storage.db": mock_db_mod,
        }), patch("minimax_code.ipc.builtins._build_system_prompt_extra", mock_bpe):
            await handle_agent_send_message(
                {"content": multimodal_content, "session_id": "test-ses-2"},
                ctx,
            )

        assert len(ctx._errors) == 0

    @pytest.mark.asyncio
    async def test_empty_list_rejected(self) -> None:
        """Empty list content should be rejected."""
        server = _make_server()
        ctx = _FakeCtx(server)

        await handle_agent_send_message(
            {"content": [], "session_id": "test-ses-3"},
            ctx,
        )

        assert len(ctx._errors) == 1
        assert ctx._errors[0][0] == -32602
        assert "non-empty" in ctx._errors[0][1]

    @pytest.mark.asyncio
    async def test_title_from_multimodal(self) -> None:
        """Title should be extracted from first text block in multimodal content."""
        server = _make_server()
        ctx = _FakeCtx(server)

        multimodal_content = [
            {"type": "text", "text": "Analyze this architecture diagram"},
            {"type": "image", "media_type": "image/png", "data": "abc"},
        ]

        created_titles: list[str] = []

        mock_agent_mod = MagicMock()
        mock_llm_cls = MagicMock()
        mock_llm_cls.return_value = MagicMock()
        mock_core_cls = MagicMock()
        mock_core = MagicMock()
        mock_result = MagicMock()
        mock_result.final_text = "analysis"
        mock_result.iterations = 1
        mock_result.usage = {}
        mock_core.run = AsyncMock(return_value=mock_result)
        mock_core_cls.return_value = mock_core
        mock_agent_mod.AgentCore = mock_core_cls
        mock_agent_mod.MiniMaxClient = mock_llm_cls
        mock_agent_mod.AgentConfig = MagicMock()

        # Track the title used in session creation
        mock_sess_dao = MagicMock()
        mock_sess_dao.get = AsyncMock(return_value=None)

        async def fake_create(**kw: Any) -> None:
            created_titles.append(kw.get("title", ""))

        mock_sess_dao.create = fake_create

        mock_app_mod = MagicMock()
        mock_app_mod.get_sessions_dao = MagicMock(return_value=mock_sess_dao)
        mock_app_mod.init_runtime = AsyncMock()

        mock_msg_dao_mod = MagicMock()
        mock_msg_dao_mod.MessagesDAO = MagicMock(return_value=None)

        mock_db_mod = MagicMock()
        mock_db_mod.AsyncDatabase = MagicMock(return_value=None)

        mock_bpe = AsyncMock(return_value=None)

        with patch.dict("sys.modules", {
            "minimax_code.agent": mock_agent_mod,
            "minimax_code.agent.core": mock_agent_mod,
            "minimax_code.app": mock_app_mod,
            "minimax_code.storage.dao.messages": mock_msg_dao_mod,
            "minimax_code.storage.db": mock_db_mod,
        }), patch("minimax_code.ipc.builtins._build_system_prompt_extra", mock_bpe):
            await handle_agent_send_message(
                {"content": multimodal_content, "session_id": "test-ses-4"},
                ctx,
            )

        assert len(created_titles) > 0
        assert "Analyze this architecture diagram"[:32] in created_titles[0]
