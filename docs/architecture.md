# MiniMax Code — 系统架构文档

> 本文档是项目的架构现状权威，与 **v1.8.0** 代码库对账重写（2026-08-30）。
> v0.2.0 时代的切换说明（Tauri → web SPA）保留在 [`docs/v0.2.0-web-architecture.md`](v0.2.0-web-architecture.md) 仅作历史参考；
> 各专项深读：IPC 全量方法 [`docs/ipc-contract.md`](ipc-contract.md) Appendix A、库表 [`docs/storage-schema.md`](storage-schema.md)、
> Agent 循环 [`docs/agent-core.md`](agent-core.md)、技能 [`docs/skills.md`](skills.md)、部署 [`docs/deployment.md`](deployment.md)。

## 1. 项目目标

复刻 MiniMax Code：AI 编码 Agent + 技能系统 + 定时任务 + 多 Agent 协作 + 移动互联 + 授权管理 + 进度面板。
形态为 **web SPA + 本地 Python agent**（前后端分离，HTTP + WebSocket 通信，JSON-RPC 2.0 协议）。
v1.8.0 当前规模：**171 个 IPC 方法 / 35 个前缀**、**24 张实体表**（+ FTS/向量虚表）、**15 个内置工具模块**、前端 **30 个 Zustand stores**、
测试 **pytest 10651 / vitest 808 / 6 个 Python 黑盒 smoke / 11 个 Playwright 跨栈 spec**。

## 2. 功能模块（截图识别 → 现状对照）

| 模块 | 现状实现 |
|---|---|
| 多轮对话 | `agent.send_message` 流式循环 + `agent.message_chunk` 流 + thinking_count 通道 + ask_user 问答卡 |
| 技能系统 | SKILL.md loader + registry + per-skill 工具绑定（`skill.*` 7 方法） |
| 定时任务 | APScheduler cron + command 载荷分支 + 真实 prompt runner（`schedule.*` 6 方法） |
| 移动互联 | 配对/推送/远程控制（`mobile.*` 7 方法，PoC 级——升级立项 v1.9） |
| 多 Agent | 子 agent 全生命周期（spawn/wait/collect + artifact 协议）+ Agent Teams 编排（`team.*` 9 方法）+ 沙盒隔离 |
| 进度面板 | task ledger + `task.progress`/`agent.subagent_progress`/`agent.team_progress` 事件族 |
| 授权管理 | per-session 权限 gater + 规则持久化 + 「始终授权」（`permission.*` 6 方法） |
| 模型选择 | 多 Provider 接入（MiniMax + anthropic 兼容 + 第三方）+ per-provider 密钥槽（`model.*`/`provider.*`） |
| 代码库检索 | per-root 索引 + chunk 持久化 + FTS/向量混合检索（`codebase.*` 4 方法） |
| Git 集成 | 状态/差异/日志只读三件套 + Code Review 工作流（写操作 v1.9 立项） |
| Patch Studio | 应用/回退/快照（`patch.*` 8 方法） |
| 预览面板 | per-project 根 + 双路由静态服务 + 热重载（`preview.*`） |

## 3. 技术栈

| 层 | 技术 | 说明 |
|---|---|---|
| 前端 | React 18 + Vite + TypeScript (strict) | `web/`，路径别名 `@/` |
| 状态 | Zustand（30 stores） | 每 store 管一个 UI 切片，`src/stores/index.ts` barrel |
| 样式 | Tailwind CSS | 自定义 `minimax` 颜色主题；最小字号 11px 有测试守护 |
| Transport | FastAPI（HTTP `POST /rpc` + `GET /ws`）+ stdio | **共享同一 handler registry**，HTTP server 只是薄 transport |
| Agent 核心 | Python 3.11+ 全 asyncio | aiosqlite / httpx / FastAPI / pydantic v2 |
| 存储 | SQLite + FTS5 + 向量虚表 | 24 实体表，migration 029，幂等迁移 |
| 调度 | APScheduler | cron + command 载荷 |
| 鉴权 | Bearer token（v1.8.0） | env `MINIMAX_CODE_HTTP_TOKEN` 启用；未启用 = 零变化 |
| 测试 | pytest / vitest / Playwright | 单元 + 黑盒 smoke + 跨栈 e2e 三层 |

## 4. 目录结构（现状，深度有裁剪）

```
minimax-code/
├── CLAUDE.md / CHANGELOG.md / README.md
├── docs/                    # architecture / ipc-contract / storage-schema / agent-core /
│                            # skills / user-guide / deployment / performance-baseline / roadmap
├── scripts/                 # dev.mjs（agent + Vite 并行拉起）
├── web/                     # React SPA（Vite）
│   ├── src/
│   │   ├── App.tsx          # 三栏壳 + 连接状态机 + 各横幅挂载
│   │   ├── components/      # layout/ chat/ settings/(14 tab) panels/ modals/ right-panel/(11 tab)
│   │   ├── stores/          # 30 个 Zustand store
│   │   ├── ipc/             # client.ts(transport+token) typed.ts(171 方法签名) mock.ts mockData.ts
│   │   ├── ui/              # strings.ts（中文文案单一来源）+ 基础组件库
│   │   └── types/ipc.ts     # 共享类型 + StreamEvent 枚举（16 事件）
│   └── tests/
├── agent/                   # Python Agent（uv 管理）
│   ├── minimax_code/
│   │   ├── __main__.py      # cli_entry() → HTTP 或 stdio
│   │   ├── app.py           # register_app_handlers(server) — 31 个 handler 文件的注册 choke point
│   │   ├── ipc/             # server.py(IPCServer+Context) http_server.py(FastAPI+token 鉴权) protocol.py
│   │   ├── agent/           # core.py(循环) llm.py(多 provider) prompts.py tools/(15 模块) skills/
│   │   ├── storage/         # db.py migrations/(001–029) dao/ backup.py
│   │   ├── scheduler/       # cron + command 载荷
│   │   ├── orchestrator/    # teams.py(团队编排) verification.py
│   │   ├── permissions/     # gater + PermissionStore
│   │   ├── mobile/          # 配对/推送
│   │   ├── progress/        # PROGRESS.jsonl 账本
│   │   ├── codebase/        # per-root 索引 + FTS/向量混合检索
│   │   ├── preview/         # PreviewState per-project 根
│   │   ├── workspace_ctx.py # per-project workspace 解析（worktree > 项目根 > env > cwd）
│   │   └── http_server.py   # FastAPI 薄 transport + token 鉴权 + web dist hosting
│   ├── skills/              # 内置技能目录（SKILL.md）
│   └── tests/               # pytest（含 test_ipc_contract_doc.py 双向守护文档锚点）
├── e2e/                     # Playwright 跨栈 spec（11 个）
└── tests/e2e/               # Python 黑盒 smoke（6 个，stdio 模式）
```

## 5. 系统架构图

```
┌──────────────────────────────────────────────────────────────────┐
│ Browser (Vite dev :5173 / 生产模式同源 :8765)                      │
│  React SPA — 30 Zustand stores ◄── IPCClient                      │
│   HTTP: POST /rpc (Authorization: Bearer <token>)                 │
│   WS:   GET /ws?since=<seq>&token=<token>（seq 纪元重放）          │
└──────────────────────────┬───────────────────────────────────────┘
                           ▼
┌──────────────────────────────────────────────────────────────────┐
│ Python Agent (FastAPI + asyncio, 默认 127.0.0.1:8765)             │
│                                                                    │
│  http_server.py — 薄 transport                                     │
│   POST /rpc   token 校验 → IPCServer.handle_request(env)           │
│   GET  /ws    握手 token 校验（失败 close 4401）→ 事件流            │
│   GET  /health 匿名探针（ok/db/version/uptime）                     │
│   GET  /preview/*  per-project 根静态服务 + token                    │
│   （生产模式另托管 web/dist — 同源 SPA，免 CORS）                    │
│                                                                    │
│  IPCServer（共享 handler registry：171 方法 / 35 前缀）              │
│   ├ AgentCore（对话循环 + 工具调度 + 流式回调 + CAS/沙盒/并发感知）   │
│   ├ ToolRegistry（15 内置工具：file_ops/edit/search/terminal/       │
│   │  ask_user/shared_memory/verification/report_* …）              │
│   ├ SkillRuntime（SKILL.md loader + registry）                     │
│   ├ SubAgentRuntime + Agent Teams（沙盒化编排 + artifact 协议）     │
│   ├ APScheduler（cron + command 载荷）                              │
│   ├ CodebaseIndexer（per-root FTS/向量检索）                        │
│   ├ PermissionStore（per-session gater）                           │
│   └ SQLite Storage（24 表 + migration 029 幂等迁移）                │
└────────────────────────────────────────────────────────────────────┘
```

stdio 模式（`python -m minimax_code --stdio`）走同一 registry，供 pytest 黑盒 smoke 与 CLI 调试；**两种 transport 不在同进程混跑**。

## 6. IPC 契约（概要）

**协议**：JSON-RPC 2.0，UTF-8。错误码 `-32700 ~ -32005`（`-32001` ToolExecution / `-32002` PermissionDenied / `-32003` LLM / `-32004` Storage / `-32005` PathSecurity）。

**方法面**：171 个注册方法、35 个前缀（34 点号命名空间 + `ping`/`status`/`shutdown` 三个无点号 built-in）。
方法级全量清单以 [`docs/ipc-contract.md`](ipc-contract.md) **Appendix A** 为权威，由 `agent/tests/test_ipc_contract_doc.py` **双向守护**——新增 handler 无文档锚点即测试红。

前缀 → 职责速查（方法数见 Appendix A）：`agent`(12) `audit`(3) `checkpoint`(5) `codebase`(4) `crash`(3) `data`(3) `diag`(1) `git`(3) `mcp`(6) `memory`(5) `message`(3) `mobile`(7) `model`(4) `notification`(5) `patch`(8) `permission`(6) `plugins`(5) `preview`(1) `project`(6) `provider`(7) `run`(2) `runner`(2) `runtime`(1) `schedule`(6) `secrets`(3) `session`(12) `skill`(7) `task`(6) `team`(9) `telemetry`(4) `terminal`(4) `webhook`(5) `workflow`(7) `workspace`(3) + built-in(3)。

**流式事件**：16 个 `StreamEvent`（`agent.message_chunk`/`agent.tool_call`/`agent.tool_result`/`agent.status`/`agent.ask_user`/`agent.subagent_progress`/`agent.team_progress`/`task.progress`/`permission.*`/`notification.*`/`run.*`），前端 `src/types/ipc.ts` 的 `StreamEvent` 枚举与后端锁死同步；另有协议级 `agent.ready`（含 `next_seq` 纪元锚点）与 `agent.ping` 心跳。WS 断线重连按 `?since=` 重放，agent 重启后纪元变化触发全量重放（v1.2.2）。

**鉴权（v1.8.0）**：env `MINIMAX_CODE_HTTP_TOKEN` 设置即启用——`/rpc` 走 `Authorization: Bearer`，`/ws` 握手与 `/preview/*` 走 `?token=` query（浏览器 WS API 不能带自定义 header），比较用 `hmac.compare_digest`；`/health` 保持匿名。未设置 = 与 v1.7.1 行为逐字节一致（本地模式零破坏）。

## 7. 数据流（典型场景）

1. 用户发送消息 → `IPCClient.request` fetch `POST /rpc`（自动注入 Bearer token）
2. HTTP server 校验 token → 反序列化 → `IPCServer.handle_request(env)`
3. `agent.send_message` handler：workspace 解析（worktree > 项目根 > env > cwd）→ AgentCore
4. AgentCore：system prompt + history → LLM 流式（usage/thinking_count 随流回填）
5. tool_call → per-session permission gater（必要时 `permission.request` 事件 → 前端弹窗）
6. ToolRegistry.dispatch（per-tool 超时；写工具带 CAS `expected_sha256` 校验 + 沙盒重定向 + in-flight 并发感知）
7. tool_result 回填 LLM → 循环至 final answer（iteration 预算 + context-pressure 节流）
8. 全程 `agent.*` 事件经 `register_listener(cb)` 推 WS；消息/usage 落 SQLite
9. 前端 store 订阅事件（session 守卫防串台）→ UI 更新

## 8. 关键技术决策（演进累积）

| 决策 | 选择 | 版本 |
|---|---|---|
| 前后端 transport | HTTP + WS 与 stdio 共享 registry | v0.2.0 |
| IPC 文档 | Appendix A 双向测试守护 | R43 |
| 长输出截断 | `max_output_tokens` 32k + env 旋钮 | v1.2.1 |
| WS 重放 | seq 纪元对齐 + `?since=` 重放 | v1.2.2 |
| 工作区隔离 | per-project root_path + 严格 containment | v1.3.0 |
| 子 agent 生命周期 | 落库 + artifact 协议 + 墙钟超时 | v1.4.0 |
| 写安全 | CAS 乐观锁 + opt-in 沙盒 + collect 三方合并 | v1.5.0 |
| 沙盒深化 | exec 逃逸检测 + 只读工具 overlay + prune | v1.6.0 |
| 协作优化 | shared_memory + DAG 门控 + 无头验收 | v1.6.1 |
| 预览根 | per-project re-root + 缓存投毒修复 | v1.7.1 |
| HTTP 鉴权 | Bearer token + WS close 4401 + /health 匿名 | **v1.8.0** |

## 9. 安全边界

- **传输鉴权**（v1.8.0）：`MINIMAX_CODE_HTTP_TOKEN` 启用后 `/rpc` `/ws` `/preview` 全部校验；公网部署必须启用（见 deployment.md）
- **路径 containment**：工具操作锚定 workspace 根，禁止 `..` 越界（`PathSecurityError` → `-32602`）；沙盒 run 写入重定向 `.minimax/sandboxes/<run_id>/`
- **CAS 乐观锁**：`write_file`/`edit_file` 可带 `expected_sha256`，不匹配 fail-fast
- **命令安全**：terminal/exec 超时 + 进程树杀（`killpg`/`taskkill /F /T`）+ 危险命令检测（跨平台）+ 沙盒逃逸 advisory 检测
- **授权粒度**：每工具调用可弹窗（per-session gater），「始终授权」仅当前会话
- **密钥存储**：OS keyring + env-var 兜底；per-provider 槽；legacy 全局 key 一次性迁移
- **日志脱敏**：不打印 API key/token；diag 导出脱敏

## 10. 版本演进（Phase 划分已成历史，现行节奏按发版）

v0.x 打基础（Tauri 剥离 → web SPA，Phase 1–6 闭环）→ v1.0–v1.2 稳定性（全局审计修复、协作专项、长输出截断）→
v1.3 工作区隔离 → v1.4 子 agent 生命周期 → v1.5–v1.6 写安全与沙盒深化 → v1.6.1 债务清偿 → v1.7 迭代优化收口 →
v1.7.1 易用性专项 → **v1.8.0 安全与收口**（HTTP token 鉴权 + 文档对账 + 易用性三连）。

完整轮次账本见 [`docs/roadmap-to-1.0.0.md`](roadmap-to-1.0.0.md) 与 [`CHANGELOG.md`](../CHANGELOG.md)。

---

> 本文档与代码对账的守护点：IPC 方法数（Appendix A 双向测试）、StreamEvent 枚举（前后端锁死）、命名空间表（根 CLAUDE.md）。
> 改架构先改本文，再动代码。
