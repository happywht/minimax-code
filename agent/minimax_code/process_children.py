"""Frozen target inheritance and durable workspace ownership for trusted children."""
from __future__ import annotations

import copy

from .process_binding import HOST_TARGET, validate_target
from .process_types import ProcessBoundaryError, canonical_hash


def child_target(profile_id):
    from .app import get_execution_profiles
    from .process_context import current_process_target
    from .run_ownership import owned_runtime

    context = owned_runtime.get()
    parent = (context or {}).get("execution_target")
    required = current_process_target()
    if required is not None and (context is None or parent is None):
        raise ProcessBoundaryError("Restricted child has no frozen parent target")
    # v1/v2 owned Native profiles have an explicit legacy-host interpretation.
    if context is not None and parent is None:
        parent = HOST_TARGET
    catalog = get_execution_profiles()
    if catalog is None:
        if profile_id not in (None, "native-host") or (parent and parent["mode"] != "host"):
            raise ProcessBoundaryError("Child execution profile service is unavailable")
        target = copy.deepcopy(HOST_TARGET)
    else:
        target = catalog.resolve(profile_id, parent_target=parent)
    if target["mode"] == "podman":
        if context is None or required is None or required.owner is None:
            raise ProcessBoundaryError("Restricted child requires a durably bound Native parent")
        if target != required.target.to_dict():
            raise ProcessBoundaryError("Child target differs from its bound parent")
    return target, (context or {}).get("execution_installation_id")


def owner_snapshot(target, installation_id, *, parent_run_id):
    target = validate_target(target)
    if not isinstance(parent_run_id, str) or not parent_run_id:
        raise ProcessBoundaryError("Child process binding requires its actual parent run")
    return {"version": 1, "parent_run_id": parent_run_id,
        "execution_target": copy.deepcopy(target), "execution_target_hash": canonical_hash(target),
        "execution_installation_id": installation_id, "prepared": False}


async def prepare_child_owner(db, run_id, child, *, target, installation_id):
    """Persist the only internal authority before any child payload can start."""
    from .budget_ledger import owned_cleanup
    from .process_context import current_process_target
    from .schedule_context import directory_identity
    from .storage.dao._base import dumps_json, loads_json
    from .storage.dao.process_executions import ProcessExecutionsDAO

    required = current_process_target()
    if required is None or required.owner is None:
        raise ProcessBoundaryError("Restricted child parent is not bound")
    snapshot = owner_snapshot(target, installation_id, parent_run_id=required.owner.run_id)
    snapshot.update(prepared=True, workspace_id=child.run_id, workspace=str(child.root),
        source_workspace=str(child.parent), source_identity=directory_identity(str(child.parent)),
        directory_identity=directory_identity(str(child.root)))
    snapshot["copy_binding"] = {"kind": "directory", **{key: snapshot[key] for key in (
        "workspace_id", "workspace", "source_workspace", "source_identity", "directory_identity")}}

    async def save():
        async with db.transaction() as conn:
            cursor = await conn.execute("SELECT metadata,status FROM agent_runs WHERE id=?", (run_id,))
            row = await cursor.fetchone()
            metadata = loads_json(row["metadata"]) if row else None
            if (not isinstance(metadata, dict) or row["status"] not in {"running", "planning"}
                    or "agent_execution" in metadata
                    or metadata.get("process_execution_owner") != owner_snapshot(
                        target, installation_id, parent_run_id=required.owner.run_id)):
                raise ProcessBoundaryError("Admitted child process authority changed before preparation")
            metadata["process_execution_owner"] = snapshot
            await conn.execute("UPDATE agent_runs SET metadata=? WHERE id=?", (dumps_json(metadata), run_id))
    await owned_cleanup(save())
    binding = await ProcessExecutionsDAO(db).binding_for_run(run_id)
    if binding.owner is None:
        raise ProcessBoundaryError("Prepared child has no durable process owner")
    return binding


async def refresh_child_metadata(dao, run_id, metadata):
    """Lifecycle writers must retain the newer prepared authority."""
    current = await dao.get_run(run_id)
    saved = (current or {}).get("metadata") or {}
    if "process_execution_owner" in saved:
        metadata["process_execution_owner"] = saved["process_execution_owner"]
