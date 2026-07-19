"""System-power event/state types — fusion of grok's ``xai-system-power`` (R41).

The host-agnostic vocabulary for cross-platform sleep/wake (suspend/resume)
notifications. Pure enums + a callback type alias; the platform listener and
FFI wiring live in :mod:`._windows` / :mod:`._fallback` (selected by
:mod:`.listener` at runtime, mirroring grok's ``#[cfg(target_os)]``).

Mapping
-------

Both ``PowerEvent`` and ``PowerState`` are grok unit enums with
``#[derive(Debug, Clone, Copy, PartialEq, Eq)]`` (no payload) → Python
``@unique enum.Enum`` — the same mapping applied to every plain unit enum since
R35. ``Copy`` semantics are automatic for Python enums (assignment shares the
singleton, no clone cost); ``PartialEq`` is value equality by identity. grok's
``Box<dyn Fn(PowerEvent) + Send + Sync + 'static>`` callback alias →
``Callable[[PowerEvent], None]``.

Product fusion
--------------

MiniMax Code's agent runs long-lived loops — LLM streaming, APScheduler cron
jobs, secret refresh, multi-round tool execution. When the OS suspends
mid-flight, asyncio timers stall and in-flight network responses can be lost
(the motivating case in grok: a rotated OIDC refresh-token response dropped
across a suspend boundary, leaving the client holding a dead token).
``PowerEvent.WillSleep`` lets a sleep-gate defer *starting* irreversible work;
``DidWake`` lets it compensate (re-fetch, re-schedule). This package is the
vocabulary; the wiring (AuthManager-style sleep gate, scheduler suspend-aware
deferral) is a future round.
"""

from __future__ import annotations

from collections.abc import Callable
from enum import Enum, unique


@unique
class PowerEvent(Enum):
    """A system power transition (grok ``PowerEvent``).

    ``WillSleep`` is best-effort: the idle-sleep negotiation can be vetoed (by
    any power client), so it is *not* a guarantee that sleep follows — it may
    be succeeded by ``DidWake`` without an intervening suspend. Handlers must
    be idempotent and safe to "cancel" via a subsequent ``DidWake``.
    """

    #: The system is about to sleep (lid close / suspend), or negotiating idle sleep.
    WillSleep = "will_sleep"
    #: The system resumed, or a previously announced sleep was cancelled.
    DidWake = "did_wake"


@unique
class PowerState(Enum):
    """Coarse, synchronously-queryable power state (grok ``PowerState``).

    The motivating distinction is **dark wake** (macOS): a brief background wake
    with the display off, which may re-sleep at any moment — frequently without
    delivering any :class:`PowerEvent` at all. Code that starts irreversible
    network work should avoid doing so during a dark wake.
    """

    #: Full / user wake: display present — safe to start irreversible work.
    FullWake = "full_wake"
    #: Dark wake: CPU up for background work, display off — may re-sleep anytime.
    DarkWake = "dark_wake"
    #: State could not be determined — callers treat as "no signal", never block on it.
    Unknown = "unknown"


#: Boxed user callback invoked on each :class:`PowerEvent` (grok ``PowerCallback``).
#:
#: In grok this is ``Box<dyn Fn(PowerEvent) + Send + Sync + 'static>`` — invoked
#: from a platform event thread, so it must be cheap and non-blocking. In Python
#: the platform thread invokes it the same way (the Windows callback fires on an
#: OS thread); keep it short.
PowerCallback = Callable[[PowerEvent], None]


__all__ = [
    "PowerEvent",
    "PowerState",
    "PowerCallback",
]
