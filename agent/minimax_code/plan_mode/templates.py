"""Plan-mode reminder templates (R26).

Ports the five template strings from grok-build's ``plan_mode`` module
verbatim. grok renders them through MiniJinja with
``${{ tools.by_kind.X }}`` / ``${{ plan_path }}`` placeholders and a
``${%- if plan_has_content %}`` conditional. MiniMax has no Jinja
dependency, so the templates are kept verbatim (placeholders intact for
any future full renderer) and a thin :func:`render_reminder` helper
resolves the slots the host actually populates today — ``plan_path``,
``plan_has_content`` (the conditional), and ``tools.by_kind.<kind>`` tool
names — with plain regex substitution.

The raw template constants stay available unchanged; callers that prefer
a different rendering engine can take them directly.
"""

from __future__ import annotations

import re
from collections.abc import Mapping

__all__ = [
    "PLAN_MODE_REMINDER_FULL_TEMPLATE",
    "PLAN_MODE_REMINDER_SPARSE_TEMPLATE",
    "PLAN_MODE_REENTRY_REMINDER_TEMPLATE",
    "PLAN_MODE_EXIT_REMINDER_TEMPLATE",
    "PLAN_MODE_EDIT_REJECTED_TEMPLATE",
    "render_reminder",
]

#: Full plan-mode reminder (plan-file write rules + turn-ending tools).
#: ``${{ plan_path }}`` / ``${{ tools.by_kind.X }}`` are resolved by
#: :func:`render_reminder`; the ``${%- if plan_has_content %}`` conditional
#: picks the "exists" vs "not written yet" branch.
PLAN_MODE_REMINDER_FULL_TEMPLATE = """\
Plan mode is active. Do not make any edits or writes to the system.

## Plan File:
${%- if plan_has_content %}
A plan file exists at ${{ plan_path }}. You can read it and make edits using the ${{ tools.by_kind.edit }} tool.
${%- else %}
No plan written yet. Write your plan to ${{ plan_path }} using the ${{ tools.by_kind.edit }} tool.
${%- endif %}

You should build your plan by writing to or editing this file. Note that this is the only file you are allowed to edit.

Your turn should only end with either ${{ tools.by_kind.ask_user }} to clarify requirements or ${{ tools.by_kind.exit_plan }} to present your plan to the user."""

#: Sparse plan-mode reminder — static read-only nudge for alternating turns.
#: No placeholders; saves tokens once the model has seen the full reminder.
PLAN_MODE_REMINDER_SPARSE_TEMPLATE = (
    "Plan mode is still active. Do not make any edits or writes to the system "
    "except for the plan file."
)

#: Reentry reminder — injected when entering plan mode a second+ time in a
#: session. ``${{ plan_path }}`` / ``${{ tools.by_kind.X }}`` resolved by
#: :func:`render_reminder`.
PLAN_MODE_REENTRY_REMINDER_TEMPLATE = """\
## Returning to Plan Mode

You are entering plan mode again after having previously exited it. A plan file exists at ${{ plan_path }} from your previous planning session.

Your turn should only end with either ${{ tools.by_kind.ask_user }} to clarify requirements or ${{ tools.by_kind.exit_plan }} to present your plan to the user."""

#: One-shot exit reminder — injected once after exiting plan mode via toggle.
#: No placeholders.
PLAN_MODE_EXIT_REMINDER_TEMPLATE = (
    "You have exited plan mode. You can now make edits, run tools, and take actions."
)

#: Rejection message for an edit outside the plan file while plan mode is
#: active. Returned as the tool result so the model learns the only editable
#: path. ``${{ plan_path }}`` resolved by :func:`render_reminder`.
PLAN_MODE_EDIT_REJECTED_TEMPLATE = (
    "Rejected: file edits are not allowed in plan mode - the only editable file "
    "is the plan file (${{ plan_path }})."
)

# ``${%- if plan_has_content %}A${%- else %}B${%- endif %}`` — keep the branch
# text (group 1 / 2), drop the marker lines themselves.
_IF_RE = re.compile(
    r"\$\{%-?\s*if\s+plan_has_content\s*%\}(.*?)"
    r"\$\{%-?\s*else\s*%\}(.*?)"
    r"\$\{%-?\s*endif\s*%\}",
    re.DOTALL,
)
# ``${{ tools.by_kind.<kind> }}`` — resolved from the caller's tool-name map.
_TOOL_RE = re.compile(r"\$\{\{\s*tools\.by_kind\.(\w+)\s*\}\}")
# ``${{ plan_path }}`` — resolved from the caller's plan path.
_PLAN_PATH_RE = re.compile(r"\$\{\{\s*plan_path\s*\}\}")


def render_reminder(
    template: str,
    *,
    plan_path: str = "",
    plan_has_content: bool = False,
    tool_names: Mapping[str, str] | None = None,
) -> str:
    """Render a plan-mode template with the simple slots the host uses today.

    Resolves the ``${%- if plan_has_content %}`` conditional, the
    ``${{ plan_path }}`` slot, and every ``${{ tools.by_kind.<kind> }}``
    slot (looked up in ``tool_names``; an unknown kind is left intact so a
    missing mapping is visible rather than silently blanked). Any other
    text is left untouched.

    Parameters
    ----------
    template:
        One of the ``PLAN_MODE_*_TEMPLATE`` constants (or any string with
        the same slot syntax).
    plan_path:
        Value for ``${{ plan_path }}``.
    plan_has_content:
        Branch selector for the ``${%- if plan_has_content %}`` conditional.
    tool_names:
        ``{kind: client_facing_name}`` — e.g. ``{"edit": "search_replace"}``.
    """
    names = tool_names or {}

    def _if_sub(match: re.Match[str]) -> str:
        return match.group(1) if plan_has_content else match.group(2)

    out = _IF_RE.sub(_if_sub, template)
    out = _PLAN_PATH_RE.sub(lambda _: plan_path, out)
    out = _TOOL_RE.sub(
        lambda m: names.get(m.group(1), m.group(0)), out
    )
    return out
