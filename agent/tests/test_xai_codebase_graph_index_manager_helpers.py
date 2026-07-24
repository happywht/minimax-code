"""Black-box tests for the ``index_manager`` pure-logic helpers (R306b).

Ported **by function** from grok ``xai-codebase-graph/src/index_manager.rs``.
This suite covers the 4 helper symbol groups that land in R306b (the
channel-actor runtime lands in R306c-R306g):

* :class:`CoalescedEvents` -- the merge rules (Created/Modified/Removed
  cancellation, rename splitting, last-writer-wins)
* :func:`is_binary_content` -- the git-style NUL-byte heuristic (PUB)
* :func:`is_binary_file` -- the 8 KB disk-prefix reader (private)
* :func:`is_under_hidden_dir` -- the hidden-component classifier (private)
* the crate-root barrel contract (``is_binary_content`` reaches the crate
  root; the 3 private helpers stay module-level)

grok has dedicated unit tests only for ``CoalescedEvents`` (L1656+
``#[cfg(test)] mod tests``) -- the ``is_*`` helpers are exercised
indirectly. The Python suite adds direct coverage for every helper's
contract surface and the 8 KB boundary.
"""

from __future__ import annotations

from pathlib import Path

import minimax_code.xai_codebase_graph as xcg_root
from minimax_code.xai_codebase_graph import index_manager as im
from minimax_code.xai_codebase_graph.index_manager import (
    CoalescedEvents,
    FileEvent,
    FileEventKind,
    is_binary_content,
    is_binary_file,
    is_under_hidden_dir,
)

# === CoalescedEvents =====================================================


def test_coalesced_events_new_starts_empty() -> None:
    """grok ``CoalescedEvents::new`` -> empty map (nothing to drain)."""
    c = CoalescedEvents()
    assert c.events == {}


def test_coalesced_add_created_stores_created() -> None:
    c = CoalescedEvents()
    c.add(FileEvent.created("a.py"))
    assert c.events == {"a.py": FileEventKind.CREATED}


def test_coalesced_add_modified_stores_modified() -> None:
    c = CoalescedEvents()
    c.add(FileEvent.modified("a.py"))
    assert c.events == {"a.py": FileEventKind.MODIFIED}


def test_coalesced_add_removed_stores_removed() -> None:
    c = CoalescedEvents()
    c.add(FileEvent.removed("a.py"))
    assert c.events == {"a.py": FileEventKind.REMOVED}


def test_coalesced_created_then_modified_becomes_modified() -> None:
    """grok L1516: Created + Modified -> Modified (already tracked, reindex)."""
    c = CoalescedEvents()
    c.add(FileEvent.created("a.py"))
    c.add(FileEvent.modified("a.py"))
    assert c.events == {"a.py": FileEventKind.MODIFIED}


def test_coalesced_modified_then_created_becomes_created() -> None:
    """Last-writer-wins on the fall-through arm (Modified + Created -> Created)."""
    c = CoalescedEvents()
    c.add(FileEvent.modified("a.py"))
    c.add(FileEvent.created("a.py"))
    assert c.events == {"a.py": FileEventKind.CREATED}


def test_coalesced_created_then_removed_cancels() -> None:
    """grok L1517: Created/Modified + Removed -> cancelled (entry dropped)."""
    c = CoalescedEvents()
    c.add(FileEvent.created("a.py"))
    c.add(FileEvent.removed("a.py"))
    assert c.events == {}


def test_coalesced_modified_then_removed_cancels() -> None:
    """grok L1517: the Modified + Removed arm also cancels."""
    c = CoalescedEvents()
    c.add(FileEvent.modified("a.py"))
    c.add(FileEvent.removed("a.py"))
    assert c.events == {}


def test_coalesced_removed_then_created_becomes_created() -> None:
    """grok L1518: Removed + Created/Modified -> Created (file replaced)."""
    c = CoalescedEvents()
    c.add(FileEvent.removed("a.py"))
    c.add(FileEvent.created("a.py"))
    assert c.events == {"a.py": FileEventKind.CREATED}


def test_coalesced_removed_then_modified_becomes_created() -> None:
    """grok L1518: Removed + Modified also resolves to Created (replaced)."""
    c = CoalescedEvents()
    c.add(FileEvent.removed("a.py"))
    c.add(FileEvent.modified("a.py"))
    assert c.events == {"a.py": FileEventKind.CREATED}


def test_coalesced_multiple_modified_collapses_to_single_modified() -> None:
    """grok L1519: multiple Modified -> single Modified."""
    c = CoalescedEvents()
    for _ in range(5):
        c.add(FileEvent.modified("a.py"))
    assert c.events == {"a.py": FileEventKind.MODIFIED}


def test_coalesced_removed_then_removed_stays_removed() -> None:
    """Last-writer-wins: Removed + Removed -> Removed (no cancellation)."""
    c = CoalescedEvents()
    c.add(FileEvent.removed("a.py"))
    c.add(FileEvent.removed("a.py"))
    assert c.events == {"a.py": FileEventKind.REMOVED}


def test_coalesced_created_then_created_stays_created() -> None:
    """Last-writer-wins: Created + Created -> Created."""
    c = CoalescedEvents()
    c.add(FileEvent.created("a.py"))
    c.add(FileEvent.created("a.py"))
    assert c.events == {"a.py": FileEventKind.CREATED}


def test_coalesced_rename_splits_into_removed_plus_created() -> None:
    """grok L1532-L1538: rename [from, to] -> from=Removed, to=Created."""
    c = CoalescedEvents()
    c.add(FileEvent.renamed("old.py", "new.py"))
    assert c.events == {
        "old.py": FileEventKind.REMOVED,
        "new.py": FileEventKind.CREATED,
    }


def test_coalesced_rename_then_remove_target_cancels_created() -> None:
    """rename sets new.py=Created; a later Removed on new.py cancels it."""
    c = CoalescedEvents()
    c.add(FileEvent.renamed("old.py", "new.py"))
    c.add(FileEvent.removed("new.py"))
    assert c.events == {"old.py": FileEventKind.REMOVED}


def test_coalesced_add_batch_event_inserts_each_path() -> None:
    """A multi-path Modified event inserts every path with the event kind."""
    c = CoalescedEvents()
    c.add(FileEvent.new(["a.py", "b.py", "c.py"], FileEventKind.MODIFIED))
    assert c.events == {
        "a.py": FileEventKind.MODIFIED,
        "b.py": FileEventKind.MODIFIED,
        "c.py": FileEventKind.MODIFIED,
    }


def test_coalesced_distinct_paths_do_not_interfere() -> None:
    """Each path is coalesced independently."""
    c = CoalescedEvents()
    c.add(FileEvent.created("a.py"))
    c.add(FileEvent.removed("b.py"))
    c.add(FileEvent.modified("c.py"))
    assert c.events == {
        "a.py": FileEventKind.CREATED,
        "b.py": FileEventKind.REMOVED,
        "c.py": FileEventKind.MODIFIED,
    }


def test_coalesced_rename_degenerate_single_path_falls_through() -> None:
    """A RENAMED event with fewer than 2 paths falls back to the per-path loop.

    grok guards ``event.paths.len() >= 2`` before splitting; a malformed
    single-path rename is treated as an ordinary event of its kind.
    """
    c = CoalescedEvents()
    c.add(FileEvent.new(["a.py"], FileEventKind.RENAMED))
    assert c.events == {"a.py": FileEventKind.RENAMED}


# === is_binary_content ===================================================


def test_is_binary_content_empty_is_not_binary() -> None:
    assert is_binary_content(b"") is False


def test_is_binary_content_plain_text_is_not_binary() -> None:
    assert is_binary_content(b"hello world\nprint('hi')\n") is False


def test_is_binary_content_nul_byte_is_binary() -> None:
    assert is_binary_content(b"hello\x00world") is True


def test_is_binary_content_leading_nul_is_binary() -> None:
    assert is_binary_content(b"\x00abc") is True


def test_is_binary_content_nul_within_first_8000_is_binary() -> None:
    """A NUL anywhere in the first 8 KB is binary (git heuristic)."""
    payload = b"a" * 4000 + b"\x00" + b"b" * 10
    assert is_binary_content(payload) is True


def test_is_binary_content_nul_after_8000_is_not_binary() -> None:
    """A NUL past the 8 KB prefix is NOT detected (only the prefix is scanned).

    Mirrors grok ``content[..check_len]`` where ``check_len = min(len, 8000)``.
    """
    payload = b"a" * 8000 + b"\x00"
    assert is_binary_content(payload) is False


def test_is_binary_content_nul_exactly_at_index_7999_is_binary() -> None:
    """The 8000th byte (index 7999) is the last one scanned."""
    payload = b"a" * 7999 + b"\x00"
    assert len(payload) == 8000
    assert is_binary_content(payload) is True


def test_is_binary_content_nul_exactly_at_index_8000_is_not_binary() -> None:
    """The 8001st byte (index 8000) is just past the scan window."""
    payload = b"a" * 8000 + b"\x00"
    assert len(payload) == 8001
    assert is_binary_content(payload) is False


def test_is_binary_content_short_buffer_with_nul_is_binary() -> None:
    """Buffers shorter than 8 KB scan their full length."""
    assert is_binary_content(b"\x00") is True


# === is_binary_file ======================================================


def test_is_binary_file_text_file_is_not_binary(tmp_path: Path) -> None:
    f = tmp_path / "text.py"
    f.write_bytes(b"print('hello')\n")
    assert is_binary_file(str(f)) is False


def test_is_binary_file_binary_file_is_binary(tmp_path: Path) -> None:
    f = tmp_path / "blob.bin"
    f.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00")
    assert is_binary_file(str(f)) is True


def test_is_binary_file_missing_file_returns_false(tmp_path: Path) -> None:
    """grok L1586: open failure -> False (do not crash the caller)."""
    missing = tmp_path / "does_not_exist"
    assert is_binary_file(str(missing)) is False


def test_is_binary_file_empty_file_is_not_binary(tmp_path: Path) -> None:
    f = tmp_path / "empty"
    f.write_bytes(b"")
    assert is_binary_file(str(f)) is False


def test_is_binary_file_nul_after_8000_is_not_binary(tmp_path: Path) -> None:
    """Only the first 8 KB is read; a trailing NUL escapes detection."""
    f = tmp_path / "sneaky"
    f.write_bytes(b"a" * 8000 + b"\x00")
    assert is_binary_file(str(f)) is False


def test_is_binary_file_nul_within_first_8000_is_binary(tmp_path: Path) -> None:
    f = tmp_path / "bin"
    f.write_bytes(b"a" * 100 + b"\x00" + b"b" * 100)
    assert is_binary_file(str(f)) is True


def test_is_binary_file_directory_returns_false(tmp_path: Path) -> None:
    """Opening a directory for read fails -> False (IsADirectoryError / Permission)."""
    assert is_binary_file(str(tmp_path)) is False


# === is_under_hidden_dir =================================================


def test_is_under_hidden_dir_plain_path_is_false() -> None:
    assert is_under_hidden_dir("src/main.rs") is False


def test_is_under_hidden_dir_dotclaude_worktree_is_true() -> None:
    """grok docstring example: ``.claude/worktrees/x`` is a tool-managed tree."""
    assert is_under_hidden_dir(".claude/worktrees/x/src/main.rs") is True


def test_is_under_hidden_dir_dotgit_is_true() -> None:
    assert is_under_hidden_dir(".git/config") is True


def test_is_under_hidden_dir_dotgrok_cache_is_true() -> None:
    """grok docstring example: ``.grok/cache`` is a tool cache."""
    assert is_under_hidden_dir(".grok/cache/index.bin") is True


def test_is_under_hidden_dir_hidden_file_component_is_true() -> None:
    """A component starting with ``.`` counts even if it is the file name."""
    assert is_under_hidden_dir("src/.env") is True
    assert is_under_hidden_dir("src/.hidden.py") is True


def test_is_under_hidden_dir_single_dot_current_dir_is_false() -> None:
    """The ``.`` current-dir component is excluded (``len == 1`` guard)."""
    assert is_under_hidden_dir(".") is False


def test_is_under_hidden_dir_bare_filename_is_false() -> None:
    assert is_under_hidden_dir("foo.py") is False


def test_is_under_hidden_dir_parent_dir_component_is_true() -> None:
    """grok's heuristic treats ``..`` as hidden (``len > 1`` arm).

    Mirrors grok behaviour: ``..`` starts with ``.`` and has length 2, so it
    trips the same arm as ``.git``. Whether that is a bug or a feature in
    grok, the Python port is faithful to it.
    """
    assert is_under_hidden_dir("foo/../bar.rs") is True


# === barrel contract (R306b additions) ===================================


def test_crate_root_exports_is_binary_content() -> None:
    """grok ``lib.rs`` L86 re-exports ``is_binary_content`` (PUB) at the root."""
    assert "is_binary_content" in xcg_root.__all__
    assert xcg_root.is_binary_content is is_binary_content


def test_private_helpers_stay_module_level() -> None:
    """The 3 private helpers are NOT at the crate root (grok-private visibility).

    They remain reachable through the leaf module (explicit import) but are
    absent from the crate-root barrel -- mirroring grok where
    ``CoalescedEvents`` / ``is_binary_file`` / ``is_under_hidden_dir`` are
    module-private ``fn`` / ``struct`` and never reach ``lib.rs``.
    """
    for private_sym in ("CoalescedEvents", "is_binary_file", "is_under_hidden_dir"):
        assert private_sym not in xcg_root.__all__
        assert not hasattr(xcg_root, private_sym)
        # ... but each is reachable via the leaf module.
        assert hasattr(im, private_sym)
