"""Identifier newtypes for the computer-hub wire protocol (R82).

Fusion of grok-build's ``xai-tool-protocol::ids`` — the string-backed
opaque id newtypes that travel on the wire, plus the per-connection
monotonic ``FrameSeq`` counter. Every wire-traveling id has a dedicated
newtype so a ``SessionId`` cannot be passed where a ``ToolId`` is
expected; constructors validate, and the validating constructor is the
*only* public construction path (the inner value is private in Rust, and
the ``str`` base *is* the value here, so construction runs the checks
through ``__new__``).

Seven string newtypes are emitted by the Rust ``opaque_id!`` macro:

* :class:`SessionId` / :class:`UserId` / :class:`ConnectionId` /
  :class:`RequestId` / :class:`ToolCallId` — non-empty only.
* :class:`ServerId` — non-empty and rejects the reserved ``auto:``
  prefix (computer-hub-synthesised ids); :meth:`ServerId.synthesize_for_tool`
  bypasses the check the way Rust's private constructor does.
* :class:`ToolId` — non-empty and well-formed (``{namespace}:{name}`` or
  ``{name}``, each segment ``[a-zA-Z0-9_-]+``).

:class:`FrameSeq` is a transparent ``u64`` newtype (not a string) — the
crate's first transparent-*non-string* id, serialising to a bare integer.

Pydantic v2 hook
----------------

A bare ``class Foo(str)`` is auto-supported by pydantic, but a subclass
that overrides ``__new__`` (as these do, to run validation before the
``str`` is materialised) is *not*. ``__get_pydantic_core_schema__``
restores support: validate the input as ``str`` and re-wrap it as ``cls``
(via ``__new__``), so a bare JSON string round-trips and a malformed one
surfaces as a pydantic ``ValidationError`` — mirroring Rust's custom
``Deserialize`` that routes through ``Self::new``.

YAGNI boundary
--------------

``ToolCallId::new_v7`` (UUID v7 factory) is deferred: Python's standard
``uuid`` module has no v7 generator. It lands with the uuid6 dependency
evaluation in a later round.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, ClassVar

from pydantic_core import core_schema

__all__ = [
    "IdError",
    "EmptyIdError",
    "InvalidFormatIdError",
    "ReservedPrefixIdError",
    "SessionId",
    "UserId",
    "ConnectionId",
    "RequestId",
    "ToolCallId",
    "ServerId",
    "ToolId",
    "FrameSeq",
]


# -----------------------------------------------------------------------
# IdError hierarchy (mirrors the rust ``IdError`` enum variants).
# -----------------------------------------------------------------------


class IdError(ValueError):
    """Base for id construction / validation errors (``ids::IdError``).

    Rust models this as a three-variant ``thiserror::Error`` enum; Python
    surfaces each variant as a subclass so callers can discriminate with
    ``isinstance`` the way Rust discriminates with ``match``.
    """


class EmptyIdError(IdError):
    """Identifier must not be empty (``IdError::Empty``)."""

    def __init__(self) -> None:
        super().__init__("identifier must not be empty")


class InvalidFormatIdError(IdError):
    """Identifier has an invalid format (``IdError::InvalidFormat``)."""

    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(f"identifier {value!r} has invalid format")


class ReservedPrefixIdError(IdError):
    """Identifier uses a reserved prefix (``IdError::ReservedPrefix``)."""

    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__(f"identifier {value!r} uses a reserved prefix")


# -----------------------------------------------------------------------
# Validation primitives (``is_id_char`` / ``is_valid_segment`` / etc.).
# -----------------------------------------------------------------------


def _is_id_char(c: str) -> bool:
    """Whether ``c`` is an id segment character (ASCII alphanumeric / _ / -).

    Mirrors ``is_id_char``: ``c.is_ascii_alphanumeric() || c == '_' || c == '-'``.
    Restricted to ASCII so a Unicode digit/letter cannot widen the segment
    alphabet the way it could in plain ``str.isalnum``.
    """
    return c.isascii() and c.isalnum() or c == "_" or c == "-"


def _is_valid_segment(s: str) -> bool:
    """Whether ``s`` is a non-empty all-id-char segment (``is_valid_segment``)."""
    return bool(s) and all(_is_id_char(c) for c in s)


def _ensure_non_empty(s: str) -> None:
    """Raise :class:`EmptyIdError` on an empty string (``ensure_non_empty``)."""
    if not s:
        raise EmptyIdError()


# -----------------------------------------------------------------------
# Transparent string id newtypes (``opaque_id!`` macro expansion).
# -----------------------------------------------------------------------


def _opaque_str_schema(cls: type[str]) -> core_schema.AfterValidatorFunctionSchema:
    """pydantic-core schema for a transparent ``str`` newtype.

    Validates the input as ``str`` and re-wraps it as ``cls`` (running the
    per-newtype ``__new__`` validation); the inner ``str_schema`` drives
    serialisation so the wire stays a bare string.
    """
    return core_schema.no_info_after_validator_function(cls, core_schema.str_schema())


class _OpaqueId(str):
    """Base for the transparent string-backed id newtypes.

    Expands the ``opaque_id!`` macro: a ``#[serde(transparent)]`` String
    newtype whose only public constructor (``new``) validates. Subclasses
    optionally declare ``_EXTRA_VALIDATOR`` (a ``fn(str) -> None`` that
    runs after the empty-string check and raises an :class:`IdError`).
    """

    __slots__ = ()

    # Optional post-empty-check validator; ``None`` for the plain
    # non-empty-only ids. Declared on subclasses.
    _EXTRA_VALIDATOR: ClassVar[Callable[[str], None] | None] = None

    def __new__(cls, value: str) -> _OpaqueId:
        if not isinstance(value, str):
            # Rust takes ``impl Into<String>``; the wire deserialiser feeds a
            # ``String``, so require ``str`` here to surface type mix-ups at
            # the boundary rather than coercing silently.
            raise IdError(f"identifier must be a string, got {type(value).__name__}")
        _ensure_non_empty(value)
        validator = cls._EXTRA_VALIDATOR
        if validator is not None:
            validator(value)
        return str.__new__(cls, value)

    def __repr__(self) -> str:
        return f"{type(self).__name__}({str.__repr__(self)})"

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        return _opaque_str_schema(cls)


class SessionId(_OpaqueId):
    """Session identifier (``ids::SessionId``).

    Service-issued or carried from a JWT claim. Non-empty opaque string.
    """


class UserId(_OpaqueId):
    """User identifier (``ids::UserId``) — the JWT ``sub`` claim."""


class ConnectionId(_OpaqueId):
    """Per-connection identifier issued by the computer hub (``ids::ConnectionId``)."""


class RequestId(_OpaqueId):
    """JSON-RPC request id as it appears on the wire (``ids::RequestId``)."""


class ToolCallId(_OpaqueId):
    """End-to-end identifier for a single tool invocation (``ids::ToolCallId``).

    SDKs SHOULD use UUID v7; :meth:`new_v7` is deferred (Python's stdlib
    ``uuid`` has no v7 generator — lands with the uuid6 evaluation).
    """


# --- ServerId ---------------------------------------------------------


#: Lexical prefix reserved for computer-hub-synthesised server ids.
#: Client-supplied values starting with this are rejected by
#: :func:`_validate_server_id`.
_SERVER_ID_RESERVED_PREFIX = "auto:"


def _validate_server_id(s: str) -> None:
    """Reject the reserved ``auto:`` prefix on client-supplied server ids."""
    if s.startswith(_SERVER_ID_RESERVED_PREFIX):
        raise ReservedPrefixIdError(s)


class ServerId(_OpaqueId):
    """Server identifier (``ids::ServerId``).

    Opaque non-empty string; the ``auto:`` prefix is reserved for
    computer-hub-synthesised ids and rejected from client-supplied values.
    """

    _EXTRA_VALIDATOR: ClassVar[Callable[[str], None] | None] = staticmethod(
        _validate_server_id
    )

    @classmethod
    def synthesize_for_tool(
        cls, connection_id: ConnectionId, tool_id: ToolId
    ) -> ServerId:
        """Synthesise the deterministic hub-side id for a single-tool registration.

        Bypasses :meth:`__new__`'s reserved-prefix check (the way Rust's
        private constructor does) by routing through ``str.__new__``.
        ``connection_id`` is part of the signature so callers cannot omit the
        connection scope they are implicitly relying on, even though the
        current encoding does not mix it in. Two connections that register
        the same ``tool_id`` without an explicit ``server_id`` share the
        synthesised id but stay distinct in the registry's primary
        ``(connection_id, tool_id)`` table.
        """
        # ``str.__new__`` skips ``_OpaqueId.__new__`` (and therefore the
        # reserved-prefix check); the value is hub-trusted.
        return str.__new__(cls, f"{_SERVER_ID_RESERVED_PREFIX}tool:{tool_id}")


# --- ToolId -----------------------------------------------------------


def _is_well_formed_tool_id(s: str) -> bool:
    """Whether ``s`` matches ``{namespace}:{name}`` or ``{name}`` (``is_well_formed_tool_id``).

    Each segment must satisfy :func:`_is_valid_segment`. Rust's
    ``splitn(3, ':')`` yields at most three parts; a third segment (a second
    colon) is rejected. Reproduced with ``str.split(':', 2)``.
    """
    parts = s.split(":", 2)
    if len(parts) == 1:
        return _is_valid_segment(parts[0])
    if len(parts) == 2:
        return _is_valid_segment(parts[0]) and _is_valid_segment(parts[1])
    # Three parts (two colons) — Rust's `_ => false` arm.
    return False


def _validate_tool_id(s: str) -> None:
    """Reject a malformed tool id with :class:`InvalidFormatIdError`."""
    if not _is_well_formed_tool_id(s):
        raise InvalidFormatIdError(s)


class ToolId(_OpaqueId):
    """Tool identifier (``ids::ToolId``).

    Format: ``{namespace}:{name}`` or ``{name}``; each segment must match
    ``[a-zA-Z0-9_-]+``.
    """

    _EXTRA_VALIDATOR: ClassVar[Callable[[str], None] | None] = staticmethod(
        _validate_tool_id
    )


# -----------------------------------------------------------------------
# FrameSeq — transparent u64 newtype.
# -----------------------------------------------------------------------


class FrameSeq:
    """Per-connection monotonic notification sequence (``ids::FrameSeq``).

    ``#[serde(transparent)]`` over ``u64`` — serialises to a bare integer.
    Starts at 0 on every new connection. The inner value is private in
    Rust; :meth:`new` / :meth:`from_wire` / the default (0) are the only
    construction paths. Not a ``str`` subclass (unlike the opaque ids), so
    it carries its own ``to_wire`` / ``from_wire`` and pydantic hook.
    """

    __slots__ = ("_value",)

    def __init__(self, value: int = 0) -> None:
        # ``u64`` on the wire: reject ``bool`` (an ``int`` subclass) and
        # negatives so a malformed value cannot widen the wire type.
        if isinstance(value, bool) or not isinstance(value, int):
            raise TypeError(
                f"FrameSeq expects u64, got {type(value).__name__}"
            )
        if value < 0:
            raise ValueError(f"FrameSeq expects u64, got negative {value}")
        self._value = value

    @classmethod
    def new(cls, value: int) -> FrameSeq:
        """Construct from a ``u64`` (``const fn new``)."""
        return cls(value)

    def get(self) -> int:
        """The inner ``u64`` (``fn get``)."""
        return self._value

    # -- wire --------------------------------------------------------------

    def to_wire(self) -> int:
        """Bare ``u64`` (transparent newtype — no wrapper)."""
        return self._value

    @classmethod
    def from_wire(cls, data: int) -> FrameSeq:
        """Reconstruct from a bare ``u64``.

        Rejects non-int and ``bool`` inputs to mirror Rust's ``u64``
        deserialiser (``bool`` is an ``int`` subclass and must be excluded).
        """
        if isinstance(data, bool) or not isinstance(data, int):
            raise ValueError(
                f"FrameSeq.from_wire expects u64, got {type(data).__name__}"
            )
        return cls(data)

    # -- dunder ------------------------------------------------------------

    def __int__(self) -> int:
        return self._value

    def __index__(self) -> int:
        # So FrameSeq is usable where an int index is expected.
        return self._value

    def __eq__(self, other: object) -> bool:
        if isinstance(other, FrameSeq):
            return self._value == other._value
        return NotImplemented

    def __hash__(self) -> int:
        return hash((FrameSeq, self._value))

    def __lt__(self, other: FrameSeq) -> bool:
        if not isinstance(other, FrameSeq):
            return NotImplemented
        return self._value < other._value

    def __le__(self, other: FrameSeq) -> bool:
        if not isinstance(other, FrameSeq):
            return NotImplemented
        return self._value <= other._value

    def __repr__(self) -> str:
        return f"FrameSeq({self._value})"

    def __str__(self) -> str:
        return str(self._value)

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        # Int-like transparent newtype: validate as ``int`` then re-wrap.
        return core_schema.no_info_after_validator_function(cls, core_schema.int_schema())
