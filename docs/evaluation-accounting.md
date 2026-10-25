# 六项编程 pilot 的核算与交付证据

本批适配 `evals/run_eval.py`，只改变评测接入和判定，不修改产品账本或历史报告。
这是六项编程 pilot，不是路线图要求的四十项任务集、三轮候选验收或十四天日常试用。
脚本当前运行 Native；Codex 回执解析的离线通过不能替代真实双引擎执行。

## 分开读取的证据

| 维度 | 判定依据 | 不能替代的证据 |
|---|---|---|
| 执行 | RPC 错误、显式 run_status、取消/暂停/截断标志 | 模型文字“完成”不能覆盖失败或未终止状态 |
| 产品验证 | verification.version=1、verdict、reason、requires_review 和验证进程结果 | 已配置校验的缺失、失败、超时不能当作未配置 |
| 独立制品 | harness 独立执行的任务验收命令和文件检查 | 制品通过不说明执行、预算或恢复通过 |
| 根用量 | 输入/输出组件，observed 或已结算请求明细 | 默认零和 recovery_interval 不能证明当前轮精确用量 |
| 家族核算 | Schema037 known/reserved/unknown、历史基线和完整性 | 子任务用量可以使家族总量大于根用量；核定责任不是测得用量 |
| 每日核算 | 实际 reservation_day 的全机日账 | 当前日期余额不能证明其他日期、其他家族责任已完整结算 |
| 费用 | 原始定价信息，当前未知 | token 上限和 estimated/reactive 不是货币硬上限 |

`overall_passed` 要求执行完成、独立制品通过、产品验证没有阻断，以及预算证据充分。
根用量仍可因区间归属不精确而单独显示未知；家族核算完整与根用量精确不是同一件事。
未知产品版本保留未知，计划模式的 not_applicable 不能作为代码验收成功。
产品明确 not_configured 或 engine_validation_not_supported 时，独立验收可以补足制品验证，
但报告继续展示该产品限制。失败命令、工具错误或 requires_review 会阻止交付通过。

## Schema037 分类

| 状态 | 含义 |
|---|---|
| reported_within_limit | 家族用量完整测得、上限与请求一致、适用日账证据充分；等于上限仍在范围内 |
| exceeded | 已测得根/家族/适用日用量明确超过相关上限 |
| liability_exceeded | 已测得用量未超限，但加上保留或核定责任后超过上限 |
| unresolved | 未知请求或历史基线未核对 |
| incomplete | 仍有待结算请求，或核对责任已解决但实际用量并未完整测得 |
| unknown | 字段损坏、家族不匹配、上限变化、测得用量回退或证据不足 |

分类优先保留明确超限事实，读回失败不能擦除回执已有的超限。
`accounting_resolved=true` 不推出 `usage_complete=true`，核对操作不增加测得 token。
旧 used_tokens/pauses 仍走原兼容分类，不能补成虚构的 Schema037 证据。

每个有 run_id 的任务补读 budget.get；启用每日上限时，再按非 released 请求的实际日期
读取预算。日期读取响应单独保留在 day_readbacks，规范化结果在 days；缺失或不匹配的
日期不认证。另一个家族的 unknown 请求和历史 baseline_incomplete 也会阻断日账通过。
回执与读回的每日上限不同则证据未知，不能通过停用上限来追认先前任务。

每日通过必须同时具有显式 accounting_resolved=true 和 usage_complete=true。
产品日账现在从同一事务内、指定预留日期的全部家族请求计算这两个全日完整性字段；家族请求明细仍只包含本家族。
另一个家族的零估算 sent 或零责任 reconciled 会产生“reserved=0、unknown=0”但并未完整测量的日账。
活动请求或已核定但未测得的请求令 usage_complete=false，因而不能认证通过。
旧产品响应缺字段仍为 unknown，不能从本家族结算完毕或分页运行列表推导全机日账完整性。
覆盖期内无请求或仅 settled/released 的日期可完整；历史未核对日期两字段均为 false，历史核定后仍未完整测量。
accounting_resolved=true 只表示没有尚待核对的未知责任，不代表请求全部结束；这些都是快照时刻证据，不能保证随后没有新请求。

## 执行限制与工作区生命周期

执行任务前，通过现有权限 RPC 设置并验证内置 spawn_subagent=deny，报告
single-agent-pilot-v1。没有验证成功就不发任务。保留普通工具权限，未新增操作系统隔离。
这是对六项 pilot 能力的明确限制，不代表多 Agent 评测已经通过。

出现 RPC 中断、预算读取 RPC 失败或后代证据时，停止自有 Agent 并等待退出，
但不宣称全部独立写进程都已停止。跳过制品检查、稳定归档和后续重置，保留原工作区路径。
未来开放多 Agent 必须先验证产品统一递归清理；不能从账本完整或父进程退出推导清理完成。
同样，非终态、未知、矛盾或 interrupted 回执不进入制品验收或归档；
把它们分类为失败并不证明工作区静止，后续任务不得重置该目录。

不再发送额外付费 key probe。真实模型认证失败由首个被请求任务记录。
离线测试使用受控响应和临时 SQLite，不读取个人模型凭据、不调用付费模型。
历史 report 文件和之前失败日志均保留，不改写旧成绩。

## 离线验证

从仓库根目录运行：

```powershell
uv run --project agent pytest agent/tests/test_eval_verdict.py -q
ruff check --config agent/pyproject.toml evals/run_eval.py agent/tests/test_eval_verdict.py
```

覆盖实际临时 SQLite 的接单、父子预留、发送、结算、BudgetHandle 快照、未知及核对转移；
也覆盖失效回执、版本化产品验证、零用量来源、跨日读取、旧格式、profile 建立失败、
后代停止和无额外付费探测的完整受控 pipeline。通过证据记录于 .verification/eval-accounting-*.log。
这些是离线 harness 证据，不是当前产品完整回归、真实模型任务成功率或正式生产验收。

2026-10-26 本批记录：评测模块最终 **146 项通过**（1.84 秒），日志
`.verification/eval-accounting-final.log`。上一轮评测 145 项与真实账本、SDK 发送边界、
Codex 核算联合 **197 项通过**（20.38 秒），日志
`.verification/eval-accounting-integration-final.log`；随后只增加损坏状态与错误并存的回归及类型防护，
已由最终 146 项覆盖。两轮存在重叠，不累计为独立场景数。Ruff 与差异检查通过。
前轮夹具导致的 20 项失败保留在 `.verification/update-review-backend.log`，未改写为通过记录。
