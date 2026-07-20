"""asyncio task carrying the current trace context (R130).

Fusion of grok-build's ``xai-tracing/src/tokio.rs``. The Rust module is a
single function ``spawn_traced`` — spawn a tokio task whose future is
instrumented with the current tracing span, so the task inherits the
caller's trace context. This landing mirrors it as a thin wrapper over
:func:`asyncio.create_task`.

Why the wrapper is so thin
--------------------------

Rust needs the explicit ``future.instrument(Span::current())`` step because
tokio tasks do **not** carry task-local span state by default —
``Instrument`` attaches it. Python is different: :func:`asyncio.create_task`
automatically copies the current :mod:`contextvars` context (via
:func:`contextvars.copy_context`) into the new task, and the active
:class:`~minimax_code.tracing.fastrace.SpanContext` lives in exactly such a
:class:`~contextvars.ContextVar` (R129). So the trace context the Rust
version has to bolt on explicitly is already present for free — the wrapper
exists for migration fidelity (1:1 crate symbol) and to make the "this spawn
must carry trace context" intent explicit at call sites, not because any
propagation work is needed inside it.

Captured at spawn-call time
---------------------------

``Span::current()`` in Rust is read at the moment ``spawn_traced`` is called
(the caller's current span) and then bound to the future; later changes to
the caller's span do not reach the spawned task. The Python counterpart is
faithful: :func:`asyncio.create_task` snapshots the contextvars context at
call time, so a context entered **after** the spawn call does not appear in
the task. The R129 suite already pinned this for the bare
:func:`asyncio.create_task` (``test_context_propagates_to_spawned_asyncio_task``
/ ``test_context_reset_in_parent_does_not_leak_to_unrelated_task``); the
R130 suite re-asserts it through ``spawn_traced`` to prove the wrapper does
not disturb the capture timing.

Not a new span
--------------

Like the Rust original, ``spawn_traced`` propagates the *current* context;
it does not mint a new span for the task. Callers that need a fresh span
should enter one inside the coroutine — the Python analogue of the Rust
docstring's "manually instrument the future using ``tracing::Instrument``"
note.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import TypeVar

__all__ = ["spawn_traced"]

#: Result type of the spawned coroutine. Bound through the return type so a
#: ``spawn_traced`` call infers ``asyncio.Task[<coro return type>]``.
_T = TypeVar("_T")


def spawn_traced(coro: Coroutine[object, object, _T]) -> asyncio.Task[_T]:
    """Spawn an asyncio task carrying the current SpanContext (R130).

    Mirrors ``xai-tracing::tokio::spawn_traced``: spawn a task that runs
    under the trace context active at the call site. Python's
    :func:`asyncio.create_task` copies the current :mod:`contextvars`
    context into the new task automatically, so the active
    :class:`~minimax_code.tracing.fastrace.SpanContext` (R129) propagates
    for free — no explicit ``instrument`` step is needed (see the module
    docstring's "why so thin" note). Returns the :class:`asyncio.Task`
    handle for awaiting or cancellation.

    The current context is captured at call time, matching Rust's
    ``Span::current()`` read before ``tokio::spawn``. Like the Rust
    original this does **not** create a new span; callers needing a fresh
    span enter one inside the coroutine.

    :param co: The coroutine to spawn as a traced task.
    :return: The spawned :class:`~asyncio.Task`, already scheduled on the
        running event loop.
    :raises RuntimeError: if no event loop is running — the standard
        :func:`asyncio.create_task` precondition, surfaced unchanged.
    """
    return asyncio.create_task(coro)
