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

from minimax_code.agent import reasoning as R
from minimax_code.agent.llm import MiniMaxClient
from minimax_code.agent.transports.mock_transport import MockTransport

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
