"""Tests for ``minimax_code.computer_hub_sdk.oidc_provider`` (R145).

Mirrors grok-build's ``xai-computer-hub-sdk/src/oidc_provider.rs`` (336
lines) -- the SDK crate's 13th leaf. An ``AuthProvider`` that refreshes its
OIDC bearer token before expiry via OIDC discovery + a ``refresh_token``
grant. The six Rust ``#[test]``s port 1:1.

Python-specific adjustments (no behavior change):

* Rust reaches a real ``https://localhost:1`` to exercise the refresh-failure
  path -> tests monkeypatch the module-level ``_new_http_client`` seam to
  inject a client whose ``get``/``post`` raise, avoiding flaky real-network
  dependency in CI.
* Rust ``DateTime<Utc>`` -> timezone-aware ``datetime``; ``Utc::now()`` ->
  ``datetime.now(timezone.utc)``; ``chrono::Duration::hours`` ->
  ``timedelta(hours=)``.
* Rust ``match cred { AuthCredential::Bearer { token } => .. }`` -> the Python
  :class:`BearerCredential` is a frozen dataclass with a ``.token`` field, so
  the assertion is ``cred.token == "..."`` after an ``isinstance`` check.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import httpx
import pytest

import minimax_code.computer_hub_sdk.oidc_provider as oidc_mod
from minimax_code.computer_hub_sdk.auth import BearerCredential
from minimax_code.computer_hub_sdk.oidc_provider import OidcAuthProviderBuilder


def _utc(hours_from_now: float) -> datetime:
    """A timezone-aware instant offset from now (``Utc::now() + Duration::hours``)."""
    return datetime.now(UTC) + timedelta(hours=hours_from_now)


class _UnreachableClient:
    """Stand-in ``httpx.Client`` whose every request raises (refresh-failure path).

    Stands in for a real unreachable host so the stale-token fallback is
    exercised deterministically without any network dependency. ``__enter__`` /
    ``__exit__`` satisfy the ``with`` block ``_do_refresh`` opens.
    """

    def __enter__(self) -> _UnreachableClient:
        return self

    def __exit__(self, *args: object) -> bool:
        return False

    def get(self, *args: object, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("unreachable")

    def post(self, *args: object, **kwargs: object) -> httpx.Response:
        raise httpx.ConnectError("unreachable")


# ---------------------------------------------------------------------------
# current -- token returned fresh when not expired / no expiry
# ---------------------------------------------------------------------------
def test_current_returns_token_when_not_expired() -> None:
    provider = (
        OidcAuthProviderBuilder(
            "access-tok", "refresh-tok", "https://auth.example.com", "client1"
        )
        .expires_at(_utc(1))  # one hour out -> not within REFRESH_MARGIN
        .build()
    )

    cred = provider.current()

    assert isinstance(cred, BearerCredential)
    assert cred.token == "access-tok"


def test_current_returns_token_when_no_expiry() -> None:
    # A token with no expiry is treated as never-expiring -> no refresh attempt.
    provider = OidcAuthProviderBuilder(
        "no-expiry-tok", "refresh-tok", "https://auth.example.com", "client1"
    ).build()

    cred = provider.current()

    assert isinstance(cred, BearerCredential)
    assert cred.token == "no-expiry-tok"


# ---------------------------------------------------------------------------
# current -- expired + refresh failure -> stale token (never raises)
# ---------------------------------------------------------------------------
def test_current_returns_stale_token_when_refresh_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Inject a client that always raises so the refresh path fails fast without
    # any real network dependency (Rust reaches https://localhost:1).
    monkeypatch.setattr(oidc_mod, "_new_http_client", lambda: _UnreachableClient())

    provider = (
        OidcAuthProviderBuilder(
            "stale-tok", "refresh-tok", "https://auth.example.com", "client1"
        )
        .expires_at(_utc(-1))  # expired an hour ago
        .build()
    )

    cred = provider.current()

    assert isinstance(cred, BearerCredential)
    assert cred.token == "stale-tok", "refresh failure must fall back to the stale token"


# ---------------------------------------------------------------------------
# identity -- surfaces the owner principal fields, None without user_id
# ---------------------------------------------------------------------------
def test_identity_surfaces_principal_fields() -> None:
    provider = (
        OidcAuthProviderBuilder("tok", "rt", "https://auth.example.com", "c1")
        .user_id("user-1")
        .principal_type("Team")
        .principal_id("team-9")
        .build()
    )

    identity = provider.identity()

    assert identity is not None
    assert identity.user_id == "user-1"
    assert identity.principal_type == "Team"
    assert identity.principal_id == "team-9"


def test_identity_none_without_user_id() -> None:
    provider = OidcAuthProviderBuilder(
        "tok", "rt", "https://auth.example.com", "c1"
    ).build()

    assert provider.identity() is None


# ---------------------------------------------------------------------------
# repr -- tokens never leak (manual Debug impl)
# ---------------------------------------------------------------------------
def test_repr_does_not_leak_tokens() -> None:
    provider = OidcAuthProviderBuilder(
        "secret-access-token",
        "secret-refresh-token",
        "https://auth.example.com",
        "client1",
    ).build()

    text = repr(provider)

    assert "secret-access-token" not in text
    assert "secret-refresh-token" not in text
