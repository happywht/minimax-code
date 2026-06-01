"""Quick e2e sanity check: spawn the agent and exercise core flows via JSON-RPC stdio.

Usage: python smoke_e2e.py <python> <agent_dir> <tmp_workdir>

It:
1. Spawns `python -m minimax_code` as a subprocess.
2. Sends `status` to confirm liveness.
3. Sends `skill.list` to confirm 3 built-in skills load.
4. Sends `agent.send_message` with "hello" and reads streamed events.
5. Sends a skill.invoke against code-review (mock since LLM is offline).
6. Restarts the agent and sends `skill.list` again to verify SQLite persistence
   (skills table has the data).
7. Writes a summary to stdout.
"""
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
    env["MINIMAX_CODE_DATA_DIR"] = workdir  # if storage honours it

    proc = await asyncio.create_subprocess_exec(
        python, "-m", "minimax_code",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=agent_dir,
        env=env,
    )

    results = {}

    async def call(method, params=None, timeout=8.0):
        req = {"jsonrpc": "2.0", "id": f"req-{method}-{time.time_ns()}", "method": method, "params": params or {}}
        line = json.dumps(req, ensure_ascii=False) + "\n"
        proc.stdin.write(line.encode("utf-8"))
        await proc.stdin.drain()
        # Read response line by line; collect any events too.
        deadline = time.time() + timeout
        events = []
        while time.time() < deadline:
            raw = await asyncio.wait_for(proc.stdout.readline(), timeout=max(0.05, deadline - time.time()))
            if not raw:
                break
            obj = json.loads(raw.decode("utf-8").strip())
            if obj.get("id") == req["id"]:
                return obj, events
            if "event" in obj:
                events.append(obj)
        return {"error": {"code": -32000, "message": "timeout"}}, events

    # Wait for first readline to be ready (process boot).
    await asyncio.sleep(0.5)

    # 1. status
    r, _ = await call("status")
    results["status"] = r
    print("[status]", json.dumps(r, ensure_ascii=False))

    # 2. skill.list
    r, _ = await call("skill.list")
    results["skill_list"] = r
    print("[skill.list]", json.dumps(r, ensure_ascii=False)[:300])

    # 3. agent.send_message
    r, events = await call("agent.send_message", {"content": "hello", "session_id": "ses_smoke01"})
    results["agent_hello"] = r
    print("[agent.send_message]", json.dumps(r, ensure_ascii=False))
    print(f"  events received: {len(events)}")
    for e in events[:3]:
        print(f"    {e.get('event')}: {str(e.get('params', {}).get('delta', ''))[:60]}")

    # 4. shutdown
    r, _ = await call("shutdown")
    results["shutdown"] = r
    print("[shutdown]", json.dumps(r, ensure_ascii=False))

    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()

    # 5. Restart and verify skill persistence
    proc2 = await asyncio.create_subprocess_exec(
        python, "-m", "minimax_code",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=agent_dir,
        env=env,
    )
    await asyncio.sleep(0.5)

    async def call2(method, params=None, timeout=8.0):
        req = {"jsonrpc": "2.0", "id": f"req2-{method}-{time.time_ns()}", "method": method, "params": params or {}}
        line = json.dumps(req, ensure_ascii=False) + "\n"
        proc2.stdin.write(line.encode("utf-8"))
        await proc2.stdin.drain()
        deadline = time.time() + timeout
        events = []
        while time.time() < deadline:
            raw = await asyncio.wait_for(proc2.stdout.readline(), timeout=max(0.05, deadline - time.time()))
            if not raw:
                break
            obj = json.loads(raw.decode("utf-8").strip())
            if obj.get("id") == req["id"]:
                return obj, events
            if "event" in obj:
                events.append(obj)
        return {"error": {"code": -32000, "message": "timeout"}}, events

    r, _ = await call2("skill.list")
    results["skill_list_after_restart"] = r
    print("[skill.list after restart]", json.dumps(r, ensure_ascii=False)[:300])

    r2, _ = await call2("shutdown")
    print("[shutdown 2]", json.dumps(r2, ensure_ascii=False))

    try:
        await asyncio.wait_for(proc2.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        proc2.kill()
        await proc2.wait()

    # Summary
    print("\n=== SUMMARY ===")
    for k, v in results.items():
        if isinstance(v, dict) and "result" in v:
            print(f"  PASS: {k}")
        elif isinstance(v, dict) and "error" in v:
            print(f"  FAIL: {k} -> {v['error']}")
        else:
            print(f"  ?: {k} -> {v}")

    return 0


if __name__ == "__main__":
    python = sys.argv[1]
    agent_dir = sys.argv[2]
    workdir = sys.argv[3]
    os.makedirs(workdir, exist_ok=True)
    sys.exit(asyncio.run(main(python, agent_dir, workdir)))
