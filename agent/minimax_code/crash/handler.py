"""Crash handler installation -- fusion of grok's ``xai-crash-handler``
``handler.rs`` (R229, signal-installation leaf / write half of crash recovery).

grok's ``handler.rs`` installs POSIX signal handlers (Unix ``sigaction`` for
SIGBUS/SIGSEGV with ``SA_SIGINFO | SA_ONSTACK | SA_RESETHAND``, or Windows
``SetUnhandledExceptionFilter``) that capture the crash instruction pointer +
frame-pointer chain and write a binary ``last-crash.bin`` blob via raw
``libc::write`` from a pre-allocated static buffer. The capture path is
deliberately async-signal-safe: no allocation, no library calls, only raw
pointer reads + direct file I/O, so it can run inside a signal trampoline
after the heap is already corrupted.

Python crash capture is allocation-safe by construction (``faulthandler``
dumps a pre-formatted traceback from its own dedicated C-level handler, not a
bare ``sigaction`` trampoline), so the FFI plumbing does not migrate
line-for-line. This leaf ports the *contract* -- an ``install`` entry point
that arms crash capture early in startup, and a write step that persists a
:class:`~minimax_code.crash.format.CrashBlob` as ``last-crash.json`` -- onto
Python-native primitives, split across two capture tiers:

* **Fatal memory faults** (SIGSEGV / SIGBUS / SIGFPE / SIGABRT / SIGILL) are
  handled by :func:`faulthandler.enable`, which writes a traceback but **cannot
  construct a CrashBlob** (the CPython interpreter is already corrupted when a
  real segfault fires, so no Python code can run). faulthandler is the
  safety-net equivalent of grok's ``register_crash_signals``.
* **Uncaught Python exceptions** (the more common "crash" for an asyncio
  agent) are handled by :data:`sys.excepthook` (main thread) and
  :data:`threading.excepthook` (worker threads); the interpreter is still
  healthy here, so each hook builds a :class:`CrashBlob` and persists it via
  :func:`_persist_crash`. This is the write step that pairs with the
  R228 ``check_previous_crash`` reader.

Migrated (this round)
---------------------

* :func:`install` -- grok ``handler::install``: create ``crash_dir`` (best
  effort), stash ``crash_dir`` + ``app_version`` in module state (grok's
  ``static mut CRASH_FD`` / ``APP_VERSION``), enable ``faulthandler``, and
  replace ``sys.excepthook`` + ``threading.excepthook``. Returns ``True`` on
  success / ``False`` if the crash directory cannot be created (mirrors grok's
  ``create_dir_all`` failure returning ``false``).
* :func:`_persist_crash` -- grok ``write_crash_blob``: build a
  :class:`~minimax_code.crash.format.CrashBlob` (signal + si_code + si_addr +
  pid + timestamp + frames + app_version), serialize it via
  :meth:`~minimax_code.crash.format.CrashBlob.as_payload`, and write it to
  ``<crash_dir>/last-crash.json`` (the R228 reader's input). Best-effort: a
  write failure is swallowed (grok ``let _ =``), and the target path is still
  returned. Returns ``None`` when no handler is installed (grok's
  ``CRASH_FD < 0`` early return).
* :func:`_python_excepthook` / :func:`_threading_excepthook` -- Python-native
  capture of uncaught exceptions (no grok equivalent; grok is not garbage-
  collected). ``SystemExit`` / ``KeyboardInterrupt`` are passed through
  untouched (they are intentional control flow, not crashes).
* :class:`_HandlerState` + ``_STATE`` -- grok's ``static`` module globals,
  held in a single module-level optional so the hooks can read the install-time
  crash directory + version without re-deriving them.

Purification decisions
----------------------

grok writes a binary GCRX blob because a POSIX signal handler is
async-signal-unsafe to allocate, so the handler writes raw bytes via
``libc::write`` into a pre-allocated buffer. R226 rebuilt the persisted record
as JSON, so this writer emits ``json.dumps(blob.as_payload())`` via the
ordinary :func:`pathlib.Path.write_text` -- the allocation constraint does not
apply on the Python side (the hooks below only fire when the interpreter is
healthy). The ``OSError`` on the write is swallowed exactly like grok's
``let _ = std::fs::write`` best-effort contract, and the path is surfaced
regardless so a caller can still point at where the record *should* have
landed.

The two capture tiers replace grok's single ``register_crash_signals`` path
because CPython cannot run arbitrary Python inside a SIGSEGV trampoline:
``faulthandler`` owns the fatal-signal tier (C-level traceback dump, no blob),
and the ``excepthook`` tier owns the recoverable-exception tier (full blob
construction + JSON write). The blob's ``signal`` field is
:data:`_PYTHON_EXCEPTION_SIGNAL` (``0``) for the excepthook path -- a Python
exception has no POSIX signal, and ``signal_name(0)`` returns
``"Unknown signal"``, an honest degradation rather than a misleading label.

YAGNI / dropped (no Python equivalent)
--------------------------------------

* ``extract_pc_and_fp`` (ucontext_t register reads) + ``walk_frame_pointers``
  (raw pointer dereference over the frame chain) -- grok's async-signal-safe
  stack walk; ``faulthandler`` is the capture mechanism and needs no manual
  walk.
* ``setup_alt_stack`` (``sigaltstack``) + ``save_termios`` (termios save) +
  the entire ``terminal.rs`` module -- grok's TUI terminal-restore escape
  sequences; MiniMax Code's agent is a Web SPA backend with no TUI.
* Windows ``SetUnhandledExceptionFilter`` + ``EXCEPTION_*`` code mapping --
  ``faulthandler`` unifies fatal-fault capture across platforms, so the
  platform split is not ported.
* ``install_terminal_restore_only`` / ``enable_terminal_escape_restore`` /
  ``disable_terminal_escape_restore`` -- TUI lifecycle hooks; out of scope.
* asyncio loop ``set_exception_handler`` -- a future round may install a
  dedicated async-exception hook once the agent wires ``install`` into
  startup; deferred to keep this leaf focused on the synchronous capture
  contract.

Product-fusion note
-------------------

This closes the write half of crash recovery. The R228
:func:`~minimax_code.crash.recovery.check_previous_crash` reader consumes the
exact ``last-crash.json`` this leaf produces: ``install`` at startup arms
capture, an uncaught exception writes the blob, the next process startup reads
it back, symbolicates, renders the report, archives it, and surfaces a
:class:`~minimax_code.crash.types.CrashReport` -- the round-trip the roadmap
``v0.9.0`` "可恢复 / recoverable" acceptance criterion calls for. The future
app-startup wiring will call ``install`` early in
``minimax_code.__main__`` (after config load, before the asyncio runtime),
and the future ``crash.*`` IPC namespace + frontend recovery prompt will
consume the R228 ``CrashReport`` rather than this writer directly.
"""

from __future__ import annotations

import faulthandler
import json
import os
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from minimax_code.crash.format import CrashBlob
from minimax_code.crash.recovery import LAST_CRASH_FILE
from minimax_code.crash.types import CrashHandlerConfig

#: Signal value stamped into a crash blob for an *uncaught Python exception*.
#:
#: grok's blob carries the POSIX signal that fired (SIGSEGV / SIGBUS / ...).
#: A Python exception has no POSIX signal, so this placeholder is ``0`` --
#: :func:`~minimax_code.crash.signals.signal_name` returns ``"Unknown signal"``
#: for it, an honest degradation rather than a misleading label. The fatal-
#: signal tier (``faulthandler``) does not construct a blob at all.
_PYTHON_EXCEPTION_SIGNAL: int = 0


@dataclass
class _HandlerState:
    """Module-private install state (grok ``static CRASH_FD`` / ``APP_VERSION``).

    Captured once at :func:`install` time so the exception hooks can read the
    crash directory + application version without re-deriving them on the hot
    path. Grok stores these in ``static mut`` globals; Python holds a single
    module-level optional.
    """

    crash_dir: Path
    app_version: str


#: Active install state, or ``None`` when no handler is installed (grok
#: ``CRASH_FD = -1`` sentinel). Set by :func:`install`; read by the exception
#: hooks + :func:`_persist_crash`.
_STATE: _HandlerState | None = None


def install(config: CrashHandlerConfig) -> bool:
    """Install the Python crash handlers (grok ``handler::install``).

    Creates ``crash_dir`` (best-effort, mirroring grok's ``create_dir_all``),
    stashes ``crash_dir`` + ``app_version`` into module state, enables
    :mod:`faulthandler` for fatal-signal traceback capture, and replaces
    :data:`sys.excepthook` + :data:`threading.excepthook` with this module's
    exception-persisting variants.

    Returns ``True`` if the handlers were armed, or ``False`` if the crash
    directory could not be created (mirrors grok's ``create_dir_all`` failure
    returning ``false`` before any handler is registered). Must be called early
    in startup, before the asyncio runtime is spun up, so an uncaught exception
    in any later phase is captured.

    :param config: install-time configuration (app version + crash directory).
    :return: whether installation succeeded.
    """
    try:
        config.crash_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False

    global _STATE
    _STATE = _HandlerState(crash_dir=config.crash_dir, app_version=config.app_version)

    # Fatal-signal safety net (grok ``register_crash_signals``): dumps a
    # traceback for SIGSEGV / SIGBUS / SIGFPE / SIGABRT / SIGILL from C code,
    # because the interpreter is already corrupted and cannot run Python.
    faulthandler.enable()

    # Recoverable-exception capture (Python-native): the interpreter is still
    # healthy, so each hook builds + persists a CrashBlob via _persist_crash.
    sys.excepthook = _python_excepthook
    threading.excepthook = _threading_excepthook
    return True


def _persist_crash(
    signal: int,
    si_code: int = 0,
    si_addr: int = 0,
    frames: tuple[int, ...] = (),
) -> Path | None:
    """Build a CrashBlob and persist it as ``last-crash.json`` (grok
    ``write_crash_blob``).

    Constructs a :class:`~minimax_code.crash.format.CrashBlob` from the given
    fields plus the live pid + current epoch timestamp + the install-time
    ``app_version``, serializes it via
    :meth:`~minimax_code.crash.format.CrashBlob.as_payload`, and writes it to
    ``<crash_dir>/last-crash.json`` -- the file R228
    :func:`~minimax_code.crash.recovery.check_previous_crash` reads at next
    startup.

    Returns the target path (regardless of whether the write succeeded, mirroring
    grok building ``report_path`` before the ``write`` and surfacing it
    unconditionally), or ``None`` when no handler is installed (grok's
    ``CRASH_FD < 0`` early return -- nothing to write to).

    :param signal: POSIX signal number (use :data:`_PYTHON_EXCEPTION_SIGNAL`
        for an uncaught Python exception with no real signal).
    :param si_code: ``siginfo_t.si_code`` qualifier (0 when unknown).
    :param si_addr: faulting address (0 when unknown).
    :param frames: raw instruction-pointer backtrace (empty for the Python
        exception path, which has no native IP chain).
    """
    state = _STATE
    if state is None:
        return None

    blob = CrashBlob(
        signal=signal,
        si_code=si_code,
        si_addr=si_addr,
        pid=os.getpid(),
        timestamp=int(time.time()),
        frames=frames,
        app_version=state.app_version,
    )
    crash_file = state.crash_dir / LAST_CRASH_FILE
    try:
        crash_file.write_text(json.dumps(blob.as_payload()), encoding="utf-8")
    except OSError:
        # Best-effort: grok ``let _ =``. The path is surfaced regardless.
        pass
    return crash_file


def _python_excepthook(
    exc_type: type[BaseException],
    exc_value: BaseException,
    exc_tb: TracebackType | None,
) -> None:
    """``sys.excepthook`` replacement: persist a crash blob for an uncaught
    main-thread exception (grok has no equivalent -- it is not garbage-collected).

    ``SystemExit`` / ``KeyboardInterrupt`` are intentional control flow, not
    crashes, so they are passed through without persisting a blob. Any other
    uncaught exception triggers :func:`_persist_crash` with the placeholder
    :data:`_PYTHON_EXCEPTION_SIGNAL` (the interpreter is healthy, so the blob
    can be built + written here -- unlike the faulthandler fatal-signal tier).
    """
    if issubclass(exc_type, (SystemExit, KeyboardInterrupt)):
        return
    _persist_crash(signal=_PYTHON_EXCEPTION_SIGNAL)


def _threading_excepthook(args: threading.ExceptHookArgs) -> None:
    """``threading.excepthook`` replacement: persist a crash blob for an uncaught
    worker-thread exception (Python-native; no grok equivalent).

    Same semantics as :func:`_python_excepthook`: ``SystemExit`` /
    ``KeyboardInterrupt`` are control flow and are skipped; everything else is
    persisted via :func:`_persist_crash`.
    """
    if issubclass(args.exc_type, (SystemExit, KeyboardInterrupt)):
        return
    _persist_crash(signal=_PYTHON_EXCEPTION_SIGNAL)


__all__ = [
    "install",
]
