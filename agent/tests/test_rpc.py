"""Tests for the workspace RPC foundation layer (R68).

Covers the externally-tagged :class:`RpcEnvelope` wire shape, the
:class:`WorkspaceRpc` protocol contract (METHOD + Response ClassVars),
the session prompt-tracking RPCs, and the agents_md discovery RPC — all
pinned to the grok source's own ``#[cfg(test)]`` assertions as the wire
contract.
"""

from __future__ import annotations

from typing import Any

import pytest

from minimax_code.workspace_types.rpc import (
    TURN_ACTIVE,
    WORKSPACE_CLIENT_EXT_NOTIFICATIONS_TOOL_ID,
    WORKSPACE_EVENTS_TOOL_ID,
    WORKSPACE_RPC_TOOL_ID,
    WORKSPACE_TOOL_NOTIFICATIONS_TOOL_ID,
    AgentConfigFile,
    BackgroundTaskSummaryWire,
    BeginPromptReq,
    ClientId,
    CodeFindDefinitionsReq,
    CodeFindReferencesReq,
    CodeGotoDefinitionReq,
    CodeGotoReferencesReq,
    CodeIndexStats,
    CodeIndexStatusReq,
    CodeIndexStatusResponse,
    CodeNavLocation,
    CodeNavResponse,
    ConfigureMcpReq,
    ConflictType,
    ContentMatch,
    ContentMatchFile,
    ContentSearchData,
    ContentSearchRequest,
    DeployError,
    DiscoverAgentsMdReq,
    DropSessionReq,
    EndPromptReq,
    FileRewindConflict,
    FileRewindResponse,
    FuzzyChangeReq,
    FuzzyCloseReq,
    FuzzyOpenReq,
    FuzzyStatusReq,
    HookEventNameWire,
    HookRegistryReq,
    HookRegistryWire,
    HookSpecWire,
    InstallPluginReq,
    ListBackgroundTasksReq,
    ListBackgroundTasksResponse,
    ListTodosReq,
    ListTodosResponse,
    LoadEnvrcReq,
    LoadPermissionsReq,
    LoadProjectConfigReq,
    RefreshPluginsReq,
    ResolveFileReferencesReq,
    RewindToReq,
    RpcEnvelope,
    RpcError,
    TargetClientId,
    TodoSummaryWire,
    ToolDefinitionsReq,
    UpdateToolConfigReq,
    WorkspaceInfo,
    WorkspaceInfoReq,
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


# -- code_nav RPCs (R69, Ok side of the envelope) ------------------------


class TestCodeNav:
    def test_method_constants(self) -> None:
        assert CodeGotoDefinitionReq.METHOD == "workspace.code_goto_definition"
        assert CodeGotoReferencesReq.METHOD == "workspace.code_goto_references"
        assert CodeFindDefinitionsReq.METHOD == "workspace.code_find_definitions"
        assert CodeFindReferencesReq.METHOD == "workspace.code_find_references"
        assert CodeIndexStatusReq.METHOD == "workspace.code_index_status"

    def test_nav_response_types(self) -> None:
        # The four navigation methods share CodeNavResponse; index status is distinct.
        for req in (CodeGotoDefinitionReq, CodeGotoReferencesReq,
                    CodeFindDefinitionsReq, CodeFindReferencesReq):
            assert req.Response is CodeNavResponse
        assert CodeIndexStatusReq.Response is CodeIndexStatusResponse

    def test_goto_definition_to_wire(self) -> None:
        # `root` is #[serde(default)] Option<PathBuf> → defaults to None on the wire.
        wire = CodeGotoDefinitionReq(file="src/lib.rs", line=10, col=3).to_wire()
        assert wire == {"col": 3, "file": "src/lib.rs", "line": 10, "root": None}

    def test_goto_definition_to_wire_with_root(self) -> None:
        wire = CodeGotoDefinitionReq(root="/repo", file="a.rs", line=1, col=1).to_wire()
        assert wire["root"] == "/repo"

    def test_goto_references_include_definition_defaults_false(self) -> None:
        wire = CodeGotoReferencesReq(file="a.rs", line=1, col=1).to_wire()
        assert wire["include_definition"] is False

    def test_find_definitions_context_file_optional(self) -> None:
        wire = CodeFindDefinitionsReq(symbol="Foo").to_wire()
        assert wire["context_file"] is None
        assert wire["symbol"] == "Foo"

    def test_nav_location_omits_symbol_when_none(self) -> None:
        # `#[serde(skip_serializing_if = "Option::is_none")]` — the `symbol`
        # key is absent when None (not emitted as null).
        wire = CodeNavLocation(path="src/lib.rs", line=5).to_wire()
        assert wire == {"line": 5, "path": "src/lib.rs"}
        assert "symbol" not in wire

    def test_nav_location_includes_symbol_when_set(self) -> None:
        wire = CodeNavLocation(path="src/lib.rs", line=5, symbol="foo").to_wire()
        assert wire == {"line": 5, "path": "src/lib.rs", "symbol": "foo"}

    def test_code_nav_response_with_locations(self) -> None:
        resp = CodeNavResponse(
            locations=[
                CodeNavLocation(path="a.rs", line=1),
                CodeNavLocation(path="b.rs", line=2, symbol="bar"),
            ]
        )
        recovered = CodeNavResponse.model_validate(resp.to_wire())
        assert len(recovered.locations) == 2
        assert recovered.locations[0].symbol is None
        assert recovered.locations[1].symbol == "bar"

    def test_index_status_response_optional_defaults_none(self) -> None:
        c = CodeIndexStatusResponse.model_validate({"active": True})
        assert c.active is True
        assert c.file_count is None
        assert c.stats is None

    def test_index_status_response_with_stats(self) -> None:
        wire = CodeIndexStatusResponse(
            active=True, file_count=42,
            stats=CodeIndexStats(files=42, definitions=100, references=500),
        ).to_wire()
        assert wire["stats"] == {"definitions": 100, "files": 42, "references": 500}

    def test_index_status_req_has_default(self) -> None:
        # `#[derive(Default)]` on the root-only request — default() succeeds.
        assert CodeIndexStatusReq.default().to_wire() == {"root": None}

    def test_index_stats_required(self) -> None:
        with pytest.raises(Exception):  # noqa: B017 — any validation error
            CodeIndexStats.model_validate({"files": 1})

    def test_envelope_ok_wraps_code_nav_response(self) -> None:
        # Full Ok-side consumption: a code_nav response rides the envelope.
        resp = CodeNavResponse(locations=[CodeNavLocation(path="a.rs", line=1)])
        wire = RpcEnvelope.ok(resp).to_wire()
        assert "ok" in wire
        recovered = RpcEnvelope.from_wire(wire, CodeNavResponse)
        ok, err = recovered.into_result()
        assert err is None
        assert isinstance(ok, CodeNavResponse)
        assert ok.locations[0].path == "a.rs"


# -- deploy error vocabulary (R69, Err side of the envelope) --------------


class TestDeployError:
    def test_wire_code_is_value(self) -> None:
        # wire_code() returns the enum value (the wire discriminant).
        assert DeployError.URL_CONFLICT.wire_code() == "deploy_url_conflict"
        assert DeployError.NOT_FOUND.wire_code() == DeployError.NOT_FOUND.value

    def test_some_wire_codes(self) -> None:
        # Spot-check the explicit member-name → wire-code mapping.
        assert DeployError.PERMISSION_DENIED.value == "deploy_permission_denied"
        assert DeployError.DEPLOYMENT_NOT_IN_BUILDING_STATE.value == "deploy_not_in_building_state"
        assert DeployError.UNSUPPORTED_PROJECT_TYPE.value == "deploy_unsupported_project_type"
        assert DeployError.FAILED_PRECONDITION.value == "deploy_failed_precondition"

    def test_from_wire_code_round_trip(self) -> None:
        # Mirrors the source `deploy_error_kind_wire_code_round_trips` test.
        for kind in DeployError.ALL:
            assert DeployError.from_wire_code(kind.wire_code()) is kind

    def test_from_wire_code_unknown_returns_none(self) -> None:
        # An unrelated RpcError.code is not a deploy error code.
        assert DeployError.from_wire_code("hub_error") is None

    def test_from_wire_code_empty_returns_none(self) -> None:
        assert DeployError.from_wire_code("") is None

    def test_all_has_fifteen_kinds(self) -> None:
        assert len(DeployError.ALL) == 15
        # ALL is exhaustive — every member appears exactly once.
        assert set(DeployError.ALL) == set(DeployError)

    def test_is_plain_enum_not_strenum(self) -> None:
        # The member name and wire code are an explicit mapping, not
        # name-derived, so DeployError is a plain Enum whose value is the
        # wire code — NOT a StrEnum (a StrEnum member is a str subclass).
        kind = DeployError.URL_CONFLICT
        assert type(kind) is DeployError
        assert not isinstance(kind, str)
        assert kind.value == "deploy_url_conflict"

    def test_envelope_err_carries_deploy_code(self) -> None:
        # Full Err-side consumption chain: DeployError.wire_code() feeds the
        # envelope's err arm, round-trips through RpcError, and parses back.
        code = DeployError.PERMISSION_DENIED.wire_code()
        env = RpcEnvelope.err_parts(code, "not allowed")
        wire = env.to_wire()
        assert wire == {"err": {"code": "deploy_permission_denied", "message": "not allowed"}}
        recovered = RpcEnvelope.from_wire(wire, type(None))
        _, err = recovered.into_result()
        assert err is not None
        assert DeployError.from_wire_code(err.code) is DeployError.PERMISSION_DENIED


# -- search RPCs (R70, mixed camelCase/snake_case + untagged enum) -------


class TestSearch:
    def test_method_constants(self) -> None:
        assert ContentSearchRequest.METHOD == "workspace.ripgrep"
        assert FuzzyOpenReq.METHOD == "workspace.fuzzy_open"
        assert FuzzyChangeReq.METHOD == "workspace.fuzzy_change"
        assert FuzzyCloseReq.METHOD == "workspace.fuzzy_close"
        assert FuzzyStatusReq.METHOD == "workspace.fuzzy_search"

    def test_response_types(self) -> None:
        # ripgrep → ContentSearchData; fuzzy_open → String; change/close → bool;
        # fuzzy_search → serde_json::Value (arbitrary JSON → Any).
        assert ContentSearchRequest.Response is ContentSearchData
        assert FuzzyOpenReq.Response is str
        assert FuzzyChangeReq.Response is bool
        assert FuzzyCloseReq.Response is bool
        assert FuzzyStatusReq.Response is Any

    # -- camelCase wire (content-search half) ------------------------------

    def test_content_search_request_to_wire_camel_case(self) -> None:
        # #[serde(rename_all = "camelCase")] — every multi-word key is camelCase;
        # respect_gitignore defaults True, sibling bools default False.
        wire = ContentSearchRequest(pattern="foo").to_wire()
        assert wire == {
            "caseInsensitive": False,
            "contextId": None,
            "cwd": None,
            "excludeGlobs": [],
            "includeGlobs": [],
            "isRegex": False,
            "maxFiles": None,
            "maxMatches": None,
            "pattern": "foo",
            "respectGitignore": True,
            "wholeWord": False,
        }

    def test_content_search_request_validates_camel_case_keys(self) -> None:
        # populate_by_name + alias_generator → camelCase keys also parse.
        req = ContentSearchRequest.model_validate(
            {"pattern": "bar", "caseInsensitive": True, "maxFiles": 10}
        )
        assert req.pattern == "bar"
        assert req.case_insensitive is True
        assert req.max_files == 10

    def test_content_search_request_defaults(self) -> None:
        # Mirrors source `content_search_request_defaults`: pattern-only input
        # → respect_gitignore=True (custom default fn), bools=False, cwd=None.
        req = ContentSearchRequest.model_validate({"pattern": "foo"})
        assert req.respect_gitignore is True
        assert not req.case_insensitive
        assert req.cwd is None

    def test_content_search_request_default_pattern_empty(self) -> None:
        # `#[derive(Default)]`: the required `pattern: String` defaults to "".
        assert ContentSearchRequest.default().pattern == ""

    # -- ContentMatch (skip_serializing_if at every depth) -----------------

    def test_content_match_omits_spans_when_none(self) -> None:
        # #[serde(default, skip_serializing_if = "Option::is_none")] on both
        # span fields — absent when None (top level).
        wire = ContentMatch(line=5, content="hi").to_wire()
        assert wire == {"content": "hi", "line": 5}

    def test_content_match_includes_spans_when_set(self) -> None:
        wire = ContentMatch(line=5, content="hi", match_start=1, match_end=3).to_wire()
        assert wire == {"content": "hi", "line": 5, "matchEnd": 3, "matchStart": 1}

    def test_content_match_skip_applies_when_nested(self) -> None:
        # The crucial model_serializer property: nested inside
        # ContentSearchData.files[].matches[], the span keys are still omitted.
        data = ContentSearchData(
            files=[
                ContentMatchFile(
                    name="a.rs",
                    path="/r/a.rs",
                    matches=[ContentMatch(line=1, content="x")],
                )
            ],
            total_matches=1,
            total_files=1,
            truncated=False,
        )
        wire = data.to_wire()
        assert wire["files"][0]["matches"] == [{"content": "x", "line": 1}]
        assert "matchStart" not in wire["files"][0]["matches"][0]

    # -- ContentMatchFile::new ---------------------------------------------

    def test_content_match_file_new_derives_name(self) -> None:
        # Mirrors source `content_match_file_new_derives_name`.
        f = ContentMatchFile.new("/repo/src/lib.rs")
        assert f.name == "lib.rs"
        assert f.path == "/repo/src/lib.rs"
        assert f.matches == []

    def test_content_match_file_new_bare_filename(self) -> None:
        assert ContentMatchFile.new("lib.rs").name == "lib.rs"

    def test_content_match_file_new_no_filename_falls_back(self) -> None:
        # Path::file_name() returns None for "." / ".." → fall back to the
        # whole path (mirrors unwrap_or_else(|| path.clone())).
        assert ContentMatchFile.new(".").name == "."
        assert ContentMatchFile.new("..").name == ".."

    # -- ClientId + TargetClientId (untagged enum) --------------------------

    def test_client_id_camel_case_round_trip(self) -> None:
        wire = ClientId(instance_id="i-1", conn_id="c-1").to_wire()
        assert wire == {"connId": "c-1", "instanceId": "i-1"}
        rec = ClientId.model_validate(wire)
        assert rec.instance_id == "i-1" and rec.conn_id == "c-1"

    def test_target_client_id_untagged_round_trip(self) -> None:
        # Mirrors source `target_client_id_untagged_round_trip`: null → None
        # variant, {instanceId, connId} → ClientId variant.
        none = TargetClientId.model_validate(None)
        assert none.is_none()
        raw = {"instanceId": "i-1", "connId": "c-1"}
        target = TargetClientId.model_validate(raw)
        assert not target.is_none()
        assert target.root is not None
        assert target.root.instance_id == "i-1"
        assert target.to_wire() == raw

    def test_target_client_id_default_is_none(self) -> None:
        # `#[default] None` — the default constructor yields the None variant.
        assert TargetClientId().is_none()
        assert TargetClientId.none().to_wire() is None

    def test_target_client_id_client_to_wire_camel_case(self) -> None:
        target = TargetClientId(ClientId(instance_id="i", conn_id="c"))
        assert target.to_wire() == {"connId": "c", "instanceId": "i"}

    # -- fuzzy RPCs (snake_case half) --------------------------------------

    def test_fuzzy_open_req_snake_case_with_default_target(self) -> None:
        # No rename_all in the source → snake_case field names on the wire;
        # target_client_id is #[serde(default)] → the None variant (wire null).
        wire = FuzzyOpenReq(root="/r", request_id="r1").to_wire()
        assert wire == {
            "hidden": False,
            "request_id": "r1",
            "root": "/r",
            "session_id": None,
            "target_client_id": None,
        }

    def test_fuzzy_open_req_carries_client_in_target(self) -> None:
        # Snake_case field name, but the nested ClientId value is camelCase.
        req = FuzzyOpenReq(
            target_client_id=TargetClientId(ClientId(instance_id="i", conn_id="c"))
        )
        assert req.to_wire()["target_client_id"] == {"connId": "c", "instanceId": "i"}

    def test_fuzzy_change_req_to_wire(self) -> None:
        wire = FuzzyChangeReq(search_id="s1", query="foo").to_wire()
        assert wire == {
            "dirs_only": False,
            "limit": None,
            "query": "foo",
            "search_id": "s1",
        }

    def test_fuzzy_close_req_to_wire(self) -> None:
        assert FuzzyCloseReq(search_id="s1").to_wire() == {"search_id": "s1"}

    def test_fuzzy_status_req_default_search_id_empty(self) -> None:
        # `#[derive(Default)]`: required `search_id: String` → "".
        assert FuzzyStatusReq.default().search_id == ""

    # -- envelope consumption of primitive / Value responses ---------------

    def test_envelope_ok_wraps_string_response(self) -> None:
        # FuzzyOpenReq.Response = String — a primitive rides the Ok arm.
        wire = RpcEnvelope.ok("search-1").to_wire()
        assert wire == {"ok": "search-1"}
        rec = RpcEnvelope.from_wire(wire, str)
        ok, err = rec.into_result()
        assert err is None and ok == "search-1"

    def test_envelope_ok_wraps_bool_response(self) -> None:
        wire = RpcEnvelope.ok(True).to_wire()
        assert wire == {"ok": True}
        rec = RpcEnvelope.from_wire(wire, bool)
        ok, _ = rec.into_result()
        assert ok is True

    def test_envelope_ok_wraps_arbitrary_json_response(self) -> None:
        # FuzzyStatusReq.Response = serde_json::Value → Any (arbitrary JSON,
        # including null when the search no longer exists).
        payload = {"paths": ["/a", "/b"], "count": 2}
        wire = RpcEnvelope.ok(payload).to_wire()
        assert wire == {"ok": payload}
        rec = RpcEnvelope.from_wire(wire, Any)
        ok, _ = rec.into_result()
        assert ok == payload

    def test_envelope_ok_wraps_content_search_data(self) -> None:
        # Full Ok-side consumption: a ripgrep response rides the envelope and
        # round-trips back, camelCase keys + nested span-skip preserved.
        data = ContentSearchData(
            files=[
                ContentMatchFile(
                    name="a.rs",
                    path="/r/a.rs",
                    matches=[
                        ContentMatch(line=1, content="foo", match_start=0, match_end=3),
                        ContentMatch(line=2, content="bar"),
                    ],
                )
            ],
            total_matches=2,
            total_files=1,
            truncated=False,
        )
        wire = RpcEnvelope.ok(data).to_wire()
        rec = RpcEnvelope.from_wire(wire, ContentSearchData)
        ok, err = rec.into_result()
        assert err is None
        assert isinstance(ok, ContentSearchData)
        assert ok.total_matches == 2
        assert ok.files[0].matches[0].match_end == 3
        assert ok.files[0].matches[1].match_start is None


# -- hooks (R71) ----------------------------------------------------------


class TestHooks:
    def test_method_constant(self) -> None:
        assert HookRegistryReq.METHOD == "workspace.hook_registry"

    def test_response_type(self) -> None:
        assert HookRegistryReq.Response is HookRegistryWire

    # -- HookEventNameWire (forward-tolerant str-subclass enum) -------------

    def test_hook_event_name_is_str_subclass(self) -> None:
        # str subclass: any instance IS a str, so equality and ordering work
        # without a custom serializer — this is what lets it serve as a JSON
        # map key (as_str avoids any subclass __str__ override).
        ev = HookEventNameWire.PRE_TOOL_USE
        assert isinstance(ev, str)
        assert isinstance(ev, HookEventNameWire)
        assert ev == "pre_tool_use"
        assert ev.as_str() == "pre_tool_use"

    def test_hook_event_name_known_constants(self) -> None:
        # The 15 known variants attached after the class body via setattr.
        assert HookEventNameWire.SESSION_START == "session_start"
        assert HookEventNameWire.SESSION_END == "session_end"
        assert HookEventNameWire.STOP == "stop"
        assert HookEventNameWire.STOP_FAILURE == "stop_failure"
        assert HookEventNameWire.PRE_TOOL_USE == "pre_tool_use"
        assert HookEventNameWire.POST_TOOL_USE == "post_tool_use"
        assert HookEventNameWire.POST_TOOL_USE_FAILURE == "post_tool_use_failure"
        assert HookEventNameWire.PERMISSION_DENIED == "permission_denied"
        assert HookEventNameWire.USER_PROMPT_SUBMIT == "user_prompt_submit"
        assert HookEventNameWire.NOTIFICATION == "notification"
        assert HookEventNameWire.SUBAGENT_START == "subagent_start"
        assert HookEventNameWire.SUBAGENT_STOP == "subagent_stop"
        assert HookEventNameWire.SUBAGENT_END == "subagent_end"
        assert HookEventNameWire.PRE_COMPACT == "pre_compact"
        assert HookEventNameWire.POST_COMPACT == "post_compact"

    def test_hook_event_name_snake_case_round_trip_all_variants(self) -> None:
        # Mirrors grok's `hook_event_name_wire_snake_case_round_trip`: all 15
        # known events round-trip as the snake_case wire string through a
        # HookSpecWire.event field — a plain server string validates into the
        # subclass, and serialises back to the plain wire string (the
        # __get_pydantic_core_schema__ path on both sides).
        variants = [
            ("SESSION_START", "session_start"),
            ("SESSION_END", "session_end"),
            ("STOP", "stop"),
            ("STOP_FAILURE", "stop_failure"),
            ("PRE_TOOL_USE", "pre_tool_use"),
            ("POST_TOOL_USE", "post_tool_use"),
            ("POST_TOOL_USE_FAILURE", "post_tool_use_failure"),
            ("PERMISSION_DENIED", "permission_denied"),
            ("USER_PROMPT_SUBMIT", "user_prompt_submit"),
            ("NOTIFICATION", "notification"),
            ("SUBAGENT_START", "subagent_start"),
            ("SUBAGENT_STOP", "subagent_stop"),
            ("SUBAGENT_END", "subagent_end"),
            ("PRE_COMPACT", "pre_compact"),
            ("POST_COMPACT", "post_compact"),
        ]
        for attr, wire_str in variants:
            const = getattr(HookEventNameWire, attr)
            assert isinstance(const, HookEventNameWire)
            assert const.as_str() == wire_str
            # Validate a plain server string → coerced subclass instance.
            spec = HookSpecWire.model_validate(
                {
                    "name": "h",
                    "event": wire_str,
                    "handler_type": "command",
                    "enabled": True,
                    "timeout_ms": 1,
                    "source_dir": "/r",
                    "extra_env": {},
                }
            )
            assert isinstance(spec.event, HookEventNameWire)
            assert spec.event == wire_str
            # Serialise → plain snake_case wire string.
            assert spec.to_wire()["event"] == wire_str

    def test_hook_event_name_unknown_is_any_string(self) -> None:
        # Mirrors grok's `hook_event_name_wire_unknown_round_trips_losslessly`:
        # the Unknown variant is an open vocabulary — any server string is a
        # legal instance (decode never fails), and distinct unknowns stay
        # distinct (so they don't collapse into one map key).
        ev = HookEventNameWire("some_future_event_v2")
        assert isinstance(ev, HookEventNameWire)
        assert ev.as_str() == "some_future_event_v2"
        assert ev != HookEventNameWire.PRE_TOOL_USE
        assert ev != HookEventNameWire("other_future_event_v3")

    # -- HookSpecWire (#[serde(skip)] matcher + snake_case) ------------------

    def test_hook_spec_wire_omits_matcher(self) -> None:
        # #[serde(skip)] matcher — the compiled regex is never on the wire; the
        # Python model simply doesn't declare the field (not skip_serializing_if
        # — it is never serialised or deserialised).
        spec = HookSpecWire(
            name="lint",
            event=HookEventNameWire.POST_TOOL_USE,
            handler_type="command",
            enabled=True,
            timeout_ms=5000,
            source_dir="/repo",
            extra_env={"RUST_LOG": "debug"},
        )
        wire = spec.to_wire()
        assert "matcher" not in wire
        assert wire["event"] == "post_tool_use"
        assert wire["extra_env"] == {"RUST_LOG": "debug"}

    def test_hook_spec_wire_snake_case_keys(self) -> None:
        # No rename_all in the source → snake_case field names verbatim on the
        # wire; Option fields appear with their value (including None).
        spec = HookSpecWire(
            name="fmt",
            event=HookEventNameWire.PRE_TOOL_USE,
            handler_type="command",
            configured_matcher="Edit",
            enabled=True,
            command="/usr/bin/fmt",
            timeout_ms=1000,
            source_dir="/r",
            extra_env={},
        )
        wire = spec.to_wire()
        assert wire == {
            "command": "/usr/bin/fmt",
            "command_raw": None,
            "configured_matcher": "Edit",
            "enabled": True,
            "event": "pre_tool_use",
            "extra_env": {},
            "handler_type": "command",
            "name": "fmt",
            "source_dir": "/r",
            "timeout_ms": 1000,
            "url": None,
            "url_raw": None,
        }

    def test_hook_spec_wire_event_validates_as_subclass(self) -> None:
        # A plain server string for `event` validates and is coerced into the
        # HookEventNameWire subclass via the core-schema hook (no
        # arbitrary_types_allowed needed on the model).
        spec = HookSpecWire.model_validate(
            {
                "name": "h",
                "event": "pre_tool_use",
                "handler_type": "command",
                "enabled": True,
                "timeout_ms": 100,
                "source_dir": "/r",
                "extra_env": {},
            }
        )
        assert isinstance(spec.event, HookEventNameWire)
        assert spec.event == "pre_tool_use"

    # -- HookRegistryWire (enum as JSON map key) ----------------------------

    def test_hook_registry_wire_event_as_map_key(self) -> None:
        # The HookEventNameWire enum (a str subclass) serialises natively as a
        # JSON object key — no custom key serializer needed.
        spec = HookSpecWire(
            name="h",
            event=HookEventNameWire.PRE_TOOL_USE,
            handler_type="command",
            enabled=True,
            timeout_ms=100,
            source_dir="/r",
            extra_env={},
        )
        wire = HookRegistryWire(hooks={HookEventNameWire.PRE_TOOL_USE: [spec]}).to_wire()
        assert "pre_tool_use" in wire["hooks"]
        assert wire["hooks"]["pre_tool_use"][0]["event"] == "pre_tool_use"

    def test_hook_registry_wire_round_trips_server_json(self) -> None:
        # Mirrors grok's `hook_registry_wire_round_trips_server_json`: a
        # realistic server payload carrying both a known event (with a full
        # HookSpecWire) and an unknown event (empty list) round-trips
        # losslessly. dict == ignores key order, so sort_mappings' alphabetical
        # reorder of the spec's fields still matches the input dict.
        server_json = {
            "hooks": {
                "pre_tool_use": [
                    {
                        "name": "lint",
                        "event": "pre_tool_use",
                        "handler_type": "command",
                        "configured_matcher": "Edit",
                        "enabled": True,
                        "command": "/bin/lint",
                        "command_raw": None,
                        "url": None,
                        "url_raw": None,
                        "timeout_ms": 3000,
                        "source_dir": "/repo",
                        "extra_env": {"LOG": "1"},
                    }
                ],
                "some_future_event_v2": [],
            }
        }
        rec = HookRegistryWire.model_validate(server_json)
        assert rec.to_wire() == server_json

    def test_hook_registry_wire_empty_default(self) -> None:
        # `#[derive(Default)]` → empty hooks map (Field(default_factory=dict)).
        assert HookRegistryWire().to_wire() == {"hooks": {}}
        assert HookRegistryWire.default().to_wire() == {"hooks": {}}
        assert HookRegistryWire.model_validate({"hooks": {}}).to_wire() == {"hooks": {}}

    def test_hook_registry_req_empty_and_default(self) -> None:
        # Empty-parameter request struct (no fields); `#[derive(Default)]` →
        # default() succeeds and to_wire is the empty object.
        assert HookRegistryReq().to_wire() == {}
        assert HookRegistryReq.default().to_wire() == {}

    # -- envelope consumption ------------------------------------------------

    def test_envelope_ok_wraps_hook_registry_wire(self) -> None:
        # Full Ok-side consumption: a hook_registry response rides the envelope
        # and round-trips back, the enum map key preserved as a plain string.
        spec = HookSpecWire(
            name="h",
            event=HookEventNameWire.POST_COMPACT,
            handler_type="command",
            enabled=True,
            timeout_ms=50,
            source_dir="/r",
            extra_env={},
        )
        data = HookRegistryWire(hooks={HookEventNameWire.POST_COMPACT: [spec]})
        wire = RpcEnvelope.ok(data).to_wire()
        rec = RpcEnvelope.from_wire(wire, HookRegistryWire)
        ok, err = rec.into_result()
        assert err is None
        assert isinstance(ok, HookRegistryWire)
        assert "post_compact" in ok.hooks


class TestWorkspace:
    # -- method constants + response types ----------------------------------

    def test_method_constants(self) -> None:
        # Mirrors grok's `method_constant` (11) plus the two list_* methods
        # grok didn't assert (defined after the #[cfg(test)] block).
        assert WorkspaceInfoReq.METHOD == "workspace.info"
        assert LoadProjectConfigReq.METHOD == "workspace.load_project_config"
        assert LoadPermissionsReq.METHOD == "workspace.load_permissions"
        assert LoadEnvrcReq.METHOD == "workspace.load_envrc"
        assert ToolDefinitionsReq.METHOD == "workspace.tool_definitions"
        assert ResolveFileReferencesReq.METHOD == "workspace.resolve_file_references"
        assert UpdateToolConfigReq.METHOD == "workspace.update_tool_config"
        assert DropSessionReq.METHOD == "workspace.drop_session"
        assert ConfigureMcpReq.METHOD == "workspace.configure_mcp"
        assert InstallPluginReq.METHOD == "workspace.install_plugin"
        assert RefreshPluginsReq.METHOD == "workspace.refresh_plugins"
        assert ListBackgroundTasksReq.METHOD == "workspace.list_background_tasks"
        assert ListTodosReq.METHOD == "workspace.list_todos"

    def test_value_response_methods_use_any(self) -> None:
        # 11 methods carry serde_json::Value responses → Response: ClassVar = Any
        # (server-defined shapes this crate deliberately does not type).
        for cls in (
            WorkspaceInfoReq,
            LoadProjectConfigReq,
            LoadPermissionsReq,
            LoadEnvrcReq,
            ToolDefinitionsReq,
            ResolveFileReferencesReq,
            UpdateToolConfigReq,
            DropSessionReq,
            ConfigureMcpReq,
            InstallPluginReq,
            RefreshPluginsReq,
        ):
            assert cls.Response is Any

    def test_typed_response_methods(self) -> None:
        # Only the two list responses are typed (their own wire structs).
        assert ListBackgroundTasksReq.Response is ListBackgroundTasksResponse
        assert ListTodosReq.Response is ListTodosResponse

    # -- WorkspaceInfo (typed shape alongside a raw Value response) ----------

    def test_workspace_info_deserializes_server_shape(self) -> None:
        # Mirrors grok's `workspace_info_deserializes_server_shape`.
        raw = {"os": "linux", "shell": "bash", "cwd": "/workspace"}
        info = WorkspaceInfo.model_validate(raw)
        assert info.os == "linux"
        assert info.shell == "bash"
        assert info.cwd == "/workspace"

    def test_workspace_info_ignores_unknown_fields(self) -> None:
        # Mirrors grok's `workspace_info_ignores_unknown_fields` — pydantic's
        # default ignores unknown keys, mirroring serde's forward tolerance.
        raw = {"os": "linux", "shell": "zsh", "cwd": "/workspace", "future_field": 42}
        info = WorkspaceInfo.model_validate(raw)
        assert info.shell == "zsh"

    def test_workspace_info_no_default(self) -> None:
        # Does NOT derive Default in the source (three required fields) —
        # default() must be overridden or fail; here it raises on missing args.
        with pytest.raises(Exception):  # noqa: B017
            WorkspaceInfo.default()

    # -- empty-parameter requests (Response = Value / Any) -------------------

    def test_empty_param_requests_default(self) -> None:
        # 6 empty-parameter requests: default() → empty object (#[derive(Default)]).
        for cls in (
            WorkspaceInfoReq,
            LoadProjectConfigReq,
            LoadPermissionsReq,
            LoadEnvrcReq,
            InstallPluginReq,
            RefreshPluginsReq,
        ):
            assert cls().to_wire() == {}
            assert cls.default().to_wire() == {}

    # -- requests carrying parameters (Response = Value / Any) ---------------

    def test_tool_definitions_req_carries_session_id(self) -> None:
        wire = ToolDefinitionsReq(session_id="s1").to_wire()
        assert wire == {"session_id": "s1"}

    def test_tool_definitions_req_default(self) -> None:
        # #[derive(Default)]: required session_id: String → "".
        assert ToolDefinitionsReq.default().to_wire() == {"session_id": ""}

    def test_resolve_file_references_req_carries_refs(self) -> None:
        wire = ResolveFileReferencesReq(refs=["@a.md", "@b.py"]).to_wire()
        assert wire == {"refs": ["@a.md", "@b.py"]}

    def test_resolve_file_references_req_default(self) -> None:
        # #[derive(Default)]: required refs: Vec<String> → [].
        assert ResolveFileReferencesReq.default().to_wire() == {"refs": []}

    def test_configure_mcp_req_carries_mcp_servers(self) -> None:
        # mcp_servers is raw serde_json::Value (the ACP McpServer list) → Any.
        servers = [{"name": "fs", "transport": "stdio"}]
        wire = ConfigureMcpReq(mcp_servers=servers).to_wire()
        assert wire == {"mcp_servers": servers}

    # -- caller_session_id elision (skip_serializing_if = "String::is_empty")

    def test_update_tool_config_req_omits_empty_caller(self) -> None:
        # The deprecated caller_session_id defaults to "" → omitted on the wire
        # (a typed client never sends a self-attested "").
        req = UpdateToolConfigReq(session_id="s1", new_config={"max_tokens": 4096})
        wire = req.to_wire()
        assert "caller_session_id" not in wire
        assert wire["session_id"] == "s1"
        assert wire["new_config"] == {"max_tokens": 4096}

    def test_update_tool_config_req_keeps_nonempty_caller(self) -> None:
        # A non-empty caller_session_id is preserved (legacy self-attested path).
        req = UpdateToolConfigReq(caller_session_id="caller-1", session_id="s1", new_config=None)
        wire = req.to_wire()
        assert wire["caller_session_id"] == "caller-1"
        # new_config = Value::Null (None) is still emitted (not an empty string).
        assert wire["new_config"] is None

    def test_update_tool_config_req_default(self) -> None:
        # #[derive(Default)]: caller_session_id → "" (omitted), session_id → "",
        # new_config → Value::Null (None).
        assert UpdateToolConfigReq.default().to_wire() == {"new_config": None, "session_id": ""}

    def test_drop_session_req_omits_empty_caller(self) -> None:
        req = DropSessionReq(session_id="s1")
        wire = req.to_wire()
        assert "caller_session_id" not in wire
        assert wire == {"session_id": "s1"}

    def test_drop_session_req_keeps_nonempty_caller(self) -> None:
        req = DropSessionReq(caller_session_id="c1", session_id="s1")
        assert req.to_wire() == {"caller_session_id": "c1", "session_id": "s1"}

    def test_drop_session_req_default(self) -> None:
        assert DropSessionReq.default().to_wire() == {"session_id": ""}

    # -- BackgroundTaskSummaryWire (#[serde(skip_serializing_if = Option::is_none)])

    def test_background_task_summary_omits_none_tool_name(self) -> None:
        wire = BackgroundTaskSummaryWire(task_id="t1", command="npm run build").to_wire()
        assert wire == {"command": "npm run build", "task_id": "t1"}

    def test_background_task_summary_keeps_tool_name(self) -> None:
        wire = BackgroundTaskSummaryWire(
            task_id="t1", command="rg foo", tool_name="ripgrep"
        ).to_wire()
        assert wire == {"command": "rg foo", "task_id": "t1", "tool_name": "ripgrep"}

    def test_background_task_summary_default(self) -> None:
        # #[derive(Default)]: task_id → "", command → "", tool_name → None.
        wire = BackgroundTaskSummaryWire.default().to_wire()
        assert wire == {"command": "", "task_id": ""}

    # -- list responses + list requests default ------------------------------

    def test_list_background_tasks_response_default(self) -> None:
        # #[derive(Default)]: tasks → [].
        assert ListBackgroundTasksResponse().to_wire() == {"tasks": []}
        assert ListBackgroundTasksResponse.default().to_wire() == {"tasks": []}

    def test_list_background_tasks_req_default(self) -> None:
        # #[derive(Default)]: required session_id: String → "".
        assert ListBackgroundTasksReq.default().to_wire() == {"session_id": ""}

    def test_todo_summary_wire_fields(self) -> None:
        wire = TodoSummaryWire(id="t1", content="do thing", status="in_progress").to_wire()
        assert wire == {"content": "do thing", "id": "t1", "status": "in_progress"}

    def test_list_todos_response_default(self) -> None:
        assert ListTodosResponse().to_wire() == {"todos": []}
        assert ListTodosResponse.default().to_wire() == {"todos": []}

    def test_list_todos_req_default(self) -> None:
        assert ListTodosReq.default().to_wire() == {"session_id": ""}

    # -- nested round-trip ---------------------------------------------------

    def test_list_background_tasks_response_round_trip(self) -> None:
        # tool_name omitted on t1, present on t2; nested dicts key-sorted by
        # sort_mappings (dict == ignores key order, but the literal is sorted).
        resp = ListBackgroundTasksResponse(
            tasks=[
                BackgroundTaskSummaryWire(task_id="t1", command="npm run build"),
                BackgroundTaskSummaryWire(task_id="t2", command="rg foo", tool_name="ripgrep"),
            ]
        )
        wire = resp.to_wire()
        assert wire == {
            "tasks": [
                {"command": "npm run build", "task_id": "t1"},
                {"command": "rg foo", "task_id": "t2", "tool_name": "ripgrep"},
            ]
        }
        rec = ListBackgroundTasksResponse.model_validate(wire)
        assert rec.to_wire() == wire

    def test_list_todos_response_round_trip(self) -> None:
        resp = ListTodosResponse(
            todos=[
                TodoSummaryWire(id="1", content="a", status="pending"),
                TodoSummaryWire(id="2", content="b", status="completed"),
            ]
        )
        wire = resp.to_wire()
        assert wire == {
            "todos": [
                {"content": "a", "id": "1", "status": "pending"},
                {"content": "b", "id": "2", "status": "completed"},
            ]
        }
        assert ListTodosResponse.model_validate(wire).to_wire() == wire

    # -- envelope consumption ------------------------------------------------

    def test_envelope_ok_wraps_list_background_tasks(self) -> None:
        # Full Ok-side consumption: a list_background_tasks response rides the
        # envelope and round-trips, the nested tool_name preserved.
        data = ListBackgroundTasksResponse(
            tasks=[BackgroundTaskSummaryWire(task_id="t1", command="ls", tool_name="terminal")]
        )
        wire = RpcEnvelope.ok(data).to_wire()
        rec = RpcEnvelope.from_wire(wire, ListBackgroundTasksResponse)
        ok, err = rec.into_result()
        assert err is None
        assert isinstance(ok, ListBackgroundTasksResponse)
        assert ok.tasks[0].task_id == "t1"
        assert ok.tasks[0].tool_name == "terminal"
