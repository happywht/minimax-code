"""In-process transport bound to a CompoundResolver (R119).

Fusion of grok-build's ``xai-computer-hub-core/src/local.rs``. Two
symbols land: the :data:`LOCAL_INVOKE_SCOPE` constant (the scope every
:class:`LocalTransport` grants its principal) and :class:`LocalTransport`
itself — the in-process :class:`Transport` that authorises a bound
``(user_id, session_id)`` and dispatches through a
:class:`~minimax_code.computer_hub_core.resolver.CompoundResolver`.

Why local lands fifth
---------------------

R115 landed transport (the ABC), R116 registry, R117 resolver, R118 the
inner-dispatch adapter. This round lands the *local transport* leaf — the
**first concrete** :class:`Transport` implementation in the crate. It is
the simpler of the two transport flavours (the remote flavour, which
forwards a tool-call request over a connection, lands in a later round):
a local transport authorises from the bound OS user and dispatches by
resolving the tool against the bound session's view of a single
in-process resolver. No network, no credential validation, no connection
lifecycle — the principal is pre-populated and dispatch is a single
delegation.

Arc<CompoundResolver> -> strong Python reference
------------------------------------------------

The Rust struct holds the resolver by ``Arc<CompoundResolver>`` — a
strong, shared-ownership handle. This is the deliberate counterpart to
R118's :class:`~minimax_code.computer_hub_core.InnerDispatchForResolver`,
which held a ``Weak<CompoundResolver>`` precisely so an inner-dispatch
handle handed to a long-lived tool would NOT anchor the router. A local
transport lives exactly as long as the resolver: the router owns both,
drops both together, and there is no scenario where the transport
outlives the resolver it dispatches through. So the Python landing holds
the resolver by a plain strong reference (``resolver: CompoundResolver``);
Python's reference semantics make the ``Arc`` a no-op, and the strong vs
weak contrast between R118 and R119 faithfully preserves Rust's ownership
discipline (``Arc<T>`` shared ownership vs ``Weak<T>`` non-owning).

Debug-only derive -> eq=False
-----------------------------

Rust derives ``Debug`` but NOT ``PartialEq``/``Clone`` (a
:class:`CompoundResolver` is not ``Eq`` — R117 lands it ``eq=False`` — so
the transport cannot be structurally equal either). The Python landing
keeps :func:`dataclasses.dataclass` with ``eq=False`` (identity-only
``__eq__``), matching the R116 ``ServerRecord`` / R117 ``ResolvedTool`` &
``CompoundResolver`` / R118 ``InnerDispatchForResolver`` pattern and the
wider crate convention for trait-object-bearing value types.

Mapping-1 coroutine shape + sync kind
-------------------------------------

:meth:`Transport.kind` is a *sync* method (it returns a
:class:`TransportKind` discriminant, no I/O); :meth:`authorize` and
:meth:`call` are mapping-1 coroutines (``async def -> value``, returned
and ``await``-ed). So :meth:`authorize` ``return``-s the built
:class:`Principal` (the local path never fails — there is no credential
to validate — so it always takes Rust's ``Ok`` arm and the
``Principal | ToolError`` return type narrows to ``Principal`` in
practice) and :meth:`call` ``return``-s ``await
resolver.resolve_and_dispatch(...)``. The :class:`Principal` is built
with the chain ``Principal.new(uid).with_session(sid).with_scope(scope)``
— an identical call-site to Rust's
``Principal::new(u).with_session(s).with_scope(sc)`` because R115's
builders mutate-and-return.

LOCAL_INVOKE_SCOPE is hoisted (a module-level constant, not a struct
field) so other code paths that authorise a principal through the local
convention can import and match it without restating the literal — the
same rationale as the Rust ``pub const``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from minimax_code.computer_hub_core.resolver import CompoundResolver
from minimax_code.computer_hub_core.transport import Principal, Transport
from minimax_code.tool_protocol import SessionId, ToolId, TransportKind, UserId
from minimax_code.tool_runtime import ToolCallContext, ToolError, ToolStream

__all__ = ["LOCAL_INVOKE_SCOPE", "LocalTransport"]

#: The scope :meth:`LocalTransport.authorize` grants to its principal.
#:
#: Hoisted (module-level, not a struct field) so adapters that authorise
#: principals through other paths can match the local convention without
#: restating the literal — the same rationale as Rust's ``pub const``.
LOCAL_INVOKE_SCOPE = "tool.invoke"


@dataclass(eq=False)
class LocalTransport(Transport):
    """In-process :class:`Transport` bound to ``(user_id, session_id)`` (R119).

    The transport authorises once at construction time — the bound user
    identity becomes a :class:`Principal` pre-populated with the bound
    session and the :data:`LOCAL_INVOKE_SCOPE` — and every subsequent
    :meth:`call` resolves ``tool_id`` against the bound session's view of
    the resolver. No network, no credential validation: this is the
    in-process flavour of :class:`Transport`, the simpler sibling of the
    remote forwarding transport (which lands in a later round).

    Construct with the resolver as a strong reference::

        transport = LocalTransport(resolver, user_id, session_id)

    The resolver is held strongly (not weakly): the transport and the
    resolver share one lifetime — the router owns both and drops both
    together. This is the deliberate counterpart to R118's
    :class:`~minimax_code.computer_hub_core.InnerDispatchForResolver`,
    which held a weak ref so an inner-dispatch handle never anchored the
    router.
    """

    resolver: CompoundResolver
    user_id: UserId
    session_id: SessionId

    def kind(self) -> TransportKind:
        """Whether the transport is local (in-process) or remote (forwarded).

        Local transports are always :attr:`TransportKind.Local` (Rust
        ``TransportKind::Local``) — a sync discriminant, no I/O.
        """
        return TransportKind.Local

    async def authorize(self) -> Principal | ToolError:
        """One-time authorisation handshake (Rust ``async fn authorize``).

        Returns a :class:`Principal` pre-populated with the bound user, the
        bound session, and the :data:`LOCAL_INVOKE_SCOPE`. The local path
        never fails (there is no credential to validate, no network to
        drop), so it always takes Rust's ``Ok`` arm — the
        ``Principal | ToolError`` return type narrows to ``Principal`` in
        practice, but the annotation keeps the R115
        :meth:`Transport.authorize` contract faithful.
        """
        return (
            Principal.new(self.user_id)
            .with_session(self.session_id)
            .with_scope(LOCAL_INVOKE_SCOPE)
        )

    async def call(
        self,
        tool_id: ToolId,
        args: Any,
        ctx: ToolCallContext,
    ) -> ToolStream:
        """Resolve ``tool_id`` through the bound resolver (Rust ``call``).

        Delegates to :meth:`CompoundResolver.resolve_and_dispatch`,
        threading the bound ``self.session_id`` (NOT any session derivable
        from ``ctx``) so a local dispatch always re-enters the resolver
        under the session this transport owns — mirroring the per-session
        lifetime of R118's inner-dispatch path.
        """
        return await self.resolver.resolve_and_dispatch(
            self.session_id, tool_id, args, ctx
        )
