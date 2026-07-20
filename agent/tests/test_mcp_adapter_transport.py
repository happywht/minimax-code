"""Contract tests for ``mcp_adapter.transport`` (R180).

R180 ports grok-build's ``xai-computer-hub-mcp-adapter/src/transport.rs``
(39 lines) -- the crate's 2nd leaf. These tests pin three invariants:

1. **Abstract boundary** -- :class:`McpTransport` is an ``abc.ABC`` that
   cannot be instantiated directly and rejects any concrete subclass that
   leaves one of the four lifecycle methods unimplemented.
2. **Coroutine contract** -- every method on the trait is a coroutine
   (``inspect.iscoroutinefunction``), so callers must ``await``.
3. **Return / raise contract** -- ``initialize`` returns
   :class:`McpServerInfo`, ``list_tools`` returns ``list[McpToolDefinition]``,
   ``call_tool`` returns :class:`McpCallResult`, ``close`` returns ``None``,
   and failures propagate as :class:`McpError` subclasses (the Python idiom
   for the Rust ``Result<_, McpError>`` envelope).
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from minimax_code.mcp_adapter import (
    McpCallResult,
    McpError,
    McpServerInfo,
    McpTimeoutError,
    McpToolDefinition,
    McpTransport,
    McpTransportError,
)


class _InMemoryTransport(McpTransport):
    """Full mock impl -- returns canned values; ``close`` is idempotent."""

    def __init__(self, *, fail: bool = False) -> None:
        self._fail = fail
        self.close_call_count = 0

    async def initialize(self) -> McpServerInfo:
        if self._fail:
            raise McpTransportError("offline")
        return McpServerInfo(name="mock", version="0.1.0")

    async def list_tools(self) -> list[McpToolDefinition]:
        if self._fail:
            raise McpTransportError("offline")
        return [McpToolDefinition(name="ping")]

    async def call_tool(self, name: str, arguments: Any) -> McpCallResult:
        if self._fail:
            raise McpTransportError("offline")
        # arguments is opaque JSON; we ignore it but the signature must accept it.
        del name, arguments
        return McpCallResult()

    async def close(self) -> None:
        self.close_call_count += 1
        # Idempotent: a second call after success must not raise.


class _PartialTransport(McpTransport):
    """Leaves ``close`` unimplemented -> must be rejected by abc."""

    async def initialize(self) -> McpServerInfo:
        return McpServerInfo(name="x", version="0")

    async def list_tools(self) -> list[McpToolDefinition]:
        return []

    async def call_tool(self, name: str, arguments: Any) -> McpCallResult:
        del name, arguments
        return McpCallResult()


# ---------------------------------------------------------------------------
# Abstract boundary
# ---------------------------------------------------------------------------


def test_transport_is_an_abstract_base_class() -> None:
    """``#[async_trait] pub trait`` -> ``abc.ABC`` with 4 abstract methods."""
    assert inspect.isabstract(McpTransport)
    assert McpTransport.__abstractmethods__ == frozenset(
        {"initialize", "list_tools", "call_tool", "close"}
    )


def test_transport_cannot_be_instantiated_directly() -> None:
    with pytest.raises(TypeError):
        McpTransport()  # type: ignore[abstract]


def test_partial_impl_missing_one_method_is_rejected() -> None:
    """A subclass leaving any of the four methods abstract is uninstantiable."""
    assert _PartialTransport.__abstractmethods__ == frozenset({"close"})
    with pytest.raises(TypeError):
        _PartialTransport()  # type: ignore[abstract]


def test_full_impl_has_no_remaining_abstract_methods() -> None:
    assert _InMemoryTransport.__abstractmethods__ == frozenset()
    # Instantiation succeeds and yields a trait instance.
    assert isinstance(_InMemoryTransport(), McpTransport)


# ---------------------------------------------------------------------------
# Coroutine contract
# ---------------------------------------------------------------------------


def test_every_method_is_a_coroutine_function() -> None:
    """``async_trait`` -> every method is ``async def`` (must be awaited)."""
    for name in ("initialize", "list_tools", "call_tool", "close"):
        method = getattr(McpTransport, name)
        assert inspect.iscoroutinefunction(method), f"{name} must be async"


# ---------------------------------------------------------------------------
# Return / raise contract
# ---------------------------------------------------------------------------


async def test_initialize_returns_server_info() -> None:
    info = await _InMemoryTransport().initialize()
    assert isinstance(info, McpServerInfo)
    assert info.name == "mock"


async def test_list_tools_returns_tool_definition_list() -> None:
    tools = await _InMemoryTransport().list_tools()
    assert isinstance(tools, list)
    assert tools and isinstance(tools[0], McpToolDefinition)


async def test_call_tool_returns_call_result() -> None:
    result = await _InMemoryTransport().call_tool("ping", {"q": 1})
    assert isinstance(result, McpCallResult)


async def test_close_returns_none_and_is_idempotent() -> None:
    transport = _InMemoryTransport()
    assert await transport.close() is None
    assert await transport.close() is None  # idempotent, no raise
    assert transport.close_call_count == 2


async def test_call_tool_accepts_arbitrary_json_arguments() -> None:
    """``arguments`` is ``serde_json::Value`` -> ``Any``; any JSON shape passes."""
    transport = _InMemoryTransport()
    await transport.call_tool("t", {"a": [1, 2], "b": None})
    await transport.call_tool("t", [1, 2, 3])
    await transport.call_tool("t", "raw")


async def test_failure_propagates_as_mcp_error_base() -> None:
    """Rust ``Result<_, McpError>`` -> Python ``raise McpError`` subclass."""
    transport = _InMemoryTransport(fail=True)
    with pytest.raises(McpError):
        await transport.initialize()
    with pytest.raises(McpTransportError):
        await transport.list_tools()


async def test_each_failure_variant_is_catchable_via_base() -> None:
    """Any ``McpError`` subclass raised by a transport is caught by the base."""

    class _TimeoutTransport(_InMemoryTransport):
        async def list_tools(self) -> list[McpToolDefinition]:
            raise McpTimeoutError("60s")

    raised: list[McpError] = []
    try:
        await _TimeoutTransport().list_tools()
    except McpError as err:
        raised.append(err)
    assert raised and isinstance(raised[0], McpTimeoutError)
