"""Grok auth dependency-inversion seam (R187+).

Fusion of grok-build's ``xai-grok-auth`` crate (411 lines / 4 files). The
crate provides the auth abstraction layer between ``xai-file-utils`` (the
outbound-request holder) and ``xai-grok-shell`` (the credential
implementer): an :class:`HttpAuth` stamps headers per request, and an
:class:`AuthCredentialProvider` adds refresh-aware snapshotting + 401
recovery. The holder depends on the trait, never on the implementer's
concrete types.

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
``retry_middleware`` / ``visibility``). The dependency order is:

1. ``visibility`` (R187) -- :class:`HttpAuth`, the narrowest seam. Landed
   first so the supertrait exists before :class:`AuthCredentialProvider`
   extends it.
2. ``auth_provider`` (R187) -- :class:`CredentialSnapshot` +
   :class:`AuthCredentialProvider` (supertrait of :class:`HttpAuth`) +
   :class:`StaticAuthCredentialProvider` (test / headless default). Lands
   in the same round: the three form one cohesive contract unit (the
   static provider implements both the trait and the supertrait, so
   landing it without the supertrait would leave :class:`HttpAuth`
   without a reference implementation).
3. ``retry_middleware`` (deferred) -- ``AuthRetryMiddleware``, the
   ``reqwest-middleware`` layer that stamps headers + retries on 401.
   Gated behind the ``middleware`` cargo feature in Rust; the Python
   equivalent (an httpx event-hook middleware) lands in a later round
   once the platform's HTTP middleware story is pinned. Until then the
   barrel exposes only the always-on contract surface.

Barrel surface (R187)
---------------------

Mirrors ``lib.rs``'s always-on ``pub use`` lines (4 symbols):

* ``pub use auth_provider::{AuthCredentialProvider, CredentialSnapshot,
  StaticAuthCredentialProvider};`` (3)
* ``pub use visibility::HttpAuth;`` (1)

The feature-gated ``pub use retry_middleware::AuthRetryMiddleware`` is
deferred until the middleware leaf lands.
"""

from minimax_code.grok_auth.auth_provider import (
    AuthCredentialProvider,
    CredentialSnapshot,
    StaticAuthCredentialProvider,
)
from minimax_code.grok_auth.visibility import HttpAuth

__all__ = [
    # auth_provider.rs barrel (R187) -- 3 lib.rs pub use symbols.
    "AuthCredentialProvider",
    "CredentialSnapshot",
    "StaticAuthCredentialProvider",
    # visibility.rs barrel (R187) -- the HttpAuth seam (1 lib.rs pub use symbol).
    "HttpAuth",
]

#: Barrel reconciliation note (R187)
#: --------------------------------
#: Mirrors grok-build ``xai-grok-auth/src/lib.rs``'s two always-on ``pub use``
#: lines (auth_provider 3 + visibility 1 = 4 symbols). The third lib.rs line
#: (``#[cfg(feature = "middleware")] pub use retry_middleware::AuthRetryMiddleware``)
#: is feature-gated in Rust behind ``middleware`` (reqwest-middleware + http);
#: its Python equivalent (an httpx event-hook middleware) is deferred to a
#: later round. When it lands, ``AuthRetryMiddleware`` joins ``__all__`` and the
#: count rises from 4 to 5.
#:
#: Crate completion ledger -- updated as each leaf lands.
#: Landed: visibility HttpAuth (R187), auth_provider CredentialSnapshot +
#: AuthCredentialProvider + StaticAuthCredentialProvider (R187). Pending:
#: retry_middleware AuthRetryMiddleware (feature-gated, deferred).
