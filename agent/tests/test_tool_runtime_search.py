"""Tests for the R112 tool_runtime search module.

Covers the migration of ``xai-tool-runtime/src/search.rs``. The Rust
source carries no inline ``#[test]`` block, so these are Python-style
semantic-equivalence checks rather than a literal port:

- ``ToolSearchResult`` / ``SearchSnapshot`` / ``ServerSummary``:
  construction, field access, derived ``Debug`` (non-empty repr),
  ``Clone`` (deepcopy equality), ``PartialEq`` (field-wise equality),
  ``input_schema`` carries arbitrary JSON, ``Option<String>`` round-trips
  through ``None``.
- ``ServerSummary.tool_count``: matches ``len(tool_names)``.
- ``ToolSearchIndex`` Protocol: ``runtime_checkable`` accepts a backend
  with both methods, rejects one missing a method.
- ``ToolIndex``: wraps an index, opaque repr (does NOT expand the dyn
  trait), wraps a Protocol-satisfying instance, ``eq=False`` mirrors the
  absence of ``PartialEq`` (identity comparison only).
"""

from __future__ import annotations

import copy

from minimax_code.tool_runtime.search import (
    SearchSnapshot,
    ServerSummary,
    ToolIndex,
    ToolSearchIndex,
    ToolSearchResult,
)

# ---------------------------------------------------------------------------
# Fakes for the ToolSearchIndex Protocol.
# ---------------------------------------------------------------------------


class _FakeIndex:
    """Minimal backend implementing both Protocol methods."""

    def search_snapshot(self, query: str, limit: int) -> SearchSnapshot:
        return SearchSnapshot(results=[], total_hidden_tools=0, is_ready=True)

    def list_server_summaries(self) -> list[ServerSummary]:
        return []


class _MissingMethodIndex:
    """Backend missing list_server_summaries -> not a ToolSearchIndex."""

    def search_snapshot(self, query: str, limit: int) -> SearchSnapshot:
        return SearchSnapshot(results=[], total_hidden_tools=0, is_ready=True)


# ---------------------------------------------------------------------------
# ToolSearchResult.
# ---------------------------------------------------------------------------


def _sample_result() -> ToolSearchResult:
    return ToolSearchResult(
        tool_name="linear__save_issue",
        server_name="linear",
        description="Save a Linear issue.",
        score=0.97,
        parameters=["teamId", "title"],
        input_schema={"type": "object", "required": ["title"]},
    )


def test_tool_search_result_fields_and_json_schema_carried():
    r = _sample_result()
    assert r.tool_name == "linear__save_issue"
    assert r.server_name == "linear"
    assert r.score == 0.97
    assert r.parameters == ["teamId", "title"]
    # Arbitrary JSON value carried verbatim (serde_json::Value -> Any).
    assert r.input_schema == {"type": "object", "required": ["title"]}


def test_tool_search_result_clone_and_equality():
    r = _sample_result()
    # #[derive(Clone)] -> deepcopy is a separate equal instance.
    clone = copy.deepcopy(r)
    assert clone == r
    assert clone is not r
    # #[derive(PartialEq)] -> field-wise equality; one field differs.
    diff = copy.deepcopy(r)
    diff.score = 0.10
    assert diff != r


def test_tool_search_result_debug_repr_non_empty():
    r = _sample_result()
    # #[derive(Debug)] -> a non-empty, informative repr.
    assert "linear__save_issue" in repr(r)


# ---------------------------------------------------------------------------
# SearchSnapshot.
# ---------------------------------------------------------------------------


def test_search_snapshot_fields():
    snap = SearchSnapshot(
        results=[_sample_result()], total_hidden_tools=3, is_ready=True
    )
    assert len(snap.results) == 1
    assert snap.total_hidden_tools == 3
    assert snap.is_ready is True


def test_search_snapshot_equality_and_clone():
    snap = SearchSnapshot(results=[], total_hidden_tools=0, is_ready=False)
    assert copy.deepcopy(snap) == snap


# ---------------------------------------------------------------------------
# ServerSummary.
# ---------------------------------------------------------------------------


def test_server_summary_fields_with_none_description():
    s = ServerSummary(name="slack", description=None, tool_names=["post", "read"])
    assert s.name == "slack"
    assert s.description is None  # Option<String>::None round-trip.
    assert s.tool_names == ["post", "read"]


def test_server_summary_tool_count_matches_len():
    s = ServerSummary(
        name="linear", description="issues", tool_names=["a", "b", "c"]
    )
    assert s.tool_count() == 3
    empty = ServerSummary(name="empty", description=None, tool_names=[])
    assert empty.tool_count() == 0


# ---------------------------------------------------------------------------
# ToolSearchIndex Protocol.
# ---------------------------------------------------------------------------


def test_tool_search_index_accepts_full_backend():
    assert isinstance(_FakeIndex(), ToolSearchIndex)


def test_tool_search_index_rejects_backend_missing_a_method():
    # runtime_checkable: a backend missing list_server_summaries is not an index.
    assert not isinstance(_MissingMethodIndex(), ToolSearchIndex)


def test_tool_search_index_protocol_methods_callable():
    # The Protocol methods are invokable on a structural implementation.
    index: ToolSearchIndex = _FakeIndex()
    snap = index.search_snapshot("query", 5)
    assert isinstance(snap, SearchSnapshot)
    assert index.list_server_summaries() == []


# ---------------------------------------------------------------------------
# ToolIndex newtype wrapper.
# ---------------------------------------------------------------------------


def test_tool_index_wraps_an_index_and_protocol_instance():
    wrapped = ToolIndex(_FakeIndex())
    # The wrapped object still satisfies the Protocol.
    assert isinstance(wrapped.index, ToolSearchIndex)


def test_tool_index_repr_is_opaque_does_not_expand_dyn_trait():
    # Rust's custom Debug impl is `debug_struct("ToolIndex").finish()` —
    # it deliberately does NOT render the dyn trait inside.
    wrapped = ToolIndex(_FakeIndex())
    r = repr(wrapped)
    assert r == "ToolIndex(...)"
    assert "FakeIndex" not in r


def test_tool_index_has_no_partialeq_identity_only():
    # Rust ToolIndex does NOT derive PartialEq -> two wrappers over the
    # same backend are equal only by identity (is), never by value.
    backend = _FakeIndex()
    a = ToolIndex(backend)
    b = ToolIndex(backend)
    assert a is not b
    # eq=False -> `==` falls back to object identity.
    assert a != b
    assert a == a
