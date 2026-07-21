"""Crash report type contract -- fusion of grok's ``xai-crash-handler`` ``lib.rs``
+ ``symbolicate.rs`` (R225, pure-type leaf).

``xai-crash-handler`` is grok's cross-platform crash handler (Unix
``sigaction`` for SIGBUS/SIGSEGV + Windows ``SetUnhandledExceptionFilter``)
with startup crash detection. It writes a binary ``last-crash.bin`` from the
signal handler (allocation-free via raw ``libc::write``), then on next startup
parses + symbolicates it into a human-readable report and archives it.

This module migrates the **value types** that flow through that pipeline --
they are pure data with no I/O and no platform coupling:

* :data:`MAX_HISTORY` -- grok ``const MAX_HISTORY`` (5): how many archived
  reports ``history/`` retains.
* :class:`ResolvedFrame` -- grok ``symbolicate::ResolvedFrame``: a
  symbolicated backtrace frame (raw instruction pointer + optional symbol /
  filename / line).
* :class:`CrashReport` -- grok ``lib::CrashReport``: the structured crash
  surfaced to the user at next startup (signal label + si_code + faulting
  address + timestamp + app version + resolved backtrace + report path).
* :class:`CrashHandlerConfig` -- grok ``lib::CrashHandlerConfig``: the
  install-time configuration (app version + crash directory).

YAGNI / deferred (this round)
-----------------------------

* **handler.rs (923 lines)** -- grok's platform-specific signal/SEH
  installation (``sigaction`` / ``SetUnhandledExceptionFilter`` / alternate
  signal stack / termios save). Python's equivalent lands in a later round
  via ``faulthandler`` + ``sys.excepthook`` + ``threading.excepthook`` +
  ``asyncio`` exception handler -- a different mechanism, so the Rust FFI
  body is not ported line-for-line.
* **format.rs ``CrashBlob`` + GCRX binary format** -- grok's custom binary
  blob (magic ``b"GCRX"`` + little-endian fixed header + frame array) exists
  because a signal handler cannot allocate. Python crash capture is
  allocation-safe (``faulthandler`` writes a pre-formatted traceback), so the
  persisted record will be JSON in a later round -- the binary parser is not
  ported.
* **``check_previous_crash`` orchestration** -- grok ``lib::check_previous_crash``
  (read blob -> parse -> symbolicate -> write report -> archive -> delete
  blob) ties together handler / format / symbolicate; it lands once those
  leaves exist.
* **``format_report`` renderer** -- grok ``symbolicate::format_report`` (the
  human-readable text written into ``last-crash-report.txt``) depends on
  ``CrashBlob``; it lands with the format leaf.
* **terminal.rs (134 lines)** -- grok's TUI terminal-restore escape
  sequences (termios save + alternate signal stack). MiniMax Code's agent is
  a Web SPA backend with no TUI, so this is out of scope (YAGNI).

Purification decisions
----------------------

The grok structs carry Rust-specific types (``&'static str``, ``u64``,
``Vec<ResolvedFrame>``, ``PathBuf``); they map to plain Python types
(``str``, ``int``, ``tuple[ResolvedFrame, ...]``, :class:`pathlib.Path`).
``Option<T>`` maps to ``T | None`` with ``None`` defaults. All three structs
are ``#[derive(Debug, Clone)]`` in grok (no ``serde``), so they are plain
``frozen=True, slots=True`` dataclasses with no ``from_payload`` /
``as_payload`` round-trip -- they are constructed directly by the recovery
orchestration layer, not deserialized from a wire envelope.

Product-fusion note
-------------------

This leaf is the type foundation of the crash recovery subsystem (roadmap
``v0.9.0`` acceptance criterion: "可恢复 / recoverable"). It unblocks the
archive algorithm (:mod:`minimax_code.crash.archive`, this round) and the
later format / symbolicate / handler / orchestration leaves. The signal
vocabulary (:mod:`minimax_code.crash.signals`) consumes the POSIX signal
numbers carried in :attr:`CrashReport.si_code`'s sibling ``signal`` field.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

#: grok ``const MAX_HISTORY`` -- maximum archived reports retained in
#: ``<crash_dir>/history/``. Pruning keeps the most recent entries (filenames
#: are ``crash-<timestamp>.txt``, monotonic in timestamp).
MAX_HISTORY: int = 5


@dataclass(frozen=True, slots=True)
class ResolvedFrame:
    """A symbolicated backtrace frame (grok ``symbolicate::ResolvedFrame``).

    A raw instruction pointer (``ip``) resolved at next startup into an
    optional symbol name, source filename, and 1-based line number. All three
    are ``None`` when the resolver had no debug info / symbol table for the
    address (typical for stripped release binaries).
    """

    ip: int
    symbol_name: str | None = None
    filename: str | None = None
    lineno: int | None = None


@dataclass(frozen=True, slots=True)
class CrashReport:
    """Structured crash surfaced to the user at next startup (grok
    ``lib::CrashReport``).

    Produced by reading + symbolocating a persisted crash record from the
    previous session. ``signal_name`` is the human-readable label (e.g.
    ``"SIGSEGV (Segmentation fault)"``); ``si_code`` is the raw
    ``siginfo_t.si_code``; ``faulting_address`` is ``siginfo_t.si_addr``;
    ``backtrace`` is the resolved frame list; ``report_path`` points at the
    saved human-readable report (``last-crash-report.txt``).
    """

    signal_name: str
    si_code: int
    faulting_address: int
    timestamp: int
    app_version: str
    backtrace: tuple[ResolvedFrame, ...]
    report_path: Path


@dataclass(frozen=True, slots=True)
class CrashHandlerConfig:
    """Install-time configuration for the crash handler (grok
    ``lib::CrashHandlerConfig``).

    ``app_version`` is stamped into each crash record (so a report identifies
    the version that crashed); ``crash_dir`` is where crash records +
    reports + ``history/`` live, created on install if absent.
    """

    app_version: str
    crash_dir: Path


__all__ = [
    "MAX_HISTORY",
    "CrashHandlerConfig",
    "CrashReport",
    "ResolvedFrame",
]
