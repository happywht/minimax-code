"""System-power listener — fusion of grok's ``xai-system-power`` (R41).

The cross-platform entry point. Selects a platform implementation at runtime
(:mod:`._windows` on win32, :mod:`._fallback` everywhere else — mirrors grok's
``#[cfg(target_os)]`` compile-time selection) and wraps it in a RAII
:class:`SystemPowerListener` whose ``close()`` / ``__del__`` unregisters the OS
callback (mirrors grok's ``Drop``).
"""

from __future__ import annotations

import sys

from .types import PowerCallback, PowerEvent, PowerState  # noqa: F401 (re-exported via __all__)

if sys.platform == "win32":  # pragma: no cover - platform branch
    from . import _windows as _impl
else:  # pragma: no cover - platform branch
    from . import _fallback as _impl


class SystemPowerListener:
    """A running system-power listener (grok ``SystemPowerListener``).

    On Windows, ``close()`` / drop unregisters the OS callback and releases the
    pinned ctypes objects. On unsupported platforms, ``start`` returns ``None``
    and no listener exists to close. Intended as a process-lifetime singleton.

    The ``_stop`` callable is the platform's unregister closure (``None`` after
    close).
    """

    __slots__ = ("_stop",)

    def __init__(self, stop):
        self._stop = stop

    @classmethod
    def start(cls, callback: PowerCallback) -> SystemPowerListener | None:
        """Start listening for system sleep/wake events.

        Returns ``None`` when the platform mechanism is unavailable (unsupported
        OS, or a registration failure) — callers treat ``None`` as "no power
        notifications" and degrade gracefully (the dependent feature simply does
        not engage). Mirrors grok's ``SystemPowerListener::start`` returning
        ``Option<Self>``.
        """
        stop = _impl.start(callback)
        if stop is None:
            return None
        return cls(stop)

    def close(self) -> None:
        """Unregister the OS callback (idempotent). Mirrors grok ``Drop``."""
        stop = self._stop
        if stop is not None:
            self._stop = None
            stop()

    def __del__(self):
        # Best-effort cleanup; never raise from __del__.
        try:
            self.close()
        except Exception:
            pass


def current_power_state() -> PowerState:
    """Query the current power state synchronously (grok ``current_power_state``).

    Cheap, non-blocking. Returns :class:`~.types.PowerState.Unknown` on platforms
    without a real implementation (currently every platform — the macOS
    FullWake/DarkWake query is deferred) or when the platform query fails.
    Callers must never block on ``Unknown``.
    """
    return _impl.current_power_state()


__all__ = [
    "SystemPowerListener",
    "current_power_state",
    "PowerEvent",
    "PowerState",
]
