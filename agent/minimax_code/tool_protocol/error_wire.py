"""Wire-friendly tool-call error type (R83).

Fusion of grok-build's ``xai-tool-protocol::error_wire`` — the stable
wire representation of a tool-call failure carried inside the JSON-RPC
``error.data`` field. Receivers SHOULD switch on the ``code`` discriminator
(the snake_case string) rather than the numeric JSON-RPC ``error.code``:
the numeric is the envelope code; the string is the Grok stable identifier.

This is the crate's **second internally-tagged enum** (after
:class:`connection.ToolDefinitionMode`, tagged on ``mode``). It is tagged
on ``code`` (``#[serde(tag = "code", rename_all = "snake_case")]``): the
discriminator rides *inside* the content object as ``{"code": "...",
...fields}``. Fifteen variants, five of which override the snake_case tag
with a stable legacy name (``PermissionDenied`` → ``forbidden``, etc.).

Implementation shape
--------------------

Fifteen variants is too many to spell out as named factory methods on a
single reference class the way :class:`ToolDefinitionMode` (2 variants)
does, so each variant is a ``@dataclass`` subclass of
:class:`_ToolErrorWireBase`. The base owns ``to_wire`` (reflecting over
``dataclasses.fields`` and skipping ``None`` to reproduce
``skip_serializing_if = "Option::is_none"``); dispatch on the ``code`` tag
happens in the module-level :func:`from_wire` via the :data:`_VARIANTS`
registry. Each variant's ``__str__`` reproduces the ``thiserror``
``#[error("...")]`` Display string verbatim.

No shared ``InternallyTagged`` base is extracted even though this is the
second internally-tagged enum: the three internal-tag enums
(``ToolDefinitionMode`` 2 variants / ``ToolErrorWire`` 15 / ``McpBlock`` 3
in :mod:`output_wire`) differ enough in shape that a shared base costs more
than it saves; ``ToolDefinitionMode`` is already stable inline, and a base
with a single caller is YAGNI. (R82's decision tree flagged "abstract when
the second appears"; R83 reassesses and defers — the shapes do not
converge.)

Naming note
-----------

Four variants carry a ``message`` field (``InvalidArguments`` /
``Execution`` / ``TerminalError`` / ``Custom``). The field name MUST stay
``message`` (not ``message_text``) so ``to_wire`` reflects the same wire
key Rust emits. The ``thiserror`` Display string is therefore exposed via
``__str__`` rather than a ``message()`` method, which would collide with
the data attribute.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, fields
from typing import Any, ClassVar

from minimax_code.tool_protocol.ids import RequestId, ToolId

__all__ = [
    "ToolErrorWire",
    "ToolNotFound",
    "SessionMismatch",
    "PermissionDenied",
    "TransportClosed",
    "Timeout",
    "Cancelled",
    "InvalidArguments",
    "Execution",
    "UnsupportedProtocolVersion",
    "PayloadTooLarge",
    "BehaviorVersionUnsupported",
    "RenderLimited",
    "TerminalError",
    "Internal",
    "Custom",
    "from_wire",
]


# -----------------------------------------------------------------------
# _ToolErrorWireBase — shared to_wire (Option::is_none skip).
# -----------------------------------------------------------------------


class _ToolErrorWireBase:
    """Base for ``ToolErrorWire`` variants.

    ``code`` is the wire discriminator (``#[serde(tag = "code")]``); for the
    five renamed variants it differs from ``snake_case(VariantName)``. The
    base reflects over ``dataclasses.fields`` for ``to_wire``; each variant
    defines its own ``__str__`` (the ``thiserror`` Display string) because a
    shared ``message()`` helper would collide with the ``message`` data
    field on four variants.
    """

    code: ClassVar[str]

    def to_wire(self) -> dict[str, object]:
        """Serialise as ``{"code": <tag>, ...non-None fields}``.

        ``Option::None`` fields are omitted (``skip_serializing_if =
        "Option::is_none"``). ``ToolId`` / ``RequestId`` are transparent
        ``str`` newtypes, so they serialise as the bare string with no
        special handling; ``serde_json::Value`` (``Any``) passes through.
        """
        d: dict[str, object] = {"code": self.code}
        for f in fields(self):
            v = getattr(self, f.name)
            if v is None:
                continue
            d[f.name] = v
        return d


# -----------------------------------------------------------------------
# Variants (order mirrors the Rust enum declaration).
# -----------------------------------------------------------------------


@dataclass
class ToolNotFound(_ToolErrorWireBase):
    """The tool id does not resolve to a registered tool (``ToolNotFound``)."""

    code: ClassVar[str] = "tool_not_found"
    tool_id: ToolId

    def __str__(self) -> str:
        return f"tool not found: {self.tool_id}"


@dataclass
class SessionMismatch(_ToolErrorWireBase):
    """The bound session does not match the request's session (``SessionMismatch``)."""

    code: ClassVar[str] = "session_mismatch"

    def __str__(self) -> str:
        return "session mismatch"


@dataclass
class PermissionDenied(_ToolErrorWireBase):
    """The caller lacks the consent / scope for the call (``PermissionDenied``).

    Wire tag ``forbidden`` (``#[serde(rename = "forbidden")]``).
    """

    code: ClassVar[str] = "forbidden"
    reason: str

    def __str__(self) -> str:
        return f"permission denied: {self.reason}"


@dataclass
class TransportClosed(_ToolErrorWireBase):
    """The tool-server transport went away mid-call (``TransportClosed``).

    Wire tag ``connection_lost`` (``#[serde(rename = "connection_lost")]``).
    """

    code: ClassVar[str] = "connection_lost"
    tool_id: ToolId

    def __str__(self) -> str:
        return f"transport closed for {self.tool_id}"


@dataclass
class Timeout(_ToolErrorWireBase):
    """The call exceeded its deadline (``Timeout``)."""

    code: ClassVar[str] = "timeout"
    tool_id: ToolId
    elapsed_ms: int

    def __str__(self) -> str:
        return f"timeout after {self.elapsed_ms}ms for {self.tool_id}"


@dataclass
class Cancelled(_ToolErrorWireBase):
    """The call was cancelled (``Cancelled``)."""

    code: ClassVar[str] = "cancelled"
    tool_id: ToolId

    def __str__(self) -> str:
        return "cancelled"


@dataclass
class InvalidArguments(_ToolErrorWireBase):
    """The call's arguments failed validation (``InvalidArguments``).

    Wire tag ``invalid_params`` (``#[serde(rename = "invalid_params")]``).
    :attr:`details` is an opaque JSON escape hatch omitted when ``None``.
    """

    code: ClassVar[str] = "invalid_params"
    message: str
    details: Any = None

    def __str__(self) -> str:
        return f"invalid arguments: {self.message}"


@dataclass
class Execution(_ToolErrorWireBase):
    """The tool ran but reported a failure (``Execution``)."""

    code: ClassVar[str] = "execution"
    tool_id: ToolId
    message: str

    def __str__(self) -> str:
        return f"execution error in {self.tool_id}: {self.message}"


@dataclass
class UnsupportedProtocolVersion(_ToolErrorWireBase):
    """Peer asked for a protocol version this side does not speak (``UnsupportedProtocolVersion``)."""

    code: ClassVar[str] = "unsupported_protocol_version"
    supported: list[str]

    def __str__(self) -> str:
        return "unsupported protocol version"


@dataclass
class PayloadTooLarge(_ToolErrorWireBase):
    """A frame exceeded the negotiated size cap (``PayloadTooLarge``).

    Wire tag ``frame_too_large`` (``#[serde(rename = "frame_too_large")]``).
    """

    code: ClassVar[str] = "frame_too_large"
    bytes: int
    limit: int

    def __str__(self) -> str:
        return f"payload too large: {self.bytes} bytes (limit {self.limit})"


@dataclass
class BehaviorVersionUnsupported(_ToolErrorWireBase):
    """The tool's declared behavior version is not supported (``BehaviorVersionUnsupported``).

    The Display string uses the underscore form ``behavior_version`` (not a
    space) — reproduced verbatim from the ``thiserror`` attribute.
    """

    code: ClassVar[str] = "behavior_version_unsupported"
    tool_id: ToolId
    requested: str

    def __str__(self) -> str:
        return "behavior_version unsupported"


@dataclass
class RenderLimited(_ToolErrorWireBase):
    """Render-card budget exceeded for the current session (``RenderLimited``).

    :attr:`card_id` carries the offending render-card id when known (omitted
    when ``None``); :attr:`reason` is a free-form human-readable explanation.

    Field order note: Rust declares ``tool_id, card_id, reason``; Python's
    dataclass rule (non-default before default) forces ``card_id`` last
    (``tool_id, reason, card_id=None``). Wire key order therefore differs
    from Rust, but JSON object key order is not semantically significant.
    """

    code: ClassVar[str] = "render_limited"
    tool_id: ToolId
    reason: str
    card_id: str | None = None

    def __str__(self) -> str:
        return f"render limited for {self.tool_id}: {self.reason}"


@dataclass
class TerminalError(_ToolErrorWireBase):
    """Terminal-tool subprocess sub-call failed (``TerminalError``).

    Distinct from :class:`Execution` because terminal sub-call failures
    have a known, retry-eligible shape.
    """

    code: ClassVar[str] = "terminal_error"
    tool_id: ToolId
    message: str

    def __str__(self) -> str:
        return f"terminal subprocess error in {self.tool_id}: {self.message}"


@dataclass
class Internal(_ToolErrorWireBase):
    """Hub-internal failure (``Internal``).

    Wire tag ``internal_error`` (``#[serde(rename = "internal_error")]``).
    Both fields are ``Option`` (omitted when ``None``); :attr:`detail` is a
    bounded human-readable cause, optional for wire compatibility with
    older peers.

    The Display string carries the detail only when present: ``"internal
    error"`` alone, or ``"internal error: {detail}"``.
    """

    code: ClassVar[str] = "internal_error"
    request_id: RequestId | None = None
    detail: str | None = None

    def __str__(self) -> str:
        if self.detail is None:
            return "internal error"
        return f"internal error: {self.detail}"


@dataclass
class Custom(_ToolErrorWireBase):
    """Free-form forward-compat error (``Custom``).

    The outer ``code`` discriminator is always the literal ``"custom"``;
    the producer-supplied subcode lives in :attr:`subcode` (the field cannot
    be named ``code`` — it would collide with the serde discriminator).
    :attr:`details` is an opaque JSON escape hatch omitted when ``None``.

    The Display string uses an em-dash (``—``, U+2014) between subcode and
    message, reproduced verbatim from the ``thiserror`` attribute.
    """

    code: ClassVar[str] = "custom"
    subcode: str
    message: str
    details: Any = None

    def __str__(self) -> str:
        return f"custom: {self.subcode} — {self.message}"


#: Module-level alias for the discriminated union of all variants.
ToolErrorWire = (
    ToolNotFound
    | SessionMismatch
    | PermissionDenied
    | TransportClosed
    | Timeout
    | Cancelled
    | InvalidArguments
    | Execution
    | UnsupportedProtocolVersion
    | PayloadTooLarge
    | BehaviorVersionUnsupported
    | RenderLimited
    | TerminalError
    | Internal
    | Custom
)


# -----------------------------------------------------------------------
# Registry + dispatch (to_wire is on the base; from_wire dispatches here).
# -----------------------------------------------------------------------


#: ``code`` tag → variant class. Built from each variant's ``code`` ClassVar
#: so the rename overrides (``forbidden`` etc.) fall out automatically.
_VARIANTS: dict[str, type[_ToolErrorWireBase]] = {
    ToolNotFound.code: ToolNotFound,
    SessionMismatch.code: SessionMismatch,
    PermissionDenied.code: PermissionDenied,
    TransportClosed.code: TransportClosed,
    Timeout.code: Timeout,
    Cancelled.code: Cancelled,
    InvalidArguments.code: InvalidArguments,
    Execution.code: Execution,
    UnsupportedProtocolVersion.code: UnsupportedProtocolVersion,
    PayloadTooLarge.code: PayloadTooLarge,
    BehaviorVersionUnsupported.code: BehaviorVersionUnsupported,
    RenderLimited.code: RenderLimited,
    TerminalError.code: TerminalError,
    Internal.code: Internal,
    Custom.code: Custom,
}


#: Wire field-name → id wrapper for ``from_wire`` re-wrapping. Fields typed
#: ``ToolId`` / ``RequestId`` (transparent ``str`` newtypes) need explicit
#: re-wrapping because the dataclass-generated ``__init__`` assigns the raw
#: value without invoking ``ToolId(value)``.
_ID_FIELD_TYPES: dict[str, type[str]] = {
    "ToolId": ToolId,
    "RequestId": RequestId,
}


def _coerce_field(type_str: Any, value: object) -> object:
    """Re-wrap a wire value if the field type is an id newtype.

    ``from __future__ import annotations`` makes ``Field.type`` the literal
    annotation string (e.g. ``"ToolId"`` or ``"RequestId | None"``); split
    off the ``| None`` arm before the lookup. Non-id types pass through
    (``serde_json::Value`` is opaque JSON).
    """
    if not isinstance(type_str, str):
        return value
    base = type_str.split("|")[0].strip()
    cls = _ID_FIELD_TYPES.get(base)
    if cls is not None and isinstance(value, str):
        return cls(value)
    return value


def from_wire(data: dict[str, object]) -> ToolErrorWire:
    """Reconstruct a variant from ``{"code": <tag>, ...fields}``.

    Dispatches on ``data["code"]``; absent optional fields default to
    ``None`` (the ``#[serde(default)]`` arm). Unknown codes raise
    :class:`ValueError` (mirrors serde failing the typed parse).
    """
    code = str(data["code"])
    cls = _VARIANTS.get(code)
    if cls is None:
        raise ValueError(f"unknown ToolErrorWire code {code!r}")
    kwargs: dict[str, object] = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        kwargs[f.name] = _coerce_field(f.type, data[f.name])
    return cls(**kwargs)  # type: ignore[arg-type]
