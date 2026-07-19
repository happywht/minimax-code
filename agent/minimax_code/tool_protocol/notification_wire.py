"""Adjacent-tagged notification wire wrapper with a forward-compat Custom (R83).

Fusion of grok-build's ``xai-tool-protocol::notification_wire`` — the
envelope around tool-server notifications. Adjacent tagging
(``#[serde(tag = "shape", content = "value", rename_all = "snake_case")]``)
was chosen over ``#[serde(untagged)]`` to eliminate the spoofing risk where
a ``Custom`` payload could silently match a known PascalCase variant: the
discriminator rides *beside* the payload, and :func:`check_custom_kind`
rejects custom kinds that shadow a known variant at emit time (not at
registration time).

Wire shape::

    {"shape": "known",  "value": {"type": "BashOutputChunk", ...}}
    {"shape": "custom", "value": {"kind": "my_tool.progress", "payload": ...}}

Pieces landed here:

* :class:`WireToolNotification` — the adjacent-tagged wrapper (``Known`` /
  :class:`Custom`).
* :class:`WireCustomNotification` — the free-form custom payload
  (``kind`` + ``payload``); ``kind`` MUST NOT collide with a known
  PascalCase variant (see :func:`check_custom_kind`).
* :class:`KnownVariantCollision` — the ``thiserror`` error raised when a
  custom kind shadows a known variant.
* :data:`KNOWN_NOTIFICATION_KINDS` — the PascalCase variant names of known
  notification types (19 entries); source of truth lives upstream, keep in
  sync.
* :func:`check_custom_kind` — collision check run at notification-emit
  time.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "WireToolNotification",
    "Known",
    "Custom",
    "WireCustomNotification",
    "KnownVariantCollision",
    "KNOWN_NOTIFICATION_KINDS",
    "known_notification_kinds",
    "check_custom_kind",
    "from_wire",
]


# -----------------------------------------------------------------------
# WireToolNotification — adjacent-tagged on ``shape`` / ``value``.
# -----------------------------------------------------------------------


@dataclass
class Known:
    """A known notification shape (``WireToolNotification::Known``).

    Serialises as ``{"shape": "known", "value": <value>}``. The payload is
    arbitrary JSON (``serde_json::Value``) — the discriminator that selects
    the concrete known variant lives *inside* ``value`` (e.g. its ``type``
    field), not at this wrapper level.
    """

    value: Any

    def to_wire(self) -> dict[str, object]:
        return {"shape": "known", "value": self.value}

    @classmethod
    def from_value(cls, value: object) -> Known:
        return cls(value=value)


@dataclass
class Custom:
    """A free-form custom notification (``WireToolNotification::Custom``).

    Serialises as ``{"shape": "custom", "value": {"kind": ..., "payload": ...}}``.
    :attr:`notification`'s ``kind`` MUST NOT collide with a known PascalCase
    variant; validate with :func:`check_custom_kind` at emit time.
    """

    notification: WireCustomNotification

    def to_wire(self) -> dict[str, object]:
        return {"shape": "custom", "value": self.notification.to_wire()}

    @classmethod
    def from_value(cls, value: dict[str, object]) -> Custom:
        return cls(notification=WireCustomNotification.from_wire(value))


#: Discriminated union of the two notification shapes.
WireToolNotification = Known | Custom


def from_wire(data: dict[str, object]) -> WireToolNotification:
    """Reconstruct a :class:`WireToolNotification` from ``{"shape", "value"}``.

    Dispatches on ``data["shape"]``; unknown shapes raise :class:`ValueError`.
    """
    shape = data["shape"]
    value = data["value"]
    if shape == "known":
        return Known.from_value(value)
    if shape == "custom":
        return Custom.from_value(value)  # type: ignore[arg-type]
    raise ValueError(f"unknown WireToolNotification shape {shape!r}")


# -----------------------------------------------------------------------
# WireCustomNotification — free-form payload struct.
# -----------------------------------------------------------------------


@dataclass
class WireCustomNotification:
    """Free-form notification payload for kinds the hub does not recognise.

    ``kind`` MUST NOT collide with a known PascalCase variant (see
    :func:`check_custom_kind`). Both fields are always present (no
    ``skip_serializing_if``).
    """

    kind: str
    payload: Any

    def to_wire(self) -> dict[str, object]:
        return {"kind": self.kind, "payload": self.payload}

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> WireCustomNotification:
        return cls(kind=str(data["kind"]), payload=data["payload"])


# -----------------------------------------------------------------------
# KnownVariantCollision — thiserror Error struct.
# -----------------------------------------------------------------------


class KnownVariantCollision(Exception):
    """A custom notification kind shadows a known PascalCase variant.

    ``thiserror`` struct with a single ``kind`` field; the Display string
    uses ``{kind:?}`` (Rust Debug format → Python :func:`repr`, i.e. the
    kind appears quoted). Raised by :func:`check_custom_kind` at
    notification-emit time.
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(
            f"custom notification kind {kind!r} collides with a known variant"
        )


# -----------------------------------------------------------------------
# KNOWN_NOTIFICATION_KINDS + collision check.
# -----------------------------------------------------------------------


#: PascalCase variant names of known notification types (``KNOWN_NOTIFICATION_KINDS``).
#:
#: Source of truth lives upstream; keep this list in sync. An audit test
#: round-trips a representative of every variant and asserts its ``type``
#: discriminator appears here, so upstream additions cause a test failure
#: rather than silent drift.
KNOWN_NOTIFICATION_KINDS: tuple[str, ...] = (
    "BashOutputChunk",
    "BashExecutionComplete",
    "BashExecutionTimeout",
    "BashExecutionBackgrounded",
    "BashExecutionFailed",
    "FileWritten",
    "TaskCompleted",
    "PlanModeEntered",
    "PlanModeExited",
    "UserQuestionAsked",
    "LspServerStarting",
    "LspServerReady",
    "LspServerCrashed",
    "LspServerRetrying",
    "LspServerFailed",
    "ScheduledTaskFired",
    "ScheduledTaskRemoved",
    "ScheduledTaskCreated",
    "MonitorEvent",
)


def known_notification_kinds() -> tuple[str, ...]:
    """Return the known notification kinds (``known_notification_kinds``).

    A function rather than importing the constant directly so the surface
    mirrors the Rust ``const fn``.
    """
    return KNOWN_NOTIFICATION_KINDS


def check_custom_kind(kind: str) -> None:
    """Reject custom notification kinds that shadow a known variant.

    Runs at notification-emit time; raises :class:`KnownVariantCollision`
    if ``kind`` equals a known PascalCase variant, returns ``None``
    otherwise. An empty ``kind`` is accepted here (the producer is
    responsible for validating non-emptiness) — reproduces the Rust
    ``Result<(), KnownVariantCollision>`` return.
    """
    if kind in KNOWN_NOTIFICATION_KINDS:
        raise KnownVariantCollision(kind)
