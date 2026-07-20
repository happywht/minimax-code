"""ToolServer runtime preamble — pure-logic type + helper layer (R175).

Fusion of grok-build's ``xai-computer-hub-sdk/src/server.rs`` preamble
(roughly lines 56-99). This is the first leaf of the ``server.rs`` port:
the small pure-logic / type-contract surface that sits ahead of the live
``HubConnection`` actor (``ToolServer`` / ``ToolServerBuilder`` /
``ToolServerInner`` / the per-session inbox dispatcher), which lands in
later leaves once a MiniMax-side consumer for the xAI socket protocol
exists.

R175 ports exactly the symbols that are pure and independently unit-tested
in the source:

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
that actor and travel with it, not with this preamble.
"""

from __future__ import annotations

import enum
import json
from collections.abc import Callable
from typing import Any

from minimax_code.computer_hub_sdk.error import ClientError, SerdeError
from minimax_code.tool_protocol.envelope import ResponseError, ResponseOutcome, ResponseResult
from minimax_code.tool_protocol.error_codes import string_for

__all__ = [
    "ReconnectSettledCallback",
    "SystemNotifyAck",
    "json_serialized_len",
    "system_notify_ack_from_outcome",
]


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
