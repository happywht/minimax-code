"""Object-safe ``ToolRegistry`` trait shared by every storage plane (R116).

Fusion of grok-build's ``xai-computer-hub-core/src/registry.rs``. A
:class:`ToolRegistry` is the object-safe abstraction every router build
resolves tools through: implementations come in two planes — one in-memory
for statically-registered local tools, one connection-keyed for incoming
remote registrations — and the router composes them via
:class:`CompoundResolver` (a later round) without caring which plane is
which.

This module is the **second leaf** of the ``xai-computer-hub-core`` crate
(R115 landed ``transport``; ``resolver`` / ``inner`` / ``local`` / ``remote``
follow). Its external dependencies are all landed leaves: the protocol id
newtypes + the registration wire types (R82-R106), the runtime search /
server-summary views (R112), and the tool-plane schema vocabulary
(:class:`ToolDescription`, R65). Its one **forward** dependency —
:class:`ResolvedTool` from the not-yet-landed ``resolver`` leaf — is a true
bidirectional cycle (``resolver`` holds ``Arc<dyn ToolRegistry>``,
``registry`` returns ``Option<ResolvedTool>``), broken the same way R109
broke the ``tool`` <-> ``render`` cycle: ``TYPE_CHECKING``-only import +
``from __future__ import annotations`` string deferral.

R115 docstring correction
-------------------------

The R115 ``transport`` docstring listed ``ToolRegistry`` among the
"structural contracts that stay :class:`typing.Protocol`". Reading the
source now: ``ToolRegistry`` is ``#[async_trait] pub trait ToolRegistry:
Send + Sync + Debug`` and ``resolver.rs`` holds it behind
``Arc<dyn ToolRegistry>`` — that is **object-safe dynamic dispatch**, the
same family as :class:`Transport` (R115) and :class:`ToolDispatch` (R113).
So :class:`ToolRegistry` lands as :class:`abc.ABC`, NOT :class:`Protocol`.
The structural-:class:`Protocol` family remains the non-object-safe
contracts (:class:`Tool` / :class:`ToolDyn` / :class:`ToolFamily` R109,
:class:`ToolSearchIndex` R112) that carry associated types or are
duck-typed seams without a dyn-erased caller.

Split sync / async surface
--------------------------

The trait deliberately splits mutating methods (``async`` — registration
changes may touch shared state and require coordination) from read-only
views (synchronous — implementations answer from a consistent snapshot
without awaiting). The Python landing mirrors that split verbatim: eight
``@abc.abstractmethod async def`` mutating methods + seven synchronous
``@abc.abstractmethod def`` view methods + one synchronous **concrete**
method (:meth:`ToolRegistry.get_server_id`) with a default body that
delegates to :meth:`ToolRegistry.get_server_record`. The concrete-default
method follows the R113 :meth:`ToolDispatch.call_terminal` pattern (abstract
trait with one non-abstract provided method subclasses may override).

Process-internal outcome enums
------------------------------

:class:`ToolSessionBindOutcome` / :class:`ToolSessionUnbindOutcome` are the
**source of truth** for storage outcomes; the wire enum
``xai_tool_protocol::ToolSessionBindOutcome`` is a strict subset. They are
process-internal discriminants (never serialised), so they land as plain
:class:`enum.Enum` (NOT :class:`StrEnum`) — there is no wire string to
honour. ``Conflict`` (cross-connection race on the ``(session, tool)``
reverse-index slot) is registry-internal: the router lifts it to a
top-level ``ServerError::ToolBindingConflict`` (-32600) rather than
mirroring it to the wire ack; the wire enum's ``SessionNotBound`` is
router-injected and never produced by any registry call, so it has no
counterpart here either.

Monotonic registration clock
----------------------------

:func:`next_registration_seq` is a process-global hybrid logical clock:
per-process strictly-increasing (no ties, immune to wall-clock step-back)
and epoch-seeded so stamps also roughly order across replicas while
inter-replica skew stays within the revive window. The Rust original is an
``AtomicU64`` CAS loop; Python serialises the read-modify-write under a
:class:`threading.Lock` (the GIL already serialises bytecode, but the lock
makes the compound read-modify-write atomic across threads explicit).
``ServerRecord.registered_at`` is display-only —
:attr:`ServerRecord.registration_seq` is the ordering key.

Why no concrete registry here
-----------------------------

The Rust docstring is explicit: the concrete in-memory implementation is
**out of scope** for this crate (it requires a concurrency story — sharded
maps, an actor — that belongs alongside the registry's collision matrix
and generation handling). The Python landing mirrors that: this module
ships the trait + the outcome / report value types + the HLC; concrete
storage planes land elsewhere. Tests exercise the trait via per-test mock
implementations, exactly as the Rust crate does.
"""

from __future__ import annotations

import abc
import datetime
import threading
import time
from dataclasses import dataclass
from enum import Enum, auto
from typing import TYPE_CHECKING, Any

from minimax_code.tool_protocol import (
    ConnectionId,
    RegistrationOutcome,
    ServerId,
    SessionId,
    ToolDefinitionMode,
    ToolId,
    ToolRegistration,
    ToolServerRegistration,
    UserId,
)
from minimax_code.tool_runtime import SearchSnapshot, ServerSummary
from minimax_code.tool_types import ToolDescription

if TYPE_CHECKING:
    # Bidirectional cycle: resolver holds Arc<dyn ToolRegistry>, registry
    # returns Option<ResolvedTool>. TYPE_CHECKING-only + `from __future__
    # import annotations` string deferral breaks the cycle at runtime
    # (mirrors R109 tool <-> render).
    from minimax_code.computer_hub_core.resolver import ResolvedTool

__all__ = [
    "ConnectionCleanupReport",
    "SessionCleanupReport",
    "ServerRecord",
    "ToolRegistry",
    "ToolSessionBindOutcome",
    "ToolSessionUnbindOutcome",
    "next_registration_seq",
]


class ToolSessionBindOutcome(Enum):
    """Outcome of a single :meth:`ToolRegistry.bind_tool_session` call.

    Process-internal source of truth (Rust ``enum ToolSessionBindOutcome``).
    The wire enum ``xai_tool_protocol::ToolSessionBindOutcome`` is a strict
    subset — ``Conflict`` is registry-internal (lifted to a top-level
    ``ToolBindingConflict`` server error by the router) and the wire enum's
    ``SessionNotBound`` is router-injected (never produced by any registry
    call), so the two layers diverge deliberately. Lands as plain
    :class:`enum.Enum` (NOT :class:`StrEnum`) because it is never
    serialised — there is no wire string to honour.
    """

    Bound = auto()
    AlreadyBound = auto()
    UnknownTool = auto()
    Conflict = auto()


class ToolSessionUnbindOutcome(Enum):
    """Outcome of a single :meth:`ToolRegistry.unbind_tool_session` call."""

    Unbound = auto()
    NotBound = auto()
    UnknownTool = auto()


@dataclass
class ConnectionCleanupReport:
    """Aggregated summary of a connection-scoped cleanup pass.

    Rust ``#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]`` -> a
    plain :func:`dataclasses.dataclass` (default ``eq=True``) with zero
    defaults (``Default``). ``Copy`` has no Python equivalent (reference
    semantics), but field-wise ``__eq__`` matches the derived ``PartialEq``.
    """

    #: Number of distinct ``(connection, tool_id)`` records dropped.
    tools_dropped: int = 0
    #: Number of reverse-index ``(session_id, tool_id)`` rows cleaned up
    #: across every session the dropped tools were bound to.
    session_bindings_cleared: int = 0


@dataclass
class SessionCleanupReport:
    """Aggregated summary of a session-scoped cleanup pass.

    A tool whose session set becomes empty is **not** removed — the owning
    connection still owns it and may rebind via
    :meth:`ToolRegistry.bind_tool_session` later.
    """

    #: Number of tools whose session set lost the unregistered session id.
    tools_touched: int = 0
    #: Number of tools whose session set became empty after the
    #: unregistration (the tool record itself is NOT removed).
    tools_left_orphaned: int = 0


@dataclass(eq=False)
class ServerRecord:
    """Server identity captured at ``register_server`` time.

    Rust ``#[derive(Debug, Clone)]`` — note: **no** ``PartialEq`` / ``Eq``
    (``serde_json::Value`` and ``chrono::DateTime`` are not ``Eq``), so the
    Python landing uses ``eq=False`` (identity-only ``__eq__``, matching
    the absent derive). ``registered_at`` is display-only;
    :attr:`registration_seq` is the monotonic newest-wins discriminator.
    """

    #: Connection that introduced this server.
    connection_id: ConnectionId
    #: Owning user identity.
    user_id: UserId
    #: Stable server identity.
    server_id: ServerId
    #: Human-readable description.
    description: str
    #: Arbitrary server metadata (Rust ``serde_json::Value`` -> ``Any``).
    metadata: Any
    #: Wall-clock registration timestamp (Rust ``chrono::DateTime<Utc>`` ->
    #: aware :class:`datetime.datetime`). Display-only.
    registered_at: datetime.datetime
    #: Monotonic registration stamp (:func:`next_registration_seq`) — the
    #: stale-vs-revived discriminator for newest-wins.
    registration_seq: int


class ToolRegistry(abc.ABC):
    """Backend-agnostic registry of tools available within a router.

    Object-safe trait (Rust ``#[async_trait] pub trait ToolRegistry: Send +
    Sync + Debug``), held behind ``Arc<dyn ToolRegistry>`` by
    :class:`CompoundResolver` -> :class:`abc.ABC` (NOT :class:`Protocol`;
    see the module docstring's R115 correction). Methods split into
    mutating (``async``) and read-only views (synchronous); subclasses MUST
    implement every abstract method. :meth:`get_server_id` carries a
    default body (delegates to :meth:`get_server_record`) and is the one
    non-abstract provided method.

    The ``Send + Sync + Debug`` supertraits have no Python equivalent under
    the GIL; ``Debug`` is a soft expectation that implementations provide a
    ``__repr__``.

    Mutations are connection-scoped: each registered tool belongs to the
    :class:`ConnectionId` that introduced it. Per-tool session bindings
    live alongside the tool's record and are mutated independently via
    :meth:`bind_tool_session` / :meth:`unbind_tool_session`. Reads
    (:meth:`find_tool`, :meth:`list_tools`, :meth:`search`) are
    session-scoped — the router resolves a tool by ``(session_id,
    tool_id)``, never by connection id.
    """

    # ------------------------------------------------------------------
    # Mutating surface (async) — registration changes may touch shared
    # state and require coordination.
    # ------------------------------------------------------------------

    @abc.abstractmethod
    async def register_tool(
        self,
        connection_id: ConnectionId,
        reg: ToolRegistration,
    ) -> RegistrationOutcome:
        """Register a single tool against ``connection_id``.

        ``reg.sessions`` may be empty — the tool is registered but
        unreachable until :meth:`bind_tool_session` adds at least one
        session binding. Implementations must enforce per-
        ``(connection_id, tool_id)`` uniqueness within their plane.
        """
        ...

    @abc.abstractmethod
    async def register_server(
        self,
        connection_id: ConnectionId,
        reg: ToolServerRegistration,
    ) -> list[RegistrationOutcome]:
        """Register a multi-tool batch from a single tool server.

        Returns one :class:`RegistrationOutcome` per tool in input order.
        Batch semantics are best-effort: per-tool failures do not abort the
        rest of the batch. The whole batch shares ``reg.sessions`` (which
        may be empty).
        """
        ...

    @abc.abstractmethod
    async def unregister_tool(self, connection_id: ConnectionId, tool: ToolId) -> bool:
        """Drop the tool registered under ``(connection_id, tool_id)``.

        Returns ``True`` if a matching entry was removed, ``False`` if no
        such entry existed. The tool is removed from every session it was
        bound to in one shot — use :meth:`unbind_tool_session` for
        per-session removal.
        """
        ...

    @abc.abstractmethod
    async def unregister_server(
        self,
        connection_id: ConnectionId,
        server: ServerId,
    ) -> int:
        """Drop every tool registered by ``connection_id`` under ``server_id``.

        Returns the number of entries removed.
        """
        ...

    @abc.abstractmethod
    async def bind_tool_session(
        self,
        connection_id: ConnectionId,
        tool: ToolId,
        session_id: SessionId,
    ) -> ToolSessionBindOutcome:
        """Add ``session_id`` to the per-tool session set of ``(connection_id, tool_id)``.

        The caller (typically the WebSocket router) is responsible for
        verifying that ``session_id`` is in the connection's bound-session
        set before calling this method.
        """
        ...

    @abc.abstractmethod
    async def unbind_tool_session(
        self,
        connection_id: ConnectionId,
        tool: ToolId,
        session_id: SessionId,
    ) -> ToolSessionUnbindOutcome:
        """Remove ``session_id`` from the per-tool session set.

        Does not unregister the tool itself.
        """
        ...

    @abc.abstractmethod
    async def drop_connection(
        self, connection_id: ConnectionId
    ) -> ConnectionCleanupReport:
        """Drop every tool registered by ``connection_id``.

        Used by the WebSocket transport on disconnect cleanup. Returns
        counters describing how much state was released.
        """
        ...

    @abc.abstractmethod
    async def unregister_session(self, session_id: SessionId) -> SessionCleanupReport:
        """Drop the binding to ``session`` from every tool that has it.

        The affected tool records are NOT removed — their owning connection
        retains them and may rebind via :meth:`bind_tool_session`. Called
        by the WebSocket transport when a session ends globally.
        """
        ...

    # ------------------------------------------------------------------
    # Read-only views (synchronous) — implementations answer from a
    # consistent snapshot without awaiting.
    # ------------------------------------------------------------------

    @abc.abstractmethod
    def find_tool(self, session: SessionId, tool: ToolId) -> ResolvedTool | None:
        """Look up the active resolution for ``(session, tool)``.

        Returns ``None`` when no entry exists or when an entry exists but
        is shadowed. A shadowed entry is never returned — the caller sees
        only the active resolution.

        (Returns the not-yet-landed :class:`ResolvedTool`; the annotation
        is a deferred string under ``from __future__ import annotations``,
        so no runtime import is needed and the registry <-> resolver cycle
        stays broken.)
        """
        ...

    @abc.abstractmethod
    def list_tools(
        self, session: SessionId, mode: ToolDefinitionMode
    ) -> list[ToolDescription]:
        """Enumerate every active tool description for ``session``.

        Filtered by the requested presentation ``mode``; implementations
        decide how to honour the mode (e.g. omit non-meta tools when
        ``Concise`` is set).
        """
        ...

    @abc.abstractmethod
    def list_servers(self, session: SessionId) -> list[ServerSummary]:
        """Enumerate active server summaries for ``session``.

        Useful for rendering connected-integrations system reminders.
        """
        ...

    @abc.abstractmethod
    def search(self, session: SessionId, query: str, limit: int) -> SearchSnapshot:
        """Run a search query against the registry's index for ``session``.

        ``limit`` caps the result count; the snapshot reports how many
        matches were hidden by the cap.
        """
        ...

    @abc.abstractmethod
    def tool_sessions(
        self, connection_id: ConnectionId, tool: ToolId
    ) -> set[SessionId]:
        """Set of session ids currently bound to ``(connection_id, tool_id)``.

        Returns an empty set when the tool is not registered. Mainly used
        by tests to assert per-tool session set invariants without leaning
        on the reverse index.
        """
        ...

    @abc.abstractmethod
    def list_servers_for_user(self, user_id: UserId) -> list[ServerRecord]:
        """All servers registered by this user across all connections."""
        ...

    @abc.abstractmethod
    def get_server_record(
        self, connection_id: ConnectionId
    ) -> ServerRecord | None:
        """Look up a server by its connection ID."""
        ...

    # ------------------------------------------------------------------
    # Concrete default (Rust trait method with a default body) — subclasses
    # MAY override to avoid deep-cloning the whole record.
    # ------------------------------------------------------------------

    def get_server_id(self, connection_id: ConnectionId) -> ServerId | None:
        """Look up only a server's id by its connection ID.

        Lighter than :meth:`get_server_record` for callers that need
        nothing else: implementations should override the default to avoid
        deep-cloning the whole record (notably its ``metadata`` JSON).
        """
        record = self.get_server_record(connection_id)
        if record is None:
            return None
        return record.server_id


# ----------------------------------------------------------------------
# Process-global hybrid logical clock (Rust ``static REGISTRATION_CLOCK``
# AtomicU64 + ``next_registration_seq`` CAS loop).
# ----------------------------------------------------------------------

_registration_clock: int = 0
_registration_clock_lock = threading.Lock()


def next_registration_seq() -> int:
    """Issue the next monotonic registration stamp.

    Per-process strictly-increasing (no ties, immune to wall-clock
    step-back) and epoch-seeded so stamps also roughly order across
    replicas — only while inter-replica clock skew stays within the revive
    window (``tool_route_ttl_ms``); past that, TTL eviction, not seq order,
    is the backstop. The recency key for bind newest-wins and
    strictly-older eviction.

    The Rust original is an ``AtomicU64`` compare-exchange loop; Python
    serialises the read-modify-write under :data:`_registration_clock_lock`
    so the compound ``max(candidate, prev + 1)`` step is atomic across
    threads (the GIL serialises bytecode but the lock makes the RMW
    explicit). ``candidate = now_ms << 10`` keeps the epoch millisecond in
    the high bits and leaves the low 10 bits for the per-millisecond tie
    breaker.
    """
    global _registration_clock
    now_ms = time.time_ns() // 1_000_000
    candidate = now_ms << 10
    with _registration_clock_lock:
        prev = _registration_clock
        nxt = max(candidate, prev + 1)
        _registration_clock = nxt
    return nxt
