"""Tests for the MCP liveness decision layer (R35).

grok's ``liveness.rs`` has no ``#[cfg(test)]`` module of its own — the state
machine is exercised via integration tests on ``McpClient``. These tests pin
the pure ``classify_liveness`` decision table (the five-row matrix from the
module doc) plus the enum shapes (member counts, hashability for the
coalescing key, singleton identity for grok's ``Copy`` semantics) and the
default poll interval.
"""

from __future__ import annotations

import pytest

from minimax_code.mcp import (
    DEFAULT_POLL_INTERVAL_MS,
    ClientStateKind,
    LivenessCheck,
    McpClientEventKind,
    classify_liveness,
)
from minimax_code.mcp.liveness import (
    ClientStateKind as ClientStateKindFromModule,
)
from minimax_code.mcp.liveness import (
    LivenessCheck as LivenessCheckFromModule,
)
from minimax_code.mcp.liveness import (
    classify_liveness as classify_from_module,
)

NON_READY_STATES = [
    ClientStateKind.EMPTY,
    ClientStateKind.PENDING,
    ClientStateKind.INITIALIZING,
]


def test_reexport():
    assert ClientStateKind is ClientStateKindFromModule
    assert LivenessCheck is LivenessCheckFromModule
    assert classify_liveness is classify_from_module


def test_default_poll_interval():
    """grok Duration::from_millis(500) — mean detection latency < 1s."""
    assert DEFAULT_POLL_INTERVAL_MS == 500
    assert isinstance(DEFAULT_POLL_INTERVAL_MS, int)


def test_client_state_kind_has_four_variants():
    assert len(ClientStateKind) == 4
    assert {k.name for k in ClientStateKind} == {
        "EMPTY",
        "PENDING",
        "INITIALIZING",
        "READY",
    }


def test_liveness_check_has_three_variants():
    assert len(LivenessCheck) == 3
    assert {k.name for k in LivenessCheck} == {
        "HEALTHY",
        "TRANSPORT_CLOSED",
        "TRANSIENT",
    }


def test_event_kind_has_seven_variants():
    assert len(McpClientEventKind) == 7
    assert {k.name for k in McpClientEventKind} == {
        "TRANSPORT_CLOSED",
        "HANDSHAKE_FAILED",
        "TOOLS_CHANGED",
        "RESOURCES_CHANGED",
        "READY",
        "CONFIG_ADDED",
        "CONFIG_REMOVED",
    }


def test_ready_and_open_is_healthy():
    assert classify_liveness(ClientStateKind.READY, False) is LivenessCheck.HEALTHY


def test_ready_and_closed_is_transport_closed():
    assert classify_liveness(ClientStateKind.READY, True) is LivenessCheck.TRANSPORT_CLOSED


@pytest.mark.parametrize("state", NON_READY_STATES)
@pytest.mark.parametrize("closed", [False, True])
def test_non_ready_states_are_transient_regardless_of_transport(state, closed):
    """The false-positive guard: only Ready distinguishes closed/open.

    A re-handshake (Initializing) with a momentarily-closed transport must NOT
    fire TransportClosed — that was the bug grok's liveness module fixed.
    """
    assert classify_liveness(state, closed) is LivenessCheck.TRANSIENT


def test_event_kind_is_hashable_for_coalescing_key():
    """grok derives Hash — the dispatcher uses (server, kind) as a dict key."""
    by_kind = {McpClientEventKind.TRANSPORT_CLOSED: "first"}
    pair_key = ("drive", McpClientEventKind.READY)
    by_pair = {pair_key: "ready-event"}
    assert by_kind[McpClientEventKind.TRANSPORT_CLOSED] == "first"
    assert by_pair[("drive", McpClientEventKind.READY)] == "ready-event"


def test_enum_members_are_singletons():
    """grok Copy semantics — repeated access is identity-equal (no allocation)."""
    assert ClientStateKind.READY is ClientStateKind.READY
    assert LivenessCheck.HEALTHY is LivenessCheck.HEALTHY
    assert McpClientEventKind.TRANSPORT_CLOSED is McpClientEventKind.TRANSPORT_CLOSED


def test_classify_return_type_is_liveness_check():
    assert isinstance(classify_liveness(ClientStateKind.READY, False), LivenessCheck)
    assert isinstance(classify_liveness(ClientStateKind.EMPTY, True), LivenessCheck)
