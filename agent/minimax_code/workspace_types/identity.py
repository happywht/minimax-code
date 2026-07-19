"""Transparent String newtype wrappers (R67).

Fusion of grok-build's ``xai-grok-workspace-types::identity`` — three
``#[serde(transparent)]`` String wrappers used as stable identifiers
across the workspace wire surface:

* :class:`SessionId`   — a conversation session.
* :class:`ToolCallId`  — one tool invocation within a session.
* :class:`HunkId`      — one tracked diff hunk.

``#[serde(transparent)]`` serialises them as the bare inner String (no
``{"value": ...}`` wrapper), so each is a ``str`` subclass in Python —
``json.dumps(SessionId("s1"))`` emits ``"s1"`` exactly as serde does.

The inner field is ``pub(crate)`` in Rust (the only public constructor is
``new``); here the ``str`` base *is* the value, so construction is just
``SessionId("s1")``. Each carries a typed ``__repr__`` for log legibility.

Pydantic v2 hook
----------------

A bare ``class Foo(str)`` is auto-supported by pydantic, but a subclass
that overrides ``__new__`` (as these do, to keep the ``str`` value
immutable) is *not* — pydantic raises ``PydanticSchemaGenerationError``
when it tries to build a model schema for a field typed as the newtype.
``__get_pydantic_core_schema__`` restores support: validate the input as
``str`` (so a bare JSON string round-trips) and re-wrap it as ``cls``.
Serialisation still emits the bare string because the underlying core
schema is ``str_schema``.
"""

from __future__ import annotations

from typing import Any

from pydantic_core import core_schema

__all__ = ["SessionId", "ToolCallId", "HunkId"]


def _opaque_str_schema(cls: type[str]) -> core_schema.AfterValidatorFunctionSchema:
    """pydantic-core schema for a transparent ``str`` newtype.

    Validates the input as ``str`` and re-wraps it as ``cls``; the inner
    ``str_schema`` drives serialisation so the wire stays a bare string.
    """
    return core_schema.no_info_after_validator_function(cls, core_schema.str_schema())


class SessionId(str):
    """Opaque session identifier — serialises as a bare string."""

    __slots__ = ()

    def __new__(cls, value: str) -> SessionId:
        return super().__new__(cls, value)

    def __repr__(self) -> str:
        return f"SessionId({str.__repr__(self)})"

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        return _opaque_str_schema(cls)


class ToolCallId(str):
    """Opaque tool-call identifier — serialises as a bare string."""

    __slots__ = ()

    def __new__(cls, value: str) -> ToolCallId:
        return super().__new__(cls, value)

    def __repr__(self) -> str:
        return f"ToolCallId({str.__repr__(self)})"

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        return _opaque_str_schema(cls)


class HunkId(str):
    """Opaque hunk identifier — serialises as a bare string."""

    __slots__ = ()

    def __new__(cls, value: str) -> HunkId:
        return super().__new__(cls, value)

    def __repr__(self) -> str:
        return f"HunkId({str.__repr__(self)})"

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        return _opaque_str_schema(cls)
