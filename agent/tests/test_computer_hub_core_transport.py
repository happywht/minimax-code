"""Tests for the R115 computer_hub_core transport module.

Covers the migration of ``xai-computer-hub-core/src/transport.rs``. The
Rust source carries no inline ``#[test]`` block, so these are
Python-style semantic-equivalence checks:

- ``Principal::new`` builds a principal with empty session / scope /
  audience lists.
- ``with_session`` / ``with_scope`` / ``with_audience`` append AND return
  ``self`` (Rust ``mut self`` move-and-return builders chain).
- ``has_scope`` / ``authorizes_session`` membership queries.
- ``#[derive(PartialEq, Eq)]`` -> field-wise ``__eq__`` (id-newtype fields
  are ``str`` subclasses with value equality).
- ``trait Transport`` is object-safe -> ``abc.ABC``: cannot instantiate
  directly; a subclass missing any of ``kind`` / ``authorize`` / ``call``
  stays abstract; a subclass implementing all three is concrete.
- ``authorize`` returns ``Principal | ToolError`` BY VALUE (R107
  convention — the error path is returned, never raised).
- ``call`` threads ``(tool_id, args, ctx)`` to the implementation.
- ``TransportKind`` is re-exported from the protocol crate (same object,
  single canonical enum).
"""

from __future__ import annotations

from typing import Any

import pytest

from minimax_code.computer_hub_core import Principal, Transport, TransportKind
from minimax_code.tool_protocol import (
    SessionId,
    ToolId,
    UserId,
)
from minimax_code.tool_protocol import (
    TransportKind as ProtoTransportKind,
)
from minimax_code.tool_protocol.ids import ToolCallId
from minimax_code.tool_runtime import ToolCallContext, ToolError

# ---------------------------------------------------------------------------
# Sentinels / fixtures.
# ---------------------------------------------------------------------------

_STREAM = object()


def _uid() -> UserId:
    return UserId("user-7")


def _sid() -> SessionId:
    return SessionId("session-3")


def _tid() -> ToolId:
    return ToolId("search__web")


def _ctx() -> ToolCallContext:
    return ToolCallContext(call_id=ToolCallId("call-1"))


# ---------------------------------------------------------------------------
# Principal::new + default empty lists.
# ---------------------------------------------------------------------------


def test_principal_new_has_empty_lists():
    p = Principal.new(_uid())
    assert p.user_id == _uid()
    assert p.session_ids == []
    assert p.scopes == []
    assert p.audiences == []


def test_principal_direct_constructor_matches_new():
    # `new` is a thin classmethod over the dataclass constructor.
    uid = _uid()
    assert Principal.new(uid) == Principal(user_id=uid)


# ---------------------------------------------------------------------------
# with_* builders — append AND return self (Rust mut self move-and-return).
# ---------------------------------------------------------------------------


def test_with_session_appends_and_returns_self():
    p = Principal.new(_uid())
    sid = _sid()
    returned = p.with_session(sid)
    assert returned is p  # same object (Rust move-and-return)
    assert p.session_ids == [sid]


def test_with_scope_appends_and_returns_self():
    p = Principal.new(_uid())
    returned = p.with_scope("tool.invoke")
    assert returned is p
    assert p.scopes == ["tool.invoke"]


def test_with_audience_appends_and_returns_self():
    p = Principal.new(_uid())
    returned = p.with_audience("hub.local")
    assert returned is p
    assert p.audiences == ["hub.local"]


def test_builder_chains_identically_to_rust():
    # Principal::new(u).with_session(s).with_scope(sc).with_audience(a)
    p = (
        Principal.new(_uid())
        .with_session(_sid())
        .with_scope("tool.invoke")
        .with_audience("hub.local")
    )
    assert p.session_ids == [_sid()]
    assert p.scopes == ["tool.invoke"]
    assert p.audiences == ["hub.local"]


# ---------------------------------------------------------------------------
# has_scope / authorizes_session membership queries.
# ---------------------------------------------------------------------------


def test_has_scope_true_when_present_false_when_absent():
    p = Principal.new(_uid()).with_scope("tool.invoke").with_scope("read")
    assert p.has_scope("tool.invoke") is True
    assert p.has_scope("read") is True
    assert p.has_scope("admin") is False


def test_authorizes_session_true_when_bound_false_otherwise():
    bound = _sid()
    other = SessionId("session-9")
    p = Principal.new(_uid()).with_session(bound)
    assert p.authorizes_session(bound) is True
    assert p.authorizes_session(other) is False


# ---------------------------------------------------------------------------
# #[derive(PartialEq, Eq)] -> field-wise equality.
# ---------------------------------------------------------------------------


def test_principal_equality_is_field_wise():
    a = Principal.new(_uid()).with_session(_sid()).with_scope("s")
    b = Principal.new(_uid()).with_session(_sid()).with_scope("s")
    c = Principal.new(_uid()).with_scope("s")  # no session
    d = Principal.new(_uid()).with_session(_sid()).with_scope("other")
    assert a == b  # identical fields
    assert a != c  # session set differs
    assert a != d  # scope differs


# ---------------------------------------------------------------------------
# Transport trait -> abc.ABC object-safety shape.
# ---------------------------------------------------------------------------


def test_transport_is_abstract_cannot_instantiate():
    # All three methods are @abstractmethod -> the bare trait has no form.
    with pytest.raises(TypeError):
        Transport()


def test_subclass_missing_kind_stays_abstract():
    class _NoKind(Transport):
        async def authorize(self):
            return None

        async def call(self, tool_id, args, ctx):
            return None

    with pytest.raises(TypeError):
        _NoKind()


def test_subclass_missing_authorize_stays_abstract():
    class _NoAuthorize(Transport):
        def kind(self):
            return TransportKind.Local

        async def call(self, tool_id, args, ctx):
            return None

    with pytest.raises(TypeError):
        _NoAuthorize()


def test_subclass_missing_call_stays_abstract():
    class _NoCall(Transport):
        def kind(self):
            return TransportKind.Local

        async def authorize(self):
            return None

    with pytest.raises(TypeError):
        _NoCall()


def test_subclass_implementing_all_methods_is_concrete():
    t = _PrincipalTransport(Principal.new(_uid()))
    assert isinstance(t, Transport)


# ---------------------------------------------------------------------------
# authorize — Principal by value (success) AND ToolError by value (not raised).
# ---------------------------------------------------------------------------


class _PrincipalTransport(Transport):
    """Transport whose ``authorize`` returns a fixed principal."""

    def __init__(self, principal: Principal) -> None:
        self._principal = principal

    def kind(self) -> TransportKind:
        return TransportKind.Local

    async def authorize(self) -> Principal | ToolError:
        return self._principal

    async def call(self, tool_id: ToolId, args: Any, ctx: ToolCallContext) -> object:
        return _STREAM


class _ErrorTransport(Transport):
    """Transport whose ``authorize`` returns a ToolError (Rust ``Err`` arm)."""

    def __init__(self, error: ToolError) -> None:
        self._error = error

    def kind(self) -> TransportKind:
        return TransportKind.Remote

    async def authorize(self) -> Principal | ToolError:
        return self._error

    async def call(self, tool_id: ToolId, args: Any, ctx: ToolCallContext) -> object:
        return _STREAM


@pytest.mark.asyncio
async def test_authorize_returns_principal_by_value():
    principal = Principal.new(_uid()).with_scope("tool.invoke")
    result = await _PrincipalTransport(principal).authorize()
    assert isinstance(result, Principal)
    assert result is principal
    assert result.has_scope("tool.invoke")


@pytest.mark.asyncio
async def test_authorize_may_return_tool_error_by_value_not_raised():
    # The Err arm: authorize() returns the ToolError, it does NOT raise.
    err = ToolError.execution(_tid(), "credential rejected")
    result = await _ErrorTransport(err).authorize()
    assert isinstance(result, ToolError)
    assert result is err
    # The caller discriminates with isinstance, not try/except.
    assert not isinstance(result, Principal)


# ---------------------------------------------------------------------------
# kind — returns a TransportKind value.
# ---------------------------------------------------------------------------


def test_principal_transport_kind_is_local():
    assert _PrincipalTransport(Principal.new(_uid())).kind() is TransportKind.Local


def test_error_transport_kind_is_remote():
    err = ToolError.execution(_tid(), "x")
    assert _ErrorTransport(err).kind() is TransportKind.Remote


# ---------------------------------------------------------------------------
# call — argument threading.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_call_threads_args_and_returns_stream():
    class _Recording(Transport):
        def __init__(self) -> None:
            self.seen: list[tuple[ToolId, Any, ToolCallContext]] = []

        def kind(self) -> TransportKind:
            return TransportKind.Local

        async def authorize(self) -> Principal | ToolError:
            return Principal.new(_uid())

        async def call(self, tool_id: ToolId, args: Any, ctx: ToolCallContext) -> object:
            self.seen.append((tool_id, args, ctx))
            return _STREAM

    t = _Recording()
    tid = _tid()
    args = {"q": "rust", "limit": 5}
    ctx = _ctx()
    result = await t.call(tid, args, ctx)
    assert result is _STREAM
    assert t.seen == [(tid, args, ctx)]


# ---------------------------------------------------------------------------
# TransportKind re-export — single canonical enum (protocol source of truth).
# ---------------------------------------------------------------------------


def test_transportkind_reexport_is_same_object_as_protocol():
    # `pub use xai_tool_protocol::TransportKind` — the hub re-export and the
    # protocol original are the SAME class object (not a copy).
    assert TransportKind is ProtoTransportKind


def test_transportkind_local_and_remote_members_exist():
    # The two transport flavours the router discriminates on.
    assert TransportKind.Local is ProtoTransportKind.Local
    assert TransportKind.Remote is ProtoTransportKind.Remote
