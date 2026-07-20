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
4. ``tokio`` (R130) — :func:`minimax_code.tracing.tokio.spawn_traced`, the
   trace-aware asyncio task spawner. Mirrors ``tokio::spawn`` +
   ``future.instrument(Span::current())`` as a thin wrapper over
   :func:`asyncio.create_task`: asyncio copies the current contextvars
   context (carrying R129's :class:`SpanContext`) into the new task for
   free, so the propagation the Rust version bolts on explicitly is already
   present. Unlike ``timer`` / ``dispatch`` / ``fastrace``, the Rust
   ``lib.rs`` declares ``pub mod tokio`` but does **not** ``pub use
   tokio::*`` — callers reach it as ``xai_tracing::tokio::spawn_traced``.
   This landing matches that: ``spawn_traced`` is **not** re-exported from
   the package barrel, only importable as
   ``minimax_code.tracing.tokio.spawn_traced``.
5. ``http_client`` (R131) — :func:`attach_trace_to_http_request` and
   :func:`traceparent_request_hook`, outbound-request traceparent injection
   over httpx. Mirrors the Rust ``TracingMiddleware``: a pure injection
   primitive (read the current :class:`SpanContext` (R129), write its W3C
   traceparent into a header mapping) plus an httpx
   ``event_hooks['request']`` handler gated on :func:`dispatcher_active`
   (R128). Only the wire-injection half lands — the Rust ``http_request``
   child-span lifecycle (new span_id, inherited trace_id,
   ``http.response.status_code`` record) needs the span-tree abstraction
   R129 defers, so the hook injects the *current* SpanContext rather than a
   freshly minted client span. The Rust ``lib.rs`` selectively re-exports
   from ``http_client`` (``attach_trace_to_http_request`` + the
   ``traced_client`` factories + the ``TracedHttpClient`` type, but **not**
   the ``TracingMiddleware`` struct itself); this landing re-exports
   :func:`attach_trace_to_http_request` (matching the Rust free-function
   re-export) and :func:`traceparent_request_hook` (the Python analogue of
   the factory use-surface — the httpx wiring point). The ``traced_client``
   / ``traced_client_new`` / ``traced_client_from_builder`` factories are
   deferred: MiniMax Code's httpx clients live in ``llm.py`` and adopting
   tracing there is a consumer-side decision, not this crate's job.

Crate completion
----------------

Six leaves total: five migrated (``timer`` R127, ``dispatch`` R128,
``fastrace`` R129, ``tokio`` R130, ``http_client`` R131) and two deferred
as YAGNI:

* ``grpc_client.rs`` — gRPC trace middleware (tonic + tower + tower_http +
  tracing_opentelemetry). MiniMax Code has no gRPC surface (its transport is
  HTTP + WebSocket), so the tonic ``TraceLayer`` /
  ``InjectTraceContextService`` stack has no Python consumer. The module's
  pure-logic core — ``attach_trace_to_grpc_request_mut(metadata)``
  injecting the W3C traceparent into gRPC metadata — is already covered by
  R131's :func:`attach_trace_to_http_request`, which takes any
  ``MutableMapping[str, str]`` (gRPC metadata is mapping-shaped); callers
  that ever adopt gRPC can reuse it directly rather than needing a
  gRPC-specific copy.
* ``testing.rs`` — OpenTelemetry SDK test helpers (``OtelTestEnv`` installs
  an in-memory OTel tracer provider + subscriber guard;
  ``otel_span_id_hex`` / ``otel_trace_id_hex`` read IDs off an OTel
  ``SpanContext``). Python's landing uses a flat :class:`SpanContext` over
  :mod:`contextvars` (R129) with no OTel SDK, so these helpers have no
  Python equivalent. ``parse_traceparent`` — a three-line
  ``str.split('-')`` — is consumed in-process by the R129 suite where it is
  needed, defined locally to keep each round's tests self-contained.

The crate's downstream consumer is ``xai-computer-hub-sdk`` (which depends
on ``xai-tracing``); the SDK landing will consume R127-R131 directly.
"""

from minimax_code.tracing.dispatch import dispatcher_active
from minimax_code.tracing.fastrace import (
    SpanContext,
    current_trace_id,
    enter_span_with_traceparent,
    local_or_random_span_ctx,
)
from minimax_code.tracing.http_client import (
    attach_trace_to_http_request,
    traceparent_request_hook,
)
from minimax_code.tracing.timer import Timer

__all__ = [
    "SpanContext",
    "Timer",
    "attach_trace_to_http_request",
    "current_trace_id",
    "dispatcher_active",
    "enter_span_with_traceparent",
    "local_or_random_span_ctx",
    "traceparent_request_hook",
]
