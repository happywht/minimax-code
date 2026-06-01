"""E2E smoke test for the progress-tracking IPC pipeline.

This is a black-box subprocess test. It:

0. Pre-seeds a ``sessions`` row in the agent's SQLite file
   (the tasks table has an FK to ``sessions``; we need a
   valid parent row before any ``task.*`` call will succeed).
1. Spawns the agent via ``python -m minimax_code``.
2. Calls ``task.start`` to create a task (should emit an
   ``agent.status`` ``started`` event in the stream).
3. Calls ``task.update`` three times to push progress (each
   should emit a ``progress`` event).
4. Calls ``task.complete`` to close the task (should emit a
   ``completed`` event).
5. Calls ``task.list`` and verifies the new task is present
   with the right status.
6. Calls ``task.get`` to retrieve the full row.
7. Calls ``task.cancel`` against a freshly-started task to
   verify the soft-cancel path.
8. Shuts the agent down cleanly.

Pass criteria
-------------
* Every JSON-RPC call returns without an error envelope.
* The streaming events for each lifecycle call include an
  ``agent.status`` event with the expected ``status`` value.
* The persisted task row reflects every state change we made.

Usage: ``python tests/e2e/smoke_progress.py <python> <agent_dir>``

Exit code 0 = pass, 1 = fail.
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
# Driver
# ---------------------------------------------------------------------------


async def main(python: str, agent_dir: str, workdir: str) -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = agent_dir
    env["MINIMAX_CODE_LOG_LEVEL"] = "WARNING"

    # 0. Pre-seed a sessions row in the agent's SQLite file so the
    # FK from tasks -> sessions is satisfied. We resolve the
    # data dir the same way the agent does (platformdirs) and
    # then insert a single row using stdlib sqlite3.
    session_id = f"ses_smoke_{uuid.uuid4().hex[:8]}"
    try:
        data_dir = _resolve_data_dir(python, agent_dir)
        data_dir.mkdir(parents=True, exist_ok=True)
        db_path = data_dir / "data.db"
        _seed_session(db_path, session_id)
        print(f"[setup] seeded session {session_id} into {db_path}")
    except Exception as exc:
        # Don't fail hard on the seed — print and let the task
        # calls fail with a clear FK error if the seed is broken.
        print(f"[setup] WARNING: could not pre-seed session: {exc}")

    proc = await asyncio.create_subprocess_exec(
        python, "-m", "minimax_code",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=agent_dir,
        env=env,
    )

    # Open a file to capture stderr to disk — easier to inspect
    # post-mortem than piping through the event loop.
    stderr_file = Path(workdir) / "smoke_progress.stderr.log"
    stderr_fp = stderr_file.open("w", encoding="utf-8")

    # Sequence of {event, status} pairs we expect to see, in order.
    expected_events: list[tuple[str, str]] = []
    actual_events: list[dict] = []
    # Track call state for diagnostics.
    failures: list[str] = []

    async def call(method: str, params: dict | None = None, timeout: float = 8.0) -> tuple[dict, list[dict]]:
        """Send a request and return (response, events_emitted_before_response).

        Events collected here are *any* ``event:`` lines that arrived
        between when we wrote the request and when we saw the
        matching response.
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
                obj = json.loads(raw.decode("utf-8").strip())
            except json.JSONDecodeError:
                continue
            if obj.get("id") == req_id:
                return obj, events
            if "event" in obj:
                events.append(obj)
        return (
            {"error": {"code": -32000, "message": f"timeout waiting for {method}"}},
            events,
        )

    # 0. Wait for the agent to boot.
    await asyncio.sleep(0.5)

    # Read & stash stderr in the background so we can dump it on
    # failure. The agent writes its log to stderr (logging_setup
    # configures it that way); without this, a failure deep inside
    # the agent is invisible to the smoke test.
    stderr_lines: list[str] = []

    async def _drain_stderr() -> None:
        assert proc.stderr is not None
        while True:
            try:
                raw = await proc.stderr.readline()
            except Exception:
                return
            if not raw:
                return
            line = raw.decode("utf-8", errors="replace").rstrip()
            stderr_lines.append(line)
            stderr_fp.write(line + "\n")
            stderr_fp.flush()

    stderr_task = asyncio.create_task(_drain_stderr())

    # 1. liveness — ``status`` is a built-in.
    r, _ = await call("status")
    if "error" in r:
        failures.append(f"status call failed: {r['error']}")
    print("[status]", "ok" if "result" in r else f"FAIL: {r.get('error')}")

    # 2. task.start — create the task and capture the task_id.
    session_id = "ses_smoke_progress"
    r, events = await call("task.start", {
        "session_id": session_id,
        "title": "smoke progress task",
    })
    if "error" in r:
        failures.append(f"task.start failed: {r['error']}")
        print("[task.start] FAIL", r)
        return _finish(proc, failures)
    task_id = r["result"]["task_id"]
    actual_events.extend(events)
    print(f"[task.start] ok -> {task_id}")
    if not _saw_status(events, "started"):
        failures.append("task.start: missing agent.status{status='started'} event")

    # 3. task.update x3 — push progress 25, 50, 75.
    for p in (25, 50, 75):
        r, events = await call("task.update", {
            "task_id": task_id,
            "progress": p,
            "message": f"p={p}",
        })
        if "error" in r:
            failures.append(f"task.update({p}) failed: {r['error']}")
            continue
        actual_events.extend(events)
        if not _saw_status(events, "progress"):
            failures.append(f"task.update({p}): missing agent.status{{status='progress'}} event")
        print(f"[task.update {p}] ok")

    # 4. task.complete — close the task.
    r, events = await call("task.complete", {"task_id": task_id})
    if "error" in r:
        failures.append(f"task.complete failed: {r['error']}")
    actual_events.extend(events)
    if not _saw_status(events, "completed"):
        failures.append("task.complete: missing agent.status{status='completed'} event")
    print(f"[task.complete] ok")

    # 5. task.list — confirm the row is there.
    r, _ = await call("task.list", {"session_id": session_id})
    if "error" in r:
        failures.append(f"task.list failed: {r['error']}")
        print("[task.list] FAIL", r)
    else:
        tasks = r["result"]["tasks"]
        match = [t for t in tasks if t["id"] == task_id]
        if not match:
            failures.append(f"task.list: task {task_id} not found in {len(tasks)} returned")
            print(f"[task.list] FAIL: not found among {len(tasks)} tasks")
        else:
            row = match[0]
            print(f"[task.list] ok, found task status={row['status']} progress={row['progress']}")
            if row["status"] != "completed":
                failures.append(f"task.list: expected status='completed', got {row['status']!r}")
            if row["progress"] != 100:
                failures.append(f"task.list: expected progress=100, got {row['progress']!r}")

    # 6. task.get — full row.
    r, _ = await call("task.get", {"task_id": task_id})
    if "error" in r:
        failures.append(f"task.get failed: {r['error']}")
    else:
        row = r["result"]["task"]
        print(f"[task.get] ok, title={row['title']!r} status={row['status']!r} progress={row['progress']}")
        if row["title"] != "smoke progress task":
            failures.append(f"task.get: wrong title {row['title']!r}")

    # 7. task.cancel — start a fresh task, then cancel it.
    r, events = await call("task.start", {
        "session_id": session_id,
        "title": "to-cancel",
    })
    if "error" in r:
        failures.append(f"task.start (for cancel) failed: {r['error']}")
    else:
        cancel_tid = r["result"]["task_id"]
        actual_events.extend(events)
        # Push one progress event so the row is mid-flight.
        await call("task.update", {"task_id": cancel_tid, "progress": 30})
        r, events = await call("task.cancel", {"task_id": cancel_tid})
        if "error" in r:
            failures.append(f"task.cancel failed: {r['error']}")
        else:
            actual_events.extend(events)
            if not _saw_status(events, "cancelled"):
                failures.append("task.cancel: missing agent.status{status='cancelled'} event")
            print(f"[task.cancel] ok, status={r['result']['task']['status']!r}")
            if r["result"]["task"]["status"] != "cancelled":
                failures.append(f"task.cancel: expected status='cancelled', got {r['result']['task']['status']!r}")

    # 8. task.list with status filter — should find completed + cancelled.
    r, _ = await call("task.list", {"session_id": session_id, "status": "completed"})
    if "error" in r:
        failures.append(f"task.list (status=completed) failed: {r['error']}")
    else:
        completed = r["result"]["tasks"]
        if not any(t["id"] == task_id for t in completed):
            failures.append(f"task.list(status=completed): task {task_id} missing")
        else:
            print(f"[task.list status=completed] ok, {len(completed)} task(s)")

    # 9. shutdown
    r, _ = await call("shutdown")
    if "error" in r:
        failures.append(f"shutdown failed: {r['error']}")
    print("[shutdown]", "ok" if "result" in r else f"FAIL: {r.get('error')}")

    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()

    # Drain any remaining stderr.
    try:
        await asyncio.wait_for(stderr_task, timeout=1.0)
    except asyncio.TimeoutError:
        stderr_task.cancel()
    stderr_fp.close()

    return _finish_with_report(proc, failures, actual_events, expected_events, stderr_lines)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_data_dir(python: str, agent_dir: str) -> Path:
    """Resolve the same data dir the agent will use.

    Delegates to the agent's own ``default_data_dir`` so we
    don't duplicate the platformdirs logic in the smoke test.
    """
    # Inline import is fine — this runs in the test's own process.
    sys.path.insert(0, agent_dir)
    try:
        from minimax_code.storage.db import default_data_dir  # type: ignore
    finally:
        # Don't pop: leaving the path in place for later imports
        # in this same process is harmless.
        pass
    return Path(default_data_dir())


def _seed_session(db_path: Path, session_id: str) -> None:
    """Open (creating if needed) the agent's data.db and insert a session.

    Migrations are idempotent — we apply them on every connect
    just like the agent would. Then we insert one row in
    ``sessions`` with a fresh ``created_at`` / ``updated_at``.

    Implementation note: we open the file with the same
    pragmas the agent uses (WAL, foreign_keys=ON) so the seed
    insert goes through the same journal the agent's
    connection will read from.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        conn.row_factory = sqlite3.Row
        # Apply the same DDL the agent uses on first boot.
        _apply_minimal_schema(conn)
        # Match the agent's pragmas so the journal mode and FK
        # enforcement behave the same way.
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA foreign_keys = ON")
        now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        conn.execute(
            "INSERT INTO sessions "
            "(id, title, created_at, updated_at, archived, model, system_prompt) "
            "VALUES (?, ?, ?, ?, 0, NULL, NULL)",
            (session_id, "smoke progress", now, now),
        )
        conn.commit()
    finally:
        conn.close()


def _apply_minimal_schema(conn: sqlite3.Connection) -> None:
    """Apply only the ``sessions`` DDL the smoke test needs.

    The agent's full schema lives in
    :mod:`minimax_code.storage.migrations`. We don't import it
    here because that would drag in ``pydantic`` / ``aiosqlite``
    just to create one row. The DDL is small and stable; the
    smoke test gets a defensible subset.
    """
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS sessions (
            id            TEXT PRIMARY KEY,
            title         TEXT NOT NULL DEFAULT '',
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            archived      INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
            model         TEXT,
            system_prompt TEXT
        );
        """
    )


def _saw_status(events: list[dict], status_value: str) -> bool:
    """Did any of ``events`` carry ``agent.status`` with the expected status?

    We accept both possible shapes of the streamed payload —
    the spec lets the tracker emit either ``{"status": ...,
    "task_id": ...}`` (top-level, used by the dashboard) or
    ``{"status": ..., "detail": {...}}`` (nested, used by the
    skill runtime). Both reach the UI.
    """
    for ev in events:
        if ev.get("event") != "agent.status":
            continue
        data = ev.get("data") or {}
        if data.get("status") == status_value:
            return True
    return False


def _finish(proc: asyncio.subprocess.Process, failures: list[str]) -> int:
    """Cleanup helper used when we've already bailed out."""
    try:
        if proc.returncode is None:
            proc.kill()
            await_proc(proc)
    except Exception:
        pass
    return 0 if not failures else 1


async def await_proc(proc: asyncio.subprocess.Process) -> None:
    try:
        await proc.wait()
    except Exception:
        pass


def _finish_with_report(
    proc: asyncio.subprocess.Process,
    failures: list[str],
    events: list[dict],
    expected: list[tuple[str, str]],
    stderr_lines: list[str] | None = None,
) -> int:
    """Print a final summary, then return 0/1."""
    if failures and stderr_lines:
        print("\n=== AGENT STDERR (for debugging) ===")
        for line in stderr_lines:
            print(f"  {line}")
    print("\n=== EVENTS COLLECTED ===")
    for ev in events[:20]:
        d = ev.get("data") or {}
        print(
            f"  {ev.get('event')}: status={d.get('status')!r} "
            f"task_id={d.get('task_id')!r} progress={d.get('progress')!r}"
        )
    if len(events) > 20:
        print(f"  ... ({len(events) - 20} more)")

    print("\n=== SUMMARY ===")
    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    print(f"  PASS: {len(events)} events collected, all expected statuses observed")
    return 0


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    python = sys.argv[1]
    agent_dir = sys.argv[2]
    workdir = sys.argv[3] if len(sys.argv) > 3 else "."
    Path(workdir).mkdir(parents=True, exist_ok=True)
    sys.exit(asyncio.run(main(python, agent_dir, workdir)))
