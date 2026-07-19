"""Ripgrep + fuzzy file-search shapes (R67).

Fusion of grok-build's ``xai-grok-workspace-types::types::search`` —
the request args and the hit/result structs for the workspace's
content-search (ripgrep) and fuzzy-path-search RPCs. Line / span
indices are ``u32`` (Python ``int``); all structs derive ``Default``.
"""

from __future__ import annotations

from pydantic import Field

from minimax_code.workspace_types._wire import WireModel

__all__ = [
    "RipgrepArgs",
    "MatchSpan",
    "ContentMatch",
    "RipgrepStats",
    "FuzzySearchArgs",
    "FuzzyMatch",
]


class RipgrepArgs(WireModel):
    """Arguments to a ripgrep content search."""

    pattern: str
    cwd: str | None = None
    globs: list[str] = Field(default_factory=list)
    case_insensitive: bool = False
    max_matches: int | None = None

    @classmethod
    def default(cls) -> RipgrepArgs:
        return cls(pattern="")


class MatchSpan(WireModel):
    """A ``[start, end)`` byte/char span within a matched line."""

    start: int
    end: int


class ContentMatch(WireModel):
    """One ripgrep hit (emitted inside ``RipgrepHit`` chunks)."""

    path: str
    line_number: int
    line: str
    spans: list[MatchSpan] = Field(default_factory=list)

    @classmethod
    def default(cls) -> ContentMatch:
        return cls(path="", line_number=0, line="")


class RipgrepStats(WireModel):
    """Aggregate counts for a completed ripgrep search."""

    files_matched: int = 0
    lines_matched: int = 0
    truncated: bool = False


class FuzzySearchArgs(WireModel):
    """Arguments to a fuzzy path search."""

    query: str
    cwd: str | None = None
    limit: int | None = None

    @classmethod
    def default(cls) -> FuzzySearchArgs:
        return cls(query="")


class FuzzyMatch(WireModel):
    """One fuzzy-search hit (emitted as ``FuzzyMatch`` chunk).

    ``score`` is ``i64``; ``matched_indices`` are ``Vec<u32>``.
    """

    path: str
    score: int = 0
    matched_indices: list[int] = Field(default_factory=list)

    @classmethod
    def default(cls) -> FuzzyMatch:
        return cls(path="")
