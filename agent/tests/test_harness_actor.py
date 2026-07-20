"""Tests for ``computer_hub_sdk.harness`` actor + bind type layer (R168, harness.rs leaf 4).

Covers the :class:`ToolHarness` actor handle + its ``Debug`` repr, the
:class:`ToolHarnessInner` 10-field inner state, and the bind state-machine
type layer (``BindFuture`` / ``PendingBind`` aliases, ``LazyBind`` /
``EagerBind`` dataclasses, ``DeferredBind`` union).

``build`` (Rust 459-542), ``ToolHarness`` impl methods (725-1775),
``ToolHarnessInner`` impl methods (650-707), ``spawn_pending_bind``
(579-594) and ``LazyBind::start`` (604-617) land in later leaves and are
not exercised here -- only the data layout + ``Debug`` surface is.

Fields whose Rust type is a live runtime object (``ConnectionBorrow``,
``HookRequestHandler``, ``SessionBindReport``, ``TraceContextProvider``,
``TypedExtensions``) are exercised with a ``_Marker`` sentinel: the
dataclass stores its argument verbatim with no runtime shape check, so
the storage contract is what matters. ``SessionId`` / ``LocalRegistry``
are exercised with their real constructors; ``BindFuture`` /
``PendingBind`` are exercised through their ``typing.get_origin`` shape.
"""

from __future__ import annotations

import asyncio
from typing import Any, get_origin

from minimax_code.computer_hub_sdk.harness import (
    BindFuture,
    DeferredBind,
    EagerBind,
    LazyBind,
    LocalRegistry,
    PendingBind,
    ToolHarness,
    ToolHarnessInner,
)
from minimax_code.tool_protocol.capabilities import ToolCapabilities
from minimax_code.tool_protocol.ids import SessionId, ToolId
from minimax_code.tool_runtime.tool import default_capabilities
from minimax_code.tool_types.types import ToolDescription


# ===========================================================================
# Stubs.
# ===========================================================================
class _Marker:
    """Arbitrary sentinel for fields whose runtime type is a live object
    (ConnectionBorrow / HookRequestHandler / SessionBindReport /
    TraceContextProvider / TypedExtensions). The dataclass stores its
    argument verbatim, so the storage contract is what matters."""


class _FakeTool:
    """Typed ``Tool`` stub; registering it bumps ``local_registry`` len
    for the repr test (mirrors ``register``'s erase-then-store path)."""

    def __init__(self, name: str) -> None:
        self._name = name

    def id(self) -> ToolId:
        return ToolId(self._name)

    def description(self, ctx: Any) -> ToolDescription:
        return ToolDescription.new(self._name, "typed")

    def capabilities(self) -> ToolCapabilities:
        return default_capabilities()

    def should_list(self, ctx: Any) -> bool:
        return True

    async def execute(self, ctx: Any, args: Any) -> Any:
        raise NotImplementedError("actor tests never execute")


# ===========================================================================
# Bind state-machine type layer (lines 568-625).
# ===========================================================================
def test_bind_future_alias_origin_is_asyncio_future():
    """BindFuture aliases asyncio.Future (BoxFuture collapse)."""
    assert get_origin(BindFuture) is asyncio.Future


def test_pending_bind_alias_origin_is_asyncio_future():
    """PendingBind aliases asyncio.Future (Shared<BindFuture> collapse)."""
    assert get_origin(PendingBind) is asyncio.Future


def test_lazy_bind_defaults_started_to_none():
    lazy = LazyBind(fut=None)
    assert lazy.fut is None
    assert lazy.started is None


def test_lazy_bind_stores_owned_future_and_started():
    fut = _Marker()
    started = _Marker()
    lazy = LazyBind(fut=fut, started=started)  # type: ignore[arg-type]
    assert lazy.fut is fut
    assert lazy.started is started


def test_eager_bind_stores_pending():
    pending = _Marker()
    eager = EagerBind(pending=pending)  # type: ignore[arg-type]
    assert eager.pending is pending


def test_deferred_bind_union_accepts_eager_and_lazy():
    """DeferredBind = EagerBind | LazyBind; both variants satisfy it."""
    eager: DeferredBind = EagerBind(pending=None)
    lazy: DeferredBind = LazyBind(fut=None)
    assert isinstance(eager, EagerBind)
    assert isinstance(lazy, LazyBind)


# ===========================================================================
# ToolHarnessInner -- required fields + defaults (lines 627-648).
# ===========================================================================
def test_tool_harness_inner_requires_session_and_default_extensions():
    """session + default_extensions are required; the rest default."""
    inner = ToolHarnessInner(
        session=SessionId("s-1"),
        default_extensions=_Marker(),  # type: ignore[arg-type]
    )
    assert inner.session == SessionId("s-1")
    assert isinstance(inner.default_extensions, _Marker)


def test_tool_harness_inner_defaults_match_rust_struct_defaults():
    """The 8 optional fields reproduce Rust defaults (None / empty / fresh)."""
    inner = ToolHarnessInner(
        session=SessionId("s-1"),
        default_extensions=_Marker(),  # type: ignore[arg-type]
    )
    assert isinstance(inner.local_registry, LocalRegistry)
    assert len(inner.local_registry) == 0
    assert inner.borrow is None
    assert inner.trace_context_provider is None
    assert inner.remote_tools == []
    assert inner.last_bind_report is None
    assert inner.discovery_handle is None
    assert inner.pending_bind is None
    assert inner.hook_request_handler is None


def test_tool_harness_inner_local_registry_default_is_per_instance():
    a = ToolHarnessInner(session=SessionId("a"), default_extensions=_Marker())  # type: ignore[arg-type]
    b = ToolHarnessInner(session=SessionId("b"), default_extensions=_Marker())  # type: ignore[arg-type]
    assert a.local_registry is not b.local_registry


def test_tool_harness_inner_remote_tools_default_is_new_list_per_instance():
    a = ToolHarnessInner(session=SessionId("a"), default_extensions=_Marker())  # type: ignore[arg-type]
    b = ToolHarnessInner(session=SessionId("b"), default_extensions=_Marker())  # type: ignore[arg-type]
    assert a.remote_tools is not b.remote_tools


def test_tool_harness_inner_accepts_all_fields():
    """Every field is settable (mirrors build() populating the struct)."""
    registry = LocalRegistry()
    borrow = _Marker()
    trace = _Marker()
    report = _Marker()
    hook = _Marker()
    eager = EagerBind(pending=None)
    inner = ToolHarnessInner(
        session=SessionId("s-1"),
        default_extensions=_Marker(),  # type: ignore[arg-type]
        local_registry=registry,
        borrow=borrow,  # type: ignore[arg-type]
        trace_context_provider=trace,  # type: ignore[arg-type]
        remote_tools=[],
        last_bind_report=report,  # type: ignore[arg-type]
        discovery_handle=None,
        pending_bind=eager,
        hook_request_handler=hook,  # type: ignore[arg-type]
    )
    assert inner.local_registry is registry
    assert inner.borrow is borrow
    assert inner.trace_context_provider is trace
    assert inner.last_bind_report is report
    assert inner.pending_bind is eager
    assert inner.hook_request_handler is hook


def test_tool_harness_inner_remote_tools_is_hot_swappable():
    """arc_swap::ArcSwap<Vec<ToolDescription>> -> plain attribute (assignment)."""
    inner = ToolHarnessInner(
        session=SessionId("s-1"),
        default_extensions=_Marker(),  # type: ignore[arg-type]
    )
    tools = [_Marker()]  # type: ignore[list-item]
    inner.remote_tools = tools
    assert inner.remote_tools is tools


# ===========================================================================
# ToolHarness -- actor handle (lines 564-566 + Clone 708-714 + Debug 716-723).
# ===========================================================================
def _make_inner(session: str = "s-1") -> ToolHarnessInner:
    return ToolHarnessInner(
        session=SessionId(session),
        default_extensions=_Marker(),  # type: ignore[arg-type]
    )


def test_tool_harness_stores_inner():
    inner = _make_inner()
    harness = ToolHarness(inner)
    assert harness.inner is inner


def test_tool_harness_assignment_shares_inner_reference():
    """Clone in Rust is an Arc bump; in Python assignment shares the ref."""
    harness = ToolHarness(_make_inner())
    clone = harness  # reference copy (Rust Arc::clone)
    assert clone.inner is harness.inner


def test_tool_harness_repr_carries_session_local_tool_count_and_ellipsis():
    harness = ToolHarness(_make_inner("s-7"))
    text = repr(harness)
    assert "ToolHarness" in text
    assert "s-7" in text
    assert "local_tool_count=0" in text
    assert "..." in text  # finish_non_exhaustive()


def test_tool_harness_repr_reflects_local_tool_count():
    """Registering a tool bumps local_tool_count in the repr."""
    inner = _make_inner()
    harness = ToolHarness(inner)
    inner.local_registry.register(_FakeTool("ns:alpha"))
    assert "local_tool_count=1" in repr(harness)


def test_tool_harness_has_slots_no_instance_dict():
    """__slots__ = ('_inner',) -> no per-instance __dict__."""
    harness = ToolHarness(_make_inner())
    assert not hasattr(harness, "__dict__")
