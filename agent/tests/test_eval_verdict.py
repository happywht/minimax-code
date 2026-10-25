"""Offline eval verdict and timeout safety regression tests (no real model)."""

from __future__ import annotations

import argparse
import asyncio
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "eval_harness", Path(__file__).resolve().parents[2] / "evals" / "run_eval.py"
)
assert SPEC is not None and SPEC.loader is not None
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)


def receipt(**changes):
    return {
        "text": "Done", "cancelled": False, "budget_paused": False,
        "truncated": False, "stub": False, "tokens_in": 10, "tokens_out": 5,
        "verification": {"requires_review": False},
        "budget": {"limit_tokens": 100, "used_tokens": 15, "daily_limit": None, "pauses": 0},
        **changes,
    }


def verdict(result=None, *, error=None, passed=True):
    return harness.evaluate_verdict(
        {"result": result, "error": error}, {"passed": passed}, 100
    )


def test_valid_receipt_and_optional_validation_missing_pass():
    result = verdict(receipt())
    assert result["overall_passed"] is True
    assert result["delivery_passed"] is True
    assert harness.evaluation_exit_code([result], 1) == 0


def test_artifact_pass_rpc_timeout_is_not_overall_pass():
    result = verdict(receipt(), error={"message": "timeout"})
    assert result["artifact_passed"] is True
    assert result["execution_status"] == "rpc_error"
    assert result["overall_passed"] is False
    assert harness.evaluation_exit_code([result], 1) == 1
    assert harness.evaluation_exit_code([verdict(receipt())], 6) == 1


@pytest.mark.parametrize("flag", ["cancelled", "budget_paused", "truncated", "stub"])
def test_incomplete_execution(flag):
    result = verdict(receipt(**{flag: True}))
    assert result["execution_status"] == flag
    assert result["overall_passed"] is False


def test_execution_status_priority():
    all_flags = receipt(cancelled=True, budget_paused=True, truncated=True, stub=True)
    assert verdict(all_flags)["execution_status"] == "cancelled"
    assert verdict(all_flags, error={"code": 1})["execution_status"] == "rpc_error"


@pytest.mark.parametrize("invalid", [None, {}, [], "done", {"text": "done"}])
def test_missing_invalid_receipt(invalid):
    result = verdict(invalid)
    assert result["execution_status"] == "unknown"
    assert result["overall_passed"] is False


@pytest.mark.parametrize("verification,status", [
    (None, "unknown"), ({}, "unknown"),
    ({"requires_review": True}, "requires_review"),
    ({"requires_review": False, "validation": {"ok": False}}, "requires_review"),
    ({"requires_review": False, "validation": {"ok": True}}, "externally_verified"),
    ({"requires_review": False}, "externally_verified"),
    ({"requires_review": False, "validation": {}}, "unknown"),
])
def test_evidence(verification, status):
    result = verdict(receipt(verification=verification))
    assert result["evidence_status"] == status
    assert result["overall_passed"] is (status == "externally_verified")


def test_artifact_failure_and_not_measured():
    assert verdict(receipt(), passed=False)["evidence_status"] == "failed"
    result = verdict(receipt(), passed=None)
    assert result["artifact_passed"] is None
    assert result["evidence_status"] == "unknown"
    assert result["overall_passed"] is False


@pytest.mark.parametrize("field", ["tokens_in", "tokens_out"])
@pytest.mark.parametrize("value", [None, 0, -1, True, False, "12", 1.5])
def test_missing_invalid_usage_preserves_raw_values(field, value):
    raw = receipt(**{field: value})
    result = verdict(raw)
    assert result["usage_status"] == "incomplete_or_unknown"
    assert result["budget_status"] == "unknown"
    assert result["overall_passed"] is False
    assert raw[field] is value


@pytest.mark.parametrize("snapshot,status", [
    (None, "unknown"), ({}, "unknown"),
    ({"limit_tokens": 100, "used_tokens": 16, "pauses": 0}, "unknown"),
    ({"limit_tokens": 100, "used_tokens": 101, "pauses": 0}, "exceeded"),
    ({"limit_tokens": 90, "used_tokens": 15, "pauses": 0}, "unknown"),
    ({"limit_tokens": 100, "used_tokens": 15, "pauses": 0, "daily_limit": 1000}, "unknown"),
    ({"limit_tokens": 100, "used_tokens": True, "pauses": 0}, "unknown"),
])
def test_budget_snapshot(snapshot, status):
    assert verdict(receipt(budget=snapshot))["budget_status"] == status


def test_usage_clear_overrun():
    result = verdict(receipt(tokens_in=101))
    assert result["budget_status"] == "exceeded"
    assert result["overall_passed"] is False


class FakeInput:
    def __init__(self, events, *, broken=False):
        self.events, self.broken = events, broken

    def write(self, data):
        self.events.append("send")
        if self.broken:
            raise BrokenPipeError

    async def drain(self):
        pass

    def close(self):
        self.events.append("stdin_closed")


class FakeOutput:
    def __init__(self, mode):
        self.mode = mode

    async def readline(self):
        if self.mode == "EOF":
            return b""
        await asyncio.Event().wait()


class FakeProcess:
    def __init__(self, events, mode="timeout"):
        self.events = events
        self.returncode = None
        self.stdin = FakeInput(events, broken=mode == "broken")
        self.stdout = FakeOutput(mode)

    def kill(self):
        self.events.append("kill")

    async def wait(self):
        await asyncio.sleep(0)
        self.returncode = -9
        self.events.append("wait_completed")
        return self.returncode


@pytest.mark.parametrize("mode", ["timeout", "EOF", "broken"])
async def test_transport_failure_waits_then_skips_acceptance_no_next_reset_send(
    monkeypatch, tmp_path, mode,
):
    events = []
    client = harness.AgentClient(FakeProcess(events, mode))
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(harness, "reset_target", lambda path: events.append("reset"))
    monkeypatch.setattr(harness, "apply_mutation", lambda *args: None)

    def forbidden_acceptance(*args):
        pytest.fail("acceptance must never inspect possibly active workspace")

    monkeypatch.setattr(harness, "run_acceptance", forbidden_acceptance)
    monkeypatch.setattr(harness.shutil, "copytree", lambda *a, **kw: pytest.fail("no archive"))
    tasks = [
        {"id": f"task{i}", "category": "bug", "prompt": "fix", "acceptance": {}}
        for i in range(6)
    ]
    args = argparse.Namespace(budget=100, timeout=0.001, model="offline", base_url="offline")
    results = await harness.run_tasks(client, tasks, workspace, tmp_path / "archive", args)
    assert events == ["reset", "send", "stdin_closed", "kill", "wait_completed"]
    assert client.stopped
    assert results[0]["acceptance"] == {
        "status": "not_run", "passed": None, "reason": "workspace_quiescence_unconfirmed",
    }
    assert results[0]["workspace_path"] == str(workspace)
    assert all(row["execution_status"] == "not_run" for row in results[1:])
    counts = harness.summarize_results(results, 6)
    assert counts["executed"] == 1 and counts["not_executed"] == 5
    assert counts["measured"] == 0 and counts["unknown"] == 6
    assert harness.evaluation_exit_code(results, 6) == 1
    report = harness.render_report(results, 6, args, "offline")
    assert "0/6" in report and "0/0" in report
    assert "6 项编程 pilot" in report and "未执行：5" in report
    assert "workspace_quiescence_unconfirmed" in report
    assert report.count("| not_run |") == 5


def test_report_distinguishes_artifact_pass_and_overall_failure():
    raw = receipt(tokens_in=0)
    result = {
        "id": "task1", "category": "bug", "receipt": raw, "rpc_error": {"message": "timeout"},
        "duration_s": 1, "acceptance": {"passed": True},
        **verdict(raw, error={"message": "timeout"}),
    }
    args = argparse.Namespace(model="offline", base_url="offline", budget=100)
    report = harness.render_report([result], 6, args, "offline")
    assert "制品验收通过率：1/6" in report
    assert "完整交付通过率：0/6" in report
    assert "总体通过率：0/6" in report
    assert "实际测量制品验收：1/1" in report
    assert "未执行：5" in report and "0(未确认)" in report
    assert "PASSED" not in report

async def test_close_escalation_awaits_exit_and_is_idempotent(monkeypatch):
    events = []
    client = harness.AgentClient(FakeProcess(events))

    async def shutdown(*args, **kwargs):
        events.append("shutdown_reply")
        return {"result": {}}

    async def expired_wait(coro, timeout):
        coro.close()
        events.append("graceful_wait_expired")
        raise TimeoutError

    monkeypatch.setattr(client, "call", shutdown)
    monkeypatch.setattr(harness.asyncio, "wait_for", expired_wait)
    await client.close()
    await client.close()
    assert events == ["shutdown_reply", "stdin_closed", "graceful_wait_expired",
                      "kill", "wait_completed"]
    assert client.stopped


@pytest.mark.parametrize("failure_method", [None, "provider.set_api_key", "model.set_current",
                                            "model.get_current", "permission.set"])
async def test_pipeline_exit_codes_and_report_without_live_calls(monkeypatch, tmp_path, failure_method):
    events = []

    class ScriptedClient:
        stopped = False

        async def call(self, method, params, **kwargs):
            events.append(method)
            if method == failure_method:
                return {"error": {"message": "offline environment failure"}}
            if method == "provider.create":
                return {"result": {"provider": {"id": "fake"}}}
            if method == "agent.send_message":
                return {"result": receipt()}
            return {"result": {}}

        async def close(self, **kwargs):
            self.stopped = True
            events.append("closed")

    monkeypatch.setattr(harness, "EVALS_DIR", tmp_path)
    monkeypatch.setattr(harness, "reset_target", lambda *a: None)
    monkeypatch.setattr(harness, "apply_mutation", lambda *a: None)
    monkeypatch.setattr(harness, "run_acceptance", lambda *a: {"passed": True, "tail": "offline"})
    monkeypatch.setattr(harness.shutil, "copytree", lambda *a, **kw: None)
    tasks = [{"id": f"task{i}", "category": "bug", "prompt": "offline", "acceptance": {}}
             for i in range(6)]
    args = argparse.Namespace(budget=100, timeout=1, model="offline", base_url="offline",
                              api_key="offline-placeholder")
    code = await harness._run_with_client(ScriptedClient(), tasks, tmp_path / "workspace", tmp_path, args)
    assert code == (2 if failure_method else 0)
    reports = list(tmp_path.glob("report-*.md"))
    if failure_method:
        assert not reports
        assert events[-1] == failure_method
    else:
        assert len(reports) == 1
        report = reports[0].read_text(encoding="utf-8")
        assert "总体通过率：6/6" in report
        assert "未执行：0" in report
        assert events[-1] == "closed"
