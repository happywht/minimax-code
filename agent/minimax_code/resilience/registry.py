"""Per-key :class:`CircuitBreaker` registry (R17).

Port of grok-build ``xai-circuit-breaker``'s ``registry.rs``. Lazily creates
breakers per logical key (e.g. ``"llm:anthropic"``, ``"tool:http"``) so
callers ask for a breaker by name without caring about construction. When
``enabled`` is False on the config, :meth:`get` returns ``None`` — callers
treat ``None`` as "no protection" and proceed unprotected (fail-open at the
registry layer, mirroring the per-breaker fail-open contract).
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator

from .breaker import CircuitBreaker, Observer
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
        # R21: optional observer factory. When set, every newly-created breaker
        # gets the factory's observer attached, so the whole breaker fleet is
        # observable through one wiring point. Retrofit-friendly: existing
        # breakers are rewired too via ``attach_observer_factory``.
        self._observer_factory: Callable[[str], Observer | None] | None = None

    def attach_observer_factory(
        self, factory: Callable[[str], Observer | None] | None
    ) -> None:
        """Install an observer factory; retrofits already-materialised breakers.

        Each new breaker built by :meth:`get` gets ``factory(key)`` attached
        (skipped when the factory returns ``None``). Existing breakers are
        rewired in place so the factory takes effect immediately for the whole
        fleet — this is the hook ``app.py`` uses to point every transport
        breaker at the shared telemetry observer.
        """

        self._observer_factory = factory
        if factory is None:
            return
        for key, br in self._breakers.items():
            observer = factory(key)
            if observer is not None:
                br.attach_observer(observer)

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
            observer = (
                self._observer_factory(key) if self._observer_factory else None
            )
            br = CircuitBreaker(key, self._config, observer=observer)
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
