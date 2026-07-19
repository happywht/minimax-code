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
:class:`ToolCallContext` through their signatures. R109 lands the third
leaf: :mod:`tool` (the ``Tool`` / ``ToolDyn`` / ``ToolFamily`` protocols +
the streaming primitives ``ToolStream`` / ``ToolStreamItem`` /
``ToolProgress`` / ``ContentBlock`` / ``terminal_only`` /
``with_progress`` + :class:`TypedToolOutput`), third because it consumes
both prior leaves. ``tool`` and ``render`` reference each other in the
crate, so they split across two rounds: R109's :mod:`tool` imports
:mod:`render` only under ``TYPE_CHECKING`` (plus one function-local lazy
import in :meth:`TypedToolOutput.from_value`); R110's :mod:`render` then
imports :class:`ContentBlock` one-way at module level (no cycle).
Subsequent rounds migrate the remaining modules; the barrel's ``__all__``
grows as each lands.

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
from minimax_code.tool_runtime.render import (
    ModelOutputExtractor,
    ToolChatCompletion,
    ToolChatCompletionResponse,
    ToolCodeExecutionResult,
    ToolOutput,
    ToolStreamError,
    extract_content_blocks,
    extractor_for,
)
from minimax_code.tool_runtime.tool import (
    ArcTool,
    ArcToolFamily,
    ContentBlock,
    Tool,
    ToolDyn,
    ToolFamily,
    ToolProgress,
    ToolStream,
    ToolStreamItem,
    ToolVariant,
    TypedToolOutput,
    terminal_only,
    with_progress,
)

__all__ = [
    "ArcTool",
    "ArcToolFamily",
    "BehaviorVersion",
    "Cancellation",
    "ContentBlock",
    "Cwd",
    "ListToolsContext",
    "ModelOutputExtractor",
    "SessionContext",
    "Tool",
    "ToolCallContext",
    "ToolChatCompletion",
    "ToolChatCompletionResponse",
    "ToolCodeExecutionResult",
    "ToolDyn",
    "ToolError",
    "ToolErrorKind",
    "ToolFamily",
    "ToolOutput",
    "ToolProgress",
    "ToolStream",
    "ToolStreamError",
    "ToolStreamItem",
    "ToolVariant",
    "TraceContext",
    "TypedExtensions",
    "TypedToolOutput",
    "WorkspaceBindMetadata",
    "WorkspaceViewerContext",
    "extract_content_blocks",
    "extractor_for",
    "terminal_only",
    "with_progress",
]
