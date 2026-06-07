"""Unit tests for the GlobFindTool (find_files).

Covers pattern matching, result limiting, directory exclusion,
path validation, and error handling.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from minimax_code.agent.tools.glob import GlobFindTool


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Create a workspace tree with various files and point the safety policy at it."""
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))

    # Create a mix of file types.
    (tmp_path / "main.py").write_text("print('hello')", encoding="utf-8")
    (tmp_path / "utils.py").write_text("def helper(): pass", encoding="utf-8")
    (tmp_path / "readme.md").write_text("# Hello", encoding="utf-8")
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")

    # Subdirectory with TypeScript files.
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "index.ts").write_text("export const x = 1", encoding="utf-8")
    (sub / "app.ts").write_text("console.log('app')", encoding="utf-8")
    (sub / "util.ts").write_text("export function add(a: number, b: number) { return a + b; }", encoding="utf-8")

    # Nested subdirectory.
    deep = sub / "components"
    deep.mkdir()
    (deep / "button.ts").write_text("export class Button {}", encoding="utf-8")

    # node_modules — should be excluded by default.
    nm = tmp_path / "node_modules"
    nm.mkdir()
    (nm / "lib.js").write_text("module.exports = {}", encoding="utf-8")

    # .git — should be excluded by default.
    git_dir = tmp_path / ".git"
    git_dir.mkdir()
    (git_dir / "config").write_text("[core]", encoding="utf-8")

    return tmp_path


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


async def test_find_python_files(workspace: Path) -> None:
    """Find all .py files at the workspace root."""
    tool = GlobFindTool()
    result = await tool.run(pattern="*.py")
    assert result.success
    assert result.output["count"] == 2
    files = result.output["files"]
    assert "main.py" in files
    assert "utils.py" in files


async def test_find_ts_files_with_doublestar(workspace: Path) -> None:
    """Find all .ts files recursively with **/*.ts pattern."""
    tool = GlobFindTool()
    result = await tool.run(pattern="**/*.ts")
    assert result.success
    assert result.output["count"] >= 3
    files = result.output["files"]
    assert "src/index.ts" in files or "src\\index.ts" in files.replace("\\", "/")
    # The forward-slash normalisation ensures consistent paths.
    normalised = [f.replace("\\", "/") for f in files]
    assert "src/index.ts" in normalised
    assert "src/app.ts" in normalised


async def test_max_results_limiting(workspace: Path) -> None:
    """max_results caps the returned file list."""
    tool = GlobFindTool()
    result = await tool.run(pattern="**/*", max_results=2)
    assert result.success
    assert result.output["count"] <= 2


async def test_exclude_dirs_filtering(workspace: Path) -> None:
    """Custom exclude_dirs prevents matching files inside excluded directories."""
    tool = GlobFindTool()
    result = await tool.run(
        pattern="**/*",
        exclude_dirs=["node_modules", ".git", "src"],
    )
    assert result.success
    files = result.output["files"]
    normalised = [f.replace("\\", "/") for f in files]
    # Nothing under src/ should appear.
    for f in normalised:
        assert not f.startswith("src/")


async def test_nonexistent_path_returns_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Searching a path that does not exist returns a failure result."""
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    tool = GlobFindTool()
    result = await tool.run(pattern="*", path="does_not_exist")
    assert not result.success
    assert "not found" in (result.error or "")


async def test_empty_pattern_validation(workspace: Path) -> None:
    """An empty pattern string is rejected."""
    tool = GlobFindTool()
    result = await tool.run(pattern="")
    assert not result.success
    assert "non-empty" in (result.error or "")


async def test_pattern_not_string_rejected(workspace: Path) -> None:
    """A non-string pattern is rejected."""
    tool = GlobFindTool()
    result = await tool.run(pattern=123)
    assert not result.success


async def test_file_path_arg_returns_single_file(workspace: Path) -> None:
    """Passing a file (not dir) as path returns that single file."""
    tool = GlobFindTool()
    result = await tool.run(pattern="*", path="main.py")
    assert result.success
    assert result.output["count"] == 1


async def test_default_excludes_skip_node_modules(workspace: Path) -> None:
    """node_modules files are excluded by default (no explicit exclude_dirs)."""
    tool = GlobFindTool()
    result = await tool.run(pattern="**/*")
    assert result.success
    normalised = [f.replace("\\", "/") for f in result.output["files"]]
    for f in normalised:
        assert "node_modules" not in f


async def test_default_excludes_skip_git(workspace: Path) -> None:
    """.git files are excluded by default."""
    tool = GlobFindTool()
    result = await tool.run(pattern="**/*")
    assert result.success
    normalised = [f.replace("\\", "/") for f in result.output["files"]]
    for f in normalised:
        assert not f.startswith(".git/")


async def test_path_traversal_rejected(workspace: Path) -> None:
    """Attempts to traverse outside the workspace are rejected."""
    tool = GlobFindTool()
    result = await tool.run(pattern="*", path="../../etc")
    assert not result.success


async def test_max_depth_limits_traversal(workspace: Path) -> None:
    """Small max_depth limits how deep the traversal goes."""
    tool = GlobFindTool()
    # max_depth=1 allows root (depth 0) + first-level dirs (depth 1).
    result = await tool.run(pattern="**/*", max_depth=1)
    assert result.success
    normalised = [f.replace("\\", "/") for f in result.output["files"]]
    # Nothing deeper than 1 level (e.g. src/components/button.ts) should appear.
    for f in normalised:
        assert f.count("/") <= 1
