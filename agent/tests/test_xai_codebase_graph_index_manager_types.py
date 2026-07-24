"""Black-box tests for the ``index_manager`` type-layer foundation (R306a).

Ported **by function** from grok ``xai-codebase-graph/src/index_manager.rs``.
This suite covers the 7 pure-data symbols that land in R306a (the channel /
tree-sitter runtime lands in R306b-R306g); grok has no dedicated unit tests
for these types (they are exercised indirectly via the actor), so the suite
adds Python-specific coverage for the contract surface:

* ``MAX_INDEXABLE_FILE_SIZE`` equals grok's 5 MB constant
* :class:`FileEventKind` 4 variants + the ``Removed`` (not ``Deleted``) spelling
* :class:`FileEvent` batch container + the 5 factories (incl. rename's
  ``[from, to]`` path layout)
* :class:`SymbolLocation` factories + ``as_path`` + equality
* :class:`QueryResult` field surface
* :class:`QueryError` 4-variant tagged union (discriminator + payload)
* :class:`IndexManagerConfig` defaults + fluent builder chain
* the crate-root barrel contract (5 non-colliding symbols exported;
  ``FileEvent`` / ``FileEventKind`` stay bound to the ``types`` version)
"""

from __future__ import annotations

from pathlib import PurePath

import minimax_code.xai_codebase_graph as xcg_root
from minimax_code.xai_codebase_graph import index_manager as im
from minimax_code.xai_codebase_graph.index_manager import (
    MAX_INDEXABLE_FILE_SIZE,
    FileEvent,
    FileEventKind,
    IndexManagerConfig,
    QueryError,
    QueryResult,
    SymbolLocation,
)
from minimax_code.xai_codebase_graph.types import (
    FileEvent as TypesFileEvent,
)
from minimax_code.xai_codebase_graph.types import (
    FileEventKind as TypesFileEventKind,
)

# === MAX_INDEXABLE_FILE_SIZE ==============================================


def test_max_indexable_file_size_is_five_megabytes() -> None:
    """grok ``5 * 1024 * 1024`` -- files above this are skipped on intake."""
    assert MAX_INDEXABLE_FILE_SIZE == 5 * 1024 * 1024
    assert MAX_INDEXABLE_FILE_SIZE == 5_242_880


# === FileEventKind ========================================================


def test_file_event_kind_has_four_variants() -> None:
    """grok ``FileEventKind`` (L62-L71) has exactly the 4 notify-derived kinds."""
    members = {member.name for member in FileEventKind}
    assert members == {"CREATED", "MODIFIED", "REMOVED", "RENAMED"}


def test_file_event_kind_delete_variant_is_removed_not_deleted() -> None:
    """grok L68 names the delete variant ``Removed`` (the ``types`` enum spells
    it ``Deleted`` -- the divergence is the fossil proving the two are
    independent types, not aliases).
    """
    assert hasattr(FileEventKind, "REMOVED")
    assert not hasattr(FileEventKind, "DELETED")
    assert FileEventKind.REMOVED.value == "removed"


def test_file_event_kind_values_are_lowercase_strings() -> None:
    """The enum value mirrors the lowercase notify-style kind string."""
    assert FileEventKind.CREATED.value == "created"
    assert FileEventKind.MODIFIED.value == "modified"
    assert FileEventKind.RENAMED.value == "renamed"


# === FileEvent (batch container) ==========================================


def test_file_event_new_carries_paths_and_kind() -> None:
    """grok ``FileEvent::new`` -- explicit paths list + kind (batch entry point)."""
    event = FileEvent.new(["a.py", "b.py"], FileEventKind.MODIFIED)
    assert event.paths == ["a.py", "b.py"]
    assert event.kind is FileEventKind.MODIFIED


def test_file_event_new_copies_paths_list() -> None:
    """``new`` takes ownership of its own list (caller mutations do not leak in)."""
    source = ["x.py"]
    event = FileEvent.new(source, FileEventKind.CREATED)
    source.append("y.py")
    assert event.paths == ["x.py"]  # isolation: the event's list is its own


def test_file_event_created_modified_removed_single_path() -> None:
    """The 3 single-path factories wrap the path in a one-element list."""
    assert FileEvent.created("a.py") == FileEvent(["a.py"], FileEventKind.CREATED)
    assert FileEvent.modified("a.py") == FileEvent(["a.py"], FileEventKind.MODIFIED)
    assert FileEvent.removed("a.py") == FileEvent(["a.py"], FileEventKind.REMOVED)


def test_file_event_renamed_stores_from_then_to() -> None:
    """grok ``FileEvent::renamed`` stores ``[from, to]`` (L104-L106)."""
    event = FileEvent.renamed("old.py", "new.py")
    assert event.paths == ["old.py", "new.py"]
    assert event.kind is FileEventKind.RENAMED
    assert len(event.paths) == 2


def test_file_event_uses_slots() -> None:
    """``slots=True`` -> instances carry no ``__dict__`` (lean per-event memory)."""
    event = FileEvent.created("a.py")
    assert not hasattr(event, "__dict__")


def test_index_manager_file_event_is_distinct_from_types_version() -> None:
    """The two ``FileEvent`` types are NOT unified (R300 deliberate split).

    ``index_manager.FileEvent`` is the batch container; ``types.FileEvent`` is
    the single-file tagged union. They have different field surfaces and must
    not be the same class -- a barrel-reconciliation brick will later align
    the crate-root binding with grok, but the types stay distinct forever.
    """
    assert FileEvent is not TypesFileEvent
    assert FileEventKind is not TypesFileEventKind

    # the batch container carries ``paths`` + ``kind``; the types union carries
    # ``kind`` + ``path`` + ``from_path`` -- different field surfaces.
    batch = FileEvent.created("a.py")
    union = TypesFileEvent.created("a.py")
    assert hasattr(batch, "paths")
    assert not hasattr(union, "paths")
    assert hasattr(union, "path")
    assert not hasattr(batch, "path")


# === SymbolLocation =======================================================


def test_symbol_location_new_has_no_matched_symbol() -> None:
    """grok ``SymbolLocation::new`` -> ``matched_symbol`` defaults to ``None``."""
    loc = SymbolLocation.new("src/foo.py", 42)
    assert loc.path == "src/foo.py"
    assert loc.line == 42
    assert loc.matched_symbol is None


def test_symbol_location_with_symbol_carries_name() -> None:
    """grok ``with_symbol`` -> ``matched_symbol`` set (alias resolution use case)."""
    loc = SymbolLocation.with_symbol("src/foo.py", 42, "Foo")
    assert loc.matched_symbol == "Foo"


def test_symbol_location_as_path_returns_purepath() -> None:
    """grok ``as_path`` -> ``Path::new(&self.path)``; Python returns a ``PurePath``.

    No filesystem access; the path is relative to the index ``root_path``. The
    ``str()`` form is platform-native (Windows flips ``/`` -> ``\\``, matching
    grok's ``Path`` display), so string equality is checked via ``as_posix()``
    which is separator-stable across platforms.
    """
    loc = SymbolLocation.new("src/foo.py", 1)
    p = loc.as_path()
    assert isinstance(p, PurePath)
    assert p.as_posix() == "src/foo.py"


def test_symbol_location_as_path_handles_backslash_separator() -> None:
    """Windows-style relative paths round-trip through ``PurePath``."""
    loc = SymbolLocation.new("src\\foo.py", 1)
    # PurePath normalises per platform; the parts are preserved.
    assert loc.as_path().parts == ("src", "foo.py")


def test_symbol_location_equality_compares_all_fields() -> None:
    """grok ``#[derive(PartialEq, Eq)]`` -> equality spans every field."""
    a = SymbolLocation.with_symbol("a.py", 1, "Foo")
    b = SymbolLocation.with_symbol("a.py", 1, "Foo")
    c = SymbolLocation.with_symbol("a.py", 1, "Bar")
    d = SymbolLocation.new("a.py", 1)  # matched_symbol=None
    assert a == b
    assert a != c  # different matched_symbol
    assert a != d  # None vs "Foo"


def test_symbol_location_uses_slots() -> None:
    """``slots=True`` -> no ``__dict__`` on instances."""
    loc = SymbolLocation.new("a.py", 1)
    assert not hasattr(loc, "__dict__")


# === QueryResult ==========================================================


def test_query_result_holds_symbol_and_locations() -> None:
    """grok ``QueryResult { symbol, locations }`` field surface."""
    locs = [SymbolLocation.new("a.py", 1)]
    result = QueryResult(symbol="foo", locations=locs)
    assert result.symbol == "foo"
    assert result.locations is locs


def test_query_result_uses_slots() -> None:
    """``slots=True`` -> no ``__dict__`` on instances."""
    result = QueryResult(symbol="foo", locations=[])
    assert not hasattr(result, "__dict__")


# === QueryError (tagged union) ============================================


def test_query_error_file_not_found_carries_path() -> None:
    """grok ``FileNotFound(PathBuf)`` -> ``kind`` + ``path`` payload."""
    err = QueryError.file_not_found("missing.py")
    assert err.kind == QueryError.KIND_FILE_NOT_FOUND
    assert err.path == "missing.py"
    assert err.row is None and err.col is None
    assert err.language is None and err.message is None


def test_query_error_no_symbol_at_position_carries_row_col() -> None:
    """grok ``NoSymbolAtPosition { row, col }`` -> named-field payload."""
    err = QueryError.no_symbol_at_position(row=10, col=3)
    assert err.kind == QueryError.KIND_NO_SYMBOL_AT_POSITION
    assert err.row == 10
    assert err.col == 3
    assert err.path is None


def test_query_error_unsupported_language_carries_language() -> None:
    """grok ``UnsupportedLanguage(String)`` -> ``language`` payload."""
    err = QueryError.unsupported_language("brainfuck")
    assert err.kind == QueryError.KIND_UNSUPPORTED_LANGUAGE
    assert err.language == "brainfuck"


def test_query_error_parse_error_carries_message() -> None:
    """grok ``ParseError(String)`` -> ``message`` payload."""
    err = QueryError.parse_error("unexpected EOF")
    assert err.kind == QueryError.KIND_PARSE_ERROR
    assert err.message == "unexpected EOF"


def test_query_error_kinds_are_distinct() -> None:
    """The 4 discriminator strings are pairwise distinct."""
    kinds = {
        QueryError.KIND_FILE_NOT_FOUND,
        QueryError.KIND_NO_SYMBOL_AT_POSITION,
        QueryError.KIND_UNSUPPORTED_LANGUAGE,
        QueryError.KIND_PARSE_ERROR,
    }
    assert len(kinds) == 4


def test_query_error_uses_slots() -> None:
    """``slots=True`` -> no ``__dict__`` on instances (KIND_* are class attrs)."""
    err = QueryError.parse_error("x")
    assert not hasattr(err, "__dict__")
    # the KIND_* constants live on the class, reachable from the instance too.
    assert err.KIND_PARSE_ERROR == "parse_error"


# === IndexManagerConfig ===================================================


def test_index_manager_config_new_defaults() -> None:
    """grok ``new`` (L513-L518): cache_path=None, load=True, save=True."""
    cfg = IndexManagerConfig.new("/workspace")
    assert cfg.root_path == "/workspace"
    assert cfg.cache_path is None
    assert cfg.load_from_cache is True
    assert cfg.save_to_cache is True


def test_index_manager_config_with_cache_path_fluent() -> None:
    """grok ``with_cache_path`` returns ``self`` (fluent move semantics)."""
    cfg = IndexManagerConfig.new("/workspace")
    returned = cfg.with_cache_path("/cache/index.bin")
    assert returned is cfg  # fluent: same object
    assert cfg.cache_path == "/cache/index.bin"


def test_index_manager_config_without_cache_load_fluent() -> None:
    """grok ``without_cache_load`` flips ``load_from_cache`` to False."""
    cfg = IndexManagerConfig.new("/workspace").without_cache_load()
    assert cfg.load_from_cache is False
    assert cfg.save_to_cache is True  # untouched


def test_index_manager_config_without_cache_save_fluent() -> None:
    """grok ``without_cache_save`` flips ``save_to_cache`` to False."""
    cfg = IndexManagerConfig.new("/workspace").without_cache_save()
    assert cfg.save_to_cache is False
    assert cfg.load_from_cache is True  # untouched


def test_index_manager_config_builder_chain() -> None:
    """The 3 builder methods chain (mirrors grok's ``mut self -> Self`` chain)."""
    cfg = (
        IndexManagerConfig.new("/ws")
        .with_cache_path("/c.bin")
        .without_cache_load()
        .without_cache_save()
    )
    assert cfg.root_path == "/ws"
    assert cfg.cache_path == "/c.bin"
    assert cfg.load_from_cache is False
    assert cfg.save_to_cache is False


def test_index_manager_config_uses_slots() -> None:
    """``slots=True`` -> no ``__dict__`` on instances."""
    cfg = IndexManagerConfig.new("/workspace")
    assert not hasattr(cfg, "__dict__")


# === barrel contract ======================================================


def test_index_manager_module_all_contains_type_layer_symbols() -> None:
    """The leaf module re-exports the 7 R306a type symbols + ``is_binary_content``.

    R306b added ``is_binary_content`` (grok ``lib.rs`` L86 PUB ``fn``) and R306c
    added the 15 command symbols (:class:`IndexCommand` + 14 variants) to the
    leaf ``__all__``. The 8 type-layer symbols are now a **subset** of the full
    ``__all__`` (8 -> 23) -- this asserts the subset invariant so the barrel
    stays a single source of truth across the type + helper + command suites
    without coupling this suite to the command-layer count.
    """
    type_layer = {
        "MAX_INDEXABLE_FILE_SIZE",
        "FileEvent",
        "FileEventKind",
        "QueryResult",
        "SymbolLocation",
        "QueryError",
        "IndexManagerConfig",
        "is_binary_content",
    }
    assert type_layer.issubset(set(im.__all__))


def test_crate_root_exports_five_non_colliding_symbols() -> None:
    """The 5 non-colliding index_manager symbols reach the crate root.

    grok ``lib.rs`` L84-L86 re-exports these verbatim; the Python port mirrors
    the surface for the symbols that do not clash with the ``types`` barrel.
    """
    for sym in (
        "MAX_INDEXABLE_FILE_SIZE",
        "QueryError",
        "QueryResult",
        "SymbolLocation",
        "IndexManagerConfig",
    ):
        assert sym in xcg_root.__all__, f"{sym} missing from crate-root barrel"
        assert hasattr(xcg_root, sym)


def test_crate_root_file_event_binds_index_manager_version_after_r308() -> None:
    """The crate-root ``FileEvent`` binds the ``index_manager`` version (R308).

    R308 barrel reconciliation switches the crate root from the ``types``
    single-file union (R300 placement, preserved through R306a) to the
    ``index_manager`` batch container, matching grok ``lib.rs`` L84-L86 in one
    atomic edit. Pre-R308 this test pinned the opposite (types-bound) state;
    the ``types``-flavored ``FileEvent`` / ``FileEventKind`` stay reachable via
    the ``types`` subpackage.
    """
    # the crate-root symbol IS the index_manager batch version (R308 switch).
    assert xcg_root.FileEvent is FileEvent
    assert xcg_root.FileEventKind is FileEventKind
    # the types single-file union is NOT at the crate root.
    assert xcg_root.FileEvent is not TypesFileEvent
    # ... but it is reachable through the types subpackage (flavors stay split).
    assert TypesFileEvent is not FileEvent


def test_crate_root_query_error_is_index_manager_version() -> None:
    """``QueryError`` (no ``types`` collision) binds the index_manager symbol."""
    assert xcg_root.QueryError is QueryError
    assert xcg_root.SymbolLocation is SymbolLocation
    assert xcg_root.IndexManagerConfig is IndexManagerConfig
    assert xcg_root.MAX_INDEXABLE_FILE_SIZE is MAX_INDEXABLE_FILE_SIZE
