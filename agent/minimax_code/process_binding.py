"""Project one authoritative admitted snapshot into a process ownership binding."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .process_types import ExecutionOwner, ExecutionTargetSpec, ProcessBoundaryError, canonical_hash

HOST_TARGET = {"version": 1, "policy_version": 1, "mode": "host", "profile_id": "native-host"}


def validate_target(value: object) -> dict:
    if not isinstance(value, dict):
        raise ProcessBoundaryError("Saved execution target is missing or incompatible")
    if value.get("mode") == "host":
        if value != HOST_TARGET or any(type(value[key]) is not type(expected)
                                        for key, expected in HOST_TARGET.items()):
            raise ProcessBoundaryError("Saved host execution target is incompatible")
        return dict(HOST_TARGET)
    if value.get("mode") == "podman":
        try:
            spec = ExecutionTargetSpec(**value)
        except (TypeError, ValueError) as exc:
            raise ProcessBoundaryError("Saved Podman execution target is incompatible") from exc
        if spec.profile_id != "linux-podman-l1" or spec.to_dict() != value:
            raise ProcessBoundaryError("Saved Podman execution target fields are incomplete")
        return spec.to_dict()
    raise ProcessBoundaryError("Saved execution target mode is incompatible")


def _pair(value):
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ProcessBoundaryError("Saved directory identity is incompatible")
    result = []
    for item in value:
        if type(item) is int and item >= 0:
            result.append(str(item))
        elif isinstance(item, str) and item.isascii() and item.isdecimal() and str(int(item)) == item:
            result.append(item)
        else:
            raise ProcessBoundaryError("Saved directory identity must be exact decimal integers")
    return tuple(result)


@dataclass(frozen=True)
class ExecutionBinding:
    target: dict
    target_hash: str
    installation_id: str | None
    owner: ExecutionOwner | None
    parent_run_id: str | None


def load_execution_binding(run: dict, *, family_root_run_id: str | None = None) -> ExecutionBinding | None:
    """Legacy v1/v2 is explicitly host; missing/corrupt v3 never downgrades.

    Root AgentSpec is the sole authority. Trusted children without that spec
    have a separate versioned process_execution_owner, never both at once.
    """
    metadata = run.get("metadata") or {}
    public = metadata.get("agent_execution")
    internal = metadata.get("process_execution_owner")
    if public is not None and internal is not None:
        raise ProcessBoundaryError("Run has conflicting public and internal execution authorities")
    snapshot = public if public is not None else internal
    if snapshot is None:
        return None
    if not isinstance(snapshot, dict):
        raise ProcessBoundaryError("Saved execution authority is incompatible")
    if public is not None:
        if snapshot.get("version") in (1, 2):
            if any(key in snapshot for key in ("execution_target", "execution_target_hash", "execution_installation_id")):
                raise ProcessBoundaryError("Legacy AgentSpec cannot carry a restricted target")
            return None
        from .skill_execution import _digest

        if (snapshot.get("version") != 3 or snapshot.get("digest") != _digest(
                {key: value for key, value in snapshot.items() if key != "digest"})):
            raise ProcessBoundaryError("Saved AgentSpec v3 digest is incompatible")
        parent_run_id = None
    else:
        version = snapshot.get("version")
        if type(version) is not int or version not in {1, 2}:
            raise ProcessBoundaryError("Saved internal process owner version is incompatible")
        parent_run_id = snapshot.get("parent_run_id")
        if version == 2:
            from .process_preflight import PROBE_KIND, probe_argv_hash
            if (snapshot.get("kind") != "runtime_preflight" or parent_run_id is not None
                    or metadata.get("origin") != "runtime_preflight"
                    or snapshot.get("probe_kind") != PROBE_KIND
                    or snapshot.get("probe_argv_hash") != probe_argv_hash()
                    or (family_root_run_id is not None and family_root_run_id != run["id"])):
                raise ProcessBoundaryError("Saved private runtime preflight root is incompatible")
        elif not isinstance(parent_run_id, str) or not parent_run_id or parent_run_id == run["id"]:
            raise ProcessBoundaryError("Internal process owner requires its real parent run")
    target = validate_target(snapshot.get("execution_target"))
    if public is not None:
        from .process_boundary import assert_restricted_agent_snapshot
        assert_restricted_agent_snapshot(snapshot)
    digest = canonical_hash(target)
    if snapshot.get("execution_target_hash") != digest:
        raise ProcessBoundaryError("Saved execution target hash is incompatible")
    installation_id = snapshot.get("execution_installation_id")
    if target["mode"] == "host":
        if installation_id is not None:
            raise ProcessBoundaryError("Host execution cannot claim a container installation")
        return ExecutionBinding(target, digest, None, None, parent_run_id)
    if not isinstance(installation_id, str) or not installation_id:
        raise ProcessBoundaryError("Saved restricted installation is missing")
    if snapshot.get("prepared") is not True:
        return ExecutionBinding(target, digest, installation_id, None, parent_run_id)
    copy = snapshot.get("copy_binding")
    if not isinstance(copy, dict) or copy.get("kind") != "directory":
        raise ProcessBoundaryError("Restricted process requires a durably prepared independent copy")
    workspace = snapshot.get("workspace")
    source = snapshot.get("source_workspace")
    if (not isinstance(workspace, str) or not Path(workspace).is_absolute()
            or not isinstance(source, str) or not Path(source).is_absolute()
            or workspace == source or copy.get("workspace") != workspace
            or copy.get("source_workspace") != source
            or not isinstance(snapshot.get("workspace_id"), str)
            or copy.get("workspace_id") != snapshot["workspace_id"]):
        raise ProcessBoundaryError("Restricted copy path binding is incompatible")
    identity = _pair(snapshot.get("directory_identity"))
    source_identity = _pair(snapshot.get("source_identity"))
    if (identity == source_identity or _pair(copy.get("directory_identity")) != identity
            or _pair(copy.get("source_identity")) != source_identity):
        raise ProcessBoundaryError("Restricted copy physical identity is incompatible")
    family = metadata.get("budget_family_id")
    if not isinstance(family, str) or not family:
        raise ProcessBoundaryError("Prepared restricted run has no budget family")
    # Only the actual family row supplies its root. Callers resolving just the
    # frozen target may omit it, but that projection cannot authorize a spawn.
    owner = ExecutionOwner(run["id"], family, family_root_run_id or "", workspace,
                           int(identity[0]), int(identity[1]))
    return ExecutionBinding(target, digest, installation_id, owner, parent_run_id)
