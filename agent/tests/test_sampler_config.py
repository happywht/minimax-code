"""Tests for sampler.config (R195, ``xai-grok-sampler`` ``src/config.rs`` subset).

Covers the two migrated pure types (:class:`OriginClientInfo` +
:class:`AuthScheme`) + the barrel surface + value semantics, plus the
**R193 source-of-truth reversal assertion**: ``grok_http.OriginClientInfo`` is
the *same object* as ``sampler.config.OriginClientInfo`` (the dependency
direction is inverted, not duplicated). The deferred/YAGNI symbols
(``SamplerConfig`` / ``RetryPolicy`` / 2 traits) are documented in
``config.py``. The constants ``DEFAULT_MAX_RETRIES`` / ``RATE_LIMIT_RETRY_
THRESHOLD`` migrated in R198 (:mod:`minimax_code.sampler.retry`); the package
barrel (tested below) re-exports the ``config`` (R195) + ``retry`` (R198
backoff + R199 decision layer) + ``types`` (R199 SamplingError) + ``doom_loop``
(R200 wire contract + tolerant parsers) + ``messages`` (R201 stop-reason + usage
+ delta-body cluster) + ``content_blocks`` (R202 ContentBlock union + 3 deps)
+ ``request_params`` (R203 request-side enums + leaf structs) + ``message_bodies``
(R204 body-shaped leaves: SystemTextBlock struct + SystemParam/MessageContent
untagged unions + StreamDelta 4-variant tagged union) + ``message_envelopes`` (R205
mega-containers: Message + MessagesResponse + MessagesRequest + MessageStreamEvent
8-variant wrapper, closing the wire-type layer) leaves.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code import sampler
from minimax_code.grok_http import OriginClientInfo as GrokHttpOriginClientInfo
from minimax_code.sampler import DEFAULT_AUTH_SCHEME, AuthScheme, OriginClientInfo
from minimax_code.sampler import config as sampler_config

# ---------------------------------------------------------------------------
# Barrel surface (package + module).
# ---------------------------------------------------------------------------


def test_package_barrel_exposes_config_retry_types_symbols() -> None:
    """R195 config (3: 2 types + 1 default) + R198 retry (10: 5 constants + 5
    functions) + R199 retry decision layer (10: 7 decision classes + 3 functions)
    + R199 types (6: 1 constant + 4 types + 1 free function) + R200 doom_loop
    (17: 5 constants/fixtures + 10 classes + 2 free functions) + R201 messages
    (16: StopReason union base + 8 variants + MessagesUsage + MessageDeltaUsage
    + StopDetails + MessageDeltaBody + StreamError + parse_stop_reason +
    stop_reason_to_wire) + R202 content_blocks (13: ContentBlock union base + 5
    variants + CacheControl + ImageSource union base + 2 variants +
    ToolResultContent union base + 2 variants) + R203 request_params (15:
    MessageRole lowercase enum + ThinkingDisplay snake_case enum + 3 tagged
    unions (ThinkingConfig 3-variant / OutputFormat 1-variant / ToolChoiceParam
    3-variant) + 3 flat structs (OutputConfig / ToolParam / Metadata)) + R204
    message_bodies (12: SystemTextBlock leaf struct + SystemParam union base + 2
    variants + MessageContent union base + 2 variants + StreamDelta union base +
    4 variants) + R205 message_envelopes (12: Message + MessagesResponse +
    MessagesRequest leaf structs + MessageStreamEvent union base + 8 variants)
    + chat_completion_leaves (R206, 10: Role / ToolType / FinishReason /
    ReasoningEffort wire-string enums + DEFAULT_REASONING_EFFORT + ImageUrl /
    ToolChoiceFunction / ToolCallFunction / PromptTokensDetails /
    CompletionTokensDetails flat leaf structs -- the first types.rs slice)
    + chat_completion_mid (R207, 11: ChatContentBlock 2-variant tagged union
    + ChatMessageContent (renamed from grok ``MessageContent``) untagged union
    + ToolChoice untagged union + ToolCallRequest struct + ChatUsage (renamed
    from grok ``Usage``) struct -- the second types.rs slice, consuming the
    R206 atomic leaves)
    = 135
    re-exported symbols. The config trio stays; R198 adds the backoff/max-retries
    leaf; R199 adds the decision layer (consuming the migrated SamplingError) and
    the SamplingError type leaf itself; R200 adds the doom-loop wire contract +
    tolerant parsers (whose parsed ``raw`` labels feed the R199 DoomLoopDetected
    variant); R201 adds the Messages API stop-reason + usage + delta-body cluster;
    R202 adds the ContentBlock 5-variant tagged union + its 3 direct dependencies
    (CacheControl leaf + ImageSource 2-variant union + ToolResultContent untagged
    recursive union); R203 adds the request-side enums + leaf structs (the
    MessagesRequest container's direct deps that carry no ContentBlock recursion
    -- strict tagged-union parse, no catch-all); R204 adds the 4 body-shaped
    middle-layer leaves (SystemTextBlock struct + SystemParam/MessageContent
    untagged string-vs-blocks unions + StreamDelta 4-variant tagged union --
    strict tagged-union parse, no catch-all, consuming the R202 ContentBlock);
    R205 adds the 4 mega-containers that close the ``messages.rs`` wire-type layer
    (Message leaf struct + MessagesResponse leaf struct + MessagesRequest
    ``#[derive(Default)]`` container + MessageStreamEvent 8-variant tagged union
    -- strict, no catch-all, aggregating every R201-R204 leaf into the full
    request / response / streaming shapes); R206 adds the first
    ``types.rs`` slice -- 9 zero-dependency atomic ChatCompletion leaves + the
    DEFAULT_REASONING_EFFORT constant (4 wire-string enums + 5 flat leaf
    structs, strict enum parse -- no catch-all); R207 adds the second
    ``types.rs`` slice -- 11 middle-layer ChatCompletion leaves composing the
    R206 atomic leaves into request/response body shapes (ChatContentBlock
    tagged union + ChatMessageContent/ToolChoice untagged unions +
    ToolCallRequest/ChatUsage structs, renamed ``Chat*`` to dodge the Anthropic
    Messages API peers -- strict tagged-union parse, no catch-all)
    + chat_completion_streaming (R208, 4: ChatChunkChoice + ChatChunkDelta +
    ToolCallDelta + ToolCallFunctionDelta -- the streaming-chunk delta leaves,
    consuming the R206 Role/FinishReason atomics + the inlined
    deserialize_null_default null-tolerant parser, Vec->tuple tool_calls)
    + compaction_headers (R208, 6: CompactionAtTokens untagged bool/int union +
    Enabled/Fixed variants + CompactionsRemaining untagged bool/int union +
    Dynamic/Fixed variants, each with a resolve() decision method -- the third
    types.rs slice, zero-dependency decision primitives, bool-before-int
    untagged parse guard)
    + search_parameters (R209, 6: SearchParameters struct + SearchSource 4-variant
    tagged union (x/web/news/rss per-variant rename) + 4 SearchSource* variant
    subclasses -- the realtime-data search knobs, the fourth types.rs slice,
    zero-dependency pure leaf; SearchSource::X carries the DEPRECATED x_handles
    field (kept for backward wire-compat), SearchSource::Rss carries the only
    non-optional links field (Vec<String> -> tuple, missing/non-list -> empty
    tuple), Option<Vec<String>> -> tuple|None tolerant parse)
    + chat_request_message (R210, 1: ChatRequestMessage struct -- role + content
    + name/tool_calls/tool_call_id/model_id/reasoning_content, the assistant/user/tool
    message body with 5 constructors + read-only helpers + copy-on-work mutators,
    consuming the R206 Role + R207 ChatMessageContent/ToolCallRequest atomics -- the
    fifth types.rs slice, role strictly required vs content tolerant, Vec<ToolCallRequest>
    -> tuple)
    + serde_helpers (R211, 1: empty_string_as_none -- the single deserialize_with
    hook from serde_helpers.rs, normalizing an empty string to None on the two
    Option<String> fingerprint fields; pure value-level normalizer, zero
    dependency, consuming containers land later) + chat_truncate (R212, 1:
    chat_truncate_for_prompt -- the types.rs free-function leaf, counting how
    many leading chat messages to keep so the slice closes after the (target
    + 1)-th user prompt; pure algorithm over an in-memory Sequence, zero
    dependency, consuming the R206 Role + R210 ChatRequestMessage) +
    reasoning_effort_meta (R213, 11: 3 wire constants + ReasoningEffortOption
    menu-entry struct + parse_canonical_effort_token canonical wire parser
    (accepting the "max" CLI/UX alias of Xhigh, mirroring grok FromStr NOT the
    strict serde Deserialize the Full-table value field uses) +
    supports_reasoning_effort_meta + parse_reasoning_effort_meta +
    reasoning_effort_meta_value singular readers+writer +
    parse_reasoning_effort_options + parse_reasoning_efforts_meta +
    reasoning_efforts_meta_value plural reader+writer -- the types.rs
    reasoning-effort meta read/write subsystem with the untagged
    Bare-string-vs-Full-table option shape + skip-invalid forward-compat,
    consuming the R206 ReasoningEffort; pure value-level over dict/list, zero
    dependency) + api_backend (R214, 2: ApiBackend 3-variant snake_case
    wire-string enum (ChatCompletions / Responses / Anthropic Messages) +
    DEFAULT_API_BACKEND constant -- the types.rs API-backend selector leaf,
    the #[serde(default)] api_backend field of the later SamplingConfig; zero
    dependency, correcting the earlier "depends on crate::rs" deferral) +
    chat_completion_response (R215, 5: ToolCallResponse +
    ChatResponseMessage + ChatChoice + ChatCompletionResponse +
    ChatCompletionChunk -- the types.rs ChatCompletion response +
    streaming-chunk exterior envelopes, the non-streaming peer of the R208
    streaming-delta interior; consumes the R206 Role/FinishReason/
    ToolCallFunction atomics + the R207 ChatUsage + the R208 ChatChunkChoice
    + the R211 empty_string_as_none hook; zero dependency, lifts the R208
    ChatCompletionChunk deferral) + sampling_config (R216, 1: SamplingConfig
    -- the types.rs sampling-client configuration container holding the
    non-secret knobs a sampling client needs (base_url / model /
    context_window required + 7 optional fields); the single types.rs leaf
    that consumes BOTH the R214 ApiBackend (its #[serde(default)]
    api_backend field) AND the R206 ReasoningEffort (its optional
    reasoning_effort field); resolves the two grok deps that previously
    blocked it (indexmap::IndexMap -> tuple-of-pairs + NonZeroU64 ->
    positive int); zero dependency). + conversation_leaves (R217, 5: the
    conversation.rs first slice -- reported_cost_ticks (Option<i64>::filter
    >0 cost-ticks normalizer: present positive int -> itself, None/0/
    negative/bool -> None) + truncate_bytes (UTF-8 char-boundary byte
    truncator: encode[:n].decode errors=ignore mirrors the
    is_char_boundary walk-back) + DanglingToolCallReason tagged union
    (no serde, pure in-program enum: UserCancelled field-less +
    HarnessHalted carrying class_; grok `class` renamed -- Python hard
    keyword, not a Rust one); strategy D pivot after types.rs 1030-1521
    exhaustion, zero dependency). + conversation_enums (R218, 2: the
    conversation.rs second slice -- the two #[serde(other)] catch-all wire
    enums that classify why a conversation item exists: SyntheticReason
    (12 typed variants + UNKNOWN unit catch-all, why a UserItem was
    synthesized by the runtime + starts_prompt_turn predicate -- the 5
    auto-wake reasons consumed a prompt_index slot) + PriorTurnInterrupt
    (3 typed variants + UNKNOWN unit catch-all, how the user fatally
    interrupted the preceding turn); unit #[serde(other)] catch-all (no
    data, unlike R201 StopReason's Unknown(String)) -> StrEnum UNKNOWN
    member whose from_payload never raises; strategy D continuation).
    + conversation_usage (R219, 4: the conversation.rs third slice -- the
    deferred "response stop + usage" cluster landing now that grok Usage is
    migrated (R207 ChatUsage): ConversationStopReason (renamed from grok
    StopReason to dodge the R201 messages.StopReason barrel collision -- the
    strict snake_case 4-variant StrEnum, NO #[serde(other)] catch-all so
    from_payload raises on unknown wire, parity with FinishReason; contrast
    R218 UNKNOWN catch-all) + TokenUsage (the flat 5x u32 conversation-side
    counter, frozen+slots, distinct from ChatUsage the wire shape) +
    from_finish_reason (impl From<FinishReason>: ToolCalls+FunctionCall
    collapse to ToolCalls) + from_usage (impl From<Usage>: cached from
    prompt_tokens_details, reasoning from completion_tokens_details, both
    default 0 when breakdown absent); strategy D continuation, clearing the
    two R217 blockers -- barrel rename precedent + ChatUsage landed).
    + conversation_tool_choice (R220, 5: the conversation.rs
    ConversationToolChoice tagged union -- the sampler package's first
    externally-tagged mixed enum, the third serde shape after R84 untagged
    JsonRpcId + R89 internally-tagged HookEvent; #[serde(rename_all=
    "snake_case")] external tagging -> the 3 unit variants serialize as bare
    snake_case strings ("auto"/"none"/"required"), the Function(String)
    newtype variant as {"function": name}; the Conversation prefix on all 4
    variants dodges the wire-layer ToolChoice family barrel collision
    (FunctionToolChoice et al.), mirroring the ConversationStopReason rename).
    + conversation_content_part (R221, 3: the conversation.rs ContentPart
    tagged union -- the sampler package's first internally-tagged mixed enum
    with data-carrying struct variants, the fourth serde shape after R84
    untagged JsonRpcId + R89 internally-tagged HookEvent + R220
    externally-tagged ConversationToolChoice; #[serde(tag="type",
    rename_all="snake_case")] -> Text{text} -> {"type":"text","text":...},
    Image{url} -> {"type":"image","url":...} (the standard OpenAI content-part
    wire shape); zero dependency, unblocks the UserItem/AssistantItem
    conversation consumer layer; no barrel collision -> no Conversation prefix
    (part vs block suffix keeps it distinct from the R202 ContentBlock family),
    <Type>Part variant naming mirrors the R202 <Type>Block precedent).
    + conversation_tool_defs (R222, 2: the conversation.rs ToolCall + ToolSpec
    flat struct pair from the "Tool Definitions and Calls" block -- the first
    **plain struct** shape in the conversation.rs migration (the prior five
    slices were all enums or free functions); the plain flat struct itself is
    serde's most common shape (already in the R206 ChatCompletion family), so
    R222 is not a new serde shape at the package level -- the milestone is the
    first conversation.rs struct and the first leaf here to carry the
    strict-required parse discipline (no #[serde(default)] on the required
    ToolCall id/name/arguments and ToolSpec name/parameters fields -> ValueError
    on missing or wrong-typed; the ToolSpec description Option<String> -> None
    default + skip_serializing_if None; extra keys tolerated); ToolCall unblocks
    the AssistantItem consumer layer; no barrel collision -> no Conversation
    prefix (distinct from the R206 ToolCallFunction wire-layer peer which
    carries no id)).
    + conversation_hosted_tools (R223, 3: the conversation.rs HostedTool
    backend-hosted tool union from the "Tool Definitions and Calls" block
    -- the in-program (#[derive(Debug,Clone)] only, NO serde) union +
    wire_name method, the first in-program union in the package to carry a
    method; R217 DanglingToolCallReason in-program-union shape + the R221
    ContentPart base-class isinstance-dispatch method pattern; completes
    the block with the backend-side tool peer; WebSearch allowed_domains
    Option<Vec<String>> -> tuple[str,...]|None; XSearch field-less;
    HostedTool unblocks the ConversationRequest hosted_tools consumer
    layer; no barrel collision -> no Conversation prefix)."""
    assert len(sampler.__all__) == 207
    assert set(sampler.__all__) == {
        # config (R195): 2 types + 1 default constant
        "AuthScheme",
        "DEFAULT_AUTH_SCHEME",
        "OriginClientInfo",
        # retry (R198): 5 constants + 5 functions
        "BACKOFF_BASE_MS",
        "BACKOFF_CAP_MS",
        "DEFAULT_MAX_RETRIES",
        "DOOM_LOOP_BOUND_MS",
        "RATE_LIMIT_RETRY_THRESHOLD",
        "backoff_base_ms",
        "doom_loop_backoff",
        "resolve_max_retries",
        "resolve_max_retries_with_env",
        "retry_backoff_with_jitter",
        # retry (R199 decision layer): 7 decision classes + 3 functions
        "EmitToSession",
        "Fatal",
        "Retry",
        "RetryDecision",
        "RetryWithBackoff",
        "RetryWithClientRebuild",
        "RetryWithImageStrip",
        "classify_error",
        "clone_error",
        "format_sampling_error",
        # types (R199): 1 constant + 4 types + 1 free function
        "EmptyReason",
        "EmptyResponseContext",
        "ResponseModelMetadata",
        "SERIALIZATION_DISPLAY_PREFIX",
        "SamplingError",
        "is_context_length_error",
        # doom_loop (R200): 5 constants/fixtures + 10 classes + 2 free functions
        "DOOM_LOOP_CHECK_EVENT_TYPE",
        "DOOM_LOOP_CHECK_HEADER",
        "SAMPLE_CHECK_EVENT_DATA",
        "SAMPLE_CHECK_EVENT_DATA_CUMULATIVE",
        "THINKING_CHANNEL",
        "CheckEvent",
        "DoomLoopPeek",
        "DoomLoopRecoveryPolicy",
        "DoomLoopSignal",
        "DoomLoopSignalKind",
        "LowLogprob",
        "NoDoomLoop",
        "ResponseField",
        "TailRepetition",
        "Unknown",
        "is_check_event",
        "peek_doom_loop",
        # messages (R201): StopReason union base + 8 variants + 2 usage types +
        # StopDetails + MessageDeltaBody + StreamError + 2 free functions
        "EndTurn",
        "MaxTokens",
        "MessageDeltaBody",
        "MessageDeltaUsage",
        "MessagesUsage",
        "ModelContextWindowExceeded",
        "PauseTurn",
        "Refusal",
        "StopDetails",
        "StopReason",
        "StopSequence",
        "StreamError",
        "ToolUse",
        "UnknownStopReason",
        "parse_stop_reason",
        "stop_reason_to_wire",
        # content_blocks (R202): ContentBlock union base + 5 variants +
        # CacheControl leaf + ImageSource union base + 2 variants +
        # ToolResultContent union base + 2 variants
        "Base64ImageSource",
        "BlocksToolResultContent",
        "CacheControl",
        "ContentBlock",
        "ImageBlock",
        "ImageSource",
        "TextBlock",
        "TextToolResultContent",
        "ThinkingBlock",
        "ToolResultBlock",
        "ToolResultContent",
        "ToolUseBlock",
        "UrlImageSource",
        # request_params (R203): MessageRole lowercase enum + ThinkingDisplay
        # snake_case enum + 3 tagged unions (ThinkingConfig 3-variant +
        # OutputFormat 1-variant + ToolChoiceParam 3-variant) + 3 flat structs
        # (OutputConfig + ToolParam + Metadata)
        "AdaptiveThinkingConfig",
        "AnyToolChoiceParam",
        "AutoToolChoiceParam",
        "DisabledThinkingConfig",
        "EnabledThinkingConfig",
        "JsonSchemaOutputFormat",
        "MessageRole",
        "Metadata",
        "NamedToolChoiceParam",
        "OutputConfig",
        "OutputFormat",
        "ThinkingConfig",
        "ThinkingDisplay",
        "ToolChoiceParam",
        "ToolParam",
        # message_bodies (R204): SystemTextBlock leaf struct + SystemParam union
        # base + 2 variants + MessageContent union base + 2 variants + StreamDelta
        # union base + 4 variants
        "BlocksMessageContent",
        "BlocksSystemParam",
        "InputJsonDelta",
        "MessageContent",
        "SignatureDelta",
        "StreamDelta",
        "SystemParam",
        "SystemTextBlock",
        "TextDelta",
        "TextMessageContent",
        "TextSystemParam",
        "ThinkingDelta",
        # message_envelopes (R205): Message + MessagesResponse + MessagesRequest
        # leaf structs + MessageStreamEvent union base + 8 variants
        "ContentBlockDeltaEvent",
        "ContentBlockStartEvent",
        "ContentBlockStopEvent",
        "Message",
        "MessageDeltaEvent",
        "MessageStartEvent",
        "MessageStopEvent",
        "MessageStreamEvent",
        "MessagesRequest",
        "MessagesResponse",
        "PingEvent",
        "StreamErrorEvent",
        # chat_completion_leaves (R206): Role / ToolType / FinishReason /
        # ReasoningEffort wire-string enums + DEFAULT_REASONING_EFFORT +
        # ImageUrl / ToolChoiceFunction / ToolCallFunction /
        # PromptTokensDetails / CompletionTokensDetails flat leaf structs
        "CompletionTokensDetails",
        "DEFAULT_REASONING_EFFORT",
        "FinishReason",
        "ImageUrl",
        "PromptTokensDetails",
        "ReasoningEffort",
        "Role",
        "ToolCallFunction",
        "ToolChoiceFunction",
        "ToolType",
        # chat_completion_mid (R207): ChatContentBlock 2-variant tagged union +
        # ChatMessageContent (renamed from grok MessageContent) untagged union +
        # ToolChoice untagged union + ToolCallRequest struct + ChatUsage
        # (renamed from grok Usage) struct -- 11 middle-layer leaves consuming
        # the R206 atomic slice
        "ChatBlocksContent",
        "ChatContentBlock",
        "ChatImageUrlBlock",
        "ChatMessageContent",
        "ChatTextBlock",
        "ChatTextContent",
        "ChatUsage",
        "FunctionToolChoice",
        "PresetToolChoice",
        "ToolCallRequest",
        "ToolChoice",
        # chat_completion_streaming (R208): ChatChunkChoice + ChatChunkDelta +
        # ToolCallDelta + ToolCallFunctionDelta -- the streaming-chunk delta
        # leaves, consuming the R206 Role/FinishReason atomics + the inlined
        # deserialize_null_default null-tolerant parser (Vec->tuple tool_calls)
        "ChatChunkChoice",
        "ChatChunkDelta",
        "ToolCallDelta",
        "ToolCallFunctionDelta",
        # compaction_headers (R208): CompactionAtTokens + CompactionsRemaining
        # untagged bool/int unions (+ Enabled/Fixed + Dynamic/Fixed variants),
        # each with a resolve() decision method -- zero-dependency decision
        # primitives, bool-before-int untagged parse guard
        "CompactionAtTokens",
        "CompactionAtTokensEnabled",
        "CompactionAtTokensFixed",
        "CompactionsRemaining",
        "CompactionsRemainingDynamic",
        "CompactionsRemainingFixed",
        # search_parameters (R209): SearchParameters struct + SearchSource
        # 4-variant tagged union (x/web/news/rss) + 4 variant subclasses --
        # the realtime-data search knobs, zero-dependency pure leaf
        "SearchParameters",
        "SearchSource",
        "SearchSourceNews",
        "SearchSourceRss",
        "SearchSourceWeb",
        "SearchSourceX",
        # chat_request_message (R210): ChatRequestMessage struct -- the
        # assistant/user/tool message body (role + content + name/tool_calls/
        # tool_call_id/model_id/reasoning_content), the fifth types.rs slice
        "ChatRequestMessage",
        # serde_helpers (R211): empty_string_as_none -- the single
        # deserialize_with hook from serde_helpers.rs, normalizing an empty
        # string to None on the two Option<String> fingerprint fields
        "empty_string_as_none",
        # chat_truncate (R212): chat_truncate_for_prompt -- the types.rs
        # free-function leaf, counting how many leading chat messages to keep
        # so the slice closes after the (target + 1)-th user prompt
        "chat_truncate_for_prompt",
        # reasoning_effort_meta (R213): 3 wire constants + ReasoningEffortOption
        # + canonical-effort parser + singular readers/writer + plural
        # readers/writer -- the types.rs reasoning-effort meta subsystem
        "REASONING_EFFORT_META_KEY",
        "REASONING_EFFORTS_META_KEY",
        "SUPPORTS_REASONING_EFFORT_META_KEY",
        "ReasoningEffortOption",
        "parse_canonical_effort_token",
        "parse_reasoning_effort_meta",
        "parse_reasoning_effort_options",
        "parse_reasoning_efforts_meta",
        "reasoning_effort_meta_value",
        "reasoning_efforts_meta_value",
        "supports_reasoning_effort_meta",
        # api_backend (R214): ApiBackend 3-variant snake_case wire-string
        # enum (ChatCompletions / Responses / Anthropic Messages) +
        # DEFAULT_API_BACKEND constant -- the types.rs API-backend selector
        "ApiBackend",
        "DEFAULT_API_BACKEND",
        # chat_completion_response (R215): ToolCallResponse +
        # ChatResponseMessage + ChatChoice + ChatCompletionResponse +
        # ChatCompletionChunk -- the types.rs ChatCompletion response +
        # streaming-chunk exterior envelopes (non-streaming peer of the R208
        # streaming-delta interior; consumes R206 atomics + R207 ChatUsage +
        # R208 ChatChunkChoice + R211 empty_string_as_none)
        "ChatChoice",
        "ChatCompletionChunk",
        "ChatCompletionResponse",
        "ChatResponseMessage",
        "ToolCallResponse",
        # sampling_config (R216): SamplingConfig -- the types.rs sampling-client
        # configuration container (non-secret knobs), consuming the R214
        # ApiBackend (#[serde(default)] api_backend field) + the R206
        # ReasoningEffort (optional reasoning_effort field); IndexMap->
        # tuple-of-pairs + NonZeroU64->positive int (the two deps that
        # previously blocked this leaf)
        "SamplingConfig",
        # conversation_leaves (R217): the conversation.rs first slice -- 3
        # zero-dependency pure leaves opening the 9481-line mega-module:
        # reported_cost_ticks (Option<i64>::filter >0 cost-ticks normalizer) +
        # truncate_bytes (UTF-8 char-boundary byte truncator) +
        # DanglingToolCallReason tagged union (#[derive(Debug,Clone,Copy)]
        # only, no serde -- pure in-program enum; UserCancelled field-less +
        # HarnessHalted carrying class_ taxonomy tag, grok `class` renamed --
        # Python hard keyword); strategy D pivot after types.rs exhaustion
        "DanglingToolCallReason",
        "HarnessHalted",
        "UserCancelled",
        "reported_cost_ticks",
        "truncate_bytes",
        # conversation_enums (R218): the conversation.rs second slice -- the
        # two #[serde(other)] catch-all wire enums classifying why a
        # conversation item exists: SyntheticReason (12 typed + UNKNOWN unit
        # catch-all, why a UserItem was synthesized + starts_prompt_turn) +
        # PriorTurnInterrupt (3 typed + UNKNOWN unit catch-all, how the user
        # fatally interrupted the preceding turn); unit catch-all -> StrEnum
        # UNKNOWN member, from_payload never raises
        "PriorTurnInterrupt",
        "SyntheticReason",
        # conversation_usage (R219): the conversation.rs third slice -- the
        # "response stop + usage" cluster (ConversationStopReason renamed
        # from grok StopReason to dodge the R201 barrel collision, strict
        # StrEnum no catch-all + TokenUsage flat 5x u32 counter + 2 From
        # conversion impls), clearing the two R217 deferral blockers
        "ConversationStopReason",
        "TokenUsage",
        "from_finish_reason",
        "from_usage",
        # conversation_tool_choice (R220): the conversation.rs tool-choice
        # tagged union -- the first externally-tagged mixed enum in the
        # sampler package (3 unit variants as bare snake_case strings +
        # Function(String) newtype variant as {"function": name}); the
        # Conversation prefix on all 4 variants dodges the wire-layer
        # ToolChoice family barrel collision (FunctionToolChoice et al.)
        "ConversationAuto",
        "ConversationFunction",
        "ConversationNone",
        "ConversationRequired",
        "ConversationToolChoice",
        # conversation_content_part (R221): the conversation.rs content-part
        # tagged union -- the first internally-tagged mixed enum with
        # data-carrying struct variants (Text{text}/Image{url} ->
        # {"type":<tag>,<field>:<value>}); no barrel collision -> no
        # Conversation prefix, <Type>Part naming mirrors the R202 <Type>Block
        "ContentPart",
        "ImagePart",
        "TextPart",
        # conversation_tool_defs (R222): the conversation.rs tool call + spec
        # flat struct pair -- the first plain struct shape in the
        # conversation.rs migration (the prior five slices were all enums or
        # free functions); strict-required + tolerant-optional parse (no
        # #[serde(default)] on required fields -> ValueError on
        # missing/wrong-typed; ToolSpec description Option -> None default);
        # ToolCall unblocks the AssistantItem consumer layer; no barrel
        # collision -> no Conversation prefix (distinct from the R206
        # ToolCallFunction wire-layer peer which carries no id)
        "ToolCall",
        "ToolSpec",
        # conversation_hosted_tools (R223): the conversation.rs HostedTool
        # backend-hosted tool union from the "Tool Definitions and Calls"
        # block -- the in-program (#[derive(Debug,Clone)] only, NO serde)
        # union + wire_name method, the first in-program union in the
        # package to carry a method; R217 DanglingToolCallReason
        # in-program-union shape + R221 ContentPart base-class
        # isinstance-dispatch method; completes the block with the
        # backend-side tool peer; WebSearch allowed_domains
        # Option<Vec<String>> -> tuple[str,...]|None; XSearch field-less;
        # HostedTool unblocks the ConversationRequest hosted_tools
        # consumer layer; no barrel collision -> no Conversation prefix
        "HostedTool",
        "WebSearch",
        "XSearch",
        # conversation_message_items (R224): the conversation.rs "Message
        # Items" block -- 4 plain structs (SystemItem + UserItem +
        # AssistantItem + ToolResultItem), the second plain-struct slice +
        # the first to compose multiple already-migrated sampler leaves
        # (ContentPart/ToolCall/SyntheticReason/PriorTurnInterrupt/
        # ReasoningEffort + empty_string_as_none); strict-required +
        # tolerant-optional parse per the R222 discipline; UserItem content
        # defaults to () honoring #[derive(Default)]; AssistantItem
        # model_fingerprint resolves the system_fingerprint alias +
        # empty_string_as_none hook; unblocks the ConversationItem
        # tagged-union consumer; no barrel collision -> no Conversation prefix
        "AssistantItem",
        "SystemItem",
        "ToolResultItem",
        "UserItem",
        # attribution (R233): SamplingConsumer 6-variant endpoint StrEnum
        # (member value == endpoint identifier) + as_endpoint method +
        # SENT_BEARER_PREFIX_LEN cross-crate invariant constant (mirrors
        # xai_grok_shell token_suffix = 12) + Auth401AttributionCallback
        # abc.ABC trait (record_401 abstractmethod, scrub-at-boundary
        # invariant: bearer truncated to 12-char prefix before crossing the
        # trait boundary) + SharedAttributionCallback TypeAlias -- the
        # xai-grok-sampler src/attribution.rs whole-leaf migration (zero
        # external crate dependency, only std::sync::Arc); clears the
        # SamplerConfig deferred-dependency ledger entry
        # (attribution::SharedAttributionCallback unmigrated)
        "Auth401AttributionCallback",
        "SENT_BEARER_PREFIX_LEN",
        "SamplingConsumer",
        "SharedAttributionCallback",
        # metrics (R234): compute_percentiles pure algorithm (p50/p99/max/
        # mean/sum from a caller-sorted slice) + InferenceLatencyStats
        # dataclass (9 fields) + from_timestamps classmethod (Instant ->
        # float, round to ms) + to_log_fields dict (record_on_span Python
        # equivalent, decoupled from tracing::Span) -- the xai-grok-sampler
        # src/metrics.rs whole-leaf migration (pure algorithm leaf, zero
        # external crate dependency, only std::time::Instant + serde);
        # clears the events.rs deferred-dependency blocker
        # (metrics::InferenceLatencyStats)
        "InferenceLatencyStats",
        "compute_percentiles",
    }


def test_module_barrel_exposes_three_symbols() -> None:
    assert len(sampler_config.__all__) == 3
    assert set(sampler_config.__all__) == {"AuthScheme", "DEFAULT_AUTH_SCHEME", "OriginClientInfo"}


# ---------------------------------------------------------------------------
# AuthScheme: serde ``#[serde(rename_all="snake_case")]`` labels + default.
# ---------------------------------------------------------------------------


def test_auth_scheme_labels_match_serde_snake_case() -> None:
    """grok ``Bearer`` -> ``bearer``; ``XApiKey`` -> ``x_api_key`` (snake_case rename)."""
    assert AuthScheme.BEARER == "bearer"
    assert AuthScheme.X_API_KEY == "x_api_key"


def test_auth_scheme_str_is_wire_value() -> None:
    """``StrEnum.__str__`` returns the wire value (what serde would emit)."""
    assert str(AuthScheme.BEARER) == "bearer"
    assert str(AuthScheme.X_API_KEY) == "x_api_key"


def test_auth_scheme_has_exactly_two_variants() -> None:
    assert {kind.value for kind in AuthScheme} == {"bearer", "x_api_key"}


def test_default_auth_scheme_is_bearer() -> None:
    """grok ``#[default] Bearer`` is mirrored by :data:`DEFAULT_AUTH_SCHEME`."""
    assert DEFAULT_AUTH_SCHEME is AuthScheme.BEARER
    assert DEFAULT_AUTH_SCHEME == "bearer"


# ---------------------------------------------------------------------------
# OriginClientInfo: shape + value semantics (frozen + slots + hashable).
# ---------------------------------------------------------------------------


def test_origin_client_info_defaults_version_none() -> None:
    info = OriginClientInfo(product="grok-desktop")
    assert info.product == "grok-desktop"
    assert info.version is None


def test_origin_client_info_round_trips_product_and_version() -> None:
    info = OriginClientInfo(product="grok-web", version="1.2.3")
    assert info == OriginClientInfo(product="grok-web", version="1.2.3")


def test_origin_client_info_is_frozen() -> None:
    """``@dataclass(frozen=True)`` -> mutation raises (mirrors grok's immutable struct)."""
    info = OriginClientInfo(product="x", version="1")
    with pytest.raises(FrozenInstanceError):
        info.product = "y"  # type: ignore[misc]


def test_origin_client_info_is_hashable_and_equal() -> None:
    a = OriginClientInfo(product="x", version="1")
    b = OriginClientInfo(product="x", version="1")
    assert a == b
    assert hash(a) == hash(b)


def test_origin_client_info_declares_slots() -> None:
    """``slots=True`` -> the class declares ``__slots__`` over its fields (no
    per-instance ``__dict__``). Combined with ``frozen=True`` (see
    :func:`test_origin_client_info_is_frozen`) the attribute namespace is closed."""
    assert OriginClientInfo.__slots__ == ("product", "version")
    info = OriginClientInfo(product="x")
    assert not hasattr(info, "__dict__")


# ---------------------------------------------------------------------------
# R193 source-of-truth reversal: grok_http re-exports the SAME object.
# ---------------------------------------------------------------------------


def test_grok_http_origin_client_info_is_sampler_origin_client_info() -> None:
    """The R193 local definition is retired; ``grok_http`` imports the faithful
    source from here (dependency direction inverted, not duplicated)."""
    assert GrokHttpOriginClientInfo is OriginClientInfo


def test_grok_http_origin_client_info_remains_in_all() -> None:
    """Backward compat: ``from minimax_code.grok_http import OriginClientInfo`` still
    works (re-export preserved in ``grok_http.__all__``)."""
    import minimax_code.grok_http as grok_http

    assert "OriginClientInfo" in grok_http.__all__
    assert grok_http.OriginClientInfo is OriginClientInfo
