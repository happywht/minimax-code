"""Offline process ownership tests use real SQLite, including two writers."""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from dataclasses import replace
from pathlib import Path

import pytest

from minimax_code.execution import ExecutionPolicy, execution_scope
from minimax_code.process_context import (
    current_process_target,
    registered_child_target_scope,
    required_target_scope,
)
from minimax_code.process_types import (
    ExecutionOwner,
    ExecutionTargetSpec,
    InstallationIdentity,
    ProcessBoundaryError,
)
from minimax_code.storage.dao.process_executions import (
    ProcessCapacity,
    ProcessCapacityExceeded,
    ProcessExecutionsDAO,
    ProcessRevisionConflict,
)
from minimax_code.storage.db import AsyncDatabase

TARGET = ExecutionTargetSpec("linux-podman-l1", "sha256:" + "a" * 64)
IDENTITY = InstallationIdentity("1" * 32, os.getuid() if hasattr(os, "getuid") else 1000,
    "host", "store", "2" * 32, "location", "", "0", "0")


@pytest.fixture
async def ownership(tmp_path, monkeypatch):
    # Deterministic offline adapter tests do not claim a Windows BOOTTIME.
    monkeypatch.setattr("minimax_code.process_deadline.read_clock",
        lambda: ("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", time.monotonic_ns()))
    db = AsyncDatabase(tmp_path / "service-data" / "state.db")
    await db.connect()
    await db.migrate()
    async with db.transaction() as conn:
        await conn.execute("INSERT INTO sessions(id,title,created_at,updated_at) VALUES('session','','now','now')")
        for run_id, family in (("root", "family"), ("child", "family"), ("other", "other")):
            await conn.execute("INSERT INTO agent_runs(id,session_id,status,created_at,metadata) VALUES(?,'session','running','now',?)",
                               (run_id, '{"budget_family_id":"' + family + '"}'))
        for family, root in (("family", "root"), ("other", "other")):
            await conn.execute("INSERT INTO budget_families(id,root_run_id,created_at) VALUES(?,?,'now')", (family, root))
    dao = ProcessExecutionsDAO(db)
    await dao.enable_durable_ownership()
    db.path.parent.chmod(0o700)
    physical = db.path.parent.stat()
    identity = replace(IDENTITY, data_dir=str(db.path.parent.resolve()),
        data_dir_device=str(physical.st_dev), data_dir_inode=str(physical.st_ino))
    dao._test_identity = identity
    await dao.begin_installation(identity)
    await dao.activate_installation(identity)
    root = tmp_path / "workspace"
    root.mkdir()
    owner = ExecutionOwner.bind(run_id="root", family_id="family", family_root_run_id="root", workspace=root)
    await save_profile(db, owner)
    try:
        yield db, dao, owner
    finally:
        await db.close()


async def intent(dao, owner, *, exec_id="execution", capacity=None, target=TARGET):
    return await dao.intent(exec_id=exec_id, container_name="mm-" + exec_id,
        identity=dao._test_identity, owner=owner, target=target, argv_hash="argv_hash",
        container_cwd="/workspace", deadline=time.time() + 60,
        capacity=capacity or ProcessCapacity())


async def save_profile(db, owner, *, parent_run_id=None, source=None):
    from minimax_code.agent.tools.terminal import ExecCommandTool
    from minimax_code.skill_bindings import SkillBindings, tool_identity
    from minimax_code.skill_execution import _digest

    source = Path(source) if source else Path(owner.workspace).parent / "source"
    source.mkdir(exist_ok=True)
    physical = source.stat()
    copy = {"kind": "directory", "workspace_id": "owned-copy", "workspace": owner.workspace,
        "source_workspace": str(source), "source_identity": [physical.st_dev, physical.st_ino],
        "directory_identity": [owner.workspace_device, owner.workspace_inode]}
    profile = {"version": 3, "execution_target": TARGET.to_dict(),
        "execution_target_hash": TARGET.fingerprint, "execution_installation_id": IDENTITY.install_id,
        "prepared": True, "copy_binding": copy, **{k: copy[k] for k in (
            "workspace_id", "workspace", "source_workspace", "source_identity", "directory_identity")}}
    if parent_run_id is None:
        # This fixture is a supported public authority, not a legacy broad spec.
        profile.update(allowlist=[ExecCommandTool.name], tool_identity=tool_identity([ExecCommandTool()]),
            skill_bindings=SkillBindings.capture(None, None).data, intent={"method": "agent.invoke"},
            hooks_policy="disabled", permission_policy_version=1)
    profile["digest"] = _digest(profile)
    authority = "agent_execution"
    if parent_run_id:
        authority = "process_execution_owner"
        profile["version"] = 1
        profile["parent_run_id"] = parent_run_id
        profile.pop("digest")
    await db.execute("UPDATE agent_runs SET metadata=? WHERE id=?", (
        json.dumps({"budget_family_id": owner.family_id, authority: profile}), owner.run_id))


def test_required_target_is_independent_of_workspace_policy(tmp_path):
    owner = ExecutionOwner.bind(run_id="root", family_id="family", family_root_run_id="root", workspace=tmp_path)
    with required_target_scope(TARGET, owner):
        with execution_scope(ExecutionPolicy(workspace=tmp_path)):
            assert current_process_target().target is TARGET
        with pytest.raises(ProcessBoundaryError):
            with required_target_scope(None):
                pass
        with pytest.raises(ProcessBoundaryError):
            with required_target_scope(ExecutionTargetSpec("other", "sha256:" + "b" * 64)):
                pass
        child = ExecutionOwner.bind(run_id="child", family_id="family", family_root_run_id="root", workspace=tmp_path)
        with registered_child_target_scope(child, parent_run_id="root"):
            assert current_process_target().owner.run_id == "child"
        assert current_process_target().owner == owner
    assert current_process_target() is None


async def test_intent_cas_event_and_foreign_keys(ownership):
    db, dao, owner = ownership
    row = await intent(dao, owner)
    assert row["state"] == "intent" and row["resource_held"] == 1
    row = await dao.transition(row["exec_id"], 0, "created", evidence={"verified": True}, container_id="c" * 64)
    with pytest.raises(ProcessRevisionConflict):
        await dao.transition(row["exec_id"], 0, "starting", evidence={})
    assert len(await dao.events(row["exec_id"])) == 2
    assert (await dao.get(row["exec_id"]))["state"] == "created"
    for table, key, value in (("agent_runs", "id", "root"), ("budget_families", "id", "family"),
                              ("execution_installation", "install_id", IDENTITY.install_id)):
        with pytest.raises(sqlite3.IntegrityError):
            async with db.transaction() as conn:
                await conn.execute(f"DELETE FROM {table} WHERE {key}=?", (value,))


async def test_two_connections_reserve_once(ownership):
    db, dao, owner = ownership
    other = AsyncDatabase(db.path)
    await other.connect()
    try:
        await ProcessExecutionsDAO(other).enable_durable_ownership()
        other_dao = ProcessExecutionsDAO(other)
        other_dao._test_identity = dao._test_identity
        results = await asyncio.gather(intent(dao, owner, exec_id="first"),
            intent(other_dao, owner, exec_id="second"), return_exceptions=True)
        assert sum(isinstance(result, dict) for result in results) == 1
        assert sum(isinstance(result, ProcessCapacityExceeded) for result in results) == 1
        assert len(await dao.active()) == 1
    finally:
        await other.close()


async def test_unknown_cleanup_keeps_resources_and_pauses_admission(ownership):
    _, dao, owner = ownership
    row = await intent(dao, owner)
    row = await dao.transition(row["exec_id"], 0, "cleanup_pending", evidence={"error": "runtime unavailable"})
    assert row["resource_held"] == 1
    with pytest.raises(ProcessBoundaryError, match="unresolved"):
        await intent(dao, owner, exec_id="second")
    with pytest.raises(ProcessBoundaryError, match="absence"):
        await dao.transition(row["exec_id"], 1, "removed", evidence={})
    row = await dao.transition(row["exec_id"], 1, "removed", evidence={"absent": True})
    assert row["resource_held"] == 0
    assert (await intent(dao, owner, exec_id="second"))["state"] == "intent"


async def test_cross_family_and_inactive_owner_rejected(ownership):
    db, dao, owner = ownership
    forged = ExecutionOwner.bind(run_id="other", family_id="family", family_root_run_id="root", workspace=owner.workspace)
    with pytest.raises(ProcessBoundaryError, match="another family"):
        await intent(dao, forged)
    await db.execute("UPDATE agent_runs SET status='cancelled' WHERE id='root'")
    with pytest.raises(ProcessBoundaryError, match="active run"):
        await intent(dao, owner)


async def test_event_failure_rolls_back_state(ownership, monkeypatch):
    _, dao, owner = ownership
    row = await intent(dao, owner)
    async def fail(*args):
        raise RuntimeError("event write failed")
    monkeypatch.setattr(dao, "_event", fail)
    with pytest.raises(RuntimeError, match="event write"):
        await dao.transition(row["exec_id"], 0, "created", evidence={}, container_id="c" * 64)
    saved = await dao.get(row["exec_id"])
    assert saved["revision"] == 0 and saved["container_id"] is None


async def test_128_bit_identity_roundtrip_is_exact_decimal_text(ownership):
    db, dao, owner = ownership
    owner = replace(owner, workspace_device=2**127 + 729, workspace_inode=2**128 - 1)
    await save_profile(db, owner)
    row = await intent(dao, owner)
    assert row["workspace_device"] == str(2**127 + 729)
    assert row["workspace_inode"] == str(2**128 - 1)


async def test_cancelled_owner_cannot_commit_start_intent(ownership):
    db, dao, owner = ownership
    row = await intent(dao, owner)
    row = await dao.transition(row["exec_id"], 0, "created", evidence={}, container_id="c" * 64)
    await db.execute("UPDATE agent_runs SET status='cancelled' WHERE id='root'")
    with pytest.raises(ProcessBoundaryError, match="owner stopped"):
        await dao.transition(row["exec_id"], row["revision"], "starting", evidence={})
    assert (await dao.get(row["exec_id"]))["state"] == "created"


async def test_consistent_sqlite_backup_preserves_unknown_process_liability(ownership):
    from minimax_code.ipc.handlers_data import backup_database, dump_database

    db, dao, owner = ownership
    row = await intent(dao, owner)
    await dao.transition(row["exec_id"], 0, "cleanup_pending", evidence={"runtime": "unknown"})
    envelope = await dump_database(db)
    assert envelope["tables"]["process_executions"][0]["resource_held"] == 1
    assert len(envelope["tables"]["process_execution_events"]) == 2
    result = await backup_database(db, db.path.parent.parent / "backups")
    with sqlite3.connect(result["path"]) as snapshot:
        assert snapshot.execute("SELECT resource_held,state FROM process_executions").fetchone() == (1, "cleanup_pending")
        assert snapshot.execute("SELECT COUNT(*) FROM process_execution_events").fetchone()[0] == 2
        assert snapshot.execute("SELECT install_id FROM execution_installation").fetchone()[0] == IDENTITY.install_id
