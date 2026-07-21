"""``xai-grok-sampler`` platform package (R195+, crate migration in progress).

Fuses grok's ``xai-grok-sampler`` crate (actor-based sampling/inference layer:
HTTP streaming + retry, no shell coupling). The crate ships 12 modules; this
package is being filled leaf-by-leaf. Currently landed: ``chat_completion_leaves`` (R206, the first
``xai-grok-sampling-types`` ``types.rs`` slice: Role / ToolType / FinishReason /
ReasoningEffort wire-string enums + ImageUrl / ToolChoiceFunction /
ToolCallFunction / PromptTokensDetails / CompletionTokensDetails flat leaf
structs + the DEFAULT_REASONING_EFFORT constant) + ``chat_completion_mid`` (R207,
the second ``types.rs`` slice: ChatContentBlock 2-variant tagged union +
ChatMessageContent (renamed from grok ``MessageContent``) untagged union +
ToolChoice untagged union + ToolCallRequest struct + ChatUsage (renamed from
grok ``Usage``) struct, 11 middle-layer leaves consuming the R206 atomic
slice) + ``chat_request_message`` (R210, the fifth ``types.rs`` slice: the
ChatRequestMessage struct -- ``role`` (required) + ``content`` (required) +
``name`` / ``tool_calls`` / ``tool_call_id`` / ``model_id`` / ``reasoning_content``
(all optional), the assistant/user/tool message body carrying 5 grok constructors
+ ``is_system_message`` / ``text_content`` read-only helpers +
``with_text_content`` / ``with_appended_text`` copy-on-work mutators, consuming the
R206 ``Role`` + R207 ``ChatMessageContent`` / ``ToolCallRequest`` atomics) +
``serde_helpers`` (R211, the ``serde_helpers.rs`` leaf: the single
``deserialize_with`` hook ``empty_string_as_none`` consumed by two
``Option<String>`` fingerprint fields -- ``types.rs`` ``system_fingerprint`` on
the ChatCompletion response + ``conversation.rs`` ``model_fingerprint``; a pure
value-level normalizer ``""`` -> ``None``, zero dependency) +
``chat_truncate`` (R212, the ``types.rs`` free-function leaf: the
``chat_truncate_for_prompt`` algorithm that counts how many leading chat
messages to keep so the slice closes after the ``(target + 1)``-th user
prompt, consuming the R206 ``Role`` + R210 ``ChatRequestMessage``; a pure
algorithm over an in-memory sequence, zero dependency) +
``reasoning_effort_meta`` (R213, the ``types.rs`` reasoning-effort meta
read/write subsystem: 3 wire constants + the canonical-effort token parser
(accepting the ``"max"`` CLI/UX alias of ``Xhigh``) + the
``supportsReasoningEffort`` / ``reasoningEffort`` singular readers + the
``reasoningEfforts`` per-model menu reader/writer with the untagged
Bare-string-vs-Full-table option shape + skip-invalid forward-compat,
consuming the R206 ``ReasoningEffort``; a pure value-level subsystem over
``dict`` / ``list``, zero dependency) +
``api_backend`` (R214, the ``types.rs`` API-backend selector leaf: the
3-variant ``snake_case`` wire-string :class:`ApiBackend` enum
(ChatCompletions / Responses / Anthropic Messages) + the
:meth:`supports_native_schema` decision method (the Messages API does not
enforce a response JSON schema natively, so structured output there routes
through the StructuredOutput tool) + the :data:`DEFAULT_API_BACKEND`
constant, the ``#[serde(default)]`` ``api_backend`` field of the later
:class:`SamplingConfig`; zero dependency, correcting the earlier "depends
on crate::rs" deferral -- ``ApiBackend`` itself has no ``crate::rs`` /
``indexmap`` / ``NonZeroU64`` dep, only :class:`SamplingConfig` does) +
``config`` (R195,
pure-type subset) + ``content_blocks`` (R202, ``xai-grok-sampling-types``
``messages.rs`` ContentBlock 5-variant union + 3 deps) + ``doom_loop`` (R200,
``xai-grok-sampling-types`` ``doom_loop.rs`` wire contract + tolerant
parsers) + ``message_bodies`` (R204, ``xai-grok-sampling-types``
``messages.rs`` body-shaped leaves: SystemTextBlock struct +
SystemParam/MessageContent untagged unions + StreamDelta 4-variant tagged
union) + ``message_envelopes`` (R205, ``xai-grok-sampling-types``
``messages.rs`` mega-containers: Message + MessagesResponse + MessagesRequest
+ MessageStreamEvent 8-variant wrapper, closing the wire-type layer) +
``messages`` (R201, ``xai-grok-sampling-types`` ``messages.rs``
stop-reason + usage + delta-body cluster) + ``request_params`` (R203,
``xai-grok-sampling-types`` ``messages.rs`` request-side enums + leaf structs)
+ ``retry`` (R198 backoff subset + R199 decision layer) + ``types`` (R199,
``xai-grok-sampling-types`` ``error.rs``). See each leaf module's docstring for
its migration map + YAGNI ledger.

Leaf order (crate ``lib.rs`` re-exports, in migration order):

1. ``config`` (R195) -- :class:`OriginClientInfo` + :class:`AuthScheme` (pure
   types). Closes the R193 ``OriginClientInfo`` source-of-truth commitment.
   Remaining ``config.rs`` symbols (:class:`SamplerConfig` / :class:`RetryPolicy`
   / 2 traits) are deferred or YAGNI -- see ``config.py`` docstring.
2. ``doom_loop`` (R200) -- server-side doom-loop wire contract + tolerant
   parsers, from ``xai-grok-sampling-types`` ``doom_loop.rs`` (no-I/O leaf).
   5 wire constants + byte-exact fixtures, the 3-variant
   :class:`DoomLoopSignalKind` + :class:`DoomLoopPeek` discriminated unions,
   :class:`DoomLoopSignal` parse/tightest, :class:`DoomLoopRecoveryPolicy`
   clamp/is_confident/confident_triggers/from_payload, and the
   :func:`peek_doom_loop` + :func:`is_check_event` free functions. Parsed
   ``raw`` labels feed the R199 :class:`SamplingError` ``DoomLoopDetected``
   variant.
3. ``retry`` (R198+R199) -- :data:`DEFAULT_MAX_RETRIES` /
   :data:`RATE_LIMIT_RETRY_THRESHOLD` + :func:`resolve_max_retries_with_env` +
   the backoff numerical core (:func:`backoff_base_ms` /
   :func:`retry_backoff_with_jitter` / :func:`doom_loop_backoff`) [R198]; plus
   the decision layer (:class:`RetryDecision` + 6 variants +
   :func:`classify_error` + :func:`format_sampling_error` + :func:`clone_error`)
   [R199] consuming the migrated :class:`SamplingError`.
4. ``types`` (R199) -- :class:`SamplingError` discriminated union (12 variants,
   ``Http``/``Serialization`` purified to ``str``) + :class:`EmptyReason` +
   :class:`EmptyResponseContext` + :class:`ResponseModelMetadata` + the
   :data:`SERIALIZATION_DISPLAY_PREFIX` constant + :func:`is_context_length_error`
   free function, from ``xai-grok-sampling-types`` ``error.rs`` (no-I/O leaf).
5. ``messages`` (R201) -- Anthropic Messages API (``/v1/messages``) stop-reason
   + usage + delta-body cluster, from ``xai-grok-sampling-types``
   ``messages.rs`` (no-I/O leaf). The tolerant :class:`StopReason` snake_case
   enum + :class:`UnknownStopReason` catch-all (+:func:`parse_stop_reason` /
   :func:`stop_reason_to_wire` faithful round-trip) + :class:`MessagesUsage` /
   :class:`MessageDeltaUsage` token counters + :class:`StopDetails` /
   :class:`MessageDeltaBody` terminal body + :class:`StreamError`. The request
   types (``MessagesRequest`` + the full ``MessageStreamEvent`` wrapper) landed
   in R205 (:mod:`message_envelopes`) -- they consume the R202
   :class:`ContentBlock` union.
6. ``content_blocks`` (R202) -- the :class:`ContentBlock` 5-variant tagged
   union (Text/Image/ToolUse/ToolResult/Thinking) shared by request + response
   + tool-result bodies, plus its 3 direct dependencies: :class:`CacheControl`
   leaf + :class:`ImageSource` 2-variant tagged union (base64/url) +
   :class:`ToolResultContent` untagged 2-variant union (string vs recursive
   blocks). No-I/O (``serde_json::Value`` -> ``dict``); strict tagged-union
   parse (unknown ``type`` raises, no catch-all -- unlike the R201 StopReason
   catch-all). The 5 ``ContentBlock`` variants carry a ``Block`` suffix to
   avoid colliding with the R201 ``StopReason::ToolUse`` variant.
7. ``request_params`` (R203) -- the request-side enums + simple leaf structs
   from ``xai-grok-sampling-types`` ``messages.rs``: the :class:`MessageRole`
   lowercase enum + :class:`ThinkingDisplay` snake_case enum + the
   :class:`ThinkingConfig` / :class:`OutputFormat` / :class:`ToolChoiceParam`
   tagged unions + the :class:`OutputConfig` / :class:`ToolParam` /
   :class:`Metadata` flat structs. No-I/O (``serde_json::Value`` -> ``dict``);
   strict tagged-union parse (unknown ``type`` raises). The list-carrying
   request containers (``MessagesRequest`` + ``Message``) land later --
   they consume the R202 :class:`ContentBlock` union + the R204 body leaves.
8. ``message_bodies`` (R204) -- the 4 "middle-layer" body-shaped leaves from
   ``xai-grok-sampling-types`` ``messages.rs``: :class:`SystemTextBlock`
   (the standalone ``TextBlock`` struct, renamed to dodge the R202
   :class:`ContentBlock::Text` variant collision) + :class:`SystemParam`
   + :class:`MessageContent` (untagged string-vs-blocks unions, consuming
   SystemTextBlock / R202 ContentBlock respectively) + :class:`StreamDelta`
   (4-variant tagged union -- text / input-json / thinking / signature
   deltas). No-I/O; strict tagged-union parse (unknown ``type`` raises);
   untagged unions match on JSON shape. The mega-containers
   (``MessagesRequest`` + ``Message`` + ``MessagesResponse`` +
   ``MessageStreamEvent``) consume these leaves and landed in R205
   (:mod:`message_envelopes`).
9. ``message_envelopes`` (R205) -- the 4 outermost wire containers from
   ``xai-grok-sampling-types`` ``messages.rs`` that aggregate every R201-R204
   leaf into the full request / response / streaming shapes: :class:`Message`
   (a single turn, role + content) + :class:`MessagesResponse` (the
   non-streaming reply) + :class:`MessagesRequest` (the
   ``#[derive(Default)]`` request body, 11 optional knobs) + the
   :class:`MessageStreamEvent` 8-variant tagged union (message_start /
   message_delta / message_stop / content_block_start / content_block_delta /
   content_block_stop / ping / error -- strict, no catch-all). This round
   closes the ``messages.rs`` wire-type layer.
10. ``serde_helpers`` (R211) -- :func:`empty_string_as_none` (the single
   ``deserialize_with`` hook from ``serde_helpers.rs``): normalizes an empty
   string to ``None`` on the two ``Option<String>`` fingerprint fields
   (``system_fingerprint`` on the ChatCompletion response +
   ``model_fingerprint`` on ``conversation.rs``). Pure value-level normalizer;
   the consumer containers land later.
11. ``chat_truncate`` (R212) -- :func:`chat_truncate_for_prompt` (the
   ``types.rs`` free-function leaf): counts how many leading chat messages to
   keep so the slice closes after the ``(target + 1)``-th user prompt
   (truncating AT the ``(target + 2)``-th user message -- the triggering user
   is excluded). Pure algorithm over an in-memory
   :class:`~collections.abc.Sequence`; consumes the R206 :class:`Role` +
   R210 :class:`ChatRequestMessage`. Strategy-B fallback after the
   :class:`ChatCompletionRequest` container proved blocked on three
   un-landed deps (``ToolDefinition`` + ``crate::rs::ResponseFormat`` +
   ``Box<dyn TraceContext>``).
12. ``reasoning_effort_meta`` (R213) -- the ``types.rs`` reasoning-effort
   meta read/write subsystem: 3 wire constants
   (:data:`REASONING_EFFORT_META_KEY` +
   :data:`SUPPORTS_REASONING_EFFORT_META_KEY` +
   :data:`REASONING_EFFORTS_META_KEY`) + the canonical-effort token parser
   :func:`parse_canonical_effort_token` (accepting the ``"max"`` CLI/UX alias
   of ``Xhigh`` -- mirrors grok ``FromStr``, NOT the strict serde
   ``Deserialize`` that the Full-table ``value`` field uses) + the singular
   readers :func:`supports_reasoning_effort_meta` /
   :func:`parse_reasoning_effort_meta` + the writer
   :func:`reasoning_effort_meta_value` + the :class:`ReasoningEffortOption`
   menu-entry struct + the plural reader/writer
   :func:`parse_reasoning_effort_options` /
   :func:`parse_reasoning_efforts_meta` /
   :func:`reasoning_efforts_meta_value` (untagged Bare-string-vs-Full-table
   option shape, skip-invalid + warn). Pure value-level subsystem over
   ``dict`` / ``list``; consumes the R206 :class:`ReasoningEffort`.
13. ``api_backend`` (R214) -- :class:`ApiBackend` (the 3-variant
   ``snake_case`` wire-string API-backend selector: Chat Completions /
   Responses / Anthropic Messages) + the :meth:`supports_native_schema`
   decision method (Messages -> ``False`` -- a schema there blocks tool
   use, so structured output routes through the StructuredOutput tool) +
   the :data:`DEFAULT_API_BACKEND` constant (``#[default]
   ChatCompletions``). The ``#[serde(default)]`` ``api_backend`` field of
   :class:`SamplingConfig` (lands later). Zero-dependency leaf, correcting
   the earlier "depends on crate::rs" deferral (``ApiBackend`` itself has
   no ``crate::rs`` / ``indexmap`` / ``NonZeroU64`` dep; only
   :class:`SamplingConfig` does).
"""

from minimax_code.sampler.api_backend import (
    DEFAULT_API_BACKEND,
    ApiBackend,
)
from minimax_code.sampler.chat_completion_leaves import (
    DEFAULT_REASONING_EFFORT,
    CompletionTokensDetails,
    FinishReason,
    ImageUrl,
    PromptTokensDetails,
    ReasoningEffort,
    Role,
    ToolCallFunction,
    ToolChoiceFunction,
    ToolType,
)
from minimax_code.sampler.chat_completion_mid import (
    ChatBlocksContent,
    ChatContentBlock,
    ChatImageUrlBlock,
    ChatMessageContent,
    ChatTextBlock,
    ChatTextContent,
    ChatUsage,
    FunctionToolChoice,
    PresetToolChoice,
    ToolCallRequest,
    ToolChoice,
)
from minimax_code.sampler.chat_completion_response import (
    ChatChoice,
    ChatCompletionChunk,
    ChatCompletionResponse,
    ChatResponseMessage,
    ToolCallResponse,
)
from minimax_code.sampler.chat_completion_streaming import (
    ChatChunkChoice,
    ChatChunkDelta,
    ToolCallDelta,
    ToolCallFunctionDelta,
)
from minimax_code.sampler.chat_request_message import ChatRequestMessage
from minimax_code.sampler.chat_truncate import chat_truncate_for_prompt
from minimax_code.sampler.compaction_headers import (
    CompactionAtTokens,
    CompactionAtTokensEnabled,
    CompactionAtTokensFixed,
    CompactionsRemaining,
    CompactionsRemainingDynamic,
    CompactionsRemainingFixed,
)
from minimax_code.sampler.config import (
    DEFAULT_AUTH_SCHEME,
    AuthScheme,
    OriginClientInfo,
)
from minimax_code.sampler.content_blocks import (
    Base64ImageSource,
    BlocksToolResultContent,
    CacheControl,
    ContentBlock,
    ImageBlock,
    ImageSource,
    TextBlock,
    TextToolResultContent,
    ThinkingBlock,
    ToolResultBlock,
    ToolResultContent,
    ToolUseBlock,
    UrlImageSource,
)
from minimax_code.sampler.conversation_content_part import (
    ContentPart,
    ImagePart,
    TextPart,
)
from minimax_code.sampler.conversation_enums import (
    PriorTurnInterrupt,
    SyntheticReason,
)
from minimax_code.sampler.conversation_leaves import (
    DanglingToolCallReason,
    HarnessHalted,
    UserCancelled,
    reported_cost_ticks,
    truncate_bytes,
)
from minimax_code.sampler.conversation_tool_choice import (
    ConversationAuto,
    ConversationFunction,
    ConversationNone,
    ConversationRequired,
    ConversationToolChoice,
)
from minimax_code.sampler.conversation_usage import (
    ConversationStopReason,
    TokenUsage,
    from_finish_reason,
    from_usage,
)
from minimax_code.sampler.doom_loop import (
    DOOM_LOOP_CHECK_EVENT_TYPE,
    DOOM_LOOP_CHECK_HEADER,
    SAMPLE_CHECK_EVENT_DATA,
    SAMPLE_CHECK_EVENT_DATA_CUMULATIVE,
    THINKING_CHANNEL,
    CheckEvent,
    DoomLoopPeek,
    DoomLoopRecoveryPolicy,
    DoomLoopSignal,
    DoomLoopSignalKind,
    LowLogprob,
    NoDoomLoop,
    ResponseField,
    TailRepetition,
    Unknown,
    is_check_event,
    peek_doom_loop,
)
from minimax_code.sampler.message_bodies import (
    BlocksMessageContent,
    BlocksSystemParam,
    InputJsonDelta,
    MessageContent,
    SignatureDelta,
    StreamDelta,
    SystemParam,
    SystemTextBlock,
    TextDelta,
    TextMessageContent,
    TextSystemParam,
    ThinkingDelta,
)
from minimax_code.sampler.message_envelopes import (
    ContentBlockDeltaEvent,
    ContentBlockStartEvent,
    ContentBlockStopEvent,
    Message,
    MessageDeltaEvent,
    MessagesRequest,
    MessagesResponse,
    MessageStartEvent,
    MessageStopEvent,
    MessageStreamEvent,
    PingEvent,
    StreamErrorEvent,
)
from minimax_code.sampler.messages import (
    EndTurn,
    MaxTokens,
    MessageDeltaBody,
    MessageDeltaUsage,
    MessagesUsage,
    ModelContextWindowExceeded,
    PauseTurn,
    Refusal,
    StopDetails,
    StopReason,
    StopSequence,
    StreamError,
    ToolUse,
    UnknownStopReason,
    parse_stop_reason,
    stop_reason_to_wire,
)
from minimax_code.sampler.reasoning_effort_meta import (
    REASONING_EFFORT_META_KEY,
    REASONING_EFFORTS_META_KEY,
    SUPPORTS_REASONING_EFFORT_META_KEY,
    ReasoningEffortOption,
    parse_canonical_effort_token,
    parse_reasoning_effort_meta,
    parse_reasoning_effort_options,
    parse_reasoning_efforts_meta,
    reasoning_effort_meta_value,
    reasoning_efforts_meta_value,
    supports_reasoning_effort_meta,
)
from minimax_code.sampler.request_params import (
    AdaptiveThinkingConfig,
    AnyToolChoiceParam,
    AutoToolChoiceParam,
    DisabledThinkingConfig,
    EnabledThinkingConfig,
    JsonSchemaOutputFormat,
    MessageRole,
    Metadata,
    NamedToolChoiceParam,
    OutputConfig,
    OutputFormat,
    ThinkingConfig,
    ThinkingDisplay,
    ToolChoiceParam,
    ToolParam,
)
from minimax_code.sampler.retry import (
    BACKOFF_BASE_MS,
    BACKOFF_CAP_MS,
    DEFAULT_MAX_RETRIES,
    DOOM_LOOP_BOUND_MS,
    RATE_LIMIT_RETRY_THRESHOLD,
    EmitToSession,
    Fatal,
    Retry,
    RetryDecision,
    RetryWithBackoff,
    RetryWithClientRebuild,
    RetryWithImageStrip,
    backoff_base_ms,
    classify_error,
    clone_error,
    doom_loop_backoff,
    format_sampling_error,
    resolve_max_retries,
    resolve_max_retries_with_env,
    retry_backoff_with_jitter,
)
from minimax_code.sampler.sampling_config import SamplingConfig
from minimax_code.sampler.search_parameters import (
    SearchParameters,
    SearchSource,
    SearchSourceNews,
    SearchSourceRss,
    SearchSourceWeb,
    SearchSourceX,
)
from minimax_code.sampler.serde_helpers import empty_string_as_none
from minimax_code.sampler.types import (
    SERIALIZATION_DISPLAY_PREFIX,
    EmptyReason,
    EmptyResponseContext,
    ResponseModelMetadata,
    SamplingError,
    is_context_length_error,
)

__all__ = [
    "AdaptiveThinkingConfig",
    "AnyToolChoiceParam",
    "ApiBackend",
    "AuthScheme",
    "AutoToolChoiceParam",
    "BACKOFF_BASE_MS",
    "BACKOFF_CAP_MS",
    "Base64ImageSource",
    "BlocksMessageContent",
    "BlocksSystemParam",
    "BlocksToolResultContent",
    "CacheControl",
    "ChatBlocksContent",
    "ChatChoice",
    "ChatChunkChoice",
    "ChatChunkDelta",
    "ChatCompletionChunk",
    "ChatCompletionResponse",
    "ChatContentBlock",
    "ChatImageUrlBlock",
    "ChatMessageContent",
    "ChatRequestMessage",
    "ChatResponseMessage",
    "ChatTextBlock",
    "ChatTextContent",
    "ChatUsage",
    "CheckEvent",
    "CompactionAtTokens",
    "CompactionAtTokensEnabled",
    "CompactionAtTokensFixed",
    "CompactionsRemaining",
    "CompactionsRemainingDynamic",
    "CompactionsRemainingFixed",
    "CompletionTokensDetails",
    "ContentBlock",
    "ContentBlockDeltaEvent",
    "ContentBlockStartEvent",
    "ContentBlockStopEvent",
    "ContentPart",
    "ConversationAuto",
    "ConversationFunction",
    "ConversationNone",
    "ConversationRequired",
    "ConversationStopReason",
    "ConversationToolChoice",
    "DEFAULT_API_BACKEND",
    "DEFAULT_AUTH_SCHEME",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_REASONING_EFFORT",
    "DOOM_LOOP_BOUND_MS",
    "DOOM_LOOP_CHECK_EVENT_TYPE",
    "DOOM_LOOP_CHECK_HEADER",
    "DanglingToolCallReason",
    "DisabledThinkingConfig",
    "DoomLoopPeek",
    "DoomLoopRecoveryPolicy",
    "DoomLoopSignal",
    "DoomLoopSignalKind",
    "EmitToSession",
    "EmptyReason",
    "EmptyResponseContext",
    "EnabledThinkingConfig",
    "EndTurn",
    "Fatal",
    "FinishReason",
    "FunctionToolChoice",
    "HarnessHalted",
    "ImageBlock",
    "ImagePart",
    "ImageSource",
    "ImageUrl",
    "InputJsonDelta",
    "JsonSchemaOutputFormat",
    "LowLogprob",
    "MaxTokens",
    "Message",
    "MessageContent",
    "MessageDeltaBody",
    "MessageDeltaEvent",
    "MessageDeltaUsage",
    "MessageRole",
    "MessageStartEvent",
    "MessageStopEvent",
    "MessageStreamEvent",
    "MessagesRequest",
    "MessagesResponse",
    "MessagesUsage",
    "Metadata",
    "ModelContextWindowExceeded",
    "NamedToolChoiceParam",
    "NoDoomLoop",
    "OriginClientInfo",
    "OutputConfig",
    "OutputFormat",
    "PauseTurn",
    "PingEvent",
    "PresetToolChoice",
    "PriorTurnInterrupt",
    "PromptTokensDetails",
    "RATE_LIMIT_RETRY_THRESHOLD",
    "REASONING_EFFORT_META_KEY",
    "REASONING_EFFORTS_META_KEY",
    "ReasoningEffort",
    "ReasoningEffortOption",
    "Refusal",
    "ResponseField",
    "ResponseModelMetadata",
    "Retry",
    "RetryDecision",
    "RetryWithBackoff",
    "RetryWithClientRebuild",
    "RetryWithImageStrip",
    "Role",
    "SAMPLE_CHECK_EVENT_DATA",
    "SAMPLE_CHECK_EVENT_DATA_CUMULATIVE",
    "SERIALIZATION_DISPLAY_PREFIX",
    "SUPPORTS_REASONING_EFFORT_META_KEY",
    "SamplingConfig",
    "SamplingError",
    "SearchParameters",
    "SearchSource",
    "SearchSourceNews",
    "SearchSourceRss",
    "SearchSourceWeb",
    "SearchSourceX",
    "SignatureDelta",
    "StopDetails",
    "StopReason",
    "StopSequence",
    "StreamDelta",
    "StreamError",
    "StreamErrorEvent",
    "SyntheticReason",
    "SystemParam",
    "SystemTextBlock",
    "THINKING_CHANNEL",
    "TailRepetition",
    "TextBlock",
    "TextDelta",
    "TextMessageContent",
    "TextPart",
    "TextSystemParam",
    "TextToolResultContent",
    "ThinkingBlock",
    "ThinkingConfig",
    "ThinkingDelta",
    "ThinkingDisplay",
    "TokenUsage",
    "ToolCallDelta",
    "ToolCallFunction",
    "ToolCallFunctionDelta",
    "ToolCallRequest",
    "ToolCallResponse",
    "ToolChoice",
    "ToolChoiceFunction",
    "ToolChoiceParam",
    "ToolParam",
    "ToolResultBlock",
    "ToolResultContent",
    "ToolType",
    "ToolUse",
    "ToolUseBlock",
    "Unknown",
    "UnknownStopReason",
    "UrlImageSource",
    "UserCancelled",
    "backoff_base_ms",
    "chat_truncate_for_prompt",
    "classify_error",
    "clone_error",
    "doom_loop_backoff",
    "empty_string_as_none",
    "format_sampling_error",
    "from_finish_reason",
    "from_usage",
    "is_check_event",
    "is_context_length_error",
    "parse_canonical_effort_token",
    "parse_reasoning_effort_meta",
    "parse_reasoning_effort_options",
    "parse_reasoning_efforts_meta",
    "parse_stop_reason",
    "peek_doom_loop",
    "reasoning_effort_meta_value",
    "reasoning_efforts_meta_value",
    "reported_cost_ticks",
    "resolve_max_retries",
    "resolve_max_retries_with_env",
    "retry_backoff_with_jitter",
    "stop_reason_to_wire",
    "supports_reasoning_effort_meta",
    "truncate_bytes",
]
