"""Tests for ``ScopeGraphIndex`` (R305c) -- cross-file symbol index runtime.

Ported from grok ``xai-codebase-graph/src/scope_graph/graph.rs``
``ScopeGraphIndex``. Covers the in-memory runtime only (SGIX binary ser/de
lands in R305d, tree-sitter bridge in R305e):

* module helpers (``_saturate_line`` u32 boundary, ``_ext_of``)
* interning round-trip + out-of-range ``get_str``
* ``add_file`` graph aggregation (0->1 line conversion + saturation)
* direct ``add_definition`` / ``add_reference`` line saturation
* alias resolution -- forward (``find_definitions`` walks the alias target)
  and reverse (``find_references`` walks ``reverse_aliases``)
* reverse file->symbol indexes -- ``remove_file`` prunes only that file's
  symbols, ``rename_file`` rewrites the path component in place
* file metadata -- ``update_file_meta`` / ``is_file_stale`` / ``is_indexed``
* smart ranking -- same-language-family first, lexicographic tie-break, dedupe
* extension filtering, statistics, query-version stamp, ``compact`` idempotency
* barrel surface (subpackage + crate-root re-export)

The real :class:`ScopeGraph` needs tree-sitter (R305e); these tests substitute
a minimal fake graph / range so the index-aggregation logic is exercised in
isolation.
"""

from __future__ import annotations

import os

from minimax_code.xai_codebase_graph import ScopeGraphIndex as ScopeGraphIndexFromRoot
from minimax_code.xai_codebase_graph.interner import StringId
from minimax_code.xai_codebase_graph.languages import LanguageRegistry
from minimax_code.xai_codebase_graph.scope_graph import ScopeGraphIndex
from minimax_code.xai_codebase_graph.scope_graph.index import (
    U32_MAX,
    _ext_of,
    _saturate_line,
)
from minimax_code.xai_codebase_graph.types import FileMeta

# === test doubles (avoid tree-sitter dependency; R305e bridges the real graph) ===


class _FakeRange:
    """Stand-in for grok ``Range``: only ``start_line()`` is consumed by ``add_file``."""

    def __init__(self, start_line: int) -> None:
        self._start = start_line

    def start_line(self) -> int:
        return self._start


class _FakeGraph:
    """Stand-in for :class:`ScopeGraph` returning canned defs/refs lists."""

    def __init__(
        self,
        defs: list[tuple[str, _FakeRange]],
        refs: list[tuple[str, _FakeRange]],
    ) -> None:
        self._defs = list(defs)
        self._refs = list(refs)

    def get_definitions(self, src: bytes) -> list[tuple[str, _FakeRange]]:
        return list(self._defs)

    def get_references(self, src: bytes) -> list[tuple[str, _FakeRange]]:
        return list(self._refs)


# === module helpers =======================================================


def test_saturate_line_boundary() -> None:
    """``_saturate_line`` mirrors grok ``line.min(u32::MAX as usize) as u32``."""
    assert _saturate_line(0) == 0
    assert _saturate_line(U32_MAX - 1) == U32_MAX - 1
    assert _saturate_line(U32_MAX) == U32_MAX
    assert _saturate_line(U32_MAX + 1) == U32_MAX
    assert _saturate_line(10**18) == U32_MAX


def test_ext_of_matches_path_extension() -> None:
    """``_ext_of`` mirrors grok ``Path::new(p).extension()`` (final segment, no dot)."""
    assert _ext_of("a/b/c.py") == "py"
    assert _ext_of("foo.tar.gz") == "gz"  # only the final extension segment
    assert _ext_of("noext") == ""
    assert _ext_of("trailing/") == ""
    # splitext treats a leading-dot filename as the name, not an extension --
    # matches grok ``Path::new(".hidden").extension() == None``.
    assert _ext_of(".hidden") == ""


# === interning ============================================================


def test_intern_get_roundtrip() -> None:
    """``intern`` / ``get_str`` / ``get_id`` round-trip; out-of-range yields None."""
    idx = ScopeGraphIndex()
    sid = idx.intern("hello")
    assert idx.get_str(sid) == "hello"
    assert idx.get_id("hello") == sid
    assert idx.get_id("missing") is None
    # out-of-range id returns None (mirrors grok get_str bounds).
    assert idx.get_str(StringId.new(99999)) is None


# === add_file: graph aggregation ==========================================


def test_add_file_indexes_graph_definitions_and_references() -> None:
    """``add_file`` walks the graph and interns names with 0->1 line conversion."""
    idx = ScopeGraphIndex()
    graph = _FakeGraph(
        defs=[("foo", _FakeRange(0)), ("bar", _FakeRange(4))],
        refs=[("foo", _FakeRange(10))],
    )
    idx.add_file("a.py", graph, b"src bytes")
    # 0-indexed start_line -> +1; lines 0->1, 4->5, 10->11.
    assert idx.find_definitions("foo") == [("a.py", 1)]
    assert idx.find_definitions("bar") == [("a.py", 5)]
    assert idx.find_references("foo") == [("a.py", 11)]
    assert idx.get_graph("a.py") is graph


def test_add_file_saturates_huge_lines() -> None:
    """``add_file`` saturates post-conversion lines past ``U32_MAX``."""
    idx = ScopeGraphIndex()
    huge = U32_MAX + 10
    graph = _FakeGraph(defs=[("foo", _FakeRange(huge - 1))], refs=[])
    idx.add_file("a.py", graph, b"")
    # (huge-1)+1 = huge > U32_MAX -> saturates to U32_MAX.
    assert idx.find_definitions("foo") == [("a.py", U32_MAX)]


# === direct insertion saturation ==========================================


def test_add_definition_saturates_line() -> None:
    """``add_definition`` saturates the line to the ``u32`` range."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "a.py", U32_MAX + 5)
    assert idx.find_definitions("foo") == [("a.py", U32_MAX)]
    # exactly U32_MAX stays U32_MAX (saturate is ``< U32_MAX`` else clamp).
    idx.add_definition("bar", "a.py", U32_MAX)
    assert idx.find_definitions("bar") == [("a.py", U32_MAX)]
    # U32_MAX - 1 is in range and stays as-is.
    idx.add_definition("baz", "a.py", U32_MAX - 1)
    assert idx.find_definitions("baz") == [("a.py", U32_MAX - 1)]


def test_has_definition() -> None:
    """``has_definition`` is True only for symbols with an indexed definition."""
    idx = ScopeGraphIndex()
    assert idx.has_definition("foo") is False
    idx.add_definition("foo", "a.py", 1)
    assert idx.has_definition("foo") is True
    # a reference-only symbol has no definition.
    idx.add_reference("bar", "a.py", 1)
    assert idx.has_definition("bar") is False


# === alias resolution =====================================================


def test_find_definitions_resolves_alias_target() -> None:
    """``find_definitions`` of an alias includes the alias target's definitions."""
    idx = ScopeGraphIndex()
    idx.add_definition("function", "real.py", 1)
    idx.add_alias("fn", "function")  # fn -> function
    idx.add_definition("fn", "alias_site.py", 1)
    # "fn" is an alias: its definitions are its own, then the target's.
    assert idx.find_definitions("fn") == [("alias_site.py", 1), ("real.py", 1)]
    # "function" is the target (not an alias itself): only its own defs.
    assert idx.find_definitions("function") == [("real.py", 1)]


def test_find_references_resolves_reverse_aliases() -> None:
    """``find_references`` includes references to every alias pointing at the symbol."""
    idx = ScopeGraphIndex()
    idx.add_reference("function", "caller.py", 3)
    idx.add_alias("fn", "function")  # fn -> function; reverse_aliases[function]={fn}
    idx.add_reference("fn", "caller2.py", 7)
    # references to "function" include refs to "fn" (its reverse alias).
    assert idx.find_references("function") == [("caller.py", 3), ("caller2.py", 7)]


def test_find_references_with_names_tags_each_site() -> None:
    """``find_references_with_names`` carries the matched spelling per location."""
    idx = ScopeGraphIndex()
    idx.add_reference("function", "caller.py", 3)
    idx.add_alias("fn", "function")
    idx.add_reference("fn", "caller2.py", 7)
    assert idx.find_references_with_names("function") == [
        ("function", "caller.py", 3),
        ("fn", "caller2.py", 7),
    ]


def test_alias_count() -> None:
    """``alias_count`` reports the number of recorded forward aliases."""
    idx = ScopeGraphIndex()
    assert idx.alias_count() == 0
    idx.add_alias("fn", "function")
    idx.add_alias("func", "function")
    assert idx.alias_count() == 2


# === reverse indexes: remove / rename =====================================


def test_remove_file_prunes_only_that_files_symbols() -> None:
    """``remove_file`` drops only the removed file's locations (O(symbols in file))."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "a.py", 1)
    idx.add_definition("foo", "b.py", 2)
    idx.add_reference("foo", "a.py", 5)
    idx.remove_file("a.py")
    # a.py pruned; b.py's definition survives.
    assert idx.find_definitions("foo") == [("b.py", 2)]
    assert idx.find_references("foo") == []
    # reverse index no longer tracks a.py.
    a_id = idx.get_id("a.py")
    assert a_id is not None
    assert a_id not in idx.file_to_defs
    assert a_id not in idx.file_to_refs


def test_remove_file_unknown_is_noop() -> None:
    """``remove_file`` on a never-indexed path is a no-op (no raise)."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "a.py", 1)
    idx.remove_file("never.py")  # not interned
    assert idx.find_definitions("foo") == [("a.py", 1)]


def test_remove_file_drops_emptied_symbol() -> None:
    """A symbol whose last location is pruned is removed from the forward map."""
    idx = ScopeGraphIndex()
    idx.add_definition("only_here", "a.py", 1)
    idx.remove_file("a.py")
    assert "only_here" not in {idx.get_str(sid) for sid in idx.definitions}
    assert idx.has_definition("only_here") is False


def test_rename_file_rewrites_locations() -> None:
    """``rename_file`` rewrites the path component of every location in place."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "old.py", 1)
    idx.add_reference("foo", "old.py", 9)
    idx.set_file_meta("old.py", FileMeta.from_stat(os.stat(__file__)))
    idx.rename_file("old.py", "new.py")
    assert idx.find_definitions("foo") == [("new.py", 1)]
    assert idx.find_references("foo") == [("new.py", 9)]
    # file_meta re-keyed under the new path.
    assert idx.get_file_meta("new.py") is not None
    assert idx.get_file_meta("old.py") is None


def test_rename_file_unknown_is_noop() -> None:
    """``rename_file`` on a never-indexed path is a no-op (no raise)."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "a.py", 1)
    idx.rename_file("never.py", "new.py")
    assert idx.find_definitions("foo") == [("a.py", 1)]


# === file metadata ========================================================


def test_update_file_meta_records_stat(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``update_file_meta`` stats the file and records its ``FileMeta``."""
    f = tmp_path / "x.py"
    f.write_text("hi")
    idx = ScopeGraphIndex()
    idx.update_file_meta(str(f))
    assert idx.is_indexed(str(f))
    assert idx.get_file_meta(str(f)) is not None
    assert idx.is_file_stale(str(f)) is False


def test_update_file_meta_missing_file_skipped() -> None:
    """A missing file is silently skipped (no raise, not indexed)."""
    idx = ScopeGraphIndex()
    idx.update_file_meta("does_not_exist.py")
    assert idx.is_indexed("does_not_exist.py") is False


def test_is_file_stale_unknown_path_returns_true() -> None:
    """An unknown path is stale (no cached entry)."""
    idx = ScopeGraphIndex()
    assert idx.is_file_stale("nope.py") is True


def test_is_file_stale_removed_file_returns_true(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """A file removed from disk after indexing is stale."""
    f = tmp_path / "gone.py"
    f.write_text("hi")
    idx = ScopeGraphIndex()
    idx.update_file_meta(str(f))
    assert idx.is_file_stale(str(f)) is False
    f.unlink()
    assert idx.is_file_stale(str(f)) is True


def test_indexed_files_and_file_count(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """``indexed_files`` / ``file_paths_with_meta`` / ``file_count`` agree."""
    f1 = tmp_path / "a.py"
    f1.write_text("x")
    f2 = tmp_path / "b.py"
    f2.write_text("y")
    idx = ScopeGraphIndex()
    idx.update_file_meta(str(f1))
    idx.update_file_meta(str(f2))
    assert idx.file_count() == 2
    assert set(idx.indexed_files()) == {str(f1), str(f2)}
    assert {p for p, _ in idx.file_paths_with_meta()} == {str(f1), str(f2)}


# === smart ranking ========================================================


def test_find_definitions_smart_ranks_same_language_first() -> None:
    """``find_definitions_smart`` surfaces same-language-family entries first."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "b.rs", 1)
    idx.add_definition("foo", "a.py", 2)
    # context is python; a.py surfaces before b.rs despite insertion order.
    result = idx.find_definitions_smart("foo", "ctx.py", LanguageRegistry())
    assert result == [("a.py", 2), ("b.rs", 1)]


def test_find_definitions_smart_breaks_ties_lexicographically() -> None:
    """Same-language ties break by path, ascending."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "z.py", 1)
    idx.add_definition("foo", "a.py", 2)
    result = idx.find_definitions_smart("foo", "ctx.py", LanguageRegistry())
    assert result == [("a.py", 2), ("z.py", 1)]


def test_find_definitions_smart_no_context_preserves_find_order() -> None:
    """Without a context file, no re-ranking: ``find_definitions`` order stands."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "b.py", 1)
    idx.add_definition("foo", "a.py", 2)
    result = idx.find_definitions_smart("foo", None, None)
    assert result == [("b.py", 1), ("a.py", 2)]


def test_find_definitions_smart_dedupes_alias_overlap() -> None:
    """``find_definitions_smart`` de-dupes the alias-resolved overlap."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "a.py", 1)
    idx.add_alias("fn", "foo")
    idx.add_definition("fn", "a.py", 1)  # same (path, line) as foo's
    result = idx.find_definitions_smart("fn", "ctx.py", LanguageRegistry())
    assert result == [("a.py", 1)]


def test_find_references_smart_ranks_same_language_first() -> None:
    """``find_references_smart`` ranks name-tagged refs by language then path."""
    idx = ScopeGraphIndex()
    idx.add_reference("foo", "b.rs", 1)
    idx.add_reference("foo", "a.py", 2)
    result = idx.find_references_smart("foo", "ctx.py", LanguageRegistry())
    assert result == [("foo", "a.py", 2), ("foo", "b.rs", 1)]


# === extension filtering ==================================================


def test_find_definitions_by_extension() -> None:
    """``find_definitions_by_extension`` keeps only matching-extension paths."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "a.py", 1)
    idx.add_definition("foo", "b.rs", 2)
    assert idx.find_definitions_by_extension("foo", {"py"}) == [("a.py", 1)]
    assert idx.find_references_by_extension("foo", {"py"}) == []


def test_find_references_by_extension() -> None:
    """``find_references_by_extension`` keeps only matching-extension paths."""
    idx = ScopeGraphIndex()
    idx.add_reference("foo", "a.py", 1)
    idx.add_reference("foo", "b.rs", 2)
    assert idx.find_references_by_extension("foo", {"rs"}) == [("foo", "b.rs", 2)]


# === statistics ===========================================================


def test_stats_counts_locations() -> None:
    """``stats`` returns ``(file_count, definition_count, reference_count)``."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "a.py", 1)
    idx.add_definition("foo", "b.py", 2)
    idx.add_reference("foo", "a.py", 5)
    idx.add_reference("foo", "c.py", 6)
    idx.add_reference("bar", "a.py", 7)
    # no file_meta set -> file_count 0; 2 def locations; 3 ref locations.
    assert idx.stats() == (0, 2, 3)


def test_top_referenced_symbols_sorted_desc() -> None:
    """``top_referenced_symbols`` returns ``(name, count)`` sorted by count desc."""
    idx = ScopeGraphIndex()
    idx.add_reference("foo", "a.py", 1)
    idx.add_reference("foo", "b.py", 2)
    idx.add_reference("foo", "c.py", 3)
    idx.add_reference("bar", "a.py", 4)
    assert idx.top_referenced_symbols(2) == [("foo", 3), ("bar", 1)]


# === query version ========================================================


def test_needs_query_rebuild_legacy_then_stamped() -> None:
    """A Legacy index always rebuilds; a stamped one rebuilds iff version differs."""
    idx = ScopeGraphIndex()
    # Legacy (unstamped) -> always needs a rebuild.
    assert idx.needs_query_rebuild(5) is True
    idx.set_query_version(5)
    assert idx.needs_query_rebuild(5) is False
    assert idx.needs_query_rebuild(6) is True


# === maintenance ==========================================================


def test_compact_is_idempotent_and_preserves_data() -> None:
    """``compact`` is idempotent and leaves the indexed data intact."""
    idx = ScopeGraphIndex()
    idx.add_definition("foo", "a.py", 1)
    idx.add_reference("foo", "a.py", 2)
    idx.add_alias("fn", "foo")
    idx.compact()
    idx.compact()  # second call must not raise or mutate
    assert idx.find_definitions("foo") == [("a.py", 1)]
    assert idx.find_references("foo") == [("a.py", 2)]


# === barrel surface =======================================================


def test_scope_graph_index_re_exported_at_crate_root() -> None:
    """The crate-root barrel re-exports ``ScopeGraphIndex`` (mirrors grok lib.rs)."""
    assert ScopeGraphIndexFromRoot is ScopeGraphIndex
    # both import paths construct an empty index.
    assert ScopeGraphIndexFromRoot() is not None
    assert ScopeGraphIndex() is not None
