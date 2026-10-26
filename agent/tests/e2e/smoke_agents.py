"""Subprocess smoke for durable Native agent admission and saved receipts.

The runner supplies an isolated data/workspace directory. Verification can
force mock mode; this script does not certify a paid provider from a key alone.
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
        python, "-m", "minimax_code", "--stdio",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=agent_dir,
        env=env,
    )

    failures: list[str] = []

    async def call(
        method: str, params: dict | None = None, timeout: float = 15.0
    ) -> dict:
        """Send a request and return the raw response envelope."""
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

    # 2. agent.create — the UI's "new sub-agent" path.
    agent_name = f"smoke_ag_{uuid.uuid4().hex[:8]}"
    r = await call(
        "agent.create",
        {
            "name": agent_name,
            "system_prompt": "You are a concise reviewer. Reply in one short sentence.",
            "tool_allowlist": None,
            "model": None,
        },
    )
    if "error" in r:
        failures.append(f"agent.create failed: {r['error']}")
        print("[agent.create] FAIL", r)
        return _finish(proc, failures)
    created = r["result"].get("agent", {})
    print(
        f"[agent.create] ok, name={created.get('name')!r} "
        f"id={created.get('id')!r} model={created.get('model')!r}"
    )
    if created.get("name") != agent_name:
        failures.append(
            f"agent.create: expected name={agent_name!r}, got {created.get('name')!r}"
        )

    # 3. agent.list — the new row is visible.
    r = await call("agent.list", {})
    if "error" in r:
        failures.append(f"agent.list failed: {r['error']}")
    else:
        names = [a["name"] for a in r["result"].get("agents", [])]
        if agent_name not in names:
            failures.append(
                f"agent.list: created name {agent_name!r} not visible, got {names}"
            )
        else:
            print(f"[agent.list] ok, total={r['result'].get('total')}")

    source = await call("session.create", {
        "title": "Agent smoke source", "engine": "native",
        "workspace_path": str(Path(workdir).resolve()),
    })
    source_id = source.get("result", {}).get("session", {}).get("id")
    # 4. agent.invoke — the real-LLM path (the runtime has the
    # process-wide MiniMaxClient injected at boot).
    r = await call(
        "agent.invoke",
        {
            "name": agent_name,
            "request": "In one short sentence, what does this code do?",
            "session_id": source_id,
            "idempotency_key": "smoke-agent-once",
        },
        timeout=20.0,
    )
    if "error" in r:
        failures.append(f"agent.invoke failed: {r['error']}")
        print("[agent.invoke] FAIL", r)
        return _finish(proc, failures)
    result = r["result"]
    durable = await call("run.get", {"run_id": result["run_id"]})
    if durable.get("result", {}).get("result") != result:
        failures.append("run.get: waiting result was not durably retained")
    text = result.get("text", "")
    is_stub = result.get("stub", True)
    iterations = result.get("iterations", 0)
    task_id = result.get("task_id")
    print(
        f"[agent.invoke] ok, stub={is_stub} iterations={iterations} "
        f"task_id={task_id!r} text_len={len(text)}"
    )
    print(f"[agent.invoke] text={text[:120]!r}{'...' if len(text) > 120 else ''}")

    if not isinstance(text, str) or not text.strip() or "stub: agent" in text:
        failures.append(f"agent.invoke: expected Native answer, got {text!r}")
    if not isinstance(iterations, int) or iterations < 1:
        failures.append(f"agent.invoke: iterations must be >=1, got {iterations!r}")
    if result.get("run_status") != "completed":
        failures.append(f"agent.invoke: expected completed receipt, got {result.get('run_status')!r}")

    # 5. task.list — the progress row landed.
    if task_id:
        r = await call("task.list", {"session_id": result.get("session_id")})
        if "error" in r:
            failures.append(f"task.list failed: {r['error']}")
        else:
            tasks = r["result"].get("tasks", [])
            matching = [t for t in tasks if t.get("id") == task_id]
            if not matching:
                failures.append(
                    f"task.list: task_id {task_id!r} not visible"
                )
            else:
                state = matching[0].get("status") or matching[0].get("state")
                print(
                    f"[task.list] ok, found task_id={task_id!r} status={state!r}"
                )
                # Should be in 'completed' state by now.
                if state not in ("completed", "complete", "done", 100, "100"):
                    failures.append(
                        f"task.list: expected status=completed, got {state!r}"
                    )

    # 6. cleanup — delete the agent row.
    r = await call("agent.delete", {"name": agent_name})
    if "error" in r:
        # Non-fatal: the next test fixture will overwrite it.
        print(f"[agent.delete] (non-fatal) {r['error']}")
    else:
        print(f"[agent.delete] ok, removed {agent_name!r}")

    # 7. shutdown.
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
    print("  PASS: agent.create round-trips a row in the agents table")
    print("  PASS: agent.invoke returns a durable Native answer")
    print("  PASS: 'stub:' prefix is absent from the response text")
    print("  PASS: durable receipt matches run.get")
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
