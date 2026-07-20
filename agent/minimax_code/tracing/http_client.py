"""Outbound HTTP request traceparent injection (R131).

Fusion of grok-build's ``xai-tracing/src/http_client.rs``. The Rust module
wraps a reqwest client in a ``TracingMiddleware`` that (a) opens an
``http_request`` child span when a trace dispatcher is active, (b) injects
that span's context as a W3C ``traceparent`` header on every outbound
request, and (c) records the response status on the span. This landing
keeps the injection half -- the part that puts trace context on the wire --
and defers the span lifecycle half, which needs the span-tree abstraction
R129 deliberately does not model.

attach_trace_to_http_request -- the pure injection primitive
------------------------------------------------------------

:func:`attach_trace_to_http_request` reads the active
:class:`~minimax_code.tracing.fastrace.SpanContext` (R129) and writes its
W3C traceparent into a header mapping. It is the direct counterpart of the
Rust free function of the same name, and like that function it is
unconditional: if a context is active it injects, otherwise it is a no-op
returning ``False``. The Rust version delegates to the OTel global text-map
propagator (which could carry ``tracestate`` / ``baggage`` alongside
``traceparent``); Python has only the W3C traceparent channel (R129), so the
injection is ``traceparent``-only -- a recorded downgrade, not a silent one.

traceparent_request_hook -- the httpx integration point
-------------------------------------------------------

:func:`traceparent_request_hook` is the httpx ``event_hooks['request']``
handler that wires :func:`attach_trace_to_http_request` onto an outbound
:class:`httpx.Request`. It is the Python counterpart of Rust's
``TracingMiddleware``: the Rust middleware opens an ``http_request`` child
span (new span_id, inherited parent trace_id) and injects *that* span's
context; Python has no child-span abstraction (R129 SpanContext is flat), so
the injected traceparent is the *current* SpanContext -- same trace_id, but
the span_id is the current context's rather than a freshly minted client
span. The :func:`~minimax_code.tracing.dispatch.dispatcher_active` gate
(R128) is preserved: when no trace subscriber is wired the hook injects
nothing, mirroring the Rust "no consumer -> no span, no log spam" rule.

Deferred (YAGNI)
----------------

* ``traced_client`` / ``traced_client_new`` / ``traced_client_from_builder``
  -- reqwest ``ClientWithMiddleware`` factories. Python's httpx equivalent
  would be a factory returning an :class:`httpx.AsyncClient` with the
  request hook pre-wired; MiniMax Code's httpx clients live in
  ``agent/minimax_code/agent/llm.py`` and are not migrated to tracing in
  this round (a consumer-side decision, not this crate's job).
* The ``http_request`` child-span lifecycle (open on send, record
  ``http.response.status_code`` on response) -- needs the span-tree
  abstraction R129 defers. Only the wire-injection half lands here.
"""

from __future__ import annotations

from collections.abc import MutableMapping

import httpx

from minimax_code.tracing.dispatch import dispatcher_active
from minimax_code.tracing.fastrace import current_trace_id

__all__ = ["attach_trace_to_http_request", "traceparent_request_hook"]

#: The W3C traceparent header name. httpx stores header names
#: case-insensitively, so this lower-cased form matches on read regardless
#: of how the upstream set it.
_TRACEPARENT_HEADER = "traceparent"


def attach_trace_to_http_request(headers: MutableMapping[str, str]) -> bool:
    """Inject the current SpanContext's traceparent into ``headers`` (R131).

    Mirrors ``xai-tracing::http_client::attach_trace_to_http_request``: read
    the active :class:`~minimax_code.tracing.fastrace.SpanContext` (R129) and
    write its W3C traceparent into the supplied header mapping. Like the
    Rust free function this is **unconditional** on dispatcher presence --
    the gate lives in :func:`traceparent_request_hook` (the middleware
    counterpart), mirroring how the Rust module gates inside
    ``TracingMiddleware::handle`` rather than inside ``attach``.

    The Rust version delegates to the OTel global text-map propagator and
    could carry ``tracestate`` / ``baggage`` alongside ``traceparent``;
    Python has only the W3C traceparent channel (R129), so the injection is
    ``traceparent``-only.

    :param headers: Any mutable string-keyed mapping (``dict``,
        :class:`httpx.Headers`, etc.). Mutated in place when a context is
        active.
    :return: ``True`` if a traceparent was written, ``False`` if no context
        is active (no mutation).
    """
    trace_id = current_trace_id()
    if trace_id is None:
        return False
    headers[_TRACEPARENT_HEADER] = trace_id
    return True


def traceparent_request_hook(request: httpx.Request) -> None:
    """httpx ``event_hooks['request']`` handler: inject traceparent (R131).

    The Python counterpart of Rust's ``TracingMiddleware``: gate on
    :func:`~minimax_code.tracing.dispatch.dispatcher_active` (R128) -- when
    no trace subscriber is wired, inject nothing (mirrors the Rust "no
    consumer -> no span, no log spam" rule) -- then delegate to
    :func:`attach_trace_to_http_request`.

    The Rust middleware opens an ``http_request`` *child* span (new span_id,
    inherited parent trace_id) and injects that span's context; Python has
    no child-span abstraction (R129 SpanContext is flat), so the injected
    traceparent is the *current* SpanContext -- same trace_id, span_id is
    the current context's rather than a freshly minted client span.

    :param request: The outbound :class:`httpx.Request`; its ``headers`` are
        mutated in place when injection occurs.
    """
    if not dispatcher_active():
        return
    attach_trace_to_http_request(request.headers)
