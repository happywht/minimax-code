"""Tests for ``minimax_code.tool_protocol`` (R82 foundation + R83 wire enums + R84 envelope).

Covers the four R82 foundation modules plus the three R83 wire-enum modules
and the two ``error_codes`` helpers R83 unblocks:

* :mod:`ids` — the 7 ``opaque_id!`` string newtypes + ``FrameSeq`` u64
  newtype + the ``IdError`` hierarchy, with constructor validation
  (empty / invalid format / reserved prefix) and transparent serialisation.
* :mod:`connection` — ``ConnectionKind`` (StrEnum) + ``ToolDefinitionMode``
  (internally-tagged enum, the crate's first).
* :mod:`handshake` — ``PROTOCOL_VERSION`` + ``HelloMsg`` / ``HelloAckMsg``
  with the ``Option::is_none`` / ``Vec::is_empty`` skip semantics.
* :mod:`error_codes` — the ``ERROR_CODES`` table + lookup helpers +
  workspace-unavailable contract + the two ``#[serde(other)]``-tolerant
  enums and their ``WorkspaceUnavailableDetails`` payload; plus the R83
  backfill ``from_tool_error_wire`` / ``workspace_unavailable_wire``.
* :mod:`error_wire` (R83) — ``ToolErrorWire`` the 15-variant
  internally-tagged enum on ``code`` (5 rename overrides) + Display
  fidelity + ``Option::is_none`` skip + id-newtype re-wrap on parse.
* :mod:`output_wire` (R83) — ``ToolOutputWire`` adjacent-tagged on
  ``kind``/``value`` + ``McpBlock`` internally-tagged on ``type``.
* :mod:`notification_wire` (R83) — ``WireToolNotification``
  adjacent-tagged on ``shape``/``value`` + the forward-compat ``Custom``
  with the spoof-resistant ``check_custom_kind`` guard.
* :mod:`envelope` (R84) — the JSON-RPC 2.0 request / notification / response
  / error wrappers, the strict literal ``"2.0"`` protocol-version marker, and
  ``JsonRpcId`` (the crate's first ``#[serde(untagged)]`` enum, String |
  Number); the response's ``result`` XOR ``error`` invariant is enforced via
  custom serde, closing the four-shape coverage (internal-tag / adjacent-tag /
  untagged / transparent-newtype).
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from minimax_code.tool_protocol import (
    ERROR_CODES,
    KNOWN_NOTIFICATION_KINDS,
    MAX_DONATION_BYTES,
    MAX_LOG_RECORDS_PER_DONATION,
    MAX_METRICS_PER_DONATION,
    MAX_SPANS_PER_DONATION,
    PROTOCOL_VERSION,
    UNKNOWN_METHOD_MSG_PREFIX,
    WORKSPACE_UNAVAILABLE_JSONRPC_CODE,
    WORKSPACE_UNAVAILABLE_MESSAGE,
    WORKSPACE_UNAVAILABLE_SUBCODE,
    BehaviorVersionUnsupported,
    BindToolSessionAck,
    BindToolSessionParams,
    Cancelled,
    ConnectionId,
    ConnectionKind,
    Custom,
    EmptyIdError,
    Execution,
    FrameSeq,
    HelloAckMsg,
    HelloMsg,
    HookKind,
    IdError,
    ImageBlock,
    Internal,
    InvalidArguments,
    InvalidFormatIdError,
    Json,
    JsonRpcError,
    JsonRpcIdNumber,
    JsonRpcIdString,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcVersion,
    JsonRpcVersionError,
    KnownVariantCollision,
    LogsDonateParams,
    Mcp,
    Method,
    MetricsDonateParams,
    NotificationFilter,
    NotificationSchemas,
    PayloadTooLarge,
    PermissionDenied,
    PingFrame,
    PongFrame,
    Registered,
    RegisterServerParams,
    RegisterToolParams,
    Rejected,
    RenderLimited,
    RequestId,
    ReservedPrefixIdError,
    ResourceBlock,
    ResponseError,
    ResponseResult,
    ServerId,
    SessionId,
    SessionMismatch,
    Shadowed,
    StreamingSpec,
    SubscribeAck,
    SubscribeNotificationsParams,
    SubscribeOutcome,
    TerminalError,
    Text,
    TextBlock,
    Timeout,
    ToolCallId,
    ToolCallParams,
    ToolCallProgressFrame,
    ToolCallResult,
    ToolCapabilities,
    ToolDefinitionMode,
    ToolDescriptionWithSchema,
    ToolId,
    ToolNotFound,
    ToolRegistration,
    ToolScope,
    ToolSearchResult,
    ToolServerConnectionStatus,
    ToolServerDisconnectReason,
    ToolServerEvictParams,
    ToolServerGetStatusParams,
    ToolServerGetStatusResult,
    ToolServerLifecycleStatus,
    ToolServerRegistration,
    ToolServerStatusPayload,
    ToolSessionBindOutcome,
    ToolSessionUnbindOutcome,
    ToolsListParams,
    ToolsListResult,
    ToolsSearchParams,
    ToolsSearchResultBody,
    TracesDonateParams,
    TransportClosed,
    TransportKind,
    UnbindToolSessionAck,
    UnbindToolSessionParams,
    UnregisterServerParams,
    UnregisterToolParams,
    UnsubscribeAck,
    UnsubscribeNotificationsParams,
    UnsubscribeOutcome,
    UnsupportedProtocolVersion,
    Updated,
    UserId,
    WireCustomNotification,
    WorkspaceGonePhase,
    WorkspaceGoneReason,
    WorkspaceUnavailableDetails,
    check_custom_kind,
    from_tool_error_wire,
    known_notification_kinds,
    numeric_for,
    string_for,
    workspace_unavailable_wire,
)
from minimax_code.tool_protocol.envelope import jsonrpc_id_from_wire
from minimax_code.tool_protocol.error_wire import from_wire as error_from_wire

# R92 + R93 + R94 + R95 — frames (tool call params/result/progress + telemetry
# donation + heartbeat + registration + per-tool session binding). The
# structs/consts/enums travel the barrel (Rust lib.rs ``pub use frames::{...}``
# is the crate's largest re-export); the from_wire converters stay
# submodule-qualified (the barrel never re-exports wire converters, mirroring
# the Rust ``pub use`` set).
from minimax_code.tool_protocol.frames import (
    bind_tool_session_ack_from_wire,
    bind_tool_session_params_from_wire,
    logs_donate_params_from_wire,
    metrics_donate_params_from_wire,
    notification_filter_from_wire,
    ping_frame_from_wire,
    pong_frame_from_wire,
    register_server_params_from_wire,
    register_tool_params_from_wire,
    subscribe_ack_from_wire,
    subscribe_notifications_params_from_wire,
    tool_call_params_from_wire,
    tool_call_progress_frame_from_wire,
    tool_call_result_from_wire,
    tool_search_result_from_wire,
    tool_server_connection_status_from_wire,
    tool_server_evict_params_from_wire,
    tool_server_get_status_params_from_wire,
    tool_server_get_status_result_from_wire,
    tool_server_status_payload_from_wire,
    tools_list_params_from_wire,
    tools_list_result_from_wire,
    tools_search_params_from_wire,
    tools_search_result_body_from_wire,
    traces_donate_params_from_wire,
    unbind_tool_session_ack_from_wire,
    unbind_tool_session_params_from_wire,
    unregister_server_params_from_wire,
    unregister_tool_params_from_wire,
    unsubscribe_ack_from_wire,
    unsubscribe_notifications_params_from_wire,
)

# R89 — hook variants are imported from the submodule (not the barrel): the
# barrel only re-exports the ``HookEvent`` union, both to mirror Rust lib.rs
# ``pub use hook::HookEvent`` and to avoid clobbering ``error_wire.Custom``
# (R83), which shares the name ``Custom`` with a hook variant.
from minimax_code.tool_protocol.hook import (
    Cancel,
    HookEvent,
    Pause,
    Resume,
    SessionEnded,
    hook_event_from_wire,
)
from minimax_code.tool_protocol.hook import (
    Custom as HookCustom,
)
from minimax_code.tool_protocol.methods import method_doc
from minimax_code.tool_protocol.notification_wire import (
    Custom as NotificationCustom,
)
from minimax_code.tool_protocol.notification_wire import (
    Known,
)
from minimax_code.tool_protocol.notification_wire import (
    from_wire as notification_from_wire,
)
from minimax_code.tool_protocol.output_wire import (
    from_wire as output_from_wire,
)
from minimax_code.tool_protocol.output_wire import (
    mcp_block_from_wire,
)
from minimax_code.tool_protocol.registration import (
    registration_outcome_from_wire,
)

# R88 — registry_error variants are imported from the submodule (not the
# barrel): the barrel only re-exports the ``RegistryError`` union, both to
# mirror Rust lib.rs and to avoid clobbering ``error_wire.SessionMismatch``
# (R83), which shares the name ``SessionMismatch`` with a registry_error variant.
from minimax_code.tool_protocol.registry_error import (
    AlreadyRegistered,
    InvalidDescription,
    RegistryError,
    ServerIdCollision,
    ServerIdInUse,
    StaleGeneration,
    registry_error_from_wire,
)
from minimax_code.tool_protocol.registry_error import (
    SessionMismatch as RegistrySessionMismatch,
)

# R90 — session_event + turn_hook leaf. session_event variant dataclasses
# stay in-submodule (only the union + two nested enums travel the barrel),
# mirroring R88/R89. turn_hook is `pub mod` with no `pub use` in lib.rs, so
# its symbols (TurnHookOutcome / TURN_HOOK_KIND) are module-qualified only.
# R91 — extends the turn_hook import to the full module surface (payloads,
# request union, reply/ack types, deny_unknown_fields structs, and the
# from_wire converters) — still module-qualified, still outside the barrel.
from minimax_code.tool_protocol.session_event import (
    PhaseChanged,
    SessionPhase,
    ToolCallCompleted,
    ToolCallOutcome,
    ToolCallStarted,
    TurnEnded,
    TurnStarted,
    Unknown,
    session_event_from_wire,
)
from minimax_code.tool_protocol.turn_hook import (
    AFTER_TURN_KIND,
    BEFORE_TURN_KIND,
    DEFAULT_SCHEMA_VERSION,
    DEFAULT_SESSION_RELATIONSHIP,
    TURN_HOOK_KIND,
    AfterTurnAckPayload,
    AfterTurnAckStatus,
    AfterTurnPayload,
    BeforeTurnPayload,
    HookInjection,
    HookReply,
    InjectionRole,
    TurnControl,
    TurnHookOutcome,
    TurnHookRequest,
    TurnHookRequestAfter,
    TurnHookRequestBefore,
    after_turn_ack_payload_from_wire,
    after_turn_payload_from_wire,
    before_turn_payload_from_wire,
    hook_injection_from_wire,
    hook_reply_from_wire,
    turn_hook_request_from_wire,
)
from minimax_code.tool_types import ToolDescription

# -----------------------------------------------------------------------
# Opaque string id newtypes.
# -----------------------------------------------------------------------


SIMPLE_IDS = [SessionId, UserId, ConnectionId, RequestId, ToolCallId]


class TestOpaqueIds:
    """The five plain non-empty-only string newtypes (``opaque_id!``)."""

    @pytest.mark.parametrize("cls", SIMPLE_IDS)
    def test_construction_yields_str_subclass(self, cls):
        v = cls("abc")
        assert isinstance(v, str)
        assert v == "abc"
        assert type(v) is cls

    @pytest.mark.parametrize("cls", SIMPLE_IDS)
    def test_empty_rejected_with_empty_id_error(self, cls):
        with pytest.raises(EmptyIdError):
            cls("")

    @pytest.mark.parametrize("cls", SIMPLE_IDS)
    def test_empty_error_is_id_error_subclass(self, cls):
        # All variants are IdError subclasses so a single except-clause
        # catches every construction failure (mirrors ``match`` on the enum).
        with pytest.raises(IdError):
            cls("")

    @pytest.mark.parametrize("cls", SIMPLE_IDS)
    def test_non_string_rejected(self, cls):
        with pytest.raises(IdError):
            cls(123)  # type: ignore[arg-type]

    @pytest.mark.parametrize("cls", SIMPLE_IDS)
    def test_repr_is_typed(self, cls):
        assert repr(cls("x")) == f"{cls.__name__}('x')"

    @pytest.mark.parametrize("cls", SIMPLE_IDS)
    def test_serialises_as_bare_string(self, cls):
        # #[serde(transparent)] → json.dumps emits the bare inner string.
        assert json.dumps(cls("s1")) == json.dumps("s1") == '"s1"'
        assert json.loads(json.dumps(cls("s1"))) == "s1"

    @pytest.mark.parametrize("cls", SIMPLE_IDS)
    def test_round_trip_via_json(self, cls):
        original = cls("opaque-value")
        wire = json.dumps(original)
        rebuilt = cls(json.loads(wire))
        assert rebuilt == original
        assert type(rebuilt) is cls

    @pytest.mark.parametrize("cls", SIMPLE_IDS)
    def test_equality_is_by_value(self, cls):
        assert cls("a") == cls("a")
        assert cls("a") == "a"  # str base equality
        assert cls("a") != cls("b")
        assert hash(cls("a")) == hash("a")  # str-base hash


# -----------------------------------------------------------------------
# ToolId — format validation.
# -----------------------------------------------------------------------


class TestToolId:
    """``ToolId`` segment-format validation (``is_well_formed_tool_id``)."""

    @pytest.mark.parametrize(
        "value",
        [
            "name",  # single segment
            "ns:name",  # two segments
            "a-b:c_d",  # segments with - and _
            "Cap:Name",  # mixed case
            "123:456",  # numeric segments
            "x" * 50,  # long single segment
        ],
    )
    def test_well_formed_accepted(self, value):
        tid = ToolId(value)
        assert tid == value
        assert str(tid) == value

    @pytest.mark.parametrize(
        "value",
        [
            "a:b:c",  # three segments (second colon) — Rust's `_ => false`
            ":name",  # empty first segment
            "ns:",  # empty second segment
            "ns name",  # space (not an id char)
            "ns.näme",  # dot + non-ASCII
            "a/b",  # slash
            "",  # empty (EmptyIdError first, but still rejected)
        ],
    )
    def test_malformed_rejected_with_invalid_format(self, value):
        if value == "":
            # Empty short-circuits to EmptyIdError before the format check.
            with pytest.raises(EmptyIdError):
                ToolId(value)
        else:
            with pytest.raises(InvalidFormatIdError) as exc:
                ToolId(value)
            assert exc.value.value == value

    def test_invalid_format_carries_value(self):
        err = InvalidFormatIdError("bad value")
        assert err.value == "bad value"
        assert "bad value" in str(err)

    def test_round_trip(self):
        for value in ("name", "ns:name", "a-b:c_d"):
            tid = ToolId(value)
            assert ToolId(str(tid)) == tid


# -----------------------------------------------------------------------
# ServerId — reserved prefix + synthesize bypass.
# -----------------------------------------------------------------------


class TestServerId:
    """``ServerId`` reserved-prefix rejection + the synthesize bypass."""

    def test_plain_value_accepted(self):
        sid = ServerId("srv-1")
        assert sid == "srv-1"

    def test_empty_rejected(self):
        with pytest.raises(EmptyIdError):
            ServerId("")

    @pytest.mark.parametrize("value", ["auto:foo", "auto:", "auto:tool:bar"])
    def test_reserved_prefix_rejected(self, value):
        with pytest.raises(ReservedPrefixIdError) as exc:
            ServerId(value)
        assert exc.value.value == value

    def test_reserved_prefix_error_is_id_error(self):
        with pytest.raises(IdError):
            ServerId("auto:x")

    def test_synthesize_bypasses_reserved_prefix_check(self):
        # The hub-private constructor produces an ``auto:tool:{tool_id}`` id
        # that would be rejected if it went through ``__new__``.
        synthesized = ServerId.synthesize_for_tool(
            ConnectionId("conn-1"), ToolId("ns:name")
        )
        assert isinstance(synthesized, ServerId)
        assert synthesized == "auto:tool:ns:name"

    def test_synthesize_signature_includes_connection_id(self):
        # ``connection_id`` is part of the signature even though the current
        # encoding does not mix it in; callers cannot omit the scope.
        synthesized = ServerId.synthesize_for_tool(
            ConnectionId("c1"), ToolId("t1")
        )
        assert "t1" in str(synthesized)
        assert "c1" not in str(synthesized)  # not mixed in (current encoding)


# -----------------------------------------------------------------------
# FrameSeq — transparent u64 newtype.
# -----------------------------------------------------------------------


class TestFrameSeq:
    """``FrameSeq`` — the crate's first transparent non-string newtype."""

    def test_default_is_zero(self):
        assert FrameSeq().get() == 0
        assert FrameSeq.new(0).get() == 0

    def test_new_and_get(self):
        seq = FrameSeq.new(42)
        assert seq.get() == 42
        assert int(seq) == 42

    def test_from_u64_construction(self):
        # ``From<u64>`` → ``__init__``.
        assert FrameSeq(7).get() == 7

    def test_to_wire_is_bare_int(self):
        assert FrameSeq(5).to_wire() == 5
        # #[serde(transparent)] → json emits the bare integer.
        assert json.dumps(FrameSeq(5).to_wire()) == "5"

    def test_from_wire_round_trip(self):
        for n in (0, 1, 100, 2**64 - 1):
            seq = FrameSeq.from_wire(n)
            assert seq.get() == n
            assert FrameSeq.from_wire(seq.to_wire()) == seq

    def test_from_wire_rejects_bool(self):
        # bool is an int subclass but must not widen the u64 wire type.
        with pytest.raises(ValueError):
            FrameSeq.from_wire(True)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            FrameSeq.from_wire(False)  # type: ignore[arg-type]

    def test_from_wire_rejects_non_int(self):
        with pytest.raises(ValueError):
            FrameSeq.from_wire("5")  # type: ignore[arg-type]

    def test_constructor_rejects_bool_and_negative(self):
        with pytest.raises(TypeError):
            FrameSeq(True)  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            FrameSeq(-1)

    def test_ordering(self):
        # Rust derives Ord/PartialOrd (u64).
        assert FrameSeq(1) < FrameSeq(2)
        assert FrameSeq(2) > FrameSeq(1)
        assert FrameSeq(1) <= FrameSeq(1)
        assert FrameSeq(1) == FrameSeq(1)
        assert FrameSeq(1) != FrameSeq(2)

    def test_hash_and_repr(self):
        assert hash(FrameSeq(3)) == hash(FrameSeq(3))
        assert repr(FrameSeq(3)) == "FrameSeq(3)"
        assert str(FrameSeq(3)) == "3"

    def test_equality_with_plain_int_is_not_supported(self):
        # FrameSeq is not an int subclass; cross-type equality returns
        # NotImplemented (falls back to identity, which is False).
        assert FrameSeq(3).__eq__(3) is NotImplemented


# -----------------------------------------------------------------------
# connection — ConnectionKind + ToolDefinitionMode.
# -----------------------------------------------------------------------


class TestConnectionKind:
    """``ConnectionKind`` — plain snake_case StrEnum."""

    def test_wire_values(self):
        assert ConnectionKind.Harness.value == "harness"
        assert ConnectionKind.ToolServer.value == "tool_server"

    def test_serde_bare_string(self):
        assert json.dumps(ConnectionKind.Harness.value) == '"harness"'
        assert ConnectionKind("tool_server") is ConnectionKind.ToolServer

    def test_membership(self):
        assert {m.value for m in ConnectionKind} == {"harness", "tool_server"}


class TestToolDefinitionMode:
    """``ToolDefinitionMode`` — internally-tagged enum on ``mode``."""

    def test_full_to_wire(self):
        assert ToolDefinitionMode.full().to_wire() == {"mode": "full"}

    def test_concise_to_wire(self):
        mode = ToolDefinitionMode.concise(ToolId("search"), ToolId("call"))
        assert mode.to_wire() == {
            "mode": "concise",
            "meta_search": "search",
            "meta_call": "call",
        }

    def test_full_round_trip(self):
        original = ToolDefinitionMode.full()
        rebuilt = ToolDefinitionMode.from_wire(original.to_wire())
        assert rebuilt == original
        assert rebuilt.mode == "full"

    def test_concise_round_trip(self):
        original = ToolDefinitionMode.concise(ToolId("a:b"), ToolId("c"))
        rebuilt = ToolDefinitionMode.from_wire(original.to_wire())
        assert rebuilt == original
        assert rebuilt.meta_search == ToolId("a:b")
        assert rebuilt.meta_call == ToolId("c")

    def test_concise_revalidates_tool_id_format(self):
        # from_wire routes meta_search/meta_call through ToolId().
        with pytest.raises(InvalidFormatIdError):
            ToolDefinitionMode.from_wire(
                {"mode": "concise", "meta_search": "a:b:c", "meta_call": "ok"}
            )

    def test_unknown_mode_rejected(self):
        with pytest.raises(ValueError):
            ToolDefinitionMode.from_wire({"mode": "weird"})

    def test_repr(self):
        assert repr(ToolDefinitionMode.full()) == "ToolDefinitionMode.full()"
        assert "concise" in repr(
            ToolDefinitionMode.concise(ToolId("s"), ToolId("c"))
        )

    def test_equality_and_hash(self):
        a = ToolDefinitionMode.full()
        b = ToolDefinitionMode.full()
        assert a == b
        assert hash(a) == hash(b)
        assert a != ToolDefinitionMode.concise(ToolId("s"), ToolId("c"))


# -----------------------------------------------------------------------
# handshake — HelloMsg + HelloAckMsg.
# -----------------------------------------------------------------------


class TestHandshake:
    """Handshake structs + ``PROTOCOL_VERSION``."""

    def test_protocol_version_pinned(self):
        assert PROTOCOL_VERSION == "1.0.0"

    def test_hello_msg_full_fields(self):
        msg = HelloMsg(
            protocol_version=PROTOCOL_VERSION,
            kind=ConnectionKind.ToolServer,
            server_id=ServerId("srv-1"),
            description="my server",
            metadata={"k": "v"},
        )
        wire = msg.to_wire()
        assert wire == {
            "protocol_version": "1.0.0",
            "kind": "tool_server",
            "server_id": "srv-1",
            "description": "my server",
            "metadata": {"k": "v"},
        }

    def test_hello_msg_option_none_fields_omitted(self):
        # skip_serializing_if = "Option::is_none".
        msg = HelloMsg(
            protocol_version=PROTOCOL_VERSION, kind=ConnectionKind.Harness
        )
        wire = msg.to_wire()
        assert wire == {"protocol_version": "1.0.0", "kind": "harness"}
        assert "server_id" not in wire
        assert "description" not in wire
        assert "metadata" not in wire

    def test_hello_msg_round_trip_full(self):
        msg = HelloMsg(
            protocol_version=PROTOCOL_VERSION,
            kind=ConnectionKind.ToolServer,
            server_id=ServerId("srv-1"),
            description="d",
            metadata=[1, 2, 3],  # arbitrary JSON value
        )
        rebuilt = HelloMsg.from_wire(msg.to_wire())
        assert rebuilt.protocol_version == msg.protocol_version
        assert rebuilt.kind is ConnectionKind.ToolServer
        assert rebuilt.server_id == msg.server_id
        assert rebuilt.description == msg.description
        assert rebuilt.metadata == msg.metadata

    def test_hello_msg_round_trip_absent_optionals(self):
        wire = {"protocol_version": "1.0.0", "kind": "harness"}
        rebuilt = HelloMsg.from_wire(wire)
        assert rebuilt.server_id is None
        assert rebuilt.description is None
        assert rebuilt.metadata is None

    def test_hello_ack_msg_full(self):
        ack = HelloAckMsg(
            connection_id=ConnectionId("c1"),
            user_id=UserId("u1"),
            computer_hub_version="0.1.0",
            supported_protocol_versions=["1.0.0", "0.9.0"],
            capabilities=["session_attach_server"],
        )
        wire = ack.to_wire()
        assert wire == {
            "connection_id": "c1",
            "user_id": "u1",
            "computer_hub_version": "0.1.0",
            "supported_protocol_versions": ["1.0.0", "0.9.0"],
            "capabilities": ["session_attach_server"],
        }

    def test_hello_ack_capabilities_omitted_when_empty(self):
        # skip_serializing_if = "Vec::is_empty".
        for caps in (None, []):
            ack = HelloAckMsg(
                connection_id=ConnectionId("c1"),
                user_id=UserId("u1"),
                computer_hub_version="0.1.0",
                supported_protocol_versions=["1.0.0"],
                capabilities=caps,
            )
            wire = ack.to_wire()
            assert "capabilities" not in wire

    def test_hello_ack_round_trip(self):
        ack = HelloAckMsg(
            connection_id=ConnectionId("c1"),
            user_id=UserId("u1"),
            computer_hub_version="0.1.0",
            supported_protocol_versions=["1.0.0"],
            capabilities=["a", "b"],
        )
        rebuilt = HelloAckMsg.from_wire(ack.to_wire())
        assert rebuilt.connection_id == ack.connection_id
        assert rebuilt.user_id == ack.user_id
        assert rebuilt.capabilities == ["a", "b"]

    def test_hello_ack_absent_capabilities_defaults_none(self):
        wire = {
            "connection_id": "c1",
            "user_id": "u1",
            "computer_hub_version": "0.1.0",
            "supported_protocol_versions": ["1.0.0"],
        }
        rebuilt = HelloAckMsg.from_wire(wire)
        assert rebuilt.capabilities is None


# -----------------------------------------------------------------------
# error_codes — table + lookups + workspace-unavailable contract.
# -----------------------------------------------------------------------


class TestErrorCodesTable:
    """The fixed ``ERROR_CODES`` table + bidirectional lookups."""

    def test_table_size(self):
        assert len(ERROR_CODES) == 28

    def test_numeric_column_unique(self):
        numerics = [n for n, _ in ERROR_CODES]
        assert len(numerics) == len(set(numerics))

    def test_string_column_unique(self):
        strings = [s for _, s in ERROR_CODES]
        assert len(strings) == len(set(strings))

    def test_numeric_for_known(self):
        assert numeric_for("parse_error") == -32700
        assert numeric_for("tool_server_gone") == -32005
        assert numeric_for("rate_limited") == -32099

    def test_numeric_for_unknown_is_none(self):
        assert numeric_for("not_a_real_code") is None

    def test_string_for_known(self):
        assert string_for(-32700) == "parse_error"
        assert string_for(-32603) == "internal_error"

    def test_string_for_unknown_is_none(self):
        assert string_for(-99999) is None

    def test_round_trip_for_every_entry(self):
        for numeric, s in ERROR_CODES:
            assert numeric_for(s) == numeric
            assert string_for(numeric) == s

    def test_known_jsonrpc_standard_codes_present(self):
        # The JSON-RPC 2.0 standard error codes are anchored in the table.
        for code in (-32700, -32600, -32601, -32602, -32603):
            assert string_for(code) is not None


class TestWorkspaceUnavailableContract:
    """The workspace-unavailable constants + tolerant enums + details."""

    def test_constants_pinned(self):
        assert WORKSPACE_UNAVAILABLE_SUBCODE == "workspace_unavailable"
        assert (
            WORKSPACE_UNAVAILABLE_MESSAGE
            == "workspace server gone; re-provision and retry"
        )
        assert WORKSPACE_UNAVAILABLE_JSONRPC_CODE == -32005

    def test_jsonrpc_code_shares_tool_server_gone_numeric(self):
        # Recognizers key on data.subcode, not this companion numeric.
        assert (
            numeric_for("tool_server_gone") == WORKSPACE_UNAVAILABLE_JSONRPC_CODE
        )

    @pytest.mark.parametrize(
        "reason",
        [
            WorkspaceGoneReason.IdleTimeout,
            WorkspaceGoneReason.Disconnect,
            WorkspaceGoneReason.Shutdown,
            WorkspaceGoneReason.NotBound,
            WorkspaceGoneReason.InstanceGone,
            WorkspaceGoneReason.Unknown,
        ],
    )
    def test_reason_serialises_to_snake_case(self, reason):
        # Display form is the rename_all snake_case string.
        expected = {
            WorkspaceGoneReason.IdleTimeout: "idle_timeout",
            WorkspaceGoneReason.Disconnect: "disconnect",
            WorkspaceGoneReason.Shutdown: "shutdown",
            WorkspaceGoneReason.NotBound: "not_bound",
            WorkspaceGoneReason.InstanceGone: "instance_gone",
            WorkspaceGoneReason.Unknown: "unknown",
        }[reason]
        assert reason.value == expected

    @pytest.mark.parametrize(
        "phase",
        [
            WorkspaceGonePhase.InFlightCancelled,
            WorkspaceGonePhase.RouteMissing,
            WorkspaceGonePhase.Attach,
            WorkspaceGonePhase.Unknown,
        ],
    )
    def test_phase_serialises_to_snake_case(self, phase):
        expected = {
            WorkspaceGonePhase.InFlightCancelled: "in_flight_cancelled",
            WorkspaceGonePhase.RouteMissing: "route_missing",
            WorkspaceGonePhase.Attach: "attach",
            WorkspaceGonePhase.Unknown: "unknown",
        }[phase]
        assert phase.value == expected

    def test_reason_from_wire_known(self):
        assert (
            WorkspaceGoneReason.from_wire("idle_timeout")
            is WorkspaceGoneReason.IdleTimeout
        )
        assert (
            WorkspaceGoneReason.from_wire("instance_gone")
            is WorkspaceGoneReason.InstanceGone
        )

    def test_reason_from_wire_unknown_falls_back_to_unknown(self):
        # #[serde(other)]: a value a newer peer emits absorbs into Unknown.
        assert (
            WorkspaceGoneReason.from_wire("reason_from_a_newer_hub")
            is WorkspaceGoneReason.Unknown
        )

    def test_reason_unknown_string_round_trips(self):
        assert WorkspaceGoneReason.from_wire("unknown") is WorkspaceGoneReason.Unknown

    def test_phase_from_wire_unknown_falls_back(self):
        assert (
            WorkspaceGonePhase.from_wire("phase_from_a_newer_hub")
            is WorkspaceGonePhase.Unknown
        )

    def test_details_round_trip(self):
        details = WorkspaceUnavailableDetails(
            code=WORKSPACE_UNAVAILABLE_SUBCODE,
            reason=WorkspaceGoneReason.IdleTimeout,
            phase=WorkspaceGonePhase.RouteMissing,
            retryable=True,
        )
        wire = details.to_wire()
        assert wire == {
            "code": "workspace_unavailable",
            "reason": "idle_timeout",
            "phase": "route_missing",
            "retryable": True,
        }
        rebuilt = WorkspaceUnavailableDetails.from_wire(wire)
        assert rebuilt == details

    def test_details_tolerates_unknown_reason_and_phase(self):
        # Independently-deployed peers may emit unknown values; the typed
        # parse absorbs them rather than failing.
        wire = {
            "code": WORKSPACE_UNAVAILABLE_SUBCODE,
            "reason": "reason_from_a_newer_hub",
            "phase": "phase_from_a_newer_hub",
            "retryable": True,
        }
        rebuilt = WorkspaceUnavailableDetails.from_wire(wire)
        assert rebuilt.reason is WorkspaceGoneReason.Unknown
        assert rebuilt.phase is WorkspaceGonePhase.Unknown
        assert rebuilt.retryable is True


# -----------------------------------------------------------------------
# Package surface.
# -----------------------------------------------------------------------


class TestPackageSurface:
    """The R82 barrel re-exports the foundation-layer symbols."""

    def test_barrel_exposes_ids(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "SessionId",
            "UserId",
            "ConnectionId",
            "RequestId",
            "ToolCallId",
            "ServerId",
            "ToolId",
            "FrameSeq",
            "IdError",
        ):
            assert hasattr(pkg, name), name

    def test_barrel_exposes_connection_handshake_error_codes(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "ConnectionKind",
            "ToolDefinitionMode",
            "PROTOCOL_VERSION",
            "HelloMsg",
            "HelloAckMsg",
            "ERROR_CODES",
            "numeric_for",
            "string_for",
            "WorkspaceGoneReason",
            "WorkspaceGonePhase",
            "WorkspaceUnavailableDetails",
        ):
            assert hasattr(pkg, name), name


# -----------------------------------------------------------------------
# error_wire — ToolErrorWire (15-variant internally-tagged enum on code).
# -----------------------------------------------------------------------


class TestToolErrorWire:
    """``ToolErrorWire`` — the 15-variant internally-tagged wire error enum."""

    def test_tool_not_found_to_wire(self):
        err = ToolNotFound(tool_id=ToolId("search"))
        assert err.to_wire() == {"code": "tool_not_found", "tool_id": "search"}

    def test_session_mismatch_to_wire(self):
        assert SessionMismatch().to_wire() == {"code": "session_mismatch"}

    def test_permission_denied_rename_to_forbidden(self):
        # #[serde(rename = "forbidden")] overrides snake_case(PermissionDenied).
        assert PermissionDenied(reason="no scope").to_wire() == {
            "code": "forbidden",
            "reason": "no scope",
        }

    def test_transport_closed_rename_to_connection_lost(self):
        err = TransportClosed(tool_id=ToolId("ns:t"))
        assert err.to_wire() == {"code": "connection_lost", "tool_id": "ns:t"}

    def test_invalid_arguments_rename_and_option_skip(self):
        full = InvalidArguments(message="bad").to_wire()
        assert full == {"code": "invalid_params", "message": "bad"}
        # details Option::None is omitted.
        assert "details" not in full

    def test_invalid_arguments_with_details(self):
        err = InvalidArguments(message="bad", details={"x": 1})
        assert err.to_wire() == {
            "code": "invalid_params",
            "message": "bad",
            "details": {"x": 1},
        }

    def test_payload_too_large_rename_to_frame_too_large(self):
        err = PayloadTooLarge(bytes=999, limit=512)
        assert err.to_wire() == {
            "code": "frame_too_large",
            "bytes": 999,
            "limit": 512,
        }

    def test_internal_rename_and_both_options_skip(self):
        # Both Option fields None → only the discriminator survives.
        assert Internal().to_wire() == {"code": "internal_error"}
        assert Internal(detail="boom").to_wire() == {
            "code": "internal_error",
            "detail": "boom",
        }
        assert Internal(request_id=RequestId("r1")).to_wire() == {
            "code": "internal_error",
            "request_id": "r1",
        }

    def test_render_limited_card_id_option_skipped(self):
        without = RenderLimited(tool_id=ToolId("t1"), reason="budget").to_wire()
        assert without == {
            "code": "render_limited",
            "tool_id": "t1",
            "reason": "budget",
        }
        assert "card_id" not in without
        with_card = RenderLimited(
            tool_id=ToolId("t1"), reason="budget", card_id="c1"
        ).to_wire()
        assert with_card["card_id"] == "c1"

    def test_custom_shape_and_option_skip(self):
        bare = Custom(subcode="my.x", message="hi").to_wire()
        assert bare == {"code": "custom", "subcode": "my.x", "message": "hi"}
        assert "details" not in bare
        with_details = Custom(subcode="my.x", message="hi", details={"k": 1}).to_wire()
        assert with_details["details"] == {"k": 1}

    @pytest.mark.parametrize(
        "err,expected_code",
        [
            (ToolNotFound(tool_id=ToolId("t")), "tool_not_found"),
            (SessionMismatch(), "session_mismatch"),
            (PermissionDenied(reason="r"), "forbidden"),
            (TransportClosed(tool_id=ToolId("t")), "connection_lost"),
            (Timeout(tool_id=ToolId("t"), elapsed_ms=10), "timeout"),
            (Cancelled(tool_id=ToolId("t")), "cancelled"),
            (InvalidArguments(message="m"), "invalid_params"),
            (Execution(tool_id=ToolId("t"), message="m"), "execution"),
            (UnsupportedProtocolVersion(supported=["1"]), "unsupported_protocol_version"),
            (PayloadTooLarge(bytes=1, limit=2), "frame_too_large"),
            (
                BehaviorVersionUnsupported(tool_id=ToolId("t"), requested="2"),
                "behavior_version_unsupported",
            ),
            (RenderLimited(tool_id=ToolId("t"), reason="r"), "render_limited"),
            (TerminalError(tool_id=ToolId("t"), message="m"), "terminal_error"),
            (Internal(), "internal_error"),
            (Custom(subcode="x", message="m"), "custom"),
        ],
    )
    def test_each_variant_carries_its_code_tag(self, err, expected_code):
        assert err.code == expected_code
        assert err.to_wire()["code"] == expected_code

    @pytest.mark.parametrize(
        "err",
        [
            ToolNotFound(tool_id=ToolId("search")),
            SessionMismatch(),
            PermissionDenied(reason="no"),
            TransportClosed(tool_id=ToolId("t")),
            Timeout(tool_id=ToolId("t"), elapsed_ms=5),
            Cancelled(tool_id=ToolId("t")),
            InvalidArguments(message="m"),
            InvalidArguments(message="m", details={"d": 1}),
            Execution(tool_id=ToolId("t"), message="m"),
            UnsupportedProtocolVersion(supported=["1.0"]),
            PayloadTooLarge(bytes=1, limit=2),
            BehaviorVersionUnsupported(tool_id=ToolId("t"), requested="9"),
            RenderLimited(tool_id=ToolId("t"), reason="r"),
            RenderLimited(tool_id=ToolId("t"), reason="r", card_id="c"),
            TerminalError(tool_id=ToolId("t"), message="m"),
            Internal(),
            Internal(detail="d"),
            Internal(request_id=RequestId("r1")),
            Internal(detail="d", request_id=RequestId("r1")),
            Custom(subcode="s", message="m"),
            Custom(subcode="s", message="m", details={"k": 1}),
        ],
    )
    def test_round_trip_to_wire_from_wire(self, err):
        rebuilt = error_from_wire(err.to_wire())
        assert type(rebuilt) is type(err)
        assert rebuilt == err

    def test_from_wire_rewraps_tool_id_newtype(self):
        # The dataclass __init__ assigns the raw value; from_wire must re-wrap
        # so the field is a typed ToolId, not a bare str.
        err = error_from_wire({"code": "tool_not_found", "tool_id": "ns:t"})
        assert type(err.tool_id) is ToolId
        assert err.tool_id == "ns:t"

    def test_from_wire_rewraps_request_id_newtype(self):
        err = error_from_wire(
            {"code": "internal_error", "request_id": "r1", "detail": "d"}
        )
        assert type(err.request_id) is RequestId
        assert err.detail == "d"

    def test_from_wire_absent_option_defaults_none(self):
        # #[serde(default)] → missing fields become None.
        err = error_from_wire({"code": "internal_error"})
        assert err.request_id is None
        assert err.detail is None

    def test_from_wire_unknown_code_raises(self):
        with pytest.raises(ValueError):
            error_from_wire({"code": "not_a_real_variant"})

    # -- thiserror Display fidelity (str(err)) -----------------------------

    def test_display_tool_not_found(self):
        assert str(ToolNotFound(tool_id=ToolId("t"))) == "tool not found: t"

    def test_display_session_mismatch(self):
        assert str(SessionMismatch()) == "session mismatch"

    def test_display_permission_denied(self):
        assert str(PermissionDenied(reason="no scope")) == "permission denied: no scope"

    def test_display_behavior_version_uses_underscore(self):
        # Reproduced verbatim: "behavior_version unsupported" (underscore, not space).
        err = BehaviorVersionUnsupported(tool_id=ToolId("t"), requested="9")
        assert str(err) == "behavior_version unsupported"

    def test_display_internal_without_detail(self):
        assert str(Internal()) == "internal error"

    def test_display_internal_with_detail(self):
        assert str(Internal(detail="boom")) == "internal error: boom"

    def test_display_custom_uses_em_dash(self):
        # Reproduced verbatim: em-dash (U+2014) between subcode and message.
        assert str(Custom(subcode="my.x", message="hi")) == "custom: my.x — hi"


# -----------------------------------------------------------------------
# output_wire — ToolOutputWire (adjacent-tagged) + McpBlock (internally-tagged).
# -----------------------------------------------------------------------


class TestToolOutputWire:
    """``ToolOutputWire`` adjacent-tagged on ``kind``/``value``."""

    def test_text_to_wire(self):
        assert Text(text="hello").to_wire() == {"kind": "text", "value": "hello"}

    def test_json_to_wire(self):
        payload = {"n": 1, "list": [1, 2]}
        assert Json(json=payload).to_wire() == {"kind": "json", "value": payload}

    def test_mcp_to_wire_nests_blocks(self):
        out = Mcp(blocks=[TextBlock(text="hi")])
        assert out.to_wire() == {
            "kind": "mcp",
            "value": {"blocks": [{"type": "text", "text": "hi"}]},
        }

    def test_text_round_trip(self):
        original = Text(text="hello")
        rebuilt = output_from_wire(original.to_wire())
        assert type(rebuilt) is Text
        assert rebuilt == original

    def test_json_round_trip(self):
        original = Json(json={"k": "v"})
        rebuilt = output_from_wire(original.to_wire())
        assert rebuilt == original

    def test_mcp_round_trip(self):
        original = Mcp(
            blocks=[TextBlock(text="hi"), ImageBlock(mime_type="image/png", data="b64")]
        )
        rebuilt = output_from_wire(original.to_wire())
        assert rebuilt == original
        assert isinstance(rebuilt.blocks[1], ImageBlock)

    def test_from_wire_unknown_kind_raises(self):
        with pytest.raises(ValueError):
            output_from_wire({"kind": "weird", "value": None})


class TestMcpBlock:
    """``McpBlock`` internally-tagged on ``type``."""

    def test_text_block(self):
        b = TextBlock(text="hi")
        assert b.to_wire() == {"type": "text", "text": "hi"}
        assert mcp_block_from_wire(b.to_wire()) == b

    def test_image_block(self):
        b = ImageBlock(mime_type="image/png", data="b64")
        assert b.to_wire() == {
            "type": "image",
            "mime_type": "image/png",
            "data": "b64",
        }
        assert mcp_block_from_wire(b.to_wire()) == b

    def test_resource_block_skips_none_optionals(self):
        bare = ResourceBlock(uri="file:///x").to_wire()
        assert bare == {"type": "resource", "uri": "file:///x"}
        assert "mime_type" not in bare
        assert "text" not in bare

    def test_resource_block_full(self):
        b = ResourceBlock(uri="u", mime_type="text/plain", text="body")
        assert b.to_wire() == {
            "type": "resource",
            "uri": "u",
            "mime_type": "text/plain",
            "text": "body",
        }
        assert mcp_block_from_wire(b.to_wire()) == b

    def test_resource_block_round_trip_absent_optionals(self):
        rebuilt = mcp_block_from_wire({"type": "resource", "uri": "u"})
        assert rebuilt.uri == "u"
        assert rebuilt.mime_type is None
        assert rebuilt.text is None

    def test_from_wire_unknown_type_raises(self):
        with pytest.raises(ValueError):
            mcp_block_from_wire({"type": "audio", "data": "x"})


# -----------------------------------------------------------------------
# notification_wire — WireToolNotification + forward-compat Custom.
# -----------------------------------------------------------------------


class TestWireToolNotification:
    """``WireToolNotification`` adjacent-tagged + ``WireCustomNotification``.

    The notification ``Custom`` is reached via the ``NotificationCustom`` alias
    so it does not collide with the error ``Custom`` re-exported by the barrel.
    """

    def test_known_to_wire(self):
        n = Known(value={"type": "FileWritten", "path": "a"})
        assert n.to_wire() == {
            "shape": "known",
            "value": {"type": "FileWritten", "path": "a"},
        }

    def test_known_round_trip(self):
        original = Known(value={"type": "FileWritten", "path": "a"})
        rebuilt = notification_from_wire(original.to_wire())
        assert type(rebuilt) is Known
        assert rebuilt == original

    def test_custom_to_wire(self):
        custom_notif = WireCustomNotification(kind="my.progress", payload={"p": 1})
        wrapper = NotificationCustom(notification=custom_notif)
        assert wrapper.to_wire() == {
            "shape": "custom",
            "value": {"kind": "my.progress", "payload": {"p": 1}},
        }

    def test_custom_round_trip(self):
        custom_notif = WireCustomNotification(kind="my.x", payload=[1, 2])
        wrapper = NotificationCustom(notification=custom_notif)
        rebuilt = notification_from_wire(wrapper.to_wire())
        assert type(rebuilt) is NotificationCustom
        assert rebuilt.notification.kind == "my.x"
        assert rebuilt.notification.payload == [1, 2]

    def test_from_wire_unknown_shape_raises(self):
        with pytest.raises(ValueError):
            notification_from_wire({"shape": "weird", "value": None})


class TestKnownNotificationKinds:
    """``KNOWN_NOTIFICATION_KINDS`` + ``check_custom_kind`` spoof guard."""

    def test_known_kinds_count(self):
        assert len(KNOWN_NOTIFICATION_KINDS) == 19

    def test_known_notification_kinds_function_matches_constant(self):
        assert known_notification_kinds() == KNOWN_NOTIFICATION_KINDS
        assert isinstance(known_notification_kinds(), tuple)

    def test_known_kinds_are_pascalcase(self):
        for kind in KNOWN_NOTIFICATION_KINDS:
            assert kind[:1].isupper(), kind
            assert "_" not in kind, kind

    @pytest.mark.parametrize("kind", list(KNOWN_NOTIFICATION_KINDS))
    def test_check_custom_kind_rejects_known_kind(self, kind):
        with pytest.raises(KnownVariantCollision) as exc:
            check_custom_kind(kind)
        assert exc.value.kind == kind

    def test_check_custom_kind_accepts_unknown_kind(self):
        # Returns None (the Ok(()) arm) for non-colliding kinds.
        assert check_custom_kind("my.custom.kind") is None

    def test_collision_message_uses_repr_quoting(self):
        # thiserror {kind:?} → Python repr → the kind appears quoted.
        try:
            check_custom_kind("FileWritten")
        except KnownVariantCollision as exc:
            assert "'FileWritten'" in str(exc)
            assert exc.kind == "FileWritten"
        else:  # pragma: no cover
            raise AssertionError("expected KnownVariantCollision")


# -----------------------------------------------------------------------
# error_codes R83 backfill — from_tool_error_wire + workspace_unavailable_wire.
# -----------------------------------------------------------------------


class TestErrorCodesBackfill:
    """The two R82-deferred helpers, unblocked by R83's ``ToolErrorWire``."""

    @pytest.mark.parametrize(
        "err,expected",
        [
            (ToolNotFound(tool_id=ToolId("t")), -32011),
            (SessionMismatch(), -32600),
            (PermissionDenied(reason="r"), -32003),
            (TransportClosed(tool_id=ToolId("t")), -32004),
            (Timeout(tool_id=ToolId("t"), elapsed_ms=1), -32001),
            (Cancelled(tool_id=ToolId("t")), -32603),
            (InvalidArguments(message="m"), -32602),
            (Execution(tool_id=ToolId("t"), message="m"), -32603),
            (UnsupportedProtocolVersion(supported=["1"]), -32605),
            (PayloadTooLarge(bytes=1, limit=2), -32018),
            (BehaviorVersionUnsupported(tool_id=ToolId("t"), requested="9"), -32020),
            (Internal(), -32603),
            (RenderLimited(tool_id=ToolId("t"), reason="r"), -32023),
            (TerminalError(tool_id=ToolId("t"), message="m"), -32024),
            (Custom(subcode="x", message="m"), -32603),
        ],
    )
    def test_from_tool_error_wire_maps_each_variant(self, err, expected):
        assert from_tool_error_wire(err) == expected

    @pytest.mark.parametrize(
        "reason",
        [
            WorkspaceGoneReason.IdleTimeout,
            WorkspaceGoneReason.Disconnect,
            WorkspaceGoneReason.Shutdown,
            WorkspaceGoneReason.NotBound,
            WorkspaceGoneReason.InstanceGone,
            WorkspaceGoneReason.Unknown,
        ],
    )
    @pytest.mark.parametrize(
        "phase",
        [
            WorkspaceGonePhase.InFlightCancelled,
            WorkspaceGonePhase.RouteMissing,
            WorkspaceGonePhase.Attach,
            WorkspaceGonePhase.Unknown,
        ],
    )
    def test_builder_emits_custom_for_every_reason_and_phase(self, reason, phase):
        err = workspace_unavailable_wire(reason, phase)
        assert isinstance(err, Custom)
        wire = err.to_wire()
        assert wire["code"] == "custom"
        assert wire["subcode"] == WORKSPACE_UNAVAILABLE_SUBCODE
        # details.code mirrors the subcode (round-trip identity).
        assert wire["details"]["code"] == WORKSPACE_UNAVAILABLE_SUBCODE
        assert wire["details"]["reason"] == reason.value
        assert wire["details"]["phase"] == phase.value
        assert wire["details"]["retryable"] is True

    def test_builder_uses_the_pinned_generic_message(self):
        err = workspace_unavailable_wire(
            WorkspaceGoneReason.IdleTimeout, WorkspaceGonePhase.RouteMissing
        )
        assert err.message == WORKSPACE_UNAVAILABLE_MESSAGE

    def test_builder_result_round_trips_through_from_wire(self):
        err = workspace_unavailable_wire(
            WorkspaceGoneReason.Disconnect, WorkspaceGonePhase.Attach
        )
        rebuilt = error_from_wire(err.to_wire())
        assert isinstance(rebuilt, Custom)
        assert rebuilt.subcode == WORKSPACE_UNAVAILABLE_SUBCODE
        assert rebuilt.details["reason"] == "disconnect"


# -----------------------------------------------------------------------
# Package surface — R83 barrel additions.
# -----------------------------------------------------------------------


class TestPackageSurfaceR83:
    """The R83 barrel re-exports the wire-enum symbols."""

    def test_barrel_exposes_error_wire_variants(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "ToolErrorWire",
            "ToolNotFound",
            "SessionMismatch",
            "PermissionDenied",
            "TransportClosed",
            "Timeout",
            "Cancelled",
            "InvalidArguments",
            "Execution",
            "UnsupportedProtocolVersion",
            "PayloadTooLarge",
            "BehaviorVersionUnsupported",
            "RenderLimited",
            "TerminalError",
            "Internal",
            "Custom",
        ):
            assert hasattr(pkg, name), name

    def test_barrel_exposes_output_wire(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "ToolOutputWire",
            "Text",
            "Json",
            "Mcp",
            "McpBlock",
            "TextBlock",
            "ImageBlock",
            "ResourceBlock",
        ):
            assert hasattr(pkg, name), name

    def test_barrel_exposes_notification_wire(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "WireToolNotification",
            "WireCustomNotification",
            "KnownVariantCollision",
            "KNOWN_NOTIFICATION_KINDS",
            "known_notification_kinds",
            "check_custom_kind",
        ):
            assert hasattr(pkg, name), name

    def test_barrel_exposes_backfill_helpers(self):
        import minimax_code.tool_protocol as pkg

        assert hasattr(pkg, "from_tool_error_wire")
        assert hasattr(pkg, "workspace_unavailable_wire")

    def test_barrel_does_not_export_from_wire(self):
        # from_wire lives on each submodule, mirroring the Rust lib.rs set.
        import minimax_code.tool_protocol as pkg

        assert not hasattr(pkg, "from_wire")


# -----------------------------------------------------------------------
# R84 — JSON-RPC 2.0 envelope (crate's first untagged enum).
# -----------------------------------------------------------------------


class TestJsonRpcVersion:
    """Strict literal ``"2.0"`` protocol-version marker (custom serde)."""

    def test_to_wire_is_literal_2_0(self):
        assert JsonRpcVersion().to_wire() == "2.0"

    def test_str_is_literal(self):
        assert str(JsonRpcVersion()) == "2.0"

    def test_from_wire_accepts_literal(self):
        assert JsonRpcVersion.from_wire("2.0") == JsonRpcVersion()

    @pytest.mark.parametrize("bad", ["1.0", "2", "3.0", "", "2.0.0", " 2.0"])
    def test_from_wire_rejects_wrong_string(self, bad):
        with pytest.raises(JsonRpcVersionError) as exc:
            JsonRpcVersion.from_wire(bad)
        assert "expected jsonrpc" in str(exc.value)
        assert repr(bad) in str(exc.value)

    @pytest.mark.parametrize("bad", [2.0, 2, None, True, {"jsonrpc": "2.0"}, ["2.0"]])
    def test_from_wire_rejects_non_string(self, bad):
        with pytest.raises(JsonRpcVersionError):
            JsonRpcVersion.from_wire(bad)

    def test_singleton_equality_and_hash(self):
        assert JsonRpcVersion() == JsonRpcVersion()
        assert hash(JsonRpcVersion()) == hash(JsonRpcVersion())
        table = {JsonRpcVersion(): 1}
        assert table[JsonRpcVersion()] == 1


class TestJsonRpcId:
    """``JsonRpcId`` untagged enum (String | Number), the crate's first."""

    def test_string_round_trip(self):
        s = JsonRpcIdString(value="abc")
        assert s.to_wire() == "abc"
        assert jsonrpc_id_from_wire("abc") == s

    def test_number_round_trip(self):
        n = JsonRpcIdNumber(value=7)
        assert n.to_wire() == 7
        assert jsonrpc_id_from_wire(7) == n

    def test_number_rejects_bool_in_constructor(self):
        with pytest.raises((TypeError, ValueError)):
            JsonRpcIdNumber(value=True)

    def test_from_wire_rejects_bool(self):
        with pytest.raises(ValueError):
            jsonrpc_id_from_wire(True)

    @pytest.mark.parametrize("bad", [1.5, None, [], {}])
    def test_from_wire_rejects_other_types(self, bad):
        with pytest.raises(ValueError):
            jsonrpc_id_from_wire(bad)

    def test_number_as_request_id_stringifies(self):
        # Number(7) projects to RequestId "7".
        assert JsonRpcIdNumber(value=7).as_request_id() == RequestId("7")

    def test_string_as_request_id_round_trip(self):
        rid = RequestId("req-1")
        projected = JsonRpcIdString.from_request_id(rid)
        assert projected.as_request_id() == rid

    def test_number_i64_range_enforced(self):
        JsonRpcIdNumber(value=2**63 - 1)
        JsonRpcIdNumber(value=-(2**63))
        with pytest.raises(ValueError):
            JsonRpcIdNumber(value=2**63)


class TestJsonRpcRequest:
    """Request envelope: ``session_id`` omitted when ``None``."""

    def _make(self, session_id=None):
        return JsonRpcRequest(
            jsonrpc=JsonRpcVersion(),
            id=JsonRpcIdNumber(value=1),
            method="tool/call",
            params={"name": "ls"},
            session_id=session_id,
        )

    def test_session_id_omitted_when_none(self):
        wire = self._make().to_wire()
        assert "session_id" not in wire
        assert wire["jsonrpc"] == "2.0"
        assert wire["id"] == 1
        assert wire["method"] == "tool/call"
        assert wire["params"] == {"name": "ls"}

    def test_session_id_present_when_some(self):
        wire = self._make(session_id=SessionId("s-1")).to_wire()
        assert wire["session_id"] == "s-1"

    def test_round_trip(self):
        req = self._make(session_id=SessionId("s-1"))
        rebuilt = JsonRpcRequest.from_wire(req.to_wire())
        assert rebuilt.method == "tool/call"
        assert rebuilt.id == JsonRpcIdNumber(value=1)
        assert rebuilt.session_id == SessionId("s-1")
        assert rebuilt.params == {"name": "ls"}


class TestJsonRpcNotification:
    """Notification: no ``id`` key; ``seq`` + ``session_id`` omitted when None."""

    def test_no_id_key_and_optionals_omitted(self):
        wire = JsonRpcNotification(
            jsonrpc=JsonRpcVersion(),
            method="progress",
            params={"p": 1},
        ).to_wire()
        assert "id" not in wire
        assert "session_id" not in wire
        assert "seq" not in wire
        assert wire["method"] == "progress"

    def test_seq_and_session_present_when_some(self):
        wire = JsonRpcNotification(
            jsonrpc=JsonRpcVersion(),
            method="progress",
            params={},
            session_id=SessionId("s"),
            seq=FrameSeq.new(5),
        ).to_wire()
        assert wire["session_id"] == "s"
        assert wire["seq"] == 5

    def test_round_trip(self):
        n = JsonRpcNotification(
            jsonrpc=JsonRpcVersion(),
            method="progress",
            params={"p": 1},
            seq=FrameSeq.new(3),
        )
        rebuilt = JsonRpcNotification.from_wire(n.to_wire())
        assert rebuilt.method == "progress"
        assert rebuilt.seq.to_wire() == 3


class TestJsonRpcError:
    """Error object: ``data`` omitted when ``None``."""

    def test_data_omitted_when_none(self):
        wire = JsonRpcError(code=-32600, message="bad").to_wire()
        assert wire == {"code": -32600, "message": "bad"}
        assert "data" not in wire

    def test_data_present_when_some(self):
        wire = JsonRpcError(
            code=-32600, message="bad", data={"subcode": "x"}
        ).to_wire()
        assert wire["data"] == {"subcode": "x"}

    def test_round_trip(self):
        e = JsonRpcError(code=-32001, message="timeout", data={"t": 1})
        rebuilt = JsonRpcError.from_wire(e.to_wire())
        assert rebuilt.code == -32001
        assert rebuilt.message == "timeout"
        assert rebuilt.data == {"t": 1}

    def test_round_trip_without_data(self):
        e = JsonRpcError(code=-32600, message="bad")
        rebuilt = JsonRpcError.from_wire(e.to_wire())
        assert rebuilt.data is None


class TestJsonRpcResponseInvariant:
    """``result`` XOR ``error`` invariant (custom serde, Flat struct pattern)."""

    def test_ok_emits_only_result(self):
        wire = JsonRpcResponse.ok(
            JsonRpcIdString(value="1"), {"done": True}
        ).to_wire()
        assert "result" in wire
        assert "error" not in wire
        assert wire["result"] == {"done": True}

    def test_err_emits_only_error(self):
        wire = JsonRpcResponse.err(
            JsonRpcIdString(value="1"), JsonRpcError(code=-1, message="boom")
        ).to_wire()
        assert "error" in wire
        assert "result" not in wire

    def test_ok_round_trip(self):
        r = JsonRpcResponse.ok(JsonRpcIdNumber(value=2), {"v": 1})
        rebuilt = JsonRpcResponse.from_wire(r.to_wire())
        assert isinstance(rebuilt.outcome, ResponseResult)
        assert rebuilt.outcome.value == {"v": 1}
        assert rebuilt.id == JsonRpcIdNumber(value=2)

    def test_err_round_trip(self):
        r = JsonRpcResponse.err(
            JsonRpcIdNumber(value=2),
            JsonRpcError(code=-32600, message="bad", data={"k": 1}),
        )
        rebuilt = JsonRpcResponse.from_wire(r.to_wire())
        assert isinstance(rebuilt.outcome, ResponseError)
        assert rebuilt.outcome.error.code == -32600
        assert rebuilt.outcome.error.data == {"k": 1}

    def test_with_session_chains_and_returns_self(self):
        r = JsonRpcResponse.ok(JsonRpcIdString(value="1"), 0)
        same = r.with_session(SessionId("s"))
        assert same is r
        assert r.session_id == SessionId("s")

    def test_response_session_id_omitted_when_none(self):
        wire = JsonRpcResponse.ok(JsonRpcIdString(value="1"), 0).to_wire()
        assert "session_id" not in wire

    def test_both_arms_rejected(self):
        with pytest.raises(ValueError, match="XOR"):
            JsonRpcResponse.from_wire(
                {
                    "jsonrpc": "2.0",
                    "id": "1",
                    "result": {"a": 1},
                    "error": {"code": -1, "message": "x"},
                }
            )

    def test_neither_arm_rejected(self):
        with pytest.raises(ValueError, match="result"):
            JsonRpcResponse.from_wire({"jsonrpc": "2.0", "id": "1"})


class TestEnvelopeSessionIdIndependence:
    """Envelope ``session_id`` is a distinct layer from ``params.session_id``."""

    def test_envelope_and_params_session_ids_do_not_flatten(self):
        req = JsonRpcRequest(
            jsonrpc=JsonRpcVersion(),
            id=JsonRpcIdString(value="1"),
            method="tool/call",
            params={"session_id": "params-layer", "x": 1},
            session_id=SessionId("envelope-layer"),
        )
        wire = req.to_wire()
        assert wire["session_id"] == "envelope-layer"
        assert wire["params"]["session_id"] == "params-layer"

        rebuilt = JsonRpcRequest.from_wire(wire)
        assert rebuilt.session_id == SessionId("envelope-layer")
        assert rebuilt.params["session_id"] == "params-layer"


class TestPackageSurfaceR84:
    """The R84 barrel re-exports the envelope symbols (but not ``from_wire``)."""

    def test_barrel_exposes_envelope_symbols(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "JsonRpcVersion",
            "JsonRpcVersionError",
            "JsonRpcId",
            "JsonRpcIdString",
            "JsonRpcIdNumber",
            "JsonRpcRequest",
            "JsonRpcNotification",
            "JsonRpcError",
            "JsonRpcResponse",
            "ResponseOutcome",
            "ResponseResult",
            "ResponseError",
        ):
            assert hasattr(pkg, name), name

    def test_barrel_does_not_export_jsonrpc_id_from_wire(self):
        # jsonrpc_id_from_wire lives on the submodule, mirroring Rust lib.rs.
        import minimax_code.tool_protocol as pkg

        assert not hasattr(pkg, "jsonrpc_id_from_wire")


# -----------------------------------------------------------------------
# Method — JSON-RPC method catalog (R85).
# -----------------------------------------------------------------------


class TestMethod:
    """Method StrEnum — 35 wire methods from a single source of truth."""

    def test_all_count_is_35(self):
        assert len(Method.ALL) == 35

    def test_all_iteration_matches_declaration_order(self):
        # Source-order direction grouping is preserved; direction
        # enforcement is the hub's job, not the enum's.
        all_methods = Method.ALL
        # The first 18 are harness → service.
        assert all_methods[0] is Method.SessionOpen
        assert all_methods[17] is Method.Pong
        # tool_server → service starts at index 18.
        assert all_methods[18] is Method.ToolCallProgress

    def test_as_wire_str_and_from_wire_str_round_trip_all_variants(self):
        # Grok's round-trip test iterates every variant.
        for method in Method.ALL:
            wire = method.as_wire_str()
            assert isinstance(wire, str)
            assert Method.from_wire_str(wire) is method

    def test_value_equals_wire_str(self):
        assert Method.ToolCall.value == "tool.call"
        assert Method.Ping.value == "ping"

    def test_str_is_wire_str_display(self):
        # StrEnum __str__ returns the value, mirroring Rust's Display →
        # as_wire_str delegation.
        assert str(Method.ToolCall) == "tool.call"
        assert str(Method.Hello) == "hello"

    def test_from_wire_str_returns_none_for_unknown(self):
        # Mirrors Rust returning None from the fallible match (no panic).
        assert Method.from_wire_str("not_a_method") is None
        assert Method.from_wire_str("") is None

    def test_specific_wire_strings(self):
        assert Method.ToolsList.as_wire_str() == "tools.list"
        assert Method.ToolsSearch.as_wire_str() == "tools.search"
        assert Method.HelloAck.as_wire_str() == "hello_ack"
        assert Method.ToolServerStatus.as_wire_str() == "tool_server.status"
        assert Method.ToolServerEvict.as_wire_str() == "tool_server.evict"
        assert Method.SessionBind.as_wire_str() == "session.bind"
        assert Method.SessionUnbind.as_wire_str() == "session.unbind"
        assert Method.TracesDonate.as_wire_str() == "traces.donate"
        assert Method.LogsDonate.as_wire_str() == "logs.donate"
        assert Method.MetricsDonate.as_wire_str() == "metrics.donate"
        assert Method.ServersList.as_wire_str() == "servers.list"
        assert Method.ToolNotification.as_wire_str() == "tool.notification"
        assert Method.ToolCallProgress.as_wire_str() == "tool_call_progress"
        assert Method.ToolCallRequest.as_wire_str() == "tool_call_request"

    def test_serde_round_trip_matches_wire_str(self):
        # A StrEnum member JSON-serialises as the quoted wire string,
        # matching #[serde(rename = $wire)].
        wire = Method.ToolCall.as_wire_str()
        assert json.loads(json.dumps(Method.ToolCall)) == wire

    def test_unknown_method_msg_prefix_pinned(self):
        # Fleet-compat pin: terminal binaries built while the SDK keyed
        # old-hub detection on this exact prefix remain in the field.
        assert UNKNOWN_METHOD_MSG_PREFIX == "unknown method `"

    def test_method_doc_present_for_documented_variants(self):
        # Ten variants carry Grok #[doc] semantics worth surfacing.
        cancel_doc = method_doc(Method.ToolCancel)
        assert cancel_doc is not None
        assert "Hook" in cancel_doc
        assert method_doc(Method.SessionAttachServer) is not None
        assert method_doc(Method.HookReply) is not None
        assert method_doc(Method.TracesDonate) is not None
        assert method_doc(Method.LogsDonate) is not None
        assert method_doc(Method.MetricsDonate) is not None
        assert method_doc(Method.ServersList) is not None
        assert method_doc(Method.Serve) is not None
        assert method_doc(Method.SessionBind) is not None
        assert method_doc(Method.SessionUnbind) is not None

    def test_method_doc_none_for_undocumented_variants(self):
        assert method_doc(Method.Ping) is None
        assert method_doc(Method.ToolCall) is None
        assert method_doc(Method.Pong) is None

    def test_envelope_consumes_method_wire_str(self):
        # Cross-module: the envelope's `method` field is the bare wire
        # string produced by Method.as_wire_str(), and it round-trips back
        # to the enum via from_wire_str.
        req = JsonRpcRequest(
            jsonrpc=JsonRpcVersion(),
            id=JsonRpcIdString(value="r1"),
            method=Method.ToolCall.as_wire_str(),
            params={"tool_call_id": "tc_1"},
        )
        wire = req.to_wire()
        assert wire["method"] == "tool.call"
        assert wire["method"] == str(Method.ToolCall)
        recovered = Method.from_wire_str(wire["method"])  # type: ignore[arg-type]
        assert recovered is Method.ToolCall

    def test_method_is_strenum(self):
        from enum import StrEnum

        assert issubclass(Method, StrEnum)
        # Member values are the wire strings (the StrEnum contract that
        # makes JSON serialisation match #[serde(rename)]).

    def test_barrel_method_is_single_source(self):
        # Method imported via the barrel is the same class object as the
        # submodule Method — no accidental duplicate enum.
        from minimax_code.tool_protocol.methods import Method as SubmoduleMethod

        assert Method is SubmoduleMethod


class TestPackageSurfaceR85:
    """The R85 barrel re-exports Method + UNKNOWN_METHOD_MSG_PREFIX."""

    def test_barrel_exposes_method_symbols(self):
        import minimax_code.tool_protocol as pkg

        assert hasattr(pkg, "Method")
        assert hasattr(pkg, "UNKNOWN_METHOD_MSG_PREFIX")

    def test_barrel_method_is_strenum(self):
        from enum import StrEnum

        import minimax_code.tool_protocol as pkg

        assert issubclass(pkg.Method, StrEnum)

    def test_barrel_does_not_export_method_doc(self):
        # method_doc lives on the submodule, mirroring Rust lib.rs
        # pub use exporting Method + UNKNOWN_METHOD_MSG_PREFIX only.
        import minimax_code.tool_protocol as pkg

        assert not hasattr(pkg, "method_doc")


# -----------------------------------------------------------------------
# R86 - capabilities (per-tool capability bitset + streaming/notification
# schemas + the two snake_case enums).
# -----------------------------------------------------------------------


class TestHookKind:
    """HookKind - snake_case StrEnum, no #[serde(other)] (unknown rejects)."""

    def test_six_variants_wire_values(self):
        assert HookKind.OnSessionOpen.to_wire() == "on_session_open"
        assert HookKind.OnSessionClose.to_wire() == "on_session_close"
        assert HookKind.OnToolCallStart.to_wire() == "on_tool_call_start"
        assert HookKind.OnToolCallResult.to_wire() == "on_tool_call_result"
        assert HookKind.OnCancel.to_wire() == "on_cancel"
        assert HookKind.OnNotification.to_wire() == "on_notification"

    def test_to_wire_equals_value_and_str(self):
        # StrEnum contract: to_wire == .value == str(member) (mirrors
        # Rust Display delegating to the snake_case rename).
        for hk in HookKind:
            assert hk.to_wire() == hk.value
            assert str(hk) == hk.value

    def test_from_wire_roundtrip(self):
        for hk in HookKind:
            assert HookKind.from_wire(hk.to_wire()) is hk

    def test_from_wire_unknown_rejects(self):
        # No #[serde(other)] arm: serde rejects unknown variants rather
        # than silently swallowing them.
        with pytest.raises(ValueError):
            HookKind.from_wire("on_session_mid")
        with pytest.raises(ValueError):
            HookKind.from_wire("OnSessionOpen")  # case-sensitive

    def test_is_strenum(self):
        from enum import StrEnum

        assert issubclass(HookKind, StrEnum)


class TestToolScope:
    """ToolScope - 2-variant snake_case StrEnum (read/write)."""

    def test_two_variants_wire_values(self):
        assert ToolScope.Read.to_wire() == "read"
        assert ToolScope.Write.to_wire() == "write"

    def test_from_wire_roundtrip(self):
        assert ToolScope.from_wire("read") is ToolScope.Read
        assert ToolScope.from_wire("write") is ToolScope.Write

    def test_from_wire_unknown_rejects(self):
        with pytest.raises(ValueError):
            ToolScope.from_wire("readonly")
        with pytest.raises(ValueError):
            ToolScope.from_wire("Read")

    def test_is_strenum(self):
        from enum import StrEnum

        assert issubclass(ToolScope, StrEnum)


class TestStreamingSpec:
    """StreamingSpec - subkind required, max_delta_bytes optional."""

    def test_subkind_only_minimal(self):
        s = StreamingSpec(subkind="bash_output_chunk")
        wire = s.to_wire()
        assert wire == {"subkind": "bash_output_chunk"}
        # max_delta_bytes omitted when None (skip_serializing_if Option::is_none).
        assert "max_delta_bytes" not in wire

    def test_with_max_delta_bytes(self):
        s = StreamingSpec(subkind="delta", max_delta_bytes=4096)
        assert s.to_wire() == {"subkind": "delta", "max_delta_bytes": 4096}

    def test_from_wire_minimal(self):
        s = StreamingSpec.from_wire({"subkind": "x"})
        assert s.subkind == "x"
        assert s.max_delta_bytes is None

    def test_roundtrip(self):
        s = StreamingSpec(subkind="k", max_delta_bytes=8192)
        assert StreamingSpec.from_wire(s.to_wire()) == s


class TestToolCapabilities:
    """ToolCapabilities - 9-field conservative-default capability bitset."""

    def test_default_all_off_to_wire_only_bools(self):
        # Default instance: bool fields ALWAYS serialise (#[serde(default)]
        # with NO skip), everything else omits when None/empty.
        caps = ToolCapabilities()
        assert caps.to_wire() == {"supports_cancel": False, "is_read_only": False}

    def test_empty_wire_roundtrips_to_default(self):
        # An empty {} payload deserialises to the all-off default
        # (every field carries #[serde(default)]).
        caps = ToolCapabilities.from_wire({})
        assert caps == ToolCapabilities()

    def test_bool_false_always_present(self):
        # The two bool fields serialise even when False (no skip on them).
        wire = ToolCapabilities().to_wire()
        assert wire["supports_cancel"] is False
        assert wire["is_read_only"] is False

    def test_option_fields_omit_when_none(self):
        caps = ToolCapabilities()  # all Option fields None
        wire = caps.to_wire()
        for key in (
            "streaming",
            "max_concurrency",
            "behavior_version",
            "max_frame_bytes",
            "timeout_ms",
            "tool_scope",
        ):
            assert key not in wire

    def test_empty_hooks_omitted(self):
        caps = ToolCapabilities(hooks=[])
        assert "hooks" not in caps.to_wire()

    def test_full_payload_roundtrip(self):
        caps = ToolCapabilities(
            streaming=StreamingSpec(subkind="k", max_delta_bytes=100),
            supports_cancel=True,
            max_concurrency=4,
            is_read_only=True,
            hooks=[HookKind.OnCancel, HookKind.OnToolCallStart],
            behavior_version="2024-01-01",
            max_frame_bytes=1048576,
            timeout_ms=30_000,
            tool_scope=ToolScope.Write,
        )
        wire = caps.to_wire()
        # bool fields present (True this time).
        assert wire["supports_cancel"] is True
        assert wire["is_read_only"] is True
        # hooks serialise as the snake_case wire strings.
        assert wire["hooks"] == ["on_cancel", "on_tool_call_start"]
        # tool_scope serialises as the snake_case wire string.
        assert wire["tool_scope"] == "write"
        # streaming nests its own to_wire.
        assert wire["streaming"] == {"subkind": "k", "max_delta_bytes": 100}
        # Round-trip preserves every field.
        assert ToolCapabilities.from_wire(wire) == caps

    def test_hooks_roundtrip_preserves_enum(self):
        caps = ToolCapabilities(hooks=list(HookKind))
        recovered = ToolCapabilities.from_wire(caps.to_wire())
        assert recovered.hooks == list(HookKind)
        assert all(isinstance(h, HookKind) for h in recovered.hooks)

    def test_tool_scope_roundtrip_preserves_enum(self):
        for scope in ToolScope:
            caps = ToolCapabilities(tool_scope=scope)
            recovered = ToolCapabilities.from_wire(caps.to_wire())
            assert recovered.tool_scope is scope

    def test_partial_wire_only_sets_given_fields(self):
        # A wire payload with only supports_cancel picks up defaults
        # for everything else.
        caps = ToolCapabilities.from_wire({"supports_cancel": True})
        assert caps.supports_cancel is True
        assert caps.is_read_only is False
        assert caps.streaming is None
        assert caps.hooks == []
        assert caps.tool_scope is None


class TestNotificationSchemas:
    """NotificationSchemas - two HashMap<String, Value> fields, empty omitted."""

    def test_default_empty_omits_both(self):
        ns = NotificationSchemas()
        assert ns.to_wire() == {}

    def test_outbound_only(self):
        ns = NotificationSchemas(outbound={"file_changed": {"type": "string"}})
        wire = ns.to_wire()
        assert wire == {"outbound": {"file_changed": {"type": "string"}}}
        assert "inbound" not in wire

    def test_both_present(self):
        ns = NotificationSchemas(
            outbound={"a": 1},
            inbound={"b": [1, 2, 3]},
        )
        assert ns.to_wire() == {"outbound": {"a": 1}, "inbound": {"b": [1, 2, 3]}}

    def test_roundtrip_and_missing_keys_default_empty(self):
        ns = NotificationSchemas(outbound={"x": "y"}, inbound={"z": 0})
        recovered = NotificationSchemas.from_wire(ns.to_wire())
        assert recovered == ns
        # Missing keys -> empty dict (#[serde(default)] on HashMap).
        empty = NotificationSchemas.from_wire({})
        assert empty == NotificationSchemas()


class TestPackageSurfaceR86:
    """The R86 barrel re-exports the five capabilities symbols."""

    def test_barrel_exposes_capabilities_symbols(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "ToolCapabilities",
            "StreamingSpec",
            "HookKind",
            "ToolScope",
            "NotificationSchemas",
        ):
            assert hasattr(pkg, name), f"barrel missing {name}"

    def test_barrel_enums_are_strenum(self):
        from enum import StrEnum

        import minimax_code.tool_protocol as pkg

        assert issubclass(pkg.HookKind, StrEnum)
        assert issubclass(pkg.ToolScope, StrEnum)

    def test_barrel_does_not_export_module_from_wire(self):
        # capabilities has no module-level from_wire function (it lives as
        # a classmethod on each type), unlike error_wire/output_wire/
        # notification_wire. The barrel mirrors Rust lib.rs pub use of
        # the five type names only.
        import minimax_code.tool_protocol as pkg

        assert not hasattr(pkg, "capabilities_from_wire")


# -----------------------------------------------------------------------
# R87 — registration payloads (TransportKind + the three structs +
# RegistrationOutcome + registration_outcome_from_wire).
# -----------------------------------------------------------------------


class TestTransportKind:
    """``TransportKind`` — snake_case StrEnum, no ``#[serde(other)]``."""

    def test_member_values_are_wire_strings(self):
        assert TransportKind.Local.value == "local"
        assert TransportKind.Remote.value == "remote"

    def test_to_wire_returns_member_value(self):
        assert TransportKind.Local.to_wire() == "local"
        assert TransportKind.Remote.to_wire() == "remote"

    def test_from_wire_accepts_known(self):
        assert TransportKind.from_wire("local") is TransportKind.Local
        assert TransportKind.from_wire("remote") is TransportKind.Remote

    def test_from_wire_rejects_unknown(self):
        with pytest.raises(ValueError):
            TransportKind.from_wire("cloud")


class TestToolDescriptionWithSchema:
    """``ToolDescriptionWithSchema`` — pydantic bridge + ``derive_tool_id``."""

    def test_derive_tool_id_bare_name(self):
        w = ToolDescriptionWithSchema(
            description=ToolDescription(name="bash", description="d")
        )
        assert w.derive_tool_id() == ToolId("bash")

    def test_derive_tool_id_namespaced(self):
        w = ToolDescriptionWithSchema(
            description=ToolDescription(
                name="bash", namespace="shell", description="d"
            )
        )
        assert w.derive_tool_id() == ToolId("shell:bash")

    def test_to_wire_description_uses_model_dump_exclude_none(self):
        # Only name/description are non-optional on ToolDescription; the
        # four Option fields (namespace/title/arguments_schema/kind) omit
        # when None, matching Rust's per-Option skip rule. The three
        # optional wrappers on ToolDescriptionWithSchema omit too.
        w = ToolDescriptionWithSchema(
            description=ToolDescription(name="bash", description="Run a shell")
        )
        d = w.to_wire()
        assert d == {"description": {"name": "bash", "description": "Run a shell"}}
        assert "input_schema" not in d
        assert "capabilities" not in d
        assert "notification_schemas" not in d

    def test_to_wire_namespaced_description_survives(self):
        w = ToolDescriptionWithSchema(
            description=ToolDescription(
                name="bash", namespace="shell", description="d"
            )
        )
        assert w.to_wire()["description"] == {
            "name": "bash",
            "namespace": "shell",
            "description": "d",
        }

    def test_round_trip_with_optional_wrappers(self):
        orig = ToolDescriptionWithSchema(
            description=ToolDescription(name="bash", description="d"),
            input_schema={"type": "object"},
            capabilities=ToolCapabilities(supports_cancel=True),
        )
        back = ToolDescriptionWithSchema.from_wire(orig.to_wire())
        assert back.description.name == "bash"
        assert back.input_schema == {"type": "object"}
        assert back.capabilities is not None
        assert back.capabilities.supports_cancel is True


class TestToolRegistration:
    """``ToolRegistration`` — 11 fields, 3-state ``sessions``, manual key order."""

    def _baseline(self) -> ToolRegistration:
        return ToolRegistration(
            tool_id=ToolId("bash"),
            user_id=UserId("u1"),
            description=ToolDescription(name="bash", description="d"),
            transport_kind=TransportKind.Local,
        )

    def test_derive_tool_id_matches_payload(self):
        reg = self._baseline()
        assert reg.derive_tool_id() == reg.tool_id

    def test_sessions_none_is_omitted(self):
        # None (field omitted) = "no change" — must NOT appear on the wire.
        assert "sessions" not in self._baseline().to_wire()

    def test_sessions_empty_list_serialises(self):
        # Explicit [] = "unbind every session" — must appear as an empty
        # array (the whole point of the 3-state shape).
        from dataclasses import replace

        assert replace(self._baseline(), sessions=[]).to_wire()["sessions"] == []

    def test_sessions_populated_serialises(self):
        from dataclasses import replace

        w = replace(
            self._baseline(), sessions=[SessionId("s1"), SessionId("s2")]
        ).to_wire()
        assert w["sessions"] == ["s1", "s2"]

    def test_required_fields_present_and_ordered(self):
        w = self._baseline().to_wire()
        keys = list(w.keys())
        # tool_id leads the manual key order; transport_kind rides after
        # description (Python dataclass reorders required-first, but wire
        # order is hand-controlled to match Rust).
        assert keys[0] == "tool_id"
        assert "user_id" in w
        assert "description" in w
        assert "transport_kind" in w
        assert keys.index("description") < keys.index("transport_kind")

    def test_optional_fields_omit_when_none(self):
        w = self._baseline().to_wire()
        for opt in (
            "server_id",
            "input_schema",
            "capabilities",
            "notification_schemas",
            "if_match_generation",
            "metadata",
        ):
            assert opt not in w

    def test_round_trip_full(self):
        from dataclasses import replace

        reg = replace(
            self._baseline(),
            sessions=[SessionId("s1")],
            server_id=ServerId("srv1"),
            if_match_generation=3,
            transport_kind=TransportKind.Remote,
        )
        back = ToolRegistration.from_wire(reg.to_wire())
        assert back.tool_id == ToolId("bash")
        assert back.user_id == UserId("u1")
        assert back.sessions == [SessionId("s1")]
        assert back.server_id == ServerId("srv1")
        assert back.if_match_generation == 3
        assert back.transport_kind is TransportKind.Remote


class TestToolServerRegistration:
    """``ToolServerRegistration`` — ``String::is_empty`` skip, ``Vec`` always rides."""

    def _baseline(self) -> ToolServerRegistration:
        return ToolServerRegistration(
            server_id=ServerId("srv1"),
            user_id=UserId("u1"),
            tools=[
                ToolDescriptionWithSchema(
                    description=ToolDescription(name="bash", description="d")
                )
            ],
        )

    def test_empty_description_is_omitted(self):
        # skip_serializing_if = "String::is_empty" — the sixth serde
        # sub-shape (bare String, not Option<String>, that still skips).
        assert "description" not in self._baseline().to_wire()

    def test_nonempty_description_rides(self):
        from dataclasses import replace

        assert (
            replace(self._baseline(), description="My server").to_wire()["description"]
            == "My server"
        )

    def test_empty_tools_still_serialise(self):
        # Vec carries no skip — an empty batch still emits "tools": [].
        from dataclasses import replace

        assert replace(self._baseline(), tools=[]).to_wire()["tools"] == []

    def test_empty_hooks_omitted(self):
        assert "hooks" not in self._baseline().to_wire()

    def test_round_trip(self):
        from dataclasses import replace

        srv = replace(
            self._baseline(),
            description="My server",
            hooks=[HookKind.OnSessionOpen],
            sessions=[SessionId("s1")],
        )
        back = ToolServerRegistration.from_wire(srv.to_wire())
        assert back.server_id == ServerId("srv1")
        assert back.description == "My server"
        assert back.hooks == [HookKind.OnSessionOpen]
        assert back.sessions == [SessionId("s1")]
        assert len(back.tools) == 1
        assert back.tools[0].description.name == "bash"


class TestRegistrationOutcome:
    """``RegistrationOutcome`` — internally-tagged enum (4 struct variants)."""

    def test_registered_to_wire_stamps_tag(self):
        r = Registered(tool_id=ToolId("ns:bash"), generation=7)
        assert r.to_wire() == {
            "outcome": "registered",
            "tool_id": "ns:bash",
            "generation": 7,
        }

    def test_updated_to_wire_stamps_tag(self):
        u = Updated(tool_id=ToolId("ns:bash"), generation=9)
        assert u.to_wire() == {
            "outcome": "updated",
            "tool_id": "ns:bash",
            "generation": 9,
        }

    def test_shadowed_to_wire_stamps_tag(self):
        s = Shadowed(tool_id=ToolId("ns:bash"), reason="leader elected")
        assert s.to_wire() == {
            "outcome": "shadowed",
            "tool_id": "ns:bash",
            "reason": "leader elected",
        }

    def test_rejected_to_wire_stamps_tag(self):
        r = Rejected(
            tool_id=ToolId("ns:bash"),
            code="namespace_taken",
            message="another server owns it",
        )
        assert r.to_wire() == {
            "outcome": "rejected",
            "tool_id": "ns:bash",
            "code": "namespace_taken",
            "message": "another server owns it",
        }

    def test_from_wire_dispatches_registered(self):
        back = registration_outcome_from_wire(
            {"outcome": "registered", "tool_id": "ns:bash", "generation": 7}
        )
        assert isinstance(back, Registered)
        assert back.tool_id == ToolId("ns:bash")
        assert back.generation == 7

    def test_from_wire_dispatches_updated(self):
        back = registration_outcome_from_wire(
            {"outcome": "updated", "tool_id": "x", "generation": 2}
        )
        assert isinstance(back, Updated)
        assert back.generation == 2

    def test_from_wire_dispatches_shadowed(self):
        back = registration_outcome_from_wire(
            {"outcome": "shadowed", "tool_id": "x", "reason": "r"}
        )
        assert isinstance(back, Shadowed)
        assert back.reason == "r"

    def test_from_wire_dispatches_rejected(self):
        back = registration_outcome_from_wire(
            {"outcome": "rejected", "tool_id": "x", "code": "c", "message": "m"}
        )
        assert isinstance(back, Rejected)
        assert back.code == "c"
        assert back.message == "m"

    def test_from_wire_rejects_unknown_tag(self):
        with pytest.raises(ValueError):
            registration_outcome_from_wire({"outcome": "nope", "tool_id": "x"})


class TestPackageSurfaceR87:
    """The R87 barrel re-exports the nine registration symbols."""

    def test_barrel_exposes_registration_symbols(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "TransportKind",
            "ToolDescriptionWithSchema",
            "ToolRegistration",
            "ToolServerRegistration",
            "RegistrationOutcome",
            "Registered",
            "Updated",
            "Shadowed",
            "Rejected",
        ):
            assert hasattr(pkg, name), f"barrel missing {name}"

    def test_transportkind_is_strenum(self):
        from enum import StrEnum

        import minimax_code.tool_protocol as pkg

        assert issubclass(pkg.TransportKind, StrEnum)

    def test_registration_outcome_union_has_four_variants(self):
        from typing import get_args

        import minimax_code.tool_protocol as pkg

        args = get_args(pkg.RegistrationOutcome)
        assert len(args) == 4
        assert set(args) == {
            pkg.Registered,
            pkg.Updated,
            pkg.Shadowed,
            pkg.Rejected,
        }

    def test_barrel_does_not_export_from_wire(self):
        # registration_outcome_from_wire lives at module scope only — the
        # barrel mirrors Rust lib.rs pub use of the type names, not the
        # union dispatch helper (same discipline as jsonrpc_id_from_wire).
        import minimax_code.tool_protocol as pkg

        assert not hasattr(pkg, "registration_outcome_from_wire")


# ------------------------------------------------------------------ R88
# Registry-error variants are imported at the top of this module (see the
# ``registry_error`` import block above) — the barrel only re-exports the
# ``RegistryError`` union, both to mirror Rust lib.rs and to avoid clobbering
# ``error_wire.SessionMismatch`` (R83), which shares the name ``SessionMismatch``
# with a registry_error variant.


class TestRegistryErrorWireTags:
    """Each variant stamps its ``code`` tag; ``AlreadyRegistered`` carries the
    per-variant rename override (``tool_already_registered``, NOT the
    ``rename_all = "snake_case"`` default ``already_registered``)."""

    def test_already_registered_uses_override_tag(self):
        err = AlreadyRegistered(tool_id=ToolId("ns:bash"))
        wire = err.to_wire()
        assert wire["code"] == "tool_already_registered"
        # The rename_all default would have produced "already_registered";
        # pin the override explicitly.
        assert wire["code"] != "already_registered"

    def test_session_mismatch_tag(self):
        wire = RegistrySessionMismatch(
            token_session=SessionId("tok"), reg_session=SessionId("reg")
        ).to_wire()
        assert wire["code"] == "session_mismatch"

    def test_server_id_collision_tag(self):
        assert ServerIdCollision(server_id=ServerId("srv")).to_wire()["code"] == (
            "server_id_collision"
        )

    def test_server_id_in_use_tag(self):
        assert ServerIdInUse(server_id=ServerId("srv")).to_wire()["code"] == (
            "server_id_in_use"
        )

    def test_invalid_description_tag(self):
        assert InvalidDescription(message="bad").to_wire()["code"] == (
            "invalid_description"
        )

    def test_stale_generation_tag(self):
        assert StaleGeneration(expected=3, actual=7).to_wire()["code"] == (
            "stale_generation"
        )


class TestRegistryErrorToWire:
    """``to_wire`` carries the named fields alongside the ``code`` tag."""

    def test_already_registered_serialises_tool_id(self):
        assert AlreadyRegistered(tool_id=ToolId("ns:bash")).to_wire() == {
            "code": "tool_already_registered",
            "tool_id": "ns:bash",
        }

    def test_session_mismatch_serialises_both_sessions(self):
        wire = RegistrySessionMismatch(
            token_session=SessionId("tok"), reg_session=SessionId("reg")
        ).to_wire()
        assert wire == {
            "code": "session_mismatch",
            "token_session": "tok",
            "reg_session": "reg",
        }

    def test_server_id_collision_serialises_server_id(self):
        assert ServerIdCollision(server_id=ServerId("srv-1")).to_wire() == {
            "code": "server_id_collision",
            "server_id": "srv-1",
        }

    def test_server_id_in_use_serialises_server_id(self):
        assert ServerIdInUse(server_id=ServerId("srv-2")).to_wire() == {
            "code": "server_id_in_use",
            "server_id": "srv-2",
        }

    def test_invalid_description_serialises_message(self):
        assert InvalidDescription(message="reserved prefix").to_wire() == {
            "code": "invalid_description",
            "message": "reserved prefix",
        }

    def test_stale_generation_serialises_expected_actual(self):
        assert StaleGeneration(expected=3, actual=7).to_wire() == {
            "code": "stale_generation",
            "expected": 3,
            "actual": 7,
        }


class TestRegistryErrorFromWire:
    """``registry_error_from_wire`` round-trips each variant and rejects
    unknown / missing ``code`` tags."""

    def test_round_trip_already_registered(self):
        back = registry_error_from_wire(
            {"code": "tool_already_registered", "tool_id": "ns:bash"}
        )
        assert isinstance(back, AlreadyRegistered)
        assert back.tool_id == ToolId("ns:bash")

    def test_round_trip_session_mismatch(self):
        back = registry_error_from_wire(
            {
                "code": "session_mismatch",
                "token_session": "tok",
                "reg_session": "reg",
            }
        )
        assert isinstance(back, RegistrySessionMismatch)
        assert back.token_session == SessionId("tok")
        assert back.reg_session == SessionId("reg")

    def test_round_trip_server_id_collision(self):
        back = registry_error_from_wire(
            {"code": "server_id_collision", "server_id": "srv-1"}
        )
        assert isinstance(back, ServerIdCollision)
        assert back.server_id == ServerId("srv-1")

    def test_round_trip_server_id_in_use(self):
        back = registry_error_from_wire(
            {"code": "server_id_in_use", "server_id": "srv-2"}
        )
        assert isinstance(back, ServerIdInUse)
        assert back.server_id == ServerId("srv-2")

    def test_round_trip_invalid_description(self):
        back = registry_error_from_wire(
            {"code": "invalid_description", "message": "reserved prefix"}
        )
        assert isinstance(back, InvalidDescription)
        assert back.message == "reserved prefix"

    def test_round_trip_stale_generation(self):
        back = registry_error_from_wire(
            {"code": "stale_generation", "expected": 3, "actual": 7}
        )
        assert isinstance(back, StaleGeneration)
        assert (back.expected, back.actual) == (3, 7)

    def test_from_wire_rejects_unknown_tag(self):
        with pytest.raises(ValueError):
            registry_error_from_wire({"code": "nope", "tool_id": "x"})

    def test_from_wire_rejects_missing_code(self):
        with pytest.raises(KeyError):
            registry_error_from_wire({"tool_id": "x"})

    def test_from_wire_rejects_default_already_registered_tag(self):
        # The rename override means "already_registered" (the rename_all
        # default) is NOT a valid tag — only "tool_already_registered" is.
        with pytest.raises(ValueError):
            registry_error_from_wire({"code": "already_registered", "tool_id": "x"})


class TestRegistryErrorUnion:
    """The union alias covers all six variants."""

    def test_union_has_six_variants(self):
        from typing import get_args

        args = get_args(RegistryError)
        assert len(args) == 6
        assert set(args) == {
            AlreadyRegistered,
            RegistrySessionMismatch,
            ServerIdCollision,
            ServerIdInUse,
            InvalidDescription,
            StaleGeneration,
        }

    def test_dispatch_map_covers_six_tags(self):
        from minimax_code.tool_protocol.registry_error import _WIRE_TAG_TO_VARIANT

        assert set(_WIRE_TAG_TO_VARIANT) == {
            "tool_already_registered",
            "session_mismatch",
            "server_id_collision",
            "server_id_in_use",
            "invalid_description",
            "stale_generation",
        }


class TestPackageSurfaceR88:
    """The barrel re-exports only the ``RegistryError`` union (mirroring Rust
    lib.rs); variants stay in-submodule so they do not clobber
    ``error_wire.SessionMismatch`` (R83), which shares the name."""

    def test_barrel_exposes_registry_error_union(self):
        import minimax_code.tool_protocol as pkg

        assert hasattr(pkg, "RegistryError")

    def test_barrel_does_not_export_variants(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "AlreadyRegistered",
            "ServerIdCollision",
            "ServerIdInUse",
            "InvalidDescription",
            "StaleGeneration",
        ):
            assert not hasattr(pkg, name), (
                f"barrel should not re-export registry_error variant {name} "
                "(mirrors Rust lib.rs; avoids error_wire.SessionMismatch clash)"
            )

    def test_barrel_session_mismatch_is_error_wire_not_registry(self):
        # The two modules both define a ``SessionMismatch`` variant. The
        # barrel must keep R83 error_wire's, not R88 registry_error's.
        import minimax_code.tool_protocol as pkg
        from minimax_code.tool_protocol.error_wire import SessionMismatch as WireSm

        assert pkg.SessionMismatch is WireSm

    def test_barrel_does_not_export_from_wire(self):
        import minimax_code.tool_protocol as pkg

        assert not hasattr(pkg, "registry_error_from_wire")

    def test_variants_accessible_via_submodule(self):
        import minimax_code.tool_protocol.registry_error as re_mod

        for name in (
            "AlreadyRegistered",
            "SessionMismatch",
            "ServerIdCollision",
            "ServerIdInUse",
            "InvalidDescription",
            "StaleGeneration",
            "RegistryError",
            "registry_error_from_wire",
        ):
            assert hasattr(re_mod, name), f"submodule missing {name}"


# ------------------------------------------------------------------ R89
# Hook variants are imported at the top of this module (see the ``hook``
# import block above) — the barrel only re-exports the ``HookEvent`` union,
# both to mirror Rust lib.rs and to avoid clobbering ``error_wire.Custom``
# (R83), which shares the name ``Custom`` with a hook variant.


class TestHookEventWireTags:
    """Each variant stamps its ``type`` tag; tags are PascalCase (no
    ``rename_all`` on the Rust enum — the crate's first PascalCase-tagged
    internally-tagged enum)."""

    def test_cancel_tag_is_pascalcase(self):
        wire = Cancel().to_wire()
        assert wire["type"] == "Cancel"
        # Pin: NOT snake_case (no rename_all on the enum).
        assert wire["type"] != "cancel"

    def test_pause_tag(self):
        assert Pause().to_wire()["type"] == "Pause"

    def test_resume_tag(self):
        assert Resume().to_wire()["type"] == "Resume"

    def test_session_ended_tag_is_pascalcase(self):
        wire = SessionEnded().to_wire()
        assert wire["type"] == "SessionEnded"
        assert wire["type"] != "session_ended"

    def test_custom_tag(self):
        assert HookCustom(kind="x", payload={}).to_wire()["type"] == "Custom"


class TestHookEventToWire:
    """Unit variants serialise as just the ``type`` tag; ``Custom`` carries
    ``kind`` + ``payload`` (arbitrary JSON)."""

    def test_unit_variants_have_no_extra_fields(self):
        for variant_cls in (Cancel, Pause, Resume, SessionEnded):
            assert variant_cls().to_wire() == {"type": variant_cls.__name__}

    def test_custom_serialises_kind_and_payload(self):
        assert HookCustom(kind="heartbeat", payload={"beat": 1}).to_wire() == {
            "type": "Custom",
            "kind": "heartbeat",
            "payload": {"beat": 1},
        }

    def test_custom_payload_preserves_arbitrary_json(self):
        # serde_json::Value → any JSON: dict / list / str / num / bool / null.
        for payload in (
            {"nested": [1, 2, {"x": None}]},
            [1, "two", True, None],
            "bare-string",
            42,
            True,
            None,
        ):
            assert HookCustom(kind="k", payload=payload).to_wire()["payload"] == (
                payload
            )


class TestHookEventFromWire:
    """``hook_event_from_wire`` round-trips each variant and rejects unknown /
    missing ``type`` tags."""

    def test_round_trip_cancel(self):
        assert isinstance(hook_event_from_wire({"type": "Cancel"}), Cancel)

    def test_round_trip_pause(self):
        assert isinstance(hook_event_from_wire({"type": "Pause"}), Pause)

    def test_round_trip_resume(self):
        assert isinstance(hook_event_from_wire({"type": "Resume"}), Resume)

    def test_round_trip_session_ended(self):
        back = hook_event_from_wire({"type": "SessionEnded"})
        assert isinstance(back, SessionEnded)

    def test_round_trip_custom(self):
        back = hook_event_from_wire(
            {"type": "Custom", "kind": "heartbeat", "payload": {"beat": 1}}
        )
        assert isinstance(back, HookCustom)
        assert back.kind == "heartbeat"
        assert back.payload == {"beat": 1}

    def test_from_wire_rejects_unknown_tag(self):
        with pytest.raises(ValueError):
            hook_event_from_wire({"type": "Nope"})

    def test_from_wire_rejects_snake_case_tags(self):
        # No rename_all → snake_case tags are NOT valid (PascalCase only).
        for bad in ("cancel", "session_ended", "pause", "resume", "custom"):
            with pytest.raises(ValueError):
                hook_event_from_wire({"type": bad})

    def test_from_wire_rejects_missing_type(self):
        with pytest.raises(KeyError):
            hook_event_from_wire({"kind": "x"})

    def test_custom_round_trip_preserves_arbitrary_payload(self):
        for payload in ({"a": [1, None]}, [1, True, "x"], "s", 7, False, None):
            back = hook_event_from_wire(
                {"type": "Custom", "kind": "k", "payload": payload}
            )
            assert isinstance(back, HookCustom)
            assert back.payload == payload


class TestHookEventUnitVariants:
    """Unit variants carry no fields; instances of the same variant are
    value-equal (mirrors ``#[derive(PartialEq)]``)."""

    def test_unit_variants_have_no_fields(self):
        for variant_cls in (Cancel, Pause, Resume, SessionEnded):
            assert vars(variant_cls()) == {}

    def test_unit_variants_value_equality(self):
        assert Cancel() == Cancel()
        assert SessionEnded() == SessionEnded()
        assert Cancel() != Pause()  # distinct classes

    def test_unit_variant_frozenset(self):
        from minimax_code.tool_protocol.hook import _UNIT_VARIANTS

        assert _UNIT_VARIANTS == frozenset({Cancel, Pause, Resume, SessionEnded})


class TestHookEventUnion:
    """The union alias covers all five variants."""

    def test_union_has_five_variants(self):
        from typing import get_args

        args = get_args(HookEvent)
        assert len(args) == 5
        assert set(args) == {Cancel, Pause, Resume, SessionEnded, HookCustom}

    def test_dispatch_map_covers_five_tags(self):
        from minimax_code.tool_protocol.hook import _WIRE_TAG_TO_VARIANT

        assert set(_WIRE_TAG_TO_VARIANT) == {
            "Cancel",
            "Pause",
            "Resume",
            "SessionEnded",
            "Custom",
        }


class TestPackageSurfaceR89:
    """The barrel re-exports only the ``HookEvent`` union (mirroring Rust
    lib.rs); variants stay in-submodule so they do not clobber
    ``error_wire.Custom`` (R83), which shares the name."""

    def test_barrel_exposes_hook_event_union(self):
        import minimax_code.tool_protocol as pkg

        assert hasattr(pkg, "HookEvent")

    def test_barrel_does_not_export_unit_variants(self):
        import minimax_code.tool_protocol as pkg

        for name in ("Cancel", "Pause", "Resume", "SessionEnded"):
            assert not hasattr(pkg, name), (
                f"barrel should not re-export hook variant {name} "
                "(mirrors Rust lib.rs; avoids error_wire.Custom clash)"
            )

    def test_barrel_custom_is_error_wire_not_hook(self):
        # Both error_wire (R83) and hook (R89) define a ``Custom`` variant.
        # The barrel must keep R83 error_wire's, not R89 hook's.
        import minimax_code.tool_protocol as pkg
        from minimax_code.tool_protocol.error_wire import Custom as WireCustom

        assert pkg.Custom is WireCustom

    def test_barrel_does_not_export_from_wire(self):
        import minimax_code.tool_protocol as pkg

        assert not hasattr(pkg, "hook_event_from_wire")

    def test_variants_accessible_via_submodule(self):
        import minimax_code.tool_protocol.hook as hook_mod

        for name in (
            "Cancel",
            "Pause",
            "Resume",
            "SessionEnded",
            "Custom",
            "HookEvent",
            "hook_event_from_wire",
        ):
            assert hasattr(hook_mod, name), f"submodule missing {name}"


# ===========================================================================
# R90 — session_event (SessionEvent / SessionPhase / ToolCallOutcome) +
# turn_hook leaf (TurnHookOutcome + TURN_HOOK_KIND).
# ===========================================================================


class TestTurnHookOutcomeR90:
    """``TurnHookOutcome`` (turn_hook leaf) — strict, no ``#[serde(other)]``.

    The strict counterpart to the tolerant ``ToolCallOutcome`` / ``SessionPhase``
    (both R90, both with an ``Unknown`` catch-all). Unknown wire values raise
    (mirrors ``from_value::<TurnHookOutcome>("timeout").is_err()``).
    """

    def test_member_values_are_snake_case(self):
        assert TurnHookOutcome.COMPLETED == "completed"
        assert TurnHookOutcome.CANCELLED == "cancelled"
        assert TurnHookOutcome.ERROR == "error"

    def test_str_is_bare_value(self):
        # #[serde(rename_all = "snake_case")] with no tag → bare strings.
        assert str(TurnHookOutcome.COMPLETED) == "completed"
        assert str(TurnHookOutcome.CANCELLED) == "cancelled"
        assert str(TurnHookOutcome.ERROR) == "error"

    def test_members_are_str_instances(self):
        for member in TurnHookOutcome:
            assert isinstance(member, str)

    def test_from_wire_round_trip(self):
        for member in TurnHookOutcome:
            assert TurnHookOutcome.from_wire(str(member)) is member

    def test_from_wire_rejects_unknown_strictly(self):
        # No #[serde(other)] arm — strict rejection (the crate's own test).
        with pytest.raises(ValueError):
            TurnHookOutcome.from_wire("timeout")
        with pytest.raises(ValueError):
            TurnHookOutcome.from_wire("completed ")  # trailing space
        with pytest.raises(ValueError):
            TurnHookOutcome.from_wire("COMPLETED")  # case-sensitive

    def test_turn_hook_kind_constant(self):
        assert TURN_HOOK_KIND == "turn_hook"


class TestSessionEventWireTags:
    """``SessionEvent`` internal tag is ``event_type`` (the 5th tag key) with
    ``rename_all = "snake_case"``."""

    def test_turn_started_tag(self):
        assert TurnStarted(1, "m", True).to_wire()["event_type"] == "turn_started"

    def test_turn_ended_tag(self):
        ev = TurnEnded(1, TurnHookOutcome.COMPLETED, 100, 0, "m")
        assert ev.to_wire()["event_type"] == "turn_ended"

    def test_tool_call_started_tag(self):
        wire = ToolCallStarted("call-1", "shell", 1).to_wire()
        assert wire["event_type"] == "tool_call_started"

    def test_tool_call_completed_tag(self):
        ev = ToolCallCompleted("call-1", "shell", 50, ToolCallOutcome.SUCCESS)
        assert ev.to_wire()["event_type"] == "tool_call_completed"

    def test_phase_changed_tag(self):
        wire = PhaseChanged(SessionPhase.IDLE).to_wire()
        assert wire["event_type"] == "phase_changed"

    def test_unknown_tag(self):
        assert Unknown().to_wire() == {"event_type": "unknown"}

    def test_tags_are_snake_case_not_pascal(self):
        # Contrast with R89 hook (PascalCase tags, no rename_all).
        assert TurnStarted(1, "m").to_wire()["event_type"] != "TurnStarted"


class TestSessionEventToWire:
    def test_turn_started_full_payload_with_yolo(self):
        wire = TurnStarted(3, "grok-4", True).to_wire()
        assert wire == {
            "event_type": "turn_started",
            "turn_number": 3,
            "model_id": "grok-4",
            "yolo_mode": True,
        }

    def test_turn_started_yolo_false_still_emitted(self):
        # #[serde(default)] is deserialise-only — serialise always emits the field.
        wire = TurnStarted(3, "grok-4", False).to_wire()
        assert wire["yolo_mode"] is False

    def test_turn_ended_embeds_outcome_as_string(self):
        # Nested enum field serialises as its snake_case value.
        wire = TurnEnded(2, TurnHookOutcome.ERROR, 250, 1, "m").to_wire()
        assert wire["outcome"] == "error"
        assert wire["duration_ms"] == 250
        assert wire["tool_call_count"] == 1

    def test_tool_call_completed_embeds_outcome_as_string(self):
        wire = ToolCallCompleted("c1", "shell", 50, ToolCallOutcome.SUCCESS).to_wire()
        assert wire["outcome"] == "success"

    def test_phase_changed_embeds_phase_as_string(self):
        wire = PhaseChanged(SessionPhase.TOOL_EXECUTION).to_wire()
        assert wire["phase"] == "tool_execution"

    def test_tool_call_started_keys_are_exhaustive(self):
        # No skip_serializing_if in this round — every field emits.
        wire = ToolCallStarted("c1", "shell", 1).to_wire()
        assert set(wire.keys()) == {"event_type", "tool_call_id", "tool_name", "turn_number"}


class TestSessionEventFromWire:
    def test_turn_started_round_trip(self):
        ev = TurnStarted(5, "grok-4", True)
        assert session_event_from_wire(ev.to_wire()) == ev

    def test_turn_started_yolo_mode_defaults_false(self):
        # #[serde(default)] — wire omits yolo_mode → False.
        data = {"event_type": "turn_started", "turn_number": 1, "model_id": "m"}
        ev = session_event_from_wire(data)
        assert isinstance(ev, TurnStarted)
        assert ev.yolo_mode is False

    def test_turn_started_yolo_mode_explicit_false(self):
        data = {
            "event_type": "turn_started",
            "turn_number": 1,
            "model_id": "m",
            "yolo_mode": False,
        }
        assert session_event_from_wire(data).yolo_mode is False

    def test_turn_ended_round_trip_all_outcomes(self):
        for outcome in TurnHookOutcome:
            ev = TurnEnded(1, outcome, 100, 2, "m")
            assert session_event_from_wire(ev.to_wire()) == ev

    def test_turn_ended_unknown_outcome_raises(self):
        # TurnHookOutcome is strict (no other arm) — nested strict reject.
        data = {
            "event_type": "turn_ended",
            "turn_number": 1,
            "outcome": "timeout",
            "duration_ms": 100,
            "tool_call_count": 0,
            "model_id": "m",
        }
        with pytest.raises(ValueError):
            session_event_from_wire(data)

    def test_tool_call_started_round_trip(self):
        ev = ToolCallStarted("c1", "shell", 1)
        assert session_event_from_wire(ev.to_wire()) == ev

    def test_tool_call_completed_round_trip_all_outcomes(self):
        for outcome in ToolCallOutcome:
            ev = ToolCallCompleted("c1", "shell", 50, outcome)
            assert session_event_from_wire(ev.to_wire()) == ev

    def test_tool_call_completed_unknown_outcome_tolerant(self):
        # ToolCallOutcome has #[serde(other)] → unknown → UNKNOWN.
        data = {
            "event_type": "tool_call_completed",
            "tool_call_id": "c1",
            "tool_name": "shell",
            "duration_ms": 50,
            "outcome": "rate_limited",
        }
        ev = session_event_from_wire(data)
        assert isinstance(ev, ToolCallCompleted)
        assert ev.outcome is ToolCallOutcome.UNKNOWN

    def test_phase_changed_round_trip_all_phases(self):
        for phase in SessionPhase:
            ev = PhaseChanged(phase)
            assert session_event_from_wire(ev.to_wire()) == ev

    def test_phase_changed_unknown_phase_tolerant(self):
        data = {"event_type": "phase_changed", "phase": "compacting"}
        ev = session_event_from_wire(data)
        assert isinstance(ev, PhaseChanged)
        assert ev.phase is SessionPhase.UNKNOWN

    def test_turn_number_zero_boundary(self):
        data = {"event_type": "turn_started", "turn_number": 0, "model_id": "m"}
        assert session_event_from_wire(data).turn_number == 0

    def test_duration_ms_zero_boundary(self):
        ev = session_event_from_wire(
            {
                "event_type": "turn_ended",
                "turn_number": 0,
                "outcome": "completed",
                "duration_ms": 0,
                "tool_call_count": 0,
                "model_id": "m",
            }
        )
        assert ev.duration_ms == 0
        assert ev.tool_call_count == 0

    def test_extra_fields_ignored_on_known_variant(self):
        # serde ignores unknown fields on a struct variant by default.
        data = {
            "event_type": "tool_call_started",
            "tool_call_id": "c1",
            "tool_name": "shell",
            "turn_number": 1,
            "extra": "ignored",
        }
        ev = session_event_from_wire(data)
        assert isinstance(ev, ToolCallStarted)
        assert ev.tool_call_id == "c1"


class TestSessionEventOtherArm:
    """``#[serde(other)]`` forward-compat catch-all — the crate's first."""

    def test_unknown_event_type_becomes_unknown(self):
        for unknown_tag in ("compaction_started", "context_truncated", "session_resumed"):
            ev = session_event_from_wire({"event_type": unknown_tag})
            assert isinstance(ev, Unknown), f"{unknown_tag} should fall through to Unknown"

    def test_literal_unknown_tag_round_trips(self):
        ev = session_event_from_wire({"event_type": "unknown"})
        assert isinstance(ev, Unknown)

    def test_unknown_to_wire_is_minimal(self):
        assert Unknown().to_wire() == {"event_type": "unknown"}

    def test_unknown_round_trip(self):
        rebuilt = session_event_from_wire(Unknown().to_wire())
        assert isinstance(rebuilt, Unknown)


class TestToolCallOutcomeEnum:
    def test_member_values_are_snake_case(self):
        assert ToolCallOutcome.SUCCESS == "success"
        assert ToolCallOutcome.ERROR == "error"
        assert ToolCallOutcome.CANCELLED == "cancelled"
        assert ToolCallOutcome.UNKNOWN == "unknown"

    def test_from_wire_known_values(self):
        assert ToolCallOutcome.from_wire("success") is ToolCallOutcome.SUCCESS
        assert ToolCallOutcome.from_wire("error") is ToolCallOutcome.ERROR
        assert ToolCallOutcome.from_wire("cancelled") is ToolCallOutcome.CANCELLED

    def test_from_wire_unknown_tolerant(self):
        assert ToolCallOutcome.from_wire("rate_limited") is ToolCallOutcome.UNKNOWN
        assert ToolCallOutcome.from_wire("") is ToolCallOutcome.UNKNOWN

    def test_from_wire_never_raises(self):
        ToolCallOutcome.from_wire("literally-anything")


class TestSessionPhaseEnum:
    def test_member_values_are_snake_case(self):
        assert SessionPhase.IDLE == "idle"
        assert SessionPhase.SAMPLING == "sampling"
        assert SessionPhase.TOOL_EXECUTION == "tool_execution"
        assert SessionPhase.PERMISSION_PROMPT == "permission_prompt"
        assert SessionPhase.UNKNOWN == "unknown"

    def test_from_wire_known_values(self):
        assert SessionPhase.from_wire("idle") is SessionPhase.IDLE
        assert SessionPhase.from_wire("sampling") is SessionPhase.SAMPLING
        assert SessionPhase.from_wire("tool_execution") is SessionPhase.TOOL_EXECUTION
        assert SessionPhase.from_wire("permission_prompt") is SessionPhase.PERMISSION_PROMPT

    def test_from_wire_unknown_tolerant(self):
        assert SessionPhase.from_wire("compacting") is SessionPhase.UNKNOWN


class TestStrictVsTolerantContrast:
    """R90 is the crate's strict↔tolerant turning point: two near-identical
    outcome enums with opposite unknown-value behaviour, landed the same round."""

    def test_turn_hook_outcome_strict(self):
        with pytest.raises(ValueError):
            TurnHookOutcome.from_wire("timeout")

    def test_tool_call_outcome_tolerant(self):
        assert ToolCallOutcome.from_wire("timeout") is ToolCallOutcome.UNKNOWN

    def test_session_phase_tolerant(self):
        assert SessionPhase.from_wire("timeout") is SessionPhase.UNKNOWN

    def test_same_input_opposite_behaviour(self):
        with pytest.raises(ValueError):
            TurnHookOutcome.from_wire("future_value")
        assert ToolCallOutcome.from_wire("future_value") is ToolCallOutcome.UNKNOWN
        assert SessionPhase.from_wire("future_value") is SessionPhase.UNKNOWN


class TestPackageSurfaceR90:
    """The barrel re-exports the ``SessionEvent`` union + the two nested enums
    (mirroring Rust lib.rs ``pub use session_event::{...}``); variant dataclasses
    stay in-submodule. ``turn_hook`` is ``pub mod`` with no ``pub use``, so its
    symbols (``TurnHookOutcome`` / ``TURN_HOOK_KIND``) stay out of the barrel."""

    def test_barrel_exposes_session_event_union(self):
        import minimax_code.tool_protocol as pkg

        assert hasattr(pkg, "SessionEvent")

    def test_barrel_exposes_nested_enums(self):
        import minimax_code.tool_protocol as pkg

        assert hasattr(pkg, "ToolCallOutcome")
        assert hasattr(pkg, "SessionPhase")

    def test_barrel_does_not_export_variant_dataclasses(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "TurnStarted",
            "TurnEnded",
            "ToolCallStarted",
            "ToolCallCompleted",
            "PhaseChanged",
            "Unknown",
        ):
            assert not hasattr(pkg, name), f"barrel should not re-export {name}"

    def test_barrel_does_not_export_turn_hook_symbols(self):
        import minimax_code.tool_protocol as pkg

        assert not hasattr(pkg, "TurnHookOutcome")
        assert not hasattr(pkg, "TURN_HOOK_KIND")

    def test_barrel_does_not_export_session_event_from_wire(self):
        import minimax_code.tool_protocol as pkg

        assert not hasattr(pkg, "session_event_from_wire")

    def test_session_event_variants_accessible_via_submodule(self):
        import minimax_code.tool_protocol.session_event as mod

        for name in (
            "TurnStarted",
            "TurnEnded",
            "ToolCallStarted",
            "ToolCallCompleted",
            "PhaseChanged",
            "Unknown",
            "SessionEvent",
            "ToolCallOutcome",
            "SessionPhase",
            "session_event_from_wire",
        ):
            assert hasattr(mod, name), f"submodule missing {name}"

    def test_turn_hook_leaf_accessible_via_submodule(self):
        import minimax_code.tool_protocol.turn_hook as mod

        assert hasattr(mod, "TurnHookOutcome")
        assert hasattr(mod, "TURN_HOOK_KIND")


# ---------------------------------------------------------------------------
# R91 — turn_hook full core: payloads, request union, reply/ack, and the
# crate's first `#[serde(default = "fn")]`, `#[serde(tag = "phase")]`, and
# `#[serde(deny_unknown_fields)]` shapes.
# ---------------------------------------------------------------------------


class TestTurnHookConstantsR91:
    """The four R91 constants + the two serde-default-fn helpers. Critically,
    the OUTER ``HookEvent::Custom`` kind strings (``before_turn`` /
    ``after_turn``) must not collide with the INNER ``TurnHookRequest`` phase
    tag values (``before`` / ``after``)."""

    def test_outer_kind_constants(self):
        assert BEFORE_TURN_KIND == "before_turn"
        assert AFTER_TURN_KIND == "after_turn"
        assert TURN_HOOK_KIND == "turn_hook"

    def test_serde_default_constants(self):
        assert DEFAULT_SESSION_RELATIONSHIP == "primary"
        assert DEFAULT_SCHEMA_VERSION == "1.0"

    def test_outer_kind_differs_from_inner_phase(self):
        # BEFORE_TURN_KIND is the outer HookEvent::Custom kind ("before_turn");
        # the inner phase tag is "before" — the two must never be conflated.
        assert BEFORE_TURN_KIND != "before"
        assert AFTER_TURN_KIND != "after"

    def test_default_fn_helpers_return_constants(self):
        from minimax_code.tool_protocol.turn_hook import (
            _default_schema_version,
            _default_session_relationship,
        )

        assert _default_session_relationship() == DEFAULT_SESSION_RELATIONSHIP
        assert _default_schema_version() == DEFAULT_SCHEMA_VERSION


class TestBeforeTurnPayloadR91:
    """``BeforeTurnPayload`` — 6 fields, four serde defaults. The crate's first
    ``#[serde(default = "fn")]`` lands on ``session_relationship`` /
    ``schema_version`` (named-function default, not the type's zero value)."""

    def test_full_round_trip(self):
        payload = BeforeTurnPayload(
            turn_number=42,
            model_id="grok-3",
            yolo_mode=True,
            conversation_message_count=9,
            session_relationship="subagent",
            schema_version="1.0",
        )
        wire = payload.to_wire()
        assert wire == {
            "turn_number": 42,
            "model_id": "grok-3",
            "yolo_mode": True,
            "conversation_message_count": 9,
            "session_relationship": "subagent",
            "schema_version": "1.0",
        }
        assert before_turn_payload_from_wire(wire) == payload

    def test_yolo_mode_default_false(self):
        # #[serde(default)] — wire-omitted → False
        payload = before_turn_payload_from_wire({"turn_number": 1, "model_id": "grok-3"})
        assert payload.yolo_mode is False

    def test_conversation_message_count_default_zero(self):
        payload = before_turn_payload_from_wire({"turn_number": 1, "model_id": "grok-3"})
        assert payload.conversation_message_count == 0

    def test_session_relationship_default_fn(self):
        # #[serde(default = "default_session_relationship")] → "primary"
        payload = before_turn_payload_from_wire({"turn_number": 1, "model_id": "grok-3"})
        assert payload.session_relationship == DEFAULT_SESSION_RELATIONSHIP
        assert payload.session_relationship == "primary"

    def test_schema_version_default_fn(self):
        # #[serde(default = "default_schema_version")] → "1.0"
        payload = before_turn_payload_from_wire({"turn_number": 1, "model_id": "grok-3"})
        assert payload.schema_version == DEFAULT_SCHEMA_VERSION
        assert payload.schema_version == "1.0"

    def test_minimal_wire_round_trip_re_emits_defaults(self):
        wire = {"turn_number": 1, "model_id": "grok-3"}
        payload = before_turn_payload_from_wire(wire)
        # to_wire re-emits ALL fields (defaults are deserialise-only)
        assert payload.to_wire() == {
            "turn_number": 1,
            "model_id": "grok-3",
            "yolo_mode": False,
            "conversation_message_count": 0,
            "session_relationship": "primary",
            "schema_version": "1.0",
        }

    def test_tolerates_unknown_fields(self):
        # No deny_unknown_fields — unknown keys are ignored.
        payload = before_turn_payload_from_wire(
            {"turn_number": 1, "model_id": "grok-3", "future_field": "x"}
        )
        assert payload.turn_number == 1


class TestAfterTurnPayloadR91:
    """``AfterTurnPayload`` — 8 fields; ``outcome`` is strict
    ``TurnHookOutcome``; two cancellation ``Option``s are
    ``skip_serializing_if``; ``cancellation_context`` is opaque."""

    def test_completed_round_trip_skips_none_cancellation(self):
        payload = AfterTurnPayload(
            turn_number=42,
            outcome=TurnHookOutcome.COMPLETED,
            duration_ms=1500,
            tool_call_count=3,
            model_id="grok-3",
            written_repo_paths=["outputs/result.md"],
        )
        wire = payload.to_wire()
        assert wire == {
            "turn_number": 42,
            "outcome": "completed",
            "duration_ms": 1500,
            "tool_call_count": 3,
            "model_id": "grok-3",
            "written_repo_paths": ["outputs/result.md"],
        }
        assert after_turn_payload_from_wire(wire) == payload

    def test_written_repo_paths_default_empty(self):
        # #[serde(default)]
        payload = after_turn_payload_from_wire(
            {
                "turn_number": 1,
                "outcome": "completed",
                "duration_ms": 10,
                "tool_call_count": 0,
                "model_id": "grok-3",
            }
        )
        assert payload.written_repo_paths == []

    def test_cancellation_fields_emitted_when_present(self):
        payload = AfterTurnPayload(
            turn_number=2,
            outcome=TurnHookOutcome.CANCELLED,
            duration_ms=5000,
            tool_call_count=7,
            model_id="grok-3",
            cancellation_category="doom_loop_repetition",
            cancellation_context={"reason": "max_turns_reached", "limit": 50},
        )
        wire = payload.to_wire()
        assert wire["cancellation_category"] == "doom_loop_repetition"
        assert wire["cancellation_context"] == {"reason": "max_turns_reached", "limit": 50}

    def test_cancellation_context_opaque_round_trip(self):
        payload = after_turn_payload_from_wire(
            {
                "turn_number": 3,
                "outcome": "error",
                "duration_ms": 0,
                "tool_call_count": 1,
                "model_id": "grok-3",
                "cancellation_context": {"nested": [1, 2, {"x": True}]},
            }
        )
        # opaque — passed through verbatim, not stringified
        assert payload.cancellation_context == {"nested": [1, 2, {"x": True}]}

    def test_outcome_strict_unknown_raises(self):
        with pytest.raises(ValueError):
            after_turn_payload_from_wire(
                {
                    "turn_number": 1,
                    "outcome": "timeout",  # strict TurnHookOutcome
                    "duration_ms": 0,
                    "tool_call_count": 0,
                    "model_id": "grok-3",
                }
            )


class TestInjectionRoleR91:
    """``InjectionRole`` — snake_case Copy string-enum, strict."""

    def test_wire_values(self):
        assert InjectionRole.SYSTEM == "system"
        assert InjectionRole.DEVELOPER == "developer"
        assert InjectionRole.USER == "user"

    def test_from_wire_round_trip(self):
        for member in InjectionRole:
            assert InjectionRole.from_wire(str(member)) is member

    def test_strict_unknown_raises(self):
        with pytest.raises(ValueError):
            InjectionRole.from_wire("assistant")


class TestTurnControlR91:
    """``TurnControl`` — snake_case Copy + Default; AUTO is the default."""

    def test_wire_values(self):
        assert TurnControl.AUTO == "auto"
        assert TurnControl.FORCE_CONTINUE == "force_continue"
        assert TurnControl.FORCE_STOP == "force_stop"

    def test_auto_is_default(self):
        # mirrors #[derive(Default)] with #[default] on Auto
        reply = HookReply()
        assert reply.control is TurnControl.AUTO

    def test_from_wire_round_trip(self):
        for member in TurnControl:
            assert TurnControl.from_wire(str(member)) is member

    def test_strict_unknown_raises(self):
        with pytest.raises(ValueError):
            TurnControl.from_wire("pause")


class TestAfterTurnAckStatusR91:
    """``AfterTurnAckStatus`` — snake_case Copy; NOTE: no #[non_exhaustive]."""

    def test_wire_values(self):
        assert AfterTurnAckStatus.ENQUEUED == "enqueued"
        assert AfterTurnAckStatus.FAILED == "failed"
        assert AfterTurnAckStatus.SKIPPED == "skipped"

    def test_from_wire_round_trip(self):
        for member in AfterTurnAckStatus:
            assert AfterTurnAckStatus.from_wire(str(member)) is member

    def test_strict_unknown_raises(self):
        with pytest.raises(ValueError):
            AfterTurnAckStatus.from_wire("pending")


class TestHookInjectionR91:
    """``HookInjection`` — deny_unknown_fields (crate's first)."""

    def test_round_trip(self):
        inj = HookInjection(role=InjectionRole.SYSTEM, content="hello")
        wire = inj.to_wire()
        assert wire == {"role": "system", "content": "hello"}
        assert hook_injection_from_wire(wire) == inj

    def test_deny_unknown_fields(self):
        # #[serde(deny_unknown_fields)] — extra key raises
        with pytest.raises(ValueError):
            hook_injection_from_wire({"role": "user", "content": "x", "extra": True})

    def test_role_nested_enum(self):
        inj = hook_injection_from_wire({"role": "developer", "content": "y"})
        assert inj.role is InjectionRole.DEVELOPER


class TestAfterTurnAckPayloadR91:
    """``AfterTurnAckPayload`` — ``error_message`` skip_serializing_if None,
    ``artifact_count`` #[serde(default)] → 0."""

    def test_round_trip_without_error_message(self):
        ack = AfterTurnAckPayload(
            turn_number=5, status=AfterTurnAckStatus.ENQUEUED, artifact_count=2
        )
        wire = ack.to_wire()
        assert wire == {
            "turn_number": 5,
            "status": "enqueued",
            "artifact_count": 2,
        }
        # error_message omitted (skip_serializing_if)
        assert "error_message" not in wire

    def test_error_message_emitted_when_present(self):
        ack = AfterTurnAckPayload(
            turn_number=5,
            status=AfterTurnAckStatus.FAILED,
            error_message="disk full",
        )
        assert ack.to_wire()["error_message"] == "disk full"

    def test_artifact_count_default_zero(self):
        ack = after_turn_ack_payload_from_wire(
            {"turn_number": 1, "status": "skipped", "error_message": "no queue"}
        )
        assert ack.artifact_count == 0  # #[serde(default)]
        assert ack.status is AfterTurnAckStatus.SKIPPED

    def test_status_strict(self):
        with pytest.raises(ValueError):
            after_turn_ack_payload_from_wire({"turn_number": 1, "status": "unknown_status"})


class TestHookReplyR91:
    """``HookReply`` — deny_unknown_fields + Default ({} → no-op)."""

    def test_default_is_noop(self):
        # #[derive(Default)] — empty injections, AUTO control, None ack
        reply = HookReply()
        assert reply.injections == []
        assert reply.control is TurnControl.AUTO
        assert reply.after_turn_ack is None
        # default {} round-trips
        assert hook_reply_from_wire({}) == reply

    def test_default_to_wire(self):
        # default reply serialises both default fields (not skip_serializing_if)
        assert HookReply().to_wire() == {"injections": [], "control": "auto"}

    def test_full_round_trip(self):
        reply = HookReply(
            injections=[HookInjection(role=InjectionRole.USER, content="go")],
            control=TurnControl.FORCE_STOP,
            after_turn_ack=AfterTurnAckPayload(
                turn_number=7, status=AfterTurnAckStatus.ENQUEUED, artifact_count=1
            ),
        )
        wire = reply.to_wire()
        assert wire == {
            "injections": [{"role": "user", "content": "go"}],
            "control": "force_stop",
            "after_turn_ack": {
                "turn_number": 7,
                "status": "enqueued",
                "artifact_count": 1,
            },
        }
        assert hook_reply_from_wire(wire) == reply

    def test_deny_unknown_fields(self):
        with pytest.raises(ValueError):
            hook_reply_from_wire({"injections": [], "control": "auto", "rogue": 1})

    def test_after_turn_ack_none_omitted(self):
        reply = HookReply()
        assert "after_turn_ack" not in reply.to_wire()


class TestTurnHookRequestR91:
    """``TurnHookRequest`` — ``#[serde(tag = "phase", rename_all =
    "snake_case")]`` (the 6th tag-key); Before/After tuple variants wrapping
    the payloads; payload fields flatten to the top level alongside ``phase``."""

    def test_before_arm_round_trip(self):
        req = TurnHookRequestBefore(
            payload=BeforeTurnPayload(turn_number=1, model_id="grok-3", yolo_mode=True)
        )
        wire = req.to_wire()
        assert wire["phase"] == "before"  # NOT "before_turn"
        assert wire["turn_number"] == 1
        assert wire["model_id"] == "grok-3"
        assert wire["yolo_mode"] is True
        # payload fields flatten to top level — no nested "payload" key
        assert "payload" not in wire
        rebuilt = turn_hook_request_from_wire(wire)
        assert isinstance(rebuilt, TurnHookRequestBefore)
        assert rebuilt.payload.turn_number == 1
        assert rebuilt.payload.yolo_mode is True

    def test_after_arm_round_trip(self):
        req = TurnHookRequestAfter(
            payload=AfterTurnPayload(
                turn_number=2,
                outcome=TurnHookOutcome.COMPLETED,
                duration_ms=100,
                tool_call_count=1,
                model_id="grok-3",
            )
        )
        wire = req.to_wire()
        assert wire["phase"] == "after"  # NOT "after_turn"
        assert wire["outcome"] == "completed"
        rebuilt = turn_hook_request_from_wire(wire)
        assert isinstance(rebuilt, TurnHookRequestAfter)
        assert rebuilt.payload.outcome is TurnHookOutcome.COMPLETED

    def test_union_type_alias_covers_both_arms(self):
        # TurnHookRequest = TurnHookRequestBefore | TurnHookRequestAfter
        before: TurnHookRequest = TurnHookRequestBefore(
            payload=BeforeTurnPayload(turn_number=0, model_id="m")
        )
        after: TurnHookRequest = TurnHookRequestAfter(
            payload=AfterTurnPayload(
                turn_number=0,
                outcome=TurnHookOutcome.ERROR,
                duration_ms=0,
                tool_call_count=0,
                model_id="m",
            )
        )
        assert isinstance(before, TurnHookRequestBefore)
        assert isinstance(after, TurnHookRequestAfter)

    def test_unknown_phase_strict_raises(self):
        # #[non_exhaustive] + no #[serde(other)] — unknown phase raises
        with pytest.raises(ValueError):
            turn_hook_request_from_wire(
                {"phase": "during", "turn_number": 1, "model_id": "m"}
            )

    def test_before_payload_defaults_apply_via_request(self):
        # phase=before with minimal payload fields → serde defaults fill in
        req = turn_hook_request_from_wire(
            {"phase": "before", "turn_number": 9, "model_id": "grok-3"}
        )
        assert isinstance(req, TurnHookRequestBefore)
        assert req.payload.session_relationship == DEFAULT_SESSION_RELATIONSHIP
        assert req.payload.schema_version == DEFAULT_SCHEMA_VERSION


class TestDenyUnknownFieldsContrastR91:
    """``#[serde(deny_unknown_fields)]`` is the crate's strictest struct
    deserialise mode — only HookInjection / HookReply carry it. Contrast
    with BeforeTurnPayload / AfterTurnPayload which tolerate unknown keys."""

    def test_hook_injection_rejects_extra(self):
        with pytest.raises(ValueError):
            hook_injection_from_wire({"role": "user", "content": "x", "z": 1})

    def test_hook_reply_rejects_extra(self):
        with pytest.raises(ValueError):
            hook_reply_from_wire({"injections": [], "z": 1})

    def test_before_turn_payload_tolerates_extra(self):
        # No deny_unknown_fields — should NOT raise
        payload = before_turn_payload_from_wire(
            {"turn_number": 1, "model_id": "m", "future": True}
        )
        assert payload.turn_number == 1

    def test_after_turn_payload_tolerates_extra(self):
        payload = after_turn_payload_from_wire(
            {
                "turn_number": 1,
                "outcome": "completed",
                "duration_ms": 0,
                "tool_call_count": 0,
                "model_id": "m",
                "future": True,
            }
        )
        assert payload.turn_number == 1


class TestCrateFirstSerdeShapesR91:
    """R91 lands three crate-first serde shapes:
    ``#[serde(default = "fn")]``, ``#[serde(tag = "phase")]``, and
    ``#[serde(deny_unknown_fields)]``."""

    def test_serde_default_fn_not_type_zero(self):
        # default = "fn" means session_relationship defaults to "primary"
        # (a named constant), NOT to the String zero value ("").
        payload = before_turn_payload_from_wire({"turn_number": 1, "model_id": "m"})
        assert payload.session_relationship == "primary"
        assert payload.schema_version == "1.0"

    def test_phase_is_sixth_tag_key(self):
        # Prior tag keys: code (R83) / kind (R83) / shape (R83) / type (R89) /
        # event_type (R90). R91 adds "phase" — the 6th.
        wire = TurnHookRequestBefore(
            payload=BeforeTurnPayload(turn_number=0, model_id="m")
        ).to_wire()
        assert "phase" in wire

    def test_deny_unknown_fields_strictest_mode(self):
        # The only struct types that reject unknown fields are HookInjection
        # and HookReply; every other struct tolerates them.
        with pytest.raises(ValueError):
            hook_injection_from_wire({"role": "user", "content": "x", "e": 0})


class TestPackageSurfaceR91:
    """``turn_hook`` stays ``pub mod`` with no ``pub use`` — none of its
    symbols (R90 leaf OR R91 core) travel the barrel. All accessible via
    the submodule."""

    def test_barrel_still_excludes_turn_hook_symbols(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "TurnHookRequest",
            "BeforeTurnPayload",
            "AfterTurnPayload",
            "HookInjection",
            "HookReply",
            "InjectionRole",
            "TurnControl",
            "AfterTurnAckPayload",
            "AfterTurnAckStatus",
            "BEFORE_TURN_KIND",
            "AFTER_TURN_KIND",
        ):
            assert not hasattr(pkg, name), f"barrel should not re-export {name}"

    def test_submodule_exposes_r91_core(self):
        import minimax_code.tool_protocol.turn_hook as mod

        for name in (
            "TurnHookRequest",
            "TurnHookRequestBefore",
            "TurnHookRequestAfter",
            "BeforeTurnPayload",
            "AfterTurnPayload",
            "HookInjection",
            "HookReply",
            "AfterTurnAckPayload",
            "AfterTurnAckStatus",
            "InjectionRole",
            "TurnControl",
            "BEFORE_TURN_KIND",
            "AFTER_TURN_KIND",
            "DEFAULT_SESSION_RELATIONSHIP",
            "DEFAULT_SCHEMA_VERSION",
            "turn_hook_request_from_wire",
            "hook_reply_from_wire",
        ):
            assert hasattr(mod, name), f"submodule missing {name}"

    def test_session_event_still_imports_turn_hook_outcome(self):
        # R90 dependency (session_event → turn_hook.TurnHookOutcome) unchanged.
        from minimax_code.tool_protocol.session_event import TurnEnded

        ev = TurnEnded(
            turn_number=1,
            outcome=TurnHookOutcome.COMPLETED,
            duration_ms=0,
            tool_call_count=0,
            model_id="m",
        )
        assert ev.to_wire()["outcome"] == "completed"

    def test_all_r91_public_names_in_all(self):
        import minimax_code.tool_protocol.turn_hook as mod

        for name in (
            "TURN_HOOK_KIND",
            "BEFORE_TURN_KIND",
            "AFTER_TURN_KIND",
            "DEFAULT_SESSION_RELATIONSHIP",
            "DEFAULT_SCHEMA_VERSION",
            "TurnHookOutcome",
            "InjectionRole",
            "TurnControl",
            "AfterTurnAckStatus",
            "BeforeTurnPayload",
            "AfterTurnPayload",
            "HookInjection",
            "HookReply",
            "AfterTurnAckPayload",
            "TurnHookRequest",
            "TurnHookRequestBefore",
            "TurnHookRequestAfter",
        ):
            assert name in mod.__all__, f"{name} not in turn_hook.__all__"


# ===== R92 — frames.rs opening slice (tool call + telemetry donation) =====


class TestFramesConstantsR92:
    """First numeric ``usize`` constant family — 4 donation caps."""

    def test_max_spans_per_donation(self):
        assert MAX_SPANS_PER_DONATION == 512

    def test_max_donation_bytes_expression_const(self):
        # Kept as the 1024 * 1024 expression; equals 1 MiB exactly.
        assert MAX_DONATION_BYTES == 1024 * 1024
        assert MAX_DONATION_BYTES == 1048576

    def test_log_and_metric_record_caps(self):
        assert MAX_LOG_RECORDS_PER_DONATION == 512
        assert MAX_METRICS_PER_DONATION == 512

    def test_donation_consts_are_int(self):
        # First numeric consts — all ``int`` (unlike PROTOCOL_VERSION which is str).
        for c in (
            MAX_SPANS_PER_DONATION,
            MAX_DONATION_BYTES,
            MAX_LOG_RECORDS_PER_DONATION,
            MAX_METRICS_PER_DONATION,
        ):
            assert isinstance(c, int)


class TestToolCallParamsR92:
    """``ToolCallParams`` — 4 ``Option`` skip arms + opaque ``arguments``."""

    def test_minimal_to_wire_omits_all_option_fields(self):
        p = ToolCallParams(tool_call_id="tc_1", tool_id="t_1", arguments={"x": 1})
        wire = p.to_wire()
        assert wire == {"tool_call_id": "tc_1", "tool_id": "t_1", "arguments": {"x": 1}}
        for absent in ("deadline_ms", "behavior_version", "cwd", "trace_context"):
            assert absent not in wire

    def test_full_to_wire_includes_all_option_fields(self):
        p = ToolCallParams(
            tool_call_id="tc_1",
            tool_id="t_1",
            arguments=None,
            deadline_ms=5000,
            behavior_version="v1",
            cwd="/tmp",
            trace_context="00-trace",
        )
        assert p.to_wire() == {
            "tool_call_id": "tc_1",
            "tool_id": "t_1",
            "arguments": None,
            "deadline_ms": 5000,
            "behavior_version": "v1",
            "cwd": "/tmp",
            "trace_context": "00-trace",
        }

    def test_from_wire_minimal_lifts_none(self):
        p = tool_call_params_from_wire(
            {"tool_call_id": "tc_1", "tool_id": "t_1", "arguments": {"x": 1}}
        )
        assert p.deadline_ms is None
        assert p.behavior_version is None
        assert p.cwd is None
        assert p.trace_context is None
        assert p.arguments == {"x": 1}

    def test_from_wire_full(self):
        p = tool_call_params_from_wire(
            {
                "tool_call_id": "tc_1",
                "tool_id": "t_1",
                "arguments": "raw",
                "deadline_ms": 9000,
                "behavior_version": "v2",
                "cwd": "/home",
                "trace_context": "tc",
            }
        )
        assert p.deadline_ms == 9000
        assert p.behavior_version == "v2"
        assert p.cwd == "/home"
        assert p.trace_context == "tc"

    def test_arguments_opaque_verbatim_round_trip(self):
        # Opaque Value round-trips ANY JSON shape untouched.
        for args in ({"a": [1, 2]}, [1, "two", None], "plain", 42, True, None):
            p = ToolCallParams(tool_call_id="tc", tool_id="t", arguments=args)
            back = tool_call_params_from_wire(p.to_wire())
            assert back.arguments == args

    def test_round_trip_preserves_option_fields(self):
        p = ToolCallParams(
            tool_call_id="tc", tool_id="t", arguments={}, deadline_ms=7, cwd="/x"
        )
        back = tool_call_params_from_wire(p.to_wire())
        assert back.deadline_ms == 7
        assert back.cwd == "/x"
        assert back.behavior_version is None
        assert back.trace_context is None


class TestToolCallResultR92:
    """``Vec::is_empty`` skip (x2) + ``Option::is_none`` skip + nested wire enum."""

    def test_minimal_to_wire_omits_empty_vecs_and_none(self):
        r = ToolCallResult(tool_call_id="tc", output=Text(text="hi"))
        wire = r.to_wire()
        assert wire == {
            "tool_call_id": "tc",
            "output": {"kind": "text", "value": "hi"},
        }
        for absent in ("follow_ups", "reminders", "chat_completion_output"):
            assert absent not in wire

    def test_to_wire_with_non_empty_vecs(self):
        r = ToolCallResult(
            tool_call_id="tc",
            output=Text(text="hi"),
            follow_ups=[{"q": 1}],
            reminders=[{"r": 2}],
            chat_completion_output={"k": "v"},
        )
        wire = r.to_wire()
        assert wire["follow_ups"] == [{"q": 1}]
        assert wire["reminders"] == [{"r": 2}]
        assert wire["chat_completion_output"] == {"k": "v"}

    def test_from_wire_defaults_empty_vecs(self):
        r = tool_call_result_from_wire(
            {"tool_call_id": "tc", "output": {"kind": "text", "value": "hi"}}
        )
        assert r.follow_ups == []
        assert r.reminders == []
        assert r.chat_completion_output is None

    def test_from_wire_reconstructs_nested_wire_enum(self):
        r = tool_call_result_from_wire(
            {"tool_call_id": "tc", "output": {"kind": "json", "value": {"n": 1}}}
        )
        assert isinstance(r.output, Json)
        assert r.output.json == {"n": 1}

    def test_round_trip_with_vecs_and_output(self):
        r = ToolCallResult(
            tool_call_id="tc",
            output=Text(text="hi"),
            follow_ups=[1, 2],
            reminders=[{"k": "v"}],
            chat_completion_output=None,
        )
        back = tool_call_result_from_wire(r.to_wire())
        assert back.tool_call_id == "tc"
        assert isinstance(back.output, Text)
        assert back.output.text == "hi"
        assert back.follow_ups == [1, 2]
        assert back.reminders == [{"k": "v"}]
        assert back.chat_completion_output is None


class TestToolCallProgressFrameR92:
    """Opaque ``body`` + ``Option`` skip on ``dropped_count``."""

    def test_minimal_to_wire_omits_dropped_count(self):
        f = ToolCallProgressFrame(tool_call_id="tc", kind="log_chunk", body={"i": 0})
        assert f.to_wire() == {
            "tool_call_id": "tc",
            "kind": "log_chunk",
            "body": {"i": 0},
        }

    def test_to_wire_with_dropped_count(self):
        f = ToolCallProgressFrame(
            tool_call_id="tc", kind="chunk", body="data", dropped_count=3
        )
        assert f.to_wire()["dropped_count"] == 3

    def test_from_wire_minimal(self):
        f = tool_call_progress_frame_from_wire(
            {"tool_call_id": "tc", "kind": "log_chunk", "body": {"i": 0}}
        )
        assert f.dropped_count is None
        assert f.body == {"i": 0}

    def test_body_opaque_verbatim_round_trip(self):
        for body in ({"a": 1}, [1, 2], "s", 5, False):
            f = ToolCallProgressFrame(tool_call_id="tc", kind="k", body=body)
            back = tool_call_progress_frame_from_wire(f.to_wire())
            assert back.body == body


class TestDonationParamsR92:
    """Traces / Logs / Metrics donation — same single-field ``otlp_request`` shape."""

    def test_traces_donate_round_trip(self):
        p = TracesDonateParams(otlp_request="base64-trace")
        assert p.to_wire() == {"otlp_request": "base64-trace"}
        back = traces_donate_params_from_wire(p.to_wire())
        assert back.otlp_request == "base64-trace"

    def test_logs_donate_round_trip(self):
        p = LogsDonateParams(otlp_request="base64-log")
        assert p.to_wire() == {"otlp_request": "base64-log"}
        assert logs_donate_params_from_wire(p.to_wire()).otlp_request == "base64-log"

    def test_metrics_donate_round_trip(self):
        p = MetricsDonateParams(otlp_request="base64-metric")
        assert p.to_wire() == {"otlp_request": "base64-metric"}
        assert (
            metrics_donate_params_from_wire(p.to_wire()).otlp_request == "base64-metric"
        )

    def test_donation_params_shape_uniformity(self):
        # All three share the exact same single-field shape (otlp_request: str).
        for cls in (TracesDonateParams, LogsDonateParams, MetricsDonateParams):
            fields = {f.name for f in dataclasses.fields(cls)}
            assert fields == {"otlp_request"}
            assert cls(otlp_request="x").to_wire() == {"otlp_request": "x"}


class TestFramesSerdeShapesR92:
    """Consolidation round — exercises four serde sub-shapes in flat params."""

    def test_vec_is_empty_skip_variant(self):
        # R86 introduced Vec::is_empty; R92 is its first landing in a params struct.
        empty = ToolCallResult(tool_call_id="tc", output=Text(text="x"))
        assert "follow_ups" not in empty.to_wire()
        nonempty = ToolCallResult(
            tool_call_id="tc", output=Text(text="x"), follow_ups=[{}]
        )
        assert "follow_ups" in nonempty.to_wire()

    def test_option_is_none_skip_variant(self):
        minimal = ToolCallParams(tool_call_id="tc", tool_id="t", arguments={})
        assert "deadline_ms" not in minimal.to_wire()
        full = ToolCallParams(
            tool_call_id="tc", tool_id="t", arguments={}, deadline_ms=1
        )
        assert "deadline_ms" in full.to_wire()

    def test_opaque_value_not_converted(self):
        # arguments / body / chat_completion_output pass through untouched.
        weird = {"nested": {"list": [1, None, True]}, "num": 3.14}
        p = ToolCallParams(tool_call_id="tc", tool_id="t", arguments=weird)
        assert p.to_wire()["arguments"] is weird  # same object, no conversion

    def test_nested_wire_enum_field(self):
        # ToolCallResult.output is a ToolOutputWire instance, not a dict.
        r = ToolCallResult(tool_call_id="tc", output=Mcp(blocks=[]))
        wire = r.to_wire()
        assert wire["output"] == {"kind": "mcp", "value": {"blocks": []}}


class TestFramesBarrelR92:
    """``frames`` is ``pub use frames::{...}`` — all 10 R92 symbols travel barrel."""

    def test_barrel_exports_frames_symbols(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "MAX_SPANS_PER_DONATION",
            "MAX_DONATION_BYTES",
            "MAX_LOG_RECORDS_PER_DONATION",
            "MAX_METRICS_PER_DONATION",
            "ToolCallParams",
            "ToolCallResult",
            "ToolCallProgressFrame",
            "TracesDonateParams",
            "LogsDonateParams",
            "MetricsDonateParams",
        ):
            assert hasattr(pkg, name), f"barrel missing frames symbol {name}"

    def test_all_frames_names_in_all(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "MAX_SPANS_PER_DONATION",
            "MAX_DONATION_BYTES",
            "MAX_LOG_RECORDS_PER_DONATION",
            "MAX_METRICS_PER_DONATION",
            "ToolCallParams",
            "ToolCallResult",
            "ToolCallProgressFrame",
            "TracesDonateParams",
            "LogsDonateParams",
            "MetricsDonateParams",
        ):
            assert name in pkg.__all__, f"{name} not in barrel __all__"

    def test_frames_submodule_exposes_from_wire(self):
        import minimax_code.tool_protocol.frames as mod

        for name in (
            "tool_call_params_from_wire",
            "tool_call_result_from_wire",
            "tool_call_progress_frame_from_wire",
            "traces_donate_params_from_wire",
            "logs_donate_params_from_wire",
            "metrics_donate_params_from_wire",
        ):
            assert hasattr(mod, name), f"frames submodule missing {name}"


# ── R93 — heartbeat domain (PingFrame/PongFrame) ──────────────────────────
# The crate's first non-derive custom impl Serialize/Deserialize: the
# ``method`` discriminator is injected by ``to_wire`` (not a struct field),
# and ``from_wire`` is lenient on ``method`` but raises on a present-but-
# mismatched value. Values sourced from ``Method.as_wire_str`` (DRY).


class TestPingFrameR93:
    """``PingFrame`` — custom Serialize injects ``method`` from Method enum."""

    def test_to_wire_injects_method_ping(self):
        wire = PingFrame(ts_ms=123).to_wire()
        assert wire["method"] == "ping"
        assert wire["ts_ms"] == 123

    def test_to_wire_method_sourced_from_enum(self):
        """DRY single-source: ``method`` value == ``Method.Ping.as_wire_str()``."""
        assert PingFrame(ts_ms=0).to_wire()["method"] == Method.Ping.as_wire_str()

    def test_to_wire_method_key_precedes_ts_ms(self):
        """Mirrors Rust's serialize_map insertion order: method first."""
        keys = list(PingFrame(ts_ms=7).to_wire())
        assert keys[0] == "method"
        assert keys[1] == "ts_ms"

    def test_method_is_not_a_struct_field(self):
        """``method`` is injected, not carried — dataclass has only ``ts_ms``."""
        field_names = {f.name for f in dataclasses.fields(PingFrame)}
        assert field_names == {"ts_ms"}


class TestPongFrameR93:
    """``PongFrame`` — symmetric counterpart to ``PingFrame``."""

    def test_to_wire_injects_method_pong(self):
        wire = PongFrame(ts_ms=456).to_wire()
        assert wire["method"] == "pong"
        assert wire["ts_ms"] == 456

    def test_to_wire_method_sourced_from_enum(self):
        assert PongFrame(ts_ms=0).to_wire()["method"] == Method.Pong.as_wire_str()

    def test_method_key_precedes_ts_ms(self):
        keys = list(PongFrame(ts_ms=9).to_wire())
        assert keys[0] == "method"
        assert keys[1] == "ts_ms"


class TestHeartbeatSerdeShapeR93:
    """Custom Deserialize: lenient on ``method``, strict on ``ts_ms``."""

    def test_ping_from_wire_lenient_no_method(self):
        """Older frames without ``method`` still parse (forward-compat)."""
        assert ping_frame_from_wire({"ts_ms": 100}).ts_ms == 100

    def test_pong_from_wire_lenient_no_method(self):
        assert pong_frame_from_wire({"ts_ms": 200}).ts_ms == 200

    def test_ping_from_wire_accepts_matching_method(self):
        assert ping_frame_from_wire({"method": "ping", "ts_ms": 1}).ts_ms == 1

    def test_ping_from_wire_rejects_mismatched_method(self):
        """Present-but-wrong ``method`` raises ValueError (serde::de::Error::custom)."""
        with pytest.raises(ValueError):
            ping_frame_from_wire({"method": "pong", "ts_ms": 1})

    def test_pong_from_wire_rejects_mismatched_method(self):
        with pytest.raises(ValueError):
            pong_frame_from_wire({"method": "ping", "ts_ms": 1})

    def test_ping_from_wire_ts_ms_required(self):
        """``ts_ms`` is required — missing raises KeyError."""
        with pytest.raises(KeyError):
            ping_frame_from_wire({"method": "ping"})

    def test_pong_from_wire_ts_ms_required(self):
        with pytest.raises(KeyError):
            pong_frame_from_wire({"method": "pong"})

    def test_ping_round_trip(self):
        original = PingFrame(ts_ms=999)
        assert ping_frame_from_wire(original.to_wire()) == original

    def test_pong_round_trip(self):
        original = PongFrame(ts_ms=888)
        assert pong_frame_from_wire(original.to_wire()) == original


class TestHeartbeatBarrelR93:
    """Heartbeat symbols travel the barrel; from_wire stay submodule-qualified."""

    def test_barrel_exports_heartbeat_symbols(self):
        import minimax_code.tool_protocol as pkg

        for name in ("PingFrame", "PongFrame"):
            assert hasattr(pkg, name), f"barrel missing heartbeat symbol {name}"

    def test_heartbeat_names_in_all(self):
        import minimax_code.tool_protocol as pkg

        for name in ("PingFrame", "PongFrame"):
            assert name in pkg.__all__, f"{name} not in barrel __all__"

    def test_frames_submodule_exposes_from_wire(self):
        import minimax_code.tool_protocol.frames as mod

        for name in ("ping_frame_from_wire", "pong_frame_from_wire"):
            assert hasattr(mod, name), f"frames submodule missing {name}"

    def test_barrel_does_not_re_export_from_wire(self):
        """from_wire converters stay submodule-qualified, mirroring the crate."""
        import minimax_code.tool_protocol as pkg

        assert not hasattr(pkg, "ping_frame_from_wire")
        assert not hasattr(pkg, "pong_frame_from_wire")


# ── R94 — registration frames (params-as-DTO-wrapper consolidation) ────────


class TestRegisterToolParams:
    """``RegisterToolParams`` — params-as-DTO-wrapper over ``ToolRegistration``."""

    def _registration(self) -> ToolRegistration:
        return ToolRegistration(
            tool_id=ToolId("bash"),
            user_id=UserId("u1"),
            description=ToolDescription(name="bash", description="d"),
            transport_kind=TransportKind.Local,
        )

    def test_to_wire_delegates_to_embedded_dto(self):
        # The wrapper adds exactly one key — "tool" — whose value is the
        # embedded DTO's own to_wire output, unchanged. This is the defining
        # shape of the params-as-DTO-wrapper pattern (no field re-ordering,
        # no re-keying, pure delegation).
        reg = self._registration()
        params = RegisterToolParams(tool=reg)
        assert params.to_wire() == {"tool": reg.to_wire()}

    def test_to_wire_has_single_key(self):
        assert list(RegisterToolParams(tool=self._registration()).to_wire()) == ["tool"]

    def test_from_wire_delegates_to_dto_classmethod(self):
        reg = self._registration()
        back = register_tool_params_from_wire({"tool": reg.to_wire()})
        assert isinstance(back, RegisterToolParams)
        assert back.tool.tool_id == ToolId("bash")
        assert back.tool.transport_kind is TransportKind.Local

    def test_round_trip(self):
        original = RegisterToolParams(tool=self._registration())
        assert register_tool_params_from_wire(original.to_wire()) == original


class TestRegisterServerParams:
    """``RegisterServerParams`` — params-as-DTO-wrapper over ``ToolServerRegistration``."""

    def _server(self) -> ToolServerRegistration:
        return ToolServerRegistration(
            server_id=ServerId("srv1"),
            user_id=UserId("u1"),
            tools=[
                ToolDescriptionWithSchema(
                    description=ToolDescription(name="bash", description="d")
                )
            ],
        )

    def test_to_wire_delegates_to_embedded_dto(self):
        srv = self._server()
        assert RegisterServerParams(server=srv).to_wire() == {"server": srv.to_wire()}

    def test_to_wire_has_single_key(self):
        assert list(RegisterServerParams(server=self._server()).to_wire()) == ["server"]

    def test_round_trip(self):
        original = RegisterServerParams(server=self._server())
        back = register_server_params_from_wire(original.to_wire())
        assert back == original
        assert back.server.server_id == ServerId("srv1")
        assert len(back.server.tools) == 1


class TestUnregisterToolParams:
    """``UnregisterToolParams`` — bare-newtype-as-str, mirrors ``ToolCallParams.tool_id``."""

    def test_to_wire_serialises_newtype_as_str(self):
        # ``tool_id`` is a ToolId str-newtype (R82); it serialises directly as
        # a string — the same shape as ToolCallParams.tool_id (R92), not wrapped
        # in any sub-object.
        assert UnregisterToolParams(tool_id=ToolId("ns:bash")).to_wire() == {
            "tool_id": "ns:bash"
        }

    def test_to_wire_has_single_key(self):
        assert list(UnregisterToolParams(tool_id=ToolId("x")).to_wire()) == ["tool_id"]

    def test_from_wire_lifts_as_newtype(self):
        back = unregister_tool_params_from_wire({"tool_id": "ns:bash"})
        assert isinstance(back, UnregisterToolParams)
        assert back.tool_id == ToolId("ns:bash")

    def test_round_trip(self):
        original = UnregisterToolParams(tool_id=ToolId("ns:bash"))
        assert unregister_tool_params_from_wire(original.to_wire()) == original


class TestUnregisterServerParams:
    """``UnregisterServerParams`` — bare-newtype-as-str over ``ServerId``."""

    def test_to_wire_serialises_newtype_as_str(self):
        assert UnregisterServerParams(server_id=ServerId("srv1")).to_wire() == {
            "server_id": "srv1"
        }

    def test_to_wire_has_single_key(self):
        assert list(
            UnregisterServerParams(server_id=ServerId("x")).to_wire()
        ) == ["server_id"]

    def test_from_wire_lifts_as_newtype(self):
        back = unregister_server_params_from_wire({"server_id": "srv1"})
        assert isinstance(back, UnregisterServerParams)
        assert back.server_id == ServerId("srv1")

    def test_round_trip(self):
        original = UnregisterServerParams(server_id=ServerId("srv1"))
        assert unregister_server_params_from_wire(original.to_wire()) == original


class TestRegistrationParamsBarrelR94:
    """Registration params travel the barrel; from_wire stay submodule-qualified."""

    def test_barrel_exports_registration_params(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "RegisterToolParams",
            "RegisterServerParams",
            "UnregisterToolParams",
            "UnregisterServerParams",
        ):
            assert hasattr(pkg, name), f"barrel missing registration param {name}"

    def test_registration_params_in_all(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "RegisterToolParams",
            "RegisterServerParams",
            "UnregisterToolParams",
            "UnregisterServerParams",
        ):
            assert name in pkg.__all__, f"{name} not in barrel __all__"

    def test_frames_submodule_exposes_from_wire(self):
        import minimax_code.tool_protocol.frames as mod

        for name in (
            "register_tool_params_from_wire",
            "register_server_params_from_wire",
            "unregister_tool_params_from_wire",
            "unregister_server_params_from_wire",
        ):
            assert hasattr(mod, name), f"frames submodule missing {name}"

    def test_barrel_does_not_re_export_from_wire(self):
        """from_wire converters stay submodule-qualified, mirroring the crate."""
        import minimax_code.tool_protocol as pkg

        for name in (
            "register_tool_params_from_wire",
            "register_server_params_from_wire",
            "unregister_tool_params_from_wire",
            "unregister_server_params_from_wire",
        ):
            assert not hasattr(pkg, name), f"barrel should not export {name}"


class TestToolSessionBindOutcome:
    """Strict snake_case StrEnum (no #[serde(other)] catch-all, like R86 HookKind)."""

    def test_to_wire_is_snake_case_value(self):
        assert ToolSessionBindOutcome.Bound.to_wire() == "bound"
        assert ToolSessionBindOutcome.AlreadyBound.to_wire() == "already_bound"
        assert ToolSessionBindOutcome.UnknownTool.to_wire() == "unknown_tool"
        assert ToolSessionBindOutcome.SessionNotBound.to_wire() == "session_not_bound"

    def test_str_is_wire_value(self):
        assert str(ToolSessionBindOutcome.Bound) == "bound"

    def test_from_wire_known(self):
        assert ToolSessionBindOutcome.from_wire("bound") is ToolSessionBindOutcome.Bound
        assert (
            ToolSessionBindOutcome.from_wire("already_bound")
            is ToolSessionBindOutcome.AlreadyBound
        )
        assert (
            ToolSessionBindOutcome.from_wire("unknown_tool")
            is ToolSessionBindOutcome.UnknownTool
        )
        assert (
            ToolSessionBindOutcome.from_wire("session_not_bound")
            is ToolSessionBindOutcome.SessionNotBound
        )

    def test_from_wire_unknown_raises(self):
        """No #[serde(other)] arm — unknown values raise (strict)."""
        with pytest.raises(ValueError):
            ToolSessionBindOutcome.from_wire("conflict")


class TestToolSessionUnbindOutcome:
    """Strict snake_case StrEnum (no #[serde(other)] catch-all)."""

    def test_to_wire_is_snake_case_value(self):
        assert ToolSessionUnbindOutcome.Unbound.to_wire() == "unbound"
        assert ToolSessionUnbindOutcome.NotBound.to_wire() == "not_bound"
        assert ToolSessionUnbindOutcome.UnknownTool.to_wire() == "unknown_tool"

    def test_from_wire_known(self):
        assert (
            ToolSessionUnbindOutcome.from_wire("unbound") is ToolSessionUnbindOutcome.Unbound
        )
        assert (
            ToolSessionUnbindOutcome.from_wire("not_bound") is ToolSessionUnbindOutcome.NotBound
        )
        assert (
            ToolSessionUnbindOutcome.from_wire("unknown_tool")
            is ToolSessionUnbindOutcome.UnknownTool
        )

    def test_from_wire_unknown_raises(self):
        with pytest.raises(ValueError):
            ToolSessionUnbindOutcome.from_wire("definitely_not_a_real_outcome")


class TestBindToolSessionParams:
    """Two bare id newtypes (newtype-as-str x 2)."""

    def test_to_wire_has_both_keys(self):
        p = BindToolSessionParams(tool_id=ToolId("ns:bash"), session_id=SessionId("s1"))
        assert p.to_wire() == {"tool_id": "ns:bash", "session_id": "s1"}

    def test_from_wire_lifts_both_newtypes(self):
        p = bind_tool_session_params_from_wire(
            {"tool_id": "ns:bash", "session_id": "s1"}
        )
        assert p.tool_id == "ns:bash"
        assert p.session_id == "s1"

    def test_round_trip(self):
        original = BindToolSessionParams(
            tool_id=ToolId("ns:bash"), session_id=SessionId("s1")
        )
        assert bind_tool_session_params_from_wire(original.to_wire()) == original


class TestUnbindToolSessionParams:
    """Two bare id newtypes (mirror of bind)."""

    def test_to_wire_has_both_keys(self):
        p = UnbindToolSessionParams(
            tool_id=ToolId("ns:bash"), session_id=SessionId("s1")
        )
        assert p.to_wire() == {"tool_id": "ns:bash", "session_id": "s1"}

    def test_from_wire_lifts_both_newtypes(self):
        p = unbind_tool_session_params_from_wire(
            {"tool_id": "ns:bash", "session_id": "s1"}
        )
        assert p.tool_id == "ns:bash"
        assert p.session_id == "s1"

    def test_round_trip(self):
        original = UnbindToolSessionParams(
            tool_id=ToolId("ns:bash"), session_id=SessionId("s1")
        )
        assert unbind_tool_session_params_from_wire(original.to_wire()) == original


class TestBindToolSessionAck:
    """Ack wraps a single strict outcome enum (ack-wraps-strict-enum)."""

    def test_to_wire_emits_outcome_string(self):
        ack = BindToolSessionAck(outcome=ToolSessionBindOutcome.Bound)
        assert ack.to_wire() == {"outcome": "bound"}

    def test_from_wire_lifts_strict_outcome(self):
        ack = bind_tool_session_ack_from_wire({"outcome": "already_bound"})
        assert ack.outcome is ToolSessionBindOutcome.AlreadyBound

    def test_from_wire_unknown_outcome_raises(self):
        """The wrapped enum is strict — unknown outcomes propagate the raise."""
        with pytest.raises(ValueError):
            bind_tool_session_ack_from_wire({"outcome": "conflict"})

    def test_round_trip(self):
        for outcome in ToolSessionBindOutcome:
            original = BindToolSessionAck(outcome=outcome)
            assert bind_tool_session_ack_from_wire(original.to_wire()) == original


class TestUnbindToolSessionAck:
    """Ack wraps a single strict outcome enum."""

    def test_to_wire_emits_outcome_string(self):
        ack = UnbindToolSessionAck(outcome=ToolSessionUnbindOutcome.Unbound)
        assert ack.to_wire() == {"outcome": "unbound"}

    def test_from_wire_lifts_strict_outcome(self):
        ack = unbind_tool_session_ack_from_wire({"outcome": "not_bound"})
        assert ack.outcome is ToolSessionUnbindOutcome.NotBound

    def test_from_wire_unknown_outcome_raises(self):
        with pytest.raises(ValueError):
            unbind_tool_session_ack_from_wire({"outcome": "definitely_bogus"})

    def test_round_trip(self):
        for outcome in ToolSessionUnbindOutcome:
            original = UnbindToolSessionAck(outcome=outcome)
            assert unbind_tool_session_ack_from_wire(original.to_wire()) == original


class TestPerToolSessionBindingBarrelR95:
    """Per-tool session binding symbols travel the barrel; from_wire stay submodule-qualified."""

    def test_barrel_exports_binding_symbols(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "BindToolSessionParams",
            "UnbindToolSessionParams",
            "BindToolSessionAck",
            "UnbindToolSessionAck",
            "ToolSessionBindOutcome",
            "ToolSessionUnbindOutcome",
        ):
            assert hasattr(pkg, name), f"barrel missing binding symbol {name}"

    def test_binding_symbols_in_all(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "BindToolSessionParams",
            "UnbindToolSessionParams",
            "BindToolSessionAck",
            "UnbindToolSessionAck",
            "ToolSessionBindOutcome",
            "ToolSessionUnbindOutcome",
        ):
            assert name in pkg.__all__, f"{name} not in barrel __all__"

    def test_frames_submodule_exposes_from_wire(self):
        import minimax_code.tool_protocol.frames as mod

        for name in (
            "bind_tool_session_params_from_wire",
            "unbind_tool_session_params_from_wire",
            "bind_tool_session_ack_from_wire",
            "unbind_tool_session_ack_from_wire",
        ):
            assert hasattr(mod, name), f"frames submodule missing {name}"

    def test_barrel_does_not_re_export_from_wire(self):
        """from_wire converters stay submodule-qualified, mirroring the crate."""
        import minimax_code.tool_protocol as pkg

        for name in (
            "bind_tool_session_params_from_wire",
            "unbind_tool_session_params_from_wire",
            "bind_tool_session_ack_from_wire",
            "unbind_tool_session_ack_from_wire",
        ):
            assert not hasattr(pkg, name), f"barrel should not export {name}"


class TestToolsListParams:
    """``tools.list`` params — session_id payload + embedded ToolDefinitionMode."""

    def test_to_wire_full_mode(self):
        params = ToolsListParams(session_id=SessionId("s-1"), mode=ToolDefinitionMode.full())
        assert params.to_wire() == {
            "session_id": "s-1",
            "mode": {"mode": "full"},
        }

    def test_to_wire_concise_mode(self):
        params = ToolsListParams(
            session_id=SessionId("s-1"),
            mode=ToolDefinitionMode.concise(ToolId("search"), ToolId("call")),
        )
        wire = params.to_wire()
        assert wire["session_id"] == "s-1"
        assert wire["mode"]["mode"] == "concise"

    def test_from_wire_lifts_session_and_mode(self):
        params = tools_list_params_from_wire(
            {"session_id": "s-1", "mode": {"mode": "full"}}
        )
        assert params.session_id == SessionId("s-1")
        assert params.mode == ToolDefinitionMode.full()

    def test_round_trip(self):
        original = ToolsListParams(
            session_id=SessionId("s-9"),
            mode=ToolDefinitionMode.concise(ToolId("a"), ToolId("b")),
        )
        assert tools_list_params_from_wire(original.to_wire()) == original


class TestToolsListResult:
    """``tools.list`` result — the crate's first list-of-bare-pydantic-model."""

    def test_to_wire_dumps_each_description(self):
        result = ToolsListResult(
            tools=[
                ToolDescription(name="bash", description="d"),
                ToolDescription(name="ls", description="list"),
            ]
        )
        wire = result.to_wire()
        assert [t["name"] for t in wire["tools"]] == ["bash", "ls"]

    def test_to_wire_excludes_none_fields(self):
        result = ToolsListResult(tools=[ToolDescription(name="bash", description="d")])
        wire = result.to_wire()
        # namespace / title / kind / arguments_schema are None → excluded.
        assert wire["tools"][0] == {"name": "bash", "description": "d"}

    def test_from_wire_validates_each_model(self):
        result = tools_list_result_from_wire(
            {"tools": [{"name": "bash", "description": "d"}]}
        )
        assert len(result.tools) == 1
        assert result.tools[0].name == "bash"

    def test_round_trip_preserves_descriptions(self):
        original = ToolsListResult(
            tools=[
                ToolDescription(name="a", description="x").with_namespace("ns"),
                ToolDescription(name="b", description="y"),
            ]
        )
        rebuilt = tools_list_result_from_wire(original.to_wire())
        assert [t.name for t in rebuilt.tools] == ["a", "b"]
        assert rebuilt.tools[0].namespace == "ns"

    def test_empty_list_round_trip(self):
        original = ToolsListResult(tools=[])
        assert tools_list_result_from_wire(original.to_wire()) == original


class TestToolsSearchParams:
    """``tools.search`` params — session_id + query + usize limit."""

    def test_to_wire(self):
        params = ToolsSearchParams(session_id=SessionId("s-1"), query="shell", limit=10)
        assert params.to_wire() == {
            "session_id": "s-1",
            "query": "shell",
            "limit": 10,
        }

    def test_from_wire(self):
        params = tools_search_params_from_wire(
            {"session_id": "s-1", "query": "shell", "limit": 10}
        )
        assert params.session_id == SessionId("s-1")
        assert params.query == "shell"
        assert params.limit == 10
        assert isinstance(params.limit, int)

    def test_round_trip(self):
        original = ToolsSearchParams(session_id=SessionId("s-9"), query="x", limit=5)
        assert tools_search_params_from_wire(original.to_wire()) == original


class TestToolSearchResult:
    """One search match — opaque input_schema passthrough + f32 score."""

    def _make(self) -> ToolSearchResult:
        return ToolSearchResult(
            tool_name="bash",
            server_name="srv-1",
            description="Run a shell",
            score=0.875,
            parameters=["cmd", "cwd"],
            input_schema={"type": "object", "properties": {}},
        )

    def test_to_wire_round_trips_opaque_schema(self):
        wire = self._make().to_wire()
        assert wire["input_schema"] == {"type": "object", "properties": {}}
        assert wire["score"] == 0.875
        assert wire["parameters"] == ["cmd", "cwd"]

    def test_from_wire_lifts_opaque_schema_verbatim(self):
        rebuilt = tool_search_result_from_wire(self._make().to_wire())
        assert rebuilt.input_schema == {"type": "object", "properties": {}}
        assert rebuilt.score == 0.875
        assert isinstance(rebuilt.score, float)

    def test_input_schema_accepts_non_dict_json(self):
        """Opaque Value accepts any JSON — arrays pass verbatim (not just dicts)."""
        result = ToolSearchResult(
            tool_name="t",
            server_name="srv",
            description="d",
            score=1.0,
            parameters=[],
            input_schema=["a", "b"],
        )
        rebuilt = tool_search_result_from_wire(result.to_wire())
        assert rebuilt.input_schema == ["a", "b"]

    def test_round_trip(self):
        original = self._make()
        assert tool_search_result_from_wire(original.to_wire()) == original


class TestToolsSearchResultBody:
    """``tools.search_result`` body — list of ToolSearchResult + counters."""

    def _result(self) -> ToolSearchResult:
        return ToolSearchResult(
            tool_name="t",
            server_name="srv",
            description="d",
            score=0.5,
            parameters=[],
            input_schema={},
        )

    def test_to_wire(self):
        body = ToolsSearchResultBody(
            results=[self._result()],
            total_hidden_tools=3,
            is_ready=True,
        )
        wire = body.to_wire()
        assert len(wire["results"]) == 1
        assert wire["total_hidden_tools"] == 3
        assert wire["is_ready"] is True

    def test_from_wire(self):
        body = tools_search_result_body_from_wire(
            {
                "results": [self._result().to_wire()],
                "total_hidden_tools": 3,
                "is_ready": True,
            }
        )
        assert len(body.results) == 1
        assert body.results[0].tool_name == "t"
        assert body.total_hidden_tools == 3
        assert body.is_ready is True

    def test_round_trip(self):
        original = ToolsSearchResultBody(
            results=[
                ToolSearchResult(
                    tool_name="a",
                    server_name="srv",
                    description="d",
                    score=0.9,
                    parameters=["p"],
                    input_schema={"x": 1},
                )
            ],
            total_hidden_tools=0,
            is_ready=False,
        )
        assert tools_search_result_body_from_wire(original.to_wire()) == original


class TestListAndSearchBarrelR96:
    """List & search symbols travel the barrel; from_wire stay submodule-qualified."""

    def test_barrel_exports_list_search_symbols(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "ToolsListParams",
            "ToolsListResult",
            "ToolsSearchParams",
            "ToolSearchResult",
            "ToolsSearchResultBody",
        ):
            assert hasattr(pkg, name), f"barrel missing list/search symbol {name}"

    def test_list_search_symbols_in_all(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "ToolsListParams",
            "ToolsListResult",
            "ToolsSearchParams",
            "ToolSearchResult",
            "ToolsSearchResultBody",
        ):
            assert name in pkg.__all__, f"{name} not in barrel __all__"

    def test_frames_submodule_exposes_from_wire(self):
        import minimax_code.tool_protocol.frames as mod

        for name in (
            "tools_list_params_from_wire",
            "tools_list_result_from_wire",
            "tools_search_params_from_wire",
            "tool_search_result_from_wire",
            "tools_search_result_body_from_wire",
        ):
            assert hasattr(mod, name), f"frames submodule missing {name}"

    def test_barrel_does_not_re_export_from_wire(self):
        """from_wire converters stay submodule-qualified, mirroring the crate."""
        import minimax_code.tool_protocol as pkg

        for name in (
            "tools_list_params_from_wire",
            "tools_list_result_from_wire",
            "tools_search_params_from_wire",
            "tool_search_result_from_wire",
            "tools_search_result_body_from_wire",
        ):
            assert not hasattr(pkg, name), f"barrel should not export {name}"


class TestSubscribeOutcome:
    """``SubscribeOutcome`` — strict snake_case StrEnum (mirrors R95 bind outcome)."""

    def test_member_values_are_snake_case(self):
        assert SubscribeOutcome.Subscribed == "subscribed"
        assert SubscribeOutcome.AlreadySubscribed == "already_subscribed"
        assert SubscribeOutcome.NotAuthorized == "not_authorized"

    def test_to_wire_returns_value(self):
        assert SubscribeOutcome.Subscribed.to_wire() == "subscribed"
        assert SubscribeOutcome.AlreadySubscribed.to_wire() == "already_subscribed"
        assert SubscribeOutcome.NotAuthorized.to_wire() == "not_authorized"

    def test_from_wire_accepts_known(self):
        assert SubscribeOutcome.from_wire("subscribed") is SubscribeOutcome.Subscribed
        assert (
            SubscribeOutcome.from_wire("already_subscribed")
            is SubscribeOutcome.AlreadySubscribed
        )
        assert (
            SubscribeOutcome.from_wire("not_authorized")
            is SubscribeOutcome.NotAuthorized
        )

    def test_from_wire_rejects_unknown(self):
        with pytest.raises(ValueError):
            SubscribeOutcome.from_wire("pending")


class TestUnsubscribeOutcome:
    """``UnsubscribeOutcome`` — strict snake_case StrEnum; ``Evicted`` is server-push."""

    def test_member_values_are_snake_case(self):
        assert UnsubscribeOutcome.Unsubscribed == "unsubscribed"
        assert UnsubscribeOutcome.NotSubscribed == "not_subscribed"
        assert UnsubscribeOutcome.Evicted == "evicted"

    def test_to_wire_returns_value(self):
        assert UnsubscribeOutcome.Unsubscribed.to_wire() == "unsubscribed"
        assert UnsubscribeOutcome.NotSubscribed.to_wire() == "not_subscribed"
        assert UnsubscribeOutcome.Evicted.to_wire() == "evicted"

    def test_from_wire_accepts_known(self):
        assert (
            UnsubscribeOutcome.from_wire("unsubscribed")
            is UnsubscribeOutcome.Unsubscribed
        )
        assert (
            UnsubscribeOutcome.from_wire("not_subscribed")
            is UnsubscribeOutcome.NotSubscribed
        )
        assert UnsubscribeOutcome.from_wire("evicted") is UnsubscribeOutcome.Evicted

    def test_from_wire_rejects_unknown(self):
        with pytest.raises(ValueError):
            UnsubscribeOutcome.from_wire("gone")


class TestNotificationFilter:
    """``NotificationFilter`` — all-Optional, serde-default-on-every-field DTO."""

    def test_empty_instance_to_wire_omits_all(self):
        assert NotificationFilter().to_wire() == {}

    def test_tool_id_only(self):
        wire = NotificationFilter(tool_id=ToolId("fs:read")).to_wire()
        assert wire == {"tool_id": "fs:read"}

    def test_kinds_only(self):
        wire = NotificationFilter(kinds=["progress", "result"]).to_wire()
        assert wire == {"kinds": ["progress", "result"]}

    def test_both_fields(self):
        wire = NotificationFilter(
            tool_id=ToolId("fs:read"), kinds=["progress"]
        ).to_wire()
        assert wire == {"tool_id": "fs:read", "kinds": ["progress"]}

    def test_empty_kinds_whitelist_is_distinct_from_none(self):
        """``Some([])`` = accept-none, ``None`` = accept-all — distinct on the wire."""
        assert NotificationFilter(kinds=[]).to_wire() == {"kinds": []}
        assert NotificationFilter().to_wire() == {}

    def test_from_wire_missing_keys_default_none(self):
        flt = notification_filter_from_wire({})
        assert flt.tool_id is None
        assert flt.kinds is None

    def test_from_wire_present(self):
        flt = notification_filter_from_wire(
            {"tool_id": "fs:read", "kinds": ["progress", "result"]}
        )
        assert flt.tool_id == ToolId("fs:read")
        assert flt.kinds == ["progress", "result"]

    def test_round_trip(self):
        original = NotificationFilter(tool_id=ToolId("fs:read"), kinds=["progress"])
        assert notification_filter_from_wire(original.to_wire()) == original


class TestSubscribeNotificationsParams:
    """``SubscribeNotificationsParams`` — session_id payload + optional filter."""

    def test_to_wire_without_filter(self):
        params = SubscribeNotificationsParams(session_id=SessionId("s1"))
        assert params.to_wire() == {"session_id": "s1"}

    def test_to_wire_with_filter(self):
        params = SubscribeNotificationsParams(
            session_id=SessionId("s1"),
            filter=NotificationFilter(tool_id=ToolId("fs:read")),
        )
        assert params.to_wire() == {
            "session_id": "s1",
            "filter": {"tool_id": "fs:read"},
        }

    def test_from_wire_without_filter(self):
        params = subscribe_notifications_params_from_wire({"session_id": "s1"})
        assert params.session_id == SessionId("s1")
        assert params.filter is None

    def test_from_wire_with_filter(self):
        params = subscribe_notifications_params_from_wire(
            {"session_id": "s1", "filter": {"kinds": ["progress"]}}
        )
        assert params.session_id == SessionId("s1")
        assert params.filter is not None
        assert params.filter.kinds == ["progress"]

    def test_round_trip_without_filter(self):
        original = SubscribeNotificationsParams(session_id=SessionId("s1"))
        assert subscribe_notifications_params_from_wire(original.to_wire()) == original

    def test_round_trip_with_filter(self):
        original = SubscribeNotificationsParams(
            session_id=SessionId("s1"),
            filter=NotificationFilter(tool_id=ToolId("fs:read"), kinds=["progress"]),
        )
        assert subscribe_notifications_params_from_wire(original.to_wire()) == original


class TestSubscribeAck:
    """``SubscribeAck`` — outcome (strict) + subscription_id."""

    def test_to_wire(self):
        ack = SubscribeAck(
            outcome=SubscribeOutcome.Subscribed, subscription_id="default"
        )
        assert ack.to_wire() == {"outcome": "subscribed", "subscription_id": "default"}

    def test_from_wire(self):
        ack = subscribe_ack_from_wire(
            {"outcome": "already_subscribed", "subscription_id": "default"}
        )
        assert ack.outcome is SubscribeOutcome.AlreadySubscribed
        assert ack.subscription_id == "default"

    def test_round_trip(self):
        original = SubscribeAck(
            outcome=SubscribeOutcome.NotAuthorized, subscription_id="cx-9"
        )
        assert subscribe_ack_from_wire(original.to_wire()) == original

    def test_from_wire_rejects_unknown_outcome(self):
        with pytest.raises(ValueError):
            subscribe_ack_from_wire({"outcome": "pending", "subscription_id": "default"})


class TestUnsubscribeNotificationsParams:
    """``UnsubscribeNotificationsParams`` — session_id + subscription_id."""

    def test_to_wire(self):
        params = UnsubscribeNotificationsParams(
            session_id=SessionId("s1"), subscription_id="default"
        )
        assert params.to_wire() == {
            "session_id": "s1",
            "subscription_id": "default",
        }

    def test_from_wire(self):
        params = unsubscribe_notifications_params_from_wire(
            {"session_id": "s1", "subscription_id": "default"}
        )
        assert params.session_id == SessionId("s1")
        assert params.subscription_id == "default"

    def test_round_trip(self):
        original = UnsubscribeNotificationsParams(
            session_id=SessionId("s1"), subscription_id="cx-9"
        )
        assert (
            unsubscribe_notifications_params_from_wire(original.to_wire()) == original
        )


class TestUnsubscribeAck:
    """``UnsubscribeAck`` — outcome (strict) + subscription_id."""

    def test_to_wire(self):
        ack = UnsubscribeAck(
            outcome=UnsubscribeOutcome.Unsubscribed, subscription_id="default"
        )
        assert ack.to_wire() == {
            "outcome": "unsubscribed",
            "subscription_id": "default",
        }

    def test_from_wire(self):
        ack = unsubscribe_ack_from_wire(
            {"outcome": "evicted", "subscription_id": "default"}
        )
        assert ack.outcome is UnsubscribeOutcome.Evicted
        assert ack.subscription_id == "default"

    def test_round_trip(self):
        original = UnsubscribeAck(
            outcome=UnsubscribeOutcome.NotSubscribed, subscription_id="cx-9"
        )
        assert unsubscribe_ack_from_wire(original.to_wire()) == original

    def test_from_wire_rejects_unknown_outcome(self):
        with pytest.raises(ValueError):
            unsubscribe_ack_from_wire(
                {"outcome": "gone", "subscription_id": "default"}
            )


class TestSubscriptionsBarrelR97:
    """Subscriptions symbols travel the barrel; from_wire stay submodule-qualified."""

    def test_barrel_exports_subscription_symbols(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "NotificationFilter",
            "SubscribeNotificationsParams",
            "SubscribeOutcome",
            "SubscribeAck",
            "UnsubscribeNotificationsParams",
            "UnsubscribeOutcome",
            "UnsubscribeAck",
        ):
            assert hasattr(pkg, name), f"barrel missing subscription symbol {name}"

    def test_subscription_symbols_in_all(self):
        import minimax_code.tool_protocol as pkg

        for name in (
            "NotificationFilter",
            "SubscribeNotificationsParams",
            "SubscribeOutcome",
            "SubscribeAck",
            "UnsubscribeNotificationsParams",
            "UnsubscribeOutcome",
            "UnsubscribeAck",
        ):
            assert name in pkg.__all__, f"{name} not in barrel __all__"

    def test_frames_submodule_exposes_from_wire(self):
        import minimax_code.tool_protocol.frames as mod

        for name in (
            "notification_filter_from_wire",
            "subscribe_notifications_params_from_wire",
            "subscribe_ack_from_wire",
            "unsubscribe_notifications_params_from_wire",
            "unsubscribe_ack_from_wire",
        ):
            assert hasattr(mod, name), f"frames submodule missing {name}"

    def test_barrel_does_not_re_export_from_wire(self):
        """from_wire converters stay submodule-qualified, mirroring the crate."""
        import minimax_code.tool_protocol as pkg

        for name in (
            "notification_filter_from_wire",
            "subscribe_notifications_params_from_wire",
            "subscribe_ack_from_wire",
            "unsubscribe_notifications_params_from_wire",
            "unsubscribe_ack_from_wire",
        ):
            assert not hasattr(pkg, name), f"barrel should not export {name}"



class TestToolServerLifecycleStatus:
    """``ToolServerLifecycleStatus`` — strict snake_case StrEnum + ``#[default]`` on Ready."""

    def test_member_values_are_snake_case(self):
        assert ToolServerLifecycleStatus.Starting == "starting"
        assert ToolServerLifecycleStatus.Ready == "ready"
        assert ToolServerLifecycleStatus.Busy == "busy"
        assert ToolServerLifecycleStatus.Draining == "draining"
        assert ToolServerLifecycleStatus.ShuttingDown == "shutting_down"
        assert ToolServerLifecycleStatus.Disconnected == "disconnected"

    def test_to_wire_returns_value(self):
        assert ToolServerLifecycleStatus.Busy.to_wire() == "busy"
        assert ToolServerLifecycleStatus.ShuttingDown.to_wire() == "shutting_down"

    def test_from_wire_accepts_known(self):
        assert (
            ToolServerLifecycleStatus.from_wire("ready")
            is ToolServerLifecycleStatus.Ready
        )
        assert (
            ToolServerLifecycleStatus.from_wire("draining")
            is ToolServerLifecycleStatus.Draining
        )

    def test_from_wire_rejects_unknown(self):
        with pytest.raises(ValueError):
            ToolServerLifecycleStatus.from_wire("paused")

    def test_default_classmethod_returns_ready(self):
        """``#[derive(Default)]`` + ``#[default]`` on Ready, mirrored via ``default()``."""
        assert ToolServerLifecycleStatus.default() is ToolServerLifecycleStatus.Ready


class TestToolServerDisconnectReason:
    """``ToolServerDisconnectReason`` — strict snake_case StrEnum, no default arm."""

    def test_member_values_are_snake_case(self):
        assert ToolServerDisconnectReason.NormalClose == "normal_close"
        assert ToolServerDisconnectReason.IdleTimeout == "idle_timeout"
        assert ToolServerDisconnectReason.ForceEvicted == "force_evicted"
        assert ToolServerDisconnectReason.ConnectionLost == "connection_lost"

    def test_to_wire_returns_value(self):
        assert ToolServerDisconnectReason.IdleTimeout.to_wire() == "idle_timeout"
        assert ToolServerDisconnectReason.ForceEvicted.to_wire() == "force_evicted"

    def test_from_wire_accepts_known(self):
        assert (
            ToolServerDisconnectReason.from_wire("normal_close")
            is ToolServerDisconnectReason.NormalClose
        )
        assert (
            ToolServerDisconnectReason.from_wire("connection_lost")
            is ToolServerDisconnectReason.ConnectionLost
        )

    def test_from_wire_rejects_unknown(self):
        with pytest.raises(ValueError):
            ToolServerDisconnectReason.from_wire("kicked")


class TestToolServerStatusPayload:
    """``ToolServerStatusPayload`` — 20-field four-mode dataclass (crate's serde-densest)."""

    def _bare(self, **overrides):
        """Minimal payload with only the 7 required fields (rest at dataclass defaults)."""
        base = dict(
            status=ToolServerLifecycleStatus.Ready,
            active_tool_calls=0,
            background_tasks=0,
            pending_tool_calls=0,
            last_tool_call_started_ms=0,
            last_tool_call_completed_ms=0,
            uptime_ms=0,
        )
        base.update(overrides)
        return ToolServerStatusPayload(**base)

    def test_default_no_skip_fields_present_at_falsy_defaults(self):
        """default-no-skip fields are ALWAYS on wire, even at 0/False (forward-compat)."""
        wire = self._bare().to_wire()
        for key in (
            "upload_queue_pending",
            "upload_queue_pending_bytes",
            "upload_queue_inflight",
            "upload_queue_circuit_breaker_tripped",
            "artifact_producers_inflight",
            "turn_active",
            "idle_ignores_background",
        ):
            assert key in wire, f"default-no-skip {key} must serialize even at falsy default"
        assert wire["upload_queue_pending"] == 0
        assert wire["upload_queue_circuit_breaker_tripped"] is False

    def test_option_and_vec_skip_fields_omitted_at_defaults(self):
        wire = self._bare().to_wire()
        assert "session_id" not in wire
        assert "connection_id" not in wire
        assert "idle_since_ms" not in wire
        assert "drain_started_ms" not in wire
        assert "active_tool_names" not in wire
        assert "background_task_ids" not in wire

    def test_option_skip_fields_present_when_set(self):
        wire = self._bare(
            session_id=SessionId("s2"),
            connection_id="cx-1",
            idle_since_ms=500,
            drain_started_ms=600,
        ).to_wire()
        assert wire["session_id"] == "s2"
        assert wire["connection_id"] == "cx-1"
        assert wire["idle_since_ms"] == 500
        assert wire["drain_started_ms"] == 600

    def test_vec_skip_fields_present_when_non_empty(self):
        wire = self._bare(
            active_tool_names=["fs:read", "fs:write"],
            background_task_ids=["bg-1"],
        ).to_wire()
        assert wire["active_tool_names"] == ["fs:read", "fs:write"]
        assert wire["background_task_ids"] == ["bg-1"]

    def test_required_field_status_serialises_as_snake_case_string(self):
        wire = self._bare(status=ToolServerLifecycleStatus.Busy).to_wire()
        assert wire["status"] == "busy"

    def test_from_wire_missing_required_raises_keyerror(self):
        """Required fields use ``data[...]``; missing ⇒ KeyError (serde fail-on-missing)."""
        with pytest.raises(KeyError):
            tool_server_status_payload_from_wire({})

    def test_from_wire_defaults_fill_missing_optional_vec_defaultnoskip(self):
        payload = tool_server_status_payload_from_wire(
            {
                "status": "ready",
                "active_tool_calls": 1,
                "background_tasks": 2,
                "pending_tool_calls": 3,
                "last_tool_call_started_ms": 10,
                "last_tool_call_completed_ms": 20,
                "uptime_ms": 30,
            }
        )
        assert payload.status is ToolServerLifecycleStatus.Ready
        assert payload.session_id is None
        assert payload.connection_id is None
        assert payload.active_tool_names == []
        assert payload.background_task_ids == []
        assert payload.upload_queue_pending == 0
        assert payload.turn_active is False

    def test_full_round_trip(self):
        original = ToolServerStatusPayload(
            status=ToolServerLifecycleStatus.Busy,
            active_tool_calls=3,
            background_tasks=1,
            pending_tool_calls=2,
            last_tool_call_started_ms=1000,
            last_tool_call_completed_ms=2000,
            uptime_ms=5000,
            session_id=SessionId("s1"),
            connection_id="cx-9",
            active_tool_names=["fs:read", "fs:write"],
            background_task_ids=["bg-1"],
            upload_queue_pending=4,
            upload_queue_pending_bytes=2048,
            upload_queue_inflight=1,
            upload_queue_circuit_breaker_tripped=True,
            artifact_producers_inflight=2,
            turn_active=True,
            idle_ignores_background=True,
        )
        assert tool_server_status_payload_from_wire(original.to_wire()) == original

    def test_terminal_classmethod_zeroes_everything_but_status(self):
        payload = ToolServerStatusPayload.terminal(
            ToolServerLifecycleStatus.Disconnected
        )
        assert payload.status is ToolServerLifecycleStatus.Disconnected
        assert payload.active_tool_calls == 0
        assert payload.uptime_ms == 0
        assert payload.upload_queue_pending == 0
        assert payload.turn_active is False


class TestToolServerEvictParams:
    """``ToolServerEvictParams`` — all three fields required (session_id + reason + grace)."""

    def test_to_wire(self):
        params = ToolServerEvictParams(
            session_id=SessionId("s1"), reason="idle", grace_period_ms=5000
        )
        assert params.to_wire() == {
            "session_id": "s1",
            "reason": "idle",
            "grace_period_ms": 5000,
        }

    def test_from_wire(self):
        params = tool_server_evict_params_from_wire(
            {"session_id": "s1", "reason": "force", "grace_period_ms": 0}
        )
        assert params.session_id == SessionId("s1")
        assert params.reason == "force"
        assert params.grace_period_ms == 0

    def test_round_trip(self):
        original = ToolServerEvictParams(
            session_id=SessionId("s1"), reason="drain", grace_period_ms=250
        )
        assert tool_server_evict_params_from_wire(original.to_wire()) == original

    def test_from_wire_missing_required_raises_keyerror(self):
        with pytest.raises(KeyError):
            tool_server_evict_params_from_wire({"session_id": "s1"})


class TestToolServerGetStatusParams:
    """``ToolServerGetStatusParams`` — single-field session scope."""

    def test_to_wire(self):
        assert ToolServerGetStatusParams(session_id=SessionId("s1")).to_wire() == {
            "session_id": "s1"
        }

    def test_round_trip(self):
        original = ToolServerGetStatusParams(session_id=SessionId("s7"))
        assert tool_server_get_status_params_from_wire(original.to_wire()) == original


class TestToolServerConnectionStatus:
    """``ToolServerConnectionStatus`` — embeds :class:`ToolServerStatusPayload` (nested DTO)."""

    def test_to_wire_nests_payload(self):
        status = ToolServerStatusPayload(
            status=ToolServerLifecycleStatus.Busy,
            active_tool_calls=2,
            background_tasks=0,
            pending_tool_calls=1,
            last_tool_call_started_ms=10,
            last_tool_call_completed_ms=20,
            uptime_ms=100,
        )
        conn = ToolServerConnectionStatus(connection_id="cx-1", status=status)
        assert conn.to_wire() == {
            "connection_id": "cx-1",
            "status": status.to_wire(),
        }

    def test_round_trip_lifts_nested_payload(self):
        status = ToolServerStatusPayload(
            status=ToolServerLifecycleStatus.Draining,
            active_tool_calls=1,
            background_tasks=0,
            pending_tool_calls=0,
            last_tool_call_started_ms=5,
            last_tool_call_completed_ms=6,
            uptime_ms=7,
            connection_id="cx-2",
        )
        original = ToolServerConnectionStatus(connection_id="cx-2", status=status)
        lifted = tool_server_connection_status_from_wire(original.to_wire())
        assert lifted == original
        assert lifted.status.status is ToolServerLifecycleStatus.Draining

    def test_from_wire_rejects_bad_lifecycle_via_nested_payload(self):
        with pytest.raises(ValueError):
            tool_server_connection_status_from_wire(
                {
                    "connection_id": "cx-3",
                    "status": {
                        "status": "paused",
                        "active_tool_calls": 0,
                        "background_tasks": 0,
                        "pending_tool_calls": 0,
                        "last_tool_call_started_ms": 0,
                        "last_tool_call_completed_ms": 0,
                        "uptime_ms": 0,
                    },
                }
            )


class TestToolServerGetStatusResult:
    """``ToolServerGetStatusResult`` — list-of-DTO (Vec<ToolServerConnectionStatus>)."""

    def _conn(self, cid):
        return ToolServerConnectionStatus(
            connection_id=cid,
            status=ToolServerStatusPayload(
                status=ToolServerLifecycleStatus.Ready,
                active_tool_calls=0,
                background_tasks=0,
                pending_tool_calls=0,
                last_tool_call_started_ms=0,
                last_tool_call_completed_ms=0,
                uptime_ms=0,
            ),
        )

    def test_to_wire_empty_list(self):
        assert ToolServerGetStatusResult(tool_servers=[]).to_wire() == {
            "tool_servers": []
        }

    def test_round_trip_multiple_entries(self):
        original = ToolServerGetStatusResult(
            tool_servers=[self._conn("cx-1"), self._conn("cx-2")]
        )
        lifted = tool_server_get_status_result_from_wire(original.to_wire())
        assert lifted == original
        assert len(lifted.tool_servers) == 2
        assert lifted.tool_servers[0].connection_id == "cx-1"


class TestToolServerStatusLifecycleBarrelR98:
    """Tool-server status lifecycle symbols travel the barrel; from_wire stay submodule-qualified."""

    _types = (
        "ToolServerLifecycleStatus",
        "ToolServerDisconnectReason",
        "ToolServerStatusPayload",
        "ToolServerEvictParams",
        "ToolServerGetStatusParams",
        "ToolServerConnectionStatus",
        "ToolServerGetStatusResult",
    )
    _converters = (
        "tool_server_status_payload_from_wire",
        "tool_server_evict_params_from_wire",
        "tool_server_get_status_params_from_wire",
        "tool_server_connection_status_from_wire",
        "tool_server_get_status_result_from_wire",
    )

    def test_barrel_exports_tool_server_symbols(self):
        import minimax_code.tool_protocol as pkg

        for name in self._types:
            assert hasattr(pkg, name), f"barrel missing {name}"

    def test_tool_server_symbols_in_all(self):
        import minimax_code.tool_protocol as pkg

        for name in self._types:
            assert name in pkg.__all__, f"{name} not in barrel __all__"

    def test_frames_submodule_exposes_from_wire(self):
        import minimax_code.tool_protocol.frames as mod

        for name in self._converters:
            assert hasattr(mod, name), f"frames submodule missing {name}"

    def test_barrel_does_not_re_export_from_wire(self):
        import minimax_code.tool_protocol as pkg

        for name in self._converters:
            assert not hasattr(pkg, name), f"barrel should not export {name}"
