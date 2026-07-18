"""Three-state circuit breaker with fail-open ``guard()`` (R17).

Forward-port of grok-build ``xai-circuit-breaker``'s ``breaker.rs``. Hot path
is :meth:`CircuitBreaker.check` (gate before a call) and
:meth:`CircuitBreaker.record` (feed outcome after). :meth:`CircuitBreaker.guard`
wraps both around a block and is fail-open by construction.

Python asyncio runs single-threaded under the GIL, so grok's atomics, CAS,
``OnceLock`` and ``Arc<Mutex>`` collapse to plain attribute access — that is
the main simplification dividend of the forward-port. The causal core is
preserved: three-state machine, sliding failure count with min-samples gate,
cool-down, bounded half-open probes with lease reaping.
"""

from __future__ import annotations

import contextlib
import logging
import time
from collections.abc import Callable

from .config import BreakerConfig
from .state import BreakerOpen, BreakerState, Outcome
from .window import SlidingWindow

logger = logging.getLogger(__name__)

# Monotonic clock callable: returns seconds. Injectable for deterministic tests.
Clock = Callable[[], float]


class Observer:
    """Hooks for breaker state transitions and per-outcome events.

    Override on a subclass to wire breaker events into telemetry/logs. Methods
    must not raise; since R21 the breaker wraps every dispatch in a fail-open
    guard (``_notify_state_change`` / ``_notify_outcome``), so a buggy observer
    can never corrupt the state machine — symmetric with the reliability stack.
    """

    def on_state_change(
        self, old: BreakerState, new: BreakerState, reason: str
    ) -> None:
        """Called after a CLOSED<->OPEN<->HALF_OPEN transition."""

    def on_outcome(self, outcome: Outcome) -> None:
        """Called for every recorded outcome (success or failure)."""


class NoopObserver(Observer):
    """Default observer: does nothing."""


class CircuitBreaker:
    """Synchronous three-state circuit breaker.

    Trips when ``sample_count >= min_samples`` AND
    ``error_rate >= error_rate_threshold`` over the live window. Stays OPEN
    for ``open_duration``, then admits up to ``half_open_max_probes`` probes;
    the first probe result closes (success) or re-opens (failure) the breaker.

    All mutation is plain attribute access — safe under the asyncio GIL.
    """

    def __init__(
        self,
        name: str,
        config: BreakerConfig | None = None,
        *,
        clock: Clock | None = None,
        observer: Observer | None = None,
    ) -> None:
        self.name = name
        self._config = config or BreakerConfig.server()
        self._clock: Clock = clock or time.monotonic
        self._observer = observer or NoopObserver()
        self._state = BreakerState.CLOSED
        self._window = SlidingWindow()
        # Monotonic deadline after which OPEN should consider half-opening.
        self._opened_until: float = 0.0
        # Probe slots claimed during HALF_OPEN; reset on close/trip.
        self._half_open_probes = 0
        # When the current probe slot was claimed; used to reap abandoned probes.
        self._probe_claimed_at: float = 0.0

    # ------------------------------------------------------------------
    # Read-only introspection
    # ------------------------------------------------------------------

    @property
    def state(self) -> BreakerState:
        """Current state, lazily transitioning OPEN->HALF_OPEN past cool-down."""
        if self._state is BreakerState.OPEN:
            self._check_open()
        return self._state

    @property
    def config(self) -> BreakerConfig:
        return self._config

    @property
    def is_open(self) -> bool:
        """``True`` while shedding (state is OPEN after lazy cool-down check)."""
        return self.state is BreakerState.OPEN

    def error_rate(self) -> float:
        return self._window.error_rate()

    def sample_count(self) -> int:
        return self._window.sample_count()

    # ------------------------------------------------------------------
    # Hot path
    # ------------------------------------------------------------------

    def check(self) -> None:
        """Gate a call; raise :class:`BreakerOpen` when shedding.

        In HALF_OPEN, claims one of ``half_open_max_probes`` probe slots
        (reaping an abandoned probe lease first if needed).
        """
        if not self._config.enabled:
            return
        state = self.state  # may transition OPEN -> HALF_OPEN
        if state is BreakerState.OPEN:
            raise BreakerOpen(max(0.0, self._opened_until - self._clock()))
        if state is BreakerState.HALF_OPEN:
            self._try_half_open_probe()

    def record(self, outcome: Outcome) -> None:
        """Feed a guarded call's outcome back; may transition state."""
        if not self._config.enabled:
            return
        self._notify_outcome(outcome)
        now = self._clock()
        self._window.evict(self._config.window_duration, now)
        state = self.state  # lazy OPEN -> HALF_OPEN
        if state is BreakerState.OPEN:
            # Defensive: check() normally gates calls, so OPEN + record
            # shouldn't happen. Don't accumulate samples while shedding.
            return
        if state is BreakerState.HALF_OPEN:
            if outcome is Outcome.SUCCESS:
                self._close("half-open probe succeeded")
            else:
                self._trip("half-open probe failed", now)
            return
        # CLOSED: accumulate samples and maybe trip.
        is_failure = outcome is Outcome.FAILURE
        self._window.push(is_failure, now)
        if (
            self._window.sample_count() >= self._config.min_samples
            and self._window.error_rate() >= self._config.error_rate_threshold
        ):
            self._trip("error-rate threshold breached", now)

    # ------------------------------------------------------------------
    # State transitions (private)
    # ------------------------------------------------------------------

    def _check_open(self) -> None:
        """Transition OPEN -> HALF_OPEN once cool-down has elapsed."""
        if self._state is not BreakerState.OPEN:
            return
        if self._clock() >= self._opened_until:
            self._half_open_probes = 0
            self._set_state(BreakerState.HALF_OPEN, "cool-down elapsed")

    def _try_half_open_probe(self) -> None:
        """Claim a probe slot in HALF_OPEN, reaping an abandoned lease first."""
        now = self._clock()
        lease = self._config.open_duration
        # Reap an abandoned probe: if a claimed slot hasn't reported back
        # within open_duration, recycle it so the breaker can't wedge.
        if (
            self._half_open_probes >= self._config.half_open_max_probes
            and self._probe_claimed_at + lease <= now
        ):
            self._half_open_probes = 0
        if self._half_open_probes >= self._config.half_open_max_probes:
            raise BreakerOpen(max(0.0, (self._probe_claimed_at + lease) - now))
        self._half_open_probes += 1
        self._probe_claimed_at = now

    def _trip(self, reason: str, now: float) -> None:
        self._opened_until = now + self._config.open_duration
        self._half_open_probes = 0
        self._set_state(BreakerState.OPEN, reason)

    def _close(self, reason: str) -> None:
        self._window.clear()
        self._half_open_probes = 0
        self._set_state(BreakerState.CLOSED, reason)

    def _set_state(self, new: BreakerState, reason: str) -> None:
        old = self._state
        if old is new:
            return
        self._state = new
        self._notify_state_change(old, new, reason)

    # ------------------------------------------------------------------
    # Observer dispatch (R21) — fail-open, symmetric with reliability stack
    # ------------------------------------------------------------------

    def attach_observer(self, observer: Observer) -> None:
        """Replace the active observer (idempotent swap).

        Used by the registry's observer factory so a telemetry-backed observer
        can be (re)wired without recreating the breaker. Mirrors
        :meth:`minimax_code.agent.reliability.CircuitBreaker.attach_observer`.
        """

        self._observer = observer

    def _notify_state_change(
        self, old: BreakerState, new: BreakerState, reason: str
    ) -> None:
        """Fail-open dispatch: a buggy observer must never corrupt the breaker."""

        try:
            self._observer.on_state_change(old, new, reason)
        except Exception:  # noqa: BLE001 - observer must not break the state machine
            logger.debug(
                "%s: observer.on_state_change failed", self.name, exc_info=True
            )

    def _notify_outcome(self, outcome: Outcome) -> None:
        """Fail-open dispatch for per-outcome hooks."""

        try:
            self._observer.on_outcome(outcome)
        except Exception:  # noqa: BLE001 - observer must not break the state machine
            logger.debug("%s: observer.on_outcome failed", self.name, exc_info=True)

    # ------------------------------------------------------------------
    # High-level fail-open API
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def guard(self):
        """Wrap a call block: check before, record outcome after.

        Fail-open by construction. A breaker-internal fault (buggy observer,
        a clock that raises, …) is logged at debug and swallowed so the
        guarded business call still runs. The business call's own exceptions
        are recorded as FAILURE and re-raised; a :class:`BreakerOpen` from the
        pre-check propagates unchanged.

        Synchronous: for ``await``-able call sites use ``check()`` /
        ``record()`` directly around the awaited expression.
        """
        try:
            self.check()
        except BreakerOpen:
            raise
        except Exception:  # noqa: BLE001 - breaker internals must not block business
            logger.debug("%s: pre-check failed; fail-open", self.name, exc_info=True)
        outcome = Outcome.SUCCESS
        try:
            yield
        except BreakerOpen:
            raise
        except BaseException:
            outcome = Outcome.FAILURE
            raise
        finally:
            try:
                self.record(outcome)
            except Exception:  # noqa: BLE001 - breaker internals must not block business
                logger.debug("%s: record failed; fail-open", self.name, exc_info=True)


__all__ = ["CircuitBreaker", "Clock", "NoopObserver", "Observer"]
