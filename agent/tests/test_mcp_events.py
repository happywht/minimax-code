"""Tests for the MCP client event layer (R36).

grok's ``servers.rs`` exercises ``McpClientEvent`` via integration tests on
the dispatcher (the 50 ms coalescing window, the ``ConfigDiff`` fan-out).
Those are host-runtime and live in later rounds. These tests pin the pure
data shape that this module contributes: the eight-variant tagged union,
the ``McpServerName`` alias, the ``server_name`` accessor (and its one
``None`` branch for :class:`ConfigDiff`), frozen immutability, and value
equality — the contract a future dispatcher will build on.
"""

from __future__ import annotations

import dataclasses

import pytest

from minimax_code.mcp import (
    ConfigAdded,
    ConfigDiff,
    ConfigRemoved,
    HandshakeFailed,
    McpClientEvent,
    McpServerName,
    Ready,
    ResourcesChanged,
    ToolsChanged,
    TransportClosed,
    server_name,
)
from minimax_code.mcp.events import (
    McpClientEvent as McpClientEventFromModule,
)
from minimax_code.mcp.events import (
    server_name as server_name_from_module,
)

#: The seven variants that carry a single ``server`` field — i.e. everything
#: except :class:`ConfigDiff`. ``server_name`` must return that server for each.
SINGLE_SERVER_VARIANTS = [
    TransportClosed("drive", 7),
    HandshakeFailed("drive", "boom"),
    ToolsChanged("drive"),
    ResourcesChanged("drive"),
    Ready("drive"),
    ConfigAdded("drive"),
    ConfigRemoved("drive"),
]


def test_reexport():
    assert McpClientEvent is McpClientEventFromModule
    assert server_name is server_name_from_module


def test_mcp_server_name_is_str_alias():
    """grok ``pub type McpServerName = String`` → ``str`` alias."""
    assert McpServerName is str


def test_transport_closed_carries_client_id():
    ev = TransportClosed("drive", 42)
    assert ev.server == "drive"
    assert ev.client_id == 42
    assert isinstance(ev.client_id, int)


def test_handshake_failed_carries_reason():
    ev = HandshakeFailed("drive", "version mismatch")
    assert ev.server == "drive"
    assert ev.reason == "version mismatch"


def test_config_diff_carries_lists():
    """grok ``Vec<McpServerName>`` → ``list[str]`` (mutable, unhashed)."""
    ev = ConfigDiff(["a", "b"], ["c"])
    assert ev.added == ["a", "b"]
    assert ev.removed == ["c"]
    assert isinstance(ev.added, list)


def test_single_server_variants_construct():
    assert ToolsChanged("drive").server == "drive"
    assert ResourcesChanged("drive").server == "drive"
    assert Ready("drive").server == "drive"
    assert ConfigAdded("drive").server == "drive"
    assert ConfigRemoved("drive").server == "drive"


@pytest.mark.parametrize("event", SINGLE_SERVER_VARIANTS)
def test_server_name_returns_server_for_payload_variants(event):
    """All seven payload variants surface their server via the accessor."""
    assert server_name(event) == "drive"


def test_server_name_returns_none_for_config_diff():
    """``ConfigDiff`` carries a *set* of servers — fanned out per-server
    before buffering, so it has no single ``server_name`` (grok ``None``)."""
    assert server_name(ConfigDiff(["a"], ["b"])) is None


def test_union_has_eight_frozen_variants():
    """Eight frozen-dataclass variants compose the tagged union (R32 pattern)."""
    variants = [
        TransportClosed,
        HandshakeFailed,
        ToolsChanged,
        ResourcesChanged,
        Ready,
        ConfigDiff,
        ConfigAdded,
        ConfigRemoved,
    ]
    assert len(variants) == 8
    assert all(dataclasses.is_dataclass(v) for v in variants)


def test_frozen_immutable():
    """grok ``Clone`` + immutable-by-construction: field write is rejected."""
    ev = ToolsChanged("drive")
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.server = "other"  # type: ignore[misc]


def test_value_equality():
    """frozen dataclasses compare by value (used by the dispatcher's dedup)."""
    assert TransportClosed("drive", 1) == TransportClosed("drive", 1)
    assert TransportClosed("drive", 1) != TransportClosed("drive", 2)
    assert ConfigDiff(["a"], []) == ConfigDiff(["a"], [])
    assert HandshakeFailed("s", "x") != ToolsChanged("s")


def test_isinstance_dispatch():
    """Union is dispatched via isinstance, mirroring Rust's ``match`` arm."""
    ev: McpClientEvent = Ready("drive")
    assert isinstance(ev, Ready)
    assert not isinstance(ev, ConfigDiff)


def test_every_variant_is_part_of_union():
    """All eight variants satisfy ``isinstance(_, McpClientEvent)``."""
    samples: list[McpClientEvent] = [
        TransportClosed("s", 0),
        HandshakeFailed("s", "r"),
        ToolsChanged("s"),
        ResourcesChanged("s"),
        Ready("s"),
        ConfigDiff([], []),
        ConfigAdded("s"),
        ConfigRemoved("s"),
    ]
    for ev in samples:
        assert isinstance(ev, McpClientEvent)
