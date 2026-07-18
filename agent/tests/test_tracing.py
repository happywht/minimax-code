"""Tests for the R14 structured tracing layer (``telemetry.tracing``).

Covers the contract the rest of the agent leans on:
- a span times itself and ends OK, or ERROR with a short repr on raise;
- nested ``async with tracer.start_span(...)`` calls share one trace_id
  and link parent→child through a per-task contextvar;
- concurrent asyncio tasks never tangle their span trees;
- :func:`build_tree` turns the flat span records into a deterministic
  parent→children forest (with orphan-parent handling);
- the default process tracer emits ``SPAN`` events through the R11
  :class:`TelemetryEngine`, and does so fail-open when the engine is
  absent or raises.
"""

from __future__ import annotations

import asyncio

import pytest

from minimax_code.telemetry import tracing
from minimax_code.telemetry.engine import TelemetryEngine
from minimax_code.telemetry.events import EventType
from minimax_code.telemetry.tracing import (
    Span,
    SpanStatus,
    Tracer,
    build_tree,
    get_tracer,
    set_tracer,
)


@pytest.fixture(autouse=True)
def _isolate_tracer() -> None:
    """Each test starts with a clean process tracer; restored on exit."""
    original = tracing._default_tracer
    tracing._default_tracer = None
    yield
    tracing._default_tracer = original


def _capture_tracer() -> tuple[Tracer, list[Span]]:
    """A tracer that records every closed span instead of emitting."""
    closed: list[Span] = []

    def on_close(span: Span) -> None:
        closed.append(span)

    tracer = Tracer(on_close=on_close)
    set_tracer(tracer)
    return tracer, closed


# ---------------------------------------------------------------------------
# Span lifecycle
# ---------------------------------------------------------------------------


async def test_span_records_duration_and_ok_status() -> None:
    tracer, closed = _capture_tracer()
    async with tracer.start_span("op"):
        await asyncio.sleep(0.005)
    assert len(closed) == 1
    span = closed[0]
    assert span.name == "op"
    assert span.status is SpanStatus.OK
    assert span.error is None
    assert span.duration_ms is not None
    assert span.duration_ms >= 0.0
    assert span.end_ms is not None


async def test_span_captures_error_on_exception() -> None:
    tracer, closed = _capture_tracer()
    with pytest.raises(ValueError, match="boom"):
        async with tracer.start_span("op"):
            raise ValueError("boom")
    assert len(closed) == 1
    span = closed[0]
    assert span.status is SpanStatus.ERROR
    assert span.error is not None
    assert "ValueError" in span.error
    assert "boom" in span.error


async def test_span_attributes_round_trip() -> None:
    tracer, closed = _capture_tracer()
    async with tracer.start_span("op", model="glm-4.6", iteration=3):
        pass
    span = closed[0]
    assert span.attributes == {"model": "glm-4.6", "iteration": 3}
    record = span.to_record()
    assert record["attributes"] == {"model": "glm-4.6", "iteration": 3}
    assert record["status"] == "ok"
    assert record["trace_id"] == span.trace_id
    assert record["span_id"] == span.span_id
    assert record["parent_id"] is None


async def test_span_set_attribute_advisory() -> None:
    tracer, closed = _capture_tracer()
    async with tracer.start_span("op") as span:
        span.set("k", "v")
    assert closed[0].attributes["k"] == "v"


# ---------------------------------------------------------------------------
# Parent→child linking via contextvars
# ---------------------------------------------------------------------------


async def test_nested_spans_share_trace_and_link_parent() -> None:
    tracer, closed = _capture_tracer()
    async with tracer.start_span("parent") as parent:
        async with tracer.start_span("child") as child:
            assert Tracer.current() is child
        assert Tracer.current() is parent
    assert Tracer.current() is None

    assert len(closed) == 2
    # Close order is LIFO: the child span exits (and fires on_close)
    # before the parent, so it lands first in the captured list.
    child_span, parent_span = closed
    assert parent_span.trace_id == child_span.trace_id
    assert child_span.parent_id == parent_span.span_id
    assert parent_span.parent_id is None


async def test_concurrent_tasks_have_isolated_span_trees() -> None:
    """Two turns running concurrently must not tangle their span trees."""
    tracer, closed = _capture_tracer()

    async def worker(name: str) -> None:
        async with tracer.start_span(name):
            await asyncio.sleep(0.005)

    await asyncio.gather(worker("a"), worker("b"))
    assert len(closed) == 2
    by_name = {s.name: s for s in closed}
    # Two independent roots → two distinct trace ids, neither has a parent.
    assert by_name["a"].trace_id != by_name["b"].trace_id
    assert by_name["a"].parent_id is None
    assert by_name["b"].parent_id is None


# ---------------------------------------------------------------------------
# Trace reconstruction
# ---------------------------------------------------------------------------


def test_build_tree_assembles_forest() -> None:
    records = [
        {"span_id": "root", "parent_id": None, "name": "agent.turn", "start_ms": 0.0},
        {"span_id": "llm1", "parent_id": "root", "name": "llm.stream", "start_ms": 1.0},
        {"span_id": "tool1", "parent_id": "root", "name": "tool.search", "start_ms": 2.0},
        {"span_id": "llm2", "parent_id": "root", "name": "llm.stream", "start_ms": 3.0},
    ]
    forest = build_tree(records)
    assert len(forest) == 1
    root = forest[0]
    assert root["span_id"] == "root"
    assert [c["span_id"] for c in root["children"]] == ["llm1", "tool1", "llm2"]


def test_build_tree_promotes_orphan_to_root() -> None:
    """A parent_id pointing at a span aged out of the buffer → a root."""
    records = [
        {"span_id": "child", "parent_id": "ghost", "name": "llm.stream", "start_ms": 0.0},
    ]
    forest = build_tree(records)
    assert len(forest) == 1
    assert forest[0]["span_id"] == "child"


def test_build_tree_sorts_levels_by_start_ms() -> None:
    records = [
        {"span_id": "b", "parent_id": None, "name": "b", "start_ms": 10.0},
        {"span_id": "a", "parent_id": None, "name": "a", "start_ms": 1.0},
    ]
    forest = build_tree(records)
    assert [n["span_id"] for n in forest] == ["a", "b"]


def test_build_tree_does_not_mutate_input() -> None:
    records = [{"span_id": "r", "parent_id": None, "name": "r", "start_ms": 0.0}]
    build_tree(records)
    assert "children" not in records[0]


# ---------------------------------------------------------------------------
# Default tracer → telemetry engine wiring (fail-open)
# ---------------------------------------------------------------------------


async def test_default_emit_publishes_span_event(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = TelemetryEngine(buffer_capacity=64)
    monkeypatch.setattr(
        "minimax_code.app.ensure_telemetry_engine",
        lambda: engine,
    )
    tracer = get_tracer()

    async with tracer.start_span("agent.turn", session_id="s1"):
        async with tracer.start_span("llm.stream", model="glm-4.6"):
            await asyncio.sleep(0.002)

    span_events = engine.recent(limit=100, event_type=EventType.SPAN)
    assert len(span_events) == 2
    # The root span carried session_id in its attributes; only its event
    # inherits it (the child did not tag session_id).
    assert any(e["session_id"] == "s1" for e in span_events)

    trace_id = span_events[0]["payload"]["trace_id"]
    spans = engine.trace_spans(trace_id)
    assert len(spans) == 2
    names = {s["name"] for s in spans}
    assert names == {"agent.turn", "llm.stream"}
    root = next(s for s in spans if s["name"] == "agent.turn")
    llm = next(s for s in spans if s["name"] == "llm.stream")
    assert llm["parent_id"] == root["span_id"]
    assert llm["trace_id"] == root["trace_id"]


async def test_emit_fail_open_when_engine_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("minimax_code.app.ensure_telemetry_engine", lambda: None)
    tracer = get_tracer()
    # Must not raise; span completes normally.
    async with tracer.start_span("op"):
        await asyncio.sleep(0.001)


async def test_emit_fail_open_when_engine_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise RuntimeError("engine init failed")

    monkeypatch.setattr("minimax_code.app.ensure_telemetry_engine", boom)
    tracer = get_tracer()
    async with tracer.start_span("op"):
        await asyncio.sleep(0.001)


# ---------------------------------------------------------------------------
# Singleton / injection
# ---------------------------------------------------------------------------


def test_get_tracer_is_singleton() -> None:
    a = get_tracer()
    b = get_tracer()
    assert a is b


def test_set_tracer_overrides_singleton() -> None:
    custom = Tracer()
    set_tracer(custom)
    assert get_tracer() is custom
    set_tracer(None)
    assert get_tracer() is not custom
