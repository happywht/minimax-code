"""Transactional occurrence identity, isolated conversation and whole-job lease."""

from __future__ import annotations

import hashlib
import json
import uuid

from ._base import loads_json, now_iso


def hydrate(row):
    if row is None:
        return None
    data = dict(row)
    data["context"] = loads_json(data.get("context"))
    return data


async def fetch(conn, sql, args=()):
    async with conn.execute(sql, args) as cursor:
        return await cursor.fetchone()


class ScheduleOccurrencesDAO:
    def __init__(self, db):
        self.db = db

    async def get(self, occurrence_id):
        return hydrate(
            await self.db.fetchone(
                "SELECT * FROM schedule_occurrences WHERE id=?", (occurrence_id,)
            )
        )

    async def list(self, job_id=None, limit=100):
        where = "WHERE job_id=?" if job_id else ""
        args = (job_id, limit) if job_id else (limit,)
        rows = [
            hydrate(row)
            for row in await self.db.fetchall(
                f"SELECT * FROM schedule_occurrences {where} ORDER BY created_at DESC,id DESC LIMIT ?",
                args,
            )
        ]
        from .tasks import TaskDAO

        for row in rows:
            task = await TaskDAO(self.db).get(row["task_id"])
            if task:
                row.update(
                    verification=task.get("verification"),
                    execution_status=task.get("execution_status"),
                    result=task.get("result"),
                )
        return rows

    async def claim(
        self,
        job_id,
        *,
        source,
        manual_key=None,
        expected_revision=None,
        scheduled_for=None,
        coalesced_through=None,
        next_due=None,
        allow_new=True,
    ):
        fingerprint = hashlib.sha256(
            json.dumps({"expected_revision": expected_revision}, sort_keys=True).encode()
        ).hexdigest()
        async with self.db.transaction() as conn:
            job = await fetch(conn, "SELECT * FROM scheduled_jobs WHERE id=?", (job_id,))
            if source == "manual":
                prior = await fetch(
                    conn,
                    "SELECT * FROM schedule_occurrences WHERE job_id=? AND manual_key=?",
                    (job_id, manual_key),
                )
            else:
                prior = await fetch(
                    conn,
                    "SELECT * FROM schedule_occurrences WHERE job_id=? AND revision=? AND scheduled_for=?",
                    (job_id, expected_revision, scheduled_for),
                )
            if prior:
                if source == "manual" and prior["fingerprint"] != fingerprint:
                    raise ValueError("Idempotency key conflicts with the original revision")
                return hydrate(prior), False
            if not allow_new:
                raise ValueError("Scheduler is stopping")
            if not job or job["deleted_at"]:
                raise ValueError("Schedule does not exist")
            if expected_revision != job["revision"]:
                raise ValueError("Schedule revision changed; refresh before triggering")
            if not job["enabled"] or job["binding_state"] != "ready":
                raise ValueError(
                    job["binding_reason"] or "Schedule is disabled or needs rebind/review"
                )
            if source == "cron" and (
                scheduled_for != job["next_run_at"]
                or not coalesced_through
                or scheduled_for > coalesced_through
            ):
                raise ValueError("Cron cursor changed or is not due")
            if job["active_occurrence_id"]:
                raise ValueError(f"Schedule busy: {job['active_occurrence_id']}")
            context = loads_json(job["context"])
            if not context or job["context_version"] != 1:
                raise ValueError("Legacy schedule needs explicit rebind")
            from ...schedule_context import validate_payload

            prompt = validate_payload(loads_json(job["payload"]))
            oid = f"occ_{uuid.uuid4().hex}"
            sid, tid, ts = f"ses_{oid}", f"task_{oid}", now_iso()
            await conn.execute(
                "INSERT INTO sessions(id,title,created_at,updated_at,workspace_mode,workspace_path,project_id,engine) "
                "VALUES(?,?,?,?, 'local',?,?,'native')",
                (sid, job["name"], ts, ts, context["workspace"], context["project_id"]),
            )
            await conn.execute(
                "INSERT INTO tasks(id,session_id,title,status,progress,created_at,started_at) VALUES(?,?,?,'running',0,?,?)",
                (tid, sid, job["name"], ts, ts),
            )
            await conn.execute(
                "INSERT INTO schedule_occurrences(id,job_id,revision,source,scheduled_for,coalesced_through,manual_key,fingerprint,prompt,context,session_id,task_id,status,created_at,updated_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,'pending',?,?)",
                (
                    oid,
                    job_id,
                    job["revision"],
                    source,
                    scheduled_for,
                    coalesced_through,
                    manual_key,
                    fingerprint,
                    prompt,
                    job["context"],
                    sid,
                    tid,
                    ts,
                    ts,
                ),
            )
            await conn.execute(
                "UPDATE scheduled_jobs SET active_occurrence_id=?,next_run_at=COALESCE(?,next_run_at) WHERE id=?",
                (oid, next_due, job_id),
            )
            return hydrate(
                await fetch(conn, "SELECT * FROM schedule_occurrences WHERE id=?", (oid,))
            ), True

    async def settle(self, occurrence_id, status, *, expected_run_id, reason=None, result=None):
        ts = now_iso()
        async with self.db.transaction() as conn:
            row = await fetch(
                conn, "SELECT * FROM schedule_occurrences WHERE id=?", (occurrence_id,)
            )
            if not row or row["run_id"] != expected_run_id or row["status"] == "superseded":
                return False
            await conn.execute(
                "UPDATE tasks SET status=?,progress=CASE WHEN ?='completed' THEN 100 ELSE MIN(progress,99) END,result=COALESCE(?,result),error=?,completed_at=? WHERE id=?",
                (
                    "failed" if status == "blocked" else status,
                    status,
                    result,
                    reason,
                    ts,
                    row["task_id"],
                ),
            )
            await conn.execute(
                "UPDATE schedule_occurrences SET status=?,reason=?,updated_at=?,completed_at=? WHERE id=?",
                (status, reason, ts, ts if status != "running" else None, occurrence_id),
            )
            if status == "completed":
                await conn.execute(
                    "UPDATE scheduled_jobs SET active_occurrence_id=NULL,last_run_at=? WHERE id=? AND active_occurrence_id=?",
                    (ts, row["job_id"], occurrence_id),
                )
            elif status not in {"pending", "running"}:
                await conn.execute(
                    "UPDATE scheduled_jobs SET enabled=0,binding_state=?,binding_reason=?,last_run_at=? WHERE id=? AND active_occurrence_id=?",
                    (
                        "blocked" if status == "blocked" else "requires_review",
                        reason or status,
                        ts,
                        row["job_id"],
                        occurrence_id,
                    ),
                )

    async def pending(self):
        return [
            hydrate(row)
            for row in await self.db.fetchall(
                "SELECT o.* FROM schedule_occurrences o JOIN scheduled_jobs j ON j.active_occurrence_id=o.id"
            )
        ]
