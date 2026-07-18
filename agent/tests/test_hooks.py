"""Unit tests for the hooks subsystem (R6): registry loading/filtering and
the fail-open executor.

The executor tests spawn tiny ``python -c`` subprocesses so we exercise
real process creation, stdin piping, stdout parsing, and timeout paths
on the current platform.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from minimax_code.hooks import (
    HookConfig,
    HookContext,
    HookEvent,
    HookExecutor,
    HookRegistry,
)


def _py(script: str) -> list[str]:
    """An argv that runs ``script`` under the current interpreter."""
    return [sys.executable, "-c", script]


# ---------------------------------------------------------------------------
# registry
# ---------------------------------------------------------------------------


def test_registry_loads_claude_style_dict() -> None:
    reg = HookRegistry()
    data = {
        "hooks": {
            "pre_tool_use": [
                {"command": ["./lint.sh"], "matcher": {"tool_name": "edit_*"}, "timeout": 5}
            ],
            "session_start": [{"command": ["./welcome.sh"]}],
        }
    }
    count = reg.load_dict(data)
    assert count == 2
    assert reg.count(HookEvent.PRE_TOOL_USE) == 1
    assert reg.count(HookEvent.SESSION_START) == 1


def test_registry_accepts_bare_event_mapping() -> None:
    reg = HookRegistry()
    count = reg.load_dict({"session_end": [{"command": ["./bye.sh"]}]})
    assert count == 1
    assert reg.count(HookEvent.SESSION_END) == 1


def test_registry_skips_unknown_event() -> None:
    reg = HookRegistry()
    count = reg.load_dict({"not_a_real_event": [{"command": ["x"]}]})
    assert count == 0


def test_registry_filters_by_tool_glob() -> None:
    reg = HookRegistry()
    reg.load_dict(
        {
            "pre_tool_use": [
                {"command": ["a"], "matcher": {"tool_name": "edit_*"}},
                {"command": ["b"], "matcher": {"tool_name": ["read_*", "glob_*"]}},
                {"command": ["c"]},  # no matcher → all tools
            ]
        }
    )
    hits = reg.for_event(HookEvent.PRE_TOOL_USE, tool_name="edit_file")
    commands = [h.command[0] for h in hits]
    assert commands == ["a", "c"]  # b doesn't match, c matches all

    hits_read = reg.for_event(HookEvent.PRE_TOOL_USE, tool_name="glob_search")
    assert [h.command[0] for h in hits_read] == ["b", "c"]


def test_registry_default_matcher_matches_all_tools() -> None:
    reg = HookRegistry()
    reg.add(HookConfig(event=HookEvent.POST_TOOL_USE, command=["x"]))
    assert reg.for_event(HookEvent.POST_TOOL_USE, tool_name="anything") != []
    assert reg.for_event(HookEvent.POST_TOOL_USE, tool_name=None) != []


def test_registry_load_file_round_trip(tmp_path: Path) -> None:
    cfg = tmp_path / "hooks.json"
    cfg.write_text(json.dumps({"hooks": {"session_start": [{"command": ["x"]}]}}), encoding="utf-8")
    reg = HookRegistry()
    assert reg.load_file(cfg) == 1
    # Missing file → 0, fail-open.
    assert HookRegistry().load_file(tmp_path / "nope.json") == 0


# ---------------------------------------------------------------------------
# executor
# ---------------------------------------------------------------------------


async def test_executor_parses_block_decision_from_stdout() -> None:
    script = (
        "import sys, json; json.load(sys.stdin); "
        "print(json.dumps({'block': True, 'block_reason': 'not allowed'}))"
    )
    hook = HookConfig(event=HookEvent.PRE_TOOL_USE, command=_py(script), timeout=5)
    ctx = HookContext(event=HookEvent.PRE_TOOL_USE, tool_name="edit_file")
    result = await HookExecutor().run(hook, ctx)
    assert result.ok
    assert result.decision is not None
    assert result.decision.block is True
    assert result.decision.block_reason == "not allowed"


async def test_executor_no_stdout_yields_no_decision() -> None:
    hook = HookConfig(event=HookEvent.POST_TOOL_USE, command=_py("print('')"), timeout=5)
    ctx = HookContext(event=HookEvent.POST_TOOL_USE)
    result = await HookExecutor().run(hook, ctx)
    assert result.ok
    assert result.decision is None


async def test_executor_timeout_is_fail_open() -> None:
    hook = HookConfig(
        event=HookEvent.PRE_TOOL_USE,
        command=_py("import time; time.sleep(5)"),
        timeout=0.3,
    )
    result = await HookExecutor().run(hook, HookContext(event=HookEvent.PRE_TOOL_USE))
    assert result.timed_out is True
    assert result.ok is False
    assert result.decision is None  # fail-open: no decision


async def test_executor_spawn_failure_is_fail_open() -> None:
    hook = HookConfig(
        event=HookEvent.SESSION_START,
        command=["this-command-does-not-exist-xyz-123"],
        timeout=2,
    )
    result = await HookExecutor().run(hook, HookContext(event=HookEvent.SESSION_START))
    assert result.error is not None
    assert result.ok is False


async def test_executor_nonzero_exit_records_code() -> None:
    hook = HookConfig(
        event=HookEvent.SESSION_END,
        command=_py("import sys; sys.exit(2)"),
        timeout=5,
    )
    result = await HookExecutor().run(hook, HookContext(event=HookEvent.SESSION_END))
    assert result.exit_code == 2
    assert result.ok is False


async def test_executor_tolerates_leading_log_lines_before_json() -> None:
    script = (
        "print('starting hook...'); "
        "print('{\"block\": true}')"
    )
    hook = HookConfig(event=HookEvent.PRE_TOOL_USE, command=_py(script), timeout=5)
    result = await HookExecutor().run(hook, HookContext(event=HookEvent.PRE_TOOL_USE))
    assert result.decision is not None
    assert result.decision.block is True
