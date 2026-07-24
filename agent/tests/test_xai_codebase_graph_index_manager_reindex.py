"""Black-box tests for the ``index_manager`` incremental-reindex path (R306g).

Ported **by function** from grok ``index_manager.rs`` -- the tree-sitter-backed
incremental reindex pipeline that the actor reaches on a file-event drain:

* ``should_index`` (L1095-L1097) -> :meth:`IndexManager._should_index`
* ``remove_file`` (L1246-L1249) -> :meth:`IndexManager._remove_file`
* ``reindex_file`` (L1182-L1210) -> :meth:`IndexManager._reindex_file`
* ``get_symbol_at_position`` (L1046-L1093) -> :meth:`IndexManager._get_symbol_at_position`
* ``process_background_refresh`` (L1100-L1127) -> :meth:`IndexManager._process_background_refresh`
* ``is_identifier_like`` (L1644-L1654) -> :func:`_is_identifier_like`
* ``find_smallest_named_node_at_point`` (L1610-L1638) -> :func:`_find_smallest_named_node_at_position`
* ``intern_symbols_directly`` (L1447-L1511) -> :func:`_intern_symbols_into_index`

The tree-sitter runtime is absent in this environment
(``_get_parser_and_query`` returns ``(None, None)``), so the suite splits into:

* **pure-logic helper tests** (section A) -- the hit-test + intern helpers are
  duck-typed (any object with ``type`` / ``start_point`` / ``end_point`` /
  ``children`` / ``start_byte`` / ``end_byte`` satisfies the contract), so they
  run against lightweight fakes with no tree-sitter dependency.
* **error-path + lifecycle tests** (sections B-F) -- ``_FakeRegistry`` gives a
  deterministic ``is_supported`` / ``for_file_path`` pair (the real
  ``LanguageRegistry`` keys off tree-sitter grammar loading, which is also
  absent here), and ``monkeypatch`` swaps in a fake parser + a controlled
  ``extract_symbols_fast`` for the two happy-path branches.

The two R306g alignment fixes are pinned by dedicated tests:

* ``_should_index`` translates the absolute path to its root-relative form
  *before* probing ``is_under_hidden_dir`` (grok L1096), so a workspace that
  itself lives under a hidden dir does not gate every file out
  (:func:`test_should_index_true_when_root_under_hidden_dir`).
* ``_process_background_refresh`` gates ``stale_files`` on
  ``registry.is_supported`` (grok L1113) and calls ``_save_cache``
  unconditionally at sweep end (grok L1126)
  (:func:`test_background_refresh_gates_stale_on_support` /
  :func:`test_background_refresh_saves_cache_unconditionally`).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from minimax_code.xai_codebase_graph import index_manager as im
from minimax_code.xai_codebase_graph.index_manager import (
    BackgroundRefreshCommand,
    ExitBeacon,
    IndexManager,
    IndexManagerConfig,
    QueryError,
)

# === fakes (duck-typed tree-sitter + index + registry stand-ins) =========


class _Point:
    """tree-sitter ``Point`` stand-in: just ``row`` + ``column``."""

    def __init__(self, row: int, column: int) -> None:
        self.row = row
        self.column = column


class _FakeNode:
    """Minimal tree-sitter ``Node`` stand-in for the hit-test + intern paths.

    Only the attributes the duck-typed helpers read are populated: ``type``
    (the grammar kind), ``children`` (the DFS walk), byte extents (the UTF-8
    decode in ``_get_symbol_at_position``), and point extents (the inclusive
    range hit-test in ``_find_smallest_named_node_at_position``).
    """

    def __init__(
        self,
        type_: str,
        *,
        children: list[_FakeNode] | None = None,
        start_point: tuple[int, int] = (0, 0),
        end_point: tuple[int, int] = (0, 0),
        start_byte: int = 0,
        end_byte: int = 0,
    ) -> None:
        self.type = type_
        self.children = children if children is not None else []
        self.start_point = _Point(*start_point)
        self.end_point = _Point(*end_point)
        self.start_byte = start_byte
        self.end_byte = end_byte


class _FakeTree:
    """tree-sitter ``Tree`` stand-in: just a ``root_node``."""

    def __init__(self, root_node: _FakeNode) -> None:
        self.root_node = root_node


class _FakeParser:
    """tree-sitter ``Parser`` stand-in: ``parse`` returns a fixed tree.

    Returns the same tree on every call (the reindex + symbol-at-position
    paths each parse once per invocation; a single canned tree suffices).
    """

    def __init__(self, tree: _FakeTree | None) -> None:
        self._tree = tree

    def parse(self, _src: bytes) -> _FakeTree | None:
        return self._tree


class _FakeRange:
    """``Range`` stand-in: only ``start_line()`` is read by ``_intern_symbols_into_index``."""

    def __init__(self, line: int) -> None:
        # ``Range.start_line()`` is 0-indexed; the intern path adds 1.
        self._line = line

    def start_line(self) -> int:
        return self._line


class _FakeIndex:
    """Minimal ``ScopeGraphIndex`` stand-in: records every mutating call.

    Lets the intern + reindex + background-refresh tests assert exactly which
    methods fired with which args, without the real ``ScopeGraphIndex``'s
    tree-sitter-coupled query surface. The ``intern`` map assigns a synthetic
    ``StringId`` per distinct string so the ``path_id`` threading is verifiable.
    """

    def __init__(self) -> None:
        self.removed: list[str] = []
        self.interned: dict[str, int] = {}
        self.definitions: list[tuple[str, int, int]] = []
        self.references: list[tuple[str, int, int]] = []
        self.aliases: list[tuple[str, str]] = []
        self.metas: dict[str, Any] = {}

    def remove_file(self, file_path: str) -> None:
        self.removed.append(file_path)

    def intern(self, s: str) -> int:
        if s not in self.interned:
            self.interned[s] = len(self.interned)
        return self.interned[s]

    def add_definition_with_path_id(self, name: str, path_id: int, line: int) -> None:
        self.definitions.append((name, path_id, line))

    def add_reference_with_path_id(self, name: str, path_id: int, line: int) -> None:
        self.references.append((name, path_id, line))

    def add_alias(self, alias_name: str, original_name: str) -> None:
        self.aliases.append((alias_name, original_name))

    def set_file_meta(self, path: str, meta: Any) -> None:
        self.metas[path] = meta


class _FakeRegistry:
    """``LanguageRegistry`` stand-in with a deterministic support table.

    Supports ``*.py`` (returns a sentinel config for ``for_file_path``). The
    real registry keys ``is_supported`` off tree-sitter grammar loading, which
    is absent here, so a fake keeps the path-translation + hidden-dir tests
    focused on pure-path logic rather than grammar-availability noise.
    """

    _SENTINEL = object()

    def is_supported(self, path: str) -> bool:
        return str(path).endswith(".py")

    def for_file_path(self, path: str) -> Any:
        return self._SENTINEL if self.is_supported(path) else None


def _make_actor_with(
    *,
    index: _FakeIndex | None = None,
    registry: Any | None = None,
    config: IndexManagerConfig | None = None,
    mailbox: asyncio.Queue[Any] | None = None,
    beacon: ExitBeacon | None = None,
) -> IndexManager:
    """Build an :class:`IndexManager` with fakes (the ``_make_actor`` fixture
    in the actor suite hard-codes ``LanguageRegistry.new()``; tests here need a
    custom registry, so they go through this keyword-only constructor)."""
    return IndexManager(
        index=index if index is not None else _FakeIndex(),
        registry=registry if registry is not None else _FakeRegistry(),
        config=config if config is not None else IndexManagerConfig.new(".").without_cache_save(),
        mailbox=mailbox if mailbox is not None else asyncio.Queue(),
        beacon=beacon if beacon is not None else ExitBeacon(),
    )


# === A. module-level helpers (pure logic, no actor) =====================


def test_identifier_kinds_has_eight_entries() -> None:
    """grok's 8-kind identifier table (L1707-L1716) ports as a frozen set.

    ``identifier`` / ``type_identifier`` / ``property_identifier`` /
    ``field_identifier`` / ``shorthand_property_identifier`` /
    ``shorthand_property_identifier_pattern`` / ``attribute`` (Python) /
    ``package_identifier`` (Go). Sampling a few of each family plus the count
    keeps the test resilient to future grammar additions while pinning the
    table's shape (frozen, non-empty, multi-language).
    """
    kinds = im._IDENTIFIER_KINDS
    assert isinstance(kinds, frozenset)
    assert len(kinds) == 8
    for sample in ("identifier", "type_identifier", "attribute", "package_identifier"):
        assert sample in kinds


@pytest.mark.parametrize(
    "kind",
    sorted(im._IDENTIFIER_KINDS),
)
def test_is_identifier_like_true_for_each_kind(kind: str) -> None:
    """Every grammar kind in the table classifies as an identifier."""
    node = _FakeNode(kind)
    assert im._is_identifier_like(node) is True


@pytest.mark.parametrize(
    "kind",
    ["keyword", "return_statement", "block", "argument_list", "(", "comment"],
)
def test_is_identifier_like_false_for_non_identifier(kind: str) -> None:
    """Grammar keywords / structural nodes are NOT identifiers (the hit-test
    walks past them to the named leaf underneath)."""
    node = _FakeNode(kind)
    assert im._is_identifier_like(node) is False


def test_find_node_returns_none_when_point_outside_range() -> None:
    """A point outside the node's byte/point extent -> no hit (``None``).

    Covers both the wrong-row case and the past-end-column case. grok's
    ``point > end`` is the exclusive-past-end boundary: a column one past the
    node's last cell is already outside.
    """
    leaf = _FakeNode("identifier", start_point=(0, 0), end_point=(0, 3))
    assert im._find_smallest_named_node_at_position(leaf, 1, 0) is None  # wrong row
    assert im._find_smallest_named_node_at_position(leaf, 0, 4) is None  # past end


def test_find_node_inclusive_on_both_endpoints() -> None:
    """Both endpoints are inclusive: the first and last cell still hit (grok
    ``point >= start && point <= end``)."""
    leaf = _FakeNode("identifier", start_point=(0, 0), end_point=(0, 3))
    assert im._find_smallest_named_node_at_position(leaf, 0, 0) is leaf  # == start
    assert im._find_smallest_named_node_at_position(leaf, 0, 3) is leaf  # == end


def test_find_node_descends_to_tightest_identifier_child() -> None:
    """The DFS returns the *smallest* identifier-like descendant, not the
    outermost containing node. A ``function_definition`` wrapping an
    ``identifier`` name resolves to the identifier."""
    child = _FakeNode("identifier", start_point=(0, 4), end_point=(0, 7))
    parent = _FakeNode(
        "function_definition",
        children=[child],
        start_point=(0, 0),
        end_point=(0, 20),
    )
    assert im._find_smallest_named_node_at_position(parent, 0, 5) is child


def test_find_node_returns_none_when_no_identifier_in_subtree() -> None:
    """A non-identifier node whose children are also non-identifiers yields
    ``None`` (the cursor sits on a keyword / bracket / structural node)."""
    parent = _FakeNode(
        "block",
        children=[_FakeNode("return_statement", start_point=(0, 0), end_point=(0, 6))],
        start_point=(0, 0),
        end_point=(0, 20),
    )
    assert im._find_smallest_named_node_at_position(parent, 0, 2) is None


def test_find_node_first_identifier_sibling_wins() -> None:
    """A child that contains the point but yields nothing does NOT abort the
    walk -- a later sibling may hold the point (grok L1736-L1738)."""
    later = _FakeNode("identifier", start_point=(0, 10), end_point=(0, 14))
    parent = _FakeNode(
        "module",
        children=[
            _FakeNode("block", start_point=(0, 0), end_point=(0, 20)),  # no id child
            later,
        ],
        start_point=(0, 0),
        end_point=(0, 20),
    )
    assert im._find_smallest_named_node_at_position(parent, 0, 12) is later


def test_intern_symbols_dispatches_definitions_references_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``_intern_symbols_into_index`` reuses ``extract_symbols_fast`` for the
    capture classification, then dispatches each symbol to the right
    ``add_*`` method under a single interning of the file's path.

    Lines are 1-indexed (``Range.start_line()`` is 0-indexed -> ``+ 1``), the
    convention ``ScopeGraphIndex.add_file`` enforces.
    """
    index = _FakeIndex()
    query = object()
    root = _FakeNode("module")
    # Controlled extraction: one def, one ref, one alias pair.
    monkeypatch.setattr(
        im,
        "extract_symbols_fast",
        lambda _q, _n, _s, _c: (
            [("foo", _FakeRange(line=3))],          # start_line 3 -> file line 4
            [("foo", _FakeRange(line=9))],          # start_line 9 -> file line 10
            [("bar", "foo")],                        # alias bar -> foo
        ),
    )
    im._intern_symbols_into_index(index, "src/mod.py", query, root, b"")
    # path interned exactly once, reused across def + ref.
    assert index.interned == {"src/mod.py": 0}
    assert index.definitions == [("foo", 0, 4)]
    assert index.references == [("foo", 0, 10)]
    assert index.aliases == [("bar", "foo")]


def test_intern_symbols_empty_extraction_is_a_no_op(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An empty extraction still interns the path (so the file is known) but
    records no symbols."""
    index = _FakeIndex()
    monkeypatch.setattr(im, "extract_symbols_fast", lambda *_a, **_k: ([], [], []))
    im._intern_symbols_into_index(index, "empty.py", object(), _FakeNode("module"), b"")
    assert index.interned == {"empty.py": 0}
    assert index.definitions == []
    assert index.references == []
    assert index.aliases == []


# === B. _should_index (pure-path gate) ==================================


def test_should_index_true_for_supported_non_hidden() -> None:
    """A supported extension under a visible path passes both gates."""
    actor = _make_actor_with()
    assert actor._should_index("a.py") is True


def test_should_index_false_for_unsupported_extension() -> None:
    """The language gate rejects files the registry has no grammar for."""
    actor = _make_actor_with()
    assert actor._should_index("a.txt") is False


def test_should_index_false_for_hidden_directory() -> None:
    """A path under a ``.git`` / ``.hidden`` component is never indexed (the
    watcher still observes it)."""
    actor = _make_actor_with()
    assert actor._should_index(".git/hooks/a.py") is False
    assert actor._should_index("src/.cache/a.py") is False


def test_should_index_true_when_root_under_hidden_dir(
    tmp_path: Path,
) -> None:
    """R306g alignment fix: the root-relative translation happens *before* the
    hidden-dir probe, so a workspace that itself lives under a hidden dir
    (e.g. ``/home/u/.cache/repo``) does not falsely gate every file out.

    Without the ``to_relative_path`` translation (grok L1096), the absolute
    path would carry the ``.cache`` component and ``is_under_hidden_dir``
    would reject every file -- a whole-workspace blackout. The relative key
    strips the root's components, so only genuine in-workspace hidden dirs
    gate the file.
    """
    # A real workspace root nested under a hidden dir (created so the path
    # resolves on platforms that stat during ``to_relative_path``).
    root = tmp_path / ".cache" / "repo"
    root.mkdir(parents=True)
    config = IndexManagerConfig.new(str(root)).without_cache_save()
    actor = _make_actor_with(config=config)
    # A normal source file under a normal subdir: relative key has no hidden
    # component, so the file passes despite the root living under ``.cache``.
    src = str(root / "src" / "main.py")
    assert actor._should_index(src) is True


# === C. _remove_file (absolute -> relative key translation) =============


def test_remove_file_translates_absolute_to_relative_key(
    tmp_path: Path,
) -> None:
    """The absolute event-path is translated to the relative-string index key
    before ``ScopeGraphIndex.remove_file`` -- a raw absolute key would silently
    miss (no matching interned string). Centralizing here keeps the
    coalesced-apply + background-eviction callers honest."""
    index = _FakeIndex()
    root = tmp_path
    config = IndexManagerConfig.new(str(root)).without_cache_save()
    actor = _make_actor_with(index=index, config=config)
    actor._remove_file(str(root / "src" / "mod.py"))
    assert index.removed == ["src/mod.py"]


def test_remove_file_strips_root_components_only(tmp_path: Path) -> None:
    """The translation strips exactly the root prefix; deeper relative paths
    keep their structure (the index keys are POSIX-relative)."""
    index = _FakeIndex()
    root = tmp_path
    config = IndexManagerConfig.new(str(root)).without_cache_save()
    actor = _make_actor_with(index=index, config=config)
    actor._remove_file(str(root / "a" / "b" / "c.py"))
    assert index.removed == ["a/b/c.py"]


# === D. _reindex_file (error paths + happy path) ========================


def test_reindex_file_skips_unsupported_language(tmp_path: Path) -> None:
    """A rename-into-``.txt`` event that slips past the extension gate is
    caught by ``for_file_path`` -> ``None`` (grok L1187). The file's old
    symbols are dropped first (the removal always runs)."""
    index = _FakeIndex()
    file = tmp_path / "renamed.txt"
    file.write_bytes(b"not python")
    config = IndexManagerConfig.new(str(tmp_path)).without_cache_save()
    actor = _make_actor_with(index=index, config=config)
    actor._reindex_file(str(file))
    # removal ran (drop old symbols), but no intern / meta (no grammar).
    assert index.removed == ["renamed.txt"]
    assert index.interned == {}
    assert index.metas == {}


def test_reindex_file_skips_missing_file(tmp_path: Path) -> None:
    """A file deleted between the event and the drain fails ``os.stat``; the
    pipeline aborts silently after the removal (grok returns ``()``)."""
    index = _FakeIndex()
    config = IndexManagerConfig.new(str(tmp_path)).without_cache_save()
    actor = _make_actor_with(index=index, config=config)
    actor._reindex_file(str(tmp_path / "ghost.py"))
    assert index.removed == ["ghost.py"]
    assert index.interned == {}


def test_reindex_file_skips_empty_file(tmp_path: Path) -> None:
    """A zero-byte file carries no symbols -- skipped before the read/parse
    (grok L1189 size gate)."""
    index = _FakeIndex()
    file = tmp_path / "empty.py"
    file.write_bytes(b"")
    config = IndexManagerConfig.new(str(tmp_path)).without_cache_save()
    actor = _make_actor_with(index=index, config=config)
    actor._reindex_file(str(file))
    assert index.removed == ["empty.py"]
    assert index.interned == {}


def test_reindex_file_skips_oversized_file(tmp_path: Path) -> None:
    """Files over ``MAX_INDEXABLE_FILE_SIZE`` (5 MB) skip -- no symbols worth
    the memory (grok L1189)."""
    index = _FakeIndex()
    file = tmp_path / "huge.py"
    file.write_bytes(b"x" * (im.MAX_INDEXABLE_FILE_SIZE + 1))
    config = IndexManagerConfig.new(str(tmp_path)).without_cache_save()
    actor = _make_actor_with(index=index, config=config)
    actor._reindex_file(str(file))
    assert index.removed == ["huge.py"]
    assert index.interned == {}


def test_reindex_file_skips_binary_content(tmp_path: Path) -> None:
    """A NUL-byte prefix flags the buffer as binary -- skipped before the
    parse (grok ``is_binary_content`` reuses the read buffer)."""
    index = _FakeIndex()
    file = tmp_path / "blob.py"
    file.write_bytes(b"\x00\x01\x02\x03 binary garbage")
    config = IndexManagerConfig.new(str(tmp_path)).without_cache_save()
    actor = _make_actor_with(index=index, config=config)
    actor._reindex_file(str(file))
    assert index.removed == ["blob.py"]
    assert index.interned == {}


def test_reindex_file_skips_when_parser_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the tree-sitter runtime is absent, ``_get_parser_and_query``
    returns ``(None, None)``; the pipeline degrades silently (grok L1194).
    This is the production reality in this environment."""
    index = _FakeIndex()
    file = tmp_path / "mod.py"
    file.write_bytes(b"def foo():\n    pass\n")
    config = IndexManagerConfig.new(str(tmp_path)).without_cache_save()
    actor = _make_actor_with(index=index, config=config)
    monkeypatch.setattr(im, "_get_parser_and_query", lambda _cfg: (None, None))
    actor._reindex_file(str(file))
    assert index.removed == ["mod.py"]
    assert index.interned == {}  # degraded: no parse, no intern


def test_reindex_file_happy_path_interns_and_sets_meta(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The full happy path: drop old symbols -> parse -> re-intern -> set meta.

    A fake parser returns a canned tree, and ``extract_symbols_fast`` is
    swapped for a controlled extraction so the intern dispatch is verifiable
    end-to-end (path interning, def/ref/alias wiring, ``FileMeta`` stamping
    for the next staleness sweep)."""
    index = _FakeIndex()
    file = tmp_path / "mod.py"
    file.write_bytes(b"def foo():\n    pass\n")
    config = IndexManagerConfig.new(str(tmp_path)).without_cache_save()
    actor = _make_actor_with(index=index, config=config)

    root_node = _FakeNode("module")
    fake_parser = _FakeParser(_FakeTree(root_node))
    fake_query = object()
    monkeypatch.setattr(
        im, "_get_parser_and_query", lambda _cfg: (fake_parser, fake_query)
    )
    monkeypatch.setattr(
        im,
        "extract_symbols_fast",
        lambda _q, _n, _s, _c: (
            [("foo", _FakeRange(line=0))],   # def on file line 1
            [("foo", _FakeRange(line=0))],   # ref on file line 1
            [],
        ),
    )
    actor._reindex_file(str(file))

    assert index.removed == ["mod.py"]              # old symbols dropped first
    assert index.interned == {"mod.py": 0}          # path interned once
    assert index.definitions == [("foo", 0, 1)]     # line = 0 + 1
    assert index.references == [("foo", 0, 1)]
    assert "mod.py" in index.metas                  # stat stamped for staleness


# === E. _get_symbol_at_position (error paths + happy path) ==============


def test_get_symbol_returns_error_when_row_is_zero() -> None:
    """``row == 0`` is the "no cursor" sentinel -> :class:`QueryError`
    (short-circuit before any FS / parse work)."""
    actor = _make_actor_with()
    result = actor._get_symbol_at_position("a.py", 0, 5)
    assert isinstance(result, QueryError)


def test_get_symbol_returns_error_when_col_is_zero() -> None:
    """``col == 0`` is the same "no cursor" sentinel."""
    actor = _make_actor_with()
    result = actor._get_symbol_at_position("a.py", 5, 0)
    assert isinstance(result, QueryError)


def test_get_symbol_returns_error_for_unsupported_language() -> None:
    """No grammar for the extension -> :class:`QueryError.unsupported_language`."""
    actor = _make_actor_with()
    result = actor._get_symbol_at_position("a.txt", 1, 1)
    assert isinstance(result, QueryError)


def test_get_symbol_returns_error_when_file_missing(tmp_path: Path) -> None:
    """A read failure (deleted between request and parse) ->
    :class:`QueryError.file_not_found`."""
    config = IndexManagerConfig.new(str(tmp_path)).without_cache_save()
    actor = _make_actor_with(config=config)
    result = actor._get_symbol_at_position(str(tmp_path / "ghost.py"), 1, 1)
    assert isinstance(result, QueryError)


def test_get_symbol_returns_error_when_parser_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Parser unavailable -> :class:`QueryError.parse_error` (the
    tree-sitter-absent production reality)."""
    file = tmp_path / "mod.py"
    file.write_bytes(b"def foo():\n    pass\n")
    config = IndexManagerConfig.new(str(tmp_path)).without_cache_save()
    actor = _make_actor_with(config=config)
    monkeypatch.setattr(im, "_get_parser_and_query", lambda _cfg: (None, None))
    result = actor._get_symbol_at_position(str(file), 1, 5)
    assert isinstance(result, QueryError)


def test_get_symbol_happy_path_returns_identifier_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The happy path: parse, hit-test, UTF-8-decode the identifier's byte span.

    ``row`` / ``col`` are 1-indexed (LSP convention), so the hit-test receives
    ``row - 1`` / ``col - 1``. The fake identifier's byte span matches ``foo``
    in ``def foo():`` (bytes [4:7])."""
    src = b"def foo():\n    pass\n"
    file = tmp_path / "mod.py"
    file.write_bytes(src)
    config = IndexManagerConfig.new(str(tmp_path)).without_cache_save()
    actor = _make_actor_with(config=config)

    # ``foo`` sits at row 0, cols 4-7 (inclusive); bytes 4-7.
    ident = _FakeNode(
        "identifier",
        start_point=(0, 4),
        end_point=(0, 7),
        start_byte=4,
        end_byte=7,
    )
    fake_parser = _FakeParser(_FakeTree(ident))
    monkeypatch.setattr(
        im, "_get_parser_and_query", lambda _cfg: (fake_parser, object())
    )
    # row=1, col=5 -> hit-test point (0, 4) == identifier start (inclusive).
    result = actor._get_symbol_at_position(str(file), 1, 5)
    assert result == "foo"


def test_get_symbol_returns_error_when_no_identifier_at_point(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cursor on a non-identifier node (keyword / bracket) -> the hit-test
    returns ``None`` -> :class:`QueryError.no_symbol_at_position`."""
    src = b"def foo():\n    pass\n"
    file = tmp_path / "mod.py"
    file.write_bytes(src)
    config = IndexManagerConfig.new(str(tmp_path)).without_cache_save()
    actor = _make_actor_with(config=config)

    # A structural node (not identifier-like) holding the point with no id child.
    structural = _FakeNode(
        "function_definition",
        start_point=(0, 0),
        end_point=(1, 8),
    )
    fake_parser = _FakeParser(_FakeTree(structural))
    monkeypatch.setattr(
        im, "_get_parser_and_query", lambda _cfg: (fake_parser, object())
    )
    result = actor._get_symbol_at_position(str(file), 1, 1)
    assert isinstance(result, QueryError)


# === F. _process_background_refresh (deleted + stale + save_cache) ======


def test_background_refresh_evicts_deleted_files() -> None:
    """Each deleted path routes through ``_remove_file`` (absolute -> relative
    key) and advances ``updates_processed`` once (grok L1122-L1123)."""
    index = _FakeIndex()
    root = Path(".")  # relative keys; the fake index records verbatim
    config = IndexManagerConfig.new(str(root)).without_cache_save()
    actor = _make_actor_with(index=index, config=config)
    before = actor._updates_processed
    cmd = BackgroundRefreshCommand(stale_files=[], deleted_files=["a.py", "b.py"])
    actor._process_background_refresh(cmd)
    assert index.removed == ["a.py", "b.py"]
    assert actor._updates_processed == before + 2


def test_background_refresh_reindexes_stale_supported_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each stale, supported file re-runs ``_reindex_file`` (drop -> parse ->
    re-intern) and advances ``updates_processed`` once (grok L1124-L1125)."""
    index = _FakeIndex()
    file = tmp_path / "mod.py"
    file.write_bytes(b"def foo():\n    pass\n")
    config = IndexManagerConfig.new(str(tmp_path)).without_cache_save()
    actor = _make_actor_with(index=index, config=config)

    fake_parser = _FakeParser(_FakeTree(_FakeNode("module")))
    monkeypatch.setattr(
        im, "_get_parser_and_query", lambda _cfg: (fake_parser, object())
    )
    monkeypatch.setattr(im, "extract_symbols_fast", lambda *_a, **_k: ([], [], []))

    before = actor._updates_processed
    cmd = BackgroundRefreshCommand(stale_files=[str(file)], deleted_files=[])
    actor._process_background_refresh(cmd)
    assert index.removed == ["mod.py"]           # _reindex_file dropped old symbols
    assert index.interned == {"mod.py": 0}        # re-interned the path
    assert actor._updates_processed == before + 1


def test_background_refresh_gates_stale_on_support(
    tmp_path: Path,
) -> None:
    """R306g alignment fix: ``stale_files`` is gated on
    ``registry.is_supported`` (grok L1113) so an extension flip mid-sweep
    does not trigger a futile parse -- and does NOT advance
    ``updates_processed``."""
    index = _FakeIndex()
    config = IndexManagerConfig.new(str(tmp_path)).without_cache_save()
    actor = _make_actor_with(index=index, config=config)
    before = actor._updates_processed
    # ``.txt`` is unsupported by ``_FakeRegistry`` -> gated out, no reindex.
    cmd = BackgroundRefreshCommand(stale_files=[str(tmp_path / "data.txt")], deleted_files=[])
    actor._process_background_refresh(cmd)
    assert index.removed == []                    # no removal (no reindex ran)
    assert actor._updates_processed == before     # gated: counter unchanged


def test_background_refresh_saves_cache_unconditionally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R306g alignment fix: ``_save_cache`` runs unconditionally at sweep end
    (grok L1126) -- a background refresh is the natural persistence point, so
    the save fires even on an empty sweep. The ``save_to_cache`` /
    ``cache_path`` guards live inside ``_save_cache``; the caller does not
    re-check them."""
    save_calls: list[int] = []
    monkeypatch.setattr(
        IndexManager, "_save_cache", lambda self: save_calls.append(1)
    )
    actor = _make_actor_with()
    # empty sweep -- still saves once.
    actor._process_background_refresh(BackgroundRefreshCommand(stale_files=[], deleted_files=[]))
    assert save_calls == [1]
    # a second non-empty sweep -- saves again (not once-per-process).
    actor._process_background_refresh(
        BackgroundRefreshCommand(stale_files=[], deleted_files=["a.py"])
    )
    assert save_calls == [1, 1]
