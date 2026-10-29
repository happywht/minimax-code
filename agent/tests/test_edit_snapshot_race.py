from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from test_subagent_sandbox import sandbox_scope, workspace_root  # noqa: F401

from minimax_code.agent.tools.edit import EditFileTool


@pytest.mark.parametrize("guarded", [False, True])
async def test_edit_does_not_replace_a_change_after_snapshot(tmp_path, monkeypatch, guarded):
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    path = tmp_path / "notes.txt"
    old = b"header\nold\n"
    other = b"user change\nold\n"
    path.write_bytes(old)
    original = Path.read_bytes
    changed = False

    def read_then_change(current):
        nonlocal changed
        data = original(current)
        if current == path and not changed:
            changed = True
            path.write_bytes(other)
        return data

    monkeypatch.setattr(Path, "read_bytes", read_then_change)
    args = {"path": str(path), "old_string": "old", "new_string": "new"}
    if guarded:
        args["expected_sha256"] = hashlib.sha256(old).hexdigest()
    result = await EditFileTool().run(**args)
    assert not result.success and "changed during write preparation" in result.error
    assert original(path) == other


async def test_overlay_edit_preserves_changed_mirror(workspace_root, sandbox_scope, monkeypatch):  # noqa: F811
    parent = workspace_root / "notes.txt"
    parent.write_bytes(b"header\nold\n")
    tool = EditFileTool()
    assert (await tool.run(path="notes.txt", old_string="header", new_string="mirror")).success
    mirror = sandbox_scope / "notes.txt"
    original = Path.read_bytes
    changed = False
    def read_then_change(current):
        nonlocal changed
        data = original(current)
        if current == mirror and not changed:
            changed = True
            mirror.write_bytes(b"user mirror\nold\n")
        return data
    monkeypatch.setattr(Path, "read_bytes", read_then_change)
    result = await tool.run(path="notes.txt", old_string="old", new_string="new")
    assert not result.success and "changed during write preparation" in result.error
    assert original(mirror) == b"user mirror\nold\n"
    assert original(parent) == b"header\nold\n"


async def test_edit_keeps_existing_universal_newline_behavior(tmp_path, monkeypatch):
    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    path = tmp_path / "lines.txt"
    path.write_bytes(b"old\r\nnext\rline\n")
    result = await EditFileTool().run(path=str(path), old_string="old\nnext", new_string="new\nnext")
    assert result.success and path.read_bytes() == b"new\nnext\nline\n"


async def test_independent_child_race_keeps_parent_and_child_changes(tmp_path, monkeypatch):
    from minimax_code.workspace.child import prepare_child

    monkeypatch.setenv("MINIMAX_CODE_WORKSPACE", str(tmp_path))
    parent = tmp_path / "notes.txt"
    parent.write_bytes(b"parent\nold\n")
    child = await prepare_child(tmp_path, "race_child")
    target = child.root / "notes.txt"
    original = Path.read_bytes
    changed = False
    def read_then_change(current):
        nonlocal changed
        data = original(current)
        if current == target and not changed:
            changed = True
            target.write_bytes(b"child user\nold\n")
        return data
    monkeypatch.setattr(Path, "read_bytes", read_then_change)
    with child.scope():
        result = await EditFileTool().run(path="notes.txt", old_string="old", new_string="new")
    assert not result.success and "changed during write preparation" in result.error
    assert original(parent) == b"parent\nold\n"
    assert original(target) == b"child user\nold\n"
