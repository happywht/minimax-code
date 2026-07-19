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
imports :class:`ContentBlock` one-way at module level (no cycle). R111
lands the fifth leaf: :mod:`streaming` (the canonical partial-result
streaming contract — :class:`PartialResultPayload` with
``deny_unknown_fields`` strict decode + :func:`stream_chunk` UTF-8-safe
delta slicing + ``DEFAULT_MAX_DELTA_BYTES``), a leaf that consumes
:class:`StreamingSpec` (tool_protocol) and ``ToolProgress`` (tool) one-way
with no cycle.
R112 lands the sixth leaf: :mod:`search` (the backend-agnostic tool
search interface — :class:`ToolSearchResult` / :class:`SearchSnapshot` /
:class:`ServerSummary` dataclasses + :class:`ToolSearchIndex`
``runtime_checkable`` Protocol + :class:`ToolIndex` the ``Arc<dyn>``
wrapper), a stdlib-only leaf that touches no other tool_runtime module.
R113 lands the seventh leaf: :mod:`dispatch` (the object-safe tool
dispatch interface — :class:`ToolDispatch` :class:`abc.ABC` with abstract
:meth:`~ToolDispatch.call` streaming surface + concrete-default
:meth:`~ToolDispatch.call_terminal` stream drain), a consumer of
:mod:`context` (R108) + :mod:`error` (R107) + the :mod:`tool` streaming
primitives (R109), and the unblocker for the ``xai-computer-hub-core``
crate.
R114 lands the eighth and final leaf: :mod:`notification` (the typed
execution-visibility messages — 19 payload structs across the bash /
file / plan-mode / LSP / scheduled-task / monitor families +
:class:`TaskKind` StrEnum + :class:`TaskSnapshot` + the
:class:`ToolNotification` ``tag = "type"`` tagged union +
:class:`ToolNotificationHandle` the ``mpsc::UnboundedSender`` wrapper),
the crate's largest leaf (24 re-exported symbols), built on
:class:`asyncio.Queue` (the executor-neutral ``mpsc`` equivalent) and
dataclass inheritance (the ``#[serde(flatten)]`` equivalent).
With R114 the crate's eight ``src`` modules — ``context``, ``dispatch``,
``error``, ``notification``, ``render``, ``search``, ``streaming``,
``tool`` — are all landed; this barrel IS the ``lib.rs`` re-export
surface, so no separate ``lib`` round is needed.

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
from minimax_code.tool_runtime.dispatch import ToolDispatch
from minimax_code.tool_runtime.error import ToolError, ToolErrorKind
from minimax_code.tool_runtime.notification import (
    BashExecutionBackgrounded,
    BashExecutionComplete,
    BashExecutionFailed,
    BashExecutionTimeout,
    BashNotificationBase,
    BashOutputChunk,
    FileRead,
    FileWritten,
    LspServerCrashed,
    LspServerFailed,
    LspServerReady,
    LspServerRetrying,
    LspServerStarting,
    MonitorEvent,
    PlanModeEntered,
    PlanModeExited,
    ScheduledTaskCreated,
    ScheduledTaskFired,
    ScheduledTaskRemoved,
    TaskKind,
    TaskSnapshot,
    ToolNotification,
    ToolNotificationHandle,
    UserQuestionAsked,
)
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
from minimax_code.tool_runtime.search import (
    SearchSnapshot,
    ServerSummary,
    ToolIndex,
    ToolSearchIndex,
    ToolSearchResult,
)
from minimax_code.tool_runtime.streaming import (
    DEFAULT_MAX_DELTA_BYTES,
    PartialResultPayload,
    stream_chunk,
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
    "BashExecutionBackgrounded",
    "BashExecutionComplete",
    "BashExecutionFailed",
    "BashExecutionTimeout",
    "BashNotificationBase",
    "BashOutputChunk",
    "BehaviorVersion",
    "Cancellation",
    "ContentBlock",
    "Cwd",
    "DEFAULT_MAX_DELTA_BYTES",
    "FileRead",
    "FileWritten",
    "ListToolsContext",
    "LspServerCrashed",
    "LspServerFailed",
    "LspServerReady",
    "LspServerRetrying",
    "LspServerStarting",
    "ModelOutputExtractor",
    "MonitorEvent",
    "PartialResultPayload",
    "PlanModeEntered",
    "PlanModeExited",
    "ScheduledTaskCreated",
    "ScheduledTaskFired",
    "ScheduledTaskRemoved",
    "SearchSnapshot",
    "ServerSummary",
    "SessionContext",
    "TaskKind",
    "TaskSnapshot",
    "Tool",
    "ToolCallContext",
    "ToolChatCompletion",
    "ToolChatCompletionResponse",
    "ToolCodeExecutionResult",
    "ToolDispatch",
    "ToolDyn",
    "ToolError",
    "ToolErrorKind",
    "ToolFamily",
    "ToolIndex",
    "ToolNotification",
    "ToolNotificationHandle",
    "ToolOutput",
    "ToolProgress",
    "ToolSearchIndex",
    "ToolSearchResult",
    "ToolStream",
    "ToolStreamError",
    "ToolStreamItem",
    "ToolVariant",
    "TraceContext",
    "TypedExtensions",
    "TypedToolOutput",
    "UserQuestionAsked",
    "WorkspaceBindMetadata",
    "WorkspaceViewerContext",
    "extract_content_blocks",
    "extractor_for",
    "stream_chunk",
    "terminal_only",
    "with_progress",
]
