"""Asyncio circuit breaker — fused from grok-build's ``xai-circuit-breaker``.

Three-state machine (Closed/Open/HalfOpen) with a sliding-window error-rate
trigger. Unlike grok's crate — which only guards the GCS storage client —
MiniMax wires this in front of the LLM call and per-tool dispatch: the two
paths that today hard-fail a whole turn on the first 429/503 or on a buggy
tool looping 12× before ``max_iterations`` kicks in.

Semantics kept identical to grok:

- ``check()`` before the call (raises :class:`BreakerOpen` to veto),
  ``record(Outcome)`` after.
- :meth:`BreakerConfig.server` / :meth:`BreakerConfig.client` presets +
  :meth:`BreakerConfig.from_env` reading ``CB_*``.
- :class:`CircuitBreakerRegistry` lazily creates one breaker per key
  (LLM endpoint / tool name).
- :class:`BreakerOpen` carries ``retry_after`` so callers can surface a hint.

Drop list (replaced by asyncio primitives): ``Arc<RwLock>`` → ``asyncio.Lock``,
``VecDeque<(Instant, Outcome)>`` → ``collections.deque`` of monotonic pairs.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field
from enum import StrEnum

logger = logging.getLogger(__name__)


class BreakerState(StrEnum):
    """The three breaker states, mirroring grok's ``BreakerState``."""

    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class Outcome(StrEnum):
    """Per-call outcome recorded after the guarded call returns."""

    SUCCESS = "success"
    FAILURE = "failure"


class Observer:
    """Hooks for breaker state transitions and per-outcome events (R20).

    Fuses grok-build ``xai-circuit-breaker``'s ``Observer`` trait. Override on
    a subclass to wire breaker events into telemetry. Methods must not raise
    — :class:`CircuitBreaker` wraps every call in a fail-open guard so a buggy
    observer can never corrupt the state machine or block the LLM hot path.

    The interface is intentionally identical to the one in
    :mod:`minimax_code.resilience.breaker` so a single adapter can bridge
    either stack to the telemetry engine.
    """

    def on_state_change(
        self, old: BreakerState, new: BreakerState, reason: str
    ) -> None:
        """Called after a CLOSED<->OPEN<->HALF_OPEN transition.

        ``reason`` is a stable lowercase string from grok's closed set —
        ``"trip"`` (error-rate exceeded), ``"open_elapsed"`` (cool-down
        elapsed), ``"probe_success"`` / ``"probe_failure"`` (half-open probe
        result). Stable strings let frontends map reasons to UI without
        parsing free text.
        """

    def on_outcome(self, outcome: Outcome) -> None:
        """Called for every recorded outcome (success or failure)."""


class NoopObserver(Observer):
    """Default observer: does nothing (zero overhead when telemetry is off)."""


# HTTP-style failure signals. Callers may ``record()`` with a raw status
# code; codes outside this set are coerced to SUCCESS. Mirrors grok's
# ``BreakerConfig.failure_codes`` default of {429,500,502,503,504}.
DEFAULT_FAILURE_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


class BreakerOpen(Exception):
    """Raised by :meth:`CircuitBreaker.check` when a call is not admitted.

    ``retry_after`` (seconds) is a hint derived from the remaining
    open-duration — not a server promise.
    """

    def __init__(
        self, retry_after: float, state: BreakerState = BreakerState.OPEN
    ) -> None:
        self.retry_after = retry_after
        self.state = state
        super().__init__(f"circuit breaker {state.value}: retry after {retry_after:.1f}s")


@dataclass
class BreakerConfig:
    """Tunable knobs — mirrors grok's ``BreakerConfig`` field-for-field."""

    window_duration: float = 60.0
    min_samples: int = 5
    error_rate_threshold: float = 0.5
    open_duration: float = 60.0
    half_open_max_probes: int = 1
    failure_codes: frozenset[int] = field(
        default_factory=lambda: DEFAULT_FAILURE_CODES
    )
    enabled: bool = True

    @classmethod
    def server(cls) -> BreakerConfig:
        """Preset for server-side calls (LLM API): short cool-down.

        A short ``open_duration`` is intentional — the LLM endpoint is
        shared and recovery is usually fast, so we re-probe sooner than
        the client preset.
        """
        return cls(
            window_duration=60.0,
            min_samples=5,
            error_rate_threshold=0.5,
            open_duration=30.0,
            half_open_max_probes=1,
        )

    @classmethod
    def client(cls) -> BreakerConfig:
        """Preset for client-side calls (tools / storage): longer cool-down.

        Fewer ``min_samples`` because a single tool call is already
        expensive; a longer ``open_duration`` because a flaky tool rarely
        recovers in seconds.
        """
        return cls(
            window_duration=60.0,
            min_samples=3,
            error_rate_threshold=0.5,
            open_duration=60.0,
            half_open_max_probes=1,
        )

    @classmethod
    def from_env(cls, prefix: str = "CB") -> BreakerConfig:
        """Build a config from ``<PREFIX>_*`` env vars (all optional)."""

        def _f(name: str, cast: type, default):
            raw = os.environ.get(f"{prefix}_{name}")
            return cast(raw) if raw is not None else default

        return cls(
            window_duration=_f("WINDOW", float, 60.0),
            min_samples=_f("MIN_SAMPLES", int, 5),
            error_rate_threshold=_f("ERROR_RATE", float, 0.5),
            open_duration=_f("OPEN_DURATION", float, 60.0),
            half_open_max_probes=_f("HALF_OPEN_PROBES", int, 1),
            enabled=os.environ.get(f"{prefix}_ENABLED", "1")
            not in ("0", "", "false", "False"),
        )


class CircuitBreaker:
    """Sliding-window error-rate breaker — asyncio-native.

    Concurrency model: a single ``asyncio.Lock`` guards state transitions
    and the sample window. Both ``check`` and ``record`` are coroutines;
    do not call them from sync code.
    """

    def __init__(
        self,
        config: BreakerConfig | None = None,
        *,
        name: str = "default",
        observer: Observer | None = None,
    ) -> None:
        self.config = config or BreakerConfig.server()
        self.name = name
        self._state: BreakerState = BreakerState.CLOSED
        self._opened_at: float = 0.0
        self._samples: deque[tuple[float, Outcome]] = deque()
        self._half_open_inflight = 0
        self._lock = asyncio.Lock()
        self._observer: Observer = observer or NoopObserver()

    def attach_observer(self, observer: Observer) -> None:
        """Attach or replace the observer (R20).

        Idempotent re-attach of the same observer is safe — callers that fetch
        a breaker from :class:`CircuitBreakerRegistry` on every turn re-attach
        the same telemetry-backed observer without duplicating events.
        """
        self._observer = observer

    def _notify_state_change(
        self, old: BreakerState, new: BreakerState, reason: str
    ) -> None:
        """Fail-open observer dispatch — never lets telemetry break the gate."""
        try:
            self._observer.on_state_change(old, new, reason)
        except Exception:  # noqa: BLE001 — observer fault must not corrupt state
            logger.debug(
                "breaker %s observer.on_state_change failed", self.name, exc_info=True
            )

    def _notify_outcome(self, outcome: Outcome) -> None:
        """Fail-open observer dispatch for per-outcome hooks."""
        try:
            self._observer.on_outcome(outcome)
        except Exception:  # noqa: BLE001 — observer fault must not corrupt state
            logger.debug(
                "breaker %s observer.on_outcome failed", self.name, exc_info=True
            )

    @property
    def state(self) -> BreakerState:
        return self._state

    def is_open(self) -> bool:
        return self._state is BreakerState.OPEN

    def error_rate(self) -> float:
        """Current window failure rate (0.0–1.0), lock-free read."""

        return self._error_rate_locked()

    def _error_rate_locked(self) -> float:
        if not self._samples:
            return 0.0
        fails = sum(1 for _, o in self._samples if o is Outcome.FAILURE)
        return fails / len(self._samples)

    def _prune(self, now: float) -> None:
        cutoff = now - self.config.window_duration
        while self._samples and self._samples[0][0] < cutoff:
            self._samples.popleft()

    async def check(self) -> None:
        """Raise :class:`BreakerOpen` if the breaker will not admit a call.

        - **CLOSED**: always admits.
        - **OPEN**: admits only after ``open_duration`` elapsed, at which
          point it transitions to HALF_OPEN.
        - **HALF_OPEN**: admits up to ``half_open_max_probes`` concurrent
          probe calls; the rest are vetoed.
        """

        if not self.config.enabled:
            return
        async with self._lock:
            now = time.monotonic()
            if self._state is BreakerState.OPEN:
                elapsed = now - self._opened_at
                if elapsed >= self.config.open_duration:
                    self._state = BreakerState.HALF_OPEN
                    self._half_open_inflight = 0
                    logger.info("breaker %s OPEN→HALF_OPEN", self.name)
                    self._notify_state_change(
                        BreakerState.OPEN, BreakerState.HALF_OPEN, "open_elapsed"
                    )
                else:
                    raise BreakerOpen(
                        self.config.open_duration - elapsed,
                        BreakerState.OPEN,
                    )
            if self._state is BreakerState.HALF_OPEN:
                if self._half_open_inflight >= self.config.half_open_max_probes:
                    raise BreakerOpen(
                        self.config.open_duration, BreakerState.HALF_OPEN
                    )
                self._half_open_inflight += 1
            # CLOSED: admit unconditionally.

    async def record(
        self, outcome: Outcome, *, status_code: int | None = None
    ) -> None:
        """Record a call outcome; drives state transitions.

        If ``status_code`` is given and not in ``failure_codes``, a FAILURE
        is coerced to SUCCESS (e.g. a 200 returned through the same path).
        """

        if not self.config.enabled:
            return
        if (
            outcome is Outcome.FAILURE
            and status_code is not None
            and status_code not in self.config.failure_codes
        ):
            outcome = Outcome.SUCCESS
        async with self._lock:
            now = time.monotonic()
            self._samples.append((now, outcome))
            self._prune(now)
            if self._state is BreakerState.HALF_OPEN:
                self._half_open_inflight = max(0, self._half_open_inflight - 1)
                if outcome is Outcome.FAILURE:
                    self._trip(now, reason="probe_failure")
                else:
                    self._state = BreakerState.CLOSED
                    self._samples.clear()
                    logger.info("breaker %s HALF_OPEN→CLOSED", self.name)
                    self._notify_state_change(
                        BreakerState.HALF_OPEN, BreakerState.CLOSED, "probe_success"
                    )
            elif self._state is BreakerState.CLOSED:
                if (
                    len(self._samples) >= self.config.min_samples
                    and self._error_rate_locked()
                    >= self.config.error_rate_threshold
                ):
                    self._trip(now)
            self._notify_outcome(outcome)

    def _trip(self, now: float, *, reason: str = "trip") -> None:
        old = self._state
        self._state = BreakerState.OPEN
        self._opened_at = now
        logger.warning(
            "breaker %s tripped OPEN: error_rate=%.2f over %d samples",
            self.name,
            self._error_rate_locked(),
            len(self._samples),
        )
        self._notify_state_change(old, BreakerState.OPEN, reason)


class CircuitBreakerRegistry:
    """Lazily creates one breaker per key (LLM endpoint / tool name).

    Mirrors grok's ``CircuitBreakerRegistry``: a shared map keyed by the
    resource under protection so independent endpoints/tools get isolated
    failure isolation rather than one shared breaker.
    """

    def __init__(self, default_config: BreakerConfig | None = None) -> None:
        self._default = default_config or BreakerConfig.server()
        self._breakers: dict[str, CircuitBreaker] = {}
        self._lock = asyncio.Lock()

    async def get_or_create(
        self, key: str, config: BreakerConfig | None = None
    ) -> CircuitBreaker:
        async with self._lock:
            if key not in self._breakers:
                self._breakers[key] = CircuitBreaker(
                    config or self._default, name=key
                )
            return self._breakers[key]

    def get(self, key: str) -> CircuitBreaker | None:
        return self._breakers.get(key)

    def all_states(self) -> dict[str, BreakerState]:
        return {k: b.state for k, b in self._breakers.items()}
