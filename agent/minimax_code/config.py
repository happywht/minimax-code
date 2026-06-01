"""Global configuration for the Python agent.

Configuration is intentionally minimal at skeleton stage — we expose
just the few knobs the IPC and logging layers need. Later tasks
(storage, agent loop) will extend this with database paths, model
endpoints, etc.
"""

from __future__ import annotations

import os
from typing import Literal

from pydantic import BaseModel, Field

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Config(BaseModel):
    """Runtime configuration. Loaded from environment variables."""

    log_level: LogLevel = "INFO"
    env: Literal["development", "production"] = "development"
    session_idle_timeout_seconds: int = Field(default=1800, ge=0)
    # Maximum bytes we will read into a single JSON message before we
    # declare it malformed. Protects against accidental huge payloads.
    max_message_bytes: int = Field(default=8 * 1024 * 1024, ge=1024)

    @classmethod
    def from_env(cls) -> "Config":
        return cls(
            log_level=os.environ.get("MINIMAX_CODE_LOG_LEVEL", "INFO"),  # type: ignore[arg-type]
            env=os.environ.get("MINIMAX_CODE_ENV", "development"),  # type: ignore[arg-type]
            session_idle_timeout_seconds=int(
                os.environ.get("MINIMAX_CODE_SESSION_IDLE_TIMEOUT", "1800")
            ),
            max_message_bytes=int(
                os.environ.get("MINIMAX_CODE_MAX_MESSAGE_BYTES", str(8 * 1024 * 1024))
            ),
        )
