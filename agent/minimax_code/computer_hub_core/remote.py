"""Remote-dispatch wire decode/encode helpers (R120, layer 4).

Fusion of grok-build's ``xai-computer-hub-core/src/remote.rs`` — the
crate's sixth and final leaf, the connection-forwarding transport. The
Rust module is 541 lines spanning four conceptual layers; R120 lands
**layer 4** (the wire decode/encode pure functions) and defers layers
1-3 to later rounds (see "Why layer 4 lands first" below).

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

1. ``ConnectionClient`` trait (later) — the object-safe connection
   abstraction (``request`` / ``subscribe_progress`` / ``notify``). Maps
   to an :class:`abc.ABC` later.
2. ``RemoteToolProxy`` + ``RemoteTransport`` (later) — the
   :class:`~minimax_code.computer_hub_core.ToolHandle` /
   :class:`~minimax_code.computer_hub_core.Transport` impls that drive
   the connection.
3. ``dispatch_via_connection`` + ``RequestStream`` (later) — the Rust
   ``Stream`` impl that interleaves progress frames with the terminal
   response; the most complex layer (``Stream`` trait -> Python async).
4. **Wire decode/encode pure functions (R120)** — the stateless seams
   that turn wire types into runtime types: :func:`progress_from_frame`,
   :func:`output_to_value`, :func:`decode_call_result`, plus the private
   :func:`_map_block`. Two further layer-4 seams
   (:func:`terminal_from_response`, :func:`error_from_envelope`,
   :func:`is_workspace_unavailable`, :func:`tool_error_from_wire`) land
   in a later round alongside the layers that call them.

Why layer 4 lands first
-----------------------

Layer 4's functions are **pure** (no trait object, no connection state,
no async stream) and **independent** (``decode_call_result`` depends only
on ``output_to_value`` + ``_map_block``; ``progress_from_frame`` is
standalone). They are also the layer layers 2-3 call into:
``dispatch_via_connection``'s ``RequestStream`` polls progress frames
through :func:`progress_from_frame` and the terminal response through
``terminal_from_response`` (itself a thin match wrapping
:func:`decode_call_result` and the later :func:`error_from_envelope`).
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

from typing import Any

from minimax_code.tool_protocol import (
    ImageBlock,
    Json,
    Mcp,
    McpBlock,
    ResourceBlock,
    Text,
    TextBlock,
    ToolCallProgressFrame,
    ToolCallResult,
    ToolId,
    ToolOutputWire,
)
from minimax_code.tool_protocol.output_wire import from_wire as output_from_wire
from minimax_code.tool_runtime import (
    ContentBlock,
    ToolChatCompletionResponse,
    ToolError,
    ToolProgress,
    TypedToolOutput,
)

__all__ = ["progress_from_frame", "output_to_value", "decode_call_result"]


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
