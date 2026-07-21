"""``xai-grok-sampler`` platform package (R195+, crate migration in progress).

Fuses grok's ``xai-grok-sampler`` crate (actor-based sampling/inference layer:
HTTP streaming + retry, no shell coupling). The crate ships 12 modules; this
package is being filled leaf-by-leaf. Currently landed: ``chat_completion_leaves`` (R206, the first
``xai-grok-sampling-types`` ``types.rs`` slice: Role / ToolType / FinishReason /
ReasoningEffort wire-string enums + ImageUrl / ToolChoiceFunction /
ToolCallFunction / PromptTokensDetails / CompletionTokensDetails flat leaf
structs + the DEFAULT_REASONING_EFFORT constant) + ``config`` (R195,
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
"""

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
    "AuthScheme",
    "AutoToolChoiceParam",
    "BACKOFF_BASE_MS",
    "BACKOFF_CAP_MS",
    "Base64ImageSource",
    "BlocksMessageContent",
    "BlocksSystemParam",
    "BlocksToolResultContent",
    "CacheControl",
    "CheckEvent",
    "CompletionTokensDetails",
    "ContentBlock",
    "ContentBlockDeltaEvent",
    "ContentBlockStartEvent",
    "ContentBlockStopEvent",
    "DEFAULT_AUTH_SCHEME",
    "DEFAULT_MAX_RETRIES",
    "DEFAULT_REASONING_EFFORT",
    "DOOM_LOOP_BOUND_MS",
    "DOOM_LOOP_CHECK_EVENT_TYPE",
    "DOOM_LOOP_CHECK_HEADER",
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
    "ImageBlock",
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
    "PromptTokensDetails",
    "RATE_LIMIT_RETRY_THRESHOLD",
    "ReasoningEffort",
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
    "SamplingError",
    "SignatureDelta",
    "StopDetails",
    "StopReason",
    "StopSequence",
    "StreamDelta",
    "StreamError",
    "StreamErrorEvent",
    "SystemParam",
    "SystemTextBlock",
    "THINKING_CHANNEL",
    "TailRepetition",
    "TextBlock",
    "TextDelta",
    "TextMessageContent",
    "TextSystemParam",
    "TextToolResultContent",
    "ThinkingBlock",
    "ThinkingConfig",
    "ThinkingDelta",
    "ThinkingDisplay",
    "ToolCallFunction",
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
    "backoff_base_ms",
    "classify_error",
    "clone_error",
    "doom_loop_backoff",
    "format_sampling_error",
    "is_check_event",
    "is_context_length_error",
    "parse_stop_reason",
    "peek_doom_loop",
    "resolve_max_retries",
    "resolve_max_retries_with_env",
    "retry_backoff_with_jitter",
    "stop_reason_to_wire",
]
