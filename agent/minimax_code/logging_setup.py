"""Logging setup.

We log to stderr so we never pollute the stdio JSON-RPC channel.
`PYTHONUNBUFFERED=1` (set by the Tauri bridge) ensures log lines appear
promptly during interactive debugging.

For the production single-process mode, `MINIMAX_CODE_LOG_FILE` adds a
rotating file sink next to stderr so sessions can be debugged after a
restart. An absolute path is used as-is; a relative path resolves
against the agent's data directory (writable regardless of CWD).
"""

from __future__ import annotations

import logging
import os
import sys
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Literal

from .telemetry.redact import SanitizerFilter

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

# File sink rotation: 5 MB per file, 3 backups (20 MB worst case).
LOG_FILE_MAX_BYTES = 5 * 1024 * 1024
LOG_FILE_BACKUP_COUNT = 3

_FORMAT = "%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s | %(message)s"
_DATEFMT = "%H:%M:%S"

# In-memory tail of formatted log lines kept for the diagnostic bundle
# (M8 / R45 ``diag.export``). Small enough to be negligible, large
# enough to be useful.
MEMORY_LOG_TAIL_LINES = 200

_recent_lines: deque[str] = deque(maxlen=MEMORY_LOG_TAIL_LINES)


class _MemoryTailHandler(logging.Handler):
    """Keep the last N formatted lines in memory for ``diag.export``.

    Records reach ``emit`` only after passing the handler-level
    ``SanitizerFilter`` (filters run before emit), so the tail is
    redacted exactly like the stderr / file sinks.
    """

    def emit(self, record: logging.LogRecord) -> None:
        try:
            _recent_lines.append(self.format(record))
        except Exception:  # noqa: BLE001 - logging must never raise
            self.handleError(record)


def get_recent_log_lines(limit: int = MEMORY_LOG_TAIL_LINES) -> list[str]:
    """Return up to *limit* most-recent formatted log lines (oldest first)."""
    if limit <= 0:
        return []
    tail = list(_recent_lines)
    return tail[-limit:]


def _log_file_path() -> Path | None:
    """Resolve ``MINIMAX_CODE_LOG_FILE``; empty/unset means "no file sink"."""
    raw = os.environ.get("MINIMAX_CODE_LOG_FILE", "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    if not path.is_absolute():
        # Relative paths land in the data dir so the file is writable
        # no matter which directory the launcher used. Imported lazily
        # to keep this module free of storage-layer dependencies.
        from .storage.db import default_data_dir

        path = default_data_dir() / path
    return path


def configure_logging(level: LogLevel = "INFO") -> None:
    """Configure root logger to write to stderr at the given level.

    When ``MINIMAX_CODE_LOG_FILE`` is set, a rotating file handler is
    attached next to the stderr stream handler. Both sinks share the
    same formatter and inherit the sanitizer filter from the root
    logger, so on-disk logs are redacted exactly like console output.
    """
    root = logging.getLogger()
    if root.handlers:
        for h in list(root.handlers):
            root.removeHandler(h)
            h.close()
    formatter = logging.Formatter(fmt=_FORMAT, datefmt=_DATEFMT)

    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(formatter)
    root.addHandler(handler)

    log_file = _log_file_path()
    if log_file is not None:
        try:
            log_file.parent.mkdir(parents=True, exist_ok=True)
            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=LOG_FILE_MAX_BYTES,
                backupCount=LOG_FILE_BACKUP_COUNT,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:
            # An unwritable log path must never take the agent down;
            # stderr-only is an acceptable degradation.
            root.warning("cannot write log file %s — stderr only", log_file)

    # M8 / R45 — keep a small in-memory tail for the diagnostic bundle.
    # Added before the sanitizer pass below, so it gets the same
    # handler-level SanitizerFilter as the other sinks.
    memory_handler = _MemoryTailHandler()
    memory_handler.setFormatter(formatter)
    root.addHandler(memory_handler)

    root.setLevel(level)
    # R15 — scrub secrets/paths/URLs from every log record via the same
    # redactor the telemetry pipeline uses.
    #
    # R17 fix: the filter is attached to each *handler*, not just the
    # root logger. A logger-level filter only runs for records the root
    # logger itself emits — records propagated from child loggers
    # (``minimax_code.*`` — i.e. all business logs) skip it entirely and
    # leaked to stderr verbatim. A handler-level filter runs for every
    # record that reaches the handler, propagated or not.
    sanitizer = SanitizerFilter()
    for h in root.handlers:
        if not any(isinstance(f, SanitizerFilter) for f in h.filters):
            h.addFilter(sanitizer)
    if not any(isinstance(f, SanitizerFilter) for f in root.filters):
        root.addFilter(sanitizer)  # belt-and-braces for root-emitted records
    # Tame third-party noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
