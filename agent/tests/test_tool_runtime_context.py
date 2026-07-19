"""Tests for the R108 tool_runtime context module.

Covers:

- ``TypedExtensions``: insert/get/contains/remove/len/is_empty,
  insert_arc (== insert), merge_defaults (existing wins), ``new()``, plus
  the Pythonic dunder surface (``len`` / ``bool`` / ``in``)
- per-type overwrite (same type replaces), base-type does NOT retrieve a
  subclass instance (TypeId precision)
- ``ToolCallContext``: ``new`` / ``default`` / ``insert`` / ``get``
  delegation, per-instance extension isolation
- ``ListToolsContext``: ``new()`` + isolation
- the four string/path newtypes (``Cwd`` / ``BehaviorVersion`` /
  ``TraceContext`` / ``SessionContext``): construct + field access
- ``Cancellation``: ``is_cancelled`` / ``cancel`` / ``cancelled`` await
  surface + shared-token sibling propagation
- ``WorkspaceViewerContext``: default + ``to_dict``/``from_dict`` round
  trip + malformed fall-back
- ``WorkspaceBindMetadata``: the five ``bind_metadata_tests`` ported
  verbatim (empty-omits, populated round-trip, rpc_only wire-compat,
  system_notifications wire-compat, malformed-field-falls-back) plus the
  legacy-payload-without-viewer-ctx case
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from minimax_code.tool_protocol.ids import ToolCallId
from minimax_code.tool_runtime.context import (
    BehaviorVersion,
    Cancellation,
    Cwd,
    ListToolsContext,
    SessionContext,
    ToolCallContext,
    TraceContext,
    TypedExtensions,
    WorkspaceBindMetadata,
    WorkspaceViewerContext,
)

# ---------------------------------------------------------------------------
# TypedExtensions
# ---------------------------------------------------------------------------


def test_new_is_empty():
    ext = TypedExtensions.new()
    assert ext.is_empty()
    assert len(ext) == 0
    assert not ext


def test_insert_and_get_roundtrip():
    ext = TypedExtensions()
    cwd = Cwd(Path("/tmp"))
    ext.insert(cwd)
    assert ext.get(Cwd) is cwd
    assert Cwd in ext
    assert ext.contains(Cwd)
    assert len(ext) == 1
    assert bool(ext)


def test_get_missing_type_returns_none():
    ext = TypedExtensions()
    assert ext.get(Cwd) is None
    assert Cwd not in ext


def test_insert_same_type_replaces_prior_value():
    ext = TypedExtensions()
    ext.insert(Cwd(Path("/a")))
    ext.insert(Cwd(Path("/b")))
    assert ext.get(Cwd).path == Path("/b")
    assert len(ext) == 1


def test_base_type_does_not_retrieve_subclass_instance():
    # TypeId precision: storing a Sub does not make it retrievable as Base.
    class Base:
        pass

    class Sub(Base):
        pass

    ext = TypedExtensions()
    ext.insert(Sub())
    assert ext.get(Base) is None
    assert ext.get(Sub) is not None


def test_remove_returns_value_and_clears_slot():
    ext = TypedExtensions()
    cwd = Cwd(Path("/tmp"))
    ext.insert(cwd)
    assert ext.remove(Cwd) is cwd
    assert ext.get(Cwd) is None
    assert ext.is_empty()
    assert ext.remove(Cwd) is None  # second remove is None


def test_insert_arc_is_identical_to_insert():
    ext = TypedExtensions()
    cwd = Cwd(Path("/tmp"))
    ext.insert_arc(cwd)
    assert ext.get(Cwd) is cwd


def test_merge_defaults_only_inserts_missing_keys():
    defaults = TypedExtensions()
    defaults.insert(Cwd(Path("/default")))
    defaults.insert(BehaviorVersion("v2"))

    self_ = TypedExtensions()
    self_.insert(Cwd(Path("/explicit")))  # Cwd already present

    self_.merge_defaults(defaults)
    # Existing Cwd wins; missing BehaviorVersion is copied in.
    assert self_.get(Cwd).path == Path("/explicit")
    assert self_.get(BehaviorVersion).version == "v2"
    assert len(self_) == 2


def test_merge_defaults_does_not_mutate_source():
    defaults = TypedExtensions()
    defaults.insert(BehaviorVersion("v2"))
    self_ = TypedExtensions()
    self_.merge_defaults(defaults)
    # Source store is untouched (still just its own entry).
    assert len(defaults) == 1


def test_dunder_len_bool_and_contains():
    ext = TypedExtensions()
    assert len(ext) == 0
    assert not bool(ext)
    ext.insert(Cwd(Path("/x")))
    assert len(ext) == 1
    assert bool(ext)
    assert Cwd in ext
    # A non-type object is not a valid key -> not contained.
    assert Cwd(Path("/x")) not in ext


# ---------------------------------------------------------------------------
# ToolCallContext
# ---------------------------------------------------------------------------


def test_tool_call_context_new_stores_call_id():
    cid = ToolCallId("call-1")
    ctx = ToolCallContext.new(cid)
    assert ctx.call_id == cid
    assert ctx.extensions.is_empty()


def test_tool_call_context_default_generates_fresh_call_id():
    a = ToolCallContext.default()
    b = ToolCallContext.default()
    assert len(str(a.call_id)) > 0
    assert a.call_id != b.call_id  # uuid4 freshness


def test_tool_call_context_insert_get_delegate_to_extensions():
    ctx = ToolCallContext.new(ToolCallId("c"))
    cwd = Cwd(Path("/tmp"))
    ctx.insert(cwd)
    assert ctx.get(Cwd) is cwd
    assert ctx.extensions.get(Cwd) is cwd


def test_tool_call_context_instances_have_isolated_extensions():
    a = ToolCallContext.new(ToolCallId("a"))
    b = ToolCallContext.new(ToolCallId("b"))
    a.insert(Cwd(Path("/a")))
    assert b.get(Cwd) is None  # b's store is independent


# ---------------------------------------------------------------------------
# ListToolsContext
# ---------------------------------------------------------------------------


def test_list_tools_context_new_is_empty():
    ctx = ListToolsContext.new()
    assert ctx.extensions.is_empty()


def test_list_tools_context_instances_have_isolated_extensions():
    a = ListToolsContext.new()
    b = ListToolsContext.new()
    a.extensions.insert(Cwd(Path("/a")))
    assert b.extensions.get(Cwd) is None


# ---------------------------------------------------------------------------
# Newtype concept markers
# ---------------------------------------------------------------------------


def test_cwd_holds_path():
    p = Path("/work")
    assert Cwd(p).path == p


def test_behavior_version_holds_string():
    assert BehaviorVersion("v2").version == "v2"


def test_trace_context_holds_string():
    assert TraceContext("00-trace").value == "00-trace"


def test_session_context_holds_string():
    assert SessionContext("sess-1").value == "sess-1"


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancellation_starts_uncancelled():
    c = Cancellation()
    assert not c.is_cancelled()


def test_cancellation_cancel_is_idempotent():
    c = Cancellation()
    c.cancel()
    assert c.is_cancelled()
    c.cancel()  # second call is a no-op
    assert c.is_cancelled()


async def test_cancellation_wait_resolves_after_cancel():
    c = Cancellation()
    c.cancel()
    # Already cancelled -> cancelled() returns immediately.
    await asyncio.wait_for(c.cancelled(), timeout=1.0)


async def test_cancellation_wait_blocks_until_cancel():
    c = Cancellation()

    async def fire_after() -> None:
        await asyncio.sleep(0.01)
        c.cancel()

    await asyncio.gather(c.cancelled(), fire_after())
    assert c.is_cancelled()


def test_cancellation_shared_token_propagates_to_siblings():
    # Two Cancellation dataclasses sharing the same event see each
    # other's cancel() — Rust CancellationToken::clone sibling semantics.
    shared = asyncio.Event()
    a = Cancellation(token=shared)
    b = Cancellation(token=shared)
    a.cancel()
    assert b.is_cancelled()


# ---------------------------------------------------------------------------
# WorkspaceViewerContext
# ---------------------------------------------------------------------------


def test_workspace_viewer_context_default_off():
    assert WorkspaceViewerContext().stream_tool_progress is False


def test_workspace_viewer_context_round_trip():
    vc = WorkspaceViewerContext(stream_tool_progress=True)
    d = vc.to_dict()
    assert d == {"stream_tool_progress": True}
    back = WorkspaceViewerContext.from_dict(d)
    assert back.stream_tool_progress is True


def test_workspace_viewer_context_malformed_falls_back_to_false():
    # Non-bool value drops to default (#[serde(default)]).
    back = WorkspaceViewerContext.from_dict({"stream_tool_progress": "yes"})
    assert back.stream_tool_progress is False


# ---------------------------------------------------------------------------
# WorkspaceBindMetadata (ported from Rust bind_metadata_tests)
# ---------------------------------------------------------------------------


def test_serialize_omits_empty_fields():
    md = WorkspaceBindMetadata()
    assert md.to_dict() == {}


def test_round_trips_populated():
    md = WorkspaceBindMetadata(
        preset="explore",
        capability_mode="read_only",
        tools=[{"id": "GrokBuild:grep"}],
        viewer_ctx=WorkspaceViewerContext(stream_tool_progress=True),
        yolo_mode=True,
        manifest_version="v1",
        manifest_hash="abc123",
        system_notifications=True,
        rpc_only=True,
    )
    value = md.to_dict()
    back = WorkspaceBindMetadata.from_dict(value)
    assert back.preset == "explore"
    assert back.capability_mode == "read_only"
    assert back.tools == [{"id": "GrokBuild:grep"}]
    assert back.viewer_ctx.stream_tool_progress is True
    assert back.yolo_mode is True
    assert back.manifest_version == "v1"
    assert back.manifest_hash == "abc123"
    assert back.system_notifications is True
    assert back.rpc_only is True


def test_rpc_only_omitted_when_false_wire_compatible():
    md = WorkspaceBindMetadata()
    value = md.to_dict()
    assert "rpc_only" not in value

    md = WorkspaceBindMetadata.from_dict({"preset": "explore"})
    assert md.rpc_only is False

    md = WorkspaceBindMetadata.from_dict({"rpc_only": True})
    assert md.rpc_only is True


def test_system_notifications_is_wire_compatible():
    md = WorkspaceBindMetadata()
    value = md.to_dict()
    assert "system_notifications" not in value

    md = WorkspaceBindMetadata(system_notifications=True)
    value = md.to_dict()
    back = WorkspaceBindMetadata.from_dict(value)
    assert back.system_notifications is True

    md = WorkspaceBindMetadata.from_dict({"preset": "explore"})
    assert md.system_notifications is None


def test_malformed_field_falls_back_to_default_keeping_siblings():
    # `tools` is the wrong type and `capability_mode` is fine: the bad
    # field drops to default, the good sibling survives.
    value = {
        "preset": "explore",
        "capability_mode": "read_only",
        "tools": "not-a-list",
    }
    md = WorkspaceBindMetadata.from_dict(value)
    assert md.preset == "explore"
    assert md.capability_mode == "read_only"
    assert md.tools == []


def test_legacy_payload_without_viewer_ctx_parses():
    md = WorkspaceBindMetadata.from_dict({"preset": "explore"})
    assert md.viewer_ctx is None
