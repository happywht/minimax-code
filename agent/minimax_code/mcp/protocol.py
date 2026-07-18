"""MCP wire constants — protocol version, method names, error codes.

Kept dependency-free (only stdlib) so both the client and server halves
of :mod:`minimax_code.mcp` can import it without pulling in Pydantic.
Method names follow the public MCP spec (``2024-11-05``) verbatim.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Protocol version
# ---------------------------------------------------------------------------

#: The MCP spec version this implementation targets. Pinned to the
#: ``2024-11-05`` stable release; advertised in the ``initialize``
#: handshake. Bump only when we adopt a newer spec intentionally.
LATEST_PROTOCOL_VERSION: str = "2024-11-05"


# ---------------------------------------------------------------------------
# JSON-RPC 2.0 method names (spec-defined)
# ---------------------------------------------------------------------------

# Lifecycle
METHOD_INITIALIZE = "initialize"
METHOD_INITIALIZED = "notifications/initialized"
METHOD_PING = "ping"

# Tools
METHOD_TOOLS_LIST = "tools/list"
METHOD_TOOLS_CALL = "tools/call"

# Resources
METHOD_RESOURCES_LIST = "resources/list"
METHOD_RESOURCES_READ = "resources/read"
METHOD_RESOURCES_TEMPLATES_LIST = "resources/templates/list"
METHOD_RESOURCES_SUBSCRIBE = "resources/subscribe"
METHOD_RESOURCES_UNSUBSCRIBE = "resources/unsubscribe"

# Prompts
METHOD_PROMPTS_LIST = "prompts/list"
METHOD_PROMPTS_GET = "prompts/get"

# Logging / completion / cancellation
METHOD_LOGGING_SET_LEVEL = "logging/setLevel"
METHOD_COMPLETION_COMPLETE = "completion/complete"
METHOD_CANCELLED = "notifications/cancelled"
METHOD_PROGRESS = "notifications/progress"

# Roots (client → server: which workspace roots the server may touch)
METHOD_ROOTS_LIST = "roots/list"
METHOD_ROOTS_LIST_CHANGED = "notifications/roots/list_changed"

# Server → client notifications
METHOD_RESOURCES_UPDATED = "notifications/resources/updated"
METHOD_RESOURCES_LIST_CHANGED = "notifications/resources/list_changed"
METHOD_TOOLS_LIST_CHANGED = "notifications/tools/list_changed"
METHOD_PROMPTS_LIST_CHANGED = "notifications/prompts/list_changed"


#: All spec-defined request methods (have a return value). Used by the
#: dispatcher to distinguish requests from notifications.
REQUEST_METHODS: frozenset[str] = frozenset(
    {
        METHOD_INITIALIZE,
        METHOD_PING,
        METHOD_TOOLS_LIST,
        METHOD_TOOLS_CALL,
        METHOD_RESOURCES_LIST,
        METHOD_RESOURCES_READ,
        METHOD_RESOURCES_TEMPLATES_LIST,
        METHOD_RESOURCES_SUBSCRIBE,
        METHOD_RESOURCES_UNSUBSCRIBE,
        METHOD_PROMPTS_LIST,
        METHOD_PROMPTS_GET,
        METHOD_LOGGING_SET_LEVEL,
        METHOD_COMPLETION_COMPLETE,
        METHOD_ROOTS_LIST,
    }
)


# ---------------------------------------------------------------------------
# MCP-defined error codes
# ---------------------------------------------------------------------------
#
# MCP layers a small set of domain codes on top of JSON-RPC's standard
# range (-32700..-32603). We reuse the standard range from the existing
# IPC protocol where it overlaps, and define MCP-specific codes in the
# application band (-32000..-32099) without colliding with the IPC
# server's own codes (which start at -32001).

# JSON-RPC standard (mirrors minimax_code.ipc.protocol)
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# MCP-specific (spec §6.4)
RESOURCE_NOT_FOUND = -32002  # NOTE: keep distinct from IPC's PERMISSION_DENIED
INVALID_CURSOR = -32003

# Application band reserved for MiniMax's MCP client/server runtime.
MCP_TRANSPORT_ERROR = -32100
MCP_SERVER_TIMEOUT = -32101
MCP_HANDSHAKE_FAILED = -32102


__all__ = [name for name in globals() if name.isupper()]
