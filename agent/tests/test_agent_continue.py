"""Tests for ``agent.continue_run`` IPC handler (v1.1.0 block-budget resume).

Validates:
- Param validation (non-dict params / missing session_id → -32602).
- Storage-unavailable and no-run / not-truncated guards reply
  ``{"ok": False, ...}`` without forwarding.
- The happy path: a truncated latest run is marked ``continued`` and the
  call is forwarded to ``handle_agent_send_message`` with the fixed
  continuation prompt.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from minimax_code.ipc import builtins as builtins_mod
from minimax_code.ipc.builtins import handle_agent_continue_run
from minimax_code.ipc.server import Context, IPCServer


def _make_ctx() -> Context:
    server = AsyncMock(spec=IPCServer)
    ctx = Context(server=server, request_id=1, method="agent.continue_run")
    ctx.reply = AsyncMock()
    ctx.reply_error = AsyncMock()
    ctx.emit = AsyncMock()
    return ctx


class _FakeRunsDAO:
    """Minimal AgentRunsDAO double: filterable list + recorded updates."""

    def __init__(self, runs: list[dict]) -> None:
        self._runs = runs
        self.updates: list[tuple[str, str, dict | None]] = []

    async def list_runs(self, *, session_id=None, limit=None, **_kw):  # type: ignore[no-untyped-def]
        matched = [r for r in self._runs if r.get("session_id") == session_id]
        return matched[: limit] if limit else matched

    async def get_run(self, run_id):
        return next((run for run in self._runs if run["id"] == run_id), None)

    async def request_payload(self, run_id):
        return None  # These legacy generic runs deliberately have no saved request.

    async def update_run_status(
        self,
        run_id: str,
        *,
        status: str,
        error: str | None = None,
        assistant_message_id: str | None = None,
        metadata: dict | None = None,
    ) -> dict | None:
        self.updates.append((run_id, status, metadata))
        return None


def _truncated_run(session_id: str = "ses_x") -> dict:
    return {
        "id": "run_trunc",
        "session_id": session_id,
        "status": "completed",
        "metadata": {"iterations": 12, "truncated": True},
    }


@pytest.fixture
def wire_dao(monkeypatch: pytest.MonkeyPatch):
    """Install a fake db + DAO; returns the DAO holder to mutate per test."""
    holder: dict[str, object] = {"dao": _FakeRunsDAO([])}

    async def raw_history(_query, params):
        return [
            {"id": run["id"], "session_id": run["session_id"],
             "metadata": json.dumps(run.get("metadata", {}))}
            for run in holder["dao"]._runs if run["session_id"] == params[0]
        ]

    holder["db"] = SimpleNamespace(fetchall=AsyncMock(side_effect=raw_history))

    def _get_db():  # type: ignore[no-untyped-def]
        return holder["db"]

    def _dao_factory(db):  # type: ignore[no-untyped-def]
        return holder["dao"]

    monkeypatch.setattr("minimax_code.app.get_db", _get_db)
    monkeypatch.setattr("minimax_code.storage.dao.runs.AgentRunsDAO", _dao_factory)
    return holder


@pytest.fixture
def stub_send(monkeypatch: pytest.MonkeyPatch):
    """Replace send_message; records forwarded params."""
    forwarded: list[dict] = []

    async def _fake_send(params, ctx, **options):  # type: ignore[no-untyped-def]
        forwarded.append({**params, "options": options})
        await ctx.reply({"session_id": params.get("session_id"), "text": "continued"})

    monkeypatch.setattr(builtins_mod, "handle_agent_send_message", _fake_send)
    return forwarded


# ---------------------------------------------------------------------------
# Validation guards
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_non_dict_params_rejected():
    ctx = _make_ctx()
    await handle_agent_continue_run("nope", ctx)
    ctx.reply_error.assert_awaited_once_with(-32602, "params must be an object")


@pytest.mark.asyncio
async def test_missing_session_id_rejected():
    ctx = _make_ctx()
    await handle_agent_continue_run({}, ctx)
    ctx.reply_error.assert_awaited_once_with(-32602, "session_id is required")


@pytest.mark.asyncio
async def test_no_db_replies_ok_false(monkeypatch: pytest.MonkeyPatch, stub_send: list):
    monkeypatch.setattr("minimax_code.app.get_db", lambda: None)
    ctx = _make_ctx()
    await handle_agent_continue_run({"session_id": "ses_x"}, ctx)
    result = ctx.reply.call_args[0][0]
    assert result["ok"] is False
    assert result["error"] == "storage unavailable"
    assert stub_send == []  # never forwarded


@pytest.mark.asyncio
async def test_no_runs_replies_ok_false(wire_dao, stub_send):
    ctx = _make_ctx()
    await handle_agent_continue_run({"session_id": "ses_empty"}, ctx)
    result = ctx.reply.call_args[0][0]
    assert result["ok"] is False
    assert "no truncated run" in result["error"]
    assert stub_send == []


@pytest.mark.asyncio
async def test_latest_run_not_truncated_replies_ok_false(wire_dao, stub_send):
    wire_dao["dao"] = _FakeRunsDAO(
        [
            {
                "id": "run_ok",
                "session_id": "ses_x",
                "status": "completed",
                "metadata": {"iterations": 3, "truncated": False},
            }
        ]
    )
    ctx = _make_ctx()
    await handle_agent_continue_run({"session_id": "ses_x"}, ctx)
    result = ctx.reply.call_args[0][0]
    assert result["ok"] is False
    assert wire_dao["dao"].updates == []  # untouched — nothing to mark


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_truncated_run_forwards_atomic_continuation_intent(wire_dao, stub_send):
    dao = _FakeRunsDAO([_truncated_run()])
    wire_dao["dao"] = dao
    ctx = _make_ctx()
    await handle_agent_continue_run({"session_id": "ses_x"}, ctx)

    # The handler must not mark success before durable admission commits.
    assert dao.updates == []

    # And the call forwarded to send_message with the fixed prompt.
    assert len(stub_send) == 1
    assert stub_send[0]["session_id"] == "ses_x"
    assert stub_send[0]["content"].startswith("[continue]")
    assert stub_send[0]["idempotency_key"].startswith("continue:")
    assert stub_send[0]["options"]["resumed_from_id"] == "run_trunc"
    assert stub_send[0]["options"]["continuation"] is True

    # The reply is whatever send_message produced (standard envelope).
    result = ctx.reply.call_args[0][0]
    assert result["text"] == "continued"


@pytest.mark.asyncio
async def test_explicit_run_pins_retry_and_cannot_cross_sessions(wire_dao, stub_send):
    original = {**_truncated_run(), "mode": "plan"}
    original["metadata"].update(model_snapshot={"model": "original"}, workspace_snapshot="/work")
    dao = _FakeRunsDAO([{**_truncated_run(), "id": "new-latest"}, original])
    wire_dao["dao"] = dao
    ctx = _make_ctx()
    await handle_agent_continue_run({"session_id": "ses_x", "run_id": "run_trunc"}, ctx)
    assert len(stub_send) == 1
    assert stub_send[0]["mode"] == "plan"
    assert stub_send[0]["options"]["required_model"] == {"model": "original"}
    assert stub_send[0]["options"]["required_workspace"] == "/work"
    await handle_agent_continue_run({"session_id": "ses_x", "run_id": "run_trunc"}, ctx)
    assert stub_send[0] == stub_send[1]
    await handle_agent_continue_run({"session_id": "other", "run_id": "run_trunc"}, ctx)
    assert len(stub_send) == 2 and ctx.reply.call_args.args[0]["ok"] is False


@pytest.mark.parametrize("params", [{"session_id": 12}, {"session_id": "s", "run_id": " "},
                                   {"session_id": "s", "run_id": []}])
async def test_invalid_continuation_identity_is_rejected(params):
    ctx = _make_ctx()
    await handle_agent_continue_run(params, ctx)
    assert ctx.reply_error.call_args.args[0] == -32602
