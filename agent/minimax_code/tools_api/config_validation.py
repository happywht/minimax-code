"""Validation of ToolConfigEntry fields (R191).

Fusion of grok-build's ``xai-grok-tools-api/src/config_validation.rs`` (257
lines). Shared validation so the backend's save-time check cannot drift from
what the tools server enforces at finalize/bind. Errors carry the offending
input so callers can render gRPC violations without re-parsing.

Consumes:

* :class:`minimax_code.tool_protocol.ids.ToolId` (R82) for the
  ``name_override`` charset/format contract.
* ``crate::ToolConfigEntry`` -- a protobuf type (the crate's ``pub mod pb``),
  not migrated on the platform (see :mod:`minimax_code.tools_api` pb YAGNI
  ledger). :func:`first_unknown_tool_id` reads only ``entry.id``, so it accepts
  any object with a string ``.id`` attribute (duck-typed), letting the rule run
  without pulling in the pb wire type.

Rust -> Python adaptation
-------------------------

``Result<T, ToolConfigEntryError>`` becomes ``T`` on success (or ``None`` for
``Ok(None)`` / ``Ok(())``), raising :class:`ToolConfigEntryError` on failure --
Rust models the error as a ``thiserror::Error`` (it ``impl std::error::Error``),
so surfacing it as a Python exception matches the type's role. Rust tests use
``.unwrap()`` / ``.unwrap_err()``; the Python suite uses ``pytest.raises``.

The three ``ToolConfigEntryErrorKind`` enum variants become three
``@dataclass(frozen=True)`` subclasses of a common marker base, so callers
discriminate with ``isinstance`` the way Rust discriminates with ``match``.
``serde_json::Value`` -> ``typing.Any`` (any JSON-decoded value); the
non-object variant stores the decoded value verbatim for error rendering.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Protocol

from minimax_code.tool_protocol.ids import IdError, ToolId

__all__ = [
    "NameOverrideInvalid",
    "ParamsJsonNotObject",
    "ParamsJsonParse",
    "ToolConfigEntryError",
    "ToolConfigEntryErrorKind",
    "first_unknown_tool_id",
    "parse_params_json",
    "validate_name_override",
]


# ---------------------------------------------------------------------------
# ToolConfigEntryErrorKind (Rust enum -> frozen-dataclass subclass family).
# ---------------------------------------------------------------------------


class ToolConfigEntryErrorKind:
    """Why a ``ToolConfigEntry`` is invalid (Rust enum marker base).

    Rust models this as a three-variant ``#[derive(Debug, Clone, PartialEq, Eq)]``
    enum; Python surfaces each variant as a ``@dataclass(frozen=True)``
    subclass so callers discriminate with ``isinstance`` the way Rust
    discriminates with ``match``. The base is never instantiated.
    """


@dataclass(frozen=True)
class ParamsJsonParse(ToolConfigEntryErrorKind):
    """``params_json`` is not valid JSON (``ParamsJsonParse``).

    Includes an explicitly-set empty string: proto3 ``optional`` tracks
    presence, so ``Some("")`` is rejected, not treated as unset.
    """

    error: str
    raw: str


@dataclass(frozen=True)
class ParamsJsonNotObject(ToolConfigEntryErrorKind):
    """``params_json`` is valid JSON but not an object (``ParamsJsonNotObject``)."""

    value: Any


@dataclass(frozen=True)
class NameOverrideInvalid(ToolConfigEntryErrorKind):
    """``name_override`` is not a valid :class:`ToolId` (``NameOverrideInvalid``)."""

    name: str
    error: str


class ToolConfigEntryError(Exception):
    """Validation error for one entry in a tool-config list.

    Carries the failing ``index``, the entry's ``tool_id``, and the structured
    ``kind`` so callers can render gRPC field violations without re-parsing.
    Rust ``impl std::error::Error`` -> Python ``Exception`` subclass; Rust
    ``Result<_, Self>`` -> raised.
    """

    def __init__(
        self,
        index: int,
        tool_id: str,
        kind: ToolConfigEntryErrorKind,
    ) -> None:
        self.index = index
        self.tool_id = tool_id
        self.kind = kind
        super().__init__(self._render())

    def field_path(self) -> str:
        """Request field path of the failing field, e.g. ``tools[3].params_json``."""
        if isinstance(self.kind, (ParamsJsonParse, ParamsJsonNotObject)):
            return f"tools[{self.index}].params_json"
        if isinstance(self.kind, NameOverrideInvalid):
            return f"tools[{self.index}].name_override"
        raise TypeError(
            f"unknown ToolConfigEntryErrorKind variant: {type(self.kind).__name__}"
        )

    def _render(self) -> str:
        kind = self.kind
        if isinstance(kind, ParamsJsonParse):
            return (
                f"{self.tool_id}: {self.field_path()} failed to parse JSON: {kind.error}"
            )
        if isinstance(kind, ParamsJsonNotObject):
            return f"{self.tool_id}: {self.field_path()} must be a JSON object"
        if isinstance(kind, NameOverrideInvalid):
            return (
                f"{self.tool_id}: {self.field_path()} is not a valid tool name "
                f"({kind.name!r}): {kind.error}"
            )
        raise TypeError(
            f"unknown ToolConfigEntryErrorKind variant: {type(self.kind).__name__}"
        )

    def __str__(self) -> str:
        return self._render()

    def __repr__(self) -> str:
        return (
            "ToolConfigEntryError("
            f"index={self.index!r}, tool_id={self.tool_id!r}, kind={self.kind!r})"
        )

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ToolConfigEntryError):
            return NotImplemented
        return (self.index, self.tool_id, self.kind) == (
            other.index,
            other.tool_id,
            other.kind,
        )


def parse_params_json(
    index: int, tool_id: str, params_json: str | None
) -> dict[str, Any] | None:
    """Parse and validate ``params_json``, returning the decoded object.

    Returns the decoded dict on success, or ``None`` when ``params_json`` is
    unset (``Ok(None)``). Raises :class:`ToolConfigEntryError` on a parse
    failure or a non-object value. ``index`` / ``tool_id`` are only used for
    error reporting.
    """
    if params_json is None:
        return None
    try:
        value = json.loads(params_json)
    except json.JSONDecodeError as err:
        raise ToolConfigEntryError(
            index,
            tool_id,
            ParamsJsonParse(error=str(err), raw=params_json),
        ) from err
    if isinstance(value, dict):
        return value
    raise ToolConfigEntryError(index, tool_id, ParamsJsonNotObject(value=value))


def validate_name_override(
    index: int, tool_id: str, name_override: str | None
) -> None:
    """Validate ``name_override`` against the :class:`ToolId` contract.

    Returns ``None`` on success (``Ok(())``) or when unset. Raises
    :class:`ToolConfigEntryError` (``NameOverrideInvalid``) when the name fails
    the :class:`ToolId` charset/format check.
    """
    if name_override is None:
        return None
    try:
        ToolId(name_override)
    except IdError as err:
        raise ToolConfigEntryError(
            index,
            tool_id,
            NameOverrideInvalid(name=name_override, error=str(err)),
        ) from err
    return None


class _ToolConfigEntryLike(Protocol):
    """Structural type for a config entry -- only ``.id`` is read.

    Mirrors ``crate::ToolConfigEntry`` (a protobuf type under the crate's
    ``pub mod pb``); not migrated on the platform (see pb YAGNI ledger).
    :func:`first_unknown_tool_id` touches only ``entry.id``, so any object with
    a string ``.id`` attribute satisfies it.
    """

    id: str


def first_unknown_tool_id(
    entries: Iterable[_ToolConfigEntryLike], allowed_ids: set[str]
) -> tuple[int, str] | None:
    """First entry whose ``id`` is not in ``allowed_ids``, as ``(index, id)``.

    Returns ``None`` when every id is allowed (or the list is empty). Pure so
    backend save-time validation and any future consumer share one rule.

    ``entries`` is typed ``Iterable[_ToolConfigEntryLike]``: any object with a
    string ``.id`` attribute satisfies the structural contract (the real
    ``ToolConfigEntry`` is a pb type, YAGNI on the platform).
    """
    for index, entry in enumerate(entries):
        if entry.id not in allowed_ids:
            return index, entry.id
    return None
