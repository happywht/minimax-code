"""E2E smoke test for the ``session.*`` IPC namespace.

This is a black-box subprocess test. It:

1. Spawns the agent via ``python -m minimax_code`` (the same
   entry point Tauri uses).
2. Calls ``session.create { title: 'smoke-test' }`` and
   confirms the row shows up in the very next ``session.list``
   (this is the sidebar "new task" button path).
3. Calls ``session.list`` to discover the existing sessions.
4. Picks the first session id, calls ``session.get`` to confirm
   the recent_messages field is present (possibly empty).
5. Calls ``session.archive`` to archive it, then
   ``session.list { archived: True }`` to confirm it shows up.
6. Calls ``session.unarchive`` to restore it, then
   ``session.list { archived: False }`` to confirm it's back.
7. Shuts the agent down cleanly.

Pass criteria
-------------

* Every JSON-RPC call returns without an error envelope.
* ``session.create`` returns a ``session_id`` string and the
  row is immediately visible in ``session.list``.
* ``session.list`` returns at least 0 sessions — the agent's
  default DB is populated on first run by the test fixture, so
  we expect at least one.
* ``session.get`` returns a row with a ``recent_messages`` key
  (list, possibly empty).
* The archive / unarchive round trip is observable in the
  follow-up list calls.

Usage
-----

``python tests/e2e/smoke_sessions.py <python> <agent_dir> [<workdir>]``

Exit code 0 = pass, 1 = fail.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path


async def main(python: str, agent_dir: str, workdir: str) -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = agent_dir
    env["MINIMAX_CODE_LOG_LEVEL"] = "WARNING"
    # Isolate the DB so the test never touches the real user profile.
    env["MINIMAX_CODE_DATA_DIR"] = workdir

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
        ``time_ns()``-suffixed id for every call). Any event
        lines emitted before the response are silently dropped.
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
            except TimeoutError:
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

    # 0. Wait for the agent to boot and the DB to migrate.
    await asyncio.sleep(0.5)

    # 1. liveness — ``status`` is a built-in.
    r = await call("status")
    if "error" in r:
        failures.append(f"status call failed: {r['error']}")
    print("[status]", "ok" if "result" in r else f"FAIL: {r.get('error')}")

    # 1b. session.create — the sidebar "new task" button goes
    # through this. It must return a session_id and the row must
    # show up in the very next session.list call (the UI
    # optimistically prepends it; we want to confirm the row is
    # actually persisted, not just synthesised in the client).
    r = await call("session.create", {"title": "smoke-test"})
    if "error" in r:
        failures.append(f"session.create failed: {r['error']}")
        print("[session.create] FAIL", r)
        return _finish(proc, failures)
    created_id = r["result"].get("session_id")
    created_title = r["result"].get("title")
    if not created_id or not isinstance(created_id, str):
        failures.append(
            f"session.create: missing or bad session_id, got {r['result']!r}"
        )
        return _finish(proc, failures)
    if created_title != "smoke-test":
        failures.append(
            f"session.create: expected title='smoke-test', got {created_title!r}"
        )
    print(
        f"[session.create] ok, session_id={created_id!r} "
        f"title={created_title!r} created_at={r['result'].get('created_at')!r}"
    )

    # 1c. The new row must be visible in the very next
    # session.list (no manual refresh needed). This is the
    # exact code path the sidebar's "session list re-render
    # after create" use case hits.
    r = await call("session.list", {})
    if "error" in r:
        failures.append(
            f"session.list (post-create) failed: {r['error']}"
        )
    else:
        ids = [s["id"] for s in r["result"]["sessions"]]
        if created_id not in ids:
            failures.append(
                f"session.create: created id {created_id!r} not visible "
                f"in session.list, got {ids}"
            )
        else:
            print(
                f"[session.list post-create] ok, total={r['result']['total']} "
                f"new row present"
            )

    # 2. Seed at least one session so ``session.list`` is non-empty.
    # We do this through a separate small Python process that opens
    # the same DB the agent is using. The agent migrates the DB on
    # first call, so by now the file is ready.
    seed_id = f"smoke_{uuid.uuid4().hex[:10]}"
    seed_proc = await asyncio.create_subprocess_exec(
        python, "-c",
        "import asyncio, os, sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, r'''%s''')\n"
        "from minimax_code.storage.db import AsyncDatabase\n"
        "async def main():\n"
        "    db = AsyncDatabase(Path(os.environ['MINIMAX_CODE_DATA_DIR']) / 'data.db')\n"
        "    await db.connect()\n"
        "    await db.migrate()\n"
        "    await db.execute(\n"
        "        \"INSERT OR IGNORE INTO sessions (id, title, created_at, updated_at, archived) \"\n"
        "        \"VALUES (?, 'smoke-seed', '2026-06-01T00:00:00Z', '2026-06-01T00:00:00Z', 0)\",\n"
        "        (r'''%s''',),\n"
        "    )\n"
        "    await db._conn.commit()\n"
        "    await db.close()\n"
        "asyncio.run(main())\n" % (agent_dir, seed_id),
        cwd=agent_dir,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    rc = await seed_proc.wait()
    if rc != 0:
        stderr = (await seed_proc.stderr.read()).decode("utf-8", errors="replace")
        failures.append(f"seed step failed (rc={rc}): {stderr[:500]}")
        return _finish(proc, failures)
    print(f"[seed] inserted session id={seed_id!r}")

    # 3. session.list — at least the seeded row is there.
    r = await call("session.list", {})
    if "error" in r:
        failures.append(f"session.list failed: {r['error']}")
        print("[session.list] FAIL", r)
        return _finish(proc, failures)
    sessions = r["result"].get("sessions", [])
    total = r["result"].get("total", 0)
    print(f"[session.list] ok, total={total} returned={len(sessions)}")
    if total < 1:
        failures.append(f"session.list: expected total>=1, got {total}")
    if not any(s["id"] == seed_id for s in sessions):
        failures.append(
            f"session.list: seeded id {seed_id!r} not in "
            f"{[s['id'] for s in sessions]}"
        )
        return _finish(proc, failures)

    # 4. session.get — has recent_messages.
    r = await call("session.get", {"session_id": seed_id})
    if "error" in r:
        failures.append(f"session.get failed: {r['error']}")
        print("[session.get] FAIL", r)
    else:
        session = r["result"].get("session") or {}
        recent = r["result"].get("recent_messages")
        print(
            f"[session.get] ok, session_id={session.get('id')!r} "
            f"recent_messages={recent!r}"
        )
        if session.get("id") != seed_id:
            failures.append(
                f"session.get: expected id={seed_id!r}, got {session.get('id')!r}"
            )
        if not isinstance(recent, list):
            failures.append(
                f"session.get: recent_messages should be a list, got {type(recent).__name__}"
            )

    # 5. session.archive — flips the flag.
    r = await call("session.archive", {"session_id": seed_id})
    if "error" in r:
        failures.append(f"session.archive failed: {r['error']}")
        print("[session.archive] FAIL", r)
        return _finish(proc, failures)
    archived_session = r["result"]["session"]
    print(
        f"[session.archive] ok, archived={archived_session['archived']!r} "
        f"updated_at={archived_session.get('updated_at')!r}"
    )
    if archived_session["archived"] is not True:
        failures.append(
            f"session.archive: expected archived=True, "
            f"got {archived_session['archived']!r}"
        )

    # 6. session.list { archived: True } — the row shows up.
    r = await call("session.list", {"archived": True})
    if "error" in r:
        failures.append(f"session.list(archived=True) failed: {r['error']}")
    else:
        archived_list = r["result"]["sessions"]
        ids = [s["id"] for s in archived_list]
        if seed_id not in ids:
            failures.append(
                f"session.list(archived=True): expected {seed_id!r} in {ids}"
            )
        else:
            print(
                f"[session.list(archived=True)] ok, "
                f"total={r['result']['total']} returned={len(ids)}"
            )

    # 7. session.unarchive — flips it back.
    r = await call("session.unarchive", {"session_id": seed_id})
    if "error" in r:
        failures.append(f"session.unarchive failed: {r['error']}")
        print("[session.unarchive] FAIL", r)
    else:
        unarchived_session = r["result"]["session"]
        print(
            f"[session.unarchive] ok, archived={unarchived_session['archived']!r}"
        )
        if unarchived_session["archived"] is not False:
            failures.append(
                f"session.unarchive: expected archived=False, "
                f"got {unarchived_session['archived']!r}"
            )

    # 8. session.list { archived: False } — the row shows up again.
    r = await call("session.list", {"archived": False})
    if "error" in r:
        failures.append(f"session.list(archived=False) failed: {r['error']}")
    else:
        active_list = r["result"]["sessions"]
        ids = [s["id"] for s in active_list]
        if seed_id not in ids:
            failures.append(
                f"session.list(archived=False): expected {seed_id!r} in {ids}"
            )
        else:
            print(
                f"[session.list(archived=False)] ok, "
                f"total={r['result']['total']} returned={len(ids)}"
            )

    # 9. shutdown.
    r = await call("shutdown")
    if "error" in r:
        failures.append(f"shutdown failed: {r['error']}")
    print("[shutdown]", "ok" if "result" in r else f"FAIL: {r.get('error')}")

    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except TimeoutError:
        proc.kill()
        await proc.wait()

    print("\n=== SUMMARY ===")
    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    print("  PASS: session.create returns session_id and persists the row")
    print("  PASS: session.list / get / archive / unarchive round-tripped")
    print("  PASS: archived-list filter and active-list filter agree with the row state")
    return 0


def _finish(proc: asyncio.subprocess.Process, failures: list[str]) -> int:
    """Cleanup helper used when we've already bailed out."""
    try:
        if proc.returncode is None:
            proc.kill()
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
