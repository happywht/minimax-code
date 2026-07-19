"""Plan-mode transition / decision shapes (R67).

Fusion of grok-build's ``xai-grok-workspace-types::types::plan_mode``.

Two adjacent-tagged payloads:

* :class:`PlanModeTransition` — emitted via ``NeedPlanModeChange``
  chunks to request entering or exiting plan mode. ``Enter`` carries an
  optional draft plan; ``Exit`` carries the optional final plan.
* :class:`PlanModeDecision` — the approver's verdict: ``Approve`` /
  ``Defer`` (unit variants) or ``Reject { feedback }``.
"""

from __future__ import annotations

from minimax_code.workspace_types._tagged import AdjacentTagged

__all__ = ["PlanModeTransition", "PlanModeDecision"]


class PlanModeTransition(AdjacentTagged):
    """Request to enter / exit plan mode (adjacent-tagged).

    Wire shapes::

        {"type": "enter", "data": {"plan": "<draft or null>"}}
        {"type": "exit",  "data": {"final_plan": "<final or null>"}}

    Both payloads carry an ``Option<String>`` — ``None`` serialises as
    ``null`` (no skip).
    """

    _VARIANTS = ("enter", "exit")

    @classmethod
    def enter(cls, plan: str | None = None) -> PlanModeTransition:
        """Request entering plan mode with an optional draft ``plan``."""
        return cls("enter", {"plan": plan})

    @classmethod
    def exit(cls, final_plan: str | None = None) -> PlanModeTransition:
        """Request exiting plan mode with the optional ``final_plan``."""
        return cls("exit", {"final_plan": final_plan})


class PlanModeDecision(AdjacentTagged):
    """Approver's verdict on a plan (adjacent-tagged).

    Wire shapes::

        {"type": "approve", "data": null}
        {"type": "reject",  "data": {"feedback": "<text or null>"}}
        {"type": "defer",   "data": null}

    ``Approve`` and ``Defer`` are unit variants (``data`` is ``null``);
    ``Reject`` carries an ``Option<String>`` feedback.
    """

    _VARIANTS = ("approve", "reject", "defer")

    @classmethod
    def approve(cls) -> PlanModeDecision:
        """Approve the plan (unit variant)."""
        return cls("approve", None)

    @classmethod
    def reject(cls, feedback: str | None = None) -> PlanModeDecision:
        """Reject the plan with optional ``feedback``."""
        return cls("reject", {"feedback": feedback})

    @classmethod
    def defer(cls) -> PlanModeDecision:
        """Defer the decision (unit variant)."""
        return cls("defer", None)
