"""Webhook action dispatcher.

Takes a :class:`WebhookPayload` and the webhook's ``action_type`` /
``action_config`` and executes the mapped action. Supported types:

* ``send-message`` — create a notification + optionally inject into a session
* ``code-review`` — trigger a code review via the code-review skill

Dispatch errors propagate to the HTTP handler. Code review remains unavailable
until it can be admitted through the existing authorized execution boundary.

"""

from __future__ import annotations

from typing import Any

from .receiver import WebhookPayload


class WebhookDispatchError(RuntimeError):
    """Safe, non-sensitive failure stage for a webhook action."""

    def __init__(self, stage: str) -> None:
        self.stage = stage
        super().__init__(stage)


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
        raise WebhookDispatchError("unknown_action")


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

    # Each operation is required. Earlier operations may already have committed
    # if a later one fails; callers must not assume a retry is deduplicated.
    try:
        from ..notifications import get_notification_manager

        mgr = get_notification_manager()
        await mgr.notify(
            type="webhook", title=title, body=body, source="webhook",
            source_id=webhook_id, priority=priority,
        )
    except Exception as exc:
        raise WebhookDispatchError("notification") from exc

    if config.get("create_session"):
        try:
            from ..app import get_sessions_dao
            from ..storage.dao.messages import MessagesDAO

            dao = get_sessions_dao()
            sessions = await dao.list_all(limit=1)
            if not sessions:
                raise WebhookDispatchError("session_unavailable")
            msgs_dao = MessagesDAO(dao._db)
            await msgs_dao.create(
                session_id=sessions[0]["id"], role="user",
                text=f"[Webhook] {title}\n\n{body}",
            )
        except WebhookDispatchError:
            raise
        except Exception as exc:
            raise WebhookDispatchError("session_message") from exc


async def _action_code_review(
    payload: WebhookPayload,
    config: dict[str, Any],
    webhook_id: str,
) -> None:
    """Trigger an automatic code review for push events."""
    if payload.event != "push" or not payload.commits:
        return

    # The old call used a nonexistent get_runtime() and an incompatible
    # invoke signature. Direct runtime invocation would bypass the durable
    # skill admission and permission checks, so fail closed instead.
    raise WebhookDispatchError("code_review_unavailable")


__all__ = ["WebhookDispatchError", "dispatch_webhook_action"]
