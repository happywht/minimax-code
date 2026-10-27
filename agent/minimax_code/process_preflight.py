"""One private, fixed zero-model RunService probe owns its real container."""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path

from .process_types import ExecutionOwner, ExecutionTargetSpec, ProcessBoundaryError, canonical_hash

PROBE_KIND = "pid1-clock-v1"
PROBE_SOURCE = '''import json,os,sys,time
from pathlib import Path
probe=Path("/workspace/.minimax-l1-preflight")
fd=os.open(probe,os.O_CREAT|os.O_EXCL|os.O_RDWR|os.O_NOFOLLOW,0o600)
try:
 data=b"minimax-l1-workspace-readback-v1"
 if os.write(fd,data)!=len(data): raise RuntimeError("Incomplete workspace write")
 os.lseek(fd,0,os.SEEK_SET)
 if os.read(fd,len(data)+1)!=data: raise RuntimeError("Workspace readback differs")
 written=os.fstat(fd)
finally:
 os.close(fd)
probe.unlink()
directory=Path("/workspace").stat()
print(json.dumps({"python":sys.executable,"version":list(sys.version_info[:3]),
"pid":os.getpid(),"ppid":os.getppid(),
"uid":os.getuid(),"gid":os.getgid(),
"uid_map":Path("/proc/self/uid_map").read_text(encoding="ascii"),
"gid_map":Path("/proc/self/gid_map").read_text(encoding="ascii"),
"workspace":{"readback":"minimax-l1-workspace-readback-v1","uid":written.st_uid,
"gid":written.st_gid,"directory_uid":directory.st_uid,"directory_gid":directory.st_gid},
"boot_id":Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip(),
"boottime_ns":time.clock_gettime_ns(time.CLOCK_BOOTTIME),
"time_namespace":os.readlink("/proc/self/ns/time")},sort_keys=True,separators=(",",":")))
'''
_probe_owner: ContextVar[str | None] = ContextVar("private_preflight_owner", default=None)
# A release acceptance decision pins exact accepted assets here. A runtime
# attestation is additionally mandatory; no bool/config/RPC can grant readiness.
ACCEPTED_GOVERNORS: frozenset[tuple[str, str]] = frozenset()


def probe_argv():
    from .pid1_guard import PYTHON_PATH
    return PYTHON_PATH, "-I", "-S", "-c", PROBE_SOURCE


def probe_argv_hash():
    return canonical_hash(probe_argv())


def time_namespace():
    return os.readlink("/proc/self/ns/time")


def assert_private_probe(run_id, snapshot, argv_hash):
    if (_probe_owner.get() != run_id or snapshot.get("kind") != "runtime_preflight"
            or snapshot.get("probe_kind") != PROBE_KIND
            or snapshot.get("probe_argv_hash") != probe_argv_hash()
            or argv_hash != probe_argv_hash()):
        raise ProcessBoundaryError("Only the private fixed RunService preflight may start this owner")


@dataclass(frozen=True)
class RuntimeAttestation:
    run_id: str
    execution_id: str
    target_hash: str
    installation_id: str
    boot_id: str
    time_namespace: str
    probe_hash: str
    runtime_fingerprint: str
    mapping_fingerprint: str

    def assert_current(self, target, installation_id, runtime, mapping_fingerprint):
        from .process_deadline import read_clock
        boot_id, _ = read_clock()
        if (self.target_hash != canonical_hash(target) or self.installation_id != installation_id
                or self.boot_id != boot_id or self.probe_hash != probe_argv_hash()
                or self.time_namespace != time_namespace()
                or self.runtime_fingerprint != canonical_hash(runtime)
                or self.mapping_fingerprint != mapping_fingerprint):
            raise ProcessBoundaryError("Runtime attestation differs from current boot, assets or installation")


def _physical(path):
    info = path.lstat()
    if path.is_symlink() or not path.is_dir() or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ProcessBoundaryError("Preflight workspace ownership changed")
    return info.st_dev, info.st_ino


def _cleanup_workspaces(paths):
    for path, identity in paths.items():
        if path.exists():
            if _physical(path) != identity:
                raise ProcessBoundaryError("Preflight directory physical identity changed")
            path.rmdir()  # Empty, exact, owned directories only; never recursive.


def _prepare_workspaces(catalog, manager):
    from .process_workspace import validate_process_workspace
    paths = {}
    try:
        for prefix in ("minimax-l1-source-", "minimax-l1-probe-"):
            path = Path(tempfile.mkdtemp(prefix=prefix))
            paths[path] = _physical(path)
            validate_process_workspace(path, data_dir=catalog.data_dir,
                protected_roots=manager._protected_roots)
        return paths
    except BaseException:
        _cleanup_workspaces(paths)
        raise


async def run_preflight(catalog, server):
    """No caller argv/target/paths. This function is never registered as RPC."""
    from .budget_ledger import BudgetHandle
    from .execution import communicate_bounded
    from .process_deadline import read_clock
    from .runs import get_run_service
    from .storage.dao.runs import AgentRunsDAO
    from .storage.dao.sessions import SessionsDAO

    catalog.assert_extension_boundary()
    manager = catalog.supervisor
    target = catalog._targets["linux-podman-l1"]
    if manager is None or not manager._ready or manager._error:
        raise ProcessBoundaryError("Preflight requires reconciled process ownership")
    spec = ExecutionTargetSpec(**target)
    paths = _prepare_workspaces(catalog, manager)
    source, owned = paths
    session_id = "ses_preflight_" + uuid.uuid4().hex
    dao = AgentRunsDAO(catalog._db)
    try:
        await SessionsDAO(catalog._db).create(id=session_id, title="Linux execution runtime preflight",
            workspace_path=str(owned), archived=True)
    except BaseException:
        # No run admission was attempted, so no process can own either tree.
        _cleanup_workspaces(paths)
        raise
    source_identity, owned_identity = paths[source], paths[owned]
    copy_binding = {"kind": "directory", "workspace_id": "probe_" + uuid.uuid4().hex,
        "source_workspace": str(source), "workspace": str(owned),
        "source_identity": list(source_identity), "directory_identity": list(owned_identity)}
    snapshot = {"version": 2, "kind": "runtime_preflight", "parent_run_id": None,
        "probe_kind": PROBE_KIND, "probe_argv_hash": probe_argv_hash(),
        "execution_target": target, "execution_target_hash": canonical_hash(target),
        "execution_installation_id": catalog.installation_id,
        "prepared": True, "copy_binding": copy_binding,
        **{key: value for key, value in copy_binding.items() if key != "kind"}}

    async def operation(job):
        run = await dao.update_run_status(job.run_id, status="running")
        await BudgetHandle.for_run(catalog._db, job.run_id)
        owner = ExecutionOwner(job.run_id, run["metadata"]["budget_family_id"], job.run_id,
            str(owned), *owned_identity)
        boot_before, before = read_clock()
        namespace = time_namespace()
        token = _probe_owner.set(job.run_id)
        try:
            handle = await manager._spawn_preflight(owner)
            output = await communicate_bounded(handle, timeout=spec.timeout_seconds + 30, limit=8192)
        finally:
            _probe_owner.reset(token)
        boot_after, after = read_clock()
        # Preserve bounded raw evidence even when a new platform fails the
        # checks below. A failed proof never grants runtime readiness.
        job.result = {"execution_id": handle.execution_id, "raw_stdout": output.stdout.decode("utf-8", errors="replace"),
            "raw_stderr": output.stderr.decode("utf-8", errors="replace"),
            "raw_stdout_hex": output.stdout.hex(), "raw_stderr_hex": output.stderr.hex(),
            "stdout_truncated": output.stdout_truncated, "stderr_truncated": output.stderr_truncated,
            "returncode": handle.returncode, "runtime": manager._runtime_evidence,
            "host_boot_id": boot_before, "host_after_boot_id": boot_after,
            "host_before_boottime_ns": before, "host_after_boottime_ns": after,
            "host_time_namespace": namespace}
        if handle.returncode != 0 or output.stdout_truncated or output.stderr_truncated or output.stderr:
            raise ProcessBoundaryError("Private runtime preflight did not produce a complete successful attestation")
        try:
            attested = json.loads(output.stdout.decode("utf-8", errors="strict"))
            from .pid1_guard import PYTHON_PATH
            if (not isinstance(attested, dict) or set(attested) != {"python", "version", "pid", "ppid",
                    "boot_id", "boottime_ns", "time_namespace", "uid", "gid", "uid_map", "gid_map", "workspace"}
                    or attested["python"] != PYTHON_PATH
                    or not isinstance(attested["version"], list) or len(attested["version"]) != 3
                    or any(type(item) is not int for item in attested["version"])
                    or attested["version"][:2] != [3, 12]
                    or type(attested["pid"]) is not int or attested["pid"] <= 1
                    or type(attested["ppid"]) is not int or attested["ppid"] != 1
                    or boot_before != boot_after or attested["boot_id"] != boot_before
                    or type(attested["boottime_ns"]) is not int or not before <= attested["boottime_ns"] <= after
                    or attested["time_namespace"] != namespace):
                raise ValueError("Attestation identity/time axis differs")
        except (UnicodeError, ValueError, TypeError) as exc:
            raise ProcessBoundaryError("Private runtime attestation is incompatible") from exc
        process = await manager.dao.get(handle.execution_id)
        if process["state"] != "removed" or process["resource_held"]:
            raise ProcessBoundaryError("Preflight container responsibility is not released")
        events = await manager.dao.events(handle.execution_id)
        created = [json.loads(event["evidence"]) for event in events if event["to_state"] == "created"]
        if len(created) != 1 or created[0].get("runtime") != manager._runtime_evidence:
            raise ProcessBoundaryError("Preflight persisted runtime evidence is incompatible")
        mapping = created[0]["mapping"]
        job.result["mapping"] = mapping
        _verify_mapping_attestation(attested, mapping, manager._runtime_evidence)
        manager._mapping_fingerprint = mapping["fingerprint"]
        proof = {"probe_kind": PROBE_KIND, "probe_argv_hash": probe_argv_hash(),
            "execution_id": handle.execution_id, "execution_target": target,
            "execution_target_hash": canonical_hash(target), "installation_id": catalog.installation_id,
            "host_boot_id": boot_before, "host_before_boottime_ns": before,
            "host_after_boottime_ns": after, "host_time_namespace": namespace,
            "attestation": attested, "raw_stdout": output.stdout.decode("utf-8"),
            "runtime": manager._runtime_evidence, "mapping": mapping,
            "runtime_fingerprint": canonical_hash(manager._runtime_evidence),
            "mapping_fingerprint": mapping["fingerprint"],
            "container_absent": True, "model_calls": 0}
        job.result = proof

    async def finalize(job, error):
        if any(row["run_id"] == job.run_id for row in await manager.dao.active()):
            raise ProcessBoundaryError("Preflight container responsibility remains; preserve its workspace")
        # Never recursively delete a possibly changed/payload-populated tree.
        _cleanup_workspaces(paths)
        if error is not None:
            await dao.terminate_run(job.run_id,
                status="cancelled" if isinstance(error, asyncio.CancelledError) else "failed",
                error=type(error).__name__, result={"preflight": "failed", "model_calls": 0,
                    "validation_error": type(error).__name__, "evidence": job.result})
        else:
            await dao.update_run_status(job.run_id, status="completed", result=job.result)

    service = get_run_service(server)
    admission = asyncio.create_task(service.admit(dao=dao, session_id=session_id,
        request={"mode": "execute", "content": "Private fixed Linux runtime clock attestation"},
        title="Linux L1 runtime preflight", request_key=None,
        metadata={"origin": "runtime_preflight", "process_execution_owner": snapshot,
            "budget": {"limit_tokens": None}}, workspace=str(owned), project_id=None,
        operation=operation, finalize=finalize))
    cancelled = False
    while not admission.done():
        try:
            await asyncio.shield(admission)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    try:
        _, job, _ = admission.result()
    except BaseException:
        # A lost admission response may hide a committed run. Only a successful
        # durable absence check permits cleanup; database uncertainty retains it.
        if not await dao.list_runs(session_id=session_id):
            _cleanup_workspaces(paths)
        raise
    if cancelled:
        from .budget_ledger import owned_cleanup
        service.cancel_run(job.run_id)
        try:
            await owned_cleanup(service.wait(job))
        finally:
            raise asyncio.CancelledError
    try:
        await service.wait(job)
    except asyncio.CancelledError:
        from .podman_execution import _finish_retained
        service.cancel_run(job.run_id)
        try:
            await _finish_retained(job.task)
        except asyncio.CancelledError:
            pass
        raise
    run = await dao.get_run(job.run_id)
    result = await dao.result_payload(job.run_id)
    if run["status"] != "completed" or not (result or {}).get("container_absent"):
        raise ProcessBoundaryError("Preflight durable completion is unconfirmed")
    return RuntimeAttestation(job.run_id, result["execution_id"], result["execution_target_hash"],
        result["installation_id"], result["host_boot_id"], result["host_time_namespace"], result["probe_argv_hash"],
        result["runtime_fingerprint"], result["mapping_fingerprint"])


def _verify_mapping_attestation(attested, mapping, runtime):
    from .process_runtime import compose_maps, map_id, proc_map

    for label in ("uid", "gid"):
        actual = attested[label]
        if type(actual) is not int or actual != runtime["host_" + label]:
            raise ProcessBoundaryError("Private probe UID/GID differs from the service owner")
        # Reading /proc/self/*_map from its own namespace reports IDs in
        # the parent namespace, not initial-host IDs. Compare the inner map
        # first, then independently compose the observed outer rootless map.
        inner = proc_map(attested[label + "_map"])
        composed = compose_maps(inner, runtime[label + "_map"])
        if (inner != mapping[label + "_map"] or composed != mapping[label + "_host_map"]
                or map_id(composed, actual) != actual or map_id(composed, 0) == 0):
            raise ProcessBoundaryError("Private probe kernel mapping differs from inspect/info")
    workspace = attested["workspace"]
    if (type(workspace) is not dict or set(workspace) != {
            "readback", "uid", "gid", "directory_uid", "directory_gid"}
            or workspace["readback"] != "minimax-l1-workspace-readback-v1"
            or any(type(workspace[key]) is not int or workspace[key] != runtime["host_" + label]
                for label in ("uid", "gid") for key in (label, "directory_" + label))):
        raise ProcessBoundaryError("Private probe owned workspace readback is incompatible")
    if mapping.get("fingerprint") != canonical_hash({key: value for key, value in mapping.items()
                                                    if key != "fingerprint"}):
        raise ProcessBoundaryError("Private probe namespace fingerprint differs")
