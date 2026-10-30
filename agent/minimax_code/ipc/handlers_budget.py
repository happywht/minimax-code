"""Authenticated RPC budget read/reconciliation surface; never invent usage."""
from __future__ import annotations

from pydantic import ValidationError

from ..budget import daily_budget_limit
from ..budget_ledger import BudgetHandle
from ..storage.dao._base import loads_json
from ..storage.dao.budgets import BudgetsDAO
from .protocol import (INVALID_PARAMS, BudgetGetParams, BudgetReconcileParams,
                       BudgetSettingsGetParams, BudgetSettingsSetParams)


def register_budget_handlers(server):
    async def database():
        from ..app import get_db
        db = get_db()
        if db is None:
            raise ValueError("Budget storage unavailable")
        return db

    async def get(params, ctx):
        try:
            request = BudgetGetParams.model_validate(params or {})
            if request.blockers_after and not request.day:
                raise ValueError("A blocker cursor requires its original budget day")
            db = await database()
            dao = BudgetsDAO(db)
            family_id, budget = None, None
            if request.run_id:
                row = await db.fetchone("SELECT metadata FROM agent_runs WHERE id=?", (request.run_id,))
                if not row:
                    raise ValueError("Unknown run")
                metadata = loads_json(row["metadata"]) or {}
                family_id = metadata.get("budget_family_id")
                if family_id:
                    handle = await BudgetHandle.for_run(db, request.run_id, daily_limit=daily_budget_limit(),
                        enforcement="reactive" if metadata.get("engine") == "codex" else "estimated")
                    budget = await handle.snapshot()
            state = await dao.snapshot(family_id, day=request.day)
            attempts = await dao.attempts(family_id) if family_id else []
            blockers = await db.fetchall(
                "SELECT a.rowid AS cursor,a.id AS attempt_id,a.family_id,a.known_tokens,COALESCE(r.id,root.id) AS run_id,COALESCE(r.session_id,root.session_id) AS session_id "
                "FROM budget_attempts a JOIN budget_families f ON f.id=a.family_id "
                "LEFT JOIN agent_runs r ON r.id=a.run_id LEFT JOIN agent_runs root ON root.id=f.root_run_id "
                "WHERE a.reservation_day=? AND a.state='unknown' AND a.rowid>? ORDER BY a.rowid LIMIT 21",
                (state["day"]["day"], request.blockers_after))
            page = blockers[:20]
            next_cursor = page[-1]["cursor"] if len(blockers) > 20 else None
            coverage = await db.fetchone("SELECT started_at FROM budget_coverage WHERE id=1")
            await ctx.reply({"budget": budget, "day": state["day"], "attempts": attempts,
                             "day_blockers": {"total": state["day"]["unknown_attempts"],
                                 "items": [{key: value for key, value in dict(row).items() if key != "cursor"} for row in page],
                                 "next_cursor": next_cursor},
                             "coverage_started_at": coverage["started_at"] if coverage else None})
        except (ValidationError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))

    async def reconcile(params, ctx):
        try:
            request = BudgetReconcileParams.model_validate(params)
            dao = BudgetsDAO(await database())
            await dao.reconcile(**request.model_dump(exclude={"acknowledged"}))
            await ctx.reply({"ok": True})
        except (ValidationError, ValueError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))

    async def settings_get(params, ctx):
        from ..budget_settings import get_config, policy_lock
        from ..storage.dao.text_artifacts import ArtifactDAO
        try:
            BudgetSettingsGetParams.model_validate(params or {})
            db = await database()
            async with policy_lock:
                service = await ArtifactDAO(db).service_id()
                config = get_config(db.path.parent)
                day = (await BudgetsDAO(db).snapshot())["day"]
            await ctx.reply({"service_id": service, **config, "day": day, "timezone": "Asia/Shanghai"})
        except (ValidationError, ValueError, OSError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))

    async def settings_set(params, ctx):
        from ..budget_settings import get_config, policy_lock, set_config
        from ..storage.dao.text_artifacts import ArtifactDAO
        try:
            request = BudgetSettingsSetParams.model_validate(params)
            db = await database()
            async with policy_lock:
                service = await ArtifactDAO(db).service_id()
                if service != request.expected_service_id:
                    raise ValueError("Service identity changed; reload budget settings")
                if "expected_saved_limit" in request.model_fields_set:
                    current = get_config(db.path.parent)["saved_limit"]
                    if current != request.expected_saved_limit:
                        raise ValueError("Daily budget settings changed; refresh before replacing")
                config = set_config(db.path.parent, request.limit_tokens)
                day = (await BudgetsDAO(db).snapshot())["day"]
            await ctx.reply({"service_id": service, **config, "day": day, "timezone": "Asia/Shanghai"})
        except (ValidationError, ValueError, OSError) as exc:
            await ctx.reply_error(INVALID_PARAMS, str(exc))

    server.register("budget.settings_get", settings_get)
    server.register("budget.settings_set", settings_set)
    server.register("budget.get", get)
    server.register("budget.reconcile", reconcile)
