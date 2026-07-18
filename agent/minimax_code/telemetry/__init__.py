"""Telemetry subsystem (R11) — in-memory, fail-open observability bus.

Fuses grok-build's telemetry-engine design (typed events, payload
redaction, per-session metrics, a recent-event ring buffer) into MiniMax's
asyncio + pydantic architecture. The engine is optional everywhere: an
``AgentCore`` / handler with no engine attached behaves exactly as before.

Public surface
--------------
* :class:`TelemetryEngine` — the bus (emit → redact → buffer + metrics).
* :class:`TelemetryEvent`, :class:`EventType`, :class:`Severity` — event model.
* :func:`redact_value` (and friends) — payload scrubbing primitives.
* :class:`RingBuffer`, :class:`MetricsRegistry` — engine internals (re-exported
  for tests / advanced injection).

Wiring lives in :mod:`minimax_code.app` via ``ensure_telemetry_engine()``;
session-lifecycle emission lives in ``ipc/builtins.py``; tool-dispatch
mirroring lives in ``agent/core.py``.
"""

from __future__ import annotations

from .engine import TelemetryEngine
from .events import EventType, Severity, TelemetryEvent
from .metrics import MetricsRegistry, SessionMetrics, global_snapshot
from .redact import redact_paths, redact_secrets, redact_value, url_origin
from .ringbuffer import RingBuffer

__all__ = [
    "EventType",
    "MetricsRegistry",
    "RingBuffer",
    "SessionMetrics",
    "Severity",
    "TelemetryEngine",
    "TelemetryEvent",
    "global_snapshot",
    "redact_paths",
    "redact_secrets",
    "redact_value",
    "url_origin",
]
