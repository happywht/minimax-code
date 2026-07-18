"""Resilience primitives (R17): circuit breaker + retry policy.

Forward-port of grok-build ``xai-circuit-breaker`` to Python asyncio. The
breaker is a synchronous, single-threaded three-state machine; the GIL makes
Rust's atomics/CAS/locks unnecessary. Fail-open by construction: a breaker's
own internal fault never blocks the guarded business call.
"""

from __future__ import annotations

from .breaker import CircuitBreaker, Clock, NoopObserver, Observer
from .config import DEFAULT_FAILURE_CODES, BreakerConfig, parse_failure_codes
from .registry import CircuitBreakerRegistry
from .retry_policy import Disposition, RetryPolicy
from .state import BreakerOpen, BreakerState, Outcome
from .window import MAX_WINDOW_ENTRIES, SlidingWindow

__all__ = [
    "BreakerConfig",
    "BreakerOpen",
    "BreakerState",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "Clock",
    "DEFAULT_FAILURE_CODES",
    "Disposition",
    "MAX_WINDOW_ENTRIES",
    "NoopObserver",
    "Observer",
    "Outcome",
    "RetryPolicy",
    "SlidingWindow",
    "parse_failure_codes",
]
