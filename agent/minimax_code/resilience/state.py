"""Breaker state types (R17).

Port of grok-build ``xai-circuit-breaker``'s ``state.rs``. Three-value state
machine, a two-value outcome enum, and the ``BreakerOpen`` signal raised by
:meth:`CircuitBreaker.check` when the breaker is shedding traffic.

Pure data: no I/O, no asyncio, no intra-package deps. Identity-mapped to
grok's wire values at the package boundary (``Closed/Open/HalfOpen`` and
``Success/Failure``).
"""

from __future__ import annotations

from enum import StrEnum


class BreakerState(StrEnum):
    """Tri-state circuit-breaker status.

    ``CLOSED`` — normal operation, traffic flows, samples accumulate in the
    sliding window. ``OPEN`` — shedding all traffic for ``open_duration``.
    ``HALF_OPEN`` — cool-down elapsed, a bounded number of probe requests
    are admitted to test whether the upstream has recovered.
    """

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class Outcome(StrEnum):
    """Outcome of a guarded call, fed back via :meth:`CircuitBreaker.record`."""

    SUCCESS = "success"
    FAILURE = "failure"


class BreakerOpen(Exception):
    """Raised by :meth:`CircuitBreaker.check` / ``guard()`` when shedding.

    ``retry_after`` is seconds until the caller should retry. Callers that
    surface this over HTTP should map it to a ``Retry-After`` header; callers
    that just want to degrade can catch and fall back.
    """

    def __init__(self, retry_after: float) -> None:
        self.retry_after = max(0.0, float(retry_after))
        super().__init__(f"circuit breaker open; retry after {self.retry_after:.1f}s")


__all__ = ["BreakerOpen", "BreakerState", "Outcome"]
