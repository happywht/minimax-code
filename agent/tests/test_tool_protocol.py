"""Tests for ``minimax_code.tool_protocol`` (R82 foundation + R83 wire enums).

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
"""

from __future__ import annotations

import json

import pytest

from minimax_code.tool_protocol import (
    ERROR_CODES,
    KNOWN_NOTIFICATION_KINDS,
    PROTOCOL_VERSION,
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
    IdError,
    ImageBlock,
    Internal,
    InvalidArguments,
    InvalidFormatIdError,
    Json,
    KnownVariantCollision,
    Mcp,
    PayloadTooLarge,
    PermissionDenied,
    RenderLimited,
    RequestId,
    ReservedPrefixIdError,
    ResourceBlock,
    ServerId,
    SessionId,
    SessionMismatch,
    TerminalError,
    Text,
    TextBlock,
    Timeout,
    ToolCallId,
    ToolDefinitionMode,
    ToolId,
    ToolNotFound,
    TransportClosed,
    UnsupportedProtocolVersion,
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
from minimax_code.tool_protocol.error_wire import from_wire as error_from_wire
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
