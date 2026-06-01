"""E2E smoke test for the schedule.* IPC namespace.

This is a black-box subprocess test. It:

1. Spawns the agent via ``python -m minimax_code``.
2. Calls ``schedule.create`` with a ``* * * * *`` cron (every minute).
3. Calls ``schedule.list`` and verifies the new job is there.
4. Calls ``schedule.run_now`` to trigger an immediate run of the
   job's payload.
5. Polls ``task.list`` until a ``completed`` task row appears (the
   scheduler writes one every time it fires a job).
6. Calls ``schedule.disable`` then ``schedule.delete`` for cleanup.
7. Shuts the agent down cleanly.

Pass criteria
-------------
* Every JSON-RPC call returns without an error envelope.
* ``schedule.list`` shows the new job after ``schedule.create``.
* A completed task row appears within ~3 seconds of ``schedule.run_now``.
* ``schedule.disable`` flips the ``enabled`` flag to ``false``.
* ``schedule.delete`` removes the job from ``schedule.list``.

Usage: ``python tests/e2e/smoke_scheduled.py <python> <agent_dir> [<workdir>]``

Exit code 0 = pass, 1 = fail.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path


async def main(python: str, agent_dir: str, workdir: str) -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = agent_dir
    env["MINIMAX_CODE_LOG_LEVEL"] = "WARNING"

    proc = await asyncio.create_subprocess_exec(
        python, "-m", "minimax_code",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=agent_dir,
        env=env,
    )

    failures: list[str] = []

    async def call(
        method: str, params: dict | None = None, timeout: float = 8.0
    ) -> dict:
        """Send a request and return the raw response envelope.

        The response is matched on ``id`` (we use a unique
        ``time_ns()``-suffixed id for every call). Any event lines
        emitted before the response are silently dropped — we
        don't need them for this smoke test.
        """
        req_id = f"req-{method}-{time.time_ns()}"
        req = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }
        line = json.dumps(req, ensure_ascii=False) + "\n"
        assert proc.stdin is not None
        proc.stdin.write(line.encode("utf-8"))
        await proc.stdin.drain()

        deadline = time.time() + timeout
        assert proc.stdout is not None
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(
                    proc.stdout.readline(),
                    timeout=max(0.05, deadline - time.time()),
                )
            except asyncio.TimeoutError:
                break
            if not raw:
                break
            try:
                obj = json.loads(raw.decode("utf-8").strip())
            except json.JSONDecodeError:
                continue
            if obj.get("id") == req_id:
                return obj
        return {"error": {"code": -32000, "message": f"timeout waiting for {method}"}}

    # 0. Wait for the agent to boot.
    await asyncio.sleep(0.5)

    # 1. liveness — ``status`` is a built-in.
    r = await call("status")
    if "error" in r:
        failures.append(f"status call failed: {r['error']}")
    print("[status]", "ok" if "result" in r else f"FAIL: {r.get('error')}")

    # 2. schedule.create — every-minute cron.
    job_name = f"smoke_{int(time.time())}"
    r = await call(
        "schedule.create",
        {"name": job_name, "cron_expr": "* * * * *", "payload": {"kind": "noop"}},
    )
    if "error" in r:
        failures.append(f"schedule.create failed: {r['error']}")
        print("[schedule.create] FAIL", r)
        return _finish(proc, failures)
    job = r["result"]["job"]
    job_id = job["id"]
    print(f"[schedule.create] ok -> {job_id} name={job_name!r}")
    if not job_id.startswith("job_"):
        failures.append(f"schedule.create: job id should start with 'job_', got {job_id!r}")
    if job.get("next_run_at") is None:
        failures.append("schedule.create: next_run_at should be set on a valid cron")

    # 3. schedule.list — verify the job is there.
    r = await call("schedule.list", {})
    if "error" in r:
        failures.append(f"schedule.list failed: {r['error']}")
    else:
        ids = [j["id"] for j in r["result"]["jobs"]]
        if job_id not in ids:
            failures.append(f"schedule.list: job {job_id} not in {[j['id'] for j in r['result']['jobs']]}")
        else:
            print(f"[schedule.list] ok, {len(ids)} job(s)")

    # 4. schedule.run_now — kick the payload.
    r = await call("schedule.run_now", {"job_id": job_id})
    if "error" in r:
        failures.append(f"schedule.run_now failed: {r['error']}")
        print("[schedule.run_now] FAIL", r)
    else:
        ack = r["result"]
        print(
            f"[schedule.run_now] ok, ok={ack.get('ok')} "
            f"triggered_at={ack.get('triggered_at')!r}"
        )
        if not ack.get("ok"):
            failures.append("schedule.run_now: ack.ok should be true")

    # 5. Wait up to 5 seconds for the fire to land in the tasks table.
    completed = None
    deadline = time.time() + 5.0
    while time.time() < deadline:
        r = await call("task.list", {})
        if "result" in r:
            tasks = r["result"].get("tasks", [])
            for t in tasks:
                # The scheduler names the task "[manual] <name>" so we
                # match by that. Status must be 'completed'.
                if (
                    t.get("title", "").endswith(f"{job_name}")
                    and t.get("status") == "completed"
                ):
                    completed = t
                    break
        if completed:
            break
        await asyncio.sleep(0.2)

    if completed is None:
        failures.append(
            f"expected a task row with status='completed' for {job_name!r} "
            "to appear within 5s of schedule.run_now"
        )
    else:
        print(
            f"[task.list] ok, found completed task {completed['id']!r} "
            f"title={completed['title']!r} progress={completed['progress']}"
        )
        if completed.get("progress") != 100:
            failures.append(
                f"task row progress should be 100, got {completed.get('progress')!r}"
            )

    # 6. schedule.disable.
    r = await call("schedule.disable", {"job_id": job_id})
    if "error" in r:
        failures.append(f"schedule.disable failed: {r['error']}")
    else:
        disabled = r["result"]["job"]
        print(f"[schedule.disable] ok, enabled={disabled['enabled']!r}")
        if disabled["enabled"] is not False:
            failures.append(
                f"schedule.disable: expected enabled=false, got {disabled['enabled']!r}"
            )

    # 7. schedule.delete.
    r = await call("schedule.delete", {"job_id": job_id})
    if "error" in r:
        failures.append(f"schedule.delete failed: {r['error']}")
    else:
        print(f"[schedule.delete] ok, response={r['result']}")
        if r["result"].get("ok") is not True:
            failures.append(
                f"schedule.delete: expected ok=true, got {r['result']!r}"
            )

    # 8. schedule.list — the deleted job is gone.
    r = await call("schedule.list", {})
    if "error" in r:
        failures.append(f"final schedule.list failed: {r['error']}")
    else:
        ids = [j["id"] for j in r["result"]["jobs"]]
        if job_id in ids:
            failures.append(
                f"schedule.list after delete: job {job_id} still present"
            )
        else:
            print(f"[schedule.list after delete] ok, {len(ids)} job(s)")

    # 9. shutdown.
    r = await call("shutdown")
    if "error" in r:
        failures.append(f"shutdown failed: {r['error']}")
    print("[shutdown]", "ok" if "result" in r else f"FAIL: {r.get('error')}")

    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()

    print("\n=== SUMMARY ===")
    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    print("  PASS: schedule.create / list / run_now / disable / delete all round-tripped")
    print("  PASS: a completed task row appeared after run_now")
    return 0


def _finish(proc: asyncio.subprocess.Process, failures: list[str]) -> int:
    """Cleanup helper used when we've already bailed out."""
    try:
        if proc.returncode is None:
            proc.kill()
            try:
                # We can't await here because this helper is sync;
                # the caller still controls the loop and will reap
                # the process via ``wait()``.
                pass
            except Exception:
                pass
    except Exception:
        pass
    print("\n=== SUMMARY ===")
    for f in failures:
        print(f"  FAIL: {f}")
    return 1


if __name__ == "__main__":
    python = sys.argv[1]
    agent_dir = sys.argv[2]
    workdir = sys.argv[3] if len(sys.argv) > 3 else "."
    Path(workdir).mkdir(parents=True, exist_ok=True)
    sys.exit(asyncio.run(main(python, agent_dir, workdir)))
