"""Location type for query results.

Mirrors grok ``xai-codebase-graph/src/types/location.rs`` (direction (2),
brick 1). Location uses 1-indexed line and column numbers for LSP
compatibility (preserved verbatim from grok).

NOTE: this is ``types::Location``, distinct from the ``navigation::Location``
that ships later in the grok crate -- the two live under different module
paths and this port keeps them 1:1 (no unification), mirroring the
``types::FileEvent`` / ``index_manager::FileEvent`` split.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from minimax_code.xai_codebase_graph.types.range import Range


@dataclass(frozen=True, slots=True)
class Location:
    """A location in the codebase, used for query results.

    Following LSP protocol conventions (preserved from grok):
    - ``file_path``: absolute path to the file.
    - ``line``: 1-indexed line number.
    - ``column``: 1-indexed column number.
    - ``range``: full range information (0-indexed internally).

    Mirrors grok ``Location`` (location.rs L20-L96). Frozen + hashable (grok
    ``#[derive(Hash)]``). ``file_path`` / ``line`` / ``column`` / ``range``
    are public fields (Pythonic attribute access); the grok getter methods
    (``file_path()`` / ``line()`` / ``column()`` / ``range()``) collapse onto
    the fields -- only the non-trivial accessors survive as methods below.
    """

    file_path: Path
    line: int
    column: int
    range: Range

    @classmethod
    def new(cls, file_path: Path, line: int, column: int, range: Range) -> Location:
        """Create a new location with explicit 1-indexed line / column."""
        return cls(file_path, line, column, range)

    @classmethod
    def from_range(cls, file_path: Path, range: Range) -> Location:
        """Create a location from a range (auto-converts start to 1-indexed).

        Mirrors grok ``Location::from_range`` (location.rs L43-L50): line /
        column come from the range's 1-indexed start accessors.
        """
        return cls(
            file_path,
            range.start_line_1indexed(),
            range.start_column_1indexed(),
            range,
        )

    def path(self) -> Path:
        """Alias for :attr:`file_path` (grok ``path()``)."""
        return self.file_path

    def extension(self) -> str | None:
        """File extension without the leading dot, or ``None`` (grok ``extension()``).

        grok's ``PathBuf::extension`` returns the extension without the dot;
        Python's :pyattr:`pathlib.Path.suffix` keeps it, so it is stripped
        here to match.
        """
        ext = self.file_path.suffix
        return ext[1:] if ext else None

    def parent_dir(self) -> Path | None:
        """Parent directory, or ``None`` when the path has no parent.

        Mirrors grok ``parent_dir()``: grok returns ``Some("")`` for a bare
        filename (no parent). The Python port normalizes that empty/``.``
        parent to ``None`` (Pythonic -- a missing parent is absent, not empty).
        """
        parent = self.file_path.parent
        if str(parent) in ("", "."):
            return None
        return parent

    def line_0indexed(self) -> int:
        """0-indexed line number (internal use; ``line - 1``, saturating)."""
        return max(self.line - 1, 0)

    def column_0indexed(self) -> int:
        """0-indexed column number (internal use; ``column - 1``, saturating)."""
        return max(self.column - 1, 0)

    def __str__(self) -> str:
        """``file:line:column`` (grok ``Display`` impl)."""
        return f"{self.file_path}:{self.line}:{self.column}"
