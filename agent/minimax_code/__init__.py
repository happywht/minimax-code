"""MiniMax Code — Python agent package.

An asyncio-based agent that exposes a JSON-RPC 2.0 interface over
HTTP + WebSocket (primary) or stdio (for CLI / testing).  The agent
manages LLM conversations, tool dispatch, SQLite storage, skills,
scheduled jobs, sub-agent orchestration, and more.

See ``ipc.server`` for the wire format and ``app`` for handler
registration.
"""

__version__ = "0.16.0"
