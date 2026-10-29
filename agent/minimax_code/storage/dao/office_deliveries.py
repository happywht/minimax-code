"""Atomic generated-file registration; caller cannot supply host/path authority."""
from __future__ import annotations

import json
from pathlib import Path

from ...office_metadata import validate_record
from ._base import now_iso
from .attachments import AttachmentDAO

RUN_HISTORY_BATCH_SIZE = 256
MAX_RUN_METADATA = 64 * 1024


def _constant(_):
    raise ValueError("Invalid run association metadata")


def _unique(pairs):
    values = {}
    for key, value in pairs:
        if key in values:
            raise ValueError("Invalid run association metadata")
        values[key] = value
    return values


async def run_binding(query, project, run_id):
    if run_id is None:
        return None
    rows = await query(
        "SELECT r.id,r.session_id,r.mode,s.project_id,s.workspace_path,s.engine "
        "FROM agent_runs r JOIN sessions s ON s.id=r.session_id WHERE r.id=?", (run_id,))
    if len(rows) != 1:
        raise ValueError("Run association is unavailable")
    row = dict(rows[0])
    if row["project_id"] != project["id"] or row["engine"] != "native" or row["mode"] == "plan":
        raise ValueError("Office association requires an ordinary native host run in this project")
    if (not project["root_path"] or not row["workspace_path"]
            or not Path(row["workspace_path"]).is_absolute()
            or Path(row["workspace_path"]).resolve(strict=True)
            != Path(project["root_path"]).resolve(strict=True)):
        raise ValueError("Run workspace differs from the project root")
    # Inspect the entire history without loading it all at once. A fixed total
    # limit would make every run in a long-lived session impossible to bind.
    # Use rowid keyset pagination so each batch follows the previous one.
    after = 0
    while True:
        history = await query(
            "SELECT rowid AS position, CASE WHEN typeof(metadata)='text' "
            "AND length(CAST(metadata AS BLOB))<=? THEN metadata ELSE NULL END AS metadata "
            "FROM agent_runs WHERE session_id=? AND rowid>? ORDER BY rowid LIMIT ?",
            (MAX_RUN_METADATA, row["session_id"], after, RUN_HISTORY_BATCH_SIZE))
        if not history:
            break
        for saved in history:
            try:
                value = json.loads(saved["metadata"], object_pairs_hook=_unique, parse_constant=_constant)
                pending = [(value, 0)]
                while pending:
                    item, depth = pending.pop()
                    if depth > 32:
                        raise ValueError("Run metadata nesting exceeds inspection limit")
                    if type(item) is dict:
                        pending.extend((child, depth + 1) for child in item.values())
                    elif type(item) is list:
                        pending.extend((child, depth + 1) for child in item)
                if (type(value) is not dict
                        or {"agent_execution", "process_execution_owner"} & value.keys()):
                    raise ValueError
            except (ValueError, TypeError, RecursionError):
                # An optional historical reference cannot enlarge authority.
                raise ValueError("Office association does not support restricted or admitted Agent runs") from None
        after = history[-1]["position"]
    return row


class OfficeDAO:
    def __init__(self, db):
        self.db = db
        self.attachments = AttachmentDAO(db)

    async def find(self, project_id, key, *, expected_session=None):
        async with self.db.transaction() as conn:
            if expected_session is not None:
                cursor = await conn.execute("SELECT project_id FROM sessions WHERE id=?", (expected_session,))
                session = await cursor.fetchone()
                if session is None or session[0] != project_id:
                    raise ValueError("Session project binding changed during office generation")
            cursor = await conn.execute(
                "SELECT * FROM office_deliveries WHERE project_id=? AND request_key=?", (project_id, key))
            row = await cursor.fetchone()
            return dict(row) if row is not None else None

    async def get(self, project_id, delivery_id):
        row = await self.db.fetchone(
            "SELECT * FROM office_deliveries WHERE project_id=? AND id=?", (project_id, delivery_id))
        if row is None:
            raise ValueError("Office delivery does not exist in this project")
        return dict(row)

    async def list(self, project_id, cursor=None, query=None):
        scope = "d.project_id=?"
        args = [project_id]
        if query is not None:
            scope += " AND (instr(a.original_name, ?) > 0 OR instr(d.id, ?) > 0 OR instr(coalesce(d.run_id, ''), ?) > 0)"
            args.extend([query, query, query])
        join = " FROM office_deliveries d LEFT JOIN attachments a ON a.id=d.attachment_id AND a.project_id=d.project_id "
        if cursor is not None:
            anchor = await self.db.fetchone("SELECT d.rowid AS position" + join + "WHERE " + scope + " AND d.id=?", (*args, cursor))
            if anchor is None:
                raise ValueError("Office cursor does not exist in this scope")
            scope += " AND d.rowid<?"
            args.append(anchor["position"])
        rows = await self.db.fetchall("SELECT d.*" + join + "WHERE " + scope + " ORDER BY d.rowid DESC LIMIT 201", tuple(args))
        return [dict(row) for row in rows]

    async def binding(self, project, run_id):
        return await run_binding(self.db.fetchall, project, run_id)

    async def insert(self, fields, attachment, *, expected_project, expected_run, expected_session=None):
        async with self.db.transaction() as conn:
            async def query(sql, args):
                return await (await conn.execute(sql, args)).fetchall()

            if expected_session is not None:
                sessions = await query("SELECT project_id FROM sessions WHERE id=?", (expected_session,))
                if len(sessions) != 1 or sessions[0]["project_id"] != fields["project_id"]:
                    raise ValueError("Session project binding changed during office generation")
            existing = await query(
                "SELECT * FROM office_deliveries WHERE project_id=? AND request_key=?",
                (fields["project_id"], fields["request_key"]))
            if existing:
                row = dict(existing[0])
                if row["request_hash"] != fields["request_hash"]:
                    raise ValueError("Office request key already belongs to different input")
                return row
            current = await run_binding(query, expected_project, fields["run_id"])
            if current != expected_run:
                raise ValueError("Office run association changed during generation")
            inserted = await self.attachments.insert_in_transaction(
                conn, attachment, expected_project=expected_project, expected_session=expected_session)
            row = {**fields, "created_at": now_iso()}
            validate_record(row, inserted)
            keys = tuple(row)
            await conn.execute(f"INSERT INTO office_deliveries ({','.join(keys)}) "
                               f"VALUES ({','.join('?' for _ in keys)})", tuple(row.values()))
        return row
