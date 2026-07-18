# 架构差距分析报告 — Grok Build vs MiniMax Code

> **回合**：R2（阶段 A 基线测绘）
> **日期**：2026-07-18
> **方法**：静态阅读 Grok Rust crate 入口 + MiniMax Python 源码，逐项对照。

## 0. 摘要

MiniMax Code 的**单体会话能力**（对话、工具、技能、调度、子 agent）已经相当完整，v0.8.0 是一个能用的 Agent。但要成为**平台型工具产品**，它缺少 Grok Build 的三大支柱：**可扩展的协议层（MCP）、生命周期钩子（Hooks）、插件市场（Plugins）**，以及围绕这三者的**安全（Sandbox/Checkpoint）、可观测（Telemetry）、感知（Codebase Graph/Memory）**能力。

差距的本质：MiniMax 是"**封闭单体**"，Grok 是"**开放平台**"。融合的核心任务 = 给 MiniMax 装上**扩展面**。

---

## 1. P0 — 平台三大支柱

### 1.1 MCP（Model Context Protocol）

| 维度 | Grok（`xai-grok-mcp`） | MiniMax 现状 | 差距 |
|------|------------------------|-------------|------|
| 协议 | rmcp 2.1，JSON-RPC 2.0 over stdio/HTTP/SSE | ❌ 无原生 MCP | 完全缺失 |
| Transport | `StreamableHttpClientTransport` + `TokioChildProcess` | — | 需新建 |
| 凭证 | `$GROK_HOME/mcp_credentials.json` + OAuth 流 | — | 需新建 |
| 健康 | `liveness` 探活 | — | 需新建 |

**融合策略**：
- 新建 `minimax_code/mcp/` 子包：`types.py`（MCP 消息类型）、`transport.py`（stdio + Streamable HTTP）、`client.py`（连接外部 MCP server）、`server.py`（MiniMax 自身作为 MCP server 暴露工具）、`registry.py`（桥接现有 `agent/tools/`）。
- 外部 MCP server 配置存 SQLite（新表 `mcp_servers`），IPC 命名空间 `mcp.*`。
- **复用**：MiniMax 已有 `xai-computer-hub-mcp-adapter` 理念 → 我们把外部 `windows-computer-use-mcp` 作为第一个 MCP client 接入对象（R31）。
- **契约**：`mcp.list` / `mcp.connect` / `mcp.disconnect` / `mcp.call_tool`。

### 1.2 Hooks（生命周期钩子）

| 维度 | Grok（`xai-grok-hooks`） | MiniMax 现状 | 差距 |
|------|--------------------------|-------------|------|
| 发现 | 文件目录（`~/.grok/hooks/` + 项目级） | ❌ 无 | 缺失 |
| 定义 | JSON，命令式（子进程） | — | 缺失 |
| 事件 | `session_start` / `pre_tool_use` / `post_tool_use` / `session_end` | — | 缺失 |
| 阻塞 | `pre_tool_use` 可 deny/allow | `permission.*` 只做工具级同意 | 语义不同 |
| 容错 | fail-open | — | 需对齐 |

**融合策略**：
- 新建 `minimax_code/hooks/` 子包：`events.py`（事件枚举 + payload schema）、`registry.py`（注册表 + 文件发现）、`dispatcher.py`（同步/异步分发）、`runner.py`（子进程 + 超时）、`matcher.py`（工具名匹配）。
- **与现有 `permission.*` 协同**：`pre_tool_use` hook 的 deny 结果与 permission 规则**叠加**（任一拒绝即拒绝），不替代。
- **与 `workflow.py` 区分**：workflow 是"事件→多步动作"，hooks 是"事件→单个守卫命令"，两者互补不重叠。
- 4 个事件先落地，后续扩展（Grok 还有 `subagent_end` 等）。
- **契约**：`hooks.list` / `hooks.reload` / 事件 payload 走 WebSocket push。

### 1.3 Plugins（插件市场）

| 维度 | Grok（`xai-grok-plugin-marketplace`） | MiniMax 现状 | 差距 |
|------|---------------------------------------|-------------|------|
| 来源 | CLI/项目 grok/项目 claude/用户/市场/git 安装 | ❌ 无 | 缺失 |
| 元数据 | `PluginScope`(Cli/Project/User/Config) + `PluginOrigin` | — | 缺失 |
| 安装 | git clone + 索引 + 信任 | — | 缺失 |
| 目录 | catalog/scanner/index/installer/matcher | — | 缺失 |

**融合策略**：
- 新建 `minimax_code/plugins/` 子包：`manifest.py`（插件清单 schema：name/version/scope/hooks/tools/skills）、`loader.py`（发现 + 加载）、`scanner.py`（目录扫描）、`installer.py`（git/本地安装）、`registry.py`（运行时启用态）。
- 插件统一目录：`~/.minimax-code/plugins/`（用户级）+ `<proj>/.minimax/plugins/`（项目级）。
- 插件可贡献：hooks、tools、skills、commands——**插件是上述能力的统一打包载体**。
- 存 SQLite `plugins` 表（启用态）+ `plugin_installs` 表（安装记录）。
- **契约**：`plugins.list` / `plugins.install` / `plugins.enable` / `plugins.disable` / `plugins.uninstall`。

---

## 2. P1 — 安全与可观测

### 2.1 Sandbox

- **Grok**：`xai-grok-sandbox`（seatbelt/cgroup）+ `computer/local/cgroup.rs`，进程级资源约束。
- **MiniMax**：`terminal.py` 工具直接 `subprocess`，无边界。
- **策略**：`minimax_code/sandbox/` — `policy.py`（命令白/黑名单 + 超时 + env 注入）、`executor.py`（受限 subprocess）。Windows 下用 Job Object 约束（或退化到超时 + 白名单）。

### 2.2 Checkpoints

- **Grok**：`workspace/recovery.rs` + `worktree/`，工作区快照与回滚。
- **MiniMax**：无。
- **策略**：`workspace/checkpoint.py` — 基于 `git stash`/`git commit --to temp` + 文件副本，`checkpoint.*` IPC（create/restore/list/diff）。

### 2.3 Telemetry

- **Grok**：`xai-grok-telemetry` + `xai-tracing`，OpenTelemetry/OTLP + fastrace + mixpanel。
- **MiniMax**：无统一遥测（只有 logging）。
- **策略**：`minimax_code/telemetry/` — `bus.py`（进程内事件总线）、`span.py`（trace 模型）、`store.py`（落 SQLite `telemetry_events`），schema OTLP 兼容以便未来接 Jaeger。

### 2.4 Token Estimation

- **Grok**：`xai-token-estimation`。
- **MiniMax**：无（LLM 调用不感知 token 预算）。
- **策略**：`agent/tokens.py` — tiktoken（若可用）+ 启发式 fallback，接入上下文预算，与 `compaction.py` 联动。

### 2.5 Crash Handler

- **Grok**：`xai-crash-handler`。
- **MiniMax**：异常仅日志。
- **策略**：`minimax_code/crash.py` — 捕获未处理异常，写崩溃报告 + session 恢复提示。

---

## 3. P2 — 协作与感知

### 3.1 Codebase Graph

- **Grok**：`xai-codebase-graph`（LSP `async-lsp` 驱动的符号图谱）。
- **MiniMax**：无。
- **策略**：`minimax_code/codegraph/` — 先做 tree-sitter-lite（或正则）符号抽取 + 依赖边，`codegraph.symbols` / `codegraph.deps` IPC。

### 3.2 Memory

- **Grok**：`xai-grok-memory`（持久记忆）。
- **MiniMax**：仅 `SELF.md`（静态）。
- **策略**：`minimax_code/memory/` — 语义/情景记忆，SQLite `memories` 表，检索后注入 system prompt。

### 3.3 Subagent Resolution / Agent Lifecycle / Sampler / Prompt Queue

- **Grok**：均为独立 crate，规范化状态机与采样。
- **MiniMax**：`orchestrator/subagent.py` 半成品（llm=None 时返回 stub）；lifecycle 隐式；无 sampler/prompt queue。
- **策略**：逐步规范化，R23–R29。

---

## 4. P3 — 多模态与发布

- **Computer Use**：外部已有 `windows-computer-use-mcp`，R31 作为 MCP client 接入即可（最大复用）。
- **Voice / Mermaid / ACP / Announcements / Worktree / Hunk Tracker / Headless**：按阶段 D/E 推进。

---

## 5. 融合实施约束（红线）

1. **不引入 Rust 工具链**：所有融合能力用 Python（后端）/ TS（前端）实现。
2. **契约三方同步**：每个新 IPC 方法同步更新 `docs/ipc-contract.md`、`web/src/types/ipc.ts`、`minimax_code/ipc/protocol.py`。
3. **存储前向迁移**：新表/字段在 `storage/migrations/` 加递增编号，不修改历史迁移。
4. **Mock backend 覆盖**：新 IPC 方法必须在 `web/src/ipc/client.ts` 的 `mockHandle` 补实现，否则无 agent 状态下前端断裂。
5. **fail-open / 优雅降级**：Hooks/Plugins/MCP 失败不能阻断核心对话循环。
6. **每回合可独立验证 + commit**：不出现跨回合的"半成品"。

---

## 6. 阶段 A 落地顺序优化结论

基于差距分析，阶段 A（R3–R9）顺序合理：**MCP 类型契约（R3）→ Server/Client（R4）→ 注册发现（R5）→ Hooks 事件注册表（R6）→ Hooks 接线（R7）→ Plugins 清单 loader（R8）→ Plugins IPC（R9）**。MCP 优先是因为它是 Plugins 和 Computer Use 的协议底座。
