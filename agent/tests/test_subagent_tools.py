"""Tests for sub-agent tools exposed to AgentCore."""

from __future__ import annotations

from minimax_code.agent.tools import get_default_registry


def test_subagent_tools_are_registered() -> None:
    names = set(get_default_registry().names())
    assert "list_subagents" in names
    assert "spawn_subagent" in names
