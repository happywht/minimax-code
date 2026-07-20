"""OIDC token-refresh AuthProvider (R145).

Fusion of grok-build's ``xai-computer-hub-sdk/src/oidc_provider.rs`` (336
lines) -- the SDK crate's 13th leaf (after R133 error / R134 handshake /
R135 refcount / R136 donate_pump / R137 trace_donate / R138 connection_borrow
/ R139 auth / R140 observability / R141 cancel / R142 admission / R143 pool /
R144 notification).

An :class:`~minimax_code.computer_hub_sdk.auth.AuthProvider` (R139) that
re-mints its bearer token by performing OIDC discovery + a ``refresh_token``
grant before the current token expires. :meth:`OidcAuthProvider.current` is
the entry point: it checks expiry and, if needed, refreshes; on refresh
failure it warns and returns the stale token (never raises out of
``current()`` -- a dead issuer never blocks the caller).

What migrates vs what does NOT (YAGNI boundary declaration)
-----------------------------------------------------------

MIGRATED (the whole leaf -- a self-contained AuthProvider impl):

* :class:`RefreshEvent` + :data:`OnRefreshCallback`.
* :class:`OidcAuthProvider` -- ``current`` / ``identity`` / ``principal_key``
  (Rust trait default) / ``_is_expired`` / ``_try_refresh`` / ``_do_refresh``
  + a redacted ``__repr__``.
* :class:`OidcAuthProviderBuilder` -- the fluent constructor.
* :data:`REFRESH_MARGIN` + the discovery/token timeouts.
* :func:`_new_http_client` -- module-level test seam (NOT in Rust; added so
  the refresh-failure test does not depend on a real unreachable host).

NOT MIGRATED: none. The only dependencies are R139 auth symbols (already
landed) and a synchronous HTTP client (httpx, already a project dependency).
There is no framework glue to stub (unlike R142 admission's metrics stubs or
R143 pool's opener registrar).

Python-specific adaptations (no behavior change)
------------------------------------------------

* Rust ``Mutex<TokenState>`` -> NO lock. asyncio is single-threaded and
  ``current()`` is synchronous, so the read-check-refresh-write sequence runs
  without preemption; the GIL makes each attribute read/write atomic. The
  ``Mutex`` was Rust's defence against multi-thread access (the ``Send +
  Sync`` trait bound); Python's concurrency model does not need it, and an
  asyncio app should not be calling ``current()`` from multiple OS threads.
  Documented inline at :meth:`OidcAuthProvider.current`.
* Rust ``tokio::runtime::Handle::try_current + block_in_place`` (a sync bridge
  to async ``do_refresh``) -> a synchronous ``httpx.Client``. Python has no
  blocking-pool equivalent, so the refresh runs synchronously inline,
  mirroring Rust's ``block_in_place`` semantics: ``current()`` MAY block on
  network I/O, and callers should invoke it off the hot path (e.g. once before
  a WebSocket upgrade, not per-message). This is a deliberate trade-off vs an
  async refresh, declared here rather than hidden.
* Rust ``reqwest::Client`` -> ``httpx.Client`` (project already depends on
  httpx). ``error_for_status()`` -> ``raise_for_status()``; ``.json()`` ->
  ``.json()``; ``.form(&params)`` -> ``data=params``.
* Rust ``chrono::DateTime<Utc>`` / ``Utc::now()`` / ``Duration`` ->
  timezone-aware :class:`datetime.datetime` /
  :func:`datetime.now(UTC)` / :class:`datetime.timedelta`.
* Rust ``tracing::warn!`` / ``tracing::info!`` -> :func:`logging.warning` /
  :func:`logging.info`.
* Rust ``impl Into<String>`` -> ``str`` parameters.
* Rust ``Arc<dyn Fn(&RefreshEvent) + Send + Sync>`` ->
  ``Callable[[RefreshEvent], None]`` (no Send/Sync under the GIL).
* Rust ``trim_end_matches('/')`` -> :meth:`str.rstrip` with ``'/'``.
* Rust ``impl AuthProvider for OidcAuthProvider`` implements only ``current``
  + ``identity``; ``principal_key`` uses the trait default
  ``self.current().principal_key()`` -> the Python Protocol carries no default
  body, so :meth:`OidcAuthProvider.principal_key` is written explicitly as
  ``self.current().principal_key()`` to reproduce the trait default verbatim.
  This means a token rotation changes the pool dedup key (pool fragmentation);
  that is the faithful Rust behaviour -- the trait docstring warns about it
  but ``OidcAuthProvider`` does not override -- declared here, not silently
  "fixed".
* Rust ``#[cfg(test)]`` reaches a real ``https://localhost:1`` to exercise the
  refresh-failure path -> the module-level :func:`_new_http_client` seam that
  tests monkeypatch to inject a failing client, avoiding flaky real-network
  dependency in CI.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx

from minimax_code.computer_hub_sdk.auth import (
    AuthCredential,
    AuthIdentity,
    PrincipalKey,
)

__all__ = [
    "OnRefreshCallback",
    "RefreshEvent",
    "OidcAuthProvider",
    "OidcAuthProviderBuilder",
]

_logger = logging.getLogger(__name__)

#: Refresh this far ahead of true expiry so the token is never observed expired.
#: Mirrors ``Duration::from_secs(60)``.
REFRESH_MARGIN: timedelta = timedelta(seconds=60)

#: Per-request timeout (seconds) for OIDC discovery (``Duration::from_secs(10)``).
_DISCOVERY_TIMEOUT: float = 10.0
#: Per-request timeout (seconds) for the token endpoint (``Duration::from_secs(15)``).
_TOKEN_TIMEOUT: float = 15.0
#: OIDC discovery document path appended to the trimmed issuer URL.
_OIDC_CONFIG_PATH: str = "/.well-known/openid-configuration"


@dataclass(frozen=True)
class RefreshEvent:
    """Payload delivered to :data:`OnRefreshCallback` after a successful refresh.

    Mirrors the Rust ``RefreshEvent`` struct. ``new_refresh_token`` is ``None``
    when the issuer did not rotate the refresh token; ``expires_at`` is ``None``
    when the issuer did not return ``expires_in``.
    """

    access_token: str
    new_refresh_token: str | None
    expires_at: datetime | None


#: Callback fired after a successful token refresh (``Arc<dyn Fn(&RefreshEvent)>``).
OnRefreshCallback = Callable[[RefreshEvent], None]


@dataclass
class _TokenState:
    """Mutable token cache (Rust ``TokenState`` behind the ``Mutex``)."""

    access_token: str
    refresh_token: str
    expires_at: datetime | None


def _new_http_client() -> httpx.Client:
    """Construct the synchronous HTTP client used for a refresh (test seam).

    Production builds a plain :class:`httpx.Client`. Tests monkeypatch this
    function to inject a client whose ``get``/``post`` raise, exercising the
    stale-token fallback without depending on a real unreachable host
    (Rust's ``#[cfg(test)]`` reaches ``https://localhost:1``).
    """
    return httpx.Client()


class OidcAuthProvider:
    """An :class:`AuthProvider` that refreshes its OIDC bearer token before expiry.

    Construct via :class:`OidcAuthProviderBuilder`. :meth:`current` checks
    token expiry against :data:`REFRESH_MARGIN`; if expired it performs OIDC
    discovery + a ``refresh_token`` grant, and on failure warns and returns the
    stale token (never raises out of ``current()``). :meth:`identity` surfaces
    the owner principal fields parsed from the auth source.

    The ``__repr__`` mirrors the hand-written Rust ``Debug`` impl: it surfaces
    only ``issuer`` + ``client_id`` and never the tokens
    (``finish_non_exhaustive``).
    """

    def __init__(
        self,
        *,
        access_token: str,
        refresh_token: str,
        issuer: str,
        client_id: str,
        expires_at: datetime | None,
        user_id: str | None,
        principal_type: str | None,
        principal_id: str | None,
        on_refresh: OnRefreshCallback | None,
    ) -> None:
        self._state = _TokenState(
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
        )
        self._issuer = issuer
        self._client_id = client_id
        self._user_id = user_id
        self._principal_type = principal_type
        self._principal_id = principal_id
        self._on_refresh = on_refresh

    # -- AuthProvider (impl AuthProvider for OidcAuthProvider) -----------------
    def current(self) -> AuthCredential:
        """Return a fresh-enough bearer credential, refreshing if expired.

        On refresh failure, warns and returns the stale token rather than
        raising (mirrors ``tracing::warn!`` + fall-through to the cached token).
        No lock is taken: asyncio is single-threaded and this method is
        synchronous, so the read-check-refresh-write sequence is atomic with
        respect to the event loop (the GIL guards each attribute access); the
        Rust ``Mutex`` existed only for the multi-threaded ``Send + Sync``
        trait bound, which Python's concurrency model does not need.
        """
        if self._is_expired():
            try:
                self._try_refresh()
            except Exception as err:
                _logger.warning("OIDC refresh failed, using stale token: %s", err)
        return AuthCredential.bearer(self._state.access_token)

    def identity(self) -> AuthIdentity | None:
        """Surface the owner principal fields; ``None`` when no ``user_id`` was given."""
        if self._user_id is None:
            return None
        return AuthIdentity(
            user_id=self._user_id,
            principal_type=self._principal_type,
            principal_id=self._principal_id,
        )

    def principal_key(self) -> PrincipalKey:
        """Stable pool-dedup key (Rust trait default ``self.current().principal_key()``).

        Reproduced verbatim from the trait default. NOTE: this keys on the
        current token, so a token rotation fragments the connection pool --
        the faithful Rust behaviour (the trait docstring warns about it but
        ``OidcAuthProvider`` does not override).
        """
        return self.current().principal_key()

    # -- refresh internals ----------------------------------------------------
    def _is_expired(self) -> bool:
        """True when ``now + REFRESH_MARGIN >= expires_at`` (``None`` expiry -> False)."""
        exp = self._state.expires_at
        if exp is None:
            return False
        return datetime.now(UTC) + REFRESH_MARGIN >= exp

    def _try_refresh(self) -> None:
        """Perform a refresh; raise on failure (mirrors ``Result<(), Box<dyn Error>>``)."""
        _logger.info("refreshing OIDC token (issuer=%s)", self._issuer)
        self._do_refresh()

    def _do_refresh(self) -> None:
        """OIDC discovery + ``refresh_token`` grant; raise on any failure.

        Mirrors ``do_refresh``: GET the well-known config, POST the token
        endpoint with the refresh grant, then update the cached state and fire
        the on-refresh callback. Any network/parse error propagates to
        :meth:`_try_refresh` and ultimately :meth:`current`'s ``except``.
        """
        refresh_token = self._state.refresh_token
        issuer = self._issuer.rstrip("/")
        params: list[tuple[str, str]] = [
            ("grant_type", "refresh_token"),
            ("refresh_token", refresh_token),
            ("client_id", self._client_id),
        ]
        if self._principal_type is not None:
            params.append(("principal_type", self._principal_type))
        if self._principal_id is not None:
            params.append(("principal_id", self._principal_id))

        with _new_http_client() as client:
            disc = client.get(f"{issuer}{_OIDC_CONFIG_PATH}", timeout=_DISCOVERY_TIMEOUT)
            disc.raise_for_status()
            disc_json = disc.json()
            token_endpoint = disc_json["token_endpoint"]

            resp = client.post(token_endpoint, data=params, timeout=_TOKEN_TIMEOUT)
            resp.raise_for_status()
            tokens = resp.json()

        new_access = tokens["access_token"]
        new_refresh = tokens.get("refresh_token")
        expires_in = tokens.get("expires_in")
        expires_at = (
            datetime.now(UTC) + timedelta(seconds=expires_in)
            if expires_in is not None
            else None
        )

        _logger.info("OIDC token refreshed (expires_at=%s)", expires_at)

        if self._on_refresh is not None:
            self._on_refresh(
                RefreshEvent(
                    access_token=new_access,
                    new_refresh_token=new_refresh,
                    expires_at=expires_at,
                )
            )

        self._state.access_token = new_access
        if new_refresh is not None:
            self._state.refresh_token = new_refresh
        self._state.expires_at = expires_at

    # -- Debug (impl Debug for OidcAuthProvider) -------------------------------
    def __repr__(self) -> str:
        # Mirrors Rust's hand-written Debug: issuer + client_id only,
        # finish_non_exhaustive() -- tokens never appear.
        return f"OidcAuthProvider(issuer={self._issuer!r}, client_id={self._client_id!r})"


class OidcAuthProviderBuilder:
    """Fluent builder for :class:`OidcAuthProvider` (Rust ``OidcAuthProviderBuilder``).

    The four required string fields are set at :meth:`__init__`; the rest are
    optional and chainable. :meth:`build` consumes the builder.
    """

    def __init__(
        self,
        access_token: str,
        refresh_token: str,
        issuer: str,
        client_id: str,
    ) -> None:
        self._access_token = access_token
        self._refresh_token = refresh_token
        self._issuer = issuer
        self._client_id = client_id
        self._expires_at: datetime | None = None
        self._user_id: str | None = None
        self._principal_type: str | None = None
        self._principal_id: str | None = None
        self._on_refresh: OnRefreshCallback | None = None

    def expires_at(self, expires_at: datetime) -> OidcAuthProviderBuilder:
        """Set the current token's expiry (``DateTime<Utc>`` -> aware ``datetime``)."""
        self._expires_at = expires_at
        return self

    def user_id(self, user_id: str) -> OidcAuthProviderBuilder:
        """Set the owner user id parsed from the auth source."""
        self._user_id = user_id
        return self

    def principal_type(self, principal_type: str) -> OidcAuthProviderBuilder:
        """Set the principal type (e.g. ``"Team"``) forwarded on the refresh grant."""
        self._principal_type = principal_type
        return self

    def principal_id(self, principal_id: str) -> OidcAuthProviderBuilder:
        """Set the principal id forwarded on the refresh grant."""
        self._principal_id = principal_id
        return self

    def on_refresh(self, cb: OnRefreshCallback) -> OidcAuthProviderBuilder:
        """Register a callback fired after each successful refresh."""
        self._on_refresh = cb
        return self

    def build(self) -> OidcAuthProvider:
        """Consume the builder and return the configured provider."""
        return OidcAuthProvider(
            access_token=self._access_token,
            refresh_token=self._refresh_token,
            issuer=self._issuer,
            client_id=self._client_id,
            expires_at=self._expires_at,
            user_id=self._user_id,
            principal_type=self._principal_type,
            principal_id=self._principal_id,
            on_refresh=self._on_refresh,
        )
