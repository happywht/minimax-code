"""Tests for the post-compaction active-agent reminder formatter (R29).

Mirrors grok-build's ``reminder.rs`` ``#[cfg(test)]`` module case-for-case —
every Rust test becomes a Python test with the same inputs and the same
substring / ordering assertions — then adds Python-specific guards for the
``TodoStatus`` helpers and the empty-state predicate.

All pure string formatting, so every test is a literal assertion over
constructed state — no fixtures, no async, no IO.
"""

from __future__ import annotations

import pytest

from minimax_code.compaction.reminder import (
    ActiveAgentReminderState,
    BackgroundTask,
    RunningSubagent,
    SubagentToolNames,
    TodoItem,
    TodoStatus,
    append_reminder_block,
    format_active_agent_reminder,
    section_background_tasks,
    section_running_subagents,
    section_todo_list,
    wrap_system_reminder,
)

# -- shared tool-name fixtures (mirror grok's tools_native / tools_renamed) --


def _tools_native() -> SubagentToolNames:
    return SubagentToolNames(poll="get_task_output", cancel="kill_task")


def _tools_renamed() -> SubagentToolNames:
    return SubagentToolNames(
        poll="get_command_or_subagent_output",
        cancel="kill_command_or_subagent",
    )


# =========================================================================
# Python-specific guards for the TodoStatus + state helpers
# =========================================================================


@pytest.mark.parametrize(
    ("status", "actionable"),
    [
        (TodoStatus.PENDING, True),
        (TodoStatus.IN_PROGRESS, True),
        (TodoStatus.COMPLETED, False),
        (TodoStatus.CANCELLED, False),
    ],
)
def test_todo_status_is_actionable(status: TodoStatus, actionable: bool):
    assert status.is_actionable() is actionable


@pytest.mark.parametrize(
    ("status", "tag"),
    [
        (TodoStatus.PENDING, "[pending]"),
        (TodoStatus.IN_PROGRESS, "[in_progress]"),
        (TodoStatus.COMPLETED, "[completed]"),
        (TodoStatus.CANCELLED, "[cancelled]"),
    ],
)
def test_todo_status_tag(status: TodoStatus, tag: str):
    assert status.tag() == tag


def test_active_agent_state_default_is_empty():
    assert ActiveAgentReminderState().is_empty() is True
    assert ActiveAgentReminderState().has_actionable_todos() is False


def test_active_agent_state_completed_only_todos_is_empty():
    """Completed/cancelled todos are not actionable → state counts as empty."""
    state = ActiveAgentReminderState(
        todos=[TodoItem(id="1", content="done", status=TodoStatus.COMPLETED)]
    )
    assert state.has_actionable_todos() is False
    assert state.is_empty() is True


# =========================================================================
# reminder.rs #[cfg(test)] mirror
# =========================================================================


def test_empty_state_is_none():
    assert format_active_agent_reminder(ActiveAgentReminderState(), _tools_native()) is None


def test_missing_tool_names_omits_subagent_section_only():
    """No tool names → subagents vanish, but background tasks still render."""
    agents = [
        RunningSubagent(
            subagent_id="sa-1", subagent_type=None, description="x", elapsed_secs=1
        )
    ]
    state = ActiveAgentReminderState(running_subagents=agents)
    assert format_active_agent_reminder(state, None) is None

    cmds = [
        BackgroundTask(
            task_id="bg-1",
            command="npm run dev",
            status="running",
            tool_name="run_terminal_command",
        )
    ]
    state = ActiveAgentReminderState(running_commands=cmds)
    out = format_active_agent_reminder(state, None)
    assert out is not None
    assert "## Running Background Tasks" in out
    assert "## Running Subagents" not in out


def test_renders_chat_style_subagent_ids_verbatim():
    """Chat-style UUIDs (no type) render verbatim — no synthesis, no type label."""
    agents = [
        RunningSubagent(
            subagent_id="019ea7f0-cb66-7aa2-9a09-488a3a795795",
            subagent_type=None,
            description="deploy staging",
            elapsed_secs=42,
        ),
        RunningSubagent(subagent_id="sa-2", elapsed_secs=5),
    ]
    state = ActiveAgentReminderState(running_subagents=agents)
    out = format_active_agent_reminder(state, _tools_native())
    assert out is not None
    assert out.startswith("<system-reminder>")
    assert out.endswith("</system-reminder>")
    assert "subagent_id: `019ea7f0-cb66-7aa2-9a09-488a3a795795`" in out
    assert 'task: "deploy staging" (running for 42s)' in out
    assert "subagent_id: `sa-2` (running for 5s)" in out
    assert "task-019ea7f0" not in out
    assert "type:" not in out


def test_renders_build_style_subagent_with_type():
    """Build-style carries a type label between id and task."""
    agents = [
        RunningSubagent(
            subagent_id="sub-1",
            subagent_type="explore",
            description="find files",
            elapsed_secs=5,
        )
    ]
    state = ActiveAgentReminderState(running_subagents=agents)
    out = format_active_agent_reminder(state, _tools_renamed())
    assert out is not None
    assert (
        "- subagent_id: `sub-1`, type: `explore`, task: \"find files\" (running for 5s)"
        in out
    )


def test_uses_renamed_tool_names_verbatim():
    """A renamed manifest's tool names are interpolated verbatim."""
    agents = [
        RunningSubagent(subagent_id="sa-1", description="x", elapsed_secs=1)
    ]
    state = ActiveAgentReminderState(running_subagents=agents)
    out = format_active_agent_reminder(state, _tools_renamed())
    assert out is not None
    assert "get_command_or_subagent_output" in out
    assert "get_task_output" not in out


def test_renders_background_tasks():
    """Background tasks — with and without a tool name — render on separate lines."""
    cmds = [
        BackgroundTask(
            task_id="019f1723-a9f0-76f2-98ae-56af965922f6",
            command="npm run dev",
            status="running",
            tool_name="run_terminal_command",
        ),
        BackgroundTask(
            task_id="bg-2",
            command="cargo watch -x test",
            status="running",
        ),
    ]
    state = ActiveAgentReminderState(running_commands=cmds)
    out = format_active_agent_reminder(state, None)
    assert out is not None
    assert (
        '- "019f1723-a9f0-76f2-98ae-56af965922f6": `npm run dev` '
        "(running, run_terminal_command)"
    ) in out
    assert '- "bg-2": `cargo watch -x test` (running)' in out


def test_renders_todo_list_without_tool_names():
    """TODO list needs no tool names; completed items collapse to a count."""
    todos = [
        TodoItem(id="1", content="scaffold the app", status=TodoStatus.COMPLETED),
        TodoItem(id="2", content="wire the API", status=TodoStatus.IN_PROGRESS),
        TodoItem(id="3", content="write tests", status=TodoStatus.PENDING),
    ]
    state = ActiveAgentReminderState(todos=todos)
    out = format_active_agent_reminder(state, None)
    assert out is not None
    assert "- [in_progress] 2: wire the API" in out
    assert "- [pending] 3: write tests" in out
    assert "(1 completed)" in out
    assert "scaffold the app" not in out


def test_only_completed_todos_is_none():
    """A list of only completed todos has nothing actionable → None."""
    todos = [TodoItem(id="1", content="done", status=TodoStatus.COMPLETED)]
    state = ActiveAgentReminderState(todos=todos)
    assert format_active_agent_reminder(state, None) is None


def test_section_order_background_todo_subagent():
    """Fixed order: Background Tasks → TODO → Subagents."""
    cmds = [
        BackgroundTask(task_id="t1", command="npm run dev", status="running")
    ]
    todos = [TodoItem(id="2", content="wire the API", status=TodoStatus.IN_PROGRESS)]
    agents = [
        RunningSubagent(subagent_id="sa-1", description="deploy staging", elapsed_secs=1)
    ]
    state = ActiveAgentReminderState(
        running_commands=cmds, todos=todos, running_subagents=agents
    )
    out = format_active_agent_reminder(state, _tools_native())
    assert out is not None
    bg = out.index("## Running Background Tasks")
    todo = out.index("## TODO List")
    sub = out.index("## Running Subagents")
    assert bg < todo < sub


def test_wrap_system_reminder_joins_and_skips_blank():
    """Blank sections are dropped; the rest join with a blank line."""
    out = wrap_system_reminder(["## A\nx", "", "  ", "## B\ny"])
    assert out is not None
    assert out == "<system-reminder>\n## A\nx\n\n## B\ny\n</system-reminder>"
    assert wrap_system_reminder(iter([])) is None


def test_appends_after_blank_line():
    """A real reminder is appended after a blank-line separator."""
    assert append_reminder_block("SUMMARY", "REMINDER") == "SUMMARY\n\nREMINDER"


def test_append_noop_when_none_or_blank():
    """None / blank reminder leaves the summary untouched."""
    assert append_reminder_block("SUMMARY", None) == "SUMMARY"
    assert append_reminder_block("SUMMARY", "  \n\t ") == "SUMMARY"


# =========================================================================
# Extra Python guards — section-level entry points + trailer combinations
# =========================================================================


def test_section_background_tasks_none_when_empty():
    assert section_background_tasks([]) is None


def test_section_todo_list_trailer_combinations():
    """All four (completed, cancelled) trailer branches."""
    assert (
        section_todo_list(
            [TodoItem(id="1", content="a", status=TodoStatus.IN_PROGRESS)]
        )
        is not None
    )
    out_both = section_todo_list(
        [
            TodoItem(id="1", content="a", status=TodoStatus.IN_PROGRESS),
            TodoItem(id="2", content="b", status=TodoStatus.COMPLETED),
            TodoItem(id="3", content="c", status=TodoStatus.CANCELLED),
        ]
    )
    assert out_both is not None
    assert "(1 completed, 1 cancelled)" in out_both

    out_cancelled_only = section_todo_list(
        [
            TodoItem(id="1", content="a", status=TodoStatus.IN_PROGRESS),
            TodoItem(id="2", content="b", status=TodoStatus.CANCELLED),
        ]
    )
    assert out_cancelled_only is not None
    assert "(1 cancelled)" in out_cancelled_only
    assert "completed" not in out_cancelled_only


def test_section_running_subagents_none_when_empty():
    assert section_running_subagents([], _tools_native()) is None
