# MiniMax Code ↔ Grok Build 融合演进路线图

> **愿景**：把 Grok Build（xAI 终端 AI 编码 Agent，70+ Rust crates）的**架构设定与平台级能力**，深度融合到 MiniMax Code（Python + React 桌面 Agent），演进为**高度进化、可通用的平台型工具产品**。
>
> **方法论**：理念融合（非代码移植）。Grok 的 Rust crate 是设计参考蓝本，我们用 Python/TypeScript 在 MiniMax Code 的 asyncio + JSON-RPC + React SPA 架构上**重新实现等价能力**，并保持与现有 IPC 契约、存储层、技能系统的兼容。

- **启动日期**：2026-07-18
- **基准版本**：v0.8.0
- **目标版本**：v0.9.0（平台版）
- **迭代节奏**：≥ 50 回合，每回合一个独立"目标 → 实现 → 验证 → commit"闭环
- **权威日志**：[`ITERATION_LOG.md`](./ITERATION_LOG.md)（每回合追加，不覆写）

---

## 一、融合原则（工程师铁律）

| 原则 | 含义 |
|------|------|
| **理念融合，非代码移植** | Grok 的 Rust crate 是设计参考；我们在 Python/TS 上重新实现，不绑定 Rust 工具链。 |
| **契约优先** | 任何新能力先落在 IPC 契约（`docs/ipc-contract.md`、`web/src/types/ipc.ts`、`protocol.py`），再实现 handler。 |
| **可观测、可回退** | 每个能力都有遥测事件 + 优雅降级路径；危险操作走 `permission.*`。 |
| **渐进式、向后兼容** | 新模块以独立子包形式落地，不破坏现有 `app.register_app_handlers` 注册流程。 |
| **平台化优先** | 每个能力都考虑"第三方能否扩展"——MCP、Hooks、Plugins 是三大平台支柱。 |

## 二、Grok Build 能力盘点 → MiniMax Code 差距

| Grok 能力 | crate 参考 | MiniMax 现状 | 融合优先级 |
|-----------|-----------|-------------|-----------|
| MCP 协议 | `xai-grok-mcp`, `xai-computer-hub-mcp-adapter` | ❌ 无原生 MCP | 🔴 P0 |
| Hooks 钩子 | `xai-grok-hooks`, `xai-hooks-plugins-types` | ❌ 无 | 🔴 P0 |
| Plugins 市场 | `xai-grok-plugin-marketplace` | ❌ 无 | 🔴 P0 |
| Sandbox 沙箱 | `xai-grok-sandbox`, `computer/local/cgroup.rs` | ⚠️ 工具直接执行 | 🟠 P1 |
| Checkpoints | `xai-grok-workspace/recovery.rs` | ❌ 无 | 🟠 P1 |
| Telemetry | `xai-grok-telemetry`, `xai-tracing` | ❌ 无 | 🟠 P1 |
| Codebase Graph | `xai-codebase-graph` | ❌ 无 | 🟡 P2 |
| Memory 记忆 | `xai-grok-memory` | ❌ 无（仅 SELF.md） | 🟡 P2 |
| Computer Use | `xai-computer-hub-core/sdk` | ⚠️ 外部 MCP 可用 | 🟡 P2 |
| Voice 语音 | `xai-grok-voice` | ❌ 无 | 🟢 P3 |
| Mermaid 渲染 | `xai-grok-mermaid`, `third_party/mermaid-to-svg` | ❌ 无 | 🟢 P3 |
| ACP 编辑器嵌入 | `xai-acp-lib` | ❌ 无 | 🟢 P3 |
| Headless/CI | `xai-grok-shell` headless entry | ⚠️ 有 stdio | 🟢 P3 |
| Token 估算 | `xai-token-estimation` | ❌ 无 | 🟠 P1 |
| Compaction 压缩 | `xai-grok-compaction` | ✅ 有 `compaction.py` | 🟡 P2 增强 |
| Subagent 解析 | `xai-grok-subagent-resolution` | ⚠️ 半成品 | 🟡 P2 |
| Agent Lifecycle | `xai-agent-lifecycle` | ⚠️ 隐式 | 🟡 P2 |
| Worktree | `xai-fast-worktree`, `workspace/worktree/` | ❌ 无 | 🟢 P3 |
| Hunk Tracker | `xai-hunk-tracker` | ❌ 无 | 🟢 P3 |
| Crash Handler | `xai-crash-handler` | ❌ 无 | 🟠 P1 |
| Sampler 采样 | `xai-grok-sampler`, `sampling-types` | ❌ 无 | 🟡 P2 |
| Prompt Queue | `xai-prompt-queue` | ❌ 无 | 🟡 P2 |
| Announcements | `xai-grok-announcements` | ❌ 无 | 🟢 P3 |

## 三、五阶段 50+ 回合路线图

### 🏗️ 阶段 A — 融合地基与平台内核（R1–R10）

> 目标：建立三大平台支柱（MCP / Hooks / Plugins）的内核，让 MiniMax Code 具备**可扩展架构**。

| 回合 | 主题 | 关键交付 |
|------|------|---------|
| R1 | 演进控制中心 + 路线图 | 本文档 + `ITERATION_LOG.md` + 回合模板 |
| R2 | 架构差距分析报告 | `gap-analysis.md`（Grok vs MiniMax 逐项对照） |
| R3 | MCP 协议层 · 类型契约 | `minimax_code/mcp/types.py`、`protocol.py`（JSON-RPC 2.0 over stdio/HTTP） |
| R4 | MCP · Server 框架 + Client | `mcp/server.py`、`mcp/client.py`、stdio transport |
| R5 | MCP · tool/resource/prompt 注册发现 | `mcp/registry.py` + 桥接现有 ToolRegistry |
| R6 | Hooks · 生命周期事件 + 注册表 | `minimax_code/hooks/`（events、registry、types） |
| R7 | Hooks · 内置 hook 点接线 | pre/post tool、session、message 钩子点 |
| R8 | Plugins · 清单格式 + loader | `minimax_code/plugins/manifest.py`、`loader.py` |
| R9 | Plugins · 安装/启用/禁用 + IPC | `handlers_plugins.py`、存储迁移 |
| R10 | 阶段 A 集成验证 | 跨栈测试 + 阶段报告 |

### 🛡️ 阶段 B — 安全与可观测（R11–R20）

> 目标：让 Agent 的每一次执行**可约束、可观测、可恢复**。

| 回合 | 主题 | 关键交付 |
|------|------|---------|
| R11 | Sandbox · 执行边界抽象 | `minimax_code/sandbox/`（policy 接口、context） |
| R12 | Sandbox · 命令白名单 + 资源限制 | Windows 友好的进程约束、超时、env 隔离 |
| R13 | Checkpoints · 工作区快照 | `workspace/checkpoint.py`（git stash + 文件副本） |
| R14 | Checkpoints · 恢复/回滚 + IPC | `handlers_workspace` 扩展、diff 预览 |
| R15 | Telemetry · 事件总线 + schema | `minimax_code/telemetry/`（bus、span、OTLP 兼容） |
| R16 | Telemetry · 落库 + 采样查询 | 迁移、DAO、`telemetry.*` IPC |
| R17 | Token Estimation · 估算器 | `agent/tokens.py`（tiktoken/启发式 + 上下文预算） |
| R18 | Compaction 增强 · 分层策略 | 升级 `compaction.py`：消息/工具/记忆分层压缩 |
| R19 | Crash Handler · 异常捕获报告 | `minimax_code/crash.py` + session 恢复 |
| R20 | 阶段 B 集成验证 | 安全/可观测端到端测试 |

### 🧠 阶段 C — 智能体协作与感知（R21–R30）

> 目标：让 Agent **理解代码库、记住经验、协作编排**。

| 回合 | 主题 | 关键交付 |
|------|------|---------|
| R21 | Codebase Graph · 符号索引 | `minimax_code/codegraph/`（tree-sitter lite / regex 符号抽取） |
| R22 | Codebase Graph · 依赖图谱 + 查询 | 依赖边、`codegraph.*` IPC、可视化数据 |
| R23 | Subagent Resolution 增强 | `orchestrator/subagent.py` 路由/并行/编排 |
| R24 | Agent Lifecycle 状态机 | `agent/lifecycle.py` 规范状态转换 |
| R25 | Prompt Queue 队列化 | `agent/prompt_queue.py` 有序输入 |
| R26 | Interjection 中途插入 | 用户在 agent 执行中插入指令的能力 |
| R27 | Memory · 持久存储 | `minimax_code/memory/`（语义/情景，迁移） |
| R28 | Memory · 检索 + 注入 | 检索 + system prompt 注入 |
| R29 | Sampler · 采样参数策略 | `agent/sampler.py`（temperature/top-p 策略） |
| R30 | 阶段 C 集成验证 | 协作/感知测试 |

### 🎨 阶段 D — 多模态与交互（R31–R40）

> 目标：让 Agent **看得见、说得出、画得清、嵌得进**。

| 回合 | 主题 | 关键交付 |
|------|------|---------|
| R31 | Computer Use · 桥接外部 MCP | 接入 `windows-computer-use-mcp` 作为 MCP client |
| R32 | Computer Use · 工具注册 | screen/click/type/scroll 工具 |
| R33 | Voice · 语音输入 STT | `minimax_code/voice/stt.py` |
| R34 | Voice · 语音输出 TTS | `voice/tts.py` |
| R35 | Mermaid · 代码→SVG 渲染 | `minimax_code/render/mermaid.py` |
| R36 | Mermaid · 前端展示 + 导出 | React 组件 + 导出 PNG/SVG |
| R37 | ACP · 协议层 | `minimax_code/acp/`（Agent Client Protocol） |
| R38 | ACP · 编辑器嵌入适配 | stdio ACP server |
| R39 | Announcements · 公告通道 | `minimax_code/announcements/` |
| R40 | 阶段 D 集成验证 | 多模态测试 |

### 🚀 阶段 E — 平台化与发布（R41–R50+）

> 目标：**收敛、打磨、发布** v0.9.0 平台版。

| 回合 | 主题 | 关键交付 |
|------|------|---------|
| R41 | Headless/CI 模式 | 批处理 entry、`--headless` 退出码语义 |
| R42 | Plugin Marketplace UI | React 浏览/安装/启用页 |
| R43 | Worktree 集成 | 并行任务隔离（`workspace/worktree.py`） |
| R44 | Hunk Tracker · 精细 diff | `minimax_code/hunks.py` |
| R45 | Fast Worktree + 并行 agent | 多 agent 并行隔离执行 |
| R46 | 统一配置系统 | Grok config-types 理念的统一 config 层 |
| R47 | 平台型 SDK | 第三方扩展文档 + 示例插件 |
| R48 | 文档系统全面更新 | CLAUDE.md / 架构文档 / API 参考 |
| R49 | e2e 集成测试 + 性能基准 | Playwright + pytest 基准 |
| R50 | v0.9.0 平台版发布 | CHANGELOG、版本号、发布说明 |
| R51+ | 持续打磨 | 基于真实使用反馈迭代 |

## 四、回合执行流程（每回合必做）

```
1. 明确本轮目标     → 在 ITERATION_LOG.md 写"本轮目标"
2. 探索现状         → 读相关代码/Grok 参考，确认差距
3. 设计             → 契约优先：先定类型/IPC，再实现
4. 实现             → 遵循 KISS/DRY/YAGNI/SOLID
5. 验证             → 单元测试 / smoke / 类型检查
6. 记录             → 在 ITERATION_LOG.md 追加"实现/验证/产出"
7. git commit       → 每回合一个 commit，message 含 [R<N>]
```

## 五、验收标准（平台型产品的定义）

到 R50，MiniMax Code 应满足：

- [ ] **可扩展**：第三方能通过 MCP / Hooks / Plugins 三大支柱接入，无需改核心代码
- [ ] **可观测**：每次工具调用、agent 生命周期、token 消耗都有遥测
- [ ] **可恢复**：工作区快照 + crash 恢复，异常不丢数据
- [ ] **多模态**：文本 + 语音 + 图表 + 桌面控制
- [ ] **可嵌入**：通过 ACP 嵌入编辑器，通过 headless 嵌入 CI
- [ ] **可记忆**：跨会话的经验记忆
- [ ] **文档完备**：CLAUDE.md 与代码同步，平台 SDK 文档齐全
