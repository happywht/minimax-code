"""v1.1.0 auto-continue — env knobs and the block-continuation loop.

``_resolve_auto_continue`` reads the operator-facing environment flags;
``_run_with_auto_continue`` drives the block loop (block 1 + up to N
automatic continuations) on top of the unchanged single-block
``AgentCore.run`` semantics.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from minimax_code.agent.core import AgentConfig, AgentRunResult
from minimax_code.ipc.builtins import (
    _CONTINUE_PROMPT,
    _resolve_auto_continue,
    _run_with_auto_continue,
)

# ---------------------------------------------------------------------------
# _resolve_auto_continue
# ---------------------------------------------------------------------------


def test_resolve_defaults_to_off(monkeypatch) -> None:
    monkeypatch.delenv("MINIMAX_AUTO_CONTINUE", raising=False)
    monkeypatch.delenv("MINIMAX_AUTO_CONTINUE_MAX_BLOCKS", raising=False)
    assert _resolve_auto_continue() == (False, 5)


def test_resolve_enabled_via_env(monkeypatch) -> None:
    monkeypatch.setenv("MINIMAX_AUTO_CONTINUE", "1")
    monkeypatch.delenv("MINIMAX_AUTO_CONTINUE_MAX_BLOCKS", raising=False)
    assert _resolve_auto_continue() == (True, 5)


def test_resolve_truthy_tokens_and_cap(monkeypatch) -> None:
    monkeypatch.setenv("MINIMAX_AUTO_CONTINUE", "true")
    monkeypatch.setenv("MINIMAX_AUTO_CONTINUE_MAX_BLOCKS", "8")
    assert _resolve_auto_continue() == (True, 8)


def test_resolve_bad_cap_falls_back(monkeypatch) -> None:
    monkeypatch.setenv("MINIMAX_AUTO_CONTINUE", "yes")
    monkeypatch.setenv("MINIMAX_AUTO_CONTINUE_MAX_BLOCKS", "lots")
    assert _resolve_auto_continue() == (True, 5)


def test_resolve_cap_floor_is_one(monkeypatch) -> None:
    # 1 means "no auto-continuation room" — equivalent to disabled loop.
    monkeypatch.setenv("MINIMAX_AUTO_CONTINUE", "1")
    monkeypatch.setenv("MINIMAX_AUTO_CONTINUE_MAX_BLOCKS", "0")
    assert _resolve_auto_continue() == (True, 1)


# ---------------------------------------------------------------------------
# _run_with_auto_continue
# ---------------------------------------------------------------------------


def _core(*, auto: bool, max_blocks: int, results: list[AgentRunResult]) -> MagicMock:
    core = MagicMock()
    core.config = AgentConfig(auto_continue=auto, auto_continue_max_blocks=max_blocks)
    core.run = AsyncMock(side_effect=results)
    return core


def _result(
    *, truncated: bool = False, iterations: int = 12, compactions: int = 0
) -> AgentRunResult:
    return AgentRunResult(
        final_text="done" if not truncated else "budget hit",
        iterations=iterations,
        truncated=truncated,
        compactions=compactions,
    )


async def test_disabled_runs_single_block() -> None:
    first = _result(truncated=True, iterations=12, compactions=1)
    core = _core(auto=False, max_blocks=5, results=[first])
    result, blocks = await _run_with_auto_continue(core, session_id="s1", content="hi")
    assert blocks == 1
    core.run.assert_awaited_once()
    # No merge path taken — the result object passes through untouched.
    assert result is first


async def test_continues_until_final_answer() -> None:
    first = _result(truncated=True, iterations=12, compactions=2)
    second = _result(truncated=False, iterations=3, compactions=1)
    core = _core(auto=True, max_blocks=5, results=[first, second])
    result, blocks = await _run_with_auto_continue(core, session_id="s1", content="hi")
    assert blocks == 2
    assert core.run.await_count == 2
    # Block 2 must carry the fixed continuation prompt.
    assert core.run.await_args_list[1].kwargs["user_message"] == _CONTINUE_PROMPT
    # Terminal state from the last block; accounting summed across blocks.
    assert result.truncated is False
    assert result.final_text == "done"
    assert result.iterations == 15
    assert result.compactions == 3


async def test_stops_at_block_cap_still_truncated() -> None:
    results = [_result(truncated=True, iterations=6) for _ in range(5)]
    core = _core(auto=True, max_blocks=3, results=results)
    result, blocks = await _run_with_auto_continue(core, session_id="s1", content="hi")
    # Cap honoured: block 1 + 2 continuations, never the prepared 4th.
    assert blocks == 3
    assert core.run.await_count == 3
    assert result.truncated is True
    assert result.iterations == 18


async def test_cancelled_block_stops_the_loop() -> None:
    first = AgentRunResult(
        final_text="stopped", iterations=4, cancelled=True, truncated=True
    )
    second = _result(truncated=False)
    core = _core(auto=True, max_blocks=5, results=[first, second])
    result, blocks = await _run_with_auto_continue(core, session_id="s1", content="hi")
    assert blocks == 1
    core.run.assert_awaited_once()
    assert result.cancelled is True
