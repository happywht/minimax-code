"""Black-box tests for the codebase-graph type layer (R300).

Exercises :mod:`minimax_code.xai_codebase_graph.types` -- the type foundation
fused from grok's ``xai-codebase-graph/src/types/`` (direction (2), brick 1).
Covers the four leaf types (``Position`` / ``Range`` / ``Location`` /
``FileEvent``) and the four structural types (``IndexStats`` /
``SymbolOccurrence`` / ``SymbolAlias`` / ``FileMeta``).

Migration decision matrix (grok ``types/range.rs`` + ``location.rs`` +
``file_event.rs`` + ``mod.rs`` inline ``mod tests``)
--------------------------------------------------------------

Position (range.rs L13-L130):
* ``frozen_dataclass_is_hashable_and_immutable`` -- **migrated**: grok
  ``#[derive(Hash, Copy)]`` -> frozen dataclass; instances are hashable and
  reject attribute mutation.
* ``column_alias_matches_character`` -- **migrated**: ``column`` property
  mirrors grok ``column()`` / LSP terminology.
* ``line_and_column_1indexed_accessors`` -- **migrated**: 1-indexed display
  accessors (grok ``line_1indexed`` / ``column_1indexed``).
* ``before_other_and_after_other_are_inclusive`` -- **migrated**: the
  ``before_other`` / ``after_other`` partial order (range.rs L89-L96).
* ``from_byte_reconstructs_line_and_column`` -- **migrated**: byte-offset ->
  (line, column) reconstruction (range.rs L99-L112), including the
  ``unwrap_or(0)`` quirk when no line-end exceeds the byte.
* ``shift_column_is_one_indexed_and_resets_byte`` -- **migrated**: the
  ``saturating_sub(1)`` column shift + byte reset (range.rs L115-L121).
* ``move_to_next_line_resets_column_and_byte`` -- **migrated** (range.rs
  L124-L129).

Range (range.rs L151-L365):
* ``byte_and_line_and_column_accessors`` -- **migrated**: the full accessor
  family (start/end, byte/line/column, 0/1-indexed).
* ``byte_size_len_is_empty_line_size`` -- **migrated**: size queries, incl.
  ``byte_size``'s ``+1`` and the negative-allowed ``line_size``.
* ``contains_family_inclusive_bounds`` -- **migrated**: ``contains`` /
  ``contains_check_line`` / ``contains_check_line_column`` /
  ``contains_position`` / ``contains_line``.
* ``intersects_without_byte_line_based`` -- **migrated**.
* ``equality_variants_line_only`` -- **migrated**: ``check_equality_without_byte``
  + ``equals_line_range``.
* ``from_byte_range_builds_both_endpoints`` -- **migrated** (range.rs
  L350-L354).

Location (location.rs):
* ``from_range_auto_converts_to_1indexed`` -- **migrated** (L43-L50).
* ``extension_strips_leading_dot`` -- **migrated**: grok
  ``PathBuf::extension`` semantics (no dot).
* ``parent_dir_none_for_bare_filename`` -- **migrated**: grok's ``Some("")``
  normalized to Python ``None``.
* ``line_and_column_0indexed_saturating`` -- **migrated**.
* ``display_is_file_line_column`` -- **migrated**: grok ``Display`` impl.

FileEvent (file_event.rs):
* ``four_factory_constructors_set_kind_and_path`` -- **migrated**: the 4
  variant constructors, incl. the ``Renamed`` ``path`` = ``to`` convention.
* ``primary_path_returns_to_for_renamed`` -- **migrated** (L37-L44).
* ``requires_reparse_only_created_modified`` -- **migrated** (L47-L54).
* ``affects_existing_false_only_for_created`` -- **migrated** (L57-L64).
* ``display_renders_each_variant`` -- **migrated**.

mod.rs structural types:
* ``index_stats_value_equality`` -- **migrated**.
* ``symbol_occurrence_and_alias_constructors`` -- **migrated**.
* ``file_meta_from_stat_splits_mtime`` -- **migrated**: grok
  ``from_metadata`` -> ``from_stat``; secs + nanos split, pre-epoch -> (0, 0).
* ``file_meta_is_stale_detects_change_and_deletion`` -- **migrated**
  (L110-L118).

barrel contract:
* ``types_barrel_exports_nine_symbols`` -- **migrated**: the ``types/__init__``
  ``__all__`` matches grok's public type surface + ``FileEventKind``.
* ``crate_root_barrel_mirrors_types`` -- **migrated**: both import paths work.

YAGNI boundaries (R300, documented in source, NOT tested here):
* tree-sitter bridge methods (``from_tree_sitter_point`` etc.) -- dropped;
  land with ``languages/``.
* mutators (``set_byte_offset`` etc.) -- dropped; use ``dataclasses.replace``.
* serde (``Serialize`` / ``Deserialize``) -- dropped; land with ``manager/``.
"""

from __future__ import annotations

import os
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from minimax_code.xai_codebase_graph import types as xcgt
from minimax_code.xai_codebase_graph.types import (
    FileEvent,
    FileEventKind,
    FileMeta,
    IndexStats,
    Location,
    Position,
    Range,
    SymbolAlias,
    SymbolOccurrence,
)
from minimax_code.xai_codebase_graph.types.range import Position as PositionFromLeaf
from minimax_code.xai_codebase_graph.types.range import Range as RangeFromLeaf

# === Position ==============================================================


def test_position_frozen_dataclass_is_hashable_and_immutable() -> None:
    """grok ``#[derive(Hash, Copy)]`` -> frozen dataclass: hashable + immutable."""
    pos = Position(line=3, character=5, byte_offset=42)
    assert hash(pos) == hash(Position(line=3, character=5, byte_offset=42))
    with pytest.raises(FrozenInstanceError):
        pos.line = 4  # type: ignore[misc]


def test_position_default_is_origin() -> None:
    """Default Position is (0, 0, 0) -- the file origin (grok ``Default``)."""
    pos = Position()
    assert (pos.line, pos.character, pos.byte_offset) == (0, 0, 0)


def test_position_column_alias_matches_character() -> None:
    """``column`` is a read-only alias for ``character`` (grok ``column()``)."""
    pos = Position(line=1, character=7, byte_offset=10)
    assert pos.column == pos.character == 7
    # column is a read-only property, not a settable field. frozen+slots +
    # property makes assignment raise TypeError (dataclass __setattr__
    # interaction); AttributeError would also be acceptable -- the point is
    # the assignment is rejected.
    with pytest.raises((AttributeError, TypeError)):
        pos.column = 8  # type: ignore[misc]


def test_position_to_byte_offset_passthrough() -> None:
    """``to_byte_offset()`` returns the stored byte_offset (grok alias)."""
    pos = Position(byte_offset=99)
    assert pos.to_byte_offset() == 99


def test_position_line_and_column_1indexed_accessors() -> None:
    """1-indexed accessors add 1 (grok ``line_1indexed`` / ``column_1indexed``)."""
    pos = Position(line=2, character=4)
    assert pos.line_1indexed() == 3
    assert pos.column_1indexed() == 5


def test_position_before_other_and_after_other_are_inclusive() -> None:
    """``before_other`` / ``after_other`` are inclusive at equal column (range.rs L89-L96)."""
    # strictly before (earlier line)
    assert Position(line=1, character=10).before_other(Position(line=2, character=0))
    # same line, earlier-or-equal column -> before_other is True
    assert Position(line=1, character=5).before_other(Position(line=1, character=5))
    assert Position(line=1, character=5).before_other(Position(line=1, character=9))
    assert not Position(line=1, character=9).before_other(Position(line=1, character=5))
    # after_other is the mirror
    assert Position(line=3, character=0).after_other(Position(line=2, character=10))
    assert Position(line=1, character=5).after_other(Position(line=1, character=5))


def test_position_from_byte_reconstructs_line_and_column() -> None:
    """Byte -> (line, column) reconstruction (range.rs L99-L112).

    ``line_end_indices[i]`` is the byte just past line ``i``. The line is the
    first index whose end-byte exceeds the target byte; the column is the
    byte minus the previous line's end-byte (saturating), or just the byte
    on line 0.
    """
    # Two short lines: "ab\ncd\n" -> line 0 ends at byte 3, line 1 at byte 6.
    line_end = [3, 6]
    # Byte 1 is on line 0, column 1.
    assert Position.from_byte(1, line_end) == Position(line=0, character=1, byte_offset=1)
    # Byte 4 is on line 1, column 4 - 3 = 1.
    assert Position.from_byte(4, line_end) == Position(line=1, character=1, byte_offset=4)
    # Byte 0 -> line 0, column 0.
    assert Position.from_byte(0, line_end) == Position(line=0, character=0, byte_offset=0)


def test_position_from_byte_no_end_exceeds_quirk() -> None:
    """grok ``unwrap_or(0)`` quirk: if no line-end exceeds the byte, line = 0."""
    # Empty index -> no end_byte > any byte -> line 0, column = byte.
    pos = Position.from_byte(50, [])
    assert pos.line == 0
    assert pos.character == 50
    assert pos.byte_offset == 50


def test_position_shift_column_is_one_indexed_and_resets_byte() -> None:
    """``shift_column`` adds ``saturating_sub(1, column_move)`` and zeros byte (range.rs L115-L121)."""
    pos = Position(line=2, character=5, byte_offset=100)
    # column_move=1 -> no-op (saturating_sub(1) == 0); byte reset to 0.
    shifted = pos.shift_column(1)
    assert shifted == Position(line=2, character=5, byte_offset=0)
    # column_move=4 -> +3 columns.
    assert pos.shift_column(4) == Position(line=2, character=8, byte_offset=0)
    # column_move=0 -> saturating_sub clamps to 0 (no negative column).
    assert pos.shift_column(0) == Position(line=2, character=5, byte_offset=0)


def test_position_move_to_next_line_resets_column_and_byte() -> None:
    """``move_to_next_line`` -> line+1, column 0, byte 0 (range.rs L124-L129)."""
    pos = Position(line=5, character=9, byte_offset=200)
    assert pos.move_to_next_line() == Position(line=6, character=0, byte_offset=0)


# === Range =================================================================


def test_range_default_is_empty_origin() -> None:
    """Default Range spans the origin: both endpoints at (0,0,0)."""
    rng = Range()
    assert rng.start_position == Position()
    assert rng.end_position == Position()
    assert rng.is_empty()


def test_range_byte_and_line_and_column_accessors() -> None:
    """Full accessor family: start/end x byte/line/column x 0/1-indexed."""
    rng = Range(
        start_position=Position(line=1, character=2, byte_offset=10),
        end_position=Position(line=3, character=5, byte_offset=40),
    )
    assert rng.start_byte() == 10
    assert rng.end_byte() == 40
    assert rng.start_line() == 1
    assert rng.end_line() == 3
    assert rng.start_column() == 2
    assert rng.end_column() == 5
    assert rng.start_line_1indexed() == 2
    assert rng.end_line_1indexed() == 4
    assert rng.start_column_1indexed() == 3
    assert rng.end_column_1indexed() == 6


def test_range_byte_size_len_is_empty_line_size() -> None:
    """``byte_size`` = ``end - start + 1`` (saturating); ``len`` = ``end - start``;
    ``is_empty`` = (len == 0); ``line_size`` = ``end_line - start_line`` (may be negative)."""
    rng = Range(
        start_position=Position(byte_offset=10),
        end_position=Position(byte_offset=40),
    )
    assert rng.byte_size() == 31  # 40 - 10 + 1
    assert rng.len() == 30
    assert not rng.is_empty()

    empty = Range(
        start_position=Position(byte_offset=10),
        end_position=Position(byte_offset=10),
    )
    assert empty.len() == 0
    assert empty.is_empty()
    assert empty.byte_size() == 1  # +1 even when len == 0

    # Saturating: end < start -> len 0, byte_size 1.
    inverted = Range(
        start_position=Position(byte_offset=40),
        end_position=Position(byte_offset=10),
    )
    assert inverted.len() == 0


def test_range_line_size_can_be_negative() -> None:
    """``line_size`` is a raw subtraction (grok i64) -- negative when end < start."""
    rng = Range(
        start_position=Position(line=5),
        end_position=Position(line=2),
    )
    assert rng.line_size() == -3


def test_range_contains_family_inclusive_bounds() -> None:
    """``contains`` / ``contains_check_line`` / ``contains_check_line_column`` /
    ``contains_position`` / ``contains_line`` -- all inclusive at the bounds."""
    outer = Range(
        start_position=Position(line=2, character=3),
        end_position=Position(line=8, character=4),
    )
    inner = Range(
        start_position=Position(line=3, character=0),
        end_position=Position(line=7, character=9),
    )
    assert outer.contains(inner)
    assert outer.contains_check_line(inner)
    assert outer.contains_check_line_column(inner)
    # contains_position: inclusive at both endpoints.
    assert outer.contains_position(Position(line=2, character=3))
    assert outer.contains_position(Position(line=8, character=4))
    assert not outer.contains_position(Position(line=2, character=2))
    assert not outer.contains_position(Position(line=8, character=5))
    # contains_line: inclusive on line bounds.
    assert outer.contains_line(2)
    assert outer.contains_line(8)
    assert not outer.contains_line(1)
    assert not outer.contains_line(9)


def test_range_intersects_without_byte_line_based() -> None:
    """``intersects_without_byte`` is a line-only overlap test."""
    a = Range(
        start_position=Position(line=1),
        end_position=Position(line=5),
    )
    overlapping = Range(
        start_position=Position(line=4),
        end_position=Position(line=9),
    )
    disjoint = Range(
        start_position=Position(line=6),
        end_position=Position(line=10),
    )
    assert a.intersects_without_byte(overlapping)
    assert not a.intersects_without_byte(disjoint)


def test_range_equality_variants_line_only() -> None:
    """``check_equality_without_byte`` / ``equals_line_range`` compare lines only."""
    a = Range(
        start_position=Position(line=2, character=0, byte_offset=10),
        end_position=Position(line=5, character=0, byte_offset=90),
    )
    # Same lines, different columns / bytes -> line-equal.
    b = Range(
        start_position=Position(line=2, character=9, byte_offset=20),
        end_position=Position(line=5, character=9, byte_offset=99),
    )
    assert a.check_equality_without_byte(b)
    assert a.equals_line_range(b)
    # Different end line -> not line-equal.
    c = Range(
        start_position=Position(line=2),
        end_position=Position(line=6),
    )
    assert not a.check_equality_without_byte(c)


def test_range_from_byte_range_builds_both_endpoints() -> None:
    """``from_byte_range(start, end, line_end_indices)`` builds both Positions (range.rs L350-L354)."""
    line_end = [3, 6]
    rng = Range.from_byte_range(1, 4, line_end)
    assert rng.start_position == Position.from_byte(1, line_end)
    assert rng.end_position == Position.from_byte(4, line_end)


def test_range_frozen_and_hashable() -> None:
    """grok ``Hash`` -> frozen dataclass: hashable + immutable."""
    rng = Range(
        start_position=Position(line=1),
        end_position=Position(line=2),
    )
    assert hash(rng) == hash(
        Range(
            start_position=Position(line=1),
            end_position=Position(line=2),
        )
    )
    with pytest.raises(FrozenInstanceError):
        rng.end_position = Position()  # type: ignore[misc]


# === Location ==============================================================


def test_location_from_range_auto_converts_to_1indexed() -> None:
    """``Location.from_range`` pulls line/column from the range's 1-indexed start (location.rs L43-L50)."""
    rng = Range(
        start_position=Position(line=3, character=5),  # 0-indexed
        end_position=Position(line=3, character=10),
    )
    loc = Location.from_range(Path("/repo/src/main.py"), rng)
    assert loc.line == 4  # 1-indexed
    assert loc.column == 6  # 1-indexed
    assert loc.range is rng


def test_location_new_takes_explicit_1indexed() -> None:
    """``Location.new`` takes explicit 1-indexed line / column."""
    rng = Range()
    loc = Location.new(Path("/a/b.py"), 10, 20, rng)
    assert loc.file_path == Path("/a/b.py")
    assert loc.line == 10
    assert loc.column == 20


def test_location_extension_strips_leading_dot() -> None:
    """``extension()`` returns the suffix without the dot (grok ``PathBuf::extension``)."""
    assert Location.new(Path("/repo/foo.py"), 1, 1, Range()).extension() == "py"
    assert Location.new(Path("/repo/foo.tar.gz"), 1, 1, Range()).extension() == "gz"
    # No extension -> None.
    assert Location.new(Path("/repo/Makefile"), 1, 1, Range()).extension() is None


def test_location_parent_dir_none_for_bare_filename() -> None:
    """grok ``parent_dir`` returns ``Some("")`` for a bare filename -> Python ``None``."""
    assert Location.new(Path("main.py"), 1, 1, Range()).parent_dir() is None
    # A real parent survives.
    parent = Location.new(Path("/repo/src/main.py"), 1, 1, Range()).parent_dir()
    assert parent == Path("/repo/src")


def test_location_path_alias() -> None:
    """``path()`` is an alias for ``file_path`` (grok ``path()``)."""
    loc = Location.new(Path("/x/y.py"), 1, 1, Range())
    assert loc.path() == Path("/x/y.py")


def test_location_line_and_column_0indexed_saturating() -> None:
    """0-indexed accessors subtract 1, saturating at 0."""
    loc = Location.new(Path("/a.py"), 5, 7, Range())
    assert loc.line_0indexed() == 4
    assert loc.column_0indexed() == 6
    # line=0 (degenerate) -> saturating_sub -> 0.
    zero = Location.new(Path("/a.py"), 0, 0, Range())
    assert zero.line_0indexed() == 0
    assert zero.column_0indexed() == 0


def test_location_display_is_file_line_column() -> None:
    """``str(Location)`` is ``file:line:column`` (grok ``Display`` impl)."""
    loc = Location.new(Path("/repo/src/main.py"), 10, 20, Range())
    assert str(loc) == "\\repo\\src\\main.py:10:20" or str(loc) == "/repo/src/main.py:10:20"


def test_location_frozen_and_hashable() -> None:
    """grok ``Hash`` -> frozen dataclass: hashable + immutable."""
    rng = Range()
    loc = Location.new(Path("/a.py"), 1, 1, rng)
    assert hash(loc) == hash(Location.new(Path("/a.py"), 1, 1, rng))
    with pytest.raises(FrozenInstanceError):
        loc.line = 2  # type: ignore[misc]


# === FileEvent =============================================================


def test_file_event_four_factory_constructors_set_kind_and_path() -> None:
    """The 4 factory constructors map onto grok's 4 enum variants."""
    created = FileEvent.created("/a.py")
    modified = FileEvent.modified("/b.py")
    deleted = FileEvent.deleted("/c.py")
    renamed = FileEvent.renamed("/old.py", "/new.py")

    assert created.kind is FileEventKind.CREATED
    assert created.path == "/a.py"
    assert created.from_path is None

    assert modified.kind is FileEventKind.MODIFIED
    assert deleted.kind is FileEventKind.DELETED

    # Renamed: path = to, from_path = from (so primary_path() returns to).
    assert renamed.kind is FileEventKind.RENAMED
    assert renamed.path == "/new.py"
    assert renamed.from_path == "/old.py"


def test_file_event_primary_path_returns_to_for_renamed() -> None:
    """``primary_path()`` returns ``path`` for C/M/D and ``to`` for Renamed (file_event.rs L37-L44)."""
    assert FileEvent.created("/a.py").primary_path() == "/a.py"
    assert FileEvent.modified("/a.py").primary_path() == "/a.py"
    assert FileEvent.deleted("/a.py").primary_path() == "/a.py"
    assert FileEvent.renamed("/old.py", "/new.py").primary_path() == "/new.py"


def test_file_event_requires_reparse_only_created_modified() -> None:
    """``requires_reparse()`` is True only for Created / Modified (file_event.rs L47-L54)."""
    assert FileEvent.created("/a.py").requires_reparse()
    assert FileEvent.modified("/a.py").requires_reparse()
    assert not FileEvent.deleted("/a.py").requires_reparse()
    assert not FileEvent.renamed("/o.py", "/n.py").requires_reparse()


def test_file_event_affects_existing_false_only_for_created() -> None:
    """``affects_existing()`` is False only for Created (file_event.rs L57-L64)."""
    assert not FileEvent.created("/a.py").affects_existing()
    assert FileEvent.modified("/a.py").affects_existing()
    assert FileEvent.deleted("/a.py").affects_existing()
    assert FileEvent.renamed("/o.py", "/n.py").affects_existing()


def test_file_event_display_renders_each_variant() -> None:
    """``str(FileEvent)`` renders each variant (grok ``Display`` impl)."""
    assert str(FileEvent.created("/a.py")) == "Created: /a.py"
    assert str(FileEvent.modified("/a.py")) == "Modified: /a.py"
    assert str(FileEvent.deleted("/a.py")) == "Deleted: /a.py"
    assert str(FileEvent.renamed("/old.py", "/new.py")) == "Renamed: /old.py -> /new.py"


def test_file_event_frozen_and_hashable() -> None:
    """grok enum variants are ``Copy + Eq`` -> frozen dataclass: hashable + immutable."""
    ev = FileEvent.created("/a.py")
    assert hash(ev) == hash(FileEvent.created("/a.py"))
    with pytest.raises(FrozenInstanceError):
        ev.path = "/b.py"  # type: ignore[misc]


# === structural types (mod.rs) =============================================


def test_index_stats_default_and_value_equality() -> None:
    """``IndexStats`` defaults to zeros and is value-equal (grok ``Copy + Eq``)."""
    assert IndexStats() == IndexStats(0, 0, 0)
    a = IndexStats.new(files=10, definitions=20, references=30)
    b = IndexStats(10, 20, 30)
    assert a == b
    assert hash(a) == hash(b)
    assert (a.files, a.definitions, a.references) == (10, 20, 30)


def test_symbol_occurrence_and_alias_constructors() -> None:
    """``SymbolOccurrence`` / ``SymbolAlias`` constructors + value equality."""
    occ = SymbolOccurrence.new("foo", 42)
    assert occ == SymbolOccurrence("foo", 42)
    assert hash(occ) == hash(SymbolOccurrence("foo", 42))
    assert (occ.name, occ.line) == ("foo", 42)

    alias = SymbolAlias.new("bar", "foo")
    assert alias == SymbolAlias("bar", "foo")
    assert hash(alias) == hash(SymbolAlias("bar", "foo"))
    assert (alias.alias, alias.original) == ("bar", "foo")


def test_file_meta_from_stat_splits_mtime(tmp_path: Path) -> None:
    """``FileMeta.from_stat`` splits ``st_mtime_ns`` into secs + nanos (grok ``from_metadata``)."""
    f = tmp_path / "f.txt"
    f.write_text("hello")
    stat = os.stat(f)
    meta = FileMeta.from_stat(stat)
    assert meta.size == stat.st_size
    mtime_ns = stat.st_mtime_ns
    assert meta.mtime_secs == mtime_ns // 1_000_000_000
    assert meta.mtime_nanos == mtime_ns % 1_000_000_000


def test_file_meta_from_path_round_trips(tmp_path: Path) -> None:
    """``FileMeta.from_path`` stats the file; ``is_stale`` is False right after."""
    f = tmp_path / "f.txt"
    f.write_text("hello")
    meta = FileMeta.from_path(f)
    assert not meta.is_stale(f)


def test_file_meta_is_stale_detects_content_change(tmp_path: Path) -> None:
    """``is_stale`` flips True when size/mtime change on disk (grok L110-L118)."""
    f = tmp_path / "f.txt"
    f.write_text("hello")
    meta = FileMeta.from_path(f)
    # Bump mtime + size deterministically (write more content, then force a
    # newer mtime so the comparison differs even on coarse filesystems).
    f.write_text("hello world, more bytes here")
    future = (os.stat(f).st_mtime_ns // 1_000_000_000 + 120) * 1_000_000_000
    os.utime(f, ns=(future, future))
    assert meta.is_stale(f)


def test_file_meta_is_stale_true_when_file_gone(tmp_path: Path) -> None:
    """``is_stale`` returns True when the file is missing (grok ``Err(_) -> true``)."""
    f = tmp_path / "gone.txt"
    f.write_text("x")
    meta = FileMeta.from_path(f)
    f.unlink()
    assert meta.is_stale(f)


def test_file_meta_value_equality_and_hashable() -> None:
    """grok ``Copy + Eq`` -> frozen dataclass: value-equal + hashable."""
    a = FileMeta.new(size=100, mtime_secs=1000, mtime_nanos=500)
    b = FileMeta(100, 1000, 500)
    assert a == b
    assert hash(a) == hash(b)


# === barrel contract =======================================================


def test_types_barrel_exports_nine_symbols() -> None:
    """``types/__init__`` ``__all__`` matches grok's public type surface + ``FileEventKind``.

    The 8 grok symbols (Position, Range, Location, FileEvent, FileMeta,
    IndexStats, SymbolOccurrence, SymbolAlias) plus the Pythonic
    ``FileEventKind`` discriminator = 9 entries.
    """
    expected = {
        "FileEvent",
        "FileEventKind",
        "FileMeta",
        "IndexStats",
        "Location",
        "Position",
        "Range",
        "SymbolAlias",
        "SymbolOccurrence",
    }
    assert set(xcgt.__all__) == expected
    assert len(xcgt.__all__) == 9


def test_crate_root_barrel_mirrors_types_barrel() -> None:
    """Both import paths expose the same surface (crate root = subpackage)."""
    import minimax_code.xai_codebase_graph as xcg

    assert set(xcg.__all__) == set(xcgt.__all__)
    # Each name resolves to the same object under both paths.
    for name in xcg.__all__:
        assert getattr(xcg, name) is getattr(xcgt, name)


def test_barrel_leaf_identity() -> None:
    """A barrel re-export is the same object as the leaf-module definition.

    Mirrors grok ``pub use range::Position``: importing ``Position`` from the
    barrel yields the very class defined in ``types/range.py``.
    """
    assert Position is PositionFromLeaf
    assert Range is RangeFromLeaf
