"""Reactive ledger proof through real scripted app-server and SQLite."""
from __future__ import annotations

import asyncio

from test_codex_continuity import codex, native, requests, rpc, settle, start  # noqa: F401

from minimax_code.runs import get_run_service
from minimax_code.storage.dao.budgets import BudgetsDAO
from minimax_code.storage.dao.sessions import SessionsDAO


async def test_fresh_families_share_thread_watermark_without_duplicate_charge(codex):  # noqa: F811
    first = await settle(codex, await start(codex))
    second = await settle(codex, await start(codex, "second", "second"))
    assert first["metadata"]["budget_family_id"] != second["metadata"]["budget_family_id"]
    dao = BudgetsDAO(codex.db)
    a = await dao.attempts(first["metadata"]["budget_family_id"])
    b = await dao.attempts(second["metadata"]["budget_family_id"])
    assert [a[0]["known_tokens"], b[0]["known_tokens"]] == [100, 50]
    assert a[0]["state"] == b[0]["state"] == "settled"
    session = await SessionsDAO(codex.db).get("one")
    assert session["engine_state"]["usage_watermark"]["totalTokens"] == 150
    day = (await dao.snapshot())["day"]
    assert day["known_tokens"] == 150 and day["reserved_tokens"] == day["unknown_attempts"] == 0


async def test_in_flight_observed_usage_keeps_sent_state_until_owner_stops(codex):  # noqa: F811
    ctx = await start(codex, "hang")
    run_id = ctx.reply.call_args.args[0]["run"]["id"]
    dao = BudgetsDAO(codex.db)
    for _ in range(300):
        run = await codex.dao.get_run(run_id)
        rows = await dao.attempts(run["metadata"]["budget_family_id"])
        if rows and rows[0]["known_tokens"] == 100:
            break
        await asyncio.sleep(.01)
    assert rows[0]["state"] == "sent"
    assert (await dao.snapshot(run["metadata"]["budget_family_id"]))["family"]["unknown_attempts"] == 0
    get_run_service(codex.server).cancel_run(run_id)
    await settle(codex, ctx)
    assert (await dao.attempts(run["metadata"]["budget_family_id"]))[0]["state"] == "unknown"


async def test_reactive_threshold_interrupts_and_records_actual_overspend(codex):  # noqa: F811
    first = await settle(codex, await start(codex, budget_tokens=90))
    assert first["status"] == "budget_paused"
    assert requests(codex, "turn/interrupt")
    dao = BudgetsDAO(codex.db)
    assert (await dao.snapshot(first["metadata"]["budget_family_id"]))["family"]["known_tokens"] == 100


async def test_reset_cannot_be_reconciled_as_reliable_watermark(codex):  # noqa: F811
    await settle(codex, await start(codex))
    dao = BudgetsDAO(codex.db)
    reset = await settle(codex, await start(codex, "reset", "reset", budget_tokens=1000))
    assert reset["status"] == "budget_paused"
    rows = await dao.attempts(reset["metadata"]["budget_family_id"])
    await dao.reconcile(target="attempt", target_id=rows[0]["id"], liability_tokens=100, reason="accounting authorization only")
    held = await settle(codex, await rpc(codex, "run.resume", {"run_id": reset["id"], "reviewed": True, "idempotency_key": "hold", "budget_tokens": 2000}))
    assert held["status"] == "budget_paused"
    assert len(requests(codex, "turn/start")) == 2
    session = await SessionsDAO(codex.db).get("one")
    assert session["engine_state"]["usage_watermark"]["totalTokens"] == 100
