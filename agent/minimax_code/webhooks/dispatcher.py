"""Webhook action dispatcher.

Takes a :class:`WebhookPayload` and the webhook's ``action_type`` /
``action_config`` and executes the mapped action. Currently supported:

* ``send-message`` — inject a user-visible message into the
  active session via the ``agent.send_message`` path.
* ``code-review`` — trigger an automatic code review using the
  configured skill.

Both actions are fire-and-forget: errors are logged but never
propagated to the caller (the inbound HTTP handler always returns 200
so the sender does not retry).
"""

from __future__ import annotations

import logging
from typing import Any

from .receiver import WebhookPayload

logger = logging.getLogger(__name__)


async def dispatch_webhook_action(
    payload: WebhookPayload,
    *,
    action_type: str,
    action_config: dict[str, Any],
    webhook_id: str,
) -> None:
    """Execute the mapped action for a validated webhook payload."""
    if action_type == "send-message":
        await _action_send_message(payload, action_config, webhook_id)
    elif action_type == "code-review":
        await _action_code_review(payload, action_config, webhook_id)
    else:
        logger.warning(
            "Unknown webhook action_type=%s for webhook=%s",
            action_type,
            webhook_id,
        )


# ---------------------------------------------------------------------------
# Action implementations
# ---------------------------------------------------------------------------


async def _action_send_message(
    payload: WebhookPayload,
    config: dict[str, Any],
    webhook_id: str,
) -> None:
    """Build a notification message and log it.

    In production this would push through the agent's session layer.
    For v0.5.0 we emit a structured log entry that the frontend can
    surface.
    """
    parts: list[str] = []
    if payload.repo:
        parts.append(f"📦 {payload.repo}")
    if payload.branch:
        parts.append(f"🌿 {payload.branch}")
    if payload.commits:
        n = len(payload.commits)
        parts.append(f"📝 {n} commit(s)")
        for c in payload.commits[:5]:
            msg = c.get("message", "").split("\n", 1)[0]
            author = c.get("author", {}).get("name", "unknown")
            parts.append(f"  - {msg} ({author})")

    message = "\n".join(parts) if parts else f"Webhook event: {payload.event}"
    logger.info(
        "Webhook send-message webhook=%s source=%s event=%s\n%s",
        webhook_id,
        payload.source,
        payload.event,
        message,
    )


async def _action_code_review(
    payload: WebhookPayload,
    config: dict[str, Any],
    webhook_id: str,
) -> None:
    """Trigger an automatic code review for push events.

    For v0.5.0 this logs the review request. Full integration with
    the code-review skill will be wired in v0.6.0.
    """
    if payload.event != "push" or not payload.commits:
        logger.info(
            "Webhook code-review skipped (event=%s, commits=%d)",
            payload.event,
            len(payload.commits),
        )
        return

    files: list[str] = []
    for c in payload.commits:
        files.extend(c.get("added", []))
        files.extend(c.get("modified", []))
        files.extend(c.get("removed", []))

    logger.info(
        "Webhook code-review webhook=%s repo=%s branch=%s files=%d",
        webhook_id,
        payload.repo,
        payload.branch,
        len(files),
    )


__all__ = ["dispatch_webhook_action"]
