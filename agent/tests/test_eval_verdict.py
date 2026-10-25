"""Offline eval verdict and timeout safety regression tests (no real model)."""

from __future__ import annotations

import argparse
import asyncio
import copy
import importlib.util
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "eval_harness", Path(__file__).resolve().parents[2] / "evals" / "run_eval.py"
)
assert SPEC is not None and SPEC.loader is not None
harness = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(harness)

PROFILE = {"id": "single-agent-pilot-v1", "verified": True, "builtin_spawn_subagent": "deny"}


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
    args = argparse.Namespace(budget=100, timeout=0.001, model="offline", base_url="offline",
                              evaluation_profile=PROFILE)
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
                                            "model.get_current", "permission.set", "permission.check"])
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
            if method == "permission.check":
                return {"result": {"allowed": False, "action": "deny"}}
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
        assert events.count("agent.send_message") == len(tasks)  # No paid key probe.
        assert events.index("permission.check") < events.index("agent.send_message")


def modern_receipt(**changes):
    defaults = dict(run_id="root", run_status="completed", budget={
        "family_id": "family", "limit_tokens": 100, "known_tokens": 15, "reserved_tokens": 0,
        "unknown_attempts": 0, "baseline_incomplete": False, "accounting_resolved": True,
        "usage_complete": True, "enforcement": "estimated", "daily_limit": None,
    }, verification={"version": 1, "verdict": "unknown", "reason": "not_configured",
                     "requires_review": False})
    defaults.update(changes)
    return receipt(**defaults)


@pytest.mark.parametrize("status", ["failed", "interrupted", "cancelled", "budget_paused",
                                    "queued", "planning", "running", "awaiting_approval", "waiting_input"])
def test_explicit_run_state_overrides_success_looking_flags(status):
    result = verdict(modern_receipt(run_status=status))
    assert result["execution_status"] == status and not result["overall_passed"]


@pytest.mark.parametrize("updates", [{"run_status": []}, {"run_status": None},
    {"run_status": "unrecognized"}, {"run_status": "completed", "cancelled": True},
    {"run_status": "completed", "budget_paused": True}, {"cancelled": "false"}])
def test_contradictory_or_malformed_modern_completion_never_passes(updates):
    raw = modern_receipt()
    raw.update(updates)
    assert not verdict(raw)["overall_passed"]


@pytest.mark.parametrize("version,verdict_name,reason,validation,status", [
    (1, "passed", "validation_passed", {"exit_code": 0, "ok": True}, "externally_verified"),
    (1, "failed", "validation_failed", None, "failed"),
    (1, "passed", "validation_passed", None, "unknown"),
    (1, "unknown", "not_configured", None, "externally_verified"),
    (1, "unknown", "engine_validation_not_supported", None, "externally_verified"),
    (1, "unknown", "validation_missing", None, "unknown"),
    (1, "unknown", "validation_timeout", {"ok": False, "timed_out": True}, "requires_review"),
    (1, "passed", "validation_passed", {"ok": True, "exit_code": 1}, "requires_review"),
    (1, "passed", "validation_passed", {"ok": True, "exit_code": False}, "unknown"),
    (1, "not_applicable", "plan_mode", None, "unknown"),
    (2, "passed", "validation_passed", {"ok": True, "exit_code": 0}, "unknown"),
])
def test_versioned_verification_is_distinct_from_external_artifact(version, verdict_name, reason, validation, status):
    verification = {"version": version, "verdict": verdict_name, "reason": reason,
                    "requires_review": False}
    if validation is not None:
        verification["validation"] = validation
    result = verdict(modern_receipt(verification=verification))
    assert result["evidence_status"] == status
    assert result["product_verification"]["reason"] == reason
    assert result["overall_passed"] is (status == "externally_verified")


@pytest.mark.parametrize("updates,status", [
    ({}, "reported_within_limit"),
    ({"known_tokens": 25}, "reported_within_limit"),  # Child spending exceeds root usage.
    ({"known_tokens": 100}, "reported_within_limit"),  # Exact cap still within limit.
    ({"known_tokens": 101}, "exceeded"),
    ({"known_tokens": 90, "reserved_tokens": 11}, "liability_exceeded"),
    ({"reserved_tokens": 0, "enforcement": "reactive", "usage_complete": False}, "incomplete"),
    ({"unknown_attempts": 1, "accounting_resolved": False, "usage_complete": False}, "unresolved"),
    ({"baseline_incomplete": True, "accounting_resolved": False}, "unresolved"),
    ({"reserved_tokens": 5, "accounting_resolved": True, "usage_complete": False}, "incomplete"),
    ({"usage_complete": False, "accounting_resolved": True}, "incomplete"),
    ({"known_tokens": 14}, "unknown"),
    ({"accounting_resolved": False}, "unknown"),
    ({"limit_tokens": 90}, "unknown"),
    ({"known_tokens": True}, "unknown"),
    ({"unknown_attempts": -1}, "unknown"),
    ({"enforcement": []}, "unknown"),
])
def test_schema037_family_accounting(updates, status):
    raw = modern_receipt()
    raw["budget"].update(updates)
    result = verdict(raw)
    assert result["budget_status"] == status
    assert result["budget_evidence"]["monetary_cost"] is None
    assert result["overall_passed"] is (status == "reported_within_limit")


def test_modern_does_not_fall_back_to_legacy_or_hide_overrun_on_readback_error():
    raw = receipt(budget={"limit_tokens": 100, "used_tokens": 15, "pauses": 0, "known_tokens": None})
    assert verdict(raw)["budget_status"] == "unknown"
    raw = modern_receipt()
    raw["budget"]["known_tokens"] = 101
    assert harness.budget_status(raw, 100, {"error": {"message": "offline"}}) == "exceeded"


def test_zero_requires_observed_provenance_and_recovery_usage_stays_interval():
    raw = modern_receipt(tokens_out=0)
    raw["budget"]["known_tokens"] = 10
    assert harness.usage_status(raw) == "incomplete_or_unknown"
    observed = {**raw, "engine": "codex", "usage_certainty": "observed"}
    assert harness.usage_status(observed) == "reported"
    assert harness.usage_status({**observed, "usage_certainty": "recovery_interval"}) == "interval_reported"
    readback = {"attempts": [{"run_id": "root", "state": "settled", "usage": {
        "prompt_tokens": 10, "completion_tokens": 0, "total_tokens": 10, "complete": True}}]}
    assert harness.usage_status(raw, readback) == "reported"
    readback["attempts"][0]["usage"].pop("completion_tokens")
    assert harness.usage_status(raw, readback) == "incomplete_or_unknown"


def daily_readback(raw):
    day = {"known_tokens": 15, "reserved_tokens": 0, "unknown_attempts": 0,
           "baseline_incomplete": False, "historical_liability": None,
           "usage_complete": True, "accounting_resolved": True}
    return {"budget": raw["budget"], "attempts": [
        {"run_id": "root", "state": "settled", "reservation_day": "2026-10-25"},
        {"run_id": "root", "state": "settled", "reservation_day": "2026-10-26"},
    ], "day": {**day, "day": "2026-10-26"},
        "days": {"2026-10-25": {**day, "day": "2026-10-25"}}}


@pytest.mark.parametrize("change,status", [
    ({}, "reported_within_limit"),
    ({"known_tokens": 101}, "exceeded"),
    ({"known_tokens": 90, "reserved_tokens": 11}, "liability_exceeded"),
    ({"unknown_attempts": 1}, "unresolved"),
    ({"baseline_incomplete": True}, "unresolved"),
    ({"reserved_tokens": 1}, "incomplete"),
    ({"historical_liability": 0}, "incomplete"),
    ({"usage_complete": False}, "incomplete"),
    ({"accounting_resolved": False}, "unknown"),
])
def test_daily_accounting_uses_every_actual_reservation_day(change, status):
    raw = modern_receipt()
    raw["budget"]["daily_limit"] = 100
    readback = daily_readback(raw)
    readback["days"]["2026-10-25"].update(change)
    assert harness.budget_status(raw, 100, readback) == status
    readback["days"] = {}
    assert harness.budget_status(raw, 100, readback) == "unknown"  # Today's low usage is insufficient.


@pytest.mark.parametrize("profile", [None, {}, {**PROFILE, "verified": 1}])
async def test_unverified_profile_stops_before_reset_or_model(monkeypatch, tmp_path, profile):
    events = []
    client = harness.AgentClient(FakeProcess(events))
    monkeypatch.setattr(harness, "reset_target", lambda path: pytest.fail("no reset"))
    args = argparse.Namespace(budget=100, evaluation_profile=profile)
    results = await harness.run_tasks(client, [{"id": "a", "category": "bug"}], tmp_path / "w", tmp_path / "a", args)
    assert events == ["stdin_closed", "kill", "wait_completed"]
    assert results[0]["execution_status"] == "not_run"


async def test_descendant_evidence_stops_before_acceptance_archive_or_next_task(monkeypatch, tmp_path):
    events = []
    class Client:
        stopped = False

        async def call(self, method, params, **kwargs):
            events.append(method)
            return {"result": receipt(verification={"children": [{"status": "running"}]})}

        async def close(self, **kwargs):
            events.append("closed")
            self.stopped = True

    monkeypatch.setattr(harness, "reset_target", lambda path: events.append("reset"))
    monkeypatch.setattr(harness, "apply_mutation", lambda *args: None)
    monkeypatch.setattr(harness, "run_acceptance", lambda *args: pytest.fail("no acceptance"))
    monkeypatch.setattr(harness.shutil, "copytree", lambda *args, **kwargs: pytest.fail("no archive"))
    args = argparse.Namespace(budget=100, timeout=1, evaluation_profile=PROFILE)
    tasks = [{"id": str(i), "category": "bug", "prompt": "work", "acceptance": {}} for i in range(2)]
    results = await harness.run_tasks(Client(), tasks, tmp_path / "w", tmp_path / "a", args)
    assert events == ["reset", "agent.send_message", "closed"]
    assert results[0]["acceptance"]["reason"] == "workspace_quiescence_unconfirmed"
    assert results[1]["execution_status"] == "not_run"


async def test_actual_sqlite_parent_child_accounting_and_unknown_reconciliation(tmp_path):
    from minimax_code.budget_ledger import BudgetHandle
    from minimax_code.storage.dao.runs import AgentRunsDAO
    from minimax_code.storage.dao.sessions import SessionsDAO
    from minimax_code.storage.db import AsyncDatabase

    db = AsyncDatabase(tmp_path / "eval.db")
    await db.connect()
    try:
        await db.migrate()
        await SessionsDAO(db).create(id="eval")
        runs = AgentRunsDAO(db)
        run, _ = await runs.admit_run(session_id="eval", request={"mode": "chat", "content": "work"},
            title="work", metadata={"budget": {"limit_tokens": 100}}, request_key="once")
        handle = await BudgetHandle.for_run(db, run["id"])
        for owner, prompt, completion in ((run["id"], 10, 5), ("child", 7, 3)):
            aid = await handle.dao.reserve(family_id=handle.family_id, run_id=owner, provider="offline",
                model="offline", estimate=20)
            await handle.dao.sent(aid)
            await handle.dao.settle(aid, {"prompt_tokens": prompt, "completion_tokens": completion,
                                         "total_tokens": prompt + completion})
        raw = modern_receipt()
        raw.update(run_id=run["id"], budget=await handle.snapshot())
        result = verdict(raw)
        assert result["budget_status"] == "reported_within_limit"
        assert result["budget_evidence"]["family"]["known_tokens"] == 25
        aid = await handle.dao.reserve(family_id=handle.family_id, run_id=run["id"], provider="offline",
            model="offline", estimate=10)
        await handle.dao.sent(aid)
        await handle.dao.settle(aid, {}, complete=False)
        raw["budget"] = await handle.snapshot()
        assert verdict(raw)["budget_status"] == "unresolved"
        await runs.update_run_status(run["id"], status="interrupted")
        await handle.dao.reconcile(target="attempt", target_id=aid, liability_tokens=8, reason="offline audit")
        raw["budget"] = await handle.snapshot()
        assert raw["budget"]["accounting_resolved"] and not raw["budget"]["usage_complete"]
        assert verdict(raw)["budget_status"] == "incomplete"
    finally:
        await db.close()


@pytest.mark.parametrize("checked", [None, {}, {"allowed": True, "action": "allow"},
    {"allowed": False, "action": "ask"}, {"allowed": 0, "action": "deny"}])
async def test_profile_denial_must_be_confirmed_before_any_task(checked, tmp_path):
    events = []

    class Client:
        async def call(self, method, params, **kwargs):
            events.append((method, params))
            if method == "provider.create":
                return {"result": {"provider": {"id": "offline"}}}
            if method == "permission.check":
                return {"result": checked}
            assert method != "agent.send_message", "Unverified profile must not send model work"
            return {"result": {}}

    args = argparse.Namespace(model="offline", base_url="offline", api_key="offline")
    assert await harness._run_with_client(Client(), [], tmp_path, tmp_path, args) == 2
    assert events[-1][0] == "permission.check"
    assert not list(tmp_path.glob("report-*.md"))


@pytest.mark.parametrize("change,expected", [
    ({"daily_limit": None}, "unknown"),
    ({"known_tokens": 14}, "unknown"),
    ({"family_id": "other"}, "unknown"),
    ({"limit_tokens": 10, "known_tokens": 15}, "exceeded"),
    ({"known_tokens": 25}, "reported_within_limit"),
])
def test_readback_cannot_change_context_or_decrease_measured_usage(change, expected):
    raw = modern_receipt()
    raw["budget"]["daily_limit"] = 100
    readback = daily_readback(raw)
    readback["budget"] = {**raw["budget"], **change}
    assert harness.budget_status(raw, 100, readback) == expected


@pytest.mark.parametrize("attempts", [None, [], [None], [{}], [{"reservation_day": []}],
    [{"reservation_day": "not-a-date"}], [{"state": "released", "reservation_day": "2026-10-26"}]])
def test_malformed_or_absent_daily_dates_never_certify_limit(attempts):
    raw = modern_receipt()
    raw["budget"]["daily_limit"] = 100
    readback = {"budget": raw["budget"], "attempts": attempts}
    assert harness.budget_status(raw, 100, readback) == "unknown"


@pytest.mark.parametrize("mode", ["normal", "rpc_error", "wrong_family", "wrong_day"])
async def test_cross_midnight_collects_actual_prior_day_and_retains_raw_readback(mode):
    raw = modern_receipt()
    raw["budget"]["daily_limit"] = 100
    complete = daily_readback(raw)
    initial = {key: value for key, value in complete.items() if key != "days"}
    events = []

    class Client:
        async def call(self, method, params, **kwargs):
            events.append((method, params))
            if "day" not in params:
                return {"result": copy.deepcopy(initial)}
            if mode == "rpc_error":
                return {"error": {"message": "offline read failure"}}
            result = {"budget": copy.deepcopy(raw["budget"]),
                      "day": copy.deepcopy(complete["days"]["2026-10-25"])}
            if mode == "wrong_family":
                result["budget"]["family_id"] = "other"
            if mode == "wrong_day":
                result["day"]["day"] = "2026-10-26"
            return {"result": result}

    response = await harness.collect_budget_readback(Client(), "root")
    assert events == [("budget.get", {"run_id": "root"}),
                      ("budget.get", {"run_id": "root", "day": "2026-10-25"})]
    if mode == "rpc_error":
        assert "error" in response
        assert response["partial_readback"]["day_readbacks"]["2026-10-25"]["error"]
    else:
        readback = response["result"]
        assert "2026-10-25" in readback["day_readbacks"]
        assert harness.budget_status(raw, 100, readback) == (
            "reported_within_limit" if mode == "normal" else "unknown")


async def test_pipeline_reports_observed_zero_and_schema037_without_paid_probe(monkeypatch, tmp_path):
    calls = []
    raw = modern_receipt(engine="codex", usage_certainty="observed", tokens_in=0, tokens_out=0)
    raw["budget"]["known_tokens"] = 0

    class Client:
        stopped = False

        async def call(self, method, params, **kwargs):
            calls.append(method)
            if method == "provider.create":
                return {"result": {"provider": {"id": "offline"}}}
            if method == "permission.check":
                return {"result": {"action": "deny", "allowed": False}}
            if method == "agent.send_message":
                return {"result": raw}
            if method == "budget.get":
                return {"result": {"budget": raw["budget"], "attempts": []}}
            return {"result": {}}

        async def close(self, **kwargs):
            self.stopped = True

    monkeypatch.setattr(harness, "EVALS_DIR", tmp_path)
    monkeypatch.setattr(harness, "reset_target", lambda *a: None)
    monkeypatch.setattr(harness, "apply_mutation", lambda *a: None)
    monkeypatch.setattr(harness, "run_acceptance", lambda *a: {"passed": True})
    monkeypatch.setattr(harness.shutil, "copytree", lambda *a, **kw: None)
    args = argparse.Namespace(budget=100, timeout=1, model="offline", base_url="offline", api_key="offline")
    tasks = [{"id": "one", "category": "bug", "prompt": "work", "acceptance": {}}]
    assert await harness._run_with_client(Client(), tasks, tmp_path / "w", tmp_path, args) == 0
    assert calls.count("agent.send_message") == 1 and calls.count("budget.get") == 1
    report = next(tmp_path.glob("report-*.md")).read_text(encoding="utf-8")
    assert "0(未确认)" not in report
    assert "reported_within_limit" in report and '"monetary_cost": null' in report
    assert '"budget_readback"' in report and '"enforcement": "estimated"' in report
    assert '"builtin_spawn_subagent": "deny"' in report


@pytest.mark.parametrize("state", ["sent", "reconciled"])
async def test_actual_sqlite_other_family_zero_liability_is_not_daily_completion(tmp_path, state):
    from minimax_code.budget_ledger import BudgetHandle
    from minimax_code.storage.dao.runs import AgentRunsDAO
    from minimax_code.storage.dao.sessions import SessionsDAO
    from minimax_code.storage.db import AsyncDatabase

    db = AsyncDatabase(tmp_path / "daily.db")
    await db.connect()
    try:
        await db.migrate()
        await SessionsDAO(db).create(id="daily")
        runs = AgentRunsDAO(db)
        handles, rows = [], []
        for name in ("root", "other"):
            run, _ = await runs.admit_run(session_id="daily", request={"content": name},
                title=name, metadata={"budget": {"limit_tokens": 100}}, request_key=name)
            rows.append(run)
            handles.append(await BudgetHandle.for_run(db, run["id"], daily_limit=100))
        root, other = handles
        aid = await root.dao.reserve(family_id=root.family_id, run_id=rows[0]["id"],
            provider="offline", model="offline", estimate=20, daily_limit=100)
        await root.dao.sent(aid)
        await root.dao.settle(aid, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
        hidden = await other.dao.reserve(family_id=other.family_id, run_id=rows[1]["id"],
            provider="offline", model="offline", estimate=0, daily_limit=100)
        await other.dao.sent(hidden)
        if state == "reconciled":
            await other.dao.settle(hidden, {}, complete=False)
            await runs.update_run_status(rows[1]["id"], status="interrupted")
            await other.dao.reconcile(target="attempt", target_id=hidden, liability_tokens=0,
                                      reason="offline audit without measured usage")
        raw = modern_receipt(run_id=rows[0]["id"], budget=await root.snapshot())
        day = (await root.dao.snapshot(root.family_id))["day"]
        assert day["known_tokens"] == 15 and day["reserved_tokens"] == day["unknown_attempts"] == 0
        assert raw["budget"]["accounting_resolved"] and raw["budget"]["usage_complete"]
        readback = {"budget": raw["budget"], "day": day, "attempts": await root.dao.attempts(root.family_id)}
        # Current product day summaries cannot prove completion across families.
        assert harness.budget_status(raw, 100, readback) == "unknown"
    finally:
        await db.close()


@pytest.mark.parametrize("updates", [
    {"run_status": "running"}, {"run_status": "queued"}, {"run_status": "awaiting_approval"},
    {"run_status": "waiting_input"}, {"run_status": "interrupted"}, {"run_status": None},
    {"run_status": {}}, {"run_status": "unknown"}, {"run_status": "running", "error": {"message": "failure"}},
    {"run_status": {}, "error": {"message": "failure"}},
    {"run_status": "completed", "cancelled": True},
])
async def test_nonterminal_receipt_never_inspects_or_reuses_workspace(monkeypatch, tmp_path, updates):
    events = []

    class Client:
        stopped = False

        async def call(self, method, params, **kwargs):
            events.append(method)
            return {"result": receipt(**updates)}

        async def close(self, **kwargs):
            events.append("closed")
            self.stopped = True

    monkeypatch.setattr(harness, "reset_target", lambda *args: events.append("reset"))
    monkeypatch.setattr(harness, "apply_mutation", lambda *args: None)
    monkeypatch.setattr(harness, "run_acceptance", lambda *args: pytest.fail("no acceptance"))
    monkeypatch.setattr(harness.shutil, "copytree", lambda *args, **kwargs: pytest.fail("no archive"))
    tasks = [{"id": str(i), "category": "bug", "prompt": "work", "acceptance": {}} for i in range(2)]
    args = argparse.Namespace(budget=100, timeout=1, evaluation_profile=PROFILE)
    result = await harness.run_tasks(Client(), tasks, tmp_path / "w", tmp_path / "a", args)
    assert events == ["reset", "agent.send_message", "closed"]
    assert result[0]["acceptance"]["reason"] == "workspace_quiescence_unconfirmed"
    assert result[1]["execution_status"] == "not_run"
