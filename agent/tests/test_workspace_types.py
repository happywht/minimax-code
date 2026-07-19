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
    SkillInfo,
    ToolCallId,
    ToolCallResult,
    ToolDef,
    ToolOutputChunk,
    ToolProgress,
    ToolServerConfig,
    UserAnswer,
    UserQuestion,
    UserQuestionOption,
    VcsKind,
    WorkspaceError,
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
