# MiniMax Code 编程 pilot 评测

`run_eval.py` 是 **6 项编程 pilot**：修 bug、跨文件、修测试、重构、边界、回归各 1 项，可选子集。它不是 40 项正式评测，也不代表三轮质量门禁。历史报告保留原始口径，不因 harness 更新而重写。

## 用法

```bash
cd agent && uv run python ../evals/run_eval.py \
    --api-key <你的 key> \
    --base-url https://open.bigmodel.cn/api/anthropic \
    --model glm-4.6 \
    [--tasks task1,task3] \
    [--budget 60000] [--timeout 600]
```

Provider、key、model 通过产品 RPC 配置；每任务从 `target-template` 重置靶场并建立独立 git 基线。真实执行会产生模型费用，离线 harness 测试不需要 key。

## 判定口径与产出

`evals/report-<时间戳>.md` 分别报告：

- **制品验收** `artifact_passed`：独立外部验收结果，保留原值；未运行时为 `null`。
- **执行** `execution_status`：优先级为 `rpc_error`、`cancelled`、`budget_paused`、`truncated`、`stub`、`completed`；回执缺失或无有效完成状态为 `unknown`，批次中未执行任务为 `not_run`。
- **证据** `evidence_status`：制品失败为 `failed`；`requires_review=true` 或自动验证 `validation.ok=false` 为 `requires_review`；缺少验证证据为 `unknown`；其余有独立验收支撑的回执为 `externally_verified`。自动验证仅在配置命令且修改文件时运行，因此单独缺少 `validation` 不判失败。
- **完整交付** `delivery_passed`：制品通过、执行完成、证据为 `externally_verified` 必须同时满足。
- **用量** `usage_status`：输入和输出均为正整数（布尔值无效）才为 `reported`；缺失、无效或零均为 `incomplete_or_unknown`。保留回执原值，零显示 `0(未确认)`。`reported` 不是独立核实过的付费成本。
- **预算** `budget_status`：完整用量与有效预算快照一致（`used_tokens` 等于回执输入输出之和、快照上限与请求一致）且未超限，才为 `reported_within_limit`；明确超限为 `exceeded`；其余为 `unknown`。父子用量不一致不能算预算通过；快照缺少每日基线，启用每日上限时也不能据此确认。
- **总体** `overall_passed`：完整交付通过且预算为 `reported_within_limit`。

报告分别给出制品、完整交付、总体通过率，均以**全部请求任务**为分母；另列实际运行外部验收的制品通过率、未知/证据不足任务数、未执行任务数，以及原始回执和验收证据。

退出码：`0` 仅限所有请求任务总体通过；`1` 表示失败、未执行或证据不足；`2` 表示环境错误。

## 超时与工作区保留

RPC 超时、EOF 或 transport 中断时，harness 关闭/强制终止并等待自有 agent 进程退出，随后停止整个批次。agent 退出不能证明独立工具子进程已经停止写文件，因此该任务**不运行外部验收，也不复制为稳定归档**，记录 `passed=null`、`status=not_run`、`reason=workspace_quiescence_unconfirmed` 和原工作区路径。后续任务列为 `not_run`，不会重置或复用该工作区。普通 RPC 错误也采用这一保守策略。

## 费用边界

`--budget` 是每任务请求预算，回执提供的用量可能缺失、不完整或与父子共享预算不同；不能据此保证 6 项最多消费 36 万付费 token。启动时 key probe 会进行额外真实调用，其用量**不计入任务统计**，也不计入各任务预算。报告不能替代供应商账单或正式成本核算。

离线验证（以下命令均从仓库根目录运行）：

```bash
uv run --project agent pytest agent/tests/test_eval_verdict.py -q
ruff check --config agent/pyproject.toml evals/run_eval.py agent/tests/test_eval_verdict.py
```
