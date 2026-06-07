"""Workflow engine — evaluates triggers and runs step sequences.

A workflow consists of:
- A **trigger** (webhook / schedule / agent_event) with configuration
- A list of **steps** (condition / action) executed sequentially

Step types:
- ``condition`` — evaluates an ``if`` clause and branches
- ``action:notify`` — creates a notification
- ``action:send-message`` — injects a message into a session
- ``action:code-review`` — triggers a code review
- ``action:run-skill`` — invokes a skill

v0.7.0 — Mobile Connectivity Enhancement.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class WorkflowEngine:
    """Evaluate triggers and run step sequences."""

    async def evaluate_trigger(
        self,
        workflow: dict[str, Any],
        event: dict[str, Any],
    ) -> bool:
        """Check if *event* matches the workflow's trigger config.

        Parameters
        ----------
        workflow:
            Hydrated workflow dict (trigger_config is already parsed).
        event:
            Dict with keys like ``source``, ``event``, ``repo``,
            ``branch``, ``action``, etc.
        """
        trigger_type = workflow.get("trigger_type", "")
        config: dict[str, Any] = workflow.get("trigger_config") or {}

        # Match trigger_type against event source
        event_trigger = event.get("trigger_type", "")
        if trigger_type != event_trigger:
            return False

        # If no filter conditions, any event of the right type matches
        if not config:
            return True

        # Simple field matching: all conditions must be satisfied
        for key, expected in config.items():
            actual = event.get(key)
            if actual is None:
                return False
            if isinstance(expected, str) and expected.startswith("~"):
                # Regex-like: check contains
                if expected[1:] not in str(actual):
                    return False
            elif str(actual) != str(expected):
                return False

        return True

    async def run_workflow(
        self,
        workflow: dict[str, Any],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a workflow's steps in sequence.

        Returns a result dict with ``steps_completed`` and ``steps_failed``.
        """
        steps: list[dict[str, Any]] = workflow.get("steps") or []
        if not steps:
            return {"steps_completed": 0, "steps_failed": 0}

        completed = 0
        failed = 0
        idx = 0

        while idx < len(steps):
            step = steps[idx]
            step_type = step.get("type", "")

            try:
                if step_type == "condition":
                    next_idx = await self._run_condition(step, context)
                    if next_idx is not None:
                        idx = next_idx
                        continue
                    # No branch taken → advance
                    idx += 1
                    continue
                elif step_type == "action":
                    await self._run_action(step, context, workflow)
                    completed += 1
                else:
                    logger.warning("Unknown step type: %s", step_type)
                    failed += 1
            except Exception:
                logger.warning("Workflow step %d failed", idx, exc_info=True)
                failed += 1

            idx += 1

        return {"steps_completed": completed, "steps_failed": failed}

    # ------------------------------------------------------------------
    # Step runners
    # ------------------------------------------------------------------

    async def _run_condition(
        self,
        step: dict[str, Any],
        context: dict[str, Any],
    ) -> int | None:
        """Evaluate a condition step. Returns next step index or None."""
        cond = step.get("if", {})
        field = cond.get("field", "")
        op = cond.get("op", "eq")
        expected = cond.get("value", "")

        actual = context.get(field, "")
        result = False

        if op == "eq":
            result = str(actual) == str(expected)
        elif op == "neq":
            result = str(actual) != str(expected)
        elif op == "contains":
            result = str(expected) in str(actual)
        elif op == "startswith":
            result = str(actual).startswith(str(expected))

        if result:
            then_step = step.get("then_step")
            return then_step if isinstance(then_step, int) else None
        else:
            else_step = step.get("else_step")
            return else_step if isinstance(else_step, int) else None

    async def _run_action(
        self,
        step: dict[str, Any],
        context: dict[str, Any],
        workflow: dict[str, Any],
    ) -> None:
        """Execute an action step."""
        action_type = step.get("action_type", "")
        config: dict[str, Any] = step.get("config") or {}

        if action_type == "notify":
            await self._action_notify(config, context, workflow)
        elif action_type == "send-message":
            await self._action_send_message(config, context, workflow)
        elif action_type == "code-review":
            await self._action_code_review(config, context, workflow)
        elif action_type == "run-skill":
            await self._action_run_skill(config, context, workflow)
        else:
            logger.warning("Unknown action_type: %s", action_type)

    async def _action_notify(
        self,
        config: dict[str, Any],
        context: dict[str, Any],
        workflow: dict[str, Any],
    ) -> None:
        try:
            from .notifications import get_notification_manager

            mgr = get_notification_manager()
            title = config.get("title", f"Workflow: {workflow.get('name', '')}")
            body = config.get("body", "")
            # Template substitution: {{field}} → context value
            for key, val in context.items():
                title = title.replace(f"{{{{{key}}}}}", str(val))
                body = body.replace(f"{{{{{key}}}}}", str(val))
            await mgr.notify(
                type="workflow",
                title=title,
                body=body,
                source="workflow",
                source_id=workflow.get("id"),
                priority=int(config.get("priority", 0)),
            )
        except Exception:
            logger.warning("Workflow action:notify failed", exc_info=True)

    async def _action_send_message(
        self,
        config: dict[str, Any],
        context: dict[str, Any],
        workflow: dict[str, Any],
    ) -> None:
        template = config.get("message_template", "")
        for key, val in context.items():
            template = template.replace(f"{{{{{key}}}}}", str(val))
        # Create notification as the message delivery
        await self._action_notify(
            {
                "title": f"Workflow message: {workflow.get('name', '')}",
                "body": template,
                "priority": config.get("priority", 0),
            },
            context,
            workflow,
        )

    async def _action_code_review(
        self,
        config: dict[str, Any],
        context: dict[str, Any],
        workflow: dict[str, Any],
    ) -> None:
        await self._action_notify(
            {
                "title": f"Code Review: {context.get('repo', '')}@{context.get('branch', '')}",
                "body": f"Triggered by workflow: {workflow.get('name', '')}",
                "priority": 1,
            },
            context,
            workflow,
        )

    async def _action_run_skill(
        self,
        config: dict[str, Any],
        context: dict[str, Any],
        workflow: dict[str, Any],
    ) -> None:
        skill_id = config.get("skill_id", "")
        if not skill_id:
            return
        try:
            from .agent.skills import get_runtime

            runtime = get_runtime()
            if runtime is not None:
                await runtime.invoke(skill_id, context)
        except Exception:
            logger.warning("Workflow action:run-skill failed", exc_info=True)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_ENGINE: WorkflowEngine | None = None


def get_workflow_engine() -> WorkflowEngine:
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = WorkflowEngine()
    return _ENGINE


__all__ = ["WorkflowEngine", "get_workflow_engine"]
