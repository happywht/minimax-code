"""Object-safe ``Transport`` trait plus the ``Principal`` identity value (R115).

Fusion of grok-build's ``xai-computer-hub-core/src/transport.rs``. A
:class:`Transport` is the object-safe abstraction every router build
authorises against and dispatches through: implementations come in two
flavours — local (resolves against an in-process registry) and remote
(forwards a tool-call request over a connection). This module is the
**first leaf** of the ``xai-computer-hub-core`` crate; the registry /
resolver / inner / local / remote leaves land in later rounds.

Why transport lands first
-------------------------

``transport`` is the crate's foundation leaf: :class:`Transport` is the
trait every other module's signatures thread (``local`` / ``remote``
implement it, ``resolver`` resolves against it, ``inner`` dispatches
through it). Its only dependencies are already-landed leaves — the
protocol id newtypes (:class:`SessionId` / :class:`UserId` /
:class:`ToolId`, R82) + :class:`TransportKind` (R87, re-exported here so
the wire and dispatch layers share one canonical enum) on the protocol
side, and :class:`ToolCallContext` (R108) + :class:`ToolError` (R107) +
:type:`ToolStream` (R109) on the runtime side.

trait Transport -> abc.ABC
--------------------------

Rust's ``#[async_trait] pub trait Transport: Send + Sync + Debug``
declares three abstract methods (``kind`` / ``authorize`` / ``call``)
with no default bodies. :class:`abc.ABC` is the faithful landing: three
:func:`abc.abstractmethod`-marked methods, subclasses MUST implement all
three to be instantiable. This mirrors the R113 :class:`ToolDispatch`
landing (object-safe trait -> ABC) and is deliberately distinct from the
*structural* contracts (``Tool`` / ``ToolDyn`` / ``ToolFamily`` in R109,
``ToolSearchIndex`` in R112, ``ToolRegistry`` in a later round) which stay
:class:`typing.Protocol` because they are duck-typed seams.

The ``Send + Sync`` object-safety bounds (which let the trait live behind
an ``Arc<dyn Transport>`` shared across tasks) have no Python equivalent
under the GIL — Python objects are already reference-shared. The
``Debug`` bound is a soft expectation: implementations SHOULD provide a
``__repr__`` but the ABC does not enforce it (Python has no trait-bounds
mechanism).

Result<T, E> by value, not raised
---------------------------------

``authorize`` returns ``Result<Principal, ToolError>`` by value. Per R107,
:class:`ToolError` does NOT subclass :exc:`Exception`: it is returned,
never raised. So ``authorize`` returns ``Principal | ToolError`` — the
caller distinguishes with ``isinstance(result, ToolError)`` — rather than
``raise``-ing the error path. This matches the Rust ``Result`` and the
R113 :meth:`ToolDispatch.call_terminal` drain contract.

Principal builder pattern
-------------------------

:class:`Principal` is built via ``new`` + a chain of ``with_*`` builders.
Each ``with_*`` consumes ``self`` and returns it (Rust ``mut self`` move
semantics), so the Python landing mutates ``self`` in place and returns
it — the call site reads identically (``Principal::new(u).with_session(s).with_scope(sc)``).
The ``#[derive(Debug, Clone, PartialEq, Eq)]`` maps to a plain
:func:`dataclasses.dataclass` (default ``eq=True``): the id-newtype fields
(:class:`UserId`, :class:`SessionId`) are ``str`` subclasses with value
equality, so field-wise ``__eq__`` behaves like Rust's derived
``PartialEq`` / ``Eq``.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any

from minimax_code.tool_protocol import SessionId, ToolId, TransportKind, UserId
from minimax_code.tool_runtime import ToolCallContext, ToolError, ToolStream

__all__ = ["Principal", "Transport", "TransportKind"]


@dataclass
class Principal:
    """Authenticated identity bound to a transport at handshake time.

    The transport authorises **once** at connect; subsequent dispatch
    calls carry no extra credentials. ``session_ids`` is plural because a
    JWT may authorise more than one session (multi-tenant tooling sessions
    sharing a single user identity); the router narrows by
    :class:`SessionId` at the per-call boundary.

    Rust ``#[derive(Debug, Clone, PartialEq, Eq)]`` -> a plain
    :func:`dataclasses.dataclass` (default ``eq=True``); the id-newtype
    fields are ``str`` subclasses with value equality, so field-wise
    equality behaves like Rust's derived ``PartialEq`` / ``Eq``.
    """

    #: Authenticated user identity.
    user_id: UserId
    #: Sessions this principal is authorised to act on. Empty when the
    #: transport authorises a user but has not yet bound a session.
    session_ids: list[SessionId] = field(default_factory=list)
    #: OAuth-style scopes granted to this principal, e.g. ``"tool.invoke"``.
    scopes: list[str] = field(default_factory=list)
    #: Token audiences claimed by the credential, for defence-in-depth
    #: audience checks beyond what the transport already validated.
    audiences: list[str] = field(default_factory=list)

    @classmethod
    def new(cls, user_id: UserId) -> Principal:
        """Build a principal for ``user_id`` with no sessions / scopes / audiences.

        Use the :meth:`with_*` builders to populate the rest (Rust ``new``).
        """
        return cls(user_id=user_id)

    def with_session(self, session_id: SessionId) -> Principal:
        """Append ``session_id`` to the authorised set (Rust ``mut self``).

        Consumes and returns ``self`` so builders chain identically to Rust's
        move-and-return ``with_session(mut self, ...)``.
        """
        self.session_ids.append(session_id)
        return self

    def with_scope(self, scope: str) -> Principal:
        """Append ``scope`` to the granted scopes (Rust ``impl Into<String>`` -> ``str``)."""
        self.scopes.append(scope)
        return self

    def with_audience(self, aud: str) -> Principal:
        """Append ``aud`` to the token's audience list."""
        self.audiences.append(aud)
        return self

    def has_scope(self, scope: str) -> bool:
        """Whether ``scope`` is present in the granted scopes."""
        return scope in self.scopes

    def authorizes_session(self, session_id: SessionId) -> bool:
        """Whether ``session_id`` is in the principal's authorised session set."""
        return session_id in self.session_ids


class Transport(abc.ABC):
    """Object-safe transport for dispatching tool calls (Rust ``trait Transport``).

    Implementations come in two flavours: :attr:`TransportKind.Local`
    resolves against an in-process registry, while
    :attr:`TransportKind.Remote` forwards a tool-call request over a
    connection. Subclasses MUST implement :meth:`kind`, :meth:`authorize`,
    and :meth:`call`.

    The ``Send + Sync + Debug`` supertraits have no Python equivalent under
    the GIL (reference-shared by default); ``Debug`` is a soft expectation
    that implementations provide a ``__repr__``.
    """

    @abc.abstractmethod
    def kind(self) -> TransportKind:
        """Whether the transport is local (in-process) or remote (forwarded)."""
        ...

    @abc.abstractmethod
    async def authorize(self) -> Principal | ToolError:
        """One-time authorisation handshake (Rust ``async fn authorize``).

        Local transports return a principal derived from the bound OS user;
        remote transports return the principal extracted from a validated
        credential. Subsequent :meth:`call` invocations reuse this
        principal — the router never re-authorises per call.

        Returns :class:`Principal` on success or :class:`ToolError` by value
        (NOT raised — R107 convention); the caller discriminates with
        ``isinstance(result, ToolError)``.
        """
        ...

    @abc.abstractmethod
    async def call(
        self,
        tool_id: ToolId,
        args: Any,
        ctx: ToolCallContext,
    ) -> ToolStream:
        """Dispatch a tool call (Rust ``async fn call``).

        The returned :type:`ToolStream` follows the runtime invariant: zero
        or more ``Progress`` items followed by exactly one ``Terminal``. A
        not-found result is reported as a single-item terminal stream
        carrying ``ToolError::NotFound``; transport-level disconnects
        surface as ``ToolError::NetworkError``.

        ``args`` is the JSON value (Rust ``serde_json::Value`` -> ``Any``).
        The return type is Rust ``ToolStream<TypedToolOutput>``; the
        ``TypedToolOutput`` parameter is elided here because Python
        generics do not specialise :type:`ToolStream` at runtime.
        """
        ...
