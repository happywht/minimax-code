"""Telemetry engine — the in-memory event bus (R11).

Glues :class:`.ringbuffer.RingBuffer`, :class:`.metrics.MetricsRegistry`,
and :func:`.redact.redact_value` into one fail-open emit pipeline. This is
the Python analogue of grok-build's ``TelemetryCtx`` + ``emit_event``:
call sites hand the engine a :class:`.events.TelemetryEvent`, and the
engine scrubs it, remembers it, and folds it into metrics — without ever
blocking or raising.

Separation of concerns vs. the ``audit_log`` table
---------------------------------------------------
The engine is the **real-time, in-memory** channel; ``AuditLogDAO`` is the
**durable, on-disk** channel for tool dispatches. They are intentionally not
coupled: ``AgentCore._record_audit`` keeps owning persistence, and merely
*mirrors* a ``TOOL_CALL`` event into the engine when one is attached (see
core.py). The agent runs identically with or without a telemetry engine —
``None`` means "disabled, zero overhead".
"""

from __future__ import annotations

import logging
from typing import Any

from .events import EventType, TelemetryEvent
from .metrics import MetricsRegistry, global_snapshot
from .redact import redact_value
from .ringbuffer import RingBuffer

logger = logging.getLogger(__name__)


class TelemetryEngine:
    """Fail-open in-memory telemetry bus.

    Parameters
    ----------
    buffer_capacity:
        Max events retained for ``recent()``. Default 500.
    max_sessions:
        Max sessions tracked in the metrics registry. Default 64.
    """

    def __init__(
        self,
        *,
        buffer_capacity: int = 500,
        max_sessions: int = 64,
    ) -> None:
        self._buffer = RingBuffer(capacity=buffer_capacity)
        self._metrics = MetricsRegistry(max_sessions=max_sessions)

    # ------------------------------------------------------------------
    # Emit (the hot path — never raises)
    # ------------------------------------------------------------------

    def emit(self, event: TelemetryEvent) -> None:
        """Scrub → buffer → metrics. Any failure is logged and swallowed.

        Call sites (session lifecycle, tool dispatch, hook fire, ...) treat
        this as fire-and-forget: telemetry must never break the agent.
        """
        try:
            # Redact a *copy* of the payload so the caller's event object
            # is not mutated in place (it may be inspected elsewhere).
            redacted = redact_value(event.payload)
            record = event.model_dump()
            record["payload"] = redacted
            self._buffer.append(record)
            self._metrics.record(event)
        except Exception:  # noqa: BLE001 — telemetry must never break callers
            logger.debug("telemetry emit failed", exc_info=True)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def recent(
        self,
        limit: int = 100,
        event_type: EventType | str | None = None,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return up to ``limit`` most-recent events, newest last.

        Optional ``event_type`` / ``session_id`` filters narrow the slice
        without scanning the whole buffer twice.
        """
        items = self._buffer.recent(limit=limit if limit > 0 else 1)
        if event_type is None and session_id is None:
            return items
        type_value = event_type.value if isinstance(event_type, EventType) else event_type
        filtered: list[dict[str, Any]] = []
        for item in items:
            if type_value is not None and item.get("type") != type_value:
                continue
            if session_id is not None and item.get("session_id") != session_id:
                continue
            filtered.append(item)
        return filtered

    def metrics(self, session_id: str | None = None) -> dict[str, Any]:
        """Per-session metrics (``session_id`` set) or all-sessions roll-up."""
        snapshot = self._metrics.snapshot(session_id=session_id)
        snapshot["global"] = global_snapshot()
        snapshot["buffered"] = len(self._buffer)
        return snapshot

    def trace_spans(self, trace_id: str) -> list[dict[str, Any]]:
        """Return the span payloads for one trace, oldest-first (R14).

        Scans the ring buffer for ``SPAN`` events whose ``payload.trace_id``
        matches. Returns ``[]`` when telemetry is empty or the trace is
        unknown / aged out of the buffer. Each entry is the span's payload
        dict (trace_id/span_id/parent_id/duration_ms/status/attributes),
        ready to feed into :func:`minimax_code.telemetry.tracing.build_tree`.
        """
        try:
            items = self._buffer.recent(limit=10_000)
        except Exception:  # noqa: BLE001 — read path is best-effort
            return []
        spans: list[dict[str, Any]] = []
        for item in items:
            if item.get("type") != EventType.SPAN.value:
                continue
            payload = item.get("payload") or {}
            if payload.get("trace_id") == trace_id:
                spans.append(dict(payload))
        return spans

    # ------------------------------------------------------------------
    # Test / admin helpers
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Drop buffered events + per-session metrics (mainly for tests)."""
        self._buffer.clear()
        self._metrics.clear()

    @property
    def buffered_count(self) -> int:
        return len(self._buffer)


__all__ = ["TelemetryEngine"]
