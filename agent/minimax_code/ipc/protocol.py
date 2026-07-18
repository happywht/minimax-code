"""JSON-RPC 2.0 message envelopes.

We model the wire format with Pydantic so we get validation for free
and the type stubs double as documentation.

Convention
----------
- ``Request``:    has ``id`` and ``method``
- ``Response``:   has ``id`` and either ``result`` or ``error``
- ``Notification``: has ``method`` but no ``id`` (caller does not
  expect a reply)
- ``Event``:      has ``event`` and ``data`` (no id, no method) —
  used for *push* streams like ``agent.message_chunk``

In practice the Rust bridge re-emits the raw JSON to the webview, so
this module is the canonical type reference for the project.
"""

from __future__ import annotations

import json
from typing import Any, Literal, Union

from pydantic import BaseModel, Field, ConfigDict


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


# ---- Error object ---------------------------------------------------------


class RPCError(_Base):
    """JSON-RPC 2.0 error object."""

    code: int
    message: str
    data: Any | None = None


# ---- Standard error codes -------------------------------------------------

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603

# Application-defined range (-32000 to -32099 reserved by spec).
TOOL_EXECUTION_ERROR = -32001
PERMISSION_DENIED = -32002
LLM_ERROR = -32003
STORAGE_ERROR = -32004
NOT_IMPLEMENTED = -32005
NOT_FOUND = -32006


# ---- Outbound (Python -> stdio) ------------------------------------------


class Response(_Base):
    """Reply to a previously-issued request."""

    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int
    result: Any = None
    error: RPCError | None = None

    def to_line(self) -> str:
        return self.model_dump_json(exclude_none=True)

    def to_bytes(self) -> bytes:
        return self.to_line().encode("utf-8") + b"\n"


class Notification(_Base):
    """Outbound notification (no reply expected)."""

    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: Any = None

    def to_line(self) -> str:
        return self.model_dump_json(exclude_none=True)

    def to_bytes(self) -> bytes:
        return self.to_line().encode("utf-8") + b"\n"


class Event(_Base):
    """Push event for streamed responses.

    Example::

        {"event": "agent.message_chunk",
         "data": {"session_id": "abc", "delta": "Hi", "done": false}}
    """

    jsonrpc: Literal["2.0"] = "2.0"
    event: str
    data: Any = None

    def to_line(self) -> str:
        return self.model_dump_json(exclude_none=True)

    def to_bytes(self) -> bytes:
        return self.to_line().encode("utf-8") + b"\n"


# ---- Inbound (stdio -> Python) -------------------------------------------


class Request(_Base):
    """JSON-RPC 2.0 request from the frontend."""

    jsonrpc: Literal["2.0"] = "2.0"
    id: str | int
    method: str
    params: Any = None

    def to_line(self) -> str:
        return self.model_dump_json(exclude_none=True)


class InboundNotification(_Base):
    """JSON-RPC 2.0 notification from the frontend (no reply expected)."""

    jsonrpc: Literal["2.0"] = "2.0"
    method: str
    params: Any = None

    def to_line(self) -> str:
        return self.model_dump_json(exclude_none=True)


# ---- Helpers --------------------------------------------------------------


def parse_envelope(line: str) -> dict[str, Any]:
    """Parse a single line into its raw JSON object.

    Raises ``ValueError`` on malformed input. Callers should wrap with
    :class:`RPCError` to surface the failure to the frontend.
    """
    line = line.strip()
    if not line:
        raise ValueError("empty line")
    try:
        return json.loads(line)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON: {exc}") from exc


def make_error_response(req_id: str | int | None, code: int, message: str, data: Any = None) -> Response:
    return Response(
        id=req_id if req_id is not None else 0,
        error=RPCError(code=code, message=message, data=data),
    )


__all__ = [
    "Event",
    "InboundNotification",
    "Notification",
    "Request",
    "Response",
    "RPCError",
    "parse_envelope",
    "make_error_response",
    # Standard codes
    "PARSE_ERROR",
    "INVALID_REQUEST",
    "METHOD_NOT_FOUND",
    "INVALID_PARAMS",
    "INTERNAL_ERROR",
    # App codes
    "TOOL_EXECUTION_ERROR",
    "PERMISSION_DENIED",
    "LLM_ERROR",
    "STORAGE_ERROR",
    "NOT_IMPLEMENTED",
]
