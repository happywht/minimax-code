"""A run ID cannot redirect artifact reads, merges, receipts or deletion."""

from __future__ import annotations

import pytest

from minimax_code.agent.tools.artifacts import ReadArtifactTool, artifact_dir
from minimax_code.agent.tools.sandbox import (
    CollectSubagentTool,
    _prune_sandbox,
    collected_marker_path,
    sandbox_root_for,
)
from minimax_code.workspace_ctx import reset_current_root, set_current_root


@pytest.mark.parametrize("run_id", ["../victim", "..", ".", "a/b", r"a\b", "/tmp", "C:\\temp", "CON", "run:stream"])
async def test_untrusted_run_id_cannot_reach_other_files(tmp_path, run_id):
    marker = tmp_path / "keep.txt"
    marker.write_text("user file", encoding="utf-8")
    token = set_current_root(tmp_path)
    try:
        assert artifact_dir(run_id) is None
        assert sandbox_root_for(run_id) is None
        assert collected_marker_path(run_id) is None
        assert not (await ReadArtifactTool().run(run_id=run_id)).success
        assert not (await CollectSubagentTool().run(run_id=run_id)).success
        _prune_sandbox(run_id)
        assert marker.read_text(encoding="utf-8") == "user file"
    finally:
        reset_current_root(token)


async def test_symlinked_managed_directory_cannot_be_read_or_pruned(tmp_path):
    root, outside = tmp_path / "project", tmp_path / "outside"
    root.mkdir()
    victim = outside / "sandboxes" / "run_victim"
    victim.mkdir(parents=True)
    marker = victim / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    try:
        (root / ".minimax").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink requires OS permission")
    token = set_current_root(root)
    try:
        assert sandbox_root_for("run_victim") is None
        assert artifact_dir("run_victim") is None
        assert not (await CollectSubagentTool().run(run_id="run_victim")).success
        _prune_sandbox("run_victim")
        assert marker.read_text(encoding="utf-8") == "preserve"
    finally:
        reset_current_root(token)
