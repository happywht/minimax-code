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
package is the runtime layer.

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
   demux / connection / harness / server) landed over R134-R177 in dependency
   order; R178 is the final barrel-reconciliation round that mirrors
   ``lib.rs``'s ``pub use`` surface now that every leaf is in (see the
   "Barrel reconciliation (R178)" note below).

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

Barrel reconciliation (R178)
----------------------------

R178 closes the crate by mirroring ``lib.rs``'s thirteen ``pub use`` lines.
Every re-exported symbol below has a ported, pure-logic Python equivalent in
this package and is unit-tested at its leaf; the barrel lifts them to the
package root so SDK consumers need not reach into submodules. (Safe by
construction: no submodule performs a barrel-level ``from minimax_code
.computer_hub_sdk import ...``, so widening the barrel cannot introduce an
import cycle.)

Re-exported (41 symbols), grouped by ``lib.rs`` line:

* ``error::ClientError`` plus the twelve sibling variants the error leaf
  already surfaced (R133).
* ``auth::{AuthCredential, AuthIdentity, AuthProvider, PrincipalKey}`` (R139).
* ``connection::{ConnKey, HubConnection, ReconnectEvent}`` -- ``HubConnection``
  from :mod:`connection` (R150-R164); ``ConnKey`` / ``ReconnectEvent`` from
  :mod:`connection_types` (Rust co-locates them in ``connection.rs``; Python
  splits the actor from its type contract, so the barrel aggregates both).
* ``harness::{CancelOnDrop, LocalRegistry, ModelOutputExtractor,
  SessionBindReport, ToolHarness, ToolHarnessBuilder}`` (R165-R174).
* ``log_donate::{LogDonationLayer, LogDonationPump, LogDonationSender,
  flush_log_layer}`` -- the Rust ``DonatingLogLayer`` is named
  :class:`LogDonationLayer` in Python (the leaf chose the ``LogDonation``-
  prefixed form for parity with its ``LogDonationPump`` / ``LogDonationSender``
  siblings; the barrel honours that name rather than aliasing it back).
* ``metric_donate::MetricDonationPump`` (R146).
* ``notification::HubNotification`` (R144).
* ``observability::ObservabilityBridge`` (R140).
* ``oidc_provider::{OidcAuthProvider, OidcAuthProviderBuilder, OnRefreshCallback,
  RefreshEvent}`` (R145).
* ``pool::HubConnectionPool`` (R143).
* ``server::SystemNotifyAck`` (R175).
* ``trace_donate::TraceDonationPump`` (R137).
* ``xai_computer_hub_core::is_workspace_unavailable`` (cross-crate, R115-era).

YAGNI boundary -- ``lib.rs`` symbols intentionally *not* re-exported:

* ``auth::SharedAuthProvider`` -- Rust ``type SharedAuthProvider =
  Arc<dyn AuthProvider + Send + Sync>`` is a thread-safe trait-object alias.
  Python has no ``Arc`` / ``dyn`` / ``Send`` / ``Sync`` vocabulary, so the
  alias collapses: consumers hold an :class:`AuthProvider` directly (R139
  folded it on the same grounds as the server leaf's
  ``ReconnectSettledCallback``).
* ``harness::extractor_for`` -- a generic ``fn extractor_for<T>()`` over a
  phantom type parameter selecting a :class:`ModelOutputExtractor`. Python
  has no phantom generics; the leaf (R165 harness_types) documents it as
  deferred.
* ``trace_donate::HubDonatingReporter`` -- a ``fastrace::collector::Reporter``
  impl. Python carries no fastrace SDK, so the reporter stays deferred (the
  R137 leaf documents the boundary).
* ``server::{ResolvedSessionHandlers, SessionHandlerResolver, ToolServer,
  ToolServerBuilder, ToolServerHandler, WeakToolServer}`` -- the live
  ``ToolServer`` actor and its handler-resolver scaffolding are bound to the
  xAI ``HubConnection`` socket protocol; MiniMax has no such consumer, so the
  whole actor leaf stays deferred (R175-R177 ported only the pure-logic
  preamble: ``SystemNotifyAck`` plus the four conversion helpers).

The ``#[cfg(feature = "metrics")]`` gate on ``metric_donate::MetricDonationPump``
has no Python equivalent (no cargo feature flags); the symbol is always
available, matching the leaf's unconditional export.
"""

from minimax_code.computer_hub_core import is_workspace_unavailable
from minimax_code.computer_hub_sdk.auth import (
    AuthCredential,
    AuthIdentity,
    AuthProvider,
    PrincipalKey,
)
from minimax_code.computer_hub_sdk.connection import HubConnection
from minimax_code.computer_hub_sdk.connection_types import ConnKey, ReconnectEvent
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
from minimax_code.computer_hub_sdk.harness import LocalRegistry, ToolHarness
from minimax_code.computer_hub_sdk.harness_builder import ToolHarnessBuilder
from minimax_code.computer_hub_sdk.harness_types import (
    CancelOnDrop,
    ModelOutputExtractor,
    SessionBindReport,
)
from minimax_code.computer_hub_sdk.log_donate import (
    LogDonationLayer,
    LogDonationPump,
    LogDonationSender,
    flush_log_layer,
)
from minimax_code.computer_hub_sdk.metric_donate import MetricDonationPump
from minimax_code.computer_hub_sdk.notification import HubNotification
from minimax_code.computer_hub_sdk.observability import ObservabilityBridge
from minimax_code.computer_hub_sdk.oidc_provider import (
    OidcAuthProvider,
    OidcAuthProviderBuilder,
    OnRefreshCallback,
    RefreshEvent,
)
from minimax_code.computer_hub_sdk.pool import HubConnectionPool
from minimax_code.computer_hub_sdk.server import SystemNotifyAck
from minimax_code.computer_hub_sdk.trace_donate import TraceDonationPump

__all__ = [
    # error taxonomy (R133) -- the boundary every consumer switches on.
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
    # auth credentials + identity (R139).
    "AuthCredential",
    "AuthIdentity",
    "AuthProvider",
    "PrincipalKey",
    # connection handle + pool key + reconnect event (R150-R164).
    "HubConnection",
    "ConnKey",
    "ReconnectEvent",
    # harness dispatch surface (R165-R174).
    "LocalRegistry",
    "ToolHarness",
    "ToolHarnessBuilder",
    "ModelOutputExtractor",
    "CancelOnDrop",
    "SessionBindReport",
    # telemetry donation pumps + layers (R136 / R137 / R146 / R148).
    "LogDonationLayer",
    "LogDonationPump",
    "LogDonationSender",
    "flush_log_layer",
    "MetricDonationPump",
    "TraceDonationPump",
    # observability + notification facade (R140 / R144).
    "ObservabilityBridge",
    "HubNotification",
    # oidc auth provider (R145).
    "OidcAuthProvider",
    "OidcAuthProviderBuilder",
    "OnRefreshCallback",
    "RefreshEvent",
    # connection pool (R143) + server notify ack (R175).
    "HubConnectionPool",
    "SystemNotifyAck",
    # cross-crate re-export (mirrors lib.rs pub use xai_computer_hub_core::...).
    "is_workspace_unavailable",
]

#: Crate completion ledger -- updated as each leaf lands.
#: Landed: error (R133), handshake (R134), refcount (R135), donate_pump (R136),
#: trace_donate (R137), connection_borrow (R138), auth (R139), observability
#: (R140), cancel (R141), admission (R142), pool (R143), notification (R144),
#: oidc_provider (R145), metric_donate (R146), metrics (R147), log_donate
#: (R148), demux (R149), connection (R150-R164), harness (R165-R174), server
#: (R175-R177). Barrel reconciliation (R178) closes the crate: every ``lib.rs``
#: ``pub use`` symbol that has a ported, pure-logic Python equivalent is
#: re-exported above; live-actor and trait-object-only symbols stay deferred
#: (see the "Barrel reconciliation (R178)" note in the module docstring).
