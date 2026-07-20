"""Grok auth dependency-inversion seam (R187-R189, crate complete).

Fusion of grok-build's ``xai-grok-auth`` crate (411 lines / 4 files). The
crate provides the auth abstraction layer between ``xai-file-utils`` (the
outbound-request holder) and ``xai-grok-shell`` (the credential
implementer): an :class:`HttpAuth` stamps headers per request, an
:class:`AuthCredentialProvider` adds refresh-aware snapshotting + 401
recovery, and :class:`AuthRetryMiddleware` wires both into a client-agnostic
retry orchestrator. The holder depends on the trait, never on the
implementer's concrete types.

Layer separation vs the SDK auth crate
--------------------------------------

:mod:`minimax_code.computer_hub_sdk.auth` (R139) is **connection-pool
level**: the credential attached at WebSocket upgrade + its principal-key
projection for pool dedup (connection lifetime). This crate is **request
level**: header stamping + per-request refresh (request lifetime). The two
are orthogonal layers.

Architecture::

    data-collector (holder)
        |-- holds HttpAuth / AuthCredentialProvider
        v
    grok_auth (this crate) -- trait boundary
        ^
        |-- installs ShellAuthCredentialProvider wrapping AuthManager
    xai-grok-shell (implementer)

Leaf order
----------

The crate's ``lib.rs`` declares three modules (``auth_provider`` /
``retry_middleware`` / ``visibility``). Migration order (all landed):

1. ``visibility`` (R187) -- :class:`HttpAuth`, the narrowest seam. Landed
   first so the supertrait exists before :class:`AuthCredentialProvider`
   extends it.
2. ``auth_provider`` (R187) -- :class:`CredentialSnapshot` +
   :class:`AuthCredentialProvider` (supertrait of :class:`HttpAuth`) +
   :class:`StaticAuthCredentialProvider` (test / headless default). Landed
   alongside ``visibility``: the three form one cohesive contract unit (the
   static provider implements both the trait and the supertrait).
3. ``retry_middleware`` (R188) -- :class:`AuthRetryMiddleware`, the
   client-agnostic retry orchestrator (stamps headers + retries on 401 via a
   ``send``-callback seam). Rust gates it behind the ``middleware`` cargo
   feature (reqwest-middleware); the platform exposes it always-on (no
   Python feature-flag mechanism, and the ``send``-callback design avoids
   binding httpx transport plumbing).

Barrel surface (R189 reconciliation)
------------------------------------

Mirrors ``lib.rs``'s full ``pub use`` surface (5 symbols, all always-on
here):

* ``pub use auth_provider::{AuthCredentialProvider, CredentialSnapshot,
  StaticAuthCredentialProvider};`` (3)
* ``pub use visibility::HttpAuth;`` (1)
* ``#[cfg(feature = "middleware")] pub use
  retry_middleware::AuthRetryMiddleware;`` (1) -- Rust feature-gates this
  behind ``middleware``; the platform exposes it always-on (see leaf 3).
"""

from minimax_code.grok_auth.auth_provider import (
    AuthCredentialProvider,
    CredentialSnapshot,
    StaticAuthCredentialProvider,
)
from minimax_code.grok_auth.retry_middleware import AuthRetryMiddleware
from minimax_code.grok_auth.visibility import HttpAuth

__all__ = [
    "AuthCredentialProvider",
    "AuthRetryMiddleware",
    "CredentialSnapshot",
    "HttpAuth",
    "StaticAuthCredentialProvider",
]

#: Crate completion ledger (R189 reconciliation)
#: ---------------------------------------------
#: grok-build ``xai-grok-auth/src/lib.rs`` surface fully mirrored:
#:   mod declarations: 3/3 (visibility R187, auth_provider R187,
#:       retry_middleware R188).
#:   pub use symbols:  5/5 (auth_provider 3 + visibility 1 + retry 1).
#: Feature parity: Rust gates retry_middleware behind the ``middleware`` cargo
#: feature (reqwest-middleware + http deps); the platform exposes it
#: always-on -- no Python feature-flag mechanism, and the R188 send-callback
#: seam avoids binding httpx transport plumbing, so there is no cost to
#: always exposing it. Crate COMPLETE (R187-R189).
