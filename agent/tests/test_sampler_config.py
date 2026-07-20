"""Tests for sampler.config (R195, ``xai-grok-sampler`` ``src/config.rs`` subset).

Covers the two migrated pure types (:class:`OriginClientInfo` +
:class:`AuthScheme`) + the barrel surface + value semantics, plus the
**R193 source-of-truth reversal assertion**: ``grok_http.OriginClientInfo`` is
the *same object* as ``sampler.config.OriginClientInfo`` (the dependency
direction is inverted, not duplicated). The deferred/YAGNI symbols
(``SamplerConfig`` / ``RetryPolicy`` / 2 traits) are documented in
``config.py``; the constants ``DEFAULT_MAX_RETRIES`` / ``RATE_LIMIT_RETRY_
THRESHOLD`` are not migrated (they serve only ``RetryPolicy`` + ``retry.rs``'s
reqwest loop).
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code import sampler
from minimax_code.grok_http import OriginClientInfo as GrokHttpOriginClientInfo
from minimax_code.sampler import DEFAULT_AUTH_SCHEME, AuthScheme, OriginClientInfo
from minimax_code.sampler import config as sampler_config

# ---------------------------------------------------------------------------
# Barrel surface (package + module).
# ---------------------------------------------------------------------------


def test_package_barrel_exposes_three_symbols() -> None:
    """2 types + 1 default constant = 3 re-exported symbols (faithful-source flip)."""
    assert len(sampler.__all__) == 3
    assert set(sampler.__all__) == {"AuthScheme", "DEFAULT_AUTH_SCHEME", "OriginClientInfo"}


def test_module_barrel_exposes_three_symbols() -> None:
    assert len(sampler_config.__all__) == 3
    assert set(sampler_config.__all__) == {"AuthScheme", "DEFAULT_AUTH_SCHEME", "OriginClientInfo"}


# ---------------------------------------------------------------------------
# AuthScheme: serde ``#[serde(rename_all="snake_case")]`` labels + default.
# ---------------------------------------------------------------------------


def test_auth_scheme_labels_match_serde_snake_case() -> None:
    """grok ``Bearer`` -> ``bearer``; ``XApiKey`` -> ``x_api_key`` (snake_case rename)."""
    assert AuthScheme.BEARER == "bearer"
    assert AuthScheme.X_API_KEY == "x_api_key"


def test_auth_scheme_str_is_wire_value() -> None:
    """``StrEnum.__str__`` returns the wire value (what serde would emit)."""
    assert str(AuthScheme.BEARER) == "bearer"
    assert str(AuthScheme.X_API_KEY) == "x_api_key"


def test_auth_scheme_has_exactly_two_variants() -> None:
    assert {kind.value for kind in AuthScheme} == {"bearer", "x_api_key"}


def test_default_auth_scheme_is_bearer() -> None:
    """grok ``#[default] Bearer`` is mirrored by :data:`DEFAULT_AUTH_SCHEME`."""
    assert DEFAULT_AUTH_SCHEME is AuthScheme.BEARER
    assert DEFAULT_AUTH_SCHEME == "bearer"


# ---------------------------------------------------------------------------
# OriginClientInfo: shape + value semantics (frozen + slots + hashable).
# ---------------------------------------------------------------------------


def test_origin_client_info_defaults_version_none() -> None:
    info = OriginClientInfo(product="grok-desktop")
    assert info.product == "grok-desktop"
    assert info.version is None


def test_origin_client_info_round_trips_product_and_version() -> None:
    info = OriginClientInfo(product="grok-web", version="1.2.3")
    assert info == OriginClientInfo(product="grok-web", version="1.2.3")


def test_origin_client_info_is_frozen() -> None:
    """``@dataclass(frozen=True)`` -> mutation raises (mirrors grok's immutable struct)."""
    info = OriginClientInfo(product="x", version="1")
    with pytest.raises(FrozenInstanceError):
        info.product = "y"  # type: ignore[misc]


def test_origin_client_info_is_hashable_and_equal() -> None:
    a = OriginClientInfo(product="x", version="1")
    b = OriginClientInfo(product="x", version="1")
    assert a == b
    assert hash(a) == hash(b)


def test_origin_client_info_declares_slots() -> None:
    """``slots=True`` -> the class declares ``__slots__`` over its fields (no
    per-instance ``__dict__``). Combined with ``frozen=True`` (see
    :func:`test_origin_client_info_is_frozen`) the attribute namespace is closed."""
    assert OriginClientInfo.__slots__ == ("product", "version")
    info = OriginClientInfo(product="x")
    assert not hasattr(info, "__dict__")


# ---------------------------------------------------------------------------
# R193 source-of-truth reversal: grok_http re-exports the SAME object.
# ---------------------------------------------------------------------------


def test_grok_http_origin_client_info_is_sampler_origin_client_info() -> None:
    """The R193 local definition is retired; ``grok_http`` imports the faithful
    source from here (dependency direction inverted, not duplicated)."""
    assert GrokHttpOriginClientInfo is OriginClientInfo


def test_grok_http_origin_client_info_remains_in_all() -> None:
    """Backward compat: ``from minimax_code.grok_http import OriginClientInfo`` still
    works (re-export preserved in ``grok_http.__all__``)."""
    import minimax_code.grok_http as grok_http

    assert "OriginClientInfo" in grok_http.__all__
    assert grok_http.OriginClientInfo is OriginClientInfo
