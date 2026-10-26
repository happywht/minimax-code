"""Startup-frozen server profiles; users select IDs, never runtime parameters."""
from __future__ import annotations

import asyncio
import copy
import json
import os
import stat
import sys
from pathlib import Path, PurePosixPath

from .process_binding import HOST_TARGET, validate_target
from .process_types import ExecutionTargetSpec, ProcessBoundaryError, canonical_hash

PROFILE_FILE = "execution-profiles.json"
PODMAN_PROFILE = "linux-podman-l1"
_RESOURCE_FIELDS = {"memory_bytes", "cpus_millis", "pids_limit", "timeout_seconds",
                    "tmpfs_bytes", "output_limit"}


class ExecutionProfileCatalog:
    def __init__(self, *, data_dir: Path, lease=None, db=None):
        self.data_dir = Path(data_dir).resolve()
        self._lease, self._db = lease, db
        self._targets = {"native-host": dict(HOST_TARGET)}
        self._labels = {"native-host": "Native 宿主", PODMAN_PROFILE: "Linux 容器 L1（需人工监督）"}
        self._podman_reason = "未配置固定本地镜像"
        self._executable = "/usr/bin/podman"
        self._additional_roots = ()
        self._manager = None
        # This is an internal service capability, never read from configuration
        # or RPC. Until a governor is implemented and accepted it remains None.
        self._governor = None
        self._preflight_task = None
        self._initialized = False
        self._extension_boundary_reason = None

    @property
    def supervisor(self):
        return self._manager

    @property
    def installation_id(self):
        return self._manager.identity.install_id if self._manager is not None else None

    def _load(self):
        path = self.data_dir / PROFILE_FILE
        if not path.exists() and not path.is_symlink():
            return
        info = path.lstat()
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 65536
                or (sys.platform == "linux" and info.st_uid != os.getuid())):
            raise ProcessBoundaryError("执行配置必须是服务所有的普通文件")
        config = json.loads(path.read_text(encoding="utf-8"))
        if (not isinstance(config, dict) or type(config.get("version")) is not int
                or config["version"] != 1
                or set(config) - {"version", "profiles", "podman_path", "protected_roots"}
                or not isinstance(config.get("profiles"), list)):
            raise ProcessBoundaryError("执行配置格式不兼容")
        executable = config.get("podman_path", "/usr/bin/podman")
        if (not isinstance(executable, str) or not PurePosixPath(executable).is_absolute()
                or PurePosixPath(executable).name != "podman"):
            raise ProcessBoundaryError("Podman 必须绑定固定本地绝对路径")
        protected = config.get("protected_roots", [])
        if (not isinstance(protected, list)
                or any(not isinstance(item, str) or not Path(item).is_absolute()
                       or "\0" in item for item in protected)):
            raise ProcessBoundaryError("附加保护目录必须是绝对路径")
        self._executable = executable
        self._additional_roots = tuple(Path(item).resolve() for item in protected)
        seen = set()
        for profile in config["profiles"]:
            if not isinstance(profile, dict) or profile.get("id") not in {"native-host", PODMAN_PROFILE}:
                raise ProcessBoundaryError("只支持已审定的 Native host 和 Linux L1 配置")
            profile_id = profile["id"]
            if profile_id in seen or type(profile.get("enabled", True)) is not bool:
                raise ProcessBoundaryError("执行配置重复或启用标记无效")
            seen.add(profile_id)
            label = profile.get("label", self._labels[profile_id])
            if not isinstance(label, str) or not label.strip() or len(label) > 128:
                raise ProcessBoundaryError("执行配置显示名称无效")
            self._labels[profile_id] = label
            if profile_id == "native-host":
                if set(profile) - {"id", "enabled", "label"} or profile.get("enabled", True) is not True:
                    raise ProcessBoundaryError("默认 Native host 配置不接受执行参数或禁用")
                continue
            if set(profile) - {"id", "enabled", "label", "image_id", *_RESOURCE_FIELDS}:
                raise ProcessBoundaryError("Linux L1 配置包含未经支持的执行参数")
            if not profile.get("enabled", True):
                self._podman_reason = "执行配置已停用"
                continue
            spec = ExecutionTargetSpec(profile_id=PODMAN_PROFILE, image_id=profile.get("image_id"),
                **{key: profile[key] for key in _RESOURCE_FIELDS if key in profile})
            self._targets[PODMAN_PROFILE] = spec.to_dict()
            self._podman_reason = "执行运行时尚未初始化"

    async def initialize(self):
        """Call once before admissions/background jobs; always preserve host mode."""
        if self._initialized:
            return self
        self._initialized = True
        try:
            self._load()
            if PODMAN_PROFILE not in self._targets:
                return self
            if sys.platform != "linux":
                raise ProcessBoundaryError("Linux L1 仅支持本地 rootless Linux Podman")
            from .podman_execution import PodmanSupervisor, _PodmanController
            from .process_installation import bootstrap_installation, require_private_directory
            from .storage.dao.process_executions import ProcessExecutionsDAO

            if self._lease is None or self._db is None:
                raise ProcessBoundaryError("受限执行要求持有服务数据目录租约")
            self._lease.assert_owned(self.data_dir, self._db.path)
            require_private_directory(self.data_dir, uid=os.getuid())
            control = self.data_dir / "execution"
            control.mkdir(mode=0o700, exist_ok=True)
            require_private_directory(control, uid=os.getuid())
            hooks = control / "empty-hooks"
            hooks.mkdir(mode=0o700, exist_ok=True)
            require_private_directory(hooks, uid=os.getuid())
            controller = _PodmanController(executable=self._executable, hooks_dir=hooks, control_cwd=control)
            runtime = await controller.info()
            dao = ProcessExecutionsDAO(self._db)
            identity = await bootstrap_installation(dao, controller=controller,
                data_dir=self.data_dir, lease=self._lease)
            manager = PodmanSupervisor(dao, controller=controller, identity=identity,
                data_dir=self.data_dir, lease=self._lease,
                protected_roots=(*self._additional_roots, *runtime.get("protected_roots", ())))
            self._manager = manager
            manager._admission_check = self.assert_extension_boundary
            manager._preflight_target = ExecutionTargetSpec(**self._targets[PODMAN_PROFILE])
            await controller.image(self._targets[PODMAN_PROFILE]["image_id"])
            await manager.reconcile()
            self._podman_reason = None
        except (OSError, KeyError):
            self._podman_reason = "受限执行文件、运行时信息或私有目录不可用"
        except (ValueError, TypeError, ProcessBoundaryError) as exc:
            self._podman_reason = str(exc)[:400] or "执行配置初始化失败"
        return self

    def _availability(self):
        if self._extension_boundary_reason:
            return False, self._extension_boundary_reason
        if self._podman_reason:
            return False, self._podman_reason
        if (self._manager is None or not self._manager._ready or self._manager._error):
            return False, "容器所有权核对或清理尚未完成"
        from .process_preflight import ACCEPTED_GOVERNORS, RuntimeAttestation
        if type(self._governor) is not RuntimeAttestation:
            return False, "deadline 治理未就绪"
        target = self._targets.get(PODMAN_PROFILE)
        if target is None:
            return False, "执行配置已停用"
        try:
            self._governor.assert_current(target, self.installation_id)
        except (OSError, ProcessBoundaryError):
            return False, "deadline 运行时证明已失效"
        governor = target["governor"]
        if (governor["guard_sha256"], governor["seccomp_sha256"]) not in ACCEPTED_GOVERNORS:
            return False, "deadline 治理尚未通过产品验收"
        return True, None

    async def prepare_service(self, server):
        if self._manager is None or self._podman_reason or self._extension_boundary_reason:
            return
        from .process_preflight import run_preflight
        if self._preflight_task is None:
            self._preflight_task = asyncio.create_task(run_preflight(self, server), name="linux-l1-runtime-preflight")
        cancelled = False
        while True:
            try:
                self._governor = await asyncio.shield(self._preflight_task)
                break
            except asyncio.CancelledError:
                if self._preflight_task.cancelled():
                    raise ProcessBoundaryError("Private runtime preflight was cancelled") from None
                cancelled = True
            except Exception:
                self._podman_reason = "deadline 运行时自检或清理未确认"
                break
        if cancelled:
            raise asyncio.CancelledError

    def revoke_for_extension(self, extension: str):
        """A process lifetime latch; disconnect/start failure cannot clear it."""
        self._extension_boundary_reason = "extension_boundary_unavailable: " + extension

    def assert_extension_boundary(self):
        if self._extension_boundary_reason:
            raise ProcessBoundaryError(self._extension_boundary_reason)

    async def before_extension_start(self, extension: str):
        # Revoke synchronously before yielding. Every pending spawn rechecks
        # this latch before create and before start, so no later admission can
        # race the responsibility inventory below.
        self.revoke_for_extension(extension)
        manager = self._manager
        if manager is not None and (manager._spawning or await manager.dao.active()):
            raise ProcessBoundaryError("仍有 L1 进程清理责任，不能启用未审计扩展")

    def cards(self):
        available, reason = self._availability()
        return [{"id": profile_id, "label": self._labels[profile_id],
                 "mode": "host" if profile_id == "native-host" else "podman",
                 "available": profile_id == "native-host" or available,
                 "reason": None if profile_id == "native-host" else reason,
                 "unattended_supported": False,
                 "boundary_summary": "宿主 Native 进程" if profile_id == "native-host" else
                     "受监督的进程隔离；宿主文件工具和磁盘配额尚未构成完整 OS 沙盒"}
                for profile_id in ("native-host", PODMAN_PROFILE)]

    def resolve(self, profile_id: str | None, *, parent_target: dict | None = None):
        if profile_id is not None and (not isinstance(profile_id, str) or not profile_id):
            raise ProcessBoundaryError("执行配置 ID 必须是字符串或 null")
        if parent_target is not None:
            parent = validate_target(parent_target)
            selected = parent["profile_id"] if profile_id is None else profile_id
            if selected != parent["profile_id"]:
                raise ProcessBoundaryError("子任务不能替换父级已冻结的执行配置")
            target = parent
        else:
            selected = "native-host" if profile_id is None else profile_id
            target = self._targets.get(selected)
        if target is None:
            raise ProcessBoundaryError("执行配置未知、未配置或已停用")
        self.assert_compatible(target, self.installation_id if target["mode"] == "podman" else None)
        return copy.deepcopy(target)

    def assert_compatible(self, target, installation_id):
        target = validate_target(target)
        current = self._targets.get(target["profile_id"])
        if current is None or canonical_hash(current) != canonical_hash(target):
            raise ProcessBoundaryError("已保存执行配置已被停用或策略改变，请创建新任务")
        if target["mode"] == "host":
            if installation_id is not None:
                raise ProcessBoundaryError("宿主配置不能绑定容器安装")
            return
        available, reason = self._availability()
        if not available:
            raise ProcessBoundaryError(reason)
        if installation_id != self.installation_id:
            raise ProcessBoundaryError("已保存执行配置属于其他安装实例")

    async def close(self):
        if self._manager is not None:
            await self._manager.close()
