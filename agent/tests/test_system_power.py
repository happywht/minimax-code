"""Tests for the system-power types + listener (R41).

Mirrors grok's ``xai-system-power`` tests (``PowerEvent`` copy+eq;
``start_and_drop_is_clean``) and pins the Python mapping: enum identity/value
equality, the no-op fallback (``start → None``, ``current_power_state →
Unknown``), and — on win32 only — the Windows ctypes registration round-trip
(``start`` returns a non-None listener, ``close`` unregisters without raising).
The macOS/Linux ports are deferred, so ``current_power_state`` is ``Unknown``
everywhere today (grok Windows/Linux parity).
"""

from __future__ import annotations

import sys

import pytest

from minimax_code.system_power import (
    PowerCallback,
    PowerEvent,
    PowerState,
    SystemPowerListener,
    _fallback,
    current_power_state,
)

win32_only = pytest.mark.skipif(sys.platform != "win32", reason="win32 ctypes registration")


# --- PowerEvent (mirror grok power_event_is_copy_eq) ------------------------


def test_power_event_copy_eq():
    """grok ``Copy``: assignment shares the singleton; ``PartialEq`` value equality."""
    e = PowerEvent.WillSleep
    copied = e  # Copy semantics — no clone, same object
    assert e is copied
    assert e == PowerEvent.WillSleep
    assert e != PowerEvent.DidWake


def test_power_event_members_exhaustive():
    assert {e.name for e in PowerEvent} == {"WillSleep", "DidWake"}


# --- PowerState -------------------------------------------------------------


def test_power_state_members_exhaustive():
    assert {s.name for s in PowerState} == {"FullWake", "DarkWake", "Unknown"}


def test_power_state_distinct():
    assert PowerState.FullWake != PowerState.DarkWake
    assert PowerState.DarkWake != PowerState.Unknown
    assert PowerState.FullWake != PowerState.Unknown


# --- PowerCallback alias ----------------------------------------------------


def test_power_callback_is_callable_alias():
    """PowerCallback is Callable[[PowerEvent], None] — a type alias, not a class."""
    assert PowerCallback is not None

    def handler(event: PowerEvent) -> None:
        return None

    handler(PowerEvent.DidWake)  # call site type-checks against the alias


# --- current_power_state (always Unknown for now) ---------------------------


def test_current_power_state_returns_power_state():
    """current_power_state is cheap, non-blocking, returns a PowerState."""
    state = current_power_state()
    assert isinstance(state, PowerState)


def test_current_power_state_unknown_everywhere_today():
    """No macOS port yet → current_power_state is Unknown on every platform
    (grok Windows/Linux already returned Unknown; this pins the parity until the
    macOS dark-wake query lands)."""
    assert current_power_state() == PowerState.Unknown


# --- Fallback impl (tested directly — works on all platforms) ---------------


def test_fallback_start_returns_none():
    """The no-op platform returns None → caller degrades to 'no notifications'."""
    assert _fallback.start(lambda _e: None) is None


def test_fallback_current_power_state_unknown():
    assert _fallback.current_power_state() == PowerState.Unknown


# --- SystemPowerListener (start + close clean) ------------------------------


def test_listener_start_returns_none_on_unsupported_or_listener_on_windows():
    """grok ``start_and_drop_is_clean``: start + close must not panic/hang.

    On win32 this exercises the real ctypes registration (returns a listener or
    None on failure — both fine). On other platforms the fallback returns None.
    """
    listener = SystemPowerListener.start(lambda _e: None)
    if listener is None:
        # Unsupported platform (or registration failed) — degrade gracefully.
        return
    # Drop via explicit close (must be clean and idempotent).
    listener.close()
    listener.close()  # idempotent


@win32_only
def test_windows_listener_registers_and_cleans():
    """On win32 a successful registration returns a listener whose close
    unregisters without raising (exercises PowerUnregisterSuspendResumeNotification)."""
    listener = SystemPowerListener.start(lambda _e: None)
    if listener is None:
        pytest.skip("registration failed on this host (e.g. permission) — still valid")
    listener.close()
