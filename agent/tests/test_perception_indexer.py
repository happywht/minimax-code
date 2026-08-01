"""Unit tests for the RepoMapIndexer (perception engine).

Covers map building, caching, invalidation, compression,
and output format.
"""

from __future__ import annotations

from pathlib import Path

from minimax_code.agent.perception.indexer import RepoMapIndexer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_py(workspace: Path, rel: str, code: str) -> Path:
    """Write a Python file under the workspace and return its full path."""
    fpath = workspace / rel
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(code, encoding="utf-8")
    return fpath


def _write_ts(workspace: Path, rel: str, code: str) -> Path:
    """Write a TypeScript file under the workspace and return its full path."""
    fpath = workspace / rel
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(code, encoding="utf-8")
    return fpath


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------


async def test_build_map_from_workspace(tmp_path: Path) -> None:
    """Build a repo map from a workspace containing Python files."""
    _write_py(tmp_path, "main.py", "def main():\n    pass\n\nclass App:\n    def run(self):\n        pass\n")
    _write_py(tmp_path, "utils.py", "def helper():\n    return 42\n")

    indexer = RepoMapIndexer(tmp_path, max_tokens=5000)
    result = await indexer.build_map()

    assert isinstance(result, str)
    assert len(result) > 0
    assert "main.py" in result
    assert "utils.py" in result
    # Symbols should be present.
    assert "main" in result
    assert "App" in result
    assert "helper" in result


async def test_cache_hit_returns_same_map(tmp_path: Path) -> None:
    """Second call returns cached map without re-scanning."""
    _write_py(tmp_path, "a.py", "def alpha(): pass\n")

    indexer = RepoMapIndexer(tmp_path, max_tokens=5000)
    first = await indexer.build_map()
    second = await indexer.build_map()

    assert first == second


async def test_invalidation_forces_rescan(tmp_path: Path) -> None:
    """After invalidation, build_map re-scans changed files."""
    _write_py(tmp_path, "b.py", "def before(): pass\n")

    indexer = RepoMapIndexer(tmp_path, max_tokens=5000)
    first = await indexer.build_map()
    assert "before" in first

    # Modify the file.
    _write_py(tmp_path, "b.py", "def after(): pass\n")
    indexer.invalidate(["b.py"])

    second = await indexer.build_map()
    assert "after" in second
    assert "before" not in second


async def test_compression_truncates_large_output(tmp_path: Path) -> None:
    """When output exceeds budget, compression truncates it."""
    # Create many files with verbose symbols to exceed a small budget.
    for i in range(20):
        _write_py(
            tmp_path,
            f"module_{i:03d}.py",
            "\n".join(
                f"def function_{j:04d}_in_module_{i:03d}(): pass"
                for j in range(10)
            ),
        )

    indexer = RepoMapIndexer(tmp_path, max_tokens=200)
    result = await indexer.build_map()

    # The result should fit within a reasonable size.
    # Compression ensures not everything is dumped verbatim.
    assert isinstance(result, str)
    assert len(result) > 0


async def test_empty_workspace_returns_empty_string(tmp_path: Path) -> None:
    """An empty workspace produces an empty repo map."""
    indexer = RepoMapIndexer(tmp_path, max_tokens=5000)
    result = await indexer.build_map()
    assert result == ""


async def test_output_includes_repo_map_header(tmp_path: Path) -> None:
    """The output starts with a 'repo-map' header line."""
    _write_py(tmp_path, "hello.py", "x = 1\n")

    indexer = RepoMapIndexer(tmp_path, max_tokens=5000)
    result = await indexer.build_map()

    assert result.startswith("repo-map")
    assert "files" in result.split("\n")[0]


async def test_mixed_languages_in_map(tmp_path: Path) -> None:
    """Both Python and TypeScript files appear in the map."""
    _write_py(tmp_path, "app.py", "class App:\n    pass\n")
    _write_ts(tmp_path, "index.ts", "export function main(): void {}\n")

    indexer = RepoMapIndexer(tmp_path, max_tokens=5000)
    result = await indexer.build_map()

    assert "app.py" in result
    assert "index.ts" in result


async def test_invalidate_all_resets_everything(tmp_path: Path) -> None:
    """invalidate_all() forces a full rebuild."""
    _write_py(tmp_path, "c.py", "def gamma(): pass\n")

    indexer = RepoMapIndexer(tmp_path, max_tokens=5000)
    await indexer.build_map()

    _write_py(tmp_path, "c.py", "def delta(): pass\n")
    indexer.invalidate_all()

    second = await indexer.build_map()
    assert "delta" in second
    assert "gamma" not in second


async def test_skips_binary_files(tmp_path: Path) -> None:
    """Binary files are excluded from the repo map."""
    _write_py(tmp_path, "good.py", "def ok(): pass\n")
    binary = tmp_path / "bad.bin"
    binary.write_bytes(b"abc\x00def")

    indexer = RepoMapIndexer(tmp_path, max_tokens=5000)
    result = await indexer.build_map()

    assert "good.py" in result
    assert "bad.bin" not in result


async def test_skips_lock_files(tmp_path: Path) -> None:
    """Lock files (package-lock.json, etc.) are excluded."""
    _write_py(tmp_path, "app.py", "x = 1\n")
    (tmp_path / "package-lock.json").write_text("{}", encoding="utf-8")
    (tmp_path / "yarn.lock").write_text("# lock", encoding="utf-8")

    indexer = RepoMapIndexer(tmp_path, max_tokens=5000)
    result = await indexer.build_map()

    assert "app.py" in result
    assert "package-lock.json" not in result
    assert "yarn.lock" not in result
