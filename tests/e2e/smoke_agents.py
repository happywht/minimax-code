"""Offline-capable subprocess smoke for durable Native agent invocation.

Creates an explicit source session, invokes with a stable key and verifies
the waiting receipt against run.get. Exit 0 means all checks passed.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time


async def main(python: str, agent_dir: str, workdir: str) -> int:
    env = os.environ.copy()
    env["PYTHONPATH"] = agent_dir
    env["MINIMAX_CODE_LOG_LEVEL"] = "WARNING"
    # Use the same data dir the smoke harness writes
    env["MINIMAX_CODE_DATA_DIR"] = workdir

    failures: list[str] = []
    events_seen: list[str] = []

    async def call(proc, method: str, params: dict | None = None, timeout: float = 8.0):
        rid = f"req-{method}-{time.time_ns()}"
        req = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
        await proc.stdin.drain()
        deadline = time.time() + timeout
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
            if obj.get("id") == rid:
                return obj
            if "event" in obj:
                events_seen.append(obj["event"])
        return {"error": {"code": -32000, "message": f"timeout waiting for {method}"}}

    proc = await asyncio.create_subprocess_exec(
        python, "-m", "minimax_code", "--stdio",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=agent_dir,
        env=env,
    )
    await asyncio.sleep(0.5)

    # 1. status
    r = await call(proc, "status")
    if "error" in r:
        failures.append(f"status failed: {r['error']}")
        print("[status] FAIL", r)
    else:
        print("[status] ok")

    # 2. list initial (may or may not be empty)
    r = await call(proc, "agent.list")
    if "error" in r:
        failures.append(f"agent.list failed: {r['error']}")
    else:
        print(f"[agent.list] initial: {len(r['result']['agents'])} agents")

    # 3. create
    r = await call(proc, "agent.create", {
        "name": "reviewer",
        "system_prompt": "You review code.",
        "tool_allowlist": ["read_file"],
    })
    if "error" in r:
        failures.append(f"agent.create failed: {r['error']}")
        print("[agent.create] FAIL", r)
    else:
        print(f"[agent.create] ok id={r['result']['agent']['id']}")

    # 4. list again
    r = await call(proc, "agent.list")
    if "error" in r:
        failures.append(f"agent.list failed: {r['error']}")
    elif not any(a.get("name") == "reviewer" for a in r["result"]["agents"]):
        failures.append("agent.list: did not find reviewer after create")
    else:
        print("[agent.list] ok, found reviewer")

    # Public execution requires an explicit Native source and stable admission key.
    source = await call(proc, "session.create", {
        "title": "Agent smoke source", "engine": "native", "workspace_path": workdir,
    })
    source_id = source.get("result", {}).get("session", {}).get("id")
    # 5. invoke
    events_before = len(events_seen)
    r = await call(proc, "agent.invoke", {
        "name": "reviewer",
        "request": "review main.py",
        "session_id": source_id,
        "idempotency_key": "smoke-review-once",
    }, timeout=10)
    if "error" in r:
        failures.append(f"agent.invoke failed: {r['error']}")
        print("[agent.invoke] FAIL", r)
    else:
        text = r["result"].get("text", "")
        if not text or not r["result"].get("run_id"):
            failures.append("agent.invoke: empty text")
        else:
            print(f"[agent.invoke] ok text={text!r}")
        receipt = r["result"]
        durable = await call(proc, "run.get", {"run_id": receipt["run_id"]})
        if durable.get("result", {}).get("result") != receipt:
            failures.append("run.get: durable receipt differs from waiting invocation")
        new_events = events_seen[events_before:]
        if not any(e.startswith("agent.") for e in new_events):
            failures.append(f"agent.invoke: no agent.* events streamed ({new_events})")
        else:
            print(f"[agent.invoke] events: {new_events}")

    # 6. task.list remains available; public execution evidence lives in run.get.
    r = await call(proc, "task.list")
    if "error" in r:
        failures.append(f"task.list failed: {r['error']}")
    else:
        tasks = r["result"].get("tasks", [])
        print(f"[task.list] ok, {len(tasks)} progress rows")

    # 7. delete
    r = await call(proc, "agent.delete", {"name": "reviewer"})
    if "error" in r:
        failures.append(f"agent.delete failed: {r['error']}")
    else:
        print("[agent.delete] ok")

    # 8. shutdown
    await call(proc, "shutdown")
    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()

    if failures:
        print("\n=== FAIL ===")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    print("\n=== PASS ===")
    return 0


if __name__ == "__main__":
    python = sys.argv[1]
    agent_dir = sys.argv[2]
    workdir = sys.argv[3] if len(sys.argv) > 3 else "."
    os.makedirs(workdir, exist_ok=True)
    sys.exit(asyncio.run(main(python, agent_dir, workdir)))
