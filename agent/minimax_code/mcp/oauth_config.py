"""OAuth configuration types for MCP servers (R34).

Ports ``xai-grok-mcp/src/oauth_config.rs`` — the BYO OAuth config parsed out
of an MCP server's config table. Pure data: no IO, no network, no token
exchange. Travels alongside the server definition; consumed by a future OAuth
flow module (the browser-based handshake is a host layer and remains a later
round).

Product fusion: MCP servers that require OAuth (e.g. a Google Drive MCP
server) need per-server client credentials. MiniMax Code's existing secrets
layer (R15 redaction, OS keyring) is the natural home for the credential
*values*; this module is the *shape* of that config — what fields a server's
OAuth block carries and what "configured" means (``client_id`` present).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

__all__ = [
    "McpOAuthConfig",
    "McpOAuthConfigMap",
]


@dataclass
class McpOAuthConfig:
    """OAuth configuration extracted from an MCP server's config.

    All fields optional — a server may carry none (no OAuth), some, or all.
    :meth:`is_configured` is the single predicate for "this server has OAuth":
    a ``client_id`` present means the operator intends OAuth for it. Mirrors
    grok's ``#[derive(Debug, Clone, Default)]`` (mutable, all-default).
    """

    client_id: str | None = None
    client_secret: str | None = None
    scopes: list[str] | None = None
    callback_port: int | None = None

    def is_configured(self) -> bool:
        """True iff a ``client_id`` is present (operator intends OAuth)."""
        return self.client_id is not None


#: Per-server OAuth configuration map, keyed by MCP server name.
#:
#: (Rust ``HashMap<String, McpOAuthConfig>`` — grok's ``pub type`` alias.)
McpOAuthConfigMap: TypeAlias = dict[str, McpOAuthConfig]
