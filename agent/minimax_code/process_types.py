"""Immutable ownership and target values shared by process adapters.

These are server-side resolved values, not a user supplied container configuration.
L1 constrains processes; it does not turn host file tools into a file sandbox.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Protocol, runtime_checkable


class ProcessBoundaryError(RuntimeError):
    """Restricted execution cannot be proved safe; no host fallback is allowed."""


@dataclass(frozen=True)
class GovernorSpec:
    kind: str
    guard_sha256: str
    seccomp_sha256: str

    def __post_init__(self):
        if (self.kind != "pid1-boottime-v1" or any(not isinstance(value, str)
                or not re.fullmatch(r"[0-9a-f]{64}", value) or value == "0" * 64
                for value in (self.guard_sha256, self.seccomp_sha256))):
            raise ValueError("Unsupported fixed execution governor")


def _default_governor():
    from .process_assets import governor_identity
    return GovernorSpec(**governor_identity())


@dataclass(frozen=True)
class ExecutionTargetSpec:
    profile_id: str
    image_id: str
    memory_bytes: int = 128 * 1024 * 1024
    cpus_millis: int = 500
    pids_limit: int = 64
    timeout_seconds: int = 300
    tmpfs_bytes: int = 32 * 1024 * 1024
    output_limit: int = 5 * 1024 * 1024
    version: int = 1
    policy_version: int = 2
    mode: str = "podman"
    governor: GovernorSpec = field(default_factory=_default_governor)

    def __post_init__(self) -> None:
        if (self.mode != "podman" or type(self.version) is not int or self.version != 1
                or type(self.policy_version) is not int or self.policy_version != 2):
            raise ValueError("Unsupported restricted execution target")
        if isinstance(self.governor, dict):
            object.__setattr__(self, "governor", GovernorSpec(**self.governor))
        if not isinstance(self.governor, GovernorSpec):
            raise ValueError("A frozen execution governor is required")
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", self.profile_id):
            raise ValueError("Invalid execution profile identifier")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", self.image_id):
            raise ValueError("A complete local image ID is required")
        for name in ("memory_bytes", "cpus_millis", "pids_limit", "timeout_seconds",
                     "tmpfs_bytes", "output_limit"):
            if type(getattr(self, name)) is not int or getattr(self, name) <= 0:
                raise ValueError(f"{name} must be a positive integer")

    def to_dict(self) -> dict:
        return asdict(self)

    @property
    def fingerprint(self) -> str:
        return canonical_hash(self.to_dict())


@dataclass(frozen=True)
class ExecutionOwner:
    run_id: str
    family_id: str
    family_root_run_id: str
    workspace: str
    workspace_device: int
    workspace_inode: int

    @classmethod
    def bind(cls, *, run_id: str, family_id: str, family_root_run_id: str,
             workspace: str | Path) -> ExecutionOwner:
        root = Path(workspace).resolve(strict=True)
        if not root.is_dir():
            raise ProcessBoundaryError("Owned workspace is not a directory")
        identity = root.stat()
        return cls(run_id, family_id, family_root_run_id, str(root),
                   identity.st_dev, identity.st_ino)

    def verify_workspace(self, cwd: str | Path | None = None) -> tuple[Path, str]:
        root = Path(self.workspace).resolve(strict=True)
        stat = root.stat()
        if (stat.st_dev, stat.st_ino) != (self.workspace_device, self.workspace_inode):
            raise ProcessBoundaryError("Owned workspace physical identity changed")
        target = Path(cwd) if cwd is not None else root
        if not target.is_absolute():
            target = root / target
        target = target.resolve(strict=True)
        if not target.is_dir() or not target.is_relative_to(root):
            raise ProcessBoundaryError("Container cwd is outside the owned workspace")
        relative = target.relative_to(root).as_posix()
        return root, "/workspace" + ("/" + relative if relative != "." else "")


@dataclass(frozen=True)
class InstallationIdentity:
    install_id: str
    uid: int
    host_fingerprint: str
    store_fingerprint: str
    bootstrap_nonce: str
    location_fingerprint: str
    data_dir: str
    data_dir_device: str
    data_dir_inode: str

    def __post_init__(self):
        if any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value)
               for value in (self.install_id, self.bootstrap_nonce)):
            raise ProcessBoundaryError("Installation UUID and bootstrap nonce are incompatible")
        if type(self.uid) is not int or self.uid < 0:
            raise ProcessBoundaryError("Installation UID is incompatible")
        if any(not isinstance(value, str) or not value.isascii() or not value.isdecimal()
               or str(int(value)) != value for value in (self.data_dir_device, self.data_dir_inode)):
            raise ProcessBoundaryError("Installation physical identity must be exact decimal text")

    def marker(self) -> dict:
        return {"version": 1, **asdict(self)}

    @property
    def marker_hash(self) -> str:
        return canonical_hash(self.marker())


@runtime_checkable
class ProcessIO(Protocol):
    stdin: asyncio.StreamWriter | None
    stdout: asyncio.StreamReader | None
    stderr: asyncio.StreamReader | None

    @property
    def returncode(self) -> int | None: ...

    async def wait(self) -> int: ...


@runtime_checkable
class OwnedProcessHandle(ProcessIO, Protocol):
    execution_id: str

    async def terminate_owned_tree(self) -> None: ...


def canonical_hash(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False, allow_nan=False).encode()).hexdigest()
