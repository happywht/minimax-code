"""Tests for tools_api.default_client_name (R192, lib.rs barrel-reconciliation).

Mirrors grok-build's ``lib.rs::default_client_name_tests`` inline suite (the
``pins_first_colon_derivation`` test, 4 assertions) plus platform structural
coverage. Closes the ``xai-grok-tools-api`` crate (R190-R192 milestone).
"""

from __future__ import annotations

import minimax_code.tools_api as tools_api
from minimax_code.tools_api import default_client_name

# ---------------------------------------------------------------------------
# Structural: barrel exposes the root free function; return type.
# ---------------------------------------------------------------------------


def test_barrel_exposes_default_client_name_at_root() -> None:
    """``lib.rs`` free function lands at the barrel root, not under a submodule."""
    assert callable(tools_api.default_client_name)
    assert tools_api.default_client_name is default_client_name
    assert "default_client_name" in tools_api.__all__


def test_default_client_name_returns_str() -> None:
    assert isinstance(default_client_name("GrokBuild:grep"), str)
    assert isinstance(default_client_name(""), str)


# ---------------------------------------------------------------------------
# Rust inline test: pins_first_colon_derivation (4 assertions).
# ---------------------------------------------------------------------------


def test_pins_first_colon_derivation() -> None:
    """Default name = segment after the FIRST colon; embedded colons resolve to
    the second segment; bare ids pass through; empty stays empty."""
    assert default_client_name("GrokBuild:grep") == "grep"
    assert default_client_name("ns:a:b") == "a"
    assert default_client_name("bare") == "bare"
    assert default_client_name("") == ""
