"""App-level retry with exponential backoff + jitter.

Fused from grok-build's ``RetryPolicy`` (``Disposition {Retryable |
AuthRefresh | Terminal}``) plus the exponential-backoff-with-jitter idiom
shared across the xai-* HTTP crates. MiniMax today has **zero** app-level
retry: a single ``LLMError`` (even a 429/503) terminates the whole turn,
because the LLM SDK's built-in ``max_retries`` only covers transport-layer
transient errors — not the application-visible ``LLMError`` the transports
raise.

``classify_exception`` maps a raised exception to a :class:`Disposition`:

- ``LLMError`` with ``breaker_open=True`` → TERMINAL (R19; the breaker is
  shedding load — retrying would just hammer an open breaker that said stop)
- ``LLMStreamTimeout`` / ``asyncio.TimeoutError`` / ``ConnectionError`` → RETRYABLE
- ``LLMError`` whose ``status_code`` ∈ {429,500,502,503,504} → RETRYABLE
- ``LLMError`` whose ``status_code`` ∈ {400,401,403,404,422} → TERMINAL
- ``LLMError`` without ``status_code`` → RETRYABLE (transports flatten
  ``APIError`` incl. connection/timeout into ``LLMError``, so the
  no-code case is most likely transient)
- anything else → TERMINAL
"""

from __future__ import annotations

import asyncio
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Status codes that warrant another attempt.
RETRYABLE_STATUS: frozenset[int] = frozenset({429, 500, 502, 503, 504})
# Status codes that will not succeed no matter how often retried.
TERMINAL_STATUS: frozenset[int] = frozenset({400, 401, 403, 404, 422})


class Disposition(StrEnum):
    """What retry should do with a given exception."""

    RETRYABLE = "retryable"
    TERMINAL = "terminal"


class RetryExhausted(Exception):
    """Raised when retry attempts are exhausted — wraps the last error.

    Kept as a distinct type so callers that want to distinguish "gave up
    after N tries" from "terminal on first try" can. ``with_retry`` itself
    re-raises the original exception when the budget runs out (to preserve
    type); this class is exported for callers that prefer to wrap manually.
    """


def classify_exception(exc: BaseException) -> Disposition:
    """Classify ``exc`` as retryable or terminal (see module docstring)."""

    # Lazy import: types.py lives in the parent package and we must not
    # create an import cycle at module load.
    from ..types import LLMError, LLMStreamTimeout

    if isinstance(exc, LLMStreamTimeout):
        return Disposition.RETRYABLE
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return Disposition.RETRYABLE
    if isinstance(exc, LLMError):
        # R19: a breaker shedding load is TERMINAL regardless of status_code
        # — retrying an open breaker just wastes the backoff budget hammering
        # a gate that said "stop". Fuses grok's "BreakerOpen = terminal".
        if getattr(exc, "breaker_open", False):
            return Disposition.TERMINAL
        code = getattr(exc, "status_code", None)
        if code is None:
            return Disposition.RETRYABLE
        if code in RETRYABLE_STATUS:
            return Disposition.RETRYABLE
        if code in TERMINAL_STATUS:
            return Disposition.TERMINAL
        return Disposition.RETRYABLE  # unknown code → optimistic
    return Disposition.TERMINAL


@dataclass
class RetryPolicy:
    """Exponential-backoff knobs.

    Delay for attempt *n* (0-indexed) is::

        min(base_delay * backoff_factor**n, max_delay) ± jitter

    where jitter is up to ``jitter_factor`` of the capped delay, symmetric.
    """

    max_attempts: int = 3
    base_delay: float = 1.0
    max_delay: float = 30.0
    backoff_factor: float = 2.0
    jitter_factor: float = 0.25

    @classmethod
    def llm(cls) -> RetryPolicy:
        """Preset for LLM calls: 3 attempts, generous backoff."""

        return cls(max_attempts=3, base_delay=1.0, max_delay=30.0, backoff_factor=2.0)

    @classmethod
    def tool(cls) -> RetryPolicy:
        """Preset for tool calls: 2 attempts, tight backoff.

        Tools fail more often terminally (bad args) than transiently, so
        fewer attempts and a lower ceiling.
        """

        return cls(max_attempts=2, base_delay=0.5, max_delay=5.0, backoff_factor=2.0)


def _delay_for(policy: RetryPolicy, attempt: int) -> float:
    """Compute the sleep before the (attempt+1)-th try."""

    raw = policy.base_delay * (policy.backoff_factor**attempt)
    capped = min(raw, policy.max_delay)
    jitter = capped * policy.jitter_factor * (2 * random.random() - 1)
    return max(0.0, capped + jitter)


async def with_retry(
    coro_factory: Callable[[], Awaitable[T]],
    policy: RetryPolicy | None = None,
    *,
    on_retry: Callable[[int, BaseException, float], Any] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Await ``coro_factory()`` with retry per ``policy``.

    ``coro_factory`` is called once per attempt (so each retry gets a fresh
    coroutine). ``on_retry(attempt, exc, delay)`` fires before each sleep
    — it may be sync or async. ``sleep`` is injectable so tests can run
    without real waiting.

    The original exception is re-raised when the budget is exhausted (no
    wrapping), so callers see the real ``LLMError`` / ``TimeoutError``.
    """

    policy = policy or RetryPolicy.llm()
    attempt = 0
    while True:
        try:
            return await coro_factory()
        except Exception as exc:
            attempt += 1
            if (
                classify_exception(exc) is Disposition.TERMINAL
                or attempt >= policy.max_attempts
            ):
                raise
            delay = _delay_for(policy, attempt - 1)
            if on_retry is not None:
                try:
                    res = on_retry(attempt, exc, delay)
                    if asyncio.iscoroutine(res):
                        await res
                except Exception:  # noqa: BLE001 — observer must not break retry
                    logger.debug("on_retry callback raised", exc_info=True)
            logger.info(
                "retry attempt %d/%d after %.2fs: %s",
                attempt,
                policy.max_attempts,
                delay,
                exc,
            )
            await sleep(delay)
