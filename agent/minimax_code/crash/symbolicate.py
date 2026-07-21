"""Backtrace symbolication for crash reports -- fusion of grok's
``xai-crash-handler`` ``symbolicate.rs`` (R227, pure-logic leaf).

grok's ``symbolicate.rs`` runs at normal startup (not in a signal handler),
turning the raw instruction-pointer array persisted in the crash blob into
human-readable function names + file/line locations, then rendering both into
a text report. It exposes two public functions (the ``signal_name`` /
``si_code_name`` POSIX vocabulary at the bottom of the file was migrated in
R225 to :mod:`minimax_code.crash.signals`):

* ``resolve_frames`` -- walks ``CrashBlob.frames`` and calls the ``backtrace``
  crate's ``backtrace::resolve`` (a DWARF / symbol-table lookup) to fill each
  ``ResolvedFrame``'s symbol / filename / lineno, defaulting to ``None`` when
  the binary is stripped.
* ``format_report`` -- renders the blob + resolved frames into the
  human-readable ``last-crash-report.txt`` text (signal header, faulting
  address, version, backtrace frame list).

Migrated (this round)
---------------------

* :func:`resolve_frames` -- grok ``resolve_frames``: ``CrashBlob.frames`` ->
  ``tuple[ResolvedFrame, ...]``. **Best-effort placeholder**: every frame is
  emitted with ``symbol_name`` / ``filename`` / ``lineno`` all ``None``,
  mirroring grok's stripped-binary fallback path. The interface contract and
  data-flow shape (raw IPs in, resolved frames out) are preserved so the
  future ``handler`` leaf (``faulthandler`` capture) can plug in real
  symbolication.
* :func:`format_report` -- grok ``format_report``: renders the blob + a
  sequence of resolved frames into the report text. Fully ported: consumes
  :func:`~minimax_code.crash.signals.signal_name` /
  :func:`~minimax_code.crash.signals.si_code_name` for the header and walks
  the frame list with the same column layout grok uses.

YAGNI / dropped (FFI-only)
--------------------------

* ``backtrace::resolve`` -- grok's per-address DWARF / symbol-table lookup
  (C library via the ``backtrace`` crate). Pure Python has no equivalent in
  the standard library (no DWARF parser, no ``dladdr`` binding), and the
  Python crash-recovery path captures Python tracebacks (via
  ``faulthandler``) rather than native instruction pointers -- a different
  paradigm that lands in the future ``handler`` leaf. ``resolve_frames``
  therefore always walks the stripped-binary fallback path (all-``None``
  symbol fields); real native symbolication, if ever needed, is a separate
  native-backend leaf.

Purification decisions
----------------------

grok formats the faulting address and each frame IP with Rust's ``{:#018x}``
(an ``0x`` prefix + zero-padded 16-hex-digit value = 18 chars). The Python
equivalent is ``f"0x{addr:016x}"`` (``0x`` + 16 hex digits), producing the
same 18-char field. Frame indices use ``{:>3}`` (right-aligned width 3) in
grok; Python ``f"{i:>3}"`` mirrors it exactly. The report is accumulated as a
list of lines joined by ``"\\n"`` (with a trailing ``"\\n"``) rather than
grok's ``String::with_capacity(4096)`` + ``push_str`` -- same output, Pythonic
build. grok binds the ``at file:line`` sub-line only when **both** filename
and lineno are ``Some``; the Python branch mirrors that conjunction.

``signal_name`` / ``si_code_name`` (the POSIX vocabulary grok defines at the
bottom of ``symbolicate.rs``) were migrated in R225 to
:mod:`minimax_code.crash.signals`; this module imports them rather than
re-defining them (single source of truth).

Product-fusion note
-------------------

The report header is rebranded ``=== MiniMax Code Crash Report ===`` (grok
uses ``=== Grok Crash Report ===``); the structural format (``=== ... Crash
Report ===`` / ``=== End Report ===`` envelope, fixed-width 9-char label
column) is preserved. This is the human-readable surface the future
``check_previous_crash`` orchestration writes to ``last-crash-report.txt``
via :func:`~minimax_code.crash.archive.archive_report`, and that the
``crash.*`` IPC namespace + frontend recovery prompt will surface to the user
at next startup.
"""

from __future__ import annotations

from collections.abc import Sequence

from minimax_code.crash.format import CrashBlob
from minimax_code.crash.signals import si_code_name, signal_name
from minimax_code.crash.types import ResolvedFrame


def resolve_frames(blob: CrashBlob) -> tuple[ResolvedFrame, ...]:
    """Resolve ``blob.frames`` into symbolicated frames (grok
    ``resolve_frames``, best-effort placeholder).

    grok calls the ``backtrace`` crate's ``backtrace::resolve`` per address
    to fill in symbol / filename / lineno from DWARF or the symbol table,
    defaulting to ``None`` for stripped binaries. Pure Python has no
    standard-library equivalent (no DWARF parser, no ``dladdr``), and the
    Python crash-recovery path captures Python tracebacks (``faulthandler``)
    rather than native instruction pointers. This implementation therefore
    always walks grok's stripped-binary fallback path: one
    :class:`~minimax_code.crash.types.ResolvedFrame` per raw IP, with
    ``symbol_name`` / ``filename`` / ``lineno`` all ``None``.

    The interface contract (``CrashBlob`` in, resolved frames out) and the
    data-flow shape are preserved so the future ``handler`` leaf can plug in
    real symbolication. Frame order matches ``blob.frames``.
    """
    return tuple(ResolvedFrame(ip=ip) for ip in blob.frames)


def format_report(blob: CrashBlob, frames: Sequence[ResolvedFrame]) -> str:
    """Render ``blob`` + ``frames`` as human-readable report text (grok
    ``format_report``).

    Produces the text written into ``last-crash-report.txt``: a product-branded
    header envelope, the signal / si_code / faulting address / PID / version /
    timestamp block (consuming :func:`~minimax_code.crash.signals.signal_name`
    and :func:`~minimax_code.crash.signals.si_code_name`), and the backtrace
    frame list (one line per frame with a zero-padded 18-char hex IP and the
    symbol name or ``<unknown>``, plus an ``at file:line`` sub-line when both
    filename and lineno are present). Mirrors grok's column layout exactly;
    the report ends with a trailing newline.
    """
    lines: list[str] = []
    lines.append("=== MiniMax Code Crash Report ===")
    lines.append("")
    lines.append(f"Signal:  {signal_name(blob.signal)}")
    lines.append(f"si_code: {blob.si_code} ({si_code_name(blob.signal, blob.si_code)})")
    lines.append(f"Address: 0x{blob.si_addr:016x}")
    lines.append(f"PID:     {blob.pid}")
    lines.append(f"Version: {blob.app_version}")
    lines.append(f"Time:    {blob.timestamp} (unix)")
    lines.append("")
    lines.append(f"Backtrace ({len(frames)} frames):")
    for i, frame in enumerate(frames):
        name = frame.symbol_name if frame.symbol_name is not None else "<unknown>"
        lines.append(f"  {i:>3}: 0x{frame.ip:016x} - {name}")
        if frame.filename is not None and frame.lineno is not None:
            lines.append(f"           at {frame.filename}:{frame.lineno}")
    lines.append("")
    lines.append("=== End Report ===")
    return "\n".join(lines) + "\n"


__all__ = [
    "format_report",
    "resolve_frames",
]
