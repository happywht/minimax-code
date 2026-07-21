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

YAGNI / deferred (later rounds): ``symbolicate.rs`` ``resolve_frames`` /
``format_report`` (Python ``traceback`` equivalent), ``handler.rs`` signal
installation (Python ``faulthandler`` + ``excepthook`` equivalent),
``lib.rs`` ``check_previous_crash`` orchestration, ``install`` /
``install_terminal_restore_only`` entry points, and the app-startup wiring +
``crash.*`` IPC namespace + frontend session-recovery prompt.
"""

from __future__ import annotations

from minimax_code.crash.archive import archive_report, prune_history
from minimax_code.crash.format import MAGIC, MAX_FRAMES, VERSION, CrashBlob
from minimax_code.crash.signals import si_code_name, signal_name
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
    "prune_history",
    "si_code_name",
    "signal_name",
]
