"""Text artifact metadata, with atomic association and per-path version checks."""
from __future__ import annotations

import uuid
from pathlib import Path

from ._base import now_iso


class ArtifactDAO:
    def __init__(self, db):
        self.db = db

    async def service_id(self):
        async with self.db.transaction() as conn:
            await conn.execute("INSERT OR IGNORE INTO artifact_service VALUES(1, ?)",
                               (f"svc_{uuid.uuid4().hex}",))
            cursor = await conn.execute("SELECT service_id FROM artifact_service WHERE singleton=1")
            return (await cursor.fetchone())[0]

    async def project(self, project_id):
        row = await self.db.fetchone("SELECT * FROM projects WHERE id=?", (project_id,))
        if row is None or not row["root_path"]:
            raise ValueError("An existing project with an explicit root is required")
        return dict(row)

    @staticmethod
    def _run_binding(row, project_id, expected_root):
        if row is None or row["project_id"] != project_id:
            raise ValueError("Run must belong to a session in this project")
        workspace = row["workspace_path"]
        if workspace and Path(workspace).resolve(strict=True) != Path(expected_root).resolve(strict=True):
            raise ValueError("Run session workspace differs from the project root")

    async def check_run(self, project_id, run_id, expected_root):
        if run_id is not None:
            row = await self.db.fetchone(
                "SELECT s.project_id,s.workspace_path FROM agent_runs r "
                "JOIN sessions s ON s.id=r.session_id "
                "WHERE r.id=?", (run_id,))
            self._run_binding(row, project_id, expected_root)

    async def insert(self, fields, *, expected_root, expected_service_id=None,
                     expected_session_id=None, idempotent=False):
        async with self.db.transaction() as conn:
            cursor = await conn.execute("SELECT root_path FROM projects WHERE id=?",
                                        (fields["project_id"],))
            project = await cursor.fetchone()
            if project is None or project[0] != expected_root:
                raise ValueError("Project binding changed during registration")
            if expected_service_id is not None:
                cursor = await conn.execute("SELECT service_id FROM artifact_service WHERE singleton=1")
                service = await cursor.fetchone()
                if service is None or service[0] != expected_service_id:
                    raise ValueError("Service identity changed during registration")
            if fields["run_id"] is not None:
                cursor = await conn.execute(
                    "SELECT s.project_id,s.workspace_path,s.id AS session_id FROM agent_runs r "
                    "JOIN sessions s ON s.id=r.session_id "
                    "WHERE r.id=?", (fields["run_id"],))
                run = await cursor.fetchone()
                self._run_binding(run, fields["project_id"], expected_root)
                if expected_session_id is not None and run["session_id"] != expected_session_id:
                    raise ValueError("Run session changed during registration")
            if idempotent:
                cursor = await conn.execute("SELECT * FROM text_artifacts WHERE id=?", (fields["id"],))
                existing = await cursor.fetchone()
                if existing is not None:
                    row = dict(existing)
                    keys = ("project_id", "run_id", "service_id", "source_path", "mime_type", "size_bytes", "sha256")
                    if any(row[key] != fields[key] for key in keys):
                        raise ValueError("Registration request already used for different content or scope")
                    return row
            cursor = await conn.execute(
                "SELECT COALESCE(MAX(version),0)+1 FROM text_artifacts "
                "WHERE project_id=? AND source_path=?",
                (fields["project_id"], fields["source_path"]))
            row = {**fields, "version": (await cursor.fetchone())[0],
                   "parse_status": "parseable", "verification_status": "unverified",
                   "created_at": now_iso()}
            keys = tuple(row)
            await conn.execute(
                f"INSERT INTO text_artifacts ({','.join(keys)}) "
                f"VALUES ({','.join('?' for _ in keys)})", tuple(row.values()))
        return row

    async def list(self, project_id, run_id=None, cursor=None, limit=200):
        scope = "project_id=?" + (" AND run_id=?" if run_id is not None else "")
        params = [project_id] + ([run_id] if run_id is not None else [])
        if cursor is not None:
            anchor = await self.db.fetchone(
                f"SELECT rowid AS position FROM text_artifacts WHERE {scope} AND id=?",
                (*params, cursor))
            if anchor is None:
                raise ValueError("Artifact cursor does not exist in this scope")
            scope += " AND rowid<?"
            params.append(anchor["position"])
        rows = await self.db.fetchall(
            f"SELECT * FROM text_artifacts WHERE {scope} ORDER BY rowid DESC LIMIT ?",
            (*params, limit))
        return [dict(row) for row in rows]

    async def get(self, project_id, artifact_id):
        row = await self.db.fetchone("SELECT * FROM text_artifacts WHERE project_id=? AND id=?",
                                     (project_id, artifact_id))
        if row is None:
            raise ValueError("Artifact does not exist in this project")
        return dict(row)
