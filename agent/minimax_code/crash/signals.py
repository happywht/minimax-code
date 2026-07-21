"""POSIX signal vocabulary -- fusion of grok's ``xai-crash-handler``
``symbolicate.rs`` (R225, pure-logic leaf).

grok's ``symbolicate::signal_name`` and ``symbolicate::si_code_name`` map raw
POSIX signal numbers (carried in the crash blob's ``signal`` / ``si_code``
fields) into human-readable labels for the crash report renderer. They are
pure functions over integers -- no I/O, no platform calls -- so they migrate
verbatim. The signal numbers are POSIX-standard and stable across platforms;
SIGBUS is the one wrinkle (7 on Linux, 10 on macOS), handled by accepting
both.

Migrated (this round, pure logic)
---------------------------------

* :func:`signal_name` -- grok ``signal_name``: SIGILL (4) / SIGBUS (7 or 10)
  / SIGSEGV (11) -> human label; unknown -> ``"Unknown signal"``.
* :func:`si_code_name` -- grok ``si_code_name``: the ``si_code`` qualifier.
  For SIGBUS: BUS_ADRALN (1) / BUS_ADRERR (2) / BUS_OBJERR (3). For SIGSEGV:
  SEGV_MAPERR (1) / SEGV_ACCERR (2). Unknown -> ``"unknown"``.

YAGNI / deferred (this round)
-----------------------------

* **Full POSIX signal table** -- grok only maps the three signals its handler
  installs (SIGILL / SIGBUS / SIGSEGV); the same scope is kept here. Expanding
  to the full ``<signal.h>`` table is out of scope until the handler installs
  more signals.

Product-fusion note
-------------------

Consumed by the future ``format_report`` renderer (writes the ``Signal:`` /
``si_code:`` lines of the human-readable crash report) and by the recovery
orchestration layer that builds :class:`~minimax_code.crash.types.CrashReport`.
``signal_name`` is the value stamped into
:attr:`CrashReport.signal_name <minimax_code.crash.types.CrashReport.signal_name>`.
"""

from __future__ import annotations

#: SIGILL -- illegal instruction (POSIX).
_SIGILL: int = 4
#: SIGBUS on Linux (POSIX).
_SIGBUS_LINUX: int = 7
#: SIGBUS on macOS (POSIX) -- same semantics, different number.
_SIGBUS_MACOS: int = 10
#: SIGSEGV -- segmentation fault (POSIX).
_SIGSEGV: int = 11


def signal_name(signal: int) -> str:
    """Map a POSIX signal number to a human-readable label (grok
    ``symbolicate::signal_name``).

    Covers SIGILL (4), SIGBUS (Linux 7 / macOS 10), and SIGSEGV (11) -- the
    three signals grok's handler installs. Any other number returns
    ``"Unknown signal"``. The macOS/Linux SIGBUS split is collapsed into one
    label.
    """
    if signal == _SIGILL:
        return "SIGILL (Illegal instruction)"
    if signal in (_SIGBUS_LINUX, _SIGBUS_MACOS):
        return "SIGBUS (Bus error)"
    if signal == _SIGSEGV:
        return "SIGSEGV (Segmentation fault)"
    return "Unknown signal"


def si_code_name(signal: int, code: int) -> str:
    """Map a signal's ``si_code`` qualifier to a human-readable label (grok
    ``symbolicate::si_code_name``).

    For SIGBUS (Linux 7 / macOS 10): BUS_ADRALN (1, invalid alignment) /
    BUS_ADRERR (2, non-existent physical address) / BUS_OBJERR (3,
    object-specific hardware error). For any other signal: SEGV_MAPERR (1,
    address not mapped) / SEGV_ACCERR (2, invalid permissions). Unknown codes
    return ``"unknown"``.
    """
    if signal in (_SIGBUS_LINUX, _SIGBUS_MACOS):
        if code == 1:
            return "BUS_ADRALN - invalid address alignment"
        if code == 2:
            return "BUS_ADRERR - non-existent physical address"
        if code == 3:
            return "BUS_OBJERR - object-specific hardware error"
        return "unknown"
    if code == 1:
        return "SEGV_MAPERR - address not mapped"
    if code == 2:
        return "SEGV_ACCERR - invalid permissions"
    return "unknown"


__all__ = [
    "si_code_name",
    "signal_name",
]
