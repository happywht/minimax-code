"""Tests for ``minimax_code.tracing.tokio.spawn_traced`` (R130).

Mirrors grok-build's ``xai-tracing/src/tokio.rs``. The Rust ``spawn_traced``
bolts the current span onto the spawned future via ``Instrument``; the
Python wrapper is a thin pass-through to :func:`asyncio.create_task` because
asyncio copies the contextvars context (carrying R129's SpanContext) for
free. The suite therefore concentrates on the contract that must survive the
wrapping:

* the return is an :class:`asyncio.Task`;
* the active :class:`SpanContext` is visible inside the spawned task (the
  core propagation contract, asserted through ``spawn_traced`` rather than
  the bare :func:`asyncio.create_task` the R129 suite used);
* the context is captured at spawn-call time, not task-run time;
* no active context propagates as ``None``;
* the task runs to a value, can be awaited, and can be cancelled;
* concurrent ``spawn_traced`` tasks stay independent under their own
  captured contexts.

Context isolation reuses the ``clean_span_context`` pattern from
``test_tracing_fastrace``: force ``_current_span_context`` to ``None`` for
the body so a leak elsewhere cannot flip a "no context" assertion. Tests are
``async def`` (pytest-asyncio auto mode runs each in its own event loop).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Iterator

import pytest

from minimax_code.tracing.fastrace import (
    current_trace_id,
    enter_span_with_traceparent,
)
from minimax_code.tracing.tokio import spawn_traced

#: A well-formed W3C traceparent for install / propagation assertions.
_VALID_TRACEPARENT = "00-aabbccddeeff00112233445566778899-0011223344556677-01"


@pytest.fixture
def clean_span_context() -> Iterator[None]:
    """Force the active SpanContext to None for the test body (R130).

    Same isolation pattern as ``test_tracing_fastrace``: a prior test may
    have set ``_current_span_context``; ``set(None)`` returns a token that
    restores the prior value on teardown, so a leak cannot flip a later
    "no context active" assertion.
    """
    from minimax_code.tracing.fastrace import _current_span_context

    token = _current_span_context.set(None)
    try:
        yield
    finally:
        _current_span_context.reset(token)


# ---------------------------------------------------------------------------
# Return type — must be an asyncio.Task scheduled on the running loop
# ---------------------------------------------------------------------------


async def test_spawn_traced_returns_task(clean_span_context: None) -> None:
    async def noop() -> None:
        return None

    task = spawn_traced(noop())
    assert isinstance(task, asyncio.Task)
    await task  # noop completes immediately; await reaps it


# ---------------------------------------------------------------------------
# Core propagation contract — current SpanContext is visible in the task
# ---------------------------------------------------------------------------


async def test_spawn_traced_propagates_current_span_context(
    clean_span_context: None,
) -> None:
    async def child() -> str | None:
        return current_trace_id()

    with enter_span_with_traceparent("test", _VALID_TRACEPARENT):
        task = spawn_traced(child())
        assert await task == _VALID_TRACEPARENT


async def test_spawn_traced_no_context_propagates_none(
    clean_span_context: None,
) -> None:
    async def child() -> str | None:
        return current_trace_id()

    task = spawn_traced(child())
    assert await task is None


# ---------------------------------------------------------------------------
# Capture timing — the context at spawn-call time, not task-run time
# ---------------------------------------------------------------------------


async def test_spawn_traced_captures_context_at_call_time(
    clean_span_context: None,
) -> None:
    # The context active when spawn_traced is *called* is the one the task
    # sees; entering a different context after the call (but before the task
    # runs) must not reach it. The task is gated on an event so it only
    # reads current_trace_id() after the caller has switched context.
    started = asyncio.Event()
    release = asyncio.Event()

    async def child() -> str | None:
        started.set()
        await release.wait()
        return current_trace_id()

    first_tp = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    second_tp = "00-" + "3" * 32 + "-" + "4" * 16 + "-01"

    with enter_span_with_traceparent("first", first_tp):
        task = spawn_traced(child())
        await started.wait()  # task has captured its context and parked
    # now the caller has no context active (the first with-block exited);
    # then it enters a *different* one. The task must still report first.
    with enter_span_with_traceparent("second", second_tp):
        release.set()
        assert await task == first_tp


# ---------------------------------------------------------------------------
# Task lifecycle — runs to a value, cancellable
# ---------------------------------------------------------------------------


async def test_spawn_traced_task_returns_value(clean_span_context: None) -> None:
    async def compute() -> int:
        return 41 + 1

    assert await spawn_traced(compute()) == 42


async def test_spawn_traced_task_is_cancellable(clean_span_context: None) -> None:
    started = asyncio.Event()

    async def long_running() -> None:
        started.set()
        await asyncio.sleep(10)  # cancelled before this elapses

    task = spawn_traced(long_running())
    await started.wait()
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
    assert task.cancelled()


# ---------------------------------------------------------------------------
# Concurrency — independent tasks carry their own captured context
# ---------------------------------------------------------------------------


async def test_spawn_traced_concurrent_tasks_independent(
    clean_span_context: None,
) -> None:
    # two tasks spawned under two different caller contexts each see their
    # own captured context, proving independence under concurrency.
    first_tp = "00-" + "1" * 32 + "-" + "2" * 16 + "-01"
    second_tp = "00-" + "3" * 32 + "-" + "4" * 16 + "-01"

    loop = asyncio.get_running_loop()

    async def child(holder: asyncio.Future[str | None]) -> None:
        holder.set_result(current_trace_id())

    fut1: asyncio.Future[str | None] = loop.create_future()
    fut2: asyncio.Future[str | None] = loop.create_future()

    with enter_span_with_traceparent("first", first_tp):
        t1 = spawn_traced(child(fut1))
    with enter_span_with_traceparent("second", second_tp):
        t2 = spawn_traced(child(fut2))

    await asyncio.gather(t1, t2)
    assert fut1.result() == first_tp
    assert fut2.result() == second_tp
