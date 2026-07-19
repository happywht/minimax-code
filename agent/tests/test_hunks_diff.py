"""Tests for the hunk-tracker diff primitives (R39).

Mirrors grok's ``xai-hunk-tracker`` diff tests
(``no_changes``, ``single_line_modification``, ``insertion``, ``deletion``,
``multiple_hunks``, ``format_unified_diff``,
``find_matching_hunk_with_identical_content_at_different_positions``,
``find_matching_hunk_fallback_best_overlap``) plus Python-specific assertions:
the ``difflib`` mapping (``autojunk=False`` is mandatory), the byte-length size
guard (Rust ``str::len`` vs Python ``len``), and the timeout deferral (YAGNI).
"""

from __future__ import annotations

from minimax_code.hunks import (
    MAX_DIFF_FILE_SIZE,
    AgentEdit,
    Hunk,
    HunkId,
    HunkLineInfo,
    compute_hunks,
    find_matching_old_hunk,
    find_overlapping_hunks,
    format_unified_diff,
    generate_hunk_patch,
    generate_unified_patch,
    hunk_moved,
    hunks_match_content,
    hunks_overlap,
    patch_lines,
)

_SOURCE = AgentEdit(prompt_index=0)


# --- compute_hunks: identical / guards --------------------------------------


def test_no_changes_returns_empty():
    assert compute_hunks("a.py", "same\n", "same\n", _SOURCE) == []


def test_oversized_file_returns_empty():
    """grok ``MAX_DIFF_FILE_SIZE`` guard: over the cap → empty hunk list."""
    big = "x" * (MAX_DIFF_FILE_SIZE + 1)
    assert compute_hunks("big.py", big, big + "y", _SOURCE) == []


def test_size_guard_uses_byte_length_not_char_count():
    """Rust ``str::len()`` is bytes; a multi-byte payload under the char budget
    but over the byte budget must still trip the guard."""
    cap = MAX_DIFF_FILE_SIZE
    # Each "ä" is 2 UTF-8 bytes; (cap//2 + 1) chars of "ä" is > cap bytes.
    over_cap_chars = "ä" * (cap // 2 + 1)
    assert len(over_cap_chars) <= cap  # char count within cap
    assert len(over_cap_chars.encode("utf-8")) > cap  # byte count over cap
    assert compute_hunks("u.py", over_cap_chars, over_cap_chars + "x", _SOURCE) == []


# --- compute_hunks: core cases (mirror grok diff tests) --------------------


def test_single_line_modification():
    """Mirrors grok ``single_line_modification``."""
    baseline = "line 1\nline 2\nline 3\n"
    current = "line 1\nmodified\nline 3\n"
    hunks = compute_hunks("t.py", baseline, current, _SOURCE)
    assert len(hunks) == 1
    h = hunks[0]
    assert h.old_text == "line 2\n"
    assert h.new_text == "modified\n"
    assert h.line_info.old_start == 2
    assert h.line_info.old_count == 1
    assert h.line_info.new_start == 2
    assert h.line_info.new_count == 1


def test_insertion():
    """Mirrors grok ``insertion``: pure insert → old_text None, old_count 0."""
    baseline = "line 1\nline 2\n"
    current = "line 1\ninserted\nline 2\n"
    hunks = compute_hunks("t.py", baseline, current, _SOURCE)
    assert len(hunks) == 1
    h = hunks[0]
    assert h.old_text is None
    assert h.new_text == "inserted\n"
    assert h.line_info.old_count == 0
    assert h.line_info.new_count == 1
    assert h.line_info.new_start == 2


def test_deletion():
    """Mirrors grok ``deletion``: pure delete → new_text empty, new_count 0."""
    baseline = "line 1\nline 2\nline 3\n"
    current = "line 1\nline 3\n"
    hunks = compute_hunks("t.py", baseline, current, _SOURCE)
    assert len(hunks) == 1
    h = hunks[0]
    assert h.old_text == "line 2\n"
    assert h.new_text == ""
    assert h.line_info.old_count == 1
    assert h.line_info.new_count == 0


def test_multiple_hunks():
    """Mirrors grok ``multiple_hunks``: two separate changes → two hunks."""
    baseline = "line 1\nline 2\nline 3\nline 4\nline 5\n"
    current = "modified 1\nline 2\nline 3\nline 4\nmodified 5\n"
    hunks = compute_hunks("t.py", baseline, current, _SOURCE)
    assert len(hunks) == 2
    assert hunks[0].line_info.old_start == 1
    assert hunks[1].line_info.old_start == 5


def test_compute_hunks_attributes_source():
    """Each produced hunk carries the source attribution passed in."""
    src = AgentEdit(prompt_index=7)
    hunks = compute_hunks("t.py", "a\n", "b\n", src)
    assert hunks[0].source is src


def test_compute_hunks_repeated_lines_diff_cleanly():
    """``autojunk=False`` is mandatory: a file with many repeated lines still
    diffs to a single clean hunk. (``difflib``'s default ``autojunk=True``
    would mark the frequent line as junk and distort the matching; we opt out.)"""
    repeated = "same\n" * 80
    baseline = repeated + "target\n" + repeated
    current = repeated + "changed\n" + repeated
    hunks = compute_hunks("t.py", baseline, current, _SOURCE)
    assert len(hunks) == 1
    assert hunks[0].old_text == "target\n"
    assert hunks[0].new_text == "changed\n"


def test_replace_keeps_delete_and_insert_in_one_hunk():
    """grok's single accumulator spans a delete+insert (replace) pair."""
    baseline = "a\nold\nb\n"
    current = "a\nnew\nb\n"
    hunks = compute_hunks("t.py", baseline, current, _SOURCE)
    assert len(hunks) == 1
    assert hunks[0].old_text == "old\n"
    assert hunks[0].new_text == "new\n"


# --- generate_unified_patch -------------------------------------------------


def test_generate_unified_patch_identical_is_none():
    assert generate_unified_patch("a.py", "x\n", "x\n") is None


def test_generate_unified_patch_carries_headers():
    patch = generate_unified_patch("src/t.py", "old\n", "new\n")
    assert patch is not None
    assert "--- a/src/t.py" in patch
    assert "+++ b/src/t.py" in patch
    assert "-old" in patch
    assert "+new" in patch


def test_generate_unified_patch_oversized_is_none():
    big = "x" * (MAX_DIFF_FILE_SIZE + 1)
    assert generate_unified_patch("big.py", big, big + "y") is None


# --- format_unified_diff ----------------------------------------------------


def test_format_unified_diff():
    """Mirrors grok ``format_unified_diff``."""
    h = Hunk(
        id=HunkId.from_string("x"),
        path="test.rs",
        line_info=HunkLineInfo(1, 1, 1, 1),
        source=_SOURCE,
        old_text="old line\n",
        new_text="new line\n",
    )
    out = format_unified_diff(h)
    assert "--- a/test.rs" in out
    assert "+++ b/test.rs" in out
    assert "-old line" in out
    assert "+new line" in out
    assert "@@ -1,1 +1,1 @@" in out


# --- generate_hunk_patch ----------------------------------------------------


def test_generate_hunk_patch_has_header_and_changes():
    h = Hunk(
        id=HunkId.from_string("x"),
        path="t.py",
        line_info=HunkLineInfo(2, 1, 2, 1),
        source=_SOURCE,
        old_text="old\n",
        new_text="new\n",
    )
    out = generate_hunk_patch("line 1\nold\nline 3\n", "line 1\nnew\nline 3\n", h)
    assert out.startswith("@@ ")
    assert "-old" in out
    assert "+new" in out
    # Context lines (CONTEXT_LINES=3 on each side; the file only has 3 lines so
    # the leading "line 1" and trailing "line 3" appear as context).
    assert " line 1" in out
    assert " line 3" in out


# --- patch_lines ------------------------------------------------------------


def test_patch_lines_replace():
    """Replace one line in the middle."""
    out = patch_lines("a\nb\nc\n", start_line=2, remove_count=1, insert_text="B\n")
    assert out == "a\nB\nc\n"


def test_patch_lines_insert():
    """Pure insert (remove_count=0)."""
    out = patch_lines("a\nc\n", start_line=2, remove_count=0, insert_text="b\n")
    assert out == "a\nb\nc\n"


def test_patch_lines_delete():
    """Pure delete (empty insert)."""
    out = patch_lines("a\nb\nc\n", start_line=2, remove_count=1, insert_text="")
    assert out == "a\nc\n"


def test_patch_lines_preserves_trailing_newline():
    """grok ``ends_with('\n')`` → keep the trailing newline."""
    out = patch_lines("a\nb\n", start_line=1, remove_count=1, insert_text="A\n")
    assert out.endswith("\n")


def test_patch_lines_no_trailing_newline_stays_without():
    out = patch_lines("a\nb", start_line=1, remove_count=1, insert_text="A\n")
    assert not out.endswith("\n")


# --- match / overlap predicates --------------------------------------------


def _hunk(path, old_start, old_count, new_start, new_count, old_text=None, new_text=""):
    return Hunk(
        id=HunkId.from_string(f"{path}-{old_start}-{new_start}"),
        path=path,
        line_info=HunkLineInfo(old_start, old_count, new_start, new_count),
        source=_SOURCE,
        old_text=old_text,
        new_text=new_text,
    )


def test_hunks_match_content():
    a = _hunk("a.py", 1, 1, 1, 1, old_text="o\n", new_text="n\n")
    b = _hunk("a.py", 5, 1, 5, 1, old_text="o\n", new_text="n\n")
    assert hunks_match_content(a, b)
    # Different path → no match.
    assert not hunks_match_content(a, _hunk("b.py", 1, 1, 1, 1, old_text="o\n", new_text="n\n"))


def test_hunk_moved():
    old = _hunk("a.py", 1, 1, 1, 1, old_text="o\n", new_text="n\n")
    moved = _hunk("a.py", 1, 1, 10, 1, old_text="o\n", new_text="n\n")
    assert hunk_moved(old, moved)
    assert not hunk_moved(old, old)  # same position → not moved


def test_hunks_overlap_regular():
    a = _hunk("a.py", 10, 5, 10, 5)
    b = _hunk("a.py", 12, 5, 12, 5)
    assert hunks_overlap(a, b)


def test_hunks_overlap_disjoint():
    a = _hunk("a.py", 1, 5, 1, 5)
    b = _hunk("a.py", 100, 5, 100, 5)
    assert not hunks_overlap(a, b)


def test_hunks_overlap_different_paths_never_overlap():
    a = _hunk("a.py", 1, 5, 1, 5)
    b = _hunk("b.py", 1, 5, 1, 5)
    assert not hunks_overlap(a, b)


def test_hunks_overlap_two_insertions_same_position():
    a = _hunk("a.py", 5, 0, 5, 1)
    b = _hunk("a.py", 5, 0, 5, 1)
    assert hunks_overlap(a, b)


def test_hunks_overlap_two_insertions_different_position():
    a = _hunk("a.py", 5, 0, 5, 1)
    b = _hunk("a.py", 6, 0, 6, 1)
    assert not hunks_overlap(a, b)


def test_find_matching_hunk_with_identical_content_at_different_positions():
    """Mirrors grok ``find_matching_hunk_with_identical_content_at_different_positions``:
    identical changes at multiple locations (e.g. a variable rename) match the
    one closest by new-file line position."""
    hunk_at_10 = _hunk("a.py", 10, 1, 10, 1, old_text="foo\n", new_text="bar\n")
    hunk_at_100 = _hunk("a.py", 100, 1, 100, 1, old_text="foo\n", new_text="bar\n")
    # New hunk near line 12 → matches the one at 10, not 100.
    new_near_12 = _hunk("a.py", 12, 1, 12, 1, old_text="foo\n", new_text="bar\n")
    match = find_matching_old_hunk(new_near_12, [hunk_at_10, hunk_at_100])
    assert match is hunk_at_10


def test_find_matching_hunk_fallback_best_overlap():
    """Mirrors grok ``find_matching_hunk_fallback_best_overlap``: with no content
    match, pick the overlapping hunk with the largest baseline overlap."""
    small_overlap = _hunk("a.py", 10, 2, 10, 2, old_text="x\n", new_text="y\n")
    big_overlap = _hunk("a.py", 10, 8, 10, 8, old_text="p\n", new_text="q\n")
    new_hunk = _hunk("a.py", 11, 5, 11, 5, old_text="DIFFERENT\n", new_text="also\n")
    match = find_matching_old_hunk(new_hunk, [small_overlap, big_overlap])
    assert match is big_overlap


def test_find_matching_old_hunk_none_when_no_match_and_no_overlap():
    far = _hunk("a.py", 1, 1, 1, 1, old_text="o\n", new_text="n\n")
    new_hunk = _hunk("a.py", 100, 1, 100, 1, old_text="X\n", new_text="Y\n")
    assert find_matching_old_hunk(new_hunk, [far]) is None


def test_find_overlapping_hunks_returns_all():
    a = _hunk("a.py", 10, 5, 10, 5)
    b = _hunk("a.py", 12, 5, 12, 5)
    c = _hunk("a.py", 100, 5, 100, 5)  # disjoint
    new_hunk = _hunk("a.py", 11, 3, 11, 3)
    overlaps = find_overlapping_hunks(new_hunk, [a, b, c])
    assert a in overlaps
    assert b in overlaps
    assert c not in overlaps
