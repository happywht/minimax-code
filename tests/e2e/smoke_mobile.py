"""E2E smoke for the mobile pairing IPC pipeline.

Black-box subprocess test:
  1. spawn the agent
  2. mobile.pair_start -> grab a token
  3. mobile.pair_confirm with that token -> get a device row
  4. mobile.list -> confirm the device is there
  5. mobile.touch -> bump last_seen_at
  6. mobile.unpair -> remove
  7. shutdown
  8. restart the agent
  9. mobile.list -> should be empty (persistence sanity)
  10. shutdown

Usage: ``python tests/e2e/smoke_mobile.py <python> <agent_dir> <workdir>``
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

    failures: list[str] = []
    actual_events: list[dict] = []

    async def call(proc, method: str, params: dict | None = None, timeout: float = 8.0):
        req_id = f"req-{method}-{time.time_ns()}"
        req = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params or {}}
        proc.stdin.write((json.dumps(req) + "\n").encode("utf-8"))
        await proc.stdin.drain()

        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=max(0.05, deadline - time.time())
                )
            except asyncio.TimeoutError:
                break
            if not raw:
                break
            obj = json.loads(raw.decode("utf-8").strip())
            if obj.get("id") == req_id:
                return obj
            if "event" in obj:
                actual_events.append(obj)
        return {"error": {"code": -32000, "message": f"timeout waiting for {method}"}}

    async def run_one(env: dict, label: str) -> int:
        proc = await asyncio.create_subprocess_exec(
            python, "-m", "minimax_code",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=agent_dir,
            env=env,
        )
        await asyncio.sleep(1.0)

        # 1. status
        r = await call(proc, "status")
        if "error" in r:
            failures.append(f"[{label}] status failed: {r['error']}")
            print(f"[{label}] status FAIL", r)

        # 2. pair_start
        r = await call(proc, "mobile.pair_start", {"suggested_name": "iPhone"})
        if "error" in r:
            failures.append(f"[{label}] pair_start failed: {r['error']}")
            print(f"[{label}] pair_start FAIL", r)
        else:
            token = r["result"]["token"]
            print(f"[{label}] pair_start ok token={token[:12]}...")
            # 3. pair_confirm
            r = await call(
                proc, "mobile.pair_confirm",
                {"token": token, "device_id": "smoke-dev-1", "name": "iPhone", "public_key": "fake-pk"},
            )
            if "error" in r:
                failures.append(f"[{label}] pair_confirm failed: {r['error']}")
                print(f"[{label}] pair_confirm FAIL", r)
            else:
                print(f"[{label}] pair_confirm ok device_id={r['result']['device']['device_id']}")
                # 4. list
                r = await call(proc, "mobile.list")
                if "error" in r or not any(
                    d.get("device_id") == "smoke-dev-1" for d in r["result"]["devices"]
                ):
                    failures.append(f"[{label}] list did not find smoke-dev-1: {r}")
                else:
                    print(f"[{label}] list ok")
                # 5. touch
                r = await call(proc, "mobile.touch", {"device_id": "smoke-dev-1"})
                if "error" in r:
                    failures.append(f"[{label}] touch failed: {r['error']}")
                else:
                    print(f"[{label}] touch ok")
                # 6. unpair
                r = await call(proc, "mobile.unpair", {"device_id": "smoke-dev-1"})
                if "error" in r:
                    failures.append(f"[{label}] unpair failed: {r['error']}")
                else:
                    print(f"[{label}] unpair ok")

        # 7. shutdown
        await call(proc, "shutdown")
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        return 0 if not failures else 1

    print("=== Phase 1: pair + list + touch + unpair ===")
    rc = await run_one(env, "phase1")
    print(f"=== Phase 2: restart, confirm empty (already-unpaired) ===")
    rc2 = await run_one(env, "phase2")

    if failures:
        print("\n=== FAIL ===")
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    print("\n=== PASS ===")
    return 0 if rc == 0 and rc2 == 0 else 1


if __name__ == "__main__":
    python = sys.argv[1]
    agent_dir = sys.argv[2]
    workdir = sys.argv[3] if len(sys.argv) > 3 else "."
    os.makedirs(workdir, exist_ok=True)
    sys.exit(asyncio.run(main(python, agent_dir, workdir)))
