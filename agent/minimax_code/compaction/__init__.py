"""Intra-compaction config + trigger (R28).

Ports the first deliverable slice of grok-build's ``xai-grok-compaction``
crate: the configuration types and the pure trigger-decision function. The
heavier select / sample / guard / commit passes (``compact.rs``,
``sampler.rs``, ``select.rs``, ``observer``, ``traits``) are future rounds;
this package is the language-agnostic policy + decision core that they will
build on, and the first real consumer of R27's token estimation.
"""

from __future__ import annotations

from .config import (
    DEFAULT_COMPACTION_MODEL_NAME,
    IntraCompactionConfig,
    IntraCompactionMode,
    IntraSummarizer,
)
from .reminder import (
    ActiveAgentReminderState,
    BackgroundTask,
    RunningSubagent,
    SubagentToolNames,
    TodoItem,
    TodoStatus,
    append_reminder_block,
    format_active_agent_reminder,
    format_active_agent_sections,
    section_background_tasks,
    section_running_subagents,
    section_todo_list,
    wrap_system_reminder,
)
from .trigger import IntraCompactionTrigger, should_compact

__all__ = [
    # config
    "DEFAULT_COMPACTION_MODEL_NAME",
    "IntraCompactionMode",
    "IntraSummarizer",
    "IntraCompactionConfig",
    # trigger
    "IntraCompactionTrigger",
    "should_compact",
    # reminder (R29)
    "TodoStatus",
    "SubagentToolNames",
    "TodoItem",
    "BackgroundTask",
    "RunningSubagent",
    "ActiveAgentReminderState",
    "section_background_tasks",
    "section_todo_list",
    "section_running_subagents",
    "format_active_agent_sections",
    "wrap_system_reminder",
    "format_active_agent_reminder",
    "append_reminder_block",
]
