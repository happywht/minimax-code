"""Tests for the workspace RPC foundation layer (R68).

Covers the externally-tagged :class:`RpcEnvelope` wire shape, the
:class:`WorkspaceRpc` protocol contract (METHOD + Response ClassVars),
the session prompt-tracking RPCs, and the agents_md discovery RPC — all
pinned to the grok source's own ``#[cfg(test)]`` assertions as the wire
contract.
"""

from __future__ import annotations

import pytest

from minimax_code.workspace_types.rpc import (
    TURN_ACTIVE,
    WORKSPACE_CLIENT_EXT_NOTIFICATIONS_TOOL_ID,
    WORKSPACE_EVENTS_TOOL_ID,
    WORKSPACE_RPC_TOOL_ID,
    WORKSPACE_TOOL_NOTIFICATIONS_TOOL_ID,
    AgentConfigFile,
    BeginPromptReq,
    ConflictType,
    DiscoverAgentsMdReq,
    EndPromptReq,
    FileRewindConflict,
    FileRewindResponse,
    RewindToReq,
    RpcEnvelope,
    RpcError,
    WorkspaceRpc,
)

# -- tool IDs (mod.rs) ----------------------------------------------------


class TestToolIds:
    def test_rpc_tool_id(self) -> None:
        assert WORKSPACE_RPC_TOOL_ID == "workspace_rpc"

    def test_events_tool_id(self) -> None:
        assert WORKSPACE_EVENTS_TOOL_ID == "workspace_events"

    def test_tool_notifications_tool_id(self) -> None:
        assert WORKSPACE_TOOL_NOTIFICATIONS_TOOL_ID == "workspace_tool_notifications"

    def test_client_ext_notifications_tool_id(self) -> None:
        assert WORKSPACE_CLIENT_EXT_NOTIFICATIONS_TOOL_ID == "workspace_client_ext_notifications"


# -- RpcError -------------------------------------------------------------


class TestRpcError:
    def test_turn_active_constant(self) -> None:
        assert TURN_ACTIVE == "turn_active"

    def test_is_turn_active_true(self) -> None:
        assert RpcError(code=TURN_ACTIVE, message="busy").is_turn_active()

    def test_is_turn_active_false(self) -> None:
        assert not RpcError(code="hub_error", message="boom").is_turn_active()

    def test_str_format(self) -> None:
        # Mirrors Rust's `write!(f, "[{}] {}", code, message)` Display.
        assert str(RpcError(code="session_not_found", message="ghost")) == (
            "[session_not_found] ghost"
        )


# -- RpcEnvelope wire shape (externally-tagged) ---------------------------


class TestEnvelopeOkWire:
    def test_ok_wire_shape(self) -> None:
        # {"ok": "hello"} — mirrors source `ok_wire_shape` test.
        assert RpcEnvelope.ok("hello").to_wire() == {"ok": "hello"}

    def test_ok_is_ok(self) -> None:
        env = RpcEnvelope.ok("hello")
        assert env.is_ok() and not env.is_err()

    def test_ok_into_result(self) -> None:
        ok, err = RpcEnvelope.ok("hello").into_result()
        assert ok == "hello" and err is None

    def test_ok_struct_payload_sorted(self) -> None:
        resp = FileRewindResponse(
            success=True,
            target_prompt_index=2,
            reverted_files=["a.py"],
            clean_files=["b.py"],
            conflicts=[],
        )
        wire = RpcEnvelope.ok(resp).to_wire()
        assert "ok" in wire
        # inner mapping keys are BTreeMap-sorted (clean_files < conflicts < …)
        inner = wire["ok"]
        assert list(inner.keys()) == sorted(inner.keys())
        assert inner["success"] is True

    def test_ok_list_of_models(self) -> None:
        files = [
            AgentConfigFile(file_name="AGENTS.md", file_path="/r/AGENTS.md", content="x"),
            AgentConfigFile(file_name="Claude.md", file_path="/r/Claude.md", content="y"),
        ]
        wire = RpcEnvelope.ok(files).to_wire()
        assert wire == {
            "ok": [
                {"content": "x", "file_name": "AGENTS.md", "file_path": "/r/AGENTS.md"},
                {"content": "y", "file_name": "Claude.md", "file_path": "/r/Claude.md"},
            ]
        }

    def test_ok_unit_payload(self) -> None:
        # Rust `Response = ()` serialises the Ok arm with a null payload.
        assert RpcEnvelope.ok(None).to_wire() == {"ok": None}


class TestEnvelopeErrWire:
    def test_err_wire_shape(self) -> None:
        # {"err": {"code": ..., "message": ...}} — mirrors `err_wire_shape`.
        assert RpcEnvelope.err_parts("session_not_found", "ghost").to_wire() == {
            "err": {"code": "session_not_found", "message": "ghost"}
        }

    def test_err_is_err(self) -> None:
        env = RpcEnvelope.err_parts("hub_error", "boom")
        assert env.is_err() and not env.is_ok()

    def test_err_into_result(self) -> None:
        ok, err = RpcEnvelope.err_parts("hub_error", "boom").into_result()
        assert ok is None and err is not None
        assert err.code == "hub_error" and err.message == "boom"

    def test_err_from_existing_rpc_error(self) -> None:
        error = RpcError(code="turn_active", message="busy")
        env = RpcEnvelope[RpcError].err(error)
        assert env.to_wire() == {"err": {"code": "turn_active", "message": "busy"}}


# -- RpcEnvelope from_wire (parse side) -----------------------------------


class TestEnvelopeFromWire:
    def test_from_wire_ok_str(self) -> None:
        env = RpcEnvelope.from_wire({"ok": "hello"}, str)
        ok, err = env.into_result()
        assert ok == "hello" and err is None

    def test_from_wire_ok_struct(self) -> None:
        wire = RpcEnvelope.ok(
            FileRewindResponse(
                success=True, target_prompt_index=1, reverted_files=[], clean_files=[], conflicts=[]
            )
        ).to_wire()
        env = RpcEnvelope.from_wire(wire, FileRewindResponse)
        ok, err = env.into_result()
        assert err is None
        assert isinstance(ok, FileRewindResponse)
        assert ok.success is True

    def test_from_wire_ok_list(self) -> None:
        raw = {
            "ok": [
                {"file_name": "AGENTS.md", "file_path": "/r/AGENTS.md", "content": "x"},
            ]
        }
        env = RpcEnvelope.from_wire(raw, list[AgentConfigFile])
        ok, err = env.into_result()
        assert err is None
        assert isinstance(ok, list) and len(ok) == 1
        assert isinstance(ok[0], AgentConfigFile)
        assert ok[0].file_name == "AGENTS.md"

    def test_from_wire_unit_response(self) -> None:
        # `Response = ()` → response_type is NoneType, ok payload is null.
        env = RpcEnvelope.from_wire({"ok": None}, type(None))
        ok, err = env.into_result()
        assert ok is None and err is None

    def test_from_wire_err(self) -> None:
        env = RpcEnvelope.from_wire(
            {"err": {"code": "hub_error", "message": "boom"}}, str
        )
        ok, err = env.into_result()
        assert ok is None and err is not None
        assert err.code == "hub_error" and err.message == "boom"

    def test_from_wire_rejects_missing_keys(self) -> None:
        with pytest.raises(ValueError, match="neither 'ok' nor 'err'"):
            RpcEnvelope.from_wire({"unexpected": 1}, str)


# -- RpcEnvelope round trip ----------------------------------------------


class TestEnvelopeRoundTrip:
    def test_round_trip_ok_struct(self) -> None:
        resp = FileRewindResponse(
            success=False,
            target_prompt_index=3,
            reverted_files=["a.py", "b.py"],
            clean_files=["c.py"],
            conflicts=[FileRewindConflict(path="d.py", conflict_type=ConflictType.DELETED_EXTERNALLY)],
            error="partial",
        )
        wire = RpcEnvelope.ok(resp).to_wire()
        recovered = RpcEnvelope.from_wire(wire, FileRewindResponse)
        ok, _ = recovered.into_result()
        assert isinstance(ok, FileRewindResponse)
        assert ok.success is False
        assert ok.target_prompt_index == 3
        assert ok.reverted_files == ["a.py", "b.py"]
        assert ok.conflicts[0].conflict_type is ConflictType.DELETED_EXTERNALLY

    def test_round_trip_err(self) -> None:
        wire = RpcEnvelope.err_parts("hub_error", "boom").to_wire()
        recovered = RpcEnvelope.from_wire(wire, str)
        _, err = recovered.into_result()
        assert err is not None
        assert err.code == "hub_error" and err.message == "boom"


# -- WorkspaceRpc protocol contract --------------------------------------


class TestWorkspaceRpcProtocol:
    def test_all_requests_satisfy_protocol(self) -> None:
        # WorkspaceRpc's METHOD is a non-method (ClassVar) member. Python's
        # typing forbids issubclass() for protocols with non-method members,
        # and the request structs have required fields (no default instance),
        # so isinstance() can't run either. Verify the contract directly:
        # each request declares a ``workspace.*`` METHOD ClassVar.
        for req in (BeginPromptReq, EndPromptReq, RewindToReq, DiscoverAgentsMdReq):
            assert hasattr(req, "METHOD"), f"{req.__name__} must declare METHOD"
            assert isinstance(req.METHOD, str)
            assert req.METHOD.startswith("workspace."), req.METHOD

    def test_protocol_requires_method(self) -> None:
        # isinstance() on a runtime_checkable Protocol still works (it checks
        # attribute presence on the instance); a class without METHOD fails it.
        class NoMethod:
            pass

        assert not isinstance(NoMethod(), WorkspaceRpc)


# -- session RPCs --------------------------------------------------------


class TestSessionMethods:
    def test_begin_prompt_method_constant(self) -> None:
        assert BeginPromptReq.METHOD == "workspace.begin_prompt"

    def test_end_prompt_method_constant(self) -> None:
        assert EndPromptReq.METHOD == "workspace.end_prompt"

    def test_rewind_to_method_constant(self) -> None:
        assert RewindToReq.METHOD == "workspace.rewind_to"

    def test_begin_prompt_response_is_unit(self) -> None:
        assert BeginPromptReq.Response is type(None)

    def test_end_prompt_response_is_unit(self) -> None:
        assert EndPromptReq.Response is type(None)

    def test_rewind_to_response_is_file_rewind_response(self) -> None:
        assert RewindToReq.Response is FileRewindResponse

    def test_begin_prompt_to_wire(self) -> None:
        wire = BeginPromptReq(session_id="s1", prompt_index=4).to_wire()
        assert wire == {"prompt_index": 4, "session_id": "s1"}

    def test_rewind_to_to_wire(self) -> None:
        wire = RewindToReq(session_id="s1", target_prompt_index=2).to_wire()
        assert wire == {"session_id": "s1", "target_prompt_index": 2}


class TestConflictType:
    def test_snake_case_values(self) -> None:
        assert ConflictType.DELETED_EXTERNALLY == "deleted_externally"
        assert ConflictType.CREATED_EXTERNALLY == "created_externally"
        assert ConflictType.MODIFIED_EXTERNALLY == "modified_externally"

    def test_wire_serializes_as_value(self) -> None:
        # StrEnum → bare value string on the wire.
        wire = FileRewindConflict(
            path="x.py", conflict_type=ConflictType.CREATED_EXTERNALLY
        ).to_wire()
        assert wire["conflict_type"] == "created_externally"

    def test_wire_parses_value_back(self) -> None:
        c = FileRewindConflict.model_validate(
            {"path": "y.py", "conflict_type": "modified_externally"}
        )
        assert c.conflict_type is ConflictType.MODIFIED_EXTERNALLY


class TestFileRewindResponse:
    def _full(self) -> FileRewindResponse:
        return FileRewindResponse(
            success=True,
            target_prompt_index=1,
            reverted_files=["a.py"],
            clean_files=["b.py"],
            conflicts=[FileRewindConflict(path="c.py", conflict_type=ConflictType.DELETED_EXTERNALLY)],
            error=None,
        )

    def test_round_trip_full(self) -> None:
        resp = self._full()
        recovered = FileRewindResponse.model_validate(resp.to_wire())
        assert recovered.success is True
        assert recovered.target_prompt_index == 1
        assert recovered.reverted_files == ["a.py"]
        assert recovered.clean_files == ["b.py"]
        assert recovered.conflicts[0].path == "c.py"
        assert recovered.error is None

    def test_error_defaults_none(self) -> None:
        # Option<String> — a missing `error` key deserialises to None.
        c = FileRewindResponse.model_validate(
            {
                "success": True,
                "target_prompt_index": 0,
                "reverted_files": [],
                "clean_files": [],
                "conflicts": [],
            }
        )
        assert c.error is None

    def test_vec_fields_required(self) -> None:
        # Rust has no #[serde(default)] on the Vec fields → they are required.
        with pytest.raises(Exception):  # noqa: B017 — any validation error
            FileRewindResponse.model_validate(
                {"success": True, "target_prompt_index": 0}
            )


# -- agents_md RPC -------------------------------------------------------


class TestAgentsMd:
    def test_method_constant(self) -> None:
        assert DiscoverAgentsMdReq.METHOD == "workspace.discover_agents_md"

    def test_response_is_list_of_agent_config_file(self) -> None:
        # list[AgentConfigFile] generic alias — compare by value, not identity
        # (GenericAlias objects are not interned: ``list[X] is list[X]`` → False).
        assert DiscoverAgentsMdReq.Response == list[AgentConfigFile]

    def test_empty_body_to_wire(self) -> None:
        # Empty request struct → empty wire object.
        assert DiscoverAgentsMdReq().to_wire() == {}

    def test_empty_request_has_default(self) -> None:
        # Rust derives Default on the empty struct.
        assert DiscoverAgentsMdReq.default().to_wire() == {}

    def test_agent_config_file_round_trip(self) -> None:
        raw = {
            "file_name": "AGENTS.md",
            "file_path": "/repo/AGENTS.md",
            "content": "# Instructions\n",
        }
        f = AgentConfigFile.model_validate(raw)
        assert f.file_name == "AGENTS.md"
        assert f.file_path == "/repo/AGENTS.md"
        assert f.to_wire() == {
            "content": "# Instructions\n",
            "file_name": "AGENTS.md",
            "file_path": "/repo/AGENTS.md",
        }

    def test_agent_config_file_ignores_unknown_fields(self) -> None:
        # Forward-compat: a future field does not break parsing.
        raw = {
            "file_name": "Claude.md",
            "file_path": "/repo/Claude.md",
            "content": "x",
            "brand_new_field": {"nested": True},
        }
        f = AgentConfigFile.model_validate(raw)
        assert f.file_name == "Claude.md"
