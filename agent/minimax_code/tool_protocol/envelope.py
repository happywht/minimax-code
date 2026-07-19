"""JSON-RPC 2.0 envelope types with Grok session_id/seq extensions (R84).

Fusion of grok-build's ``xai-tool-protocol::envelope`` — the JSON-RPC 2.0
request / notification / response / error wrappers, the strict protocol-
version marker, and the dual-typed envelope ``id`` field.

Two serde shapes land here, both new to the crate:

* :class:`JsonRpcVersion` — a unit struct with a *custom* ``Serialize`` /
  ``Deserialize`` that only accepts the literal string ``"2.0"`` and
  rejects anything else (mirrors Rust's ``deserialize_str`` visitor that
  errors on any non-literal value).
* :class:`JsonRpcId` — ``#[serde(untagged)]`` over ``String | Number``,
  the crate's **first untagged enum**. Landing it closes the crate's
  four-shape serde coverage: internal-tag (``ToolDefinitionMode`` /
  ``ToolErrorWire`` / ``McpBlock``), adjacent-tag (``ToolOutputWire`` /
  ``WireToolNotification``), untagged (``JsonRpcId``), and transparent
  newtype (the id / ``FrameSeq`` family). R83 flagged untagged as the one
  untouched shape; R84 touches it.

The response envelope carries a ``result`` XOR ``error`` invariant
enforced via a custom ``Serialize`` / ``Deserialize`` (Rust's ``Flat``
struct pattern): a payload containing both keys, or neither, fails to
deserialize with a message that names the violated arm so callers can
assert on it (``"... XOR ..."`` / ``"... `result` or `error` ..."``).

Two id concepts
---------------

* :class:`JsonRpcId` (this module) is the JSON-RPC envelope ``id`` field —
  string OR number on the wire, per-connection, sender-allocated.
* :class:`~minimax_code.tool_protocol.ids.RequestId` (ids) is an opaque
  newtype wrapping a string, used internally as a correlator. Convert
  between them via :meth:`JsonRpcIdString.from_request_id` /
  :meth:`JsonRpcIdString.as_request_id` (and the ``Number`` arm's
  :meth:`JsonRpcIdNumber.as_request_id`, which stringifies).

Naming
------

Rust's ``JsonRpcId::String`` / ``::Number`` tuple variants and
``ResponseOutcome::Result`` / ``::Error`` variants collide with Python
builtins and common names; they land here with a prefix
(:class:`JsonRpcIdString` / :class:`JsonRpcIdNumber`,
:class:`ResponseResult` / :class:`ResponseError`). :data:`JsonRpcId` and
:data:`ResponseOutcome` are the discriminated-union aliases.

YAGNI
-----

``JsonRpcId::new_uuid_v7`` is deferred: Python's stdlib ``uuid`` has no v7
generator (same constraint as ``ToolCallId::new_v7`` in R82). It is not
re-exported from the Rust ``lib.rs`` ``pub use`` set, so the barrel is
unaffected.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, TypeVar

from minimax_code.tool_protocol.ids import FrameSeq, RequestId, SessionId

__all__ = [
    "JsonRpcVersion",
    "JsonRpcVersionError",
    "JsonRpcId",
    "JsonRpcIdString",
    "JsonRpcIdNumber",
    "jsonrpc_id_from_wire",
    "JsonRpcRequest",
    "JsonRpcNotification",
    "JsonRpcError",
    "JsonRpcResponse",
    "ResponseOutcome",
    "ResponseResult",
    "ResponseError",
]

P = TypeVar("P")
R = TypeVar("R")

#: ``i64`` lower bound (mirrors Rust's ``i64`` wire type on the ``Number`` arm).
_I64_MIN = -(2**63)
#: ``i64`` upper bound.
_I64_MAX = 2**63 - 1


# -----------------------------------------------------------------------
# JsonRpcVersion — strict literal "2.0" unit struct.
# -----------------------------------------------------------------------


class JsonRpcVersionError(ValueError):
    """A wire value was not the literal ``"2.0"`` (custom Deserialize reject).

    Reproduces Rust's ``de::Error::custom("expected jsonrpc \\"2.0\\", got {v:?}")``
    — the ``{v:?}`` Debug format maps to Python :func:`repr` (quoted for
    strings).
    """

    def __init__(self, value: object) -> None:
        self.value = value
        super().__init__(f'expected jsonrpc "2.0", got {value!r}')


class JsonRpcVersion:
    """JSON-RPC 2.0 protocol version marker (unit struct).

    Serialises as the literal string ``"2.0"``; :meth:`from_wire` rejects
    any other value (non-string or a different string) with
    :class:`JsonRpcVersionError`, mirroring Rust's ``deserialize_str``
    visitor that only accepts the literal. Singleton semantics — all
    instances are equal and hash identically.
    """

    VERSION: str = "2.0"

    __slots__ = ()

    def to_wire(self) -> str:
        """The literal ``"2.0"`` (custom ``Serialize``)."""
        return self.VERSION

    @classmethod
    def from_wire(cls, data: object) -> JsonRpcVersion:
        """Reconstruct only if ``data`` is the literal ``"2.0"`` string.

        Mirrors Rust's ``visit_str``: a non-string or any value other than
        ``"2.0"`` raises :class:`JsonRpcVersionError`.
        """
        if not isinstance(data, str) or data != cls.VERSION:
            raise JsonRpcVersionError(data)
        return cls()

    def __eq__(self, other: object) -> bool:
        return isinstance(other, JsonRpcVersion)

    def __hash__(self) -> int:
        return hash(("JsonRpcVersion", self.VERSION))

    def __repr__(self) -> str:
        return "JsonRpcVersion"

    def __str__(self) -> str:
        return self.VERSION


# -----------------------------------------------------------------------
# JsonRpcId — untagged enum (String | Number), the crate's first untagged.
# -----------------------------------------------------------------------


@dataclass
class JsonRpcIdString:
    """The string variant of :data:`JsonRpcId` (``JsonRpcId::String``).

    Untagged: serialises as the bare string, no discriminator. Built via
    :meth:`new_string` or :meth:`from_request_id`; round-trips through
    :func:`jsonrpc_id_from_wire`.
    """

    value: str

    def to_wire(self) -> str:
        return self.value

    @classmethod
    def new_string(cls, s: str) -> JsonRpcIdString:
        """Build a string id (``JsonRpcId::new_string``)."""
        return cls(value=s)

    @classmethod
    def from_request_id(cls, request_id: RequestId) -> JsonRpcIdString:
        """Project a :class:`RequestId` to the envelope id (``from_request_id``)."""
        return cls(value=str(request_id))

    def as_request_id(self) -> RequestId:
        """Project to a :class:`RequestId` (``as_request_id``).

        Raises :class:`~minimax_code.tool_protocol.ids.EmptyIdError` (via
        :class:`RequestId`'s constructor) if the string is empty.
        """
        return RequestId(self.value)

    def __str__(self) -> str:
        return self.value


@dataclass
class JsonRpcIdNumber:
    """The number variant of :data:`JsonRpcId` (``JsonRpcId::Number``).

    Untagged: serialises as the bare integer. ``bool`` is rejected (it is
    an ``int`` subclass that serde does not coerce to ``i64``); the value
    is range-checked against ``i64`` the way Rust's ``i64`` deserialiser
    rejects out-of-range inputs.
    """

    value: int

    def __post_init__(self) -> None:
        if isinstance(self.value, bool) or not isinstance(self.value, int):
            raise TypeError(
                f"JsonRpcIdNumber expects i64, got {type(self.value).__name__}"
            )
        if self.value < _I64_MIN or self.value > _I64_MAX:
            raise ValueError(
                f"JsonRpcIdNumber expects i64, got out-of-range {self.value}"
            )

    def to_wire(self) -> int:
        return self.value

    def as_request_id(self) -> RequestId:
        """Project to a :class:`RequestId`, stringifying the number.

        ``Number(7)`` → :class:`RequestId` ``"7"`` (mirrors Rust's
        ``n.to_string()``).
        """
        return RequestId(str(self.value))

    def __str__(self) -> str:
        return str(self.value)


#: Discriminated union of the two untagged variants (the ``JsonRpcId`` enum).
JsonRpcId = JsonRpcIdString | JsonRpcIdNumber


def jsonrpc_id_from_wire(data: object) -> JsonRpcId:
    """Reconstruct a :data:`JsonRpcId` from an untagged wire value.

    Dispatches on Python type the way serde's ``untagged`` tries each arm:
    ``str`` → :class:`JsonRpcIdString`, ``int`` (non-``bool``) →
    :class:`JsonRpcIdNumber`. ``bool`` and any other type raise
    :class:`ValueError` (mirrors serde failing the untagged parse).
    """
    if isinstance(data, bool):
        raise ValueError(
            f"JsonRpcId must be string or number, got {type(data).__name__}"
        )
    if isinstance(data, str):
        return JsonRpcIdString(value=data)
    if isinstance(data, int):
        return JsonRpcIdNumber(value=data)
    raise ValueError(
        f"JsonRpcId must be string or number, got {type(data).__name__}"
    )


# -----------------------------------------------------------------------
# Shared payload helper (params / result / error.data).
# -----------------------------------------------------------------------


def _payload_to_wire(obj: object) -> object:
    """Serialise a generic params/result/``error.data`` payload.

    If the payload carries a ``to_wire`` (a frames struct once that module
    lands), call it; otherwise pass through (the ``serde_json::Value`` arm
    — a bare dict / list / primitive).
    """
    to_wire = getattr(obj, "to_wire", None)
    if callable(to_wire):
        return to_wire()
    return obj


# -----------------------------------------------------------------------
# JsonRpcRequest<P> — request envelope.
# -----------------------------------------------------------------------


@dataclass
class JsonRpcRequest(Generic[P]):
    """JSON-RPC 2.0 request envelope (``JsonRpcRequest<P>``).

    Generic over ``params`` so callers can pin a concrete schema (e.g. a
    frames ``ToolCallParams`` once that module lands) without losing the
    envelope's invariants. ``session_id`` is a Grok routing/sanity-check
    extension omitted from the wire when ``None``
    (``skip_serializing_if = "Option::is_none"``).

    Field order note: Rust declares ``jsonrpc, id, session_id, method,
    params``; Python's dataclass rule (non-default before default) forces
    ``session_id`` last. Wire key order is hand-controlled in
    :meth:`to_wire` to match Rust; JSON object key order is not
    semantically significant.
    """

    jsonrpc: JsonRpcVersion
    id: JsonRpcId
    method: str
    params: P
    session_id: SessionId | None = None

    def to_wire(self) -> dict[str, object]:
        d: dict[str, object] = {
            "jsonrpc": self.jsonrpc.to_wire(),
            "id": self.id.to_wire(),
        }
        if self.session_id is not None:
            d["session_id"] = str(self.session_id)
        d["method"] = self.method
        d["params"] = _payload_to_wire(self.params)
        return d

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> JsonRpcRequest[Any]:
        kwargs: dict[str, object] = {
            "jsonrpc": JsonRpcVersion.from_wire(data["jsonrpc"]),
            "id": jsonrpc_id_from_wire(data["id"]),
            "method": str(data["method"]),
            "params": data["params"],
        }
        if "session_id" in data and data["session_id"] is not None:
            kwargs["session_id"] = SessionId(str(data["session_id"]))
        return cls(**kwargs)  # type: ignore[arg-type]


# -----------------------------------------------------------------------
# JsonRpcNotification<P> — notification envelope (no id).
# -----------------------------------------------------------------------


@dataclass
class JsonRpcNotification(Generic[P]):
    """JSON-RPC 2.0 notification envelope (``JsonRpcNotification<P>``).

    No ``id`` (notifications produce no response). ``seq`` is an optional
    per-connection monotonic counter (:class:`FrameSeq`) so receivers can
    dedup and detect drops; both ``session_id`` and ``seq`` are omitted
    when ``None``.
    """

    jsonrpc: JsonRpcVersion
    method: str
    params: P
    session_id: SessionId | None = None
    seq: FrameSeq | None = None

    def to_wire(self) -> dict[str, object]:
        d: dict[str, object] = {
            "jsonrpc": self.jsonrpc.to_wire(),
        }
        if self.session_id is not None:
            d["session_id"] = str(self.session_id)
        if self.seq is not None:
            d["seq"] = self.seq.to_wire()
        d["method"] = self.method
        d["params"] = _payload_to_wire(self.params)
        return d

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> JsonRpcNotification[Any]:
        kwargs: dict[str, object] = {
            "jsonrpc": JsonRpcVersion.from_wire(data["jsonrpc"]),
            "method": str(data["method"]),
            "params": data["params"],
        }
        if "session_id" in data and data["session_id"] is not None:
            kwargs["session_id"] = SessionId(str(data["session_id"]))
        if "seq" in data and data["seq"] is not None:
            kwargs["seq"] = FrameSeq.from_wire(data["seq"])  # type: ignore[arg-type]
        return cls(**kwargs)  # type: ignore[arg-type]


# -----------------------------------------------------------------------
# JsonRpcError — error object.
# -----------------------------------------------------------------------


@dataclass
class JsonRpcError:
    """JSON-RPC error object (``JsonRpcError``).

    ``code`` is the numeric envelope code; ``data`` typically carries a
    serialised :class:`~minimax_code.tool_protocol.error_wire.ToolErrorWire`
    so receivers can switch on the stable string code rather than the
    numeric. ``data`` is omitted from the wire when ``None``.
    """

    code: int
    message: str
    data: Any = None

    def to_wire(self) -> dict[str, object]:
        d: dict[str, object] = {"code": self.code, "message": self.message}
        if self.data is not None:
            d["data"] = _payload_to_wire(self.data)
        return d

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> JsonRpcError:
        return cls(
            code=int(data["code"]),  # type: ignore[arg-type]
            message=str(data["message"]),
            data=data.get("data"),
        )


# -----------------------------------------------------------------------
# ResponseOutcome<R> — Result(R) | Error(JsonRpcError).
# -----------------------------------------------------------------------


@dataclass
class ResponseResult(Generic[R]):
    """The success arm of :data:`ResponseOutcome` (``ResponseOutcome::Result``)."""

    value: R


@dataclass
class ResponseError:
    """The failure arm of :data:`ResponseOutcome` (``ResponseOutcome::Error``)."""

    error: JsonRpcError


#: Discriminated union of the two response outcomes (the ``ResponseOutcome<R>`` enum).
ResponseOutcome = ResponseResult | ResponseError


# -----------------------------------------------------------------------
# JsonRpcResponse<R> — response envelope (result XOR error invariant).
# -----------------------------------------------------------------------


@dataclass
class JsonRpcResponse(Generic[R]):
    """JSON-RPC 2.0 response envelope (``JsonRpcResponse<R>``).

    Per the spec exactly one of ``result`` / ``error`` is present; the
    custom ``Serialize`` / ``Deserialize`` (Rust's ``Flat`` struct pattern)
    enforces that invariant — a payload with both keys, or neither, fails
    to deserialize. ``session_id`` is optional, omitted when ``None``.

    Field order note: Rust declares ``jsonrpc, id, session_id, outcome``;
    Python's dataclass rule forces ``session_id`` last. Wire key order is
    hand-controlled in :meth:`to_wire`.
    """

    jsonrpc: JsonRpcVersion
    id: JsonRpcId
    outcome: ResponseOutcome
    session_id: SessionId | None = None

    @classmethod
    def ok(
        cls,
        id_: JsonRpcId,
        result: R,
        session_id: SessionId | None = None,
    ) -> JsonRpcResponse[R]:
        """Build a success response (``JsonRpcResponse::ok``)."""
        return cls(
            jsonrpc=JsonRpcVersion(),
            id=id_,
            outcome=ResponseResult(value=result),
            session_id=session_id,
        )

    @classmethod
    def err(
        cls,
        id_: JsonRpcId,
        error: JsonRpcError,
        session_id: SessionId | None = None,
    ) -> JsonRpcResponse[R]:
        """Build an error response (``JsonRpcResponse::err``)."""
        return cls(
            jsonrpc=JsonRpcVersion(),
            id=id_,
            outcome=ResponseError(error=error),
            session_id=session_id,
        )

    def with_session(self, session_id: SessionId) -> JsonRpcResponse[R]:
        """Attach a session id, returning ``self`` (``with_session``).

        Mirrors Rust's ``mut self`` consumer: the method mutates and
        returns the same instance so it chains inline.
        """
        self.session_id = session_id
        return self

    def to_wire(self) -> dict[str, object]:
        d: dict[str, object] = {
            "jsonrpc": self.jsonrpc.to_wire(),
            "id": self.id.to_wire(),
        }
        if self.session_id is not None:
            d["session_id"] = str(self.session_id)
        if isinstance(self.outcome, ResponseResult):
            d["result"] = _payload_to_wire(self.outcome.value)
        else:
            d["error"] = self.outcome.error.to_wire()
        return d

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> JsonRpcResponse[Any]:
        jsonrpc = JsonRpcVersion.from_wire(data["jsonrpc"])
        id_ = jsonrpc_id_from_wire(data["id"])
        result = data.get("result")
        error = data.get("error")
        # ``Option<R>`` arm: absent or JSON ``null`` both surface as ``None``
        # (serde ``Option`` maps ``null`` → ``None``), so ``is not None``
        # matches Rust's has-arm semantics rather than a bare key-presence check.
        if result is not None and error is not None:
            raise ValueError(
                "JSON-RPC response must contain `result` XOR `error`, got both"
            )
        if result is None and error is None:
            raise ValueError(
                "JSON-RPC response must contain `result` or `error`"
            )
        if result is not None:
            outcome: ResponseOutcome = ResponseResult(value=result)
        else:
            outcome = ResponseError(
                error=JsonRpcError.from_wire(error)  # type: ignore[arg-type]
            )
        kwargs: dict[str, object] = {
            "jsonrpc": jsonrpc,
            "id": id_,
            "outcome": outcome,
        }
        if "session_id" in data and data["session_id"] is not None:
            kwargs["session_id"] = SessionId(str(data["session_id"]))
        return cls(**kwargs)  # type: ignore[arg-type]
