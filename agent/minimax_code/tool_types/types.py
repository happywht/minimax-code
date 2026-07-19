"""Tool schema core type layer (R65).

Fusion of grok-build's ``xai-tool-types`` crate — the wire vocabulary for
describing tools, their arguments, and JSON-Schema argument schemas. Pure
types + pure logic, zero I/O.

This is the **tool-plane schema vocabulary**, the symmetric counterpart to
R64's management-plane wire DTO (:mod:`minimax_code.extensions`):

* R64 — extension system wire contract (hooks / plugins / MCP / marketplace).
* R65 — tool system schema vocabulary (tool / argument / type-tag / validate).

Together they close the two "type contract" surfaces a platform shell needs:
*what tools exist* (R65) and *how the shell is extended* (R64).

Forward-migrated from Rust (``#[serde(rename_all = "lowercase")]`` /
``#[serde(untagged)]`` / ``skip_serializing_if``) to pydantic v2 + StrEnum +
a custom ``SchemaType`` class. Wire fidelity is preserved by a hand-written
``to_wire()`` on each struct model that reproduces serde's conditional
omission (``required=true`` and empty ``allowed_values`` are dropped).

Mapping notes (see ITERATION_LOG R65 for the full decision tree):

* ``ArgumentType`` enum → :class:`StrEnum` (wire value == member value).
* ``SchemaType`` untagged enum → plain class holding a tuple of
  :class:`ArgumentType`; ``Single`` is a 1-tuple, ``Multiple`` a longer
  tuple. ``from_value`` normalises a single-element array to ``Single``
  (matching grok); the ``Multiple`` constructor is preserved verbatim.
* ``extra: Extensions`` field on ``ToolDescription`` is dropped (YAGNI): it
  is a Rust ``TypeId``-keyed type-erased map, ``#[serde(skip)]`` so never on
  the wire, with no Python equivalent. ``PartialEq`` ignoring it is moot.
* ``validate()`` returns :class:`ValidationErrors` (empty == ok), mirroring
  Rust ``Result<(), ValidationErrors>`` rather than raising.
"""

from __future__ import annotations

import copy
import re
import warnings
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

# pydantic v2 emits a UserWarning because a field named ``schema`` shadows the
# historical ``BaseModel.schema()`` method. That method was **removed** in v2
# (replaced by ``model_json_schema()``), so the shadow is a false positive — no
# real collision exists. We keep the name ``schema`` for wire fidelity with
# grok's ``xai-tool-types`` crate (the Rust field and the on-wire JSON key are
# both ``schema``); renaming would diverge from the source for no behavioural
# benefit. The filter is scoped to this module and this exact message.
warnings.filterwarnings(
    "ignore",
    message=r'Field name "schema" in "ToolArgument" shadows an attribute .*',
    category=UserWarning,
)

__all__ = [
    "ArgumentType",
    "SchemaType",
    "ToolArgument",
    "ToolDescription",
    "ValidationError",
    "ValidationErrors",
]


# ---------------------------------------------------------------------------
# ArgumentType — JSON Schema type tag (serde rename_all = "lowercase")
# ---------------------------------------------------------------------------


class ArgumentType(StrEnum):
    """A single JSON Schema ``type`` tag.

    Wire form is the lowercase member value (``"string"``, ``"integer"``,
    …), matching Rust ``#[serde(rename_all = "lowercase")]``. As a
    :class:`StrEnum` the value round-trips through ``json`` unchanged and
    compares equal to its bare string (``ArgumentType.STRING == "string"``).
    """

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    ARRAY = "array"
    OBJECT = "object"
    NULL = "null"

    @classmethod
    def from_schema_type(cls, s: str) -> ArgumentType | None:
        """Parse a JSON Schema ``type`` string; ``None`` if unrecognized.

        Mirrors Rust ``ArgumentType::from_schema_type`` — unknown tags
        return ``None`` rather than raising (used by ``SchemaType.from_value``
        to fall back to the default).
        """
        try:
            return cls(s)
        except ValueError:
            return None

    def as_str(self) -> str:
        """Stable lowercase identifier (identical to ``self.value``)."""
        return self.value

    def is_primitive(self) -> bool:
        """String / Integer / Number / Boolean / Null are primitive."""
        return self in (
            ArgumentType.STRING,
            ArgumentType.INTEGER,
            ArgumentType.NUMBER,
            ArgumentType.BOOLEAN,
            ArgumentType.NULL,
        )

    def is_numeric(self) -> bool:
        """Integer / Number are numeric (bounds are meaningful only then)."""
        return self in (ArgumentType.INTEGER, ArgumentType.NUMBER)

    def is_composite(self) -> bool:
        """Array / Object are composite (carry a nested ``schema``)."""
        return self in (ArgumentType.ARRAY, ArgumentType.OBJECT)


# ---------------------------------------------------------------------------
# SchemaType — JSON Schema "type" value (serde untagged: string | array)
# ---------------------------------------------------------------------------


class SchemaType:
    """A JSON Schema ``type`` value: a single type or a union of types.

    Wire form is **untagged** (Rust ``#[serde(untagged)]``): ``"string"`` for
    a single type, ``["string", "null"]`` for a union. Internally we hold a
    tuple of :class:`ArgumentType` plus a variant flag so the
    ``Single([t])`` vs ``Multiple([t])`` distinction (which matters for serde
    round-trips of single-element arrays) is preserved.

    Construct with :meth:`single` / :meth:`multiple`, or parse an arbitrary
    JSON value with :meth:`from_value` (which normalises a single-element
    array down to ``Single``, matching grok).
    """

    __slots__ = ("_types", "_variant")

    def __init__(
        self,
        types: ArgumentType | list[ArgumentType] | tuple[ArgumentType, ...],
        *,
        variant: str | None = None,
    ) -> None:
        if isinstance(types, ArgumentType):
            self._types: tuple[ArgumentType, ...] = (types,)
            self._variant: str = "single"
        else:
            self._types = tuple(types)
            self._variant = variant if variant is not None else (
                "single" if len(self._types) <= 1 else "multiple"
            )

    # -- constructors -------------------------------------------------------

    @classmethod
    def single(cls, t: ArgumentType) -> SchemaType:
        """The ``Single(t)`` variant."""
        return cls((t,), variant="single")

    @classmethod
    def multiple(cls, types: list[ArgumentType]) -> SchemaType:
        """The ``Multiple([t, ...])`` variant (single-element preserved)."""
        return cls(tuple(types), variant="multiple")

    @classmethod
    def from_value(cls, v: Any) -> SchemaType:
        """Parse a JSON Schema ``type`` value (string or array).

        * bare string → ``Single`` (unknown string falls back to default).
        * array → 0 elements: default; 1 element: ``Single`` (normalised);
          >1 elements: ``Multiple``.
        * anything else → default.

        Note: this is the *manual* parse path used by the schema parser and
        callers. It normalises single-element arrays to ``Single``, unlike
        serde untagged deserialization (which would keep them ``Multiple``).
        """
        if isinstance(v, str):
            t = ArgumentType.from_schema_type(v)
            return cls.single(t) if t is not None else cls.default()
        if isinstance(v, list):
            parsed: list[ArgumentType] = []
            for item in v:
                if isinstance(item, str):
                    t = ArgumentType.from_schema_type(item)
                    if t is not None:
                        parsed.append(t)
            if not parsed:
                return cls.default()
            if len(parsed) == 1:
                return cls.single(parsed[0])
            return cls.multiple(parsed)
        return cls.default()

    @classmethod
    def default(cls) -> SchemaType:
        """Default is ``Single(String)`` (Rust ``#[default]`` on ArgumentType)."""
        return cls.single(ArgumentType.STRING)

    # -- accessors ----------------------------------------------------------

    @property
    def is_single(self) -> bool:
        return self._variant == "single"

    @property
    def is_multiple(self) -> bool:
        return self._variant == "multiple"

    @property
    def types(self) -> tuple[ArgumentType, ...]:
        """Underlying type tuple (single-element for the ``Single`` variant)."""
        return self._types

    def primary_type(self) -> ArgumentType:
        """First non-null type, or ``Null`` if the union is all-null.

        ``Single(t)`` → ``t``; ``Multiple([String, Null])`` → ``String``;
        ``Multiple([Null])`` / ``Single(Null)`` → ``Null``.
        """
        for t in self._types:
            if t != ArgumentType.NULL:
                return t
        return ArgumentType.NULL

    def is_nullable(self) -> bool:
        """Whether the union accepts null alongside its primary type."""
        if self._variant == "single":
            return self._types[0] == ArgumentType.NULL
        return ArgumentType.NULL in self._types

    def contains(self, ty: ArgumentType) -> bool:
        """Whether the union includes a specific :class:`ArgumentType`."""
        return ty in self._types

    def is_primitive(self) -> bool:
        """True when **every** type in the union is primitive."""
        return all(t.is_primitive() for t in self._types)

    def is_composite(self) -> bool:
        """True when **any** type in the union is composite."""
        return any(t.is_composite() for t in self._types)

    def is_numeric(self) -> bool:
        """True when **any** type in the union is numeric."""
        return any(t.is_numeric() for t in self._types)

    def to_schema_value(self) -> Any:
        """The JSON Schema ``type`` representation (string for single, array)."""
        if self._variant == "single":
            return self._types[0].value
        return [t.value for t in self._types]

    # -- dunder -------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        # Rust ``PartialEq<ArgumentType>``: a SchemaType equals a bare
        # ArgumentType iff it is the Single variant holding that type.
        # Multiple never equals a bare ArgumentType.
        if isinstance(other, ArgumentType):
            return self._variant == "single" and self._types == (other,)
        if isinstance(other, SchemaType):
            return self._types == other._types and self._variant == other._variant
        return NotImplemented

    def __hash__(self) -> int:
        return hash((self._variant, self._types))

    def __repr__(self) -> str:
        if self._variant == "single":
            return f"SchemaType.single({self._types[0]!r})"
        return f"SchemaType.multiple({list(self._types)!r})"

    def __str__(self) -> str:
        return ", ".join(t.value for t in self._types)


# ---------------------------------------------------------------------------
# Validation errors
# ---------------------------------------------------------------------------


@dataclass
class ValidationError:
    """A single structural-validation issue keyed by field name."""

    field: str
    message: str

    def __str__(self) -> str:
        return f"{self.field}: {self.message}"


@dataclass
class ValidationErrors:
    """A collection of :class:`ValidationError` (Rust newtype wrapper).

    Returned by :meth:`ToolDescription.validate`; empty == ok. Iterable and
    sized so callers can write ``any(e.field == "name" for e in errors)``
    and ``len(errors)`` directly, mirroring the Rust ``iter`` / ``len`` API.
    """

    errors: list[ValidationError] = field(default_factory=list)

    def __iter__(self):
        return iter(self.errors)

    def __len__(self) -> int:
        return len(self.errors)

    def is_empty(self) -> bool:
        return not self.errors

    def __str__(self) -> str:
        return "; ".join(str(e) for e in self.errors)


# ---------------------------------------------------------------------------
# ToolArgument
# ---------------------------------------------------------------------------


# ASCII-only identifier check — Rust ``char::is_ascii_alphanumeric | '_' | '-'``.
# Python ``str.isalnum`` is Unicode-wide, so a regex pin is required to reject
# CJK / accented letters (e.g. "中文" must be invalid).
_IDENTIFIER_RE = re.compile(r"[A-Za-z0-9_-]+")


def _validate_identifier(field_name: str, value: str, errors: list[ValidationError]) -> None:
    """Append a :class:`ValidationError` if ``value`` is empty or non-identifier."""
    if not value:
        errors.append(
            ValidationError(
                field=field_name,
                message=f"{field_name} must not be empty",
            )
        )
    elif not _IDENTIFIER_RE.fullmatch(value):
        errors.append(
            ValidationError(
                field=field_name,
                message=(
                    f"{field_name} {value!r} contains invalid characters "
                    "(allowed: a-z, A-Z, 0-9, _, -)"
                ),
            )
        )


class ToolArgument(BaseModel):
    """A single argument of a :class:`ToolDescription`.

    Wire shape (grok serde, reproduced by :meth:`to_wire`):

    * ``name``, ``description`` always present.
    * ``type`` (alias of ``arg_type``) always present — single string or
      array, per :class:`SchemaType`.
    * ``schema`` / ``default`` / ``minimum`` / ``maximum`` /
      ``exclusive_minimum`` / ``exclusive_maximum`` omitted when ``None``.
    * ``required`` omitted when ``True`` (the default); emitted as
      ``false`` only when optional.
    * ``allowed_values`` omitted when empty.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    name: str
    description: str
    arg_type: SchemaType = Field(default_factory=SchemaType.default, alias="type")
    schema: Any | None = None
    required: bool = True
    default: Any | None = None
    allowed_values: list[Any] = Field(default_factory=list)
    minimum: int | float | None = None
    maximum: int | float | None = None
    exclusive_minimum: int | float | None = None
    exclusive_maximum: int | float | None = None

    # -- serde bridges for the SchemaType field -----------------------------

    @field_validator("arg_type", mode="before")
    @classmethod
    def _normalize_arg_type(cls, v: Any) -> SchemaType:
        if isinstance(v, SchemaType):
            return v
        return SchemaType.from_value(v)

    @field_serializer("arg_type")
    def _serialize_arg_type(self, v: SchemaType) -> Any:
        return v.to_schema_value()

    # -- constructors / builder chain ---------------------------------------

    @classmethod
    def new(cls, name: str, description: str) -> ToolArgument:
        """Minimal required-argument constructor (``arg_type`` defaults to String)."""
        return cls(name=name, description=description)

    def with_type(self, arg_type: ArgumentType | SchemaType) -> ToolArgument:
        self.arg_type = arg_type if isinstance(arg_type, SchemaType) else SchemaType.single(arg_type)
        return self

    def with_schema(self, schema: Any) -> ToolArgument:
        self.schema = schema
        return self

    def set_optional(self) -> ToolArgument:
        self.required = False
        return self

    def with_default(self, default: Any) -> ToolArgument:
        self.default = default
        return self

    def with_allowed_values(self, values: list[Any]) -> ToolArgument:
        self.allowed_values = list(values)
        return self

    def with_minimum(self, min_: int | float) -> ToolArgument:
        self.minimum = min_
        return self

    def with_maximum(self, max_: int | float) -> ToolArgument:
        self.maximum = max_
        return self

    def with_exclusive_minimum(self, min_: int | float) -> ToolArgument:
        self.exclusive_minimum = min_
        return self

    def with_exclusive_maximum(self, max_: int | float) -> ToolArgument:
        self.exclusive_maximum = max_
        return self

    # -- wire serialization (precise grok serde skip semantics) -------------

    def to_wire(self) -> dict[str, Any]:
        """Emit the grok wire shape with conditional omission.

        Reproduces Rust ``skip_serializing_if`` exactly: ``required`` is
        dropped when ``True``, ``allowed_values`` when empty, and every
        ``Optional`` field when ``None``. The ``arg_type`` field is emitted
        under its ``type`` alias as the schema value (string or array).
        """
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "type": self.arg_type.to_schema_value(),
        }
        if self.schema is not None:
            out["schema"] = self.schema
        if not self.required:
            out["required"] = False
        if self.default is not None:
            out["default"] = self.default
        if self.allowed_values:
            out["allowed_values"] = self.allowed_values
        if self.minimum is not None:
            out["minimum"] = self.minimum
        if self.maximum is not None:
            out["maximum"] = self.maximum
        if self.exclusive_minimum is not None:
            out["exclusive_minimum"] = self.exclusive_minimum
        if self.exclusive_maximum is not None:
            out["exclusive_maximum"] = self.exclusive_maximum
        return out


# ---------------------------------------------------------------------------
# ToolDescription
# ---------------------------------------------------------------------------


class ToolDescription(BaseModel):
    """A tool's declarative description: name, namespace, kind, schema.

    The Rust struct carries an ``extra: Extensions`` field
    (``#[serde(skip)]``) — a ``TypeId``-keyed type-erased metadata map with
    no Python equivalent and no wire presence. It is dropped here (YAGNI);
    its only behavioural effect in Rust was making ``PartialEq`` ignore it,
    which is moot once the field is absent.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        arbitrary_types_allowed=True,
    )

    name: str
    namespace: str | None = None
    title: str | None = None
    description: str
    arguments_schema: Any | None = None
    kind: str | None = None

    # -- constructors / builder chain ---------------------------------------

    @classmethod
    def new(cls, name: str, description: str) -> ToolDescription:
        return cls(name=name, description=description)

    def with_namespace(self, namespace: str) -> ToolDescription:
        self.namespace = namespace
        return self

    def with_kind(self, kind: str) -> ToolDescription:
        self.kind = kind
        return self

    def with_title(self, title: str) -> ToolDescription:
        self.title = title
        return self

    def with_arguments_schema(self, schema: Any) -> ToolDescription:
        self.arguments_schema = schema
        return self

    # -- schema access ------------------------------------------------------

    def to_arguments_lossy(self) -> list[ToolArgument]:
        """Structured args parsed (lossy) from ``arguments_schema``.

        Delegates to :func:`minimax_code.tool_types.schema_parser.\
        parse_arguments_from_schema_lossy` (lazy import to avoid a circular
        dependency — the parser imports the type layer). Returns ``[]`` when
        no schema is attached.
        """
        if self.arguments_schema is None:
            return []
        from .schema_parser import parse_arguments_from_schema_lossy

        return parse_arguments_from_schema_lossy(self.arguments_schema)

    def get_arguments_schema(self) -> Any | None:
        """The raw attached JSON Schema, if any (``None`` when unset)."""
        return self.arguments_schema

    def to_input_schema(self) -> Any:
        """The JSON Schema to present to the model for this tool's arguments.

        If a raw ``arguments_schema`` is attached it is returned verbatim
        (deep-copied, mirroring Rust ``.clone()``) — top-level ``$defs`` are
        preserved so downstream ``$ref`` resolvers work. Otherwise an empty
        object schema (``{"type": "object", "properties": {}, "required": []}``)
        is synthesised.
        """
        if self.arguments_schema is not None:
            return copy.deepcopy(self.arguments_schema)
        return {"type": "object", "properties": {}, "required": []}

    # -- validation ---------------------------------------------------------

    def validate(self) -> ValidationErrors:
        """Check structural invariants; return all issues (empty == ok).

        * ``name`` must be non-empty and ``[a-zA-Z0-9_-]`` only.
        * ``namespace`` (if set) must be non-empty and same charset.

        Returns every error found (not just the first), matching Rust
        ``Result<(), ValidationErrors>``.
        """
        errors: list[ValidationError] = []
        _validate_identifier("name", self.name, errors)
        if self.namespace is not None:
            if self.namespace == "":
                errors.append(
                    ValidationError(
                        field="namespace",
                        message="namespace must not be empty when set",
                    )
                )
            else:
                _validate_identifier("namespace", self.namespace, errors)
        return ValidationErrors(errors)

    # -- display ------------------------------------------------------------

    def __str__(self) -> str:
        """``"name"``, ``"ns.name"``, or ``"… — description"`` (debug format).

        Uses an em dash (U+2014) to match grok's ``Display`` impl exactly.
        Not a canonical identifier — for display/debug only.
        """
        prefix = f"{self.namespace}.{self.name}" if self.namespace else self.name
        if self.description:
            return f"{prefix} — {self.description}"
        return prefix
