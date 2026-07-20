"""Remote-dispatch wire decode/encode helpers + connection contract
(R120/R121 layer 4, R122 layer 1).

Fusion of grok-build's ``xai-computer-hub-core/src/remote.rs`` — the
crate's sixth and final leaf, the connection-forwarding transport. The
Rust module is 541 lines spanning four conceptual layers; R120 opens
**layer 4** (the wire decode/encode pure functions), R121 completes the
error-decode group, and R122 opens **layer 1** (the
:class:`ConnectionClient` contract); layers 2-3 defer to later rounds
(see "Why layer 4 lands first" below).

Why remote lands sixth
----------------------

R115-R119 landed the five transport/registry/resolver/inner/local leaves
of ``xai-computer-hub-core``. This round opens the *remote* leaf — the
transport that forwards a tool call over a ``ConnectionClient`` to a
remote tool server and re-encodes the wire response into the runtime's
typed shapes. It is the heavier sibling of R119's
:class:`~minimax_code.computer_hub_core.LocalTransport`: where a local
transport dispatches in-process through a resolver, a remote transport
crosses a connection boundary and must decode the JSON-RPC envelope back
into :class:`~minimax_code.tool_runtime.TypedToolOutput` /
:class:`~minimax_code.tool_runtime.ToolProgress` /
:class:`~minimax_code.tool_runtime.ToolError`.

The four layers of remote.rs
---------------------------

1. ``ConnectionClient`` trait (R122) — the object-safe connection
   abstraction (``request`` / ``subscribe_progress`` / ``notify``). Maps
   to an :class:`abc.ABC`; the Python landing drops the
   ``Send + Sync + Debug`` bounds (GIL-vacuous / default ``__repr__``)
   and maps ``BoxStream`` to :class:`~collections.abc.AsyncIterator`.
2. ``RemoteToolProxy`` + ``RemoteTransport`` (later) — the
   :class:`~minimax_code.computer_hub_core.ToolHandle` /
   :class:`~minimax_code.computer_hub_core.Transport` impls that drive
   the connection.
3. ``dispatch_via_connection`` + ``RequestStream`` (later) — the Rust
   ``Stream`` impl that interleaves progress frames with the terminal
   response; the most complex layer (``Stream`` trait -> Python async).
4. **Wire decode/encode pure functions (R120 + R121)** — the stateless
   seams that turn wire types into runtime types. R120 lands the
   success-path seams (:func:`progress_from_frame`,
   :func:`output_to_value`, :func:`decode_call_result`, plus the private
   :func:`_map_block`); R121 completes the error-decode group
   (:func:`tool_error_from_wire`, :func:`error_from_envelope`,
   :func:`is_workspace_unavailable`, plus the private
   :func:`_terminal_from_response` that closes the loop over
   :func:`decode_call_result` + :func:`error_from_envelope`).

Why layer 4 lands first
-----------------------

Layer 4's functions are **pure** (no trait object, no connection state,
no async stream) and **independent** (``decode_call_result`` depends only
on ``output_to_value`` + ``_map_block``; ``progress_from_frame`` is
standalone). They are also the layer layers 2-3 call into:
``dispatch_via_connection``'s ``RequestStream`` polls progress frames
through :func:`progress_from_frame` and the terminal response through
:func:`_terminal_from_response` (R121; a thin dispatch wrapping
:func:`decode_call_result` and :func:`error_from_envelope`).
Landing them first means later rounds assemble the connection machinery
on top of already-tested seams, and it exercises every wire-to-runtime
type boundary in isolation.

Mapping decisions
-----------------

- **serde_json::Value -> Any**. The Rust functions take/return
  ``serde_json::Value``; the Python landing uses ``Any`` (a JSON value
  is a ``str`` / ``int`` / ``float`` / ``bool`` / ``None`` / ``list`` /
  ``dict``). :func:`output_to_value` returns a JSON value (a bare
  ``str`` for ``Text``, the forwarded payload for ``Json``, a
  ``{"blocks": [...]}`` dict for ``Mcp``) exactly as Rust's
  ``Value::String`` / verbatim passthrough / ``json!`` macro do.

- **Rust ``match`` on enum -> ``isinstance`` dispatch on union members**.
  :class:`~minimax_code.tool_protocol.ToolOutputWire` is
  ``Text | Json | Mcp``; :func:`output_to_value` and :func:`_map_block`
  dispatch with ``isinstance`` checks. The trailing ``raise TypeError``
  is the Python counterpart to Rust's compile-time exhaustiveness
  (unreachable in practice — the unions have only their tagged members).

- **serde_json::from_value::<ToolCallResult> -> strict manual decode**.
  :class:`~minimax_code.tool_protocol.ToolCallResult` ships ``to_wire``
  but no ``from_wire`` (the crate never round-trips results through a
  generic decoder). :func:`_decode_tool_call_result` mirrors
  ``serde_json::from_value`` by strict-extracting the required
  ``tool_call_id`` + ``output`` fields and decoding ``output`` via the
  strict adjacent-tagged :func:`output_from_wire` (raises ``ValueError``
  on an unknown ``kind``). A missing key (``KeyError``) or malformed
  ``output`` (``ValueError``/``TypeError``) becomes
  ``ToolError.custom("response_decoding", str(e))`` — Rust's
  ``.map_err(|e| ToolError::custom("response_decoding", e.to_string()))``.

- **chat_completion_output degrade-to-None**. Rust's
  ``result.chat_completion_output.and_then(|cco|
  serde_json::from_value::<ToolChatCompletionResponse>(cco).ok())``
  silently drops an unparseable cco. :func:`_decode_chat_completion_output`
  mirrors it: a non-object cco and a cco that fails
  :meth:`ToolChatCompletionResponse.from_dict` both return ``None``
  (``except Exception`` — :meth:`from_dict` can raise on a malformed
  sub-block, and the degrade arm must swallow any shape mismatch).

- **map_block McpBlock -> ContentBlock**. The wire block union
  (:class:`~minimax_code.tool_protocol.McpBlock` =
  ``TextBlock | ImageBlock | ResourceBlock``) maps one-to-one onto the
  runtime :class:`~minimax_code.tool_runtime.ContentBlock` variants.
  ``Image`` loses no data: the wire ``ImageBlock`` carries only
  ``mime_type`` + ``data``, so the runtime ``ContentBlock.Image`` is
  built with the four optional fields defaulted
  (``media_id``/``filename``/``path`` = ``None``, ``metadata`` = ``{}``)
  — identical to Rust's ``media_id: None, filename: None, path: None,
  metadata: Default::default()``.

- **Mcp{blocks} re-serialisation**. Rust re-serialises the runtime
  ``Vec<ContentBlock>`` via ``serde_json::to_value`` (infallible for
  valid variants, collapsed via ``unwrap_or(Value::Null)`` to keep the
  function total). Python's :meth:`ContentBlock.to_dict` is likewise
  infallible for the three variants :func:`_map_block` produces, so the
  landing builds ``{"blocks": [b.to_dict() for b in runtime_blocks]}``
  with no fallback — the ``unwrap_or`` arm has no Python equivalent
  because the comprehension cannot fail for the variants produced here.

Pure-function shape
-------------------

All four functions are **sync** (no I/O, no ``await``) — the
decode/encode seams are CPU-only transforms over already-deserialised
wire values. :func:`decode_call_result` returns
``TypedToolOutput | ToolError`` — the Python counterpart to Rust's
``Result<TypedToolOutput, ToolError>`` — and never raises on a malformed
body (it surfaces the failure as the ``ToolError`` arm).

Consumes R65/R82-R106 (tool_protocol wire types: ``ToolOutputWire`` /
``McpBlock`` / ``ToolCallResult`` / ``ToolCallProgressFrame``) +
R107-R114 (tool_runtime types: ``ContentBlock`` / ``ToolProgress`` /
``TypedToolOutput`` / ``ToolError`` / ``ToolChatCompletionResponse``).
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from typing import Any

from minimax_code.tool_protocol import (
    WORKSPACE_UNAVAILABLE_SUBCODE,
    BehaviorVersionUnsupported,
    Cancelled,
    Custom,
    Execution,
    ImageBlock,
    Internal,
    InvalidArguments,
    Json,
    JsonRpcError,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcResponse,
    Mcp,
    McpBlock,
    PayloadTooLarge,
    PermissionDenied,
    RenderLimited,
    ResourceBlock,
    ResponseError,
    ResponseResult,
    SessionMismatch,
    TerminalError,
    Text,
    TextBlock,
    Timeout,
    ToolCallId,
    ToolCallProgressFrame,
    ToolCallResult,
    ToolErrorWire,
    ToolId,
    ToolNotFound,
    ToolOutputWire,
    TransportClosed,
    UnsupportedProtocolVersion,
)
from minimax_code.tool_protocol.error_wire import from_wire as error_wire_from_wire
from minimax_code.tool_protocol.output_wire import from_wire as output_from_wire
from minimax_code.tool_runtime import (
    ContentBlock,
    ToolChatCompletionResponse,
    ToolError,
    ToolErrorKind,
    ToolProgress,
    TypedToolOutput,
)

__all__ = [
    "ConnectionClient",
    "decode_call_result",
    "error_from_envelope",
    "is_workspace_unavailable",
    "output_to_value",
    "progress_from_frame",
    "tool_error_from_wire",
]


# -----------------------------------------------------------------------
# ConnectionClient — object-safe connection contract (layer 1).
# -----------------------------------------------------------------------


class ConnectionClient(abc.ABC):
    """Object-safe contract for a connected remote endpoint (layer 1).

    Fusion of grok-build's ``ConnectionClient`` trait. Concrete
    implementations supply the wire transport — the Rust SDK uses
    ``tokio_tungstenite``; tests use channel-backed mocks. The crate stays
    free of any runtime/transport dependency so callers can pick their own.

    The trait is the thin contract a downstream WebSocket SDK (or an
    in-test channel-backed mock) implements; the four layer-4 decode
    helpers landed in R120/R121 sit *above* it — they re-encode what a
    :class:`JsonRpcResponse` carries into runtime types — while layers 2-3
    (``RemoteToolProxy`` / ``RemoteTransport`` / ``dispatch_via_connection``
    + ``RequestStream``) will sit *below* it, driving a connection through
    this contract. R122 lands the contract itself; the layers that consume
    it follow in later rounds.

    Mapping decisions (Rust trait -> Python ABC)
    --------------------------------------------

    - **``Send + Sync + std::fmt::Debug`` bounds -> dropped.** The Rust
      trait is ``object-safe`` and carries the three standard bounds:
      ``Send`` + ``Sync`` (so the trait object can cross an ``await`` and
      live behind an ``Arc<dyn ConnectionClient>``) plus ``Debug``. The
      Python landing drops ``Send``/``Sync`` — Python's GIL makes every
      object shareable across the single interpreted thread, so the bounds
      are vacuous — and treats ``Debug`` as the default ``__repr__`` every
      object already has. The ABC therefore enforces nothing but the three
      async method signatures.

    - **``#[async_trait]`` -> ``async def`` abstract methods.** Each Rust
      method is an ``async fn``; each Python method is an ``async def``
      decorated with :func:`abc.abstractmethod`. All three are coroutine
      functions (a clean test point — see ``test_*_is_coroutine_function``).

    - **``BoxStream<'static, ToolCallProgressFrame>`` ->
      :class:`~collections.abc.AsyncIterator`.** Rust's
      ``subscribe_progress`` is an ``async fn`` whose future resolves to a
      boxed stream; the Python landing keeps the same two-layer shape —
      ``subscribe_progress`` is an ``async def`` whose coroutine, when
      awaited, yields an :class:`~collections.abc.AsyncIterator`. Callers
      write ``stream = await client.subscribe_progress(id)`` then
      ``async for frame in stream`` — a faithful collapse of Rust's
      "async fn -> Stream" into Python's native async-iteration protocol,
      and the exact shape layer 3's ``dispatch_via_connection`` will
      consume when it interleaves progress frames with the terminal
      response.

    - **``Result<T, ToolError>`` -> ``T | ToolError``.** ``request``
      returns ``JsonRpcResponse | ToolError``; ``notify`` returns
      ``None | ToolError``. A method-level error outcome (the response
      envelope carries a :class:`ResponseError`) is NOT a
      :class:`ToolError` here — it is a successful
      :class:`JsonRpcResponse` wrapping the error, decoded later by
      :func:`_terminal_from_response`. Only transport-level failures
      (write failed, connection closed before the response arrived)
      surface as a :class:`ToolError`.

    Implementations are expected to (mirroring the Rust trait doc):

    - correlate request/response pairs by :class:`JsonRpcId`;
    - deliver progress notifications matching ``tool_call_id`` to whichever
      subscriber registered for them;
    - surface transport-level disconnects as a :class:`ToolError`
      (network_error).
    """

    @abc.abstractmethod
    async def request(self, request: JsonRpcRequest) -> JsonRpcResponse | ToolError:
        """Send a JSON-RPC request and await the matching response.

        Errors signal a transport-level failure (write failed, connection
        closed before the response arrived); a successful return carries
        the response envelope verbatim, including method-level error
        outcomes — those surface as a :class:`JsonRpcResponse` wrapping a
        :class:`ResponseError`, NOT as a :class:`ToolError`. The
        R121 :func:`_terminal_from_response` decode handles that envelope
        distinction downstream.
        """

    @abc.abstractmethod
    async def subscribe_progress(
        self, tool_call_id: ToolCallId
    ) -> AsyncIterator[ToolCallProgressFrame]:
        """Subscribe to progress notifications for ``tool_call_id``.

        Awaits to an :class:`~collections.abc.AsyncIterator` that yields
        :class:`ToolCallProgressFrame` items. The iterator closes when the
        call's terminal frame arrives, when the connection drops, or when
        the caller stops iterating. Subscribers MUST be registered before
        the corresponding request is sent — otherwise progress frames that
        arrive before subscription is complete are lost.

        Mirrors Rust's ``async fn subscribe_progress -> BoxStream``:
        awaiting this coroutine resolves to the stream, exactly as Rust's
        future resolves to the boxed stream.
        """

    @abc.abstractmethod
    async def notify(self, notification: JsonRpcNotification) -> None | ToolError:
        """Send a one-way notification (no response expected).

        Useful for hook frames such as cancel. ``None`` signals the
        notification was written; a :class:`ToolError` signals a
        transport-level failure.
        """


# -----------------------------------------------------------------------
# progress_from_frame — ToolCallProgressFrame -> ToolProgress.
# -----------------------------------------------------------------------


def progress_from_frame(frame: ToolCallProgressFrame) -> ToolProgress:
    """Map a wire :class:`ToolCallProgressFrame` into a runtime :class:`ToolProgress`.

    Rust ``progress_from_frame`` lifts the frame's producer-defined
    ``kind`` into a ``Custom { subkind, payload }`` progress item so
    callers can dispatch on the producer-defined identifier without
    losing the body. The ``kind`` becomes the ``Custom`` subkind and the
    ``body`` becomes the opaque payload — a single-arg lift, no data loss.
    """
    return ToolProgress.Custom(frame.kind, frame.body)


# -----------------------------------------------------------------------
# output_to_value — ToolOutputWire -> JSON value (+ _map_block helper).
# -----------------------------------------------------------------------


def output_to_value(output: ToolOutputWire) -> Any:
    """Project a wire :class:`ToolOutputWire` into a JSON value (Rust ``output_to_value``).

    Three shapes collapse to one runtime type:

    - ``Text`` becomes a bare JSON string (``Value::String(s)``);
    - ``Json`` is forwarded verbatim (the opaque payload passes through);
    - ``Mcp { blocks }`` is re-serialised as
      ``{"blocks": [ContentBlock, ...]}`` so the same downstream decoder
      used for in-process content blocks works without case-by-case
      adaptation.

    The wire blocks are first mapped through :func:`_map_block` into
    runtime :class:`ContentBlock` instances, then re-serialised via
    :meth:`ContentBlock.to_dict` — the Python counterpart to Rust's
    ``serde_json::to_value(&runtime_blocks)``.
    """
    if isinstance(output, Text):
        return output.text
    if isinstance(output, Json):
        return output.json
    if isinstance(output, Mcp):
        runtime_blocks = [_map_block(block) for block in output.blocks]
        return {"blocks": [block.to_dict() for block in runtime_blocks]}
    # Unreachable: ToolOutputWire is a closed union of Text/Json/Mcp.
    raise TypeError(f"unknown ToolOutputWire variant: {type(output).__name__}")


def _map_block(block: McpBlock) -> ContentBlock:
    """Map a wire :class:`McpBlock` into a runtime :class:`ContentBlock` (Rust ``map_block``).

    Private to this module (Rust ``fn map_block`` is module-private).
    One-to-one: ``TextBlock`` -> ``ContentBlock.Text``, ``ImageBlock`` ->
    ``ContentBlock.Image`` (with the four optional fields defaulted — the
    wire ``ImageBlock`` carries only ``mime_type`` + ``data``),
    ``ResourceBlock`` -> ``ContentBlock.Resource``.
    """
    if isinstance(block, TextBlock):
        return ContentBlock.Text(block.text)
    if isinstance(block, ImageBlock):
        return ContentBlock.Image(mime_type=block.mime_type, data=block.data)
    if isinstance(block, ResourceBlock):
        return ContentBlock.Resource(
            uri=block.uri, mime_type=block.mime_type, text=block.text
        )
    # Unreachable: McpBlock is a closed union of TextBlock/ImageBlock/ResourceBlock.
    raise TypeError(f"unknown McpBlock variant: {type(block).__name__}")


# -----------------------------------------------------------------------
# decode_call_result — tool_call_result body -> TypedToolOutput (+ helpers).
# -----------------------------------------------------------------------


def decode_call_result(
    tool_id: ToolId, value: Any
) -> TypedToolOutput | ToolError:
    """Decode a ``tool_call_result`` success body into a :class:`TypedToolOutput`.

    Rust ``decode_call_result``. A body carrying a ``tool_call_id`` is
    decoded strictly (``response_decoding`` on failure), reconstructing
    ``chat_completion_output`` (an unparseable cco degrades to ``None``)
    and re-projecting the typed ``output`` via :func:`output_to_value`
    before building the :class:`TypedToolOutput`. A bare body — e.g. a
    hub-local tool's raw output — passes through to
    :meth:`TypedToolOutput.from_value` unchanged.

    Never raises: a malformed body surfaces as the ``ToolError`` arm
    (keyed ``response_decoding``), mirroring Rust's
    ``Result<TypedToolOutput, ToolError>`` return.
    """
    # Rust `value.get("tool_call_id").is_none()` — a non-object value
    # (Rust non-Value::Object) also yields None -> bare-output passthrough.
    if not isinstance(value, dict) or "tool_call_id" not in value:
        return TypedToolOutput.from_value(tool_id, value)
    try:
        result = _decode_tool_call_result(value)
    except (KeyError, ValueError, TypeError) as exc:
        return ToolError.custom("response_decoding", str(exc))
    chat_completion_output = _decode_chat_completion_output(
        result.chat_completion_output
    )
    reprojected = output_to_value(result.output)
    return TypedToolOutput.from_value(tool_id, reprojected).with_chat_completion_output(
        chat_completion_output
    )


def _decode_tool_call_result(value: dict[str, Any]) -> ToolCallResult:
    """Strict reconstruction mirroring ``serde_json::from_value::<ToolCallResult>``.

    Private to this module. ``tool_call_id`` and ``output`` are required;
    ``output`` is decoded via the strict adjacent-tagged
    :func:`output_from_wire` (raises ``ValueError`` on an unknown/missing
    ``kind``). A missing key (``KeyError``) or malformed ``output``
    (``ValueError``/``TypeError``) propagates to :func:`decode_call_result`,
    which surfaces it as ``response_decoding``.
    """
    tool_call_id = value["tool_call_id"]
    output = output_from_wire(value["output"])
    return ToolCallResult(
        tool_call_id=tool_call_id,
        output=output,
        follow_ups=value.get("follow_ups", []),
        reminders=value.get("reminders", []),
        chat_completion_output=value.get("chat_completion_output"),
    )


def _decode_chat_completion_output(
    cco_raw: Any,
) -> ToolChatCompletionResponse | None:
    """Reconstruct a :class:`ToolChatCompletionResponse`, degrading to ``None``.

    Private to this module. Mirrors Rust's
    ``result.chat_completion_output.and_then(|cco|
    serde_json::from_value::<ToolChatCompletionResponse>(cco).ok())``:
    a ``None`` cco stays ``None``, a non-object cco (which serde cannot
    deserialise into a struct) becomes ``None``, and a cco whose
    :meth:`ToolChatCompletionResponse.from_dict` fails also becomes
    ``None`` (the degrade swallows any shape mismatch rather than failing
    the whole decode).
    """
    if not isinstance(cco_raw, dict):
        return None
    try:
        return ToolChatCompletionResponse.from_dict(cco_raw)
    except Exception:
        return None


# -----------------------------------------------------------------------
# tool_error_from_wire — ToolErrorWire (14 variants) -> ToolError.
# -----------------------------------------------------------------------


def tool_error_from_wire(wire: ToolErrorWire) -> ToolError:
    """Map a wire :class:`ToolErrorWire` back into a runtime :class:`ToolError`.

    Rust ``tool_error_from_wire``. The runtime error variants are the
    source-of-truth taxonomy; the wire form is a lossy projection onto stable
    codes for serialisation, so a few wire variants land on
    :meth:`ToolError.custom` keyed by their wire code rather than a dedicated
    runtime variant (``SessionMismatch`` / ``TransportClosed`` /
    ``UnsupportedProtocolVersion`` / ``PayloadTooLarge`` / ``Internal``).

    The 14-arm ``isinstance`` dispatch mirrors Rust's ``match wire { ... }``;
    the union is closed so the trailing ``raise TypeError`` is unreachable
    for a valid wire value (covered by the union-narrowing guarantee).
    """
    # InvalidArguments{message, details?} -> invalid_arguments + optional details.
    if isinstance(wire, InvalidArguments):
        e = ToolError.invalid_arguments(wire.message)
        return e.with_details(wire.details) if wire.details is not None else e
    # ToolNotFound{tool_id} -> not_found(tool_id, "tool not found: {tool_id}").
    if isinstance(wire, ToolNotFound):
        return ToolError.not_found(wire.tool_id, f"tool not found: {wire.tool_id}")
    # PermissionDenied{reason} -> permission_denied(reason).
    if isinstance(wire, PermissionDenied):
        return ToolError.permission_denied(wire.reason)
    # Timeout{tool_id, elapsed_ms} -> new(TIMEOUT, "timed out after {ms}ms")
    #   + details {"tool_id", "elapsed_ms"}.
    if isinstance(wire, Timeout):
        return ToolError.new(
            ToolErrorKind.TIMEOUT, f"timed out after {wire.elapsed_ms}ms"
        ).with_details({"tool_id": str(wire.tool_id), "elapsed_ms": wire.elapsed_ms})
    # Cancelled{tool_id} -> cancelled(tool_id, "cancelled").
    if isinstance(wire, Cancelled):
        return ToolError.cancelled(wire.tool_id, "cancelled")
    # Execution{tool_id, message} -> execution(tool_id, message).
    if isinstance(wire, Execution):
        return ToolError.execution(wire.tool_id, wire.message)
    # BehaviorVersionUnsupported{tool_id, requested} ->
    #   new(BEHAVIOR_VERSION_UNSUPPORTED, "behavior version {requested} not
    #   supported") + details {"tool_id", "requested"}.
    if isinstance(wire, BehaviorVersionUnsupported):
        return ToolError.new(
            ToolErrorKind.BEHAVIOR_VERSION_UNSUPPORTED,
            f"behavior version {wire.requested} not supported",
        ).with_details({"tool_id": str(wire.tool_id), "requested": wire.requested})
    # RenderLimited{tool_id, card_id?, reason} -> new(RENDER_LIMITED, reason)
    #   + details {"tool_id", "card_id"} (card_id None -> null in JSON, as
    #   Rust's json! macro emits Value::Null for Option::None).
    if isinstance(wire, RenderLimited):
        return ToolError.new(ToolErrorKind.RENDER_LIMITED, wire.reason).with_details(
            {"tool_id": str(wire.tool_id), "card_id": wire.card_id}
        )
    # TerminalError{tool_id, message} -> terminal_error(tool_id, message).
    if isinstance(wire, TerminalError):
        return ToolError.terminal_error(wire.tool_id, wire.message)
    # Custom{subcode, message, details?} -> custom(subcode, message) + opt details.
    if isinstance(wire, Custom):
        e = ToolError.custom(wire.subcode, wire.message)
        return e.with_details(wire.details) if wire.details is not None else e
    # SessionMismatch (unit) -> custom("session_mismatch", "session mismatch").
    if isinstance(wire, SessionMismatch):
        return ToolError.custom("session_mismatch", "session mismatch")
    # TransportClosed{tool_id} -> network_error("transport closed for {tool_id}").
    if isinstance(wire, TransportClosed):
        return ToolError.network_error(f"transport closed for {wire.tool_id}")
    # UnsupportedProtocolVersion{supported} -> custom("unsupported_protocol_version",
    #   "supported versions: {supported:?}") (Rust Vec<String> Debug format
    #   mirrors Python list repr).
    if isinstance(wire, UnsupportedProtocolVersion):
        return ToolError.custom(
            "unsupported_protocol_version", f"supported versions: {wire.supported!r}"
        )
    # PayloadTooLarge{bytes, limit} -> custom("payload_too_large",
    #   "payload {bytes} bytes exceeds limit {limit}").
    if isinstance(wire, PayloadTooLarge):
        return ToolError.custom(
            "payload_too_large",
            f"payload {wire.bytes} bytes exceeds limit {wire.limit}",
        )
    # Internal{request_id?, detail?} -> custom("internal_error", detail or
    #   fallback) + optional details {"code": "internal_error", "request_id"}.
    #   with_details replaces custom's {"code": ...} so the new details
    #   re-carries "code" (mirrors Rust's explicit re-install).
    if isinstance(wire, Internal):
        detail = wire.detail if wire.detail is not None else "internal router error"
        e = ToolError.custom("internal_error", detail)
        if wire.request_id is not None:
            return e.with_details(
                {"code": "internal_error", "request_id": str(wire.request_id)}
            )
        return e
    # Unreachable: ToolErrorWire is a closed union of the 14 variants above.
    raise TypeError(f"unknown ToolErrorWire variant: {type(wire).__name__}")


# -----------------------------------------------------------------------
# error_from_envelope — JsonRpcError -> ToolError (wire path vs fallback).
# -----------------------------------------------------------------------


def error_from_envelope(err: JsonRpcError) -> ToolError:
    """Decode a JSON-RPC error envelope into a :class:`ToolError`.

    Rust ``error_from_envelope``. The envelope's ``data`` field is expected to
    carry a serialised :class:`ToolErrorWire` when available; falls back to a
    :meth:`ToolError.custom` keyed by ``jsonrpc_{code}`` (the numeric envelope
    code) when the data shape is unknown or absent.

    Rust's ``if let Ok(wire) = serde_json::from_value::<ToolErrorWire>(data)``
    succeeds only for an object carrying a known ``code`` tag; a non-object
    data or an unknown code both fall through. Python mirrors it: a non-dict
    data skips the wire arm, and :func:`error_wire_from_wire` raising on an
    unknown code (``ValueError``) or a missing ``code`` key (``KeyError``) is
    swallowed into the fallback. The fallback's ``with_details(data)``
    replaces the ``{"code": "jsonrpc_{code}"}`` object :meth:`ToolError.custom`
    installed with the original data verbatim — mirroring Rust's
    ``e = e.with_details(data)`` reassignment.
    """
    data = err.data
    if isinstance(data, dict):
        try:
            wire = error_wire_from_wire(data)
        except (KeyError, ValueError, TypeError):
            wire = None
        if wire is not None:
            return tool_error_from_wire(wire)
    # Fallback: custom keyed by the numeric envelope code; the original data
    # (if any) replaces the {"code": ...} details ToolError.custom installed.
    e = ToolError.custom(f"jsonrpc_{err.code}", err.message)
    if data is not None:
        return e.with_details(data)
    return e


# -----------------------------------------------------------------------
# is_workspace_unavailable — recognise the hub's workspace-gone error.
# -----------------------------------------------------------------------


def is_workspace_unavailable(err: ToolError) -> bool:
    """Recognise the hub's ``workspace_unavailable`` error.

    Rust ``is_workspace_unavailable``. Keys on ``details["code"]`` — the field
    that survives :meth:`ToolError.custom` + :meth:`with_details` — not the
    numeric envelope code or the wire ``Custom.subcode``. A
    non-:attr:`ToolErrorKind.CUSTOM` kind, a non-dict ``details``, or a
    non-string ``code`` all return ``False``; only a ``Custom`` error whose
    ``details["code"]`` is exactly :data:`WORKSPACE_UNAVAILABLE_SUBCODE`
    matches.
    """
    if err.kind != ToolErrorKind.CUSTOM:
        return False
    details = err.details
    if not isinstance(details, dict):
        return False
    code = details.get("code")
    return isinstance(code, str) and code == WORKSPACE_UNAVAILABLE_SUBCODE


# -----------------------------------------------------------------------
# _terminal_from_response — JsonRpcResponse -> TypedToolOutput | ToolError.
# (Rust private ``terminal_from_response``; closes the R120/R121 loop.)
# -----------------------------------------------------------------------


def _terminal_from_response(
    tool_id: ToolId, resp: JsonRpcResponse
) -> TypedToolOutput | ToolError:
    """Decode a response envelope into the terminal ``TypedToolOutput | ToolError``.

    Rust private ``terminal_from_response``. A thin two-arm dispatch over
    :attr:`JsonRpcResponse.outcome`: the success arm reuses R120's
    :func:`decode_call_result`, the error arm decodes via
    :func:`error_from_envelope`. This is the seam layer 3's
    ``dispatch_via_connection`` / ``RequestStream`` will call to turn the
    terminal JSON-RPC frame into the runtime's typed terminal — landing it now
    (alongside :func:`error_from_envelope`) closes the internal loop so later
    rounds assemble the connection machinery on a complete decode surface.

    Private (Rust ``fn terminal_from_response`` has no ``pub``); the leading
    underscore follows R120's ``_map_block`` / ``_decode_*`` convention for
    module-private decode helpers.
    """
    outcome = resp.outcome
    if isinstance(outcome, ResponseResult):
        return decode_call_result(tool_id, outcome.value)
    if isinstance(outcome, ResponseError):
        # Decode the JSON-RPC error envelope into a ToolError.
        return error_from_envelope(outcome.error)
    # Unreachable for a valid ResponseOutcome (ResponseResult | ResponseError);
    # the closed-union TypeError mirrors R120's ``_map_block`` unreachable arm.
    raise TypeError(f"unknown response outcome: {type(outcome).__name__}")
