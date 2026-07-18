"""Tests for transport ↔ circuit-breaker integration (R18).

Two layers:

1. ``transports/_breaker.py`` helpers (``resolve_breaker`` /
   ``check_or_raise`` / ``record_outcome``) — unit-tested directly with real
   :class:`CircuitBreaker` instances and fail-open stubs.
2. ``OpenAITransport.stream_chat`` — exercised end-to-end with a fake SDK
   client to prove the transport calls the helpers at the right moments
   (pre-check before the SDK call, record after). The Anthropic transport is
   a mirror of the OpenAI one; its integration is covered by symmetry and the
   helper unit tests, so it is not duplicated here.
"""

from __future__ import annotations

from types import SimpleNamespace

import openai
import pytest

from minimax_code.agent.transports._breaker import (
    check_or_raise,
    record_outcome,
    resolve_breaker,
)
from minimax_code.agent.transports.openai_transport import OpenAITransport
from minimax_code.agent.types import LLMError
from minimax_code.resilience import (
    BreakerConfig,
    CircuitBreaker,
    CircuitBreakerRegistry,
    Outcome,
)

# ---------------------------------------------------------------------------
# resolve_breaker
# ---------------------------------------------------------------------------


def test_resolve_breaker_none_when_registry_unavailable(monkeypatch):
    import minimax_code.app as app

    monkeypatch.setattr(app, "ensure_breaker_registry", lambda: None)
    assert resolve_breaker("llm:openai") is None


def test_resolve_breaker_returns_breaker_for_key(monkeypatch):
    import minimax_code.app as app

    reg = CircuitBreakerRegistry(BreakerConfig.server())
    monkeypatch.setattr(app, "ensure_breaker_registry", lambda: reg)
    br = resolve_breaker("llm:openai")
    assert br is reg.get("llm:openai")


def test_resolve_breaker_failopen_on_registry_fault(monkeypatch):
    import minimax_code.app as app

    def boom():
        raise RuntimeError("registry dead")

    monkeypatch.setattr(app, "ensure_breaker_registry", boom)
    assert resolve_breaker("x") is None  # fail-open, must not raise


# ---------------------------------------------------------------------------
# check_or_raise
# ---------------------------------------------------------------------------


def test_check_or_raise_noop_on_none():
    check_or_raise(None)  # must not raise


def test_check_or_raise_translates_open_to_503():
    cfg = BreakerConfig(
        min_samples=2, error_rate_threshold=0.5, failure_codes=frozenset({500})
    )
    br = CircuitBreaker("t", cfg)
    br.record(Outcome.FAILURE)
    br.record(Outcome.FAILURE)  # trips to OPEN
    assert br.is_open
    with pytest.raises(LLMError) as ei:
        check_or_raise(br)
    assert ei.value.status_code == 503
    assert ei.value.breaker_open is True  # R19: so with_retry fast-fails
    assert "open" in str(ei.value).lower()


def test_check_or_raise_failopen_on_internal_fault():
    class BadBreaker:
        name = "bad"

        def check(self):
            raise RuntimeError("check broke")

    check_or_raise(BadBreaker())  # swallowed, must not raise


# ---------------------------------------------------------------------------
# record_outcome
# ---------------------------------------------------------------------------


def test_record_outcome_noop_on_none():
    record_outcome(None, success=True)
    record_outcome(None, success=False, status_code=500)


def test_record_outcome_success_records_success():
    br = CircuitBreaker("t", BreakerConfig.server())
    record_outcome(br, success=True)
    assert br.sample_count() == 1
    assert br.error_rate() == 0.0


def test_record_outcome_failure_with_failure_status_records():
    br = CircuitBreaker("t", BreakerConfig.server())  # 500 ∈ failure_codes
    record_outcome(br, success=False, status_code=500)
    assert br.sample_count() == 1
    assert br.error_rate() == 1.0


def test_record_outcome_skips_client_side_status():
    br = CircuitBreaker("t", BreakerConfig.server())  # 400 ∉ failure_codes
    record_outcome(br, success=False, status_code=400)
    assert br.sample_count() == 0  # neutral — not a health sample


def test_record_outcome_unknown_status_records_failure():
    br = CircuitBreaker("t", BreakerConfig.server())
    record_outcome(br, success=False, status_code=None)
    assert br.sample_count() == 1
    assert br.error_rate() == 1.0


def test_record_outcome_failopen_on_internal_fault():
    class BadBreaker:
        name = "bad"
        config = SimpleNamespace(is_failure_status=lambda s: True)

        def record(self, outcome):
            raise RuntimeError("record broke")

    record_outcome(BadBreaker(), success=False, status_code=500)  # swallowed


# ---------------------------------------------------------------------------
# OpenAITransport integration (fake SDK client)
# ---------------------------------------------------------------------------


class _FakeAPIError(openai.APIError):
    """An ``openai.APIError`` we can attach a status_code to without
    constructing a full httpx response."""

    def __init__(self, status_code: int) -> None:
        Exception.__init__(self, f"fake http {status_code}")
        self.status_code = status_code


class _FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


class _FakeCompletions:
    def __init__(self, chunks, error):
        self._chunks = chunks
        self._error = error

    async def create(self, **kwargs):
        if self._error:
            raise self._error
        return _FakeStream(self._chunks)


class _FakeChat:
    def __init__(self, completions):
        self.completions = completions


class _FakeClient:
    def __init__(self, chunks=None, error=None):
        self.chat = _FakeChat(_FakeCompletions(chunks or [], error))

    async def close(self):
        return None


def _content_chunk(text="hi"):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                delta=SimpleNamespace(content=text, tool_calls=None),
                finish_reason=None,
            )
        ],
        usage=None,
    )


@pytest.mark.asyncio
async def test_transport_success_records_success(monkeypatch):
    import minimax_code.app as app

    reg = CircuitBreakerRegistry(BreakerConfig.server())
    monkeypatch.setattr(app, "ensure_breaker_registry", lambda: reg)
    transport = OpenAITransport(
        api_key="k",
        base_url="http://x",
        client=_FakeClient(chunks=[_content_chunk("hello")]),
    )
    out = [c async for c in transport.stream_chat([], model="m")]
    assert out  # stream produced data
    br = reg.get("llm:openai")
    assert br.sample_count() == 1
    assert br.error_rate() == 0.0


@pytest.mark.asyncio
async def test_transport_open_breaker_returns_503(monkeypatch):
    import minimax_code.app as app

    cfg = BreakerConfig(
        min_samples=2, error_rate_threshold=0.5, failure_codes=frozenset({500})
    )
    reg = CircuitBreakerRegistry(cfg)
    br = reg.get("llm:openai")
    br.record(Outcome.FAILURE)
    br.record(Outcome.FAILURE)  # trips to OPEN
    assert br.is_open
    monkeypatch.setattr(app, "ensure_breaker_registry", lambda: reg)
    transport = OpenAITransport(
        api_key="k", base_url="http://x", client=_FakeClient(chunks=[_content_chunk("x")])
    )
    with pytest.raises(LLMError) as ei:
        async for _ in transport.stream_chat([], model="m"):
            pass
    assert ei.value.status_code == 503
    assert ei.value.breaker_open is True  # R19: with_retry must not retry this


@pytest.mark.asyncio
async def test_transport_api_error_invokes_record_outcome(monkeypatch):
    import minimax_code.agent.transports.openai_transport as ot
    import minimax_code.app as app

    # registry None -> breaker None; we spy on record_outcome directly to
    # prove the transport invokes it with the upstream status on API errors.
    monkeypatch.setattr(app, "ensure_breaker_registry", lambda: None)
    calls: list[tuple] = []
    monkeypatch.setattr(
        ot,
        "record_outcome",
        lambda b, *, success, status_code=None: calls.append((success, status_code)),
    )
    transport = OpenAITransport(
        api_key="k", base_url="http://x", client=_FakeClient(error=_FakeAPIError(500))
    )
    with pytest.raises(LLMError) as ei:
        async for _ in transport.stream_chat([], model="m"):
            pass
    assert ei.value.status_code == 500
    assert calls == [(False, 500)]


@pytest.mark.asyncio
async def test_transport_unprotected_when_registry_none(monkeypatch):
    """Fail-open: no registry => the stream runs normally; the missing
    breaker wiring never trips the business path."""
    import minimax_code.app as app

    monkeypatch.setattr(app, "ensure_breaker_registry", lambda: None)
    transport = OpenAITransport(
        api_key="k", base_url="http://x", client=_FakeClient(chunks=[_content_chunk("ok")])
    )
    out = [c async for c in transport.stream_chat([], model="m")]
    assert out
