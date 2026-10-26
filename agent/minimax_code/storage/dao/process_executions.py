"""Atomic process intent, resource admission, and revisioned ownership evidence."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass

from ...process_types import (
    ExecutionOwner,
    ExecutionTargetSpec,
    InstallationIdentity,
    ProcessBoundaryError,
)
from ._base import dumps_json, loads_json, now_iso
from .runs import _fetchall, _fetchone


class ProcessCapacityExceeded(ProcessBoundaryError):
    pass


class ProcessRevisionConflict(ProcessBoundaryError):
    pass


@dataclass(frozen=True)
class ProcessCapacity:
    installation_count: int = 2
    family_count: int = 1
    installation_memory: int = 256 * 1024 * 1024
    installation_cpus: int = 1000
    installation_pids: int = 128

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in asdict(self).values()):
            raise ValueError("Process capacity must contain positive integers")


_TRANSITIONS = {
    "intent": {"created", "stopped", "cleanup_pending", "needs_review", "removed"},
    "created": {"starting", "stopped", "cleanup_pending", "needs_review", "removed"},
    "starting": {"running", "stopped", "cleanup_pending", "needs_review", "removed"},
    "running": {"stopped", "cleanup_pending", "needs_review", "removed"},
    "stopped": {"removing", "cleanup_pending", "needs_review", "removed"},
    "removing": {"removed", "cleanup_pending", "needs_review"},
    "cleanup_pending": {"stopped", "removing", "removed", "cleanup_pending", "needs_review"},
    "needs_review": {"stopped", "removed", "needs_review", "cleanup_pending"},
    "removed": set(),
}
_UPDATABLE = {"container_id", "outcome", "exit_code", "oom_killed", "error"}


def _identity_matches(row, identity):
    return row is not None and all(row[key] == value for key, value in asdict(identity).items())


class ProcessExecutionsDAO:
    def __init__(self, db):
        self.db = db

    async def enable_durable_ownership(self):
        """Startup only, under the service lease before any job can start.

        SQLite NORMAL can lose committed WAL transactions after OS power loss.
        Intent-before-create ownership requires FULL on this actual connection.
        """
        await self.db.execute("PRAGMA synchronous=FULL")
        row = await self.db.fetchone("PRAGMA synchronous")
        if row is None or row[0] != 2:
            raise ProcessBoundaryError("SQLite FULL durability is required for restricted ownership")

    async def installation(self):
        row = await self.db.fetchone("SELECT * FROM execution_installation WHERE singleton=1")
        return dict(row) if row else None

    async def begin_installation(self, identity: InstallationIdentity):
        """Persist pending identity before marker publication, under the lease."""
        async with self.db.transaction() as conn:
            row = await _fetchone(conn, "SELECT * FROM execution_installation WHERE singleton=1")
            if row is not None:
                if not _identity_matches(row, identity):
                    raise ProcessBoundaryError("Execution installation identity mismatch")
                return
            await conn.execute(
                "INSERT INTO execution_installation(singleton,install_id,uid,host_fingerprint,"
                "store_fingerprint,bootstrap_nonce,location_fingerprint,data_dir,data_dir_device,"
                "data_dir_inode,marker_hash,phase,created_at) "
                "VALUES(1,?,?,?,?,?,?,?,?,?,?,'bootstrap_pending',?)",
                (*asdict(identity).values(), identity.marker_hash, now_iso()))

    async def activate_installation(self, identity: InstallationIdentity):
        """Caller has read back the durable immutable marker before this CAS."""
        async with self.db.transaction() as conn:
            row = await _fetchone(conn, "SELECT * FROM execution_installation WHERE singleton=1")
            if not _identity_matches(row, identity) or row["marker_hash"] != identity.marker_hash:
                raise ProcessBoundaryError("Execution installation activation identity mismatch")
            if row["phase"] == "active":
                return
            cursor = await conn.execute(
                "UPDATE execution_installation SET phase='active',activated_at=? "
                "WHERE singleton=1 AND phase='bootstrap_pending' AND marker_hash=?",
                (now_iso(), identity.marker_hash))
            if cursor.rowcount != 1:
                raise ProcessBoundaryError("Execution installation activation changed")

    async def assert_installation(self, identity: InstallationIdentity):
        durability = await self.db.fetchone("PRAGMA synchronous")
        if durability is None or durability[0] != 2:
            raise ProcessBoundaryError("Restricted ownership connection lost FULL durability")
        row = await self.installation()
        if (not _identity_matches(row, identity) or row["phase"] != "active"
                or row["marker_hash"] != identity.marker_hash):
            raise ProcessBoundaryError("Execution installation identity mismatch")

    async def intent(self, *, exec_id: str, container_name: str,
                     identity: InstallationIdentity, owner: ExecutionOwner,
                     target: ExecutionTargetSpec, argv_hash: str, container_cwd: str,
                     deadline: float, capacity: ProcessCapacity):
        async with self.db.transaction() as conn:
            durability = await _fetchone(conn, "PRAGMA synchronous")
            if durability is None or durability[0] != 2:
                raise ProcessBoundaryError("Restricted ownership requires FULL durability before intent")
            installation = await _fetchone(conn,
                "SELECT * FROM execution_installation WHERE singleton=1")
            if (not _identity_matches(installation, identity) or installation["phase"] != "active"
                    or installation["marker_hash"] != identity.marker_hash):
                raise ProcessBoundaryError("Execution installation identity mismatch")
            run = await _fetchone(conn, "SELECT * FROM agent_runs WHERE id=?", (owner.run_id,))
            family = await _fetchone(conn,
                "SELECT * FROM budget_families WHERE id=?", (owner.family_id,))
            if run is None or family is None or family["root_run_id"] != owner.family_root_run_id:
                raise ProcessBoundaryError("Restricted process requires a real run and family root")
            metadata = loads_json(run["metadata"]) or {}
            if metadata.get("budget_family_id") != owner.family_id:
                raise ProcessBoundaryError("Restricted process run belongs to another family")
            from ...process_binding import load_execution_binding

            binding = load_execution_binding({**dict(run), "metadata": metadata},
                family_root_run_id=family["root_run_id"])
            if (binding is None or binding.owner != owner or binding.target != target.to_dict()
                    or binding.installation_id != identity.install_id):
                raise ProcessBoundaryError("Process owner differs from its authoritative admitted snapshot")
            internal = metadata.get("process_execution_owner") or {}
            if internal.get("version") == 2:
                from ...process_preflight import assert_private_probe
                if family["root_run_id"] != run["id"] or metadata.get("origin") != "runtime_preflight":
                    raise ProcessBoundaryError("Runtime preflight requires its own real family root")
                assert_private_probe(run["id"], internal, argv_hash)
            if binding.parent_run_id:
                parent = await _fetchone(conn, "SELECT * FROM agent_runs WHERE id=?", (binding.parent_run_id,))
                parent_md = loads_json(parent["metadata"]) or {} if parent else {}
                parent_binding = load_execution_binding({**dict(parent), "metadata": parent_md},
                    family_root_run_id=family["root_run_id"]) if parent else None
                if (parent is None or parent_md.get("budget_family_id") != owner.family_id
                        or parent_binding is None or parent_binding.target_hash != binding.target_hash
                        or parent_binding.installation_id != binding.installation_id
                        or parent_binding.owner is None
                        or metadata["process_execution_owner"].get("source_workspace") != parent_binding.owner.workspace
                        or [str(value) for value in metadata["process_execution_owner"].get("source_identity", [])]
                           != [str(parent_binding.owner.workspace_device), str(parent_binding.owner.workspace_inode)]
                        or parent["status"] not in {"running", "planning"}):
                    raise ProcessBoundaryError("Restricted child parent authority or family is unavailable")
            if run["status"] not in {"running", "planning"}:
                raise ProcessBoundaryError("Restricted process owner is not an active run")
            unresolved = await _fetchone(conn,
                "SELECT exec_id FROM process_executions WHERE install_id=? "
                "AND state IN ('needs_review','cleanup_pending') LIMIT 1", (identity.install_id,))
            if unresolved:
                raise ProcessBoundaryError("Restricted admission paused by unresolved process ownership")
            active = await _fetchall(conn,
                "SELECT * FROM process_executions WHERE install_id=? AND resource_held=1",
                (identity.install_id,))
            if (len(active) >= capacity.installation_count
                    or sum(row["family_id"] == owner.family_id for row in active) >= capacity.family_count
                    or sum(row["reserved_memory"] for row in active) + target.memory_bytes > capacity.installation_memory
                    or sum(row["reserved_cpus"] for row in active) + target.cpus_millis > capacity.installation_cpus
                    or sum(row["reserved_pids"] for row in active) + target.pids_limit > capacity.installation_pids):
                raise ProcessCapacityExceeded("Restricted process family or installation capacity exhausted")
            stamp = now_iso()
            from ...process_deadline import new_deadline
            # Bind once in the same intent/resource transaction. Neither start
            # nor recovery can recompute this deadline from elapsed wall time.
            deadline_boot_id, deadline_boottime_ns = new_deadline(target.timeout_seconds)
            await conn.execute(
                "INSERT INTO process_executions(exec_id,run_id,family_id,family_root_run_id,"
                "install_id,location_fingerprint,container_name,state,target_json,target_hash,workspace,workspace_device,"
                "workspace_inode,argv_hash,container_cwd,deadline,deadline_boot_id,deadline_boottime_ns,"
                "reserved_memory,reserved_cpus,reserved_pids,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,'intent',?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (exec_id, owner.run_id, owner.family_id, owner.family_root_run_id,
                 identity.install_id, identity.location_fingerprint, container_name,
                 dumps_json(target.to_dict()), target.fingerprint,
                 owner.workspace, str(owner.workspace_device), str(owner.workspace_inode), argv_hash,
                 container_cwd, deadline, deadline_boot_id, deadline_boottime_ns, target.memory_bytes, target.cpus_millis,
                 target.pids_limit, stamp, stamp))
            await self._event(conn, exec_id, 0, None, "intent", {
                "target_hash": target.fingerprint, "argv_hash": argv_hash,
                "deadline": deadline, "deadline_boot_id": deadline_boot_id,
                "deadline_boottime_ns": deadline_boottime_ns, "run_id": owner.run_id})
        return await self.get(exec_id)

    async def transition(self, exec_id: str, revision: int, state: str, *, evidence: dict,
                         **updates):
        if set(updates) - _UPDATABLE:
            raise ValueError("Invalid process execution update")
        if state == "removed" and evidence.get("absent") is not True:
            raise ProcessBoundaryError("Resource release requires exact container absence evidence")
        # Validate serializability before entering the transaction; never persist secrets/argv.
        json.dumps(evidence)
        async with self.db.transaction() as conn:
            row = await _fetchone(conn,
                "SELECT * FROM process_executions WHERE exec_id=?", (exec_id,))
            if row is None or row["revision"] != revision:
                raise ProcessRevisionConflict("Process execution revision changed")
            if state not in _TRANSITIONS[row["state"]]:
                raise ProcessBoundaryError(f"Invalid process transition {row['state']} -> {state}")
            if state == "starting":
                from ...process_deadline import remaining_seconds
                if remaining_seconds(dict(row)) <= 0:
                    raise ProcessBoundaryError("Process immutable deadline expired before start")
                owner_run = await _fetchone(conn,
                    "SELECT * FROM agent_runs WHERE id=?", (row["run_id"],))
                if owner_run is None or owner_run["status"] not in {"running", "planning"}:
                    raise ProcessBoundaryError("Process owner stopped before durable start intent")
                from ...process_binding import load_execution_binding

                metadata = loads_json(owner_run["metadata"]) or {}
                binding = load_execution_binding({**dict(owner_run), "metadata": metadata},
                    family_root_run_id=row["family_root_run_id"])
                if (binding is None or binding.owner is None
                        or binding.target_hash != row["target_hash"]
                        or binding.installation_id != row["install_id"]
                        or binding.owner.workspace != row["workspace"]
                        or str(binding.owner.workspace_device) != row["workspace_device"]
                        or str(binding.owner.workspace_inode) != row["workspace_inode"]):
                    raise ProcessBoundaryError("Process admitted ownership changed before start")
                internal = metadata.get("process_execution_owner") or {}
                if internal.get("version") == 2:
                    from ...process_preflight import assert_private_probe
                    assert_private_probe(row["run_id"], internal, row["argv_hash"])
            if "container_id" in updates and row["container_id"] not in {None, updates["container_id"]}:
                raise ProcessBoundaryError("Process container identity is immutable")
            fields = {"state": state, "revision": revision + 1, "updated_at": now_iso(),
                      **updates}
            if state == "removed":
                fields["resource_held"] = 0
            cursor = await conn.execute(
                "UPDATE process_executions SET " + ",".join(f"{name}=?" for name in fields)
                + " WHERE exec_id=? AND revision=?",
                (*fields.values(), exec_id, revision))
            if cursor.rowcount != 1:
                raise ProcessRevisionConflict("Process execution revision changed")
            await self._event(conn, exec_id, revision + 1, row["state"], state, evidence)
        return await self.get(exec_id)

    @staticmethod
    async def _event(conn, exec_id, ordinal, from_state, to_state, evidence):
        await conn.execute(
            "INSERT INTO process_execution_events(exec_id,ordinal,from_state,to_state,evidence,created_at) "
            "VALUES(?,?,?,?,?,?)", (exec_id, ordinal, from_state, to_state,
                                    dumps_json(evidence), now_iso()))

    async def get(self, exec_id):
        row = await self.db.fetchone("SELECT * FROM process_executions WHERE exec_id=?", (exec_id,))
        return dict(row) if row else None

    async def active(self):
        return [dict(row) for row in await self.db.fetchall(
            "SELECT * FROM process_executions WHERE resource_held=1 ORDER BY created_at,exec_id")]

    async def events(self, exec_id):
        return [dict(row) for row in await self.db.fetchall(
            "SELECT * FROM process_execution_events WHERE exec_id=? ORDER BY ordinal", (exec_id,))]

    async def binding_for_run(self, run_id):
        from ...process_binding import load_execution_binding

        async with self.db.transaction() as conn:
            run = await _fetchone(conn, "SELECT * FROM agent_runs WHERE id=?", (run_id,))
            if run is None:
                raise ProcessBoundaryError("Process durable run is unavailable")
            metadata = loads_json(run["metadata"]) or {}
            family = await _fetchone(conn, "SELECT root_run_id FROM budget_families WHERE id=?",
                (metadata.get("budget_family_id"),))
            if family is None:
                raise ProcessBoundaryError("Process durable budget family is unavailable")
            return load_execution_binding({**dict(run), "metadata": metadata},
                family_root_run_id=family["root_run_id"])
