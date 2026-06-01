"""E2E integration smoke for the Phase 2b modules.

Black-box subprocess test that runs ALL of the new modules
end-to-end in two phases (initial spawn + restart) and verifies
persistence across the restart:

Phase 1 — first process
  1.  status (liveness)
  2.  agent.create   { name: "smoke-agent", system_prompt: "smoke test" }
  3.  session.list   -> grab a real session_id -> session.archive
  4.  mobile.pair_start -> grab a token
  5.  mobile.pair_confirm -> pair smoke-dev-001
  6.  permission.set { tool_pattern: "exec_command", action: "deny" }
  7.  agent.invoke   { name: "smoke-agent", request: "hello" }
  8.  task.list      -> see the sub-agent's task
  9.  schedule.create { name: "smoke-job", cron_expr: "*/5 * * * *" }
  10. shutdown

Phase 2 — restart with the SAME data dir
  11. status (liveness)
  12. session.list  { archived: true }  -> find the archived row
  13. mobile.list                    -> find smoke-dev-001
  14. permission.check { tool_name: "exec_command" } -> denied
  15. agent.list                     -> find smoke-agent
  16. schedule.list                  -> find smoke-job
  17. shutdown

Setup (before any spawn): pre-seed a ``sessions`` row in the
fresh data dir so step 3 has something to archive. The agent
subprocess resolves the DB path via ``default_database_path()``
which honours ``MINIMAX_CODE_DATA_DIR`` (mirrors the env-var
override used by :mod:`minimax_code.storage.db`).

Usage
-----
``python tests/e2e/smoke_phase2b.py <python> <agent_dir> <workdir>``

Exit 0 = pass, 1 = fail.

Notes
-----
* The subprocess MUST be spawned with ``cwd=agent_dir`` —
  otherwise the agent's relative ``.env`` paths resolve to
  two different files (same trap as the mobile + sessions
  smokes).
* ``MINIMAX_CODE_DATA_DIR`` MUST be set to a fresh temp dir
  so the test doesn't touch the user's real SQLite store.
"""

from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path


# ---------------------------------------------------------------------------
# Setup — pre-warm migration, then insert a sessions row
# ---------------------------------------------------------------------------


async def _warm_migration(
    env: dict, agent_dir: str, python: str, db_path: Path
) -> str | None:
    """Spawn the agent briefly so it runs migrations on a fresh DB.

    Why: the agent's storage layer runs the full DDL on first
    boot via :mod:`minimax_code.storage.migrations`. We can't
    apply a partial DDL inline (it would conflict with the
    agent's migration — ``table sessions already exists``).
    Cheapest workaround: spawn → let ``init_runtime`` run →
    shutdown → then INSERT a session row directly via stdlib
    ``sqlite3``.

    Returns the agent's stderr if migration succeeded, else ``None``.
    """
    if db_path.exists():
        # An existing DB at this path is from a prior run; trust
        # that the schema is already there. Don't re-migrate.
        return "reuse-existing-db"
    proc = await _spawn(env, agent_dir, python)
    # Wait for init_runtime to finish — agent is ready once
    # ``status`` responds. Then ping ``session.list`` to force
    # the sessions DAO to be built (its ``migrate`` runs in the
    # factory inside ``session.list`` if not already done).
    deadline = time.time() + 6.0
    booted = False
    sessions_ready = False
    while time.time() < deadline:
        r, _ = await _call(proc, "status", timeout=2.0)
        if "result" in r:
            booted = True
            break
        await asyncio.sleep(0.2)
    # Once status is up, ping session.list so the storage layer
    # actually opens the DB and runs migrations end-to-end.
    if booted:
        r, _ = await _call(proc, "session.list", timeout=4.0)
        if "result" in r:
            sessions_ready = True
    # A bit of extra slack so the WAL is flushed to the main file
    # before the next subprocess tries to read it via plain
    # sqlite3 (which doesn't share the agent's WAL).
    await asyncio.sleep(0.3)
    await _shutdown(proc)
    # Belt-and-suspenders: confirm the file exists.
    if not db_path.exists():
        return f"boot-ok-but-no-db-at-{db_path}"
    return "ok" if (booted and sessions_ready) else "partial-boot"


def _insert_session_row(db_path: Path, session_id: str) -> None:
    """Append one row to the agent's ``sessions`` table.

    Assumes the table already exists (the warm-up spawn ran
    the migration).
    """
    conn = sqlite3.connect(str(db_path))
    try:
        # The agent's DDL is the source of truth — read the
        # column list from the schema so we don't drift if the
        # migration adds / removes a column.
        cur = conn.execute("PRAGMA table_info(sessions)")
        cols = [row[1] for row in cur.fetchall()]
        if not cols:
            raise RuntimeError("sessions table not present after warm-up")
        # Build an INSERT that uses the canonical columns and
        # NULL for anything else.
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        values: dict[str, object] = {
            "id": session_id,
            "title": "phase2b smoke seed",
            "created_at": now,
            "updated_at": now,
            "archived": 0,
        }
        # Model + system_prompt are nullable, leave them NULL.
        col_list = ", ".join(values.keys())
        placeholders = ", ".join(["?"] * len(values))
        conn.execute(
            f"INSERT INTO sessions ({col_list}) VALUES ({placeholders})",
            list(values.values()),
        )
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Helpers — IPC plumbing
# ---------------------------------------------------------------------------


class _StepLog:
    """One-line step log used to build the final summary table."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str]] = []  # (phase, step, status)

    def add(self, phase: str, step: str, status: str) -> None:
        self.rows.append((phase, step, status))
        print(f"  [{phase:<5}] step {step:>2}: {status}")


async def _call(
    proc: asyncio.subprocess.Process,
    method: str,
    params: dict | None = None,
    timeout: float = 8.0,
) -> tuple[dict, list[dict]]:
    """Send a request, return (response, streamed_events).

    The agent writes one JSON line per message (response or
    event) to stdout. We drain until we see the matching
    response id; any ``event:`` lines we collect in between
    are returned as a side channel.
    """
    req_id = f"req-{method}-{time.time_ns()}"
    req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
    assert proc.stdin is not None
    proc.stdin.write((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
    await proc.stdin.drain()

    events: list[dict] = []
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
            obj = json.loads(raw.decode("utf-8", errors="replace").strip())
        except json.JSONDecodeError:
            continue
        if obj.get("id") == req_id:
            return obj, events
        if "event" in obj:
            events.append(obj)
    return ({"error": {"code": -32000, "message": f"timeout waiting for {method}"}}, events)


async def _spawn(env: dict, agent_dir: str, python: str) -> asyncio.subprocess.Process:
    proc = await asyncio.create_subprocess_exec(
        python, "-m", "minimax_code",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=agent_dir,
        env=env,
    )
    await asyncio.sleep(0.6)  # boot
    return proc


async def _drain_stderr(proc: asyncio.subprocess.Process, log_path: Path) -> list[str]:
    """Drain stderr in the background and tee to a file.

    Returns the collected lines so the caller can dump them on
    failure.
    """
    out: list[str] = []
    log_fp = log_path.open("w", encoding="utf-8")

    async def _drain() -> None:
        assert proc.stderr is not None
        while True:
            raw = await proc.stderr.readline()
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").rstrip()
            out.append(line)
            log_fp.write(line + "\n")
            log_fp.flush()

    task = asyncio.create_task(_drain())
    return out, task, log_fp


async def _shutdown(proc: asyncio.subprocess.Process) -> None:
    """Best-effort shutdown: send the IPC shutdown then wait/kill."""
    try:
        await _call(proc, "shutdown", timeout=2.0)
    except Exception:
        pass
    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            await proc.wait()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


async def main(python: str, agent_dir: str, workdir: str) -> int:
    # CRITICAL: MINIMAX_CODE_DATA_DIR must be absolute. The
    # agent subprocess runs with ``cwd=agent_dir`` and the env
    # override is resolved as a relative path against the
    # subprocess cwd, not the smoke's own cwd — so a relative
    # path would seed one DB and read another.
    workdir_path = Path(workdir).resolve()
    workdir_path.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(agent_dir).resolve())
    env["MINIMAX_CODE_LOG_LEVEL"] = "WARNING"
    env["MINIMAX_CODE_DATA_DIR"] = str(workdir_path)  # agent uses workdir/data.db

    # Pre-warm: spawn the agent briefly so it runs the storage
    # migration on a fresh DB. Then insert one session row
    # directly (no IPC to do this — the session.* namespace
    # has no ``create`` method by design).
    db_path = workdir_path / "data.db"
    seed_session_id = f"ses_phase2b_{uuid.uuid4().hex[:8]}"
    try:
        warm_result = await _warm_migration(env, agent_dir, python, db_path)
        print(f"[setup] warm-up: {warm_result} (db={db_path})")
        _insert_session_row(db_path, seed_session_id)
        print(f"[setup] seeded session {seed_session_id} into {db_path}")
    except Exception as exc:
        print(f"[setup] WARNING: pre-seed failed: {exc} (step 3 will likely fail)")

    log = _StepLog()
    failures: list[str] = []

    def _fail(step: str, msg: str) -> None:
        failures.append(f"step {step}: {msg}")

    # ======================================================================
    # PHASE 1
    # ======================================================================
    print("\n=== PHASE 1: create + archive + pair + permission + invoke + schedule ===")
    proc1 = await _spawn(env, agent_dir, python)
    stderr_lines, stderr_task, stderr_fp = await _drain_stderr(
        proc1, workdir_path / "smoke_phase2b.phase1.stderr.log"
    )

    # ---- step 1: status ----
    r, _ = await _call(proc1, "status")
    if "error" in r:
        _fail("1", f"status failed: {r['error']}")
        log.add("P1", "1", f"FAIL ({r['error'].get('message')})")
    else:
        log.add("P1", "1", "ok")

    # ---- step 2: agent.create ----
    r, _ = await _call(proc1, "agent.create", {
        "name": "smoke-agent",
        "system_prompt": "smoke test",
    })
    if "error" in r:
        _fail("2", f"agent.create failed: {r['error']}")
        log.add("P1", "2", f"FAIL ({r['error'].get('message')})")
    else:
        log.add("P1", "2", f"ok id={r['result']['agent'].get('id', '?')[:14]}")

    # ---- step 3: session.list -> session.archive ----
    r, _ = await _call(proc1, "session.list")
    if "error" in r:
        _fail("3", f"session.list failed: {r['error']}")
        log.add("P1", "3", f"FAIL ({r['error'].get('message')})")
    else:
        sessions = r["result"].get("sessions", [])
        if not sessions:
            _fail("3", "session.list returned 0 sessions (pre-seed missing?)")
            log.add("P1", "3", "FAIL (no sessions)")
        else:
            archived_id = sessions[0]["id"]
            r2, _ = await _call(proc1, "session.archive", {"session_id": archived_id})
            if "error" in r2:
                _fail("3", f"session.archive failed: {r2['error']}")
                log.add("P1", "3", f"FAIL (archive: {r2['error'].get('message')})")
            else:
                log.add("P1", "3", f"ok archived id={archived_id[:18]}")

    # ---- step 4: mobile.pair_start ----
    r, _ = await _call(proc1, "mobile.pair_start", {"suggested_name": "smoke-phone"})
    if "error" in r:
        _fail("4", f"mobile.pair_start failed: {r['error']}")
        log.add("P1", "4", f"FAIL ({r['error'].get('message')})")
        pair_token = None
    else:
        pair_token = r["result"].get("token")
        log.add("P1", "4", f"ok token={pair_token[:12] if pair_token else 'NONE'}...")

    # ---- step 5: mobile.pair_confirm ----
    if pair_token:
        r, _ = await _call(proc1, "mobile.pair_confirm", {
            "token": pair_token,
            "device_id": "smoke-dev-001",
            "name": "test",
            "public_key": "k",
        })
        if "error" in r:
            _fail("5", f"mobile.pair_confirm failed: {r['error']}")
            log.add("P1", "5", f"FAIL ({r['error'].get('message')})")
        else:
            log.add("P1", "5", "ok")
    else:
        _fail("5", "skipped (no token from step 4)")
        log.add("P1", "5", "FAIL (no token)")

    # ---- step 6: permission.set ----
    r, _ = await _call(proc1, "permission.set", {
        "tool_pattern": "exec_command",
        "action": "deny",
    })
    if "error" in r:
        _fail("6", f"permission.set failed: {r['error']}")
        log.add("P1", "6", f"FAIL ({r['error'].get('message')})")
    else:
        log.add("P1", "6", f"ok action={r['result']['rule'].get('action')!r}")

    # ---- step 7: agent.invoke ----
    r, events = await _call(
        proc1, "agent.invoke",
        {"name": "smoke-agent", "request": "hello"},
        timeout=10.0,
    )
    if "error" in r:
        _fail("7", f"agent.invoke failed: {r['error']}")
        log.add("P1", "7", f"FAIL ({r['error'].get('message')})")
        sub_task_id = None
    else:
        text = r["result"].get("text", "")
        sub_task_id = r["result"].get("task_id")
        streamed_agent_events = [e["event"] for e in events if e.get("event", "").startswith("agent.")]
        if "stub: agent smoke-agent" not in text:
            _fail("7", f"agent.invoke: unexpected text {text!r}")
            log.add("P1", "7", f"FAIL (text: {text[:40]!r})")
        elif not streamed_agent_events:
            _fail("7", f"agent.invoke: no agent.* events streamed (events={events[:3]})")
            log.add("P1", "7", "FAIL (no events)")
        else:
            log.add("P1", "7", f"ok task_id={sub_task_id} events={streamed_agent_events[:3]}")

    # ---- step 8: task.list ----
    r, _ = await _call(proc1, "task.list")
    if "error" in r:
        _fail("8", f"task.list failed: {r['error']}")
        log.add("P1", "8", f"FAIL ({r['error'].get('message')})")
    else:
        tasks = r["result"].get("tasks", [])
        # The sub-agent invoke creates a task with title
        # "subagent:smoke-agent".
        sub = [t for t in tasks if (t.get("title") or "").startswith("subagent:smoke-agent")]
        if not sub:
            _fail("8", f"task.list: no subagent task found (titles={[t.get('title') for t in tasks]})")
            log.add("P1", "8", f"FAIL (no subagent task; {len(tasks)} total)")
        else:
            log.add("P1", "8", f"ok subagent task status={sub[0].get('status')!r} progress={sub[0].get('progress')}")

    # ---- step 9: schedule.create ----
    r, _ = await _call(proc1, "schedule.create", {
        "name": "smoke-job",
        "cron_expr": "*/5 * * * *",
    })
    if "error" in r:
        _fail("9", f"schedule.create failed: {r['error']}")
        log.add("P1", "9", f"FAIL ({r['error'].get('message')})")
        smoke_job_id = None
    else:
        smoke_job_id = r["result"].get("job", {}).get("id")
        log.add("P1", "9", f"ok job_id={smoke_job_id}")

    # ---- step 10: shutdown ----
    await _shutdown(proc1)
    try:
        await asyncio.wait_for(stderr_task, timeout=1.0)
    except asyncio.TimeoutError:
        stderr_task.cancel()
    stderr_fp.close()
    log.add("P1", "10", "shutdown")

    # ======================================================================
    # PHASE 2 — restart with same data dir
    # ======================================================================
    print("\n=== PHASE 2: restart, verify persistence ===")
    proc2 = await _spawn(env, agent_dir, python)
    stderr_lines2, stderr_task2, stderr_fp2 = await _drain_stderr(
        proc2, workdir_path / "smoke_phase2b.phase2.stderr.log"
    )

    # ---- step 11: status ----
    r, _ = await _call(proc2, "status")
    if "error" in r:
        _fail("11", f"status failed: {r['error']}")
        log.add("P2", "11", f"FAIL ({r['error'].get('message')})")
    else:
        log.add("P2", "11", "ok")

    # ---- step 12: session.list { archived: true } ----
    r, _ = await _call(proc2, "session.list", {"archived": True})
    if "error" in r:
        _fail("12", f"session.list(archived) failed: {r['error']}")
        log.add("P2", "12", f"FAIL ({r['error'].get('message')})")
    else:
        archived = r["result"].get("sessions", [])
        if not any(s.get("id") == seed_session_id for s in archived):
            _fail(
                "12",
                f"session.list(archived): {seed_session_id} not found among "
                f"{[s.get('id') for s in archived]}",
            )
            log.add("P2", "12", f"FAIL ({len(archived)} archived, seed missing)")
        else:
            log.add("P2", "12", f"ok found {seed_session_id[:18]} in {len(archived)} archived")

    # ---- step 13: mobile.list ----
    r, _ = await _call(proc2, "mobile.list")
    if "error" in r:
        _fail("13", f"mobile.list failed: {r['error']}")
        log.add("P2", "13", f"FAIL ({r['error'].get('message')})")
    else:
        devs = r["result"].get("devices", [])
        if not any(d.get("device_id") == "smoke-dev-001" for d in devs):
            _fail("13", f"mobile.list: smoke-dev-001 not in {[d.get('device_id') for d in devs]}")
            log.add("P2", "13", f"FAIL ({len(devs)} devices, smoke-dev-001 missing)")
        else:
            log.add("P2", "13", f"ok found smoke-dev-001 in {len(devs)} devices")

    # ---- step 14: permission.check ----
    r, _ = await _call(proc2, "permission.check", {"tool_name": "exec_command"})
    if "error" in r:
        _fail("14", f"permission.check failed: {r['error']}")
        log.add("P2", "14", f"FAIL ({r['error'].get('message')})")
    else:
        allowed = r["result"].get("allowed")
        action = r["result"].get("action")
        if allowed is not False or action != "deny":
            _fail(
                "14",
                f"permission.check: expected allowed=False action='deny', "
                f"got allowed={allowed!r} action={action!r}",
            )
            log.add("P2", "14", f"FAIL (allowed={allowed} action={action!r})")
        else:
            log.add("P2", "14", f"ok allowed=False action={action!r}")

    # ---- step 15: agent.list ----
    r, _ = await _call(proc2, "agent.list")
    if "error" in r:
        _fail("15", f"agent.list failed: {r['error']}")
        log.add("P2", "15", f"FAIL ({r['error'].get('message')})")
    else:
        agents = r["result"].get("agents", [])
        if not any(a.get("name") == "smoke-agent" for a in agents):
            _fail("15", f"agent.list: smoke-agent not in {[a.get('name') for a in agents]}")
            log.add("P2", "15", f"FAIL ({len(agents)} agents, smoke-agent missing)")
        else:
            log.add("P2", "15", f"ok found smoke-agent in {len(agents)} agents")

    # ---- step 16: schedule.list ----
    r, _ = await _call(proc2, "schedule.list")
    if "error" in r:
        _fail("16", f"schedule.list failed: {r['error']}")
        log.add("P2", "16", f"FAIL ({r['error'].get('message')})")
    else:
        jobs = r["result"].get("jobs", [])
        if not any(j.get("name") == "smoke-job" for j in jobs):
            _fail("16", f"schedule.list: smoke-job not in {[j.get('name') for j in jobs]}")
            log.add("P2", "16", f"FAIL ({len(jobs)} jobs, smoke-job missing)")
        else:
            log.add("P2", "16", f"ok found smoke-job in {len(jobs)} jobs")

    # ---- step 17: shutdown ----
    await _shutdown(proc2)
    try:
        await asyncio.wait_for(stderr_task2, timeout=1.0)
    except asyncio.TimeoutError:
        stderr_task2.cancel()
    stderr_fp2.close()
    log.add("P2", "17", "shutdown")

    # ======================================================================
    # Summary
    # ======================================================================
    print("\n=== STEP TABLE ===")
    for phase, step, status in log.rows:
        print(f"  [{phase:<5}] step {step:>2}: {status}")

    print("\n=== SUMMARY ===")
    n_total = len(log.rows)
    n_fail = sum(1 for _, _, s in log.rows if s.startswith("FAIL"))
    print(f"  total steps: {n_total}")
    print(f"  pass:        {n_total - n_fail}")
    print(f"  fail:        {n_fail}")

    if failures:
        print("\n=== FAILURES ===")
        for f in failures:
            print(f"  FAIL: {f}")
        # Dump stderr on failure (both phases).
        if stderr_lines:
            print("\n=== PHASE 1 AGENT STDERR (last 30) ===")
            for line in stderr_lines[-30:]:
                print(f"  {line}")
        if stderr_lines2:
            print("\n=== PHASE 2 AGENT STDERR (last 30) ===")
            for line in stderr_lines2[-30:]:
                print(f"  {line}")
        return 1

    print("\n=== PASS ===")
    return 0


if __name__ == "__main__":
    python = sys.argv[1]
    agent_dir = sys.argv[2]
    workdir = sys.argv[3] if len(sys.argv) > 3 else "."
    Path(workdir).mkdir(parents=True, exist_ok=True)
    sys.exit(asyncio.run(main(python, agent_dir, workdir)))
