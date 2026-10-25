"""Review adapters use the durable Native executor, with no paid requests."""

from unittest.mock import AsyncMock

import pytest
import test_skill_execution as skill_cases

from minimax_code.agent.skills._builtin.code_review import Provider, _diff_stats, _review_diff
from minimax_code.agent.skills.loader import Skill
from minimax_code.skill_execution import _private_model as actual_private_model
from minimax_code.storage.dao.budgets import BudgetsDAO

invoke = skill_cases.invoke
skill_env = skill_cases.skill_env

DIFF = "diff --git a/test.py b/test.py\n--- a/test.py\n+++ b/test.py\n@@ -1 +1,2 @@\n-x=1\n+x=2\n+print(x)\n"


def review_skill(state):
    skill = Skill(
        name="code-review",
        description="Review",
        when_to_use="Review diffs",
        body="Use supplied analysis tools and describe actual evidence.",
        path=state.root / "code-review",
        version="1",
        tools=["run_linter", "find_complex_functions"],
    )
    state.runtime.registry._skills[skill.skill_id] = skill
    state.runtime.register_tool_provider(skill, Provider())
    state.skill = skill
    state.runtime.invoke = AsyncMock(side_effect=AssertionError("Nested runtime must not execute"))


async def test_real_review_has_durable_stats_and_unparsed_text(skill_env):
    state = skill_env
    review_skill(state)
    ctx = await invoke(state, diff=DIFF)
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.await_args.args[0]
    assert receipt["text"] == receipt["output"] == "answer"
    assert receipt["comments"] == [] and receipt["stats"] == _diff_stats(DIFF)
    assert receipt == await state.dao.result_payload(receipt["run_id"])
    assert len(state.calls) == 1 and "```diff\n" + DIFF in state.calls[0]["messages"][-1]["content"]
    names = {item["function"]["name"] for item in state.calls[0]["tools"]}
    assert {"read_file", "run_linter", "find_complex_functions", "test_coverage"}.issubset(names)
    state.runtime.invoke.assert_not_awaited()


async def test_deterministic_review_walks_actual_diff_inside_same_core_and_persists(
    skill_env, monkeypatch
):
    state = skill_env
    review_skill(state)
    state.selected.mock = True
    state.selected.protocol = "mock"
    monkeypatch.setattr("minimax_code.skill_execution._private_model", actual_private_model)
    ctx = await invoke(state, diff=DIFF)
    ctx.reply_error.assert_not_awaited()
    receipt = ctx.reply.await_args.args[0]
    expected = _review_diff(DIFF)
    assert receipt["text"] == receipt["output"] == expected["text"]
    assert receipt["comments"] == expected["comments"] and receipt["stats"] == _diff_stats(DIFF)
    assert receipt["stub"] and receipt["status"] == "completed"
    assert receipt == await state.dao.result_payload(receipt["run_id"])
    run = await state.dao.get_run(receipt["run_id"])
    assert not await BudgetsDAO(state.db).attempts(run["metadata"]["budget_family_id"])
    chunks = [args for args in ctx.emit.await_args_list if args.args[0] == "agent.message_chunk"]
    assert chunks and any(args.args[1]["done"] for args in chunks)
    assert not state.calls


async def test_real_review_streams_with_durable_run_identity(skill_env):
    state = skill_env
    review_skill(state)
    ctx = await invoke(state, diff=DIFF)
    receipt = ctx.reply.await_args.args[0]
    chunks = [
        args.args[1] for args in ctx.emit.await_args_list if args.args[0] == "agent.message_chunk"
    ]
    assert chunks and all(chunk["session_id"] == "one" for chunk in chunks)
    assert any(chunk.get("run_id") == receipt["run_id"] for chunk in chunks)


async def test_generic_review_request_does_not_invent_diff_findings(skill_env):
    state = skill_env
    review_skill(state)
    ctx = await invoke(state, request="Inspect performance risks")
    receipt = ctx.reply.await_args.args[0]
    assert receipt["output"] == "answer" and "comments" not in receipt and "stats" not in receipt
    state.runtime.invoke.assert_not_awaited()


@pytest.mark.parametrize("diff", ["", " "])
async def test_empty_diff_is_invalid_before_send(skill_env, diff):
    review_skill(skill_env)
    ctx = await invoke(skill_env, diff=diff)
    ctx.reply.assert_not_awaited()
    assert "non-empty diff" in str(ctx.reply_error.await_args)
    assert not skill_env.calls


async def test_other_skill_cannot_claim_review_adapter(skill_env):
    ctx = await invoke(skill_env, diff=DIFF)
    ctx.reply.assert_not_awaited()
    assert "only for code-review" in str(ctx.reply_error.await_args)
    assert not skill_env.calls


async def test_review_replay_ignores_later_skill_removal(skill_env):
    state = skill_env
    review_skill(state)
    ctx = await invoke(state, diff=DIFF)
    state.runtime.registry._skills.clear()
    state.runtime._tool_providers.clear()
    retry = await invoke(state, diff=DIFF)
    assert retry.reply.await_args.args[0] == ctx.reply.await_args.args[0]
    assert len(state.calls) == 1
