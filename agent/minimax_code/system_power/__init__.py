"""System power — fusion of grok's ``xai-system-power`` (R41).

Cross-platform system sleep/wake (suspend/resume) notifications. MiniMax Code's
agent runs long-lived loops (LLM streaming, APScheduler cron, secret refresh,
multi-round tools); when the OS suspends mid-flight, asyncio timers stall and
in-flight network responses can be lost. This package gives the agent a
vocabulary to defer irreversible work across a suspend boundary and compensate
on wake.

Scope of this package
---------------------

* :mod:`.types` (R41) — :class:`~.types.PowerEvent` (``WillSleep`` / ``DidWake``)
  and :class:`~.types.PowerState` (``FullWake`` / ``DarkWake`` / ``Unknown``)
  enums + the :data:`~.types.PowerCallback` alias. Host-agnostic vocabulary.
* :mod:`.listener` (R41) — :class:`~.listener.SystemPowerListener` (RAII;
  ``start → Optional``, ``close`` unregisters) +
  :func:`~.listener.current_power_state`. Selects the platform impl at runtime.
* :mod:`._windows` (R41) — Windows ctypes impl
  (``PowerRegisterSuspendResumeNotification`` + ``DEVICE_NOTIFY_CALLBACK``).
  Only imported on win32.
* :mod:`._fallback` (R41) — no-op for unsupported platforms
  (``start → None``, ``current_power_state → Unknown``).

What is NOT here (environment-expansion round): the macOS IOKit port (grok
``macos.rs``, 367 lines — would need pyobjc) and the Linux logind D-Bus port
(grok ``linux.rs``, 93 lines — would need dbus). Both are deferred until
MiniMax Code targets those OSes; the fallback gives clean "no notifications"
degradation on them today. The macOS dark-wake (FullWake vs DarkWake) sync query
is likewise deferred, so ``current_power_state`` returns ``Unknown`` everywhere
for now (matching grok's Windows/Linux behavior).
"""

from __future__ import annotations

from .listener import SystemPowerListener, current_power_state
from .types import PowerCallback, PowerEvent, PowerState

__all__ = [
    "PowerEvent",
    "PowerState",
    "PowerCallback",
    "SystemPowerListener",
    "current_power_state",
]
