"""Tool-server frame protocol — per-method params/result payloads (R92 + R93 + R94 + R95 + R96 + R97 + R98 + R99 + R100).

Fusion of grok-build's ``xai-tool-protocol::frames`` — the per-method
``params`` and ``result`` payload structs that ride inside a
:class:`~minimax_code.tool_protocol.envelope.JsonRpcRequest` /
:class:`~minimax_code.tool_protocol.envelope.JsonRpcResponse` /
:class:`~minimax_code.tool_protocol.envelope.JsonRpcNotification`.

R92 lands the **opening slice** of the crate's largest module (``frames.rs``
is 1549 lines, 86 top-level pub symbols, 14 functional domains): the
tool-call params/result/progress family plus the telemetry-donation family
(tool call + trace/log/metric donation, lines 21-125 of the source). The
remaining 11 domains — tool notification / system notify, registration,
per-tool session binding, server discovery + binding, list & search, session
lifecycle, simplified lifecycle, subscriptions, hooks, service→harness
pushes, tool-server status lifecycle — are deferred to R94+. R93 lands the
heartbeat (:class:`PingFrame` / :class:`PongFrame`) — the crate's first
**non-derive custom Serialize/Deserialize**. R94 lands the **registration
frames** (:class:`RegisterToolParams` / :class:`RegisterServerParams` /
:class:`UnregisterToolParams` / :class:`UnregisterServerParams`) — a
consolidation round exercising the "params-as-DTO-wrapper" pattern: params
structs thin-wrap existing R82/R87 wire DTOs and delegate ``to_wire`` /
``from_wire`` to the embedded DTO. No crate-first serde shape lands here.
R95 lands the **per-tool session binding** family
(:class:`BindToolSessionParams` / :class:`UnbindToolSessionParams` /
:class:`BindToolSessionAck` / :class:`UnbindToolSessionAck` plus the two
strict snake_case outcome enums :class:`ToolSessionBindOutcome` /
:class:`ToolSessionUnbindOutcome`) — the first ack-wraps-strict-enum shape
in the crate: a params struct carries two bare id newtypes
(:class:`~minimax_code.tool_protocol.ids.ToolId` /
:class:`~minimax_code.tool_protocol.ids.SessionId`) and a result struct
wraps a single outcome enum whose ``from_wire`` rejects unknown values
(mirroring R86 :class:`~minimax_code.tool_protocol.capabilities.HookKind`
strict StrEnum — the strict counterpart to R90's tolerant
:class:`~minimax_code.tool_protocol.session_event.ToolCallOutcome`). No
crate-first serde shape lands here. R96 lands the **list & search** family
(:class:`ToolsListParams` / :class:`ToolsListResult` /
:class:`ToolsSearchParams` / :class:`ToolSearchResult` /
:class:`ToolsSearchResultBody`) — the crate's first
**list-of-bare-pydantic-model**: :attr:`ToolsListResult.tools` is a
``Vec<ToolDescription>`` whose elements are the codegen crate's pydantic
:class:`~minimax_code.tool_types.ToolDescription` (R65), lifted via
``model_validate`` / ``model_dump(exclude_none=True)`` rather than a
per-element ``from_wire`` classmethod (R87's ``Vec<ToolDescriptionWithSchema>``
had hand-controlled wrappers; here the element has none). The family also
carries the R92 opaque-``serde_json::Value`` passthrough on
:attr:`ToolSearchResult.input_schema` and is the first params family to
carry ``session_id`` as payload (overriding the R92 family-scoped "no
session_id on params" rule). R97 lands the **subscriptions** family
(:class:`SubscribeNotificationsParams` / :class:`NotificationFilter` /
:class:`SubscribeOutcome` / :class:`SubscribeAck` /
:class:`UnsubscribeNotificationsParams` / :class:`UnsubscribeOutcome` /
:class:`UnsubscribeAck`) — two strict snake_case outcome enums (mirroring
R95's bind/unbind outcomes), the crate's first
**all-``Optional`` + ``#[serde(default)]``-on-every-field** filter DTO
(:class:`NotificationFilter`, a Python dataclass with all-``None`` defaults
where ``#[serde(default)]`` ⇒ missing keys become ``None`` rather than
raising), and two acks wrapping outcome + ``subscription_id`` (R95
ack-wraps-strict-enum shape, this round with an extra ``String`` handle).
R98 lands the **tool-server status lifecycle** family
(:class:`ToolServerLifecycleStatus` / :class:`ToolServerDisconnectReason` /
:class:`ToolServerStatusPayload` / :class:`ToolServerEvictParams` /
:class:`ToolServerGetStatusParams` / :class:`ToolServerConnectionStatus` /
:class:`ToolServerGetStatusResult`) — two strict snake_case enums (the first,
:class:`ToolServerLifecycleStatus`, also carries a ``#[default]`` member
``Ready`` mirrored via the :meth:`default` classmethod), and the crate's most
serde-dense single struct :class:`ToolServerStatusPayload` (20 fields
exercising four field modes in one dataclass: required / Option-skip /
Vec-skip / default-no-skip). This domain lands the cross-domain
:class:`ToolServerLifecycleStatus` referenced by the still-deferred
"server discovery + binding" domain, unblocking it for a future round.
R99 lands the **server discovery + binding** family
(:class:`ServersListParams` / :class:`ServerInfo` /
:class:`ServersListResult` / :class:`ServerBindParams` /
:class:`ServerBindOutcome` / :class:`ServerBindAck` /
:class:`ServerUnbindParams` / :class:`ServerUnbindOutcome` /
:class:`ServerUnbindAck`) — the hub<->client tool-server discovery & routing
channel, and the **R98 -> R99 consumer edge**: :class:`ServerInfo.status`
references the :class:`ToolServerLifecycleStatus` enum R98 landed precisely
to unblock this domain. Two more strict snake_case outcome enums
(:class:`ServerBindOutcome` / :class:`ServerUnbindOutcome`, no
``#[default]``), an empty-struct params (:class:`ServersListParams`), a
six-field mixed-mode DTO (:class:`ServerInfo`: required / Option-skip /
default-no-skip, with an opaque ``serde_json::Value`` ``metadata``), and the
crate's second list-of-DTO result (:class:`ServersListResult`) round out the
domain.
The remaining 5 domains are deferred to R100+.

``session_id`` belongs in the JSON-RPC envelope field — always. These
params structs do NOT carry a ``session_id``; the hub reads it from
``request.session_id`` on the envelope. (Types that are NOT request params —
e.g. ``ToolsChanged`` notification body, ``ServerInfo`` display struct — keep
their own ``session_id`` because it is payload data, not routing; those land
in later slices.)

Serde shapes landing here (consolidation round — no crate-first shape)
----------------------------------------------------------------------

This round exercises four serde sub-shapes that earlier rounds introduced, in
the new context of flat params/result structs:

1. **``#[serde(default, skip_serializing_if = "Option::is_none")]``** — four
   ``Option`` arms on :class:`ToolCallParams` (``deadline_ms`` /
   ``behavior_version`` / ``cwd`` / ``trace_context``), one on
   :class:`ToolCallProgressFrame.dropped_count`, one on
   :class:`ToolCallResult.chat_completion_output`. Mirrors R86 / R87 / R90.
2. **``#[serde(default, skip_serializing_if = "Vec::is_empty")]``** — the
   ``Vec`` empty-skip variant on :class:`ToolCallResult.follow_ups` /
   ``reminders``. R86 introduced ``Vec`` / ``HashMap`` empty-skip on
   ``capabilities``; R92 is its first landing in a params/result struct.
3. **Opaque ``serde_json::Value`` fields** — :attr:`ToolCallParams.arguments`,
   :attr:`ToolCallProgressFrame.body`, :attr:`ToolCallResult.follow_ups` /
   ``reminders`` (``Vec<Value>``) / :attr:`ToolCallResult.chat_completion_output`
   (``Option<Value>``). The crate deliberately carries these as opaque
   ``Value`` (not typed frames) so it need not depend on
   ``xai-tool-runtime``; sampler-side decoders reconstruct the typed frames.
   Python maps to ``object`` / ``list[object]`` / ``object | None`` and
   round-trips the JSON verbatim — no conversion, no validation.
4. **Params struct embedding a wire enum** — :attr:`ToolCallResult.output`
   is a :class:`~minimax_code.tool_protocol.output_wire.ToolOutputWire`
   (R83's adjacent-tagged enum). Mirrors R90's enum-in-enum
   (:attr:`~minimax_code.tool_protocol.session_event.TurnEnded.outcome`).

First numeric ``usize`` constants
---------------------------------

R92 also lands the crate's first **numeric** module-level constants:

* :data:`MAX_SPANS_PER_DONATION` / :data:`MAX_LOG_RECORDS_PER_DONATION` /
  :data:`MAX_METRICS_PER_DONATION` — literal ``usize = 512``.
* :data:`MAX_DONATION_BYTES` — expression constant ``1024 * 1024`` (kept as
  the expression, not folded to ``1048576``, for documentary clarity; the
  hub rejects oversized donation batches wholesale so donors chunk before
  encoding).

Prior module constants were string-valued
(:data:`~minimax_code.tool_protocol.handshake.PROTOCOL_VERSION`,
:data:`~minimax_code.tool_protocol.error_codes.WORKSPACE_UNAVAILABLE_*`,
:data:`~minimax_code.tool_protocol.methods.UNKNOWN_METHOD_MSG_PREFIX`) or
the :data:`~minimax_code.tool_protocol.error_codes.ERROR_CODES` mapping
table; R92 is the first ``int`` constant family.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from minimax_code.tool_protocol.connection import ToolDefinitionMode
from minimax_code.tool_protocol.ids import (
    ConnectionId,
    FrameSeq,
    ServerId,
    SessionId,
    ToolCallId,
    ToolId,
)
from minimax_code.tool_protocol.methods import Method
from minimax_code.tool_protocol.notification_wire import (
    Custom,
    Known,
    WireCustomNotification,
    WireToolNotification,
)
from minimax_code.tool_protocol.notification_wire import from_wire as notification_from_wire
from minimax_code.tool_protocol.output_wire import ToolOutputWire
from minimax_code.tool_protocol.output_wire import from_wire as tool_output_wire_from_wire
from minimax_code.tool_protocol.registration import ToolRegistration, ToolServerRegistration
from minimax_code.tool_types import ToolDescription

__all__ = [
    # consts (first numeric usize family)
    "MAX_SPANS_PER_DONATION",
    "MAX_DONATION_BYTES",
    "MAX_LOG_RECORDS_PER_DONATION",
    "MAX_METRICS_PER_DONATION",
    "MAX_SYSTEM_NOTIFY_PAYLOAD_BYTES",
    # structs
    "ToolCallParams",
    "ToolCallResult",
    "ToolCallProgressFrame",
    "TracesDonateParams",
    "LogsDonateParams",
    "MetricsDonateParams",
    # heartbeat (R93 — crate's first non-derive custom Serialize/Deserialize)
    "PingFrame",
    "PongFrame",
    # registration (R94 — params-as-DTO-wrapper consolidation)
    "RegisterToolParams",
    "RegisterServerParams",
    "UnregisterToolParams",
    "UnregisterServerParams",
    # per-tool session binding (R95 — ack-wraps-strict-enum)
    "BindToolSessionAck",
    "BindToolSessionParams",
    "ToolSessionBindOutcome",
    "ToolSessionUnbindOutcome",
    "UnbindToolSessionAck",
    "UnbindToolSessionParams",
    # list & search (R96 — list-of-bare-pydantic-model + opaque Value passthrough)
    "ToolSearchResult",
    "ToolsListParams",
    "ToolsListResult",
    "ToolsSearchParams",
    "ToolsSearchResultBody",
    # subscriptions (R97 — all-Optional filter DTO + strict outcome enums)
    "NotificationFilter",
    "SubscribeAck",
    "SubscribeNotificationsParams",
    "SubscribeOutcome",
    "UnsubscribeAck",
    "UnsubscribeNotificationsParams",
    "UnsubscribeOutcome",
    # tool-server status lifecycle (R98 — strict lifecycle/disconnect enums
    # + 20-field four-mode status payload, unblocks server discovery)
    "ToolServerConnectionStatus",
    "ToolServerDisconnectReason",
    "ToolServerEvictParams",
    "ToolServerGetStatusParams",
    "ToolServerGetStatusResult",
    "ToolServerLifecycleStatus",
    "ToolServerStatusPayload",
    # server discovery + binding (R99 — consumes R98 ToolServerLifecycleStatus
    # via ServerInfo.status; two strict outcome enums + empty-struct params
    # + six-field mixed-mode DTO + list-of-DTO result)
    "ServerBindAck",
    "ServerBindOutcome",
    "ServerBindParams",
    "ServerInfo",
    "ServersListParams",
    "ServersListResult",
    "ServerUnbindAck",
    "ServerUnbindOutcome",
    "ServerUnbindParams",
    # tool/system notifications (R100 — consumes R83 WireToolNotification via
    # ToolNotificationFrame.notification; default-no-skip bool + opaque Value
    # payload + custom/known factory methods)
    "SystemNotifyParams",
    "ToolNotificationFrame",
    # session lifecycle open/close primitives (R101 — consumes R82
    # ConnectionId/FrameSeq via LastSeq; default-no-skip resume bool +
    # Option-skip last_seq/reason + empty-struct open result)
    "LastSeq",
    "SessionCloseParams",
    "SessionOpenParams",
    "SessionOpenResult",
    # session bind/attach server (R102 — frames.py's first #[serde(other)]
    # tolerant StrEnum AttachRoute; consumes R82 ServerId + R65 ToolDescription
    # via Vec-is_empty-skip bare-pydantic tool lists; all-Default DTOs)
    "AttachRoute",
    "SessionAttachServerParams",
    "SessionAttachServerResult",
    "SessionBindServerParams",
    "SessionBindServerResult",
    "SessionUnbindServerParams",
    # wire converters
    "tool_call_params_from_wire",
    "tool_call_result_from_wire",
    "tool_call_progress_frame_from_wire",
    "traces_donate_params_from_wire",
    "logs_donate_params_from_wire",
    "metrics_donate_params_from_wire",
    "ping_frame_from_wire",
    "pong_frame_from_wire",
    "register_tool_params_from_wire",
    "register_server_params_from_wire",
    "unregister_tool_params_from_wire",
    "unregister_server_params_from_wire",
    "bind_tool_session_params_from_wire",
    "unbind_tool_session_params_from_wire",
    "bind_tool_session_ack_from_wire",
    "unbind_tool_session_ack_from_wire",
    "tools_list_params_from_wire",
    "tools_list_result_from_wire",
    "tools_search_params_from_wire",
    "tool_search_result_from_wire",
    "tools_search_result_body_from_wire",
    # subscriptions (R97)
    "notification_filter_from_wire",
    "subscribe_ack_from_wire",
    "subscribe_notifications_params_from_wire",
    "unsubscribe_ack_from_wire",
    "unsubscribe_notifications_params_from_wire",
    # tool-server status lifecycle (R98)
    "tool_server_connection_status_from_wire",
    "tool_server_evict_params_from_wire",
    "tool_server_get_status_params_from_wire",
    "tool_server_get_status_result_from_wire",
    "tool_server_status_payload_from_wire",
    # server discovery + binding (R99)
    "server_bind_ack_from_wire",
    "server_bind_params_from_wire",
    "server_info_from_wire",
    "server_unbind_ack_from_wire",
    "server_unbind_params_from_wire",
    "servers_list_params_from_wire",
    "servers_list_result_from_wire",
    # tool/system notifications (R100)
    "system_notify_params_from_wire",
    "tool_notification_frame_from_wire",
    # session lifecycle open/close primitives (R101)
    "last_seq_from_wire",
    "session_close_params_from_wire",
    "session_open_params_from_wire",
    "session_open_result_from_wire",
    # session bind/attach server (R102). AttachRoute.from_wire is a tolerant
    # classmethod on the enum (#[serde(other)] -> UNKNOWN), NOT a module-level
    # converter — mirrors the 8 prior frames.py StrEnums (R95 ... R101).
    "session_attach_server_params_from_wire",
    "session_attach_server_result_from_wire",
    "session_bind_server_params_from_wire",
    "session_bind_server_result_from_wire",
    "session_unbind_server_params_from_wire",
]


# ── Tool call params / result / progress ─────────────────────────────────


@dataclass
class ToolCallParams:
    """``tool.call`` (harness → service) / ``tool_call_request``
    (service → tool_server) params.

    Both directions share the same shape; ``tool_call_id`` is preserved
    end-to-end. :attr:`arguments` is an opaque JSON value — the tool's
    input-schema payload, round-tripped verbatim.
    """

    tool_call_id: ToolCallId
    tool_id: ToolId
    #: Opaque JSON value (the tool's input arguments). Round-tripped verbatim.
    arguments: object
    #: Deadline in milliseconds. ``#[serde(default, skip_serializing_if =
    #: "Option::is_none")]`` — wire-omitted when ``None``.
    deadline_ms: int | None = None
    #: Behavior-version negotiation string.
    behavior_version: str | None = None
    #: OS-native path; informational for cross-FS tools.
    cwd: str | None = None
    #: W3C ``traceparent`` for distributed tracing.
    trace_context: str | None = None

    def to_wire(self) -> dict[str, object]:
        out: dict[str, object] = {
            "tool_call_id": self.tool_call_id,
            "tool_id": self.tool_id,
            "arguments": self.arguments,
        }
        if self.deadline_ms is not None:
            out["deadline_ms"] = self.deadline_ms
        if self.behavior_version is not None:
            out["behavior_version"] = self.behavior_version
        if self.cwd is not None:
            out["cwd"] = self.cwd
        if self.trace_context is not None:
            out["trace_context"] = self.trace_context
        return out


@dataclass
class ToolCallResult:
    """Body of a successful ``tool_call_result`` response.

    :attr:`follow_ups` and :attr:`reminders` only fire for **local** tools
    and are empty (and field-skipped) for remote calls.
    :attr:`chat_completion_output` is carried as opaque ``Value`` (not the
    runtime's typed frame) so this crate need not depend on
    ``xai-tool-runtime``; sampler-side wire decoders reconstruct it.
    """

    tool_call_id: ToolCallId
    output: ToolOutputWire
    #: ``#[serde(default, skip_serializing_if = "Vec::is_empty")]`` — defaults
    #: to empty, wire-omitted when empty.
    follow_ups: list[object] = field(default_factory=list)
    #: ``#[serde(default, skip_serializing_if = "Vec::is_empty")]``.
    reminders: list[object] = field(default_factory=list)
    #: ``#[serde(default, skip_serializing_if = "Option::is_none")]`` — opaque.
    chat_completion_output: object | None = None

    def to_wire(self) -> dict[str, object]:
        out: dict[str, object] = {
            "tool_call_id": self.tool_call_id,
            "output": self.output.to_wire(),
        }
        if self.follow_ups:  # Vec::is_empty
            out["follow_ups"] = self.follow_ups
        if self.reminders:  # Vec::is_empty
            out["reminders"] = self.reminders
        if self.chat_completion_output is not None:  # Option::is_none
            out["chat_completion_output"] = self.chat_completion_output
        return out


@dataclass
class ToolCallProgressFrame:
    """Body of a ``tool_call_progress`` notification.

    :attr:`kind` is a producer-defined string (e.g. ``"log_chunk"`` /
    ``"chunk"``); :attr:`body` is an opaque JSON value.
    :attr:`dropped_count` is a drop-bookkeeping counter — non-zero when prior
    progress frames for this ``tool_call_id`` were dropped under rate
    pressure.
    """

    tool_call_id: ToolCallId
    kind: str
    #: Opaque JSON value (the chunk payload). Round-tripped verbatim.
    body: object
    #: ``#[serde(default, skip_serializing_if = "Option::is_none")]``.
    dropped_count: int | None = None

    def to_wire(self) -> dict[str, object]:
        out: dict[str, object] = {
            "tool_call_id": self.tool_call_id,
            "kind": self.kind,
            "body": self.body,
        }
        if self.dropped_count is not None:
            out["dropped_count"] = self.dropped_count
        return out


# ── Trace donation ────────────────────────────────────────────────────────

#: Hub rejects oversized batches wholesale; donors chunk before encoding.
MAX_SPANS_PER_DONATION: int = 512

#: Maximum decoded ``ExportTraceServiceRequest`` size the hub accepts.
#: Kept as the ``1024 * 1024`` expression for documentary clarity.
MAX_DONATION_BYTES: int = 1024 * 1024


@dataclass
class TracesDonateParams:
    """``traces.donate`` params (tool_server → service notification).

    Envelope ``session_id`` required. ``hub.*`` span attributes are reserved
    — the hub strips them and stamps its own attribution.
    :attr:`otlp_request` is base64 (standard alphabet, padded) protobuf-
    encoded ``opentelemetry.proto.collector.trace.v1.ExportTraceServiceRequest``.
    """

    otlp_request: str

    def to_wire(self) -> dict[str, object]:
        return {"otlp_request": self.otlp_request}


# ── Log donation ──────────────────────────────────────────────────────────

#: Secondary record cap, symmetric with :data:`MAX_SPANS_PER_DONATION`; the
#: 1 MiB :data:`MAX_DONATION_BYTES` decoded-size cap is the real bound.
MAX_LOG_RECORDS_PER_DONATION: int = 512


@dataclass
class LogsDonateParams:
    """``logs.donate`` params (tool_server → service notification).

    Envelope ``session_id`` required. ``hub.*`` log attributes are reserved
    — the hub strips them and stamps its own attribution.
    :attr:`otlp_request` is base64 protobuf-encoded
    ``ExportLogsServiceRequest``.
    """

    otlp_request: str

    def to_wire(self) -> dict[str, object]:
        return {"otlp_request": self.otlp_request}


# ── Metric donation ──────────────────────────────────────────────────────

#: Secondary guard alongside the 1 MiB :data:`MAX_DONATION_BYTES` cap.
MAX_METRICS_PER_DONATION: int = 512


@dataclass
class MetricsDonateParams:
    """``metrics.donate`` params (tool_server → service notification).

    **No envelope ``session_id``** — metrics are process-aggregate, not
    per-session (unlike :class:`LogsDonateParams`). ``hub.*`` resource
    attributes are reserved — the hub strips them and stamps its own
    attribution. :attr:`otlp_request` is base64 protobuf-encoded
    ``ExportMetricsServiceRequest``.
    """

    otlp_request: str

    def to_wire(self) -> dict[str, object]:
        return {"otlp_request": self.otlp_request}


# ── Wire converters ──────────────────────────────────────────────────────


def _opt_int(data: dict[str, object], key: str) -> int | None:
    """Lift a ``#[serde(default)]`` ``Option<u{32,64}>`` field — ``None`` when
    the wire omits it."""
    value = data.get(key)
    return int(value) if value is not None else None


def _opt_str(data: dict[str, object], key: str) -> str | None:
    """Lift a ``#[serde(default)]`` ``Option<String>`` field — ``None`` when
    the wire omits it."""
    value = data.get(key)
    return str(value) if value is not None else None


def tool_call_params_from_wire(data: dict[str, object]) -> ToolCallParams:
    """Reconstruct :class:`ToolCallParams` from its wire form.

    :attr:`arguments` is passed through verbatim (opaque JSON). The four
    ``Option`` arms lift via :func:`_opt_int` / :func:`_opt_str`.
    """
    return ToolCallParams(
        tool_call_id=str(data["tool_call_id"]),
        tool_id=str(data["tool_id"]),
        arguments=data["arguments"],
        deadline_ms=_opt_int(data, "deadline_ms"),
        behavior_version=_opt_str(data, "behavior_version"),
        cwd=_opt_str(data, "cwd"),
        trace_context=_opt_str(data, "trace_context"),
    )


def tool_call_result_from_wire(data: dict[str, object]) -> ToolCallResult:
    """Reconstruct :class:`ToolCallResult` from its wire form.

    :attr:`output` is reconstructed via
    :func:`~minimax_code.tool_protocol.output_wire.from_wire` (R83's
    adjacent-tagged ``ToolOutputWire``). :attr:`follow_ups` / :attr:`reminders`
    default to empty when wire-omitted (``Vec::is_empty`` skip);
    :attr:`chat_completion_output` is ``None`` when omitted.
    """
    return ToolCallResult(
        tool_call_id=str(data["tool_call_id"]),
        output=tool_output_wire_from_wire(data["output"]),  # type: ignore[arg-type]
        follow_ups=list(data.get("follow_ups", [])),  # Vec::is_empty default
        reminders=list(data.get("reminders", [])),  # Vec::is_empty default
        chat_completion_output=data.get("chat_completion_output"),  # Option::is_none
    )


def tool_call_progress_frame_from_wire(data: dict[str, object]) -> ToolCallProgressFrame:
    """Reconstruct :class:`ToolCallProgressFrame` from its wire form.

    :attr:`body` is passed through verbatim (opaque JSON).
    :attr:`dropped_count` lifts via :func:`_opt_int`.
    """
    return ToolCallProgressFrame(
        tool_call_id=str(data["tool_call_id"]),
        kind=str(data["kind"]),
        body=data["body"],
        dropped_count=_opt_int(data, "dropped_count"),
    )


def traces_donate_params_from_wire(data: dict[str, object]) -> TracesDonateParams:
    """Reconstruct :class:`TracesDonateParams` from its wire form."""
    return TracesDonateParams(otlp_request=str(data["otlp_request"]))


def logs_donate_params_from_wire(data: dict[str, object]) -> LogsDonateParams:
    """Reconstruct :class:`LogsDonateParams` from its wire form."""
    return LogsDonateParams(otlp_request=str(data["otlp_request"]))


def metrics_donate_params_from_wire(data: dict[str, object]) -> MetricsDonateParams:
    """Reconstruct :class:`MetricsDonateParams` from its wire form."""
    return MetricsDonateParams(otlp_request=str(data["otlp_request"]))


# ── Heartbeat (R93 — crate's first non-derive custom Serialize) ───────────
#
# PingFrame / PongFrame carry a ``method`` discriminator on the wire so any
# receiver (hub or SDK) can route them through a method-based demux. The
# ``method`` value is baked into ``to_wire`` — callers just set ``ts_ms`` and
# the correct method string (``Method.Ping`` / ``Method.Pong``) appears in the
# JSON output. Deserialization is lenient: ``method`` is accepted but ignored,
# so frames produced by older builds (without ``method``) still parse.


@dataclass
class PingFrame:
    """Application-level heartbeat ping.

    Serialises as ``{"method":"ping","ts_ms":<u64>}``. The ``method``
    discriminator is **injected by the hand-written :meth:`to_wire`** (the
    crate's first non-derive ``impl Serialize``), not a struct field —
    mirroring Rust's ``PingFrame`` whose ``#[derive]`` deliberately omits
    ``Serialize`` / ``Deserialize``. The value comes from
    :attr:`Method.Ping <minimax_code.tool_protocol.methods.Method.Ping>` via
    :meth:`~minimax_code.tool_protocol.methods.Method.as_wire_str` (single
    source of truth, DRY — not a hardcoded literal).
    """

    ts_ms: int

    def to_wire(self) -> dict[str, object]:
        # Custom Serialize: always includes "method" on the wire (method first,
        # ts_ms second — mirrors Rust's serialize_map insertion order).
        return {"method": Method.Ping.as_wire_str(), "ts_ms": self.ts_ms}


@dataclass
class PongFrame:
    """Application-level heartbeat pong (``{"method":"pong","ts_ms":<u64>}``).

    The counterpart to :class:`PingFrame`; ``method`` injected by
    :meth:`to_wire` from
    :attr:`Method.Pong <minimax_code.tool_protocol.methods.Method.Pong>`.
    """

    ts_ms: int

    def to_wire(self) -> dict[str, object]:
        return {"method": Method.Pong.as_wire_str(), "ts_ms": self.ts_ms}


def ping_frame_from_wire(data: dict[str, object]) -> PingFrame:
    """Reconstruct :class:`PingFrame` from its wire form (custom Deserialize).

    Lenient on ``method``: accepted but ignored — so frames produced by older
    builds (without ``method``) still parse. But if ``method`` is **present
    and mismatches** (e.g. a ``"pong"`` frame handed to this constructor),
    raise :class:`ValueError` — mirrors Rust's ``serde::de::Error::custom``.
    ``ts_ms`` is required.
    """
    method = data.get("method")
    if method is not None and str(method) != Method.Ping.as_wire_str():
        raise ValueError(
            f'expected method "{Method.Ping.as_wire_str()}" but got "{method}"'
        )
    return PingFrame(ts_ms=int(data["ts_ms"]))


def pong_frame_from_wire(data: dict[str, object]) -> PongFrame:
    """Reconstruct :class:`PongFrame` from its wire form (custom Deserialize).

    Lenient on ``method`` (accepted but ignored); raises :class:`ValueError`
    on a present-but-mismatched ``method``. ``ts_ms`` is required.
    """
    method = data.get("method")
    if method is not None and str(method) != Method.Pong.as_wire_str():
        raise ValueError(
            f'expected method "{Method.Pong.as_wire_str()}" but got "{method}"'
        )
    return PongFrame(ts_ms=int(data["ts_ms"]))


# ── Registration frames (R94 — params-as-DTO-wrapper consolidation) ────────
#
# Four single-field params structs that thin-wrap existing R82/R87 wire DTOs:
# ``register_tool`` / ``register_server`` / ``unregister_tool`` /
# ``unregister_server``. This is a consolidation round — no crate-first serde
# shape; it exercises the "params struct delegates to_wire / from_wire to an
# embedded DTO" pattern that ``frames.rs`` uses pervasively for the remaining
# domains. Two structs embed a full DTO (:class:`ToolRegistration` /
# :class:`ToolServerRegistration` — delegate to the DTO's own ``to_wire`` and
# ``from_wire``); two embed a bare identifier newtype (:class:`ToolId` /
# :class:`ServerId` — serialise as a string directly, mirroring
# :class:`ToolCallParams.tool_id` in R92).


@dataclass
class RegisterToolParams:
    """``register_tool`` params — single-tool sugar over ``register_server``.

    Thin wrapper around
    :class:`~minimax_code.tool_protocol.registration.ToolRegistration`;
    :meth:`to_wire` delegates to :meth:`ToolRegistration.to_wire` (R87), and
    :func:`register_tool_params_from_wire` delegates back via
    :meth:`ToolRegistration.from_wire`.
    """

    tool: ToolRegistration

    def to_wire(self) -> dict[str, object]:
        return {"tool": self.tool.to_wire()}


@dataclass
class RegisterServerParams:
    """``register_server`` params — multi-tool batch.

    Thin wrapper around
    :class:`~minimax_code.tool_protocol.registration.ToolServerRegistration`.
    """

    server: ToolServerRegistration

    def to_wire(self) -> dict[str, object]:
        return {"server": self.server.to_wire()}


@dataclass
class UnregisterToolParams:
    """``unregister_tool`` params — drop a tool entirely from the connection.

    Connection-wide removal (across every session the tool was bound to).
    For per-session removal use ``unbind_tool_session`` (later slice).
    :attr:`tool_id` is a bare
    :class:`~minimax_code.tool_protocol.ids.ToolId` newtype (R82), serialised
    as a string directly — the same newtoype-as-str shape as
    :attr:`ToolCallParams.tool_id` in R92.
    """

    tool_id: ToolId

    def to_wire(self) -> dict[str, object]:
        return {"tool_id": self.tool_id}


@dataclass
class UnregisterServerParams:
    """``unregister_server`` params — drop every tool registered under the id.

    :attr:`server_id` is a bare
    :class:`~minimax_code.tool_protocol.ids.ServerId` newtype (R82).
    """

    server_id: ServerId

    def to_wire(self) -> dict[str, object]:
        return {"server_id": self.server_id}


def register_tool_params_from_wire(data: dict[str, object]) -> RegisterToolParams:
    """Reconstruct :class:`RegisterToolParams` from its wire form.

    Delegates the embedded DTO reconstruction to
    :meth:`ToolRegistration.from_wire` (R87 classmethod) — the params struct
    itself adds no fields beyond the ``tool`` wrapper key.
    """
    return RegisterToolParams(
        tool=ToolRegistration.from_wire(data["tool"])  # type: ignore[arg-type]
    )


def register_server_params_from_wire(data: dict[str, object]) -> RegisterServerParams:
    """Reconstruct :class:`RegisterServerParams`.

    Delegates to :meth:`ToolServerRegistration.from_wire` (R87 classmethod).
    """
    return RegisterServerParams(
        server=ToolServerRegistration.from_wire(data["server"])  # type: ignore[arg-type]
    )


def unregister_tool_params_from_wire(data: dict[str, object]) -> UnregisterToolParams:
    """Reconstruct :class:`UnregisterToolParams`.

    :attr:`tool_id` lifts as :class:`ToolId` (str newtype).
    """
    return UnregisterToolParams(tool_id=ToolId(str(data["tool_id"])))


def unregister_server_params_from_wire(data: dict[str, object]) -> UnregisterServerParams:
    """Reconstruct :class:`UnregisterServerParams`.

    :attr:`server_id` lifts as :class:`ServerId` (str newtype).
    """
    return UnregisterServerParams(server_id=ServerId(str(data["server_id"])))


# ── Per-tool session binding (R95 — ack-wraps-strict-enum) ─────────────────
#
# ``bind_tool_session`` / ``unbind_tool_session`` mutate a registered tool's
# per-tool session set (the reverse-index from a tool to the sessions it is
# bound to on a given connection). This is the first ack-wraps-strict-enum
# shape in the crate:
#
# * The **params** structs (:class:`BindToolSessionParams` /
#   :class:`UnbindToolSessionParams`) carry two bare id newtypes
#   (:class:`ToolId` + :class:`SessionId`) — both serialise as strings
#   directly (the same newtype-as-str shape as
#   :attr:`ToolCallParams.tool_id` in R92).
# * The **result** structs (:class:`BindToolSessionAck` /
#   :class:`UnbindToolSessionAck`) wrap a single outcome enum whose
#   ``from_wire`` rejects unknown values — the strict counterpart to R90's
#   ``#[serde(other)]``-tolerant :class:`ToolCallOutcome` /
#   :class:`SessionPhase`, mirroring R86 :class:`HookKind` / :class:`ToolScope`.
#   Unknown outcomes raise :class:`ValueError` rather than silently mapping
#   to a catch-all (the registry's ``Conflict`` variant is lifted to a
#   top-level ``ServerError::ToolBindingConflict`` wire error, NOT mirrored
#   here — see the source comment in ``frames.rs``).
#
# Neither field matches the envelope-level ``session_id``; both are subjects
# of the bind / unbind operation.


class ToolSessionBindOutcome(StrEnum):
    """Outcome reported by :class:`BindToolSessionAck`.

    ``#[serde(rename_all = "snake_case")]`` with no ``#[serde(other)]``:
    member values are the snake_case wire strings; an unknown wire string
    fails :meth:`from_wire`. The registry's ``Conflict`` variant is NOT
    mirrored here — the router lifts it to a top-level wire error instead.
    """

    Bound = "bound"
    AlreadyBound = "already_bound"
    UnknownTool = "unknown_tool"
    SessionNotBound = "session_not_bound"

    def to_wire(self) -> str:
        """The snake_case wire string (``#[serde(rename_all)]``)."""
        return self.value

    @classmethod
    def from_wire(cls, data: str) -> ToolSessionBindOutcome:
        """Reconstruct from a wire string; reject unknown values.

        Mirrors serde with no ``#[serde(other)]`` arm: an unknown string
        raises :class:`ValueError` rather than being silently swallowed.
        """
        member = cls._value2member_map_.get(data)
        if member is None:
            raise ValueError(f"unknown ToolSessionBindOutcome wire value: {data!r}")
        return member  # type: ignore[return-value]


class ToolSessionUnbindOutcome(StrEnum):
    """Outcome reported by :class:`UnbindToolSessionAck`.

    ``#[serde(rename_all = "snake_case")]`` with no ``#[serde(other)]``.
    """

    Unbound = "unbound"
    NotBound = "not_bound"
    UnknownTool = "unknown_tool"

    def to_wire(self) -> str:
        """The snake_case wire string."""
        return self.value

    @classmethod
    def from_wire(cls, data: str) -> ToolSessionUnbindOutcome:
        """Reconstruct from a wire string; reject unknown values."""
        member = cls._value2member_map_.get(data)
        if member is None:
            raise ValueError(f"unknown ToolSessionUnbindOutcome wire value: {data!r}")
        return member  # type: ignore[return-value]


@dataclass
class BindToolSessionParams:
    """``bind_tool_session`` params — add ``session_id`` to a tool's session set.

    Both fields are SUBJECTS of the operation: :attr:`tool_id` names the tool
    whose session set is being mutated, :attr:`session_id` names the session
    being added (which must already be in the connection's bound-session
    set). Neither matches the envelope-level ``session_id`` (the calling-frame
    routing scope, typically omitted on connection-control frames). Both are
    bare id newtypes serialised as strings directly.
    """

    tool_id: ToolId
    session_id: SessionId

    def to_wire(self) -> dict[str, object]:
        return {"tool_id": self.tool_id, "session_id": self.session_id}


@dataclass
class UnbindToolSessionParams:
    """``unbind_tool_session`` params — drop ``session_id`` from a tool's set.

    Same envelope-vs-payload distinction as :class:`BindToolSessionParams`.
    """

    tool_id: ToolId
    session_id: SessionId

    def to_wire(self) -> dict[str, object]:
        return {"tool_id": self.tool_id, "session_id": self.session_id}


@dataclass
class BindToolSessionAck:
    """Reply to :class:`BindToolSessionParams`.

    Wraps a single :class:`ToolSessionBindOutcome`; :meth:`to_wire` emits the
    outcome's snake_case wire string under the ``outcome`` key.
    """

    outcome: ToolSessionBindOutcome

    def to_wire(self) -> dict[str, object]:
        return {"outcome": str(self.outcome)}


@dataclass
class UnbindToolSessionAck:
    """Reply to :class:`UnbindToolSessionParams`.

    Wraps a single :class:`ToolSessionUnbindOutcome`.
    """

    outcome: ToolSessionUnbindOutcome

    def to_wire(self) -> dict[str, object]:
        return {"outcome": str(self.outcome)}


def bind_tool_session_params_from_wire(data: dict[str, object]) -> BindToolSessionParams:
    """Reconstruct :class:`BindToolSessionParams`.

    Both id fields lift as bare newtypes (:class:`ToolId` / :class:`SessionId`).
    """
    return BindToolSessionParams(
        tool_id=ToolId(str(data["tool_id"])),
        session_id=SessionId(str(data["session_id"])),
    )


def unbind_tool_session_params_from_wire(data: dict[str, object]) -> UnbindToolSessionParams:
    """Reconstruct :class:`UnbindToolSessionParams`.

    Both id fields lift as bare newtypes.
    """
    return UnbindToolSessionParams(
        tool_id=ToolId(str(data["tool_id"])),
        session_id=SessionId(str(data["session_id"])),
    )


def bind_tool_session_ack_from_wire(data: dict[str, object]) -> BindToolSessionAck:
    """Reconstruct :class:`BindToolSessionAck`.

    :attr:`outcome` lifts via :meth:`ToolSessionBindOutcome.from_wire` (strict
    — unknown values raise).
    """
    return BindToolSessionAck(outcome=ToolSessionBindOutcome.from_wire(str(data["outcome"])))


def unbind_tool_session_ack_from_wire(data: dict[str, object]) -> UnbindToolSessionAck:
    """Reconstruct :class:`UnbindToolSessionAck`.

    :attr:`outcome` lifts via :meth:`ToolSessionUnbindOutcome.from_wire` (strict).
    """
    return UnbindToolSessionAck(outcome=ToolSessionUnbindOutcome.from_wire(str(data["outcome"])))


# ── List & search (R96) ───────────────────────────────────────────────────


@dataclass
class ToolsListParams:
    """``tools.list`` params (harness → hub).

    Unlike the tool-call params family (R92), this struct **does** carry a
    ``session_id`` — for the list/search family it is payload data identifying
    which session's bound tool set to enumerate, not envelope routing. The
    R92 module docstring's "params structs do NOT carry a session_id" rule is
    family-scoped to ``tool.call`` (whose ``session_id`` lives on the envelope),
    not module-scoped; the list/search family overrides it. :attr:`mode` is the
    internally-tagged
    :class:`~minimax_code.tool_protocol.connection.ToolDefinitionMode`
    (R82 hand-controlled class; ``to_wire`` returns a dict).
    """

    session_id: SessionId
    mode: ToolDefinitionMode

    def to_wire(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "mode": self.mode.to_wire(),
        }


@dataclass
class ToolsListResult:
    """``tools.list`` result body — the crate's first **list-of-bare-pydantic-model**.

    :attr:`tools` is a ``Vec<ToolDescription>`` whose elements are the codegen
    crate's pydantic :class:`~minimax_code.tool_types.ToolDescription` (R65),
    lifted directly via :meth:`ToolDescription.model_validate` /
    :meth:`ToolDescription.model_dump(exclude_none=True)`. R87's
    :attr:`ToolServerRegistration.tools` was a ``Vec<ToolDescriptionWithSchema>``
    — every element had its own ``from_wire`` / ``to_wire`` classmethod; here
    the element type has **no** hand-controlled converters and round-trips
    through pydantic's own validators, so the list comprehension calls
    ``model_validate`` / ``model_dump`` directly rather than delegating to a
    per-element ``from_wire``.
    """

    tools: list[ToolDescription]

    def to_wire(self) -> dict[str, object]:
        return {"tools": [t.model_dump(exclude_none=True) for t in self.tools]}


@dataclass
class ToolsSearchParams:
    """``tools.search`` params (harness → hub).

    Carries ``session_id`` as payload (same family rule as
    :class:`ToolsListParams`). :attr:`limit` is the Rust ``usize`` → Python ``int``.
    """

    session_id: SessionId
    query: str
    limit: int

    def to_wire(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "query": self.query,
            "limit": self.limit,
        }


@dataclass
class ToolSearchResult:
    """One match in a ``tools.search_result`` body.

    Wire-local definition (the Rust comment notes this keeps the protocol crate
    free of the codegen crate's transitive deps — distinct from
    :class:`ToolsListResult.tools` which embeds the codegen
    :class:`~minimax_code.tool_types.ToolDescription` directly).
    :attr:`input_schema` is an opaque ``serde_json::Value`` → Python ``object``
    round-tripped verbatim (the R92 opaque-``Value`` passthrough shape — no
    conversion, no validation). :attr:`score` is the Rust ``f32`` → Python ``float``.
    """

    tool_name: str
    server_name: str
    description: str
    score: float
    parameters: list[str]
    #: Opaque JSON value (the tool's input schema). Round-tripped verbatim.
    input_schema: object

    def to_wire(self) -> dict[str, object]:
        return {
            "tool_name": self.tool_name,
            "server_name": self.server_name,
            "description": self.description,
            "score": self.score,
            "parameters": self.parameters,
            "input_schema": self.input_schema,
        }


@dataclass
class ToolsSearchResultBody:
    """``tools.search_result`` body.

    :attr:`results` is a ``Vec<ToolSearchResult>`` — a hand-controlled DTO with
    its own ``from_wire`` / ``to_wire`` (unlike
    :class:`ToolsListResult.tools`'s bare-pydantic list, each element here
    delegates to :func:`tool_search_result_from_wire`). :attr:`total_hidden_tools`
    is ``usize`` → ``int``; :attr:`is_ready` is ``bool`` (always serialised,
    mirroring R86's bool-always-serialises rule).
    """

    results: list[ToolSearchResult]
    total_hidden_tools: int
    is_ready: bool

    def to_wire(self) -> dict[str, object]:
        return {
            "results": [r.to_wire() for r in self.results],
            "total_hidden_tools": self.total_hidden_tools,
            "is_ready": self.is_ready,
        }


def tools_list_params_from_wire(data: dict[str, object]) -> ToolsListParams:
    """Reconstruct :class:`ToolsListParams`.

    :attr:`session_id` lifts as a bare newtype; :attr:`mode` lifts via
    :meth:`ToolDefinitionMode.from_wire` (R82 hand-controlled class — the wire
    value is the internally-tagged dict).
    """
    return ToolsListParams(
        session_id=SessionId(str(data["session_id"])),
        mode=ToolDefinitionMode.from_wire(data["mode"]),  # type: ignore[arg-type]
    )


def tools_list_result_from_wire(data: dict[str, object]) -> ToolsListResult:
    """Reconstruct :class:`ToolsListResult`.

    :attr:`tools` lifts as a list of bare pydantic models via
    :meth:`ToolDescription.model_validate` (R65 — the element type has no
    ``from_wire`` classmethod; this is the list counterpart of R87's
    single-element ``ToolDescription.model_validate`` embed).
    """
    return ToolsListResult(
        tools=[
            ToolDescription.model_validate(t)  # type: ignore[arg-type]
            for t in data["tools"]  # type: ignore[union-attr]
        ],
    )


def tools_search_params_from_wire(data: dict[str, object]) -> ToolsSearchParams:
    """Reconstruct :class:`ToolsSearchParams`.

    :attr:`session_id` lifts as a bare newtype; :attr:`limit` is ``usize`` → ``int``.
    """
    return ToolsSearchParams(
        session_id=SessionId(str(data["session_id"])),
        query=str(data["query"]),
        limit=int(data["limit"]),  # type: ignore[arg-type]
    )


def tool_search_result_from_wire(data: dict[str, object]) -> ToolSearchResult:
    """Reconstruct :class:`ToolSearchResult`.

    :attr:`input_schema` is the opaque ``Value`` passthrough (verbatim).
    :attr:`score` is ``f32`` → ``float``; :attr:`parameters` is ``Vec<String>``.
    """
    return ToolSearchResult(
        tool_name=str(data["tool_name"]),
        server_name=str(data["server_name"]),
        description=str(data["description"]),
        score=float(data["score"]),  # type: ignore[arg-type]
        parameters=[str(p) for p in data["parameters"]],  # type: ignore[union-attr]
        input_schema=data["input_schema"],
    )


def tools_search_result_body_from_wire(data: dict[str, object]) -> ToolsSearchResultBody:
    """Reconstruct :class:`ToolsSearchResultBody`.

    :attr:`results` lifts via :func:`tool_search_result_from_wire`;
    :attr:`total_hidden_tools` is ``usize`` → ``int``; :attr:`is_ready` is ``bool``.
    """
    return ToolsSearchResultBody(
        results=[
            tool_search_result_from_wire(r)  # type: ignore[arg-type]
            for r in data["results"]  # type: ignore[union-attr]
        ],
        total_hidden_tools=int(data["total_hidden_tools"]),  # type: ignore[arg-type]
        is_ready=bool(data["is_ready"]),
    )


# ── R97: subscriptions (subscribe / unsubscribe notifications) ──────────
#
# frames.rs 612-691: the notification-subscription family —
# SubscribeNotificationsParams / NotificationFilter / SubscribeOutcome /
# SubscribeAck / UnsubscribeNotificationsParams / UnsubscribeOutcome /
# UnsubscribeAck. Two strict snake_case enums (no #[serde(other)] — unknown
# wire values raise), a fully-optional filter DTO (the crate's first
# #[serde(default)]-on-every-field struct → Python dataclass with all-None
# defaults), and two acks wrapping outcome + subscription_id (R95
# ack-wraps-strict-enum shape, this round with an extra String handle).


class SubscribeOutcome(StrEnum):
    """Outcome reported by :class:`SubscribeAck`.

    ``#[serde(rename_all = "snake_case")]`` with no ``#[serde(other)]``:
    member values are the snake_case wire strings; an unknown wire string
    fails :meth:`from_wire`. Mirrors R95 :class:`ToolSessionBindOutcome`.
    """

    Subscribed = "subscribed"
    AlreadySubscribed = "already_subscribed"
    NotAuthorized = "not_authorized"

    def to_wire(self) -> str:
        """The snake_case wire string (``#[serde(rename_all)]``)."""
        return self.value

    @classmethod
    def from_wire(cls, data: str) -> SubscribeOutcome:
        """Reconstruct from a wire string; reject unknown values.

        Mirrors serde with no ``#[serde(other)]`` arm: an unknown string
        raises :class:`ValueError` rather than being silently swallowed.
        """
        member = cls._value2member_map_.get(data)
        if member is None:
            raise ValueError(f"unknown SubscribeOutcome wire value: {data!r}")
        return member  # type: ignore[return-value]


class UnsubscribeOutcome(StrEnum):
    """Outcome reported by :class:`UnsubscribeAck`.

    ``#[serde(rename_all = "snake_case")]`` with no ``#[serde(other)]``.
    :attr:`Evicted` is server-pushed (slow-consumer eviction), not a client
    request outcome — clients reading it on a connection that did not
    initiate an unsubscribe treat the subscription as gone.
    """

    Unsubscribed = "unsubscribed"
    NotSubscribed = "not_subscribed"
    Evicted = "evicted"

    def to_wire(self) -> str:
        """The snake_case wire string."""
        return self.value

    @classmethod
    def from_wire(cls, data: str) -> UnsubscribeOutcome:
        """Reconstruct from a wire string; reject unknown values."""
        member = cls._value2member_map_.get(data)
        if member is None:
            raise ValueError(f"unknown UnsubscribeOutcome wire value: {data!r}")
        return member  # type: ignore[return-value]


@dataclass
class NotificationFilter:
    """Client-side filter on a notification subscription.

    ``#[serde(default, skip_serializing_if = "Option::is_none")]`` on both
    fields → :meth:`to_wire` omits ``None`` arms; ``NotificationFilter()`` is
    a valid all-``None`` instance (accept-everything). :attr:`kinds` is
    ``None`` = accept all kinds, ``Some(vec![])`` = accept none (empty
    whitelist), ``Some([...])`` = whitelist.
    """

    tool_id: ToolId | None = None
    kinds: list[str] | None = None

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {}
        if self.tool_id is not None:
            wire["tool_id"] = self.tool_id
        if self.kinds is not None:
            wire["kinds"] = self.kinds
        return wire


@dataclass
class SubscribeNotificationsParams:
    """``subscribe_notifications`` params — register a notification subscriber.

    :attr:`session_id` is payload (the session whose notifications to
    receive), overriding the R92 family-scoped "no session_id on params"
    rule — same nuance as R96 list/search. :attr:`filter` is optional
    (``#[serde(default, skip_serializing_if)]``): ``None`` = accept all.
    """

    session_id: SessionId
    filter: NotificationFilter | None = None

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {"session_id": self.session_id}
        if self.filter is not None:
            wire["filter"] = self.filter.to_wire()
        return wire


@dataclass
class SubscribeAck:
    """Reply to :class:`SubscribeNotificationsParams`.

    :attr:`subscription_id` is the harness-facing handle threaded through
    subsequent :class:`UnsubscribeNotificationsParams`; the service reuses a
    single ``"default"`` value per ``(connection, session)`` pair.
    """

    outcome: SubscribeOutcome
    subscription_id: str

    def to_wire(self) -> dict[str, object]:
        return {"outcome": str(self.outcome), "subscription_id": self.subscription_id}


@dataclass
class UnsubscribeNotificationsParams:
    """``unsubscribe_notifications`` params — drop a notification subscriber."""

    session_id: SessionId
    subscription_id: str

    def to_wire(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "subscription_id": self.subscription_id,
        }


@dataclass
class UnsubscribeAck:
    """Reply to :class:`UnsubscribeNotificationsParams` (and the
    service-pushed slow-consumer-eviction frame via :attr:`Evicted`)."""

    outcome: UnsubscribeOutcome
    subscription_id: str

    def to_wire(self) -> dict[str, object]:
        return {"outcome": str(self.outcome), "subscription_id": self.subscription_id}


def notification_filter_from_wire(data: dict[str, object]) -> NotificationFilter:
    """Reconstruct :class:`NotificationFilter` (both arms optional).

    ``#[serde(default)]`` → missing keys default to ``None`` rather than
    raising; :attr:`tool_id` lifts via :class:`ToolId`, :attr:`kinds` lifts
    element-wise via ``str``.
    """
    tool_id_raw = data.get("tool_id")
    kinds_raw = data.get("kinds")
    return NotificationFilter(
        tool_id=ToolId(str(tool_id_raw)) if tool_id_raw is not None else None,
        kinds=[str(k) for k in kinds_raw] if kinds_raw is not None else None,  # type: ignore[union-attr]
    )


def subscribe_notifications_params_from_wire(
    data: dict[str, object],
) -> SubscribeNotificationsParams:
    """Reconstruct :class:`SubscribeNotificationsParams`.

    :attr:`filter` is optional — ``None`` when the key is absent
    (``#[serde(default)]``); present-but-null is treated as absent too.
    """
    filter_raw = data.get("filter")
    return SubscribeNotificationsParams(
        session_id=SessionId(str(data["session_id"])),
        filter=notification_filter_from_wire(filter_raw)  # type: ignore[arg-type]
        if filter_raw is not None
        else None,
    )


def subscribe_ack_from_wire(data: dict[str, object]) -> SubscribeAck:
    """Reconstruct :class:`SubscribeAck`; outcome is strict (rejects unknown)."""
    return SubscribeAck(
        outcome=SubscribeOutcome.from_wire(str(data["outcome"])),
        subscription_id=str(data["subscription_id"]),
    )


def unsubscribe_notifications_params_from_wire(
    data: dict[str, object],
) -> UnsubscribeNotificationsParams:
    """Reconstruct :class:`UnsubscribeNotificationsParams`."""
    return UnsubscribeNotificationsParams(
        session_id=SessionId(str(data["session_id"])),
        subscription_id=str(data["subscription_id"]),
    )


def unsubscribe_ack_from_wire(data: dict[str, object]) -> UnsubscribeAck:
    """Reconstruct :class:`UnsubscribeAck`; outcome is strict (rejects unknown)."""
    return UnsubscribeAck(
        outcome=UnsubscribeOutcome.from_wire(str(data["outcome"])),
        subscription_id=str(data["subscription_id"]),
    )


# ── Tool server status lifecycle (R98) ───────────────────────────────────
#
# The ``tool_server.status`` / ``tool_server.get_status`` / ``tool_server.evict``
# family — the hub↔tool-server health/telemetry channel. This domain unblocks
# the deferred "server discovery + binding" domain by landing the
# cross-domain :class:`ToolServerLifecycleStatus` enum it references.


class ToolServerLifecycleStatus(StrEnum):
    """Lifecycle status of a tool-server connection.

    ``#[serde(rename_all = "snake_case")]`` with no ``#[serde(other)]`` AND a
    ``#[default]`` on :attr:`Ready` (the crate's first strict enum to also
    carry a default member — mirroring Rust's ``#[derive(Default)]`` via the
    :meth:`default` classmethod). Transitions:
    ``starting → ready → busy ↔ ready → draining → shutting_down``;
    :attr:`Disconnected` is hub-only (set during disconnect cleanup, never
    sent by the tool server itself).
    """

    Starting = "starting"
    Ready = "ready"  # #[default]
    Busy = "busy"
    Draining = "draining"
    ShuttingDown = "shutting_down"
    Disconnected = "disconnected"

    def to_wire(self) -> str:
        """The snake_case wire string (``#[serde(rename_all)]``)."""
        return self.value

    @classmethod
    def from_wire(cls, data: str) -> ToolServerLifecycleStatus:
        """Reconstruct from a wire string; reject unknown values.

        No ``#[serde(other)]`` arm: an unknown string raises
        :class:`ValueError` rather than being silently swallowed.
        """
        member = cls._value2member_map_.get(data)
        if member is None:
            raise ValueError(f"unknown ToolServerLifecycleStatus wire value: {data!r}")
        return member  # type: ignore[return-value]

    @classmethod
    def default(cls) -> ToolServerLifecycleStatus:
        """The ``#[default]`` member (:attr:`Ready`).

        Mirrors Rust's ``#[derive(Default)]`` on the enum —
        ``ToolServerLifecycleStatus::default() == Ready``.
        """
        return cls.Ready


class ToolServerDisconnectReason(StrEnum):
    """Why a tool-server connection was dropped (carried in the hub's
    ``tool_server.status_changed`` disconnect notification).

    ``#[serde(rename_all = "snake_case")]`` with no ``#[serde(other)]`` and no
    ``#[default]`` — strict round-trip, unknown values fail :meth:`from_wire`.
    """

    NormalClose = "normal_close"
    IdleTimeout = "idle_timeout"
    ForceEvicted = "force_evicted"
    ConnectionLost = "connection_lost"

    def to_wire(self) -> str:
        """The snake_case wire string."""
        return self.value

    @classmethod
    def from_wire(cls, data: str) -> ToolServerDisconnectReason:
        """Reconstruct from a wire string; reject unknown values."""
        member = cls._value2member_map_.get(data)
        if member is None:
            raise ValueError(f"unknown ToolServerDisconnectReason wire value: {data!r}")
        return member  # type: ignore[return-value]


@dataclass
class ToolServerStatusPayload:
    """``tool_server.status`` payload — the hub↔tool-server health snapshot.

    The crate's most serde-dense single struct: **20 fields exercising four
    distinct field modes in one dataclass**. Field order is reshuffled from
    the Rust source because Python dataclasses require all no-default fields
    ahead of defaulted ones (wire shape is unaffected — ``to_wire`` controls
    key presence, not field declaration order):

    * **required (7)** — no ``#[serde(...)]`` attr: ``status``,
      ``active_tool_calls``, ``background_tasks``, ``pending_tool_calls``,
      ``last_tool_call_started_ms``, ``last_tool_call_completed_ms``,
      ``uptime_ms``. Always serialised; missing on the wire ⇒ ``from_wire``
      raises ``KeyError`` (Rust would fail deserialisation).
    * **Option-skip (4)** — ``#[serde(default, skip_serializing_if =
      "Option::is_none")]``: ``session_id``, ``connection_id``,
      ``idle_since_ms``, ``drain_started_ms``. Omitted when ``None``; a
      missing key deserialises to ``None``.
    * **Vec-skip (2)** — ``#[serde(default, skip_serializing_if =
      "Vec::is_empty")]``: ``active_tool_names``, ``background_task_ids``.
      Omitted when empty; a missing key deserialises to ``[]``.
    * **default-no-skip (7)** — ``#[serde(default)]`` with NO skip:
      ``upload_queue_pending``, ``upload_queue_pending_bytes``,
      ``upload_queue_inflight``, ``upload_queue_circuit_breaker_tripped``,
      ``artifact_producers_inflight``, ``turn_active``,
      ``idle_ignores_background``. **Always serialised even at the falsy
      default** (0 / ``False``); a missing key deserialises to the default.
      These are newer forward-compat fields whose presence is guaranteed so
      consumers can read them unconditionally.

    :attr:`connection_id` is a raw :class:`str`, NOT a typed
    :class:`~minimax_code.tool_protocol.ids.ConnectionId`: a malformed id
    degrades leniently on the consumer (re-parsed, logged, ignored) instead
    of failing deserialisation of the whole status frame. :attr:`session_id`
    scopes counters to that session; ``None`` is the aggregate across all
    sessions. :attr:`idle_since_ms` is ``None`` while busy.
    """

    # required (7)
    status: ToolServerLifecycleStatus
    active_tool_calls: int
    background_tasks: int
    pending_tool_calls: int
    last_tool_call_started_ms: int
    last_tool_call_completed_ms: int
    uptime_ms: int
    # Option-skip (4)
    session_id: SessionId | None = None
    connection_id: str | None = None
    idle_since_ms: int | None = None
    drain_started_ms: int | None = None
    # Vec-skip (2)
    active_tool_names: list[str] = field(default_factory=list)
    background_task_ids: list[str] = field(default_factory=list)
    # default-no-skip (7) — always on wire, even at 0/False
    upload_queue_pending: int = 0
    upload_queue_pending_bytes: int = 0
    upload_queue_inflight: int = 0
    upload_queue_circuit_breaker_tripped: bool = False
    artifact_producers_inflight: int = 0
    turn_active: bool = False
    idle_ignores_background: bool = False

    @classmethod
    def terminal(cls, status: ToolServerLifecycleStatus) -> ToolServerStatusPayload:
        """Zeroed-out payload for terminal states (Disconnected, Starting).

        Mirrors ``ToolServerStatusPayload::terminal(status)`` —
        ``Self { status, ..Default::default() }``. The 6 required int counters
        are zeroed explicitly (Rust's ``u64::default() == 0``); Option-skip /
        Vec-skip / default-no-skip fields fall back to their dataclass defaults
        (``None`` / ``[]`` / ``0`` / ``False``). NB: the counters stay
        *required on the wire* (``from_wire`` still uses ``data[...]``); only
        ``terminal()`` shortcuts to a zeroed instance.
        """
        return cls(
            status=status,
            active_tool_calls=0,
            background_tasks=0,
            pending_tool_calls=0,
            last_tool_call_started_ms=0,
            last_tool_call_completed_ms=0,
            uptime_ms=0,
        )

    def to_wire(self) -> dict[str, object]:
        out: dict[str, object] = {
            # required (7) — always on wire
            "status": str(self.status),
            "active_tool_calls": self.active_tool_calls,
            "background_tasks": self.background_tasks,
            "pending_tool_calls": self.pending_tool_calls,
            "last_tool_call_started_ms": self.last_tool_call_started_ms,
            "last_tool_call_completed_ms": self.last_tool_call_completed_ms,
            "uptime_ms": self.uptime_ms,
            # default-no-skip (7) — always on wire, even at 0/False
            "upload_queue_pending": self.upload_queue_pending,
            "upload_queue_pending_bytes": self.upload_queue_pending_bytes,
            "upload_queue_inflight": self.upload_queue_inflight,
            "upload_queue_circuit_breaker_tripped": self.upload_queue_circuit_breaker_tripped,
            "artifact_producers_inflight": self.artifact_producers_inflight,
            "turn_active": self.turn_active,
            "idle_ignores_background": self.idle_ignores_background,
        }
        # Option-skip (4) — omit when None
        if self.session_id is not None:
            out["session_id"] = self.session_id
        if self.connection_id is not None:
            out["connection_id"] = self.connection_id
        if self.idle_since_ms is not None:
            out["idle_since_ms"] = self.idle_since_ms
        if self.drain_started_ms is not None:
            out["drain_started_ms"] = self.drain_started_ms
        # Vec-skip (2) — omit when empty
        if self.active_tool_names:
            out["active_tool_names"] = self.active_tool_names
        if self.background_task_ids:
            out["background_task_ids"] = self.background_task_ids
        return out


@dataclass
class ToolServerEvictParams:
    """``tool_server.evict`` params — the hub requests graceful shutdown.

    :attr:`grace_period_ms` is the deadline before the hub force-closes the
    connection; :attr:`reason` is a free-form diagnostic string.
    """

    session_id: SessionId
    reason: str
    grace_period_ms: int

    def to_wire(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "reason": self.reason,
            "grace_period_ms": self.grace_period_ms,
        }


@dataclass
class ToolServerGetStatusParams:
    """``tool_server.get_status`` params — scope the query to one session."""

    session_id: SessionId

    def to_wire(self) -> dict[str, object]:
        return {"session_id": self.session_id}


@dataclass
class ToolServerConnectionStatus:
    """One entry in :class:`ToolServerGetStatusResult`.

    :attr:`connection_id` is a raw :class:`str` (same lenient-string rationale
    as :attr:`ToolServerStatusPayload.connection_id`); :attr:`status` is the
    embedded health payload.
    """

    connection_id: str
    status: ToolServerStatusPayload

    def to_wire(self) -> dict[str, object]:
        return {"connection_id": self.connection_id, "status": self.status.to_wire()}


@dataclass
class ToolServerGetStatusResult:
    """Reply to :class:`ToolServerGetStatusParams` — list-of-DTO.

    :attr:`tool_servers` is a ``Vec<ToolServerConnectionStatus>`` lifted
    element-wise via :func:`tool_server_connection_status_from_wire` (R96's
    list-of-bare-pydantic-model shape, here applied to hand-controlled
    dataclass elements rather than codegen pydantic models).
    """

    tool_servers: list[ToolServerConnectionStatus]

    def to_wire(self) -> dict[str, object]:
        return {"tool_servers": [ts.to_wire() for ts in self.tool_servers]}


def tool_server_status_payload_from_wire(
    data: dict[str, object],
) -> ToolServerStatusPayload:
    """Reconstruct :class:`ToolServerStatusPayload` (four field modes).

    Required fields use ``data[...]`` (missing ⇒ ``KeyError``, mirroring
    serde's fail-on-missing for un-attributed fields); Option-skip / Vec-skip
    / default-no-skip fields use ``data.get(...)`` with the appropriate
    default. :attr:`status` lifts via :meth:`ToolServerLifecycleStatus.from_wire`
    (strict — rejects unknown wire values).
    """
    session_id_raw = data.get("session_id")
    connection_id_raw = data.get("connection_id")
    idle_raw = data.get("idle_since_ms")
    drain_raw = data.get("drain_started_ms")
    return ToolServerStatusPayload(
        status=ToolServerLifecycleStatus.from_wire(str(data["status"])),
        active_tool_calls=int(data["active_tool_calls"]),  # type: ignore[arg-type]
        background_tasks=int(data["background_tasks"]),  # type: ignore[arg-type]
        pending_tool_calls=int(data["pending_tool_calls"]),  # type: ignore[arg-type]
        last_tool_call_started_ms=int(data["last_tool_call_started_ms"]),  # type: ignore[arg-type]
        last_tool_call_completed_ms=int(data["last_tool_call_completed_ms"]),  # type: ignore[arg-type]
        uptime_ms=int(data["uptime_ms"]),  # type: ignore[arg-type]
        session_id=SessionId(str(session_id_raw)) if session_id_raw is not None else None,
        connection_id=str(connection_id_raw) if connection_id_raw is not None else None,
        active_tool_names=[str(n) for n in data.get("active_tool_names", [])],  # type: ignore[union-attr]
        background_task_ids=[str(n) for n in data.get("background_task_ids", [])],  # type: ignore[union-attr]
        idle_since_ms=int(idle_raw) if idle_raw is not None else None,  # type: ignore[arg-type]
        upload_queue_pending=int(data.get("upload_queue_pending", 0)),  # type: ignore[arg-type]
        upload_queue_pending_bytes=int(data.get("upload_queue_pending_bytes", 0)),  # type: ignore[arg-type]
        upload_queue_inflight=int(data.get("upload_queue_inflight", 0)),  # type: ignore[arg-type]
        upload_queue_circuit_breaker_tripped=bool(
            data.get("upload_queue_circuit_breaker_tripped", False)
        ),
        artifact_producers_inflight=int(data.get("artifact_producers_inflight", 0)),  # type: ignore[arg-type]
        drain_started_ms=int(drain_raw) if drain_raw is not None else None,  # type: ignore[arg-type]
        turn_active=bool(data.get("turn_active", False)),
        idle_ignores_background=bool(data.get("idle_ignores_background", False)),
    )


def tool_server_evict_params_from_wire(
    data: dict[str, object],
) -> ToolServerEvictParams:
    """Reconstruct :class:`ToolServerEvictParams` (all three fields required)."""
    return ToolServerEvictParams(
        session_id=SessionId(str(data["session_id"])),
        reason=str(data["reason"]),
        grace_period_ms=int(data["grace_period_ms"]),  # type: ignore[arg-type]
    )


def tool_server_get_status_params_from_wire(
    data: dict[str, object],
) -> ToolServerGetStatusParams:
    """Reconstruct :class:`ToolServerGetStatusParams`."""
    return ToolServerGetStatusParams(session_id=SessionId(str(data["session_id"])))


def tool_server_connection_status_from_wire(
    data: dict[str, object],
) -> ToolServerConnectionStatus:
    """Reconstruct :class:`ToolServerConnectionStatus` (embeds the payload DTO)."""
    return ToolServerConnectionStatus(
        connection_id=str(data["connection_id"]),
        status=tool_server_status_payload_from_wire(data["status"]),  # type: ignore[arg-type]
    )


def tool_server_get_status_result_from_wire(
    data: dict[str, object],
) -> ToolServerGetStatusResult:
    """Reconstruct :class:`ToolServerGetStatusResult` (list-of-DTO)."""
    return ToolServerGetStatusResult(
        tool_servers=[
            tool_server_connection_status_from_wire(ts)  # type: ignore[arg-type]
            for ts in data["tool_servers"]  # type: ignore[union-attr]
        ]
    )


# ── Server discovery + binding (R99) ──────────────────────────────────────
#
# The ``servers.list`` / ``server.bind`` / ``server.unbind`` family — the
# hub<->client tool-server discovery & routing channel. This domain consumes
# the :class:`ToolServerLifecycleStatus` enum landed in R98 (via
# :class:`ServerInfo`'s ``status`` field), closing the dependency that R98
# explicitly unblocked: "server discovery + binding (now unblocked by landing
# ToolServerLifecycleStatus)".


@dataclass
class ServersListParams:
    """``servers.list`` params — enumerate connected tool servers.

    Empty struct (``pub struct ServersListParams {}``) — no payload fields;
    routing lives on the JSON-RPC envelope. ``ServersListParams()`` round-trips
    as ``{}``; :meth:`from_wire` accepts (and ignores) any payload.
    """

    def to_wire(self) -> dict[str, object]:
        return {}

    @classmethod
    def from_wire(cls, data: dict[str, object]) -> ServersListParams:  # noqa: ARG003
        """Reconstruct (empty struct — payload ignored)."""
        return cls()


@dataclass
class ServerInfo:
    """Metadata about a connected tool server (one entry in :class:`ServersListResult`).

    Six-field mixed-mode struct mirroring ``ServerInfo``:

    * :attr:`server_id` — required (no serde default); always serialised.
    * :attr:`status` — required :class:`ToolServerLifecycleStatus` (R98);
      always serialised as a snake_case wire string; lifts via
      :meth:`ToolServerLifecycleStatus.from_wire`. **This is the R98 -> R99
      consumer edge** the prior round explicitly unblocked.
    * :attr:`session_id` — ``#[serde(default, skip_serializing_if = "Option::is_none")]``
      -> Option-skip; omitted on the wire when ``None`` (hub may omit it on
      ``servers.list`` responses).
    * :attr:`description` — ``#[serde(default)]`` string -> default-no-skip;
      ``""`` by default, always serialised (even when empty).
    * :attr:`metadata` — ``#[serde(default)]`` ``serde_json::Value`` ->
      default-no-skip; ``None`` (= JSON null) by default, always serialised
      (even as ``null``). Opaque passthrough, same convention as
      :attr:`ToolCallParams.arguments`.
    * :attr:`connected_since` — ``#[serde(default)]`` string -> default-no-skip;
      ``""`` by default, always serialised.

    Field order diverges from the Rust source: Python dataclass rules force the
    two required fields before the four defaulted ones; the wire format is
    unaffected because :meth:`to_wire` controls key presence, not field order.
    """

    server_id: ServerId
    status: ToolServerLifecycleStatus
    session_id: SessionId | None = None
    description: str = ""
    metadata: object = None
    connected_since: str = ""

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {
            "server_id": self.server_id,
            "status": self.status.to_wire(),
            "description": self.description,
            "metadata": self.metadata,
            "connected_since": self.connected_since,
        }
        if self.session_id is not None:
            wire["session_id"] = self.session_id
        return wire


@dataclass
class ServersListResult:
    """Reply to :class:`ServersListParams`.

    :attr:`servers` is a ``Vec<ServerInfo>`` — required (no serde default),
    always serialised; lifts element-wise via :meth:`ServerInfo.from_wire`.
    An empty list is a valid (if unusual) wire value.
    """

    servers: list[ServerInfo]

    def to_wire(self) -> dict[str, object]:
        return {"servers": [s.to_wire() for s in self.servers]}


@dataclass
class ServerBindParams:
    """``server.bind`` params — bind a tool server's tools to a harness session.

    Both :attr:`server_id` and :attr:`session_id` are required (no serde
    defaults). :attr:`session_id` here is payload (the harness session whose
    tool set is being mutated), distinct from any envelope-level
    ``session_id`` — same family-scoped carve-out as
    :class:`BindToolSessionParams` (R95).
    """

    server_id: ServerId
    session_id: SessionId

    def to_wire(self) -> dict[str, object]:
        return {"server_id": self.server_id, "session_id": self.session_id}


class ServerBindOutcome(StrEnum):
    """Outcome of a ``server.bind`` request.

    ``#[serde(rename_all = "snake_case")]`` with no ``#[serde(other)]``.
    :attr:`Unavailable` covers ack-timeout / transport send-or-delivery
    failure / malformed-or-explicit-error ack — distinct from
    :attr:`ServerNotFound` (no such server registered at all).
    """

    Bound = "bound"
    AlreadyBound = "already_bound"
    ServerNotFound = "server_not_found"
    Unavailable = "unavailable"

    def to_wire(self) -> str:
        """The snake_case wire string (``#[serde(rename_all)]``)."""
        return self.value

    @classmethod
    def from_wire(cls, data: str) -> ServerBindOutcome:
        """Reconstruct from a wire string; reject unknown values."""
        member = cls._value2member_map_.get(data)
        if member is None:
            raise ValueError(f"unknown ServerBindOutcome wire value: {data!r}")
        return member  # type: ignore[return-value]


@dataclass
class ServerBindAck:
    """Reply to :class:`ServerBindParams` — wraps a :class:`ServerBindOutcome`.

    Single required field; ``ServerBindAck`` is ``Copy`` in Rust (single-enum
    wrapper, mirroring :class:`ServerBindAck`/``BindToolSessionAck`` shape) —
    Python keeps it a plain one-field dataclass.
    """

    outcome: ServerBindOutcome

    def to_wire(self) -> dict[str, object]:
        return {"outcome": self.outcome.to_wire()}


@dataclass
class ServerUnbindParams:
    """``server.unbind`` params — drop a tool server's tools from a session.

    Mirrors :class:`ServerBindParams`; both fields required.
    """

    server_id: ServerId
    session_id: SessionId

    def to_wire(self) -> dict[str, object]:
        return {"server_id": self.server_id, "session_id": self.session_id}


class ServerUnbindOutcome(StrEnum):
    """Outcome of a ``server.unbind`` request.

    ``#[serde(rename_all = "snake_case")]`` with no ``#[serde(other)]``.
    Deliberately no ``AlreadyUnbound`` arm — unbinding a non-bound session is
    idempotent success (:attr:`Unbound`), so the only failure mode is the
    server being gone (:attr:`ServerNotFound`).
    """

    Unbound = "unbound"
    ServerNotFound = "server_not_found"

    def to_wire(self) -> str:
        """The snake_case wire string (``#[serde(rename_all)]``)."""
        return self.value

    @classmethod
    def from_wire(cls, data: str) -> ServerUnbindOutcome:
        """Reconstruct from a wire string; reject unknown values."""
        member = cls._value2member_map_.get(data)
        if member is None:
            raise ValueError(f"unknown ServerUnbindOutcome wire value: {data!r}")
        return member  # type: ignore[return-value]


@dataclass
class ServerUnbindAck:
    """Reply to :class:`ServerUnbindParams` — wraps a :class:`ServerUnbindOutcome`."""

    outcome: ServerUnbindOutcome

    def to_wire(self) -> dict[str, object]:
        return {"outcome": self.outcome.to_wire()}


# ── Wire converters (server discovery + binding, R99) ─────────────────────


def server_info_from_wire(data: dict[str, object]) -> ServerInfo:
    """Reconstruct :class:`ServerInfo` (mixed-mode, embeds the R98 lifecycle enum).

    :attr:`server_id` / :attr:`status` are wire-required (``data[...]``);
    :attr:`session_id` is Option-skip (``data.get(...)``); :attr:`description`
    / :attr:`metadata` / :attr:`connected_since` fall back to their dataclass
    defaults when absent. :attr:`status` lifts via
    :meth:`ToolServerLifecycleStatus.from_wire` — the R98 -> R99 edge.
    """
    session_id_raw = data.get("session_id")
    return ServerInfo(
        server_id=ServerId(str(data["server_id"])),
        status=ToolServerLifecycleStatus.from_wire(str(data["status"])),
        session_id=SessionId(str(session_id_raw)) if session_id_raw is not None else None,  # type: ignore[arg-type]
        description=str(data.get("description", "")),
        metadata=data.get("metadata"),
        connected_since=str(data.get("connected_since", "")),
    )


def servers_list_params_from_wire(data: dict[str, object]) -> ServersListParams:
    """Reconstruct :class:`ServersListParams` (empty struct — ignores payload)."""
    return ServersListParams.from_wire(data)


def servers_list_result_from_wire(data: dict[str, object]) -> ServersListResult:
    """Reconstruct :class:`ServersListResult` (list-of-DTO, elements lift via R99)."""
    return ServersListResult(
        servers=[server_info_from_wire(s) for s in data["servers"]]  # type: ignore[union-attr]
    )


def server_bind_params_from_wire(data: dict[str, object]) -> ServerBindParams:
    """Reconstruct :class:`ServerBindParams` (both fields required)."""
    return ServerBindParams(
        server_id=ServerId(str(data["server_id"])),
        session_id=SessionId(str(data["session_id"])),
    )


def server_bind_ack_from_wire(data: dict[str, object]) -> ServerBindAck:
    """Reconstruct :class:`ServerBindAck` (wraps the strict outcome enum)."""
    return ServerBindAck(outcome=ServerBindOutcome.from_wire(str(data["outcome"])))


def server_unbind_params_from_wire(data: dict[str, object]) -> ServerUnbindParams:
    """Reconstruct :class:`ServerUnbindParams` (both fields required)."""
    return ServerUnbindParams(
        server_id=ServerId(str(data["server_id"])),
        session_id=SessionId(str(data["session_id"])),
    )


def server_unbind_ack_from_wire(data: dict[str, object]) -> ServerUnbindAck:
    """Reconstruct :class:`ServerUnbindAck` (wraps the strict outcome enum)."""
    return ServerUnbindAck(outcome=ServerUnbindOutcome.from_wire(str(data["outcome"])))


# ── Tool/system notifications (R100) ─────────────────────────────────────


@dataclass
class ToolNotificationFrame:
    """A notification emitted by a tool server or the harness (``ToolNotificationFrame``).

    Carries an R83 :data:`~minimax_code.tool_protocol.notification_wire.WireToolNotification`
    (``Known`` / ``Custom``) plus optional routing ids. ``notification`` is
    required; ``tool_call_id`` and ``tool_id`` are ``Option``-skip
    (wire-omitted when ``None``). The ``custom`` / ``known`` classmethods
    mirror the Rust constructors — both leave ``tool_call_id`` unset, matching
    the source (which sets it to ``None`` in both factory paths).

    The Python dataclass reorders fields so the required ``notification``
    precedes the two defaulted id fields (Python dataclass rule: required
    before defaulted). The wire order is controlled independently by
    :meth:`to_wire`, which follows the Rust declaration order
    (``tool_call_id`` -> ``tool_id`` -> ``notification``).
    """

    notification: WireToolNotification
    tool_call_id: ToolCallId | None = None
    tool_id: ToolId | None = None

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {}
        if self.tool_call_id is not None:
            wire["tool_call_id"] = self.tool_call_id
        if self.tool_id is not None:
            wire["tool_id"] = self.tool_id
        wire["notification"] = self.notification.to_wire()
        return wire

    @classmethod
    def custom(cls, tool_id: ToolId, kind: str, payload: object) -> ToolNotificationFrame:
        """Build a free-form custom notification (``ToolNotificationFrame::custom``).

        Wraps an app-defined ``kind`` + opaque ``payload`` in the ``Custom``
        shape. ``tool_call_id`` is left ``None`` (matches the Rust constructor).
        """
        return cls(
            notification=Custom(WireCustomNotification(kind=kind, payload=payload)),
            tool_id=tool_id,
        )

    @classmethod
    def known(cls, tool_id: ToolId, notification: object) -> ToolNotificationFrame:
        """Build a notification wrapping a typed ("known") value (``ToolNotificationFrame::known``).

        The ``notification`` is wrapped in the ``Known`` shape. The Rust
        source returns ``Result<Self, serde_json::Error>`` from
        ``serde_json::to_value``; the Python payload is already a JSON-ready
        object (``object``), so there is no serialisation step — and therefore
        no error arm — to reproduce.
        """
        return cls(
            notification=Known.from_value(notification),
            tool_id=tool_id,
        )


def tool_notification_frame_from_wire(data: dict[str, object]) -> ToolNotificationFrame:
    """Reconstruct :class:`ToolNotificationFrame`.

    ``notification`` is required and lifts via the R83 module-level
    :func:`~minimax_code.tool_protocol.notification_wire.from_wire`
    dispatcher; the two id fields are ``Option``-skip (absent -> ``None``,
    present -> str newtype).
    """
    tool_call_id_raw = data.get("tool_call_id")
    tool_id_raw = data.get("tool_id")
    return ToolNotificationFrame(
        notification=notification_from_wire(data["notification"]),  # type: ignore[arg-type]
        tool_call_id=(
            ToolCallId(str(tool_call_id_raw)) if tool_call_id_raw is not None else None
        ),
        tool_id=ToolId(str(tool_id_raw)) if tool_id_raw is not None else None,
    )


#: Maximum serialized size of a ``system.notify`` opaque payload (``usize``).
MAX_SYSTEM_NOTIFY_PAYLOAD_BYTES: int = 256 * 1024


@dataclass
class SystemNotifyParams:
    """Body of a ``system.notify`` frame (``SystemNotifyParams``).

    ``payload`` is an opaque JSON value (the crate's ``SystemNotification``)
    forwarded verbatim without decoding. ``echo_to_subscribers`` is a
    default-no-skip ``bool`` (``#[serde(default)]`` with no
    ``skip_serializing_if`` — always on wire, even at ``False``);
    ``conversation_id_override`` and ``request_id`` are ``Option``-skip.

    Wire order follows the Rust declaration: ``payload`` ->
    ``conversation_id_override`` (when set) -> ``echo_to_subscribers`` ->
    ``request_id`` (when set).
    """

    payload: object
    conversation_id_override: str | None = None
    echo_to_subscribers: bool = False
    request_id: str | None = None

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {"payload": self.payload}
        if self.conversation_id_override is not None:
            wire["conversation_id_override"] = self.conversation_id_override
        wire["echo_to_subscribers"] = self.echo_to_subscribers
        if self.request_id is not None:
            wire["request_id"] = self.request_id
        return wire


def system_notify_params_from_wire(data: dict[str, object]) -> SystemNotifyParams:
    """Reconstruct :class:`SystemNotifyParams`.

    ``payload`` is required and passed through verbatim (opaque).
    ``echo_to_subscribers`` is a default-no-skip field on the wire, but a
    missing key is tolerated on read (defaults to ``False``). The two
    ``Option``-skip fields lift to ``str | None``.
    """
    conversation_id_override_raw = data.get("conversation_id_override")
    request_id_raw = data.get("request_id")
    return SystemNotifyParams(
        payload=data["payload"],
        conversation_id_override=(
            str(conversation_id_override_raw)
            if conversation_id_override_raw is not None
            else None
        ),
        echo_to_subscribers=bool(data.get("echo_to_subscribers", False)),
        request_id=str(request_id_raw) if request_id_raw is not None else None,
    )



# ── Session lifecycle open/close primitives (R101) ────────────────────────
#
# Fusion of grok-build's ``xai-tool-protocol::frames`` session-lifecycle
# open/close primitives (frames.rs 438-463): ``SessionOpenParams`` /
# ``LastSeq`` / ``SessionCloseParams`` / ``SessionOpenResult``. This is the
# leaf of the larger session-lifecycle domain — the open/close primitives a
# connection uses to (re)attach and tear down a session, deliberately kept
# separate from the heavier bind/attach/serve flows (R102+ candidates).
#
# Wire-shape notes:
# * ``SessionOpenParams.resume`` is ``#[serde(default)]`` with **no**
#   ``skip_serializing_if`` — a default-no-skip field that always rides the
#   wire (even when ``false``), but a missing key is tolerated on read.
# * ``SessionOpenParams.last_seq`` and ``SessionCloseParams.reason`` are
#   ``Option``-skip fields (absent from the wire when ``None``).
# * ``LastSeq`` carries an R82 :class:`ConnectionId` (str newtype, emitted as
#   a bare string) and an R82 :class:`FrameSeq` (transparent u64, emitted as
#   a bare integer); both lift through their respective ``from_wire``.
# * ``SessionOpenResult`` is an empty ``#[derive(Default)]`` struct —
#   ``to_wire`` returns ``{}`` and ``from_wire`` ignores its argument.


@dataclass
class LastSeq:
    """Reconnect cursor (``frames::LastSeq``).

    The ``(connection_id, seq)`` pair a client replays to resume a session
    mid-stream. Both fields are required (no ``#[serde(default)]`` upstream).
    """

    connection_id: ConnectionId
    seq: FrameSeq

    def to_wire(self) -> dict[str, object]:
        return {
            "connection_id": self.connection_id,
            "seq": self.seq.to_wire(),
        }


def last_seq_from_wire(data: dict[str, object]) -> LastSeq:
    """Reconstruct :class:`LastSeq`.

    ``connection_id`` lifts through :class:`ConnectionId` (str newtype);
    ``seq`` lifts through :meth:`FrameSeq.from_wire` (bare ``u64``).
    """
    return LastSeq(
        connection_id=ConnectionId(str(data["connection_id"])),
        seq=FrameSeq.from_wire(data["seq"]),
    )


@dataclass
class SessionOpenParams:
    """``session.open`` params (``frames::SessionOpenParams``).

    ``resume`` (default-no-skip ``bool``) always rides the wire; ``last_seq``
    (``Option``-skip) is present only when reconnecting.
    """

    resume: bool = False
    last_seq: LastSeq | None = None

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {"resume": self.resume}
        if self.last_seq is not None:
            wire["last_seq"] = self.last_seq.to_wire()
        return wire


def session_open_params_from_wire(data: dict[str, object]) -> SessionOpenParams:
    """Reconstruct :class:`SessionOpenParams`.

    ``resume`` is a default-no-skip field (tolerates a missing key); ``last_seq``
    is an ``Option``-skip field lifted through :func:`last_seq_from_wire`.
    """
    last_seq_raw = data.get("last_seq")
    return SessionOpenParams(
        resume=bool(data.get("resume", False)),
        last_seq=(
            last_seq_from_wire(last_seq_raw)  # type: ignore[arg-type]
            if last_seq_raw is not None
            else None
        ),
    )


@dataclass
class SessionCloseParams:
    """``session.close`` params (``frames::SessionCloseParams``).

    ``reason`` is an ``Option``-skip field — absent from the wire when ``None``.
    """

    reason: str | None = None

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {}
        if self.reason is not None:
            wire["reason"] = self.reason
        return wire


def session_close_params_from_wire(data: dict[str, object]) -> SessionCloseParams:
    """Reconstruct :class:`SessionCloseParams`.

    ``reason`` is an ``Option``-skip field lifting to ``str | None``.
    """
    reason_raw = data.get("reason")
    return SessionCloseParams(
        reason=str(reason_raw) if reason_raw is not None else None,
    )


@dataclass
class SessionOpenResult:
    """``session.open`` result (``frames::SessionOpenResult``).

    Empty ``#[derive(Default)]`` struct — carries no fields on the wire.
    """

    def to_wire(self) -> dict[str, object]:
        return {}


def session_open_result_from_wire(data: dict[str, object]) -> SessionOpenResult:
    """Reconstruct :class:`SessionOpenResult` (empty struct — ``data`` ignored)."""
    return SessionOpenResult()


# ── Session bind/attach server (R102) ─────────────────────────────────────
#
# Fusion of grok-build's ``xai-tool-protocol::frames`` session bind/attach
# server sub-domain (frames.rs 465-544): ``AttachRoute`` /
# ``SessionBindServerParams`` / ``SessionBindServerResult`` /
# ``SessionUnbindServerParams`` / ``SessionAttachServerParams`` /
# ``SessionAttachServerResult``. This is the upper half of the
# session-lifecycle domain — harness→hub requests to bind/unbind/attach a
# tool server to the envelope session, plus the attach-discovery route enum.
# The simplified-lifecycle serve sub-domain (ServeParams/ServeResult/
# SessionBindParams, frames.rs 546+) remains deferred — it depends on
# ``crate::ToolDescriptionWithSchema`` which has no Python mirror yet.
#
# Wire-shape notes:
# * ``AttachRoute`` is the crate's first ``#[serde(other)]`` forward-tolerant
#   StrEnum INSIDE frames.py: ``rename_all = "snake_case"`` + a ``#[serde(other)]``
#   ``Unknown`` catch-all that absorbs route values a newer peer may add, so
#   the typed parse never fails across independently-deployed hub/SDK
#   versions. Mirrors R90 ``session_event.ToolCallOutcome``'s tolerant
#   ``try/except -> UNKNOWN`` pattern. The eight prior frames.py StrEnums
#   (R95 ``ToolSessionBindOutcome`` ... R101 ``ServerUnbindOutcome``) were all
#   strict (unknown values raised).
# * ``SessionBindServerParams.metadata`` is the R92 opaque
#   ``serde_json::Value`` passthrough (``object``, round-tripped verbatim).
# * ``SessionBindServerResult.tools`` / ``SessionAttachServerResult.tools``
#   are ``Vec<ToolDescription>`` with ``#[serde(default,
#   skip_serializing_if = "Vec::is_empty")]`` — the list-of-bare-pydantic-model
#   shape (R96 ``ToolsListResult.tools``), lifted via ``model_validate`` /
#   ``model_dump(exclude_none=True)``; default-empty and wire-omitted when
#   empty.
# * ``SessionBindServerResult`` / ``SessionAttachServerParams`` /
#   ``SessionAttachServerResult`` are ``#[derive(Default)]`` (all-Optional +
#   default-empty-list).


class AttachRoute(StrEnum):
    """Where a ``session_attach_server`` found the session's tool-server.

    ``#[serde(rename_all = "snake_case")]``, ``#[derive(Copy, Eq)]``. The
    :data:`UNKNOWN` variant is the ``#[serde(other)]`` forward-compat
    catch-all for routes added in newer protocol versions — the typed parse
    never fails across independently-deployed hub/SDK versions.

    This is the first ``#[serde(other)]`` tolerant StrEnum inside frames.py;
    the eight prior frames.py StrEnums (R95 ``ToolSessionBindOutcome`` …
    R101 ``ServerUnbindOutcome``) were all strict (unknown values raised).
    Mirrors R90 :class:`~minimax_code.tool_protocol.session_event.ToolCallOutcome`'s
    tolerant ``try/except -> UNKNOWN`` pattern.
    """

    LOCAL = "local"
    REMOTE = "remote"
    #: ``#[serde(other)]`` forward-compat catch-all.
    UNKNOWN = "unknown"

    def to_wire(self) -> str:
        """The snake_case wire string (``#[serde(rename_all)]``)."""
        return self.value

    @classmethod
    def from_wire(cls, value: str) -> AttachRoute:
        """Reconstruct from its wire string.

        Unknown values → :data:`UNKNOWN` (the ``#[serde(other)]`` forward-compat
        catch-all); never raises. Mirrors R90
        :class:`~minimax_code.tool_protocol.session_event.ToolCallOutcome`.
        """
        try:
            return cls(value)
        except ValueError:
            return cls.UNKNOWN


@dataclass
class SessionBindServerParams:
    """``session_bind_server`` params (harness → hub).

    Bind a tool server's tools to the current session. :attr:`server_id` is a
    required R82 :class:`ServerId` (str newtype, emitted bare).
    :attr:`cwd` is the working directory the tool server should root the
    session at (absent → server default CWD). :attr:`metadata` is the R92
    opaque ``serde_json::Value`` passthrough (sandbox_id, agent config, …) —
    the hub does not interpret it.
    """

    server_id: ServerId
    #: ``#[serde(default, skip_serializing_if = "Option::is_none")]``.
    cwd: str | None = None
    #: Opaque JSON value, round-tripped verbatim (R92 passthrough).
    metadata: object = None

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {"server_id": self.server_id}
        if self.cwd is not None:
            wire["cwd"] = self.cwd
        if self.metadata is not None:
            wire["metadata"] = self.metadata
        return wire


def session_bind_server_params_from_wire(data: dict[str, object]) -> SessionBindServerParams:
    """Reconstruct :class:`SessionBindServerParams`.

    :attr:`server_id` lifts as :class:`ServerId` (str newtype); :attr:`cwd`
    and the opaque :attr:`metadata` lift to ``None`` when wire-omitted.
    """
    cwd_raw = data.get("cwd")
    metadata_raw = data.get("metadata")
    return SessionBindServerParams(
        server_id=ServerId(str(data["server_id"])),
        cwd=str(cwd_raw) if cwd_raw is not None else None,
        metadata=metadata_raw,
    )


@dataclass
class SessionBindServerResult:
    """Reply to :class:`SessionBindServerParams`.

    ``#[derive(Default)]``. :attr:`tools` is a ``Vec<ToolDescription>`` with
    ``#[serde(default, skip_serializing_if = "Vec::is_empty")]`` — the
    list-of-bare-pydantic-model shape (R96), lifted via
    ``model_validate`` / ``model_dump(exclude_none=True)``. The three remaining
    fields are ``Option``-skip. :attr:`unserved_tool_ids` /
    :attr:`resolve_error` forward :class:`SessionBindResult`'s closed-resolution
    diagnostics verbatim.
    """

    tools: list[ToolDescription] = field(default_factory=list)
    binary_version: str | None = None
    unserved_tool_ids: list[str] = field(default_factory=list)
    resolve_error: str | None = None

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {}
        if self.tools:
            wire["tools"] = [t.model_dump(exclude_none=True) for t in self.tools]
        if self.binary_version is not None:
            wire["binary_version"] = self.binary_version
        if self.unserved_tool_ids:
            wire["unserved_tool_ids"] = self.unserved_tool_ids
        if self.resolve_error is not None:
            wire["resolve_error"] = self.resolve_error
        return wire


def session_bind_server_result_from_wire(data: dict[str, object]) -> SessionBindServerResult:
    """Reconstruct :class:`SessionBindServerResult`.

    :attr:`tools` lifts as a list of bare pydantic models (default-empty when
    wire-omitted); the two ``Option``-skip strings lift to ``None``;
    :attr:`unserved_tool_ids` defaults to empty when omitted.
    """
    binary_raw = data.get("binary_version")
    resolve_raw = data.get("resolve_error")
    return SessionBindServerResult(
        tools=[
            ToolDescription.model_validate(t)  # type: ignore[arg-type]
            for t in data.get("tools", [])  # type: ignore[union-attr]
        ],
        binary_version=str(binary_raw) if binary_raw is not None else None,
        unserved_tool_ids=list(data.get("unserved_tool_ids", [])),
        resolve_error=str(resolve_raw) if resolve_raw is not None else None,
    )


@dataclass
class SessionUnbindServerParams:
    """``session_unbind_server`` params (harness → hub).

    Unbind a tool server from the current session. :attr:`server_id` is a
    required R82 :class:`ServerId`.
    """

    server_id: ServerId

    def to_wire(self) -> dict[str, object]:
        return {"server_id": self.server_id}


def session_unbind_server_params_from_wire(data: dict[str, object]) -> SessionUnbindServerParams:
    """Reconstruct :class:`SessionUnbindServerParams` (:attr:`server_id` bare newtype)."""
    return SessionUnbindServerParams(server_id=ServerId(str(data["server_id"])))


@dataclass
class SessionAttachServerParams:
    """``session_attach_server`` params (harness → hub).

    Attach this harness connection to an EXISTING session as an observer.
    Hub-local: never forwarded to the tool server, never creates a workspace
    session, never mutates toolsets/handlers. ``#[derive(Default)]`` — both
    fields ``Option``-skip. :attr:`server_id` is an optional expected server
    (diagnostics + directory cross-check); the authoritative key is the
    envelope ``session_id``. :attr:`caller` is a free-form caller label for
    metrics/logs.
    """

    server_id: ServerId | None = None
    caller: str | None = None

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {}
        if self.server_id is not None:
            wire["server_id"] = self.server_id
        if self.caller is not None:
            wire["caller"] = self.caller
        return wire


def session_attach_server_params_from_wire(data: dict[str, object]) -> SessionAttachServerParams:
    """Reconstruct :class:`SessionAttachServerParams`.

    Both fields lift to ``None`` when wire-omitted; :attr:`server_id` is a
    nullable :class:`ServerId` str newtype.
    """
    server_raw = data.get("server_id")
    caller_raw = data.get("caller")
    return SessionAttachServerParams(
        server_id=ServerId(str(server_raw)) if server_raw is not None else None,
        caller=str(caller_raw) if caller_raw is not None else None,
    )


@dataclass
class SessionAttachServerResult:
    """Reply to :class:`SessionAttachServerParams`.

    ``#[derive(Default)]``. :attr:`tools` is the same
    ``Vec<ToolDescription>`` list-of-bare-pydantic-model shape as
    :class:`SessionBindServerResult.tools` (R96, default-empty +
    wire-omitted-when-empty). :attr:`route` is an ``Option``-skip
    :class:`AttachRoute` (the first tolerant ``#[serde(other)]`` enum in
    frames.py) describing where the session's tool-server was found.
    """

    tools: list[ToolDescription] = field(default_factory=list)
    route: AttachRoute | None = None

    def to_wire(self) -> dict[str, object]:
        wire: dict[str, object] = {}
        if self.tools:
            wire["tools"] = [t.model_dump(exclude_none=True) for t in self.tools]
        if self.route is not None:
            wire["route"] = self.route.to_wire()
        return wire


def session_attach_server_result_from_wire(data: dict[str, object]) -> SessionAttachServerResult:
    """Reconstruct :class:`SessionAttachServerResult`.

    :attr:`tools` lifts as a list of bare pydantic models (default-empty when
    omitted); :attr:`route` lifts via :meth:`AttachRoute.from_wire` (tolerant
    — unknown values fall back to :data:`AttachRoute.UNKNOWN`).
    """
    return SessionAttachServerResult(
        tools=[
            ToolDescription.model_validate(t)  # type: ignore[arg-type]
            for t in data.get("tools", [])  # type: ignore[union-attr]
        ],
        route=(
            AttachRoute.from_wire(str(data["route"]))  # type: ignore[arg-type]
            if data.get("route") is not None
            else None
        ),
    )
