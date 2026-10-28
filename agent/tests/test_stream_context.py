"""Streaming deadlines preserve request budget context through generator cleanup."""
from __future__ import annotations

import asyncio

import pytest

from minimax_code.agent.core import AgentConfig, AgentCore
from minimax_code.agent.tools import ToolRegistry
from minimax_code.agent.types import LLMStreamTimeout, StreamChunk
from minimax_code.budget_ledger import BudgetHandle, budget_scope, current_handle


class ScopedStream:
    def __init__(self, handle, *, block=False):
        self.handle = handle
        self.block = block
        self.waiting = asyncio.Event()
        self.observed = []
        self.closed = []

    async def stream_chat(self, *args, **kwargs):
        with budget_scope(self.handle):
            try:
                self.observed.append((current_handle(), asyncio.current_task()))
                yield StreamChunk(delta="first")
                self.observed.append((current_handle(), asyncio.current_task()))
                if self.block:
                    self.waiting.set()
                    await asyncio.Event().wait()
                yield StreamChunk(delta="last", finish_reason="stop")
            finally:
                self.closed.append((current_handle(), asyncio.current_task()))


@pytest.mark.parametrize("cancel_at_chunk", [False, True])
async def test_stream_budget_context_survives_chunks_and_cooperative_close(cancel_at_chunk):
    parent = BudgetHandle(None, "family", "owner")
    request = parent.descendant("request")
    llm = ScopedStream(request)
    core = AgentCore(llm=llm, registry=ToolRegistry(), config=AgentConfig(stall_timeout=1))
    callbacks = []
    owner = asyncio.current_task()

    async def on_chunk(delta, done, metadata=None):
        callbacks.append((delta, current_handle(), asyncio.current_task()))
        if cancel_at_chunk:
            core.cancel()

    core.on_chunk = on_chunk
    before = current_handle()
    with budget_scope(parent):
        result = await core._stream_turn([])
        assert current_handle() is parent
    assert current_handle() is before
    assert result.message["content"] == ("first" if cancel_at_chunk else "firstlast")
    assert llm.observed == [(request, owner), (request, owner)]
    assert llm.closed == [(request, owner)]
    assert [delta for delta, _, _ in callbacks] == (["first"] if cancel_at_chunk else ["first", "last"])
    assert all(handle is request and task is owner for _, handle, task in callbacks)


async def test_stream_stall_closes_budget_scope_in_owner_context():
    parent = BudgetHandle(None, "family", "owner")
    request = parent.descendant("request")
    llm = ScopedStream(request, block=True)
    core = AgentCore(llm=llm, registry=ToolRegistry(), config=AgentConfig(stall_timeout=0.01))
    owner = asyncio.current_task()
    before = current_handle()
    with budget_scope(parent):
        with pytest.raises(LLMStreamTimeout):
            await core._stream_turn([])
        assert current_handle() is parent
    assert current_handle() is before
    assert owner.cancelling() == 0
    assert llm.observed == [(request, owner), (request, owner)]
    assert llm.closed == [(request, owner)]


async def test_stream_owner_cancellation_closes_budget_scope_before_propagating():
    parent = BudgetHandle(None, "family", "owner")
    request = parent.descendant("request")
    llm = ScopedStream(request, block=True)
    core = AgentCore(llm=llm, registry=ToolRegistry(), config=AgentConfig(stall_timeout=10))
    restored = []

    async def run():
        with budget_scope(parent):
            try:
                await core._stream_turn([])
            finally:
                restored.append(current_handle())

    before = current_handle()
    owner = asyncio.create_task(run())
    try:
        await asyncio.wait_for(llm.waiting.wait(), timeout=1)
    finally:
        owner.cancel()
        with pytest.raises(asyncio.CancelledError):
            await owner
    assert current_handle() is before
    assert restored == [parent]
    assert llm.observed == [(request, owner), (request, owner)]
    assert llm.closed == [(request, owner)]
