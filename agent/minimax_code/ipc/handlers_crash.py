"""JSON-RPC handlers for the ``crash.*`` namespace (R231).

These handlers expose the **consumption half** of the crash-recovery layer
(R225-R230) to the frontend: the persisted human-readable report from the
previous session, the archived history of past crashes, and a dismiss
action that clears the "your last session crashed" prompt without touching
the archival record. They are the terminal product surface that closes the
crash module's loop -- the write half (R229 ``handler``) + startup wiring
(R230) produce the files these handlers read.

Endpoints
---------
``crash.previous_report`` -> ``{available, report_text}``
``crash.history``         -> ``{entries: [{filename, timestamp, report_text}]}``
``crash.dismiss``         -> ``{dismissed}``

Design: read persisted files, never re-run recovery
---------------------------------------------------
The handlers read the **already-rendered** report files that
:func:`~minimax_code.crash.check_previous_crash` (R228) wrote at boot:

* ``<crash_dir>/last-crash-report.txt`` -- the most recent crash's
  human-readable report (written once at startup, consumed read-only here).
* ``<crash_dir>/history/crash-<timestamp>.txt`` -- the archived reports
  (R225 ``archive_report`` retention bucket, ``MAX_HISTORY`` entries).

They deliberately do **not** call ``check_previous_crash``: that function
*consumes* ``last-crash.json`` (deletes it after parsing) and is therefore
one-shot. The startup wiring (R230 ``_wire_xai_crash_handler``) already ran
it once; calling it again from an IPC handler would always return ``None``
and would re-archive a phantom report. Reading the rendered ``.txt`` files
is idempotent and safe to call any number of times.

Error contract
--------------
Every handler is fail-open and stateless. A missing ``crash_dir``, an
unreadable file, or a half-written report collapses to the honest
"nothing available" shape (``available: False`` / ``entries: []`` /
``dismissed: False``) rather than a JSON-RPC error, so a flaky filesystem
never breaks the recovery UI. (This differs from ``git.*``, which surfaces
a namespace error on failure -- git being unavailable is actionable for
the user; a missing crash report is the normal steady state.) The only
``HandlerError`` raised is ``INVALID_PARAMS`` if a caller passes an
unexpected param shape -- none of the three methods take params, but the
guard keeps the contract uniform with the other read-only namespaces.

Wire-up
-------
:func:`register_crash_handlers` is called from
:func:`minimax_code.app.register_app_handlers`. The ``crash_dir`` is
resolved once per call from :func:`~minimax_code.storage.db.default_data_dir`
(so ``MINIMAX_CODE_DATA_DIR`` overrides propagate), mirroring the R230
startup wiring's ``default_data_dir() / "crashes"`` resolution.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from .handler_utils import HandlerError
from .protocol import INVALID_PARAMS
from .server import Context

logger = logging.getLogger(__name__)

# Filename of the rendered previous-crash report (R228 ``recovery`` writes
# it). Duplicated rather than imported from ``crash.recovery`` so this
# handler stays a leaf in the IPC layer -- it talks to the persisted files,
# not the recovery API, and a future rename of the constant stays a
# conscious edit here rather than a silent coupling.
_LAST_CRASH_REPORT_FILE = "last-crash-report.txt"

# History entries are ``crash-<epoch-seconds>.txt`` (R225 ``archive_report``).
# The timestamp is a monotonic int, so lexical filename order is chronological
# order -- the same invariant R225's ``prune_history`` relies on.
_HISTORY_FILE_RE = re.compile(r"^crash-(?P<ts>\d+)\.txt$")
# Generous read-side ceiling so a pathological history dir (a long-lived dev
# box that somehow bypassed prune) does not stream hundreds of reports over
# the wire. R225's ``MAX_HISTORY`` is the retention bound; this is independent.
_HISTORY_MAX_ENTRIES = 50


def _crash_dir() -> Path:
    """Resolve the crash directory (``<data_dir>/crashes``, R230 wiring).

    Imported lazily so a stripped test build without the storage layer does
    not break handler import; the call itself honours
    ``MINIMAX_CODE_DATA_DIR`` overrides exactly like the R230 startup path.
    """
    from ..storage.db import default_data_dir

    return Path(default_data_dir()) / "crashes"


def _reject_extra_params(params: Any) -> None:
    """Reject any params on the three no-arg ``crash.*`` methods.

    Keeps the contract uniform with the other read-only namespaces: an
    empty object (or ``None``) is fine, anything else is ``INVALID_PARAMS``.
    """
    if params is None or params == {}:
        return
    raise HandlerError(INVALID_PARAMS, "crash.* methods take no parameters")


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------


def register_crash_handlers(server: Any) -> None:
    """Register the ``crash.*`` handlers on ``server``."""

    async def handle_crash_previous_report(params: Any, ctx: Context) -> None:
        try:
            _reject_extra_params(params)
            report_path = _crash_dir() / _LAST_CRASH_REPORT_FILE
            try:
                text = report_path.read_text(encoding="utf-8")
            except (OSError, ValueError):
                # Missing file (no previous crash, or already dismissed) or a
                # half-written / non-UTF-8 report -> honest "nothing to show".
                await ctx.reply({"available": False, "report_text": None})
                return
            await ctx.reply({"available": True, "report_text": text})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover -- defensive fail-open
            logger.exception("crash.previous_report failed")
            await ctx.reply({"available": False, "report_text": None})

    async def handle_crash_history(params: Any, ctx: Context) -> None:
        try:
            _reject_extra_params(params)
            history_dir = _crash_dir() / "history"
            try:
                # reverse=True -> newest first (lexical order == chronological
                # order because the timestamp is a fixed-width monotonic int).
                files = sorted(history_dir.glob("*.txt"), reverse=True)
            except OSError:
                files = []
            entries: list[dict[str, Any]] = []
            for path in files:
                if len(entries) >= _HISTORY_MAX_ENTRIES:
                    break
                match = _HISTORY_FILE_RE.match(path.name)
                if match is None:
                    # A stray .txt that is not a crash archive (e.g. a user
                    # dropped a README) -- skip rather than poison the list.
                    continue
                try:
                    report_text = path.read_text(encoding="utf-8")
                except (OSError, ValueError):
                    continue
                entries.append(
                    {
                        "filename": path.name,
                        "timestamp": int(match.group("ts")),
                        "report_text": report_text,
                    }
                )
            await ctx.reply({"entries": entries})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover -- defensive fail-open
            logger.exception("crash.history failed")
            await ctx.reply({"entries": []})

    async def handle_crash_dismiss(params: Any, ctx: Context) -> None:
        try:
            _reject_extra_params(params)
            report_path = _crash_dir() / _LAST_CRASH_REPORT_FILE
            try:
                report_path.unlink()
            except FileNotFoundError:
                await ctx.reply({"dismissed": False})
                return
            except OSError:
                # Permission / lock -- surface as "not dismissed" so the UI
                # can retry rather than falsely hiding the prompt.
                logger.warning("crash.dismiss: could not remove %s", report_path)
                await ctx.reply({"dismissed": False})
                return
            await ctx.reply({"dismissed": True})
        except HandlerError as exc:
            await ctx.reply_error(exc.code, exc.message, exc.data)
        except Exception:  # pragma: no cover -- defensive fail-open
            logger.exception("crash.dismiss failed")
            await ctx.reply({"dismissed": False})

    server.register("crash.previous_report", handle_crash_previous_report)
    server.register("crash.history", handle_crash_history)
    server.register("crash.dismiss", handle_crash_dismiss)


__all__ = [
    "register_crash_handlers",
]
