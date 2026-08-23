"""Output-token budget regression tests (v1.2.1).

Bug: every long write_file/edit_file with Chinese content failed with
``tool 'X' got malformed JSON args`` because the anthropic transport
silently capped streamed output at ``max_tokens or 4096`` — a 300-line
Chinese document easily needs 5-8k tokens, so the streamed
``input_json_delta`` fragments were cut mid-string (Anthropic
``stop_reason=max_tokens`` → ``finish_reason="length"``) and the
re-assembled arguments JSON no longer parsed.

Fix under test (four seams):

1. ``AgentConfig.max_output_tokens`` + the
   ``MINIMAX_CODE_MAX_OUTPUT_TOKENS`` env knob (default 32 768, clamped
   to [1024, 131 072], garbage falls back).
2. ``AgentCore._stream_turn`` forwards ``max_tokens`` to the client.
3. The anthropic transport fallback rises 4096 → 32 768 (Anthropic
   requires the field, so a fallback must exist even for direct
   callers); explicit values still win.
4. A malformed-args tool failure tells the model how to recover
   (smaller payload / raise the env knob) instead of a bare parse error.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from minimax_code.agent.core import AgentConfig, AgentCore
from minimax_code.agent.llm import MiniMaxClient
from minimax_code.agent.tools import ToolRegistry

_MSGS = [{"role": "user", "content": "ping"}]


# ---------------------------------------------------------------------------
# 1. Env knob: _default_max_output_tokens / AgentConfig.max_output_tokens
# ---------------------------------------------------------------------------


def test_default_max_output_tokens_env_knob(monkeypatch: pytest.MonkeyPatch) -> None:
    """The output budget defaults to 32 768 and honours the env var
    (clamped to [1024, 131 072]; garbage falls back to the default)."""
    from minimax_code.agent.core import (
        _DEFAULT_MAX_OUTPUT_TOKENS,
        _default_max_output_tokens,
    )

    assert _DEFAULT_MAX_OUTPUT_TOKENS == 32_768

    monkeypatch.delenv("MINIMAX_CODE_MAX_OUTPUT_TOKENS", raising=False)
    assert _default_max_output_tokens() == 32_768
    assert AgentConfig().max_output_tokens == 32_768

    monkeypatch.setenv("MINIMAX_CODE_MAX_OUTPUT_TOKENS", "8192")
    assert _default_max_output_tokens() == 8192

    monkeypatch.setenv("MINIMAX_CODE_MAX_OUTPUT_TOKENS", "1")  # clamped up
    assert _default_max_output_tokens() == 1024

    monkeypatch.setenv("MINIMAX_CODE_MAX_OUTPUT_TOKENS", "99999999")  # clamped down
    assert _default_max_output_tokens() == 131_072

    monkeypatch.setenv("MINIMAX_CODE_MAX_OUTPUT_TOKENS", "lots")  # garbage → default
    assert _default_max_output_tokens() == 32_768


def test_config_accepts_explicit_max_output_tokens() -> None:
    """An explicit AgentConfig value bypasses the env knob entirely."""
    assert AgentConfig(max_output_tokens=2048).max_output_tokens == 2048


# ---------------------------------------------------------------------------
# 2. Core seam: AgentCore._stream_turn forwards max_tokens to the client
# ---------------------------------------------------------------------------


class _SpyClient(MiniMaxClient):
    """A mock-mode client that records the kwargs of each stream_chat call.

    The parent ``stream_chat`` is an async generator, so the override must
    also be one (``yield`` each chunk through) — returning an awaitable
    instead breaks ``_stream_turn``'s ``.__aiter__()`` at the call site.
    """

    def __init__(self) -> None:
        super().__init__(mock=True)
        self.captured_kwargs: dict[str, Any] = {}

    async def stream_chat(self, *args: Any, **kwargs: Any):  # type: ignore[override]
        self.captured_kwargs = dict(kwargs)
        async for chunk in super().stream_chat(*args, **kwargs):
            yield chunk


async def test_core_forwards_config_budget_to_stream_chat():
    """_stream_turn must send ``max_tokens=config.max_output_tokens``.

    This is the regression pin for the bug: before v1.2.1 the call omitted
    ``max_tokens`` entirely, so the anthropic transport fell back to its
    (old, 4096-token) default and long tool arguments were truncated.
    """
    spy = _SpyClient()
    core = AgentCore(
        llm=spy,
        registry=ToolRegistry(),
        config=AgentConfig(max_output_tokens=65_536),
    )
    await core._stream_turn(_MSGS)
    assert spy.captured_kwargs.get("max_tokens") == 65_536


async def test_core_default_budget_is_32k():
    """The AgentConfig default (32 768) reaches the client unmodified."""
    spy = _SpyClient()
    core = AgentCore(llm=spy, registry=ToolRegistry(), config=AgentConfig())
    await core._stream_turn(_MSGS)
    assert spy.captured_kwargs.get("max_tokens") == 32_768


# ---------------------------------------------------------------------------
# 3. Anthropic transport wire fallback: 4096 → 32 768
# ---------------------------------------------------------------------------


class _AnthropicFakeStream:
    """Empty async context-manager stream — these tests assert kwargs only."""

    async def __aenter__(self) -> _AnthropicFakeStream:
        return self

    async def __aexit__(self, *_: Any) -> bool:
        return False

    def __aiter__(self) -> _AnthropicFakeStream:
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _AnthropicKwargsCapturingClient:
    """Minimal fake ``anthropic.AsyncAnthropic`` recording stream kwargs."""

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
) -> tuple[Any, _AnthropicKwargsCapturingClient]:
    import minimax_code.app as app
    from minimax_code.agent.transports.anthropic_transport import AnthropicTransport

    monkeypatch.setattr(app, "ensure_breaker_registry", lambda: None)
    fake = _AnthropicKwargsCapturingClient()
    transport = AnthropicTransport(api_key="k", base_url="http://x", client=fake)
    return transport, fake


async def test_anthropic_fallback_budget_is_32k_not_4096(anthropic_transport):
    """The wire fallback is the regression core: omitted max_tokens must
    send 32 768, not the pre-v1.2.1 4096 that truncated long tool args."""
    transport, fake = anthropic_transport
    _ = [c async for c in transport.stream_chat(_MSGS, model="m")]
    assert fake.captured_kwargs is not None
    assert fake.captured_kwargs["max_tokens"] == 32_768


async def test_anthropic_explicit_budget_wins_over_fallback(anthropic_transport):
    """An explicit value (what the core now always sends) passes through."""
    transport, fake = anthropic_transport
    _ = [c async for c in transport.stream_chat(_MSGS, model="m", max_tokens=65_536)]
    assert fake.captured_kwargs["max_tokens"] == 65_536


# ---------------------------------------------------------------------------
# 4. OpenAI transport parity: explicit budgets emit, omitted stays unset
# ---------------------------------------------------------------------------


class _EmptyStream:
    def __aiter__(self) -> _EmptyStream:
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class _OpenAIKwargsCapturingClient:
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


async def test_openai_explicit_budget_emitted(monkeypatch: pytest.MonkeyPatch):
    """OpenAI transport forwards an explicit budget (existing behaviour,
    pinned so both transports honor the new core knob)."""
    import minimax_code.app as app
    from minimax_code.agent.transports.openai_transport import OpenAITransport

    monkeypatch.setattr(app, "ensure_breaker_registry", lambda: None)
    fake = _OpenAIKwargsCapturingClient()
    transport = OpenAITransport(api_key="k", base_url="http://x", client=fake)
    _ = [c async for c in transport.stream_chat(_MSGS, model="m", max_tokens=65_536)]
    assert fake.captured_kwargs is not None
    assert fake.captured_kwargs["max_tokens"] == 65_536


async def test_openai_omitted_budget_stays_unset(monkeypatch: pytest.MonkeyPatch):
    """Omitted budget → no ``max_tokens`` key (server default) — the OpenAI
    transport never had the truncation bug; lock the asymmetry in place."""
    import minimax_code.app as app
    from minimax_code.agent.transports.openai_transport import OpenAITransport

    monkeypatch.setattr(app, "ensure_breaker_registry", lambda: None)
    fake = _OpenAIKwargsCapturingClient()
    transport = OpenAITransport(api_key="k", base_url="http://x", client=fake)
    _ = [c async for c in transport.stream_chat(_MSGS, model="m")]
    assert "max_tokens" not in fake.captured_kwargs


# ---------------------------------------------------------------------------
# 5. Malformed-args error carries recovery guidance
# ---------------------------------------------------------------------------


async def test_malformed_args_error_mentions_truncation_recovery():
    """A truncated-JSON tool failure must point at the output-token limit
    and name the recovery paths, so the model can self-heal on the next
    iteration instead of repeating the oversized write."""
    core = AgentCore(
        llm=MiniMaxClient(mock=True),
        registry=ToolRegistry(),
        config=AgentConfig(),
    )
    # Cut mid-string inside Chinese content — exactly the shape a
    # stop_reason=max_tokens truncation leaves behind.
    truncated = '{"path": "/tmp/doc.md", "content": "这是一段很长的中文文档内'
    prepared = await core._prepare_tool_call(
        {"id": "tc1", "name": "write_file", "arguments": truncated}
    )
    assert prepared.short_circuit is not None
    assert prepared.short_circuit.success is False
    text = str(prepared.short_circuit.error)
    assert "malformed JSON" in text
    assert "truncated by the output-token limit" in text
    assert "MINIMAX_CODE_MAX_OUTPUT_TOKENS" in text
