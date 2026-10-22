# 当前能力基线

核对起点：2026-10-22 的 `78962fb`。根 package.json、web/package.json、agent/pyproject.toml 的包版本均为 1.6.0，之后有未发布修复。加入 M2 事件读取接口后，当前注册 **171 个 RPC，33 个带点号命名空间，加 3 个无点号方法**。统计来自 `register_app_handlers`；AGENTS 表与 IPC 文档受测试守护。

成熟度：**功能覆盖较广的个人开发预览版**。不使用单一完成百分比。以下“生产入口已接入”仅指真实启动路径能够调用，不代表无人值守、真实模型质量或全部边界已验收。

## 接入状态

| 分类 | 模块/路径（相对 `agent/minimax_code/`） | 当前事实与边界 |
|---|---|---|
| 生产入口已接入 | `__main__.py`、`http_server.py`、`ipc/`、`app.py` | HTTP/WS 与 stdio 共用注册表；WS 重放环在内存中，不能据此保证进程重启恢复 |
| 生产入口已接入 | `agent/core.py`、`agent/llm.py`、Provider transports、`agent/tools/` | 对话、流式、工具、多 Provider；多个执行入口仍需统一模型/权限/取消语义 |
| 生产入口已接入 | `storage/`、`permissions/`、`secrets.py`、`crash/` | 持久化、授权、keyring、崩溃记录；不等同运行事件完整重放、完整数据备份或 OS 沙盒 |
| 生产入口已接入 | `workspace/`、Git/patch/checkpoint handlers | 项目、worktree、差异、快照；并发 shell 写入和精确撤销边界待完善 |
| 生产入口已接入 | `agent/skills/`、`hooks/`、`plugins/`、`mcp/` | 技能、Hooks、插件和 MCP 桥接；外部进程权限未统一，现代 HTTP transport 互操作待验 |
| 生产入口已接入 | `codebase/`、`xai_codebase_graph/`、`memory/` | FTS、符号分析与索引；默认向量为字符哈希，不是语义模型，记忆不能代替可靠任务状态 |
| 生产入口已接入，实验边界 | `orchestrator/`、`agent/subagent*`、teams/workflows handlers | 子任务生命周期、交接、冲突检测；事后检测不能阻止共享目录的覆盖 |
| 生产入口已接入，实验边界 | `scheduler/`、`agent/self_evolution/payload.py` | Cron 和命令任务；提示词 payload 为单次模型调用，尚未接入完整工具循环 |
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
| 定时提示词没有完整工具循环 | `agent/self_evolution/payload.py` | M2/M5 |
| Codex 引擎、SSH 主机与长期运行未联调 | 尚无统一 Engine/Host 产品接口 | M4/M5 |
| MCP HTTP 互操作、办公文件解析及交付物验收缺口 | `mcp/`、前端附件/预览链路 | M6 |
| 安装、升级、完整备份与持续试用 | `scripts/`、`docs/deployment.md` | M7 |

工作目录、worktree、overlay 和命令逃逸检测不等于操作系统隔离。默认 Windows 本机执行应按受信环境理解。未来 Linux 无人值守任务使用受限容器；容器不可用时暂停，不能静默退回宿主机任意执行。

## 验证结论的边界

本轮之前的评估发现前端 750/751、跨栈 20/21；M0 针对两处夹具/初始化问题修复，最新结果看 [验证证据说明](verification.md) 与 `.verification/`。后端全量和核心数量分开计数，不相加作为独立场景数量。

尚未进行付费模型任务集、远程 Linux 部署、真实办公产物质量和连续 14 天试用验收。任何一个本地绿色测试报告，都不能把这些项目自动标为完成。
