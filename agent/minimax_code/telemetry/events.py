"""Telemetry event model (R11).

Fuses grok-build's ``TelemetryEvent`` trait idea (a typed struct bound to
a stable ``NAME`` plus a serialisable payload) with MiniMax's pydantic v2
data layer. Every observable agent moment — a session starting, a tool
dispatch, a hook firing, a permission decision — becomes one of these.

Design notes
------------
* ``EventType`` is a closed enum mirroring grok's ``session_metrics`` /
  ``events.rs`` lifecycle set, trimmed to what MiniMax actually emits
  today (YAGNI — doom-loop / trace-upload events stay out until those
  subsystems exist here).
* ``payload`` is ``ConfigDict(extra="allow")`` so call sites can attach
  arbitrary structured fields without a schema migration; the engine
  redacts it before buffering (see :mod:`.redact`).
* ``ts`` defaults to ``now_iso()`` so callers rarely pass it — same clock
  as the ``audit_log`` table, keeping the two timelines joinable.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ..storage.dao._base import now_iso


class EventType(StrEnum):
    """Closed set of observable agent moments."""

    SESSION_START = "session_start"
    SESSION_END = "session_end"
    TURN = "turn"
    TURN_COMPLETED = "turn_completed"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    HOOK_FIRE = "hook_fire"
    PERMISSION = "permission"
    PLUGIN_LOAD = "plugin_load"
    ERROR = "error"


class Severity(StrEnum):
    """Coarse severity for filtering / dashboards."""

    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class TelemetryEvent(BaseModel):
    """One observable moment in the agent's lifecycle.

    Attributes
    ----------
    type:
        Stable event discriminator (see :class:`EventType`).
    session_id:
        Owning session, when applicable. Lifecycle / global events may
        leave this ``None``.
    severity:
        Defaults to ``INFO``; escalte to ``WARN`` / ``ERROR`` for
        degraded-but-recovered or failed moments.
    name:
        Optional sub-label — e.g. the tool name for ``TOOL_CALL``, the
        hook event for ``HOOK_FIRE``. Keeps ``type`` closed while still
        letting call sites carry a fine-grained label.
    payload:
        Free-form structured detail. Redacted in place by the engine
        before it reaches the ring buffer or metrics.
    ts:
        ISO-8601 timestamp; defaults to now via ``now_iso()`` so callers
        rarely construct it.
    """

    model_config = ConfigDict(extra="allow")

    type: EventType
    session_id: str | None = None
    severity: Severity = Severity.INFO
    name: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    ts: str = Field(default_factory=now_iso)


__all__ = ["EventType", "Severity", "TelemetryEvent"]
