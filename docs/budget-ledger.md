# 共享 token 预算账本（Schema037）

本批是 M2/M5 的部分交付，不代表完整生产验收。账本管理 token 责任，价格及币种未知时保持 null；没有货币硬上限承诺。

Native 在每次实际 SDK 发送前，以当前转换后的 messages/tools、UTF-8 序列化字节数、256 framing 余量与强制 max_tokens 估算预留。实际用量可能超过估算；不支持 strict 上界保证。SDK 隐式重试关闭，应用层每次发送都需要独立预留。成功用量与 budget RunEvent 同事务落盘后才返回工具调用供派发。存储失败会停止本次调用和工具；流式文本可以先显示。

Codex 只能根据外部用量通知反应式中断，无法预留引擎内部的每次调用。线程累计水位、账本增量和事件同事务保存；重复通知不重复入账，新普通用户轮次使用新家族但沿用同线程水位。计数重置保留旧水位并暂停该线程；核定责任不能伪造可靠的外部水位，应使用新线程及经审查的交接。缺失用量保持未知，即使未配置上限也进入 budget_paused，需要工作区审查及明确责任核定后接续；之后的 recovery_interval 用量是自上次水位起整个间隔的增量，不能归作精确的新轮次用量。核定责任可能与之后的间隔用量保守叠加，不自动退款。当前 compact_history 是本地同步处理，不生成虚构付费请求。

普通新用户轮次和每个新 schedule occurrence 创建家族。子任务、递归子任务、重试、自动继续、显式 agent.continue 和经审查的 run.resume 使用原家族。提高 token 上限是新的累计授权，不能重置已知用量、预留或未知请求。先前预算暂停后可保留原运行的累计上限继续，或在 UI 输入新的累计上限；未设置上限也会原样保留。家族上限若已变化，账本会拒绝降低额度，需核对最新运行。日额度及未知责任仍须通过原有检查。

请求状态为 reserved → sent → settled；仅确证未发送才能 released。超时、EOF、取消、异常或崩溃后的 sent 请求保留 unknown 责任，部分用量仅作已知下界，不自动退款或盲目重发。reserved/sent 的估算与 settled 已知用量都占用额度，活动 sent 不阻塞有余额的并发请求。启动恢复在接受新工作前把遗留 reserved 释放、sent 标为 unknown；正常父任务结束不回收仍活动的异步子请求。已结束任务的责任继续保留。

本机应用日账与家族预留在 SQLite BEGIN IMMEDIATE 事务内同时检查。范围为本执行主机共享此数据库的应用服务，不包括无关 CLI/其他程序或整个提供方账户的支出。日账采用 Asia/Shanghai 的预留日期，跨午夜结算仍属于原日。旧日未知责任不污染一个干净的新日，但原家族的未知责任仍需处理。缓存/推理 token 按提供方的重叠关系归一化，推理 token 不再次加到总量；缺失字段不会变成测得的零。

## 查看与核对

任务时间线展示 known_tokens、reserved_tokens、unknown_attempts、remaining_estimate、enforcement、pause_reason、家族与日期。已知下界和核定责任分开；accounting_resolved 不表示 usage_complete。

已认证本地 RPC：

- budget.get 参数 `{run_id?, day?}`，返回 `{budget, day, attempts, day_blockers, coverage_started_at}`。budget 可为 null，表示旧任务尚无完整家族记录。attempts 仅属当前家族；day_blockers 返回当日未知请求总数及最多 20 项身份/家族/所属 run/session/已知下界，子任务无独立记录时回退根任务。UI 可跳转所属任务逐项核查，日历史核定不能清除其他家族的未知请求。
- budget.reconcile 参数 `{target: "day"|"family"|"attempt", target_id, liability_tokens, reason, acknowledged: true}`。责任不得小于已知下界。只允许核定 unknown 请求，活动 sent 不可核定；核定家族历史或请求责任时，任何共享该家族的排队、运行或等待中的任务都会阻止核定。此检查和核定在同一写事务中完成。责任核定留下时间、依据和 provenance=reconciled，保持原 usage/known_tokens 不变；核定本身不会续跑任务。

### 全日日账完整性

`day.accounting_resolved` 与 `day.usage_complete` 从指定预留日期的全部家族请求、历史基线
在同一事务计算，不能仅从 known/reserved/unknown 的数值推断。
覆盖期内空日或仅 settled/released 的日期完整；reserved/sent（包括零估算）不完整，
unknown 或未核对历史使责任未解决且用量不完整；reconciled（包括零责任）及已核定历史
表示责任已解决、实际用量仍未完整测得。活动请求可以 accounting_resolved=true，不能据此认定已经结束。

预算面板分别展示完整、未完整或旧响应的未知，并保留“已知至少”的下界表达。
完整性只描述当前快照，不能保证随后没有新请求。没有新增数据库结构或改动准入、结算及核定规则。
旧服务响应缺少字段时，前端和评测器均保留未知；mock 不生成虚假的完整零日账。

037 迁移记录覆盖起点，并把当日已有 assistant 历史或已启动运行视为历史基线不完整。已知历史仅是下界。设置日额度的付费调用在当日基线核对前失败关闭；没有历史的干净日期不会被无条件阻止。调整日额度不能清除未知。旧任务恢复形成 legacy_incomplete 家族，须明确核定历史责任后才可付费继续。

只读查询覆盖起点之前且无已核定日基线的日期时返回历史不完整，不把未跟踪历史认证为零。日/旧家族核定只允许在不完整状态进行一次，不能覆盖原授权依据。

预算结算落盘失败时，共享该数据库实例的所有家族暂停付费预留和发送，包括已经预留但尚未发送的子任务。持久 sent 责任保留，不伪造成功事件；需重启并成功完成启动恢复，随后逐项核定 unknown。启动恢复失败时应用可供诊断，但 recovery snapshot 的 available 为 false，付费治理持续关闭；移除临时存储故障本身不会绕过暂停。已发送的并发请求仍负责排空并保存自己的结果。

尚未统一接单的公开 team 付费根入口拒绝无账本发送，并返回失败。`skill.invoke`、`agent.invoke` 和 `agent.spawn_subagent` 已接入持久 Native 执行器，真实与离线/mock 调用都要求显式 Native 来源 session、非空字符串 request 和稳定 idempotency_key；旧无作用域调用不再兼容。每个公开 Agent 根建立新家族，受信工具子任务才继承真实父运行的家族；来源会话不是预算父运行。含 skills 依赖的 Agent 配置暂不支持公开执行。配置、子任务归属及恢复边界见 [skill-execution.md](skill-execution.md) 和 [agent-execution.md](agent-execution.md)。

## 迁移、备份与回退

升级前使用 data.backup 创建 SQLite 在线一致性快照并核实文件可读。037 增加 budget_families、budget_attempts、budget_days、budget_coverage，保留原运行和消息；备份完整数据库后再升级。回退需停止服务、保留升级后的库及 WAL/SHM 证据，恢复升级前的一致性 SQLite 快照并使用匹配的程序版本；不得删除预算表来解除责任。

Schema037 暂时拒绝 data.import 对预算表及 sessions/messages/agent_runs/agent_run_steps/run_events/schedule_occurrences/scheduled_jobs/tasks 的 JSON 替换，包括旧版本导出与完整 037 导出。拒绝发生在任何修改之前，避免旧快照静默抹除责任。无关 memory/model 设置部分导入继续支持，037 之前的恢复行为保持。data.export 与 SQLite data.backup 仍包含预算责任。

完整 JSON 灾难恢复兼容性是 **M7 必须完成的后续项**：需要账本合并/恢复围栏与核对流程；本批临时拒绝不是完整恢复能力。SQLite 快照不包括外部附件、凭据或工作区文件，恢复时必须分别核实。

### 日账完整性追加验证（2026-10-26）

本追加批次未改表结构、准入算法或模型调用方式。精确十文件（两共享文件仅日账片段）
从暂存区导出的独立源码快照中，预算/SDK/Codex/运行预算/评测/IPC 契约 **230 项通过**
（25.99 秒），预算面板 **8 项通过**，TypeScript、定点 ESLint、Ruff 及差异检查通过。
证据为 `.verification/budget-day-staged-backend.log`、`budget-day-staged-ui.log`、
`budget-day-staged-types.log`、`budget-day-eslint.log` 和 `budget-day-ruff.log`。
这不包含尚未提交的技能执行改动；工作区较早联合检查的文档 token 失败仍保留，
不将草稿修正与独立快照结果混记。没有付费模型或远程生产验收。

## 离线验证

测试选择器、完整结果和本批 55 个文件清单保存在 `.verification/budget-*.log` 与 `.verification/budget-context-files.txt`。覆盖双 SQLite 连接最后额度竞争、真实 SDK MockTransport、取消/崩溃/重开、结算幂等、跨午夜、历史基线、公开付费入口拒绝、Codex 水位/恢复及 UI 未知/核对行为。测试不发送付费模型请求。

最终记录：53 个后端模块共选择 825 项，824 项通过，唯一失败为旧迁移断言仍预期 `[35, 36]`；仅同步为 `[35, 36, 37]` 后，受影响 `test_task_outcomes.py` 的 41 项全部通过，原失败日志保留，没有把两次运行相加为独立测试数量。另有启动恢复 32 项通过。前端完整 106 个文件的 824 项全部通过，ESLint、TypeScript 构建与 Vite 生产构建通过，Python Ruff 与 diff 检查通过。真实浏览器的运行恢复和调度场景共 2 项通过；先前调度断言同时匹配任务行与 toast 的失败日志也保留，修正仅限定任务行选择器。完整后端记录为 `budget-backend-final.log`，迁移修正为 `budget-migration-sync-final.log`，浏览器最终记录为 `budget-browser-scheduler-rerun.log`。
