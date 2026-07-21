"""Crash report archiving -- fusion of grok's ``xai-crash-handler`` ``lib.rs``
``archive_report`` (R225, pure-logic leaf).

grok's ``lib::archive_report`` runs at normal startup (not in a signal
handler): it writes the human-readable crash report under
``<crash_dir>/history/crash-<timestamp>.txt`` and prunes the directory to the
most recent :data:`~minimax_code.crash.types.MAX_HISTORY` entries. All filesystem
errors are swallowed (``let _ =``) because archiving is best-effort -- a
failure to archive must not block the recovery prompt.

This module migrates that orchestration verbatim. It is pure filesystem logic
parameterized by a directory + report text + timestamp, so it tests cleanly
with a ``tmp_path``.

Migrated (this round, pure logic)
---------------------------------

* :func:`archive_report` -- grok ``archive_report``: create ``history/``,
  write ``crash-<timestamp>.txt``, prune to ``MAX_HISTORY``.
* :func:`prune_history` -- extracted from grok's inline pruning loop (the
  ``read_dir`` + ``.txt`` filter + sort + oldest-slice + remove loop) as a
  DRY helper so the retention policy is independently testable.

Purification decisions
----------------------

grok returns ``()`` (unit) and discards every I/O result with ``let _ =``.
The Python mirror returns the archived :class:`~pathlib.Path` on success and
``None`` on failure -- a small ergonomic lift that makes the round-trip
assertable in tests without changing the best-effort contract (callers that
ignore the return value behave exactly like grok). Pruning sorts entries by
filename; since filenames embed a monotonic timestamp, lexical order is
chronological order, so the oldest slice is the lexically-first slice (mirrors
grok's ``files.sort()``).

Product-fusion note
-------------------

Consumed by the future ``check_previous_crash`` orchestration (grok
``lib::check_previous_crash``), which renders the report text via
``format_report`` then hands it here for persistence + retention. This leaf
closes the "write the report + keep history" half of recovery; the "read +
parse the previous crash" half lands with the format leaf.
"""

from __future__ import annotations

from pathlib import Path

from minimax_code.crash.types import MAX_HISTORY


def archive_report(crash_dir: Path, report_text: str, timestamp: int) -> Path | None:
    """Persist a crash report into ``history/`` and prune old entries (grok
    ``lib::archive_report``).

    Writes ``<crash_dir>/history/crash-<timestamp>.txt`` with ``report_text``,
    then prunes the directory to the most recent
    :data:`~minimax_code.crash.types.MAX_HISTORY` ``.txt`` reports. Returns the
    archived path on success, or ``None`` if the directory could not be
    created or the report could not be written (best-effort, mirrors grok's
    ``let _ =`` error swallowing). Pruning failures are swallowed internally
    and never affect the return value.
    """
    history_dir = crash_dir / "history"
    try:
        history_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None

    archive_path = history_dir / f"crash-{timestamp}.txt"
    try:
        archive_path.write_text(report_text, encoding="utf-8")
    except OSError:
        return None

    prune_history(history_dir)
    return archive_path


def prune_history(history_dir: Path) -> None:
    """Keep only the most recent ``MAX_HISTORY`` ``.txt`` reports in
    ``history_dir`` (extracted from grok ``archive_report``'s inline loop).

    Sorts entries by filename (chronological because filenames embed a
    monotonic timestamp) and removes the oldest beyond
    :data:`~minimax_code.crash.types.MAX_HISTORY`. All errors are swallowed
    (best-effort): a directory listing failure or a single stale-file removal
    failure leaves the remaining reports intact.
    """
    try:
        files = sorted(history_dir.glob("*.txt"))
    except OSError:
        return
    if len(files) <= MAX_HISTORY:
        return
    excess = len(files) - MAX_HISTORY
    for stale in files[:excess]:
        try:
            stale.unlink()
        except OSError:
            continue


__all__ = [
    "archive_report",
    "prune_history",
]
