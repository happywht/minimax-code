"""v1.6.0 sandbox overlay view regression tests.

v1.5.0 shipped ``read_file`` overlay but left ``search_files`` /
``find_files`` / ``list_directory`` showing the bare workspace — a
sandboxed sub-agent could not find the files it had just written
(split view), and the Python search walker's prune table did not
skip ``.minimax``, so the *main* agent could stumble over sandbox /
backup internals. These tests pin the v1.6.0 fixes:

* search forces the Python engine under a sandbox (rg cannot see
  mirrors) and unions mirror files into the candidates, mirror wins
  on key collision, single-file reads go through the overlay;
* find_files appends sandbox-only matches (prefix / depth / pattern
  aware) and excludes ``.minimax`` even when the caller replaces the
  default ``exclude_dirs`` table;
* list_directory merges mirror children (same-name → sandbox entry,
  workspace address projection, ``sandboxed: true``) and answers for
  sandbox-only directories while hiding ``_base`` / the marker.

All tool exercises go through ``registry.dispatch`` (project
convention since v1.4.1).
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from minimax_code.agent.tools import get_default_registry
from minimax_code.agent.tools import search as search_mod
from minimax_code.agent.tools.subagents import SANDBOX_PROTOCOL_PROMPT
from minimax_code.workspace_ctx import (
    reset_current_root,
    reset_sandbox,
    set_current_root,
    set_sandbox,
)


@pytest.fixture
def workspace_root(tmp_path: Path):
    root = tmp_path / "proj_root"
    root.mkdir()
    token = set_current_root(root)
    try:
        yield root
    finally:
        reset_current_root(token)


@contextmanager
def sandbox_run(root: Path, run_id: str):
    """Publish the sandbox ContextVar for the block; reset after."""
    sb = root / ".minimax" / "sandboxes" / run_id
    sb.mkdir(parents=True, exist_ok=True)
    token = set_sandbox(sb)
    try:
        yield sb
    finally:
        reset_sandbox(token)


def make_mirror(sb: Path, rel: str, content: str) -> None:
    """Plant a mirror file at workspace-relative *rel* inside *sb*."""
    p = sb.joinpath(*rel.split("/"))
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")


async def _dispatch(name: str, **args: Any):
    return await get_default_registry().dispatch(name, args)


# ---------------------------------------------------------------------------
# find_files — mirror union
# ---------------------------------------------------------------------------


async def test_find_files_unions_mirror_only_files(workspace_root: Path) -> None:
    (workspace_root / "a.py").write_text("x = 1\n", encoding="utf-8")

    with sandbox_run(workspace_root, "run_gu") as sb:
        make_mirror(sb, "new.py", "x = 2\n")
        res = await _dispatch("find_files", pattern="**/*.py")
    assert res.success, res.error

    files = res.output["files"]
    assert "a.py" in files
    assert "new.py" in files  # sandbox-only file is visible


async def test_find_files_mirror_key_dedup(workspace_root: Path) -> None:
    sub = workspace_root / "src"
    sub.mkdir()
    (sub / "x.py").write_text("x = 1\n", encoding="utf-8")

    with sandbox_run(workspace_root, "run_dedup") as sb:
        make_mirror(sb, "src/x.py", "x = 2\n")
        res = await _dispatch("find_files", pattern="**/*.py")
    assert res.success, res.error

    files = [f.replace("\\", "/") for f in res.output["files"]]
    assert files.count("src/x.py") == 1  # same-key entry, not duplicated


async def test_find_files_prefix_and_depth_gates(workspace_root: Path) -> None:
    src = workspace_root / "src"
    src.mkdir()
    (src / "top.py").write_text("x = 1\n", encoding="utf-8")

    with sandbox_run(workspace_root, "run_pd") as sb:
        make_mirror(sb, "src/deep/n.txt", "deep\n")
        # Path narrowed to src/ — the mirror file projects under it.
        res = await _dispatch("find_files", pattern="**/*", path="src")
        assert res.success, res.error
        files = [f.replace("\\", "/") for f in res.output["files"]]
        assert "top.py" in files
        assert "deep/n.txt" in files

        # max_depth=1 stops the walker at src's direct children — the
        # mirror union must honour the same depth budget.
        res1 = await _dispatch("find_files", pattern="**/*", path="src", max_depth=1)
        assert res1.success, res1.error
        files1 = [f.replace("\\", "/") for f in res1.output["files"]]
        assert "top.py" in files1
        assert "deep/n.txt" not in files1


async def test_find_files_minimax_hard_excluded(workspace_root: Path) -> None:
    """A caller-supplied exclude_dirs table cannot un-exclude .minimax."""
    leak = workspace_root / ".minimax" / "sandboxes" / "run_other"
    leak.mkdir(parents=True)
    (leak / "leak.py").write_text("x = 1\n", encoding="utf-8")
    (workspace_root / "ok.py").write_text("x = 1\n", encoding="utf-8")

    res = await _dispatch("find_files", pattern="**/*.py", exclude_dirs=["dist"])
    assert res.success, res.error
    files = [f.replace("\\", "/") for f in res.output["files"]]
    assert "ok.py" in files
    assert not any(".minimax" in f for f in files)


# ---------------------------------------------------------------------------
# search_files — forced Python engine + overlay reads
# ---------------------------------------------------------------------------


async def test_search_forces_python_engine_under_sandbox(workspace_root: Path) -> None:
    (workspace_root / "a.py").write_text("needle\n", encoding="utf-8")

    with sandbox_run(workspace_root, "run_engine"):
        res = await _dispatch("search_files", pattern="needle")
    assert res.success, res.error
    assert res.output["engine"] == "python"  # rg cannot see mirrors


async def test_search_reads_mirror_and_shadows_workspace(workspace_root: Path) -> None:
    (workspace_root / "f.py").write_text("OLD\n", encoding="utf-8")

    with sandbox_run(workspace_root, "run_ov") as sb:
        make_mirror(sb, "f.py", "NEEDLE\n")
        hit = await _dispatch("search_files", pattern="NEEDLE")
        assert hit.success, hit.error
        # One match, reported at the workspace address, read from the mirror.
        assert hit.output["matches"] == [{"file": "f.py", "line": 1, "text": "NEEDLE"}]

        # The workspace copy is shadowed by the mirror — "OLD" is gone.
        miss = await _dispatch("search_files", pattern="OLD")
        assert miss.success, miss.error
        assert miss.output["matches"] == []


async def test_search_single_file_overlay(workspace_root: Path) -> None:
    (workspace_root / "f.py").write_text("OLD\n", encoding="utf-8")

    with sandbox_run(workspace_root, "run_sf") as sb:
        make_mirror(sb, "f.py", "NEEDLE\n")
        res = await _dispatch("search_files", pattern="NEEDLE", path="f.py")
    assert res.success, res.error
    assert res.output["matches"] == [{"file": "f.py", "line": 1, "text": "NEEDLE"}]


async def test_search_prunes_minimax_on_main_path(
    workspace_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Main-agent regression: the Python walker skips .minimax internals."""
    # Force the Python engine regardless of rg availability.
    monkeypatch.setattr(search_mod.shutil, "which", lambda _name: None)
    junk = workspace_root / ".minimax" / "backups"
    junk.mkdir(parents=True)
    (junk / "junk.py").write_text("NEEDLE\n", encoding="utf-8")

    res = await _dispatch("search_files", pattern="NEEDLE")
    assert res.success, res.error
    assert res.output["matches"] == []


# ---------------------------------------------------------------------------
# list_directory — merged children view
# ---------------------------------------------------------------------------


async def test_list_directory_shows_sandboxed_projection(workspace_root: Path) -> None:
    sub = workspace_root / "sub"
    sub.mkdir()

    with sandbox_run(workspace_root, "run_ls") as sb:
        make_mirror(sb, "sub/new.txt", "hello")
        res = await _dispatch("list_directory", path="sub")
    assert res.success, res.error

    by_name = {e["name"]: e for e in res.output["entries"]}
    assert "new.txt" in by_name
    entry = by_name["new.txt"]
    # Workspace address — never the sandbox location.
    assert entry["path"] == str(sub / "new.txt")
    assert entry["sandboxed"] is True
    assert entry["size_bytes"] == 5


async def test_list_directory_same_name_takes_mirror(workspace_root: Path) -> None:
    sub = workspace_root / "sub"
    sub.mkdir()
    (sub / "f.txt").write_text("x" * 10, encoding="utf-8")

    with sandbox_run(workspace_root, "run_sm") as sb:
        make_mirror(sb, "sub/f.txt", "y" * 3)
        res = await _dispatch("list_directory", path="sub")
    assert res.success, res.error

    entries = [e for e in res.output["entries"] if e["name"] == "f.txt"]
    assert len(entries) == 1
    assert entries[0]["sandboxed"] is True
    assert entries[0]["size_bytes"] == 3  # the mirror's size, not the workspace's


async def test_list_directory_sandbox_only_dir(workspace_root: Path) -> None:
    with sandbox_run(workspace_root, "run_only") as sb:
        make_mirror(sb, "dirx/inner.txt", "z")
        res = await _dispatch("list_directory", path="dirx")
    assert res.success, res.error
    assert [e["name"] for e in res.output["entries"]] == ["inner.txt"]
    assert res.output["entries"][0]["sandboxed"] is True


async def test_list_directory_hides_base_and_marker(workspace_root: Path) -> None:
    with sandbox_run(workspace_root, "run_hide") as sb:
        make_mirror(sb, "_base/orig.py", "snapshot")
        make_mirror(sb, "mine.txt", "visible")
        marker = sb / ".merged"
        marker.write_text("{}", encoding="utf-8")
        res = await _dispatch("list_directory", path=".")
    assert res.success, res.error

    names = {e["name"] for e in res.output["entries"]}
    assert "_base" not in names
    assert ".merged" not in names
    assert "mine.txt" in names


# ---------------------------------------------------------------------------
# No-sandbox regressions + prompt pin + walker unit
# ---------------------------------------------------------------------------


async def test_no_sandbox_view_has_no_sandboxed_entries(workspace_root: Path) -> None:
    (workspace_root / "a.py").write_text("x = 1\n", encoding="utf-8")
    # A foreign run's sandbox on disk must stay invisible without a
    # sandbox context of our own.
    foreign = workspace_root / ".minimax" / "sandboxes" / "run_foreign"
    foreign.mkdir(parents=True)
    (foreign / "ghost.py").write_text("x = 2\n", encoding="utf-8")

    ls = await _dispatch("list_directory", path=".")
    assert ls.success, ls.error
    assert not any(e.get("sandboxed") for e in ls.output["entries"])
    names = {e["name"] for e in ls.output["entries"]}
    assert "ghost.py" not in names

    find = await _dispatch("find_files", pattern="**/*.py")
    assert find.success, find.error
    files = [f.replace("\\", "/") for f in find.output["files"]]
    assert files == ["a.py"]


def test_protocol_prompt_teaches_overlay() -> None:
    lowered = SANDBOX_PROTOCOL_PROMPT.lower()
    assert "sandboxed: true" in lowered
    assert "search_files" in lowered
    assert "find_files" in lowered
    # The v1.5 split-view caveat must be gone.
    assert "not your sandboxed writes" not in lowered


def test_walk_files_prunes_minimax(tmp_path: Path) -> None:
    """Unit pin on the walker's prune table (pre-v1.6 gap)."""
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    mm = tmp_path / ".minimax" / "sandboxes" / "run_x"
    mm.mkdir(parents=True)
    (mm / "m.py").write_text("x", encoding="utf-8")

    files = list(search_mod._walk_files(tmp_path, "*"))
    assert files == [tmp_path / "a.py"]
