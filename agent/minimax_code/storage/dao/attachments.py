"""Attachment metadata authority; no filename or project path is opened here."""
from __future__ import annotations

from ._base import now_iso
from .text_artifacts import ArtifactDAO

METADATA_COLUMNS = (
    "id,project_id,service_id,snapshot_id,original_name,declared_type,recognized_type,"
    "size_bytes,sha256,version,parser_status,parser_version,preview_truncated,quality_status,created_at,"
    "parser_reason,parser_scope"
)


def metadata(row):
    result = {key: value for key, value in dict(row).items() if key != "preview_text"}
    result["preview_truncated"] = bool(result["preview_truncated"])
    return result


class AttachmentDAO:
    def __init__(self, db):
        self.db = db

    async def service_id(self):
        return await ArtifactDAO(self.db).service_id()

    async def current_service(self):
        row = await self.db.fetchone("SELECT service_id FROM artifact_service WHERE singleton=1")
        if row is None:
            raise ValueError("Attachment service identity is unavailable")
        return row["service_id"]

    async def project(self, project_id):
        row = await self.db.fetchone("SELECT id,root_path,created_at FROM projects WHERE id=?",
                                    (project_id,))
        if row is None:
            raise ValueError("An existing project is required")
        return dict(row)

    async def insert(self, fields, *, expected_project, expected_session=None):
        async with self.db.transaction() as conn:
            row = await self.insert_in_transaction(conn, fields, expected_project=expected_project,
                                                   expected_session=expected_session)
        return metadata(row)

    async def insert_in_transaction(self, conn, fields, *, expected_project, expected_session=None):
        """Shared atomic insert for uploads and generated-office associations."""
        cursor = await conn.execute("SELECT id,root_path,created_at FROM projects WHERE id=?",
                                    (fields["project_id"],))
        project = await cursor.fetchone()
        if project is None or dict(project) != expected_project:
            raise ValueError("Attachment project binding changed during upload")
        if expected_session is not None:
            cursor = await conn.execute("SELECT project_id FROM sessions WHERE id=?", (expected_session,))
            session = await cursor.fetchone()
            if session is None or session[0] != fields["project_id"]:
                raise ValueError("Session project binding changed during document generation")
        cursor = await conn.execute("SELECT service_id FROM artifact_service WHERE singleton=1")
        service = await cursor.fetchone()
        if service is None or service[0] != fields["service_id"]:
            raise ValueError("Attachment service identity changed during upload")
        cursor = await conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM attachments "
                                    "WHERE project_id=? AND original_name=?",
                                    (fields["project_id"], fields["original_name"]))
        row = {**fields, "version": (await cursor.fetchone())[0], "created_at": now_iso()}
        keys = tuple(row)
        await conn.execute(f"INSERT INTO attachments ({','.join(keys)}) "
                           f"VALUES ({','.join('?' for _ in keys)})", tuple(row.values()))
        return row

    async def list(self, project_id, cursor=None, limit=200):
        params = [project_id]
        condition = "project_id=?"
        if cursor is not None:
            anchor = await self.db.fetchone(
                "SELECT rowid AS position FROM attachments WHERE project_id=? AND id=?",
                (project_id, cursor))
            if anchor is None:
                raise ValueError("Attachment cursor does not exist in this project")
            condition += " AND rowid<?"
            params.append(anchor["position"])
        return [metadata(row) for row in await self.db.fetchall(
            f"SELECT {METADATA_COLUMNS} FROM attachments WHERE {condition} ORDER BY rowid DESC LIMIT ?",
            (*params, limit))]

    async def get(self, project_id, attachment_id):
        row = await self.db.fetchone("SELECT * FROM attachments WHERE project_id=? AND id=?",
                                     (project_id, attachment_id))
        if row is None:
            raise ValueError("Attachment does not exist in this project")
        return dict(row)
