"""Reliability primitives for the agent call stack — fused from grok-build.

R13 adds two gates MiniMax entirely lacked:

- :class:`CircuitBreaker` — sliding-window error-rate breaker
  (xai-circuit-breaker semantics), wired in front of the LLM call and
  per-tool dispatch.
- :func:`with_retry` — app-level exponential backoff with jitter
  (RetryPolicy Disposition {Retryable|Terminal}).

Drop list (grok had, deferred from this round — see
``docs/evolution/ITERATION_LOG.md`` R13): three-tier Admission semaphores
(single-process, no multi-session tool server yet), token/$ spend cap,
OS-level sandbox.
"""

from __future__ import annotations

from .circuit_breaker import (
    DEFAULT_FAILURE_CODES,
    BreakerConfig,
    BreakerOpen,
    BreakerState,
    CircuitBreaker,
    CircuitBreakerRegistry,
    Outcome,
)
from .retry import (
    RETRYABLE_STATUS,
    TERMINAL_STATUS,
    Disposition,
    RetryExhausted,
    RetryPolicy,
    classify_exception,
    with_retry,
)

__all__ = [
    "DEFAULT_FAILURE_CODES",
    "BreakerConfig",
    "BreakerOpen",
    "BreakerState",
    "CircuitBreaker",
    "CircuitBreakerRegistry",
    "Outcome",
    "RETRYABLE_STATUS",
    "TERMINAL_STATUS",
    "Disposition",
    "RetryExhausted",
    "RetryPolicy",
    "classify_exception",
    "with_retry",
]
