"""``BreakerConfig`` — tuning knobs for :class:`CircuitBreaker` (R17).

Port of grok-build ``xai-circuit-breaker``'s ``config.rs``. Two named presets
(``server`` / ``client``) and an env loader (``MINIMAX_CODE_CB_*``). Frozen
dataclass so configs are safely shareable across breakers and registries.

Durations are ``float`` seconds (not ``datetime.timedelta``) to keep the
hot path in plain arithmetic — the breaker compares against a monotonic
clock that is already in seconds.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, replace

logger = logging.getLogger(__name__)

DEFAULT_FAILURE_CODES: frozenset[int] = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class BreakerConfig:
    """Sliding-window-with-min-samples tuning knobs.

    The breaker trips when ``sample_count >= min_samples`` AND
    ``error_rate >= error_rate_threshold`` over the live window.
    """

    window_duration: float = 60.0
    min_samples: int = 10
    error_rate_threshold: float = 0.5
    open_duration: float = 10.0
    half_open_max_probes: int = 1
    failure_codes: frozenset[int] = field(default_factory=lambda: DEFAULT_FAILURE_CODES)
    enabled: bool = True

    # ------------------------------------------------------------------
    # Named presets
    # ------------------------------------------------------------------

    @classmethod
    def server(cls) -> BreakerConfig:
        """Server preset (``min_samples=10``, ``error_rate=0.5``, 60s window,
        10s open duration, failure codes ``[429,500,502,503,504]``)."""
        return cls()

    @classmethod
    def client(cls) -> BreakerConfig:
        """Client preset (``min_samples=5``, ``error_rate=0.5``, 60s window,
        60s open duration, failure code ``[401]``)."""
        return cls(
            min_samples=5,
            open_duration=60.0,
            failure_codes=frozenset({401}),
        )

    # ------------------------------------------------------------------
    # Env loading
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls, prefix: str = "MINIMAX_CODE_CB_") -> BreakerConfig:
        """Load knobs from ``<prefix>*`` environment variables.

        Recognised keys: ``WINDOW_SECS``, ``MIN_SAMPLES``,
        ``ERROR_RATE_THRESHOLD``, ``OPEN_DURATION_SECS``,
        ``HALF_OPEN_MAX_PROBES``, ``FAILURE_CODES``, ``ENABLED``. Unparsable
        values log a warning and fall back to the default.
        """
        def get(key: str) -> str | None:
            return os.environ.get(prefix + key)

        def lookup_or(key: str, cast, default):
            raw = get(key)
            if raw is None:
                return default
            try:
                return cast(raw)
            except (TypeError, ValueError):
                logger.warning("env %s%s=%r failed to parse, using default", prefix, key, raw)
                return default

        raw_codes = get("FAILURE_CODES")
        codes = parse_failure_codes(raw_codes) if raw_codes is not None else DEFAULT_FAILURE_CODES
        if raw_codes is not None and not codes:
            logger.warning(
                "%sFAILURE_CODES=%r produced no valid codes, using defaults", prefix, raw_codes
            )
            codes = DEFAULT_FAILURE_CODES

        return cls(
            window_duration=lookup_or("WINDOW_SECS", float, 60.0),
            min_samples=lookup_or("MIN_SAMPLES", int, 10),
            error_rate_threshold=lookup_or("ERROR_RATE_THRESHOLD", float, 0.5),
            open_duration=lookup_or("OPEN_DURATION_SECS", float, 10.0),
            half_open_max_probes=max(1, lookup_or("HALF_OPEN_MAX_PROBES", int, 1)),
            failure_codes=codes,
            enabled=lookup_or("ENABLED", _parse_bool, True),
        )

    # ------------------------------------------------------------------
    # Predicates
    # ------------------------------------------------------------------

    def is_failure_status(self, status: int) -> bool:
        """``True`` if ``status`` is in the configured failure code set."""
        return status in self.failure_codes

    def with_half_open_floor(self) -> BreakerConfig:
        """Return a copy with ``half_open_max_probes`` clamped to ``>= 1``."""
        if self.half_open_max_probes >= 1:
            return self
        return replace(self, half_open_max_probes=1)


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def parse_failure_codes(s: str) -> frozenset[int]:
    """Parse a comma-separated list of status codes; invalid entries drop."""
    out: set[int] = set()
    for chunk in s.split(","):
        chunk = chunk.strip()
        if not chunk:
            continue
        try:
            out.add(int(chunk))
        except ValueError:
            # Silently drop invalid entries, mirroring grok.
            continue
    return frozenset(out)


__all__ = ["DEFAULT_FAILURE_CODES", "BreakerConfig", "parse_failure_codes"]
