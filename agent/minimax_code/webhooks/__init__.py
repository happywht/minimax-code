"""Webhook framework — inbound endpoint processing.

v0.5.0 — Security Sandbox.
"""

from __future__ import annotations

from .dispatcher import dispatch_webhook_action
from .receiver import WebhookReceiver

__all__ = ["WebhookReceiver", "dispatch_webhook_action"]
