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

    root.setLevel(level)
    # R15 — scrub secrets/paths/URLs from every log record via the same
    # redactor the telemetry pipeline uses. Attached to the root logger so
    # every child logger inherits it. Idempotent: repeated
    # configure_logging() calls do not stack duplicate filters.
    if not any(isinstance(f, SanitizerFilter) for f in root.filters):
        root.addFilter(SanitizerFilter())
    # Tame third-party noise.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
