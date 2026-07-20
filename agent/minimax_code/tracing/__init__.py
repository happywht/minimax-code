"""Tracing — observability primitives (R127+).

Fusion of grok-build's ``xai-tracing`` crate. The crate is a
cross-cutting observability layer: operation timers, trace-subscriber
presence detection, W3C traceparent context propagation, and HTTP/gRPC
client trace-injection middleware. ``xai-computer-hub-sdk`` depends on
it, so it sits upstream of the SDK landing.

Why a separate package
----------------------

The Rust workspace keeps ``xai-tracing`` as its own crate so the
observability surface (timers, trace context, client middleware) can be
pulled in by every other crate without dragging the heavier hub / tool
contracts along. The Python landing mirrors that split:
:mod:`minimax_code.tracing` is the observability contract; the hub
(:mod:`minimax_code.computer_hub_core`, R115-R126) and the SDK (landing
after this crate) consume it.

Leaf order
----------

The crate's ``lib.rs`` re-exports six modules. The dependency order is:

1. ``timer`` (R127) — :class:`Timer`, a small RAII operation timer that
   logs START / FINISHED / FAILED boundaries (prefixed with a ``uuid4``
   for correlation). The only leaf with no fastrace / opentelemetry /
   tokio dependency — pure ``Instant`` + ``Uuid`` + ``log`` — so it is
   the natural first leaf and the entry point for the crate's Python
   landing.
2. ``dispatch`` (R128) — :func:`dispatcher_active`, the subscriber
   presence gate: ``True`` when the root logger has a real handler wired
   (a consumer will receive records). Like ``timer`` it has no fastrace
   / opentelemetry / tokio dependency — pure ``tracing::dispatcher`` in
   Rust, ``logging.getLogger().handlers`` in Python — so it lands as the
   second self-contained primitive. The request-span factories landing
   later (``http_client`` / ``grpc_client``) call it before building a
   span, so an unconfigured process pays no trace-construction cost.
3. ``fastrace`` (R129) — :class:`SpanContext` and the W3C traceparent
   codec, plus :func:`current_trace_id` /
   :func:`local_or_random_span_ctx` / :func:`enter_span_with_traceparent`
   over a :class:`~contextvars.ContextVar`. Lands the *pure-logic* subset
   of the Rust ``fastrace`` module: the OTLP reporter, reqwest middleware
   and tonic channel stay deferred (no OTLP backend configured; those
   pieces move with the ``http_client`` / ``grpc_client`` leaves). This
   leaf is the foundation the later leaves consume — every span-bearing
   leaf needs a :class:`SpanContext`.

Later rounds land the remaining leaves (``http_client`` — trace-injecting
httpx middleware; ``grpc_client`` — gRPC trace middleware; ``tokio`` —
asyncio task trace-context propagation; plus the test-only ``testing``
helpers).
"""

from minimax_code.tracing.dispatch import dispatcher_active
from minimax_code.tracing.fastrace import (
    SpanContext,
    current_trace_id,
    enter_span_with_traceparent,
    local_or_random_span_ctx,
)
from minimax_code.tracing.timer import Timer

__all__ = [
    "SpanContext",
    "Timer",
    "current_trace_id",
    "dispatcher_active",
    "enter_span_with_traceparent",
    "local_or_random_span_ctx",
]
