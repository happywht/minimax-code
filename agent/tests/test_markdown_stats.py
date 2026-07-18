"""Tests for the markdown analysis type skeleton (R37).

grok's ``analyze()`` loop populates these types from a ``pulldown-cmark`` event
stream; that wiring is a later round (needs a Python parser). These tests pin
the pure data shapes: default-zero counts, the ``headings`` derived total, the
fixed-order ``as_pairs`` projection (single source of truth for
serialization), the two-variant issue enum + its ``as_str`` keys, and the
analysis container.
"""

from __future__ import annotations

from minimax_code.markdown import (
    MarkdownAnalysis,
    MarkdownStats,
    StructuralIssue,
)
from minimax_code.markdown.stats import (
    MarkdownStats as MarkdownStatsFromModule,
)
from minimax_code.markdown.stats import (
    StructuralIssue as StructuralIssueFromModule,
)


def test_reexport():
    assert MarkdownStats is MarkdownStatsFromModule
    assert StructuralIssue is StructuralIssueFromModule


def test_default_all_zero():
    stats = MarkdownStats()
    assert stats.h1 == 0
    assert stats.tables == 0
    assert stats.list_items == 0
    assert stats.headings() == 0


def test_headings_sums_all_levels():
    stats = MarkdownStats(h1=1, h2=2, h3=0, h4=1, h5=0, h6=3)
    assert stats.headings() == 7


def test_as_pairs_has_twenty_two_entries_in_fixed_order():
    """The ``as_pairs`` projection is the single source of truth — pin its
    length (22 = headings + 21 counters) and exact key order so adding a
    counter without updating it fails loudly."""
    stats = MarkdownStats()
    pairs = stats.as_pairs()
    keys = [k for k, _ in pairs]
    assert keys == [
        "headings",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "tables",
        "fenced_code",
        "indented_code",
        "inline_code",
        "strong",
        "emphasis",
        "strikethrough",
        "links",
        "images",
        "blockquotes",
        "thematic_breaks",
        "inline_math",
        "display_math",
        "task_list_items",
        "list_items",
    ]


def test_as_pairs_headings_first_uses_derived_total():
    stats = MarkdownStats(h1=2, h2=3)
    assert stats.as_pairs()[0] == ("headings", 5)
    assert stats.as_pairs()[-1] == ("list_items", 0)


def test_as_pairs_reflects_mutation():
    """Plain (non-frozen) dataclass: counters are mutable post-construction."""
    stats = MarkdownStats()
    stats.tables = 4
    stats.fenced_code = 2
    pairs = dict(stats.as_pairs())
    assert pairs["tables"] == 4
    assert pairs["fenced_code"] == 2


def test_structural_issue_has_two_variants():
    assert len(StructuralIssue) == 2
    assert {m.name for m in StructuralIssue} == {
        "MALFORMED_TABLE",
        "UNTERMINATED_CODE_BLOCK",
    }


def test_structural_issue_as_str_keys():
    """snake_case values are the stable log/metric keys."""
    assert StructuralIssue.MALFORMED_TABLE.as_str() == "malformed_table"
    assert StructuralIssue.UNTERMINATED_CODE_BLOCK.as_str() == "unterminated_code_block"
    assert StructuralIssue.MALFORMED_TABLE.value == "malformed_table"


def test_structural_issue_is_hashable():
    """Unit enum — usable as a dict/set key (coalescing, dedup)."""
    by_issue = {StructuralIssue.MALFORMED_TABLE: 1}
    assert by_issue[StructuralIssue.MALFORMED_TABLE] == 1


def test_markdown_analysis_default():
    analysis = MarkdownAnalysis()
    assert isinstance(analysis.stats, MarkdownStats)
    assert analysis.stats.headings() == 0
    assert analysis.issues == []


def test_markdown_analysis_carries_issues():
    issues = [StructuralIssue.MALFORMED_TABLE, StructuralIssue.UNTERMINATED_CODE_BLOCK]
    analysis = MarkdownAnalysis(
        stats=MarkdownStats(tables=1),
        issues=issues,
    )
    assert analysis.stats.tables == 1
    assert analysis.issues == issues


def test_markdown_stats_value_equality():
    """grok PartialEq/Eq → dataclass eq=True gives value equality."""
    assert MarkdownStats(h1=1, tables=2) == MarkdownStats(h1=1, tables=2)
    assert MarkdownStats(h1=1) != MarkdownStats(h1=2)


def test_markdown_analysis_value_equality():
    a = MarkdownAnalysis(stats=MarkdownStats(h1=1), issues=[StructuralIssue.MALFORMED_TABLE])
    b = MarkdownAnalysis(stats=MarkdownStats(h1=1), issues=[StructuralIssue.MALFORMED_TABLE])
    assert a == b
