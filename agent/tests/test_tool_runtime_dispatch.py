"""Tests for the R113 tool_runtime dispatch module.

Covers the migration of ``xai-tool-runtime/src/dispatch.rs``. The Rust
source carries no inline ``#[test]`` block, so these are Python-style
semantic-equivalence checks:

- ``ToolDispatch`` is an ``abc.ABC``: cannot be instantiated directly; a
  subclass that implements only ``call_terminal`` (not ``call``) stays
  abstract; a subclass that implements ``call`` is concrete.
- ``call_terminal`` default drain:
  * stream with a Terminal success -> returns the TypedToolOutput.
  * stream with a Terminal error -> returns the ToolError verbatim (NOT
    rewritten to ``stream_no_terminal``).
  * stream with Progress frames then a Terminal -> skips every Progress,
    returns the Terminal payload.
  * stream that ends with NO Terminal -> ``ToolError.custom`` with code
    ``stream_no_terminal``.
- argument threading: ``call`` receives the ``tool_id`` / ``args`` / ``ctx``
  the caller passed to ``call_terminal``.
- the default ``call_terminal`` body can be overridden by a subclass
  (Rust trait default-impl override).
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from minimax_code.tool_protocol.ids import ToolCallId, ToolId
from minimax_code.tool_runtime.context import ToolCallContext
from minimax_code.tool_runtime.dispatch import ToolDispatch
from minimax_code.tool_runtime.error import ToolError, ToolErrorKind
from minimax_code.tool_runtime.tool import (
    ToolProgress,
    ToolStreamItem,
    TypedToolOutput,
)

# ---------------------------------------------------------------------------
# Stream factories — async generators yielding a pre-baked item sequence.
# A dispatcher's `call` returns one of these (an AsyncIterator), matching the
# Rust `async fn call() -> ToolStream` shape: `await call(...)` resolves to
# the stream, then `async for item in stream` pulls items.
# ---------------------------------------------------------------------------


async def _terminal_success(
    tool_id: ToolId, args: Any, ctx: ToolCallContext
) -> AsyncIterator[ToolStreamItem[Any]]:
    yield ToolStreamItem.Terminal(
        TypedToolOutput(tool_id=tool_id, value={"ok": True})
    )


async def _terminal_error(
    tool_id: ToolId, args: Any, ctx: ToolCallContext
) -> AsyncIterator[ToolStreamItem[Any]]:
    yield ToolStreamItem.Terminal(ToolError.execution(tool_id, "boom"))


async def _progress_then_terminal(
    tool_id: ToolId, args: Any, ctx: ToolCallContext
) -> AsyncIterator[ToolStreamItem[Any]]:
    yield ToolStreamItem.Progress(ToolProgress.Text("working..."))
    yield ToolStreamItem.Progress(ToolProgress.Text("still working..."))
    yield ToolStreamItem.Terminal(
        TypedToolOutput(tool_id=tool_id, value={"ok": True})
    )


async def _no_terminal(
    tool_id: ToolId, args: Any, ctx: ToolCallContext
) -> AsyncIterator[ToolStreamItem[Any]]:
    yield ToolStreamItem.Progress(ToolProgress.Text("..."))
    # ends without a Terminal — a protocol violation.


class _FakeDispatch(ToolDispatch):
    """Test dispatch whose ``call`` returns a pre-baked stream factory.

    Records every ``(tool_id, args, ctx)`` triple the default drain threaded
    into ``call`` so the argument-threading test can assert it.
    """

    def __init__(self, factory: Any) -> None:
        self._factory = factory
        self.seen: list[tuple[ToolId, Any, ToolCallContext]] = []

    async def call(
        self,
        tool_id: ToolId,
        args: Any,
        ctx: ToolCallContext,
    ) -> AsyncIterator[ToolStreamItem[Any]]:
        self.seen.append((tool_id, args, ctx))
        return self._factory(tool_id, args, ctx)


# ---------------------------------------------------------------------------
# Fixtures.
# ---------------------------------------------------------------------------


def _tid() -> ToolId:
    return ToolId("search__web")


def _ctx() -> ToolCallContext:
    return ToolCallContext(call_id=ToolCallId("call-1"))


# ---------------------------------------------------------------------------
# abc.ABC shape — Rust `trait ToolDispatch` object-safety + abstract surface.
# ---------------------------------------------------------------------------


def test_tool_dispatch_is_abstract_cannot_instantiate():
    # `call` is @abstractmethod -> the bare trait has no concrete form.
    with pytest.raises(TypeError):
        ToolDispatch()


def test_subclass_missing_call_stays_abstract():
    # Overriding only `call_terminal` does NOT satisfy the abstract `call`.
    class _OnlyTerminal(ToolDispatch):
        async def call_terminal(self, tool_id, args, ctx):
            return None

    with pytest.raises(TypeError):
        _OnlyTerminal()


def test_subclass_implementing_call_is_concrete():
    # _FakeDispatch implements `call`; it instantiates without error and
    # is recognized as a ToolDispatch.
    d = _FakeDispatch(_terminal_success)
    assert isinstance(d, ToolDispatch)


# ---------------------------------------------------------------------------
# call_terminal default drain — Terminal success / error propagation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_terminal_returns_terminal_success():
    d = _FakeDispatch(_terminal_success)
    result = await d.call_terminal(_tid(), {"q": "x"}, _ctx())
    assert isinstance(result, TypedToolOutput)
    assert result.value == {"ok": True}
    assert result.tool_id == _tid()


@pytest.mark.asyncio
async def test_call_terminal_propagates_terminal_error_verbatim():
    # A Terminal carrying a ToolError is returned as-is; it is NOT
    # rewritten to `stream_no_terminal` (the error survived the drain).
    d = _FakeDispatch(_terminal_error)
    result = await d.call_terminal(_tid(), {}, _ctx())
    assert isinstance(result, ToolError)
    assert result.kind is ToolErrorKind.EXECUTION
    # The drain short-circuited at the Terminal, not at stream end.
    assert "terminal" not in result.detail


# ---------------------------------------------------------------------------
# call_terminal default drain — Progress frames are skipped.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_terminal_skips_progress_frames():
    d = _FakeDispatch(_progress_then_terminal)
    result = await d.call_terminal(_tid(), {}, _ctx())
    # Two Progress frames were yielded before the Terminal; the drain
    # skipped both and returned the Terminal's payload.
    assert isinstance(result, TypedToolOutput)
    assert result.value == {"ok": True}


# ---------------------------------------------------------------------------
# call_terminal default drain — no Terminal is a protocol violation.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_terminal_no_terminal_yields_stream_no_terminal_error():
    d = _FakeDispatch(_no_terminal)
    result = await d.call_terminal(_tid(), {}, _ctx())
    assert isinstance(result, ToolError)
    assert result.kind is ToolErrorKind.CUSTOM
    # ToolError.custom(code, detail) stashes the code in details["code"].
    assert result.details == {"code": "stream_no_terminal"}
    assert "terminal" in result.detail


# ---------------------------------------------------------------------------
# Argument threading — call() receives what call_terminal() was handed.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_terminal_threads_args_into_call():
    d = _FakeDispatch(_terminal_success)
    tid = _tid()
    args = {"query": "rust", "limit": 5}
    ctx = _ctx()
    await d.call_terminal(tid, args, ctx)
    assert d.seen == [(tid, args, ctx)]


# ---------------------------------------------------------------------------
# Default override — Rust default impl can be overridden.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_terminal_default_can_be_overridden():
    class _Override(ToolDispatch):
        async def call(self, tool_id, args, ctx):
            # Unused: the override never drains the stream.
            yield ToolStreamItem.Progress(ToolProgress.Text("x"))

        async def call_terminal(self, tool_id, args, ctx):
            return "overridden"

    assert await _Override().call_terminal(_tid(), {}, _ctx()) == "overridden"
