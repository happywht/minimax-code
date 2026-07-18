"""Circuit-breaker integration helpers for LLM transports (R18).

Three small fail-open adapters sit between :class:`CircuitBreaker` (R17) and
the Anthropic / OpenAI transports. The transports' ``stream_chat`` is an
async generator, so the synchronous ``guard()`` context manager from R17
doesn't fit (it cannot wrap a generator that yields across await points).
Instead each transport calls these three helpers by hand:

* :func:`resolve_breaker` — once per stream, to fetch the breaker (or ``None``)
* :func:`check_or_raise` — before the SDK call, to fast-fail when shedding
* :func:`record_outcome` — after the stream settles, to feed the health window

Fail-open ironclad: a missing registry, a disabled breaker, or any internal
breaker fault NEVER blocks the LLM call. The business path runs unprotected
rather than not at all — the breaker is observation and protection, never a
dependency.
"""

from __future__ import annotations

import logging

from ...resilience import BreakerOpen, CircuitBreaker, Outcome
from ..types import LLMError

logger = logging.getLogger(__name__)


def resolve_breaker(key: str) -> CircuitBreaker | None:
    """Return the breaker for *key* from the app registry, or ``None``.

    Imports :mod:`minimax_code.app` lazily inside the function body to dodge
    the transports → app → core → llm → transports import cycle (the same
    trick the tool layer uses for ``ensure_fs_bus``). Any failure — import
    error, registry build fault, disabled config, ``get`` fault — resolves to
    ``None``; callers treat ``None`` as "proceed unprotected".
    """
    try:
        from ...app import ensure_breaker_registry
    except Exception:  # noqa: BLE001 - env without app / import cycle
        return None
    try:
        reg = ensure_breaker_registry()
    except Exception:  # noqa: BLE001 - registry build fault; fail-open
        logger.debug("breaker registry unavailable for %s; fail-open", key, exc_info=True)
        return None
    if reg is None:
        return None
    try:
        return reg.get(key)
    except Exception:  # noqa: BLE001 - registry access fault; fail-open
        logger.debug("breaker get(%s) failed; fail-open", key, exc_info=True)
        return None


def check_or_raise(breaker: CircuitBreaker | None) -> None:
    """Pre-check *breaker*; raise :class:`LLMError` (503) if it is shedding.

    ``BreakerOpen`` (the breaker is OPEN, or HALF_OPEN with no free probe
    slot) is translated to ``LLMError(status_code=503)`` so the existing
    transport error contract still holds — callers see a
    service-unavailable error of the same type they already handle for
    upstream 503s. Any other internal breaker fault is swallowed (fail-open).
    """
    if breaker is None:
        return
    try:
        breaker.check()
    except BreakerOpen as exc:
        raise LLMError(
            f"LLM circuit breaker '{breaker.name}' open; "
            f"retry after {exc.retry_after:.1f}s",
            status_code=503,
        ) from exc
    except Exception:  # noqa: BLE001 - breaker internal fault; fail-open
        logger.debug("%s: check failed; fail-open", breaker.name, exc_info=True)


def record_outcome(
    breaker: CircuitBreaker | None,
    *,
    success: bool,
    status_code: int | None = None,
) -> None:
    """Record a stream outcome on *breaker* (fail-open).

    Sample selection rules (mirror grok-build ``xai-circuit-breaker``):

    * A successful stream always records ``SUCCESS``.
    * A failed stream records ``FAILURE`` only when ``status_code`` is one
      the breaker considers a downstream failure
      (:meth:`BreakerConfig.is_failure_status`). A client bug (400 bad
      request, 401 unauthorised, 404 not found) is the caller's fault, not
      a sign the model endpoint is sick, so it must not pollute the health
      window and risk tripping the breaker on a self-inflicted wound.
    * A failed stream with unknown status (``None`` — e.g. a connection
      drop or timeout flattened by the SDK) records ``FAILURE``
      conservatively: it could be downstream sickness, and the window's
      ``min_samples`` / ``error_rate`` thresholds absorb isolated blips.

    Internal breaker faults are swallowed (fail-open).
    """
    if breaker is None:
        return
    if not success:
        if status_code is not None and not breaker.config.is_failure_status(status_code):
            return  # client-side error: neutral, not a health sample
        outcome: Outcome = Outcome.FAILURE
    else:
        outcome = Outcome.SUCCESS
    try:
        breaker.record(outcome)
    except Exception:  # noqa: BLE001 - breaker internal fault; fail-open
        logger.debug("%s: record failed; fail-open", breaker.name, exc_info=True)


__all__ = ["check_or_raise", "record_outcome", "resolve_breaker"]
