"""Position and Range types for representing source code locations.

Mirrors grok ``xai-codebase-graph/src/types/range.rs`` (direction (2), brick 1).

Following LSP conventions (preserved verbatim from grok):
- Internally stored as 0-indexed (tree-sitter compatible).
- Public API provides both 0-indexed and 1-indexed accessors.

YAGNI boundaries (R300):
- tree-sitter bridge methods (``from_tree_sitter_point``, ``to_tree_sitter``,
  ``for_tree_node``, ``from_tree_sitter_node``, ``to_tree_sitter_range``) are
  NOT migrated: they depend on the ``tree_sitter`` crate, which would break
  this module's zero-dependency leaf invariant (stdlib + ``dataclasses``
  only). They land with the ``languages/`` tree-sitter bindings migration.
- Mutators (``set_byte_offset``, ``set_start_position``, ``set_end_position``,
  ``set_start_byte``, ``set_end_byte``) are NOT migrated: grok's ``&mut self``
  setters exist to patch byte offsets after tree-sitter node walks. The
  Python port is frozen (immutable + hashable, mirroring grok's ``Hash``
  derive); callers compose new instances via ``dataclasses.replace`` when an
  offset needs patching.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class Position:
    """A position in a source file.

    Stored 0-indexed internally (tree-sitter compatible). Use
    :meth:`line_1indexed` / :meth:`column_1indexed` for display / LSP output.
    Mirrors grok ``Position`` (range.rs L13-L130). Frozen + hashable (grok
    ``#[derive(Hash)]``); grok's mutators are dropped in favor of immutability
    (see module docstring).
    """

    line: int = 0
    """Line number (0-indexed)."""

    character: int = 0
    """Character / column number (0-indexed)."""

    byte_offset: int = 0
    """Byte offset from the start of the file."""

    # === 0-indexed accessors (grok line() / character() / byte_offset()) ===

    @property
    def column(self) -> int:
        """Alias for :attr:`character` -- matches grok ``column()`` / LSP terminology."""
        return self.character

    def to_byte_offset(self) -> int:
        """Alias for :attr:`byte_offset` (grok ``to_byte_offset()``)."""
        return self.byte_offset

    # === 1-indexed accessors (grok line_1indexed() / column_1indexed()) ===

    def line_1indexed(self) -> int:
        """Line number 1-indexed (for LSP / display)."""
        return self.line + 1

    def column_1indexed(self) -> int:
        """Column number 1-indexed (for LSP / display)."""
        return self.character + 1

    # === comparison (grok before_other() / after_other()) ===

    def before_other(self, other: Position) -> bool:
        """True if this position is before or at ``other`` (line, then column).

        Mirrors grok ``before_other`` (range.rs L89-L91).
        """
        return self.line < other.line or (
            self.line == other.line and self.character <= other.character
        )

    def after_other(self, other: Position) -> bool:
        """True if this position is after or at ``other`` (line, then column).

        Mirrors grok ``after_other`` (range.rs L95-L96).
        """
        return self.line > other.line or (
            self.line == other.line and self.character >= other.character
        )

    # === byte reconstruction (grok from_byte()) ===

    @classmethod
    def from_byte(cls, byte: int, line_end_indices: list[int]) -> Position:
        """Build a position from a byte offset and line-end byte indices.

        ``line_end_indices[i]`` is the byte offset just past the end of line
        ``i`` (the standard tree-sitter line-index layout). Mirrors grok
        ``from_byte`` (range.rs L99-L112): the line is the first index whose
        end-byte exceeds ``byte`` (``unwrap_or(0)`` if none -- a quirk
        preserved 1:1); the column is ``byte`` minus the previous line's
        end-byte (saturating), or just ``byte`` on line 0.
        """
        line = next(
            (idx for idx, end_byte in enumerate(line_end_indices) if end_byte > byte),
            0,  # grok unwrap_or(0): no end_byte > byte -> line 0 (quirk preserved).
        )
        if line >= 1:
            column = max(byte - line_end_indices[line - 1], 0)  # saturating_sub
        else:
            column = byte
        return cls(line, column, byte)

    # === transforms (grok shift_column() / move_to_next_line()) ===

    def shift_column(self, column_move: int) -> Position:
        """Return a new position shifted right by ``column_move`` columns.

        Mirrors grok ``shift_column`` (range.rs L115-L121): byte_offset resets
        to 0 (the shift is column-only; the caller recomputes bytes); the move
        is 1-indexed via ``saturating_sub(1)`` so a ``column_move`` of 1 leaves
        the column unchanged.
        """
        return Position(
            line=self.line,
            character=self.character + max(column_move - 1, 0),
            byte_offset=0,
        )

    def move_to_next_line(self) -> Position:
        """Return a new position at column 0 of the next line (byte_offset=0).

        Mirrors grok ``move_to_next_line`` (range.rs L124-L129).
        """
        return Position(line=self.line + 1, character=0, byte_offset=0)


@dataclass(frozen=True, slots=True)
class Range:
    """A range in a source file.

    Stored 0-indexed internally (tree-sitter compatible). Mirrors grok
    ``Range`` (range.rs L151-L365). Frozen + hashable (grok ``Hash``); grok's
    ``&mut self`` setters (``set_start_position`` / ``set_end_position`` /
    ``set_start_byte`` / ``set_end_byte``) are dropped in favor of
    immutability (see module docstring). Start / end positions are exposed as
    fields (``rng.start_position``) rather than grok's ``start_position()``
    methods -- Pythonic attribute access replaces the Rust getter call.
    """

    start_position: Position = field(default_factory=Position)
    end_position: Position = field(default_factory=Position)

    # === byte accessors (grok start_byte() / end_byte()) ===

    def start_byte(self) -> int:
        """Start byte offset (grok ``start_byte``)."""
        return self.start_position.byte_offset

    def end_byte(self) -> int:
        """End byte offset (grok ``end_byte``)."""
        return self.end_position.byte_offset

    # === line accessors ===

    def start_line(self) -> int:
        """Start line (0-indexed)."""
        return self.start_position.line

    def end_line(self) -> int:
        """End line (0-indexed)."""
        return self.end_position.line

    def start_line_1indexed(self) -> int:
        """Start line (1-indexed, for LSP / display)."""
        return self.start_position.line + 1

    def end_line_1indexed(self) -> int:
        """End line (1-indexed, for LSP / display)."""
        return self.end_position.line + 1

    # === column accessors ===

    def start_column(self) -> int:
        """Start column (0-indexed)."""
        return self.start_position.character

    def end_column(self) -> int:
        """End column (0-indexed)."""
        return self.end_position.character

    def start_column_1indexed(self) -> int:
        """Start column (1-indexed, for LSP / display)."""
        return self.start_position.character + 1

    def end_column_1indexed(self) -> int:
        """End column (1-indexed, for LSP / display)."""
        return self.end_position.character + 1

    # === size (grok byte_size() / len() / is_empty() / line_size()) ===

    def byte_size(self) -> int:
        """Byte size of the range (``end_byte - start_byte + 1``, saturating)."""
        return max(self.end_byte() - self.start_byte(), 0) + 1

    def len(self) -> int:
        """Number of bytes spanned (``end_byte - start_byte``, saturating).

        Named ``len`` to mirror grok; invoke as ``rng.len()`` (does not shadow
        the ``len`` builtin, which would require ``__len__``).
        """
        return max(self.end_byte() - self.start_byte(), 0)

    def is_empty(self) -> bool:
        """True if the range spans zero bytes (``self.len() == 0``)."""
        return self.len() == 0

    def line_size(self) -> int:
        """Number of lines spanned (``end_line - start_line``; may be negative)."""
        return self.end_line() - self.start_line()

    # === containment (grok contains() family) ===

    def contains(self, other: Range) -> bool:
        """True if this range contains ``other`` (line + column check)."""
        return self.contains_check_line_column(other)

    def contains_check_line(self, other: Range) -> bool:
        """Line-only containment check (grok ``contains_check_line``)."""
        return self.start_line() <= other.start_line() and self.end_line() >= other.end_line()

    def contains_check_line_column(self, other: Range) -> bool:
        """Line + column containment check (grok ``contains_check_line_column``)."""
        start_ok = self.start_line() < other.start_line() or (
            self.start_line() == other.start_line()
            and self.start_column() <= other.start_column()
        )
        end_ok = self.end_line() > other.end_line() or (
            self.end_line() == other.end_line() and self.end_column() >= other.end_column()
        )
        return start_ok and end_ok

    def contains_position(self, position: Position) -> bool:
        """True if ``position`` lies within this range (inclusive)."""
        return self.start_position.before_other(position) and self.end_position.after_other(
            position
        )

    def contains_line(self, line: int) -> bool:
        """True if ``line`` (0-indexed) lies within this range (inclusive)."""
        return self.start_position.line <= line <= self.end_position.line

    # === intersection (grok intersects_without_byte()) ===

    def intersects_without_byte(self, other: Range) -> bool:
        """True if this range intersects ``other`` (line-based)."""
        return self.start_line() <= other.end_line() and self.end_line() >= other.start_line()

    # === equality variants (grok check_equality_without_byte() / equals_line_range()) ===

    def check_equality_without_byte(self, other: Range) -> bool:
        """Line-only equality (start_line == start_line and end_line == end_line)."""
        return self.start_line() == other.start_line() and self.end_line() == other.end_line()

    def equals_line_range(self, other: Range) -> bool:
        """Line-range equality (alias for :meth:`check_equality_without_byte`)."""
        return self.start_line() == other.start_line() and self.end_line() == other.end_line()

    # === construction (grok from_byte_range()) ===

    @classmethod
    def from_byte_range(
        cls, start: int, end: int, line_end_indices: list[int]
    ) -> Range:
        """Build a range from byte offsets + line-end indices.

        Mirrors grok ``from_byte_range(range, line_end_indices)`` (range.rs
        L350-L354). Rust takes a ``Range<usize>`` (``start..end``); the Python
        port takes two ints (``start``, ``end``) -- language-idiomatic, no
        semantic loss.
        """
        start_pos = Position.from_byte(start, line_end_indices)
        end_pos = Position.from_byte(end, line_end_indices)
        return cls(start_pos, end_pos)
