"""Per-key :class:`CircuitBreaker` registry (R17).

Port of grok-build ``xai-circuit-breaker``'s ``registry.rs``. Lazily creates
breakers per logical key (e.g. ``"llm:anthropic"``, ``"tool:http"``) so
callers ask for a breaker by name without caring about construction. When
``enabled`` is False on the config, :meth:`get` returns ``None`` — callers
treat ``None`` as "no protection" and proceed unprotected (fail-open at the
registry layer, mirroring the per-breaker fail-open contract).
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator

from .breaker import CircuitBreaker
from .config import BreakerConfig


class CircuitBreakerRegistry:
    """Lazily-constructed, per-key breaker map.

    :meth:`get` returns the same :class:`CircuitBreaker` instance for the
    same key on every call, creating it on first access from the registry's
    shared config. Returns ``None`` when disabled so callers can
    short-circuit (``if breaker: breaker.check()``).
    """

    def __init__(self, config: BreakerConfig | None = None) -> None:
        self._config = config or BreakerConfig.server()
        self._breakers: dict[str, CircuitBreaker] = {}

    @property
    def config(self) -> BreakerConfig:
        return self._config

    @property
    def enabled(self) -> bool:
        return self._config.enabled

    def get(self, key: str) -> CircuitBreaker | None:
        """Return the breaker for ``key``, or ``None`` if the registry is disabled."""
        if not self._config.enabled:
            return None
        br = self._breakers.get(key)
        if br is None:
            br = CircuitBreaker(key, self._config)
            self._breakers[key] = br
        return br

    def keys(self) -> Iterator[str]:
        """Iterate over keys that have been materialised so far."""
        return iter(self._breakers)

    def known_keys(self) -> Iterable[str]:
        """Materialised keys as a snapshot list (convenience for telemetry)."""
        return list(self._breakers)

    def clear(self) -> None:
        """Drop all materialised breakers (state is not preserved)."""
        self._breakers.clear()


__all__ = ["CircuitBreakerRegistry"]
