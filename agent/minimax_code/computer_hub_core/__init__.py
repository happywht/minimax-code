"""Computer hub core — transport-routing + tool registry core (R115+).

Fusion of grok-build's ``xai-computer-hub-core`` crate. The crate sits
between the tool *runtime* (:mod:`minimax_code.tool_runtime`, the
execution contract) and the *router* (the agent-orchestration layer that
decides which tool runs where): it owns the object-safe
:class:`Transport` abstraction (local vs remote dispatch), the
:class:`Principal` identity value, the :class:`ToolRegistry`
session-binding state machine, the ``CompoundResolver`` tool-resolution
strategy, and the ``RemoteToolProxy`` connection-forwarding layer.

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
2. ``registry`` (R116) — :class:`ToolRegistry` ABC (object-safe trait;
   8 async mutating + 7 sync view + 1 concrete ``get_server_id``) +
   :class:`ToolSessionBindOutcome` / :class:`ToolSessionUnbindOutcome` +
   :class:`ConnectionCleanupReport` / :class:`SessionCleanupReport` +
   :class:`ServerRecord` + :func:`next_registration_seq` HLC.
3. ``resolver`` (R117) — :class:`CompoundResolver` (local-first,
   remote-fallback resolution + ``resolve_and_dispatch``) /
   :class:`ErasedTool` (the blanket ``impl<T: Tool> ToolHandle`` adapter
   that drives a typed Tool's stream and re-encodes each terminal item
   into a :class:`~minimax_code.tool_runtime.TypedToolOutput`) /
   :class:`ResolvedTool` (the value :meth:`ToolRegistry.find_tool`
   returns; closes the R116 ``registry`` <-> ``resolver`` cycle) /
   :class:`ToolHandle` (the object-safe dispatch surface a resolved tool
   exposes).
4. ``inner`` (R118) — :class:`InnerDispatchForResolver` (a concrete
   :class:`~minimax_code.tool_runtime.dispatch.ToolDispatch` bound to one
   session via a :func:`weakref.ref`-held :class:`CompoundResolver`; the
   first consumer of R113's :class:`ToolDispatch` ABC and the loop-closer
   "resolver resolves a tool -> that tool's inner calls re-enter the
   resolver").
5. ``local`` (R119) — :class:`LocalTransport` (the first concrete
   :class:`Transport`, an in-process transport that authorises a bound
   ``(user_id, session_id)`` and dispatches through a
   :class:`CompoundResolver`; holds the resolver by a strong Python
   reference — the ``Arc<T>`` counterpart to R118's ``Weak`` ->
   :func:`weakref.ref` mapping) + :data:`LOCAL_INVOKE_SCOPE`.
6. ``remote`` (R120+R121 layer 4, R122 layer 1) — ``RemoteTransport`` /
   ``RemoteToolProxy`` / ``ConnectionClient`` + the wire decode helpers.
   R120 lands the three pure success-path layer-4 seams
   (:func:`decode_call_result` / :func:`output_to_value` /
   :func:`progress_from_frame`) + the private :func:`_map_block`;
   R121 completes the layer-4 error-decode group
   (:func:`tool_error_from_wire` / :func:`error_from_envelope` /
   :func:`is_workspace_unavailable` + the private
   :func:`_terminal_from_response`); R122 opens layer 1
   (:class:`ConnectionClient` — the object-safe connection contract:
   ``request`` / ``subscribe_progress`` / ``notify``; drops the Rust
   ``Send + Sync + Debug`` bounds and maps ``BoxStream`` to
   :class:`~collections.abc.AsyncIterator`). Layers 2-3 (the
   ``RemoteToolProxy`` / ``RemoteTransport`` impls, the
   ``dispatch_via_connection`` + ``RequestStream`` async stream) land in
   later rounds.

R120+R121 land the full layer-4 decode/encode surface of leaf 6; R122
lands the layer-1 connection contract; the remaining connection
machinery (layers 2-3) lands one module per round.
"""

from minimax_code.computer_hub_core.inner import (
    InnerDispatchForResolver,
)
from minimax_code.computer_hub_core.local import (
    LOCAL_INVOKE_SCOPE,
    LocalTransport,
)
from minimax_code.computer_hub_core.registry import (
    ConnectionCleanupReport,
    ServerRecord,
    SessionCleanupReport,
    ToolRegistry,
    ToolSessionBindOutcome,
    ToolSessionUnbindOutcome,
    next_registration_seq,
)
from minimax_code.computer_hub_core.remote import (
    ConnectionClient,
    decode_call_result,
    error_from_envelope,
    is_workspace_unavailable,
    output_to_value,
    progress_from_frame,
    tool_error_from_wire,
)
from minimax_code.computer_hub_core.resolver import (
    CompoundResolver,
    ErasedTool,
    ResolvedTool,
    ToolHandle,
)
from minimax_code.computer_hub_core.transport import (
    Principal,
    Transport,
    TransportKind,
)

__all__ = [
    "CompoundResolver",
    "ConnectionCleanupReport",
    "ConnectionClient",
    "ErasedTool",
    "InnerDispatchForResolver",
    "LOCAL_INVOKE_SCOPE",
    "LocalTransport",
    "Principal",
    "ResolvedTool",
    "SessionCleanupReport",
    "ServerRecord",
    "ToolHandle",
    "ToolRegistry",
    "ToolSessionBindOutcome",
    "ToolSessionUnbindOutcome",
    "Transport",
    "TransportKind",
    "decode_call_result",
    "error_from_envelope",
    "is_workspace_unavailable",
    "next_registration_seq",
    "output_to_value",
    "progress_from_frame",
    "tool_error_from_wire",
]
