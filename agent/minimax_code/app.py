"""Application bootstrap — wires IPC handlers to the server.

This module is the seam where the :mod:`minimax_code.ipc` layer
meets the rest of the agent. Today it registers a single
``agent.send_message`` stub. Future tasks will register the full
agent loop, storage, skill, and scheduler handlers.
"""

from __future__ import annotations

import logging

from .ipc.builtins import handle_agent_send_message
from .ipc.server import IPCServer

logger = logging.getLogger(__name__)


def register_app_handlers(server: IPCServer) -> None:
    """Register all application-level JSON-RPC methods on ``server``."""
    server.register("agent.send_message", handle_agent_send_message)
    logger.info("registered %d application handler(s)", 1)


__all__ = ["register_app_handlers"]
