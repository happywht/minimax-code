"""Faultable controller with real durable storage and actual service lease."""
from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import os
import re
from dataclasses import replace
from pathlib import Path

import pytest
from test_process_ownership import IDENTITY, TARGET, intent, save_profile
from test_process_ownership import ownership as _ownership_fixture

from minimax_code.pid1_guard import PYTHON_PATH
from minimax_code.podman_execution import (
    _CONTAINER_ENV,
    _LABEL_PREFIX,
    PodmanSupervisor,
    _labels,
    _PodmanController,
)
from minimax_code.process_runtime import container_mapping, runtime_evidence
from minimax_code.process_types import ProcessBoundaryError
from minimax_code.runtime.service_lease import acquire_service_lease

ownership = _ownership_fixture

CID = "c" * 64
RECORDED_RUNTIME_FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "podman-4.9.3-runtime.json").read_text(encoding="utf-8"))


def runtime_fixture_for_uid(uid):
    # The recording belongs to UID 1000; the real filesystem-backed test
    # installation belongs to the runner. Model keep-id for that owner without
    # changing the captured evidence or relaxing production namespace checks.
    fixture = copy.deepcopy(RECORDED_RUNTIME_FIXTURE)
    fixture["uid"] = uid
    fixture["podman_info"]["host"]["idMappings"]["uidmap"][0]["host_id"] = uid
    fixture["HostConfig"]["IDMappings"]["UidMap"] = [
        f"0:1:{uid}", f"{uid}:0:1", f"{uid + 1}:{uid + 1}:{65536 - uid}"]
    return fixture


RUNTIME_FIXTURE = runtime_fixture_for_uid(IDENTITY.uid)
RUNTIME = runtime_evidence(RUNTIME_FIXTURE["podman_info"], uid=IDENTITY.uid, gid=1001)


@pytest.mark.parametrize("uid", [1000, 1001, 12345])
def test_offline_runtime_fixture_preserves_runner_uid(uid):
    fixture = runtime_fixture_for_uid(uid)
    evidence = runtime_evidence(fixture["podman_info"], uid=uid, gid=1001)
    mapping = container_mapping(fixture["HostConfig"], evidence)
    assert evidence["host_uid"] == uid
    assert mapping["uid_host_map"][1] == [uid, uid, 1]
    assert mapping["gid_host_map"][1] == [1001, 1001, 1]
    assert RECORDED_RUNTIME_FIXTURE["uid"] == 1000
    assert RECORDED_RUNTIME_FIXTURE["podman_info"]["host"]["idMappings"]["uidmap"][0]["host_id"] == 1000


@pytest.mark.parametrize("uid", [1001, 12345])
def test_offline_runtime_fixture_rejects_foreign_service_uid(uid):
    fixture = runtime_fixture_for_uid(uid)
    with pytest.raises(ProcessBoundaryError, match="does not belong to the service UID/GID"):
        runtime_evidence(fixture["podman_info"], uid=uid + 1, gid=1001)
    evidence = runtime_evidence(fixture["podman_info"], uid=uid, gid=1001)
    with pytest.raises(ProcessBoundaryError, match="does not preserve the rootless owner"):
        container_mapping(RECORDED_RUNTIME_FIXTURE["HostConfig"], evidence)


class FakeProcess:
    pid = 123456789
    stdin = None
    returncode = None

    def __init__(self, controller):
        self.controller = controller
        self.stdout, self.stderr = asyncio.StreamReader(), asyncio.StreamReader()
        self.stdout.feed_data(b"payload stdout")
        self.stdout.feed_eof()
        self.stderr.feed_data(b"payload stderr")
        self.stderr.feed_eof()

    async def wait(self):
        await self.controller.payload_finished.wait()
        self.returncode = 125  # Deliberately different from payload ExitCode.
        return self.returncode


class Controller:
    seccomp_path = Path("/private/seccomp-policy.json")
    def __init__(self):
        self.containers = {}
        self.calls = []
        self.payload_finished = asyncio.Event()
        self.payload_finished.set()
        self.payload_exit_code = 42
        self.fail_after = set()
        self.block_at = None
        self.blocked = asyncio.Event()
        self.release = asyncio.Event()

    async def checkpoint(self, name):
        self.calls.append(name)
        if name == self.block_at:
            self.blocked.set()
            await self.release.wait()
        if name in self.fail_after:
            raise ProcessBoundaryError("lost " + name + " response")

    async def info(self):
        await self.checkpoint("info")
        return {"graph_root": "/runtime/graph-root", "runtime_evidence": copy.deepcopy(RUNTIME), **{key: getattr(IDENTITY, key)
            for key in ("uid", "host_fingerprint", "store_fingerprint")}}

    async def image(self, image):
        await self.checkpoint("image")

    async def inventory(self):
        await self.checkpoint("inventory")
        return list(self.containers)

    async def create(self, row, argv, **kwargs):
        from minimax_code.pid1_guard import build_guard_argv
        guarded = build_guard_argv(exec_id=row["exec_id"] if re.fullmatch(r"[0-9a-f]{32}", row["exec_id"]) else "e" * 32,
            boot_id=row["deadline_boot_id"], deadline_boottime_ns=row["deadline_boottime_ns"], argv=argv)
        self.containers[CID] = {
            "Id": CID, "Name": row["container_name"], "Image": TARGET.image_id,
            "Path": guarded[0], "Args": list(guarded[1:]), "EffectiveCaps": [], "BoundingCaps": [],
            "Config": {"Labels": _labels(row), "Env": [k + "=" + v for k, v in _CONTAINER_ENV.items()],
                "WorkingDir": row["container_cwd"], "Tty": False,
                "Entrypoint": list(guarded[:1]), "Cmd": list(guarded[1:]),
                "User": f"{IDENTITY.uid}:1001", "Timeout": 60},
            "HostConfig": {"Privileged": False, "ReadonlyRootfs": True, "NetworkMode": "none",
                "PidMode": "private", "IpcMode": "none", "UsernsMode": "private", "AutoRemove": False,
                "IDMappings": copy.deepcopy(RUNTIME_FIXTURE["HostConfig"]["IDMappings"]),
                "UTSMode": "private", "CgroupMode": "private", "Init": False,
                "Memory": TARGET.memory_bytes, "MemorySwap": TARGET.memory_bytes,
                "PidsLimit": TARGET.pids_limit, "NanoCpus": TARGET.cpus_millis * 1_000_000,
                "CpuQuota": TARGET.cpus_millis * 100, "CpuPeriod": 100000,
                "SecurityOpt": ["no-new-privileges", "seccomp=" + str(self.seccomp_path)], "RestartPolicy": {"Name": "no"},
                "Tmpfs": {"/tmp": f"rw,noexec,nosuid,nodev,size={TARGET.tmpfs_bytes}"},
                "LogConfig": {"Type": "k8s-file", "Size": "4.981MB"}},
            "Mounts": [{"Type": "bind", "Source": row["workspace"], "Destination": "/workspace", "RW": True}],
            "State": {"Running": False, "Pid": 0, "ExitCode": 0, "OOMKilled": False,
                "Status": "created", "StartedAt": "0001-01-01T00:00:00Z",
                "FinishedAt": "0001-01-01T00:00:00Z"}}
        Path(kwargs["cid_file"]).write_text(CID)
        await self.checkpoint("create")

    async def inspect(self, reference):
        await self.checkpoint("inspect")
        for cid, data in self.containers.items():
            if reference in (cid, data["Name"]):
                data = copy.deepcopy(data)
                if self.payload_finished.is_set() and "start" in self.calls:
                    data["State"].update(Running=False, Pid=0, ExitCode=self.payload_exit_code,
                        Status="exited", FinishedAt="2026-10-26T10:00:01Z")
                return data
        return None

    async def exists(self, reference):
        await self.checkpoint("exists")
        return reference in self.containers

    async def start(self, cid, **kwargs):
        self.containers[cid]["State"].update(Running=True, Pid=100,
            Status="running", StartedAt="2026-10-26T10:00:00Z")
        await self.checkpoint("start")
        return FakeProcess(self)

    async def stop(self, cid):
        self.containers[cid]["State"].update(Running=False, Pid=0, ExitCode=137,
            Status="exited", FinishedAt="2026-10-26T10:00:01Z")
        self.payload_finished.set()
        await self.checkpoint("stop")

    async def kill(self, cid):
        await self.stop(cid)
        await self.checkpoint("kill")

    async def remove(self, cid):
        self.containers.pop(cid, None)
        await self.checkpoint("remove")


@pytest.fixture
async def supervisor(ownership, monkeypatch):
    db, dao, owner = ownership
    data = db.path.parent
    protected_home = data.parent / "protected-home"
    protected_home.mkdir()
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: protected_home))
    lease = acquire_service_lease(data)
    (data / ".process-installation.json").write_text(json.dumps(dao._test_identity.marker()))
    (data / ".process-installation.json").chmod(0o600)
    controller = Controller()
    manager = PodmanSupervisor(dao, controller=controller, identity=dao._test_identity, data_dir=data, lease=lease)
    async def terminate(proc):
        proc.controller.payload_finished.set()
        await proc.wait()
    monkeypatch.setattr("minimax_code.execution.terminate_tree", terminate)
    await manager.reconcile()
    try:
        yield manager, controller, dao, owner
    finally:
        try:
            await manager.close()
        except ProcessBoundaryError:
            pass
        lease.close()


async def test_wait_uses_payload_exit_and_persists_receipt(supervisor):
    manager, controller, dao, owner = supervisor
    handle = await manager.spawn(("/bin/sh", "-c", "exit 42"), target=TARGET, owner=owner)
    assert await handle.wait() == 42
    row = await dao.get(handle.execution_id)
    assert row["state"] == "removed" and row["resource_held"] == 0
    assert row["exit_code"] == 42
    assert controller.containers == {}
    assert [event["to_state"] for event in await dao.events(handle.execution_id)] == [
        "intent", "created", "starting", "running", "stopped", "removing", "removed"]


@pytest.mark.parametrize("entrypoint", [PYTHON_PATH, [PYTHON_PATH]])
async def test_exact_single_entrypoint_representations_preserve_full_verification(supervisor, monkeypatch, entrypoint):
    manager, controller, dao, owner = supervisor
    original = controller.create

    async def create(*args, **kwargs):
        await original(*args, **kwargs)
        controller.containers[CID]["Config"]["Entrypoint"] = entrypoint

    monkeypatch.setattr(controller, "create", create)
    handle = await manager.spawn(("true",), target=TARGET, owner=owner)
    assert await handle.wait() == 42
    assert (await dao.get(handle.execution_id))["resource_held"] == 0


async def test_recorded_podman_493_entrypoint_shape_is_accepted(supervisor, monkeypatch):
    record = json.loads((Path(__file__).parent / "fixtures" / "podman-4.9.3-entrypoint-shape.json").read_text(encoding="utf-8"))
    assert record["podman_version"] == "4.9.3"
    assert type(record["Entrypoint"]) is str and record["Entrypoint"] == record["Path"] == PYTHON_PATH
    assert record["Args"] == record["Cmd"] and len(record["Args"]) == 5
    assert record["Args"][:3] == ["-I", "-S", "-c"]
    assert hashlib.sha256(record["Args"][3].encode()).hexdigest() == record["guard_source_sha256"]
    # Replay the observed inspect representation against a newly generated
    # current guard/envelope; historical captured source is never upgraded.
    await test_exact_single_entrypoint_representations_preserve_full_verification(
        supervisor, monkeypatch, record["Entrypoint"])


@pytest.mark.parametrize("entrypoint", [PYTHON_PATH + " -I", '"' + PYTHON_PATH + '"',
    PYTHON_PATH + "\n", [PYTHON_PATH, "-I"], [], [True], [[PYTHON_PATH]],
    {"0": PYTHON_PATH}, 1, True, None])
async def test_nonexact_entrypoint_never_starts(supervisor, monkeypatch, entrypoint):
    manager, controller, dao, owner = supervisor
    original = controller.create

    async def create(*args, **kwargs):
        await original(*args, **kwargs)
        controller.containers[CID]["Config"]["Entrypoint"] = entrypoint

    monkeypatch.setattr(controller, "create", create)
    with pytest.raises(ProcessBoundaryError, match="entrypoint or payload configuration mismatch"):
        await manager.spawn(("true",), target=TARGET, owner=owner)
    assert "start" not in controller.calls
    assert not await dao.active()


@pytest.mark.parametrize("field", ["Path", "Args", "Cmd", "source", "envelope"])
async def test_string_entrypoint_does_not_relax_other_guard_checks(supervisor, monkeypatch, field):
    manager, controller, dao, owner = supervisor
    original = controller.create

    async def create(*args, **kwargs):
        await original(*args, **kwargs)
        data = controller.containers[CID]
        data["Config"]["Entrypoint"] = PYTHON_PATH
        if field == "Path":
            data["Path"] += " -I"
        elif field == "Cmd":
            data["Config"]["Cmd"] = [*data["Config"]["Cmd"], "extra"]
        elif field == "Args":
            data["Args"] = [*data["Args"][:-1], "{}"]
        elif field == "source":
            data["Args"][3] += "\n# changed source"
        else:
            value = json.loads(data["Args"][-1])
            value["argv"] = ["different-payload"]
            data["Args"][-1] = json.dumps(value)

    monkeypatch.setattr(controller, "create", create)
    with pytest.raises(ProcessBoundaryError, match="guard envelope|entrypoint or payload"):
        await manager.spawn(("true",), target=TARGET, owner=owner)
    assert "start" not in controller.calls
    assert not await dao.active()


async def test_attach_exits_before_container_start_never_reports_payload_zero(supervisor, monkeypatch):
    manager, controller, dao, owner = supervisor

    async def attach_without_start(cid, **kwargs):
        await controller.checkpoint("attach_without_start")
        return FakeProcess(controller)

    monkeypatch.setattr(controller, "start", attach_without_start)
    handle = await manager.spawn(("true",), target=TARGET, owner=owner)
    with pytest.raises(ProcessBoundaryError, match="exit code is unknown"):
        await handle.wait()
    row = await dao.get(handle.execution_id)
    assert row["state"] == "removed" and row["resource_held"] == 0
    assert row["exit_code"] is None and row["outcome"] == "unknown"
    assert controller.containers == {}


@pytest.mark.parametrize("timestamp", ["0001-01-01T00:00:00Z", "garbage", None])
async def test_missing_terminal_timestamp_retains_unknown_responsibility(supervisor, monkeypatch, timestamp):
    manager, controller, dao, owner = supervisor
    original = controller.inspect

    async def damaged_terminal(reference):
        data = await original(reference)
        if data and data["State"].get("Status") == "exited":
            data["State"]["FinishedAt"] = timestamp
        return data

    monkeypatch.setattr(controller, "inspect", damaged_terminal)
    handle = await manager.spawn(("true",), target=TARGET, owner=owner)
    with pytest.raises(ProcessBoundaryError, match="exit code is unconfirmed"):
        await handle.wait()
    row = await dao.get(handle.execution_id)
    assert row["state"] == "cleanup_pending" and row["resource_held"] == 1


@pytest.mark.parametrize("window", ["create", "start", "remove"])
async def test_effect_applied_response_lost_still_reconciles(supervisor, window):
    manager, controller, dao, owner = supervisor
    controller.fail_after.add(window)
    if window == "remove":
        handle = await manager.spawn(("true",), target=TARGET, owner=owner)
        assert await handle.wait() == 42
    else:
        with pytest.raises(ProcessBoundaryError, match="lost"):
            await manager.spawn(("true",), target=TARGET, owner=owner)
    assert controller.containers == {}
    assert await dao.active() == []


@pytest.mark.parametrize("window", ["image", "create", "start", "stop", "remove"])
async def test_cancel_double_cancel_retains_ownership_to_cleanup(supervisor, window):
    manager, controller, dao, owner = supervisor
    controller.block_at = window
    if window in {"stop", "remove"}:
        controller.payload_finished.clear()
        handle = await manager.spawn(("sleep", "10"), target=TARGET, owner=owner)
        task = asyncio.create_task(handle.terminate_owned_tree())
    else:
        task = asyncio.create_task(manager.spawn(("true",), target=TARGET, owner=owner))
    await asyncio.wait_for(controller.blocked.wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)
    assert not task.done()
    controller.release.set()
    result = await asyncio.gather(task, return_exceptions=True)
    if window not in {"stop", "remove"}:
        assert isinstance(result[0], asyncio.CancelledError)
    assert await dao.active() == [] and controller.containers == {}


async def test_foreign_owner_never_stopped_or_removed(supervisor):
    manager, controller, dao, owner = supervisor
    row = await intent(dao, owner)
    await controller.create(row, ("true",), cid_file=manager._cid_dir / "fake.cid")
    controller.containers[CID]["Config"]["Labels"][_LABEL_PREFIX + "install"] = "foreign"
    with pytest.raises(ProcessBoundaryError, match="ownership mismatch"):
        await manager.cleanup(row["exec_id"])
    assert "stop" not in controller.calls and "remove" not in controller.calls
    saved = await dao.get(row["exec_id"])
    assert saved["resource_held"] == 1 and saved["state"] == "cleanup_pending"


async def test_exists_control_failure_retains_cleanup_responsibility(supervisor):
    manager, controller, dao, owner = supervisor
    controller.fail_after.add("exists")
    handle = await manager.spawn(("true",), target=TARGET, owner=owner)
    with pytest.raises(ProcessBoundaryError, match="exists"):
        await handle.wait()
    saved = await dao.get(handle.execution_id)
    assert saved["state"] == "cleanup_pending" and saved["resource_held"] == 1
    with pytest.raises(ProcessBoundaryError, match="not ready"):
        await manager.spawn(("true",), target=TARGET, owner=owner)


async def test_restore_reconciles_registered_object_without_replaying(supervisor):
    manager, controller, dao, owner = supervisor
    row = await intent(dao, owner)
    await controller.create(row, ("true",), cid_file=manager._cid_dir / "fake.cid")
    await manager.reconcile()
    assert "start" not in controller.calls and controller.containers == {}
    assert (await dao.get(row["exec_id"]))["state"] == "removed"


@pytest.mark.parametrize("state", ["created", "starting", "running", "stopped", "removing", "removed"])
@pytest.mark.parametrize("effect_applied", [False, True])
async def test_persistence_failure_at_lifecycle_boundary(supervisor, monkeypatch, state, effect_applied):
    manager, controller, dao, owner = supervisor
    original = dao.transition
    failed = False

    async def transition(exec_id, revision, next_state, **kwargs):
        nonlocal failed
        if next_state == state and not failed:
            failed = True
            if effect_applied:
                await original(exec_id, revision, next_state, **kwargs)
            raise RuntimeError("persistence response lost")
        return await original(exec_id, revision, next_state, **kwargs)

    monkeypatch.setattr(dao, "transition", transition)
    with pytest.raises(RuntimeError, match="persistence response lost"):
        handle = await manager.spawn(("true",), target=TARGET, owner=owner)
        await handle.wait()
    rows = await dao.active()
    if state in {"created", "starting", "running"}:
        assert rows == [] and controller.containers == {}
        if state in {"created", "starting"}:
            assert "start" not in controller.calls
    elif state == "removed" and effect_applied:
        assert rows == [] and controller.containers == {}
    else:
        assert len(rows) == 1 and rows[0]["resource_held"] == 1
        assert rows[0]["state"] == "cleanup_pending"


async def test_cancellation_before_start_does_not_run_payload(supervisor):
    manager, controller, dao, owner = supervisor
    controller.block_at = "create"
    task = asyncio.create_task(manager.spawn(("touch", "side-effect"), target=TARGET, owner=owner))
    await asyncio.wait_for(controller.blocked.wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    controller.release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "start" not in controller.calls
    assert await dao.active() == []


async def test_marker_mismatch_never_controls_existing_object(supervisor):
    manager, controller, dao, owner = supervisor
    row = await intent(dao, owner)
    await controller.create(row, ("true",), cid_file=manager._cid_dir / "fake.cid")
    (manager.data_dir / ".process-installation.json").write_text("{}")
    with pytest.raises(ProcessBoundaryError, match="marker mismatch"):
        await manager.cleanup(row["exec_id"])
    assert "stop" not in controller.calls and "remove" not in controller.calls
    assert (await dao.get(row["exec_id"]))["resource_held"] == 1


async def test_orphan_same_installation_blocks_without_deletion(supervisor):
    manager, controller, dao, owner = supervisor
    row = await intent(dao, owner)
    await controller.create(row, ("true",), cid_file=manager._cid_dir / "fake.cid")
    controller.containers[CID]["Name"] = "unregistered"
    # Matching install but no matching CID/name is never globally cleaned.
    with pytest.raises(ProcessBoundaryError, match="Unregistered"):
        await manager.reconcile()
    assert "stop" not in controller.calls and "remove" not in controller.calls
    assert CID in controller.containers


async def test_reconcile_foreign_installation_does_not_touch_it(supervisor):
    manager, controller, _, _ = supervisor
    controller.containers[CID] = {"Id": CID, "Name": "foreign", "Config": {"Labels": {}}}
    await manager.reconcile()
    assert "stop" not in controller.calls and "remove" not in controller.calls
    assert CID in controller.containers


@pytest.mark.parametrize("boundary", range(1, 22))
@pytest.mark.parametrize("after_effect", [False, True])
async def test_double_cancel_each_external_await_boundary(supervisor, monkeypatch, boundary, after_effect):
    """Both sides of all controller/DAO awaits in one successful lifecycle.

    Real DAO transactions run inside the retained lifecycle task. Cancellation
    cannot interrupt a commit and then silently discard its ownership result.
    """
    manager, controller, dao, owner = supervisor
    count = 0
    blocked, release = asyncio.Event(), asyncio.Event()

    async def checkpoint():
        nonlocal count
        count += 1
        if count == boundary:
            blocked.set()
            await release.wait()

    def wrap(original):
        async def wrapped(*args, **kwargs):
            if not after_effect:
                await checkpoint()
            result = await original(*args, **kwargs)
            if after_effect:
                await checkpoint()
            return result
        return wrapped

    for obj, names in ((controller, ("info", "image", "create", "inspect", "exists", "start", "stop", "kill", "remove")),
                       (dao, ("assert_installation", "intent", "transition", "get"))):
        for name in names:
            monkeypatch.setattr(obj, name, wrap(getattr(obj, name)))

    async def caller():
        handle = None
        try:
            handle = await manager.spawn(("true",), target=TARGET, owner=owner)
            return await handle.wait()
        finally:
            if handle is not None:
                await handle.terminate_owned_tree()

    task = asyncio.create_task(caller())
    try:
        await asyncio.wait_for(blocked.wait(), 2)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
    finally:
        release.set()
    await asyncio.gather(task, return_exceptions=True)
    assert await dao.active() == []
    assert controller.containers == {}


async def test_exists_125_is_unknown_never_absence(monkeypatch):
    controller = object.__new__(_PodmanController)
    async def capture(argv):
        return 125, b"", b"runtime unavailable"
    monkeypatch.setattr(controller, "_capture", capture)
    with pytest.raises(ProcessBoundaryError, match="unknown"):
        await controller.exists(CID)
    with pytest.raises(ProcessBoundaryError, match="Invalid owned"):
        await controller.exists("--all")


async def test_container_environment_cannot_inherit_host_credentials(supervisor):
    manager, controller, dao, owner = supervisor
    with pytest.raises(ProcessBoundaryError, match="environment is server owned"):
        await manager.spawn(("true",), target=TARGET, owner=owner, env={"HOME": "/host", "API_KEY": "fake"})
    assert "create" not in controller.calls and await dao.active() == []


async def test_mutated_security_configuration_is_never_started(supervisor, monkeypatch):
    manager, controller, dao, owner = supervisor
    create = controller.create
    async def mutate(*args, **kwargs):
        await create(*args, **kwargs)
        controller.containers[CID]["HostConfig"]["NetworkMode"] = "host"
    monkeypatch.setattr(controller, "create", mutate)
    with pytest.raises(ProcessBoundaryError, match="security/resource"):
        await manager.spawn(("true",), target=TARGET, owner=owner)
    assert "start" not in controller.calls
    assert await dao.active() == [] and controller.containers == {}


async def test_connection_reopen_loses_durability_and_blocks_spawn(supervisor):
    manager, controller, dao, owner = supervisor
    await dao.db.close()
    await dao.db.connect()
    with pytest.raises(ProcessBoundaryError, match="FULL durability"):
        await manager.spawn(("true",), target=TARGET, owner=owner)
    assert "create" not in controller.calls
    await dao.enable_durable_ownership()


async def test_close_continues_draining_after_retained_spawn_cancelled(supervisor):
    manager, controller, dao, owner = supervisor
    other_path = Path(owner.workspace).parent / "other-copy"
    other_path.mkdir()
    physical = other_path.stat()
    other = replace(owner, run_id="other", family_id="other", family_root_run_id="other",
        workspace=str(other_path), workspace_device=physical.st_dev, workspace_inode=physical.st_ino)
    await save_profile(dao.db, other)
    await intent(dao, other, exec_id="other-intent")
    controller.block_at = "image"
    task = asyncio.create_task(manager.spawn(("true",), target=TARGET, owner=owner))
    await asyncio.wait_for(controller.blocked.wait(), 2)
    task.cancel()
    await asyncio.sleep(0)
    closing = asyncio.create_task(manager.close())
    await asyncio.sleep(0)
    controller.release.set()
    await closing
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await dao.active() == []


async def test_cid_directory_alias_is_rejected_before_any_runtime_control(supervisor, tmp_path):
    manager, controller, dao, owner = supervisor
    original = manager._cid_dir
    moved = original.with_name("original-cids")
    original.rename(moved)
    foreign = tmp_path / "foreign-cids"
    foreign.mkdir(mode=0o700)
    try:
        os.symlink(foreign, original, target_is_directory=True)
    except OSError:
        moved.rename(original)
        pytest.skip("Platform does not permit creating a directory symlink")
    try:
        before = list(controller.calls)
        with pytest.raises(ProcessBoundaryError, match="real private directory"):
            await manager.spawn(("true",), target=TARGET, owner=owner)
        assert controller.calls == before
        assert await dao.active() == []
    finally:
        original.unlink()
        moved.rename(original)

@pytest.mark.parametrize("init", [None, 0, 1, "false", "true", True, [], {}])
async def test_init_must_be_absent_or_exact_false(supervisor, monkeypatch, init):
    manager, controller, dao, owner = supervisor
    create = controller.create

    async def mutate(*args, **kwargs):
        await create(*args, **kwargs)
        controller.containers[CID]["HostConfig"]["Init"] = init

    monkeypatch.setattr(controller, "create", mutate)
    with pytest.raises(ProcessBoundaryError, match="security/resource"):
        await manager.spawn(("true",), target=TARGET, owner=owner)
    assert "start" not in controller.calls
    assert not await dao.active() and not controller.containers


async def test_actual_omitempty_init_and_runtime_evidence_are_persisted(supervisor, monkeypatch):
    manager, controller, dao, owner = supervisor
    create = controller.create

    async def mutate(*args, **kwargs):
        await create(*args, **kwargs)
        del controller.containers[CID]["HostConfig"]["Init"]

    monkeypatch.setattr(controller, "create", mutate)
    handle = await manager.spawn(("true",), target=TARGET, owner=owner)
    assert await handle.wait() == 42
    event = next(event for event in await dao.events(handle.execution_id) if event["to_state"] == "created")
    proof = json.loads(event["evidence"])
    assert proof["runtime"] == RUNTIME
    assert proof["mapping"]["uid_host_map"][1] == [IDENTITY.uid, IDENTITY.uid, 1]
    assert proof["mapping"]["gid_host_map"][1] == [1001, 1001, 1]
    assert proof["runtime_log"] == {"display": "4.981MB", "upper_bytes": 4985982,
        "algorithm": "human-size-4sig-si-upper-v1", "requested_bytes": 4980736,
        "profile_limit": TARGET.output_limit}


@pytest.mark.parametrize("field", ["Memory", "MemorySwap", "PidsLimit", "NanoCpus", "CpuQuota", "CpuPeriod", "Timeout"])
@pytest.mark.parametrize("kind", ["float", "bool", "null", "str"])
async def test_resource_quantities_are_strict_json_integers(supervisor, monkeypatch, field, kind):
    manager, controller, dao, owner = supervisor
    create = controller.create

    async def mutate(*args, **kwargs):
        await create(*args, **kwargs)
        section = controller.containers[CID]["Config" if field == "Timeout" else "HostConfig"]
        old = section[field]
        section[field] = {"float": float(old), "bool": True, "null": None, "str": str(old)}[kind]

    monkeypatch.setattr(controller, "create", mutate)
    with pytest.raises(ProcessBoundaryError, match="exact integers"):
        await manager.spawn(("true",), target=TARGET, owner=owner)
    assert "start" not in controller.calls
    assert not await dao.active() and not controller.containers


@pytest.mark.parametrize("annotation", ["true", True, None, "1", "unknown"])
async def test_init_annotation_cannot_enable_a_helper(supervisor, monkeypatch, annotation):
    manager, controller, dao, owner = supervisor
    create = controller.create

    async def mutate(*args, **kwargs):
        await create(*args, **kwargs)
        controller.containers[CID]["Config"]["Annotations"] = {"io.podman.annotations.init": annotation}

    monkeypatch.setattr(controller, "create", mutate)
    with pytest.raises(ProcessBoundaryError, match="init annotation"):
        await manager.spawn(("true",), target=TARGET, owner=owner)
    assert "start" not in controller.calls and not await dao.active()


async def test_platform_drift_revokes_spawn_before_create(supervisor, monkeypatch):
    manager, controller, dao, owner = supervisor
    original = controller.info

    async def changed():
        data = await original()
        data["runtime_evidence"]["oci_version"] += " changed"
        return data

    monkeypatch.setattr(controller, "info", changed)
    with pytest.raises(ProcessBoundaryError, match="platform or namespace"):
        await manager.spawn(("true",), target=TARGET, owner=owner)
    assert "create" not in controller.calls and not await dao.active()


async def test_preflight_map_fingerprint_is_required_before_start(supervisor):
    manager, controller, dao, owner = supervisor
    manager._mapping_fingerprint = "different"
    with pytest.raises(ProcessBoundaryError, match="differs from the private preflight"):
        await manager.spawn(("true",), target=TARGET, owner=owner)
    assert "start" not in controller.calls and not await dao.active()


async def test_controller_requests_log_headroom_without_expanding_profile(supervisor, monkeypatch):
    manager, controller, dao, owner = supervisor
    handle = await manager.spawn(("true",), target=TARGET, owner=owner)
    await handle.wait()
    row = await dao.get(handle.execution_id)
    controller = object.__new__(_PodmanController)
    controller._hooks_dir = Path("/private/hooks")
    controller.seccomp_path = Path("/private/seccomp-policy.json")
    monkeypatch.setattr(controller, "_assert_assets", lambda: TARGET.to_dict()["governor"])
    calls = []

    async def capture(argv):
        calls.append(argv)
        return 0, b"", b""

    monkeypatch.setattr(controller, "_capture", capture)
    await controller.create(row, ("true",), stdin=False, cid_file=Path("/private/cid"), uid=1000, gid=1001)
    assert "--log-opt=max-size=4980736" in calls[0]
    assert "--user=1000:1001" in calls[0]
    assert TARGET.output_limit == 5 * 1024**2
