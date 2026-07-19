"""Tool-server frame protocol — per-method params/result payloads (R92 + R93 + R94 + R95 + R96 + R97).

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
The remaining 7 domains are deferred to R98+.

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
from minimax_code.tool_protocol.ids import ServerId, SessionId, ToolCallId, ToolId
from minimax_code.tool_protocol.methods import Method
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
