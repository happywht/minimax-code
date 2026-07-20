"""Server-side session event emitter facade (R140).

Fusion of grok-build's ``xai-computer-hub-sdk/src/observability.rs`` (275
lines). This is the SDK crate's 8th leaf (after R133 error / R134 handshake /
R135 refcount / R136 donate_pump / R137 trace_donate / R138 connection_borrow
/ R139 auth): a thin facade for emitting session-level events (turn lifecycle,
phase changes) to the connected server as ``ToolNotificationFrame`` custom
notifications. Tool-call events are emitted automatically by the harness's
``call`` path; this bridge handles only the session-event leg.

What migrates vs what does NOT (R132-style YAGNI boundary declaration)
---------------------------------------------------------------------

MIGRATED (transport-agnostic facade):

* :class:`ObservabilityBridge` -- the facade: an optional harness reference +
  the :class:`~minimax_code.tool_protocol.ids.SessionId` it was created for.
* :meth:`ObservabilityBridge.session_id` / :meth:`has_harness` -- accessors.
* :meth:`ObservabilityBridge.emit` -- compute the event_type, dispatch the
  metrics counter, then delegate frame construction + wire send to the harness.
* :func:`_event_type` -- pure module function mapping a
  :data:`~minimax_code.tool_protocol.session_event.SessionEvent` variant to its
  snake_case event_type string.

NOT MIGRATED (framework glue with no Python equivalent yet):

* ``ToolHarness::emit_session_event`` -- the single canonical encoding path
  (frame construction + wire dispatch) lives in ``harness.rs`` (2940 lines),
  a later leaf. The bridge holds the harness as ``Any | None`` and duck-types
  ``await harness.emit_session_event(event)``; a typed accessor + the frame
  builder land with the harness leaf.
* ``crate::metrics::session_event`` -- the session-event counter lives in
  ``metrics.rs`` (552 lines), a later leaf. :func:`_metrics_session_event` is
  a no-op stub whose signature is preserved so the metrics leaf replaces the
  body without touching :meth:`emit` call sites.

Callers MUST also emit to their local sink separately (the bridge handles only
the server leg); see the Rust module docstring's "local sink" note.
"""

from __future__ import annotations

from typing import Any

from minimax_code.tool_protocol.ids import SessionId
from minimax_code.tool_protocol.session_event import SessionEvent

__all__ = ["ObservabilityBridge"]


def _event_type(event: SessionEvent) -> str:
    """Map a ``SessionEvent`` variant to its snake_case event_type string (R140).

    Mirrors the Rust ``match`` arm, but reuses the variant's ``to_wire()``
    ``event_type`` (established by R90 ``session_event.py``) rather than
    re-deriving an ``isinstance`` chain -- DRY: a new variant auto-adapts as
    long as its ``to_wire`` emits ``event_type``. Falls back to ``"unknown"``
    (the forward-compat catch-all) defensively if a variant ever omits it.
    """
    return str(event.to_wire().get("event_type", "unknown"))


def _metrics_session_event(event_type: str) -> None:
    """YAGNI boundary: ``crate::metrics::session_event`` counter (R140).

    No-op until the ``metrics.rs`` leaf (552 lines) lands. The signature is
    preserved so the metrics leaf replaces this body without touching
    :meth:`ObservabilityBridge.emit` call sites.
    """
    # No-op: counter wired up in the metrics.rs leaf.
    return None


class ObservabilityBridge:
    """Facade emitting :data:`SessionEvent`\\ s to the connected server (R140).

    No-ops gracefully when no harness is present (``harness`` is ``None``) and
    silently ignores server-notification failures (fire-and-forget, so server
    issues never affect the sampler's main loop). Callers MUST also emit to
    their local sink separately; this bridge handles only the server leg.
    """

    def __init__(self, harness: Any | None, session_id: SessionId) -> None:
        """Construct a bridge (mirrors Rust ``ObservabilityBridge::new``).

        ``harness`` is ``Any | None`` rather than ``ToolHarness`` because the
        harness leaf (``harness.rs``, 2940 lines) has not landed; :meth:`emit`
        duck-types ``await harness.emit_session_event(event)`` so a typed
        accessor can replace the ``Any`` later without touching call sites.
        """
        self._harness = harness
        self._session_id = session_id

    def session_id(self) -> SessionId:
        """The session id this bridge was created for."""
        return self._session_id

    def has_harness(self) -> bool:
        """Whether a harness is present (i.e. server emission is active)."""
        return self._harness is not None

    async def emit(self, event: SessionEvent) -> None:
        """Emit a session event to the connected server; no-op if no harness (R140).

        Computes the event_type for the metrics counter (YAGNI stub until the
        ``metrics.rs`` leaf), then delegates frame construction + wire dispatch
        to ``harness.emit_session_event`` so the SDK keeps a single canonical
        encoding path. Fire-and-forget: server-notification failures are
        swallowed by the harness's send path, never propagated here.
        """
        event_type = _event_type(event)
        _metrics_session_event(event_type)
        if self._harness is not None:
            await self._harness.emit_session_event(event)
