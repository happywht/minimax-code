"""Intra/inter-compaction config + trigger + reminder + observers (R28-R30).

Ports the host-agnostic slices of grok-build's ``xai-grok-compaction`` crate:

* **R28** — intra-compaction configuration types and the pure trigger-decision
  function (the first consumer of R27's token estimation).
* **R29** — the post-compaction active-agent reminder formatter (rebuilds the
  running-tasks / todo / subagent state and wraps it in ``<system-reminder>``).
* **R30** — the compaction observability seam: ``CompactionTarget`` plus the
  intra/inter observer base classes, the metrics-backend-free mirror of the
  R20-R21 resilience observer stack.

The heavier select / sample / guard / commit passes (``compact.rs``,
``sampler.rs``, ``select.rs``, ``CompactionStreamProc``) are host-integration
layers and remain future rounds; this package is the language-agnostic core
they will build on.
"""

from __future__ import annotations

from .config import (
    DEFAULT_COMPACTION_MODEL_NAME,
    IntraCompactionConfig,
    IntraCompactionMode,
    IntraSummarizer,
)
from .observers import (
    NULL_INTER_OBSERVER,
    NULL_INTRA_OBSERVER,
    CompactionTarget,
    InterCompactionObserver,
    IntraCompactionObserver,
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
    # config (R28)
    "DEFAULT_COMPACTION_MODEL_NAME",
    "IntraCompactionMode",
    "IntraSummarizer",
    "IntraCompactionConfig",
    # trigger (R28)
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
    # observers (R30)
    "CompactionTarget",
    "IntraCompactionObserver",
    "InterCompactionObserver",
    "NULL_INTRA_OBSERVER",
    "NULL_INTER_OBSERVER",
]
