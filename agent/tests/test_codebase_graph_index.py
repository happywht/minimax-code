"""Tests for :class:`CodebaseGraphIndex`."""

from __future__ import annotations

from pathlib import Path

import pytest

from minimax_code.codebase.graph_index import CodebaseGraphIndex


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    ws = tmp_path / "workspace"
    ws.mkdir()
    (ws / "main.py").write_text(
        "def helper(x):\n    return x + 1\n\n"
        "def main():\n    result = helper(1)\n    return result\n"
    )
    return ws


@pytest.mark.asyncio
async def test_graph_index_finds_definitions(workspace: Path) -> None:
    index = CodebaseGraphIndex(workspace)
    result = await index.find_definitions("helper")
    assert result["symbol"] == "helper"
    assert len(result["locations"]) >= 1
    loc = result["locations"][0]
    assert loc["file_path"] == "main.py"
    assert loc["line"] == 1


@pytest.mark.asyncio
async def test_graph_index_finds_references(workspace: Path) -> None:
    index = CodebaseGraphIndex(workspace)
    result = await index.find_references("helper", include_definition=True)
    assert result["symbol"] == "helper"
    # Should include the definition on line 1 and the reference on line 5.
    lines = {loc["line"] for loc in result["locations"]}
    assert 1 in lines
    assert 5 in lines


@pytest.mark.asyncio
async def test_graph_index_goto_definition_by_position(workspace: Path) -> None:
    index = CodebaseGraphIndex(workspace)
    # Cursor on "helper" in "result = helper(1)" at line 5, column 14.
    result = await index.goto_definition("main.py", 5, 14)
    assert result["symbol"] == "helper"
    assert len(result["locations"]) >= 1
    assert result["locations"][0]["line"] == 1


@pytest.mark.asyncio
async def test_graph_index_empty_for_unknown_symbol(workspace: Path) -> None:
    index = CodebaseGraphIndex(workspace)
    result = await index.find_definitions("does_not_exist")
    assert result["locations"] == []


async def test_planning_navigation_does_not_spawn_git(workspace, monkeypatch):
    import subprocess

    from minimax_code.execution import ExecutionPolicy, execution_scope

    def forbidden(*args, **kwargs):
        raise AssertionError("Code navigation must not start unmanaged processes")

    monkeypatch.setattr(subprocess, "run", forbidden)
    with execution_scope(ExecutionPolicy(workspace, read_only=True)):
        result = await CodebaseGraphIndex(workspace).find_definitions("helper")
    assert result["locations"]


async def test_graph_index_excludes_external_symlink(workspace, tmp_path):
    external = tmp_path / "private.py"
    external.write_text("def private_external_symbol(): pass\n", encoding="utf-8")
    try:
        (workspace / "linked.py").symlink_to(external)
    except OSError:
        pytest.skip("symlink requires OS permission")
    index = CodebaseGraphIndex(workspace)
    assert (await index.find_definitions("helper"))["locations"]
    assert not (await index.find_definitions("private_external_symbol"))["locations"]
