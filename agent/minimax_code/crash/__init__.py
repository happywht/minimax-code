"""``xai-crash-handler`` platform package (R225+, crate migration in progress).

Fuses grok's ``xai-crash-handler`` crate (cross-platform crash handler: Unix
``sigaction`` for SIGBUS/SIGSEGV + Windows ``SetUnhandledExceptionFilter``,
with startup crash detection via a persisted ``last-crash.bin``). The crate
ships 5 modules (1592 lines); this package is being filled leaf-by-leaf.

The fusion is **concept-for-concept**, not line-for-line: grok's Rust FFI
signal installation and custom GCRX binary blob format (designed for
allocation-free signal-handler writes) become a Python-native recovery layer
built on ``faulthandler`` + ``sys.excepthook`` + ``threading.excepthook`` +
``asyncio`` exception handlers, with a JSON persisted record. grok's TUI
terminal-restore (``terminal.rs``) is out of scope (the agent is a Web SPA
backend with no TUI).

Currently landed:

* ``types`` (R225) -- grok ``lib.rs`` value types (``CrashReport`` /
  ``CrashHandlerConfig``) + ``symbolicate.rs`` ``ResolvedFrame`` + the
  ``MAX_HISTORY`` constant. Pure data, no I/O.
* ``signals`` (R225) -- grok ``symbolicate.rs`` ``signal_name`` /
  ``si_code_name`` POSIX signal vocabulary.
* ``archive`` (R225) -- grok ``lib.rs`` ``archive_report`` report persistence
  + ``history/`` retention pruning.
* ``format`` (R226) -- grok ``format.rs`` ``CrashBlob`` + GCRX binary format,
  rebuilt as JSON (``from_payload`` / ``as_payload``); the binary layout
  constants (``HEADER_SIZE`` / ``MAX_FILE_SIZE`` / ``VERSION_STRING_LEN``)
  and ``writer`` module are dropped (allocation-safe capture makes a custom
  binary format unnecessary).
* ``symbolicate`` (R227) -- grok ``symbolicate.rs`` ``resolve_frames`` /
  ``format_report``. ``resolve_frames`` is a best-effort placeholder (pure
  Python has no ``backtrace::resolve`` equivalent; every frame mirrors grok's
  stripped-binary fallback with all-``None`` symbol fields, real native
  symbolication deferred to the future ``handler`` leaf). ``format_report``
  renders the blob + resolved frames into the human-readable report text
  (product-branded ``=== MiniMax Code Crash Report ===`` envelope, signal /
  address / version / backtrace block).

YAGNI / deferred (later rounds): ``backtrace::resolve`` native symbolication
(``resolve_frames`` lands as a best-effort all-``None`` placeholder; real
DWARF / symbol-table lookup needs a native backend and is superseded by the
``faulthandler`` Python-traceback path in the ``handler`` leaf),
``handler.rs`` signal installation (Python ``faulthandler`` + ``excepthook``
equivalent), ``lib.rs`` ``check_previous_crash`` orchestration (consumes
format + archive + symbolicate), ``install`` /
``install_terminal_restore_only`` entry points, and the app-startup wiring +
``crash.*`` IPC namespace + frontend session-recovery prompt.
"""

from __future__ import annotations

from minimax_code.crash.archive import archive_report, prune_history
from minimax_code.crash.format import MAGIC, MAX_FRAMES, VERSION, CrashBlob
from minimax_code.crash.signals import si_code_name, signal_name
from minimax_code.crash.symbolicate import format_report, resolve_frames
from minimax_code.crash.types import (
    MAX_HISTORY,
    CrashHandlerConfig,
    CrashReport,
    ResolvedFrame,
)

__all__ = [
    "MAGIC",
    "MAX_FRAMES",
    "MAX_HISTORY",
    "VERSION",
    "CrashBlob",
    "CrashHandlerConfig",
    "CrashReport",
    "ResolvedFrame",
    "archive_report",
    "format_report",
    "prune_history",
    "resolve_frames",
    "si_code_name",
    "signal_name",
]
