"""Structured tracing — span/trace causal model (R14).

Fused from grok-build's ``xai-tracing`` crate, stripped to what a
single-process asyncio agent actually needs. grok-tracing ships the full
OpenTelemetry stack — fastrace spans, OTLP/gRPC exporters,
reqwest-middleware propagation, tower layers, sampling strategies — all of
which is dead weight in one Python process talking to one LLM endpoint.

What survives the YAGNI cut
--------------------------
- :class:`Span` — a named, timed, status-tagged record carrying a
  trace_id/span_id/parent_id triple and free-form attributes.
- :class:`Tracer` — starts spans, threading the parent→child link through a
  ``contextvars.ContextVar`` so nested ``async with tracer.start_span(...)``
  calls form a tree automatically, with per-asyncio-task isolation.
- :class:`Span` is an async context manager: on exit it captures duration +
  exception status, then fires an ``on_close`` callback that emits a
  ``SPAN`` telemetry event through the R11 :class:`TelemetryEngine`.

Why this layer exists
---------------------
The flat R11 event stream answers *that* a turn happened and *that* tools
fired, but not *where* the time went. A turn that takes 3.2s could be LLM
latency, a slow tool, or a retry storm — the flat log can't tell. Spans
answer "LLM 2.1s + tool.search 0.8s + tool.write 0.3s" by giving every
timed operation a parent. :func:`build_tree` reconstructs the forest from
the buffered SPAN events for a trace, so a UI can paint a waterfall.
"""

from __future__ import annotations

import contextvars
import logging
import time
import uuid
from collections.abc import Callable
from enum import StrEnum
from typing import Any

logger = logging.getLogger(__name__)


class SpanStatus(StrEnum):
    """Terminal status of a span, mirroring OTel's UNSET→OK/ERROR model."""

    OK = "ok"
    ERROR = "error"


def _new_id() -> str:
    return uuid.uuid4().hex


# The current span for *this* asyncio task. A nested ``start_span`` reads
# its parent here; ``Span.__aexit__`` resets it. contextvars gives us
# per-task isolation for free — each turn / sub-agent runs in its own task,
# so concurrent turns never tangle their span trees.
_current_span: contextvars.ContextVar[Span | None] = contextvars.ContextVar(
    "minimax_tracing_current_span", default=None
)


class Span:
    """One timed operation in a trace tree.

    Use as an async context manager — never instantiate and time by hand::

        async with tracer.start_span("llm.stream", model="glm-4.6"):
            response = await client.stream(...)

    On normal exit ``status`` is OK and ``duration_ms`` is set; on an
    exception exit ``status`` is ERROR and ``error`` holds a short repr.
    The ``on_close`` callback (set by :class:`Tracer`) fires exactly once,
    after the span is detached from the contextvar — it must never raise.
    """

    __slots__ = (
        "name",
        "trace_id",
        "span_id",
        "parent_id",
        "start_ms",
        "end_ms",
        "status",
        "error",
        "attributes",
        "_on_close",
        "_token",
    )

    def __init__(
        self,
        name: str,
        trace_id: str,
        *,
        parent_id: str | None = None,
        on_close: Callable[[Span], None] | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        self.name = name
        self.trace_id = trace_id
        self.span_id = _new_id()
        self.parent_id = parent_id
        self.start_ms: float = 0.0
        self.end_ms: float | None = None
        self.status = SpanStatus.OK
        self.error: str | None = None
        self.attributes: dict[str, Any] = dict(attributes) if attributes else {}
        self._on_close = on_close
        self._token: contextvars.Token[Span | None] | None = None

    @property
    def duration_ms(self) -> float | None:
        if self.end_ms is None:
            return None
        return round(self.end_ms - self.start_ms, 3)

    def set(self, key: str, value: Any) -> None:
        """Tag the span with an attribute (cheap, fail-open)."""
        try:
            self.attributes[key] = value
        except Exception:  # noqa: BLE001 — attributes are advisory
            logger.debug("span.set(%s) failed", key, exc_info=True)

    def to_record(self) -> dict[str, Any]:
        """Flat dict shape stored in the SPAN event payload."""
        return {
            "trace_id": self.trace_id,
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "duration_ms": self.duration_ms,
            "status": self.status.value,
            "error": self.error,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "attributes": dict(self.attributes),
        }

    async def __aenter__(self) -> Span:
        self.start_ms = time.monotonic() * 1000.0
        self._token = _current_span.set(self)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.end_ms = time.monotonic() * 1000.0
        if exc is not None and exc_type is not None:
            self.status = SpanStatus.ERROR
            self.error = f"{exc_type.__name__}: {exc}"
        if self._token is not None:
            try:
                _current_span.reset(self._token)
            except Exception:  # noqa: BLE001 — reset must never break callers
                logger.debug("span contextvar reset failed", exc_info=True)
            self._token = None
        if self._on_close is not None:
            try:
                self._on_close(self)
            except Exception:  # noqa: BLE001 — observer must not break callers
                logger.debug("span on_close raised", exc_info=True)


class Tracer:
    """Starts spans, linking parent→child via the current-span ContextVar.

    A tracer holds one ``on_close`` callback applied to every span it starts.
    The default process tracer (:func:`get_tracer`) wires ``on_close`` to
    emit a SPAN event through the R11 telemetry engine.
    """

    def __init__(
        self, on_close: Callable[[Span], None] | None = None
    ) -> None:
        self._on_close = on_close

    def start_span(self, name: str, **attributes: Any) -> Span:
        parent = _current_span.get()
        if parent is not None:
            trace_id = parent.trace_id
            parent_id = parent.span_id
        else:
            trace_id = _new_id()
            parent_id = None
        return Span(
            name=name,
            trace_id=trace_id,
            parent_id=parent_id,
            on_close=self._on_close,
            attributes=attributes or None,
        )

    @staticmethod
    def current() -> Span | None:
        """The active span for the current task, or ``None`` at the root."""
        return _current_span.get()


# ---------------------------------------------------------------------------
# Process-level default tracer — emits SPAN events to the R11 engine.
# ---------------------------------------------------------------------------

_default_tracer: Tracer | None = None


def _default_emit(span: Span) -> None:
    """Emit a SPAN telemetry event through the R11 engine (fail-open).

    Every lookup is lazy + guarded: tracing must work before the telemetry
    engine exists (early boot), with the engine disabled (``None``), and
    when the event model itself cannot be imported — in all three cases we
    silently drop the span rather than let observability break the agent.
    """
    try:
        from ..app import ensure_telemetry_engine
    except Exception:  # noqa: BLE001 — optional wiring at import time
        return
    try:
        engine = ensure_telemetry_engine()
    except Exception:  # noqa: BLE001 — engine init must never break callers
        logger.debug("telemetry engine unavailable for span emit", exc_info=True)
        return
    if engine is None:
        return
    try:
        from .events import EventType, TelemetryEvent

        engine.emit(
            TelemetryEvent(
                type=EventType.SPAN,
                session_id=span.attributes.get("session_id"),
                name=span.name,
                payload=span.to_record(),
            )
        )
    except Exception:  # noqa: BLE001 — telemetry is best-effort
        logger.debug("span emit failed", exc_info=True)


def get_tracer() -> Tracer:
    """Process-level singleton tracer wired to the telemetry engine.

    The first call constructs the tracer; later calls return the same
    instance. The ``on_close`` emit is resolved lazily per span close, so
    a tracer created before the engine boots still emits once the engine
    attaches.
    """
    global _default_tracer
    if _default_tracer is None:
        _default_tracer = Tracer(on_close=_default_emit)
    return _default_tracer


def set_tracer(tracer: Tracer | None) -> None:
    """Override (or clear with ``None``) the process tracer — tests only."""
    global _default_tracer
    _default_tracer = tracer


# ---------------------------------------------------------------------------
# Trace reconstruction — flat span records → nested tree.
# ---------------------------------------------------------------------------


def build_tree(span_records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Assemble flat span records into a parent→children forest.

    Each input record must carry ``span_id``; ``parent_id`` is optional.
    Roots are spans whose ``parent_id`` is missing or points at a span not
    in this batch (e.g. a parent that aged out of the ring buffer). Each
    level is sorted by ``start_ms`` so a waterfall view is deterministic.

    Records are shallow-copied before nesting so the caller's list is not
    mutated, and a synthetic ``children`` key is added to every node.
    """
    by_id: dict[str, dict[str, Any]] = {}
    for rec in span_records:
        span_id = rec.get("span_id")
        if not span_id:
            continue
        node = dict(rec)
        node["children"] = []
        by_id[span_id] = node
    roots: list[dict[str, Any]] = []
    for node in by_id.values():
        parent_id = node.get("parent_id")
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(node)
        else:
            roots.append(node)

    def _sort(nodes: list[dict[str, Any]]) -> None:
        nodes.sort(key=lambda n: n.get("start_ms", 0.0))
        for n in nodes:
            _sort(n["children"])

    _sort(roots)
    return roots


__all__ = [
    "Span",
    "SpanStatus",
    "Tracer",
    "build_tree",
    "get_tracer",
    "set_tracer",
]
