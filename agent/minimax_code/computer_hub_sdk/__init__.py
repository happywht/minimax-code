"""Computer Hub SDK -- client-side connection pool, transparent reconnect,
tool harness and tool-server runtime (R133+).

Fusion of grok-build's ``xai-computer-hub-sdk`` crate. The crate is the
client-facing surface of the computer-hub protocol: it owns the
:class:`~minimax_code.computer_hub_core` connection pool, the transparent
reconnect / replay machinery, the harness-side tool dispatch surface, and the
tool-server runtime that tool authors embed. It sits downstream of five
already-landed crates -- :mod:`minimax_code.tool_protocol` (R82-R106),
:mod:`minimax_code.tool_runtime` (R107-R114), :mod:`minimax_code.tool_types`
(R65), :mod:`minimax_code.computer_hub_core` (R115-R126) and
:mod:`minimax_code.tracing` (R127-R132) -- so its landing is the last contract
layer before the SDK's runtime leaves (pool / connection / harness / server)
wire up.

Why a separate package
----------------------

The Rust workspace keeps ``xai-computer-hub-sdk`` distinct from
``xai-computer-hub-core``: ``core`` holds the transport-agnostic contracts
(Principal / Transport / ToolRegistry / RemoteToolProxy / RemoteTransport),
while ``sdk`` adds the client runtime that *uses* those contracts --
connection pooling, WebSocket lifecycle, transparent reconnect, and the
harness + tool-server dispatch loops. Python mirrors that split:
:mod:`minimax_code.computer_hub_core` (R115-R126) is the contract layer; this
package is the runtime layer that lands over the coming rounds.

Leaf order
----------

The crate's ``lib.rs`` re-exports nineteen modules (fifteen ``pub mod`` +
four ``pub(crate) mod``). The dependency order -- small-to-large,
contract-before-runtime -- is:

1. ``error`` (R133) -- :class:`ClientError`, the type-contract root. The
   ``lib.rs`` ``pub use error::ClientError`` line is the first symbol every
   SDK consumer reaches, and every runtime leaf (connection / harness /
   server) propagates ``ClientError`` through ``?``. Landing it first pins
   the SDK's error vocabulary before any runtime code emits it.

   Subsequent leaves (handshake / refcount / donate_pump / trace_donate /
   connection_borrow / auth / observability / cancel / admission / pool /
   notification / oidc_provider / metric_donate / metrics / log_donate /
   demux / connection / server / harness) land over the following rounds in
   dependency order; this ``__init__`` grows as each lands, with a final
   barrel-reconciliation round mirroring ``lib.rs`` once every leaf is in.

Two-error-layer note
--------------------

The SDK's :class:`ClientError` (this package) and ``core``'s
:func:`~minimax_code.computer_hub_core.error_from_envelope` /
:func:`~minimax_code.computer_hub_core.is_workspace_unavailable` (acting on
``ToolError``) are *two distinct error layers*: ``ClientError`` is the
boundary taxonomy consumers switch on; ``ToolError`` is the runtime
classification the harness re-provision loop keys on. ``lib.rs`` re-exports
:func:`is_workspace_unavailable` so SDK-only consumers need not depend on
``computer_hub_core`` directly; this barrel mirrors that re-export.
"""

from minimax_code.computer_hub_core import is_workspace_unavailable
from minimax_code.computer_hub_sdk.error import (
    AuthError,
    BackpressureError,
    CallIdInUse,
    ClientError,
    Closed,
    HandshakeAuthFailed,
    InsecureScheme,
    InvalidConfig,
    NetworkError,
    ProtocolError,
    RegistrationConflict,
    SerdeError,
    Wire,
)

__all__ = [
    "AuthError",
    "BackpressureError",
    "CallIdInUse",
    "ClientError",
    "Closed",
    "HandshakeAuthFailed",
    "InsecureScheme",
    "InvalidConfig",
    "NetworkError",
    "ProtocolError",
    "RegistrationConflict",
    "SerdeError",
    "Wire",
    "is_workspace_unavailable",
]

#: Crate completion ledger -- updated as each leaf lands.
#: Landed: error (R133).
