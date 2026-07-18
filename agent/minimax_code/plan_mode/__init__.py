"""Plan-mode state machine (R26).

Ports grok-build's ``xai-grok-shell::session::plan_mode`` — the pure
:class:`PlanModeTracker` state machine
(``Inactive`` → ``Pending`` → ``Active`` → ``ExitPending``) plus the
:class:`PromptMode` enum and the five reminder templates.

No AgentCore / storage / async wiring lives in this package: pure
transition logic + a serializable snapshot, tested in isolation. The
host integration (injecting reminders into the conversation, blocking
write tools, persisting the snapshot) is a future iteration — this
package is the language-agnostic core that integration builds on.
"""

from __future__ import annotations

from .templates import (
    PLAN_MODE_EDIT_REJECTED_TEMPLATE,
    PLAN_MODE_EXIT_REMINDER_TEMPLATE,
    PLAN_MODE_REENTRY_REMINDER_TEMPLATE,
    PLAN_MODE_REMINDER_FULL_TEMPLATE,
    PLAN_MODE_REMINDER_SPARSE_TEMPLATE,
    render_reminder,
)
from .tracker import (
    PendingActivation,
    PlanModeSnapshot,
    PlanModeState,
    PlanModeTracker,
    PromptMode,
    is_markdown_file_path,
    is_plan_file_write,
)

__all__ = [
    # tracker
    "PlanModeState",
    "PromptMode",
    "PendingActivation",
    "PlanModeSnapshot",
    "PlanModeTracker",
    "is_plan_file_write",
    "is_markdown_file_path",
    # templates
    "PLAN_MODE_REMINDER_FULL_TEMPLATE",
    "PLAN_MODE_REMINDER_SPARSE_TEMPLATE",
    "PLAN_MODE_REENTRY_REMINDER_TEMPLATE",
    "PLAN_MODE_EXIT_REMINDER_TEMPLATE",
    "PLAN_MODE_EDIT_REJECTED_TEMPLATE",
    "render_reminder",
]
