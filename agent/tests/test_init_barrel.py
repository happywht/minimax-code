"""Barrel reconciliation tests for ``computer_hub_sdk.__init__`` (R178).

R178 mirrors grok-build ``xai-computer-hub-sdk/src/lib.rs``'s thirteen
``pub use`` lines at the package root. These tests pin three invariants:

1. **Coverage** -- the barrel exposes exactly the ported, pure-logic symbols
   (41), one assertion per ``lib.rs`` re-export group, no more, no less.
2. **Fidelity** -- each barrel symbol *is* the same object its leaf submodule
   exports (identity via ``is``), so the barrel cannot drift into a shadow or
   a stale re-binding.
3. **YAGNI boundary** -- the live-actor and trait-object-only ``lib.rs``
   symbols that have no Python equivalent stay absent from the barrel.

The identity checks are the load-bearing ones: a ``getattr``/count test only
proves *something* is bound to the name; the ``is`` check proves the barrel
honours the same single source the leaf established.
"""

from __future__ import annotations

import minimax_code.computer_hub_sdk as sdk
from minimax_code import computer_hub_core
from minimax_code.computer_hub_sdk import (
    auth,
    connection,
    connection_types,
    error,
    harness,
    harness_builder,
    harness_types,
    log_donate,
    metric_donate,
    notification,
    observability,
    oidc_provider,
    pool,
    server,
    trace_donate,
)

#: The complete barrel surface (41 symbols), grouped by lib.rs pub use line.
#: Asserted as a set so the test fails on *any* drift -- a dropped rename, an
#: accidental extra export, or a missing leaf all surface here.
EXPECTED_BARREL: set[str] = {
    # error taxonomy (R133) -- ClientError + 12 sibling variants.
    "AuthError", "BackpressureError", "CallIdInUse", "ClientError", "Closed",
    "HandshakeAuthFailed", "InsecureScheme", "InvalidConfig", "NetworkError",
    "ProtocolError", "RegistrationConflict", "SerdeError", "Wire",
    # auth credentials + identity (R139).
    "AuthCredential", "AuthIdentity", "AuthProvider", "PrincipalKey",
    # connection handle + pool key + reconnect event (R150-R164).
    "HubConnection", "ConnKey", "ReconnectEvent",
    # harness dispatch surface (R165-R174).
    "LocalRegistry", "ToolHarness", "ToolHarnessBuilder",
    "ModelOutputExtractor", "CancelOnDrop", "SessionBindReport",
    # telemetry donation pumps + layers (R136 / R137 / R146 / R148).
    "LogDonationLayer", "LogDonationPump", "LogDonationSender",
    "flush_log_layer", "MetricDonationPump", "TraceDonationPump",
    # observability + notification facade (R140 / R144).
    "ObservabilityBridge", "HubNotification",
    # oidc auth provider (R145).
    "OidcAuthProvider", "OidcAuthProviderBuilder", "OnRefreshCallback",
    "RefreshEvent",
    # connection pool (R143) + server notify ack (R175).
    "HubConnectionPool", "SystemNotifyAck",
    # cross-crate re-export (mirrors lib.rs pub use xai_computer_hub_core::...).
    "is_workspace_unavailable",
}

#: lib.rs pub use symbols intentionally *not* re-exported (YAGNI). Each has a
#: documented reason in the __init__ docstring: trait-object alias / phantom
#: generic / fastrace SDK / live ToolServer actor bound to the xAI socket.
YAGNI_ABSENT: set[str] = {
    "SharedAuthProvider",          # auth: Arc<dyn AuthProvider + Send + Sync>
    "extractor_for",               # harness: generic fn over phantom T
    "HubDonatingReporter",         # trace_donate: fastrace Reporter impl
    "ResolvedSessionHandlers",     # server: live actor scaffolding
    "SessionHandlerResolver",      # server: live actor scaffolding
    "ToolServer",                  # server: live actor
    "ToolServerBuilder",           # server: live actor builder
    "ToolServerHandler",           # server: live actor handler
    "WeakToolServer",              # server: live actor weak handle
}


def test_barrel_matches_expected_surface_exactly() -> None:
    """The package __all__ is exactly EXPECTED_BARREL -- no drift."""
    assert set(sdk.__all__) == EXPECTED_BARREL


def test_barrel_has_no_duplicates() -> None:
    """__all__ lists each export once (a duplicate would mask a rename bug)."""
    assert len(sdk.__all__) == len(set(sdk.__all__))


def test_barrel_count_is_41() -> None:
    """Sanity guard against a silent add/drop when EXPECTED_BARREL is edited."""
    assert len(sdk.__all__) == 41
    assert len(EXPECTED_BARREL) == 41


def test_every_export_resolves_on_the_package() -> None:
    """Each __all__ name is getattr-able on the package and is not None."""
    for name in sdk.__all__:
        assert hasattr(sdk, name), f"barrel lists {name!r} but package lacks it"
        assert getattr(sdk, name) is not None, f"{name!r} resolves to None"


# ---------------------------------------------------------------------------
# Fidelity: each barrel symbol *is* its leaf-submodule source (identity).
# One representative per lib.rs pub use line; the group-level identity implies
# the rest of the group came through the same import statement.
# ---------------------------------------------------------------------------

def test_error_taxonomy_identity() -> None:
    assert sdk.ClientError is error.ClientError
    assert sdk.SerdeError is error.SerdeError


def test_auth_surface_identity() -> None:
    assert sdk.AuthCredential is auth.AuthCredential
    assert sdk.AuthProvider is auth.AuthProvider
    assert sdk.PrincipalKey is auth.PrincipalKey


def test_connection_surface_identity() -> None:
    # Rust co-locates ConnKey/ReconnectEvent in connection.rs; Python splits
    # the actor (connection) from its type contract (connection_types), so the
    # barrel aggregates both -- each must still bind to its single source.
    assert sdk.HubConnection is connection.HubConnection
    assert sdk.ConnKey is connection_types.ConnKey
    assert sdk.ReconnectEvent is connection_types.ReconnectEvent


def test_harness_surface_identity() -> None:
    assert sdk.ToolHarness is harness.ToolHarness
    assert sdk.LocalRegistry is harness.LocalRegistry
    assert sdk.ToolHarnessBuilder is harness_builder.ToolHarnessBuilder
    assert sdk.ModelOutputExtractor is harness_types.ModelOutputExtractor
    assert sdk.CancelOnDrop is harness_types.CancelOnDrop
    assert sdk.SessionBindReport is harness_types.SessionBindReport


def test_log_donate_surface_identity() -> None:
    # Naming-fidelity guard: Rust's DonatingLogLayer is Python's
    # LogDonationLayer (the leaf chose the LogDonation- prefix for parity with
    # its LogDonationPump/LogDonationSender siblings).
    assert sdk.LogDonationLayer is log_donate.LogDonationLayer
    assert sdk.LogDonationPump is log_donate.LogDonationPump
    assert sdk.LogDonationSender is log_donate.LogDonationSender
    assert sdk.flush_log_layer is log_donate.flush_log_layer


def test_metric_donate_surface_identity() -> None:
    assert sdk.MetricDonationPump is metric_donate.MetricDonationPump


def test_notification_surface_identity() -> None:
    assert sdk.HubNotification is notification.HubNotification


def test_observability_surface_identity() -> None:
    assert sdk.ObservabilityBridge is observability.ObservabilityBridge


def test_oidc_surface_identity() -> None:
    assert sdk.OidcAuthProvider is oidc_provider.OidcAuthProvider
    assert sdk.OidcAuthProviderBuilder is oidc_provider.OidcAuthProviderBuilder
    assert sdk.OnRefreshCallback is oidc_provider.OnRefreshCallback
    assert sdk.RefreshEvent is oidc_provider.RefreshEvent


def test_pool_surface_identity() -> None:
    assert sdk.HubConnectionPool is pool.HubConnectionPool


def test_server_surface_identity() -> None:
    assert sdk.SystemNotifyAck is server.SystemNotifyAck


def test_trace_donate_surface_identity() -> None:
    assert sdk.TraceDonationPump is trace_donate.TraceDonationPump


def test_cross_crate_reexport_identity() -> None:
    """lib.rs re-exports is_workspace_unavailable from the core crate; the
    barrel binds the *same* function so SDK-only consumers skip the core dep."""
    assert sdk.is_workspace_unavailable is computer_hub_core.is_workspace_unavailable


# ---------------------------------------------------------------------------
# YAGNI boundary: live-actor + trait-object-only lib.rs symbols stay absent.
# ---------------------------------------------------------------------------

def test_yagni_symbols_absent_from_all() -> None:
    """None of the YAGNI-deferred lib.rs symbols leaked into __all__."""
    leaked = YAGNI_ABSENT & set(sdk.__all__)
    assert not leaked, f"YAGNI symbols leaked into barrel: {leaked}"


def test_yagni_symbols_not_bound_on_package() -> None:
    """The YAGNI symbols are not accidentally bound on the package root even
    outside __all__ (a stray import would re-expose them via getattr)."""
    for name in YAGNI_ABSENT:
        assert not hasattr(sdk, name), (
            f"YAGNI symbol {name!r} is bound on the package root; the "
            f"live-actor / trait-object boundary was breached"
        )
