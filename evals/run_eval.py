"""MiniMax Code 六项编程 pilot；不是四十项正式生产验收。

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
from datetime import date
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
    if receipt.get("error") is not None:
        return "failed"
    if "run_status" in receipt:
        status = receipt["run_status"]
        if not isinstance(status, str):
            return "unknown"
        if status in {"failed", "cancelled", "interrupted", "budget_paused", "queued",
                      "planning", "running", "awaiting_approval", "waiting_input"}:
            return status
        if status != "completed":
            return "unknown"
        if receipt.get("cancelled") is True or receipt.get("budget_paused") is True:
            return "inconsistent"
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
    if "version" in verification:
        product = product_verification(receipt)
        if product["status"] in {"failed", "requires_review"}:
            return product["status"]
        return "externally_verified" if product["status"] in {"passed", "unsupported"} else "unknown"
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


def product_verification(receipt: object) -> dict:
    raw = receipt.get("verification") if isinstance(receipt, dict) else None
    raw = raw if isinstance(raw, dict) else {}
    result = {key: raw.get(key) for key in ("version", "verdict", "reason", "requires_review")}
    status = "unknown"
    if raw.get("requires_review") is True:
        status = "requires_review"
    elif type(raw.get("version")) is int and raw["version"] == 1:
        validation = raw.get("validation")
        if raw.get("verdict") == "failed" or (
            type(raw.get("failed_tool_calls")) is int and raw["failed_tool_calls"] > 0
        ):
            status = "failed"
        elif raw.get("requires_review") is False:
            if (raw.get("verdict") == "passed" and isinstance(validation, dict)
                    and type(validation.get("exit_code")) is int and validation["exit_code"] == 0
                    and validation.get("ok") is True and validation.get("timed_out") is not True
                    and validation.get("error") is None):
                status = "passed"
            elif (raw.get("verdict") == "unknown" and isinstance(raw.get("reason"), str)
                  and raw["reason"] in {
                "not_configured", "engine_validation_not_supported",
            } and validation is None):
                status = "unsupported"
            elif isinstance(validation, dict) and (
                validation.get("ok") is False or validation.get("timed_out") is True
                or validation.get("error") is not None
                or type(validation.get("exit_code")) is int and validation["exit_code"] != 0
            ):
                status = "requires_review"
    result["status"] = status
    return result


def _attempt_usage(row: dict) -> dict:
    usage = row.get("usage")
    if isinstance(usage, str):
        try:
            usage = json.loads(usage)
        except (ValueError, TypeError):
            return {}
    return usage if isinstance(usage, dict) else {}


def usage_status(receipt: object, readback: object = None) -> str:
    if not isinstance(receipt, dict):
        return "incomplete_or_unknown"
    certainty = receipt.get("usage_certainty")
    if certainty == "recovery_interval":
        return "interval_reported" if all(_integer(receipt.get(field)) for field in (
            "tokens_in", "tokens_out")) else "incomplete_or_unknown"
    if certainty is not None and certainty != "observed":
        return "incomplete_or_unknown"
    if isinstance(receipt, dict) and all(
        _integer(receipt.get(field), positive=True) for field in ("tokens_in", "tokens_out")
    ):
        return "reported"
    if all(_integer(receipt.get(field)) for field in ("tokens_in", "tokens_out")):
        if receipt.get("engine") == "codex" and certainty == "observed":
            return "reported"
        rows = readback.get("attempts") if isinstance(readback, dict) else None
        run_id = receipt.get("run_id")
        if isinstance(rows, list) and isinstance(run_id, str) and run_id:
            owned = [row for row in rows if isinstance(row, dict)
                     and row.get("run_id") == run_id and row.get("state") != "released"]
            usages = [_attempt_usage(row) for row in owned]
            if owned and all(row.get("state") == "settled" for row in owned) and all(
                usage.get("complete") is True and all(_integer(usage.get(field))
                    for field in ("prompt_tokens", "completion_tokens")) for usage in usages
            ) and all(sum(usage[field] for usage in usages) == receipt[receipt_field]
                for field, receipt_field in (("prompt_tokens", "tokens_in"),
                                             ("completion_tokens", "tokens_out"))):
                return "reported"
    return "incomplete_or_unknown"


def _legacy_budget_status(receipt: object, budget_tokens: object) -> str:
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


def _daily_accounting(snapshot: dict, readback: object) -> dict:
    cap = snapshot.get("daily_limit")
    if cap is None and "daily_limit" in snapshot:
        return {"status": "not_configured", "days": []}
    if not _integer(cap, positive=True) or not isinstance(readback, dict):
        return {"status": "unknown", "reason": "daily_dates_unverified"}
    attempts = readback.get("attempts")
    if not isinstance(attempts, list) or not attempts or not all(isinstance(row, dict) for row in attempts):
        return {"status": "unknown", "reason": "reservation_dates_missing"}
    try:
        dates = {date.fromisoformat(row["reservation_day"]).isoformat() for row in attempts
                 if isinstance(row, dict) and row.get("state") != "released"}
    except (KeyError, TypeError, ValueError):
        return {"status": "unknown", "reason": "reservation_dates_invalid"}
    if not dates:
        return {"status": "unknown", "reason": "reservation_dates_missing"}
    day_values = readback.get("days", {})
    if not isinstance(day_values, dict):
        return {"status": "unknown", "reason": "daily_readback_invalid"}
    days = dict(day_values)
    current = readback.get("day")
    if isinstance(current, dict) and isinstance(current.get("day"), str):
        days[current["day"]] = current
    evidence, statuses = [], []
    for day in sorted(dates):
        state = days.get(day)
        status = "unknown"
        if isinstance(state, dict) and state.get("day") == day and all(
            _integer(state.get(key)) for key in ("known_tokens", "reserved_tokens", "unknown_attempts")
        ) and type(state.get("baseline_incomplete")) is bool:
            if state["known_tokens"] > cap:
                status = "exceeded"
            elif state["known_tokens"] + state["reserved_tokens"] > cap:
                status = "liability_exceeded"
            elif state["unknown_attempts"] or state["baseline_incomplete"]:
                status = "unresolved"
            elif (state["reserved_tokens"] or state.get("historical_liability") is not None
                  or state.get("usage_complete") is False):
                status = "incomplete"
            elif state.get("accounting_resolved") is not True or state.get("usage_complete") is not True:
                # A different family's zero-estimate active request is invisible
                # to numeric totals. Never infer complete daily usage from zero.
                status = "unknown"
            else:
                status = "reported_within_limit"
        statuses.append(status)
        evidence.append({"day": day, "state": state, "status": status})
    priority = ("exceeded", "liability_exceeded", "unknown", "unresolved", "incomplete")
    return {"status": next((item for item in priority if item in statuses), "reported_within_limit"),
            "limit_tokens": cap, "days": evidence}


def budget_evidence(receipt: object, budget_tokens: object, readback: object = None) -> dict:
    """Classify reported token accounting; never certify provider billing."""
    result = {"status": "unknown", "schema": "unknown", "enforcement": "unknown",
              "monetary_cost": None, "monetary_cost_status": "unknown"}
    if not isinstance(receipt, dict) or not _integer(budget_tokens, positive=True):
        return result
    snapshot = receipt.get("budget")
    if not isinstance(snapshot, dict):
        return {**result, "status": _legacy_budget_status(receipt, budget_tokens)}
    if not any(key in snapshot for key in ("known_tokens", "reserved_tokens", "family_id",
                                           "usage_complete", "accounting_resolved")):
        return {**result, "schema": "legacy", "status": _legacy_budget_status(receipt, budget_tokens)}
    result.update(schema="037", enforcement=snapshot.get("enforcement", "unknown"))
    # Readback failure must not erase a clear, already-reported overrun.
    original_known, original_cap = snapshot.get("known_tokens"), snapshot.get("limit_tokens")
    root_total = sum(receipt[field] for field in ("tokens_in", "tokens_out")) if all(
        _integer(receipt.get(field)) for field in ("tokens_in", "tokens_out")) else None
    if (_integer(original_known) and (original_known > budget_tokens
        or _integer(original_cap, positive=True) and original_known > original_cap)
        or root_total is not None and root_total > budget_tokens):
        return {**result, "status": "exceeded", "reason": "reported_usage_over_cap"}
    # Fresh readback may include descendant spend after the root receipt.
    if readback is not None:
        fresh = readback.get("budget") if isinstance(readback, dict) else None
        if not isinstance(fresh, dict) or fresh.get("family_id") != snapshot.get("family_id"):
            return {**result, "reason": "budget_readback_unavailable_or_mismatched"}
        fresh_known, fresh_cap = fresh.get("known_tokens"), fresh.get("limit_tokens")
        if _integer(fresh_known) and (fresh_known > budget_tokens or (
            _integer(fresh_cap, positive=True) and fresh_known > fresh_cap
        )):
            return {**result, "status": "exceeded", "reason": "measured_family_over_cap"}
        if fresh.get("daily_limit") != snapshot.get("daily_limit"):
            return {**result, "reason": "daily_cap_changed_since_receipt"}
        if _integer(original_known) and _integer(fresh_known) and fresh_known < original_known:
            return {**result, "reason": "measured_family_usage_regressed"}
        snapshot = fresh
    result.update(family={key: snapshot.get(key) for key in (
        "family_id", "limit_tokens", "known_tokens", "reserved_tokens", "unknown_attempts",
        "baseline_incomplete", "accounting_resolved", "usage_complete")},
        enforcement=snapshot.get("enforcement", "unknown"))
    known, reserved, cap = (snapshot.get(key) for key in ("known_tokens", "reserved_tokens", "limit_tokens"))
    if _integer(known) and known > budget_tokens:
        return {**result, "status": "exceeded", "reason": "measured_family_over_request_cap"}
    if (not isinstance(snapshot.get("family_id"), str) or not snapshot["family_id"]
            or not all(_integer(snapshot.get(key)) for key in ("known_tokens", "reserved_tokens", "unknown_attempts"))
            or not all(type(snapshot.get(key)) is bool for key in (
                "baseline_incomplete", "accounting_resolved", "usage_complete"))
            or not _integer(cap, positive=True) or cap != budget_tokens
            or not isinstance(result["enforcement"], str)
            or result["enforcement"] not in {"estimated", "reactive"}):
        return {**result, "reason": "malformed_or_changed_family_context"}
    daily = _daily_accounting(snapshot, readback)
    result["daily"] = daily
    if known + reserved > cap:
        status, reason = "liability_exceeded", "family_liability_over_cap"
    elif daily["status"] in {"exceeded", "liability_exceeded"}:
        status, reason = daily["status"], "daily_accounting_over_cap"
    elif snapshot["unknown_attempts"] or snapshot["baseline_incomplete"]:
        status, reason = "unresolved", "unknown_or_historical_family_liability"
    elif reserved or not snapshot["usage_complete"]:
        status, reason = "incomplete", "pending_or_authorized_family_liability"
    elif not snapshot["accounting_resolved"]:
        status, reason = "unknown", "inconsistent_resolution_flags"
    elif daily["status"] not in {"not_configured", "reported_within_limit"}:
        status, reason = daily["status"], "daily_evidence_insufficient"
    else:
        total = sum(receipt[field] for field in ("tokens_in", "tokens_out")) if all(
            _integer(receipt.get(field)) for field in ("tokens_in", "tokens_out")) else None
        status, reason = "reported_within_limit", "complete_measured_family"
        if total is not None and total > known:
            status, reason = "unknown", "root_usage_exceeds_family_usage"
        if known == 0 and usage_status(receipt, readback) != "reported":
            status, reason = "unknown", "zero_usage_provenance_missing"
    return {**result, "status": status, "reason": reason}


def budget_status(receipt: object, budget_tokens: object, readback: object = None) -> str:
    return budget_evidence(receipt, budget_tokens, readback)["status"]


def evaluate_verdict(reply: dict, acceptance: dict, budget_tokens: int, readback: object = None) -> dict:
    receipt = reply.get("result")
    artifact = acceptance.get("passed")
    execution = execution_status(reply)
    evidence = evidence_status(receipt, artifact)
    usage = usage_status(receipt, readback)
    accounting = budget_evidence(receipt, budget_tokens, readback)
    budget = accounting["status"]
    delivery = artifact is True and execution == "completed" and evidence == "externally_verified"
    return {
        "artifact_passed": artifact, "execution_status": execution,
        "evidence_status": evidence, "delivery_passed": delivery,
        "usage_status": usage, "budget_status": budget,
        "product_verification": product_verification(receipt), "budget_evidence": accounting,
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
            or record["usage_status"] != "reported"
            or record["budget_status"] not in {"reported_within_limit", "exceeded"}
            for record in results
        ),
    }


def evaluation_exit_code(results: list[dict], requested_total: int) -> int:
    return 0 if requested_total > 0 and len(results) == requested_total and all(
        record["overall_passed"] is True for record in results
    ) else 1


def _display_usage(value: object, *, measured: bool = False) -> str:
    raw = json.dumps(value, ensure_ascii=False)
    return raw if _integer(value, positive=not measured) else f"{raw}(未确认)"


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
        "- 不额外发送付费 key probe；首个请求任务直接报告认证或模型失败。",
        "- estimated/reactive 是 token 执行方式，货币费用未知；预算参数不是货币硬上限。",
        "- 评测 profile：single-agent-pilot-v1，仅在隔离评测服务中设置并验证内置 spawn_subagent=deny；不代表多 Agent 或系统沙盒验收。", "",
        "| 任务 | 类别 | 制品验收 | 执行 | 证据 | 产品验证 | 完整交付 | 用量状态 | 预算 | 执行方式 | 总体 | 迭代 | tokens | 耗时 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ])
    for record in results:
        receipt = record["receipt"] if isinstance(record["receipt"], dict) else {}
        tokens = "+".join(_display_usage(receipt.get(field), measured=record["usage_status"] == "reported")
                          for field in ("tokens_in", "tokens_out"))
        lines.append(
            f"| {record['id']} | {record['category']} | {record['artifact_passed']} "
            f"| {record['execution_status']} | {record['evidence_status']} "
            f"| {(record.get('product_verification') or {}).get('verdict')} "
            f"| {record['delivery_passed']} | {record['usage_status']} | {record['budget_status']} "
            f"| {(record.get('budget_evidence') or {}).get('enforcement', 'unknown')} "
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
                "product_verification": record.get("product_verification"),
                "budget_evidence": record.get("budget_evidence"),
                "budget_readback": record.get("budget_readback"),
                "evaluation_profile": record.get("evaluation_profile"),
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

    # Do not send an additional paid credential probe outside the task set.
    # The first actual requested task supplies authentication/model evidence.

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

    denied = await client.call("permission.set", {
        "tool_pattern": "spawn_subagent", "action": "deny", "scope": "global",
    })
    checked = await client.call("permission.check", {"tool_name": "spawn_subagent", "scope": "global"})
    rule = checked.get("result")
    if (denied.get("error") is not None or checked.get("error") is not None
            or not isinstance(rule, dict) or rule.get("allowed") is not False
            or rule.get("action") != "deny"):
        print("[eval] cannot verify single-agent pilot profile; no task started", file=sys.stderr)
        return 2
    args.evaluation_profile = {"id": "single-agent-pilot-v1", "verified": True,
                               "builtin_spawn_subagent": "deny"}

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
    profile = getattr(args, "evaluation_profile", None)
    if not isinstance(profile, dict) or profile.get("verified") is not True or profile != {
        "id": "single-agent-pilot-v1", "verified": True, "builtin_spawn_subagent": "deny",
    }:
        await client.close(force=True)
        return [_not_run(task, args.budget, "single_agent_profile_unverified") for task in tasks]
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
        receipt = reply.get("result")
        readback = None
        interrupted = reply.get("error") is not None
        if not interrupted and isinstance(receipt, dict) and receipt.get("run_id"):
            response = await collect_budget_readback(client, receipt["run_id"])
            readback = response.get("result") if response.get("error") is None else response
            interrupted = response.get("error") is not None or not isinstance(readback, dict)
        interrupted = (interrupted or not _terminal_receipt(receipt)
                       or _descendant_evidence(receipt, readback))
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
        record = {
            "id": task_id, "category": task["category"],
            "rpc_error": reply.get("error"), "receipt": receipt,
            "duration_s": round(duration, 1), "acceptance": acceptance,
            "workspace_path": str(workspace),
            "budget_readback": readback, "evaluation_profile": profile,
            **evaluate_verdict(reply, acceptance, args.budget, readback),
        }
        results.append(record)
        print(f"  -> artifact={record['artifact_passed']} "
              f"execution={record['execution_status']} overall={record['overall_passed']} "
              f"in {duration:.0f}s", flush=True)
        if interrupted or client.stopped:
            for skipped in tasks[task_index + 1:]:
                results.append(_not_run(skipped, args.budget, "batch_stopped_without_quiescence"))
            break
    return results


async def collect_budget_readback(client: AgentClient, run_id: str) -> dict:
    """Read the family and each actual reservation day without altering accounting."""
    response = await client.call("budget.get", {"run_id": run_id})
    readback = response.get("result")
    if response.get("error") is not None or not isinstance(readback, dict):
        return response
    snapshot = readback.get("budget")
    if not isinstance(snapshot, dict) or not _integer(snapshot.get("daily_limit"), positive=True):
        return response
    attempts = readback.get("attempts")
    if not isinstance(attempts, list) or not attempts or not all(isinstance(row, dict) for row in attempts):
        return response  # Classifier retains missing-date evidence as unknown.
    try:
        dates = {date.fromisoformat(row["reservation_day"]).isoformat() for row in attempts
                 if row.get("state") != "released"}
    except (KeyError, TypeError, ValueError):
        return response
    readback = {**readback, "days": {}, "day_readbacks": {}}
    current = readback.get("day")
    if isinstance(current, dict) and isinstance(current.get("day"), str):
        readback["days"][current["day"]] = current
    for day in sorted(dates - readback["days"].keys()):
        daily_response = await client.call("budget.get", {"run_id": run_id, "day": day})
        readback["day_readbacks"][day] = daily_response
        if daily_response.get("error") is not None:
            return {"error": daily_response["error"], "partial_readback": readback}
        daily = daily_response.get("result")
        if (isinstance(daily, dict) and isinstance(daily.get("day"), dict)
                and daily["day"].get("day") == day
                and isinstance(daily.get("budget"), dict)
                and daily["budget"].get("family_id") == snapshot.get("family_id")
                and daily["budget"].get("daily_limit") == snapshot.get("daily_limit")):
            readback["days"][day] = daily["day"]
    return {**response, "result": readback}


def _not_run(task: dict, budget: int, reason: str) -> dict:
    acceptance = {"status": "not_run", "passed": None, "reason": reason}
    return {"id": task["id"], "category": task["category"], "rpc_error": None,
            "receipt": None, "duration_s": 0, "acceptance": acceptance,
            **evaluate_verdict({}, acceptance, budget), "execution_status": "not_run"}


def _terminal_receipt(receipt: object) -> bool:
    state = execution_status({"result": receipt})
    terminal = {"completed", "failed", "cancelled", "budget_paused", "truncated", "stub"}
    if not isinstance(receipt, dict) or state not in terminal:
        return False
    if "run_status" in receipt:
        status = receipt["run_status"]
        return isinstance(status, str) and status in {"completed", "failed", "cancelled", "budget_paused"}
    return True


def _descendant_evidence(receipt: object, readback: object) -> bool:
    if not isinstance(receipt, dict):
        return False
    verification = receipt.get("verification")
    if isinstance(verification, dict) and verification.get("children"):
        return True
    if any(receipt.get(key) for key in ("children", "descendants", "subagents", "subagent_runs", "async_mode")):
        return True
    attempts = readback.get("attempts", []) if isinstance(readback, dict) else []
    return isinstance(attempts, list) and any(
        isinstance(row, dict) and row.get("run_id") != receipt.get("run_id")
        and row.get("state") != "released" for row in attempts)


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
