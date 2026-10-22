from __future__ import annotations

import copy

import pytest

from minimax_code.conversation import project_history
from minimax_code.run_errors import RunPersistenceError


def call(call_id):
    return {"id": call_id, "type": "function", "function": {"name": "write_file", "arguments": "{}"}}


def test_missing_tool_result_is_explicitly_unknown_without_rewriting_evidence():
    rows = [{"role": "user", "content": "work"},
            {"role": "assistant", "content": "", "tool_calls": [call("a"), call("b")]},
            {"role": "tool", "tool_call_id": "a", "content": "saved"},
            {"role": "user", "content": "continue after review"}]
    before = copy.deepcopy(rows)
    history = project_history(rows)
    assert rows == before
    assert history[2] == rows[2]
    assert history[3]["role"] == "tool" and history[3]["tool_call_id"] == "b"
    assert "UNKNOWN" in history[3]["content"] and "may already have changed" in history[3]["content"]
    assert history[4] == rows[3]


def test_crash_at_end_of_history_does_not_leave_unpaired_tool_calls():
    rows = [{"role": "assistant", "tool_calls": [call("a"), call("b")]}]
    history = project_history(rows)
    assert [message.get("tool_call_id") for message in history[1:]] == ["a", "b"]


def test_complete_history_and_reused_ids_in_later_turn_are_preserved():
    turn = [{"role": "assistant", "content": "", "tool_calls": [call("a")]},
            {"role": "tool", "tool_call_id": "a", "content": "known result"}]
    rows = turn + [{"role": "user", "content": "next"}] + turn
    assert project_history(rows) == rows


@pytest.mark.parametrize("rows", [
    [{"role": "tool", "tool_call_id": "orphan", "content": "result"}],
    [{"role": "assistant", "tool_calls": [call("a"), call("a")]}],
    [{"role": "assistant", "tool_calls": [{"function": {"name": "write"}}]}],
    [{"role": "user", "tool_calls": [call("a")]}],
])
def test_corrupt_history_stops_instead_of_inventing_tool_calls(rows):
    with pytest.raises(RunPersistenceError):
        project_history(rows)
