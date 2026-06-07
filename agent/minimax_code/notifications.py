"""Notification manager — process-level singleton.

Wraps ``NotificationDAO`` and ``IPCServer`` so any subsystem can
create a notification with a single call and have it both persisted
*and* pushed to all connected WebSocket clients.

v0.7.0 — Mobile Connectivity Enhancement.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from .storage.dao.notifications import NotificationDAO

if TYPE_CHECKING:
    from .ipc.server import IPCServer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Singleton helpers (same pattern as mobile/__init__.py)
# ---------------------------------------------------------------------------

_MANAGER: NotificationManager | None = None


def get_notification_manager() -> NotificationManager:
    """Return the process-level ``NotificationManager``.

    Raises ``RuntimeError`` if ``set_notification_manager`` has not been
    called yet.
    """
    if _MANAGER is None:
        raise RuntimeError("NotificationManager not initialised")
    return _MANAGER


def set_notification_manager(mgr: NotificationManager) -> None:
    global _MANAGER
    _MANAGER = mgr


# ---------------------------------------------------------------------------
# NotificationManager
# ---------------------------------------------------------------------------


class NotificationManager:
    """High-level helper: persist + push notification in one call."""

    def __init__(self, dao: NotificationDAO, server: IPCServer) -> None:
        self._dao = dao
        self._server = server

    # convenience accessor for handlers that need raw DAO
    @property
    def dao(self) -> NotificationDAO:
        return self._dao

    async def notify(
        self,
        *,
        type: str,
        title: str,
        body: str = "",
        source: str | None = None,
        source_id: str | None = None,
        priority: int = 0,
    ) -> dict[str, Any]:
        """Create a notification and push it to all WS clients.

        Returns the hydrated notification dict (with ``read`` as bool).
        """
        entry = await self._dao.create(
            type=type,
            title=title,
            body=body,
            source=source,
            source_id=source_id,
            priority=priority,
        )
        # Fan-out via IPCServer → _WSManager → all connected browsers / devices
        try:
            self._server.notify({
                "event": "notification.new",
                "data": entry,
            })
        except Exception:
            logger.warning("Failed to push notification.new event", exc_info=True)
        return entry


__all__ = [
    "NotificationManager",
    "get_notification_manager",
    "set_notification_manager",
]
