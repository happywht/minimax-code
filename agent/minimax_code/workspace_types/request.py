"""Request envelope (R78).

Fusion of grok's ``xai-grok-workspace-types::request`` — the
:class:`RequestMessage` generic wire envelope, the crate's top-level
dispatch surface. Every workspace RPC starts life wrapped in a
:class:`RequestMessage` before the runtime layer adds cancellation /
extensions: this is just the parts that survive a network hop (typed
payload + per-call metadata + optional deadline).

This is the first of the crate's four top-level dispatch modules
(``request`` → ``requests`` → ``events`` → ``chunks``) to land: ``rpc/``
(R67-R77) and ``types/`` (R67) are the leaf payloads this envelope
carries. R78 lands the envelope itself, plus the crate's re-export of
``RequestMessage`` at the ``lib.rs`` root.

This file lands five serde patterns new to the layer:

* **pydantic v2 generic wire model** — :class:`RequestMessage` is
  ``Generic[T]`` over the payload type, the layer's first generic wire
  model. ``RequestMessage[str]`` / ``RequestMessage[int]`` parameterise
  the ``message`` field schema; pydantic v2 generates a per-parameter
  schema on subscription. This mirrors Rust's ``RequestMessage<T>``
  without a Python-side type-erasure hack.
* **``#[serde(default)]`` always-emitted field** — ``metadata`` carries
  ``#[serde(default)]`` with **no** ``skip_serializing_if``, so an empty
  map still emits ``"metadata": {}``. ``Field(default_factory=Metadata)``
  plus the inherited :meth:`WireModel.to_wire` (no field elision)
  reproduces this: the key is always present.
* **``skip_serializing_if = "Option::is_none"`` deadline elision** —
  ``deadline`` is ``Option<DateTime<Utc>>`` with
  ``#[serde(default, skip_serializing_if = "Option::is_none")]``: omitted
  when ``None``. :meth:`RequestMessage.to_wire` overrides the base to pop
  the ``deadline`` key when ``self.deadline is None`` — a targeted
  single-field override (simpler than R77's generic ``_DropNoneWire``
  base, which exists for structs where every Option is dropped).
* **``DateTime<Utc>`` RFC 3339 ``Z`` suffix** — ``deadline`` is a
  ``chrono::DateTime<Utc>``, which serde emits with a ``Z`` suffix.
  pydantic's default ``+00:00`` is rewritten to ``Z`` via a
  :data:`PlainSerializer`, identical to R76's ``rpc.hunks.IsoUtc``. The
  helper is duplicated here rather than imported to keep the top-level
  envelope free of a ``rpc/`` dependency; a future round may lift it to
  ``_wire``.
* **Rust builder move semantics** — ``with_metadata`` / ``with_deadline``
  take ``mut self`` and return ``self`` (``#[must_use]``); reproduced as
  in-place mutation returning the same instance. :meth:`map` mirrors
  ``map<U>(self, f: FnOnce(T) -> U) -> RequestMessage<U>``: it preserves
  metadata + deadline while applying ``f`` to the payload, instantiating
  a fresh envelope whose payload type may differ from the source's.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Any, Generic, TypeVar

from pydantic import Field, PlainSerializer

from minimax_code.workspace_types._wire import WireModel
from minimax_code.workspace_types.metadata import Metadata

__all__ = ["RequestMessage", "IsoUtc"]

T = TypeVar("T")


# === DateTime<Utc> RFC 3339 Z-suffix serialiser ===


def _dt_to_wire(d: datetime) -> str:
    """Serialise a datetime as RFC 3339 with a ``Z`` suffix (chrono-compatible).

    Identical to ``rpc.hunks._dt_to_wire`` (R76); duplicated here to keep the
    top-level envelope free of a ``rpc/`` dependency. A future round may lift
    both into ``_wire``.
    """
    d = d.astimezone(UTC)
    if d.microsecond:
        base = d.strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0").rstrip(".")
    else:
        base = d.strftime("%Y-%m-%dT%H:%M:%S")
    return base + "Z"


#: ``chrono::DateTime<Utc>`` wire alias (chrono emits a ``Z`` suffix).
IsoUtc = Annotated[datetime, PlainSerializer(_dt_to_wire, return_type=str)]


class RequestMessage(WireModel, Generic[T]):
    """Wire-side request envelope (generic over the payload type).

    Wraps a typed payload (:attr:`message`) with per-call
    :attr:`metadata` and an optional absolute :attr:`deadline`. The
    runtime envelope adds cancellation + an in-process extensions map;
    those are not wire concerns (see the module doc for why). Mirrors
    Rust's ``RequestMessage<T>``.

    ``metadata`` defaults to an empty :class:`Metadata` and is always
    emitted (``#[serde(default)]``). ``deadline`` defaults to ``None``
    and is omitted when ``None`` (``skip_serializing_if =
    "Option::is_none"``).
    """

    message: T
    metadata: Metadata = Field(default_factory=Metadata)
    deadline: IsoUtc | None = None

    @classmethod
    def new(cls, message: T) -> RequestMessage[T]:
        """Construct a request with empty metadata and no deadline."""
        return cls(message=message)

    def with_metadata(self, metadata: Metadata) -> RequestMessage[T]:
        """Builder: attach metadata (in-place, mirrors ``mut self``)."""
        self.metadata = metadata
        return self

    def with_deadline(self, deadline: datetime) -> RequestMessage[T]:
        """Builder: set the absolute deadline (in-place, mirrors ``mut self``)."""
        self.deadline = deadline
        return self

    def map(self, f):  # noqa: ANN001, ANN202
        """Map the inner payload while preserving metadata and deadline.

        Mirrors ``map<U>(self, f: FnOnce(T) -> U) -> RequestMessage<U>``:
        applies ``f`` to :attr:`message` and returns a fresh envelope
        carrying the new payload with the original ``metadata`` (copied
        into a new :class:`Metadata`, mirrors the source's move) and
        ``deadline``. Uses :meth:`pydantic.BaseModel.model_construct`
        rather than direct construction or :meth:`model_copy`: direct
        ``RequestMessage(...)`` re-runs validation under an
        unparameterised ``T = Any`` schema and misroutes the kwargs
        through the ``metadata`` dict field (pydantic generic +
        ``Metadata`` dict-schema interaction); :meth:`model_copy` would
        keep the source's parameterised schema (e.g.
        ``RequestMessage[int]``) and emit a spurious serializer warning
        when the new payload type ``U`` differs from ``T``.
        ``model_construct`` bypasses validation, so the result carries an
        unparameterised schema (``T = Any``) that accepts any payload
        type without warning — the closest Python can get to grok's
        ``RequestMessage<U>``.
        """
        return RequestMessage.model_construct(
            message=f(self.message),
            metadata=Metadata(self.metadata),
            deadline=self.deadline,
        )

    def to_wire(self) -> dict[str, Any]:
        """JSON-safe dict with BTreeMap-sorted keys; ``deadline`` omitted when ``None``."""
        wire = super().to_wire()
        if self.deadline is None:
            wire.pop("deadline", None)
        return wire
