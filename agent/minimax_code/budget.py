"""Run-scoped and daily token budgets (M2: reliable runs — budget pause).

A budget is checked *before* every LLM call (never after spend). When a
run exceeds its limit it stops cleanly at the next call boundary: the
run row moves to ``budget_paused``, which ``run.resume`` can continue
without the review gate — the boundary is a known-safe point, unlike an
interrupted run whose last write outcome is unknown.

Budget dimensions (M2 acceptance: 单任务、每日、父子共用):

- **per-run**: one run's cumulative tokens (request ``budget_tokens`` >
  ``MINIMAX_CODE_RUN_BUDGET_TOKENS`` env default > unlimited).
- **daily**: all of today's runs combined (``MINIMAX_CODE_DAILY_BUDGET_TOKENS``
  env, 0/unset = unlimited). Baseline is queried once at admission; the
  in-flight run adds its own usage on top, so a long run also pauses
  when it pushes the day over the line.
- **parent+child shared**: sub-agents inherit the parent's
  :class:`BudgetState` instance via the ContextVar published by
  ``AgentCore.run`` — every child token spends from the same pot.

State lives on the :class:`AgentCore` instance, so auto-continue blocks
(one logical turn = several ``run()`` calls on the same core) accumulate
into one budget instead of resetting per block.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class BudgetExhausted(Exception):
    """A pre-call projection shows the next call would exceed the budget."""


@dataclass
class BudgetState:
    """Mutable spend tracker shared by a run and its sub-agents.

    ``limit_tokens`` caps this run (and everything it spawns). ``daily_limit``
    caps today's whole-process spend; ``daily_baseline`` is the
    already-spent total queried at admission, so the check is
    ``baseline + used`` against the daily limit without re-querying.
    """

    limit_tokens: int | None = None
    daily_limit: int | None = None
    daily_baseline: int = 0
    used_tokens: int = 0
    pauses: int = field(default=0)

    def record(self, usage: dict[str, int] | None) -> int:
        """Accumulate one LLM response's usage; returns the new total."""
        total = int((usage or {}).get("total_tokens")
                    or (int((usage or {}).get("prompt_tokens") or 0)
                        + int((usage or {}).get("completion_tokens") or 0)))
        self.used_tokens += max(0, total)
        return self.used_tokens

    def projected_total(self, projected_next: int) -> int:
        """Whole-day + this-run projection before the next call."""
        return self.daily_baseline + self.used_tokens + max(0, projected_next)

    def would_exceed(self, projected_next: int) -> bool:
        """True when the next call (≈ ``projected_next`` tokens) breaks a limit."""
        projected = self.projected_total(projected_next)
        if self.limit_tokens is not None and self.used_tokens + max(0, projected_next) > self.limit_tokens:
            return True
        if self.daily_limit is not None and projected > self.daily_limit:
            return True
        return False

    def snapshot(self) -> dict[str, Any]:
        """Persistable summary stored in run metadata for resume decisions."""
        return {
            "limit_tokens": self.limit_tokens,
            "daily_limit": self.daily_limit,
            "used_tokens": self.used_tokens,
            "pauses": self.pauses,
        }


def _env_int(name: str) -> int | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError:
        logger.warning("%s=%r is not an int; ignoring", name, raw)
        return None
    return value if value > 0 else None


def resolve_run_budget(
    requested: int | None, *, scheduled: bool = False
) -> int | None:
    """Resolve the per-run cap: explicit request > scheduled floor > env.

    ``scheduled`` marks unattended jobs — the planning principle says an
    unattended task must always carry a budget, so when
    ``MINIMAX_CODE_SCHEDULED_BUDGET_TOKENS`` is set it overrides the
    plain env default for those runs (explicit request params still win).
    Non-positive / unparseable values mean unlimited.
    """
    if requested is not None:
        return requested if requested > 0 else None
    if scheduled:
        scheduled_floor = _env_int("MINIMAX_CODE_SCHEDULED_BUDGET_TOKENS")
        if scheduled_floor is not None:
            return scheduled_floor
    return _env_int("MINIMAX_CODE_RUN_BUDGET_TOKENS")


def daily_budget_limit() -> int | None:
    """Today's whole-process cap from the environment (None = unlimited)."""
    return _env_int("MINIMAX_CODE_DAILY_BUDGET_TOKENS")


def projected_call_tokens(last_prompt_tokens: int | None, max_output_tokens: int) -> int:
    """Cheap upper-bound estimate for the next LLM call.

    The previous iteration's real prompt usage is the best proxy for the
    next one (history only grows); the configured output cap bounds the
    completion side. Before the first call the prompt side is unknown and
    counts as zero — the first call is also the cheapest to over-estimate.
    """
    return (last_prompt_tokens or 0) + max(0, max_output_tokens)


# -- parent/child budget sharing --------------------------------------------

_current_budget: Any = None  # ContextVar[BudgetState | None], lazy-bound


def publish_current_budget(budget: BudgetState | None) -> Any:
    """Publish the run's budget for sub-agent inheritance (token returned)."""
    global _current_budget
    if _current_budget is None:
        from contextvars import ContextVar

        _current_budget = ContextVar("minimax_current_budget", default=None)
    return _current_budget.set(budget)


def reset_current_budget(token: Any) -> None:
    if _current_budget is not None and token is not None:
        _current_budget.reset(token)


def current_budget() -> BudgetState | None:
    """The in-flight run's shared budget, or None outside a run scope."""
    if _current_budget is None:
        return None
    return _current_budget.get()


__all__ = [
    "BudgetExhausted",
    "BudgetState",
    "current_budget",
    "daily_budget_limit",
    "projected_call_tokens",
    "publish_current_budget",
    "reset_current_budget",
    "resolve_run_budget",
]
