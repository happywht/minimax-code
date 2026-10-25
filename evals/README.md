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
- **执行** `execution_status`：RPC 错误优先；显式 `run_status` 的失败、取消、中断、预算暂停及未终止状态不能被文字或旧标志覆盖。`completed` 与取消/暂停标志矛盾时为 `inconsistent`，字段无效为 `unknown`。旧回执保守读取原标志；批次未执行为 `not_run`。
- **产品验证** `product_verification`：保留 version、verdict、reason、requires_review，单独展示产品判定。版本 1 的失败或要求核查阻止交付；未知版本保持未知。
- **证据** `evidence_status`：独立制品失败即失败；版本 1 的验证命令必须有成功退出证据，失败、超时、工具错误或要求核查不能被外部制品通过掩盖。产品明确未配置校验或引擎不支持时，独立验收可以验证交付，但产品限制继续展示。配置过的验证证据缺失不能按未配置处理。
- **完整交付** `delivery_passed`：制品通过、执行完成、证据为 `externally_verified` 必须同时满足。
- **用量** `usage_status`：保留根运行的输入/输出原值，布尔值不是 token 数。正数可报告；含零需要 observed Codex 证据或已结算请求明细与根组件吻合，Native 默认零不是证据。`recovery_interval` 单列 `interval_reported`，不能精确归到当前轮。未确认零仍显示 `0(未确认)`。
- **预算** `budget_status`：Schema037 按家族账本核算，包含子任务，不要求家族用量等于根用量。区分测得超限 `exceeded`、责任超限 `liability_exceeded`、未知责任/历史基线 `unresolved`、待结算或已核定但未测得用量 `incomplete`，以及证据无效 `unknown`。完整测得账目、请求上限匹配、适用日账充分才为 `reported_within_limit`；刚好等于上限仍属于本次未超限。旧 `used_tokens` 回执走独立兼容分类，不伪造新账本字段。
- **总体** `overall_passed`：完整交付通过且预算为 `reported_within_limit`。

报告分别给出制品、完整交付、总体通过率，均以**全部请求任务**为分母；另列实际运行外部验收的制品通过率、未知/证据不足任务数、未执行任务数，以及原始回执、`budget.get` 读回、家族/每日分类、执行方式和验收证据。根用量精确性、家族核算完整性和货币费用是不同维度。

有运行 ID 时只读补取 `budget.get`。启用每日上限时，按请求明细的实际预留日期补取各日账；今天余额不能证明昨天未超限。日账缺失、历史基线未知、其他家族未知请求、上限变化均不能认证通过。读回 RPC 错误停止批次。详细语义见 [评测核算说明](../docs/evaluation-accounting.md)。

每日认证还要求全日日账显式报告 `accounting_resolved=true` 与 `usage_complete=true`。
新版产品日账从同一事务内指定日期的全部家族请求计算这两个字段；数值为零仍可能有其他家族的零估算活动请求或已核定但未测得的请求。
这类请求令 `usage_complete=false`，不能认证总体通过。旧产品响应缺少字段时显示 `unknown`，不会默认完整。
两字段只说明快照时刻的账目，不证明随后没有新请求；`accounting_resolved=true` 也不代表活动请求已结束。
明确超限、未知责任和保留责任仍如实分类。

退出码：`0` 仅限所有请求任务总体通过；`1` 表示失败、未执行或证据不足；`2` 表示环境错误。

## 单 Agent profile、超时与工作区保留

执行任务前，harness 在隔离评测服务中设置并核实内置 `spawn_subagent=deny`，报告 profile `single-agent-pilot-v1`。无法验证时不发送任务。发现后代记录时，在制品验收、归档及下一次工作区重置前停止批次。profile 只约束本轮 pilot 的内置子 Agent，不是操作系统沙盒或通用后代清理证明；多 Agent 正式评测须等产品统一清理屏障验收。

回执必须能确认终态才进入制品检查。running、queued、等待批准/输入、unknown、矛盾或中断回执都停止批次；仅将其总体分数判为失败，不能证明工作区已停止写入。

RPC 超时、EOF 或 transport 中断时，harness 关闭/强制终止并等待自有 agent 进程退出，随后停止整个批次。agent 退出不能证明独立工具子进程已经停止写文件，因此该任务**不运行外部验收，也不复制为稳定归档**，记录 `passed=null`、`status=not_run`、`reason=workspace_quiescence_unconfirmed` 和原工作区路径。后续任务列为 `not_run`，不会重置或复用该工作区。普通 RPC 错误也采用这一保守策略。

## 费用边界

`--budget` 是每个新根任务家族的 token 请求上限。Native 是发送前估算预留，提供方实际计量可能超过估算；Codex 回执分类支持反应式用量，不能推理前预留其内部调用。报告展示 `estimated` / `reactive`，均不保证货币硬上限。价格未知保持 `null`，核定责任不能转成测得费用。

已移除额外付费 key probe，首个被请求任务直接呈现认证/模型失败；不会为探测另发一次模型调用。脚本目前启动 Native 六项 pilot，支持 Codex 回执分类不等于已经通过双引擎实测。历史报告不重写，不能套用本轮分类追认旧成绩。

离线验证（以下命令均从仓库根目录运行）：

```bash
uv run --project agent pytest agent/tests/test_eval_verdict.py -q
ruff check --config agent/pyproject.toml evals/run_eval.py agent/tests/test_eval_verdict.py
```
