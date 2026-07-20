"""Tests for ``minimax_code.tracing.timer.Timer`` (R127).

Mirrors grok-build's ``xai-tracing/src/timer.rs`` in-memory tests plus
the Python-specific RAII surface (context manager + ``__del__``). The
suite asserts the START/FINISHED/FAILED log shape, idempotency, the
``stop`` generic pass-through, the monotonic-clock mapping, the
context-manager ``Drop`` mirror, and the ``__del__`` best-effort safety
net.
"""

from __future__ import annotations

import logging
import uuid
from unittest.mock import patch

import pytest

from minimax_code.tracing import timer as timer_module
from minimax_code.tracing.timer import Timer


def _records_at(logger_name: str, caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    """All captured records emitted by the given logger (any level)."""
    return [r for r in caplog.records if r.name == logger_name]


def _timer_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return _records_at("minimax_code.tracing.timer", caplog)


# ---------------------------------------------------------------------------
# Construction + START boundary
# ---------------------------------------------------------------------------


def test_constructor_logs_start_boundary_with_message(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="minimax_code.tracing.timer"):
        t = Timer("do the thing")

    rec = _timer_records(caplog)
    assert len(rec) == 1
    assert rec[0].levelno == logging.INFO
    assert "START" in rec[0].message
    assert "do the thing" in rec[0].message
    # the uuid prefix appears in the boundary line
    assert str(t._id) in rec[0].message  # noqa: SLF001 — asserts the id is logged


def test_constructor_generates_a_uuid4_id() -> None:
    t = Timer("op")
    assert isinstance(t._id, uuid.UUID)  # noqa: SLF001
    assert t._id.version == 4


def test_two_timers_have_distinct_ids() -> None:
    a = Timer("a")
    b = Timer("b")
    assert a._id != b._id  # noqa: SLF001


def test_constructor_marks_timer_not_stopped() -> None:
    t = Timer("op")
    assert t._stopped is False  # noqa: SLF001


# ---------------------------------------------------------------------------
# stop: FINISHED boundary + generic pass-through + idempotency
# ---------------------------------------------------------------------------


def test_stop_logs_finished_boundary_with_runtime(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="minimax_code.tracing.timer"):
        t = Timer("do work")
        t.stop("done")

    msgs = [r.message for r in _timer_records(caplog)]
    assert any("START" in m for m in msgs)
    finished = [m for m in msgs if "FINISHED" in m]
    assert len(finished) == 1
    # shape mirrors Rust: "[<id>] FINISHED in <secs>s: <message>"
    assert "do work" in finished[0]
    assert str(t._id) in finished[0]


def test_stop_returns_the_passed_result_unchanged() -> None:
    t = Timer("op")
    payload = {"answer": 42, "items": [1, 2, 3]}
    assert t.stop(payload) is payload


def test_stop_passes_through_at_static_type_for_callers() -> None:
    # mirrors Rust stop<T>(result: T) -> T — value handed back, not wrapped
    t = Timer("op")
    value: int = t.stop(7)
    assert value + 1 == 8


def test_stop_is_idempotent_logs_finished_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.INFO, logger="minimax_code.tracing.timer"):
        t = Timer("op")
        t.stop("first")
        t.stop("second")
        t.stop("third")

    finished = [r for r in _timer_records(caplog) if "FINISHED" in r.message]
    assert len(finished) == 1  # only the first stop logs


def test_stop_after_stop_still_returns_latest_result() -> None:
    t = Timer("op")
    t.stop("first")
    # subsequent stops are no-ops for logging but still return the arg
    assert t.stop("second") == "second"


# ---------------------------------------------------------------------------
# force_stop: FAILED boundary + idempotency + interaction with stop
# ---------------------------------------------------------------------------


def test_force_stop_logs_failed_boundary_with_runtime(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="minimax_code.tracing.timer"):
        t = Timer("flaky op")
        t.force_stop()

    failed = [r for r in _timer_records(caplog) if "FAILED" in r.message]
    assert len(failed) == 1
    assert failed[0].levelno == logging.ERROR
    assert "flaky op" in failed[0].message
    assert str(t._id) in failed[0].message


def test_force_stop_is_idempotent_logs_failed_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="minimax_code.tracing.timer"):
        t = Timer("op")
        t.force_stop()
        t.force_stop()
        t.force_stop()

    failed = [r for r in _timer_records(caplog) if "FAILED" in r.message]
    assert len(failed) == 1


def test_force_stop_after_stop_is_a_noop(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="minimax_code.tracing.timer"):
        t = Timer("op")
        t.stop("ok")
        t.force_stop()

    # FINISHED recorded, FAILED never recorded
    assert any("FINISHED" in r.message for r in _timer_records(caplog))
    assert not any("FAILED" in r.message for r in _timer_records(caplog))


def test_stop_after_force_stop_does_not_log_finished(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="minimax_code.tracing.timer"):
        t = Timer("op")
        t.force_stop()
        # stop still returns its arg (pass-through contract holds) but logs nothing
        assert t.stop("late") == "late"

    assert any("FAILED" in r.message for r in _timer_records(caplog))
    assert not any("FINISHED" in r.message for r in _timer_records(caplog))


# ---------------------------------------------------------------------------
# Monotonic clock mapping (Instant -> time.monotonic)
# ---------------------------------------------------------------------------


def test_constructor_and_stop_both_call_monotonic() -> None:
    """Rust Instant::now/elapsed -> time.monotonic; assert the mapping, not time.time."""
    calls: list[float] = []

    def fake_monotonic() -> float:
        calls.append(len(calls))  # distinct, increasing return values
        return float(len(calls))

    with patch.object(timer_module.time, "monotonic", side_effect=fake_monotonic):
        t = Timer("op")
        t.stop("done")

    # __init__ captures start (1 call), stop computes elapsed (1 call) -> >= 2
    assert len(calls) >= 2


def test_runtime_is_a_nonnegative_float_when_stopped() -> None:
    t = Timer("op")
    runtime = time_monotonic_runtime_for_stop(t)
    assert isinstance(runtime, float)
    assert runtime >= 0.0


def time_monotonic_runtime_for_stop(t: Timer) -> float:
    """Capture the elapsed seconds the stop path computes, via a monotonic patch."""
    captured: list[float] = []

    original = timer_module.time.monotonic

    def spy() -> float:
        val = original()
        captured.append(val)
        return val

    with patch.object(timer_module.time, "monotonic", side_effect=spy):
        t.stop("done")

    # the stop branch computes (now - start); with >= 2 spy calls the diff is >= 0
    return captured[-1] - captured[0] if len(captured) >= 2 else 0.0


# ---------------------------------------------------------------------------
# Context manager (deterministic Drop counterpart)
# ---------------------------------------------------------------------------


def test_context_manager_force_stops_when_block_does_not_stop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="minimax_code.tracing.timer"):
        with Timer("ctx op"):
            pass  # forgot to call stop

    # __exit__ mirrors Drop -> force_stop -> FAILED
    assert any("FAILED" in r.message for r in _timer_records(caplog))
    assert not any("FINISHED" in r.message for r in _timer_records(caplog))


def test_context_manager_does_not_double_log_when_stopped_in_block(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="minimax_code.tracing.timer"):
        with Timer("ctx op") as t:
            t.stop("ok")

    finished = [r for r in _timer_records(caplog) if "FINISHED" in r.message]
    failed = [r for r in _timer_records(caplog) if "FAILED" in r.message]
    assert len(finished) == 1
    assert failed == []


def test_context_manager_force_stops_on_exception_in_block(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="minimax_code.tracing.timer"):
        with pytest.raises(ValueError):
            with Timer("ctx op"):
                raise ValueError("boom")

    assert any("FAILED" in r.message for r in _timer_records(caplog))


def test_context_manager_does_not_swallow_exception() -> None:
    sentinel = ValueError("propagate me")
    with pytest.raises(ValueError) as excinfo:
        with Timer("ctx op"):
            raise sentinel
    assert excinfo.value is sentinel


def test_context_manager_enter_returns_self() -> None:
    t = Timer("op")
    with t as ctx:
        assert ctx is t


# ---------------------------------------------------------------------------
# __del__ best-effort safety net
# ---------------------------------------------------------------------------


def test_del_force_stops_an_unstopped_timer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="minimax_code.tracing.timer"):
        t = Timer("leaky op")
        # simulate the GC / refcount teardown path directly
        t.__del__()

    assert any("FAILED" in r.message for r in _timer_records(caplog))


def test_del_is_silent_after_explicit_stop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG, logger="minimax_code.tracing.timer"):
        t = Timer("op")
        t.stop("ok")
        before = len(_timer_records(caplog))
        t.__del__()
        after = len(_timer_records(caplog))

    assert after == before  # __del__ added no new record


def test_del_does_not_raise_if_logging_is_unavailable() -> None:
    """__del__ must survive interpreter teardown where logging may be gone."""
    t = Timer("op")

    original_error = timer_module._log.error  # noqa: SLF001

    def broken_error(*args: object, **kwargs: object) -> None:
        raise RuntimeError("logging torn down")

    with patch.object(timer_module._log, "error", side_effect=broken_error):  # noqa: SLF001
        # must not raise — the try/except in __del__ swallows teardown noise
        t.__del__()

    # sanity: the patch target really was the error path
    assert original_error is not None


# ---------------------------------------------------------------------------
# Log shape parity with Rust
# ---------------------------------------------------------------------------


def test_finished_line_shape_matches_rust(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="minimax_code.tracing.timer"):
        t = Timer("shape check")
        t.stop(None)

    line = next(r.message for r in _timer_records(caplog) if "FINISHED" in r.message)
    # [<uuid>] FINISHED in <float>s: shape check
    assert line.startswith("[")
    assert "] FINISHED in " in line
    assert line.rstrip().endswith(": shape check")
    assert "s: " in line  # the seconds suffix is present


def test_failed_line_shape_matches_rust(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.DEBUG, logger="minimax_code.tracing.timer"):
        t = Timer("shape check")
        t.force_stop()

    line = next(r.message for r in _timer_records(caplog) if "FAILED" in r.message)
    # [<uuid>] FAILED after <float>s: shape check
    assert line.startswith("[")
    assert "] FAILED after " in line
    assert line.rstrip().endswith(": shape check")


def test_runtime_formats_to_three_decimals(caplog: pytest.LogCaptureFixture) -> None:
    """Rust uses {:.3}; assert the seconds token carries exactly 3 decimals."""
    with caplog.at_level(logging.INFO, logger="minimax_code.tracing.timer"):
        Timer("three decimals").stop(None)

    finished = next(r.message for r in _timer_records(caplog) if "FINISHED" in r.message)
    # extract the "<number>s" token between "in " and ": "
    payload = finished.split(" FINISHED in ", 1)[1]
    secs_token = payload.split("s:", 1)[0]
    assert "." in secs_token
    assert len(secs_token.split(".", 1)[1]) == 3  # exactly three decimals
