"""End-to-end pipe-through tests for reasoning_effort (R54).

R53 built the :class:`~minimax_code.agent.reasoning.ReasoningEffort` type layer
in isolation; R54 threads it through the LLM call path. These tests pin the
*plumbing* — that the ``reasoning_effort`` value a caller passes to
:meth:`MiniMaxClient.stream_chat` / :meth:`MiniMaxClient.chat` reaches the
active transport, is normalised once via :func:`coerce_effort`, and surfaces
unchanged on both ``transport.last_reasoning_effort`` and the public
``client.last_reasoning_effort``.

The contract deliberately under test here is **pipe-through, not wire
emission**: every transport accepts the kwarg but emits nothing on the wire
(the per-protocol effort contract is unsettled), so ``None`` (the default)
must leave the request byte-identical to the pre-R54 behaviour. The
:func:`coerce_effort` normaliser itself is unit-tested in ``test_reasoning``;
these tests prove it is *wired in*.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import anthropic
import pytest

from minimax_code.agent import reasoning as R
from minimax_code.agent.core import AgentConfig, AgentCore
from minimax_code.agent.llm import MiniMaxClient
from minimax_code.agent.tools import ToolRegistry
from minimax_code.agent.transports.anthropic_transport import AnthropicTransport
from minimax_code.agent.transports.mock_transport import MockTransport
from minimax_code.agent.transports.openai_transport import OpenAITransport

_MSGS = [{"role": "user", "content": "ping"}]


# ---------------------------------------------------------------------------
# Transport layer: MockTransport records the coerced effort (observation seam)
# ---------------------------------------------------------------------------


async def test_transport_mock_records_coerced_effort_from_max_alias():
    """A bare ``"max"`` alias normalises to ``Xhigh`` at the transport seam."""
    transport = MockTransport()
    _ = [c async for c in transport.stream_chat(_MSGS, model="m", reasoning_effort="max")]
    assert transport.last_reasoning_effort is R.ReasoningEffort.XHIGH


async def test_transport_mock_records_none_effort_by_default():
    """Omitting the kwarg leaves ``last_reasoning_effort`` ``None`` (no effort)."""
    transport = MockTransport()
    _ = [c async for c in transport.stream_chat(_MSGS, model="m")]
    assert transport.last_reasoning_effort is None


async def test_transport_mock_records_typed_enum_unchanged():
    """An already-typed :class:`ReasoningEffort` passes through unmodified."""
    transport = MockTransport()
    _ = [
        c async for c in transport.stream_chat(
            _MSGS, model="m", reasoning_effort=R.ReasoningEffort.LOW
        )
    ]
    assert transport.last_reasoning_effort is R.ReasoningEffort.LOW


async def test_transport_mock_unknown_string_degrades_to_none():
    """A typo degrades to ``None`` (send nothing) rather than raising."""
    transport = MockTransport()
    _ = [c async for c in transport.stream_chat(_MSGS, model="m", reasoning_effort="turbo")]
    assert transport.last_reasoning_effort is None


# ---------------------------------------------------------------------------
# Public surface: MiniMaxClient → transport end-to-end (pipe-through)
# ---------------------------------------------------------------------------


async def test_client_streams_string_effort_to_transport():
    """A bare string flows ``client → transport`` and is coerced + surfaced."""
    client = MiniMaxClient(mock=True)
    _ = [c async for c in client.stream_chat(_MSGS, model="m", reasoning_effort="high")]
    assert client.last_reasoning_effort is R.ReasoningEffort.HIGH


async def test_client_default_effort_is_none_zero_behaviour_change():
    """No ``reasoning_effort`` → ``client.last_reasoning_effort`` is ``None``.

    This is the zero-regression guarantee: the default leaves the call
    byte-identical to the pre-R54 path, so every existing consumer is
    unaffected unless it opts in.
    """
    client = MiniMaxClient(mock=True)
    _ = [c async for c in client.stream_chat(_MSGS, model="m")]
    assert client.last_reasoning_effort is None


async def test_client_streams_typed_enum_passes_through():
    """A typed enum is carried end-to-end without re-parsing."""
    client = MiniMaxClient(mock=True)
    _ = [
        c async for c in client.stream_chat(
            _MSGS, model="m", reasoning_effort=R.ReasoningEffort.MEDIUM
        )
    ]
    assert client.last_reasoning_effort is R.ReasoningEffort.MEDIUM


async def test_client_streams_max_alias_resolves_to_xhigh():
    """The ``"max"`` CLI alias resolves to ``Xhigh`` across the full pipe."""
    client = MiniMaxClient(mock=True)
    _ = [c async for c in client.stream_chat(_MSGS, model="m", reasoning_effort="max")]
    assert client.last_reasoning_effort is R.ReasoningEffort.XHIGH


# ---------------------------------------------------------------------------
# Non-streaming path: chat() must propagate effort too (single code path)
# ---------------------------------------------------------------------------


async def test_client_chat_path_propagates_effort():
    """The non-streaming :meth:`chat` path forwards effort to ``stream_chat``."""
    client = MiniMaxClient(mock=True)
    _ = await client.chat(_MSGS, model="m", reasoning_effort="xhigh")
    assert client.last_reasoning_effort is R.ReasoningEffort.XHIGH


async def test_client_chat_path_default_is_none():
    """The non-streaming path with no effort also yields ``None``."""
    client = MiniMaxClient(mock=True)
    _ = await client.chat(_MSGS, model="m")
    assert client.last_reasoning_effort is None


# ---------------------------------------------------------------------------
# Upstream half: AgentConfig.reasoning_effort → AgentCore → MiniMaxClient (R55)
# ---------------------------------------------------------------------------
#
# R54 closed the downstream half (client → transport: coerce + record). R55
# closes the upstream half (config → core → client call site) so the two
# configuration axes of an LLM call — ``model`` (R50) picks *who* answers,
# ``reasoning_effort`` picks *how deeply* it thinks — both flow from a single
# :class:`AgentConfig` into the stream. Together R53 + R54 + R55 make the full
# end-to-end reasoning-effort pipe (still pipe-through-only — no wire emission
# until the per-protocol effort contract settles).

_PING = [{"role": "user", "content": "ping"}]


async def test_core_streams_config_effort_to_client():
    """A bare string in ``AgentConfig.reasoning_effort`` reaches the client coerced."""
    client = MiniMaxClient(mock=True)
    core = AgentCore(
        llm=client,
        registry=ToolRegistry(),
        config=AgentConfig(reasoning_effort="high"),
    )
    await core._stream_turn(_PING)
    assert client.last_reasoning_effort is R.ReasoningEffort.HIGH


async def test_core_default_config_effort_is_none():
    """No ``reasoning_effort`` on the config ⇒ client sees ``None`` (zero regression).

    The AgentConfig default is ``None``, which the transport records as ``None``
    and emits nothing on the wire — every existing turn stays byte-identical to
    the pre-R55 path.
    """
    client = MiniMaxClient(mock=True)
    core = AgentCore(llm=client, registry=ToolRegistry(), config=AgentConfig())
    await core._stream_turn(_PING)
    assert client.last_reasoning_effort is None


async def test_core_streams_typed_enum_effort_unchanged():
    """A typed enum on the config passes through the core seam unmodified."""
    client = MiniMaxClient(mock=True)
    core = AgentCore(
        llm=client,
        registry=ToolRegistry(),
        config=AgentConfig(reasoning_effort=R.ReasoningEffort.LOW),
    )
    await core._stream_turn(_PING)
    assert client.last_reasoning_effort is R.ReasoningEffort.LOW


async def test_core_max_alias_resolves_to_xhigh():
    """The ``"max"`` CLI alias on the config resolves to ``Xhigh`` end-to-end."""
    client = MiniMaxClient(mock=True)
    core = AgentCore(
        llm=client,
        registry=ToolRegistry(),
        config=AgentConfig(reasoning_effort="max"),
    )
    await core._stream_turn(_PING)
    assert client.last_reasoning_effort is R.ReasoningEffort.XHIGH


# ---------------------------------------------------------------------------
# OpenAI transport wire emission (R56)
# ---------------------------------------------------------------------------
#
# R54/R55 carried reasoning_effort end-to-end as pipe-through (coerce + record,
# no wire signal). R56 closes the loop: ``OpenAITransport`` now *emits* the
# ``reasoning_effort`` field on the wire via the emit seam
# (:meth:`ReasoningEffort.to_openai_effort_token`). These tests inject a fake
# SDK client that captures the ``kwargs`` dict passed to
# ``chat.completions.create`` so we can assert exactly what reached the wire:
# the default (None) leaves kwargs untouched (zero regression), HIGH/MINIMAL
# inject their token, XHIGH degrades to "high", and the NONE variant is dropped
# (the OpenAI field has no ``none`` tier).


class _EmptyStream:
    """An async stream that yields nothing — these tests inspect kwargs, not chunks."""

    def __aiter__(self) -> _EmptyStream:
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _KwargsCapturingClient:
    """Minimal fake ``openai.AsyncOpenAI`` that records the create kwargs (R56).

    Modeled on ``_FakeClient`` in ``test_transport_breaker`` but specialised for
    wire-emission assertions: it captures the exact ``kwargs`` dict the transport
    builds so a test can assert whether ``reasoning_effort`` was injected (and
    with what token).
    """

    def __init__(self) -> None:
        self.captured_kwargs: dict[str, Any] | None = None
        outer = self

        class _Completions:
            async def create(self, **kwargs: Any) -> _EmptyStream:
                outer.captured_kwargs = dict(kwargs)
                return _EmptyStream()

        self.chat = SimpleNamespace(completions=_Completions())

    async def close(self) -> None:
        return None


@pytest.fixture
def openai_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[OpenAITransport, _KwargsCapturingClient]:
    """An ``OpenAITransport`` wired to a kwargs-capturing fake (breaker disabled).

    Returns ``(transport, fake)`` so a test can drive the stream then assert on
    ``fake.captured_kwargs`` — the exact kwargs that reached
    ``chat.completions.create``. The breaker registry is monkeypatched to ``None``
    (fail-open) so the circuit-breaker pre-check is a no-op.
    """
    import minimax_code.app as app

    monkeypatch.setattr(app, "ensure_breaker_registry", lambda: None)
    fake = _KwargsCapturingClient()
    transport = OpenAITransport(api_key="k", base_url="http://x", client=fake)
    return transport, fake


async def test_openai_transport_default_emits_no_reasoning_effort(openai_transport):
    """Zero regression: no effort ⇒ kwargs has no ``reasoning_effort`` key.

    The AgentConfig default (None) flows coerce → None → emit-seam None, so the
    kwargs dict is byte-identical to the pre-R56 path (no key added).
    """
    transport, fake = openai_transport
    _ = [c async for c in transport.stream_chat(_MSGS, model="m")]
    assert fake.captured_kwargs is not None
    assert "reasoning_effort" not in fake.captured_kwargs


async def test_openai_transport_high_emits_high_token(openai_transport):
    """HIGH → ``kwargs["reasoning_effort"] == "high"``."""
    transport, fake = openai_transport
    _ = [c async for c in transport.stream_chat(_MSGS, model="m", reasoning_effort="high")]
    assert fake.captured_kwargs["reasoning_effort"] == "high"


async def test_openai_transport_xhigh_degrades_to_high(openai_transport):
    """XHIGH (via the ``"max"`` alias) → ``"high"`` (degraded; OpenAI max tier)."""
    transport, fake = openai_transport
    _ = [c async for c in transport.stream_chat(_MSGS, model="m", reasoning_effort="max")]
    assert fake.captured_kwargs["reasoning_effort"] == "high"


async def test_openai_transport_minimal_emits_minimal(openai_transport):
    """MINIMAL → ``"minimal"`` (kept — OpenAI accepts it as the lowest tier)."""
    transport, fake = openai_transport
    _ = [c async for c in transport.stream_chat(_MSGS, model="m", reasoning_effort="minimal")]
    assert fake.captured_kwargs["reasoning_effort"] == "minimal"


async def test_openai_transport_none_variant_emits_nothing(openai_transport):
    """The NONE variant → no ``reasoning_effort`` key (OpenAI rejects ``"none"``)."""
    transport, fake = openai_transport
    _ = [c async for c in transport.stream_chat(_MSGS, model="m", reasoning_effort="none")]
    assert "reasoning_effort" not in fake.captured_kwargs


# ---------------------------------------------------------------------------
# Anthropic transport wire emission (R57)
# ---------------------------------------------------------------------------
#
# The Anthropic mirror of the R56 OpenAI block above. R57 emits reasoning_effort
# on the wire via the Anthropic-native ``output_config.effort`` field (the emit
# seam :meth:`ReasoningEffort.to_messages_api`), using a fake SDK client that
# captures the kwargs passed to ``client.messages.stream``. The crucial point
# these tests pin is *not* parity with OpenAI but the deliberate divergence of
# the two seams — Anthropic's ``OutputConfigParam.effort`` Literal is
# ``low``/``medium``/``high``/``xhigh``/``max`` (SDK-confirmed), so:
#
# * XHIGH surfaces as ``"max"`` (kept — Anthropic accepts ``max``), whereas the
#   OpenAI seam degrades XHIGH → ``"high"``.
# * MINIMAL is dropped (``NOT_GIVEN``), whereas the OpenAI seam keeps it as
#   ``"minimal"``.
#
# The default (None) leaves ``output_config`` at ``NOT_GIVEN`` — zero regression
# vs the pre-R57 request body.


class _AnthropicFakeStream:
    """An async context manager + async iterator that yields nothing (R57).

    ``client.messages.stream(...)`` returns an *async context manager* whose
    ``__aenter__`` yields the stream itself (which is then iterated). These
    tests inspect the captured call kwargs, not the streamed events, so the
    stream body is empty.
    """

    async def __aenter__(self) -> _AnthropicFakeStream:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    def __aiter__(self) -> _AnthropicFakeStream:
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _AnthropicKwargsCapturingClient:
    """Minimal fake ``anthropic.AsyncAnthropic`` that records stream kwargs (R57).

    Mirrors :class:`_KwargsCapturingClient` (OpenAI, R56) but specialised for
    the Anthropic ``messages.stream`` path: ``stream(**kwargs)`` returns an async
    context manager (not an awaitable), so the fake captures kwargs at call time
    and returns an empty :class:`_AnthropicFakeStream`.
    """

    def __init__(self) -> None:
        self.captured_kwargs: dict[str, Any] | None = None
        outer = self

        class _Messages:
            def stream(self, **kwargs: Any) -> _AnthropicFakeStream:
                outer.captured_kwargs = dict(kwargs)
                return _AnthropicFakeStream()

        self.messages = _Messages()

    async def close(self) -> None:
        return None


@pytest.fixture
def anthropic_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[AnthropicTransport, _AnthropicKwargsCapturingClient]:
    """An ``AnthropicTransport`` wired to a kwargs-capturing fake (breaker disabled).

    Returns ``(transport, fake)`` so a test can drive the stream then assert on
    ``fake.captured_kwargs`` — the exact kwargs that reached
    ``client.messages.stream``. The breaker registry is monkeypatched to ``None``
    (fail-open) so the circuit-breaker pre-check is a no-op (same shape as the
    R56 OpenAI fixture).
    """
    import minimax_code.app as app

    monkeypatch.setattr(app, "ensure_breaker_registry", lambda: None)
    fake = _AnthropicKwargsCapturingClient()
    transport = AnthropicTransport(api_key="k", base_url="http://x", client=fake)
    return transport, fake


async def test_anthropic_transport_default_emits_no_output_config(anthropic_transport):
    """Zero regression: no effort ⇒ ``output_config`` is ``NOT_GIVEN``.

    The AgentConfig default (None) flows coerce → None → emit-seam None, so the
    request body is byte-identical to the pre-R57 path (no ``output_config``
    injected).
    """
    transport, fake = anthropic_transport
    _ = [c async for c in transport.stream_chat(_MSGS, model="m")]
    assert fake.captured_kwargs is not None
    assert fake.captured_kwargs["output_config"] is anthropic.NOT_GIVEN


async def test_anthropic_transport_high_emits_high_effort(anthropic_transport):
    """HIGH → ``output_config == {"effort": "high"}`` (pass-through)."""
    transport, fake = anthropic_transport
    _ = [c async for c in transport.stream_chat(_MSGS, model="m", reasoning_effort="high")]
    assert fake.captured_kwargs["output_config"] == {"effort": "high"}


async def test_anthropic_transport_xhigh_emits_max_unlike_openai(anthropic_transport):
    """XHIGH (via the ``"max"`` alias) → ``"max"`` (kept; Anthropic accepts ``max``).

    This is the key divergence from the OpenAI seam (R56), where XHIGH degrades
    to ``"high"``: Anthropic's ``OutputConfigParam.effort`` Literal includes
    ``max`` (and ``xhigh``), so the deepest requested tier survives intact.
    """
    transport, fake = anthropic_transport
    _ = [c async for c in transport.stream_chat(_MSGS, model="m", reasoning_effort="max")]
    assert fake.captured_kwargs["output_config"] == {"effort": "max"}


async def test_anthropic_transport_minimal_omitted_unlike_openai(anthropic_transport):
    """MINIMAL → ``NOT_GIVEN`` (dropped; Anthropic has no ``minimal`` tier).

    The other key divergence from the OpenAI seam (R56), where MINIMAL is kept
    as ``"minimal"``: Anthropic's ``OutputConfigParam.effort`` Literal has no
    ``minimal`` value, so the emit seam returns ``None`` and the field is
    omitted rather than erroring.
    """
    transport, fake = anthropic_transport
    _ = [c async for c in transport.stream_chat(_MSGS, model="m", reasoning_effort="minimal")]
    assert fake.captured_kwargs["output_config"] is anthropic.NOT_GIVEN


async def test_anthropic_transport_none_variant_emits_nothing(anthropic_transport):
    """The NONE variant → ``NOT_GIVEN`` (Anthropic has no ``none`` tier)."""
    transport, fake = anthropic_transport
    _ = [c async for c in transport.stream_chat(_MSGS, model="m", reasoning_effort="none")]
    assert fake.captured_kwargs["output_config"] is anthropic.NOT_GIVEN
