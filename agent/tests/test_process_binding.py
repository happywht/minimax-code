"""An actual admitted run snapshot is required, not an in-memory copy flag."""
from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_process_ownership import TARGET, intent, save_profile
from test_process_ownership import ownership as _ownership_fixture

from minimax_code.process_binding import load_execution_binding
from minimax_code.process_types import ProcessBoundaryError
from minimax_code.skill_execution import _digest

ownership = _ownership_fixture


async def run_row(db, run_id="root"):
    row = dict(await db.fetchone("SELECT * FROM agent_runs WHERE id=?", (run_id,)))
    row["metadata"] = json.loads(row["metadata"])
    return row


@pytest.mark.parametrize("version", [1, 2])
def test_legacy_is_explicit_host_and_cannot_smuggle_new_target(version):
    run = {"id": "root", "metadata": {"agent_execution": {"version": version}}}
    assert load_execution_binding(run) is None
    run["metadata"]["agent_execution"]["execution_target"] = TARGET.to_dict()
    with pytest.raises(ProcessBoundaryError, match="Legacy"):
        load_execution_binding(run)


@pytest.mark.parametrize("damage", ["missing_target", "null_target", "missing_resource", "target_hash", "outer_digest", "copy_kind", "copy_identity"])
async def test_v3_damage_never_downgrades_to_host(ownership, damage):
    db, _, _ = ownership
    run = await run_row(db)
    profile = run["metadata"]["agent_execution"]
    if damage == "missing_target":
        profile.pop("execution_target")
    elif damage == "null_target":
        profile["execution_target"] = None
    elif damage == "missing_resource":
        profile["execution_target"].pop("pids_limit")
    elif damage == "target_hash":
        profile["execution_target_hash"] = "foreign"
    elif damage == "copy_kind":
        profile["copy_binding"]["kind"] = "git_worktree"
    elif damage == "copy_identity":
        profile["copy_binding"]["directory_identity"] = [0, 0]
    if damage != "outer_digest":
        profile["digest"] = _digest({k: v for k, v in profile.items() if k != "digest"})
    else:
        profile["digest"] = "foreign"
    with pytest.raises(ProcessBoundaryError):
        load_execution_binding(run, family_root_run_id="root")


async def test_two_authorities_rejected_even_if_identical(ownership):
    db, _, _ = ownership
    run = await run_row(db)
    run["metadata"]["process_execution_owner"] = copy.deepcopy(run["metadata"]["agent_execution"])
    with pytest.raises(ProcessBoundaryError, match="conflicting"):
        load_execution_binding(run)


async def test_in_memory_owner_cannot_replace_admitted_copy(ownership, tmp_path):
    _, dao, owner = ownership
    forged = tmp_path / "unadmitted"
    forged.mkdir()
    info = forged.stat()
    owner = replace(owner, workspace=str(forged), workspace_device=info.st_dev, workspace_inode=info.st_ino)
    with pytest.raises(ProcessBoundaryError, match="authoritative admitted snapshot"):
        await intent(dao, owner)


async def test_child_has_its_own_real_run_and_copy_same_frozen_target(ownership):
    db, dao, owner = ownership
    child_path = Path(owner.workspace).parent / "child-copy"
    child_path.mkdir()
    info = child_path.stat()
    child = replace(owner, run_id="child", workspace=str(child_path),
                    workspace_device=info.st_dev, workspace_inode=info.st_ino)
    await save_profile(db, child, parent_run_id="root", source=owner.workspace)
    row = await intent(dao, child)
    assert row["run_id"] == "child" and row["family_root_run_id"] == "root"
    assert row["workspace"] == str(child_path)
    assert row["target_hash"] == TARGET.fingerprint


async def test_child_cannot_claim_unrelated_parent_or_copy_source(ownership):
    db, dao, owner = ownership
    child_path = Path(owner.workspace).parent / "child-copy"
    child_path.mkdir()
    info = child_path.stat()
    child = replace(owner, run_id="child", workspace=str(child_path),
                    workspace_device=info.st_dev, workspace_inode=info.st_ino)
    await save_profile(db, child, parent_run_id="other", source=owner.workspace)
    with pytest.raises(ProcessBoundaryError, match="parent authority"):
        await intent(dao, child)
    await save_profile(db, child, parent_run_id="root")  # unrelated source directory
    with pytest.raises(ProcessBoundaryError, match="parent authority"):
        await intent(dao, child)
