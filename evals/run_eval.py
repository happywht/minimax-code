"""MiniMax Code 真实模型评测 harness（M3 验收：固定编程任务集首轮）.

用法::

    python evals/run_eval.py \
        --api-key <key> \
        --base-url https://open.bigmodel.cn/api/anthropic \
        --model glm-4.6 \
        [--tasks task1,task3] [--budget 60000]

流程（每个任务）:

1. 从 ``target-template`` 重置一个干净的靶场副本。
2. 应用任务变异（启用注释测试 / 注入错误断言）。
3. 通过 stdio JSON-RPC 给 agent 发 ``send_message``（带预算与幂等键）。
4. 收集 receipt（text / iterations / usage / verification）。
5. 跑验收命令（pytest），对照期望。
6. 全部任务完成后写 ``evals/report-<stamp>.md``。

退出码：0 = 全部验收通过；1 = 有失败；2 = 环境错误。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

EVALS_DIR = Path(__file__).resolve().parent
TEMPLATE = EVALS_DIR / "target-template"
TASKS_FILE = EVALS_DIR / "tasks.json"


# ---------------------------------------------------------------------------
# Task mutations
# ---------------------------------------------------------------------------


def _enable_block(path: Path, marker: str) -> None:
    """Uncomment a ``# TASK-...:`` commented block in a test file."""
    lines = path.read_text(encoding="utf-8").splitlines()
    out: list[str] = []
    in_block = False
    for line in lines:
        if f"# {marker}" in line:
            in_block = True
            continue
        if in_block:
            if line.startswith("# ") or line == "#":
                out.append(line[2:] if line.startswith("# ") else "")
                continue
            in_block = False
        out.append(line)
    path.write_text("\n".join(out) + "\n", encoding="utf-8")


def _break_test_discount(path: Path) -> None:
    """TASK-3 mutation: the code is right, the test expectation is wrong."""
    src = path.read_text(encoding="utf-8")
    src = src.replace(
        "def test_ten_percent_tier():\n    assert discount.discount_rate(15) == 0.10",
        "def test_ten_percent_tier():\n    assert discount.discount_rate(15) == 0.0  # broken expectation",
    )
    path.write_text(src, encoding="utf-8")


def apply_mutation(run_dir: Path, mutation: str | None) -> None:
    if mutation == "enable_test_orders":
        _enable_block(run_dir / "test_orders.py", "TASK-2:")
    elif mutation == "enable_test_inventory":
        _enable_block(run_dir / "test_inventory.py", "TASK-5:")
    elif mutation == "enable_test_discount_boundary":
        _enable_block(run_dir / "test_discount.py", "TASK-6:")
    elif mutation == "break_test_discount":
        _break_test_discount(run_dir / "test_discount.py")
    elif mutation is not None:
        raise ValueError(f"unknown mutation: {mutation}")


# ---------------------------------------------------------------------------
# Agent subprocess (stdio JSON-RPC) — mirrors tests/e2e/smoke_sessions.py
# ---------------------------------------------------------------------------


class AgentClient:
    def __init__(self, proc: asyncio.subprocess.Process) -> None:
        self.proc = proc
        self._next_id = 0

    @classmethod
    async def start(cls, agent_dir: Path, data_dir: Path, workspace: Path) -> "AgentClient":
        import os

        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "minimax_code", "--stdio",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=None,  # inherit — diagnostics
            cwd=agent_dir,  # never cwd into the workspace: the agent
            # process would lock the directory and block per-task resets
            env={
                **os.environ,
                "PYTHONPATH": str(agent_dir),
                "MINIMAX_CODE_DATA_DIR": str(data_dir),
                "MINIMAX_CODE_LOG_LEVEL": "WARNING",
                "MINIMAX_CODE_WORKSPACE": str(workspace),
            },
        )
        return cls(proc)

    async def call(self, method: str, params: dict | None = None,
                   timeout: float = 30.0) -> dict:
        self._next_id += 1
        req_id = f"eval-{self._next_id}"
        line = json.dumps({"jsonrpc": "2.0", "id": req_id,
                           "method": method, "params": params or {}},
                          ensure_ascii=False) + "\n"
        assert self.proc.stdin is not None
        self.proc.stdin.write(line.encode("utf-8"))
        await self.proc.stdin.drain()
        deadline = time.time() + timeout
        assert self.proc.stdout is not None
        while time.time() < deadline:
            try:
                raw = await asyncio.wait_for(self.proc.stdout.readline(),
                                             timeout=max(0.05, deadline - time.time()))
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

    async def close(self) -> None:
        try:
            await self.call("shutdown", {}, timeout=10)
        except Exception:
            pass
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=10)
        except TimeoutError:
            self.proc.kill()


# ---------------------------------------------------------------------------
# Evaluation loop
# ---------------------------------------------------------------------------


def _force_remove(func, path, _exc) -> None:
    """Windows: .git pack objects carry the read-only bit."""
    import stat

    Path(path).chmod(stat.S_IWRITE)
    func(path)


def reset_target(run_dir: Path) -> None:
    if run_dir.exists():
        shutil.rmtree(run_dir, onerror=_force_remove)
    shutil.copytree(TEMPLATE, run_dir)
    # A fresh git repo per task — the agent's baseline checkpoint and
    # merge tooling expect one, and it keeps runs independent.
    subprocess.run(["git", "init", "-q"], cwd=run_dir, check=True)
    subprocess.run(["git", "config", "user.email", "eval@example.com"], cwd=run_dir, check=True)
    subprocess.run(["git", "config", "user.name", "eval"], cwd=run_dir, check=True)
    subprocess.run(["git", "add", "."], cwd=run_dir, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "pristine"], cwd=run_dir, check=True)


def run_acceptance(run_dir: Path, acceptance: dict) -> dict:
    proc = subprocess.run(
        acceptance["command"], shell=True, cwd=run_dir,
        capture_output=True, text=True, timeout=180,
    )
    passed = proc.returncode == 0
    require_file = acceptance.get("require_file")
    if passed and require_file:
        passed = (run_dir / require_file).exists()
    return {"passed": passed, "returncode": proc.returncode,
            "tail": (proc.stdout + proc.stderr)[-800:]}


async def run_eval(args: argparse.Namespace) -> int:
    spec = json.loads(TASKS_FILE.read_text(encoding="utf-8"))
    wanted = [w.strip() for w in args.tasks.split(",")] if args.tasks else None
    tasks = [t for t in spec["tasks"]
             if not wanted or any(t["id"] == w or t["id"].startswith(w) for w in wanted)]
    if not tasks:
        print("no tasks matched", file=sys.stderr)
        return 2

    agent_dir = EVALS_DIR.parent / "agent"
    run_root = EVALS_DIR / "runs"
    run_root.mkdir(parents=True, exist_ok=True)
    data_dir = EVALS_DIR / ".data"
    data_dir.mkdir(parents=True, exist_ok=True)
    # A unique workspace per invocation: a leftover agent process from a
    # previous (killed) run holds the old dir open on Windows and would
    # block rmtree.
    workspace = run_root / f"workspace-{time.strftime('%H%M%S')}"
    reset_target(workspace)

    client = await AgentClient.start(agent_dir, data_dir, workspace)
    await asyncio.sleep(1.0)  # boot + migrate

    # Configure the provider through the product path (the same RPC the
    # settings UI uses) so the eval exercises real configuration.
    r = await client.call("provider.create", {
        "name": "eval-provider", "protocol": "anthropic",
        "base_url": args.base_url, "models": [{"id": args.model}],
    })
    if "error" in r:
        print("provider.create failed:", r["error"], file=sys.stderr)
        await client.close()
        return 2
    provider_id = r["result"]["provider"]["id"]
    r_key = await client.call("provider.set_api_key", {"provider_id": provider_id,
                                                       "api_key": args.api_key})
    print(f"[eval] set_api_key -> {json.dumps(r_key.get('result') or r_key.get('error'), ensure_ascii=False)[:200]}",
          flush=True)
    r_cur = await client.call("model.set_current", {"model_id": args.model,
                                                    "provider_id": provider_id})
    print(f"[eval] set_current -> {json.dumps(r_cur.get('result') or r_cur.get('error'), ensure_ascii=False)[:300]}",
          flush=True)
    current = await client.call("model.get_current", {}, timeout=10)
    print(f"[eval] model.get_current -> {json.dumps(current.get('result'), ensure_ascii=False)[:300]}",
          flush=True)

    # Key probe: one real call up front. A dead key should fail HERE,
    # not after six tasks have burned their budgets on 401s. The probe
    # budget must exceed the max-output projection (32768) or the M2
    # budget gate pauses the run BEFORE any API call — which proves
    # nothing about the key.
    probe_session = f"ses-probe-{int(time.time())}"
    probe = await client.call("agent.send_message", {
        "session_id": probe_session,
        "content": "Reply with exactly: OK",
        "idempotency_key": f"eval-probe-{int(time.time())}",
        "budget_tokens": 45000,
    }, timeout=120)
    probe_receipt = probe.get("result") or {}
    key_proven = (
        probe.get("error") is None
        and not probe_receipt.get("stub")
        and not probe_receipt.get("budget_paused")
        and bool(str(probe_receipt.get("text", "")).strip())
    )
    if not key_proven:
        print("[eval] KEY PROBE FAILED — provider/model rejected the call:",
              json.dumps({"error": probe.get("error"),
                          "stub": probe_receipt.get("stub"),
                          "budget_paused": probe_receipt.get("budget_paused"),
                          "text": str(probe_receipt.get("text", ""))[:200]},
                         ensure_ascii=False), flush=True)
        print("[eval] fix the API key (or its balance) and re-run.", flush=True)
        await client.close()
        return 2
    print("[eval] key probe OK — model answered:", str(probe_receipt.get("text", ""))[:80], flush=True)

    # Pre-authorise exec for unattended evaluation. The factory default
    # is exec_* → ask: nobody is watching the consent modal, so every
    # pytest would eat a 300s consent timeout before being denied.
    r_perm = await client.call("permission.set", {
        "tool_pattern": "exec_command", "action": "allow", "scope": "global",
    })
    print(f"[eval] permission.set exec_command -> "
          f"{json.dumps(r_perm.get('result') or r_perm.get('error'), ensure_ascii=False)[:150]}",
          flush=True)

    results: list[dict] = []
    archive_root = run_root / "archive"
    archive_root.mkdir(parents=True, exist_ok=True)
    for task in tasks:
        task_id = task["id"]
        reset_target(workspace)
        apply_mutation(workspace, task.get("mutation"))
        # Cold-start semantics: a fresh session per task — the model must
        # not carry earlier tasks' history into an independent fix.
        session_id = f"ses-eval-{task_id}-{int(time.time())}"
        print(f"\n=== {task_id} ===", flush=True)
        t0 = time.time()
        # send_message auto-creates the session row on first use.
        reply = await client.call("agent.send_message", {
            "session_id": session_id,
            "content": task["prompt"],
            "idempotency_key": f"eval-{task_id}",
            "budget_tokens": args.budget,
        }, timeout=args.timeout)
        duration = time.time() - t0

        receipt = reply.get("result") or {}
        error = reply.get("error")
        print(f"[eval] receipt: stub={receipt.get('stub')} cancelled={receipt.get('cancelled')} "
              f"text[:150]={str(receipt.get('text', ''))[:150]!r}", flush=True)
        acceptance = run_acceptance(workspace, task["acceptance"])
        # Archive the run's workspace for post-hoc review — the next
        # task's reset would otherwise destroy the evidence.
        archive_dir = archive_root / task_id
        if archive_dir.exists():
            shutil.rmtree(archive_dir,
                          onerror=lambda f, p, e: (Path(p).chmod(0o200), f(p)))
        shutil.copytree(workspace, archive_dir,
                        ignore=shutil.ignore_patterns(".git"))
        verification = receipt.get("verification") or {}
        record = {
            "id": task_id,
            "category": task["category"],
            "rpc_error": error,
            "budget_paused": receipt.get("budget_paused"),
            "cancelled": receipt.get("cancelled"),
            "iterations": receipt.get("iterations"),
            "tokens_in": receipt.get("tokens_in"),
            "tokens_out": receipt.get("tokens_out"),
            "verification": {
                "tool_calls": verification.get("tool_calls"),
                "failed_tool_calls": verification.get("failed_tool_calls"),
                "requires_review": verification.get("requires_review"),
                "validation": (verification.get("validation") or {}).get("ok")
                if verification.get("validation") else None,
            },
            "duration_s": round(duration, 1),
            "acceptance": acceptance,
        }
        results.append(record)
        verdict = "PASS" if acceptance["passed"] else "FAIL"
        print(f"  -> {verdict} in {duration:.0f}s "
              f"(iters={record['iterations']} tokens={record['tokens_in']}+{record['tokens_out']} "
              f"review={record['verification']['requires_review']})", flush=True)

    await client.close()

    # ---- report -------------------------------------------------------
    stamp = time.strftime("%Y%m%d-%H%M%S")
    report = EVALS_DIR / f"report-{stamp}.md"
    passed_n = sum(1 for r in results if r["acceptance"]["passed"])
    lines = [
        f"# MiniMax Code 真实评测报告 — {stamp}",
        "",
        f"- 模型：{args.model} @ {args.base_url}",
        f"- 任务：{len(results)}，验收通过：{passed_n}（{passed_n / len(results):.0%}）",
        f"- 每任务预算：{args.budget} tokens",
        "",
        "| 任务 | 类别 | 验收 | 迭代 | tokens | 失败工具 | 需复核 | 自动验证 | 耗时 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        v = r["verification"]
        lines.append(
            f"| {r['id']} | {r['category']} | {'✅' if r['acceptance']['passed'] else '❌'} "
            f"| {r['iterations']} | {r['tokens_in']}+{r['tokens_out']} "
            f"| {v['failed_tool_calls']} | {v['requires_review']} | {v['validation']} "
            f"| {r['duration_s']}s |")
    lines.append("")
    for r in results:
        if not r["acceptance"]["passed"] or r["rpc_error"]:
            lines.append(f"## 失败样本：{r['id']}")
            lines.append(f"- rpc_error: `{r['rpc_error']}`")
            lines.append(f"- acceptance tail:\n```\n{r['acceptance']['tail']}\n```\n")
    report.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nreport -> {report}")
    print(f"PASSED {passed_n}/{len(results)}")
    return 0 if passed_n == len(results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--base-url", default="https://open.bigmodel.cn/api/anthropic")
    parser.add_argument("--model", default="glm-4.6")
    parser.add_argument("--tasks", default=None, help="逗号分隔的任务 id 子集")
    parser.add_argument("--budget", type=int, default=60000, help="每任务 token 预算")
    parser.add_argument("--timeout", type=float, default=600, help="单任务 RPC 超时秒数")
    args = parser.parse_args()
    try:
        return asyncio.run(run_eval(args))
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
