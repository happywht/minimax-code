"""JSON-RPC method catalog (R85).

Fusion of grok-build's ``xai-tool-protocol::methods`` — the closed
enumeration of every JSON-RPC method on the wire, defined once from a
single source of truth (Rust's ``define_methods!`` macro) together with
its wire string.

Rust generates the enum, the serde renames, :meth:`Method.as_wire_str`,
and :meth:`Method.from_wire_str` from one macro invocation. Python lands
the same single-source discipline as a :class:`enum.StrEnum` whose member
values *are* the wire strings (so JSON serialisation is the value,
matching ``#[serde(rename = $wire)]``), plus the matching
:meth:`Method.as_wire_str` / :meth:`Method.from_wire_str` accessors and
the :data:`Method.ALL` tuple for exhaustive iteration.

Direction grouping (harness → service, tool_server → service, etc.) is
preserved in source order exactly as Grok declares it; the enum is flat
— direction enforcement is the computer hub's job, not the protocol
crate's.

:data:`UNKNOWN_METHOD_MSG_PREFIX` is pinned here: it is the shape an OLD
hub produces when rejecting a request whose ``method`` string does not
parse into :class:`Method`. Current clients answer hub skew from the
``hello_ack`` ``capabilities`` advertisement instead of sniffing this
message, but the shape stays pinned so terminal binaries built while the
SDK still keyed old-hub detection on this exact prefix remain in the
fleet. Do not change casually.

This module is the direct consumer of :mod:`envelope`'s ``method`` field
(a bare ``str`` on the wire): callers build the envelope's ``method`` via
``Method.ToolCall.as_wire_str()`` and recover the enum from the wire via
:meth:`Method.from_wire_str`.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = [
    "Method",
    "UNKNOWN_METHOD_MSG_PREFIX",
    "method_doc",
]

#: Prefix the hub uses when rejecting a request whose ``method`` string
#: does not parse into :class:`Method` — the shape an OLD hub produces for
#: verbs it predates. Pinned for fleet-compat (do not change casually):
#: terminal binaries built while the SDK keyed old-hub detection on this
#: exact prefix remain in the field.
UNKNOWN_METHOD_MSG_PREFIX: str = "unknown method `"


class Method(StrEnum):
    """Every JSON-RPC method understood by the computer hub.

    The variants are grouped by direction in source order; the enum is
    flat — direction enforcement is the computer hub's job, not the
    protocol crate's. Member values are the wire strings
    (``#[serde(rename = $wire)]``); :meth:`__str__` (inherited from
    :class:`StrEnum`) returns the value, mirroring Rust's ``Display``
    impl that delegates to ``as_wire_str``.
    """

    # harness → service
    SessionOpen = "session_open"
    SessionClose = "session_close"
    SessionBindServer = "session_bind_server"
    SessionUnbindServer = "session_unbind_server"
    SessionAttachServer = "session_attach_server"
    ToolsList = "tools.list"
    ToolsSearch = "tools.search"
    ToolCall = "tool.call"
    ToolCancel = "tool.cancel"
    ToolNotify = "tool.notify"
    SystemNotify = "system.notify"
    SubscribeNotifications = "subscribe_notifications"
    UnsubscribeNotifications = "unsubscribe_notifications"
    Hook = "hook"
    Hello = "hello"
    HelloAck = "hello_ack"
    Ping = "ping"
    Pong = "pong"

    # tool_server → service
    ToolCallProgress = "tool_call_progress"
    ToolNotification = "tool.notification"
    HookReply = "hook_reply"
    TracesDonate = "traces.donate"
    LogsDonate = "logs.donate"
    MetricsDonate = "metrics.donate"

    # service → tool_server
    ToolCallRequest = "tool_call_request"

    # service → harness
    ToolsChanged = "tools_changed"
    SubscribeAck = "subscribe_ack"
    UnsubscribeAck = "unsubscribe_ack"

    # harness → service (server discovery)
    ServersList = "servers.list"

    # tool_server status lifecycle
    ToolServerStatus = "tool_server.status"
    ToolServerGetStatus = "tool_server.get_status"
    ToolServerEvict = "tool_server.evict"

    # session lifecycle
    Serve = "serve"
    SessionBind = "session.bind"
    SessionUnbind = "session.unbind"

    def as_wire_str(self) -> str:
        """Wire string for this method (``Method::as_wire_str``, const fn).

        Equivalent to the serde serialisation (the member value) without a
        round-trip through :mod:`json`.
        """
        return self.value

    @classmethod
    def from_wire_str(cls, s: str) -> Method | None:
        """Inverse of :meth:`as_wire_str` (``Method::from_wire_str``).

        Returns ``None`` for strings that do not match any known method —
        mirrors Rust returning ``None`` from the fallible match rather
        than panicking.
        """
        member = cls._value2member_map_.get(s)
        if member is not None:
            return member  # type: ignore[return-value]
        return None


#: Every :class:`Method` variant in declaration order, for exhaustive
#: iteration in tests (``Method::ALL`` — a ``pub const`` slice in Rust).
#: Assigned after the class body because the enum cannot reference itself
#: from inside its own body.
Method.ALL = tuple(Method)  # type: ignore[attr-defined]


#: Docstrings Grok carries on selected variants (``#[doc]`` attributes),
#: keyed by variant name — preserved for fidelity so callers can surface
#: the same semantics (e.g. ``ToolCancel`` being sugar for ``Hook`` +
#: ``HookEvent::Cancel``). Ten variants carry docs; the rest have ``None``.
_METHOD_DOCS: dict[str, str] = {
    "SessionAttachServer": (
        "Attach this harness connection to an EXISTING session as an "
        "observer. Answered hub-locally from the session→tool-server "
        "routing established by the owner's session_bind_server (or the "
        "server's re-serve); never forwarded to the tool server."
    ),
    "ToolCancel": (
        "Sugar for Method.Hook with HookEvent.Cancel. SDKs translate "
        "this method to a hook frame before sending; there is no "
        "separate tool.cancel wire frame and no ToolCancelParams struct "
        "in frames."
    ),
    "HookReply": (
        "Reply to a request/response hook, correlated back to the "
        "harness by hook_id."
    ),
    "TracesDonate": (
        "Notification (no id, no response); rejects surface only in hub "
        "metrics. Only hub-minted trace-ids are accepted."
    ),
    "LogsDonate": (
        "Notification (no id, no response); rejects surface only in hub "
        "metrics. Donor service.name must be hub-allowlisted."
    ),
    "MetricsDonate": (
        "Notification (no id, no response); rejects surface only in hub "
        "metrics. Donor service.name must be hub-allowlisted. No "
        "envelope session_id — metrics are process-aggregate."
    ),
    "ServersList": "List available tool servers for the authenticated user.",
    "Serve": (
        "Full tool snapshot for a session (server → hub). Idempotent: "
        "re-sending replaces the tool set; the hub diffs and emits "
        "tools_changed."
    ),
    "SessionBind": (
        "Hub requests the server to start serving a session (hub → "
        "server). The server responds with its tool snapshot."
    ),
    "SessionUnbind": (
        "Hub tells the server to stop serving a session (hub → server). "
        "Notification — no response expected."
    ),
}


def method_doc(method: Method) -> str | None:
    """Return the Grok ``#[doc]`` for *method*'s variant, or ``None``.

    Mirrors accessing a Rust variant's docstring; ten variants carry
    semantics worth surfacing, the rest return ``None``.
    """
    return _METHOD_DOCS.get(method.name)
