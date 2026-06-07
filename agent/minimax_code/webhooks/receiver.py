"""Webhook receiver — HMAC signature verification + payload parsing.

Handles inbound webhook requests from GitHub, Gitee, and custom
sources. Verifies the ``X-Hub-Signature-256`` header using the
stored secret, then parses the push-event payload into a
normalised dict consumed by :func:`dispatch_webhook_action`.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class WebhookPayload:
    """Normalised webhook event data."""

    source: str  # 'github' | 'gitee' | 'custom'
    event: str  # 'push' | 'ping' | 'custom'
    repo: str = ""
    branch: str = ""
    commits: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class WebhookReceiver:
    """Stateless receiver — one instance shared across all requests."""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def handle_request(
        self,
        webhook_cfg: dict[str, Any],
        headers: dict[str, str],
        body: bytes,
    ) -> WebhookPayload | None:
        """Process an inbound webhook request.

        Returns ``None`` if signature verification fails.
        """
        source = webhook_cfg.get("source", "custom")
        secret = webhook_cfg.get("secret")

        # 1. Verify signature (skip if no secret configured).
        if secret:
            sig_header = (
                headers.get("x-hub-signature-256")
                or headers.get("X-Hub-Signature-256")
                or ""
            )
            if not self._verify_signature(body, secret, sig_header):
                logger.warning("Webhook signature mismatch for %s", webhook_cfg.get("id"))
                return None

        # 2. Parse body as JSON.
        try:
            raw = json.loads(body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("Webhook body is not valid JSON")
            return None

        # 3. Dispatch by source.
        event = (
            headers.get("x-github-event")
            or headers.get("X-GitHub-Event")
            or headers.get("x-gitee-event")
            or headers.get("X-Gitee-Event")
            or "custom"
        )

        if source == "github":
            return self._parse_github(event, raw)
        elif source == "gitee":
            return self._parse_gitee(event, raw)
        else:
            return WebhookPayload(source="custom", event=event, raw=raw)

    # ------------------------------------------------------------------
    # Signature verification
    # ------------------------------------------------------------------

    @staticmethod
    def _verify_signature(body: bytes, secret: str, signature: str) -> bool:
        """Verify HMAC-SHA256 ``signature`` against ``body`` + ``secret``.

        GitHub and Gitee both use ``sha256=<hex>`` format.
        """
        if not signature.startswith("sha256="):
            return False
        expected = "sha256=" + hmac.new(
            secret.encode("utf-8"),
            body,
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)

    # ------------------------------------------------------------------
    # Payload parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_github(event: str, raw: dict[str, Any]) -> WebhookPayload:
        if event == "push":
            return WebhookPayload(
                source="github",
                event="push",
                repo=raw.get("repository", {}).get("full_name", ""),
                branch=(raw.get("ref") or "").replace("refs/heads/", ""),
                commits=raw.get("commits", []),
                raw=raw,
            )
        # ping / other events.
        return WebhookPayload(
            source="github",
            event=event,
            repo=raw.get("repository", {}).get("full_name", ""),
            raw=raw,
        )

    @staticmethod
    def _parse_gitee(event: str, raw: dict[str, Any]) -> WebhookPayload:
        if event == "push":
            return WebhookPayload(
                source="gitee",
                event="push",
                repo=raw.get("repository", {}).get("full_name", ""),
                branch=(raw.get("ref") or "").replace("refs/heads/", ""),
                commits=raw.get("commits", []),
                raw=raw,
            )
        return WebhookPayload(
            source="gitee",
            event=event,
            repo=raw.get("repository", {}).get("full_name", ""),
            raw=raw,
        )


__all__ = ["WebhookPayload", "WebhookReceiver"]
