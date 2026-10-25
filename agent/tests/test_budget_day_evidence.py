"""Whole-day evidence through actual SQLite and budget.get, without paid models."""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from minimax_code.budget_ledger import BudgetHandle
from minimax_code.config import Config
from minimax_code.ipc.handlers_budget import register_budget_handlers
from minimax_code.ipc.server import IPCServer
from minimax_code.storage.dao.runs import AgentRunsDAO
from minimax_code.storage.dao.sessions import SessionsDAO
from minimax_code.storage.db import AsyncDatabase
from test_eval_verdict import harness, modern_receipt
from test_run_service import Context


@pytest.fixture
async def daily(tmp_path, monkeypatch):
    from minimax_code import app
    from minimax_code.ipc import handlers_budget
    from minimax_code.storage.dao import budgets

    db = AsyncDatabase(tmp_path / "day-evidence.db")
    await db.connect()
    await db.migrate()
    await db.execute("UPDATE budget_coverage SET started_at='2026-10-01T00:00:00+00:00' WHERE id=1")
    monkeypatch.setattr(budgets, "reservation_day", lambda: "2026-10-26")
    monkeypatch.setattr(app, "get_db", lambda: db)
    monkeypatch.setattr(handlers_budget, "daily_budget_limit", lambda: 100)
    runs, handles, records = AgentRunsDAO(db), {}, {}
    for sid in ("root", "other"):
        await SessionsDAO(db).create(id=sid)
        run, _ = await runs.admit_run(session_id=sid, request={"content": sid}, title=sid,
            metadata={"budget": {"limit_tokens": 100}}, request_key=sid)
        records[sid] = run
        handles[sid] = await BudgetHandle.for_run(db, run["id"], daily_limit=100)
    server = IPCServer(Config())
    register_budget_handlers(server)
    try:
        yield SimpleNamespace(db=db, runs=runs, handles=handles, records=records, server=server)
    finally:
        await db.close()


async def attempt(state, owner="root", *, day=None, estimate=0):
    handle = state.handles[owner]
    return await handle.dao.reserve(family_id=handle.family_id, run_id=state.records[owner]["id"],
        provider="offline", model="wire", estimate=estimate, daily_limit=100, day=day)


async def rpc(state, *, owner="root", day=None):
    ctx = Context(state.server)
    params = {"run_id": state.records[owner]["id"]}
    if day is not None:
        params["day"] = day
    await state.server._handlers["budget.get"](params, ctx)
    ctx.reply_error.assert_not_awaited()
    return ctx.reply.await_args.args[0]


def completeness(day, *, resolved, measured):
    assert day["accounting_resolved"] is resolved
    assert day["usage_complete"] is measured


async def test_empty_reserved_sent_settled_and_released_days(daily):
    root = daily.handles["root"]
    completeness((await rpc(daily))["day"], resolved=True, measured=True)
    pending = await attempt(daily)
    day = (await rpc(daily))["day"]
    assert day["reserved_tokens"] == 0
    completeness(day, resolved=True, measured=False)
    await root.dao.sent(pending)
    completeness((await rpc(daily))["day"], resolved=True, measured=False)
    await root.dao.settle(pending, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    completeness((await rpc(daily))["day"], resolved=True, measured=True)
    unsent = await attempt(daily, estimate=10)
    completeness((await rpc(daily))["day"], resolved=True, measured=False)
    await root.dao.release(unsent, reason="provably not sent")
    completeness((await rpc(daily))["day"], resolved=True, measured=True)


@pytest.mark.parametrize("state", ["sent", "unknown", "reconciled"])
async def test_other_family_zero_amounts_do_not_certify_whole_day(daily, state):
    root, other = daily.handles["root"], daily.handles["other"]
    paid = await attempt(daily, estimate=20)
    await root.dao.sent(paid)
    await root.dao.settle(paid, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    hidden = await attempt(daily, "other")
    await other.dao.sent(hidden)
    if state != "sent":
        await other.dao.settle(hidden, {}, complete=False)
    if state == "reconciled":
        await daily.runs.update_run_status(daily.records["other"]["id"], status="interrupted")
        await other.dao.reconcile(target="attempt", target_id=hidden, liability_tokens=0,
                                  reason="authorized liability, usage not measured")
    result = await rpc(daily)
    assert result["budget"]["usage_complete"] is True  # Current family is complete.
    assert len(result["attempts"]) == 1  # Foreign family is absent from this detail list.
    assert result["day"]["known_tokens"] == 15 and result["day"]["reserved_tokens"] == 0
    completeness(result["day"], resolved=state != "unknown", measured=False)
    raw = modern_receipt(run_id=daily.records["root"]["id"], budget=result["budget"])
    assert harness.budget_status(raw, 100, result) == ("unresolved" if state == "unknown" else "incomplete")


async def test_unknown_settlement_can_restore_measurement_but_reconciliation_cannot(daily):
    root = daily.handles["root"]
    pending = await attempt(daily)
    await root.dao.sent(pending)
    await root.dao.settle(pending, {}, complete=False)
    completeness((await rpc(daily))["day"], resolved=False, measured=False)
    await root.dao.settle(pending, {"total_tokens": 0})
    completeness((await rpc(daily))["day"], resolved=True, measured=True)


async def test_historical_authorization_is_resolved_but_not_measured(daily):
    dao = daily.handles["root"].dao
    before = [dict(row) for row in await daily.db.fetchall("SELECT * FROM budget_days")]
    completeness((await rpc(daily, day="2000-01-01"))["day"], resolved=False, measured=False)
    assert [dict(row) for row in await daily.db.fetchall("SELECT * FROM budget_days")] == before
    await dao.reconcile(target="day", target_id="2000-01-01", liability_tokens=0,
                        reason="reviewed incomplete historical records")
    day = (await rpc(daily, day="2000-01-01"))["day"]
    assert day["historical_liability"] == 0 and not day["baseline_incomplete"]
    completeness(day, resolved=True, measured=False)
    completeness((await rpc(daily, day="2026-10-26"))["day"], resolved=True, measured=True)


async def test_actual_reservation_day_isolation_and_evaluator_rpc_readback(daily):
    root, other = daily.handles["root"], daily.handles["other"]
    prior = await attempt(daily, day="2026-10-25", estimate=20)
    await root.dao.sent(prior)
    await root.dao.settle(prior, {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15})
    hidden = await attempt(daily, "other", day="2026-10-26")
    await other.dao.sent(hidden)
    completeness((await rpc(daily, day="2026-10-25"))["day"], resolved=True, measured=True)
    completeness((await rpc(daily, day="2026-10-26"))["day"], resolved=True, measured=False)

    class Client:
        async def call(self, method, params):
            assert method == "budget.get"
            return {"result": await rpc(daily, day=params.get("day"))}

    result = (await harness.collect_budget_readback(Client(), daily.records["root"]["id"]))["result"]
    raw = modern_receipt(run_id=daily.records["root"]["id"], budget=result["budget"])
    # Root only spent on the prior day; today's different family cannot pollute it.
    assert harness.budget_status(raw, 100, result) == "reported_within_limit"
    assert result["days"]["2026-10-25"]["known_tokens"] == 15
    legacy = {**result, "days": {}}
    assert harness.budget_status(raw, 100, legacy) == "unknown"
