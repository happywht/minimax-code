"""Tests for the hunk-tracker type layer (R39).

Mirrors grok's ``xai-hunk-tracker`` type-level assertions
(``file_created_marks_all_new_lines``, ``hunk_id_round_trips``) and pins the
Python-specific value-type behavior (frozen immutability, value equality,
hashability, union dispatch via isinstance) that the frozen-dataclass mapping
adds.
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from minimax_code.hunks import (
    AgentEdit,
    External,
    ExternalEditOnAgentFile,
    Hunk,
    HunkId,
    HunkLineInfo,
)
from minimax_code.hunks.types import Hunk as HunkFromModule
from minimax_code.hunks.types import HunkId as HunkIdFromModule
from minimax_code.hunks.types import HunkLineInfo as HunkLineInfoFromModule


def test_reexport():
    assert HunkId is HunkIdFromModule
    assert HunkLineInfo is HunkLineInfoFromModule
    assert Hunk is HunkFromModule


# --- HunkId ----------------------------------------------------------------


def test_hunk_id_new_is_unique():
    a = HunkId.new()
    b = HunkId.new()
    assert a != b
    assert a.value != b.value


def test_hunk_id_from_string_round_trips():
    raw = "abc-123"
    hid = HunkId.from_string(raw)
    assert hid.as_str() == raw
    assert hid.value == raw


def test_hunk_id_value_equality():
    assert HunkId.from_string("x") == HunkId.from_string("x")
    assert HunkId.from_string("x") != HunkId.from_string("y")


def test_hunk_id_is_frozen():
    hid = HunkId.new()
    with pytest.raises(FrozenInstanceError):
        hid.value = "mutated"  # type: ignore[misc]


def test_hunk_id_is_hashable():
    by_id = {HunkId.from_string("k"): "v"}
    assert by_id[HunkId.from_string("k")] == "v"


# --- HunkLineInfo ----------------------------------------------------------


def test_hunk_line_info_str_is_unified_diff_header():
    info = HunkLineInfo(old_start=10, old_count=3, new_start=10, new_count=5)
    assert str(info) == "@@ -10,3 +10,5 @@"


def test_hunk_line_info_value_equality():
    a = HunkLineInfo(1, 2, 3, 4)
    assert a == HunkLineInfo(1, 2, 3, 4)
    assert a != HunkLineInfo(1, 2, 3, 5)


def test_hunk_line_info_is_frozen():
    info = HunkLineInfo(1, 1, 1, 1)
    with pytest.raises(FrozenInstanceError):
        info.old_start = 99  # type: ignore[misc]


# --- HunkSource union ------------------------------------------------------


def test_agent_edit_carries_prompt_index():
    src = AgentEdit(prompt_index=3)
    assert src.prompt_index == 3


def test_unit_variants_value_equality():
    assert ExternalEditOnAgentFile() == ExternalEditOnAgentFile()
    assert External() == External()
    assert ExternalEditOnAgentFile() != External()


def test_source_union_dispatch_via_isinstance():
    """grok ``match`` on enum variant → Python isinstance on the union members."""
    sources = [AgentEdit(prompt_index=0), ExternalEditOnAgentFile(), External()]
    assert isinstance(sources[0], AgentEdit)
    assert isinstance(sources[1], ExternalEditOnAgentFile)
    assert isinstance(sources[2], External)
    # Each variant matches only its own arm (mirrors Rust exhaustive match).
    assert not isinstance(sources[0], External)


def test_unit_variants_are_hashable():
    assert {ExternalEditOnAgentFile(): 1}[ExternalEditOnAgentFile()] == 1


# --- Hunk ------------------------------------------------------------------


def _make_hunk(**overrides):
    base = dict(
        id=HunkId.from_string("fixed-id"),
        path="src/app.py",
        line_info=HunkLineInfo(2, 1, 2, 1),
        source=AgentEdit(prompt_index=1),
        old_text="old\n",
        new_text="new\n",
    )
    base.update(overrides)
    return Hunk(**base)


def test_hunk_defaults_patch_none_selected_false():
    h = _make_hunk()
    assert h.patch is None
    assert h.selected is False
    assert h.created_at is not None


def test_hunk_value_equality():
    a = _make_hunk()
    b = _make_hunk()
    assert a == b
    assert a != _make_hunk(new_text="different\n")


def test_hunk_is_frozen():
    h = _make_hunk()
    with pytest.raises(FrozenInstanceError):
        h.selected = True  # type: ignore[misc]


def test_hunk_file_created_marks_all_new_lines():
    """Mirrors grok ``file_created`` semantics: old_* zero, new_start=1, new_count=line count."""
    h = Hunk.file_created("new.py", "a\nb\nc\n", AgentEdit(prompt_index=0))
    assert h.path == "new.py"
    assert h.old_text is None
    assert h.new_text == "a\nb\nc\n"
    assert h.line_info == HunkLineInfo(0, 0, 1, 3)


def test_hunk_file_created_empty_content_is_one_line():
    """grok ``content.lines().count().max(1)``: empty content still counts as 1 line."""
    h = Hunk.file_created("empty.py", "", External())
    assert h.line_info == HunkLineInfo(0, 0, 1, 1)
    assert h.new_text == ""
