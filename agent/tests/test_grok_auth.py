"""Tests for the grok_auth trait contract layer (R187).

R187 lands the always-on ``xai-grok-auth`` surface: :class:`HttpAuth`
(visibility) + :class:`CredentialSnapshot` /
:class:`AuthCredentialProvider` /
:class:`StaticAuthCredentialProvider` (auth_provider). The tests pin three
invariants:

1. **Abstraction** -- :class:`HttpAuth` and :class:`AuthCredentialProvider`
   are abstract (not directly instantiable); the provider IS-A
   :class:`HttpAuth` (Rust supertrait -> Python inheritance).
2. **Snapshot safety** -- :class:`CredentialSnapshot.__repr__` never
   surfaces the token value (platform redaction convention, stricter than
   Rust's verbatim ``Debug``).
3. **Static provider delegation** -- :class:`StaticAuthCredentialProvider`
   delegates :meth:`apply` to its inner :class:`HttpAuth`, returns the
   fixed bearer from :meth:`snapshot`, no-ops
   :meth:`refresh_after_unauthorized`, and inherits the trait defaults.
"""

from __future__ import annotations

import httpx
import pytest

from minimax_code.grok_auth import (
    AuthCredentialProvider,
    CredentialSnapshot,
    HttpAuth,
    StaticAuthCredentialProvider,
)


class _RecordingHttpAuth(HttpAuth):
    """Fake :class:`HttpAuth` that records :meth:`apply` calls + stamps a header."""

    def __init__(self, header_value: str = "Bearer recorded") -> None:
        self._header_value = header_value
        self.apply_calls: list[tuple[str, str]] = []

    def apply(self, request: httpx.Request, base_url: str) -> httpx.Request:
        self.apply_calls.append((base_url, self._header_value))
        request.headers["X-Test-Auth"] = self._header_value
        return request


# ---------------------------------------------------------------------------
# Abstraction: HttpAuth and AuthCredentialProvider are abstract; the provider
# IS-A HttpAuth (Rust supertrait -> Python inheritance).
# ---------------------------------------------------------------------------

def test_http_auth_is_abstract() -> None:
    with pytest.raises(TypeError):
        HttpAuth()  # type: ignore[abstract]


def test_auth_credential_provider_is_abstract() -> None:
    with pytest.raises(TypeError):
        AuthCredentialProvider()  # type: ignore[abstract]


def test_auth_credential_provider_is_a_http_auth() -> None:
    """Rust ``AuthCredentialProvider: HttpAuth`` -> Python IS-A relationship."""
    assert issubclass(AuthCredentialProvider, HttpAuth)
    assert issubclass(StaticAuthCredentialProvider, HttpAuth)


def test_static_provider_satisfies_both_traits() -> None:
    """StaticAuthCredentialProvider is both an HttpAuth and an AuthCredentialProvider."""
    provider = StaticAuthCredentialProvider(inner=_RecordingHttpAuth(), bearer="abc")
    assert isinstance(provider, HttpAuth)
    assert isinstance(provider, AuthCredentialProvider)


# ---------------------------------------------------------------------------
# CredentialSnapshot: defaults + repr redaction.
# ---------------------------------------------------------------------------

def test_credential_snapshot_defaults_all_none() -> None:
    snap = CredentialSnapshot()
    assert snap.token is None
    assert snap.user_id is None
    assert snap.team_id is None
    assert snap.deployment_id is None
    assert snap.api_key_id is None
    assert snap.organization_id is None


def test_credential_snapshot_fields_settable() -> None:
    snap = CredentialSnapshot(
        token="t",
        user_id="u",
        team_id="tm",
        deployment_id="d",
        api_key_id="k",
        organization_id="o",
    )
    assert snap.token == "t"
    assert snap.user_id == "u"
    assert snap.team_id == "tm"
    assert snap.deployment_id == "d"
    assert snap.api_key_id == "k"
    assert snap.organization_id == "o"


@pytest.mark.parametrize("token", ["secret-bearer-12345", "short"])
def test_credential_snapshot_repr_redacts_token(token: str) -> None:
    """Platform safety: repr never surfaces the token value (stricter than Rust)."""
    snap = CredentialSnapshot(token=token)
    assert token not in repr(snap)
    assert "token=<set>" in repr(snap)


def test_credential_snapshot_repr_shows_none_when_no_token() -> None:
    snap = CredentialSnapshot()
    assert "token=<none>" in repr(snap)


# ---------------------------------------------------------------------------
# StaticAuthCredentialProvider: delegation + snapshot + refresh + defaults.
# ---------------------------------------------------------------------------

def test_static_provider_apply_delegates_to_inner() -> None:
    inner = _RecordingHttpAuth()
    provider = StaticAuthCredentialProvider(inner=inner, bearer="abc")
    request = httpx.Request("GET", "https://example.test/api")

    returned = provider.apply(request, "https://example.test")

    assert returned is request  # same object returned for chaining
    assert request.headers["X-Test-Auth"] == "Bearer recorded"
    assert inner.apply_calls == [("https://example.test", "Bearer recorded")]


def test_static_provider_snapshot_token_is_bearer() -> None:
    provider = StaticAuthCredentialProvider(
        inner=_RecordingHttpAuth(), bearer="wire-tokens"
    )
    snap = provider.snapshot()
    assert snap.token == "wire-tokens"
    # Other fields default -- the static provider carries no identity.
    assert snap.user_id is None
    assert snap.organization_id is None


def test_static_provider_snapshot_none_bearer() -> None:
    provider = StaticAuthCredentialProvider(inner=_RecordingHttpAuth(), bearer=None)
    assert provider.snapshot().token is None


async def test_static_provider_refresh_always_false() -> None:
    provider = StaticAuthCredentialProvider(inner=_RecordingHttpAuth(), bearer="abc")
    assert await provider.refresh_after_unauthorized() is False


def test_static_provider_inherits_trait_defaults() -> None:
    """Rust default impls -> Python inherited concrete methods on the ABC."""
    provider = StaticAuthCredentialProvider(inner=_RecordingHttpAuth(), bearer="abc")
    assert provider.needs_token_auth_header() is True
    assert provider.has_usable_credential() is True


def test_static_provider_repr_shows_has_bearer_only() -> None:
    """Rust Debug surfaces ``has_bearer``; the token must not leak."""
    with_bearer = StaticAuthCredentialProvider(
        inner=_RecordingHttpAuth(), bearer="secret-value"
    )
    without = StaticAuthCredentialProvider(inner=_RecordingHttpAuth(), bearer=None)

    assert "secret-value" not in repr(with_bearer)
    assert "has_bearer=True" in repr(with_bearer)
    assert "has_bearer=False" in repr(without)
