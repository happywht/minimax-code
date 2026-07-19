"""Tool runtime — tool-execution type contract layer (R107+).

Fusion of grok-build's ``xai-tool-runtime`` crate. Where
:mod:`minimax_code.tool_protocol` is the *wire shape* (frames, ids, error
wire types that travel in JSON-RPC), ``tool_runtime`` is the *execution
contract*: the types a tool author codes against and the runtime that
drives them.

The crate's ``lib.rs`` re-exports eight modules — ``context``, ``dispatch``,
``error``, ``notification``, ``render``, ``search``, ``streaming``,
``tool`` — plus a handful of protocol re-exports
(``StreamingSpec`` / ``ToolCallId`` / ``ToolCapabilities`` / ``ToolId`` /
``ToolScope``). R107 lands the foundational leaf: :mod:`error`
(:class:`ToolError` + :class:`ToolErrorKind`), first because every other
module's signatures reference :class:`ToolError`. R108 lands the second
leaf: :mod:`context` (:class:`TypedExtensions` + the per-call / per-turn
contexts + the newtype concept markers + the ``session.bind`` wire
metadata), second because :mod:`tool` and :mod:`dispatch` thread a
:class:`ToolCallContext` through their signatures. Subsequent rounds
migrate the remaining modules; the barrel's ``__all__`` grows as each
lands.

R23 fused this crate's *concurrency model* (the structured-concurrency /
cancellation primitives the agent core uses) but **not** its *type
contract* — :class:`ToolError`, ``ToolDispatch``, ``ToolCallContext``,
``ToolStream``, ``TypedToolOutput``, ``terminal_only`` had no Python
equivalents. R107 onward fills that gap; it is also the unblocker for the
``xai-computer-hub-core`` crate (whose ``inner.rs`` consumes
``xai_tool_runtime::{ToolCallContext, ToolDispatch, ToolError, ToolStream,
TypedToolOutput, terminal_only}``).
"""

from minimax_code.tool_runtime.context import (
    BehaviorVersion,
    Cancellation,
    Cwd,
    ListToolsContext,
    SessionContext,
    ToolCallContext,
    TraceContext,
    TypedExtensions,
    WorkspaceBindMetadata,
    WorkspaceViewerContext,
)
from minimax_code.tool_runtime.error import ToolError, ToolErrorKind

__all__ = [
    "BehaviorVersion",
    "Cancellation",
    "Cwd",
    "ListToolsContext",
    "SessionContext",
    "ToolCallContext",
    "ToolError",
    "ToolErrorKind",
    "TraceContext",
    "TypedExtensions",
    "WorkspaceBindMetadata",
    "WorkspaceViewerContext",
]
