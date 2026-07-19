"""Computer hub core — transport-routing + tool registry core (R115+).

Fusion of grok-build's ``xai-computer-hub-core`` crate. The crate sits
between the tool *runtime* (:mod:`minimax_code.tool_runtime`, the
execution contract) and the *router* (the agent-orchestration layer that
decides which tool runs where): it owns the object-safe
:class:`Transport` abstraction (local vs remote dispatch), the
:class:`Principal` identity value, the ``ToolRegistry`` session-binding
state machine, the ``CompoundResolver`` tool-resolution strategy, and the
``RemoteToolProxy`` connection-forwarding layer.

Why a separate package
----------------------

The Rust workspace keeps ``xai-tool-runtime`` (the execution contract)
and ``xai-computer-hub-core`` (the routing contract) as distinct crates:
the runtime types are stable and leaf-shaped, while the hub types compose
them into routing strategies. The Python landing mirrors that split —
:mod:`minimax_code.tool_runtime` (R107-R114, all eight ``src`` modules
landed) is the execution contract;
:mod:`minimax_code.computer_hub_core` (R115 onward) is the routing
contract. Keeping them separate means a router can evolve (new transport
kinds, new resolver strategies) without churning the runtime contract.

Leaf order
----------

The crate's ``lib.rs`` re-exports six modules. The dependency order is:

1. ``transport`` (R115) — :class:`Principal` + :class:`Transport` ABC +
   :class:`TransportKind` re-export. Foundation: every other leaf threads
   a :class:`Transport` in its signatures.
2. ``registry`` (later) — ``ToolRegistry`` session bind/unbind state
   machine + ``ConnectionCleanupReport`` / ``SessionCleanupReport``.
3. ``resolver`` (later) — ``CompoundResolver`` / ``ErasedTool`` /
   ``ResolvedTool`` / ``ToolHandle``.
4. ``inner`` (later) — ``InnerDispatchForResolver`` (the dispatch adapter
   a resolver exposes to the runtime).
5. ``local`` (later) — ``LocalTransport`` + ``LOCAL_INVOKE_SCOPE``.
6. ``remote`` (later) — ``RemoteTransport`` / ``RemoteToolProxy`` /
   ``ConnectionClient`` + the wire decode helpers
   (``decode_call_result`` / ``error_from_envelope`` /
   ``is_workspace_unavailable`` / ``output_to_value`` /
   ``progress_from_frame`` / ``tool_error_from_wire``).

R115 lands leaf 1 only; this barrel will grow one module per round.
"""

from minimax_code.computer_hub_core.transport import (
    Principal,
    Transport,
    TransportKind,
)

__all__ = ["Principal", "Transport", "TransportKind"]
