"""Adjacent-tagged wire-union helper (R67).

Mirrors serde's ``#[serde(tag = "type", content = "data")]`` — every wire
enum in ``xai-grok-workspace-types`` uses this shape:

    {"type": "<variant_snake_case>", "data": <payload>}

Adjacent tagging is the only serde form that works uniformly across
struct, newtype, and unit variants — internal tagging rejects the
newtype/unit shapes the crate leans on (``WorkspaceError::Vcs(String)``,
``PermissionDecision::AllowOnce``). Pydantic v2 discriminated unions
need every arm to be a BaseModel sharing the discriminator field, which
is awkward when the discriminator lives in an *outer* wrapper and
payloads are heterogeneous — so we hand-roll a tiny base class instead.
The crate's ``lib.rs`` "# Wire format" doc-comment spells out the
rationale: adjacent tagging keeps the rendered JSON consistent across
the whole tree (no mix of adjacent + internal tagging in one document).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, ClassVar

__all__ = ["AdjacentTagged"]


class AdjacentTagged:
    """Base for ``{type, data}`` adjacent-tagged wire unions.

    Subclasses define ``_VARIANTS`` (tuple of valid snake_case ``type``
    strings, in declaration order) and a factory classmethod per arm.
    The payload shape per arm mirrors its Rust variant:

    * **struct variant**  → ``dict[str, Any]``
    * **newtype variant** → the bare inner value (``str`` / ``list[str]``)
    * **unit variant**    → ``None``

    The payload is intentionally a plain value, not a pydantic model —
    variant payloads range from empty (unit) to a bare string (newtype)
    to nested structs, and a uniform container keeps the wire code
    trivial. Variant field validation lives in each subclass's factory
    methods.
    """

    _VARIANTS: ClassVar[tuple[str, ...]] = ()

    # Plain attributes — no __slots__: payload is polymorphic and some
    # subclasses add extra surface (e.g. WorkspaceError.__str__).
    kind: str
    payload: Any

    def __init__(self, kind: str, payload: Any = None) -> None:
        if kind not in self._VARIANTS:
            raise ValueError(
                f"unknown {type(self).__name__} variant {kind!r}; "
                f"expected one of {self._VARIANTS}"
            )
        self.kind = kind
        self.payload = payload

    # -- wire ---------------------------------------------------------------

    def to_wire(self) -> dict[str, Any]:
        """Emit ``{"type": kind, "data": payload}``."""
        return {"type": self.kind, "data": self.payload}

    @classmethod
    def from_wire(cls, data: Any) -> AdjacentTagged:
        """Parse ``{"type": ..., "data": ...}`` (inverse of :meth:`to_wire`)."""
        if not isinstance(data, Mapping) or "type" not in data or "data" not in data:
            raise ValueError(
                f"{cls.__name__}.from_wire expects a mapping with 'type' and "
                f"'data' keys; got {data!r}"
            )
        return cls(data["type"], data["data"])

    # -- dunder -------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        if type(self) is not type(other):
            return NotImplemented
        return self.kind == other.kind and self.payload == other.payload

    def __hash__(self) -> int:  # pragma: no cover - wire unions are rarely hashed
        payload = self.payload
        if isinstance(payload, dict):
            keyed = tuple(sorted(payload.items()))
        elif isinstance(payload, list):
            keyed = tuple(payload)
        else:
            keyed = payload
        return hash((type(self).__name__, self.kind, keyed))

    def __repr__(self) -> str:
        if self.payload is None:
            return f"{type(self).__name__}.{self.kind}()"
        return f"{type(self).__name__}.{self.kind}({self.payload!r})"
