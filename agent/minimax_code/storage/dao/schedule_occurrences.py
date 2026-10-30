"""Transactional occurrence identity, isolated conversation and whole-job lease."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid

from ._base import loads_json, now_iso

logger = logging.getLogger(__name__)


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

    async def list(self, job_id=None, limit=100, before=None, status_group=None):
        clauses, args = [], []
        groups = {
            "attention": ("blocked", "awaiting_approval", "waiting_input", "budget_paused"),
            "failed": ("failed", "interrupted", "cancelled"),
            "completed": ("completed",),
            "active": ("pending", "queued", "planning", "running"),
        }
        if status_group:
            # Match the same status projection as the UI: blocked takes precedence,
            # otherwise use the task's current run status when available.
            status = "CASE WHEN status='blocked' THEN status ELSE COALESCE((SELECT r.status FROM tasks t JOIN agent_runs r ON r.id=t.run_id WHERE t.id=schedule_occurrences.task_id), status) END"
            values = groups.get(status_group)
            if status_group == "other":
                values = tuple(value for group in groups.values() for value in group)
                operator = "NOT IN"
            elif values:
                operator = "IN"
            else:
                raise ValueError("Invalid occurrence status group")
            clauses.append(f"({status}) {operator} ({','.join('?' for _ in values)})")
            args.extend(values)
        if job_id:
            clauses.append("job_id=?")
            args.append(job_id)
        if before:
            clauses.append("(created_at < ? OR (created_at = ? AND id < ?))")
            args.extend((before["created_at"], before["created_at"], before["id"]))
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        args.append(limit)
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

    async def find_manual_receipt(self, *, job_id, manual_key, expected_revision, expected_service_id):
        """Read an exact admission receipt without scheduling or changing the occurrence."""
        fingerprint = hashlib.sha256(
            json.dumps({"expected_revision": expected_revision}, sort_keys=True).encode()
        ).hexdigest()
        async with self.db.transaction() as conn:
            from .scheduled_jobs import ScheduledJobsDAO
            await ScheduledJobsDAO._check_service(conn, expected_service_id)
            row = await fetch(conn,
                "SELECT * FROM schedule_occurrences WHERE job_id=? AND manual_key=? AND source='manual'",
                (job_id, manual_key))
            if row is None:
                return None
            if row["fingerprint"] != fingerprint:
                raise ValueError("Idempotency key conflicts with the original revision")
            return hydrate(row)

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
        expected_service_id=None,
    ):
        fingerprint = hashlib.sha256(
            json.dumps({"expected_revision": expected_revision}, sort_keys=True).encode()
        ).hexdigest()
        async with self.db.transaction() as conn:
            if expected_service_id is not None:
                from .scheduled_jobs import ScheduledJobsDAO
                await ScheduledJobsDAO._check_service(conn, expected_service_id)
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

            # Notification indexing is auxiliary: a failure must not roll back
            # the actual occurrence outcome. Stable ids prevent reconciliation
            # from creating duplicate notifications for the same terminal state.
            entry = None
            if status not in {"pending", "running"}:
                notification_id = "ntf_sched_" + hashlib.sha256(
                    f"{occurrence_id}:{status}".encode()
                ).hexdigest()[:32]
                title, guidance = {
                    "completed": ("定时任务执行已结束", "执行结束不代表验收通过，请在历史中查看验收证据。"),
                    "failed": ("定时任务执行失败", "请在历史中查看失败原因和运行结果，处理后再恢复。"),
                    "blocked": ("定时任务无法启动", "请在历史中查看阻塞原因，修复配置后再恢复。"),
                    "requires_review": ("定时任务需要复核", "请在历史中检查运行结果和验收证据后再恢复。"),
                    "budget_paused": ("定时任务因预算暂停", "请在历史中检查预算和运行结果后再恢复。"),
                    "interrupted": ("定时任务已中断", "请在历史中检查已产生的影响后再恢复。"),
                    "cancelled": ("定时任务已取消", "请在历史中检查已产生的影响后再恢复。"),
                }.get(status, ("定时任务需要关注", "请在历史中查看运行状态和结果。"))
                kind = "info" if status == "completed" else "warning"
                job = await fetch(conn, "SELECT name FROM scheduled_jobs WHERE id=?", (row["job_id"],))
                body = f"{job['name'] if job else '定时任务'} · {guidance}"
                await conn.execute("SAVEPOINT schedule_notification")
                try:
                    cursor = await conn.execute(
                        "INSERT OR IGNORE INTO notifications (id,type,title,body,source,source_id,priority,read,created_at,read_at) VALUES (?,?,?,?,?,?,?,0,?,NULL)",
                        (notification_id, kind, title, body, "schedule",
                         json.dumps({"job_id": row["job_id"], "occurrence_id": occurrence_id}), 0, ts),
                    )
                    if cursor.rowcount:
                        entry = dict(await fetch(conn, "SELECT * FROM notifications WHERE id=?", (notification_id,)))
                        entry["read"] = False
                    await conn.execute("RELEASE SAVEPOINT schedule_notification")
                except Exception:
                    await conn.execute("ROLLBACK TO SAVEPOINT schedule_notification")
                    await conn.execute("RELEASE SAVEPOINT schedule_notification")
                    logger.exception("Could not record scheduled occurrence notification")
            return entry

    async def pending(self):
        return [
            hydrate(row)
            for row in await self.db.fetchall(
                "SELECT o.* FROM schedule_occurrences o JOIN scheduled_jobs j ON j.active_occurrence_id=o.id"
            )
        ]
