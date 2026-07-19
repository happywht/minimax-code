"""Tests for backend environment presets — fusion of grok's ``xai-grok-env`` (R43).

Mirrors grok's four tests (the env-prefix is an operator interface; EnvVarGuard
``set_value`` updates then restores on drop; relay and gateway URLs are distinct;
``from_flags``) and pins the Python mapping: the frozen endpoints bundle, the
single-member ``@unique`` enum, env-var override resolution, and the RAII
guard's restore + lock-release semantics.
"""

from __future__ import annotations

import os
from dataclasses import FrozenInstanceError

import pytest

from minimax_code.env_presets import (
    PROD_ASSET_SERVER_URL,
    PROD_CLI_CHAT_PROXY_BASE_URL,
    PROD_GATEWAY_WS_URL,
    PROD_RELAY_WS_URL,
    PROD_WS_ORIGIN,
    PRODUCTION_ENDPOINTS,
    BuildEndpoints,
    BuildEnvironment,
    EnvVarGuard,
)

# --- BuildEndpoints (frozen value bundle, branch 2) ------------------------


def test_production_endpoints_fields():
    """grok's 5-field structure; api_base_url is the MiniMax chat-completions base."""
    e = PRODUCTION_ENDPOINTS
    assert e.cli_chat_proxy_base_url == "https://api.minimax.chat/v1"
    # The extension slots preserve grok's multi-service shape with sane schemes.
    assert e.asset_server_url.startswith("https://")
    assert e.relay_ws_url.startswith("wss://")
    assert e.gateway_ws_url.startswith("wss://")
    assert e.ws_origin.startswith("https://")


def test_prod_const_aliases_match_endpoints():
    """PROD_* are stable handles for the single source of truth in PRODUCTION_ENDPOINTS."""
    assert PROD_CLI_CHAT_PROXY_BASE_URL == PRODUCTION_ENDPOINTS.cli_chat_proxy_base_url
    assert PROD_ASSET_SERVER_URL == PRODUCTION_ENDPOINTS.asset_server_url
    assert PROD_RELAY_WS_URL == PRODUCTION_ENDPOINTS.relay_ws_url
    assert PROD_GATEWAY_WS_URL == PRODUCTION_ENDPOINTS.gateway_ws_url
    assert PROD_WS_ORIGIN == PRODUCTION_ENDPOINTS.ws_origin


def test_endpoints_is_frozen():
    """frozen dataclass mirrors grok's value semantics (branch 2)."""
    with pytest.raises(FrozenInstanceError):
        PRODUCTION_ENDPOINTS.cli_chat_proxy_base_url = "x"  # type: ignore[misc]


def test_endpoints_equality():
    a = BuildEndpoints("u1", "u2", "u3", "u4", "u5")
    b = BuildEndpoints("u1", "u2", "u3", "u4", "u5")
    assert a == b
    assert a != BuildEndpoints("z", "u2", "u3", "u4", "u5")


# --- BuildEnvironment (single-member @unique enum, branch 3) ---------------


def test_env_prefix():
    """grok: the env-prefix is an operator interface; do not rename."""
    assert BuildEnvironment.Production.env_prefix() == "MINIMAX_PRODUCTION"


def test_environment_single_member():
    """grok public builds compile Dev/Staging out — Production is the only member."""
    assert {m.name for m in BuildEnvironment} == {"Production"}


def test_from_flags_always_production():
    """grok from_flags: public builds always resolve to Production (flags are no-ops)."""
    assert BuildEnvironment.from_flags(False, False) == BuildEnvironment.Production
    assert BuildEnvironment.from_flags(True, True) == BuildEnvironment.Production


def test_is_production_and_indicator_and_str():
    p = BuildEnvironment.Production
    assert p.is_production() is True
    assert p.indicator() is None  # None for Production (grok indicator)
    assert str(p) == "production"  # grok Display


# --- resolve: env-var override vs compiled default -------------------------


def test_resolve_returns_compiled_default_without_env():
    """No MINIMAX_PRODUCTION_* env → compiled default (grok resolve unwrap_or_else)."""
    with EnvVarGuard.remove("MINIMAX_PRODUCTION_CLI_CHAT_PROXY_BASE_URL"):
        p = BuildEnvironment.Production
        assert p.cli_chat_proxy_base_url() == PRODUCTION_ENDPOINTS.cli_chat_proxy_base_url


def test_resolve_honors_env_override():
    """Set MINIMAX_PRODUCTION_WS_ORIGIN → override wins; cleared on exit."""
    p = BuildEnvironment.Production
    with EnvVarGuard.set("MINIMAX_PRODUCTION_WS_ORIGIN", "https://override.example"):
        assert p.ws_origin() == "https://override.example"
    # Restored on exit → compiled default again.
    assert p.ws_origin() == PRODUCTION_ENDPOINTS.ws_origin


def test_relay_and_gateway_urls_are_distinct():
    """grok: guards against conflating relay and gateway endpoints (different protocols)."""
    p = BuildEnvironment.Production
    assert p.relay_ws_url() != p.gateway_ws_url()


def test_relay_and_gateway_use_distinct_env_vars():
    """grok per-endpoint suffixes are an operator interface — _WS_URL ≠ _GATEWAY_WS_URL."""
    p = BuildEnvironment.Production
    with EnvVarGuard.set("MINIMAX_PRODUCTION_WS_URL", "wss://relay.override"):
        assert p.relay_ws_url() == "wss://relay.override"
        # Gateway unaffected — different env var (_GATEWAY_WS_URL), still the default.
        assert p.gateway_ws_url() == PRODUCTION_ENDPOINTS.gateway_ws_url


# --- EnvVarGuard (RAII restore, grok #[cfg(test)]) -------------------------


def test_env_var_guard_set_then_restores_on_with_exit():
    """grok Drop: the with-block exit restores the pre-guard snapshot."""
    key = "MINIMAX_CODE_ENV_GUARD_WITH_PROBE"
    before = os.environ.get(key)
    with EnvVarGuard.set(key, "initial"):
        assert os.environ.get(key) == "initial"
    assert os.environ.get(key) == before


def test_env_var_guard_set_value_updates_then_restores_on_close():
    """grok env_var_guard_set_value_updates_then_restores_on_drop.

    Exercises the explicit close() path (non-`with` usage, like grok's ``let guard``
    + drop). set_value must update while the guard is live; close restores.
    """
    key = "MINIMAX_CODE_ENV_GUARD_SETVALUE_PROBE"
    before = os.environ.get(key)
    guard = EnvVarGuard.set(key, "initial")
    assert os.environ.get(key) == "initial"
    guard.set_value("updated")
    assert os.environ.get(key) == "updated"
    guard.close()
    assert os.environ.get(key) == before


def test_env_var_guard_remove_restores_a_prior_value(monkeypatch):
    """remove() snapshots a prior value and restores it on close (not just deletes).

    Uses monkeypatch (not a nested ``EnvVarGuard.set``) to pre-populate the value —
    ``_ENV_LOCK`` is non-reentrant (mirrors grok's ``std::sync::Mutex``), so guards
    cannot nest. The remove-guard alone acquires + releases the lock cleanly.
    """
    key = "MINIMAX_CODE_ENV_GUARD_REMOVE_PROBE"
    monkeypatch.setenv(key, "pre-existing")
    with EnvVarGuard.remove(key):
        assert key not in os.environ
    # remove() restored the snapshotted prior value.
    assert os.environ.get(key) == "pre-existing"


def test_env_var_guard_close_is_idempotent_and_releases_lock_once():
    """Double close must not double-release the lock (would raise RuntimeError)."""
    guard = EnvVarGuard.set("MINIMAX_CODE_ENV_GUARD_IDEMPOTENT_PROBE", "x")
    guard.close()
    guard.close()  # idempotent — no RuntimeError
    # Lock is released; a fresh guard can acquire it.
    with EnvVarGuard.set("MINIMAX_CODE_ENV_GUARD_IDEMPOTENT_PROBE_2", "y"):
        assert os.environ.get("MINIMAX_CODE_ENV_GUARD_IDEMPOTENT_PROBE_2") == "y"


def test_env_var_guard_set_value_after_close_raises():
    """set_value on a closed guard is a programming error (use-after-drop guard)."""
    guard = EnvVarGuard.set("MINIMAX_CODE_ENV_GUARD_CLOSED_PROBE", "x")
    guard.close()
    with pytest.raises(RuntimeError):
        guard.set_value("y")
