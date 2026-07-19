"""Hook events delivered from the harness to tools (R89).

Fusion of grok-build's ``xai-tool-protocol::hook`` — the internally-tagged
hook payload enum. The harness delivers these to tool servers bound to a
session: cancel an in-flight call, pause / resume streaming, broadcast
``session_ended``. :class:`Custom` is the forward-compatible escape hatch
for hook kinds not yet named in the enum — unknown kinds travel *inside*
``Custom`` (as ``kind``), not as top-level tags.

Mirrors the Rust ``HookEvent`` enum: ``#[serde(tag = "type")]`` with **no**
``rename_all``, so wire tags are the PascalCase variant names verbatim
(``"Cancel"``, ``"Pause"``, ``"Resume"``, ``"SessionEnded"``,
``"Custom"``) — the crate's first PascalCase-tagged internally-tagged enum
(:mod:`~error_wire` ToolErrorWire / :mod:`~capabilities` HookKind /
:mod:`~registry_error` RegistryError all use snake_case). Four unit variants
plus one struct variant (:class:`Custom`) — the crate's first
internally-tagged enum to mix unit and struct arms (R83 / R88 variants all
carry named fields).

Serde shape
-----------

``#[serde(tag = "type")]`` — internally-tagged, tag key ``"type"`` (the first
appearance of this key; prior enums use ``code`` / ``kind`` / ``shape``).
Unit variants serialise as ``{"type": "<PascalCase>"}`` (no extra fields);
the struct variant serialises as
``{"type": "Custom", "kind": "...", "payload": <any json>}``. Wire tags:

* :class:`Cancel` → ``"Cancel"``.
* :class:`Pause` → ``"Pause"``.
* :class:`Resume` → ``"Resume"``.
* :class:`SessionEnded` → ``"SessionEnded"``.
* :class:`Custom` → ``"Custom"`` (carries ``kind`` + ``payload``).

``to_wire`` lives on each variant dataclass; :func:`hook_event_from_wire`
is the module-level dispatcher keyed on the ``type`` tag (a union of five
dataclasses cannot host a classmethod, mirroring
:func:`~minimax_code.tool_protocol.registry_error.registry_error_from_wire`
and :func:`~minimax_code.tool_protocol.registration.registration_outcome_from_wire`).
Unknown tags raise :class:`ValueError` — there is no catch-all arm beyond
``Custom`` (forward-compat is carried *inside* Custom's ``kind`` field, not
as a top-level ``type``), matching the crate's strict-rejection convention.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = [
    "HookEvent",
    "Cancel",
    "Pause",
    "Resume",
    "SessionEnded",
    "Custom",
    "hook_event_from_wire",
]


# Unit variants carry no fields, so each serialises as just its ``type`` tag.
# One dataclass per variant (rather than a shared empty base) preserves
# per-variant type identity — the ``HookEvent`` union and ``isinstance``
# dispatch rely on distinct classes.


@dataclass
class Cancel:
    """Cancel an in-flight tool call.

    The owning ``tool_call_id`` travels in the enclosing ``hook`` frame, not
    in this payload (the variant carries no fields). Wire tag: ``"Cancel"``.
    """

    def to_wire(self) -> dict[str, object]:
        return {"type": "Cancel"}


@dataclass
class Pause:
    """Pause streaming on an in-flight call. Wire tag: ``"Pause"``."""

    def to_wire(self) -> dict[str, object]:
        return {"type": "Pause"}


@dataclass
class Resume:
    """Resume streaming on a paused call. Wire tag: ``"Resume"``."""

    def to_wire(self) -> dict[str, object]:
        return {"type": "Resume"}


@dataclass
class SessionEnded:
    """Broadcast to every tool server bound to the session.

    Wire tag: ``"SessionEnded"``. No payload — the session id travels in the
    enclosing frame.
    """

    def to_wire(self) -> dict[str, object]:
        return {"type": "SessionEnded"}


@dataclass
class Custom:
    """Forward-compatible escape hatch for hook kinds not yet in the enum.

    ``kind`` is the caller-supplied hook name (a ``String``); ``payload`` is
    an arbitrary JSON value (``serde_json::Value``). Unknown *named* hook
    kinds travel here rather than as top-level ``type`` tags, so the enum
    stays closed while remaining extensible. Wire tag: ``"Custom"``.
    """

    #: The caller-supplied hook kind name.
    kind: str
    #: Arbitrary JSON payload (``serde_json::Value``).
    payload: Any

    def to_wire(self) -> dict[str, object]:
        return {
            "type": "Custom",
            "kind": self.kind,
            "payload": self.payload,
        }


#: The hook-event union — one of the five variants above.
HookEvent = Cancel | Pause | Resume | SessionEnded | Custom

#: ``type`` wire tag → variant dataclass. Keys are PascalCase (no
#: ``rename_all`` on the Rust enum).
_WIRE_TAG_TO_VARIANT: dict[str, type[HookEvent]] = {
    "Cancel": Cancel,
    "Pause": Pause,
    "Resume": Resume,
    "SessionEnded": SessionEnded,
    "Custom": Custom,
}

#: The four unit variants (no constructor fields) — used by the dispatcher
#: to instantiate via ``variant()``.
_UNIT_VARIANTS: frozenset[type[HookEvent]] = frozenset(
    {Cancel, Pause, Resume, SessionEnded}
)


def hook_event_from_wire(data: dict[str, object]) -> HookEvent:
    """Reconstruct a :data:`HookEvent` from its wire form.

    Dispatches on the ``type`` tag (``#[serde(tag = "type")]``). Unknown tags
    raise :class:`ValueError` — forward-compatibility is carried inside
    :class:`Custom`'s ``kind`` field, not as a top-level tag, so there is no
    catch-all arm (matching the crate's strict-rejection convention). The
    four unit variants take no fields; :class:`Custom` re-reads ``kind`` and
    ``payload``.
    """
    tag = str(data["type"])
    variant = _WIRE_TAG_TO_VARIANT.get(tag)
    if variant is None:
        raise ValueError(f"unknown HookEvent type tag: {tag!r}")
    if variant is Custom:
        return Custom(kind=str(data["kind"]), payload=data["payload"])
    return variant()
