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

退出码：0 = 全部任务完整交付且预算证据充分；1 = 失败或证据不足；2 = 环境错误。
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
        self.stopped = False

    @classmethod
    async def start(cls, agent_dir: Path, data_dir: Path, workspace: Path) -> AgentClient:
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
        if self.stopped:
            raise RuntimeError("agent process has already stopped")
        self._next_id += 1
        req_id = f"eval-{self._next_id}"
        line = json.dumps({"jsonrpc": "2.0", "id": req_id,
                           "method": method, "params": params or {}},
                          ensure_ascii=False) + "\n"
        failure = "timeout"
        try:
            assert self.proc.stdin is not None
            self.proc.stdin.write(line.encode("utf-8"))
            await asyncio.wait_for(self.proc.stdin.drain(), timeout=timeout)
            deadline = time.monotonic() + timeout
            assert self.proc.stdout is not None
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(self.proc.stdout.readline(),
                                                 timeout=max(0.001, deadline - time.monotonic()))
                except TimeoutError:
                    break
                if not raw:
                    failure = "EOF"
                    break
                try:
                    obj = json.loads(raw.decode("utf-8").strip())
                except json.JSONDecodeError:
                    continue
                if isinstance(obj, dict) and obj.get("id") == req_id:
                    return obj
        except TimeoutError:
            failure = "timeout"
        except (OSError, ConnectionError) as exc:
            failure = f"transport interruption ({type(exc).__name__})"
        # A timed-out read does not cancel the backend run. Reap this owned
        # process and skip validation/archive/reset of its workspace: detached
        # tool writers may outlive the parent process.
        await self.close(force=True)
        return {"error": {"code": -32000, "message": f"{failure} waiting for {method}",
                          "data": {"agent_stopped": True}}}

    async def close(self, *, force: bool = False) -> None:
        if self.stopped:
            return
        if not force and self.proc.returncode is None:
            try:
                await self.call("shutdown", {}, timeout=10)
            except Exception:
                pass
        if self.stopped:
            return
        if self.proc.stdin is not None:
            self.proc.stdin.close()
        if force and self.proc.returncode is None:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(self.proc.wait(), timeout=10)
        except TimeoutError:
            try:
                self.proc.kill()
            except ProcessLookupError:
                pass
            # kill() alone is not evidence that the process exited.
            await self.proc.wait()
        self.stopped = True


def _integer(value: object, *, positive: bool = False) -> bool:
    return type(value) is int and value >= (1 if positive else 0)


def execution_status(reply: dict) -> str:
    """Classify explicit receipt state; absence is never proof of completion."""
    if reply.get("error") is not None:
        return "rpc_error"
    receipt = reply.get("result")
    if not isinstance(receipt, dict):
        return "unknown"
    flags = ("cancelled", "budget_paused", "truncated", "stub")
    for flag in flags:
        if receipt.get(flag) is True:
            return flag
    if (all(type(receipt.get(flag)) is bool for flag in flags)
            and isinstance(receipt.get("text"), str)):
        return "completed"
    return "unknown"


def evidence_status(receipt: object, artifact_passed: bool | None) -> str:
    if artifact_passed is False:
        return "failed"
    if artifact_passed is not True:
        return "unknown"
    verification = receipt.get("verification") if isinstance(receipt, dict) else None
    if not isinstance(verification, dict) or not verification:
        return "unknown"
    validation = verification.get("validation")
    if (verification.get("requires_review") is True
            or isinstance(validation, dict) and validation.get("ok") is False):
        return "requires_review"
    # Automatic validation is optional: it only runs for configured projects
    # when files were touched. The independent acceptance already ran here.
    if type(verification.get("requires_review")) is not bool:
        return "unknown"
    if validation is not None and (
        not isinstance(validation, dict) or type(validation.get("ok")) is not bool
    ):
        return "unknown"
    return "externally_verified"


def usage_status(receipt: object) -> str:
    if isinstance(receipt, dict) and all(
        _integer(receipt.get(field), positive=True) for field in ("tokens_in", "tokens_out")
    ):
        return "reported"
    return "incomplete_or_unknown"


def budget_status(receipt: object, budget_tokens: object) -> str:
    """Check reported accounting consistency, not independently measured cost."""
    if not isinstance(receipt, dict) or not _integer(budget_tokens, positive=True):
        return "unknown"
    snapshot = receipt.get("budget")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    used = snapshot.get("used_tokens")
    limit = snapshot.get("limit_tokens")
    tokens_in, tokens_out = receipt.get("tokens_in"), receipt.get("tokens_out")
    total = tokens_in + tokens_out if all(
        _integer(value) for value in (tokens_in, tokens_out)
    ) else None
    # A clear reported overrun cannot become a pass through missing evidence.
    if ((total is not None and total > budget_tokens)
            or _integer(used) and used > budget_tokens
            or _integer(used) and _integer(limit, positive=True) and used > limit):
        return "exceeded"
    if (usage_status(receipt) != "reported" or not _integer(used)
            or not _integer(limit, positive=True) or limit != budget_tokens
            or used != total or not _integer(snapshot.get("pauses"))):
        return "unknown"
    # The snapshot omits daily baseline, so an active daily cap cannot be
    # certified from per-run totals. Child spend mismatches also stay unknown.
    if snapshot.get("daily_limit") is not None:
        return "unknown"
    return "reported_within_limit"


def evaluate_verdict(reply: dict, acceptance: dict, budget_tokens: int) -> dict:
    receipt = reply.get("result")
    artifact = acceptance.get("passed")
    execution = execution_status(reply)
    evidence = evidence_status(receipt, artifact)
    usage = usage_status(receipt)
    budget = budget_status(receipt, budget_tokens)
    delivery = artifact is True and execution == "completed" and evidence == "externally_verified"
    return {
        "artifact_passed": artifact, "execution_status": execution,
        "evidence_status": evidence, "delivery_passed": delivery,
        "usage_status": usage, "budget_status": budget,
        "overall_passed": delivery and budget == "reported_within_limit",
    }


def summarize_results(results: list[dict], requested_total: int) -> dict:
    executed = sum(record["execution_status"] != "not_run" for record in results)
    not_executed = max(0, requested_total - executed)
    return {
        "requested_total": requested_total, "executed": executed,
        "measured": sum(type(record["artifact_passed"]) is bool for record in results),
        "not_executed": not_executed,
        **{key: sum(record[key] is True for record in results)
           for key in ("artifact_passed", "delivery_passed", "overall_passed")},
        "unknown": max(0, requested_total - len(results)) + sum(
            record["execution_status"] in {"unknown", "not_run"} or record["evidence_status"] == "unknown"
            or record["usage_status"] != "reported" or record["budget_status"] == "unknown"
            for record in results
        ),
    }


def evaluation_exit_code(results: list[dict], requested_total: int) -> int:
    return 0 if requested_total > 0 and len(results) == requested_total and all(
        record["overall_passed"] is True for record in results
    ) else 1


def _display_usage(value: object) -> str:
    raw = json.dumps(value, ensure_ascii=False)
    return raw if _integer(value, positive=True) else f"{raw}(未确认)"


def render_report(
    results: list[dict], requested_total: int, args: argparse.Namespace, stamp: str,
) -> str:
    counts = summarize_results(results, requested_total)
    lines = [
        f"# MiniMax Code 真实评测报告 — {stamp}", "",
        "- 范围：6 项编程 pilot（可选子集）；不是 40 项正式评测或三轮质量门禁。",
        f"- 模型：{args.model} @ {args.base_url}",
        f"- 请求任务：{requested_total}；已执行：{counts['executed']}；未执行：{counts['not_executed']}",
    ]
    for label, key in (("制品验收通过率", "artifact_passed"),
                       ("完整交付通过率", "delivery_passed"), ("总体通过率", "overall_passed")):
        count = counts[key]
        rate = count / requested_total if requested_total else 0
        lines.append(f"- {label}：{count}/{requested_total}（{rate:.0%}；分母为请求任务）")
    measured_rate = counts["artifact_passed"] / counts["measured"] if counts["measured"] else 0
    lines.append(f"- 实际测量制品验收：{counts['artifact_passed']}/{counts['measured']}"
                 f"（{measured_rate:.0%}；仅包含已运行外部验收任务）")
    lines.extend([
        f"- 证据未知/不足任务数：{counts['unknown']}（含未执行任务）",
        f"- 每任务请求预算：{args.budget} tokens；usage/budget 为回执报告值，非独立成本核算。",
        "- Key probe 用量未纳入任务统计；预算参数不是总付费 token 的硬上限。", "",
        "| 任务 | 类别 | 制品验收 | 执行 | 证据 | 完整交付 | 用量状态 | 预算 | 总体 | 迭代 | tokens | 耗时 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ])
    for record in results:
        receipt = record["receipt"] if isinstance(record["receipt"], dict) else {}
        tokens = "+".join(_display_usage(receipt.get(field))
                          for field in ("tokens_in", "tokens_out"))
        lines.append(
            f"| {record['id']} | {record['category']} | {record['artifact_passed']} "
            f"| {record['execution_status']} | {record['evidence_status']} "
            f"| {record['delivery_passed']} | {record['usage_status']} | {record['budget_status']} "
            f"| {record['overall_passed']} | {receipt.get('iterations')} | {tokens} "
            f"| {record['duration_s']}s |")
    lines.append("")
    for record in results:
        lines.extend([
            f"## 任务证据：{record['id']}",
            # Raw values preserve missing/null/zero/invalid accounting evidence.
            "```json", json.dumps({
                "rpc_error": record["rpc_error"], "receipt": record["receipt"],
                "acceptance": record["acceptance"],
                "workspace_path": record.get("workspace_path"),
            }, ensure_ascii=False, indent=2), "```", "",
        ])
    return "\n".join(lines)


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
    try:
        await asyncio.sleep(1.0)  # boot + migrate
        return await _run_with_client(client, tasks, workspace, run_root, args)
    finally:
        await client.close()


async def _run_with_client(
    client: AgentClient, tasks: list[dict], workspace: Path, run_root: Path,
    args: argparse.Namespace,
) -> int:
    # Configure the provider through the product path (the same RPC the
    # settings UI uses) so the eval exercises real configuration.
    r = await client.call("provider.create", {
        "name": "eval-provider", "protocol": "anthropic",
        "base_url": args.base_url, "models": [{"id": args.model}],
    })
    if "error" in r:
        print("provider.create failed:", r["error"], file=sys.stderr)
        return 2
    provider_id = r["result"]["provider"]["id"]
    r_key = await client.call("provider.set_api_key", {"provider_id": provider_id,
                                                       "api_key": args.api_key})
    print(f"[eval] set_api_key -> {json.dumps(r_key.get('result') or r_key.get('error'), ensure_ascii=False)[:200]}",
          flush=True)
    if r_key.get("error") is not None:
        return 2
    r_cur = await client.call("model.set_current", {"model_id": args.model,
                                                    "provider_id": provider_id})
    print(f"[eval] set_current -> {json.dumps(r_cur.get('result') or r_cur.get('error'), ensure_ascii=False)[:300]}",
          flush=True)
    if r_cur.get("error") is not None:
        return 2
    current = await client.call("model.get_current", {}, timeout=10)
    print(f"[eval] model.get_current -> {json.dumps(current.get('result'), ensure_ascii=False)[:300]}",
          flush=True)
    if current.get("error") is not None:
        return 2

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
    probe_receipt = probe.get("result")
    probe_receipt = probe_receipt if isinstance(probe_receipt, dict) else {}
    key_proven = (
        execution_status(probe) == "completed"
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
    if r_perm.get("error") is not None:
        return 2

    results = await run_tasks(client, tasks, workspace, run_root / "archive", args)
    await client.close()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    report = EVALS_DIR / f"report-{stamp}.md"
    report.write_text(render_report(results, len(tasks), args, stamp), encoding="utf-8")
    counts = summarize_results(results, len(tasks))
    print(f"\nreport -> {report}")
    print(f"ARTIFACT {counts['artifact_passed']}/{len(tasks)}; "
          f"DELIVERY {counts['delivery_passed']}/{len(tasks)}; "
          f"OVERALL {counts['overall_passed']}/{len(tasks)}; "
          f"NOT EXECUTED {counts['not_executed']}")
    return evaluation_exit_code(results, len(tasks))


async def run_tasks(
    client: AgentClient, tasks: list[dict], workspace: Path, archive_root: Path,
    args: argparse.Namespace,
) -> list[dict]:
    results: list[dict] = []
    archive_root.mkdir(parents=True, exist_ok=True)
    for task_index, task in enumerate(tasks):
        task_id = task["id"]
        reset_target(workspace)
        apply_mutation(workspace, task.get("mutation"))
        session_id = f"ses-eval-{task_id}-{int(time.time())}"
        print(f"\n=== {task_id} ===", flush=True)
        t0 = time.time()
        reply = await client.call("agent.send_message", {
            "session_id": session_id,
            "content": task["prompt"],
            "idempotency_key": f"eval-{task_id}",
            "budget_tokens": args.budget,
        }, timeout=args.timeout)
        duration = time.time() - t0
        # Stopping the parent does not prove detached tools stopped writing.
        # On interrupted transport, preserve the workspace without inspecting
        # or copying it, and never reuse it for another case in this batch.
        interrupted = reply.get("error") is not None
        if interrupted:
            await client.close(force=True)
            acceptance = {
                "status": "not_run", "passed": None,
                "reason": "workspace_quiescence_unconfirmed",
            }
        else:
            acceptance = run_acceptance(workspace, task["acceptance"])
            archive_dir = archive_root / task_id
            if archive_dir.exists():
                shutil.rmtree(archive_dir, onerror=_force_remove)
            shutil.copytree(workspace, archive_dir, ignore=shutil.ignore_patterns(".git"))
        receipt = reply.get("result")
        record = {
            "id": task_id, "category": task["category"],
            "rpc_error": reply.get("error"), "receipt": receipt,
            "duration_s": round(duration, 1), "acceptance": acceptance,
            "workspace_path": str(workspace),
            **evaluate_verdict(reply, acceptance, args.budget),
        }
        results.append(record)
        print(f"  -> artifact={record['artifact_passed']} "
              f"execution={record['execution_status']} overall={record['overall_passed']} "
              f"in {duration:.0f}s", flush=True)
        if interrupted or client.stopped:
            for skipped in tasks[task_index + 1:]:
                skipped_acceptance = {"status": "not_run", "passed": None,
                                      "reason": "batch_stopped_after_rpc_error"}
                results.append({
                    "id": skipped["id"], "category": skipped["category"],
                    "rpc_error": None, "receipt": None, "duration_s": 0,
                    "acceptance": skipped_acceptance,
                    **evaluate_verdict({}, skipped_acceptance, args.budget),
                    "execution_status": "not_run",
                })
            break
    return results


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
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"environment error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
