"""Tests for the MCP-over-ACP wire constants (R34).

grok's ``wire.rs`` has no ``#[cfg(test)]`` module (it is a constants-only
file), so these tests pin the exact literal values — the whole point of the
module is that the strings never drift between the agent and an SDK peer.
"""

from __future__ import annotations

from minimax_code.mcp import MCP_CALL, MCP_SDK, MCP_SDK_CALL, MCP_SERVERS
from minimax_code.mcp.wire import (
    MCP_CALL as MCP_CALL_FROM_MODULE,
)
from minimax_code.mcp.wire import (
    MCP_SDK as MCP_SDK_FROM_MODULE,
)


def test_constants_reexported():
    assert MCP_CALL is MCP_CALL_FROM_MODULE
    assert MCP_SDK is MCP_SDK_FROM_MODULE


def test_call_method_value():
    assert MCP_CALL == "x.ai/mcp/call"


def test_sdk_call_method_value():
    assert MCP_SDK_CALL == "x.ai/mcp/sdk_call"


def test_servers_meta_key_value():
    assert MCP_SERVERS == "x.ai/mcp/servers"


def test_sdk_capability_flag_value():
    assert MCP_SDK == "x.ai/mcp/sdk"


def test_all_constants_share_namespace_prefix():
    """All four live under the x.ai/mcp/ namespace — the protocol's root."""
    for const in (MCP_CALL, MCP_SDK_CALL, MCP_SERVERS, MCP_SDK):
        assert const.startswith("x.ai/mcp/")


def test_call_and_sdk_call_are_distinct():
    """Forward (call) and reverse (sdk_call) methods are disjoint — no shared string."""
    assert MCP_CALL != MCP_SDK_CALL
    assert MCP_CALL.endswith("/call")
    assert MCP_SDK_CALL.endswith("/sdk_call")


def test_constants_are_str_type():
    for const in (MCP_CALL, MCP_SDK_CALL, MCP_SERVERS, MCP_SDK):
        assert isinstance(const, str)
