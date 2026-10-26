"""Real SQLite/lease/filesystem bootstrap crash and identity tests."""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest
from test_process_supervisor import CID, Controller

from minimax_code.process_installation import (
    MARKER_NAME,
    bootstrap_installation,
    read_installation_marker,
)
from minimax_code.process_types import ProcessBoundaryError, canonical_hash
from minimax_code.runtime.service_lease import acquire_service_lease
from minimax_code.storage.dao.process_executions import ProcessExecutionsDAO
from minimax_code.storage.db import AsyncDatabase


@pytest.fixture
async def installation(tmp_path):
    data = tmp_path / "service"
    data.mkdir(mode=0o700)
    db = AsyncDatabase(data / "state.db")
    await db.connect()
    await db.migrate()
    dao = ProcessExecutionsDAO(db)
    lease = acquire_service_lease(data)
    controller = Controller()
    async def boot():
        return await bootstrap_installation(dao, controller=controller, data_dir=data, lease=lease)
    try:
        yield db, dao, lease, controller, data, boot
    finally:
        await db.close()
        lease.close()


async def test_bootstrap_durable_marker_identity_and_repeat(installation):
    db, dao, _, _, data, boot = installation
    identity = await boot()
    row = await dao.installation()
    assert row["phase"] == "active" and row["marker_hash"] == identity.marker_hash
    assert row["data_dir_device"] == str(data.stat().st_dev)
    assert row["data_dir_inode"] == str(data.stat().st_ino)
    assert read_installation_marker(data, uid=identity.uid) == identity.marker()
    assert (data / MARKER_NAME).stat().st_nlink == 1
    assert (await db.fetchone("PRAGMA synchronous"))[0] == 2
    assert await boot() == identity


@pytest.mark.parametrize("operation", ["pending", "publish", "activate"])
@pytest.mark.parametrize("after_effect", [False, True])
async def test_restart_at_every_bootstrap_commit_window(installation, monkeypatch, operation, after_effect):
    _, dao, _, _, data, boot = installation
    if operation == "publish":
        import minimax_code.process_installation as module
        original = module.publish_installation_marker
        def fault(*args):
            if after_effect:
                original(*args)
            raise RuntimeError("service died during marker publication")
        with monkeypatch.context() as patch:
            patch.setattr(module, "publish_installation_marker", fault)
            with pytest.raises(RuntimeError):
                await boot()
    else:
        method = "begin_installation" if operation == "pending" else "activate_installation"
        original = getattr(dao, method)
        async def fault(*args):
            if after_effect:
                await original(*args)
            raise RuntimeError("service died during durable transition")
        with monkeypatch.context() as patch:
            patch.setattr(dao, method, fault)
            with pytest.raises(RuntimeError):
                await boot()
    row_before = await dao.installation()
    identity = await boot()
    if row_before:
        assert identity.install_id == row_before["install_id"]
        assert identity.bootstrap_nonce == row_before["bootstrap_nonce"]
    assert (await dao.installation())["phase"] == "active"
    assert read_installation_marker(data, uid=identity.uid) == identity.marker()


async def test_recover_hardlink_published_before_temporary_unlink(installation, monkeypatch):
    _, dao, _, _, data, boot = installation
    original = os.link
    def link_then_die(source, destination):
        original(source, destination)
        raise RuntimeError("service died after hardlink")
    with monkeypatch.context() as patch:
        patch.setattr(os, "link", link_then_die)
        with pytest.raises(RuntimeError):
            await boot()
    assert (data / MARKER_NAME).stat().st_nlink == 2
    assert (await dao.installation())["phase"] == "bootstrap_pending"
    identity = await boot()
    assert (data / MARKER_NAME).stat().st_nlink == 1
    assert not list(data.glob(".process-installation-*.tmp"))
    assert read_installation_marker(data, uid=identity.uid) == identity.marker()


async def test_publication_conflict_never_replaces_marker(installation, monkeypatch):
    _, dao, _, _, data, boot = installation
    original = os.link
    def conflict(source, destination):
        Path(destination).write_text('{"foreign":true}')
        Path(destination).chmod(0o600)
        return original(source, destination)
    with monkeypatch.context() as patch:
        patch.setattr(os, "link", conflict)
        with pytest.raises(ProcessBoundaryError, match="publication conflict"):
            await boot()
    assert json.loads((data / MARKER_NAME).read_text()) == {"foreign": True}
    assert (await dao.installation())["phase"] == "bootstrap_pending"
    with pytest.raises(ProcessBoundaryError, match="marker mismatch"):
        await boot()


@pytest.mark.parametrize("mutation", ["missing", "truncated", "wrong_nonce", "hardlink"])
async def test_active_marker_mutations_never_recreate_or_adopt(installation, mutation):
    _, dao, _, controller, data, boot = installation
    identity = await boot()
    marker = data / MARKER_NAME
    if mutation == "missing":
        marker.unlink()
    elif mutation == "truncated":
        marker.write_text("{")
    elif mutation == "wrong_nonce":
        marker.write_text(json.dumps({**identity.marker(), "bootstrap_nonce": "foreign"}))
    else:
        os.link(marker, data / "foreign-link")
    with pytest.raises(ProcessBoundaryError):
        await boot()
    assert (await dao.installation())["install_id"] == identity.install_id
    assert "stop" not in controller.calls and "remove" not in controller.calls
    if mutation == "missing":
        assert not marker.exists()


async def test_database_missing_with_marker_never_bootstraps_new_identity(installation):
    db, dao, _, _, data, boot = installation
    identity = await boot()
    await db.execute("DELETE FROM execution_installation")
    with pytest.raises(ProcessBoundaryError, match="without its database identity"):
        await boot()
    assert await dao.installation() is None
    assert read_installation_marker(data, uid=identity.uid) == identity.marker()


async def test_fresh_database_with_same_location_orphan_is_blocked(installation):
    _, dao, _, controller, data, boot = installation
    runtime = await controller.info()
    location = canonical_hash({"host": runtime["host_fingerprint"], "uid": runtime["uid"],
        "graph_root": runtime["graph_root"], "data_dir": str(data.resolve())})
    controller.containers[CID] = {"Id": CID, "Name": "orphan", "Config": {"Labels": {
        "ai.minimax-code.product": "minimax-code", "ai.minimax-code.location": location}}}
    with pytest.raises(ProcessBoundaryError, match="without its database identity"):
        await boot()
    assert await dao.installation() is None and not (data / MARKER_NAME).exists()
    assert CID in controller.containers
    assert "stop" not in controller.calls and "remove" not in controller.calls


async def test_inventory_failure_is_never_treated_as_empty(installation):
    _, dao, _, controller, data, boot = installation
    controller.fail_after.add("inventory")
    with pytest.raises(ProcessBoundaryError, match="inventory"):
        await boot()
    assert await dao.installation() is None and not (data / MARKER_NAME).exists()


async def test_restored_store_same_path_orphan_still_blocks_new_installation(installation, monkeypatch):
    _, dao, _, controller, data, boot = installation
    runtime = await controller.info()
    old_location = canonical_hash({"host": runtime["host_fingerprint"], "uid": runtime["uid"],
        "graph_root": runtime["graph_root"], "data_dir": str(data.resolve())})
    controller.containers[CID] = {"Id": CID, "Name": "restored-orphan", "Config": {"Labels": {
        "ai.minimax-code.product": "minimax-code", "ai.minimax-code.location": old_location}}}
    async def restored_info():
        return {**runtime, "store_fingerprint": "restored-different-inode"}
    monkeypatch.setattr(controller, "info", restored_info)
    with pytest.raises(ProcessBoundaryError, match="without its database identity"):
        await boot()
    assert await dao.installation() is None
    assert CID in controller.containers and "remove" not in controller.calls


async def test_copied_database_and_marker_do_not_acquire_original_ownership(installation, tmp_path):
    db, _, _, controller, data, boot = installation
    await boot()
    destination = tmp_path / "copied-service"
    destination.mkdir(mode=0o700)
    from minimax_code.ipc.handlers_data import backup_database

    snapshot = await backup_database(db, tmp_path / "snapshots")
    shutil.copy2(snapshot["path"], destination / "state.db")
    shutil.copy2(data / MARKER_NAME, destination / MARKER_NAME)
    other = AsyncDatabase(destination / "state.db")
    await other.connect()
    lease = acquire_service_lease(destination)
    try:
        with pytest.raises(ProcessBoundaryError, match="directory identity changed"):
            await bootstrap_installation(ProcessExecutionsDAO(other), controller=controller,
                data_dir=destination, lease=lease)
    finally:
        await other.close()
        lease.close()


@pytest.mark.skipif(os.name != "posix", reason="POSIX mode ownership")
async def test_existing_directory_permissions_are_not_silently_changed(installation):
    _, dao, _, _, data, boot = installation
    data.chmod(0o755)
    with pytest.raises(ProcessBoundaryError, match="mode 0700"):
        await boot()
    assert data.stat().st_mode & 0o777 == 0o755
    assert await dao.installation() is None
    data.chmod(0o700)


def test_target_hash_rejects_nan():
    with pytest.raises(ValueError):
        canonical_hash({"resource": float("nan")})
