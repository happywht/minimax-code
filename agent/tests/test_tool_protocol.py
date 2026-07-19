"""Tests for ``minimax_code.tool_protocol`` (R82 — foundation layer).

Covers the four foundation modules landed in R82:

* :mod:`ids` — the 7 ``opaque_id!`` string newtypes + ``FrameSeq`` u64
  newtype + the ``IdError`` hierarchy, with constructor validation
  (empty / invalid format / reserved prefix) and transparent serialisation.
* :mod:`connection` — ``ConnectionKind`` (StrEnum) + ``ToolDefinitionMode``
  (internally-tagged enum, the crate's first).
* :mod:`handshake` — ``PROTOCOL_VERSION`` + ``HelloMsg`` / ``HelloAckMsg``
  with the ``Option::is_none`` / ``Vec::is_empty`` skip semantics.
* :mod:`error_codes` — the ``ERROR_CODES`` table + lookup helpers +
  workspace-unavailable contract + the two ``#[serde(other)]``-tolerant
  enums and their ``WorkspaceUnavailableDetails`` payload.
"""

from __future__ import annotations

import json

import pytest

from minimax_code.tool_protocol import (
    ERROR_CODES,
    PROTOCOL_VERSION,
    WORKSPACE_UNAVAILABLE_JSONRPC_CODE,
    WORKSPACE_UNAVAILABLE_MESSAGE,
    WORKSPACE_UNAVAILABLE_SUBCODE,
    ConnectionId,
    ConnectionKind,
    EmptyIdError,
    FrameSeq,
    HelloAckMsg,
    HelloMsg,
    IdError,
    InvalidFormatIdError,
    RequestId,
    ReservedPrefixIdError,
    ServerId,
    SessionId,
    ToolCallId,
    ToolDefinitionMode,
    ToolId,
    UserId,
    WorkspaceGonePhase,
    WorkspaceGoneReason,
    WorkspaceUnavailableDetails,
    numeric_for,
    string_for,
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
