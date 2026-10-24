"""Real Codex live-fire check — run.start engine=codex against the real
Codex App Server on a fresh target-repo copy.

Usage (from agent/ with uv)::

    uv run python ../evals/codex_live.py [--task task1-fix-total]

Exit 0 = the run completed and pytest accepts the diff.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from run_eval import AgentClient, apply_mutation, reset_target, run_acceptance  # noqa: E402

EVALS_DIR = Path(__file__).resolve().parent


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", default="task1-fix-total")
    parser.add_argument("--timeout", type=float, default=900)
    args = parser.parse_args()

    spec = json.loads((EVALS_DIR / "tasks.json").read_text(encoding="utf-8"))
    task = next(t for t in spec["tasks"] if t["id"] == args.task
                or t["id"].startswith(args.task))

    agent_dir = EVALS_DIR.parent / "agent"
    data_dir = EVALS_DIR / ".data-codex"
    data_dir.mkdir(parents=True, exist_ok=True)
    workspace = EVALS_DIR / "runs" / f"workspace-codex-{int(time.time())}"
    reset_target(workspace)
    apply_mutation(workspace, task.get("mutation"))

    client = await AgentClient.start(agent_dir, data_dir, workspace)
    client.auto_approve = True
    await asyncio.sleep(1.0)

    cards = await client.call("engine.list", {}, timeout=30)
    engines = {e["id"]: e for e in cards.get("result", {}).get("engines", [])}
    print("[live] codex card:", json.dumps(engines.get("codex"), ensure_ascii=False)[:250], flush=True)
    if engines and not engines.get("codex", {}).get("available"):
        print("[live] codex unavailable — aborting")
        await client.close()
        return 2

    print(f"[live] starting engine=codex run for {task['id']} ...", flush=True)
    t0 = time.time()
    reply = await client.call("run.start", {
        "session_id": "ses-codex-live",
        "content": task["prompt"],
        "idempotency_key": f"codex-live-{int(time.time())}",
        "budget_tokens": 80000,
        "engine": "codex",
    }, timeout=60)
    if "error" in reply:
        print("[live] run.start failed:", reply["error"], flush=True)
        await client.close()
        return 2
    run = reply["result"]["run"]
    run_id = run["id"]
    print(f"[live] admitted {run_id}; driving (real Codex, may take minutes) ...", flush=True)

    receipt = None
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        await asyncio.sleep(5)
        state = await client.call("run.get", {"run_id": run_id}, timeout=30)
        row = state.get("result", {}).get("run", {})
        status = row.get("status")
        if status in {"completed", "failed", "cancelled", "budget_paused"}:
            receipt = state["result"].get("result")
            print(f"[live] run {status} in {time.time() - t0:.0f}s total", flush=True)
            break
    else:
        print("[live] TIMED OUT waiting for the run", flush=True)

    acceptance = run_acceptance(workspace, task["acceptance"])
    print(f"[live] acceptance: {'PASS' if acceptance['passed'] else 'FAIL'}", flush=True)
    if not acceptance["passed"]:
        print(acceptance["tail"][:600])
    if receipt:
        print(f"[live] receipt text[:300]: {str(receipt.get('text', ''))[:300]!r}", flush=True)
        print(f"[live] thread: {receipt.get('codex_thread_id')} usage: "
              f"{receipt.get('tokens_in')}+{receipt.get('tokens_out')} "
              f"budget_paused={receipt.get('budget_paused')}", flush=True)

    await client.close()
    ok = acceptance["passed"] and receipt is not None and not receipt.get("cancelled")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
