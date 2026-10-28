"""One durable owner for every Podman container; no process fallback.

The controller is deliberately private and has no arbitrary command/bypass API.
Container absence, not the attach CLI's death, releases reserved resources.
"""
from __future__ import annotations

import asyncio
import json
import os
import platform
import re
import sys
import time
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from .process_runtime import (
    SCHEMA,
    container_mapping,
    log_bound,
    runtime_evidence,
    runtime_log_request,
)
from .process_types import (
    ExecutionOwner,
    ExecutionTargetSpec,
    InstallationIdentity,
    ProcessBoundaryError,
    canonical_hash,
)
from .storage.dao.process_executions import ProcessCapacity, ProcessExecutionsDAO

_CID = re.compile(r"[0-9a-f]{64}")
_LABEL_PREFIX = "ai.minimax-code."
_CONTAINER_ENV = {
    "PATH": "/usr/local/bin:/usr/bin:/bin",
    "LANG": "C.UTF-8",
    "PYTHONUNBUFFERED": "1",
    "PYTHONIOENCODING": "utf-8",
}


async def _finish_retained(task):
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            continue
    return task.result()


def _exact_cid(value):
    if not isinstance(value, str) or not _CID.fullmatch(value):
        raise ProcessBoundaryError("A full container identity is required")
    return value


def _terminal_timestamp(value):
    # Wall time proves an execution occurred; it never governs the deadline.
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.year > 1 and parsed.tzinfo is not None


def _single_entrypoint(value):
    # Podman 4.9.3 reports a single OCI entrypoint as a string; other
    # inspect implementations retain the exact one-element JSON array.
    # Neither representation is shell text or a command to split.
    if type(value) is str:
        return value
    if type(value) is list and len(value) == 1 and type(value[0]) is str:
        return value[0]
    return None


def _owned_reference(value):
    if not isinstance(value, str) or (
            not _CID.fullmatch(value)
            and not re.fullmatch(r"mm-[0-9a-f]{32}-[0-9a-f]{32}", value)):
        raise ProcessBoundaryError("Invalid owned container reference")
    return value


def _labels(row):
    return {_LABEL_PREFIX + "install": row["install_id"],
            _LABEL_PREFIX + "product": "minimax-code",
            _LABEL_PREFIX + "location": row["location_fingerprint"],
            _LABEL_PREFIX + "execution": row["exec_id"],
            _LABEL_PREFIX + "run": row["run_id"],
            _LABEL_PREFIX + "target": row["target_hash"]}


class _PodmanController:
    """Fixed local argv only. Nothing here is exposed as an agent tool."""

    def __init__(self, *, executable: str, hooks_dir: Path, control_cwd: Path):
        path = Path(executable)
        if not path.is_absolute() or path.name != "podman" or sys.platform != "linux":
            raise ProcessBoundaryError("Restricted execution requires a fixed local Linux Podman")
        if not path.is_file() or not os.access(path, os.X_OK):
            raise ProcessBoundaryError("Configured Podman executable is unavailable")
        self._executable = str(path.resolve(strict=True))
        self._hooks_dir = hooks_dir.resolve(strict=True)
        self._cwd = control_cwd.resolve(strict=True)
        if platform.machine().lower() != "x86_64":
            raise ProcessBoundaryError("L1 governor is verified only for Linux x86_64")
        from .process_assets import install_private_policy, verify_private_policy
        self.seccomp_path = install_private_policy(self._cwd, uid=os.getuid())
        self._policy_identity = verify_private_policy(self.seccomp_path, uid=os.getuid())
        if any(self._hooks_dir.iterdir()) or self._hooks_dir.stat().st_mode & 0o077:
            raise ProcessBoundaryError("Podman requires an empty private OCI hooks directory")
        # No CONTAINER_HOST, proxy, auth, provider key, or service data variable.
        self._env = {key: os.environ[key] for key in (
            "HOME", "XDG_RUNTIME_DIR", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "DBUS_SESSION_BUS_ADDRESS"
        ) if key in os.environ}
        self._env.update(PATH="/usr/local/bin:/usr/bin:/bin", LANG="C.UTF-8")

    def _assert_assets(self):
        from .process_assets import governor_identity, verify_private_policy
        from .process_installation import require_private_directory
        require_private_directory(self._cwd, uid=os.getuid())
        require_private_directory(self._hooks_dir, uid=os.getuid())
        if any(self._hooks_dir.iterdir()):
            raise ProcessBoundaryError("Fixed OCI hooks directory changed")
        if verify_private_policy(self.seccomp_path, uid=os.getuid()) != self._policy_identity:
            raise ProcessBoundaryError("Private seccomp policy physical identity changed")
        return governor_identity()

    async def _capture(self, argv):
        from .execution import _spawn_managed_exec, capture_output

        proc = await _spawn_managed_exec(self._executable, *argv,
            cwd=str(self._cwd), env=self._env,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        stdout, stderr = await capture_output(proc, timeout=15, limit=1024 * 1024)
        return proc.returncode, stdout, stderr

    async def info(self):
        code, stdout, _ = await self._capture(("info", "--format", "json"))
        if code != 0:
            raise ProcessBoundaryError("Podman runtime information is unavailable")
        data = json.loads(stdout)
        evidence = runtime_evidence(data, uid=os.getuid(), gid=os.getgid())
        store = data.get("store") or {}
        root = Path(store["graphRoot"]).resolve(strict=True)
        graph_stat = root.stat()
        run_root = Path(store["runRoot"]).resolve(strict=True)
        machine = Path("/etc/machine-id").read_text().strip()
        if not machine:
            raise ProcessBoundaryError("Stable local host identity is unavailable")
        host_fingerprint = canonical_hash(machine)
        return {"uid": os.getuid(), "host_fingerprint": host_fingerprint,
                "store_fingerprint": canonical_hash({"graph_root": str(root),
                    "device": str(graph_stat.st_dev), "inode": str(graph_stat.st_ino),
                    "driver": store["graphDriverName"], "uid": os.getuid(), "host": host_fingerprint}),
                "graph_root": str(root), "runtime_evidence": evidence,
                "protected_roots": (str(root), str(run_root)), "run_root": str(run_root)}

    async def image(self, image_id):
        code, stdout, _ = await self._capture(("image", "inspect", image_id))
        if code != 0:
            raise ProcessBoundaryError("Fixed local execution image is unavailable; pulling is disabled")
        rows = json.loads(stdout)
        if len(rows) != 1 or "sha256:" + rows[0]["Id"].removeprefix("sha256:") != image_id:
            raise ProcessBoundaryError("Local image does not match the frozen image identity")

    async def inventory(self):
        code, stdout, _ = await self._capture(("container", "list", "--all", "--no-trunc", "--format", "json"))
        if code != 0:
            raise ProcessBoundaryError("Podman ownership inventory is unavailable")
        rows = json.loads(stdout)
        if not isinstance(rows, list):
            raise ProcessBoundaryError("Invalid Podman ownership inventory")
        return [_exact_cid(row["Id"]) for row in rows]

    async def create(self, row, argv, *, stdin, cid_file, uid, gid):
        from .pid1_guard import build_guard_argv
        from .process_deadline import remaining_seconds
        target = ExecutionTargetSpec(**json.loads(row["target_json"]))
        if target.to_dict()["governor"] != self._assert_assets():
            raise ProcessBoundaryError("Queued governor assets changed")
        if remaining_seconds(row) <= 0:
            raise ProcessBoundaryError("Immutable deadline expired before create")
        guarded = build_guard_argv(exec_id=row["exec_id"], boot_id=row["deadline_boot_id"],
            deadline_boottime_ns=row["deadline_boottime_ns"], argv=argv)
        flags = ["--hooks-dir", str(self._hooks_dir), "create",
            "--name", row["container_name"], "--cidfile", str(cid_file),
            "--pull=never", "--network=none", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--security-opt=seccomp=" + str(self.seccomp_path),
            "--init=false", "--read-only", "--read-only-tmpfs=false",
            "--userns=keep-id", f"--user={uid}:{gid}", "--pid=private", "--ipc=none",
            "--uts=private", "--cgroupns=private", "--systemd=false", "--http-proxy=false",
            "--unsetenv-all", "--health-cmd=none", "--restart=no", "--image-volume=ignore",
            "--log-driver=k8s-file", f"--log-opt=max-size={runtime_log_request(target.output_limit)}",
            f"--memory={target.memory_bytes}", f"--memory-swap={target.memory_bytes}",
            f"--cpus={target.cpus_millis / 1000:.3f}", f"--pids-limit={target.pids_limit}",
            f"--timeout={target.timeout_seconds}", "--stop-timeout=1", "--tty=false",
            f"--interactive={'true' if stdin else 'false'}", "--workdir", row["container_cwd"],
            "--tmpfs", f"/tmp:rw,noexec,nosuid,nodev,size={target.tmpfs_bytes},mode=1777",
            "--mount", f"type=bind,source={row['workspace']},destination=/workspace,rw=true",
            "--entrypoint", json.dumps([guarded[0]])]
        for key, value in _labels(row).items():
            flags.extend(("--label", key + "=" + value))
        for key, value in _CONTAINER_ENV.items():
            flags.extend(("--env", key + "=" + value))
        code, _, _ = await self._capture((*flags, target.image_id, *guarded[1:]))
        if code != 0:
            raise ProcessBoundaryError("Podman create failed; exact-name ownership reconciliation required")

    async def inspect(self, reference):
        # Caller may use only its durable random name or a full CID.
        code, stdout, _ = await self._capture(("container", "inspect", _owned_reference(reference)))
        if code != 0:
            if not await self.exists(reference):
                return None
            raise ProcessBoundaryError("Container inspect failed")
        data = json.loads(stdout)
        if not isinstance(data, list) or len(data) != 1:
            raise ProcessBoundaryError("Container inspect did not return one exact object")
        return data[0]

    async def exists(self, reference):
        code, _, _ = await self._capture(("container", "exists", _owned_reference(reference)))
        if code not in (0, 1):
            raise ProcessBoundaryError("Container existence is unknown (Podman control failure)")
        return code == 0

    async def start(self, cid, *, stdin):
        from .execution import _spawn_managed_exec

        self._assert_assets()
        flags = ("start", "--attach", "--sig-proxy=false", "--detach-keys=",
                 *(('--interactive',) if stdin else ()), _exact_cid(cid))
        return await _spawn_managed_exec(self._executable, *flags, cwd=str(self._cwd), env=self._env,
            stdin=asyncio.subprocess.PIPE if stdin else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)

    async def stop(self, cid):
        code, _, _ = await self._capture(("stop", "--time=1", _exact_cid(cid)))
        if code != 0:
            raise ProcessBoundaryError("Owned container stop was not confirmed")

    async def kill(self, cid):
        code, _, _ = await self._capture(("kill", "--signal=KILL", _exact_cid(cid)))
        if code != 0:
            raise ProcessBoundaryError("Owned container kill was not confirmed")

    async def remove(self, cid):
        code, _, _ = await self._capture(("rm", _exact_cid(cid)))
        if code != 0:
            raise ProcessBoundaryError("Owned container removal was not confirmed")


class PodmanProcessHandle:
    def __init__(self, supervisor, row, proc):
        self.execution_id = row["exec_id"]
        self._supervisor = supervisor
        self._proc = proc
        self.stdin, self.stdout, self.stderr = proc.stdin, proc.stdout, proc.stderr
        self.controller_pid = proc.pid  # Diagnostics only; never a container identity.
        self._returncode = None
        self._watching = asyncio.create_task(supervisor._watch(self, row))
        # The task retains ownership even if a caller abandons its handle.
        # Observe failures to avoid an un-retrieved task exception; awaiting
        # this same task still raises, and durable held rows keep close unsafe.
        self._watching.add_done_callback(lambda task: None if task.cancelled() else task.exception())

    @property
    def returncode(self):
        return self._returncode

    async def wait(self):
        # A cancelled waiter does not cancel the unique lifecycle owner.
        return await asyncio.shield(self._watching)

    async def terminate_owned_tree(self):
        await self._supervisor.cleanup(self.execution_id, outcome="cancelled")
        return await _finish_retained(self._watching)


class PodmanSupervisor:
    def __init__(self, dao: ProcessExecutionsDAO, *, controller, identity: InstallationIdentity,
                 data_dir: Path, lease, capacity: ProcessCapacity | None = None,
                 protected_roots: Sequence[Path] = ()):
        self.dao, self._controller, self.identity = dao, controller, identity
        self.data_dir, self._lease = data_dir.resolve(strict=True), lease
        self.capacity = capacity or ProcessCapacity()
        self._protected_roots = tuple(Path(path).resolve() for path in protected_roots)
        self._handles = {}
        self._cleaning = {}
        self._spawning = set()
        self._closed = False
        self._ready = False
        self._error = None
        self._runtime_evidence = None
        self._mapping_fingerprint = None
        self._cid_dir = self.data_dir / "process-cids"
        self._cid_dir.mkdir(mode=0o700, exist_ok=True)
        from .process_installation import require_private_directory
        require_private_directory(self._cid_dir, uid=identity.uid)
        info = self._cid_dir.stat()
        self._cid_identity = (info.st_dev, info.st_ino)

    def _assert_cid_directory(self):
        from .process_installation import require_private_directory
        require_private_directory(self._cid_dir, uid=self.identity.uid)
        info = self._cid_dir.stat()
        if (info.st_dev, info.st_ino) != self._cid_identity:
            raise ProcessBoundaryError("Private CID directory physical identity changed")

    async def _assert_authority(self):
        from .process_installation import read_installation_marker, require_private_directory

        self._lease.assert_owned(self.data_dir, self.dao.db.path)
        self._assert_cid_directory()
        require_private_directory(self.data_dir, uid=self.identity.uid)
        physical = self.data_dir.stat()
        if (str(self.data_dir) != self.identity.data_dir
                or str(physical.st_dev) != self.identity.data_dir_device
                or str(physical.st_ino) != self.identity.data_dir_inode):
            raise ProcessBoundaryError("Execution installation data directory physical identity changed")
        await self.dao.assert_installation(self.identity)
        runtime = await self._controller.info()
        if any(runtime.get(key) != getattr(self.identity, key)
               for key in ("uid", "host_fingerprint", "store_fingerprint")):
            raise ProcessBoundaryError("Runtime identity changed; container ownership is unconfirmed")
        evidence = runtime.get("runtime_evidence")
        if type(evidence) is not dict or evidence.get("schema") != SCHEMA:
            raise ProcessBoundaryError("Verified Podman runtime schema evidence is missing")
        if self._runtime_evidence is not None and evidence != self._runtime_evidence:
            raise ProcessBoundaryError("Podman platform or namespace mapping changed")
        self._runtime_evidence = evidence
        try:
            marker = read_installation_marker(self.data_dir, uid=self.identity.uid)
        except (OSError, ValueError) as exc:
            raise ProcessBoundaryError("Private execution installation marker is unavailable") from exc
        if marker != self.identity.marker():
            raise ProcessBoundaryError("Private execution installation marker mismatch")

    async def reconcile(self):
        """Never replay a payload. Recover only registered, exactly matching objects."""
        self._ready = False
        await self._assert_authority()
        active = await self.dao.active()
        known = {row["container_id"] for row in active if row["container_id"]}
        known_names = {row["container_name"] for row in active}
        for cid in await self._controller.inventory():
            inspected = await self._controller.inspect(cid)
            labels = ((inspected or {}).get("Config") or {}).get("Labels") or {}
            if (labels.get(_LABEL_PREFIX + "install") == self.identity.install_id
                    and cid not in known and (inspected or {}).get("Name") not in known_names):
                self._error = "Unregistered container belongs to this installation; manual review required"
                raise ProcessBoundaryError(self._error)
        for row in active:
            if row["install_id"] != self.identity.install_id:
                self._error = "Registered process belongs to another installation"
                raise ProcessBoundaryError(self._error)
            await self.cleanup(row["exec_id"], outcome="interrupted")
        self._ready = True
        self._error = None

    async def spawn(self, argv: Sequence[str], *, target: ExecutionTargetSpec,
                    owner: ExecutionOwner, cwd=None, env: Mapping[str, str] | None = None,
                    stdin=False):
        check = getattr(self, "_admission_check", None)
        if check is not None:
            check()
        if self._closed or not self._ready or self._error:
            raise ProcessBoundaryError("Restricted process supervisor is not ready")
        if not argv or any(not isinstance(item, str) or "\0" in item for item in argv):
            raise ValueError("A valid process argv is required")
        if env:
            raise ProcessBoundaryError("L1 container environment is server owned")
        cancelled = asyncio.Event()
        task = asyncio.create_task(self._spawn(tuple(argv), target, owner, cwd, bool(stdin), cancelled))
        self._spawning.add(task)
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled.set()
            handle = None
            try:
                handle = await _finish_retained(task)
            finally:
                if handle is not None:
                    await _finish_retained(asyncio.create_task(handle.terminate_owned_tree()))
            raise
        finally:
            self._spawning.discard(task)

    async def _spawn_preflight(self, owner):
        from .process_preflight import _probe_owner, probe_argv
        if _probe_owner.get() != owner.run_id or not hasattr(self, "_preflight_target"):
            raise ProcessBoundaryError("Private fixed runtime preflight capability is missing")
        return await self.spawn(probe_argv(), target=self._preflight_target, owner=owner)

    def _check_start_allowed(self, cancelled):
        check = getattr(self, "_admission_check", None)
        if check is not None:
            check()
        if cancelled.is_set():
            raise asyncio.CancelledError()
        if self._closed:
            raise ProcessBoundaryError("Restricted process supervisor is shutting down")

    async def _spawn(self, argv, target, owner, cwd, stdin, cancelled):
        await self._assert_authority()
        root, mapped_cwd = owner.verify_workspace(cwd)
        self._validate_workspace(root)
        await self._controller.image(target.image_id)
        self._check_start_allowed(cancelled)
        exec_id = uuid.uuid4().hex
        row = await self.dao.intent(exec_id=exec_id,
            container_name=f"mm-{self.identity.install_id}-{exec_id}", identity=self.identity,
            owner=owner, target=target, argv_hash=canonical_hash(argv), container_cwd=mapped_cwd,
            deadline=time.time() + target.timeout_seconds, capacity=self.capacity)
        try:
            self._check_start_allowed(cancelled)
            self._assert_cid_directory()
            await self._controller.create(row, argv, stdin=stdin,
                cid_file=self._cid_dir / (exec_id + ".cid"), uid=self.identity.uid,
                gid=self._runtime_evidence["host_gid"])
            inspected = await self._controller.inspect(row["container_name"])
            cid = self._verify_container(row, inspected, full=True)
            cid_path = self._cid_dir / (exec_id + ".cid")
            self._assert_cid_directory()
            if cid_path.exists() and cid_path.read_text().strip() != cid:
                raise ProcessBoundaryError("CID file differs from the inspected container")
            row = await self.dao.transition(exec_id, row["revision"], "created",
                evidence={"container_id": cid, "configuration_verified": True,
                    "runtime": self._runtime_evidence,
                    "mapping": container_mapping(inspected["HostConfig"], self._runtime_evidence),
                    "runtime_log": log_bound(inspected["HostConfig"]["LogConfig"]["Size"], target.output_limit)},
                container_id=cid)
            self._check_start_allowed(cancelled)
            from .process_deadline import remaining_seconds
            if remaining_seconds(row) <= 0:
                raise TimeoutError("Container deadline expired before start")
            owner.verify_workspace(cwd)
            await self._assert_authority()
            self._check_start_allowed(cancelled)
            # Durable start intent precedes the only payload-starting operation.
            row = await self.dao.transition(exec_id, row["revision"], "starting",
                evidence={"container_id": cid, "deadline": row["deadline"]})
            self._check_start_allowed(cancelled)
            proc = await self._controller.start(cid, stdin=stdin)
            try:
                row = await self.dao.transition(exec_id, row["revision"], "running",
                    evidence={"attach_started": True})
            except BaseException:
                from .execution import terminate_tree
                await terminate_tree(proc)
                raise
            handle = PodmanProcessHandle(self, row, proc)
            self._handles[exec_id] = handle
            return handle
        except BaseException:
            # Even create/start response loss can mean the runtime already acted.
            # Resolve exact-name intent; never infer absence from CLI failure.
            await _finish_retained(asyncio.create_task(self.cleanup(exec_id, outcome="failed")))
            raise

    def _validate_workspace(self, root):
        from .process_workspace import validate_process_workspace
        validate_process_workspace(root, data_dir=self.data_dir, protected_roots=self._protected_roots)

    def _verify_container(self, row, inspected, *, full=False):
        if inspected is None:
            raise ProcessBoundaryError("Owned container is absent before ownership confirmation")
        cid = _exact_cid(inspected.get("Id"))
        config = inspected.get("Config") or {}
        actual_labels = config.get("Labels") or {}
        if (row["container_id"] not in (None, cid)
                or inspected.get("Name", "").removeprefix("/") != row["container_name"]
                or any(actual_labels.get(key) != value for key, value in _labels(row).items())):
            raise ProcessBoundaryError("Container ownership mismatch; no stop or remove is authorized")
        # Historical policy1/changed-asset rows remain eligible for exact owner
        # cleanup. Never construct a current start target on this read-only path.
        saved_target = json.loads(row["target_json"])
        if (not isinstance(saved_target, dict) or canonical_hash(saved_target) != row["target_hash"]
                or saved_target.get("mode") != "podman"
                or type(saved_target.get("policy_version")) is not int
                or saved_target["policy_version"] not in {1, 2}
                or not re.fullmatch(r"sha256:[0-9a-f]{64}", str(saved_target.get("image_id", "")))):
            raise ProcessBoundaryError("Historical container target identity is incompatible")
        if ("sha256:" + str(inspected.get("Image", "")).removeprefix("sha256:")) != saved_target["image_id"]:
            raise ProcessBoundaryError("Container image identity mismatch")
        if not full:
            return cid
        target = ExecutionTargetSpec(**saved_target)
        from .pid1_guard import build_guard_argv
        from .process_assets import governor_identity
        if target.to_dict()["governor"] != governor_identity():
            raise ProcessBoundaryError("Container governor assets are not the current approved policy")
        actual_argv = [inspected.get("Path"), *(inspected.get("Args") or [])]
        try:
            envelope = json.loads(actual_argv[-1])
            payload = envelope["argv"]
            expected_argv = list(build_guard_argv(exec_id=row["exec_id"], boot_id=row["deadline_boot_id"],
                deadline_boottime_ns=row["deadline_boottime_ns"], argv=payload))
        except (ValueError, KeyError, TypeError, IndexError) as exc:
            raise ProcessBoundaryError("Container guard envelope is incompatible") from exc
        if (actual_argv != expected_argv or canonical_hash(payload) != row["argv_hash"]
                or _single_entrypoint(config.get("Entrypoint")) != expected_argv[0]
                or config.get("Cmd") != expected_argv[1:]):
            raise ProcessBoundaryError("Container guard entrypoint or payload configuration mismatch")
        host = inspected.get("HostConfig") or {}
        if type(host) is not dict:
            raise ProcessBoundaryError("Container HostConfig schema is incompatible")
        mounts = inspected.get("Mounts") or []
        expected_env = [key + "=" + value for key, value in _CONTAINER_ENV.items()]
        env = config.get("Env") or []
        if (len(env) != len(expected_env) or set(env) != set(expected_env)
                or config.get("WorkingDir") != row["container_cwd"]
                or config.get("Tty") is not False
                or config.get("User") != f"{self.identity.uid}:{self._runtime_evidence['host_gid']}"):
            raise ProcessBoundaryError("Container payload/environment configuration mismatch")
        if (len(mounts) != 1 or mounts[0].get("Type") != "bind"
                or mounts[0].get("Source") != row["workspace"]
                or mounts[0].get("Destination") != "/workspace" or mounts[0].get("RW") is not True):
            raise ProcessBoundaryError("Container mounts differ from the one owned workspace")
        numeric = ("Memory", "MemorySwap", "PidsLimit", "NanoCpus", "CpuQuota", "CpuPeriod")
        if any(type(host.get(key)) is not int for key in numeric) or type(config.get("Timeout")) is not int:
            raise ProcessBoundaryError("Container resource quantities must be exact integers")
        nanos, quota, period = (host[key] for key in ("NanoCpus", "CpuQuota", "CpuPeriod"))
        if (nanos < 0 or quota <= 0 or period <= 0
                or quota * 1_000_000_000 != target.cpus_millis * 1_000_000 * period):
            raise ProcessBoundaryError("Container CPU resource configuration mismatch")
        cpus = nanos or quota * 1_000_000_000 // period
        annotations = config.get("Annotations", {})
        if type(annotations) is not dict or ("io.podman.annotations.init" in annotations
                and annotations["io.podman.annotations.init"] != "false"):
            raise ProcessBoundaryError("Container init annotation is incompatible")
        if (host.get("Privileged") is not False or host.get("ReadonlyRootfs") is not True
                or host.get("NetworkMode") != "none" or host.get("PidMode") != "private"
                or host.get("IpcMode") != "none" or host.get("UsernsMode") != "private"
                or host.get("UTSMode") != "private" or host.get("CgroupMode") != "private"
                or host.get("CapAdd") or host.get("Devices") or host.get("VolumesFrom")
                or ("Init" in host and host["Init"] is not False)
                or host.get("AutoRemove") is not False
                or host.get("Memory") != target.memory_bytes
                or host.get("MemorySwap") != target.memory_bytes
                or host.get("PidsLimit") != target.pids_limit
                or cpus != target.cpus_millis * 1_000_000
                or "no-new-privileges" not in (host.get("SecurityOpt") or [])
                or set(host.get("SecurityOpt") or []) != {"no-new-privileges", "seccomp=" + str(self._controller.seccomp_path)}
                or inspected.get("EffectiveCaps") or inspected.get("BoundingCaps")
                or (host.get("RestartPolicy") or {}).get("Name") not in {"no", ""}
                or not 0 < config.get("Timeout", 0) <= target.timeout_seconds):
            raise ProcessBoundaryError("Container security/resource configuration mismatch")
        tmpfs = host.get("Tmpfs") or {}
        tmp_options = set(str(tmpfs.get("/tmp", "")).split(","))
        if (set(tmpfs) != {"/tmp"} or f"size={target.tmpfs_bytes}" not in tmp_options
                or not {"noexec", "nosuid", "nodev"}.issubset(tmp_options)):
            raise ProcessBoundaryError("Container writable temporary storage is unconfirmed")
        logging = host.get("LogConfig") or {}
        if logging.get("Type") != "k8s-file":
            raise ProcessBoundaryError("Container bounded runtime logging is unconfirmed")
        log_bound(logging.get("Size"), target.output_limit)
        mapping = container_mapping(host, self._runtime_evidence)
        if self._mapping_fingerprint is not None and mapping["fingerprint"] != self._mapping_fingerprint:
            raise ProcessBoundaryError("Container namespace mapping differs from the private preflight")
        health = config.get("Healthcheck") or {}
        if health.get("Test") not in (None, [], ["NONE"]):
            raise ProcessBoundaryError("Container image health subprocesses are not disabled")
        return cid

    async def _watch(self, handle, process_row):
        from .process_deadline import remaining_seconds
        waiting = asyncio.create_task(handle._proc.wait())
        cleanup_started = False
        try:
            while True:
                remaining = remaining_seconds(process_row)
                if remaining <= 0:
                    outcome = "deadline_observed"
                    break
                # Recheck BOOTTIME after suspend, even if monotonic wait timers
                # did not include suspended time. Guard owns the hard teardown.
                if waiting.done():
                    await waiting
                    outcome = "exited"
                    break
                await asyncio.wait({waiting}, timeout=min(remaining, 0.25))
            cleanup_started = True
            row = await self.cleanup(handle.execution_id, outcome=outcome)
            if row["exit_code"] is None:
                raise ProcessBoundaryError("Container payload exit code is unknown")
            handle._returncode = row["exit_code"]
            return handle._returncode
        except BaseException:
            # A failed cleanup has already persisted unresolved responsibility.
            # Do not silently retry it here and erase that diagnostic boundary.
            if not cleanup_started:
                await _finish_retained(asyncio.create_task(
                    self.cleanup(handle.execution_id, outcome="unknown")))
            raise
        finally:
            self._handles.pop(handle.execution_id, None)
            if not waiting.done():
                waiting.cancel()
            await asyncio.gather(waiting, return_exceptions=True)

    async def cleanup(self, exec_id, *, outcome="cancelled"):
        task = self._cleaning.get(exec_id)
        if task is None:
            task = asyncio.create_task(self._cleanup(exec_id, outcome))
            self._cleaning[exec_id] = task
        # Same retained task is shared by all cancel/close/wait callers.
        try:
            return await _finish_retained(task)
        finally:
            if task.done() and self._cleaning.get(exec_id) is task:
                self._cleaning.pop(exec_id)

    async def _cleanup(self, exec_id, outcome):
        row = await self.dao.get(exec_id)
        if row is None:
            raise ProcessBoundaryError("Process ownership record is missing")
        if row["state"] == "removed":
            return row
        control_errors = []
        try:
            await self._assert_authority()
            reference = row["container_id"] or row["container_name"]
            inspected = await self._controller.inspect(reference)
            if inspected is None:
                # A previously running object disappearing has no trustworthy
                # payload result, but exact absence still resolves cleanup.
                return await self.dao.transition(exec_id, row["revision"], "removed",
                    evidence={"absent": True, "reference": reference}, outcome=outcome)
            cid = self._verify_container(row, inspected)
            state = inspected.get("State") or {}
            if state.get("Running") or state.get("Pid", 0):
                try:
                    await self._controller.stop(cid)
                except Exception as exc:
                    control_errors.append({"operation": "stop", "error_type": type(exc).__name__,
                                           "error": str(exc)[:500]})
                    # Re-prove the exact same ownership before escalating.
                    inspected = await self._controller.inspect(cid)
                    if self._verify_container(row, inspected) != cid:
                        raise ProcessBoundaryError("Container identity changed during stop") from exc
                    state = inspected.get("State") or {}
                    if state.get("Running") or state.get("Pid", 0):
                        try:
                            await self._controller.kill(cid)
                        except Exception as kill_exc:
                            # The kill may have taken effect before its response
                            # failed. The common inspect/terminal checks below
                            # must independently confirm the same owned object.
                            control_errors.append({"operation": "kill",
                                "error_type": type(kill_exc).__name__, "error": str(kill_exc)[:500]})
                    elif state.get("Running") is not False or state.get("Pid") != 0:
                        raise ProcessBoundaryError("Container tree stop is unconfirmed") from exc
                inspected = await self._controller.inspect(cid)
                if self._verify_container(row, inspected) != cid:
                    raise ProcessBoundaryError("Container identity changed during stop")
                state = inspected.get("State") or {}
            if state.get("Running") is not False or state.get("Pid") != 0:
                raise ProcessBoundaryError("Container tree stop is unconfirmed")
            exit_code = state.get("ExitCode")
            started = state.get("StartedAt")
            finished = state.get("FinishedAt")
            never_started = (state.get("Status") == "created" and isinstance(started, str)
                and started.startswith("0001-01-01T00:00:00"))
            if never_started:
                # Podman reports zero for a created container whose payload has
                # never run. That is cleanup evidence, never payload success.
                exit_code, outcome = None, "unknown"
            elif (type(exit_code) is not int or state.get("Status") != "exited"
                    or not _terminal_timestamp(started) or not _terminal_timestamp(finished)):
                raise ProcessBoundaryError("Payload exit code is unconfirmed")
            if state.get("OOMKilled"):
                outcome = "oom_killed"
            elif exit_code in {124, 125}:
                # Guard and payload deliberately share these raw values. No
                # authenticated status channel exists in v1; preserve ambiguity.
                outcome = "unknown"
            if row["state"] not in {"stopped", "removing"}:
                row = await self.dao.transition(exec_id, row["revision"], "stopped",
                    evidence={"container_id": cid, "running": False, "pid": 0,
                              "container_exit_code": exit_code, "exit_code_source": "podman_inspect_guard",
                              "payload_exit_ambiguous": exit_code in {124, 125},
                              "container_status": state.get("Status"),
                              "started_at": started, "finished_at": finished,
                              "oom_killed": bool(state.get("OOMKilled")),
                              **({"control_errors": control_errors} if control_errors else {})},
                    container_id=cid, exit_code=exit_code,
                    oom_killed=int(bool(state.get("OOMKilled"))), outcome=outcome)
            if row["state"] == "stopped":
                row = await self.dao.transition(exec_id, row["revision"], "removing",
                    evidence={"container_id": cid, "stop_verified": True})
            try:
                await self._controller.remove(cid)
            except Exception:
                # rm may have succeeded before the control response was lost.
                if await self._controller.exists(cid):
                    raise
            if await self._controller.exists(cid):
                raise ProcessBoundaryError("Container remains after removal")
            row = await self.dao.transition(exec_id, row["revision"], "removed",
                evidence={"absent": True, "container_id": cid,
                          **({"control_errors": control_errors} if control_errors else {})})
            cid_path = self._cid_dir / (exec_id + ".cid")
            self._assert_cid_directory()
            cid_path.unlink(missing_ok=True)
            return row
        except BaseException as exc:
            self._ready = False
            self._error = "Restricted process cleanup is unconfirmed"
            # Preserve resources on all unknown persistence/control outcomes.
            # If persistence itself is down the prior durable row remains held.
            try:
                latest = await self.dao.get(exec_id)
                if latest and latest["state"] != "removed":
                    await self.dao.transition(exec_id, latest["revision"], "cleanup_pending",
                        evidence={"error_type": type(exc).__name__,
                                  **({"control_errors": control_errors} if control_errors else {})},
                        error=str(exc)[:500], outcome=outcome)
            except BaseException:
                pass
            raise
        finally:
            handle = self._handles.get(exec_id)
            if handle is not None:
                from .execution import terminate_tree
                await terminate_tree(handle._proc)

    async def close(self):
        self._closed = True
        for task in tuple(self._spawning):
            try:
                await _finish_retained(task)
            except asyncio.CancelledError:
                # A retained spawn may safely abort before payload start after
                # its caller cancelled. It must not skip draining other rows.
                pass
            except Exception:
                pass
        errors = []
        for row in await self.dao.active():
            try:
                await self.cleanup(row["exec_id"], outcome="cancelled")
            except Exception as exc:
                errors.append(exc)
        for handle in tuple(self._handles.values()):
            try:
                await _finish_retained(handle._watching)
            except Exception as exc:
                errors.append(exc)
        if errors:
            raise ProcessBoundaryError("Restricted process shutdown retained unresolved ownership") from errors[0]
