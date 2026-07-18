"""Tests for the MCP OAuth config types (R34).

grok's ``oauth_config.rs`` has no ``#[cfg(test)]`` module, so these tests pin
the ``is_configured`` predicate (``client_id`` is the single signal), the
all-default construction (grok ``Default``), mutability (grok ``Clone``, no
``Eq``/``Hash``), and the map type alias.
"""

from __future__ import annotations

from minimax_code.mcp import McpOAuthConfig, McpOAuthConfigMap
from minimax_code.mcp.oauth_config import McpOAuthConfig as McpOAuthConfigFromModule


def test_reexport():
    assert McpOAuthConfig is McpOAuthConfigFromModule


def test_default_is_unconfigured():
    cfg = McpOAuthConfig()
    assert not cfg.is_configured()
    assert cfg.client_id is None
    assert cfg.client_secret is None
    assert cfg.scopes is None
    assert cfg.callback_port is None


def test_client_id_alone_means_configured():
    assert McpOAuthConfig(client_id="abc").is_configured()


def test_secret_without_client_id_is_unconfigured():
    """client_id is the single predicate — a secret alone doesn't count."""
    cfg = McpOAuthConfig(client_secret="shh")
    assert not cfg.is_configured()


def test_full_config():
    cfg = McpOAuthConfig(
        client_id="id",
        client_secret="secret",
        scopes=["read", "write"],
        callback_port=8765,
    )
    assert cfg.is_configured()
    assert cfg.scopes == ["read", "write"]
    assert cfg.callback_port == 8765


def test_map_alias_is_dict_of_config():
    """McpOAuthConfigMap is dict[str, McpOAuthConfig]."""
    m: McpOAuthConfigMap = {
        "drive": McpOAuthConfig(client_id="g"),
        "github": McpOAuthConfig(client_id="gh"),
    }
    assert m["drive"].is_configured()
    assert len(m) == 2


def test_config_is_mutable_and_default_constructible():
    """grok derive Default + Clone — plain dataclass is mutable + zero-arg constructible."""
    cfg = McpOAuthConfig()
    cfg.client_id = "late"  # mutable (not frozen)
    assert cfg.is_configured()
