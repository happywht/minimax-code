# MiniMax Code

本地优先的个人 AI 编码 Agent。支持多轮对话、代码工具、技能系统、定时任务、多 Agent 协作、授权管理、Git 与 Code Review 工作流。

当前版本：**v0.11.0**。产品采用本地 Web SPA + Python Agent 架构，默认只监听 `127.0.0.1`，会话、配置和任务数据保存在本机 SQLite 中。

## 个人使用（推荐）

首次安装依赖：

```bash
pnpm install
cd agent && uv sync && cd ..
```

之后使用一个命令构建并启动完整产品：

```bash
pnpm start
```

浏览器打开 <http://127.0.0.1:8765>。Python Agent 会直接托管构建后的前端，不需要长期运行 Vite。首次进入后可在 Settings 中配置模型 Provider 和 API Key；没有密钥时会进入 mock 模式，便于体验界面和工作流。

## 快速启动（dev mode — 两终端）

```bash
# 终端 1 — Python agent（HTTP 服务，默认绑 127.0.0.1:8765）
cd agent
uv run python -m minimax_code
#  → "agent server listening on http://127.0.0.1:8765"

# 终端 2 — Vite 前端 dev server
AGENT_SKIP=1 pnpm dev
#  → vite ready

# 浏览器开 http://localhost:5173
```

> 想单终端跑完整开发环境：直接 `pnpm dev`，它会同时拉起 agent + Vite。
> 如果你已经在另一个终端手动启动了 agent，就用 `AGENT_SKIP=1 pnpm dev`
> 或 `pnpm dev:web` 只启动前端，避免 8765 端口竞用。

### 首次安装依赖

```bash
# 1. 装 JS 依赖
pnpm install

# 2. 装 Python 依赖（agent 端）
cd agent && uv sync && cd ..
```

完整契约见 [`docs/architecture.md`](docs/architecture.md) / [`docs/ipc-contract.md`](docs/ipc-contract.md) / [`docs/v0.2.0-web-architecture.md`](docs/v0.2.0-web-architecture.md)。

## 当前状态（v0.11.0）

- **Phase 1（基础闭环）**：✅ — Vite + React 18 前端 + Python agent 核心 + SQLite 存储 + 技能系统
- **Phase 2a（授权 / 调度 / 进度）**：✅
- **Phase 2b（会话历史 / 移动配对 / 多 Agent）**：✅ — 49+ 单测全过 + 17 步集成 e2e smoke 全过
- **Phase 3（端到端 chat + 文档）**：✅ — `agent.send_message` 真接通 AgentCore + mock LLM + 消息持久化
- **Phase 4（模型选择 + 子 Agent 真 LLM）**：✅ — `model.list/get_current/set_current` IPC + `SubAgentRuntime` 走真 MiniMax API（注入式）
- **Phase 5（设置页 + 权限真弹窗 + 密钥 keyring）**：✅ — Settings 三 Tab、tool-call 运行时授权弹窗、API Key 走 OS keyring
- **Phase 6（前端完成度 + Playwright e2e + 切到 web）**：✅ — 技能面板 + 三栏布局 + 工作区切换 + per-turn 摘要 + Playwright 跨栈 e2e 跑通
- **v0.2.0 切换（删 Tauri）**：✅ — 去掉 `src-tauri/`、去掉 `@tauri-apps/api`、`scripts/dev.mjs` 起 agent + Vite，dev 工作流从 3 终端简化为 2 终端

## 架构

| 层 | 技术 | 备注 |
|---|---|---|
| 前端 | React 18 + Vite + TypeScript + Tailwind + Zustand | 见 `web/src/`；Vite-served SPA |
| Transport | HTTP + WebSocket (FastAPI on agent) | POST `/rpc` + GET `/ws`（`127.0.0.1:8765`） |
| Agent 核心 | Python 3.11+ (asyncio) | JSON-RPC 2.0 over HTTP/WS；stdio 模式保留给测试 |
| LLM 客户端 | httpx (async) | MiniMax API；mock mode（无 KEY 时降级） |
| 存储 | SQLite (aiosqlite) | 8 张表（sessions / messages / tasks / skills / scheduled_jobs / permission_rules / mobile_devices / agents） |
| 调度 | APScheduler | 持久化 cron |
| 测试 | pytest + vitest + Playwright | 单元 + e2e smoke + 跨栈 e2e |

详细见 [`docs/architecture.md`](docs/architecture.md) / [`docs/ipc-contract.md`](docs/ipc-contract.md)。

## 目录结构

```
.
├── docs/                  # 架构 / IPC 契约 / 设计文档
├── web/                   # React + Vite 前端
│   └── src/
│       ├── components/    # Sidebar / ChatPanel / MessageInput / ProgressPanel / SkillPanel / ...
│       ├── stores/        # Zustand stores
│       ├── ipc/           # IPC client (HTTP/WS 包装)
│       └── types/         # 共享类型
├── agent/                 # Python agent 核心
│   ├── minimax_code/
│   │   ├── ipc/           # asyncio JSON-RPC server (HTTP + stdio) + handlers
│   │   ├── agent/         # AgentCore + MiniMaxClient (LLM)
│   │   ├── tools/         # file_ops / terminal / edit / search / skill
│   │   ├── skills/        # SKILL.md loader + registry + runtime
│   │   ├── storage/       # SQLite + 8 个 DAO + migrations
│   │   ├── scheduler/     # APScheduler
│   │   ├── orchestrator/  # SubAgentRuntime
│   │   ├── auth/          # PermissionStore
│   │   ├── mobile/        # PairingManager
│   │   ├── progress/      # ProgressTracker
│   │   └── secrets/       # OS keyring + env-var fallback
│   └── tests/             # pytest 单元
├── tests/                 # e2e 测试
│   ├── e2e/               # Python 黑盒 smoke（agent stdio）
│   └── e2e-web/           # Playwright 跨栈 e2e
├── scripts/               # dev.mjs / dev-agent.mjs / start-agent.*
├── package.json
├── pnpm-workspace.yaml
├── README.md
└── CHANGELOG.md
```

## 前置环境（仅源码开发需要）

- **Node.js 20+** (测试用 22.18)
- **pnpm 9+** — `npm i -g pnpm`
- **Python 3.11+** (测试用 3.12)
- **uv** — `pip install uv` 或下载 [astral-sh/uv](https://docs.astral.sh/uv/)

> v0.1.x 时代需要的 Rust / Tauri CLI / Visual Studio Build Tools / WiX 已全部丢弃。
> 普通用户直接 clone 仓库 + 两终端命令即可，不再有安装包。

## 测试

```bash
# Python 单元
pnpm py:test
# 等价于：cd agent && uv run pytest

# 前端单元（vitest）
pnpm test
# 等价于：pnpm --filter @minimax/web test

# e2e 跨栈（Playwright）— 起两终端的 dev 服务后跑
pnpm test:e2e
# 等价于：playwright test

# Python 黑盒 smoke（subprocess 驱动，跑前需在另一终端起 agent）
cd tests
python e2e/smoke_sessions.py   <python> <agent_dir> <workdir>
python e2e/smoke_mobile.py     <python> <agent_dir> <workdir>
python e2e/smoke_agents.py     <python> <agent_dir> <workdir>
python e2e/smoke_phase2b.py    <python> <agent_dir> <workdir>
python e2e/smoke_chat.py       <python> <agent_dir> <workdir>
python e2e/smoke_progress.py   <python> <agent_dir> <workdir>
python e2e/smoke_model.py      <python> <agent_dir> <workdir>
```

> 跑 Python smoke 时 `<workdir>` 必须是**新创建的**空目录 — 所有 smoke 内部用
> `MINIMAX_CODE_DATA_DIR=<workdir>` 隔离 DB，避免相互污染。

## 分发（release build）

**v0.2.0 内部无打包流程。** 项目是内部 web 工具，**不分发安装包**。要给同事用：

```bash
git clone <repo>
pnpm install
cd agent && uv sync
# 然后发"两条命令两终端"的说明即可
```

v0.1.x 时代的 Tauri NSIS / MSI 安装包保留在 `src-tauri/target/release/bundle/`（如历史
artifact 还在硬盘上），不再被任何文档引用。要彻底删 `src-tauri/` 目录，可 `git rm` 整
个目录（团队已实施）。

## 已知限制

### 1. `thinking_count` metadata 字段未实现

Web 端 `MessageMetadata` 类型里预留了 `thinkingCount: number` 字段，但当前 AgentCore 的 `metadata` payload 不发这个键。前端会安全地 fallback 到 `0`。后续计划接通真实 MiniMax API 的 thinking-token 计数。

### 2. Sub-agent LLM 走 mock mode（除注入式之外）

`SubAgentRuntime` 默认用 `AgentCore(llm=None)` — stub 模式返回确定性文本。主 chat 链路（`agent.send_message`）的 mock mode 触发条件是 `MINIMAX_API_KEY` 为空。已加 `SubAgentRuntime.inject_llm(client)` 入口：测试用真 LLM 时手动注入 `MiniMaxClient` 即可。`smoke_agents` 已支持这种注入式 e2e。

### 3. 旧 README "Tasks ahead" 段

仓库原 README（Phase 1 skeleton 时代）里"Tasks ahead"等段已过时 — 当前所有 Phase 1–6 + v0.2.0 切换任务都已完成。本 README 是 v0.2.0 终态。完整变更见 [`CHANGELOG.md`](CHANGELOG.md)。

## License

Internal use only.
