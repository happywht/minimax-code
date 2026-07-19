"""Tests for the R116 computer_hub_core registry module.

Covers the migration of ``xai-computer-hub-core/src/registry.rs``. The
Rust source carries an inline ``seq_tests`` module (one HLC test); these
Python tests mirror that plus the trait-shape / value-type
semantic-equivalence checks:

- ``ToolSessionBindOutcome`` / ``ToolSessionUnbindOutcome``: plain
  :class:`enum.Enum` (NOT :class:`StrEnum` — process-internal, never
  serialised); member roster matches the Rust variants.
- ``ConnectionCleanupReport`` / ``SessionCleanupReport``:
  :func:`dataclasses.dataclass` with zero defaults (Rust ``Default``) +
  field-wise ``__eq__`` (derived ``PartialEq, Eq``).
- ``ServerRecord``: ``eq=False`` (Rust derive ``Debug, Clone`` only — no
  ``PartialEq`` because ``serde_json::Value`` / ``chrono::DateTime`` are
  not ``Eq``); identity-only ``__eq__``.
- ``trait ToolRegistry`` -> :class:`abc.ABC` (object-safe, ``Arc<dyn
  ToolRegistry>`` — corrects the R115 ``Protocol`` prediction): bare trait
  cannot instantiate; re-abstracting any of the 15 abstract methods keeps
  a subclass abstract; a full subclass is concrete.
- ``get_server_id``: the one **concrete** provided method — not
  ``@abstractmethod``; default body delegates to ``get_server_record``;
  subclasses MAY override.
- Sync / async split: the eight mutating methods are coroutine functions;
  the seven view methods are plain functions.
- ``next_registration_seq``: the HLC — strictly increasing across a burst,
  epoch-seeded (high 54 bits decode to a recent epoch ms), and advances at
  least one per call.
"""

from __future__ import annotations

import abc
import datetime
import inspect

import pytest

from minimax_code.computer_hub_core import (
    ConnectionCleanupReport,
    ServerRecord,
    SessionCleanupReport,
    ToolRegistry,
    ToolSessionBindOutcome,
    ToolSessionUnbindOutcome,
    next_registration_seq,
)
from minimax_code.tool_protocol import (
    ConnectionId,
    ServerId,
    SessionId,
    ToolId,
    UserId,
)

# ---------------------------------------------------------------------------
# Sentinels / fixtures.
# ---------------------------------------------------------------------------

# RegistrationOutcome / SearchSnapshot stand-ins — the mock registry only
# needs to prove the trait is callable; the internal shape of those types
# is covered by their own leaf tests (R76/R87, R112).
_REGISTERED = object()
_SNAPSHOT = object()


def _cid() -> ConnectionId:
    return ConnectionId("conn-1")


def _sid() -> ServerId:
    return ServerId("server-7")


def _tid() -> ToolId:
    return ToolId("search__web")


def _session() -> SessionId:
    return SessionId("session-3")


def _uid() -> UserId:
    return UserId("user-9")


def _server_record(**overrides) -> ServerRecord:
    fields = dict(
        connection_id=_cid(),
        user_id=_uid(),
        server_id=_sid(),
        description="test server",
        metadata={"k": "v"},
        registered_at=datetime.datetime(2025, 1, 1, 12, 0, 0, tzinfo=datetime.UTC),
        registration_seq=42,
    )
    fields.update(overrides)
    return ServerRecord(**fields)


class _FullRegistry(ToolRegistry):
    """Concrete registry implementing every abstract method.

    State is the minimum needed to exercise the concrete ``get_server_id``
    default: a connection -> record map. Other methods return fixed
    sentinels / empty containers (trait-shape only).
    """

    def __init__(self) -> None:
        self._servers: dict[ConnectionId, ServerRecord] = {}

    async def register_tool(self, connection_id, reg):
        return _REGISTERED

    async def register_server(self, connection_id, reg):
        return [_REGISTERED]

    async def unregister_tool(self, connection_id, tool):
        return False

    async def unregister_server(self, connection_id, server):
        return 0

    async def bind_tool_session(self, connection_id, tool, session_id):
        return ToolSessionBindOutcome.Bound

    async def unbind_tool_session(self, connection_id, tool, session_id):
        return ToolSessionUnbindOutcome.Unbound

    async def drop_connection(self, connection_id):
        return ConnectionCleanupReport()

    async def unregister_session(self, session_id):
        return SessionCleanupReport()

    def find_tool(self, session, tool):
        return None

    def list_tools(self, session, mode):
        return []

    def list_servers(self, session):
        return []

    def search(self, session, query, limit):
        return _SNAPSHOT

    def tool_sessions(self, connection_id, tool):
        return set()

    def list_servers_for_user(self, user_id):
        return []

    def get_server_record(self, connection_id):
        return self._servers.get(connection_id)


# ---------------------------------------------------------------------------
# ToolSessionBindOutcome / ToolSessionUnbindOutcome — plain Enum.
# ---------------------------------------------------------------------------


def test_bind_outcome_has_four_members_matching_rust_variants():
    assert {m.name for m in ToolSessionBindOutcome} == {
        "Bound", "AlreadyBound", "UnknownTool", "Conflict",
    }


def test_unbind_outcome_has_three_members_matching_rust_variants():
    assert {m.name for m in ToolSessionUnbindOutcome} == {
        "Unbound", "NotBound", "UnknownTool",
    }


def test_outcomes_are_plain_enum_not_strenum():
    # Process-internal discriminants (never serialised) -> enum.Enum, NOT
    # StrEnum. Members are Enum instances but NOT str instances.
    assert isinstance(ToolSessionBindOutcome.Bound, ToolSessionBindOutcome)
    assert isinstance(ToolSessionUnbindOutcome.Unbound, ToolSessionUnbindOutcome)
    assert not isinstance(ToolSessionBindOutcome.Bound, str)
    assert not isinstance(ToolSessionUnbindOutcome.Unbound, str)


def test_outcome_members_within_enum_are_distinct():
    bind = list(ToolSessionBindOutcome)
    unbind = list(ToolSessionUnbindOutcome)
    assert len(bind) == len(set(bind))
    assert len(unbind) == len(set(unbind))


# ---------------------------------------------------------------------------
# ConnectionCleanupReport / SessionCleanupReport — Default + PartialEq.
# ---------------------------------------------------------------------------


def test_connection_cleanup_report_defaults_zero():
    r = ConnectionCleanupReport()
    assert r.tools_dropped == 0
    assert r.session_bindings_cleared == 0


def test_connection_cleanup_report_field_equality():
    assert ConnectionCleanupReport(3, 5) == ConnectionCleanupReport(3, 5)
    assert ConnectionCleanupReport(3, 5) != ConnectionCleanupReport(3, 6)
    assert ConnectionCleanupReport(3, 5) != ConnectionCleanupReport(4, 5)


def test_session_cleanup_report_defaults_zero():
    r = SessionCleanupReport()
    assert r.tools_touched == 0
    assert r.tools_left_orphaned == 0


def test_session_cleanup_report_field_equality():
    assert SessionCleanupReport(2, 1) == SessionCleanupReport(2, 1)
    assert SessionCleanupReport(2, 1) != SessionCleanupReport(2, 0)


# ---------------------------------------------------------------------------
# ServerRecord — no PartialEq (eq=False -> identity-only).
# ---------------------------------------------------------------------------


def test_server_record_field_access():
    r = _server_record()
    assert r.connection_id == _cid()
    assert r.user_id == _uid()
    assert r.server_id == _sid()
    assert r.description == "test server"
    assert r.metadata == {"k": "v"}
    assert r.registration_seq == 42


def test_server_record_no_partialeq_identity_only():
    # Rust derive is `Debug, Clone` (no PartialEq/Eq) -> eq=False -> two
    # records with identical fields are NOT equal (identity only).
    a = _server_record()
    b = _server_record()
    assert a != b
    assert a == a


# ---------------------------------------------------------------------------
# ToolRegistry -> abc.ABC object-safety shape (corrects R115 Protocol guess).
# ---------------------------------------------------------------------------


def test_registry_is_abstract_cannot_instantiate():
    with pytest.raises(TypeError):
        ToolRegistry()


def test_registry_has_fifteen_abstract_methods_plus_concrete_get_server_id():
    # 8 async mutating + 7 sync view = 15 abstract; get_server_id concrete.
    abstract = set(ToolRegistry.__abstractmethods__)
    expected = {
        "register_tool", "register_server", "unregister_tool", "unregister_server",
        "bind_tool_session", "unbind_tool_session", "drop_connection",
        "unregister_session",
        "find_tool", "list_tools", "list_servers", "search", "tool_sessions",
        "list_servers_for_user", "get_server_record",
    }
    assert abstract == expected


def test_subclass_re_abstracting_one_method_stays_abstract():
    # A subclass that re-marks ANY of the 15 abstract methods as abstract
    # is itself non-instantiable (proves every listed method is genuinely
    # required, not just decorative).
    class _ReAbstract(_FullRegistry):
        @abc.abstractmethod
        async def register_tool(self, connection_id, reg):
            ...

    with pytest.raises(TypeError):
        _ReAbstract()


def test_subclass_re_abstracting_a_sync_view_stays_abstract():
    class _ReAbstract(_FullRegistry):
        @abc.abstractmethod
        def find_tool(self, session, tool):
            ...

    with pytest.raises(TypeError):
        _ReAbstract()


def test_full_registry_is_concrete():
    r = _FullRegistry()
    assert isinstance(r, ToolRegistry)


def test_get_server_id_is_not_abstract():
    # The one concrete provided method — its __isabstractmethod__ is False.
    assert getattr(ToolRegistry.get_server_id, "__isabstractmethod__", False) is False
    # And the abstract methods set does NOT contain get_server_id.
    assert "get_server_id" not in ToolRegistry.__abstractmethods__


# ---------------------------------------------------------------------------
# get_server_id — concrete default delegates to get_server_record.
# ---------------------------------------------------------------------------


def test_get_server_id_default_returns_server_id_when_record_present():
    r = _FullRegistry()
    cid = _cid()
    sid = _sid()
    r._servers[cid] = _server_record(connection_id=cid, server_id=sid)
    assert r.get_server_id(cid) == sid


def test_get_server_id_default_returns_none_when_no_record():
    r = _FullRegistry()
    assert r.get_server_id(ConnectionId("missing")) is None


def test_get_server_id_can_be_overridden():
    class _Override(_FullRegistry):
        def get_server_id(self, connection_id):
            return ServerId("override")

    assert _Override().get_server_id(_cid()) == ServerId("override")


# ---------------------------------------------------------------------------
# Sync / async split — mutating coroutine functions, view plain functions.
# ---------------------------------------------------------------------------


def test_mutating_methods_are_coroutine_functions():
    r = _FullRegistry()
    assert inspect.iscoroutinefunction(r.register_tool)
    assert inspect.iscoroutinefunction(r.register_server)
    assert inspect.iscoroutinefunction(r.unregister_tool)
    assert inspect.iscoroutinefunction(r.unregister_server)
    assert inspect.iscoroutinefunction(r.bind_tool_session)
    assert inspect.iscoroutinefunction(r.unbind_tool_session)
    assert inspect.iscoroutinefunction(r.drop_connection)
    assert inspect.iscoroutinefunction(r.unregister_session)


def test_view_methods_are_not_coroutine_functions():
    r = _FullRegistry()
    assert not inspect.iscoroutinefunction(r.find_tool)
    assert not inspect.iscoroutinefunction(r.list_tools)
    assert not inspect.iscoroutinefunction(r.list_servers)
    assert not inspect.iscoroutinefunction(r.search)
    assert not inspect.iscoroutinefunction(r.tool_sessions)
    assert not inspect.iscoroutinefunction(r.list_servers_for_user)
    assert not inspect.iscoroutinefunction(r.get_server_record)


# ---------------------------------------------------------------------------
# Async dispatch — awaiting the mutating surface.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_register_tool_await_returns_outcome():
    r = _FullRegistry()
    result = await r.register_tool(_cid(), object())
    assert result is _REGISTERED


@pytest.mark.asyncio
async def test_register_server_await_returns_outcome_list():
    r = _FullRegistry()
    result = await r.register_server(_cid(), object())
    assert result == [_REGISTERED]


@pytest.mark.asyncio
async def test_bind_tool_session_await_returns_bind_outcome():
    r = _FullRegistry()
    result = await r.bind_tool_session(_cid(), _tid(), _session())
    assert result is ToolSessionBindOutcome.Bound


@pytest.mark.asyncio
async def test_unbind_tool_session_await_returns_unbind_outcome():
    r = _FullRegistry()
    result = await r.unbind_tool_session(_cid(), _tid(), _session())
    assert result is ToolSessionUnbindOutcome.Unbound


@pytest.mark.asyncio
async def test_drop_connection_await_returns_cleanup_report():
    r = _FullRegistry()
    result = await r.drop_connection(_cid())
    assert isinstance(result, ConnectionCleanupReport)


@pytest.mark.asyncio
async def test_unregister_session_await_returns_session_cleanup_report():
    r = _FullRegistry()
    result = await r.unregister_session(_session())
    assert isinstance(result, SessionCleanupReport)


# ---------------------------------------------------------------------------
# Sync views — return-type shape.
# ---------------------------------------------------------------------------


def test_tool_sessions_returns_set_of_session_ids():
    r = _FullRegistry()
    result = r.tool_sessions(_cid(), _tid())
    assert isinstance(result, set)


def test_find_tool_returns_none_from_mock():
    # Mock returns None (ResolvedTool is not yet landed; None is the
    # legitimate "not found / shadowed" return).
    assert _FullRegistry().find_tool(_session(), _tid()) is None


def test_list_tools_returns_list():
    assert _FullRegistry().list_tools(_session(), None) == []


def test_search_returns_snapshot_sentinel():
    assert _FullRegistry().search(_session(), "q", 10) is _SNAPSHOT


# ---------------------------------------------------------------------------
# next_registration_seq — the hybrid logical clock (Rust seq_tests mirror).
# ---------------------------------------------------------------------------


def test_next_registration_seq_strictly_increasing_under_burst():
    # Rust: 50_000-iteration burst asserting strict monotonicity. Python
    # mirrors with 2_000 (enough to surface any regression, fast enough for
    # the unit-test budget).
    prev = next_registration_seq()
    for _ in range(2_000):
        cur = next_registration_seq()
        assert cur > prev, "registration seq regressed under burst"
        prev = cur


def test_next_registration_seq_epoch_seeded_high_bits_decode_to_recent_epoch():
    # candidate = now_ms << 10 -> the high 54 bits ARE an epoch millisecond.
    # A stamp issued now must decode to a plausible recent UTC year. This
    # replaces the Rust wall-clock comparison (which is flaky under the
    # prev+1 fallback path when an earlier burst inflated the clock) with a
    # range check that is robust to it: even the prev+1 path inherits an
    # epoch-seeded high word.
    stamp = next_registration_seq()
    epoch_ms = stamp >> 10
    dt = datetime.datetime.fromtimestamp(epoch_ms / 1000, tz=datetime.UTC)
    assert 2024 <= dt.year <= 2035


def test_next_registration_seq_advances_at_least_one_per_call():
    # Each call bumps by >= 1 (max(candidate, prev + 1) -> prev + 1 when
    # candidate is stale). So N calls advance the stamp by >= N.
    first = next_registration_seq()
    prev = first
    n = 500
    for _ in range(n):
        prev = next_registration_seq()
    assert prev - first >= n


def test_next_registration_seq_returns_int():
    assert isinstance(next_registration_seq(), int)
