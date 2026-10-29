"""Request-boundary durable budget handle, inherited by recursive descendants.

Estimates are declared assumptions, never a provider billing upper bound. A
sent attempt without complete usage remains an outstanding liability.
"""
from __future__ import annotations

import asyncio
import json
import uuid
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from typing import Any

from .budget import BudgetExhausted

_current: ContextVar[BudgetHandle | None] = ContextVar("durable_budget", default=None)


def current_handle():
    return _current.get()


@contextmanager
def budget_scope(handle):
    token = _current.set(handle)
    try:
        yield handle
    finally:
        _current.reset(token)


def normalize_usage(usage):
    usage = usage or {}
    def number(key):
        value = usage.get(key)
        return value if type(value) is int and value >= 0 else None
    prompt, completion, total = (number(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens"))
    if total is None and prompt is not None and completion is not None:
        total = prompt + completion
    # Cache reads/creation are separate only when the provider declares them
    # non-overlapping. Reasoning tokens are included in completion, never added.
    known = total if total is not None else ((prompt or 0) + (completion or 0) if prompt is not None or completion is not None else None)
    return {"prompt_tokens": prompt, "completion_tokens": completion, "total_tokens": total,
            "known_tokens": known, "complete": total is not None,
            "cached_tokens": number("cached_tokens"), "cache_creation_tokens": number("cache_creation_tokens"),
            "reasoning_tokens": number("reasoning_tokens"), "raw": usage}


def estimate_request(kwargs):
    output = kwargs.get("max_tokens")
    if type(output) is not int or output <= 0:
        raise BudgetExhausted("A positive enforced max_tokens is required for estimated Native budgeting")
    # UTF-8 bytes plus explicit framing allowance deliberately overestimates
    # ordinary text. Image/audio/provider-specific tokenization is not bounded.
    body = json.dumps(kwargs, ensure_ascii=False, separators=(",", ":"))
    return len(body.encode("utf-8")) + 256 + output


@dataclass
class BudgetHandle:
    dao: Any
    family_id: str
    run_id: str
    provider: str | None = None
    model: str | None = None
    daily_limit: int | None = None
    enforcement: str = "estimated"
    purpose: str = "inference"

    @classmethod
    async def for_run(cls, db, run_id, *, daily_limit=None, enforcement="estimated"):
        from .storage.dao._base import loads_json
        from .storage.dao.budgets import BudgetsDAO
        row = await db.fetchone("SELECT metadata FROM agent_runs WHERE id=?", (run_id,))
        md = loads_json(row["metadata"]) if row else {}
        family_id = md.get("budget_family_id")
        if not family_id:
            raise ValueError("Run has no durable budget family")
        model = md.get("model_snapshot") or md.get("codex_settings") or {}
        return cls(BudgetsDAO(db), family_id, run_id, model.get("provider_id"), model.get("model"), daily_limit, enforcement)

    def descendant(self, run_id):
        return replace(self, run_id=run_id, purpose="subagent")

    async def snapshot(self):
        state = await self.dao.snapshot(self.family_id)
        return self.format_snapshot(state)

    def format_snapshot(self, state):
        family, day = state["family"], state["day"]
        from .budget_settings import get_config
        daily_limit = get_config(self.dao.db.path.parent, fallback=self.daily_limit)["effective_limit"]
        reason = family["pause_reason"]
        if getattr(self.dao.db, "_budget_governance_error", None):
            reason = "budget_storage_unavailable"
        if daily_limit is not None:
            if day["baseline_incomplete"] or day["unknown_attempts"]:
                reason = reason or "daily_accounting_incomplete"
            elif day["known_tokens"] + day["reserved_tokens"] >= daily_limit:
                reason = reason or "daily_ceiling"
        return {**family, "family_id": self.family_id, "day": day, "daily_limit": daily_limit,
                "enforcement": self.enforcement, "pause_reason": reason,
                "assumptions": "External cumulative usage notifications; internal calls cannot be reserved" if self.enforcement == "reactive" else "UTF-8 serialized request bytes + 256 framing tokens + enforced max_tokens; provider usage can exceed estimate",
                "price": None, "currency": None}


@dataclass
class RequestAttempt:
    handle: BudgetHandle
    id: str
    usage: dict = field(default_factory=dict)
    finished: bool = False

    def observe(self, chunk):
        if chunk.usage:
            self.usage.update(chunk.usage)
        self.finished = self.finished or bool(chunk.finish_reason)


@asynccontextmanager
async def governed_request(kwargs, *, protocol):
    handle = current_handle()
    if handle is None:
        yield None
        return
    estimate = estimate_request(kwargs)  # Local serialization failure is provably unsent.
    aid = f"attempt_{uuid.uuid4().hex}"
    attempt = RequestAttempt(handle, aid)
    try:
        await handle.dao.reserve(family_id=handle.family_id, run_id=handle.run_id,
            provider=handle.provider or protocol, model=kwargs.get("model") or handle.model,
            estimate=estimate, daily_limit=handle.daily_limit, purpose=handle.purpose, attempt_id=aid)
        # A crash after sent commit is ambiguous even before SDK entry.
        await handle.dao.sent(aid)
        yield attempt
        await owned_cleanup(handle.dao.settle(aid, attempt.usage, complete=attempt.finished,
                                reason=None if attempt.finished else "stream_eof_without_finish"))
        row = await handle.dao.db.fetchone("SELECT state FROM budget_attempts WHERE id=?", (aid,))
        if row["state"] == "unknown":
            raise BudgetExhausted("Provider usage is incomplete; reconcile outstanding liability before another call")
    except BaseException as exc:
        try:
            await owned_cleanup(handle.dao.fail_attempt(aid, attempt.usage, reason=type(exc).__name__))
        except Exception:
            handle.dao.db._budget_governance_error = "budget failure settlement persistence failed"
            raise
        raise


async def owned_cleanup(coro):
    task = asyncio.create_task(coro)
    cancelled = None
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError as exc:
            cancelled = cancelled or exc
    result = task.result()
    if cancelled is not None:
        raise cancelled
    return result


def governed_client(client):
    """The actual SDK client must expose retry control under governance."""
    if current_handle() is None:
        return client
    options = getattr(client, "with_options", None)
    if options is None:
        raise BudgetExhausted("Native client cannot prove SDK retries are disabled")
    return options(max_retries=0)
