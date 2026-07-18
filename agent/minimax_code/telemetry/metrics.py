"""Per-session metrics aggregation (R11).

Holds rolling counters and a latency sample window per session so
``telemetry.metrics`` can answer "how busy / how error-prone was this
session?" without scanning the full audit trail. Mirrors the intent of
grok-build's ``session_metrics.rs`` (per-session lifecycle counters),
trimmed to the events MiniMax emits today.

The registry is bounded: once ``max_sessions`` distinct sessions are
tracked, the least-recently-touched one is evicted (FIFO by insertion),
so a long-running process cannot grow this unboundedly.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

from .events import EventType, Severity, TelemetryEvent


@dataclass
class SessionMetrics:
    """Rolling counters + latency samples for one session."""

    session_id: str
    session_starts: int = 0
    session_ends: int = 0
    turns: int = 0
    turn_completions: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    hook_fires: int = 0
    permissions: int = 0
    plugin_loads: int = 0
    errors: int = 0
    warnings: int = 0
    tool_durations_ms: list[int] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Snapshot to a JSON-friendly dict (latency stats folded in)."""
        durations = self.tool_durations_ms
        count = len(durations)
        total = sum(durations)
        avg = round(total / count, 2) if count else 0.0
        p50 = _percentile(durations, 50) if count else 0
        p95 = _percentile(durations, 95) if count else 0
        return {
            "session_id": self.session_id,
            "session_starts": self.session_starts,
            "session_ends": self.session_ends,
            "turns": self.turns,
            "turn_completions": self.turn_completions,
            "tool_calls": self.tool_calls,
            "tool_errors": self.tool_errors,
            "hook_fires": self.hook_fires,
            "permissions": self.permissions,
            "plugin_loads": self.plugin_loads,
            "errors": self.errors,
            "warnings": self.warnings,
            "latency_ms": {
                "count": count,
                "avg": avg,
                "p50": p50,
                "p95": p95,
                "max": max(durations) if durations else 0,
            },
        }


def _percentile(samples: list[int], pct: float) -> int:
    """Nearest-rank percentile; ``samples`` is small (capped window)."""
    if not samples:
        return 0
    ordered = sorted(samples)
    # Nearest-rank: ceil(pct/100 * n), clamped to [1, n].
    import math

    rank = max(1, math.ceil(pct / 100.0 * len(ordered)))
    return ordered[min(rank, len(ordered)) - 1]


class MetricsRegistry:
    """Bounded map of ``session_id`` → :class:`SessionMetrics`."""

    def __init__(self, max_sessions: int = 64, latency_window: int = 200) -> None:
        if max_sessions < 1:
            raise ValueError("max_sessions must be >= 1")
        self._sessions: OrderedDict[str, SessionMetrics] = OrderedDict()
        self._lock = Lock()
        self._max_sessions = max_sessions
        self._latency_window = latency_window

    def _get_or_create(self, session_id: str) -> SessionMetrics:
        metrics = self._sessions.get(session_id)
        if metrics is not None:
            # Mark as most-recently-used (move to end).
            self._sessions.move_to_end(session_id)
            return metrics
        metrics = SessionMetrics(session_id=session_id)
        self._sessions[session_id] = metrics
        # Evict the least-recently-inserted session if over capacity.
        while len(self._sessions) > self._max_sessions:
            self._sessions.popitem(last=False)
        return metrics

    def record(self, event: TelemetryEvent) -> None:
        """Fold one event into per-session (and global) counters."""
        # Global counters apply even to events with no session id.
        global_metrics = self._global_locked()
        self._bump_severity(global_metrics, event)
        if event.session_id is None:
            return
        with self._lock:
            metrics = self._get_or_create(event.session_id)
            self._apply(metrics, event)

    def _global_locked(self) -> _GlobalMetrics:
        return _GLOBAL

    def _bump_severity(self, global_metrics: _GlobalMetrics, event: TelemetryEvent) -> None:
        if event.severity == Severity.ERROR:
            global_metrics.errors += 1
        elif event.severity == Severity.WARN:
            global_metrics.warnings += 1

    def _apply(self, metrics: SessionMetrics, event: TelemetryEvent) -> None:
        if event.severity == Severity.ERROR:
            metrics.errors += 1
        elif event.severity == Severity.WARN:
            metrics.warnings += 1
        et = event.type
        if et == EventType.SESSION_START:
            metrics.session_starts += 1
        elif et == EventType.SESSION_END:
            metrics.session_ends += 1
        elif et == EventType.TURN:
            metrics.turns += 1
        elif et == EventType.TURN_COMPLETED:
            metrics.turn_completions += 1
        elif et == EventType.TOOL_CALL:
            metrics.tool_calls += 1
            duration = event.payload.get("duration_ms")
            if isinstance(duration, (int, float)):
                metrics.tool_durations_ms.append(int(duration))
                # Cap the latency window (drop oldest samples).
                if len(metrics.tool_durations_ms) > self._latency_window:
                    del metrics.tool_durations_ms[: -self._latency_window]
        elif et == EventType.TOOL_RESULT:
            if event.payload.get("status") == "error":
                metrics.tool_errors += 1
        elif et == EventType.HOOK_FIRE:
            metrics.hook_fires += 1
        elif et == EventType.PERMISSION:
            metrics.permissions += 1
        elif et == EventType.PLUGIN_LOAD:
            metrics.plugin_loads += 1

    def snapshot(self, session_id: str | None = None) -> dict[str, Any]:
        """Return one session's metrics, or an ``all`` roll-up when ``None``."""
        with self._lock:
            if session_id is not None:
                metrics = self._sessions.get(session_id)
                return metrics.as_dict() if metrics else {}
            return {
                "sessions": len(self._sessions),
                "per_session": [m.as_dict() for m in self._sessions.values()],
            }

    def clear(self) -> None:
        """Drop all per-session metrics (mainly for tests)."""
        with self._lock:
            self._sessions.clear()


@dataclass
class _GlobalMetrics:
    """Process-wide severity counters (events with no session id count here)."""

    errors: int = 0
    warnings: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {"errors": self.errors, "warnings": self.warnings}


# Single process-wide instance — severity tallies survive session eviction.
_GLOBAL = _GlobalMetrics()


def global_snapshot() -> dict[str, Any]:
    """Return the process-wide severity counters."""
    return _GLOBAL.as_dict()


__all__ = ["SessionMetrics", "MetricsRegistry", "global_snapshot"]
