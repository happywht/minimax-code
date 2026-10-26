"""Real RunService/SQLite private probe ownership with controlled runtime output."""
from __future__ import annotations

import asyncio
import json
import time
from types import SimpleNamespace

import pytest
from test_process_ownership import TARGET
from test_process_ownership import ownership as ownership
from test_process_supervisor import FakeProcess
from test_process_supervisor import supervisor as supervisor

from minimax_code import process_preflight as module
from minimax_code.execution import terminate_tree as _terminate_tree
from minimax_code.pid1_guard import PYTHON_PATH
from minimax_code.process_types import ProcessBoundaryError
from minimax_code.runs import get_run_service
from minimax_code.storage.dao.runs import AgentRunsDAO


@pytest.fixture
def probe_setup(supervisor, tmp_path, monkeypatch):
    manager, controller, dao, _ = supervisor
    manager._preflight_target = TARGET
    controller.payload_exit_code = 0
    paths = []

    def make_directory(*, prefix):
        path = tmp_path / (prefix + str(len(paths)))
        path.mkdir(mode=0o700)
        paths.append(path)
        return str(path)

    # The production POSIX permission check has no Windows substitute. This
    # fixture keeps real path identities while exercising orchestration offline.
    monkeypatch.setattr(module, "_physical", lambda path: (path.stat().st_dev, path.stat().st_ino))
    monkeypatch.setattr(module.tempfile, "mkdtemp", make_directory)
    monkeypatch.setattr(module, "time_namespace", lambda: "time:[unit-test]")

    def process_output(self, controller):
        self.controller = controller
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdout.feed_data(json.dumps({"python": PYTHON_PATH, "version": [3, 12, 0],
            "pid": 2, "ppid": 1, "boot_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "boottime_ns": time.monotonic_ns(), "time_namespace": "time:[unit-test]"}).encode())
        self.stdout.feed_eof()
        self.stderr.feed_eof()

    monkeypatch.setattr(FakeProcess, "__init__", process_output)
    async def terminate(proc):
        if isinstance(proc, FakeProcess):
            proc.controller.payload_finished.set()
            await proc.wait()
        else:
            await _terminate_tree(proc)
    monkeypatch.setattr("minimax_code.execution.terminate_tree", terminate)
    catalog = SimpleNamespace(supervisor=manager, _targets={"linux-podman-l1": TARGET.to_dict()},
        _db=dao.db, data_dir=dao.db.path.parent, installation_id=manager.identity.install_id,
        assert_extension_boundary=lambda: None)
    return catalog, SimpleNamespace(), paths


async def test_private_preflight_owns_real_run_family_and_releases_exact_container(probe_setup):
    catalog, server, paths = probe_setup
    proof = await module.run_preflight(catalog, server)
    run = await AgentRunsDAO(catalog._db).get_run(proof.run_id)
    result = await AgentRunsDAO(catalog._db).result_payload(proof.run_id)
    binding = await catalog.supervisor.dao.binding_for_run(proof.run_id)
    process = await catalog.supervisor.dao.get(proof.execution_id)
    assert run["status"] == "completed" and result["model_calls"] == 0
    assert result["container_absent"] is True
    assert result["raw_stdout"]
    assert binding.owner.run_id == binding.owner.family_root_run_id == proof.run_id
    assert run["metadata"]["process_execution_owner"]["version"] == 2
    assert process["state"] == "removed" and process["resource_held"] == 0
    assert all(not path.exists() for path in paths)
    assert not module.ACCEPTED_GOVERNORS


@pytest.mark.parametrize("boundary", ["session", "admit"])
async def test_preflight_setup_failure_removes_only_proven_empty_directories(probe_setup, monkeypatch, boundary):
    catalog, server, paths = probe_setup

    async def fail(*args, **kwargs):
        raise RuntimeError("controlled setup failure")

    if boundary == "session":
        monkeypatch.setattr("minimax_code.storage.dao.sessions.SessionsDAO.create", fail)
    else:
        monkeypatch.setattr(get_run_service(server), "admit", fail)
    with pytest.raises(RuntimeError, match="controlled setup failure"):
        await module.run_preflight(catalog, server)
    assert all(not path.exists() for path in paths)
    assert not await catalog.supervisor.dao.active()


async def test_preflight_double_cancel_retains_container_cleanup(probe_setup):
    catalog, server, paths = probe_setup
    controller = catalog.supervisor._controller
    controller.block_at = "create"
    task = asyncio.create_task(module.run_preflight(catalog, server))
    await asyncio.wait_for(controller.blocked.wait(), timeout=5)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    controller.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not await catalog.supervisor.dao.active()
    assert controller.containers == {}
    assert all(not path.exists() for path in paths)


def test_private_probe_metadata_alone_cannot_authorize_start():
    snapshot = {"kind": "runtime_preflight", "probe_kind": module.PROBE_KIND,
        "probe_argv_hash": module.probe_argv_hash()}
    with pytest.raises(ProcessBoundaryError, match="Only the private fixed"):
        module.assert_private_probe("forged-run", snapshot, module.probe_argv_hash())
