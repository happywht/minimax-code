"""Parsed server notification events (R144).

Fusion of grok-build's ``xai-computer-hub-sdk/src/notification.rs`` (362
lines) -- the SDK crate's 12th leaf (after R133 error / R134 handshake /
R135 refcount / R136 donate_pump / R137 trace_donate / R138 connection_borrow
/ R139 auth / R140 observability / R141 cancel / R142 admission / R143 pool).

:data:`HubNotification` is the typed representation of server-pushed
notification frames that arrive on a session inbox. :func:`parse` classifies
a raw JSON value by its ``method`` field and deserialises the known shapes;
anything unrecognised lands in :class:`HubUnknown` so callers never lose
data. :func:`parse` returns ``None`` ONLY when the value lacks a ``method``
field (i.e. it is not a notification at all).

Three dispatch paths + one Unknown catch-all (mirrors ``HubNotification::parse``):

1. ``"tools_changed"`` -- deserialise :class:`ToolsChanged` from ``params``
   (``session_id`` rides inside ``params``); on failure fall back to Unknown.
2. ``"tool.notification"`` -- deserialise :class:`ToolNotificationFrame` from
   ``params`` (the frame carries no ``session_id``; the envelope field is
   used) AND lift ``session_id`` from the envelope; BOTH must succeed to
   continue. Special-case: when ``frame.tool_id == "__tool_server_status"``
   and the notification is a :class:`Custom` whose ``kind == "status_changed"``,
   deserialise :class:`ToolServerStatusPayload` from the custom payload -- on
   success return :class:`HubToolServerStatusChanged` (early return); on
   failure ``warn`` and fall through to :class:`HubToolNotification`. If the
   frame OR the envelope session_id is missing/invalid, fall back to Unknown.
3. Any other ``method`` -- :class:`HubUnknown`.

What migrates vs what does NOT (R132-style YAGNI boundary declaration)
---------------------------------------------------------------------

MIGRATED (pure parsing logic, every dependency already landed in R82-R106):

* Four frozen dataclasses + Union alias (:data:`HubNotification`) and the
  :func:`parse` classifier.
* ``_envelope_session_id`` -- the ``SessionId::new(s).ok()`` lift.
* Method/string discriminators + the ``__tool_server_status`` sentinel.

NOT MIGATED: none. Unlike R142 admission (metrics stubs) or R143 pool
(opener registrar), this leaf is a pure parser with no framework glue to
stub -- it consumes only R82-R106 tool_protocol symbols.

Python-specific adaptations (no behavior change):

* Rust ``enum HubNotification { 4 variants }`` -> four ``@dataclass(frozen=True)``
  classes (:class:`HubToolsChanged` / :class:`HubToolNotification` /
  :class:`HubToolServerStatusChanged` / :class:`HubUnknown`) plus a Union
  alias :data:`HubNotification`. A ``Hub`` prefix avoids collision with the
  tool_protocol :class:`ToolsChanged` / :class:`ToolNotificationFrame` the
  variants reference. Callers dispatch with ``isinstance`` (the Rust
  ``match`` equivalent).
* Rust ``HubNotification::parse(value)`` associated function -> module-level
  :func:`parse` (Python has no associated-function syntax).
* Rust ``serde_json::from_value::<T>(v)`` -> the R82-R106 module-level
  ``*_from_wire(v)`` helpers wrapped in ``try/except`` (catching
  KeyError/ValueError/TypeError); failure degrades to Unknown, never raises.
* Rust ``tracing::warn!`` -> :func:`logging.warning`.
* Rust ``Vec<ToolId>`` -> ``tuple[ToolId, ...]`` (an immutable sequence for
  the frozen dataclass; built from the wire list).
* Rust ``SessionId::new(s).ok()`` -> ``SessionId(raw)`` guarded by an
  ``isinstance(raw, str)`` check (SessionId is a str newtype that never
  raises on construction; a non-str envelope value yields ``None``, mirroring
  ``.ok()``'s None arm).
* Rust ``c.kind`` / ``c.payload`` (where ``c`` is the newtype-variant payload
  ``WireToolNotification::Custom(WireCustomNotification)``) ->
  ``frame.notification.notification.kind`` / ``.payload`` in Python: R83 chose
  a struct ``Custom(notification=WireCustomNotification)`` for the
  adjacent-tagging serde shape, so the access path carries one extra
  ``.notification`` hop (documented in notification_wire.py).
* Rust ``Value`` (arbitrary JSON) for Unknown params -> Python ``Any`` so a
  non-object ``params`` value is preserved verbatim in :class:`HubUnknown`
  (matches Rust's ``value.get("params").cloned().unwrap_or(Value::Object(default))``
  which keeps the raw value when present).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from minimax_code.tool_protocol.frames import (
    ToolNotificationFrame,
    ToolServerStatusPayload,
    tool_notification_frame_from_wire,
    tool_server_status_payload_from_wire,
    tools_changed_from_wire,
)
from minimax_code.tool_protocol.ids import SessionId, ToolId
from minimax_code.tool_protocol.notification_wire import Custom

__all__ = [
    "HubNotification",
    "HubToolsChanged",
    "HubToolNotification",
    "HubToolServerStatusChanged",
    "HubUnknown",
    "parse",
]

_logger = logging.getLogger(__name__)

#: ``method`` discriminator for the tool-set delta push.
_METHOD_TOOLS_CHANGED: str = "tools_changed"
#: ``method`` discriminator for a forwarded tool notification.
_METHOD_TOOL_NOTIFICATION: str = "tool.notification"
#: Sentinel ``tool_id`` marking a server-status notification (not a real tool).
_TOOL_SERVER_STATUS_ID: str = "__tool_server_status"
#: Custom-notification ``kind`` that lifts a tool-server status payload.
_TOOL_SERVER_STATUS_KIND: str = "status_changed"


@dataclass(frozen=True)
class HubToolsChanged:
    """The active tool set for a session changed (``HubNotification::ToolsChanged``).

    ``session_id`` rides inside ``params`` (not the envelope); the three
    delta tuples are immutable (``Vec<ToolId>`` -> ``tuple[ToolId, ...]``).
    """

    session_id: SessionId
    added: tuple[ToolId, ...]
    removed: tuple[ToolId, ...]
    updated: tuple[ToolId, ...]


@dataclass(frozen=True)
class HubToolNotification:
    """A tool notification forwarded by the server (``HubNotification::ToolNotification``).

    ``session_id`` comes from the envelope (the frame itself carries none);
    :attr:`frame` is the typed :class:`ToolNotificationFrame`.
    """

    session_id: SessionId
    frame: ToolNotificationFrame


@dataclass(frozen=True)
class HubToolServerStatusChanged:
    """Tool-server lifecycle status change (``HubNotification::ToolServerStatusChanged``).

    Extracted from the ``__tool_server_status`` / ``status_changed`` custom
    notification; :attr:`status` is the typed :class:`ToolServerStatusPayload`.
    """

    session_id: SessionId
    status: ToolServerStatusPayload


@dataclass(frozen=True)
class HubUnknown:
    """A notification whose ``method`` is not recognised by this SDK version.

    Carries the raw ``method`` + ``params`` so the caller never loses the
    event (forward-compat for methods a newer server may emit). ``params`` is
    :data:`typing.Any` to preserve a non-object value verbatim, mirroring
    Rust's ``serde_json::Value`` payload.
    """

    method: str
    params: Any


#: Typed sum of the four notification shapes (the Rust ``enum`` equivalent).
#: Callers dispatch with ``isinstance`` rather than a Rust ``match``.
HubNotification = (
    HubToolsChanged | HubToolNotification | HubToolServerStatusChanged | HubUnknown
)


def _envelope_session_id(value: dict[str, Any]) -> SessionId | None:
    """Lift ``session_id`` from the envelope (``SessionId::new(s).ok()``).

    Returns ``None`` when absent or non-str, or when ``SessionId`` construction
    itself fails -- mirroring Rust's
    ``value.get("session_id").and_then(Value::as_str).and_then(SessionId::new).ok()``.
    SessionId is a str newtype so construction is infallible for a str; the
    ``isinstance`` guard rejects non-str envelope values, and the broad
    ``except`` is a belt-and-suspenders match for ``.ok()`` (BLE is off the
    ruff select list, so bare ``except Exception`` is acceptable here).
    """
    raw = value.get("session_id")
    if not isinstance(raw, str):
        return None
    try:
        return SessionId(raw)
    except Exception:
        return None


def parse(value: dict[str, Any]) -> HubNotification | None:
    """Classify a raw JSON-RPC notification into a typed :data:`HubNotification`.

    Returns ``None`` when ``value`` has no ``method`` field (it is not a
    notification). Known shapes are deserialised; a deserialisation failure
    OR an unrecognised ``method`` lands in :class:`HubUnknown` so the caller
    never loses the event (mirrors ``HubNotification::parse``).

    The ``params`` value is preserved verbatim into :class:`HubUnknown`
    (matching Rust's ``value.get("params").cloned().unwrap_or(Value::Object(default))``
    -- present-and-non-object stays as-is, absent becomes ``{}``). The two
    deserialising paths pass ``params`` straight to the ``*_from_wire``
    helper; a non-object ``params`` there raises KeyError/ValueError/TypeError
    and degrades to Unknown rather than propagating.
    """
    raw_method = value.get("method")
    if not isinstance(raw_method, str):
        return None
    method = raw_method
    raw_params = value.get("params")
    # Rust: value.get("params").cloned().unwrap_or(Value::Object(default)).
    # Present -> keep the raw value (any JSON type); absent -> empty object.
    params: Any = raw_params if raw_params is not None else {}

    if method == _METHOD_TOOLS_CHANGED:
        try:
            tc = tools_changed_from_wire(params)
        except (KeyError, ValueError, TypeError) as err:
            _logger.warning(
                "tools_changed params failed to deserialize; "
                "falling back to Unknown: %s",
                err,
            )
            return HubUnknown(method=method, params=params)
        return HubToolsChanged(
            session_id=tc.session_id,
            added=tuple(tc.added),
            removed=tuple(tc.removed),
            updated=tuple(tc.updated),
        )

    if method == _METHOD_TOOL_NOTIFICATION:
        try:
            frame = tool_notification_frame_from_wire(params)
        except (KeyError, ValueError, TypeError) as err:
            _logger.warning(
                "tool.notification params failed to deserialize; "
                "falling back to Unknown: %s",
                err,
            )
            return HubUnknown(method=method, params=params)
        session_id = _envelope_session_id(value)
        if session_id is None:
            _logger.warning(
                "tool.notification missing or invalid session_id; "
                "falling back to Unknown"
            )
            return HubUnknown(method=method, params=params)

        # Special-case: __tool_server_status + status_changed custom kind ->
        # lift the health payload. A payload-deserialize failure here falls
        # through to the generic ToolNotification (matches Rust: warn +
        # continue, NOT Unknown -- the frame itself parsed fine).
        notif = frame.notification
        if (
            frame.tool_id == _TOOL_SERVER_STATUS_ID
            and isinstance(notif, Custom)
            and notif.notification.kind == _TOOL_SERVER_STATUS_KIND
        ):
            try:
                status = tool_server_status_payload_from_wire(
                    notif.notification.payload
                )
            except (KeyError, ValueError, TypeError) as err:
                _logger.warning(
                    "tool_server status payload failed to deserialize: %s", err
                )
            else:
                return HubToolServerStatusChanged(
                    session_id=session_id, status=status
                )
        return HubToolNotification(session_id=session_id, frame=frame)

    return HubUnknown(method=method, params=params)
