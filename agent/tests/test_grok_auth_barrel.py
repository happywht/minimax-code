"""Barrel reconciliation tests for the grok_auth crate (R189, crate complete).

R189 closes the ``xai-grok-auth`` crate: verifies the platform barrel
mirrors grok-build's ``src/lib.rs`` surface exactly -- 3 ``mod``
declarations + 5 ``pub use`` symbols, all always-on (the Rust ``middleware``
feature gate is a no-op on the platform).

These tests are the crate's completion gate: they would fail if a future
edit dropped a symbol from ``__init__.py``'s ``__all__``, shifted a symbol
to a different submodule, or broke the re-export identity. They complement
the per-leaf behavior tests (:mod:`tests.test_grok_auth`,
:mod:`tests.test_grok_auth_retry`).
"""

from __future__ import annotations

import minimax_code.grok_auth as barrel
from minimax_code.grok_auth import (
    AuthCredentialProvider,
    AuthRetryMiddleware,
    CredentialSnapshot,
    HttpAuth,
    StaticAuthCredentialProvider,
)

#: The exact 5-symbol set ``lib.rs`` re-exports (auth_provider 3 + visibility
#: 1 + retry_middleware 1).
_EXPECTED_BARREL = {
    "AuthCredentialProvider",
    "AuthRetryMiddleware",
    "CredentialSnapshot",
    "HttpAuth",
    "StaticAuthCredentialProvider",
}


# ---------------------------------------------------------------------------
# lib.rs mod declarations: 3/3 submodules exist and are importable.
# ---------------------------------------------------------------------------


def test_visibility_submodule_present() -> None:
    """lib.rs ``pub mod visibility`` -> the submodule is importable."""
    from minimax_code.grok_auth import visibility

    assert visibility is not None
    assert hasattr(visibility, "HttpAuth")


def test_auth_provider_submodule_present() -> None:
    """lib.rs ``pub mod auth_provider`` -> the submodule is importable."""
    from minimax_code.grok_auth import auth_provider

    assert auth_provider is not None
    assert hasattr(auth_provider, "AuthCredentialProvider")


def test_retry_middleware_submodule_present() -> None:
    """lib.rs ``#[cfg(feature=middleware)] pub mod retry_middleware`` -> always-on here."""
    from minimax_code.grok_auth import retry_middleware

    assert retry_middleware is not None
    assert hasattr(retry_middleware, "AuthRetryMiddleware")


# ---------------------------------------------------------------------------
# lib.rs pub use surface: __all__ mirrors the 5-symbol set exactly.
# ---------------------------------------------------------------------------


def test_barrel_all_matches_librs_surface() -> None:
    """``__all__`` is exactly the 5 ``pub use`` symbols (no more, no less)."""
    assert set(barrel.__all__) == _EXPECTED_BARREL


def test_barrel_all_count_is_five() -> None:
    """3 (auth_provider) + 1 (visibility) + 1 (retry_middleware) = 5."""
    assert len(barrel.__all__) == 5


# ---------------------------------------------------------------------------
# Re-export identity: barrel symbols ARE the submodule symbols (not copies).
# ---------------------------------------------------------------------------


def test_auth_provider_symbols_are_reexports() -> None:
    from minimax_code.grok_auth import auth_provider

    assert AuthCredentialProvider is auth_provider.AuthCredentialProvider
    assert CredentialSnapshot is auth_provider.CredentialSnapshot
    assert StaticAuthCredentialProvider is auth_provider.StaticAuthCredentialProvider


def test_visibility_symbol_is_reexport() -> None:
    from minimax_code.grok_auth import visibility

    assert HttpAuth is visibility.HttpAuth


def test_retry_middleware_symbol_is_reexport() -> None:
    from minimax_code.grok_auth import retry_middleware

    assert AuthRetryMiddleware is retry_middleware.AuthRetryMiddleware


# ---------------------------------------------------------------------------
# Feature parity: Rust gates retry_middleware behind the ``middleware`` cargo
# feature; the platform exposes it always-on.
# ---------------------------------------------------------------------------


def test_auth_retry_middleware_always_on() -> None:
    """No feature flag / env setup needed -- importable straight from the barrel.

    Rust gates ``AuthRetryMiddleware`` behind its ``middleware`` cargo feature
    (reqwest-middleware + http). The platform has no equivalent feature-flag
    mechanism, and the R188 ``send``-callback seam avoids binding httpx
    transport plumbing, so the symbol is always exposed at no cost.
    """
    assert AuthRetryMiddleware is barrel.AuthRetryMiddleware
    assert "AuthRetryMiddleware" in barrel.__all__
