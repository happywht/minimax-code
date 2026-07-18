"""Runtime support — process-lifecycle utilities.

Public surface (R12):

- :mod:`minimax_code.runtime.crash_detect` — marker-file crash detection
  + faulthandler sink, fused from grok-build's ``xai-crash-handler``.

This package is intentionally tiny and dependency-free at import time so
that ``crash_detect`` can be wired into ``cli_entry`` long before the
async runtime, DAOs, or telemetry exist (fail-open on every path).
"""

from __future__ import annotations

__all__: list[str] = ["crash_detect"]
