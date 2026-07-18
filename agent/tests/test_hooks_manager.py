"""Unit tests for HookManager (R7) — the facade binding registry + executor.

Uses real ``python -c`` subprocess hooks so the full stdin/stdout/decision
path is exercised end-to-end.
"""

from __future__ import annotations

import sys

from minimax_code.hooks import HookManager, PreToolOutcome


def _py(script: str) -> list[str]:
    return [sys.executable, "-c", script]


def _block_hook(reason: str = "nope") -> dict:
    script = (
        f"import json; print(json.dumps({{'block': True, "
        f"'block_reason': {reason!r}}}))"
    )
    return {"command": _py(script)}


def _pass_hook() -> dict:
    return {"command": _py("print('ok')")}


# ---------------------------------------------------------------------------
# pre_tool_use
# ---------------------------------------------------------------------------


async def test_pre_tool_use_no_hooks_is_not_blocked() -> None:
    mgr = HookManager()
    outcome = await mgr.fire_pre_tool_use("s1", "edit_file", {"path": "a"})
    assert isinstance(outcome, PreToolOutcome)
    assert outcome.blocked is False
    assert outcome.should_run is True
    assert outcome.results == []


async def test_pre_tool_use_block_decision_blocks() -> None:
    mgr = HookManager()
    mgr.load_hooks({"pre_tool_use": [{**_block_hook("forbidden"), "matcher": {"tool_name": "edit_*"}}]})
    outcome = await mgr.fire_pre_tool_use("s1", "edit_file", {"path": "a"})
    assert outcome.blocked is True
    assert outcome.should_run is False
    assert outcome.block_reason == "forbidden"


async def test_pre_tool_use_first_block_wins_and_short_circuits() -> None:
    mgr = HookManager()
    # Two block hooks; only the first should run.
    mgr.load_hooks(
        {
            "pre_tool_use": [
                _block_hook("first"),
                _block_hook("second"),
            ]
        }
    )
    outcome = await mgr.fire_pre_tool_use("s1", "any_tool")
    assert outcome.blocked is True
    assert outcome.block_reason == "first"
    assert len(outcome.results) == 1  # short-circuited


async def test_pre_tool_use_pass_hook_does_not_block() -> None:
    mgr = HookManager()
    mgr.load_hooks({"pre_tool_use": [_pass_hook()]})
    outcome = await mgr.fire_pre_tool_use("s1", "any_tool")
    assert outcome.blocked is False
    assert outcome.should_run is True
    assert len(outcome.results) == 1


async def test_pre_tool_use_matcher_skips_unrelated_tool() -> None:
    mgr = HookManager()
    mgr.load_hooks(
        {"pre_tool_use": [{**_block_hook(), "matcher": {"tool_name": "edit_*"}}]}
    )
    outcome = await mgr.fire_pre_tool_use("s1", "read_file")
    assert outcome.blocked is False  # matcher didn't match read_file


# ---------------------------------------------------------------------------
# post_tool_use / session lifecycle
# ---------------------------------------------------------------------------


async def test_post_tool_use_runs_matching_hooks() -> None:
    mgr = HookManager()
    mgr.load_hooks({"post_tool_use": [_pass_hook()]})
    results = await mgr.fire_post_tool_use("s1", "edit_file", {"path": "a"}, {"ok": True})
    assert len(results) == 1
    assert results[0].ok


async def test_session_start_and_end_fire() -> None:
    mgr = HookManager()
    mgr.load_hooks(
        {
            "session_start": [_pass_hook()],
            "session_end": [_pass_hook()],
        }
    )
    start = await mgr.fire_session_start("s1")
    end = await mgr.fire_session_end("s1")
    assert len(start) == 1
    assert len(end) == 1
    assert start[0].ok and end[0].ok


async def test_crashing_hook_is_fail_open_in_manager() -> None:
    # A hook whose decision JSON is unparseable should not block.
    mgr = HookManager()
    mgr.load_hooks(
        {"pre_tool_use": [{"command": _py("print('not json at all')")}]}
    )
    outcome = await mgr.fire_pre_tool_use("s1", "any_tool")
    assert outcome.blocked is False
    assert len(outcome.results) == 1
