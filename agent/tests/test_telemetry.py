"""Unit tests for the telemetry subsystem (R11).

Covers the four layers in isolation before the engine integrates them:

* :mod:`redact` — secret/path/url scrubbing, recursive value walk.
* :class:`RingBuffer` — capacity, recency, clear.
* :class:`MetricsRegistry` — per-session counters, latency stats, eviction.
* :class:`TelemetryEngine` — emit → redact → buffer + metrics, fail-open.
"""

from __future__ import annotations

import pytest

from minimax_code.telemetry import (
    EventType,
    MetricsRegistry,
    RingBuffer,
    TelemetryEngine,
    TelemetryEvent,
    redact_paths,
    redact_secrets,
    redact_value,
    url_origin,
)

# ---------------------------------------------------------------------------
# redact
# ---------------------------------------------------------------------------


def test_redact_secrets_scrubs_known_shapes() -> None:
    assert "[REDACTED]" in redact_secrets("key sk-abcd1234efgh5678 end")
    assert "[REDACTED]" in redact_secrets("Authorization: Bearer abcdef1234567890")
    assert "[REDACTED]" in redact_secrets("api_key=AKIAIOSFODNN7EXAMPLE")
    assert "[REDACTED]" in redact_secrets('"password": "supersecret123"')


def test_redact_secrets_leaves_plain_text_alone() -> None:
    assert redact_secrets("just a normal sentence") == "just a normal sentence"
    # Bare keyword without a secret-shaped value must not be touched.
    assert redact_secrets("the api key is missing") == "the api key is missing"


def test_redact_paths_collapses_home(tmp_path) -> None:
    home = str(tmp_path)
    assert redact_paths(home, home=home) == "~"
    import os

    sub = home + os.sep + "proj" + os.sep + "src"
    assert redact_paths(sub, home=home) == "~" + os.sep + "proj" + os.sep + "src"
    # Unrelated path passes through.
    assert redact_paths("/etc/passwd", home=home) == "/etc/passwd"


def test_url_origin_drops_path_and_query() -> None:
    assert url_origin("https://collector.example:4318/v1/logs?token=x") == (
        "https://collector.example:4318"
    )
    assert url_origin("not a url") == "not a url"


def test_redact_value_walks_nested_structures(monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch _home so the test path is recognised as a home subtree on any OS.
    import minimax_code.telemetry.redact as _redact_mod

    monkeypatch.setattr(_redact_mod, "_home", lambda: "/Users/alice")
    payload = {
        "path": "/Users/alice/secret.txt",
        "nested": {"key": "sk-abcd1234efgh5678", "ok": "plain"},
        "urls": ["https://api.example.com/users/42?token=abc1234567"],
        "n": 7,
    }
    out = redact_value(payload)
    assert "alice" not in out["path"]
    assert out["nested"]["key"] == "[REDACTED]"
    assert out["nested"]["ok"] == "plain"
    assert out["urls"][0] == "https://api.example.com"
    assert out["n"] == 7
    # Input is not mutated.
    assert payload["nested"]["key"] == "sk-abcd1234efgh5678"


# ---------------------------------------------------------------------------
# RingBuffer
# ---------------------------------------------------------------------------


def test_ring_buffer_drops_oldest_at_capacity() -> None:
    buf = RingBuffer(capacity=3)
    for i in range(5):
        buf.append(i)
    assert len(buf) == 3
    assert buf.recent() == [2, 3, 4]
    assert buf.recent(limit=2) == [3, 4]
    assert buf.recent(limit=0) == []


def test_ring_buffer_clear() -> None:
    buf = RingBuffer(capacity=10)
    buf.append("x")
    buf.clear()
    assert len(buf) == 0
    assert buf.recent() == []


def test_ring_buffer_rejects_bad_capacity() -> None:
    with pytest.raises(ValueError):
        RingBuffer(capacity=0)


# ---------------------------------------------------------------------------
# MetricsRegistry
# ---------------------------------------------------------------------------


def test_metrics_counts_tool_calls_and_latency() -> None:
    reg = MetricsRegistry(max_sessions=8)
    sid = "sess-1"
    reg.record(TelemetryEvent(type=EventType.SESSION_START, session_id=sid))
    for dur in (10, 20, 30, 40):
        reg.record(
            TelemetryEvent(
                type=EventType.TOOL_CALL,
                session_id=sid,
                name="read_file",
                payload={"duration_ms": dur},
            )
        )
    reg.record(
        TelemetryEvent(
            type=EventType.TOOL_RESULT,
            session_id=sid,
            payload={"status": "error"},
        )
    )
    snap = reg.snapshot(session_id=sid)
    assert snap["session_starts"] == 1
    assert snap["tool_calls"] == 4
    assert snap["tool_errors"] == 1
    assert snap["latency_ms"]["count"] == 4
    assert snap["latency_ms"]["avg"] == 25.0
    assert snap["latency_ms"]["max"] == 40


def test_metrics_evicts_oldest_session() -> None:
    reg = MetricsRegistry(max_sessions=2)
    reg.record(TelemetryEvent(type=EventType.TURN, session_id="a"))
    reg.record(TelemetryEvent(type=EventType.TURN, session_id="b"))
    reg.record(TelemetryEvent(type=EventType.TURN, session_id="c"))
    roll = reg.snapshot()
    assert roll["sessions"] == 2
    present = {s["session_id"] for s in roll["per_session"]}
    assert present == {"b", "c"}  # "a" evicted (least-recently-touched)


# ---------------------------------------------------------------------------
# TelemetryEngine (integration of the three layers)
# ---------------------------------------------------------------------------


def test_engine_emit_redacts_buffers_and_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    import minimax_code.telemetry.redact as _redact_mod

    # /home/bob only collapses when _home returns it — patch for portability.
    monkeypatch.setattr(_redact_mod, "_home", lambda: "/home/bob")
    engine = TelemetryEngine(buffer_capacity=10)
    engine.emit(
        TelemetryEvent(
            type=EventType.TOOL_CALL,
            session_id="s1",
            name="read_file",
            payload={"api_key": "sk-abcd1234efgh5678", "path": "/home/bob/x"},
        )
    )
    events = engine.recent()
    assert len(events) == 1
    payload = events[0]["payload"]
    assert payload["api_key"] == "[REDACTED]"
    assert "bob" not in payload["path"]
    metrics = engine.metrics(session_id="s1")
    assert metrics["tool_calls"] == 1


def test_engine_recent_filters_by_type_and_session() -> None:
    engine = TelemetryEngine()
    engine.emit(TelemetryEvent(type=EventType.SESSION_START, session_id="s1"))
    engine.emit(TelemetryEvent(type=EventType.TOOL_CALL, session_id="s1", name="t"))
    engine.emit(TelemetryEvent(type=EventType.TOOL_CALL, session_id="s2", name="t"))
    assert len(engine.recent(event_type=EventType.TOOL_CALL)) == 2
    assert len(engine.recent(event_type=EventType.TOOL_CALL, session_id="s2")) == 1
    assert len(engine.recent(session_id="s1")) == 2


def test_engine_emit_is_fail_open(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = TelemetryEngine()

    def boom(_item: object) -> None:
        raise RuntimeError("buffer exploded")

    monkeypatch.setattr(engine._buffer, "append", boom)
    # Must not raise even though the buffer is broken.
    engine.emit(TelemetryEvent(type=EventType.TURN, session_id="s1"))
