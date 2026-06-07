"""Webhook action dispatcher.

Takes a :class:`WebhookPayload` and the webhook's ``action_type`` /
``action_config`` and executes the mapped action. Supported types:

* ``send-message`` — create a notification + optionally inject into a session
* ``code-review`` — trigger a code review via the code-review skill

Both actions are fire-and-forget: errors are logged but never propagated
to the caller (the inbound HTTP handler always returns 200 so the sender
does not retry).

v0.7.0 — real execution using NotificationManager.
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
# Helpers
# ---------------------------------------------------------------------------


def _build_message_body(payload: WebhookPayload) -> str:
    """Render a human-readable summary of the webhook event."""
    parts: list[str] = []
    if payload.repo:
        parts.append(f"📦 {payload.repo}")
    if payload.branch:
        parts.append(f"🌿 {payload.branch}")
    if payload.action:
        parts.append(f"⚡ {payload.action}")
    if payload.tag:
        parts.append(f"🏷️ {payload.tag}")
    if payload.commits:
        n = len(payload.commits)
        parts.append(f"📝 {n} commit(s)")
        for c in payload.commits[:5]:
            msg = c.get("message", "").split("\n", 1)[0]
            author = c.get("author", {}).get("name", "unknown")
            parts.append(f"  - {msg} ({author})")
    if payload.pull_request:
        title = payload.pull_request.get("title", "")
        url = payload.pull_request.get("html_url") or payload.pull_request.get("url", "")
        parts.append(f"🔀 PR: {title}")
        if url:
            parts.append(f"  {url}")
    if payload.issue:
        title = payload.issue.get("title", "")
        parts.append(f"❓ Issue: {title}")
    if payload.comment:
        body_text = payload.comment.get("body", "")[:200]
        parts.append(f"💬 Comment: {body_text}")
    return "\n".join(parts) if parts else f"Webhook event: {payload.event}"


# ---------------------------------------------------------------------------
# Action implementations
# ---------------------------------------------------------------------------


async def _action_send_message(
    payload: WebhookPayload,
    config: dict[str, Any],
    webhook_id: str,
) -> None:
    """Create a notification and optionally inject into a session."""
    title = f"Webhook: {payload.event} from {payload.repo or 'unknown'}"
    body = _build_message_body(payload)
    priority = int(config.get("priority", 0))

    # 1. Create a notification (persists + pushes to all WS clients)
    try:
        from ..notifications import get_notification_manager

        mgr = get_notification_manager()
        await mgr.notify(
            type="webhook",
            title=title,
            body=body,
            source="webhook",
            source_id=webhook_id,
            priority=priority,
        )
        logger.info(
            "Webhook send-message: created notification webhook=%s event=%s",
            webhook_id,
            payload.event,
        )
    except Exception:
        # NotificationManager not initialised — fall back to log
        logger.warning(
            "Webhook send-message: NotificationManager not available, "
            "logging instead. webhook=%s\n%s",
            webhook_id,
            body,
        )

    # 2. Optionally inject message into a session
    if config.get("create_session"):
        try:
            from ..app import get_sessions_dao

            dao = get_sessions_dao()
            sessions = await dao.list_all(limit=1)
            if sessions:
                from ..storage.dao.messages import MessagesDAO

                msgs_dao = MessagesDAO(dao._db)
                await msgs_dao.create(
                    session_id=sessions[0]["id"],
                    role="user",
                    text=f"[Webhook] {title}\n\n{body}",
                )
        except Exception:
            logger.warning(
                "Webhook send-message: failed to inject into session",
                exc_info=True,
            )


async def _action_code_review(
    payload: WebhookPayload,
    config: dict[str, Any],
    webhook_id: str,
) -> None:
    """Trigger an automatic code review for push events."""
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

    title = f"Code Review: {payload.repo}@{payload.branch} ({len(files)} files)"
    body = f"Automated review triggered by {len(payload.commits)} commit(s).\n"
    if files:
        body += "Changed files:\n" + "\n".join(f"  - {f}" for f in files[:20])

    # 1. Create notification
    try:
        from ..notifications import get_notification_manager

        mgr = get_notification_manager()
        await mgr.notify(
            type="webhook",
            title=title,
            body=body,
            source="webhook",
            source_id=webhook_id,
            priority=1,
        )
    except Exception:
        logger.warning(
            "Webhook code-review: NotificationManager not available",
            exc_info=True,
        )

    # 2. Try to invoke the code-review skill
    try:
        from ..agent.skills import get_runtime

        runtime = get_runtime()
        if runtime is not None:
            result = await runtime.invoke(
                "code-review",
                {
                    "repo": payload.repo,
                    "branch": payload.branch,
                    "files": files[:50],
                },
            )
            logger.info(
                "Webhook code-review: skill invoked, result=%s webhook=%s",
                type(result).__name__,
                webhook_id,
            )
    except Exception:
        logger.warning(
            "Webhook code-review: skill invocation failed",
            exc_info=True,
        )


__all__ = ["dispatch_webhook_action"]
