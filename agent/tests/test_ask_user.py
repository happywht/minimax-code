"""Tests for the ``ask_user`` tool and its interactive wait pipeline.

Covers three layers:
- ``_validate_questions`` / ``AskUserTool.run`` — schema contract.
- ``AgentCore._execute_tool_call`` interception — the pending marker is
  replaced by the user's answers (answered / timeout / cancelled /
  no-callback paths), including route-table and pending-map cleanup.
- ``handle_agent_answer_user`` — the ``agent.answer_user`` RPC.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import minimax_code.agent.core as core_module
from minimax_code.agent.core import _ASK_USER_ROUTES, AgentConfig, AgentCore
from minimax_code.agent.tools import ToolRegistry
from minimax_code.agent.tools.ask_user import (
    ASK_USER_MARKER,
    AskUserTool,
    _validate_questions,
    format_answers,
)
from minimax_code.agent.tools.base import ToolResult
from minimax_code.ipc.builtins import handle_agent_answer_user
from minimax_code.ipc.server import Context, IPCServer

# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------

VALID_QUESTIONS: list[dict[str, Any]] = [
    {
        "question": "Which database should the migration target?",
        "header": "Database",
        "options": [
            {"label": "SQLite", "description": "Zero-ops embedded, current default"},
            {"label": "PostgreSQL", "description": "Concurrent writes, needs a server"},
        ],
        "multiSelect": False,
    },
    {
        "question": "Should failing tests block the release?",
        "header": "Gate",
        "options": [
            {"label": "Yes, hard gate", "description": "Block release on any red test"},
            {"label": "No, warn only", "description": "Ship with a warning banner"},
        ],
        "multiSelect": False,
    },
]


def _registry() -> ToolRegistry:
    """Fresh registry with only the ask_user tool registered."""
    registry = ToolRegistry()
    registry.register(AskUserTool())
    return registry


def _core(callback: Any = None) -> AgentCore:
    core = AgentCore(llm=MagicMock(), registry=_registry(), config=AgentConfig())
    if callback is not None:
        core.on_ask_user = callback
    return core


@pytest.fixture(autouse=True)
def _clean_routes():
    """Route table must never leak between tests."""
    _ASK_USER_ROUTES.clear()
    yield
    _ASK_USER_ROUTES.clear()


def _make_ctx() -> Context:
    server = MagicMock(spec=IPCServer)
    ctx = Context(server=server, request_id=1, method="agent.answer_user")
    ctx.reply = AsyncMock()
    ctx.reply_error = AsyncMock()
    ctx.emit = AsyncMock()
    return ctx


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_validate_accepts_valid_questions_and_normalises():
    normalised, error = _validate_questions(
        [
            {
                "question": "  Proceed?  ",
                "options": [
                    {"label": " Yes ", "description": "go"},
                    {"label": "No", "description": "stop", "extra": "dropped"},
                ],
            }
        ]
    )
    assert error is None
    assert normalised is not None
    assert normalised[0]["question"] == "Proceed?"
    # header defaults to a trimmed prefix of the question text
    assert normalised[0]["header"] == "Proceed?"
    assert normalised[0]["options"][0] == {"label": "Yes", "description": "go"}
    assert normalised[0]["multiSelect"] is False


def test_validate_rejects_over_limit_question_count():
    too_many = [
        {"question": f"Q{i}?", "options": [
            {"label": "a", "description": ""},
            {"label": "b", "description": ""},
        ]}
        for i in range(5)
    ]
    _, error = _validate_questions(too_many)
    assert error is not None
    assert "4" in error


@pytest.mark.parametrize(
    "questions, fragment",
    [
        ([], "non-empty"),
        ([{"question": "q?", "options": [{"label": "a", "description": ""}]}], "2..4"),
        (
            [{"question": "q?", "options": [
                {"label": "a", "description": ""},
                {"label": "a", "description": ""},
            ]}],
            "duplicate",
        ),
        ([{"question": "", "options": []}], "non-empty string"),
        (
            [{"question": "q?", "options": [
                {"label": "a", "description": ""},
                {"label": "b", "description": ""},
            ], "multiSelect": "yes"}],
            "boolean",
        ),
    ],
)
def test_validate_rejects_malformed_payloads(questions: Any, fragment: str):
    _, error = _validate_questions(questions)
    assert error is not None
    assert fragment in error


@pytest.mark.asyncio
async def test_tool_run_returns_pending_marker():
    result = await AskUserTool().run(questions=VALID_QUESTIONS)
    assert result.success is True
    assert result.metadata[ASK_USER_MARKER] == VALID_QUESTIONS
    assert result.output == {"status": "pending", "questions": VALID_QUESTIONS}


@pytest.mark.asyncio
async def test_tool_run_rejects_bad_payload():
    result = await AskUserTool().run(questions=[])
    assert result.success is False
    assert ASK_USER_MARKER not in result.metadata


def test_format_answers_renders_single_and_multi():
    text = format_answers(
        [
            {"header": "DB", "question": "which?", "options": [], "multiSelect": False},
            {"header": "Gate", "question": "gate?", "options": [], "multiSelect": True},
        ],
        ["SQLite", ["Yes, hard gate", "other: also run e2e"]],
    )
    assert "- DB: SQLite" in text
    assert "- Gate: Yes, hard gate; other: also run e2e" in text
    assert text.startswith("The user answered:")


def test_format_answers_tolerates_missing_entries():
    text = format_answers([{"header": "DB", "options": [], "multiSelect": False}], [])
    assert "(no answer)" in text


# ---------------------------------------------------------------------------
# AgentCore interception — _execute_tool_call
# ---------------------------------------------------------------------------


def _prepared() -> Any:
    from minimax_code.agent.core import _PreparedToolCall

    return _PreparedToolCall(
        call_log={"id": "toolu_1", "name": "ask_user", "args": {
            "questions": VALID_QUESTIONS
        }},
        name="ask_user",
        args={"questions": VALID_QUESTIONS},
        action="allow",
        breaker=None,
        short_circuit=None,
    )


@pytest.mark.asyncio
async def test_execute_replaces_marker_with_user_answers():
    """End-to-end: the tool's pending marker never escapes — the
    dispatch result carries the user's answers instead."""
    async def answer_immediately(payload: dict) -> None:
        assert payload["questions"] == VALID_QUESTIONS
        assert payload["request_id"].startswith("ask_")
        assert payload["timeout_s"] > 0
        core.resolve_ask_user(payload["request_id"], ["SQLite", ["No, warn only"]])

    core = _core(answer_immediately)
    result = await core._execute_tool_call(_prepared())

    assert result.success is True
    assert "- Database: SQLite" in result.output
    assert "- Gate: No, warn only" in result.output
    assert result.metadata.get("ask_user_answered") is True
    # marker must be gone — the LLM would otherwise see a dangling
    # "pending" payload as the tool output.
    assert ASK_USER_MARKER not in result.metadata
    # route table cleaned up
    assert _ASK_USER_ROUTES == {}
    assert core._pending_ask_user == {}


@pytest.mark.asyncio
async def test_execute_without_callback_reports_unavailable():
    core = _core()  # on_ask_user stays None (headless sub-agent core)
    result = await core._execute_tool_call(_prepared())

    assert result.success is True
    assert "unavailable" in str(result.output)
    assert result.metadata.get("ask_user_skipped") is True
    assert _ASK_USER_ROUTES == {}


@pytest.mark.asyncio
async def test_execute_times_out(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(core_module, "ASK_USER_TIMEOUT_S", 0.05)
    core = _core(lambda payload: asyncio.sleep(0))  # never resolves
    result = await core._execute_tool_call(_prepared())

    assert result.success is True
    assert "did not answer" in str(result.output)
    assert result.metadata.get("ask_user_timeout") is True
    assert core._pending_ask_user == {}


@pytest.mark.asyncio
async def test_cancel_wakes_suspended_wait():
    core = _core(lambda payload: asyncio.sleep(0))

    task = asyncio.create_task(core._execute_tool_call(_prepared()))
    # Wait until the wait is actually suspended (route registered).
    for _ in range(100):
        if _ASK_USER_ROUTES:
            break
        await asyncio.sleep(0.01)
    assert _ASK_USER_ROUTES, "ask_user wait never started"

    core.cancel()
    result = await asyncio.wait_for(task, timeout=2)

    assert result.success is True
    assert "cancelled" in str(result.output)
    assert result.metadata.get("ask_user_cancelled") is True


# ---------------------------------------------------------------------------
# resolve_ask_user semantics
# ---------------------------------------------------------------------------


def test_resolve_unknown_request_id_returns_false():
    core = _core()
    assert core.resolve_ask_user("ask_missing", ["x"]) is False


@pytest.mark.asyncio
async def test_resolve_twice_returns_false_second_time():
    async def answer(payload: dict) -> None:
        pass

    core = _core(answer)
    task = asyncio.create_task(core._wait_for_ask_user(VALID_QUESTIONS))
    for _ in range(100):
        if core._pending_ask_user:
            break
        await asyncio.sleep(0.01)
    request_id = next(iter(core._pending_ask_user))
    assert core.resolve_ask_user(request_id, ["SQLite"]) is True
    result = await asyncio.wait_for(task, timeout=2)
    assert result.success is True
    # The future is done; a second resolve must report failure, not raise.
    assert core.resolve_ask_user(request_id, ["SQLite"]) is False


# ---------------------------------------------------------------------------
# agent.answer_user handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_answer_user_handler_rejects_bad_params():
    ctx = _make_ctx()
    await handle_agent_answer_user("not-a-dict", ctx)
    ctx.reply_error.assert_awaited_once_with(-32602, "params must be an object")

    ctx = _make_ctx()
    await handle_agent_answer_user({"answers": ["x"]}, ctx)
    ctx.reply_error.assert_awaited_once_with(-32602, "request_id is required")

    ctx = _make_ctx()
    await handle_agent_answer_user({"request_id": "ask_1", "answers": []}, ctx)
    ctx.reply_error.assert_awaited_once_with(
        -32602, "answers must be a non-empty array"
    )


@pytest.mark.asyncio
async def test_answer_user_handler_unknown_request():
    ctx = _make_ctx()
    await handle_agent_answer_user(
        {"request_id": "ask_missing", "answers": ["x"]}, ctx
    )
    ctx.reply.assert_awaited_once()
    assert ctx.reply.call_args[0][0]["ok"] is False


@pytest.mark.asyncio
async def test_answer_user_handler_resolves_pending_request():
    core = _core(lambda payload: asyncio.sleep(0))
    task = asyncio.create_task(core._wait_for_ask_user(VALID_QUESTIONS))
    for _ in range(100):
        if core._pending_ask_user:
            break
        await asyncio.sleep(0.01)
    request_id = next(iter(core._pending_ask_user))

    ctx = _make_ctx()
    await handle_agent_answer_user(
        {"request_id": request_id, "answers": ["SQLite", ["No, warn only"]]}, ctx
    )
    ctx.reply.assert_awaited_once()
    assert ctx.reply.call_args[0][0]["ok"] is True

    result: ToolResult = await asyncio.wait_for(task, timeout=2)
    assert result.success is True
    assert "SQLite" in str(result.output)


# ---------------------------------------------------------------------------
# Registry wiring
# ---------------------------------------------------------------------------


def test_default_registry_contains_ask_user():
    from minimax_code.agent.tools import get_default_registry

    assert get_default_registry().get("ask_user") is not None
