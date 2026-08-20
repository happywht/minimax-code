# MiniMax Code 复刻 - 架构设计文档

> 这是整个项目的架构契约。所有后续任务（project-skeleton, storage-layer, agent-core, ui-shell, skills-system）都必须遵守本文定义的接口和模块边界。
>
> **v0.2.0 状态**：项目从 Tauri 桌面壳切到"web SPA + 本地 Python agent"。架构图、技术栈表、目录树、IPC 路由全部按 web 模式重写；v0.1.x 时代的 Rust / Tauri 描述已过时，仅作历史参考。详细切换说明见 [`docs/v0.2.0-web-architecture.md`](v0.2.0-web-architecture.md) 与 [`CHANGELOG.md`](../CHANGELOG.md) 的 v0.2.0 段。

## 1. 项目目标

复刻 MiniMax Code：AI 编码 Agent + 技能系统 + 定时任务 + 多 Agent 协作 + 移动互联 + 授权管理。**v0.2.0 起以 web SPA + 本地 Python agent 的形态继续开发**（不再有 Tauri 桌面壳）。Phase 1 阶段目标是跑通最小可演示闭环：浏览器 ↔ HTTP/WS ↔ Agent ↔ 工具 ↔ LLM。

## 2. 截图识别的核心功能（必须覆盖）

| 模块 | 截图位置 | 核心能力 |
|---|---|---|
| 多轮对话 | 主区 | 流式 LLM 输出、tool call 中间状态、消息历史 |
| 技能系统 | 侧边栏"技能" | SKILL.md 加载、enable/disable、运行时注入 |
| 定时任务 | 侧边栏"定时任务" | cron 表达式、调度、持久化 |
| 连接手机 | 侧边栏"连接手机" | 配对、推送、远程控制 PoC |
| 任务历史 | 侧边栏"任务历史" | 会话持久化、检索、归档 |
| 多 Agent | 侧边栏"Agents" | 子 Agent spawn、任务分发、结果汇总 |
| 进度面板 | 右上"进度" | 长任务进度上报、可视化 |
| 授权管理 | 底部"始终授权" | 工具调用权限粒度控制 |
| 模型选择 | 底部"模型" | 多模型切换 |
| 新建任务 | 侧边栏顶部 | 创建新会话 |

## 3. 技术栈

| 层 | 技术 | 理由 |
|---|---|---|
| 前端 | React 18 + Vite + TypeScript | 生态成熟、组件化、类型安全；Vite dev server 直接服务 SPA |
| Transport | HTTP transport (FastAPI on agent) + WebSocket events | `POST /rpc` 处理请求、`GET /ws` 推流；与 stdio 共享同一 handler registry |
| 样式 | Tailwind CSS | 快速、对标 Linear/Claude Code 风格 |
| 状态 | Zustand | 轻量、TypeScript 友好 |
| Agent 核心 | Python 3.11+ | 用户舒适区、生态丰富（LLM SDK、工具库） |
| LLM 客户端 | httpx (async) | 流式支持、轻量、可控 |
| 存储 | SQLite (stdlib sqlite3 / aiosqlite) | 本地、零运维、ACID |
| IPC | JSON-RPC 2.0 over HTTP/WS (web) + stdio (tests / CLI) | 双模式：stdio 给 pytest + CLI；HTTP+WS 给浏览器 |
| 调度 | APScheduler | 成熟、支持 cron |
| 测试 (Python) | pytest + pytest-asyncio | 标准 |
| 测试 (前端) | vitest + @testing-library/react | 快速、TS 原生 |
| 测试 (e2e) | Playwright | 跨栈 e2e，浏览器真跑 + agent 真起 |

> **Transport** 走两条路，**共享同一份 handler registry**（`agent/minimax_code/ipc/server.py`）：
> 1. **stdio JSON-RPC 2.0** — 给 pytest 子进程 smoke + CLI 调试（`python -m minimax_code --stdio`）
> 2. **HTTP + WebSocket** — 给 web 客户端（`python -m minimax_code` 默认模式）
>
> 这两条路由不是平行两份实现：HTTP server 只是薄 transport，把 `POST /rpc` 反序列化后调
> `IPCServer.handle_request(env)`，再把响应序列化回 HTTP body；流式事件经
> `IPCServer.register_listener(cb)` 注入到 WebSocket。详细见
> [`docs/v0.2.0-web-architecture.md`](v0.2.0-web-architecture.md)。

## 4. 目录结构

```
minimax-code/                          # 仓库根
├── package.json                       # pnpm workspace 根（含 dev / test:e2e 脚本）
├── pnpm-workspace.yaml
├── README.md
├── docs/
│   ├── architecture.md                # 本文档
│   ├── ipc-contract.md                # IPC 消息格式
│   ├── v0.2.0-web-architecture.md     # v0.2.0 切换的 API 契约 / 工作流
│   ├── storage-schema.md              # 数据库 schema
│   ├── agent-core.md                  # agent 循环图
│   ├── skills.md                      # 技能格式规范
│   └── ui-components.md               # 组件树
├── web/                               # React 前端（Vite-served SPA）
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── index.html
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── components/
│   │   │   ├── Sidebar.tsx
│   │   │   ├── ChatPanel.tsx
│   │   │   ├── ProgressPanel.tsx
│   │   │   ├── MessageItem.tsx
│   │   │   ├── MessageInput.tsx
│   │   │   ├── ModelSelector.tsx
│   │   │   └── ...
│   │   ├── stores/                    # Zustand stores
│   │   ├── ipc/
│   │   │   └── client.ts              # HTTP/WS client（封装 fetch + WebSocket）
│   │   └── types/
│   │       └── ipc.ts                 # 共享 IPC 类型
│   └── tests/
├── agent/                             # Python Agent 核心
│   ├── pyproject.toml                 # uv 管理
│   ├── README.md
│   ├── minimax_code/
│   │   ├── __init__.py
│   │   ├── __main__.py                # python -m minimax_code 入口（HTTP/stdio 模式）
│   │   ├── config.py                  # 全局配置
│   │   ├── logging.py
│   │   ├── ipc/
│   │   │   ├── __init__.py
│   │   │   ├── server.py              # JSON-RPC over stdio server（共享 handler registry）
│   │   │   ├── http_server.py         # FastAPI 薄 transport（POST /rpc + GET /ws）
│   │   │   ├── client.py              # 客户端（测试用）
│   │   │   └── protocol.py            # 消息类型定义
│   │   ├── agent/
│   │   │   ├── __init__.py
│   │   │   ├── core.py                # 对话循环
│   │   │   ├── llm.py                 # MiniMax API client
│   │   │   ├── prompts.py             # system prompt 模板
│   │   │   ├── tools/
│   │   │   │   ├── __init__.py
│   │   │   │   ├── base.py            # Tool 抽象 + registry
│   │   │   │   ├── file_ops.py
│   │   │   │   ├── terminal.py
│   │   │   │   ├── edit.py
│   │   │   │   └── search.py
│   │   │   └── skills/
│   │   │       ├── __init__.py
│   │   │       ├── loader.py
│   │   │       ├── registry.py
│   │   │       └── runtime.py
│   │   ├── storage/
│   │   │   ├── __init__.py
│   │   │   ├── db.py                  # SQLite 连接管理
│   │   │   ├── schema.py
│   │   │   ├── migrations/
│   │   │   │   └── 001_initial.py
│   │   │   └── dao/
│   │   │       ├── sessions.py
│   │   │       ├── messages.py
│   │   │       ├── tasks.py
│   │   │       ├── skills.py
│   │   │       ├── scheduled_jobs.py
│   │   │       ├── agents.py
│   │   │       └── permissions.py
│   │   ├── scheduler/
│   │   │   ├── __init__.py
│   │   │   └── cron.py
│   │   ├── orchestrator/              # Phase 2
│   │   │   └── manager.py
│   │   ├── auth/
│   │   │   └── permissions.py
│   │   └── mobile/                    # Phase 2
│   │       └── pairing.py
│   ├── skills/                        # 内置技能
│   │   ├── commit-helper/
│   │   │   └── SKILL.md
│   │   ├── code-review/
│   │   │   ├── SKILL.md
│   │   │   └── tools/
│   │   └── test-generator/
│   │       └── SKILL.md
│   └── tests/
│       ├── conftest.py
│       ├── test_ipc.py
│       ├── test_storage.py
│       ├── test_agent_core.py
│       ├── test_tools.py
│       └── test_skills.py
└── tests/                             # 端到端测试
    ├── e2e/                           # Python 黑盒 smoke（agent stdio 模式）
    │   ├── smoke_sessions.py
    │   ├── smoke_mobile.py
    │   ├── smoke_agents.py
    │   ├── smoke_phase2b.py
    │   ├── smoke_chat.py
    │   ├── smoke_progress.py
    │   └── smoke_model.py
    └── e2e-web/                       # Playwright 跨栈 e2e（web 模式）
        ├── playwright.config.ts
        └── *.spec.ts
```

## 5. 系统架构图

```
┌────────────────────────────────────────────────────────────────┐
│  Browser (Chromium / Firefox / Edge)                            │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  React SPA (web/, served by Vite)                         │  │
│  │  ┌─────────┐ ┌──────────────┐ ┌──────────────────────┐  │  │
│  │  │ Sidebar │ │  Chat Panel  │ │  Progress Panel      │  │  │
│  │  └─────────┘ └──────────────┘ └──────────────────────┘  │  │
│  │  Zustand stores  ◄────  IPC Client (fetch + WebSocket)  │  │
│  └──────────────────────────────────────────────────────────┘  │
│                              │ HTTP POST /rpc + WS /ws         │
│                              │ (127.0.0.1:8765)                │
└──────────────────────────────┼─────────────────────────────────┘
                               │
┌──────────────────────────────▼─────────────────────────────────┐
│  agent/  (Python 3.11+, asyncio)                                │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  HTTP Server (FastAPI, thin transport)                   │  │
│  │  - POST /rpc  ──► IPCServer.handle_request(env) ─┐       │  │
│  │  - GET  /ws   ──► register_listener(cb)  ◄───────┤       │  │
│  │  - GET  /health                                  │       │  │
│  │  - CORS: 5173 + MINIMAX_CODE_CORS_ORIGINS         │       │  │
│  └──────────────────────────────────────────────────┼───────┘  │
│                                                     │          │
│  ┌──────────────────────────────────────────────────▼───────┐  │
│  │  IPC Server (asyncio, shared handler registry)            │  │
│  │  - HandlerRegistry: agent.* / skill.* / task.* /         │  │
│  │    session.* / mobile.* / permission.* / schedule.* /    │  │
│  │    model.* / secrets.* / system.* / __init__.*            │  │
│  │  - register_listener(cb) for HTTP→WS event forwarding    │  │
│  └──────┬────────────────────────────────────────────────────┘  │
│         │                                                        │
│  ┌──────▼─────────┐  ┌────────────────┐  ┌──────────────────┐   │
│  │  Agent Core    │  │  LLM Client    │  │  Tool Registry   │   │
│  │  (loop)        │  │  MiniMax API   │  │  - file_ops      │   │
│  │                │  │  httpx+SSE     │  │  - terminal      │   │
│  │                │  │                │  │  - edit / search │   │
│  │                │  │                │  │  - skill_<X>     │   │
│  └────────────────┘  └────────────────┘  └──────────────────┘   │
│                                                                 │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │  Storage (SQLite)                                          │  │
│  │  - sessions / messages / tasks / skills / jobs / agents   │  │
│  │  - permission_rules / mobile_devices                       │  │
│  └───────────────────────────────────────────────────────────┘  │
│                                                                 │
│  ┌──────────────┐ ┌──────────────┐ ┌────────────────────────┐  │
│  │ Scheduler    │ │ Orchestrator │ │ Auth/Permissions       │  │
│  │ (cron)       │ │ (multi-agent)│ │ (tool gating)          │  │
│  └──────────────┘ └──────────────┘ └────────────────────────┘  │
└─────────────────────────────────────────────────────────────────┘
```

## 6. IPC 契约（概要，详细见 `ipc-contract.md` 与 `v0.2.0-web-architecture.md`）

**协议**：JSON-RPC 2.0，UTF-8。

**Transport（双模式，共享 handler registry）**：

1. **stdio JSON-RPC 2.0** — 给 pytest 子进程 smoke + CLI 调试
   - 命令：`python -m minimax_code --stdio`
   - 编码：UTF-8 行分隔（每条消息一行 JSON，以 `\n` 结尾）
   - 用途：tests/ 下的黑盒 smoke、CLI 调试
2. **HTTP + WebSocket** — 给浏览器里的 web 客户端
   - 命令：`python -m minimax_code`（默认）
   - 端点：`POST /rpc`（请求/响应）、`GET /ws`（流式事件）、`GET /health`（存活探针）
   - 绑定：`127.0.0.1:8765`（CORS allow-list 默认 `http://localhost:5173` / `http://127.0.0.1:5173`，可用 `MINIMAX_CODE_CORS_ORIGINS` 追加受信 origin）
   - HTTP status 永远 `200`，错误走 JSON-RPC 错误信封；`500` 只用于"agent 自己崩了"或"请求无法反序列化"
   - 流式事件（如 `agent.message_chunk`）经 `IPCServer.register_listener(cb)` 推到 WebSocket

**消息方向**：
- 浏览器 → agent：`request` (有 id) / `notification` (无 id) over `POST /rpc`
- agent → 浏览器：`response` / `event`（推送）over `POST /rpc` 响应体 / `GET /ws` 帧

**请求方法**（Python 端实现，前端通过 `POST /rpc` 调用）：
- `agent.send_message` { session_id, content, attachments? } → 流式推送 `agent.message_chunk` event
- `agent.cancel` { session_id }
- `session.create` { title? } → { session_id }
- `session.list` { archived?, limit?, offset? } → { sessions: [...] }
- `session.archive` / `session.unarchive` { session_id }
- `session.delete` { session_id }
- `message.list` { session_id, limit?, before? } → { messages: [...] }
- `skill.list` / `skill.enable` / `skill.disable` / `skill.invoke`
- `scheduler.list_jobs` / `scheduler.create_job` / `scheduler.delete_job` / `scheduler.toggle_job`
- `agent.list_agents` / `agent.spawn_subagent` (Phase 2)
- `mobile.pair` / `mobile.list_devices` / `mobile.send` (Phase 2)
- `permission.set_rule` / `permission.list_rules`
- `model.list` / `model.set_current`

**流式事件**（Python → 前端）：
- `agent.message_chunk` { session_id, message_id, delta, done }
- `agent.tool_call` { session_id, tool_call_id, name, args }
- `agent.tool_result` { session_id, tool_call_id, name?, result, error?, message_id }
- `agent.status` { session_id, status, detail? }
- `task.progress` { task_id, progress, message? }
- `permission.request` { request_id, tool, args } — 前端弹窗确认
- `permission.resolved` { request_id, decision }

**错误码**（标准 JSON-RPC + 自定义）：
- `-32700` ParseError
- `-32600` InvalidRequest
- `-32601` MethodNotFound
- `-32602` InvalidParams
- `-32603` InternalError
- `-32001` ToolExecutionError
- `-32002` PermissionDenied
- `-32003` LLMError
- `-32004` StorageError

## 7. 数据流（典型场景：用户问"列出当前目录"）

1. 用户在输入框敲消息，点发送
2. 前端：`ipc.send('agent.send_message', { session_id, content })` — `IPCClient.request` fetch `POST /rpc`，body 为 JSON-RPC 2.0 envelope
3. Agent HTTP server：反序列化 → 调 `IPCServer.handle_request(env)`
4. Python：IPC server 收到 → 解析 → 调 AgentCore
5. AgentCore：拼 system prompt + history → LLM.stream
6. LLM 返回：包含 tool_call(name=list_directory, args={path: "."})
7. AgentCore：解析 tool_call → 注册 permission 事件 → ToolRegistry.dispatch
8. Tool 执行：list_directory(".") → 返回 ["file1.py", "file2.py", ...]
9. AgentCore：把 tool_result 回填 LLM → LLM 继续生成
10. LLM 返回 final answer："当前目录有以下文件：..."
11. AgentCore：流式推送 `agent.message_chunk` — 经 `IPCServer.register_listener(cb)` 推到所有 WebSocket 客户端
12. 前端：WebSocket 收到事件 → 实时更新 UI
13. 完成后：HTTP 响应体返回 `{result: {session_id, message_id, text}}`；同时存储到 SQLite 的 messages 表

## 8. 关键技术决策

| 决策 | 选择 | 理由 |
|---|---|---|
| 前端分发 | Vite-served React SPA | 单一 web 形态，无桌面壳；hot reload 简单 |
| 前后端 transport | HTTP + WebSocket（FastAPI on agent） | 与 stdio 共享同一 handler registry；浏览器友好，调试标准 |
| 前端框架 | React | 生态最广、招人容易（未来扩展） |
| IPC 协议 | JSON-RPC 2.0（HTTP/WS + stdio 双模式） | 简单、跨语言、调试方便；既有 stdio 测试基础设施全部保留 |
| 异步 | asyncio | Python 生态标准 |
| LLM SDK | 直接用 httpx，不绑 SDK | 可控、易测试 |
| 存储 | SQLite | 本地、零运维 |
| 任务调度 | APScheduler | 成熟、支持 cron、async 友好 |
| 测试 | pytest + vitest + Playwright | 行业标准；Playwright 接替 v0.1.x 缺失的跨栈 e2e |
| 打包 | 无（内部 web 工具） | 不分发安装包，省掉 Rust/Tauri/MSI/NSIS 工具链 |

## 9. 安全边界

- **路径白名单**：工具操作禁止 `..`、禁止访问 `~/.ssh`、系统目录
- **命令超时**：terminal 工具强制超时（默认 30s）
- **授权粒度**：每个工具调用前可弹窗确认（或"始终授权"）
- **API Key 存储**：OS keyring（Windows Credential Manager）
- **日志脱敏**：不在日志里打印 API key、token

## 10. Phase 划分

- **Phase 1**：T0 基础闭环 = project-skeleton + storage + agent-core + ui-shell + skills + 集成测试
- **Phase 2**：多 Agent / 调度 / 移动互联 / 授权粒度 / 进度面板
- **Phase 3**：端到端 chat + 文档
- **Phase 4**：模型选择 + 子 Agent 真 LLM
- **Phase 5**：设置页 + 权限真弹窗 + 密钥 keyring
- **Phase 6**：前端完成度 + 跨栈 e2e
- **v0.2.0 切换（删 Tauri）**：去掉 `src-tauri/` 与 `@tauri-apps/api`；agent 加 HTTP+WS transport（复用 IPCServer 同一份 handler registry）；dev workflow 从 3 终端简化为 2 终端；引入 Playwright 跨栈 e2e。详见 [`docs/v0.2.0-web-architecture.md`](v0.2.0-web-architecture.md)。

---

> 本文档作为所有 worker 任务的契约源。任务 prompt 中提到"参考 docs/architecture.md"时即指本文。如对架构有疑问，先在 deliverable.md 中提出，由 orchestrator 决策。
