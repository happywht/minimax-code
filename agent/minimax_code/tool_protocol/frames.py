"""Tool-server frame protocol — per-method params/result payloads (R92).

Fusion of grok-build's ``xai-tool-protocol::frames`` — the per-method
``params`` and ``result`` payload structs that ride inside a
:class:`~minimax_code.tool_protocol.envelope.JsonRpcRequest` /
:class:`~minimax_code.tool_protocol.envelope.JsonRpcResponse` /
:class:`~minimax_code.tool_protocol.envelope.JsonRpcNotification`.

R92 lands the **opening slice** of the crate's largest module (``frames.rs``
is 1549 lines, 86 top-level pub symbols, 14 functional domains): the
tool-call params/result/progress family plus the telemetry-donation family
(tool call + trace/log/metric donation, lines 21-125 of the source). The
remaining 12 domains — tool notification / system notify, registration,
per-tool session binding, server discovery + binding, list & search, session
lifecycle, simplified lifecycle, subscriptions, hooks, service→harness
pushes, tool-server status lifecycle, heartbeat — are deferred to R93+.

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

from minimax_code.tool_protocol.ids import ToolCallId, ToolId
from minimax_code.tool_protocol.output_wire import ToolOutputWire
from minimax_code.tool_protocol.output_wire import from_wire as tool_output_wire_from_wire

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
    # wire converters
    "tool_call_params_from_wire",
    "tool_call_result_from_wire",
    "tool_call_progress_frame_from_wire",
    "traces_donate_params_from_wire",
    "logs_donate_params_from_wire",
    "metrics_donate_params_from_wire",
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
