"""Logging setup.

We log to stderr so we never pollute the stdio JSON-RPC channel.
`PYTHONUNBUFFERED=1` (set by the Tauri bridge) ensures log lines appear
promptly during interactive debugging.
"""

from __future__ import annotations

import logging
import sys
from typing import Literal

from .telemetry.redact import SanitizerFilter

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


def configure_logging(level: LogLevel = "INFO") -> None:
    """Configure root logger to write to stderr at the given level."""
    root = logging.getLogger()
    if root.handlers:
        for h in list(root.handlers):
            root.removeHandler(h)
    handler = logging.StreamHandler(stream=sys.stderr)
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s.%(msecs)03d %(levelname)-7s %(name)s | %(message)s",
            datefmt="%H:%M:%S",
        )
    )
    root.addHandler(handler)
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
