"""E2E smoke for the model.* IPC + persistence.

Black-box subprocess test:
  1. spawn agent (fresh workdir)
  2. model.list  -> returns the hard-coded candidate set
  3. model.get_current  -> default model from DB row
  4. model.set_current {model: "MiniMax-Code"}  -> ok
  5. shutdown
  6. restart agent (same workdir)
  7. model.get_current  -> still "MiniMax-Code" (persistence sanity)
  8. model.set_current {model: "unknown-xyz"}  -> -32602 reject
  9. shutdown

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
    env["MINIMAX_CODE_DATA_DIR"] = workdir

    failures: list[str] = []

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
        return {"error": {"code": -32000, "message": f"timeout waiting for {method}"}}

    async def run_phase(label: str, current_model: str = "MiniMax-M3") -> None:
        proc = await asyncio.create_subprocess_exec(
            python, "-m", "minimax_code",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=agent_dir,
            env=env,
        )
        await asyncio.sleep(0.5)

        # 2. model.list
        r = await call(proc, "model.list")
        if "error" in r:
            failures.append(f"[{label}] model.list failed: {r['error']}")
            print(f"[{label}] model.list FAIL", r)
        else:
            models = r["result"]["models"]
            print(f"[{label}] model.list ok: {len(models)} models")
            if "MiniMax-M3" not in models:
                failures.append(f"[{label}] model.list missing MiniMax-M3 in {models}")

        # 3. model.get_current
        r = await call(proc, "model.get_current")
        if "error" in r:
            failures.append(f"[{label}] model.get_current failed: {r['error']}")
        else:
            actual = r["result"]["model"]
            print(f"[{label}] model.get_current: {actual}")
            if actual != current_model:
                failures.append(
                    f"[{label}] model.get_current expected {current_model}, got {actual}"
                )

        # shutdown
        await call(proc, "shutdown")
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()

    # ---- Phase 1: fresh workdir ----
    print("=== Phase 1: fresh workdir, default model ===")
    await run_phase("P1")

    # ---- Phase 2: set new model, restart, verify persistence ----
    print("\n=== Phase 2: set + restart + persist ===")
    proc = await asyncio.create_subprocess_exec(
        python, "-m", "minimax_code",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=agent_dir,
        env=env,
    )
    await asyncio.sleep(0.5)
    r = await call(proc, "model.set_current", {"model": "MiniMax-Code"})
    if "error" in r:
        failures.append(f"[P2] set_current failed: {r['error']}")
    else:
        print(f"[P2] set_current ok -> {r['result']['model']}")
    await call(proc, "shutdown")
    try:
        await asyncio.wait_for(proc.wait(), timeout=3.0)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()

    # restart, verify persistence
    await run_phase("P2", current_model="MiniMax-Code")

    # ---- Phase 3: reject unknown model ----
    print("\n=== Phase 3: reject unknown model ===")
    proc = await asyncio.create_subprocess_exec(
        python, "-m", "minimax_code",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=agent_dir,
        env=env,
    )
    await asyncio.sleep(0.5)
    r = await call(proc, "model.set_current", {"model": "unknown-xyz"})
    if "error" not in r or r["error"].get("code") != -32602:
        failures.append(
            f"[P3] expected -32602 for unknown model, got {r.get('error')}"
        )
    else:
        print(f"[P3] unknown model rejected with -32602 (msg={r['error'].get('message')!r})")
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
