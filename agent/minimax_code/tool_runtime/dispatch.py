"""Object-safe tool dispatch interface (R113).

Fusion of grok-build's ``xai-tool-runtime/src/dispatch.rs``. A dispatcher
resolves a ``ToolId`` + args into a :type:`ToolStream` of
:class:`TypedToolOutput`. The :meth:`ToolDispatch.call` surface streams
Progress / Terminal items; the :meth:`ToolDispatch.call_terminal` default
drains the stream and returns only the Terminal payload.

Why dispatch lands seventh
--------------------------

R107 landed the error leaf, R108 the context leaf, R109 the tool leaf
(streaming primitives + :class:`TypedToolOutput`), R110 the render leaf,
R111 the streaming leaf, R112 the search leaf. This round lands the
*dispatch* leaf. It is a consumer of three prior leaves —
:class:`ToolCallContext` (R108), :class:`ToolError` (R107), and the
streaming primitives :type:`ToolStream` / :class:`ToolStreamItem` /
:class:`TypedToolOutput` (R109) — and the unblocker for the
``xai-computer-hub-core`` crate, whose ``inner.rs`` consumes
``xai_tool_runtime::ToolDispatch`` to drive the per-call execution path.

trait ToolDispatch -> abc.ABC
-----------------------------

Rust's ``#[async_trait] pub trait ToolDispatch: Send + Sync`` declares one
abstract method (``call``) plus one concrete default (``call_terminal``).
:class:`abc.ABC` is the faithful landing: it carries an
:func:`abc.abstractmethod`-marked ``call`` (subclasses MUST implement it
to be instantiable) alongside a concrete ``call_terminal`` body (the
default drain, overridable). A :class:`typing.Protocol` would not enforce
the abstract ``call`` and has no clean place for a shared default body, so
the ABC is the better fit — unlike the *structural* contracts
(``Tool`` / ``ToolDyn`` / ``ToolFamily`` in R109, ``ToolSearchIndex`` in
R112) which stay Protocols because they are duck-typed seams with no
default body to share.

The ``Send + Sync`` object-safety bounds (which let the trait live behind
an ``Arc<dyn ToolDispatch>`` shared across tasks) have no Python
equivalent under the GIL — Python objects are already reference-shared,
so a dispatcher instance is shared by alias exactly as Rust's ``Arc``
shares it.

Result<T, E> by value, not raised
---------------------------------

:class:`ToolError` does NOT subclass :exc:`Exception` (R107): it is
returned, never raised. So :meth:`ToolDispatch.call_terminal` returns
``TypedToolOutput | ToolError`` by value — the caller distinguishes with
``isinstance(result, ToolError)`` — rather than ``raise``-ing the error
path. This matches :meth:`ToolStreamItem.is_error` (a Terminal carrying a
:class:`ToolError`) and Rust's ``Result<TypedToolOutput, ToolError>``: the
two outcomes share a return slot, and the discriminator is the type of
the payload.
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from typing import Any

from minimax_code.tool_protocol.ids import ToolId
from minimax_code.tool_runtime.context import ToolCallContext
from minimax_code.tool_runtime.error import ToolError
from minimax_code.tool_runtime.tool import (
    ToolStreamItem,
    TypedToolOutput,
)

__all__ = ["ToolDispatch"]


class ToolDispatch(abc.ABC):
    """Object-safe tool dispatch interface (Rust ``trait ToolDispatch``).

    A dispatcher resolves a ``ToolId`` + args into a streaming output of
    :class:`TypedToolOutput`. :meth:`call` is the streaming surface
    (Progress + Terminal items); :meth:`call_terminal` is the convenience
    drain that returns only the Terminal payload.

    Subclasses MUST implement :meth:`call`. :meth:`call_terminal` ships a
    default body (drain + short-circuit on the first Terminal) that
    subclasses MAY override (e.g. to enforce a per-call timeout or to wire
    cancellation).
    """

    @abc.abstractmethod
    async def call(
        self,
        tool_id: ToolId,
        args: Any,
        ctx: ToolCallContext,
    ) -> AsyncIterator[ToolStreamItem[TypedToolOutput]]:
        """Resolve ``tool_id`` into a streaming output (Rust ``call``).

        Returns a :type:`ToolStream` — an async iterator of
        :class:`ToolStreamItem` items. Implementations yield
        :meth:`ToolStreamItem.Progress` frames as work proceeds and end
        with exactly one :meth:`ToolStreamItem.Terminal` carrying
        :class:`TypedToolOutput` on success or :class:`ToolError` on
        failure.

        ``self.call(...).await`` (Rust) becomes ``await self.call(...)``
        here: the coroutine resolves to the async iterator, which the
        caller then ``async for``-drives. An implementation therefore
        ``return``-s an async iterator (e.g. ``return terminal_only(x)``)
        rather than ``yield``-ing directly — matching Rust's
        ``async fn call() -> ToolStream`` shape (await yields the stream,
        then ``.next().await`` pulls items).
        """
        ...

    async def call_terminal(
        self,
        tool_id: ToolId,
        args: Any,
        ctx: ToolCallContext,
    ) -> TypedToolOutput | ToolError:
        """Drain the stream and return only the Terminal result (Rust default).

        Default impl: pull items off the stream :meth:`call` produced,
        skip every :meth:`ToolStreamItem.Progress` frame (``continue`` in
        Rust), and return the first
        :meth:`~ToolStreamItem.is_terminal` item's
        :attr:`~ToolStreamItem.terminal` payload — :class:`TypedToolOutput`
        on ``Ok``, :class:`ToolError` on ``Err``. A stream that ends
        without a Terminal item is a protocol violation (the dispatcher
        promised exactly one terminal) and is surfaced as
        :meth:`ToolError.custom` ``"stream_no_terminal"``.

        Mirrors the Rust default body verbatim::

            let mut stream = self.call(tool_id, args, ctx).await;
            while let Some(item) = stream.next().await {
                match item {
                    ToolStreamItem::Progress(_) => continue,
                    ToolStreamItem::Terminal(result) => return result,
                }
            }
            Err(ToolError::custom(
                "stream_no_terminal",
                "dispatch stream ended without a terminal item",
            ))
        """
        stream = await self.call(tool_id, args, ctx)
        async for item in stream:
            if item.is_terminal():
                return item.terminal
        return ToolError.custom(
            "stream_no_terminal",
            "dispatch stream ended without a terminal item",
        )
