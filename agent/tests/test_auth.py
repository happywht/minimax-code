"""Tests for ``minimax_code.computer_hub_sdk.auth`` (R139).

Mirrors grok-build's ``xai-computer-hub-sdk/src/auth.rs`` -- the most
self-contained SDK leaf so far. The whole file ports (AuthCredential +
PrincipalKey + AuthIdentity + AuthProvider are pure data/protocol types); the
only Rust framework type was ``http::HeaderName``, replaced by an RFC 7230
token-charset regex.

Security is the headline invariant: Bearer secrets and PrincipalKey
fingerprints MUST NOT appear in ``repr()``. Each redaction is pinned directly
here (mirrors the Rust ``Debug`` ``finish_non_exhaustive`` contract).
"""

from __future__ import annotations

import pytest

from minimax_code.computer_hub_sdk.auth import (
    AuthCredential,
    AuthIdentity,
    AuthProvider,
    PrincipalKey,
)
from minimax_code.computer_hub_sdk.error import InvalidConfig

SECRET_TOKEN = "super-secret-bearer-token-do-not-log"
SECRET_VALUE = "super-secret-header-value-do-not-log"


# ---------------------------------------------------------------------------
# AuthCredential.headers construction + HeaderName validation
# ---------------------------------------------------------------------------
def test_invalid_header_name_rejected_at_construction() -> None:
    # Newline injection attempt -- the Rust test's exact vector.
    with pytest.raises(InvalidConfig) as exc_info:
        AuthCredential.headers([("authorization\nx-injected", "value")])
    assert "invalid header name" in str(exc_info.value), f"got: {exc_info.value}"


def test_valid_headers_accepted() -> None:
    cred = AuthCredential.headers([("authorization", "Bearer token")])
    assert len(cred.upgrade_headers()) == 1


def test_headers_canonicalises_names_to_lowercase() -> None:
    cred = AuthCredential.headers([("Authorization", "Bearer token"), ("X-Custom", "v")])
    names = [n for n, _ in cred.upgrade_headers()]
    assert names == ["authorization", "x-custom"]


def test_headers_rejects_empty_name() -> None:
    with pytest.raises(InvalidConfig):
        AuthCredential.headers([("", "value")])


# ---------------------------------------------------------------------------
# AuthCredential.bearer construction + upgrade_headers
# ---------------------------------------------------------------------------
def test_bearer_constructs_and_emits_authorization_header() -> None:
    cred = AuthCredential.bearer(SECRET_TOKEN)
    assert cred.upgrade_headers() == [("authorization", f"Bearer {SECRET_TOKEN}")]


def test_bearer_principal_key_fingerprint() -> None:
    cred = AuthCredential.bearer(SECRET_TOKEN)
    # fingerprint includes the token BY DESIGN (pool dedup keys on the secret).
    assert cred.principal_key().fingerprint == f"bearer:{SECRET_TOKEN}"


# ---------------------------------------------------------------------------
# Security contract: repr MUST NOT leak secrets
# ---------------------------------------------------------------------------
def test_bearer_repr_does_not_leak_token() -> None:
    cred = AuthCredential.bearer(SECRET_TOKEN)
    r = repr(cred)
    assert SECRET_TOKEN not in r, f"bearer repr leaked token: {r!r}"
    assert "Bearer" in r or "redacted" in r, f"repr missing variant hint: {r!r}"


def test_headers_repr_does_not_leak_values() -> None:
    cred = AuthCredential.headers([("x-custom", SECRET_VALUE)])
    r = repr(cred)
    assert SECRET_VALUE not in r, f"headers repr leaked value: {r!r}"
    assert "header_count=1" in r, f"repr missing count: {r!r}"


def test_principal_key_repr_does_not_leak_fingerprint() -> None:
    key = AuthCredential.bearer(SECRET_TOKEN).principal_key()
    r = repr(key)
    assert SECRET_TOKEN not in r, f"PrincipalKey repr leaked fingerprint: {r!r}"
    assert "redacted" in r, f"repr missing redaction marker: {r!r}"


# ---------------------------------------------------------------------------
# PrincipalKey: order-independent fingerprint + eq/hash
# ---------------------------------------------------------------------------
def test_headers_principal_key_is_order_independent() -> None:
    a = AuthCredential.headers([("x-a", "1"), ("x-b", "2")])
    b = AuthCredential.headers([("x-b", "2"), ("x-a", "1")])
    assert a.principal_key() == b.principal_key()
    assert hash(a.principal_key()) == hash(b.principal_key())


def test_distinct_credentials_have_distinct_principal_keys() -> None:
    a = AuthCredential.bearer("token-one")
    b = AuthCredential.bearer("token-two")
    assert a.principal_key() != b.principal_key()


def test_principal_key_is_hashable_and_usable_in_set() -> None:
    ka = AuthCredential.bearer("t").principal_key()
    kb = AuthCredential.bearer("t").principal_key()
    assert {ka, kb} == {ka}  # same fingerprint -> dedup in a set


# ---------------------------------------------------------------------------
# AuthIdentity defaults
# ---------------------------------------------------------------------------
def test_auth_identity_defaults_optional_fields_to_none() -> None:
    ident = AuthIdentity(user_id="u123")
    assert ident.user_id == "u123"
    assert ident.principal_type is None
    assert ident.principal_id is None


# ---------------------------------------------------------------------------
# AuthCredential satisfies AuthProvider (impl AuthProvider for AuthCredential)
# ---------------------------------------------------------------------------
def test_auth_credential_satisfies_auth_provider_protocol() -> None:
    cred = AuthCredential.bearer(SECRET_TOKEN)
    assert isinstance(cred, AuthProvider)  # runtime_checkable Protocol


def test_auth_credential_current_returns_self() -> None:
    cred = AuthCredential.bearer(SECRET_TOKEN)
    assert cred.current() is cred


def test_auth_credential_identity_defaults_none() -> None:
    cred = AuthCredential.bearer(SECRET_TOKEN)
    assert cred.identity() is None


def test_auth_credential_principal_key_via_provider_protocol() -> None:
    # A function typed to take AuthProvider must accept a bare AuthCredential.
    def provider_key(p: AuthProvider) -> PrincipalKey:
        return p.principal_key()

    cred = AuthCredential.bearer(SECRET_TOKEN)
    assert provider_key(cred).fingerprint == f"bearer:{SECRET_TOKEN}"
