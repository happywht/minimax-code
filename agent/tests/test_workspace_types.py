"""Round-trip + wire-fidelity tests for ``workspace_types`` (R67).

Verifies the four hardest-to-get-right wire contracts:

1. **Adjacent tagging** — every tagged enum emits ``{"type", "data"}``
   with the snake_case variant name; unit variants carry ``data: null``,
   newtype variants carry the bare value, struct variants carry a dict.
2. **RFC-4648 base64** — ``ToolOutputChunk.bytes`` round-trips through a
   standard base64 string (not a JSON int array).
3. **BTreeMap ordering** — ``Metadata`` / ``ProjectConfig.values`` /
   config ``args`` / ``extra_env`` emit sorted keys on the wire.
4. **Display vs wire** — ``ChunkKind`` serialises as snake_case but
   ``WorkspaceError.__str__`` re-expands it to the PascalCase Display
   form (the ``protocol mismatch: ... got Ack`` contract).

Plus exhaustive ``to_wire`` round-trips for every public struct/enum.
"""

from __future__ import annotations

import base64
import json
import warnings
from datetime import UTC, datetime

import pytest

from minimax_code.workspace_types import (
    META_PROMPT_INDEX,
    META_SESSION_ID,
    META_TRACEPARENT,
    STANDARD_META_KEYS,
    AgentSessionConfig,
    AgentSessionInfo,
    CapabilityMode,
    ChunkKind,
    ContentMatch,
    EventLag,
    FileReference,
    FsEventKind,
    FuzzyMatch,
    FuzzySearchArgs,
    GitBranchInfo,
    GitDiff,
    GitDiffArgs,
    GitMetadata,
    GitStatus,
    GitStatusOpts,
    HookInfo,
    Hunk,
    HunkAction,
    HunkId,
    IoKind,
    IsolationMode,
    LspServerStatus,
    MatchSpan,
    McpServerStatus,
    MemoryChunk,
    Metadata,
    PermissionDecision,
    PermissionPolicy,
    PermissionRequest,
    PlanModeDecision,
    PlanModeTransition,
    PluginInfo,
    ProjectConfig,
    RequestMessage,
    ResolvedFile,
    RewindPoint,
    RewindResult,
    RipgrepArgs,
    RipgrepStats,
    ServerStatus,
    SessionId,
    SessionLifecycleRequest,
    SkillInfo,
    ToolCallArgs,
    ToolCallId,
    ToolCallResult,
    ToolDef,
    ToolOutputChunk,
    ToolProgress,
    ToolRequest,
    ToolServerConfig,
    UserAnswer,
    UserQuestion,
    UserQuestionOption,
    VcsKind,
    WorkspaceError,
    WorkspaceEvent,
    WorkspaceOpsRequest,
    WorkspaceRequest,
    WorkspaceTopic,
    WorkspaceTopicSet,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


# --------------------------------------------------------------------- helpers


def _round_trip(model):
    """to_wire -> JSON text -> model_validate_json -> to_wire; returns both wires.

    Uses ``model_validate_json`` (not ``model_validate``) so the ``bytes``
    field round-trips via base64 (JSON mode) rather than being UTF-8
    re-encoded from the base64 string.
    """
    wire1 = model.to_wire()
    text = json.dumps(wire1, sort_keys=True)
    rebuilt = type(model).model_validate_json(text)
    return wire1, rebuilt.to_wire()


# --------------------------------------------------------------------- identity


def test_session_id_serialises_as_bare_string():
    sid = SessionId("s-123")
    assert json.dumps(sid) == '"s-123"'
    assert repr(sid) == "SessionId('s-123')"
    assert sid == "s-123"  # behaves as str


def test_identity_newtypes_are_distinct_str_subclasses():
    assert isinstance(SessionId("x"), str)
    assert isinstance(ToolCallId("x"), str)
    assert isinstance(HunkId("x"), str)
    # transparent serialisation — no wrapper object
    assert json.dumps(ToolCallId("c1")) == '"c1"'
    assert json.dumps(HunkId("h1")) == '"h1"'


# --------------------------------------------------------------------- metadata


def test_metadata_to_wire_sorts_keys_btreemap_order():
    md = Metadata()
    md.insert(META_PROMPT_INDEX, "3")
    md.insert(META_SESSION_ID, "s1")
    md.insert(META_TRACEPARENT, "tp")
    wire = md.to_wire()
    assert list(wire.keys()) == sorted(wire.keys())
    # lexicographic order: "traceparent" < "x-workspace-prompt-index"
    # < "x-workspace-session-id" — BTreeMap sorts by raw key bytes.
    assert list(wire.keys()) == [
        META_TRACEPARENT,
        META_PROMPT_INDEX,
        META_SESSION_ID,
    ]


def test_metadata_remove_and_contains_key():
    md = Metadata({"a": "1"})
    assert md.contains_key("a")
    assert md.remove("a") == "1"
    assert md.remove("a") is None
    assert not md.contains_key("a")


def test_standard_meta_keys_constant():
    assert META_SESSION_ID in STANDARD_META_KEYS
    assert META_PROMPT_INDEX in STANDARD_META_KEYS
    assert len(STANDARD_META_KEYS) == 6


# --------------------------------------------------------------------- ChunkKind


def test_chunk_kind_has_29_variants():
    assert len(ChunkKind.all()) == 29


def test_chunk_kind_wire_value_is_snake_case():
    assert ChunkKind.ToolOutput.value == "tool_output"
    assert ChunkKind.GitStatus.value == "git_status"
    assert ChunkKind.SessionAck.value == "session_ack"


def test_chunk_kind_as_str_is_pascalcase_display():
    assert ChunkKind.ToolOutput.as_str() == "ToolOutput"
    assert ChunkKind.Ack.as_str() == "Ack"
    assert ChunkKind.GitStatus.as_str() == "GitStatus"


# --------------------------------------------------------------------- IoKind


def test_iokind_has_39_variants():
    # declaration-order check via the transient/non-transient split
    transient = {k for k in IoKind if k.is_transient()}
    assert len(transient) == 12
    assert len(list(IoKind)) == 39


@pytest.mark.parametrize(
    "kind",
    [
        IoKind.BrokenPipe,
        IoKind.ConnectionReset,
        IoKind.ConnectionAborted,
        IoKind.ConnectionRefused,
        IoKind.TimedOut,
        IoKind.Interrupted,
        IoKind.WouldBlock,
        IoKind.HostUnreachable,
        IoKind.NetworkUnreachable,
        IoKind.NetworkDown,
        IoKind.ResourceBusy,
        IoKind.Deadlock,
    ],
)
def test_iokind_transient_kinds_are_retryable(kind):
    assert kind.is_transient() is True


@pytest.mark.parametrize(
    "kind", [IoKind.NotFound, IoKind.PermissionDenied, IoKind.InvalidInput, IoKind.Other]
)
def test_iokind_non_transient_kinds(kind):
    assert kind.is_transient() is False


# --------------------------------------------------------------------- WorkspaceError


def test_workspace_error_io_factory_shape():
    err = WorkspaceError.io("disk full", IoKind.StorageFull)
    assert err.to_wire() == {
        "type": "io",
        "data": {"message": "disk full", "kind": "storage_full"},
    }
    assert err.kind == "io"


def test_workspace_error_unit_variants_carry_null_data():
    assert WorkspaceError.cancelled().to_wire() == {"type": "cancelled", "data": None}
    assert WorkspaceError.empty_stream().to_wire() == {"type": "empty_stream", "data": None}


def test_workspace_error_newtype_variants_carry_bare_string():
    assert WorkspaceError.vcs("bad head").to_wire() == {"type": "vcs", "data": "bad head"}
    assert WorkspaceError.not_found("x").to_wire() == {"type": "not_found", "data": "x"}


def test_workspace_error_retryable_predicates():
    assert WorkspaceError.timeout(100).is_retryable() is True
    assert WorkspaceError.remote("net").is_retryable() is True
    # transient io kind -> retryable
    assert WorkspaceError.io("x", IoKind.BrokenPipe).is_retryable() is True
    # non-transient io kind -> not retryable
    assert WorkspaceError.io("x", IoKind.NotFound).is_retryable() is False
    # vcs / permission -> never retryable
    assert WorkspaceError.vcs("x").is_retryable() is False
    assert WorkspaceError.permission("x").is_retryable() is False


def test_workspace_error_is_cancelled():
    assert WorkspaceError.cancelled().is_cancelled() is True
    assert WorkspaceError.timeout(1).is_cancelled() is False


def test_workspace_error_protocol_mismatch_uses_chunk_kind_display():
    # The Rust test pins: "protocol mismatch: expected GitStatus, got Ack"
    # — {got} uses ChunkKind's Display (PascalCase), not the wire snake_case.
    err = WorkspaceError.protocol_mismatch("GitStatus", ChunkKind.Ack)
    assert str(err) == "protocol mismatch: expected GitStatus, got Ack"
    # wire still stores the snake_case value
    assert err.to_wire()["data"]["got"] == "ack"


def test_workspace_error_display_templates():
    assert str(WorkspaceError.timeout(250)) == "deadline exceeded after 250ms"
    assert str(WorkspaceError.permission("nope")) == "permission denied: nope"
    assert str(WorkspaceError.session_not_found("s1")) == "session not found: s1"
    assert str(WorkspaceError.tool("E1", "boom")) == "tool error [E1]: boom"
    assert str(WorkspaceError.internal("oops")) == "internal: oops"
    assert str(WorkspaceError.cancelled()) == "cancelled"


def test_workspace_error_from_wire_round_trips():
    err = WorkspaceError.vcs("bad")
    wire = err.to_wire()
    back = WorkspaceError.from_wire(wire)
    assert back.to_wire() == wire
    assert back.kind == "vcs"


# --------------------------------------------------------------------- AdjacentTagged


def test_adjacent_tagged_rejects_unknown_variant():
    with pytest.raises(ValueError):
        WorkspaceError("nonsense", None)


def test_adjacent_tagged_from_wire_rejects_missing_keys():
    with pytest.raises(ValueError):
        WorkspaceError.from_wire({"type": "io"})  # no "data"
    with pytest.raises(ValueError):
        WorkspaceError.from_wire("not a dict")


def test_adjacent_tagged_equality_and_hash():
    a = UserAnswer.selected("x")
    b = UserAnswer.selected("x")
    c = UserAnswer.other("x")
    assert a == b
    assert a != c
    assert hash(a) == hash(b)
    assert repr(a) == "UserAnswer.selected('x')"


# --------------------------------------------------------------------- tools


def test_tool_output_chunk_bytes_round_trip_base64():
    payload = bytes(range(256))
    chunk = ToolOutputChunk(
        call_id=ToolCallId("c1"), stream="stdout", bytes=payload, at=_EPOCH
    )
    wire = chunk.to_wire()
    # RFC 4648 base64 string, not a JSON int array
    assert isinstance(wire["bytes"], str)
    assert base64.b64decode(wire["bytes"]) == payload
    # full round-trip
    wire1, wire2 = _round_trip(chunk)
    assert wire1 == wire2
    assert base64.b64decode(wire2["bytes"]) == payload


def test_tool_output_chunk_at_defaults_to_epoch():
    chunk = ToolOutputChunk(call_id=ToolCallId("c1"))
    assert chunk.at == _EPOCH
    # omitted `at` on the wire deserialises to epoch (not now())
    back = ToolOutputChunk.model_validate({"call_id": "c1"})
    assert back.at == _EPOCH


def test_tool_output_chunk_uses_snake_case_field_names():
    chunk = ToolOutputChunk(call_id=ToolCallId("c1"), bytes=b"hi")
    wire = chunk.to_wire()
    assert "call_id" in wire
    assert "callId" not in wire


def test_tool_progress_adjacent_tagged_variants():
    assert ToolProgress.started("c1").to_wire() == {
        "type": "started",
        "data": {"call_id": "c1"},
    }
    assert ToolProgress.status("c1", "installing").to_wire() == {
        "type": "status",
        "data": {"call_id": "c1", "message": "installing"},
    }
    pct = ToolProgress.percent("c1", 0.5)
    assert pct.to_wire() == {"type": "percent", "data": {"call_id": "c1", "fraction": 0.5}}


def test_tool_call_result_and_def_round_trip():
    _round_trip(ToolCallResult(call_id=ToolCallId("c1"), exit_code=2, summary="done"))
    _round_trip(ToolDef(name="read_file", requires_permission=True))


# --------------------------------------------------------------------- config


def test_isolation_and_capability_defaults():
    assert IsolationMode.default() is IsolationMode.None_
    assert IsolationMode.None_.value == "none"
    assert CapabilityMode.default() is CapabilityMode.ReadWrite
    assert CapabilityMode.ReadWrite.value == "read_write"


def test_btreemap_fields_sorted_on_wire():
    # ToolServerConfig.args is a BTreeMap — keys sort on the wire.
    tsc = ToolServerConfig(id="mcp", args={"z": "1", "a": "2"})
    assert list(tsc.to_wire()["args"].keys()) == ["a", "z"]
    # AgentSessionConfig.extra_env likewise.
    sess = AgentSessionConfig(agent_id="a1", extra_env={"ZED": "1", "ALPHA": "2"})
    assert list(sess.to_wire()["extra_env"].keys()) == ["ALPHA", "ZED"]
    # Round-trips with a nested tool-server entry whose args also sort.
    _round_trip(
        AgentSessionConfig(
            agent_id="a1",
            tool_config=[ToolServerConfig(id="mcp", args={"z": "1", "a": "2"})],
        )
    )


def test_project_config_values_sorted_and_permission_policy_round_trip():
    pc = ProjectConfig(values={"b": "2", "a": "1"}, trusted=True)
    assert list(pc.to_wire()["values"].keys()) == ["a", "b"]
    _round_trip(PermissionPolicy(allow=["x"], deny=["y"], ask=["z"]))


# --------------------------------------------------------------------- git


def test_vcs_kind_default_and_git_structs_round_trip():
    assert VcsKind.default() is VcsKind.Git
    _round_trip(GitStatusOpts(include_untracked=True))
    _round_trip(GitStatus(branch="main", staged=["a.py"], clean=False))
    _round_trip(GitDiffArgs(range="HEAD~1", paths=["a.py"], staged=True))
    _round_trip(GitDiff(patch="@@ ", files=["a.py"]))
    _round_trip(GitBranchInfo(current="main", local=["main", "dev"], upstream="origin/main"))
    _round_trip(GitMetadata(origin_url="u", root="/r", default_branch="main"))


# --------------------------------------------------------------------- search


def test_search_structs_round_trip():
    _round_trip(RipgrepArgs(pattern="fn ", globs=["*.rs"], case_insensitive=True, max_matches=10))
    _round_trip(MatchSpan(start=0, end=3))
    _round_trip(ContentMatch(path="a.rs", line_number=4, line="fn main", spans=[MatchSpan(start=0, end=2)]))
    _round_trip(RipgrepStats(files_matched=3, lines_matched=9, truncated=False))
    _round_trip(FuzzySearchArgs(query="main", limit=5))
    _round_trip(FuzzyMatch(path="src/main.rs", score=-12, matched_indices=[0, 1]))


# --------------------------------------------------------------------- interaction


def test_user_question_option_preview_skipped_when_none():
    opt = UserQuestionOption(label="L", description="d")
    wire = opt.to_wire()
    assert "preview" not in wire  # skip_serializing_if
    assert wire == {"label": "L", "description": "d"}


def test_user_question_option_preview_present_when_set():
    opt = UserQuestionOption(label="L", preview="P")
    assert opt.to_wire()["preview"] == "P"


def test_user_question_recurses_option_to_wire_so_preview_skip_propagates():
    q = UserQuestion(question="q?", options=[UserQuestionOption(label="L")], multi_select=True)
    wire = q.to_wire()
    assert "preview" not in wire["options"][0]
    assert wire["multi_select"] is True


def test_user_answer_adjacent_tagged_variants():
    assert UserAnswer.selected("a").to_wire() == {"type": "selected", "data": "a"}
    assert UserAnswer.other("txt").to_wire() == {"type": "other", "data": "txt"}
    assert UserAnswer.multiple(["a", "b"]).to_wire() == {
        "type": "multiple",
        "data": ["a", "b"],
    }


# --------------------------------------------------------------------- session


def test_agent_session_info_defaults_and_round_trip():
    info = AgentSessionInfo(id=SessionId("s1"))
    assert info.isolation is IsolationMode.None_
    assert info.created_at == _EPOCH
    assert info.parent is None
    wire1, wire2 = _round_trip(info)
    assert wire1 == wire2


def test_rewind_structs_round_trip():
    _round_trip(RewindResult(session=SessionId("s1"), head_prompt_index=5, prompts_dropped=2))
    _round_trip(RewindPoint(prompt_index=3, at=_EPOCH, summary="before refactor"))


def test_fs_event_kind_and_server_status_defaults():
    assert FsEventKind.default() is FsEventKind.Modified
    assert ServerStatus.default() is ServerStatus.Running
    # aliases share identity
    assert LspServerStatus is ServerStatus
    assert McpServerStatus is ServerStatus


# --------------------------------------------------------------------- plan_mode


def test_plan_mode_transition_variants():
    assert PlanModeTransition.enter("draft").to_wire() == {
        "type": "enter",
        "data": {"plan": "draft"},
    }
    assert PlanModeTransition.exit(None).to_wire() == {
        "type": "exit",
        "data": {"final_plan": None},
    }


def test_plan_mode_decision_variants():
    assert PlanModeDecision.approve().to_wire() == {"type": "approve", "data": None}
    assert PlanModeDecision.defer().to_wire() == {"type": "defer", "data": None}
    assert PlanModeDecision.reject("nope").to_wire() == {
        "type": "reject",
        "data": {"feedback": "nope"},
    }


# --------------------------------------------------------------------- hunk


def test_hunk_round_trip_and_action_newtype_variants():
    _round_trip(Hunk(id=HunkId("h1"), path="a.rs", added=3, removed=1, start_line=10, summary="fix"))
    for variant in ("accept", "reject", "revert"):
        action = getattr(HunkAction, variant)(HunkId("h1"))
        assert action.to_wire() == {"type": variant, "data": "h1"}


# --------------------------------------------------------------------- permission


def test_permission_request_round_trip():
    _round_trip(
        PermissionRequest(
            tool_name="write_file", summary="overwrite", input_json='{"p":"x"}', destructive=True
        )
    )


def test_permission_decision_variants():
    for variant in ("allow_once", "allow_session", "allow_project"):
        dec = getattr(PermissionDecision, variant)()
        assert dec.to_wire() == {"type": variant, "data": None}
    assert PermissionDecision.deny("no").to_wire() == {"type": "deny", "data": {"reason": "no"}}
    assert PermissionDecision.deny().to_wire() == {"type": "deny", "data": {"reason": ""}}


# --------------------------------------------------------------------- plugins/files/skills/memory


def test_plugin_and_hook_info_round_trip():
    _round_trip(PluginInfo(id="p1", name="P", version="0.1.0", path="/p", source="global", enabled=True))
    _round_trip(HookInfo(id="pre-tool-call", name="Pre", event="PreToolUse", plugin_id="p1", enabled=True))


def test_files_structs_round_trip():
    _round_trip(FileReference(raw="@docs/x.md", absolute_path="/abs/x.md"))
    _round_trip(
        ResolvedFile(reference="@docs/x.md", path="/abs/x.md", resolved=True, preview="...", error=None)
    )


def test_skill_info_round_trip():
    _round_trip(SkillInfo(id="commit", display_name="Commit", description="d", path="/s", source="bundled"))


def test_memory_chunk_round_trip_with_optional_score():
    _round_trip(MemoryChunk(id="m1", content="body", source="/s", score=0.87))
    mc = MemoryChunk(id="m1")
    assert mc.score is None
    assert mc.source is None


# --------------------------------------------------------------------- RequestMessage (R78)


_WHEN = datetime(2026, 7, 19, 12, 0, 0, tzinfo=UTC)


class TestRequestMessage:
    """Round-trip / builder / map tests for the R78 request envelope.

    Mirrors grok's ``request.rs`` test set (string payload round-trip,
    deadline elision, deadline ``Z`` suffix, ``map`` preservation) plus
    the Python-specific schema contract: ``metadata`` is always emitted
    (even empty) and rewraps as :class:`Metadata` on read-back.
    """

    def test_round_trips_with_string_payload(self):
        r = RequestMessage[str].new("hello")
        wire = r.to_wire()
        assert wire == {"message": "hello", "metadata": {}}
        # deadline omitted when None (skip_serializing_if = "Option::is_none")
        assert "deadline" not in wire
        back = RequestMessage[str].model_validate(wire)
        assert back.message == "hello"
        assert isinstance(back.metadata, Metadata)

    def test_metadata_always_emitted_even_when_empty(self):
        # #[serde(default)] with NO skip_serializing_if -> empty map still emits.
        wire = RequestMessage[int].new(42).to_wire()
        assert wire["metadata"] == {}

    def test_metadata_round_trips_and_rewraps_as_metadata(self):
        meta = Metadata({META_SESSION_ID: "s1"})
        r = RequestMessage[str].new("hi").with_metadata(meta)
        wire = r.to_wire()
        assert wire["metadata"] == {"x-workspace-session-id": "s1"}
        assert isinstance(r.metadata, Metadata)
        back = RequestMessage[str].model_validate(wire)
        # read-back rewraps the dict as a Metadata (sorted-map methods preserved)
        assert isinstance(back.metadata, Metadata)
        assert back.metadata == {"x-workspace-session-id": "s1"}

    def test_round_trips_with_deadline_emits_z_suffix(self):
        r = RequestMessage[int].new(42).with_deadline(_WHEN)
        wire = r.to_wire()
        # chrono::DateTime<Utc> emits an RFC 3339 Z suffix
        assert wire["deadline"] == "2026-07-19T12:00:00Z"
        assert wire == {
            "deadline": "2026-07-19T12:00:00Z",
            "message": 42,
            "metadata": {},
        }
        back = RequestMessage[int].model_validate(wire)
        assert back.deadline == _WHEN
        assert back.message == 42

    def test_with_metadata_and_with_deadline_builders_mutate_in_place(self):
        r = RequestMessage[str].new("x")
        r2 = r.with_metadata(Metadata({"k": "v"}))
        r3 = r.with_deadline(_WHEN)
        # builders take `mut self` and return the same instance
        assert r is r2 is r3
        assert r.metadata == Metadata({"k": "v"})
        assert r.deadline == _WHEN

    def test_map_preserves_metadata_and_deadline(self):
        r = (
            RequestMessage[int]
            .new(1)
            .with_metadata(Metadata({"k": "v"}))
            .with_deadline(_WHEN)
        )
        mapped = r.map(lambda n: str(n))
        assert mapped.message == "1"
        assert mapped.metadata == Metadata({"k": "v"})
        assert isinstance(mapped.metadata, Metadata)
        assert mapped.deadline == _WHEN
        # source metadata is copied into a new Metadata (move, not alias)
        assert mapped.metadata is not r.metadata
        # wire re-dumps correctly with the new payload type
        assert mapped.to_wire() == {
            "deadline": "2026-07-19T12:00:00Z",
            "message": "1",
            "metadata": {"k": "v"},
        }

    def test_map_emits_no_serializer_warning(self):
        # map<U> in Python keeps an unparameterised schema (T = Any) via
        # model_construct, so to_wire does NOT warn about an int schema
        # receiving a str payload.
        r = RequestMessage[int].new(1)
        with warnings.catch_warnings():
            warnings.simplefilter("error", UserWarning)
            mapped = r.map(lambda n: str(n))
            wire = mapped.to_wire()  # would raise under simplefilter("error")
        assert wire["message"] == "1"

    def test_map_with_no_metadata_or_deadline_round_trips(self):
        r = RequestMessage[str].new("payload")
        mapped = r.map(str.upper)
        assert mapped.message == "PAYLOAD"
        wire = mapped.to_wire()
        assert wire == {"message": "PAYLOAD", "metadata": {}}
        assert "deadline" not in wire


# --------------------------------------------------------------------- requests (R79)


class TestToolCallArgs:
    """ToolCallArgs wire struct (R79)."""

    def test_round_trips_with_all_fields(self):
        args = ToolCallArgs(
            session=SessionId("s1"),
            tool_name="read_file",
            input_json='{"path": "/etc/hosts"}',
            call_id=ToolCallId("c1"),
        )
        wire1, wire2 = _round_trip(args)
        assert wire1 == wire2
        # BTreeMap-sorted keys (call_id < input_json < session < tool_name)
        assert wire1 == {
            "call_id": "c1",
            "input_json": '{"path": "/etc/hosts"}',
            "session": "s1",
            "tool_name": "read_file",
        }

    def test_input_json_defaults_to_empty_string(self):
        # #[serde(default)] -> always emitted, defaults to ""
        args = ToolCallArgs(session=SessionId("s1"), tool_name="t", call_id=ToolCallId("c"))
        wire = args.to_wire()
        assert wire["input_json"] == ""


class TestToolRequest:
    """ToolRequest adjacent-tagged enum (R79): call + definitions."""

    def test_call_variant_wraps_tool_call_args(self):
        args = ToolCallArgs(
            session=SessionId("s1"),
            tool_name="read_file",
            input_json="{}",
            call_id=ToolCallId("c1"),
        )
        req = ToolRequest.call(args)
        wire = req.to_wire()
        assert wire == {
            "type": "call",
            "data": {
                "call_id": "c1",
                "input_json": "{}",
                "session": "s1",
                "tool_name": "read_file",
            },
        }
        back = ToolRequest.from_wire(wire)
        assert back.to_wire() == wire
        assert back.kind == "call"

    def test_definitions_unit_variant(self):
        req = ToolRequest.definitions()
        assert req.to_wire() == {"type": "definitions", "data": None}
        back = ToolRequest.from_wire(req.to_wire())
        assert back.kind == "definitions"
        assert back.payload is None


class TestWorkspaceOpsRequest:
    """WorkspaceOpsRequest adjacent-tagged enum (R79, 18 variants)."""

    def test_unit_variants_emit_null_data(self):
        for factory, kind in [
            (WorkspaceOpsRequest.git_branch_info, "git_branch_info"),
            (WorkspaceOpsRequest.git_metadata, "git_metadata"),
            (WorkspaceOpsRequest.list_hunks, "list_hunks"),
            (WorkspaceOpsRequest.discover_skills, "discover_skills"),
            (WorkspaceOpsRequest.discover_plugins, "discover_plugins"),
            (WorkspaceOpsRequest.load_project_config, "load_project_config"),
            (WorkspaceOpsRequest.load_permissions, "load_permissions"),
            (WorkspaceOpsRequest.load_envrc, "load_envrc"),
            (WorkspaceOpsRequest.refresh_plugins, "refresh_plugins"),
        ]:
            req = factory()
            assert req.to_wire() == {"type": kind, "data": None}, kind
            back = WorkspaceOpsRequest.from_wire(req.to_wire())
            assert back.kind == kind

    def test_struct_newtype_variants_delegate_to_inner_to_wire(self):
        # WireModel-struct newtype variants: _payload calls the inner to_wire.
        gs_inner = GitStatusOpts()
        assert WorkspaceOpsRequest.git_status(gs_inner).to_wire() == {
            "type": "git_status",
            "data": gs_inner.to_wire(),
        }
        gd_inner = GitDiffArgs()
        assert WorkspaceOpsRequest.git_diff(gd_inner).to_wire() == {
            "type": "git_diff",
            "data": gd_inner.to_wire(),
        }
        rg_inner = RipgrepArgs(pattern="TODO")
        assert WorkspaceOpsRequest.ripgrep(rg_inner).to_wire() == {
            "type": "ripgrep",
            "data": rg_inner.to_wire(),
        }
        fz_inner = FuzzySearchArgs(query="main")
        assert WorkspaceOpsRequest.fuzzy_search(fz_inner).to_wire() == {
            "type": "fuzzy_search",
            "data": fz_inner.to_wire(),
        }

    def test_act_on_hunk_nests_adjacent_tagged_enum(self):
        # HunkAction is itself AdjacentTagged -> _payload calls its to_wire,
        # producing a nested {type, data} object as the variant's data.
        action = HunkAction.accept(HunkId("h1"))
        req = WorkspaceOpsRequest.act_on_hunk(action)
        assert req.to_wire() == {
            "type": "act_on_hunk",
            "data": {"type": "accept", "data": "h1"},
        }
        back = WorkspaceOpsRequest.from_wire(req.to_wire())
        assert back.to_wire() == req.to_wire()

    def test_string_newtype_variants_carry_bare_string(self):
        # MemoryWrite(String) / InstallPlugin(String) -> data is the bare string.
        assert WorkspaceOpsRequest.memory_write("note").to_wire() == {
            "type": "memory_write",
            "data": "note",
        }
        assert WorkspaceOpsRequest.install_plugin("https://x.example").to_wire() == {
            "type": "install_plugin",
            "data": "https://x.example",
        }

    def test_resolve_file_refs_carry_bare_list(self):
        # ResolveFileRefs(Vec<String>) -> data is the bare list (order preserved).
        req = WorkspaceOpsRequest.resolve_file_refs(["@README.md", "@src/main.rs"])
        assert req.to_wire() == {
            "type": "resolve_file_refs",
            "data": ["@README.md", "@src/main.rs"],
        }

    def test_memory_search_struct_variant(self):
        # MemorySearch { query, limit } -> data is a hand-built dict (u32 limit).
        req = WorkspaceOpsRequest.memory_search("auth", 5)
        assert req.to_wire() == {
            "type": "memory_search",
            "data": {"query": "auth", "limit": 5},
        }
        # limit coerced to int (defensive against bool / numeric subclass)
        assert WorkspaceOpsRequest.memory_search("x", True).to_wire()["data"]["limit"] == 1

    def test_all_18_variants_round_trip_via_from_wire(self):
        samples = [
            WorkspaceOpsRequest.git_status(GitStatusOpts()),
            WorkspaceOpsRequest.git_diff(GitDiffArgs()),
            WorkspaceOpsRequest.git_branch_info(),
            WorkspaceOpsRequest.git_metadata(),
            WorkspaceOpsRequest.list_hunks(),
            WorkspaceOpsRequest.act_on_hunk(HunkAction.reject("h2")),
            WorkspaceOpsRequest.ripgrep(RipgrepArgs(pattern="X")),
            WorkspaceOpsRequest.fuzzy_search(FuzzySearchArgs(query="Y")),
            WorkspaceOpsRequest.discover_skills(),
            WorkspaceOpsRequest.discover_plugins(),
            WorkspaceOpsRequest.load_project_config(),
            WorkspaceOpsRequest.load_permissions(),
            WorkspaceOpsRequest.load_envrc(),
            WorkspaceOpsRequest.resolve_file_refs(["@a"]),
            WorkspaceOpsRequest.memory_search("q", 3),
            WorkspaceOpsRequest.memory_write("w"),
            WorkspaceOpsRequest.install_plugin("src"),
            WorkspaceOpsRequest.refresh_plugins(),
        ]
        assert len(samples) == 18
        kinds = {s.to_wire()["type"] for s in samples}
        assert len(kinds) == 18  # all 18 wire tags distinct
        for s in samples:
            wire = s.to_wire()
            back = WorkspaceOpsRequest.from_wire(wire)
            assert back.to_wire() == wire


class TestSessionLifecycleRequest:
    """SessionLifecycleRequest adjacent-tagged enum (R79, 8 variants)."""

    def test_list_unit_variant_shadows_builtin(self):
        # List -> "list" wire tag; factory method name shadows the builtin.
        req = SessionLifecycleRequest.list()
        assert req.to_wire() == {"type": "list", "data": None}

    def test_session_id_newtype_variants_carry_bare_string(self):
        # Destroy/ApplyWorktree/GetRewindPoints wrap SessionId, which has no
        # to_wire -> _payload passes the str subclass through as a bare string.
        assert SessionLifecycleRequest.destroy(SessionId("s1")).to_wire() == {
            "type": "destroy",
            "data": "s1",
        }
        assert SessionLifecycleRequest.apply_worktree(SessionId("s1")).to_wire() == {
            "type": "apply_worktree",
            "data": "s1",
        }
        assert SessionLifecycleRequest.get_rewind_points(SessionId("s1")).to_wire() == {
            "type": "get_rewind_points",
            "data": "s1",
        }

    def test_fork_delegates_to_agent_session_config(self):
        cfg = AgentSessionConfig.default()
        req = SessionLifecycleRequest.fork(cfg)
        assert req.to_wire() == {"type": "fork", "data": cfg.to_wire()}

    def test_struct_variants_carry_session_plus_u64(self):
        # BeginPrompt/EndPrompt/Rewind: idx/target are u64 (Python int).
        assert SessionLifecycleRequest.begin_prompt(SessionId("s1"), 0).to_wire() == {
            "type": "begin_prompt",
            "data": {"session": "s1", "idx": 0},
        }
        assert SessionLifecycleRequest.end_prompt(SessionId("s1"), 7).to_wire() == {
            "type": "end_prompt",
            "data": {"session": "s1", "idx": 7},
        }
        assert SessionLifecycleRequest.rewind(SessionId("s1"), 3).to_wire() == {
            "type": "rewind",
            "data": {"session": "s1", "target": 3},
        }
        # u64 coercion: bool -> int
        assert SessionLifecycleRequest.begin_prompt(SessionId("s"), True).to_wire()["data"]["idx"] == 1

    def test_all_8_variants_round_trip_via_from_wire(self):
        samples = [
            SessionLifecycleRequest.fork(AgentSessionConfig.default()),
            SessionLifecycleRequest.destroy(SessionId("s1")),
            SessionLifecycleRequest.list(),
            SessionLifecycleRequest.apply_worktree(SessionId("s1")),
            SessionLifecycleRequest.begin_prompt(SessionId("s1"), 0),
            SessionLifecycleRequest.end_prompt(SessionId("s1"), 0),
            SessionLifecycleRequest.rewind(SessionId("s1"), 3),
            SessionLifecycleRequest.get_rewind_points(SessionId("s1")),
        ]
        assert len(samples) == 8
        kinds = {s.to_wire()["type"] for s in samples}
        assert len(kinds) == 8
        for s in samples:
            wire = s.to_wire()
            back = SessionLifecycleRequest.from_wire(wire)
            assert back.to_wire() == wire


# ------------------------------------------------------------------------- R80


class TestEventLag:
    """EventLag single-variant adjacent-tagged enum (R80)."""

    def test_lagged_wire_shape(self):
        # Adjacent-tagged newtype variant: {"type": "lagged", "data": <u64>}.
        assert EventLag.lagged(3).to_wire() == {"type": "lagged", "data": 3}

    def test_u64_coercion(self):
        # u64 field — bool is widened to int defensively.
        assert EventLag.lagged(True).to_wire()["data"] == 1

    def test_str_display_mirrors_thiserror(self):
        # thiserror #[error("lagged by {0} events")] -> __str__.
        assert str(EventLag.lagged(3)) == "lagged by 3 events"

    def test_round_trip_via_from_wire(self):
        lag = EventLag.lagged(7)
        wire = lag.to_wire()
        back = EventLag.from_wire(wire)
        assert back.to_wire() == wire
        assert str(back) == "lagged by 7 events"


class TestWorkspaceTopic:
    """WorkspaceTopic plain snake_case StrEnum (R80, 7 variants)."""

    def test_snake_case_wire_values(self):
        # Plain #[serde(rename_all = "snake_case")] — bare snake_case string,
        # NOT adjacent-tagged {type, data}.
        assert WorkspaceTopic.Fs == "fs"
        assert WorkspaceTopic.Vcs == "vcs"
        assert WorkspaceTopic.Discovery == "discovery"
        assert WorkspaceTopic.Servers == "servers"
        assert WorkspaceTopic.Index == "index"
        assert WorkspaceTopic.Config == "config"
        assert WorkspaceTopic.Tools == "tools"

    def test_seven_variants(self):
        assert len(list(WorkspaceTopic)) == 7

    def test_serialises_as_bare_string(self):
        # Externally-tagged enum: the wire form is the bare value string.
        assert json.dumps(WorkspaceTopic.Fs.value) == '"fs"'


class TestWorkspaceTopicSet:
    """WorkspaceTopicSet transparent u32 bitmask newtype (R80)."""

    def test_empty(self):
        s = WorkspaceTopicSet.empty()
        assert s.bits == 0
        assert s.is_empty()
        assert s.to_wire() == 0

    def test_all_covers_every_topic(self):
        s = WorkspaceTopicSet.all()
        assert s.bits == 0b1111111  # 7 topics -> bits 0..6
        assert not s.is_empty()
        for topic in WorkspaceTopic:
            assert s.contains(topic)
        assert s.to_wire() == 127

    def test_with_topic_builds_bitmask(self):
        s = WorkspaceTopicSet.empty().with_topic(WorkspaceTopic.Fs)
        assert s.bits == 0b1  # Fs -> index 0
        s = s.with_topic(WorkspaceTopic.Tools)  # Tools -> index 6
        assert s.bits == 0b1000001

    def test_contains_membership(self):
        s = (
            WorkspaceTopicSet.empty()
            .with_topic(WorkspaceTopic.Vcs)
            .with_topic(WorkspaceTopic.Index)
        )
        assert s.contains(WorkspaceTopic.Vcs)
        assert s.contains(WorkspaceTopic.Index)
        assert not s.contains(WorkspaceTopic.Fs)

    def test_transparent_wire_returns_bare_int(self):
        # #[serde(transparent)] over u32 -> bare integer, no field wrapper.
        wire = WorkspaceTopicSet(bits=5).to_wire()
        assert wire == 5
        assert isinstance(wire, int)
        assert not isinstance(wire, dict)

    def test_from_wire_round_trip(self):
        s = WorkspaceTopicSet.all()
        assert WorkspaceTopicSet.from_wire(s.to_wire()) == s

    def test_from_wire_rejects_bool(self):
        # bool is an int subclass; a u32 deserialiser must reject it.
        with pytest.raises(ValueError):
            WorkspaceTopicSet.from_wire(True)

    def test_from_wire_rejects_non_int(self):
        with pytest.raises(ValueError):
            WorkspaceTopicSet.from_wire("5")
        with pytest.raises(ValueError):
            WorkspaceTopicSet.from_wire([1, 2])

    def test_eq_and_hash(self):
        a = WorkspaceTopicSet.empty().with_topic(WorkspaceTopic.Fs)
        b = WorkspaceTopicSet(bits=1)
        assert a == b
        assert hash(a) == hash(b)
        assert a != WorkspaceTopicSet.empty()

    def test_u32_mask_clamps(self):
        # Negative and >u32 literals are masked into the u32 range.
        assert WorkspaceTopicSet(bits=-1).bits == 0xFFFFFFFF
        assert WorkspaceTopicSet(bits=0x1_0000_0000).bits == 0  # wraps to 0


class TestWorkspaceEvent:
    """WorkspaceEvent adjacent-tagged enum (R80, 12 variants)."""

    def test_fs_changed_struct_variant(self):
        ev = WorkspaceEvent.fs_changed("/a/b", FsEventKind.Modified)
        assert ev.to_wire() == {
            "type": "fs_changed",
            "data": {"path": "/a/b", "kind": "modified"},
        }

    def test_git_head_changed_with_branch(self):
        ev = WorkspaceEvent.git_head_changed("abc123", "main", VcsKind.Git)
        assert ev.to_wire() == {
            "type": "git_head_changed",
            "data": {"commit": "abc123", "branch": "main", "vcs": "git"},
        }

    def test_git_head_changed_detached_head(self):
        # branch: Option<String> -> None when detached.
        ev = WorkspaceEvent.git_head_changed("abc123", None, VcsKind.Jj)
        assert ev.to_wire() == {
            "type": "git_head_changed",
            "data": {"commit": "abc123", "branch": None, "vcs": "jj"},
        }

    def test_git_lock_held_datetime_z_suffix(self):
        # until: DateTime<Utc> -> RFC 3339 with Z suffix (reuses R78 _dt_to_wire).
        ev = WorkspaceEvent.git_lock_held(_EPOCH)
        assert ev.to_wire() == {
            "type": "git_lock_held",
            "data": {"until": "1970-01-01T00:00:00Z"},
        }

    def test_skills_changed_nested_wiremodel_list(self):
        # added: Vec<SkillInfo> -> each element delegates via to_wire.
        skill = SkillInfo(id="review", display_name="Code Review")
        ev = WorkspaceEvent.skills_changed([skill], ["old"])
        assert ev.to_wire() == {
            "type": "skills_changed",
            "data": {"added": [skill.to_wire()], "removed": ["old"]},
        }

    def test_plugins_changed(self):
        plugin = PluginInfo(id="p1")
        ev = WorkspaceEvent.plugins_changed([plugin], True)
        assert ev.to_wire() == {
            "type": "plugins_changed",
            "data": {"plugins": [plugin.to_wire()], "project_trusted": True},
        }

    def test_hooks_changed(self):
        hook = HookInfo(id="h1")
        ev = WorkspaceEvent.hooks_changed([hook], False)
        assert ev.to_wire() == {
            "type": "hooks_changed",
            "data": {"hooks": [hook.to_wire()], "project_trusted": False},
        }

    def test_mcp_server_state_changed(self):
        # McpServerStatus is a ServerStatus alias.
        ev = WorkspaceEvent.mcp_server_state_changed("ctx7", McpServerStatus.Running)
        assert ev.to_wire() == {
            "type": "mcp_server_state_changed",
            "data": {"server": "ctx7", "status": "running"},
        }

    def test_lsp_server_state_changed(self):
        # LspServerStatus is a ServerStatus alias.
        ev = WorkspaceEvent.lsp_server_state_changed("rust-analyzer", LspServerStatus.Starting)
        assert ev.to_wire() == {
            "type": "lsp_server_state_changed",
            "data": {"server": "rust-analyzer", "status": "starting"},
        }

    def test_codebase_index_updated_u64(self):
        ev = WorkspaceEvent.codebase_index_updated(42)
        assert ev.to_wire() == {
            "type": "codebase_index_updated",
            "data": {"files_indexed": 42},
        }
        # u64 coercion: bool -> int
        assert WorkspaceEvent.codebase_index_updated(True).to_wire()["data"]["files_indexed"] == 1

    def test_project_config_changed_unit_variant(self):
        assert WorkspaceEvent.project_config_changed().to_wire() == {
            "type": "project_config_changed",
            "data": None,
        }

    def test_permission_policy_changed_unit_variant(self):
        assert WorkspaceEvent.permission_policy_changed().to_wire() == {
            "type": "permission_policy_changed",
            "data": None,
        }

    def test_tools_changed(self):
        ev = WorkspaceEvent.tools_changed("sess-1")
        assert ev.to_wire() == {
            "type": "tools_changed",
            "data": {"session_id": "sess-1"},
        }

    def test_all_12_variants_round_trip_via_from_wire(self):
        samples = [
            WorkspaceEvent.fs_changed("/x", FsEventKind.Created),
            WorkspaceEvent.git_head_changed("c", "main", VcsKind.Git),
            WorkspaceEvent.git_lock_held(_EPOCH),
            WorkspaceEvent.skills_changed([SkillInfo.default()], ["x"]),
            WorkspaceEvent.plugins_changed([PluginInfo.default()], True),
            WorkspaceEvent.hooks_changed([HookInfo.default()], False),
            WorkspaceEvent.mcp_server_state_changed("m", McpServerStatus.Running),
            WorkspaceEvent.lsp_server_state_changed("l", LspServerStatus.Starting),
            WorkspaceEvent.codebase_index_updated(0),
            WorkspaceEvent.project_config_changed(),
            WorkspaceEvent.permission_policy_changed(),
            WorkspaceEvent.tools_changed("s"),
        ]
        assert len(samples) == 12
        kinds = {s.to_wire()["type"] for s in samples}
        assert len(kinds) == 12  # all 12 wire tags distinct
        for s in samples:
            wire = s.to_wire()
            back = WorkspaceEvent.from_wire(wire)
            assert back.to_wire() == wire

    def test_topic_mapping_covers_all_variants(self):
        assert WorkspaceEvent.fs_changed("/x", FsEventKind.Modified).topic() == WorkspaceTopic.Fs
        assert WorkspaceEvent.git_head_changed("c", None, VcsKind.Git).topic() == WorkspaceTopic.Vcs
        assert WorkspaceEvent.git_lock_held(_EPOCH).topic() == WorkspaceTopic.Vcs
        assert WorkspaceEvent.skills_changed([], []).topic() == WorkspaceTopic.Discovery
        assert WorkspaceEvent.plugins_changed([], True).topic() == WorkspaceTopic.Discovery
        assert WorkspaceEvent.hooks_changed([], False).topic() == WorkspaceTopic.Discovery
        assert (
            WorkspaceEvent.mcp_server_state_changed("m", McpServerStatus.Running).topic()
            == WorkspaceTopic.Servers
        )
        assert (
            WorkspaceEvent.lsp_server_state_changed("l", LspServerStatus.Running).topic()
            == WorkspaceTopic.Servers
        )
        assert WorkspaceEvent.codebase_index_updated(0).topic() == WorkspaceTopic.Index
        assert WorkspaceEvent.project_config_changed().topic() == WorkspaceTopic.Config
        assert WorkspaceEvent.permission_policy_changed().topic() == WorkspaceTopic.Config
        assert WorkspaceEvent.tools_changed("s").topic() == WorkspaceTopic.Tools


class TestWorkspaceRequest:
    """WorkspaceRequest outer envelope (R79): tool / ops / session dispatch."""

    def test_each_variant_nests_sub_enum(self):
        tool = WorkspaceRequest.tool(ToolRequest.definitions())
        assert tool.to_wire() == {
            "type": "tool",
            "data": {"type": "definitions", "data": None},
        }
        ops = WorkspaceRequest.ops(WorkspaceOpsRequest.list_hunks())
        assert ops.to_wire() == {
            "type": "ops",
            "data": {"type": "list_hunks", "data": None},
        }
        sess = WorkspaceRequest.session(SessionLifecycleRequest.list())
        assert sess.to_wire() == {
            "type": "session",
            "data": {"type": "list", "data": None},
        }

    def test_round_trips_through_nested_enums(self):
        for req in [
            WorkspaceRequest.tool(ToolRequest.call(ToolCallArgs(
                session=SessionId("s1"), tool_name="t", call_id=ToolCallId("c1"),
            ))),
            WorkspaceRequest.ops(WorkspaceOpsRequest.memory_search("q", 2)),
            WorkspaceRequest.session(SessionLifecycleRequest.rewind(SessionId("s1"), 5)),
        ]:
            wire = req.to_wire()
            back = WorkspaceRequest.from_wire(wire)
            assert back.to_wire() == wire

    def test_unknown_variant_rejected_by_from_wire(self):
        # "events" is the subscription type, not a WorkspaceRequest variant.
        with pytest.raises(ValueError):
            WorkspaceRequest.from_wire({"type": "events", "data": None})
