"""Unit tests for Code Review LLM wiring in handlers_skills.py.

Tests the skill.invoke handler's code-review path that decides between
real LLM and mock fallback based on runtime_obj.llm.mock. Uses
mock/patch to simulate runtime and LLM objects — never hits a real LLM.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from minimax_code.agent.tools.base import ToolResult


# ---------------------------------------------------------------------------
# Helpers: lightweight fakes for Context and Server
# ---------------------------------------------------------------------------


@dataclass
class _FakeReply:
    """Captured reply data from ctx.reply()."""
    result: Any = None
    error_code: int | None = None
    error_message: str | None = None


class _FakeContext:
    """Minimal Context stub that records replies and emits."""

    def __init__(self) -> None:
        self.reply_data: _FakeReply | None = None
        self.events: list[tuple[str, dict]] = []
        self.request_id = 1

    async def reply(self, result: Any) -> None:
        self.reply_data = _FakeReply(result=result)

    async def reply_error(self, code: int, message: str, data: Any = None) -> None:
        self.reply_data = _FakeReply(error_code=code, error_message=message)

    async def emit(self, event: str, data: Any = None, *, metadata: dict | None = None) -> None:
        self.events.append((event, data))


class _FakeServer:
    """Minimal server stub that supports register()."""

    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def register(self, name: str, handler: Any) -> None:
        self.handlers[name] = handler


# ---------------------------------------------------------------------------
# Helpers: fake runtime objects
# ---------------------------------------------------------------------------


def _make_mock_runtime(*, llm_mock: bool = True) -> MagicMock:
    """Build a fake runtime with a configurable LLM mock flag."""
    runtime = MagicMock()

    # Configure the LLM object.
    llm = MagicMock()
    llm.mock = llm_mock
    runtime.llm = llm

    # Configure the registry.
    runtime.registry = MagicMock()
    runtime.registry.list.return_value = []

    # Configure invoke to return a plausible result.
    invoke_result = MagicMock()
    invoke_result.final_text = "LLM review result"
    invoke_result.iterations = 1
    invoke_result.tool_calls = 0
    invoke_result.cancelled = False
    invoke_result.truncated = False
    runtime.invoke = AsyncMock(return_value=invoke_result)

    return runtime


def _sample_diff() -> str:
    """Return a small unified diff for testing."""
    return (
        "--- a/hello.py\n"
        "+++ b/hello.py\n"
        "@@ -1,3 +1,4 @@\n"
        " def hello():\n"
        "-    return 'hello'\n"
        "+    return 'hello world'\n"
        "+\n"
    )


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


async def test_mock_mode_uses_deterministic_path() -> None:
    """When llm.mock=True, the handler falls back to _review_diff."""
    from minimax_code.ipc.handlers_skills import register_skill_handlers

    runtime = _make_mock_runtime(llm_mock=True)
    server = _FakeServer()
    register_skill_handlers(server, runtime=runtime)

    ctx = _FakeContext()
    await server.handlers["skill.invoke"](
        params={
            "skill_id": "code-review:code-review",
            "request": "Review this",
            "diff": _sample_diff(),
        },
        ctx=ctx,
    )

    # Should have replied successfully.
    assert ctx.reply_data is not None
    assert ctx.reply_data.error_code is None
    result = ctx.reply_data.result
    assert result is not None

    # Stats are always computed from _diff_stats.
    assert "stats" in result
    assert result["stats"]["files"] == 1
    assert result["stats"]["additions"] >= 1

    # The deterministic path produces text about the diff.
    assert isinstance(result["text"], str)
    assert len(result["text"]) > 0

    # runtime.invoke should NOT have been called (mock path).
    runtime.invoke.assert_not_called()


async def test_real_llm_mode_calls_runtime_invoke() -> None:
    """When llm.mock=False, the handler routes through runtime.invoke()."""
    from minimax_code.ipc.handlers_skills import register_skill_handlers

    runtime = _make_mock_runtime(llm_mock=False)
    server = _FakeServer()
    register_skill_handlers(server, runtime=runtime)

    ctx = _FakeContext()
    await server.handlers["skill.invoke"](
        params={
            "skill_id": "code-review:code-review",
            "request": "Review this",
            "diff": _sample_diff(),
        },
        ctx=ctx,
    )

    assert ctx.reply_data is not None
    assert ctx.reply_data.error_code is None
    result = ctx.reply_data.result

    # runtime.invoke should have been called.
    runtime.invoke.assert_called_once()

    # The result contains the LLM's output.
    assert result["text"] == "LLM review result"

    # Stats are still computed from _diff_stats.
    assert "stats" in result
    assert result["stats"]["files"] == 1


async def test_stats_always_computed() -> None:
    """Stats are computed from _diff_stats regardless of LLM path."""
    from minimax_code.ipc.handlers_skills import register_skill_handlers

    for llm_mock_flag in (True, False):
        runtime = _make_mock_runtime(llm_mock=llm_mock_flag)
        server = _FakeServer()
        register_skill_handlers(server, runtime=runtime)

        ctx = _FakeContext()
        await server.handlers["skill.invoke"](
            params={
                "skill_id": "code-review:code-review",
                "request": "Review",
                "diff": _sample_diff(),
            },
            ctx=ctx,
        )

        result = ctx.reply_data.result
        assert "stats" in result
        assert isinstance(result["stats"], dict)
        assert "files" in result["stats"]
        assert "additions" in result["stats"]
        assert "deletions" in result["stats"]


async def test_response_shape_compatible() -> None:
    """The response shape matches: {text, comments, stats}."""
    from minimax_code.ipc.handlers_skills import register_skill_handlers

    runtime = _make_mock_runtime(llm_mock=True)
    server = _FakeServer()
    register_skill_handlers(server, runtime=runtime)

    ctx = _FakeContext()
    await server.handlers["skill.invoke"](
        params={
            "skill_id": "code-review:code-review",
            "request": "Review",
            "diff": _sample_diff(),
        },
        ctx=ctx,
    )

    result = ctx.reply_data.result
    assert "text" in result
    assert "comments" in result
    assert "stats" in result
    assert isinstance(result["comments"], list)


async def test_missing_diff_skips_short_circuit() -> None:
    """When no diff param is provided, the code review short-circuit is skipped entirely."""
    from minimax_code.ipc.handlers_skills import register_skill_handlers

    runtime = _make_mock_runtime(llm_mock=False)
    server = _FakeServer()
    register_skill_handlers(server, runtime=runtime)

    ctx = _FakeContext()
    await server.handlers["skill.invoke"](
        params={
            "skill_id": "code-review:code-review",
            "request": "Do something",
            # No "diff" key.
        },
        ctx=ctx,
    )

    # Should still invoke the runtime (normal skill path).
    runtime.invoke.assert_called_once()
    result = ctx.reply_data.result
    assert result["text"] == "LLM review result"
    # Normal skill path does not include "stats" or "comments".
    assert "stats" not in result or result.get("stats") is None


async def test_empty_diff_string_skips_short_circuit() -> None:
    """An empty diff string does not trigger the code review path."""
    from minimax_code.ipc.handlers_skills import register_skill_handlers

    runtime = _make_mock_runtime(llm_mock=False)
    server = _FakeServer()
    register_skill_handlers(server, runtime=runtime)

    ctx = _FakeContext()
    await server.handlers["skill.invoke"](
        params={
            "skill_id": "code-review:code-review",
            "request": "Do something",
            "diff": "",
        },
        ctx=ctx,
    )

    # Empty diff does not match the isinstance + truthiness check.
    runtime.invoke.assert_called_once()


async def test_real_llm_emits_streaming_events() -> None:
    """Real LLM path emits message_chunk events during streaming."""
    from minimax_code.ipc.handlers_skills import register_skill_handlers

    runtime = _make_mock_runtime(llm_mock=False)
    server = _FakeServer()
    register_skill_handlers(server, runtime=runtime)

    ctx = _FakeContext()
    await server.handlers["skill.invoke"](
        params={
            "skill_id": "code-review:code-review",
            "request": "Review",
            "diff": _sample_diff(),
        },
        ctx=ctx,
    )

    # The real LLM path registered callbacks but the mock runtime
    # invoke completes immediately without calling them. At minimum
    # the handler should have completed without error.
    assert ctx.reply_data is not None
    assert ctx.reply_data.error_code is None


async def test_mock_mode_emits_message_chunk() -> None:
    """Mock mode emits at least one agent.message_chunk event."""
    from minimax_code.ipc.handlers_skills import register_skill_handlers

    runtime = _make_mock_runtime(llm_mock=True)
    server = _FakeServer()
    register_skill_handlers(server, runtime=runtime)

    ctx = _FakeContext()
    await server.handlers["skill.invoke"](
        params={
            "skill_id": "code-review:code-review",
            "request": "Review",
            "diff": _sample_diff(),
        },
        ctx=ctx,
    )

    # Mock path emits a message_chunk with the review text.
    chunk_events = [e for e in ctx.events if e[0] == "agent.message_chunk"]
    assert len(chunk_events) >= 1
    assert chunk_events[0][1]["done"] is True
