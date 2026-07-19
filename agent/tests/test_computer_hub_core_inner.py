"""Tests for the R118 computer_hub_core inner module.

Covers the migration of ``xai-computer-hub-core/src/inner.rs``. The Rust
source is a single struct (``InnerDispatchForResolver``) implementing the
``ToolDispatch`` trait with a two-arm ``call``: live-resolver delegation
through ``resolve_and_dispatch``, and a dead-weak-ref short-circuit to
``terminal_only(ToolError::custom("computer_hub_dropped"))``.

These Python tests mirror that plus the trait-shape / weak-ref semantics:

- ``InnerDispatchForResolver`` subclasses R113's :class:`ToolDispatch`
  ABC and implements its one abstract ``call`` -> the class is concrete
  (empty ``__abstractmethods__``) and instantiable.
- The ``resolver`` field is a :func:`weakref.ref`: constructing a
  dispatch does NOT keep the resolver alive — once the only strong ref
  drops, ``self.resolver()`` returns ``None`` (the Python counterpart to
  Rust's ``Weak::upgrade() -> None`` after the referent is dropped).
- ``call`` is a coroutine function (mapping 1, ``async def ->
  AsyncIterator``); the live arm ``return``-s the resolver's stream
  verbatim and threads ``(self.session_id, tool_id, args, ctx)`` into
  ``resolve_and_dispatch``.
- The dead arm short-circuits to a single Terminal ``ToolError.custom``
  keyed ``computer_hub_dropped`` and never invokes the resolver.
- ``eq=False`` (Rust ``Debug``-only derive) -> identity-only equality.
- ``call_terminal`` is inherited unchanged from :class:`ToolDispatch`
  (the inner adapter does not override the default drain).
"""

from __future__ import annotations

import gc
import inspect
import weakref

import pytest

from minimax_code.computer_hub_core.inner import InnerDispatchForResolver
from minimax_code.tool_protocol import SessionId, ToolId
from minimax_code.tool_runtime import ToolError
from minimax_code.tool_runtime.dispatch import ToolDispatch

# ---------------------------------------------------------------------------
# Sentinels / fixtures.
# ---------------------------------------------------------------------------

# ToolCallContext is a duck-typed seam for the adapter (it forwards ctx
# opaquely into resolve_and_dispatch); a bare sentinel exercises the
# threading without needing a real context. args is likewise opaque.
_CTX = object()
_ARGS = {"q": "hello"}
# The fake resolver returns this opaque sentinel as its "stream"; the
# adapter must forward it verbatim (it does not drive the stream).
_STREAM = object()


def _tid() -> ToolId:
    return ToolId("search__web")


def _session() -> SessionId:
    return SessionId("session-3")


class _FakeResolver:
    """Minimal duck-typed CompoundResolver for inner-dispatch tests.

    Real :class:`~minimax_code.computer_hub_core.resolver.CompoundResolver`
    (R117) needs a live :class:`~minimax_code.computer_hub_core.ToolRegistry`;
    for trait-shape + forwarding tests a bare duck type exposing the single
    dispatch method the adapter calls is enough. Carries no ``__slots__`` so
    it is weakly referenceable, mirroring the real CompoundResolver.
    """

    def __init__(self) -> None:
        self.dispatch_calls: list = []

    async def resolve_and_dispatch(self, session_id, tool_id, args, ctx):
        self.dispatch_calls.append((session_id, tool_id, args, ctx))
        return _STREAM


# ---------------------------------------------------------------------------
# Construction + field access.
# ---------------------------------------------------------------------------


def test_construct_with_weakref_ref():
    r = _FakeResolver()
    d = InnerDispatchForResolver(weakref.ref(r), _session())
    assert isinstance(d.resolver, weakref.ref)
    assert d.session_id == _session()


def test_is_tool_dispatch_subclass():
    d = InnerDispatchForResolver(weakref.ref(_FakeResolver()), _session())
    assert isinstance(d, ToolDispatch)
    assert isinstance(d, InnerDispatchForResolver)


def test_concrete_class_instantiable_call_implemented():
    # ToolDispatch.call is implemented -> no abstract methods -> the class
    # is concrete and instantiable.
    assert InnerDispatchForResolver.__abstractmethods__ == set()
    d = InnerDispatchForResolver(weakref.ref(_FakeResolver()), _session())
    assert d is not None


def test_call_is_coroutine_function():
    # Mapping 1: call is an `async def -> AsyncIterator` (await-resolved,
    # then async-for driven), NOT an async generator.
    d = InnerDispatchForResolver(weakref.ref(_FakeResolver()), _session())
    assert inspect.iscoroutinefunction(d.call)


# ---------------------------------------------------------------------------
# Weak-ref semantics — the adapter holds the resolver weakly.
# ---------------------------------------------------------------------------


def test_resolver_field_holds_weak_not_strong():
    # Constructing a dispatch must NOT keep the resolver alive. The only
    # strong ref is the local `r`; once it drops the weak ref dies.
    r = _FakeResolver()
    d = InnerDispatchForResolver(weakref.ref(r), _session())
    assert d.resolver() is r  # alive while the local strong ref lives
    del r
    gc.collect()
    assert d.resolver() is None  # dead once the only strong ref drops


# ---------------------------------------------------------------------------
# eq=False (Rust Debug-only derive) -> identity-only equality.
# ---------------------------------------------------------------------------


def test_identity_equality_only():
    r = _FakeResolver()
    d1 = InnerDispatchForResolver(weakref.ref(r), _session())
    assert d1 == d1
    # Two adapters with equal-looking fields are NOT equal (identity only).
    d2 = InnerDispatchForResolver(weakref.ref(r), _session())
    assert d1 != d2


def test_repr_mentions_class_name():
    d = InnerDispatchForResolver(weakref.ref(_FakeResolver()), _session())
    assert "InnerDispatchForResolver" in repr(d)


# ---------------------------------------------------------------------------
# call — live-resolver arm delegates to resolve_and_dispatch.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_live_resolver_returns_resolver_stream():
    r = _FakeResolver()
    d = InnerDispatchForResolver(weakref.ref(r), _session())
    result = await d.call(_tid(), _ARGS, _CTX)
    # The adapter forwards the resolver's stream verbatim (no wrapping).
    assert result is _STREAM


@pytest.mark.asyncio
async def test_call_live_resolver_threads_bound_session_and_args():
    r = _FakeResolver()
    d = InnerDispatchForResolver(weakref.ref(r), _session())
    await d.call(_tid(), _ARGS, _CTX)
    # Threads (self.session_id, tool_id, args, ctx) — in particular the
    # BOUND session, not any session derivable from ctx.
    assert r.dispatch_calls == [(_session(), _tid(), _ARGS, _CTX)]


@pytest.mark.asyncio
async def test_call_threads_bound_session_even_when_ctx_differs():
    # The adapter MUST thread self.session_id (bound at construction), not
    # anything read off ctx — mirroring Rust's per-session lifetime. A
    # different ctx sentinel must not leak into the session slot.
    r = _FakeResolver()
    d = InnerDispatchForResolver(weakref.ref(r), _session())
    other_ctx = object()
    await d.call(_tid(), _ARGS, other_ctx)
    assert r.dispatch_calls[0][0] == _session()  # bound session wins
    assert r.dispatch_calls[0][3] is other_ctx  # ctx forwarded as-is


# ---------------------------------------------------------------------------
# call — dead-resolver arm short-circuits to computer_hub_dropped.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_dead_resolver_short_circuits_to_hub_dropped():
    r = _FakeResolver()
    d = InnerDispatchForResolver(weakref.ref(r), _session())
    del r
    gc.collect()
    assert d.resolver() is None  # precondition: the weak ref is dead

    stream = await d.call(_tid(), _ARGS, _CTX)
    # terminal_only yields exactly one Terminal item.
    items = [item async for item in stream]
    assert len(items) == 1
    item = items[0]
    assert item.is_terminal()
    assert item.is_error()
    err = item.terminal
    assert isinstance(err, ToolError)
    assert err.detail == "computer hub dropped before inner call could execute"
    # ToolError.custom(code, detail) -> details == {"code": code}.
    assert err.details == {"code": "computer_hub_dropped"}


# ---------------------------------------------------------------------------
# call_terminal — inherited unchanged from ToolDispatch (not overridden).
# ---------------------------------------------------------------------------


def test_call_terminal_is_inherited_not_overridden():
    # The inner adapter does NOT override call_terminal; it reuses the
    # concrete default drain shipped by R113's ToolDispatch ABC.
    assert InnerDispatchForResolver.call_terminal is ToolDispatch.call_terminal
    # And it is not abstract.
    assert "call_terminal" not in InnerDispatchForResolver.__abstractmethods__
