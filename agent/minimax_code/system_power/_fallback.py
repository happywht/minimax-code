"""No-op fallback for platforms without a power-listener implementation (R41).

Mirrors grok's ``#[cfg(not(any(target_os = ...)))]`` no-op module:
``start`` returns ``None`` (caller degrades gracefully — "no power
notifications") and ``current_power_state`` returns :class:`~.types.PowerState.Unknown`.

This is the implementation selected on every platform except win32 (where
:mod:`._windows` is used). The macOS (IOKit / pyobjc) and Linux (logind D-Bus)
ports are deferred to an environment-expansion round — YAGNI until MiniMax Code
targets those OSes; the fallback gives clean "no notifications" degradation on
them today.
"""

from __future__ import annotations

from collections.abc import Callable

from .types import PowerCallback, PowerState


def start(callback: PowerCallback) -> Callable[[], None] | None:
    """Unsupported platform → ``None`` (caller degrades to "no notifications")."""
    del callback  # unused — no mechanism to subscribe on this platform
    return None


def current_power_state() -> PowerState:
    """Unknown on unsupported platforms — callers must not block on this."""
    return PowerState.Unknown


__all__ = ["start", "current_power_state"]
