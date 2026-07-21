"""Crash recovery orchestration -- fusion of grok's ``xai-crash-handler``
``lib.rs`` ``check_previous_crash`` (R228, pure-logic orchestration leaf).

grok's ``lib::check_previous_crash`` runs at the very start of normal startup
(before the handler is installed, because install opens ``last-crash.bin``
with ``O_TRUNC``): it looks for a crash record written by the *previous*
process's signal handler, and if present, parses it, symbolicates the
backtrace, renders a human-readable report, persists + archives it, removes
the raw blob so it is not re-processed, and returns a structured
:class:`~minimax_code.crash.types.CrashReport` for the startup UI to surface
("the application crashed during your last session").

This is the orchestration leaf that consumes the three crash-formatting leaves
landed in R224-R227:

* :mod:`minimax_code.crash.format` (R226) -- ``CrashBlob.from_payload`` parses
  the persisted record back into a blob.
* :mod:`minimax_code.crash.symbolicate` (R227) -- ``resolve_frames`` walks the
  blob's frame pointers and ``format_report`` renders the text report.
* :mod:`minimax_code.crash.archive` (R225) -- ``archive_report`` writes the
  report into ``history/`` and prunes old entries.

plus :func:`~minimax_code.crash.signals.signal_name` (R225) for the
human-readable signal label on the returned :class:`CrashReport`.

Migrated (this round, pure orchestration)
-----------------------------------------

* :func:`check_previous_crash` -- grok ``check_previous_crash``: read the
  persisted record -> parse -> resolve -> render -> write -> archive -> delete
  -> return the structured report. All filesystem errors after a successful
  parse are best-effort (the report is surfaced even if writing / archiving /
  deletion fails), mirroring grok's ``let _ =`` swallowing.
* :data:`LAST_CRASH_FILE` / :data:`LAST_CRASH_REPORT_FILE` -- the two persisted
  filenames grok inlines as ``"last-crash.bin"`` / ``"last-crash-report.txt"``.
  Extracted as module constants so the future ``handler`` leaf (the writer
  side) and these tests share a single source of truth.

Purification decisions
----------------------

grok persists a binary ``last-crash.bin`` because a POSIX signal handler is
async-signal-unsafe to allocate, so the handler writes raw bytes via
``libc::write``. Python crash capture is allocation-safe (``faulthandler``
dumps a pre-formatted traceback from its own handler), so R226 rebuilt the
persisted record as JSON; this reader therefore consumes ``last-crash.json``
(not ``.bin``) via :meth:`~minimax_code.crash.format.CrashBlob.from_payload`
on a ``json.loads``-parsed dict. The three failure modes (file missing /
unreadable, malformed JSON, structural validation) all collapse to ``None``
exactly like grok's chained ``?`` (``read(...).ok()?`` -> ``parse(...)?``).

grok deletes the blob only on the *successful* path (after parse); a
malformed or unparseable record is left on disk for a later retry. The
Python mirror preserves that ordering: ``unlink`` runs only after
``from_payload`` succeeds, so a bad-magic / bad-version / bad-JSON file
survives the call. Report write + archive + unlink are each wrapped in
``try/except OSError`` to mirror grok's ``let _ =`` best-effort contract;
``report_path`` is surfaced on the returned report regardless of whether the
write succeeded (grok builds ``report_path`` before the ``write`` and returns
it unconditionally).

YAGNI / dropped
---------------

* grok's binary ``std::fs::read`` + ``CrashBlob::parse`` byte-level decoder is
  superseded by the JSON path (R226); not ported.
* The ``install`` / ``install_terminal_restore_only`` /
  ``enable_terminal_escape_restore`` / ``disable_terminal_escape_restore``
  entry points (grok ``lib.rs``) are the *writer* side -- signal-handler
  installation -- and land with the future ``handler`` leaf, not here.

Product-fusion note
-------------------

This closes the read half of crash recovery (format + symbolicate + archive
were the write / render half). The returned :class:`CrashReport` is the
structured surface the future ``crash.*`` IPC namespace and frontend
session-recovery prompt will consume to tell the user "your last session
crashed, here is the report" at next startup. The signal_name label, resolved
backtrace, and persisted report path are exactly the fields a recovery dialog
needs -- no extra shaping required, which is the payoff of keeping the grok
``CrashReport`` shape intact in R225.
"""

from __future__ import annotations

import json
from pathlib import Path

from minimax_code.crash.archive import archive_report
from minimax_code.crash.format import CrashBlob
from minimax_code.crash.signals import signal_name
from minimax_code.crash.symbolicate import format_report, resolve_frames
from minimax_code.crash.types import CrashReport

#: Filename of the persisted crash record from the previous session (grok
#: ``"last-crash.bin"``). grok writes a binary GCRX blob; Python writes JSON
#: (R226 format leaf), so the extension tracks the on-disk format. Shared by
#: this reader and the future ``handler`` writer leaf.
LAST_CRASH_FILE: str = "last-crash.json"

#: Filename of the rendered human-readable report (grok
#: ``"last-crash-report.txt"``). Written by this leaf, surfaced on the
#: returned :class:`~minimax_code.crash.types.CrashReport.report_path`.
LAST_CRASH_REPORT_FILE: str = "last-crash-report.txt"


def check_previous_crash(crash_dir: Path) -> CrashReport | None:
    """Detect + recover a crash from the previous session (grok
    ``check_previous_crash``).

    Reads ``<crash_dir>/last-crash.json``; if present and well-formed, parses
    it into a :class:`~minimax_code.crash.format.CrashBlob`, symbolicates the
    frame pointers, renders the human-readable report text, writes it to
    ``<crash_dir>/last-crash-report.txt``, archives it under ``history/``
    (retaining :data:`~minimax_code.crash.types.MAX_HISTORY` entries), removes
    the raw record so it is not re-processed, and returns the structured
    :class:`~minimax_code.crash.types.CrashReport`.

    Returns ``None`` when no record exists, the file is unreadable, the JSON
    is malformed, or :meth:`~minimax_code.crash.format.CrashBlob.from_payload`
    rejects the structure (bad magic / version / field types) -- mirroring
    grok's chained ``?`` early-return. A parse-time ``None`` leaves the raw
    file on disk for a later retry; the post-parse write / archive / unlink
    steps are best-effort (failures are swallowed, the report is still
    surfaced).

    :param crash_dir: directory holding ``last-crash.json`` and ``history/``.
    """
    crash_file = crash_dir / LAST_CRASH_FILE
    try:
        text = crash_file.read_text(encoding="utf-8")
        payload = json.loads(text)
    except (OSError, ValueError):
        # OSError: file missing / unreadable. ValueError: malformed JSON
        # (json.JSONDecodeError subclasses ValueError) or a non-string token
        # fed to json.loads. from_payload separately rejects a well-formed
        # JSON value that is not a valid blob (bad magic / version / types).
        return None

    blob = CrashBlob.from_payload(payload)
    if blob is None:
        return None

    frames = resolve_frames(blob)
    report_text = format_report(blob, frames)

    # Write the human-readable report (best-effort; grok ``let _ =``).
    report_path = crash_dir / LAST_CRASH_REPORT_FILE
    try:
        report_path.write_text(report_text, encoding="utf-8")
    except OSError:
        pass

    # Archive to history/ + prune (best-effort; archive_report swallows I/O).
    archive_report(crash_dir, report_text, blob.timestamp)

    # Remove the raw record so it is not re-processed (best-effort).
    try:
        crash_file.unlink()
    except OSError:
        pass

    return CrashReport(
        signal_name=signal_name(blob.signal),
        si_code=blob.si_code,
        faulting_address=blob.si_addr,
        timestamp=blob.timestamp,
        app_version=blob.app_version,
        backtrace=frames,
        report_path=report_path,
    )


__all__ = [
    "LAST_CRASH_FILE",
    "LAST_CRASH_REPORT_FILE",
    "check_previous_crash",
]
