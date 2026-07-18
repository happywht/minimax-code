"""Post-compaction active-agent reminder formatting (R29).

Ports grok-build's ``xai-grok-compaction::reminder`` — the host-agnostic
formatting layer that rebuilds the *active agent state* (running background
tasks, TODO list, running sub-agents) and wraps it in a ``<system-reminder>``
block appended to a compaction summary. The point: after a context compaction
the model still knows what it was in the middle of, so it does not abandon
running tasks or lose the todo list.

This is the output end of the compaction chain (R28 decides *when* to
compact; R29 preserves *what was in flight*) and it is pure string
formatting — no async, no LLM call, no host state. The three sections map
cleanly onto MiniMax Code primitives that already exist:

* ``BackgroundTask``    ↔ background terminal tasks
* ``TodoItem``          ↔ the task list (``TaskCreate`` / ``TaskList``)
* ``RunningSubagent``   ↔ ``SubAgentRuntime`` children

so a future host-wiring round can render a reminder straight from live
state without re-implementing the layout.

Borrowed-view parity
--------------------
grok passes ``&str`` / ``&[T]`` *borrowed views* over live harness state so
long fields are not cloned just to format. Python has no lifetimes, so the
dataclasses here hold plain ``str`` / ``list`` references — semantically the
same "do not own a private copy of the world" contract, just expressed by
reference rather than by lifetime.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from enum import Enum

__all__ = [
    # types
    "TodoStatus",
    "SubagentToolNames",
    "TodoItem",
    "BackgroundTask",
    "RunningSubagent",
    "ActiveAgentReminderState",
    # formatters
    "section_background_tasks",
    "section_todo_list",
    "section_running_subagents",
    "format_active_agent_sections",
    "wrap_system_reminder",
    "format_active_agent_reminder",
    "append_reminder_block",
]


class TodoStatus(Enum):
    """Status of a todo item in the post-compaction reminder."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"

    def is_actionable(self) -> bool:
        """A todo the agent should keep working on (pending or in-progress)."""
        return self in (TodoStatus.PENDING, TodoStatus.IN_PROGRESS)

    def tag(self) -> str:
        """The bracketed label rendered in front of the todo id."""
        if self is TodoStatus.PENDING:
            return "[pending]"
        if self is TodoStatus.IN_PROGRESS:
            return "[in_progress]"
        if self is TodoStatus.COMPLETED:
            return "[completed]"
        return "[cancelled]"


@dataclass(frozen=True)
class SubagentToolNames:
    """Model-facing poll/cancel tool names from the current toolset.

    Never hard-coded: a client manifest can rename them, so callers pass the
    resolved names and the formatter interpolates them verbatim.
    """

    poll: str
    cancel: str


@dataclass(frozen=True)
class TodoItem:
    """A single todo line rendered under ``## TODO List``."""

    id: str
    content: str
    status: TodoStatus


@dataclass(frozen=True)
class BackgroundTask:
    """A still-running background task. ``task_id`` is rendered verbatim."""

    task_id: str
    command: str
    #: Parenthetical status (typically ``"running"``).
    status: str
    #: Optional tool name appended after the status.
    tool_name: str | None = None


@dataclass(frozen=True)
class RunningSubagent:
    """A still-running sub-agent. ``subagent_id`` is rendered verbatim.

    ``subagent_type`` / ``description`` are optional so chat (no type,
    optional desc) and build (both present) share one line format.
    """

    subagent_id: str
    subagent_type: str | None = None
    description: str | None = None
    elapsed_secs: int = 0


@dataclass
class ActiveAgentReminderState:
    """Borrowed active-agent state for reminder rendering.

    All three slices default to empty; :meth:`is_empty` is true only when
    there is nothing actionable to remind the model about (note a list of
    only completed/cancelled todos counts as empty — nothing to *do*).
    """

    running_commands: list[BackgroundTask] = field(default_factory=list)
    todos: list[TodoItem] = field(default_factory=list)
    running_subagents: list[RunningSubagent] = field(default_factory=list)

    def is_empty(self) -> bool:
        return (
            not self.running_commands
            and not self.running_subagents
            and not self.has_actionable_todos()
        )

    def has_actionable_todos(self) -> bool:
        return any(t.status.is_actionable() for t in self.todos)


# ---------------------------------------------------------------------------
# Section formatters
# ---------------------------------------------------------------------------


def _format_background_line(t: BackgroundTask) -> str:
    if t.tool_name is not None:
        return f'- "{t.task_id}": `{t.command}` ({t.status}, {t.tool_name})'
    return f'- "{t.task_id}": `{t.command}` ({t.status})'


def section_background_tasks(tasks: list[BackgroundTask]) -> str | None:
    """``## Running Background Tasks`` block, or ``None`` when empty."""
    if not tasks:
        return None
    lines = "\n".join(_format_background_line(t) for t in tasks)
    return f"## Running Background Tasks\nThese tasks are still running:\n{lines}"


def _todo_trailer(completed: int, cancelled: int) -> str:
    """Count trailer for completed/cancelled todos (collapsed, not listed)."""
    if completed == 0 and cancelled == 0:
        return ""
    if cancelled == 0:
        return f"\n({completed} completed)"
    if completed == 0:
        return f"\n({cancelled} cancelled)"
    return f"\n({completed} completed, {cancelled} cancelled)"


def section_todo_list(todos: list[TodoItem]) -> str | None:
    """``## TODO List`` for actionable items, or ``None`` when none.

    Completed/cancelled items collapse to a count trailer rather than
    appearing as lines (the model does not need to re-act on them).
    """
    active = [t for t in todos if t.status.is_actionable()]
    if not active:
        return None
    active_lines = "\n".join(
        f"- {t.status.tag()} {t.id}: {t.content}" for t in active
    )
    completed = sum(1 for t in todos if t.status is TodoStatus.COMPLETED)
    cancelled = sum(1 for t in todos if t.status is TodoStatus.CANCELLED)
    trailer = _todo_trailer(completed, cancelled)
    return (
        "## TODO List\n"
        "This is your task list from before the conversation was compacted — it is still "
        "active. Keep working through the items below and update their status as you make "
        f"progress:\n{active_lines}{trailer}"
    )


def _format_subagent_line(s: RunningSubagent) -> str:
    head = f"subagent_id: `{s.subagent_id}`"
    if s.subagent_type is not None:
        head += f", type: `{s.subagent_type}`"
    if s.description is not None:
        head += f", task: \"{s.description}\""
    return f"- {head} (running for {s.elapsed_secs}s)"


def section_running_subagents(
    subagents: list[RunningSubagent], tools: SubagentToolNames
) -> str | None:
    """``## Running Subagents`` block, or ``None`` when empty.

    The poll/cancel tool names from ``tools`` are interpolated verbatim so a
    renamed manifest still points at the right tools.
    """
    if not subagents:
        return None
    lines = "\n".join(_format_subagent_line(s) for s in subagents)
    return (
        "## Running Subagents\n"
        "These subagents were launched before this compaction and are still running. "
        f"Use `{tools.poll}` with the subagent_id to check their status or retrieve results. "
        f"Use `{tools.cancel}` with the subagent_id to cancel a subagent.\n{lines}"
    )


def format_active_agent_sections(
    state: ActiveAgentReminderState,
    subagent_tools: SubagentToolNames | None,
) -> list[str]:
    """Common sections in order: Background Tasks → TODO → Subagents.

    Empty kinds are omitted; the subagents section is also omitted when
    ``subagent_tools`` is ``None`` (we cannot name the tools to manage them).
    """
    sections: list[str] = []
    bg = section_background_tasks(state.running_commands)
    if bg is not None:
        sections.append(bg)
    todo = section_todo_list(state.todos)
    if todo is not None:
        sections.append(todo)
    if subagent_tools is not None:
        sub = section_running_subagents(state.running_subagents, subagent_tools)
        if sub is not None:
            sections.append(sub)
    return sections


def wrap_system_reminder(sections: Iterable[str]) -> str | None:
    """Wrap non-empty sections in ``<system-reminder>…</system-reminder>``.

    Blank/whitespace-only sections are skipped; the rest are joined with a
    blank line. Returns ``None`` when nothing remains after filtering.
    """
    body_parts = [s for s in sections if s.strip() != ""]
    if not body_parts:
        return None
    body = "\n\n".join(body_parts)
    return f"<system-reminder>\n{body}\n</system-reminder>"


def format_active_agent_reminder(
    state: ActiveAgentReminderState,
    subagent_tools: SubagentToolNames | None,
) -> str | None:
    """Full active-agent-state ``<system-reminder>``, or ``None`` when empty."""
    return wrap_system_reminder(format_active_agent_sections(state, subagent_tools))


# ---------------------------------------------------------------------------
# Summary injection
# ---------------------------------------------------------------------------


def append_reminder_block(summary: str, reminder: str | None) -> str:
    """Append a trailing block to a compaction summary, separated by a blank line.

    Returns ``summary`` unchanged when ``reminder`` is ``None`` or blank.
    """
    if reminder is not None and reminder.strip() != "":
        return f"{summary}\n\n{reminder}"
    return summary
