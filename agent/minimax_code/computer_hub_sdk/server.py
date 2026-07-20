"""ToolServer runtime — pure-logic type, helper, and conversion layer (R175-R177).

Fusion of grok-build's ``xai-computer-hub-sdk/src/server.rs``. Three leaf
rounds port the pure-logic surface that sits ahead of the live
``HubConnection`` actor:

* R175 — the type-contract preamble (``server.rs`` 56-99): the reconnect
  callback alias, the ``system.notify`` ack enum, and the two pure helpers
  that feed it.
* R176 — two pure conversion helpers embedded deeper in the file
  (``server.rs`` 1865 + 2249): the ``tool_call_request`` id extractor and
  the ``ToolProgress`` -> ``ToolCallProgressFrame`` wire shaper.
* R177 — the failed-call error response builder (``server.rs`` 2276):
  preserves the full :class:`ToolError` as a decodable ``ToolErrorWire`` in
  ``error.data`` plus the matching numeric code (unblocks once R83's
  ``from_tool_error_wire`` backfill and R107's ``ToolError.to_wire`` bridge
  both land).

The live actor itself (``ToolServer`` / ``ToolServerBuilder`` /
``ToolServerInner`` / the per-session inbox dispatcher) lands in later
leaves once a MiniMax-side consumer for the xAI socket protocol exists.

The ported symbols, all pure and independently unit-tested in the source:

* :data:`ReconnectSettledCallback` — ``Box<dyn Fn() + Send + Sync + 'static>``
  collapsed to the bare call shape ``() -> None`` (Python has no
  trait-object / lifetime vocabulary; the call signature is the contract).
* :class:`SystemNotifyAck` — the two-variant ``pub enum`` (``Accepted`` /
  ``ForwardingUnsupported``) reporting the outcome of a ``system.notify``
  round-trip.
* :func:`json_serialized_len` — byte length of a JSON serialisation
  without materialising the string (Rust counts bytes via a no-op
  ``std::io::Write`` sink; Python serialises to a compact string and
  measures the UTF-8 byte length — see the fidelity note on the function).
* :func:`parse_tool_call_id` — extract ``params.tool_call_id`` from a raw
  ``tool_call_request`` frame (Rust JSON-pointer walk + ``from_value .ok()``).
* :func:`progress_to_frame` — shape a :class:`ToolProgress` into a wire
  :class:`ToolCallProgressFrame` (three-arm ``match`` on the ``kind``
  discriminator; ``dropped_count`` always ``None`` here).
* :func:`build_error_response` — assemble a :class:`JsonRpcResponse` error
  envelope for a failed tool call, preserving the full :class:`ToolError`
  as a decodable :class:`ToolErrorWire` in ``error.data`` (Rust
  ``ToolErrorWire::from`` + ``from_tool_error_wire`` +
  ``serde_json::to_value().ok()``).
* :func:`system_notify_ack_from_outcome` — pure match mapping
  :data:`~minimax_code.tool_protocol.envelope.ResponseOutcome` to
  :class:`SystemNotifyAck`, lifting a bare ``-32601 method_not_found``
  (no ``data`` discriminator) into ``ForwardingUnsupported``.

YAGNI boundary
--------------

The remainder of ``server.rs`` (``ToolServer`` actor, ``ToolServerBuilder``,
``ToolServerInner``, the per-session inbox loop, ``handle_notification`` /
``execute_call`` / ``run_session_loop`` / ``send_overloaded`` ...) is bound
to the live xAI ``HubConnection`` socket protocol; MiniMax carries no such
consumer, so those leaves stay deferred under YAGNI until a MiniMax
transport needs them. The ``SESSION_INBOX_BUFFER`` constant and
``SessionHandlerMap`` / ``SessionHandlerResolver`` type aliases belong to
that actor and travel with it, not with this layer.
"""

from __future__ import annotations

import enum
import json
import logging
from collections.abc import Callable
from typing import Any

from minimax_code.computer_hub_sdk.error import ClientError, SerdeError
from minimax_code.tool_protocol.envelope import (
    JsonRpcError,
    JsonRpcId,
    JsonRpcResponse,
    ResponseError,
    ResponseOutcome,
    ResponseResult,
)
from minimax_code.tool_protocol.error_codes import from_tool_error_wire, string_for
from minimax_code.tool_protocol.frames import ToolCallProgressFrame
from minimax_code.tool_protocol.ids import IdError, SessionId, ToolCallId
from minimax_code.tool_runtime.error import ToolError
from minimax_code.tool_runtime.tool import ToolProgress

__all__ = [
    "ReconnectSettledCallback",
    "SystemNotifyAck",
    "build_error_response",
    "json_serialized_len",
    "parse_tool_call_id",
    "progress_to_frame",
    "system_notify_ack_from_outcome",
]

#: Module logger for the defensive ``warn!``-equivalent fallbacks (R176
#: ``progress_to_frame`` content-serialisation path mirrors Rust's ``warn!``).
_log = logging.getLogger(__name__)


#: Fired after reconnect ``serve`` replay completes (async settle) (R175).
#:
#: Rust ``pub type ReconnectSettledCallback = Box<dyn Fn() + Send + Sync + 'static>;``
#: — a heap-allocated, thread-safe, ownership-transferring callback. Python
#: has neither trait objects nor ``'static`` / ``Send`` / ``Sync`` bounds, so
#: the alias collapses to the bare call shape ``() -> None``; the contract a
#: caller must satisfy is exactly that signature (the ``ToolServerBuilder``
#: setter that stores it lands with the actor leaf).
ReconnectSettledCallback = Callable[[], None]


class SystemNotifyAck(enum.Enum):
    """Outcome of a ``system.notify`` request (``server.rs`` ``pub enum``).

    Mirrors ``SystemNotifyAck`` (``#[derive(Debug, Clone, Copy, PartialEq, Eq)]``):

    * :attr:`Accepted` — the server acknowledged the notify and will forward it.
    * :attr:`ForwardingUnsupported` — an older server that lacks the method,
      surfaced as a bare ``-32601 method_not_found`` reply.

    The Rust variant is a plain C-like enum (no associated data), so the
    Python port is :class:`enum.Enum`; ``Enum`` already supplies the
    ``PartialEq`` / ``Eq`` / ``Debug`` equivalents and the per-variant
    singleton identity stands in for Rust's ``Copy`` value semantics.
    """

    Accepted = "Accepted"
    ForwardingUnsupported = "ForwardingUnsupported"


def json_serialized_len(value: Any) -> int:
    """Byte length of ``value``'s compact JSON serialisation (R175).

    Mirrors ``json_serialized_len(value: &Value) -> Result<usize, ClientError>``:
    Rust drives ``serde_json::to_writer`` into a no-op ``std::io::Write`` byte
    counter (a ``Counter(usize)`` sink whose ``write`` just accumulates
    ``buf.len()``), returning the UTF-8 byte length without ever allocating
    the serialised string.

    Fidelity note
    -------------
    Python has no zero-copy serde sink, so the faithful equivalent serialises
    to a compact string and measures its UTF-8 byte length; the intermediate
    :class:`str` is the unavoidable fidelity cost (the byte count itself
    matches exactly). Three knobs reproduce ``serde_json``'s defaults:

    * ``separators=(",", ":")`` — ``serde_json`` emits no whitespace between
      tokens; Python's default ``json.dumps`` adds a space after every ``,``
      and ``:``.
    * ``ensure_ascii=False`` — ``serde_json`` writes non-ASCII verbatim as
      UTF-8 bytes; Python's default ``ensure_ascii=True`` would escape them
      to ``\\uXXXX`` and inflate the count.
    * ``allow_nan=False`` — ``serde_json`` rejects ``NaN`` / ``Infinity``
      (they are not valid JSON) with a serialize error; Python's default
      ``allow_nan=True`` would emit the non-standard literals ``NaN`` /
      ``Infinity`` silently.

    A serialisation failure (a non-finite float, or a value ``json`` cannot
    encode such as a ``set``) is mapped to
    :class:`~minimax_code.computer_hub_sdk.error.SerdeError`, reproducing the
    Rust arm ``ClientError::Serde(e.to_string())``; the lone rename
    ``Serde -> SerdeError`` is documented on the error module.
    """
    try:
        text = json.dumps(
            value,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise SerdeError(str(exc)) from exc
    return len(text.encode("utf-8"))


def system_notify_ack_from_outcome(outcome: ResponseOutcome[Any]) -> SystemNotifyAck:
    """Map a ``system.notify`` :data:`ResponseOutcome` to :class:`SystemNotifyAck` (R175).

    Mirrors ``system_notify_ack_from_outcome(outcome: ResponseOutcome<Value>)
    -> Result<SystemNotifyAck, ClientError>``. The Rust ``match`` has three
    arms, reproduced here as ordered branches:

    * ``ResponseOutcome::Result(_)`` — success; the server acked and will
      forward, so yield :attr:`SystemNotifyAck.Accepted`.
    * ``ResponseOutcome::Error(err) if err.data.is_none()
      && string_for(err.code) == Some("method_not_found")`` — a bare
      ``-32601`` with no ``data`` discriminator means the server simply lacks
      the method; the ``data.is_none()`` guard is load-bearing so that a
      richer ``ToolErrorWire`` payload still flows through the normal
      taxonomy instead of being silently swallowed as "unsupported". Yield
      :attr:`SystemNotifyAck.ForwardingUnsupported`.
    * ``ResponseOutcome::Error(err)`` — any other failure; lift the envelope
      error via :meth:`ClientError.from_jsonrpc_error`.

    The ``Value`` type parameter of the Rust generic is irrelevant to the
    match (only the discriminator arm matters), so the success payload is
    not inspected; the call signature accepts :data:`ResponseOutcome` of
    any value type.
    """
    if isinstance(outcome, ResponseResult):
        return SystemNotifyAck.Accepted
    if isinstance(outcome, ResponseError):
        err = outcome.error
        # Plain -32601 (no `data` discriminator) means the server lacks the
        # method; the ``data.is_none()`` guard is load-bearing so that a richer
        # ``ToolErrorWire`` payload still flows through the normal taxonomy
        # instead of being swallowed as "unsupported".
        if err.data is None and string_for(err.code) == "method_not_found":
            return SystemNotifyAck.ForwardingUnsupported
        raise ClientError.from_jsonrpc_error(err)
    # ``ResponseOutcome`` is ``ResponseResult | ResponseError``; the two branches
    # above exhaust the union. Mirror Rust's total ``match`` by raising rather
    # than returning a silent default for a variant a well-typed caller cannot
    # produce — this limb is unreachable in practice.
    raise TypeError(f"unexpected ResponseOutcome variant: {type(outcome).__name__}")


def parse_tool_call_id(value: Any) -> ToolCallId | None:
    """Extract ``params.tool_call_id`` from a raw ``tool_call_request`` frame (R176).

    Mirrors ``parse_tool_call_id(value: &Value) -> Option<ToolCallId>``: the
    dispatcher uses it to register a cancellation token under the call id
    *before* spawning the handler. The Rust body walks the JSON pointer
    ``/params/tool_call_id``, clones the node, then runs
    ``serde_json::from_value::<ToolCallId>`` (a ``#[serde(transparent)]``
    String newtype whose ``new`` validates) and flattens any failure to
    ``None`` via ``.ok()``.

    Fidelity note
    -------------
    ``serde_json::Value::pointer`` returns ``None`` when any path segment is
    missing or an interior node is not an object; Python reproduces that by
    walking the path one key at a time and bailing on a non-dict node or a
    missing key. ``serde_json::from_value::<ToolCallId>(v)`` fails when ``v``
    is not a JSON string (a type mismatch) or when the inner ``new`` rejects
    the value (an empty string, since :class:`ToolCallId` is a plain
    non-empty opaque id); both failure modes collapse to ``None`` via
    ``.ok()``, which Python reproduces with an ``isinstance(str)`` guard plus
    a ``try`` / ``except IdError`` around the validating constructor.
    """
    node: Any = value
    for key in ("params", "tool_call_id"):
        if not isinstance(node, dict) or key not in node:
            return None
        node = node[key]
    if not isinstance(node, str):
        # ``serde_json::from_value::<ToolCallId>`` rejects a non-string node
        # (type mismatch); ``.ok()`` flattens that to ``None``.
        return None
    try:
        return ToolCallId(node)
    except IdError:
        # ``ToolCallId::new`` rejects an empty (or otherwise malformed) id;
        # ``serde_json::from_value`` surfaces that as an error, ``.ok()``
        # flattens it to ``None``.
        return None


def progress_to_frame(
    progress: ToolProgress,
    tool_call_id: ToolCallId,
) -> ToolCallProgressFrame:
    """Convert a :class:`ToolProgress` into a wire :class:`ToolCallProgressFrame` (R176).

    Mirrors ``progress_to_frame(progress: ToolProgress, tool_call_id: ToolCallId)
    -> ToolCallProgressFrame``. The Rust ``match`` has three arms over the
    ``ToolProgress`` enum, reproduced here as branches on the Python
    dataclass's ``kind`` discriminator (the Python port flattens the Rust
    internally-tagged enum into one dataclass with a ``kind`` field plus
    ``Text`` / ``Content`` / ``Custom`` classmethod constructors):

    * ``Text { text }`` — ``kind = "text"``, ``body = {"text": text}``
      (the ``serde_json::json!`` macro).
    * ``Content { blocks }`` — ``kind = "content"``, ``body =
      serde_json::to_value(blocks)`` with a ``warn!`` +
      ``Value::default()`` (``= Null``) fallback on a serialisation failure.
    * ``Custom { subkind, payload }`` — ``kind = subkind`` (the snake_case
      discriminator the runtime dispatches on), ``body = payload`` (an
      opaque :class:`serde_json::Value` carried verbatim).

    The frame's ``dropped_count`` is always ``None`` here — the demux inbox
    fills it in later when prior frames for this ``tool_call_id`` are
    dropped under rate pressure.

    Fidelity note
    -------------
    ``serde_json::to_value(blocks)`` for the ``Content`` arm serialises the
    ``Vec<ContentBlock>`` into a JSON array; the Python port drives
    :meth:`ContentBlock.to_dict` over the list. The ``warn!`` +
    ``Value::default()`` fallback maps to :func:`logging.warning` + ``None``
    (``Value::Null``); in practice :meth:`to_dict` does not raise, so the
    branch is defensive — it exists to mirror Rust's exhaustiveness rather
    than to handle a reachable failure.
    """
    if progress.kind == "text":
        kind = "text"
        body: Any = {"text": progress.text}
    elif progress.kind == "content":
        kind = "content"
        try:
            body = [block.to_dict() for block in (progress.blocks or [])]
        except Exception as exc:  # serde_json::to_value failure -> Value::default() (Null)
            _log.warning("failed to serialize Content blocks for progress frame: %s", exc)
            body = None
    elif progress.kind == "custom":
        kind = progress.subkind
        body = progress.payload
    else:
        # Rust exhaustiveness is over a closed enum; the Python ``kind`` field
        # is a free ``str``, so a value outside the three known discriminators
        # is a programmer error — surface it rather than emit a malformed frame.
        raise ValueError(f"unknown ToolProgress kind: {progress.kind!r}")
    return ToolCallProgressFrame(
        tool_call_id=tool_call_id,
        kind=kind,
        body=body,
        dropped_count=None,
    )


def build_error_response(
    id_: JsonRpcId,
    session_id: SessionId,
    err: ToolError,
) -> JsonRpcResponse:
    """Build the error response for a failed tool call (R177).

    Mirrors ``build_error_response(id: JsonRpcId, session_id: SessionId,
    err: ToolError) -> JsonRpcResponse``: the full :class:`ToolError` is
    preserved as a decodable :class:`ToolErrorWire` in ``error.data`` (plus
    the matching numeric code) so the harness recovers kind + detail +
    structured details instead of collapsing everything to a bare ``-32603``
    string.

    Fidelity note
    -------------
    * ``err.to_string()`` -> :meth:`ToolError.__str__` (the ``detail`` field,
      the model-facing message).
    * ``ToolErrorWire::from(err)`` -> :meth:`ToolError.to_wire` (the R107
      bridge that is the Python equivalent of the crate's
      ``impl From<ToolError> for ToolErrorWire``).
    * ``error_codes::from_tool_error_wire(&wire)`` ->
      :func:`from_tool_error_wire` (the R83 backfill that dispatches on the
      wire ``code`` tag to the numeric envelope code).
    * ``serde_json::to_value(&wire).ok()`` -> :meth:`ToolErrorWire.to_wire`
      inside a ``try`` / ``except`` that flattens any serialisation failure
      to ``None``. Unlike the ``progress_to_frame`` content arm (R176), Rust
      has **no** ``warn!`` on this path — the ``.ok()`` is silent — so the
      Python port stays silent too (no log). In practice
      :meth:`ToolErrorWire.to_wire` does not raise (it is a deterministic
      dict construction), so the branch is defensive exhaustiveness rather
      than a reachable failure.
    * The response is assembled via :meth:`JsonRpcResponse.err`, which pins
      ``jsonrpc`` to :class:`JsonRpcVersion` and wraps the error in the
      ``ResponseOutcome::Error`` arm — matching the Rust struct literal
      field-for-field.
    """
    message = str(err)
    wire = err.to_wire()
    try:
        data: Any = wire.to_wire()
    except Exception:  # serde_json::to_value(&wire).ok() — silent flatten
        data = None
    error = JsonRpcError(
        code=from_tool_error_wire(wire),
        message=message,
        data=data,
    )
    return JsonRpcResponse.err(id_, error, session_id=session_id)
