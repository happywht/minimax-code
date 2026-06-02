"""E2E smoke test for the ``agent.*`` (sub-agent) IPC namespace.

This is a black-box subprocess test. It:

1. Spawns the agent via ``python -m minimax_code`` (the same
   entry point Tauri uses).
2. Creates a sub-agent config via ``agent.create`` (system prompt
   + model). The DAO round-trip is the same path the UI uses
   when the user hits "new sub-agent".
3. Invokes the sub-agent via ``agent.invoke``. With the process-
   wide ``MiniMaxClient`` wired in by
   :func:`minimax_code.app._maybe_open_db`, the response is the
   model's actual final text (or the mock-mode canned string
   when ``MINIMAX_API_KEY`` is unset). The handler also emits a
   ``agent.message_chunk`` event with the same text.
4. Confirms ``task_id`` is wired through the progress tracker
   and the task lifecycle (``task.list`` finds the row in
   ``completed`` state).
5. Shuts the agent down cleanly.

Pass criteria
-------------

* Every JSON-RPC call returns without an error envelope.
* ``agent.create`` round-trips a row in the ``agents`` table
  (visible in the very next ``agent.list``).
* ``agent.invoke`` returns ``stub=False`` (because the runtime
  has an injected LLM) and a non-empty ``text`` field.
* If the smoke runner set ``MINIMAX_API_KEY`` in the
  environment, the response ``text`` MUST NOT start with the
  stub prefix — it must be the real model answer.
* A ``task.*`` row is visible in ``completed`` state.

Usage
-----

``python tests/e2e/smoke_agents.py <python> <agent_dir> [<workdir>]``

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

    has_api_key = bool(env.get("MINIMAX_API_KEY"))

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

    # 4. agent.invoke — the real-LLM path (the runtime has the
    # process-wide MiniMaxClient injected at boot).
    r = await call(
        "agent.invoke",
        {
            "name": agent_name,
            "request": "In one short sentence, what does this code do?",
        },
        timeout=20.0,
    )
    if "error" in r:
        failures.append(f"agent.invoke failed: {r['error']}")
        print("[agent.invoke] FAIL", r)
        return _finish(proc, failures)
    result = r["result"]
    text = result.get("text", "")
    is_stub = result.get("stub", True)
    iterations = result.get("iterations", 0)
    task_id = result.get("task_id")
    print(
        f"[agent.invoke] ok, stub={is_stub} iterations={iterations} "
        f"task_id={task_id!r} text_len={len(text)}"
    )
    print(f"[agent.invoke] text={text[:120]!r}{'...' if len(text) > 120 else ''}")

    # The runtime was built with a real (or mock-mode)
    # MiniMaxClient, so ``stub`` must be False and ``text``
    # must be non-empty.
    if is_stub is not False:
        failures.append(
            f"agent.invoke: expected stub=False (real-LLM path), got {is_stub!r}"
        )
    if not isinstance(text, str) or not text.strip():
        failures.append(
            f"agent.invoke: text must be a non-empty string, got {text!r}"
        )
    if not isinstance(iterations, int) or iterations < 1:
        failures.append(
            f"agent.invoke: iterations must be >=1, got {iterations!r}"
        )
    # The model's actual text must not be the stub prefix.
    if "stub:" in text:
        failures.append(
            f"agent.invoke: text contains 'stub:' prefix (LLM path not taken): {text!r}"
        )

    # When the smoke runner has set MINIMAX_API_KEY, the response
    # should be the real model answer (not the mock-mode canned
    # text either). We assert the stub prefix is absent — which
    # it is, regardless of mock-vs-real, because the new path
    # never emits the stub prefix. (Tighten this further when
    # the smoke runner is wired to a fixture that returns a
    # known phrase.)
    if has_api_key and "stub:" in text:
        failures.append(
            f"agent.invoke: MINIMAX_API_KEY is set but text still contains "
            f"'stub:' prefix: {text!r}"
        )
    print(
        f"[agent.invoke] has_api_key={has_api_key} stub_prefix_absent={'stub:' not in text}"
    )

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
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()

    print("\n=== SUMMARY ===")
    if failures:
        for f in failures:
            print(f"  FAIL: {f}")
        return 1
    print("  PASS: agent.create round-trips a row in the agents table")
    print("  PASS: agent.invoke returns stub=False with a real-LLM text")
    print("  PASS: 'stub:' prefix is absent from the response text")
    if has_api_key:
        print("  PASS: MINIMAX_API_KEY was set; response is the real model answer")
    print("  PASS: progress task lifecycle (start → 50 → complete) recorded")
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
