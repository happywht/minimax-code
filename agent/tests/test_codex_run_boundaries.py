"""Codex 0.163.0 event replay: terminal truth, accounting and execution policy."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from minimax_code.engines import codex_run


def usage_event(total: int) -> dict:
    breakdown = {"inputTokens": total - 10, "outputTokens": 10,
                 "totalTokens": total, "cachedInputTokens": 0, "reasoningOutputTokens": 0}
    return {"method": "thread/tokenUsage/updated", "params": {
        "threadId": "thread-1", "turnId": "turn-1",
        "tokenUsage": {"total": breakdown, "last": breakdown, "modelContextWindow": 10000}}}


@pytest.fixture
def replay(monkeypatch, tmp_path):
    connections = []
    events = []

    class Connection:
        def __init__(self, **kwargs):
            self.calls = []
            self.closed = False
            connections.append(self)

        async def start(self):
            pass

        async def initialize(self):
            return {"userAgent": "codex-cli/0.163.0"}

        async def close(self):
            self.closed = True

        async def request(self, method, params, **kwargs):
            self.calls.append((method, params))
            if method in {"thread/start", "thread/resume"}:
                return {"thread": {"id": "thread-1"}}
            if method == "turn/start":
                for event in events:
                    self.on_notification(event)
                return {"turn": {"id": "turn-1"}}
            return {}

    monkeypatch.setattr(codex_run, "AppServerConnection", Connection)
    monkeypatch.delenv("MINIMAX_CODE_CODEX_CMD", raising=False)
    job = SimpleNamespace(workspace=str(tmp_path), session_id="s", run_id="r",
                          message_id="m", budget_tokens=None, daily_budget_limit=None,
                          cancel_callback=None)

    async def drive(status="completed", **kwargs):
        events.append({"method": "turn/completed", "params": {
            "threadId": "thread-1", "turn": {"id": "turn-1", "status": status,
            "error": {"message": "model failed"} if status == "failed" else None}}})
        return await codex_run.drive_codex_run(
            job, prompt="task", emit=AsyncMock(), codex_cmd=["fake"], **kwargs)

    return SimpleNamespace(job=job, events=events, connections=connections, drive=drive)


@pytest.mark.parametrize("status", ["failed", "interrupted"])
async def test_terminal_failure_is_not_success(replay, status):
    receipt = await replay.drive(status)
    assert receipt["run_status"] == status
    assert receipt["error"]
    assert receipt["tokens_in"] is None and receipt["tokens_out"] is None
    assert replay.connections[0].closed


@pytest.mark.parametrize("status", [None, "inProgress", "unknown"])
async def test_unknown_terminal_status_fails_closed(replay, status):
    with pytest.raises(RuntimeError, match="terminal status"):
        await replay.drive(status)
    assert replay.connections[0].closed


async def test_cumulative_usage_counts_only_increases(replay):
    replay.job.budget_tokens = 200
    replay.events.extend([usage_event(100), usage_event(100), usage_event(120)])
    receipt = await replay.drive()
    assert receipt["budget"]["used_tokens"] == 120
    assert receipt["tokens_in"] == 110 and receipt["tokens_out"] == 10
    assert not receipt["budget_paused"]


async def test_budget_pauses_on_actual_camelcase_usage(replay):
    replay.job.budget_tokens = 10
    replay.events.append(usage_event(120))
    receipt = await replay.drive()
    assert receipt["budget_paused"]
    assert receipt["budget"]["used_tokens"] == 120


async def test_unknown_usage_is_not_zero_and_cannot_pass_budget_gate(replay):
    replay.job.budget_tokens = 10
    receipt = await replay.drive()
    assert receipt["budget_paused"]
    assert receipt["tokens_in"] is None


async def test_failed_turn_does_not_become_budget_pause(replay):
    replay.job.budget_tokens = 10
    replay.events.append(usage_event(120))
    receipt = await replay.drive("failed")
    assert receipt["run_status"] == "failed"
    assert not receipt["budget_paused"]


@pytest.mark.parametrize("mode,policy", [("plan", "readOnly"), ("execute", "workspaceWrite")])
async def test_policy_is_explicit_and_plan_cannot_grant_escape(replay, mode, policy):
    await replay.drive(mode=mode)
    conn = replay.connections[0]
    params = dict(conn.calls)["turn/start"]
    assert params["sandboxPolicy"]["type"] == policy
    assert params["sandboxPolicy"]["networkAccess"] is False
    assert params["approvalsReviewer"] == "user"
    if mode == "plan":
        assert params["approvalPolicy"] == "never"
        assert conn.on_server_request({"id": 1, "method": "item/commandExecution/requestApproval",
                                       "params": {}}) == {"decision": "decline"}


async def test_missing_admitted_directory_never_launches_child(replay, tmp_path):
    replay.job.workspace = str(tmp_path / "removed-project")
    with pytest.raises(ValueError, match="Rebind"):
        await replay.drive()
    assert not replay.connections


async def test_retryable_error_does_not_finish_turn(replay):
    replay.events.append({"method": "error", "params": {"willRetry": True, "error": {"message": "retry"}}})
    receipt = await replay.drive()
    assert not receipt.get("error")


@pytest.mark.parametrize("payload", [{"tokenUsage": 7}, {"tokenUsage": {"total": {"totalTokens": "120"}}}])
async def test_malformed_usage_terminates_instead_of_hanging(replay, payload):
    replay.events.append({"method": "thread/tokenUsage/updated", "params": payload})
    with pytest.raises((RuntimeError, ValueError), match="[Ii]nvalid Codex"):
        await replay.drive()
    assert replay.connections[0].closed


@pytest.mark.parametrize("returncode,version,available", [
    (0, "codex-cli 0.163.0", True), (2, "codex-cli 0.163.0", False),
    (0, "codex-cli 0.999.0", False),
])
async def test_probe_requires_supported_successful_version(monkeypatch, returncode, version, available):
    from minimax_code.engines import codex

    monkeypatch.setattr(codex.shutil, "which", lambda _: "codex")
    monkeypatch.setattr(codex, "_launch_codex", AsyncMock(return_value=SimpleNamespace(
        returncode=returncode)))
    monkeypatch.setattr(codex, "communicate_bounded", AsyncMock(return_value=SimpleNamespace(
        stdout=version.encode())))
    info = await codex._probe_codex_async()
    assert info.available is available
    assert info.capabilities["resume"] and not info.capabilities["steer"]
    if not available:
        assert info.unavailable_reason


async def test_decreasing_current_counter_marks_uncertainty_and_stops_bounded_run(replay):
    replay.job.budget_tokens = 200
    replay.events.extend([usage_event(100), usage_event(80), usage_event(120)])
    receipt = await replay.drive()
    assert receipt["usage_certainty"] == "uncertain"
    assert receipt["budget_paused"]
    assert receipt["budget"]["used_tokens"] == 100  # Never rebase a reset to zero.
