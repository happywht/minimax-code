"""Windows system sleep/wake via ``PowerRegisterSuspendResumeNotification``
(``DEVICE_NOTIFY_CALLBACK`` recipient, Windows 8+) — fusion of grok's
``xai-system-power`` windows.rs (R41).

Translates ``PBT_APMSUSPEND`` → :class:`~.types.PowerEvent.WillSleep` and
``PBT_APMRESUMEAUTOMATIC`` / ``PBT_APMRESUMESUSPEND`` →
:class:`~.types.PowerEvent.DidWake`. A single resume can deliver *both* resume
events, so ``DidWake`` may fire twice per wake — that is intentional and
harmless (the sleep-gate lowering is idempotent), mirroring grok windows.rs; do
not try to dedupe it.

The ctypes callback object and the params struct are pinned by the returned
``stop`` closure (mirrors grok's ``Box::into_raw`` heap pinning — the OS holds a
raw pointer to the params for the registration lifetime). Calling ``stop``
unregisters and releases that pin. Only imported on win32.
"""

from __future__ import annotations

import ctypes
from collections.abc import Callable
from ctypes import wintypes

from .types import PowerCallback, PowerEvent, PowerState

# PBT_ power-broadcast event types (WM_POWERBROADCAST wParam).
_PBT_APMSUSPEND = 0x0004
_PBT_APMRESUMESUSPEND = 0x0007
_PBT_APMRESUMEAUTOMATIC = 0x0012

# DEVICE_NOTIFY_CALLBACK recipient flag (Win32 UI WindowsAndMessaging).
_DEVICE_NOTIFY_CALLBACK = 0x00000002

_ERROR_SUCCESS = 0


# ULONG CALLBACK DeviceNotifyCallback(PVOID Context, ULONG Type, PVOID Setting)
_DEVICE_NOTIFY_CALLBACK_T = ctypes.WINFUNCTYPE(
    wintypes.ULONG,  # return ULONG
    wintypes.LPVOID,  # Context (PVOID)
    wintypes.ULONG,  # Type (ULONG)
    wintypes.LPVOID,  # Setting (PVOID)
)


class _DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS(ctypes.Structure):
    """``DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS``: { Callback, Context }."""

    _fields_ = [
        ("Callback", _DEVICE_NOTIFY_CALLBACK_T),
        ("Context", wintypes.LPVOID),
    ]


def _load_powrprof():
    """Load powrprof.dll (PowerRegister/UnregisterSuspendResumeNotification)."""
    return ctypes.windll.powrprof  # type: ignore[attr-defined]


def start(callback: PowerCallback) -> Callable[[], None] | None:
    """Register a suspend/resume callback.

    Returns a ``stop()`` callable, or ``None`` if registration failed (mirrors
    grok's ``None`` sentinel — the caller degrades to "no notifications").
    """
    powrprof = _load_powrprof()
    powrprof.PowerRegisterSuspendResumeNotification.restype = wintypes.DWORD
    powrprof.PowerRegisterSuspendResumeNotification.argtypes = [
        wintypes.DWORD,
        wintypes.LPVOID,
        ctypes.POINTER(wintypes.LPVOID),
    ]
    powrprof.PowerUnregisterSuspendResumeNotification.restype = wintypes.DWORD
    powrprof.PowerUnregisterSuspendResumeNotification.argtypes = [wintypes.LPVOID]

    def _on_event(_context, event_type, _setting) -> int:
        if event_type == _PBT_APMSUSPEND:
            callback(PowerEvent.WillSleep)
        elif event_type in (_PBT_APMRESUMEAUTOMATIC, _PBT_APMRESUMESUSPEND):
            callback(PowerEvent.DidWake)
        return _ERROR_SUCCESS

    # Pin the callback: the ctypes func object + params struct must outlive the
    # OS registration (the OS holds a raw pointer to params). They are captured
    # by the stop closure below and released on unregister (mirrors grok's Drop).
    cb = _DEVICE_NOTIFY_CALLBACK_T(_on_event)
    params = _DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS(Callback=cb, Context=None)

    handle = wintypes.LPVOID()
    status = powrprof.PowerRegisterSuspendResumeNotification(
        _DEVICE_NOTIFY_CALLBACK,
        ctypes.byref(params),
        ctypes.byref(handle),
    )
    if status != _ERROR_SUCCESS or not handle.value:
        return None

    def stop() -> None:
        # Unregister, then drop the pinned refs so the ctypes objects can GC.
        # ``nonlocal`` binds cb/params into this closure's cell (they are not
        # referenced elsewhere), keeping them alive until stop is called.
        nonlocal cb, params
        try:
            powrprof.PowerUnregisterSuspendResumeNotification(handle)
        finally:
            cb = None  # type: ignore[assignment]
            params = None  # type: ignore[assignment]

    return stop


def current_power_state() -> PowerState:
    """No synchronous dark-wake query on Windows (mirrors grok windows.rs)."""
    return PowerState.Unknown


__all__ = ["start", "current_power_state"]
