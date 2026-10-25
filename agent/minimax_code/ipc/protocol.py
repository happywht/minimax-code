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
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _Base(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)


class SendMessageParams(_Base):
    """Additive task mode; plan is enforced by execution/tool policy."""

    session_id: str | None = None
    content: str | list[dict[str, Any]]
    mode: Literal["chat", "plan", "execute"] = "chat"
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)
    # M2 — per-run token budget. None defers to the env default
    # (MINIMAX_CODE_RUN_BUDGET_TOKENS); 0 disables budgeting for this
    # run; a positive value caps the run (and its sub-agents).
    budget_tokens: int | None = Field(default=None, ge=0)


class RunStartParams(SendMessageParams):
    model_config = ConfigDict(extra="forbid", strict=True)
    session_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1, max_length=128)
    # M4 — engine pinned at admission. "codex" routes to the Codex
    # App Server adapter; unknown/unavailable values are named in the
    # error instead of silently falling back to native.
    engine: Literal["native", "codex"] = "native"


class RunIdParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1)


class RunResumeParams(RunIdParams):
    idempotency_key: str = Field(min_length=1, max_length=128)
    reviewed: bool = False
    # M2 — raise the per-run cap when resuming a budget_paused run.
    # None inherits the original run's cap from its metadata.
    budget_tokens: int | None = Field(default=None, ge=0)


class SendMessageResult(_Base):
    session_id: str
    message_id: str
    text: str
    run_id: str | None = None
    cancelled: bool = False
    iterations: int | None = None
    # Missing engine usage is unknown, never inferred to be zero.
    tokens_in: int | None = None
    tokens_out: int | None = None
    run_status: Literal["failed", "interrupted"] | None = None


class MessageMetadata(_Base):
    """Persisted and streamed metadata; old messages may omit run linkage."""

    run_id: str | None = None
    thinking_count: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None


class RunStreamContext(_Base):
    """Chat notification context; older emitters may omit the durable run ID."""

    session_id: str
    run_id: str | None = None


class ChildWorkspaceSummary(_Base):
    """Optional result field on real sub-agent and team member execution."""

    run_id: str
    path: str
    kind: Literal["git_worktree", "directory"]
    state: Literal["preparing", "running", "completed", "failed", "cancelled", "needs_review", "collected"]
    files_written: list[str] = Field(default_factory=list)
    omitted: list[str] = Field(default_factory=list)
    collection_required: bool = True


class PreviewAccessResult(_Base):
    """HTTP-only read capability; never a general application auth token."""

    path: str
    expires_in: int = Field(ge=0, le=900)


class RunEventsParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    run_id: str = Field(min_length=1)
    after_seq: int = Field(default=0, ge=0, le=2**53 - 1)
    limit: int = Field(default=100, ge=1, le=500)


class NativeModelSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")
    engine: Literal["native"] = "native"
    provider_id: str | None
    model: str
    protocol: str | None
    reasoning_effort: str | None
    context_window: int


class RunEvent(_Base):
    id: str
    run_id: str
    seq: int = Field(ge=1)
    type: Literal["created", "status", "step_created", "step_updated", "recovered"]
    created_at: str
    data: dict[str, Any]


class RunEventsResult(_Base):
    events: list[RunEvent]
    next_seq: int = Field(ge=0)
    has_more: bool


class RunSnapshotResult(_Base):
    run: dict[str, Any]
    steps: list[dict[str, Any]]
    cursor: int = Field(ge=0)


# ---- Error object ---------------------------------------------------------


class HostTransferStatusParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    host_id: str = Field(min_length=1)
    remote_path: str = Field(min_length=1)
    transfer_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class HostPushParams(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    host_id: str = Field(min_length=1)
    local_path: str = Field(min_length=1)
    remote_path: str = Field(min_length=1)
    transfer_id: str | None = Field(default=None, pattern=r"^[0-9a-f]{32}$")


class HostTransferResult(_Base):
    ok: bool
    exit_code: int | None
    stderr: str
    remote_path: str
    transfer_id: str | None = None
    publication_state: Literal["staged", "publishing", "published", "aborted", "unknown", "not_published"] | None = None
    backup_path: str | None = None


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
