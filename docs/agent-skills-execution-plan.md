# B2a.1：补齐 Agent 技能配置的执行能力

2026-10-26 后续实现设计，尚未实现或验收。前置是直接子 Agent 持久执行批次通过。
本批必须在 M3 完整编程闭环验收之前完成，不将暂时拒绝非空 skills 当成永久减少功能。

## 已核对的起点

原 `SubAgentConfig.skills` 是存储和透传的平面字符串列表，历史配置含短名；
既有 resolver 和 `SubAgentRuntime.build` 没有真正消费它。实际 Skill 提供 instructions、
tools、version 和 extra，模型由调用方选择。因此本批是补齐已声明配置的执行能力；
多技能组合的顺序及冲突规则须成为明确的新契约，不能称为恢复已实现的多技能循环。

保持单一 Agent 根：同一 Native Core、RunService、私有模型客户端、权限、预算家族和
工作副本。技能是该根的指令和工具配置，不调用 `SkillRuntime.invoke` 另起运行循环。
不从未定义的 extra 字段推导模型切换、依赖 DAG、Hooks 或自动安装命令。

## 服务器解析与冻结

新增小型内部 bindings helper，复用已有 B1 技能工具解析中不涉及会话或模型的部分：

1. 验证 skills 为非空字符串的列表；先精确匹配 composite ID，再兼容唯一短名。
   未知、歧义、重复或 disabled 配置明确拒绝，不静默跳过。
2. 保留配置顺序，保存 canonical ID、版本、冻结 instructions、manifest tools、
   实际 provider 工具名，以及类身份/工具 schema 指纹。
3. 仅使用已注册、受信 provider 的私有 deepcopy；不把秘密或 Provider URL 写入元数据。

指令顺序是 Agent instructions 在前，技能按冻结顺序在后。模型路由、迭代配置及预算
继续归 Agent 根；挂载 code-review 技能不会自动切换到 diff 专用回执格式。
Python provider 仍是受信代码，这些指纹和私有实例不等于 OS 沙盒或源代码的可移植快照。

## 工具面与冲突

先建立基础工具加所选 provider 的私有候选表，再应用 Agent allowlist：

| 配置 | 执行规则 |
|---|---|
| `tool_allowlist = None` | 冻结接单时完整候选集合 |
| `tool_allowlist = []` | 无工具；所选技能需要工具则报配置冲突 |
| 显式工具名单 | 仅允许名单内工具；未知工具拒绝 |
| 技能必需工具被名单排除 | 明确拒绝，不能自动扩大名单或假装技能完整 |
| 基础工具/provider 之间工具重名 | 拒绝，避免覆盖实现 |
| provider 额外导出工具 | 记录真实集合；不能把 manifest 当作完整 allowlist |

可信内部后代使用同一解析器并持久保存 bindings；其工具能力不能超出父任务已获能力。
不满足的技能或工具配置应在子模型调用前明确拒绝，不能默默丢掉 skills。

## 幂等、排队与恢复

source/key 回执重放仍先于当前技能配置查询。删除技能或 Agent 配置后，同 key 重试
仍读取原回执，不重新准备目录或发送 SDK 请求。

真正开始新的执行、queued 运行、显式 resume/continue 时，核对 canonical 依赖列表、
enabled、版本、manifest/provider 指纹及已固定模型路由。正文使用冻结内容，不因同版本
编辑而静默漂移；删除、禁用、版本或 provider 变化等不兼容情况阻止继续。
权限始终采用已保存策略与当前策略的交集。unknown 预算责任保留，不能靠新技能配置消除。

建议使用 AgentExecutionSpec v2、bindings schema v1，继续保存在现有 JSON metadata，
不新增 SQL 迁移。显式支持已知无 skills 的旧 v1 profile；v2 缺少 bindings 或结构损坏
必须拒绝，不能默认为空或回落普通聊天。B1 SkillExecutionSpec v1 保持兼容。
旧程序不应执行新 profile；回退保留当前数据库/工作副本证据，恢复匹配版本的备份。

## 实现范围

新增 bindings helper；更新 `agent_execution.py`、技能解析 helper 的窄复用、
`handlers_agents`/protocol 的 CRUD 类型校验、Native 私有指令/registry 组装，以及
内部子任务解析和持久记录接缝。同步 Agent 配置 UI、mention、types、mock、IPC 契约
与执行说明；能力真正完成后再撤掉临时 blanket refusal，冲突展示可采取的操作。

本批不改变团队调度、预算算法或 Linux L1，不新增模型 Provider 组合或自动依赖安装。

## 必须取得的证据

- 实际 handler → Native Core → 受控 SDK wire → SQLite，两个技能的文本顺序、精确工具面
  及真实 provider 工具调用正确；两个并发 Agent 的私有实例互不污染。
- allowlist、同名工具、未知/歧义/disabled 依赖在实际模型发送前拒绝。
- 原回执在配置删除后重放，零新 SDK；queued 配置漂移及权限撤销不能扩大执行范围。
- SQLite 在线备份/reopen 后保留同 family/copy/profile；unknown liability 仍阻止盲目续跑。
- 旧 v1、正常 v2、缺字段和损坏 profile 分别验证；内部后代不能超出父能力。
- 真实技能工具 OS 后代在重复取消和清理中新后代注册时，保持所有权直到实际结束。
- 真实浏览器配置 skills → @mention → 回执/reload；受控冲突 UI 单独计为 UI 证据。
- B1 技能、调度和无 skills 的 B2a 回归通过，失败保留因果和日志。

这些离线和合成证据不替代付费模型质量、完整 Linux 约束或个人生产任务集验收。
