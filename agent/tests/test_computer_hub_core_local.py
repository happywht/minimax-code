"""Tests for the R119 computer_hub_core local module.

Covers the migration of ``xai-computer-hub-core/src/local.rs``. The Rust
source lands two symbols: the ``LOCAL_INVOKE_SCOPE`` constant and
``LocalTransport`` — the first concrete ``Transport`` implementation in
the crate, an in-process transport that authorises a bound
``(user_id, session_id)`` and dispatches through a ``CompoundResolver``.

These Python tests mirror that plus the trait-shape / strong-ref / sync-kind
semantics that distinguish R119 from R118's weak-ref inner adapter:

- ``LocalTransport`` subclasses R115's :class:`Transport` ABC and
  implements all three abstract methods (``kind`` / ``authorize`` /
  ``call``) -> the class is concrete (empty ``__abstractmethods__``) and
  instantiable.
- ``kind`` is a **sync** method returning :attr:`TransportKind.Local`
  (the other two are mapping-1 coroutines).
- The ``resolver`` field is a **strong** reference: constructing a
  transport DOES keep the resolver alive — once the caller's strong ref
  drops, the transport is an additional strong ref so the resolver
  survives. This is the deliberate counterpart to R118's inner adapter,
  which held the resolver weakly.
- ``authorize`` builds the principal via
  ``Principal.new(uid).with_session(sid).with_scope(LOCAL_INVOKE_SCOPE)``
  and always returns a :class:`Principal` (never a :class:`ToolError` —
  the local path has no credential to validate, so it always takes Rust's
  ``Ok`` arm).
- ``call`` delegates to ``resolve_and_dispatch``, threading the bound
  ``self.session_id`` (NOT any session derivable from ``ctx``).
- ``eq=False`` (Rust ``Debug``-only derive) -> identity-only equality.
"""

from __future__ import annotations

import gc
import inspect
import weakref

import pytest

from minimax_code.computer_hub_core.local import LOCAL_INVOKE_SCOPE, LocalTransport
from minimax_code.computer_hub_core.transport import Principal, Transport
from minimax_code.tool_protocol import SessionId, ToolId, TransportKind, UserId
from minimax_code.tool_runtime import ToolError

# ---------------------------------------------------------------------------
# Sentinels / fixtures.
# ---------------------------------------------------------------------------

# ToolCallContext is a duck-typed seam (the transport forwards ctx opaquely
# into resolve_and_dispatch); a bare sentinel exercises the threading
# without needing a real context. args is likewise opaque.
_CTX = object()
_ARGS = {"q": "hello"}
# The fake resolver returns this opaque sentinel as its "stream"; the
# transport must forward it verbatim (it does not drive the stream).
_STREAM = object()


def _tid() -> ToolId:
    return ToolId("search__web")


def _session() -> SessionId:
    return SessionId("session-3")


def _user() -> UserId:
    return UserId("user-7")


class _FakeResolver:
    """Minimal duck-typed CompoundResolver for local-transport tests.

    Real :class:`~minimax_code.computer_hub_core.resolver.CompoundResolver`
    (R117) needs a live :class:`~minimax_code.computer_hub_core.ToolRegistry`;
    for trait-shape + forwarding tests a bare duck type exposing the single
    dispatch method the transport calls is enough. Carries no ``__slots__``
    so it is weakly referenceable (needed to prove the strong-ref invariant
    via a weakref sentinel).
    """

    def __init__(self) -> None:
        self.dispatch_calls: list = []

    async def resolve_and_dispatch(self, session_id, tool_id, args, ctx):
        self.dispatch_calls.append((session_id, tool_id, args, ctx))
        return _STREAM


def _make_transport(resolver: _FakeResolver | None = None) -> LocalTransport:
    return LocalTransport(resolver or _FakeResolver(), _user(), _session())


# ---------------------------------------------------------------------------
# LOCAL_INVOKE_SCOPE constant.
# ---------------------------------------------------------------------------


def test_local_invoke_scope_constant():
    assert LOCAL_INVOKE_SCOPE == "tool.invoke"
    assert isinstance(LOCAL_INVOKE_SCOPE, str)


# ---------------------------------------------------------------------------
# Transport subclass + concrete class.
# ---------------------------------------------------------------------------


def test_is_transport_subclass():
    t = _make_transport()
    assert isinstance(t, Transport)
    assert isinstance(t, LocalTransport)


def test_concrete_class_instantiable_three_methods_implemented():
    # Transport.kind/authorize/call are all implemented -> no abstract
    # methods -> the class is concrete and instantiable.
    assert LocalTransport.__abstractmethods__ == set()
    t = _make_transport()
    assert t is not None


# ---------------------------------------------------------------------------
# Construction + field access.
# ---------------------------------------------------------------------------


def test_fields_accessed_directly():
    r = _FakeResolver()
    t = LocalTransport(r, _user(), _session())
    assert t.resolver is r  # strong ref, held directly
    assert t.user_id == _user()
    assert t.session_id == _session()


# ---------------------------------------------------------------------------
# Strong-ref semantics — the transport holds the resolver strongly.
# ---------------------------------------------------------------------------


def test_resolver_held_strongly_keeps_alive():
    # CONTRAST with R118 InnerDispatchForResolver (weak ref -> dies).
    # A local transport holds the resolver strongly: the transport IS an
    # additional strong ref, so dropping the caller's local keeps the
    # resolver alive (Rust Arc shared-ownership semantics).
    r = _FakeResolver()
    t = LocalTransport(r, _user(), _session())
    ref = weakref.ref(r)  # sentinel to observe liveness
    assert t.resolver is r
    del r
    gc.collect()
    assert ref() is not None  # still alive — the transport holds it
    assert t.resolver is ref()  # same object


# ---------------------------------------------------------------------------
# eq=False (Rust Debug-only derive) -> identity-only equality.
# ---------------------------------------------------------------------------


def test_identity_equality_only():
    r = _FakeResolver()
    t1 = LocalTransport(r, _user(), _session())
    assert t1 == t1
    # Two transports with equal-looking fields are NOT equal (identity only).
    t2 = LocalTransport(r, _user(), _session())
    assert t1 != t2


def test_repr_mentions_class_name():
    t = _make_transport()
    assert "LocalTransport" in repr(t)


# ---------------------------------------------------------------------------
# kind — sync method returning TransportKind.Local.
# ---------------------------------------------------------------------------


def test_kind_returns_local():
    t = _make_transport()
    assert t.kind() == TransportKind.Local
    assert t.kind() is TransportKind.Local


def test_kind_is_sync_not_coroutine():
    # kind is the one sync Transport method (no I/O — just a discriminant).
    t = _make_transport()
    assert not inspect.iscoroutinefunction(t.kind)
    # And calling it returns the value directly (no coroutine object).
    result = t.kind()
    assert result == TransportKind.Local


def test_authorize_is_coroutine_function():
    t = _make_transport()
    assert inspect.iscoroutinefunction(t.authorize)


def test_call_is_coroutine_function():
    # Mapping 1: call is an `async def -> ToolStream` (await-resolved), NOT
    # an async generator.
    t = _make_transport()
    assert inspect.iscoroutinefunction(t.call)


# ---------------------------------------------------------------------------
# authorize — builds a Principal pre-populated with the bound identity.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorize_returns_principal_with_bound_identity():
    t = _make_transport()
    result = await t.authorize()
    assert isinstance(result, Principal)
    assert result.user_id == _user()
    assert result.session_ids == [_session()]


@pytest.mark.asyncio
async def test_authorize_principal_has_local_invoke_scope():
    t = _make_transport()
    p = await t.authorize()
    assert LOCAL_INVOKE_SCOPE in p.scopes
    assert p.has_scope(LOCAL_INVOKE_SCOPE) is True


@pytest.mark.asyncio
async def test_authorize_principal_authorizes_bound_session():
    t = _make_transport()
    p = await t.authorize()
    assert p.authorizes_session(_session()) is True


@pytest.mark.asyncio
async def test_authorize_never_returns_tool_error():
    # The local path always takes Rust's Ok arm: there is no credential to
    # validate and no network to drop, so authorize never surfaces a
    # ToolError. (The Principal | ToolError return type narrows to Principal
    # in practice, but the annotation keeps the Transport contract faithful.)
    t = _make_transport()
    result = await t.authorize()
    assert not isinstance(result, ToolError)


# ---------------------------------------------------------------------------
# call — delegates to resolve_and_dispatch, threading the bound session.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_delegates_to_resolver_threads_bound_session_and_args():
    r = _FakeResolver()
    t = LocalTransport(r, _user(), _session())
    result = await t.call(_tid(), _ARGS, _CTX)
    # The transport forwards the resolver's stream verbatim (no wrapping).
    assert result is _STREAM
    # Threads (self.session_id, tool_id, args, ctx) — the bound session,
    # not anything derivable from ctx.
    assert r.dispatch_calls == [(_session(), _tid(), _ARGS, _CTX)]


@pytest.mark.asyncio
async def test_call_threads_bound_session_not_ctx():
    # The transport MUST thread self.session_id (bound at construction),
    # not anything read off ctx. A different ctx sentinel must not leak
    # into the session slot.
    r = _FakeResolver()
    t = LocalTransport(r, _user(), _session())
    other_ctx = object()
    await t.call(_tid(), _ARGS, other_ctx)
    assert r.dispatch_calls[0][0] == _session()  # bound session wins
    assert r.dispatch_calls[0][3] is other_ctx  # ctx forwarded as-is
