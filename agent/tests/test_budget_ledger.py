"""Offline durable liability invariants, including independent SQLite writers."""
from __future__ import annotations

import asyncio

import pytest

from minimax_code.budget import BudgetExhausted
from minimax_code.budget_ledger import BudgetHandle, budget_scope, estimate_request, normalize_usage
from minimax_code.storage.dao.budgets import BudgetsDAO
from minimax_code.storage.db import AsyncDatabase


@pytest.fixture
async def ledger(tmp_path):
    path = tmp_path / "budget.db"
    db = AsyncDatabase(path)
    await db.connect()
    await db.migrate()
    async with db.transaction() as conn:
        for fid in ("family", "other"):
            await conn.execute("INSERT INTO budget_families(id,root_run_id,cap_tokens,created_at) VALUES(?,?,1000,'now')", (fid, fid))
    try:
        yield db, BudgetsDAO(db), path
    finally:
        await db.close()


async def reserve(dao, *, family="family", estimate=100, day=None, daily_limit=None):
    return await dao.reserve(family_id=family, run_id="run", provider="test", model="test",
                             estimate=estimate, day=day, daily_limit=daily_limit)


async def test_two_connections_reserve_last_daily_quota_once(ledger):
    db, dao, path = ledger
    other = AsyncDatabase(path)
    await other.connect()
    try:
        results = await asyncio.gather(reserve(dao, estimate=80, daily_limit=100),
            reserve(BudgetsDAO(other), family="other", estimate=80, daily_limit=100), return_exceptions=True)
        assert sum(isinstance(result, str) for result in results) == 1
        assert sum(isinstance(result, BudgetExhausted) for result in results) == 1
        assert (await dao.snapshot())["day"]["reserved_tokens"] == 80
    finally:
        await other.close()


async def test_settlement_idempotent_actual_over_estimate_is_truth(ledger):
    _, dao, _ = ledger
    aid = await reserve(dao, estimate=10)
    await dao.sent(aid)
    assert await dao.settle(aid, {"total_tokens": 1200})
    assert not await dao.settle(aid, {"total_tokens": 1200})
    family = (await dao.snapshot("family"))["family"]
    assert family["known_tokens"] == 1200 and family["pause_reason"] == "family_ceiling"
    with pytest.raises(BudgetExhausted):
        await reserve(dao, estimate=1)


@pytest.mark.parametrize("state", ["reserved", "sent", "unknown"])
async def test_reopen_preserves_outstanding_liability(ledger, state):
    db, dao, _ = ledger
    aid = await reserve(dao, estimate=120)
    if state != "reserved":
        await dao.sent(aid)
    if state == "unknown":
        await dao.settle(aid, {"prompt_tokens": 25}, complete=False)
    await db.close()
    await db.connect()
    if state != "unknown":
        # Startup recovery precedes admission. Reserved is provably unsent;
        # persisted sent has an unknown outcome across this process boundary.
        await dao.recover()
    snapshot = await dao.snapshot("family")
    assert snapshot["family"]["known_tokens"] + snapshot["family"]["reserved_tokens"] == (0 if state == "reserved" else 120)
    assert snapshot["family"]["unknown_attempts"] == int(state != "reserved")
    if state != "reserved":
        with pytest.raises(BudgetExhausted, match="unresolved_attempt"):
            await reserve(dao)
        with pytest.raises(ValueError, match="provably unsent"):
            await dao.release(aid, reason="cannot prove")


async def test_release_only_unsent_and_reconcile_does_not_forge_usage(ledger):
    _, dao, _ = ledger
    first = await reserve(dao)
    await dao.release(first, reason="local validation before send")
    second = await reserve(dao)
    await dao.sent(second)
    await dao.settle(second, {"prompt_tokens": 30}, complete=False)
    with pytest.raises(ValueError):
        await dao.reconcile(target="attempt", target_id=second, liability_tokens=20, reason="too low")
    await dao.reconcile(target="attempt", target_id=second, liability_tokens=80, reason="operator authorization")
    row = (await dao.attempts("family"))[1]
    assert row["state"] == "reconciled" and row["known_tokens"] == 30
    assert row["provenance"] == "reconciled" and row["liability_tokens"] == 80
    family = (await dao.snapshot("family"))["family"]
    assert family["known_tokens"] == 30 and family["reserved_tokens"] == 50
    assert family["accounting_resolved"] and not family["usage_complete"]


async def test_active_sent_reservations_allow_parallel_children_and_other_families(ledger):
    _, dao, _ = ledger
    aid = await reserve(dao, estimate=100, daily_limit=1000)
    await dao.sent(aid)
    assert await reserve(dao, estimate=100, daily_limit=1000)
    assert await reserve(dao, family="other", estimate=100, daily_limit=1000)
    state = await dao.snapshot("family")
    assert state["family"]["unknown_attempts"] == 0
    assert not state["family"]["usage_complete"]


async def test_midnight_keeps_settlement_on_reservation_day(ledger):
    _, dao, _ = ledger
    aid = await reserve(dao, day="2026-10-26")
    await dao.sent(aid)
    await dao.settle(aid, {"total_tokens": 70})
    assert (await dao.snapshot(day="2026-10-26"))["day"]["known_tokens"] == 70
    assert (await dao.snapshot(day="2026-10-27"))["day"]["known_tokens"] == 0


async def test_prior_day_unknown_holds_family_but_not_fresh_future_day(ledger):
    _, dao, _ = ledger
    aid = await reserve(dao, day="2026-10-25")
    await dao.sent(aid)
    await dao.settle(aid, {}, complete=False)
    with pytest.raises(BudgetExhausted):
        await reserve(dao, day="2026-10-26", daily_limit=1000)
    assert await reserve(dao, family="other", day="2026-10-26", daily_limit=1000)


async def test_unresolved_historical_baseline_fails_closed_until_authorized(ledger):
    db, dao, _ = ledger
    async with db.transaction() as conn:
        await conn.execute("INSERT OR REPLACE INTO budget_days(day,known_lower_bound,incomplete) VALUES('2026-10-25',30,1)")
    with pytest.raises(BudgetExhausted, match="daily_accounting_incomplete"):
        await reserve(dao, day="2026-10-25", daily_limit=1000)
    await dao.reconcile(target="day", target_id="2026-10-25", liability_tokens=90, reason="historical authorization")
    with pytest.raises(ValueError):
        await dao.reconcile(target="day", target_id="2026-10-25", liability_tokens=30, reason="cannot erase authorization")
    state = (await dao.snapshot(day="2026-10-25"))["day"]
    assert state["known_tokens"] == 30 and state["reserved_tokens"] == 60
    assert await reserve(dao, day="2026-10-25", daily_limit=1000)


@pytest.mark.parametrize("usage,total,complete", [
    ({}, None, False), ({"prompt_tokens": 5}, 5, False),
    ({"prompt_tokens": 5, "completion_tokens": 2, "reasoning_tokens": 2, "cached_tokens": 4}, 7, True),
    ({"total_tokens": 0}, 0, True),
    ({"prompt_tokens": None, "completion_tokens": 2}, 2, False),
    ({"prompt_tokens": 0}, 0, False),
])
def test_normalization_preserves_unknown_and_does_not_double_count(usage, total, complete):
    result = normalize_usage(usage)
    assert result["known_tokens"] == total and result["complete"] is complete


def test_estimate_uses_current_serialized_request_not_previous_prompt():
    small = estimate_request({"messages": [{"role": "user", "content": "a"}], "max_tokens": 10})
    large = estimate_request({"messages": [{"role": "user", "content": "a" * 1000}], "tools": [{"large": "b" * 500}], "max_tokens": 10})
    assert large > small + 1000
    with pytest.raises(BudgetExhausted):
        estimate_request({"messages": []})


async def test_handle_is_inherited_recursively_without_copying_balance(ledger):
    from minimax_code.budget_ledger import current_handle
    _, dao, _ = ledger
    handle = BudgetHandle(dao, "family", "parent")
    async def child():
        assert current_handle().family_id == "family"
        await reserve(current_handle().dao, estimate=100)
    with budget_scope(handle):
        await asyncio.create_task(child())
    assert (await handle.snapshot())["reserved_tokens"] == 100


@pytest.mark.parametrize("history", ["none", "assistant_unknown", "assistant_known", "started"])
async def test_migration_distinguishes_clean_day_from_unmeasured_history(tmp_path, monkeypatch, history):
    import minimax_code.storage.db as module
    from minimax_code.storage.dao._base import now_iso
    from minimax_code.storage.dao.runs import AgentRunsDAO
    from minimax_code.storage.dao.sessions import SessionsDAO
    discover = module.discover_migrations
    monkeypatch.setattr(module, "discover_migrations", lambda applied: [(v, f) for v, f in discover(applied) if v < 37])
    db = AsyncDatabase(tmp_path / "legacy.db")
    await db.connect()
    try:
        await db.migrate()
        await SessionsDAO(db).create(id="s")
        if history.startswith("assistant"):
            await db.execute("INSERT INTO messages(id,session_id,role,content,created_at,tokens_in,tokens_out) VALUES('old','s','assistant','old',?,?,?)",
                (now_iso(), 12 if history == "assistant_known" else 0, 3 if history == "assistant_known" else 0))
        if history == "started":
            run = await AgentRunsDAO(db).create_run(session_id="s", mode="chat", status="interrupted")
            await db.execute("UPDATE agent_runs SET started_at=? WHERE id=?", (now_iso(), run["id"]))
        monkeypatch.setattr(module, "discover_migrations", discover)
        await db.migrate()
        snapshot = await BudgetsDAO(db).snapshot()
        assert snapshot["day"]["baseline_incomplete"] is (history != "none")
        assert snapshot["day"]["known_tokens"] == (15 if history == "assistant_known" else 0)
        assert not (await BudgetsDAO(db).snapshot(day="2099-01-01"))["day"]["baseline_incomplete"]
    finally:
        await db.close()


async def test_reading_uncovered_historical_day_is_unknown_without_mutating_coverage(ledger):
    db, dao, _ = ledger
    before = await db.fetchall("SELECT * FROM budget_days")
    past = (await dao.snapshot(day="2000-01-01"))["day"]
    assert past["baseline_incomplete"] and past["historical_liability"] is None
    assert [dict(row) for row in await db.fetchall("SELECT * FROM budget_days")] == [dict(row) for row in before]
    assert not (await dao.snapshot(day="2099-01-01"))["day"]["baseline_incomplete"]
    await dao.reconcile(target="day", target_id="2000-01-01", liability_tokens=40, reason="reviewed old records")
    assert not (await dao.snapshot(day="2000-01-01"))["day"]["baseline_incomplete"]
