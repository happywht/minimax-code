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

import json

import pytest

from minimax_code.tool_protocol import (
    ERROR_CODES,
    KNOWN_NOTIFICATION_KINDS,
    PROTOCOL_VERSION,
    UNKNOWN_METHOD_MSG_PREFIX,
    WORKSPACE_UNAVAILABLE_JSONRPC_CODE,
    WORKSPACE_UNAVAILABLE_MESSAGE,
    WORKSPACE_UNAVAILABLE_SUBCODE,
    BehaviorVersionUnsupported,
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
    Mcp,
    Method,
    NotificationSchemas,
    PayloadTooLarge,
    PermissionDenied,
    Registered,
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
    TerminalError,
    Text,
    TextBlock,
    Timeout,
    ToolCallId,
    ToolCapabilities,
    ToolDefinitionMode,
    ToolDescriptionWithSchema,
    ToolId,
    ToolNotFound,
    ToolRegistration,
    ToolScope,
    ToolServerRegistration,
    TransportClosed,
    TransportKind,
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
