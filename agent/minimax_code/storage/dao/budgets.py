"""Atomic family/day token reservation, settlement and explicit reconciliation."""
from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from ...budget import BudgetExhausted
from ._base import dumps_json, loads_json, now_iso
from .runs import _append_event, _fetchall, _fetchone


def reservation_day() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


async def attach_family(conn, run_id, metadata, *, source_id=None):
    """Called in run admission's transaction, including explicit continuation."""
    cap = (metadata.get("budget") or {}).get("limit_tokens")
    family = None
    if source_id:
        source = await _fetchone(conn, "SELECT * FROM agent_runs WHERE id=?", (source_id,))
        if not source:
            raise ValueError("Budget recovery source is missing")
        source_md = loads_json(source["metadata"]) or {}
        family_id = source_md.get("budget_family_id")
        if family_id:
            family = await _fetchone(conn, "SELECT * FROM budget_families WHERE id=?", (family_id,))
        if family is None:
            # Legacy cumulative history is incomplete. Persist the hold even
            # when no limit is currently configured; resume cannot erase it.
            family_id = f"budget_{uuid.uuid4().hex}"
            receipt = loads_json(source["result"]) or {}
            lower = max(int(receipt.get("tokens_total") or 0),
                        int(receipt.get("tokens_in") or 0) + int(receipt.get("tokens_out") or 0),
                        int((receipt.get("budget") or {}).get("used_tokens") or 0))
            await conn.execute(
                "INSERT INTO budget_families(id,root_run_id,cap_tokens,legacy_lower_bound,legacy_incomplete,created_at) VALUES(?,?,?,?,1,?)",
                (family_id, source_id, (source_md.get("budget") or {}).get("limit_tokens"), lower, now_iso()))
            source_md["budget_family_id"] = family_id
            await conn.execute("UPDATE agent_runs SET metadata=? WHERE id=?", (dumps_json(source_md), source_id))
            family = await _fetchone(conn, "SELECT * FROM budget_families WHERE id=?", (family_id,))
        old_cap = family["cap_tokens"]
        if old_cap is not None and (cap is None or cap < old_cap):
            raise ValueError("Recovery must preserve or raise the cumulative family token ceiling")
        if cap != old_cap:
            authorizations = loads_json(family["authorizations"]) or []
            authorizations.append({"at": now_iso(), "run_id": run_id, "cumulative_cap": cap})
            await conn.execute("UPDATE budget_families SET cap_tokens=?,authorizations=? WHERE id=?",
                               (cap, dumps_json(authorizations), family["id"]))
    else:
        family_id = f"budget_{uuid.uuid4().hex}"
        await conn.execute("INSERT INTO budget_families(id,root_run_id,cap_tokens,created_at,authorizations) VALUES(?,?,?,?,?)",
                           (family_id, run_id, cap, now_iso(), dumps_json([{"at": now_iso(), "run_id": run_id, "cumulative_cap": cap}])))
    metadata["budget_family_id"] = family_id
    await conn.execute("UPDATE agent_runs SET metadata=? WHERE id=?", (dumps_json(metadata), run_id))
    return family_id


def _totals(rows):
    known, reserved, unknown = 0, 0, 0
    for row in rows:
        if row["state"] == "released":
            continue
        measured = row["known_tokens"] or 0
        known += measured
        if row["state"] == "reconciled":
            reserved += max(0, (row["liability_tokens"] or 0) - measured)
        elif row["state"] in {"reserved", "sent", "unknown"}:
            reserved += max(0, row["estimated_tokens"] - measured)
            unknown += int(row["state"] == "unknown")
    return known, reserved, unknown


class BudgetsDAO:
    def __init__(self, db):
        self.db = db

    async def _snapshot(self, conn, family_id=None, day=None):
        day = day or reservation_day()
        family = await _fetchone(conn, "SELECT * FROM budget_families WHERE id=?", (family_id,)) if family_id else None
        daily = await _fetchone(conn, "SELECT * FROM budget_days WHERE day=?", (day,))
        coverage = await _fetchone(conn, "SELECT started_at FROM budget_coverage WHERE id=1")
        coverage_day = datetime.fromisoformat(coverage["started_at"].replace("Z", "+00:00")).astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat() if coverage else None
        uncovered = daily is None and (coverage_day is None or day < coverage_day)
        rows = await _fetchall(conn, "SELECT * FROM budget_attempts WHERE reservation_day=?", (day,))
        dk, dr, du = _totals(rows)
        lower = daily["known_lower_bound"] if daily else 0
        if uncovered:
            historical_row = await _fetchone(conn, "SELECT COALESCE(SUM(tokens_in+tokens_out),0) AS lower FROM messages WHERE role='assistant' AND date(created_at,'+8 hours')=?", (day,))
            lower = int(historical_row["lower"])
        historical = (daily["historical_liability"] if daily and daily["historical_liability"] is not None else lower)
        daily_state = {"day": day, "known_tokens": dk + lower, "reserved_tokens": dr + max(0, historical - lower),
                       "unknown_attempts": du, "baseline_incomplete": uncovered or bool(daily and daily["incomplete"]),
                       "historical_lower_bound": lower, "historical_liability": daily["historical_liability"] if daily else None}
        if not family:
            return {"family": None, "day": daily_state}
        rows = await _fetchall(conn, "SELECT * FROM budget_attempts WHERE family_id=?", (family_id,))
        known, reserved, unknown = _totals(rows)
        known += family["legacy_lower_bound"]
        reserved += max(0, (family["legacy_liability"] or 0) - family["legacy_lower_bound"])
        incomplete = bool(family["legacy_incomplete"])
        cap = family["cap_tokens"]
        reason = "unresolved_attempt" if unknown else "legacy_family_incomplete" if incomplete else None
        if reason is None and cap is not None and known + reserved >= cap:
            reason = "family_ceiling"
        return {"family": {"id": family_id, "limit_tokens": cap, "known_tokens": known,
                "reserved_tokens": reserved, "unknown_attempts": unknown, "baseline_incomplete": incomplete,
                "remaining_estimate": max(0, cap - known - reserved) if cap is not None else None,
                "pause_reason": reason, "accounting_resolved": not unknown and not incomplete,
                "usage_complete": not incomplete and family["legacy_liability"] is None
                    and all(row["state"] in {"settled", "released"} for row in rows),
                "authorizations": loads_json(family["authorizations"])}, "day": daily_state}

    async def snapshot(self, family_id=None, *, day=None):
        async with self.db.transaction() as conn:
            return await self._snapshot(conn, family_id, day)

    async def reserve(self, *, family_id, run_id, provider, model, estimate, purpose="inference", pricing=None, daily_limit=None, day=None, reactive=False, attempt_id=None):
        if getattr(self.db, "_budget_governance_error", None):
            raise BudgetExhausted("Budget persistence/recovery is unavailable; restart and complete recovery before paid work")
        if type(estimate) is not int or estimate < 0:
            raise ValueError("A nonnegative declared reservation is required")
        day = day or reservation_day()
        async with self.db.transaction() as conn:
            if getattr(self.db, "_budget_governance_error", None):
                raise BudgetExhausted("Budget persistence/recovery is unavailable; restart and complete recovery before paid work")
            state = await self._snapshot(conn, family_id, day)
            family, daily = state["family"], state["day"]
            if not family:
                raise ValueError("Budget family is missing")
            reason = family["pause_reason"]
            if reason or (family["limit_tokens"] is not None and family["known_tokens"] + family["reserved_tokens"] + estimate > family["limit_tokens"]):
                raise BudgetExhausted(reason or "family_ceiling")
            if daily_limit is not None:
                if daily["baseline_incomplete"] or daily["unknown_attempts"]:
                    raise BudgetExhausted("daily_accounting_incomplete")
                if (daily["known_tokens"] + daily["reserved_tokens"] + estimate > daily_limit
                        or (reactive and daily["known_tokens"] + daily["reserved_tokens"] >= daily_limit)):
                    raise BudgetExhausted("daily_ceiling")
            aid = attempt_id or f"attempt_{uuid.uuid4().hex}"
            owner = await _fetchone(conn, "SELECT metadata FROM agent_runs WHERE id=?", (run_id,))
            if owner:
                metadata = loads_json(owner["metadata"]) or {}
                if metadata.get("budget_family_id") not in {None, family_id}:
                    raise ValueError("Attempt owner belongs to another budget family")
                metadata["budget_family_id"] = family_id
                await conn.execute("UPDATE agent_runs SET metadata=? WHERE id=?", (dumps_json(metadata), run_id))
            await conn.execute(
                "INSERT INTO budget_attempts(id,family_id,run_id,provider,model,purpose,pricing,reservation_day,estimated_tokens,state,created_at) VALUES(?,?,?,?,?,?,?,?,?,'reserved',?)",
                (aid, family_id, run_id, provider, model, purpose, dumps_json(pricing or {"price": None, "currency": None}), day, estimate, now_iso()))
            await self._event(conn, run_id, aid, "reserved", {"estimated_tokens": estimate, "enforcement": "reactive" if reactive else "estimated"})
            return aid

    async def sent(self, attempt_id):
        async with self.db.transaction() as conn:
            if getattr(self.db, "_budget_governance_error", None):
                raise BudgetExhausted("Budget persistence/recovery is unavailable; reserved request was not sent")
            cursor = await conn.execute("UPDATE budget_attempts SET state='sent',sent_at=? WHERE id=? AND state='reserved'", (now_iso(), attempt_id))
            if cursor.rowcount != 1:
                raise BudgetExhausted("Request reservation is no longer live; no request was sent")

    async def fail_attempt(self, attempt_id, usage, *, reason):
        from ...budget_ledger import normalize_usage
        async with self.db.transaction() as conn:
            row = await _fetchone(conn, "SELECT * FROM budget_attempts WHERE id=?", (attempt_id,))
            if row is None or row["state"] in {"released", "settled", "reconciled"}:
                return
            if row["state"] == "reserved":
                await conn.execute("UPDATE budget_attempts SET state='released',reason=?,settled_at=? WHERE id=?", (reason, now_iso(), attempt_id))
                await self._event(conn, row["run_id"], attempt_id, "released", {"reason": reason})
            else:
                await self.settle_in_transaction(conn, attempt_id, normalize_usage(usage), complete=False, reason=reason)

    async def recover_in_transaction(self, conn, *, family_id=None):
        rows = await _fetchall(conn, "SELECT * FROM budget_attempts WHERE state IN ('reserved','sent')" + (" AND family_id=?" if family_id else ""), (family_id,) if family_id else ())
        for row in rows:
            state = "released" if row["state"] == "reserved" else "unknown"
            await conn.execute("UPDATE budget_attempts SET state=?,reason='owner stopped before settlement',settled_at=? WHERE id=?", (state, now_iso(), row["id"]))
            await self._event(conn, row["run_id"], row["id"], state, {"reason": "owner stopped before settlement"})

    async def recover(self, *, family_id=None):
        async with self.db.transaction() as conn:
            await self.recover_in_transaction(conn, family_id=family_id)
        if family_id is None:
            self.db._budget_governance_error = None

    async def settle(self, attempt_id, usage, *, complete=True, reason=None):
        from ...budget_ledger import normalize_usage
        normalized = normalize_usage(usage)
        try:
            async with self.db.transaction() as conn:
                return await self.settle_in_transaction(conn, attempt_id, normalized, complete=complete and normalized["complete"], reason=reason)
        except Exception:
            self.db._budget_governance_error = "budget settlement persistence failed"
            raise

    async def settle_in_transaction(self, conn, attempt_id, normalized, *, complete=True, reason=None, in_flight=False):
        attempt = await _fetchone(conn, "SELECT * FROM budget_attempts WHERE id=?", (attempt_id,))
        if not attempt:
            raise ValueError("Attempt not found")
        if attempt["state"] in {"settled", "released", "reconciled"}:
            return False
        known = normalized["known_tokens"]
        known = max(attempt["known_tokens"] or 0, known) if known is not None else attempt["known_tokens"]
        state = "sent" if in_flight else "settled" if complete else "unknown"
        if attempt["state"] == state and attempt["known_tokens"] == known and attempt["usage"] == dumps_json(normalized):
            return False
        await conn.execute("UPDATE budget_attempts SET state=?,known_tokens=?,usage=?,provenance='provider',settled_at=?,reason=? WHERE id=?",
                           (state, known, dumps_json(normalized), now_iso(), reason, attempt_id))
        await self._event(conn, attempt["run_id"], attempt_id, state, {"usage": normalized, "reason": reason})
        return True

    async def release(self, attempt_id, *, reason):
        async with self.db.transaction() as conn:
            row = await _fetchone(conn, "SELECT * FROM budget_attempts WHERE id=?", (attempt_id,))
            if not row or row["state"] != "reserved":
                raise ValueError("Only a provably unsent reservation can be released")
            await conn.execute("UPDATE budget_attempts SET state='released',reason=?,settled_at=? WHERE id=?", (reason, now_iso(), attempt_id))
            await self._event(conn, row["run_id"], attempt_id, "released", {"reason": reason})

    async def reconcile(self, *, target, target_id, liability_tokens, reason):
        if type(liability_tokens) is not int or liability_tokens < 0 or not isinstance(reason, str) or not reason.strip():
            raise ValueError("Reconciliation requires nonnegative authorized liability and a reason")
        async with self.db.transaction() as conn:
            if target == "day":
                row = await _fetchone(conn, "SELECT * FROM budget_days WHERE day=?", (target_id,))
                if row is None:
                    historical = (await self._snapshot(conn, day=target_id))["day"]
                    if historical["baseline_incomplete"]:
                        await conn.execute("INSERT INTO budget_days(day,known_lower_bound,incomplete) VALUES(?,?,1)", (target_id, historical["historical_lower_bound"]))
                        row = await _fetchone(conn, "SELECT * FROM budget_days WHERE day=?", (target_id,))
                if not row or not row["incomplete"] or liability_tokens < row["known_lower_bound"]:
                    raise ValueError("Authorized historical liability must cover its known lower bound")
                await conn.execute("UPDATE budget_days SET historical_liability=?,incomplete=0,reconciled_at=?,reason=? WHERE day=?", (liability_tokens, now_iso(), reason, target_id))
            elif target == "family":
                row = await _fetchone(conn, "SELECT * FROM budget_families WHERE id=?", (target_id,))
                if not row or not row["legacy_incomplete"] or liability_tokens < row["legacy_lower_bound"]:
                    raise ValueError("Authorized family liability must cover its known lower bound")
                auth = loads_json(row["authorizations"]) or []
                auth.append({"at": now_iso(), "historical_liability": liability_tokens, "reason": reason})
                await conn.execute("UPDATE budget_families SET legacy_liability=?,legacy_incomplete=0,authorizations=? WHERE id=?", (liability_tokens, dumps_json(auth), target_id))
            elif target == "attempt":
                row = await _fetchone(conn, "SELECT * FROM budget_attempts WHERE id=?", (target_id,))
                if not row or row["state"] != "unknown" or liability_tokens < (row["known_tokens"] or 0):
                    raise ValueError("Only unresolved attempts can be reconciled above their known lower bound")
                owner = await _fetchone(conn, "SELECT status FROM agent_runs WHERE id=?", (row["run_id"],))
                if owner is None:
                    owner = await _fetchone(conn, "SELECT r.status FROM budget_families f JOIN agent_runs r ON r.id=f.root_run_id WHERE f.id=?", (row["family_id"],))
                if owner and owner["status"] in {"queued", "planning", "running", "awaiting_approval", "waiting_input"}:
                    raise ValueError("Wait for owner execution and cleanup before reconciling its attempt")
                await conn.execute("UPDATE budget_attempts SET state='reconciled',liability_tokens=?,provenance='reconciled',reason=?,settled_at=? WHERE id=?", (liability_tokens, reason, now_iso(), target_id))
                await self._event(conn, row["run_id"], target_id, "reconciled", {"authorized_liability": liability_tokens, "reason": reason})
            else:
                raise ValueError("Unknown reconciliation target")

    async def _event(self, conn, run_id, aid, state, data):
        owner = await _fetchone(conn, "SELECT id FROM agent_runs WHERE id=?", (run_id,))
        if not owner:
            owner = await _fetchone(conn, "SELECT r.id FROM budget_attempts a JOIN budget_families f ON a.family_id=f.id JOIN agent_runs r ON r.id=f.root_run_id WHERE a.id=?", (aid,))
        if owner:
            await _append_event(conn, owner["id"], "budget", {"attempt_id": aid, "attempt_run_id": run_id, "state": state, **data})

    async def attempts(self, family_id):
        return [dict(row) for row in await self.db.fetchall("SELECT * FROM budget_attempts WHERE family_id=? ORDER BY rowid", (family_id,))]
