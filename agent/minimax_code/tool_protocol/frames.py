"""Tool-server frame protocol — per-method params/result payloads (R92 + R93 + R94).

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
``from_wire`` to the embedded DTO. No crate-first serde shape lands here;
the remaining 10 domains are deferred to R95+.

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

from minimax_code.tool_protocol.ids import ServerId, ToolCallId, ToolId
from minimax_code.tool_protocol.methods import Method
from minimax_code.tool_protocol.output_wire import ToolOutputWire
from minimax_code.tool_protocol.output_wire import from_wire as tool_output_wire_from_wire
from minimax_code.tool_protocol.registration import ToolRegistration, ToolServerRegistration

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
