# 当前能力基线

核对起点：2026-10-22 的 `78962fb`。根 package.json、web/package.json、agent/pyproject.toml 的包版本均为 1.6.0，之后有未发布修复。至 2026-10-26 共享预算批次，当前注册 **192 个 RPC，36 个带点号命名空间，另有 3 个无点号方法**。统计来自 `register_app_handlers`；AGENTS 表与 IPC 文档受测试守护。

成熟度：**功能覆盖较广的个人开发预览版**。不使用单一完成百分比。以下“生产入口已接入”仅指真实启动路径能够调用，不代表无人值守、真实模型质量或全部边界已验收。

## 接入状态

| 分类 | 模块/路径（相对 `agent/minimax_code/`） | 当前事实与边界 |
|---|---|---|
| 生产入口已接入 | `__main__.py`、`http_server.py`、`ipc/`、`app.py` | HTTP/WS 与 stdio 共用注册表；WS 重放环在内存中，不能据此保证进程重启恢复 |
| 生产入口已接入 | `agent/core.py`、`agent/llm.py`、Provider transports、`agent/tools/` | 对话、流式、工具、多 Provider；多个执行入口仍需统一模型/权限/取消语义 |
| 生产入口已接入 | `storage/`、`permissions/`、`secrets.py`、`crash/` | 持久化、授权、keyring、崩溃记录；不等同运行事件完整重放、完整数据备份或 OS 沙盒 |
| 生产入口已接入 | `workspace/`、Git/patch/checkpoint handlers | 项目、worktree、差异、快照及独立子任务副本已接入；交接做冲突预检与原子收取，精确撤销及完整编程闭环仍待验收 |
| 生产入口已接入 | `agent/skills/`、`hooks/`、`plugins/`、`mcp/` | 技能持久接单及共享进程策略已有局部验收；剩余入口审计、插件能力约束和现代 MCP HTTP 互操作待完成 |
| 生产入口已接入 | `codebase/`、`xai_codebase_graph/`、`memory/` | FTS、符号分析与索引；默认向量为字符哈希，不是语义模型，记忆不能代替可靠任务状态 |
| 生产入口已接入，实验边界 | `orchestrator/`、`agent/subagent*`、teams/workflows handlers | 独立工作副本、交接、技能和直接子 Agent 持久入口已局部验收；Agent 非空 skills 配置、团队及其余根入口仍待补齐 |
| 生产入口已接入，实验边界 | `scheduler/`、`agent/self_evolution/payload.py` | 定时提示词已进入 Native RunService；本地发生身份、固定上下文、会话隔离、执行/校验证据与共享家族/日账已接入；远端持久服务和真实自动化尚未验收 |
| 生产入口已接入，实验边界 | `progress/`、`telemetry/`、`preview/`、`mobile/`、`webhooks/` | 有处理器/服务；进度不是验收结论，预览边界需统一，移动与外部通知不属于首版生产保证 |
| 纯协议/算法辅助 | `plan_mode/`、`compaction/`、`voice/` | 类型、状态机、触发器、模板；模块明确保留 host wiring 为后续工作，不据此宣称只读执行、语义压缩或语音输入完成 |
| 协议/SDK 辅助，非产品远程主机功能 | `computer_hub_core/`、`computer_hub_sdk/`、`tool_protocol/`、`tool_runtime/`、`tool_types/`、`tools_api/`、`grok_auth/` | 有协议和连接/调度辅助实现；没有已验收的 SSH 主机注册、长期服务和跨主机数据归属闭环 |
| 辅助库，按调用点判断接入 | `dagre/`、`mermaid/`、`markdown/`、`hunks/`、`data_structures/` | 布局、渲染和数据结构；测试通过不能算作报告/DOCX/PDF 交付链已经完成 |
| 辅助库/局部运行支持 | `auth/`、`config_types/`、`extensions/`、`fsnotify/`、`lifecycle/`、`mcp_adapter/`、`prompt_queue/`、`resilience/`、`runtime/`、`sampler/`、`sandbox/`、`system_power/`、`tracing/`、`workspace_types/` | 保留现有实现；具体安全与恢复能力必须验证真实入口，不把包名当作已生效的产品保证 |
| 前端已接入 | `web/src/components`、`stores`、`ipc` | 项目、会话、模型、权限、工具时间线等；状态恢复和交付物统一体验后续推进 |

此表是功能分组，不是静态依赖图，也不是未列出文件的删除名单。M0 不为提升测试数量把辅助库强行接入主流程。

## 生产阻断项与归属

| 缺口 | 证据入口 | 处理阶段 |
|---|---|---|
| shell 执行及写入隔离；路径失效时回退 cwd | `agent/tools/terminal.py`、`workspace_ctx.py` | M1 |
| 应用认证、Origin/Host、预览与下载边界 | `http_server.py` | M1 |
| 执行入口分散、模型/预算/取消语义不统一 | `agent/core.py`、`ipc/builtins.py`、`orchestrator/` | M1–M2 |
| WS 内存重放、持久运行恢复不完整 | `http_server.py`、`ipc/handlers_runs.py` | M2 |
| 文本截取式压缩、哈希向量、快照恢复语义 | `agent/compaction.py`、`codebase/`、`ipc/handlers_checkpoint.py` | M3 |
| 共享累计家族/全机日账与未知责任已接入，Native 估算预留、Codex 反应式中断；尚未完成真实任务及远程生产验收 | `budget_ledger.py`、`storage/dao/budgets.py`、`scheduler/` | M2/M5 |
| Codex 和 SSH 产品入口已有局部实现，真实推理/远程长期运行闭环未验收 | `engines/`、`hosts/`、`ipc/handlers_hosts.py` | M4/M5 |
| MCP HTTP 互操作、办公文件解析及交付物验收缺口 | `mcp/`、前端附件/预览链路 | M6 |
| 安装、升级、完整备份与持续试用 | `scripts/`、`docs/deployment.md` | M7 |

工作目录、worktree、overlay 和命令逃逸检测不等于操作系统隔离。默认 Windows 本机执行应按受信环境理解。未来 Linux 无人值守任务使用受限容器；容器不可用时暂停，不能静默退回宿主机任意执行。

## 验证结论的边界

本轮之前的评估发现前端 750/751、跨栈 20/21；M0 针对两处夹具/初始化问题修复，最新结果看 [验证证据说明](verification.md) 与 `.verification/`。后端全量和核心数量分开计数，不相加作为独立场景数量。

以上为最初基线，不能代替后续提交的验收记录。至 2026-10-25，仓库已有六项真实编程任务的评测报告，以及早期版本的 Linux 验证记录；仍不等于完整四十项任务集、当前远程运行闭环、真实办公产物质量和连续十四天试用验收通过。

2026-10-25 后续上传保护批次增加同目录 staging、checksum 与目标身份校验、旧文件备份和
原子替换，强制 SFTP 路径传输。提交结果不明时保留 journal；调用方预先保存 transfer ID 后
可用 `host.transfer_status` 查询，重复 ID 不会再次 SCP/提交。取消未提交传输清理本次
payload，提交开始后保留不确定结果及备份。协作上传锁不能完全排除外部进程在最终检查
之后写入；保留目录清理策略、远端服务生命周期、当前 Linux 部署及真实模型闭环仍待验收。
本批 Windows 局部后端/契约 60 通过、2 跳过，前端主机/运行 IPC 6 通过，TypeScript 与 ruff
通过；相同源码 helper 在 Ubuntu 24.04 / Python 3.12 的隔离目录 8 项通过，验证真实 fcntl
互斥、权限/只读文件与 file/directory fsync，证据 `.verification/upload-linux-evidence.json`。
这仍是 helper 验证，完整产品 SSH/SFTP 传输链待验。其余证据见 `.verification/upload-*.log`
和 [生产路线图](production-roadmap.md)。这不是 M5 完成声明。

2026-10-25 对 `bc26dd4` 的检查确认 M2/M3 与 M4/M5 已有新增实现，但 Codex 恢复、聊天历史落库、调度上下文与幂等、全局预算和远程持久执行仍需补齐。随后修复了 Codex 终态和累计用量解析、失效目录拒绝执行、显式规划模式策略，以及下载校验后原子发布与本地 SSH/SCP 子进程回收。Codex 的恢复和中途补充能力卡暂时关闭；预算仍是引擎用量通知驱动的中断，不是调用前硬限额。该轮尚未包含上传的远端原子替换；后续结果见上文上传保护批次。


2026-10-25 当前补充：Codex 同 thread 多轮、SQLite 重开、部分历史、审查恢复和审批隔离已通过受控 App Server 离线闭环；resume 已开启，steer 关闭。Native 正常 chat/execute 的配置校验已与 FsBus 解耦，任务/聊天分别呈现执行状态和 version1 校验证据，暂停/取消/失败的部分结果可恢复。定时提示词已调用 Native RunService；稳定 occurrence 幂等、调度上下文固定和跨运行共享每日预算账本尚未完成。以上覆盖前述早期基线描述，仍不代表 M3–M5 整体验收。


2026-10-26 本地调度补充：Local/Native prompt 的配置快照、独立项目会话、稳定手动请求键/cron UTC 发生身份、跨修订租约、错过周期合并、软删除及审查恢复已实现。旧任务全部停用待重新绑定；不支持的命令/自演进分派拒绝执行。自动任务禁用全局/插件钩子，权限快照只允许后续收紧。父任务重复取消、子进程超时或退出期间继续持有递归清理，等待真实清理及新注册后代结束后才释放运行资源。当前仅离线受控验证，未验收付费推理、远程持久服务或整阶段 M5；当时尚未完成的共享每日预算已由下述 Schema037 批次补齐。

2026-10-26 共享预算与评测补充：`934bde2` 的 Schema037 已接入实际 SDK 发送前预留、
父子家族与每日责任、未知请求恢复核对，以及持久化故障后的付费发送保护。
`98d693c` 的日账完整性覆盖指定预留日期的全部家族，活动请求或已核定但未测得请求
不能显示完整测量；旧响应缺字段保留未知。Native 是估算预留，Codex 是通知后的反应式
限制，均不等同货币硬上限。`0a66bcc` 的六项 Native pilot 评判分别处理执行终态、
产品校验、外部制品和预算证据，不能把部分输出当作任务成功。
局部验证、JSON 导入临时限制和回退方法见 [预算账本](budget-ledger.md) 与
[评测计量](evaluation-accounting.md)。其余公开执行入口收拢仍在进行，完整恢复、
真实模型任务、Linux 产品链及 14 天试用均未据此验收通过。

2026-10-26 技能执行补充：`40986bd` 将公开 `skill.invoke` 与代码审查接入 Native
持久接单，要求显式 Native 会话及稳定请求键，保存技能/模型/权限规格，使用私有客户端
与工具表。递归异步后代清理完成前保留运行资源；子任务登记失败阻止该子任务执行，
终态保存失败保留不完整/需核对证据，预算归属指向真实持久运行。
冻结后端 929 项、前端 838 项和浏览器 4 项通过，范围与证据见
[技能执行说明](skill-execution.md)。没有付费模型验证；直接子 Agent、团队等其他公开
根入口和普通 Native 后代生命周期不在该批范围，未据此宣称完整生产可用；直接入口
随后接入，结果见下文。

2026-10-26 生产启动补充：`c7c0029` 使用预构建前端和已安装 Python，正常启动不再
隐式构建/安装；缺失工作区或环境时明确拒绝。Windows 启动、真实后代清理、日志排空
及隔离后端健康/SPA/认证共 14 项通过，详情见 [部署说明](deployment.md)。该批没有
Linux 完整产品服务或冷启动门槛验收，不代表完整 M7 安装、升级和数据恢复完成。
随后同一提交在 Linux 隔离临时目录执行启动器检查，10 项通过、4 项明确跳过，
包含四项真实合成进程测试；Windows 专项及缺少预构建后端的健康检查没有计为通过。
源码归档传输校验一致，详情和局限见同一部署说明。

2026-10-26 直接子 Agent 补充：`623fa16` 将两个公开 Agent 执行入口统一到 Native
持久接单，source/key 与独立会话/run/预算家族原子登记，私有配置和副本归既有 owner。
最终冻结后端 1177 项、前端 848 项、浏览器 6 项通过（4 项真实后端、2 项受控 UI），
证据、Schema038 回退及历史归档限制见 [执行说明](agent-execution.md)。非空 skills
仍明确拒绝；[B2a.1](agent-skills-execution-plan.md) 将补齐此前仅声明的技能配置能力。
团队等入口、完整远程链及真实任务验收未据此完成。
