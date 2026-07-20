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
import collections.abc
from typing import Any, get_origin

import pytest

from minimax_code.computer_hub_sdk.harness import (
    BindFuture,
    DeferredBind,
    EagerBind,
    LazyBind,
    LocalRegistry,
    PendingBind,
    ToolHarness,
    ToolHarnessInner,
    spawn_pending_bind,
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
def test_bind_future_alias_origin_is_awaitable():
    """BindFuture aliases collections.abc.Awaitable (BoxFuture = any future)."""
    assert get_origin(BindFuture) is collections.abc.Awaitable


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


# ===========================================================================
# spawn_pending_bind -- behaviour layer (lines 579-594) -- R169.
# ===========================================================================
async def test_spawn_pending_bind_resolves_to_bind_result():
    """Ok branch: bind resolves to a ToolHarness -> pending awaits to it."""
    target = ToolHarness(_make_inner("bind-ok"))

    async def _bind() -> ToolHarness:
        return target

    pending = spawn_pending_bind(_bind())
    assert isinstance(pending, asyncio.Future)
    resolved = await pending
    assert resolved is target


async def test_spawn_pending_bind_propagates_bind_exception():
    """Err branch: bind raises -> the same exception surfaces on await
    (Rust JoinError panic projection -> set_exception)."""
    async def _bind() -> ToolHarness:
        raise RuntimeError("auth rejected")

    pending = spawn_pending_bind(_bind())
    with pytest.raises(RuntimeError, match="auth rejected"):
        await pending


async def test_spawn_pending_bind_is_shared_single_run():
    """Shared<BindFuture>: two awaiters observe one bind run (cached result)."""
    target = ToolHarness(_make_inner("shared"))
    runs = 0

    async def _bind() -> ToolHarness:
        nonlocal runs
        runs += 1
        return target

    pending = spawn_pending_bind(_bind())
    first = await pending
    second = await pending  # cached result, no re-run
    assert first is target
    assert second is target
    assert runs == 1  # bind ran exactly once


# ===========================================================================
# LazyBind::start -- behaviour layer (lines 604-617) -- R169.
# ===========================================================================
async def test_lazy_bind_start_spawns_and_records_handle():
    """First start(): take fut, spawn_pending_bind it, store handle in started."""
    target = ToolHarness(_make_inner("lazy-start"))

    async def _bind() -> ToolHarness:
        return target

    lazy = LazyBind(fut=_bind())
    assert lazy.started is None
    handle = lazy.start()
    # fut is taken (slot nulled -- Rust Mutex::take).
    assert lazy.fut is None
    # started records the spawned handle (Rust OnceLock get_or_init).
    assert lazy.started is handle
    assert isinstance(handle, asyncio.Future)
    resolved = await handle
    assert resolved is target


async def test_lazy_bind_start_is_idempotent_once_lock():
    """Second start() returns the same handle (OnceLock get_or_init)."""
    target = ToolHarness(_make_inner("lazy-once"))
    runs = 0

    async def _bind() -> ToolHarness:
        nonlocal runs
        runs += 1
        return target

    lazy = LazyBind(fut=_bind())
    first = lazy.start()
    second = lazy.start()
    assert first is second  # OnceLock: same handle returned
    await first
    assert runs == 1  # bind ran once despite two start() calls


def test_lazy_bind_start_after_take_panics():
    """fut already taken (None) -> assert "taken more than once" (Rust expect)."""
    lazy = LazyBind(fut=None)  # fut consumed elsewhere
    with pytest.raises(AssertionError, match="taken more than once"):
        lazy.start()


# ===========================================================================
# ToolHarness construction entries + has_pending_bind (lines 725-818) -- R170.
# ===========================================================================
def test_local_only_with_builds_local_harness_no_bind():
    """local_only_with: borrow=None, pending_bind=None, identity fields stored."""
    reg = LocalRegistry()
    sess = SessionId("s-lo")
    ext = _Marker()
    harness = ToolHarness.local_only_with(reg, sess, ext)  # type: ignore[arg-type]
    assert harness.inner.session == sess
    assert harness.inner.local_registry is reg
    assert harness.inner.default_extensions is ext
    assert harness.inner.borrow is None
    assert harness.inner.pending_bind is None
    assert harness.has_pending_bind() is False


def test_local_only_with_repr_carries_local_tool_count():
    """A tool registered before construction shows up in the repr."""
    reg = LocalRegistry()
    reg.register(_FakeTool("ns:alpha"))
    harness = ToolHarness.local_only_with(reg, SessionId("s-repr"), _Marker())  # type: ignore[arg-type]
    assert "local_tool_count=1" in repr(harness)


def test_local_only_with_instances_have_independent_inner():
    """Two local_only_with harnesses do not share inner state."""
    a = ToolHarness.local_only_with(LocalRegistry(), SessionId("a"), _Marker())  # type: ignore[arg-type]
    b = ToolHarness.local_only_with(LocalRegistry(), SessionId("b"), _Marker())  # type: ignore[arg-type]
    assert a.inner is not b.inner
    assert a.inner.local_registry is not b.inner.local_registry


async def test_local_with_pending_bind_stores_eager_and_spawns():
    """local_with_pending_bind wraps bind via spawn_pending_bind (Eager variant);
    the bind is already running, so awaiting resolves to the target harness."""
    target = ToolHarness(_make_inner("eager-target"))

    async def _bind() -> ToolHarness:
        return target

    reg = LocalRegistry()
    harness = ToolHarness.local_with_pending_bind(
        reg, SessionId("s-eager"), _Marker(), _bind()  # type: ignore[arg-type]
    )
    assert harness.has_pending_bind() is True
    pending_bind = harness.inner.pending_bind
    assert isinstance(pending_bind, EagerBind)
    assert isinstance(pending_bind.pending, asyncio.Future)
    # The eager bind is spawned at construction; awaiting resolves to target.
    resolved = await pending_bind.pending
    assert resolved is target


def test_local_with_lazy_bind_stores_lazy_unstarted():
    """local_with_lazy_bind stores a LazyBind with fut set + started=None; the
    bind is NOT spawned at construction (no running loop required)."""
    async def _bind() -> ToolHarness:
        return ToolHarness(_make_inner("lazy-target"))

    bind_coro = _bind()
    try:
        reg = LocalRegistry()
        harness = ToolHarness.local_with_lazy_bind(
            reg, SessionId("s-lazy"), _Marker(), bind_coro  # type: ignore[arg-type]
        )
        assert harness.has_pending_bind() is True
        pending_bind = harness.inner.pending_bind
        assert isinstance(pending_bind, LazyBind)
        assert pending_bind.fut is bind_coro  # stored verbatim, unspawned
        assert pending_bind.started is None  # not spawned yet
    finally:
        bind_coro.close()  # avoid "coroutine was never awaited" warning


async def test_has_pending_bind_three_state_matrix():
    """has_pending_bind: local_only_with=False, eager=True, lazy=True."""
    async def _bind() -> ToolHarness:
        return ToolHarness(_make_inner("matrix"))

    local = ToolHarness.local_only_with(LocalRegistry(), SessionId("s1"), _Marker())  # type: ignore[arg-type]
    eager = ToolHarness.local_with_pending_bind(
        LocalRegistry(), SessionId("s2"), _Marker(), _bind()  # type: ignore[arg-type]
    )
    lazy_bind_coro = _bind()
    try:
        lazy = ToolHarness.local_with_lazy_bind(
            LocalRegistry(), SessionId("s3"), _Marker(), lazy_bind_coro  # type: ignore[arg-type]
        )
        assert local.has_pending_bind() is False
        assert eager.has_pending_bind() is True
        assert lazy.has_pending_bind() is True
    finally:
        lazy_bind_coro.close()  # avoid "coroutine was never awaited" warning
