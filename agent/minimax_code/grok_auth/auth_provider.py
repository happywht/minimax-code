"""Refresh-aware credential provider + static default (R187).

Fusion of grok-build's ``xai-grok-auth/src/auth_provider.rs`` (118 lines) --
the ``xai-grok-auth`` crate's 2nd leaf. :class:`AuthCredentialProvider` is
the wider auth contract: a supertrait of :class:`HttpAuth` that adds
refresh-aware snapshotting (:meth:`snapshot`) and 401 recovery
(:meth:`refresh_after_unauthorized`). Shell installs the real provider
(wrapping its ``AuthManager`` + ``TokenRefresher``); data-collector code
holds an :class:`AuthCredentialProvider` and resolves credentials per
request.

:class:`StaticAuthCredentialProvider` is the test / headless default: wraps
a bare :class:`HttpAuth` + a fixed bearer, :meth:`snapshot` returns that
bearer, :meth:`refresh_after_unauthorized` is a no-op (always ``False``).

This leaf + :mod:`minimax_code.grok_auth.visibility` together form the
crate's always-on contract surface; the ``middleware`` feature-gated
``AuthRetryMiddleware`` is deferred to a later round.

Python-specific adaptations (no behavior change)
------------------------------------------------

* Rust ``#[async_trait] pub trait AuthCredentialProvider: HttpAuth + Send
  + Sync + 'static`` -> :class:`AuthCredentialProvider(HttpAuth, ABC)`.
  The ``HttpAuth`` supertrait becomes Python multiple-inheritance
  (an :class:`AuthCredentialProvider` IS-A :class:`HttpAuth`). ``Send +
  Sync + 'static`` omitted (single-threaded asyncio). ``async_trait``
  desugars to ``async def refresh_after_unauthorized``.
* Default-impl trait methods (``needs_token_auth_header`` -> ``True``,
  ``has_usable_credential`` -> ``True``) become concrete methods on the
  ABC -- Python ABC methods with bodies are inherited default
  implementations (the idiom R139 uses on :class:`AuthCredential`).
* Rust ``Box<dyn HttpAuth>`` -> a bare :class:`HttpAuth` reference
  (Python garbage collection owns the lifetime; no boxing needed).
* :class:`CredentialSnapshot` derives ``Clone, Debug, Default`` in Rust ->
  :func:`dataclasses.dataclass` with ``str | None = None`` defaults (gives
  ``Default``; dataclasses are cloneable via :func:`copy.copy`; the auto
  ``__repr__`` is overridden to redact the token).

Security contract
-----------------

:class:`CredentialSnapshot` carries the bearer token (secret). Rust's
``Debug`` derives verbatim, but the platform's outbound-secret-redaction
convention (R139 :class:`BearerCredential`, R15 outbound scrubbing) is
stricter: ``__repr__`` surfaces ``token=<set>`` / ``token=<none>`` rather
than the value. :class:`StaticAuthCredentialProvider.__repr__` mirrors the
Rust ``Debug`` (``has_bearer`` only) for the same reason.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING

from minimax_code.grok_auth.visibility import HttpAuth

if TYPE_CHECKING:
    import httpx

__all__ = [
    "AuthCredentialProvider",
    "CredentialSnapshot",
    "StaticAuthCredentialProvider",
]


@dataclass(repr=False)
class CredentialSnapshot:
    """Snapshot of the currently effective credentials (R187).

    Read by callers that build their own header maps (the OTel OTLP
    exporter) or need the bearer prefix for 401-attribution telemetry. All
    fields are ``None`` when no auth is configured (CI / ``--api-key``
    headless).

    ``token`` mirrors the bearer that :meth:`HttpAuth.apply` would send on
    the wire, so 401-attribution prefixes match the actual request.

    ``__repr__`` redacts ``token`` (secret) -- platform safety convention
    (R139 / R15), stricter than Rust's verbatim ``Debug``.
    """

    token: str | None = None
    user_id: str | None = None
    team_id: str | None = None
    deployment_id: str | None = None
    api_key_id: str | None = None
    organization_id: str | None = None

    def __repr__(self) -> str:
        token_repr = "<set>" if self.token is not None else "<none>"
        return (
            f"CredentialSnapshot(token={token_repr}, "
            f"user_id={self.user_id!r}, team_id={self.team_id!r}, "
            f"deployment_id={self.deployment_id!r}, "
            f"api_key_id={self.api_key_id!r}, "
            f"organization_id={self.organization_id!r})"
        )


class AuthCredentialProvider(HttpAuth, ABC):
    """Source of truth for outbound auth on data-collector requests (R187).

    A supertrait of :class:`HttpAuth` (so a single implementation satisfies
    both the refresh-aware snapshot contract here and the
    header-construction seam in :class:`HttpAuth`). Shell installs the real
    provider; data-collector code resolves credentials per request via
    :meth:`snapshot` and retries on 401 via
    :meth:`refresh_after_unauthorized`.
    """

    @abstractmethod
    def snapshot(self) -> CredentialSnapshot:
        """Return the current credential snapshot.

        Implementations should issue a cheap disk re-read
        (``AuthManager::refresh``) before snapshotting so callers see
        updates from sibling processes (``grok-desktop``, ``grok login``).
        The ``token`` field MUST mirror the bearer that
        :meth:`HttpAuth.apply` would send on the wire so 401-attribution
        prefixes match the actual request.
        """

    @abstractmethod
    async def refresh_after_unauthorized(self) -> bool:
        """Attempt to obtain a fresh token after a 401.

        Returns:
            ``True`` if a different token was obtained (caller should retry
            the failed request once); ``False`` if no refresher is
            configured or refresh failed.
        """

    def needs_token_auth_header(self) -> bool:
        """Whether ``X-XAI-Token-Auth`` should accompany the bearer.

        ``False`` for deployment keys (bare Bearer), ``True`` for user /
        OAuth tokens. Default ``True`` (the common path).
        """
        return True

    def has_usable_credential(self) -> bool:
        """Whether the provider holds a credential worth a real attempt.

        An unexpired token (in memory or on disk), or a static key.
        Default ``True`` (always attempt) -- overridden by providers that
        know they are empty.
        """
        return True


class StaticAuthCredentialProvider(AuthCredentialProvider):
    """Static credential provider: fixed bearer + delegate :class:`HttpAuth` (R187).

    Used by tests and by callers that pass a raw token with no
    ``AuthManager`` available. :meth:`apply` delegates to the inner
    :class:`HttpAuth`; :meth:`refresh_after_unauthorized` is a no-op
    (always ``False``); :meth:`snapshot` returns the fixed bearer so the
    snapshot's ``token`` mirrors what the inner :class:`HttpAuth` sends.

    ``__repr__`` surfaces only ``has_bearer`` (never the token) -- mirrors
    Rust's ``Debug``.
    """

    def __init__(self, inner: HttpAuth, bearer: str | None) -> None:
        self._inner = inner
        self._bearer = bearer

    def apply(self, request: httpx.Request, base_url: str) -> httpx.Request:
        """Delegate header stamping to the inner :class:`HttpAuth`."""
        return self._inner.apply(request, base_url)

    def snapshot(self) -> CredentialSnapshot:
        return CredentialSnapshot(token=self._bearer)

    async def refresh_after_unauthorized(self) -> bool:
        return False

    def __repr__(self) -> str:
        return f"StaticAuthCredentialProvider(has_bearer={self._bearer is not None})"
