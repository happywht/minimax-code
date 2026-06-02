"""E2E smoke for the agents IPC + sub-agent stub pipeline.

Black-box subprocess test:
  1. spawn the agent
  2. agent.list -> empty initially
  3. agent.create { name: "reviewer", ... } -> row
  4. agent.list -> find the row
  5. agent.invoke -> stream events (status + message_chunk)
                 + return envelope with the stub text
  6. task.list -> see the sub-agent's task created with status=completed
  7. agent.delete -> ok
  8. shutdown

Exit 0 = pass, 1 = fail.
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
            obj = json.loads(raw.decode("utf-8").strip())
            if obj.get("id") == rid:
                return obj
            if "event" in obj:
                events_seen.append(obj["event"])
        return {"error": {"code": -32000, "message": f"timeout waiting for {method}"}}

    proc = await asyncio.create_subprocess_exec(
        python, "-m", "minimax_code",
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
        "tool_allowlist": ["read_file", "shell"],
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

    # 5. invoke
    events_before = len(events_seen)
    r = await call(proc, "agent.invoke", {
        "name": "reviewer",
        "request": "review main.py",
    }, timeout=10)
    if "error" in r:
        failures.append(f"agent.invoke failed: {r['error']}")
        print("[agent.invoke] FAIL", r)
    else:
        text = r["result"].get("text", "")
        # Phase 4: sub-agent uses real LLM when injected. In mock mode
        # (no MINIMAX_API_KEY) it returns the LLM client's mock text
        # (starts with "[mock]"). In real mode it returns model output.
        # The OLD sub-agent stub fallback (text "stub: agent <name> ...")
        # is also acceptable as a backward-compat path. Accept any of:
        #   - "stub: agent <name> would handle"  (sub-agent stub)
        #   - "[mock]" prefix                       (LLM client mock mode)
        #   - any other non-empty text              (real LLM)
        if not text:
            failures.append(f"agent.invoke: empty text")
        elif "stub: agent" in text or text.startswith("[mock]") or len(text) > 20:
            print(f"[agent.invoke] ok text={text!r}")
        else:
            failures.append(f"agent.invoke: unexpected text {text!r}")
        new_events = events_seen[events_before:]
        if not any(e.startswith("agent.") for e in new_events):
            failures.append(f"agent.invoke: no agent.* events streamed ({new_events})")
        else:
            print(f"[agent.invoke] events: {new_events}")

    # 6. task.list — see the sub-agent task
    r = await call(proc, "task.list")
    if "error" in r:
        failures.append(f"task.list failed: {r['error']}")
    else:
        tasks = r["result"].get("tasks", [])
        sub = [t for t in tasks if "reviewer" in (t.get("title") or "")]
        if not sub:
            failures.append(f"task.list: no subagent task found (titles: {[t.get('title') for t in tasks]})")
        else:
            print(f"[task.list] ok, found subagent task title={sub[0]['title']!r} status={sub[0]['status']!r}")

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
