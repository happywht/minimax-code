"""Tests for the workspace RPC foundation layer (R68).

Covers the externally-tagged :class:`RpcEnvelope` wire shape, the
:class:`WorkspaceRpc` protocol contract (METHOD + Response ClassVars),
the session prompt-tracking RPCs, and the agents_md discovery RPC — all
pinned to the grok source's own ``#[cfg(test)]`` assertions as the wire
contract.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import TypeAdapter, ValidationError

from minimax_code.workspace_types._wire import sort_mappings
from minimax_code.workspace_types.rpc import (
    # fs (R77)
    CLIENT_FS_LIST_METHOD,
    CLIENT_FS_READ_FILE_METHOD,
    CLIENT_FS_STAT_METHOD,
    TURN_ACTIVE,
    # git (R74)
    UNTRACKED_CONTENT_THRESHOLD,
    WORKSPACE_CLIENT_EXT_NOTIFICATIONS_TOOL_ID,
    WORKSPACE_EVENTS_TOOL_ID,
    WORKSPACE_RPC_TOOL_ID,
    WORKSPACE_TOOL_NOTIFICATIONS_TOOL_ID,
    AgentConfigFile,
    # worktree (R75)
    ApplyMode,
    ApplyWorktreeRequest,
    ApplyWorktreeResponse,
    ApplyWorktreeResponseConflicts,
    ApplyWorktreeResponseSuccess,
    BackgroundTaskSummaryWire,
    BeginPromptReq,
    BinaryFileInfoData,
    # hunks (R76)
    BulkHunkActionResponse,
    ChangeType,
    CheckoutCommitResponse,
    ClientFsListNode,
    ClientFsListReq,
    ClientFsListRes,
    ClientFsReadFileReq,
    ClientFsReadFileRes,
    ClientFsStatReq,
    ClientFsStatRes,
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
    CommitData,
    CommitResult,
    CommitWithPatchData,
    ConfigureMcpReq,
    ConflictType,
    ContentMatch,
    ContentMatchFile,
    ContentSearchData,
    ContentSearchRequest,
    CopiedChangesSummary,
    CreateWorktreeFromWorktreeRequestWire,
    CreateWorktreeFromWorktreeResponse,
    CreateWorktreeFromWorktreeSyncReq,
    CreateWorktreeRequest,
    CreateWorktreeResponse,
    CreateWorktreeResponseCreating,
    CreateWorktreeResponseExists,
    DeployError,
    DetectVcsKindReq,
    DiffStatsSummary,
    DirtyStateSummary,
    DiscardScope,
    DiscoverAgentsMdReq,
    DiscoverPluginsReq,
    DiscoverSkillsReq,
    DropSessionReq,
    EndPromptReq,
    FileConflict,
    FileContentEntryWire,
    FileContentStatusWire,
    FileContentViewWire,
    FileRewindConflict,
    FileRewindResponse,
    FileSummary,
    FilteredHunksResponse,
    FsContentType,
    FsDeleteFileReq,
    FsExistsData,
    FsExistsReq,
    FsListData,
    FsListNode,
    FsListReq,
    FsNodeType,
    FsReadEncoding,
    FsReadFileData,
    FsReadFileReq,
    FsWriteFileReq,
    FuzzyChangeReq,
    FuzzyCloseReq,
    FuzzyOpenReq,
    FuzzyStatusReq,
    GetFileEntry,
    GetFileResult,
    GetFilesReq,
    GetFilesRes,
    GitBranchesReq,
    GitBranchInfoReq,
    GitBranchListData,
    GitCheckoutCommitReq,
    GitCheckoutReq,
    GitCollectChangesReq,
    GitCollectChangesResponse,
    GitCommitReq,
    GitCurrentCommitReq,
    GitDiffReq,
    GitDiffsData,
    GitDiscardReq,
    GitError,
    GitFileChange,
    GitFilesReq,
    GitInfoData,
    GitInfoReq,
    GitMetadataReq,
    GitReadFilesData,
    GitResolveRootReq,
    GitStageContentReq,
    GitStageReq,
    GitStashReq,
    GitStatusData,
    GitStatusExtReq,
    GitStatusExtResponse,
    GitStatusFormat,
    GitStatusReq,
    GitUnstageReq,
    HookEventNameWire,
    HookRegistryReq,
    HookRegistryWire,
    HookSpecWire,
    HunkActionKind,
    HunkActionReq,
    HunkActionResponse,
    HunkAllActionReq,
    HunkFileActionReq,
    HunkGetAllFileContentsReq,
    HunkGetAllHunksReq,
    HunkGetFileSummariesReq,
    HunkGetFilteredHunksReq,
    HunkGetSessionSummaryReq,
    HunkGetStagedFilesReq,
    HunkLineInfoWire,
    HunkSingleActionReq,
    HunkSourceWire,
    HunkTurnActionReq,
    HunkWire,
    IdentityData,
    InstallPluginReq,
    ListBackgroundTasksReq,
    ListBackgroundTasksResponse,
    ListTodosReq,
    ListTodosResponse,
    LoadEnvrcReq,
    LoadPermissionsReq,
    LoadProjectConfigReq,
    PrepareWorktreeFromWorktreeResponse,
    PutFileEntry,
    PutFileResult,
    PutFilesReq,
    PutFilesRes,
    RefreshPluginsReq,
    RemoveWorktreeRequest,
    RemoveWorktreeResponse,
    RepoInfo,
    ResolveFileReferencesReq,
    RewindToReq,
    RpcEnvelope,
    RpcError,
    SessionStatsWire,
    SessionSummaryWire,
    SkillInfo,
    SkillScope,
    StageData,
    TargetClientId,
    TodoSummaryWire,
    ToolDefinitionsReq,
    TurnSummaryWire,
    UncommittedChangesData,
    UpdateToolConfigReq,
    VcsKind,
    WorkspaceInfo,
    WorkspaceInfoReq,
    WorkspaceRpc,
    WorktreeCopyMode,
    WorktreeCreateSyncReq,
    WorktreeDbPathReq,
    WorktreeDbPathResponse,
    WorktreeDbRebuildReq,
    WorktreeDbStatsReq,
    WorktreeGcReq,
    WorktreeListReq,
    WorktreeShowReq,
    WorktreeType,
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


class TestSkills:
    """R73: workspace.discover_skills / discover_plugins + SkillScope + SkillInfo."""

    # -- method constants (grok method_constant) ---------------------------

    def test_method_constants(self):
        assert DiscoverSkillsReq.METHOD == "workspace.discover_skills"
        assert DiscoverPluginsReq.METHOD == "workspace.discover_plugins"

    # -- Response types (bare-list Value / typed list) ---------------------

    def test_discover_skills_req_response_is_list_skill_info(self):
        # Bare typed list response (distinct from R72's wrapped-struct
        # ListBackgroundTasksResponse).
        assert DiscoverSkillsReq.Response == list[SkillInfo]

    def test_discover_plugins_req_response_is_list_any(self):
        # Bare arbitrary-JSON list response (distinct from R72's scalar Any).
        assert DiscoverPluginsReq.Response == list[Any]

    def test_empty_param_requests_default(self):
        assert DiscoverSkillsReq.default() == DiscoverSkillsReq()
        assert DiscoverPluginsReq.default() == DiscoverPluginsReq()
        assert DiscoverSkillsReq().to_wire() == {}
        assert DiscoverPluginsReq().to_wire() == {}

    # -- SkillScope forward-tolerant enum ----------------------------------

    def test_skill_scope_known_values_decode(self):
        cases = {
            "local": SkillScope.LOCAL,
            "repo": SkillScope.REPO,
            "user": SkillScope.USER,
            "server": SkillScope.SERVER,
            "bundled": SkillScope.BUNDLED,
            "plugin": SkillScope.PLUGIN,
        }
        for raw, expected in cases.items():
            v = TypeAdapter(SkillScope).validate_python(raw)
            assert v == expected, raw

    def test_skill_scope_class_attrs(self):
        assert SkillScope.LOCAL == "local"
        assert SkillScope.REPO == "repo"
        assert SkillScope.USER == "user"
        assert SkillScope.SERVER == "server"
        assert SkillScope.BUNDLED == "bundled"
        assert SkillScope.PLUGIN == "plugin"

    def test_skill_scope_unknown_round_trips_losslessly(self):
        v = TypeAdapter(SkillScope).validate_python("galactic")
        assert v == "galactic"
        assert TypeAdapter(SkillScope).dump_python(v, mode="json") == "galactic"

    def test_skill_scope_known_values_round_trip(self):
        for raw in ["local", "repo", "user", "server", "bundled", "plugin"]:
            v = TypeAdapter(SkillScope).validate_python(raw)
            assert TypeAdapter(SkillScope).dump_python(v, mode="json") == raw

    def test_skill_scope_as_str(self):
        assert SkillScope.LOCAL.as_str() == "local"
        # Unknown preserves the raw captured value.
        unknown = TypeAdapter(SkillScope).validate_python("galactic")
        assert unknown.as_str() == "galactic"

    def test_skill_scope_is_str_subclass(self):
        # The forward-tolerant shape is a str subclass (same shape as R71's
        # HookEventNameWire), so it works wherever a plain str does.
        assert isinstance(SkillScope.SERVER, str)

    # -- SkillInfo minimal / full / round-trip ------------------------------

    def test_skill_info_minimal_payload(self):
        raw = {
            "name": "my-skill",
            "description": "A test skill",
            "path": "/workspace/.grok/skills/my-skill/SKILL.md",
            "scope": "local",
        }
        info = SkillInfo.model_validate(raw)
        assert info.name == "my-skill"
        assert info.scope == SkillScope.LOCAL
        assert info.user_invocable is True  # default_true
        assert info.enabled is True  # default_true
        assert info.has_user_specified_description is False  # #[serde(default)]
        assert info.disable_model_invocation is False
        assert info.config_source is None

    def test_skill_info_full_payload_round_trip(self):
        raw = {
            "name": "deploy",
            "display_name": "Deploy Helper",
            "description": "Deploys the app",
            "has_user_specified_description": True,
            "paths": ["infra/**"],
            "when_to_use": "Use when deploying",
            "short_description": "Deploy",
            "author": "someone",
            "argument_hint": "environment name",
            "license": "Apache-2.0",
            "compatibility": "Requires kubectl",
            "metadata": {"team": "infra"},
            "path": "/root/.grok/server-skills/deploy/SKILL.md",
            "scope": "server",
            "config_source": {"type": "user", "path": "/root/.grok/skills"},
            "plugin_name": "infra-plugin",
            "plugin_version": "1.0.0",
            "plugin_root": "/root/.grok/plugins/infra-plugin",
            "plugin_data": "/root/.grok/plugin-data/infra-plugin",
            "allowed_tools": ["bash"],
            "model": "grok-4",
            "effort": "high",
            "user_invocable": True,
            "disable_model_invocation": False,
            "enabled": True,
            "body": "# Deploy\n",
        }
        info = SkillInfo.model_validate(raw)
        assert info.scope == SkillScope.SERVER
        assert info.display_name == "Deploy Helper"
        assert info.plugin_name == "infra-plugin"
        assert info.config_source["type"] == "user"
        # Re-serializing reproduces the input (sorted); skip_serializing_if is
        # a no-op here because no field is None in the full payload.
        assert info.to_wire() == sort_mappings(raw)

    def test_skill_info_omits_none_optionals(self):
        # Only the 4 required fields + 4 bool (2 default_true, 2 default) —
        # every one of the 18 None Option fields is dropped from the wire
        # by the wrap model_serializer's `v is not None` filter.
        info = SkillInfo(name="n", description="d", path="/p/SKILL.md", scope="repo")
        assert info.to_wire() == {
            "description": "d",
            "disable_model_invocation": False,
            "enabled": True,
            "has_user_specified_description": False,
            "name": "n",
            "path": "/p/SKILL.md",
            "scope": "repo",
            "user_invocable": True,
        }

    def test_skill_info_default_true_fields_default_to_true(self):
        info = SkillInfo(name="n", description="d", path="/p", scope="local")
        assert info.user_invocable is True
        assert info.enabled is True

    def test_skill_info_false_bools_survive_serialization(self):
        # default_true fields with an explicit False are still emitted — bool
        # fields carry no skip_serializing_if, only None is elided.
        info = SkillInfo(
            name="n",
            description="d",
            path="/p",
            scope="local",
            user_invocable=False,
            enabled=False,
        )
        wire = info.to_wire()
        assert wire["user_invocable"] is False
        assert wire["enabled"] is False

    def test_skill_info_no_default(self):
        # grok does not #[derive(Default)] (4 required fields); the base
        # default() = cls() must therefore raise.
        with pytest.raises(Exception):  # noqa: B017
            SkillInfo.default()

    def test_skill_info_ignores_unknown_fields(self):
        raw = {
            "name": "n",
            "description": "d",
            "path": "/p/SKILL.md",
            "scope": "repo",
            "brand_new_field": {"nested": True},
        }
        info = SkillInfo.model_validate(raw)
        assert info.scope == SkillScope.REPO

    def test_skill_info_empty_map_survives(self):
        # Option::is_none elides only None, not an empty map.
        info = SkillInfo(
            name="n", description="d", path="/p", scope="local", metadata={}
        )
        assert info.to_wire()["metadata"] == {}

    def test_skill_info_empty_vec_survives(self):
        # Option::is_none elides only None, not an empty vec.
        info = SkillInfo(
            name="n", description="d", path="/p", scope="local", paths=[]
        )
        assert info.to_wire()["paths"] == []

    # -- envelope round-trip with bare-list responses ----------------------

    def test_envelope_ok_wraps_discover_skills(self):
        # Bare typed list response: {"ok": [<SkillInfo>, ...]}.
        skills = [
            SkillInfo(name="a", description="da", path="/p/a", scope="local"),
            SkillInfo(name="b", description="db", path="/p/b", scope="server"),
        ]
        wire = RpcEnvelope.ok(skills).to_wire()
        rec = RpcEnvelope.from_wire(wire, DiscoverSkillsReq.Response)
        ok, err = rec.into_result()
        assert err is None
        assert isinstance(ok, list)
        assert all(isinstance(item, SkillInfo) for item in ok)
        assert [s.name for s in ok] == ["a", "b"]
        assert ok[1].scope == SkillScope.SERVER

    def test_envelope_ok_wraps_discover_plugins(self):
        # Bare arbitrary-JSON list response: {"ok": [<Value>, ...]}.
        plugins = [{"name": "p1", "v": 1}, {"name": "p2"}]
        wire = RpcEnvelope.ok(plugins).to_wire()
        rec = RpcEnvelope.from_wire(wire, DiscoverPluginsReq.Response)
        ok, err = rec.into_result()
        assert err is None
        assert ok == plugins


class TestGit:
    """R74: workspace.git_* / detect_vcs_kind + 4 enums + ~22 wire types.

    Pins the serde shape of grok's ``xai-grok-workspace-types::rpc::git`` —
    the 20 RPC method constants, the mixed skip matrix, the ``rename="type"``
    key override, ``Vec::is_empty`` elision, and the hand-written legacy-flat
    Deserialize on :class:`GitStatusExtResponse`.
    """

    # -- method constants (grok method_constant × 20) -----------------------

    def test_method_constants(self):
        assert GitStatusReq.METHOD == "workspace.git_status"
        assert GitStatusExtReq.METHOD == "workspace.git_status_ext"
        assert GitFilesReq.METHOD == "workspace.git_files"
        assert GitDiffReq.METHOD == "workspace.git_diff"
        assert GitStageReq.METHOD == "workspace.git_stage"
        assert GitStageContentReq.METHOD == "workspace.git_stage_content"
        assert GitUnstageReq.METHOD == "workspace.git_unstage"
        assert GitDiscardReq.METHOD == "workspace.git_discard"
        assert GitCommitReq.METHOD == "workspace.git_commit"
        assert GitCheckoutReq.METHOD == "workspace.git_checkout"
        assert GitStashReq.METHOD == "workspace.git_stash"
        assert GitInfoReq.METHOD == "workspace.git_info"
        assert GitBranchesReq.METHOD == "workspace.git_branches"
        assert GitResolveRootReq.METHOD == "workspace.git_resolve_root"
        assert GitCurrentCommitReq.METHOD == "workspace.git_current_commit"
        assert DetectVcsKindReq.METHOD == "workspace.detect_vcs_kind"
        assert GitCheckoutCommitReq.METHOD == "workspace.git_checkout_commit"
        assert GitBranchInfoReq.METHOD == "workspace.git_branch_info"
        assert GitMetadataReq.METHOD == "workspace.git_metadata"
        assert GitCollectChangesReq.METHOD == "workspace.git_collect_changes"

    # -- Response ClassVar shapes (Value / () / Option / enum / struct) ------

    def test_value_responses_are_any(self):
        # serde_json::Value → Any (no type parameter).
        assert GitStatusReq.Response is Any
        assert GitMetadataReq.Response is Any

    def test_unit_responses_are_none_type(self):
        # () → type(None).
        assert GitStageContentReq.Response is type(None)
        assert GitUnstageReq.Response is type(None)
        assert GitDiscardReq.Response is type(None)
        assert GitCheckoutReq.Response is type(None)
        assert GitStashReq.Response is type(None)

    def test_option_pathbuf_and_string_responses_are_str_or_none(self):
        # Option<PathBuf> / Option<String> → str | None.
        assert GitResolveRootReq.Response == str | None
        assert GitCurrentCommitReq.Response == str | None

    def test_option_git_info_data_response(self):
        # Option<GitInfoData> → GitInfoData | None.
        assert GitBranchInfoReq.Response == GitInfoData | None

    def test_enum_response_is_vcs_kind(self):
        assert DetectVcsKindReq.Response is VcsKind

    def test_struct_responses_are_typed(self):
        assert GitStatusExtReq.Response is GitStatusExtResponse
        assert GitFilesReq.Response is GitReadFilesData
        assert GitDiffReq.Response is GitDiffsData
        assert GitStageReq.Response is StageData
        assert GitCommitReq.Response is CommitResult
        assert GitInfoReq.Response is GitInfoData
        assert GitBranchesReq.Response is GitBranchListData
        assert GitCheckoutCommitReq.Response is CheckoutCommitResponse
        assert GitCollectChangesReq.Response is GitCollectChangesResponse

    # -- enums (4) ----------------------------------------------------------

    def test_vcs_kind_camel_case_wire_values(self):
        assert VcsKind.GIT == "git"
        assert VcsKind.JUJUTSU_COLOCATED == "jujutsuColocated"
        assert VcsKind.NONE == "none"
        # Predicates mirror grok's is_jj / is_repo.
        assert VcsKind.JUJUTSU_COLOCATED.is_jj() is True
        assert VcsKind.GIT.is_jj() is False
        assert VcsKind.GIT.is_repo() is True
        assert VcsKind.NONE.is_repo() is False

    def test_change_type_lowercase_wire_values(self):
        # No #[default] in grok → no default() method.
        assert ChangeType.CREATE == "create"
        assert ChangeType.EDIT == "edit"
        assert ChangeType.DELETE == "delete"
        assert ChangeType.RENAME == "rename"
        assert ChangeType.COPY == "copy"
        assert ChangeType.TYPECHANGE == "typechange"
        assert ChangeType.UNTRACKED == "untracked"
        assert not hasattr(ChangeType, "default")

    def test_git_status_format_lowercase_with_default(self):
        assert GitStatusFormat.STRUCTURED == "structured"
        assert GitStatusFormat.PROMPT == "prompt"

    def test_discard_scope_lowercase_with_both_default(self):
        assert DiscardScope.WORKING == "working"
        assert DiscardScope.STAGED == "staged"
        assert DiscardScope.BOTH == "both"

    # -- GitFileChange rename="type" override (grok git_file_change_serializes_type_key)

    def test_git_file_change_serializes_type_key(self):
        # #[serde(rename = "type")] → wire key is literally "type", not the
        # "changeType" the camelCase generator would produce.
        ch = GitFileChange(path="a.txt", change_type=ChangeType.EDIT, additions=1, deletions=2)
        wire = ch.to_wire()
        assert "type" in wire
        assert wire["type"] == "edit"
        assert "changeType" not in wire
        assert "change_type" not in wire
        # Required + Option-skip mix: path/additions/deletions present; the
        # 7 Option fields (old_path, staged, patch, ...) omitted when None.
        assert wire["path"] == "a.txt"
        assert wire["additions"] == 1
        assert wire["deletions"] == 2
        for absent in ("oldPath", "staged", "patch", "patchBytes", "patchLines",
                       "oldText", "newText"):
            assert absent not in wire

    def test_git_file_change_accepts_type_alias_on_construct(self):
        # populate_by_name=True: validates under either the snake name or the
        # "type" wire alias.
        ch = GitFileChange.model_validate(
            {"path": "b.txt", "type": "create", "additions": 0, "deletions": 0}
        )
        assert ch.change_type is ChangeType.CREATE
        assert ch.to_wire()["type"] == "create"

    # -- GitStatusExtResponse constructors + legacy-flat Deserialize --------

    def test_git_status_ext_response_constructors(self):
        data = GitStatusData(root="/r", branch="main")
        structured = GitStatusExtResponse.structured(data)
        assert structured.format is GitStatusFormat.STRUCTURED
        assert structured.data is data
        assert structured.prompt is None
        prompt = GitStatusExtResponse.with_prompt("M file")
        assert prompt.format is GitStatusFormat.PROMPT
        assert prompt.prompt == "M file"
        assert prompt.data is None
        default = GitStatusExtResponse.default()
        assert default.format is GitStatusFormat.STRUCTURED
        assert default.data is None
        assert default.prompt is None

    def test_git_status_ext_response_structured_roundtrip(self):
        data = GitStatusData(root="/r", branch="main", staged=[])
        wire = GitStatusExtResponse.structured(data).to_wire()
        # format always emitted; data present; prompt omitted (None).
        assert wire["format"] == "structured"
        assert "data" in wire
        assert "prompt" not in wire
        rec = GitStatusExtResponse.model_validate(wire)
        assert rec.format is GitStatusFormat.STRUCTURED
        assert isinstance(rec.data, GitStatusData)
        assert rec.data.root == "/r"

    def test_git_status_ext_response_prompt_roundtrip(self):
        wire = GitStatusExtResponse.with_prompt("ahead 2").to_wire()
        assert wire == {"format": "prompt", "prompt": "ahead 2"}
        rec = GitStatusExtResponse.model_validate(wire)
        assert rec.format is GitStatusFormat.PROMPT
        assert rec.prompt == "ahead 2"
        assert rec.data is None

    def test_git_status_ext_response_default_emits_only_format(self):
        wire = GitStatusExtResponse.default().to_wire()
        assert wire == {"format": "structured"}

    def test_git_status_ext_response_new_envelope(self):
        # A payload already carrying an envelope key passes through the
        # before-validator unwrapped.
        raw = {"format": "prompt", "prompt": "clean"}
        rec = GitStatusExtResponse.model_validate(raw)
        assert rec.format is GitStatusFormat.PROMPT

    def test_git_status_ext_response_legacy_flat_is_rewrapped(self):
        # Version skew: a legacy flat GitStatusData (no format/data/prompt
        # keys) is wrapped as {format: structured, data: <payload>}.
        legacy = {"root": "/r", "branch": "main", "staged": [], "unstaged": []}
        rec = GitStatusExtResponse.model_validate(legacy)
        assert rec.format is GitStatusFormat.STRUCTURED
        assert isinstance(rec.data, GitStatusData)
        assert rec.data.root == "/r"
        assert rec.data.branch == "main"
        assert rec.prompt is None

    def test_git_status_ext_response_empty_mapping_not_miswrapped(self):
        # An empty mapping (cls()) is not falsely treated as a legacy flat
        # payload — the `and data` guard short-circuits the rewrap.
        rec = GitStatusExtResponse()
        assert rec.format is GitStatusFormat.STRUCTURED
        assert rec.data is None
        assert rec.to_wire() == {"format": "structured"}

    # -- Vec::is_empty elision (CommitWithPatchData / UncommittedChangesData)

    def test_commit_with_patch_data_omits_empty_binary_files(self):
        # binary_files: Vec with #[serde(default, skip_serializing_if =
        # "Vec::is_empty")] → omitted when empty (NEW non-Option elision).
        stats = DiffStatsSummary(files_changed=1, insertions=2, deletions=3)
        ident = IdentityData(time_seconds=0, offset_minutes=0)
        c = CommitWithPatchData(
            id="abc", parents=[], author=ident, committer=ident, stats=stats,
        )
        wire = c.to_wire()
        assert "binaryFiles" not in wire
        # Non-empty → emitted.
        c2 = c.model_copy(update={"binary_files": [
            BinaryFileInfoData(path="x.bin", status="added", size_bytes=10,
                               blob_included=False, truncated=False),
        ]})
        assert "binaryFiles" in c2.to_wire()

    def test_uncommitted_changes_data_omits_empty_binary_files(self):
        stats = DiffStatsSummary(files_changed=0, insertions=0, deletions=0)
        u = UncommittedChangesData(staged_stats=stats, unstaged_stats=stats)
        wire = u.to_wire()
        assert "stagedBinaryFiles" not in wire
        assert "unstagedBinaryFiles" not in wire

    # -- GitInfoData mixed skip matrix --------------------------------------

    def test_git_info_data_current_branch_null_kept(self):
        # current_branch has NO skip_serializing_if → None stays as wire null.
        # default_branch / vcs_kind DO skip → omitted when None.
        info = GitInfoData(root="/r", remotes=["origin"])
        wire = info.to_wire()
        assert wire["currentBranch"] is None  # null preserved
        assert "defaultBranch" not in wire  # None omitted
        assert "vcsKind" not in wire  # None omitted
        assert wire["root"] == "/r"
        assert wire["remotes"] == ["origin"]

    def test_git_info_data_default_branch_and_vcs_kind_emitted_when_set(self):
        info = GitInfoData(root="/r", remotes=[], current_branch="main",
                           default_branch="main", vcs_kind=VcsKind.GIT)
        wire = info.to_wire()
        assert wire["currentBranch"] == "main"
        assert wire["defaultBranch"] == "main"
        assert wire["vcsKind"] == "git"

    # -- RepoInfo is_detached (non-Option bool always emitted) ---------------

    def test_repo_info_is_detached_false_is_emitted(self):
        # #[serde(default)] bool with no skip → False is emitted as false
        # (False is not None, so it survives _omit_none).
        info = RepoInfo(root="/r")
        wire = info.to_wire()
        assert wire["isDetached"] is False
        assert wire["root"] == "/r"
        # All 8 Option fields omitted when None.
        for absent in ("gitDir", "head", "branch", "upstream", "upstreamHead",
                       "remoteUrl", "ahead", "behind"):
            assert absent not in wire

    # -- GitError keeps null path (Option without skip) ---------------------

    def test_git_error_keeps_null_path(self):
        err = GitError(code="notfound", message="nope")
        assert err.to_wire() == {"path": None, "code": "notfound", "message": "nope"}

    # -- GitStatusExtReq manual Default (camelCase + default_true bools) ----

    def test_git_status_ext_req_default_roundtrip(self):
        req = GitStatusExtReq.default()
        # Manual Default: the two default_true bools are True (not False as a
        # derived Default would set them).
        assert req.include_untracked is True
        assert req.ignore_submodules is True
        assert req.include_stats is False
        assert req.include_patches is False
        assert req.format is GitStatusFormat.STRUCTURED
        wire = req.to_wire()
        # camelCase wire keys (gitRoot, includeUntracked, ...).
        assert wire["includeUntracked"] is True
        assert wire["ignoreSubmodules"] is True
        assert wire["format"] == "structured"

    def test_git_status_req_value_response(self):
        assert GitStatusReq().to_wire() == {}

    # -- GitCollectChangesReq defaults (default_true + default_max_file_bytes)

    def test_git_collect_changes_req_defaults(self):
        req = GitCollectChangesReq(repo_path="/r")
        assert req.include_commits is True
        assert req.include_uncommitted is True
        assert req.base_ref is None
        assert req.max_file_bytes == 0
        assert req.force_include_paths == []
        wire = req.to_wire()
        # snake_case (no rename_all): repo_path stays snake.
        assert wire["repo_path"] == "/r"
        assert wire["include_commits"] is True
        assert wire["max_file_bytes"] == 0

    # -- GitDiffReq from_ alias="from" --------------------------------------

    def test_git_diff_req_from_alias(self):
        req = GitDiffReq(paths=["a.txt"])
        assert req.from_ == "HEAD"  # default_head
        assert req.to == "working"  # default_working
        wire = req.to_wire()
        # "from" is a Python keyword → field is from_, wire key is "from".
        assert wire["from"] == "HEAD"
        assert wire["to"] == "working"

    # -- GitCheckoutCommitResponse / CommitResult keep null (Option no skip)

    def test_checkout_commit_response_keeps_null_error(self):
        resp = CheckoutCommitResponse(checked_out=True, stashed=False, fetched=False)
        assert resp.to_wire() == {
            "checked_out": True, "stashed": False, "fetched": False, "error": None,
        }

    def test_commit_result_keeps_null_warning(self):
        result = CommitResult(data=CommitData())
        wire = result.to_wire()
        assert wire["warning"] is None
        # Nested CommitData fires its _CamelOmitNone serializer at depth.
        assert "commitHash" not in wire["data"]

    # -- UNTRACKED_CONTENT_THRESHOLD constant -------------------------------

    def test_untracked_content_threshold(self):
        assert UNTRACKED_CONTENT_THRESHOLD == 1024 * 1024

    # -- envelope round-trip with Option-shaped responses -------------------

    def test_envelope_ok_path_response_some(self):
        wire = RpcEnvelope.ok("/repo/root").to_wire()
        rec = RpcEnvelope.from_wire(wire, GitResolveRootReq.Response)
        ok, err = rec.into_result()
        assert err is None
        assert ok == "/repo/root"

    def test_envelope_ok_path_response_none(self):
        wire = RpcEnvelope.ok(None).to_wire()
        rec = RpcEnvelope.from_wire(wire, GitResolveRootReq.Response)
        ok, err = rec.into_result()
        assert err is None
        assert ok is None

    def test_envelope_ok_vcs_kind_response(self):
        wire = RpcEnvelope.ok("git").to_wire()
        rec = RpcEnvelope.from_wire(wire, DetectVcsKindReq.Response)
        ok, err = rec.into_result()
        assert err is None
        assert ok is VcsKind.GIT

    def test_envelope_ok_git_info_data_or_none(self):
        info = GitInfoData(root="/r", remotes=["origin"], current_branch="main")
        wire = RpcEnvelope.ok(info).to_wire()
        rec = RpcEnvelope.from_wire(wire, GitBranchInfoReq.Response)
        ok, err = rec.into_result()
        assert err is None
        assert isinstance(ok, GitInfoData)
        assert ok.root == "/r"


class TestWorktree:
    """R75: workspace.worktree_* / create_worktree / remove_worktree / apply_worktree.

    Pins the serde shape of grok's ``xai-grok-workspace-types::rpc::worktree``
    — the 11 RPC method constants, the internally-tagged unions
    (:data:`CreateWorktreeResponse`, :data:`ApplyWorktreeResponse`), the
    transparent ``WorktreeCreateSyncReq`` newtype vs the non-transparent
    ``CreateWorktreeFromWorktreeSyncReq`` ``{inner: …}`` wrapper, the custom
    ``default_copy_mode`` enum default, and the mixed skip matrices.
    """

    # -- method constants (grok method_constants × 11) -----------------------

    def test_method_constants(self):
        assert CreateWorktreeRequest.METHOD == "workspace.create_worktree"
        assert WorktreeCreateSyncReq.METHOD == "workspace.worktree_create_sync"
        assert RemoveWorktreeRequest.METHOD == "workspace.remove_worktree"
        assert ApplyWorktreeRequest.METHOD == "workspace.apply_worktree"
        assert WorktreeShowReq.METHOD == "workspace.worktree_show"
        assert WorktreeGcReq.METHOD == "workspace.worktree_gc"
        assert WorktreeListReq.METHOD == "workspace.worktree_list"
        assert WorktreeDbRebuildReq.METHOD == "workspace.worktree_db_rebuild"
        assert WorktreeDbPathReq.METHOD == "workspace.worktree_db_path"
        assert WorktreeDbStatsReq.METHOD == "workspace.worktree_db_stats"
        assert (
            CreateWorktreeFromWorktreeSyncReq.METHOD
            == "workspace.worktree_create_from_worktree_sync"
        )

    # -- Response ClassVar shapes -------------------------------------------

    def test_value_responses_are_any(self):
        # serde_json::Value → Any.
        assert CreateWorktreeRequest.Response is Any
        assert WorktreeCreateSyncReq.Response is Any
        assert RemoveWorktreeRequest.Response is Any
        assert ApplyWorktreeRequest.Response is Any
        assert WorktreeShowReq.Response is Any
        assert WorktreeGcReq.Response is Any
        assert WorktreeListReq.Response is Any
        assert WorktreeDbRebuildReq.Response is Any
        assert WorktreeDbStatsReq.Response is Any

    def test_typed_responses(self):
        # CreateWorktreeFromWorktreeSyncReq.Response is the typed response struct.
        assert (
            CreateWorktreeFromWorktreeSyncReq.Response
            is CreateWorktreeFromWorktreeResponse
        )
        # WorktreeDbPathReq.Response is WorktreeDbPathResponse.
        assert WorktreeDbPathReq.Response is WorktreeDbPathResponse

    # -- enums (lowercase + #[default]) -------------------------------------

    def test_worktree_type_lowercase_with_linked_default(self):
        assert WorktreeType.LINKED == "linked"
        assert WorktreeType.STANDALONE == "standalone"
        assert WorktreeType.GIT == "git"
        assert WorktreeType.default() is WorktreeType.LINKED

    def test_worktree_copy_mode_lowercase_with_dirty_default(self):
        assert WorktreeCopyMode.CLEAN == "clean"
        assert WorktreeCopyMode.DIRTY == "dirty"
        assert WorktreeCopyMode.default() is WorktreeCopyMode.DIRTY

    def test_apply_mode_lowercase_with_overwrite_default(self):
        assert ApplyMode.OVERWRITE == "overwrite"
        assert ApplyMode.MERGE == "merge"
        assert ApplyMode.default() is ApplyMode.OVERWRITE

    # -- WorktreeType from_str round trip (grok worktree_type_from_str_round_trip)

    def test_worktree_type_from_str_round_trip(self):
        # StrEnum(value) is the Python analogue of FromStr.
        assert WorktreeType("linked") is WorktreeType.LINKED
        assert WorktreeType("standalone") is WorktreeType.STANDALONE
        assert WorktreeType("git") is WorktreeType.GIT
        with pytest.raises(ValueError):  # mirrors grok Err(())
            WorktreeType("bogus")

    # -- WorktreeCreateSyncReq transparent newtype (grok ...is_transparent) --

    def test_worktree_create_sync_req_is_transparent(self):
        req = WorktreeCreateSyncReq(session_id="s1", source_path="/repo")
        wire = req.to_wire()
        # Transparent: inner fields at top level, no "inner" wrapper key.
        assert wire["sessionId"] == "s1"
        assert wire["sourcePath"] == "/repo"
        assert "inner" not in wire
        # copy_mode defaults to Dirty via default_copy_mode.
        assert wire["copyMode"] == "dirty"
        # Option fields with #[serde(default)] (no skip) → null preserved.
        assert wire["worktreePath"] is None
        assert wire["gitRef"] is None
        # Empty vec with no skip → [].
        assert wire["ignoredSkipPatterns"] == []

    # -- CreateWorktreeFromWorktreeSyncReq non-transparent wrapper -----------

    def test_create_worktree_from_worktree_sync_req_keeps_inner_wrapper(self):
        req = CreateWorktreeFromWorktreeSyncReq(
            inner=CreateWorktreeFromWorktreeRequestWire(
                source_worktree_path="/src", new_session_id="s2",
            )
        )
        wire = req.to_wire()
        inner = wire["inner"]
        assert inner["sourceWorktreePath"] == "/src"
        assert inner["copyMode"] == "dirty"
        # The two #[serde(skip)] runtime fields are absent from the wire.
        assert "cancellationToken" not in inner
        assert "resolvedDestPath" not in inner

    # -- CreateWorktreeResponse internally tagged union ---------------------

    def test_create_worktree_response_creating_status_tagged(self):
        # grok create_worktree_response_status_tagged.
        resp = CreateWorktreeResponseCreating(
            session_id="s1", worktree_path="/wt", source_git_root=None,
        )
        wire = resp.to_wire()
        assert wire["status"] == "creating"
        assert wire["sessionId"] == "s1"
        assert wire["worktreePath"] == "/wt"
        # sourceGitRoot skip_serializing_if Option::is_none → omitted.
        assert "sourceGitRoot" not in wire

    def test_create_worktree_response_exists_status_tagged(self):
        resp = CreateWorktreeResponseExists(
            session_id="s1", worktree_path="/wt", commit="abc",
        )
        wire = resp.to_wire()
        assert wire["status"] == "exists"
        assert wire["commit"] == "abc"
        assert "sourceGitRoot" not in wire

    def test_create_worktree_response_union_discriminates(self):
        # TypeAdapter over the Annotated union picks the variant by status.
        adapter = TypeAdapter(CreateWorktreeResponse)
        creating = adapter.validate_python(
            {"status": "creating", "sessionId": "s", "worktreePath": "/w"}
        )
        assert isinstance(creating, CreateWorktreeResponseCreating)
        exists = adapter.validate_python(
            {"status": "exists", "sessionId": "s", "worktreePath": "/w", "commit": "c"}
        )
        assert isinstance(exists, CreateWorktreeResponseExists)

    # -- ApplyWorktreeResponse internally tagged union (reuses R74 types) ----

    def test_apply_worktree_response_success_tagged(self):
        files = [
            GitFileChange(path="a.txt", change_type=ChangeType.EDIT, additions=1, deletions=0)
        ]
        resp = ApplyWorktreeResponseSuccess(files=files, git_root="/r")
        wire = resp.to_wire()
        assert wire["status"] == "success"
        assert wire["gitRoot"] == "/r"
        assert wire["files"][0]["type"] == "edit"  # R74 rename="type" override

    def test_apply_worktree_response_conflicts_tagged(self):
        files = [
            GitFileChange(path="a.txt", change_type=ChangeType.EDIT, additions=1, deletions=0)
        ]
        conflicts = [FileConflict(path="a.txt", change_type=ChangeType.EDIT)]
        resp = ApplyWorktreeResponseConflicts(files=files, conflicts=conflicts)
        wire = resp.to_wire()
        assert wire["status"] == "conflicts"
        # FileConflict.change_type reuses R74 ChangeType with rename="type".
        assert wire["conflicts"][0]["type"] == "edit"
        assert wire["conflicts"][0]["path"] == "a.txt"

    def test_apply_worktree_response_union_discriminates(self):
        adapter = TypeAdapter(ApplyWorktreeResponse)
        ok = adapter.validate_python({"status": "success", "files": [], "gitRoot": "/r"})
        assert isinstance(ok, ApplyWorktreeResponseSuccess)
        bad = adapter.validate_python(
            {"status": "conflicts", "files": [], "conflicts": []}
        )
        assert isinstance(bad, ApplyWorktreeResponseConflicts)

    # -- FileConflict reuses R74 ChangeType, keeps null Options --------------

    def test_file_conflict_keeps_null_bases(self):
        fc = FileConflict(path="a.txt", change_type=ChangeType.DELETE)
        wire = fc.to_wire()
        assert wire["type"] == "delete"
        assert wire["path"] == "a.txt"
        # base/ours/theirs: no skip_serializing_if → null preserved.
        assert wire["base"] is None
        assert wire["ours"] is None
        assert wire["theirs"] is None

    # -- RemoveWorktreeResponse mixed skip matrix ----------------------------

    def test_remove_worktree_response_omits_none_resolved_path(self):
        resp = RemoveWorktreeResponse(removed=True)
        assert resp.to_wire() == {"removed": True}

    def test_remove_worktree_response_emits_resolved_path_when_set(self):
        resp = RemoveWorktreeResponse(removed=False, resolved_path="/wt")
        wire = resp.to_wire()
        assert wire["removed"] is False
        assert wire["resolvedPath"] == "/wt"

    # -- CreateWorktreeFromWorktreeResponse 3-Option skip matrix -------------

    def test_create_worktree_from_worktree_response_omits_all_three_none(self):
        resp = CreateWorktreeFromWorktreeResponse(
            status="ok", new_session_id="s2", worktree_path="/wt",
        )
        wire = resp.to_wire()
        assert wire["status"] == "ok"
        assert wire["newSessionId"] == "s2"
        assert wire["worktreePath"] == "/wt"
        for absent in ("commit", "copiedChanges", "sourceGitRoot"):
            assert absent not in wire

    def test_create_worktree_from_worktree_response_emits_copied_changes(self):
        summary = CopiedChangesSummary(
            staged_copied=1, modified_copied=2, untracked_copied=3,
            deletions_applied=0, warnings=[],
        )
        resp = CreateWorktreeFromWorktreeResponse(
            status="ok", new_session_id="s2", worktree_path="/wt",
            commit="abc", copied_changes=summary, source_git_root="/r",
        )
        wire = resp.to_wire()
        assert wire["commit"] == "abc"
        assert wire["sourceGitRoot"] == "/r"
        assert wire["copiedChanges"]["stagedCopied"] == 1

    # -- CreateWorktreeRequest copy_mode default + ignored_skip_patterns -----

    def test_create_worktree_request_copy_mode_defaults_dirty(self):
        req = CreateWorktreeRequest(session_id="s1", source_path="/repo")
        assert req.copy_mode is WorktreeCopyMode.DIRTY
        assert req.ignored_skip_patterns == []
        assert req.copy_ignored_in_background is False

    # -- ApplyWorktreeRequest mode default -----------------------------------

    def test_apply_worktree_request_mode_defaults_overwrite(self):
        req = ApplyWorktreeRequest(session_id="s1", worktree_path="/wt")
        assert req.mode is ApplyMode.OVERWRITE
        assert req.to_wire()["mode"] == "overwrite"

    # -- WorktreeGcReq max_age_secs required (no #[serde(default)]) ----------

    def test_worktree_gc_req_max_age_secs_nullable(self):
        # No #[serde(default)] on max_age_secs → key required (value may be null).
        req = WorktreeGcReq(max_age_secs=None)
        assert req.max_age_secs is None
        assert req.dry_run is False
        assert req.force is False

    def test_worktree_gc_req_missing_max_age_secs_raises(self):
        with pytest.raises(ValidationError):
            WorktreeGcReq()

    # -- WorktreeListReq types rename="type" + empty vec --------------------

    def test_worktree_list_req_types_alias_and_empty_vec(self):
        req = WorktreeListReq()
        wire = req.to_wire()
        # rename = "type" → wire key "type", not "types".
        assert wire["type"] == []
        assert "types" not in wire
        # No rename_all on this struct → snake_case keys (grok source L285-292).
        assert wire["include_all"] is False
        assert wire["repo"] is None

    # -- WorktreeDbPathResponse keeps null path ------------------------------

    def test_worktree_db_path_response_keeps_null(self):
        assert WorktreeDbPathResponse().to_wire() == {"path": None}

    # -- PrepareWorktreeFromWorktreeResponse keeps null Options --------------

    def test_prepare_worktree_from_worktree_response_keeps_null(self):
        resp = PrepareWorktreeFromWorktreeResponse(spawn_task=True)
        wire = resp.to_wire()
        assert wire["spawn_task"] is True
        assert wire["response"] is None
        assert wire["error"] is None

    # -- DirtyStateSummary / CopiedChangesSummary camelCase ------------------

    def test_dirty_state_summary_camel_case(self):
        s = DirtyStateSummary(
            staged_count=1, modified_count=2, deleted_count=3, untracked_count=4,
            has_partially_staged=True, skipped_dirs=["node_modules"],
        )
        wire = s.to_wire()
        assert wire["stagedCount"] == 1
        assert wire["hasPartiallyStaged"] is True
        assert wire["skippedDirs"] == ["node_modules"]

    # -- empty-struct RPCs default to {} ------------------------------------

    def test_empty_struct_requests(self):
        assert WorktreeDbRebuildReq().to_wire() == {}
        assert WorktreeDbPathReq().to_wire() == {}
        assert WorktreeDbStatsReq().to_wire() == {}


class TestHunks:
    """R76: workspace.hunk_* / get_all_hunks / get_session_summary.

    Pins the serde shape of grok's ``xai-grok-workspace-types::rpc::hunks``
    — the 10 RPC method constants (two without the ``hunk_`` prefix), the
    forward-tolerant :class:`HunkSourceWire` tagged enum (``#[serde(other)]``
    fallback modelled as a flat ``type: str`` model), the hand-written
    forward-tolerant :class:`FileContentStatusWire` string enum (unknowns route
    to ``UNKNOWN``), the ``DateTime<Utc>`` ``Z`` suffix, ``PathBuf`` → ``str``,
    and the :class:`FileContentViewWire` mixed skip matrix. Round-trip
    assertions use key-indexed access: :meth:`WireModel.to_wire` lexically sorts
    keys at every depth, so they never match grok's field-ordered serde output
    as a whole dict.
    """

    # -- method constants (grok method_constants × 10) -----------------------
    # Note: two methods drop the ``hunk_`` prefix on the wire.

    def test_method_constants(self):
        assert HunkSingleActionReq.METHOD == "workspace.hunk_action"
        assert HunkFileActionReq.METHOD == "workspace.hunk_file_action"
        assert HunkTurnActionReq.METHOD == "workspace.hunk_turn_action"
        assert HunkAllActionReq.METHOD == "workspace.hunk_all_action"
        assert HunkGetStagedFilesReq.METHOD == "workspace.hunk_get_staged_files"
        assert HunkGetFileSummariesReq.METHOD == "workspace.hunk_get_file_summaries"
        assert HunkGetFilteredHunksReq.METHOD == "workspace.hunk_get_filtered_hunks"
        assert HunkGetAllFileContentsReq.METHOD == "workspace.hunk_get_all_file_contents"
        # Two methods without the ``hunk_`` prefix (grok source verbatim).
        assert HunkGetAllHunksReq.METHOD == "workspace.get_all_hunks"
        assert HunkGetSessionSummaryReq.METHOD == "workspace.get_session_summary"

    # -- Response ClassVar shapes -------------------------------------------

    def test_typed_responses(self):
        assert HunkSingleActionReq.Response is HunkActionResponse
        assert HunkFileActionReq.Response is BulkHunkActionResponse
        assert HunkTurnActionReq.Response is BulkHunkActionResponse
        assert HunkAllActionReq.Response is BulkHunkActionResponse
        assert HunkGetFilteredHunksReq.Response is FilteredHunksResponse
        assert HunkGetSessionSummaryReq.Response is SessionSummaryWire

    def test_list_responses(self):
        # bare-list Response types surfaced as list[...].
        assert HunkGetStagedFilesReq.Response == list[str]
        assert HunkGetFileSummariesReq.Response == list[FileSummary]
        assert HunkGetAllHunksReq.Response == list[HunkWire]
        assert HunkGetAllFileContentsReq.Response == list[FileContentEntryWire]

    # -- HunkActionKind lowercase (no #[default]) ---------------------------

    def test_hunk_action_kind_lowercase(self):
        # grok hunk_action_kind_lowercase: #[serde(rename_all = "lowercase")].
        assert HunkActionKind.ACCEPT == "accept"
        assert HunkActionKind.REJECT == "reject"

    def test_hunk_action_kind_from_str_round_trip(self):
        assert HunkActionKind("accept") is HunkActionKind.ACCEPT
        assert HunkActionKind("reject") is HunkActionKind.REJECT
        with pytest.raises(ValueError):  # mirrors grok Err(())
            HunkActionKind("bogus")

    def test_hunk_action_kind_has_no_default(self):
        # Unlike R75 WorktreeType/ApplyMode there is no #[default] on this enum.
        assert not hasattr(HunkActionKind, "default")

    # -- HunkSourceWire forward-tolerant tagged enum (#[serde(other)]) -------

    def test_hunk_source_wire_emits_prompt_index_when_set(self):
        wire = HunkSourceWire(type="agentEdit", prompt_index=3).to_wire()
        # Flat model: no discriminator nesting; type at top level.
        assert wire["type"] == "agentEdit"
        assert wire["prompt_index"] == 3

    def test_hunk_source_wire_omits_prompt_index_when_none(self):
        wire = HunkSourceWire(type="external").to_wire()
        assert wire["type"] == "external"
        assert "prompt_index" not in wire  # omitted when None

    def test_hunk_source_wire_forward_tolerant_unknown_tag(self):
        # grok hunk_source_wire_unknown: #[serde(other)] → Unknown catches it.
        # Flat-model equivalent: any tag decodes and round-trips verbatim.
        wire = HunkSourceWire(type="futureSource").to_wire()
        assert wire["type"] == "futureSource"
        assert "prompt_index" not in wire

    # -- FileContentStatusWire hand-written forward-tolerant string enum ----

    def test_file_content_status_wire_known_values(self):
        assert FileContentStatusWire.MISSING == "missing"
        assert FileContentStatusWire.BINARY == "binary"
        assert FileContentStatusWire.TOO_LARGE == "tooLarge"
        assert FileContentStatusWire.LFS_POINTER == "lfsPointer"
        assert FileContentStatusWire.SYMLINK == "symlink"
        assert FileContentStatusWire.FULL == "full"
        assert FileContentStatusWire.UNKNOWN == "unknown"

    def test_file_content_status_wire_missing_default(self):
        # grok #[default] Missing → default() returns MISSING.
        assert FileContentStatusWire.default() is FileContentStatusWire.MISSING

    def test_file_content_status_wire_known_decode(self):
        adapter = TypeAdapter(FileContentStatusWire)
        assert adapter.validate_python("missing") is FileContentStatusWire.MISSING
        assert adapter.validate_python("tooLarge") is FileContentStatusWire.TOO_LARGE

    def test_file_content_status_wire_unknown_decode_routes_to_unknown(self):
        # grok file_content_status_wire_unknown: hand-written Deserialize →
        # Unknown instead of failing the whole structured response.
        adapter = TypeAdapter(FileContentStatusWire)
        assert adapter.validate_python("bogus") is FileContentStatusWire.UNKNOWN

    def test_file_content_status_wire_serialises_member_value(self):
        adapter = TypeAdapter(FileContentStatusWire)
        assert adapter.dump_python(FileContentStatusWire.TOO_LARGE) == "tooLarge"
        assert adapter.dump_python(FileContentStatusWire.UNKNOWN) == "unknown"

    # -- HunkWire round trip (camelCase + DateTime<Utc> Z + PathBuf → str) ---

    def test_hunk_wire_round_trip(self):
        # grok hunk_wire_round_trips: Z-suffixed created_at, snake_case null Options.
        hunk = HunkWire(
            id="h1",
            path="/src/a.txt",
            line_info=HunkLineInfoWire(old_start=1, old_count=2, new_start=1, new_count=3),
            source=HunkSourceWire(type="agentEdit", prompt_index=0),
            old_text="old\n",
            new_text="new\n",
            patch=None,
            created_at=datetime(2026, 6, 23, tzinfo=UTC),
        )
        wire = hunk.to_wire()
        # PathBuf → bare str.
        assert wire["path"] == "/src/a.txt"
        # DateTime<Utc> → RFC 3339 with Z suffix (chrono-compatible).
        assert wire["createdAt"] == "2026-06-23T00:00:00Z"
        # Option<String> with no skip_serializing_if → null kept.
        assert wire["oldText"] == "old\n"
        assert wire["patch"] is None
        # Nested camelCase structs.
        assert wire["lineInfo"]["oldStart"] == 1
        assert wire["lineInfo"]["newCount"] == 3
        assert wire["source"]["type"] == "agentEdit"
        # prompt_index is a struct-variant field: rename_all="camelCase" renames
        # the variant *name* (AgentEdit→agentEdit) but NOT variant *fields*, so
        # it stays snake_case on the wire (grok source L111-114 comment).
        assert wire["source"]["prompt_index"] == 0

    def test_hunk_wire_microsecond_created_at(self):
        hunk = HunkWire(
            id="h2",
            path="b.txt",
            line_info=HunkLineInfoWire(old_start=0, old_count=0, new_start=0, new_count=0),
            source=HunkSourceWire(type="external"),
            new_text="x",
            created_at=datetime(2026, 6, 23, 12, 30, 45, 500000, tzinfo=UTC),
        )
        # Microsecond form: fractional seconds, trailing zeros stripped.
        assert hunk.to_wire()["createdAt"] == "2026-06-23T12:30:45.5Z"

    # -- FileContentViewWire mixed skip matrix (status always, others skip) --

    def test_file_content_view_wire_omits_none_byte_len_and_content(self):
        # grok file_content_entry_wire_omits None status: status always emitted,
        # byteLen/content skip_serializing_if Option::is_none.
        wire = FileContentViewWire(status=FileContentStatusWire.FULL).to_wire()
        assert wire["status"] == "full"
        assert "byteLen" not in wire
        assert "content" not in wire

    def test_file_content_view_wire_emits_byte_len_and_content_when_set(self):
        wire = FileContentViewWire(
            status=FileContentStatusWire.FULL, byte_len=42, content="hi"
        ).to_wire()
        assert wire["byteLen"] == 42
        assert wire["content"] == "hi"

    def test_file_content_view_wire_baseline_missing_omits_fields(self):
        # Missing status → no byteLen/content emitted (baseline of a new file).
        wire = FileContentViewWire(status=FileContentStatusWire.MISSING).to_wire()
        assert wire["status"] == "missing"
        assert "byteLen" not in wire

    # -- FileContentEntryWire round trip (nested mixed skip) ----------------

    def test_file_content_entry_wire_round_trip(self):
        entry = FileContentEntryWire(
            path="/src/a.txt",
            baseline=FileContentViewWire(status=FileContentStatusWire.MISSING),
            current=FileContentViewWire(
                status=FileContentStatusWire.FULL, byte_len=3, content="new"
            ),
            is_agent_file=True,
            staged=False,
        )
        wire = entry.to_wire()
        assert wire["path"] == "/src/a.txt"
        assert wire["isAgentFile"] is True
        assert wire["staged"] is False
        # baseline: missing status, byteLen/content omitted.
        assert wire["baseline"]["status"] == "missing"
        assert "content" not in wire["baseline"]
        # current: full status with content.
        assert wire["current"]["status"] == "full"
        assert wire["current"]["byteLen"] == 3
        assert wire["current"]["content"] == "new"

    # -- camelCase summary structs ------------------------------------------

    def test_session_stats_wire_camel_case(self):
        stats = SessionStatsWire(
            accepted_hunks=1, rejected_hunks=2,
            accepted_lines_added=10, accepted_lines_removed=5,
            rejected_lines_added=3, rejected_lines_removed=1,
        )
        wire = stats.to_wire()
        assert wire["acceptedHunks"] == 1
        assert wire["acceptedLinesAdded"] == 10
        assert wire["rejectedLinesRemoved"] == 1

    def test_turn_summary_wire_camel_case_with_files(self):
        turn = TurnSummaryWire(
            prompt_index=0, files=["/a.txt", "/b.txt"],
            pending_hunks=[], lines_added=4, lines_removed=2,
        )
        wire = turn.to_wire()
        assert wire["promptIndex"] == 0
        # Vec<PathBuf> → list[str].
        assert wire["files"] == ["/a.txt", "/b.txt"]
        assert wire["linesAdded"] == 4

    # -- snake_case response / request types --------------------------------

    def test_bulk_hunk_action_response_default_empty_vec(self):
        # derive Default + no skip_serializing_if → affected: [] kept.
        assert BulkHunkActionResponse().to_wire() == {"affected": []}

    def test_filtered_hunks_response_default(self):
        wire = FilteredHunksResponse().to_wire()
        assert wire["hunks"] == []
        assert wire["total"] == 0

    def test_file_summary_snake_case(self):
        wire = FileSummary(path="a.txt", hunk_count=3, is_agent_file=True).to_wire()
        assert wire == {"path": "a.txt", "hunk_count": 3, "is_agent_file": True}

    def test_hunk_single_action_req_nested_snake_case(self):
        req = HunkSingleActionReq(
            action=HunkActionReq(hunk_id="h1", action=HunkActionKind.ACCEPT)
        )
        wire = req.to_wire()
        assert wire["action"]["hunk_id"] == "h1"
        assert wire["action"]["action"] == "accept"

    def test_hunk_get_filtered_hunks_req_defaults_null(self):
        # #[serde(default)] on both Options + no skip → null kept.
        wire = HunkGetFilteredHunksReq().to_wire()
        assert wire["path"] is None
        assert wire["source"] is None

    # -- empty-struct RPCs default to {} ------------------------------------

    def test_empty_struct_requests(self):
        assert HunkGetStagedFilesReq().to_wire() == {}
        assert HunkGetFileSummariesReq().to_wire() == {}
        assert HunkGetAllHunksReq().to_wire() == {}
        assert HunkGetAllFileContentsReq().to_wire() == {}
        assert HunkGetSessionSummaryReq().to_wire() == {}


class TestFs:
    """R77: workspace file I/O — put_files/get_files + fs_* + client_fs_*.

    Pins the serde shape of grok's ``xai-grok-workspace-types::rpc::fs``
    — the 10 RPC method constants, the generic ``skip_serializing_if =
    "Option::is_none"`` base (first generic None-elision base in the layer,
    supplanting R74/R75/R76's per-class pop lists), ``rename = "type"`` on a
    ``String`` (the ``str`` analogue of R74/R75's enum override), the
    ``Response = ()`` unit type, Req-snake / Res-camelCase asymmetry, and
    ``u64`` / ``i64`` / ``usize`` / ``u32`` → ``int`` (``mtime_ms`` epoch
    millis distinct from ``modified_at`` RFC 3339). Round-trip assertions use
    key-indexed access (:meth:`WireModel.to_wire` lexically sorts keys).
    """

    # -- method constants (grok method_constants × 10) -----------------------

    def test_method_constants(self):
        assert PutFilesReq.METHOD == "workspace.put_files"
        assert GetFilesReq.METHOD == "workspace.get_files"
        assert FsListReq.METHOD == "workspace.fs_list"
        assert FsExistsReq.METHOD == "workspace.fs_exists"
        assert FsReadFileReq.METHOD == "workspace.fs_read_file"
        assert FsWriteFileReq.METHOD == "workspace.fs_write_file"
        assert FsDeleteFileReq.METHOD == "workspace.fs_delete_file"

    def test_client_fs_method_constants(self):
        # grok exposes these as standalone CLIENT_FS_*_METHOD consts (source L322-327).
        assert CLIENT_FS_LIST_METHOD == "workspace.client_fs_list"
        assert CLIENT_FS_STAT_METHOD == "workspace.client_fs_stat"
        assert CLIENT_FS_READ_FILE_METHOD == "workspace.client_fs_read_file"
        assert ClientFsListReq.METHOD == CLIENT_FS_LIST_METHOD
        assert ClientFsStatReq.METHOD == CLIENT_FS_STAT_METHOD
        assert ClientFsReadFileReq.METHOD == CLIENT_FS_READ_FILE_METHOD

    # -- Response ClassVar shapes -------------------------------------------

    def test_typed_responses(self):
        assert PutFilesReq.Response is PutFilesRes
        assert GetFilesReq.Response is GetFilesRes
        assert FsListReq.Response is FsListData
        assert FsExistsReq.Response is FsExistsData
        assert FsReadFileReq.Response is FsReadFileData
        assert ClientFsListReq.Response is ClientFsListRes
        assert ClientFsStatReq.Response is ClientFsStatRes
        assert ClientFsReadFileReq.Response is ClientFsReadFileRes

    def test_unit_responses(self):
        # grok ``type Response = ()`` → type(None). Server acks with no payload.
        assert FsWriteFileReq.Response is type(None)
        assert FsDeleteFileReq.Response is type(None)

    # -- enums (lowercase rename_all) ---------------------------------------

    def test_fs_node_type_lowercase_no_default(self):
        assert FsNodeType.DIRECTORY == "directory"
        assert FsNodeType.FILE == "file"
        assert not hasattr(FsNodeType, "default")  # no #[default]

    def test_fs_read_encoding_default_utf8(self):
        assert FsReadEncoding.UTF8 == "utf8"
        assert FsReadEncoding.BASE64 == "base64"
        assert FsReadEncoding.default() is FsReadEncoding.UTF8

    def test_fs_content_type_lowercase_no_default(self):
        assert FsContentType.TEXT == "text"
        assert FsContentType.BINARY == "binary"
        assert not hasattr(FsContentType, "default")

    # -- service-level put/get (snake_case) ---------------------------------

    def test_put_file_entry_defaults(self):
        wire = PutFileEntry(path="a.txt", content="hi").to_wire()
        assert wire["create_dirs"] is True  # default_true
        assert wire["append"] is False  # #[serde(default)] → False

    def test_put_file_result_drops_none(self):
        # _DropNoneWire pops error/hash when None (inherited wrap serializer).
        wire = PutFileResult(path="a.txt", ok=True).to_wire()
        assert wire == {"path": "a.txt", "ok": True}
        full = PutFileResult(path="a.txt", ok=True, error="boom", hash="abc").to_wire()
        assert full["error"] == "boom"
        assert full["hash"] == "abc"

    def test_get_file_entry_drops_none(self):
        wire = GetFileEntry(path="a.txt").to_wire()
        assert wire == {"path": "a.txt"}
        full = GetFileEntry(path="a.txt", if_none_match="v1", offset=0, length=10).to_wire()
        assert full["if_none_match"] == "v1"
        assert full["offset"] == 0
        assert full["length"] == 10

    def test_get_file_result_matched_always_emitted(self):
        # matched is a non-Option bool with #[serde(default)] → always emitted.
        wire = GetFileResult(path="a.txt", exists=True).to_wire()
        assert wire == {"path": "a.txt", "exists": True, "matched": False}

    # -- fs_* requests (snake_case, cwd emits null) -------------------------

    def test_fs_list_req_defaults_apply(self):
        # grok fs_list_req_defaults_apply.
        wire = FsListReq(path=".").to_wire()
        assert wire["cwd"] is None  # #[serde(default)] → null kept (not DropNone)
        assert wire["depth"] == 1  # default_depth
        assert wire["limit"] == 1000  # default_limit
        assert wire["offset"] == 0
        assert wire["include_hidden"] is True  # default_true
        assert wire["follow_symlinks"] is True
        assert wire["respect_git_ignore"] is True
        assert wire["include_globs"] == []
        assert wire["exclude_globs"] == []

    def test_fs_read_file_req_defaults_are_legacy_full_read(self):
        # grok fs_read_file_req_defaults_are_legacy_full_read.
        wire = FsReadFileReq(path="a.txt").to_wire()
        assert wire["cwd"] is None
        assert wire["offset"] is None  # None → null (no skip)
        assert wire["length"] is None
        assert wire["max_bytes"] == 1_048_576  # default_max_bytes
        assert wire["encoding"] == "utf8"  # FsReadEncoding::default()

    def test_fs_write_file_req_defaults(self):
        wire = FsWriteFileReq(path="a.txt", content="hi").to_wire()
        assert wire["cwd"] is None
        assert wire["create_dirs"] is True

    def test_fs_exists_req_cwd_null(self):
        wire = FsExistsReq(path=".").to_wire()
        assert wire == {"path": ".", "cwd": None}

    def test_fs_delete_file_req_shape(self):
        wire = FsDeleteFileReq(path="a.txt").to_wire()
        assert wire == {"path": "a.txt", "cwd": None}

    # -- fs_* responses (FsListData snake; FsListNode/FsExistsData/FsReadFileData camel) --

    def test_fs_list_node_renames_type_key(self):
        # grok fs_list_node_renames_type_key. node_type is String rename="type".
        node = FsListNode(
            name="a", path="/a", node_type="file", is_symlink=None, size=1, modified_at=None
        )
        wire = node.to_wire()
        assert wire["type"] == "file"  # rename="type" on a String
        assert wire["size"] == 1
        assert "isSymlink" not in wire  # None dropped (camelCase key)
        assert "modifiedAt" not in wire

    def test_fs_list_node_false_kept_none_dropped(self):
        node = FsListNode(
            name="d",
            path="/d",
            node_type="directory",
            is_symlink=False,
            size=None,
            modified_at="2026-01-01T00:00:00Z",
        )
        wire = node.to_wire()
        assert wire["isSymlink"] is False  # False kept (not None)
        assert "size" not in wire  # None dropped
        assert wire["modifiedAt"] == "2026-01-01T00:00:00Z"

    def test_fs_list_data_snake_case_envelope(self):
        # Outer envelope is snake_case despite camelCase FsListNode children.
        data = FsListData(
            nodes=[FsListNode(name="a", path="/a", node_type="file")], truncated=False
        )
        wire = data.to_wire()
        assert "nodes" in wire  # snake_case key
        assert "truncated" in wire  # snake_case key
        assert wire["nodes"][0]["type"] == "file"

    def test_fs_exists_data_camel_case(self):
        assert FsExistsData(exists=False).to_wire() == {"exists": False}

    def test_fs_read_file_data_renames_type_key(self):
        # content_type is String rename="type"; content_base64/line_count dropped when None.
        wire = FsReadFileData(content="hi", size=2, content_type="text").to_wire()
        assert wire["type"] == "text"
        assert wire["size"] == 2
        assert "contentBase64" not in wire
        assert "lineCount" not in wire

    # -- client_fs_* (camelCase both sides) ---------------------------------

    def test_client_fs_list_req_defaults(self):
        # grok client_fs_wire_stability_snapshot (minimal decode). camelCase wire.
        wire = ClientFsListReq(path="docs").to_wire()
        assert wire["depth"] == 1  # default_client_depth
        assert wire["includeHidden"] is True
        assert wire["limit"] == 1000  # default_client_limit
        assert wire["offset"] == 0
        assert wire["followSymlinks"] is True
        assert wire["respectGitIgnore"] is True
        assert wire["includeGlobs"] == []
        assert wire["excludeGlobs"] == []

    def test_client_fs_list_req_fully_populated_round_trip(self):
        req = ClientFsListReq(
            path="docs",
            depth=2,
            include_hidden=False,
            limit=100,
            offset=200,
            follow_symlinks=False,
            respect_git_ignore=False,
            include_globs=["*.md"],
            exclude_globs=[".git"],
        )
        wire = req.to_wire()
        assert wire["includeGlobs"] == ["*.md"]
        assert wire["excludeGlobs"] == [".git"]
        back = ClientFsListReq.model_validate(wire)
        assert back.depth == 2
        assert back.include_hidden is False
        assert back.offset == 200
        assert back.include_globs == ["*.md"]

    def test_client_fs_list_node_enum_type(self):
        # node_type is the FsNodeType enum with rename="type" (cf. FsListNode's String).
        node = ClientFsListNode(
            name="a.txt",
            path="docs/a.txt",
            node_type=FsNodeType.FILE,
            is_symlink=True,
            size=11,
            mtime_ms=1_700_000_000_000,
        )
        wire = node.to_wire()
        assert wire["type"] == "file"  # enum value via rename="type"
        assert wire["mtimeMs"] == 1_700_000_000_000
        assert wire["isSymlink"] is True

    def test_client_fs_list_res_nested(self):
        res = ClientFsListRes(
            nodes=[
                ClientFsListNode(
                    name="a.txt",
                    path="docs/a.txt",
                    node_type=FsNodeType.FILE,
                    mtime_ms=1_700_000_000_000,
                )
            ],
            truncated=True,
        )
        wire = res.to_wire()
        assert wire["nodes"][0]["mtimeMs"] == 1_700_000_000_000
        assert wire["truncated"] is True

    def test_client_fs_stat_req_minimal(self):
        assert ClientFsStatReq(path="a.txt").to_wire() == {"path": "a.txt"}

    def test_client_fs_stat_res_missing(self):
        # grok client_fs_wire_stability_snapshot (missing). All Options dropped.
        wire = ClientFsStatRes(exists=False).to_wire()
        assert wire == {"exists": False}

    def test_client_fs_stat_res_full(self):
        res = ClientFsStatRes(
            exists=True,
            node_type=FsNodeType.FILE,
            size=5,
            mtime_ms=1_700_000_000_000,
            hash="abc",
        )
        wire = res.to_wire()
        assert wire["nodeType"] == "file"  # ClientFsStatRes.node_type has NO rename="type"
        assert wire["size"] == 5
        assert wire["mtimeMs"] == 1_700_000_000_000
        assert wire["hash"] == "abc"

    def test_client_fs_read_file_req_defaults(self):
        wire = ClientFsReadFileReq(path="a.bin").to_wire()
        assert wire["offset"] is None  # None → null (no skip)
        assert wire["length"] is None
        assert wire["maxBytes"] == 1_048_576  # camelCase wire
        assert wire["encoding"] == "utf8"

    def test_client_fs_read_file_res_base64_binary(self):
        # grok client_fs_wire_stability_snapshot (binary read). content dropped when None.
        res = ClientFsReadFileRes(
            content=None,
            content_base64="aGVsbG8=",
            size=5,
            hash="abc123",
            content_type=FsContentType.BINARY,
        )
        wire = res.to_wire()
        assert wire["type"] == "binary"
        assert wire["contentBase64"] == "aGVsbG8="
        assert wire["size"] == 5
        assert wire["hash"] == "abc123"
        assert "content" not in wire

    def test_client_fs_read_file_res_text(self):
        res = ClientFsReadFileRes(content="hi", size=2, hash="abc", content_type=FsContentType.TEXT)
        wire = res.to_wire()
        assert wire["type"] == "text"
        assert wire["content"] == "hi"
        assert "contentBase64" not in wire

