"""MCP client events (R36).

Ports the payload-carrying event enum from grok's ``xai-grok-mcp``
``servers.rs`` — ``McpClientEvent`` and its ``McpServerName`` alias. This is
the **event layer** that pairs with R35's ``McpClientEventKind`` discriminant:
where the Kind is a hashable coalescing key, the Event carries the payload
(server name, client id, failure reason, config diff).

Three sources produce these (per grok's doc):

1. The liveness watcher — ``TransportClosed`` (R35's decision layer emits it).
2. The client handler — server-pushed ``ToolsChanged`` / ``ResourcesChanged``.
3. The session/managed-config layer — ``Ready``, ``ConfigDiff`` (fanned out to
   ``ConfigAdded`` / ``ConfigRemoved``).

What is NOT ported (host-runtime, later rounds): the dispatcher that fans
``ConfigDiff`` out per-server, the 50 ms coalescing window, and the ACP
``x.ai/mcp/server_status`` push serialization. This module is the pure data
shape + the ``server_name`` accessor.

Mapping
-------

grok's ``McpClientEvent`` is an enum with **struct-variant payloads** and
``#[derive(Debug, Clone)]`` (no ``PartialEq``/``Eq``/``Hash``). Per the
R32-established policy, a payload-carrying Rust enum maps to a **frozen
dataclass union** (PEP 604 ``A | B | C``), dispatched via ``isinstance`` /
``match`` — mirroring Rust's ``match`` on enum variants. ``frozen=True,
slots=True`` mirrors the ``Debug + Clone`` value-ish semantics (immutable
instances, compact layout); ``ConfigDiff``'s ``Vec`` fields become
``list[str]`` (the payload is consumed by iteration, not hashed, so the
unhashable list is faithful to grok's no-``Hash`` derive).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeAlias

__all__ = [
    "McpServerName",
    "TransportClosed",
    "HandshakeFailed",
    "ToolsChanged",
    "ResourcesChanged",
    "Ready",
    "ConfigDiff",
    "ConfigAdded",
    "ConfigRemoved",
    "McpClientEvent",
    "server_name",
]

#: Per-server identifier (grok ``pub type McpServerName = String``).
McpServerName: TypeAlias = str


@dataclass(frozen=True, slots=True)
class TransportClosed:
    """The rmcp service loop terminated; the client is unusable for tool calls.

    ``client_id`` identifies *which* client closed — a mismatch with the client
    currently registered under ``server`` marks the event stale (a replacement
    was already installed); the dispatcher must NOT tear down the replacement.
    """

    server: McpServerName
    client_id: int


@dataclass(frozen=True, slots=True)
class HandshakeFailed:
    """``ensure_initialized`` returned ``Err``; ``reason`` is surfaced verbatim."""

    server: McpServerName
    reason: str


@dataclass(frozen=True, slots=True)
class ToolsChanged:
    """Server pushed ``notifications/tools/list_changed``."""

    server: McpServerName


@dataclass(frozen=True, slots=True)
class ResourcesChanged:
    """Server pushed ``notifications/resources/list_changed``."""

    server: McpServerName


@dataclass(frozen=True, slots=True)
class Ready:
    """Client transitioned to ``Ready``; dispatcher surfaces a "ready" status."""

    server: McpServerName


@dataclass(frozen=True, slots=True)
class ConfigDiff:
    """Managed/local config diff resolved. The dispatcher fans this out into
    one :class:`ConfigAdded` / :class:`ConfigRemoved` per affected server."""

    added: list[McpServerName]
    removed: list[McpServerName]


@dataclass(frozen=True, slots=True)
class ConfigAdded:
    """Per-server ``(server, ConfigAdded)`` fan-out of :class:`ConfigDiff`."""

    server: McpServerName


@dataclass(frozen=True, slots=True)
class ConfigRemoved:
    """Per-server ``(server, ConfigRemoved)`` fan-out of :class:`ConfigDiff`."""

    server: McpServerName


#: Tagged union of all MCP client events (grok ``McpClientEvent``).
McpClientEvent = (
    TransportClosed
    | HandshakeFailed
    | ToolsChanged
    | ResourcesChanged
    | Ready
    | ConfigDiff
    | ConfigAdded
    | ConfigRemoved
)


def server_name(event: McpClientEvent) -> McpServerName | None:
    """Server carried by the event, or ``None`` for :class:`ConfigDiff`.

    Mirrors grok's ``McpClientEvent::server_name``. ``ConfigDiff`` returns
    ``None`` because it carries a *set* of servers; the dispatcher fans it out
    into per-server :class:`ConfigAdded` / :class:`ConfigRemoved` children
    (each with a single server) before buffering.
    """
    if isinstance(event, ConfigDiff):
        return None
    # Every non-ConfigDiff variant carries a `server` field by construction.
    return event.server
