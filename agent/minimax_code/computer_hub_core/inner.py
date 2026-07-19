"""Resolver-backed inner-dispatch adapter (R118).

Fusion of grok-build's ``xai-computer-hub-core/src/inner.rs``. The single
symbol :class:`InnerDispatchForResolver` is an object-safe
:class:`~minimax_code.tool_runtime.dispatch.ToolDispatch` that routes
inner tool calls through a
:class:`~minimax_code.computer_hub_core.resolver.CompoundResolver` bound
to one session.

Why inner lands fourth
----------------------

R115 landed transport, R116 registry, R117 resolver. This round lands the
*inner-dispatch* leaf — the adapter a tool uses to call *another* tool
through the same resolver that resolved it. It is the first concrete
implementation of R113's :class:`ToolDispatch` ABC, and the consumer that
closes the loop "resolver resolves a tool -> that tool's inner calls
re-enter the resolver". Holding the resolver by weak reference means an
inner-dispatch handle handed to a long-lived tool never anchors the
router: when the owning router drops the resolver, in-flight inner calls
fail cleanly with ``ToolError.custom("computer_hub_dropped")`` rather
than dangling.

Weak<CompoundResolver> -> weakref.ref
-------------------------------------

The Rust adapter holds the resolver by ``Weak<CompoundResolver>`` so an
inner-dispatch handle never keeps the router alive past its natural
lifetime. Python's :func:`weakref.ref` is the faithful landing:

- Rust ``Arc::downgrade(&resolver)`` (caller-side weak construction) maps
  to the caller passing ``weakref.ref(resolver)`` into the adapter.
- Rust ``Weak::upgrade()`` (returns ``Option<Arc<T>>`` — ``None`` once the
  referent is dropped) maps to calling the ref: ``self.resolver()``
  returns the live :class:`CompoundResolver` or ``None`` once it has been
  garbage-collected.
- The ``None`` arm is the "computer hub dropped" path: the adapter
  short-circuits to ``terminal_only(ToolError.custom(...))`` exactly as
  Rust returns ``terminal_only(Err(ToolError::custom(...)))``.

This is the first landing in the fusion that exercises Python's weak-ref
machinery as a deliberate counterpart to a Rust ``Weak<T>`` ownership
discipline. R117's :class:`CompoundResolver` carries no ``__slots__``, so
it has the default ``__weakref__`` slot and is weakly referenceable — the
runtime precondition for this mapping.

Debug-only derive -> eq=False
-----------------------------

Rust derives ``Debug`` but NOT ``PartialEq``/``Clone`` (a ``Weak`` is not
``Eq``, and the bound session id alone does not characterise the adapter
for structural equality). The Python landing keeps
:func:`dataclasses.dataclass` with ``eq=False`` (identity-only
``__eq__``), matching the R117 pattern for ``ResolvedTool`` /
``CompoundResolver`` and the wider crate convention for
trait-object-bearing value types.

Mapping-1 coroutine shape
-------------------------

:meth:`ToolDispatch.call` is mapping 1 (``async def -> AsyncIterator``,
``await``-resolved then ``async for``-driven), identical to R113's
:class:`ToolDispatch` and R115's :class:`Transport`. So
:meth:`InnerDispatchForResolver.call` ``return``-s an async iterator on
both arms — ``return terminal_only(...)`` for the dead-resolver path and
``return await resolver.resolve_and_dispatch(...)`` for the live path —
rather than ``yield``-ing directly.
"""

from __future__ import annotations

import weakref
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from minimax_code.computer_hub_core.resolver import CompoundResolver
from minimax_code.tool_protocol import SessionId, ToolId
from minimax_code.tool_runtime import (
    ToolCallContext,
    ToolError,
    ToolStreamItem,
    TypedToolOutput,
    terminal_only,
)
from minimax_code.tool_runtime.dispatch import ToolDispatch

__all__ = ["InnerDispatchForResolver"]


@dataclass(eq=False)
class InnerDispatchForResolver(ToolDispatch):
    """Resolver-backed :class:`ToolDispatch` bound to one session (R118).

    Tools that need to call other tools (the inner-dispatch pattern) ask
    the runtime for a dispatcher; this adapter answers with a
    resolver-backed implementation. The resolver is held by
    :func:`weakref.ref` so the handle never anchors the router — when the
    owning router drops the resolver, in-flight inner calls fail cleanly
    with :meth:`ToolError.custom` keyed ``computer_hub_dropped``.

    Bound to a single :class:`~minimax_code.tool_protocol.SessionId` at
    construction (rather than reading a session from the
    :class:`~minimax_code.tool_runtime.ToolCallContext`) so the
    inner-dispatch path mirrors the per-session lifetime of the outer
    router: an inner call always re-enters the resolver under the *same*
    session that the outer router owns.

    Construct with the resolver wrapped in :func:`weakref.ref`::

        handle = InnerDispatchForResolver(weakref.ref(resolver), session_id)
    """

    resolver: weakref.ref[CompoundResolver]
    session_id: SessionId

    async def call(
        self,
        tool_id: ToolId,
        args: Any,
        ctx: ToolCallContext,
    ) -> AsyncIterator[ToolStreamItem[TypedToolOutput]]:
        """Resolve ``tool_id`` through the bound resolver (Rust ``call``).

        Two arms:

        - **Dead resolver** (the weak ref has gone ``None``): short-circuit
          to a single Terminal carrying
          ``ToolError.custom("computer_hub_dropped", ...)`` — the inner
          call cannot proceed because the router that owned the resolver
          has been torn down.
        - **Live resolver**: delegate to
          :meth:`CompoundResolver.resolve_and_dispatch`, threading the
          bound ``self.session_id`` (NOT any session derivable from
          ``ctx``) so inner calls re-enter the same per-session resolution
          path as the outer router.

        Both arms ``return`` an async iterator (mapping 1), matching
        R113's :class:`ToolDispatch.call` contract.
        """
        resolver = self.resolver()
        if resolver is None:
            return terminal_only(
                ToolError.custom(
                    "computer_hub_dropped",
                    "computer hub dropped before inner call could execute",
                )
            )
        return await resolver.resolve_and_dispatch(self.session_id, tool_id, args, ctx)
