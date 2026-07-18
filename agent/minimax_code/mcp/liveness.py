"""Liveness watcher decision layer (R35).

Ports the pure-logic core of grok's ``xai-grok-mcp`` liveness module — the
state machine that classifies a per-tick observation of an MCP client into one
of three decisions (keep polling / emit ``TransportClosed`` / withdraw
silently). This is the **decision layer**; the tokio task, the ``Arc``/Mutex
slot, and the ``DropGuard`` cancellation in grok's
``spawn_transport_liveness`` are host-runtime integration and remain a future
wiring round.

What lives here (host-agnostic, pure):

* :data:`DEFAULT_POLL_INTERVAL_MS` — the 500 ms tick grok picked to keep mean
  transport-close detection latency under one second (one state read per tick).
* :class:`ClientStateKind` — the ``Copy`` projection of grok's ``ClientState``
  (Empty / Pending / Initializing / Ready), payload-stripped for cheap state
  inspection without borrowing the state mutex's inner data.
* :class:`LivenessCheck` — the three-way classification a watcher tick
  produces (Healthy / TransportClosed / Transient).
* :func:`classify_liveness` — the pure state machine: given a state kind and a
  transport-closed flag, return the decision. Mirrors grok's
  ``McpClient::liveness_check`` minus the mutex acquisition.
* :class:`McpClientEventKind` — the ``Hash`` discriminant grok's session-side
  dispatcher uses as the coalescing key ``(server, kind)``; liveness emits the
  ``TransportClosed`` kind.

Mapping note
------------

grok's **unit-only** enums (``#[derive(Debug, Clone, Copy, PartialEq, Eq)]``,
plus ``Hash`` for the event kind) map to :class:`enum.Enum` here — NOT to the
frozen-dataclass union used for **payload-carrying** variants in R32
(``VoiceEvent``). Unit variants have nothing to carry, and ``Enum`` gives
singleton value semantics + hashability for free, matching grok's
``Copy``/``Hash`` derives exactly. The ``@unique`` decorator reasserts the
variant-uniqueness Rust's compiler guarantees.

Product fusion
--------------

MiniMax Code's existing MCP layer (R3-R5 protocol/client/server/registry +
R34 wire/oauth_config) has no transport-health story: a connected server that
silently drops is indistinguishable from a live one until a tool call fails.
This module is the pure predicate a future liveness watcher will call each
tick — the smallest host-agnostic slice of grok's "detect dead transports
without false positives" logic. Centralizing the decision table here means the
watcher (when wired) is a thin ``classify_liveness`` + sleep loop, not a
re-implementation of the state machine.
"""

from __future__ import annotations

from enum import Enum, unique

__all__ = [
    "DEFAULT_POLL_INTERVAL_MS",
    "ClientStateKind",
    "LivenessCheck",
    "McpClientEventKind",
    "classify_liveness",
]

#: Default liveness poll interval. grok's ``Duration::from_millis(500)`` —
#: picked to keep mean transport-close detection latency under one second
#: while a tick is cheap (a single state read). See module doc.
DEFAULT_POLL_INTERVAL_MS: int = 500


@unique
class ClientStateKind(Enum):
    """Payload-stripped projection of grok's ``ClientState``.

    A ``Copy`` view used for cheap state-machine inspection without borrowing
    the state mutex's inner data. Mirrors grok's
    ``#[derive(Debug, Clone, Copy, PartialEq, Eq)]`` (Enum is a singleton,
    comparable, and hashable by default).
    """

    EMPTY = "empty"
    PENDING = "pending"
    INITIALIZING = "initializing"
    READY = "ready"


@unique
class LivenessCheck(Enum):
    """Three-way per-tick classification (grok ``LivenessCheck``).

    * :attr:`HEALTHY` — ``Ready`` + transport open → keep polling.
    * :attr:`TRANSPORT_CLOSED` — ``Ready`` + transport closed → emit + exit.
    * :attr:`TRANSIENT` — anything else (re-handshake, externally-reset
      transport, post-failure empty slot) → the watcher exits silently; the
      new state is managed externally and the owner can re-arm if it returns
      to ``Ready``.
    """

    HEALTHY = "healthy"
    TRANSPORT_CLOSED = "transport_closed"
    TRANSIENT = "transient"


@unique
class McpClientEventKind(Enum):
    """Discriminant for an MCP client event (grok ``McpClientEventKind``).

    The second half of the dispatcher's coalescing key ``(server, kind)``:
    two events with the same key collapse into the latest inside a 50 ms
    tumbling window. Distinct from the payload-carrying event because the
    payload (e.g. ``reason`` on a handshake failure) must not participate in
    equality / hashing. grok ``#[derive(Debug, Clone, Copy, Hash, Eq,
    PartialEq)]`` → Enum (hashable singleton, usable as a dict key).
    """

    TRANSPORT_CLOSED = "transport_closed"
    HANDSHAKE_FAILED = "handshake_failed"
    TOOLS_CHANGED = "tools_changed"
    RESOURCES_CHANGED = "resources_changed"
    READY = "ready"
    CONFIG_ADDED = "config_added"
    CONFIG_REMOVED = "config_removed"


def classify_liveness(state: ClientStateKind, transport_closed: bool) -> LivenessCheck:
    """Pure state machine: project a tick's observation to a decision.

    Mirrors grok's ``McpClient::liveness_check`` minus the mutex acquisition:
    the caller has already read ``state`` and the ``Ready`` service's
    ``is_transport_closed()`` flag, so this function is pure and synchronous.

    Only ``Ready`` distinguishes "actually closed" (emit) from "still open"
    (continue). Every non-``Ready`` state collapses to :attr:`TRANSIENT`
    (silent withdrawal) — this is exactly what prevents a false
    ``TransportClosed`` firing whenever a re-handshake moves the state to
    ``Initializing`` (the bug grok's liveness module was written to fix).
    """
    if state is ClientStateKind.READY:
        if transport_closed:
            return LivenessCheck.TRANSPORT_CLOSED
        return LivenessCheck.HEALTHY
    return LivenessCheck.TRANSIENT
