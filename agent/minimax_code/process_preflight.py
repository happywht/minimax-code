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
print(json.dumps({"python":sys.executable,"version":list(sys.version_info[:3]),
"pid":os.getpid(),"ppid":os.getppid(),
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

    def assert_current(self, target, installation_id):
        from .process_deadline import read_clock
        boot_id, _ = read_clock()
        if (self.target_hash != canonical_hash(target) or self.installation_id != installation_id
                or self.boot_id != boot_id or self.probe_hash != probe_argv_hash()
                or self.time_namespace != time_namespace()):
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
        if handle.returncode != 0 or output.stdout_truncated or output.stderr_truncated or output.stderr:
            raise ProcessBoundaryError("Private runtime preflight did not produce a complete successful attestation")
        try:
            attested = json.loads(output.stdout.decode("utf-8", errors="strict"))
            from .pid1_guard import PYTHON_PATH
            if (not isinstance(attested, dict) or set(attested) != {"python", "version", "pid", "ppid",
                    "boot_id", "boottime_ns", "time_namespace"}
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
        proof = {"probe_kind": PROBE_KIND, "probe_argv_hash": probe_argv_hash(),
            "execution_id": handle.execution_id, "execution_target": target,
            "execution_target_hash": canonical_hash(target), "installation_id": catalog.installation_id,
            "host_boot_id": boot_before, "host_before_boottime_ns": before,
            "host_after_boottime_ns": after, "host_time_namespace": namespace,
            "attestation": attested, "raw_stdout": output.stdout.decode("utf-8"),
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
                error=type(error).__name__, result={"preflight": "failed", "model_calls": 0})
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
        result["installation_id"], result["host_boot_id"], result["host_time_namespace"], result["probe_argv_hash"])
