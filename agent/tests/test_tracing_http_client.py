"""Tests for ``minimax_code.tracing.http_client`` (R131).

Mirrors grok-build's ``xai-tracing/src/http_client.rs``. The Rust module has
two surfaces: the ``attach_trace_to_http_request`` free function (pure
header injection) and the ``TracingMiddleware`` reqwest middleware
(dispatcher-gated, opens an ``http_request`` child span, injects, records
status). This landing keeps the injection half --
:func:`attach_trace_to_http_request` and its httpx event-hook wiring
:func:`traceparent_request_hook` -- so the suite concentrates on:

* attach returns False / leaves headers untouched when no context is active,
  and writes the exact current traceparent when one is;
* attach is unconditional on dispatcher presence (the gate lives in the
  hook, mirroring how Rust gates inside ``TracingMiddleware::handle`` rather
  than inside ``attach``);
* the hook injects only when a subscriber is active AND a context is set;
* the hook drops cleanly into ``httpx`` ``event_hooks['request']`` and fires
  on a real (transport-stubbed) client send.

Context isolation reuses the ``clean_span_context`` pattern from
``test_tracing_fastrace`` / ``test_tracing_tokio``.
"""

from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest

from minimax_code.tracing.fastrace import (
    current_trace_id,
    enter_span_with_traceparent,
)
from minimax_code.tracing.http_client import (
    attach_trace_to_http_request,
    traceparent_request_hook,
)

#: A well-formed W3C traceparent for injection assertions.
_VALID_TRACEPARENT = "00-aabbccddeeff00112233445566778899-0011223344556677-01"


@pytest.fixture
def clean_span_context() -> Iterator[None]:
    """Force the active SpanContext to None for the test body (R131).

    Same isolation pattern as ``test_tracing_fastrace`` /
    ``test_tracing_tokio``: ``set(None)`` returns a token that restores the
    prior value on teardown, so a leak cannot flip a later "no context
    active" assertion.
    """
    from minimax_code.tracing.fastrace import _current_span_context

    token = _current_span_context.set(None)
    try:
        yield
    finally:
        _current_span_context.reset(token)


def _force_dispatcher(active: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """Override the http_client module's ``dispatcher_active`` reference."""
    monkeypatch.setattr(
        "minimax_code.tracing.http_client.dispatcher_active",
        lambda: active,
    )


# ---------------------------------------------------------------------------
# attach_trace_to_http_request -- pure injection primitive
# ---------------------------------------------------------------------------


def test_attach_returns_false_when_no_context(clean_span_context: None) -> None:
    headers: dict[str, str] = {}
    assert attach_trace_to_http_request(headers) is False
    assert headers == {}


def test_attach_writes_traceparent_when_context_set(
    clean_span_context: None,
) -> None:
    headers: dict[str, str] = {}
    with enter_span_with_traceparent("test", _VALID_TRACEPARENT):
        assert attach_trace_to_http_request(headers) is True
    assert headers["traceparent"] == _VALID_TRACEPARENT


def test_attach_value_matches_current_trace_id(
    clean_span_context: None,
) -> None:
    headers: dict[str, str] = {}
    with enter_span_with_traceparent("test", _VALID_TRACEPARENT):
        attach_trace_to_http_request(headers)
        assert headers["traceparent"] == current_trace_id()


def test_attach_is_unconditional_on_dispatcher(
    clean_span_context: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # attach mirrors the Rust free function: it does NOT gate on dispatcher
    # presence. The gate lives in the hook (TracingMiddleware counterpart).
    # Even with dispatcher_active forced False, a set context still injects.
    _force_dispatcher(False, monkeypatch)
    headers: dict[str, str] = {}
    with enter_span_with_traceparent("test", _VALID_TRACEPARENT):
        assert attach_trace_to_http_request(headers) is True
    assert headers["traceparent"] == _VALID_TRACEPARENT


# ---------------------------------------------------------------------------
# traceparent_request_hook -- httpx integration, dispatcher-gated
# ---------------------------------------------------------------------------


def _make_request() -> httpx.Request:
    return httpx.Request("GET", "http://example.test/health")


def test_hook_injects_when_dispatcher_active_and_context_set(
    clean_span_context: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_dispatcher(True, monkeypatch)
    request = _make_request()
    with enter_span_with_traceparent("test", _VALID_TRACEPARENT):
        traceparent_request_hook(request)
    assert request.headers.get("traceparent") == _VALID_TRACEPARENT


def test_hook_skips_when_dispatcher_inactive(
    clean_span_context: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # mirrors Rust TracingMiddleware's gate: no subscriber -> no injection,
    # even with a context active.
    _force_dispatcher(False, monkeypatch)
    request = _make_request()
    with enter_span_with_traceparent("test", _VALID_TRACEPARENT):
        traceparent_request_hook(request)
    assert "traceparent" not in request.headers


def test_hook_skips_when_no_context(
    clean_span_context: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _force_dispatcher(True, monkeypatch)
    request = _make_request()
    traceparent_request_hook(request)
    assert "traceparent" not in request.headers


def test_hook_wires_into_httpx_event_hooks(
    clean_span_context: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # the hook is shaped to drop into httpx event_hooks['request']; verify
    # it fires when a real (transport-stubbed) client sends a request.
    _force_dispatcher(True, monkeypatch)
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    with enter_span_with_traceparent("test", _VALID_TRACEPARENT):
        with httpx.Client(
            transport=transport,
            event_hooks={"request": [traceparent_request_hook]},
        ) as client:
            client.get("http://example.test/health")
    assert len(captured) == 1
    assert captured[0].headers.get("traceparent") == _VALID_TRACEPARENT
