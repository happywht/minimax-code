"""Tool chunk payloads (R67).

Fusion of grok-build's ``xai-grok-workspace-types::types::tools`` — the
incremental output frame, the lifecycle/progress enum, the terminal
result, and the tool definition surfaced through ``ToolChunk``.

Two wire subtleties:

* :class:`ToolOutputChunk.bytes` is ``Vec<u8>`` serialised as **RFC-4648
  base64** (a custom ``bytes_as_base64`` serde module; the default would
  be a JSON array of integers, which is wasteful). Pydantic v2's
  ``bytes`` field emits exactly this under ``mode="json"``.
* :class:`ToolOutputChunk.at` defaults to the **Unix epoch** — a
  deterministic sentinel, not ``Utc::now()``. The receiver must not
  pretend its wall clock is the originator's.
* :class:`ToolProgress` is adjacent-tagged (``{type, data}``) like every
  other wire enum in the crate — uniform shape across the whole JSON
  tree even though it nests inside ``ToolChunk::Progress``.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime

from pydantic import Field, field_serializer, field_validator

from minimax_code.workspace_types._tagged import AdjacentTagged
from minimax_code.workspace_types._wire import WireModel
from minimax_code.workspace_types.identity import ToolCallId

__all__ = ["ToolOutputChunk", "ToolProgress", "ToolCallResult", "ToolDef"]

#: Unix-epoch sentinel — deterministic default for ``at``.
_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)

#: Alias for the builtin ``bytes`` type. ``ToolOutputChunk`` has a field
#: literally named ``bytes`` (it is the wire key from Rust's ``bytes:
#: Vec<u8>``); under ``from __future__ import annotations`` the annotation
#: string ``"bytes"`` would otherwise resolve to the field's default value
#: (``b""``) instead of the ``bytes`` type. The alias sidesteps the clash
#: while keeping both the wire key and the base64-serialising type intact.
_RawBytes = bytes


class ToolOutputChunk(WireModel):
    """One incremental tool output frame (e.g. bash stdout).

    ``bytes`` serialises as RFC-4648 base64 (pydantic ``bytes`` field
    under ``mode="json"``). ``at`` defaults to the Unix epoch.
    """

    call_id: ToolCallId
    stream: str = ""
    bytes: _RawBytes = b""
    at: datetime = Field(default=_EPOCH)

    @field_validator("bytes", mode="before")
    @classmethod
    def _decode_bytes_payload(cls, value: object) -> object:
        """Base64-decode the wire string into raw bytes.

        Pydantic's default ``bytes`` JSON handling UTF-8-decodes the
        string, which corrupts non-UTF-8 payloads and breaks the Rust
        ``bytes_as_base64`` contract. JSON input arrives as a base64
        ``str``; raw-bytes input (dict path) passes through unchanged.
        """
        if isinstance(value, str):
            return base64.b64decode(value)
        return value

    @field_serializer("bytes", when_used="json")
    def _encode_bytes_payload(self, value: bytes) -> str:
        """Emit raw bytes as an RFC-4648 base64 string on the wire."""
        return base64.b64encode(value).decode("ascii")

    @classmethod
    def default(cls) -> ToolOutputChunk:
        return cls(call_id=ToolCallId(""))


class ToolProgress(AdjacentTagged):
    """Lifecycle / progress event emitted by a tool (adjacent-tagged).

    Wire shapes::

        {"type": "started", "data": {"call_id": "<id>"}}
        {"type": "status",  "data": {"call_id": "<id>", "message": "..."}}
        {"type": "percent", "data": {"call_id": "<id>", "fraction": 0.5}}

    Carries an ``f32`` ``fraction`` on ``Percent``, so (like the Rust
    enum) it cannot derive ``Eq``.
    """

    _VARIANTS = ("started", "status", "percent")

    @classmethod
    def started(cls, call_id: ToolCallId | str) -> ToolProgress:
        """Tool started (after permission, before execution)."""
        return cls("started", {"call_id": str(call_id)})

    @classmethod
    def status(cls, call_id: ToolCallId | str, message: str) -> ToolProgress:
        """Free-form status string (e.g. ``"installing dependencies"``)."""
        return cls("status", {"call_id": str(call_id), "message": message})

    @classmethod
    def percent(cls, call_id: ToolCallId | str, fraction: float) -> ToolProgress:
        """Quantitative progress; ``fraction`` in ``[0.0, 1.0]``."""
        return cls("percent", {"call_id": str(call_id), "fraction": float(fraction)})


class ToolCallResult(WireModel):
    """Terminal result emitted as exactly one ``ToolChunk::Final`` per call."""

    call_id: ToolCallId
    exit_code: int = 0
    summary: str = ""
    output_json: str = ""
    cancelled: bool = False

    @classmethod
    def default(cls) -> ToolCallResult:
        return cls(call_id=ToolCallId(""))


class ToolDef(WireModel):
    """Tool definition surfaced via ``ToolChunk::Definitions``."""

    name: str
    description: str = ""
    input_schema_json: str = ""
    requires_permission: bool = False

    @classmethod
    def default(cls) -> ToolDef:
        return cls(name="")
