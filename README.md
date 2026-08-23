# MiniMax Code

![version](https://img.shields.io/badge/version-1.1.1-blue) ![python](https://img.shields.io/badge/python-3.11%2B-3776ab) ![node](https://img.shields.io/badge/node-20%2B-339933) ![local-first](https://img.shields.io/badge/local--first-SQLite-8b5cf6)

本地优先的个人 AI 编码 Agent。多轮对话、代码工具、技能系统、定时任务、多 Agent 协作、授权管理、Git 与 Code Review 工作流、数据导出备份——全部跑在你自己的机器上。

当前版本：**v1.1.3**（发布公告见 [`docs/release-1.0.0.md`](docs/release-1.0.0.md)）。本地 Web SPA + Python Agent 架构，默认只监听 `127.0.0.1`，会话、配置和任务数据保存在本机 SQLite 中，不经过任何第三方服务器。

## 快速开始

### 个人使用（推荐）

```bash
# 首次：安装依赖
pnpm install
cd agent && uv sync && cd ..

# 之后：一个命令构建并启动完整产品
pnpm start
```

浏览器打开 <http://127.0.0.1:8765>。Python Agent 直接托管构建后的前端，不需要 Vite。首次进入后在 Settings 中配置模型 Provider 和 API Key；没有密钥时自动进入 mock 模式，可完整体验界面和工作流。

### 开发模式（两终端）

```bash
# 终端 1 — Python agent（默认 127.0.0.1:8765）
cd agent
uv run python -m minimax_code

# 终端 2 — Vite 前端 dev server
AGENT_SKIP=1 pnpm dev
# 浏览器打开 http://localhost:5173
```

单终端跑完整开发环境直接 `pnpm dev`（同时拉起 agent + Vite）；已在别处启动 agent 时用 `AGENT_SKIP=1 pnpm dev` 或 `pnpm dev:web` 避免端口竞用。

### 前置环境

- **Node.js 20+**（测试用 22.18）
- **pnpm 9+** — `npm i -g pnpm`
- **Python 3.11+**（测试用 3.12）
- **uv** — `pip install uv` 或 [docs.astral.sh/uv](https://docs.astral.sh/uv/)

## 常见命令

| 命令 | 用途 |
|------|------|
| `pnpm install` | 安装 JS 依赖 |
| `cd agent && uv sync` | 安装 Python 依赖 |
| `pnpm start` | 构建前端 + 单进程启动完整产品（生产模式） |
| `pnpm build` | 只构建前端生产版本（`web/dist/`） |
| `pnpm dev` | 开发模式：同时起 agent + Vite |
| `pnpm dev:web` | 只起 Vite 前端 |
| `pnpm dev:agent` | 只起 agent |
| `pnpm test` | 前端单元测试（vitest） |
| `pnpm lint` | 前端 ESLint 检查 |
| `pnpm py:test` | Python 单元测试（pytest） |
| `pnpm test:e2e` | Playwright 跨栈 e2e（需先起 dev 服务） |

## 常用环境变量

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `MINIMAX_API_KEY` | 空（mock 模式） | MiniMax API 密钥 |
| `MINIMAX_CODE_HTTP_PORT` | `8765` | Agent HTTP/WS 端口 |
| `MINIMAX_CODE_HTTP_HOST` | `127.0.0.1` | Agent 绑定地址 |
| `MINIMAX_CODE_DATA_DIR` | 系统数据目录 | SQLite 数据库所在目录 |
| `MINIMAX_CODE_CORS_ORIGINS` | dev 白名单 | 生产/自定义 origin 允许列表 |
| `MINIMAX_CODE_LOG_FILE` | 空（仅控制台） | 日志落盘路径（带轮转） |
| `VITE_AGENT_URL` | `http://127.0.0.1:8765` | 前端连接的 agent 地址 |
| `VITE_AGENT_MODE` | 自动检测 | 设为 `mock` 强制前端 mock 模式 |

生产部署细节（端口、数据目录、日志、健康检查）见 [`docs/deployment.md`](docs/deployment.md)。

## FAQ

**Q: 没有 MiniMax API Key 能用吗？**
A: 能。无 Key 时 agent 和前端都自动降级到 mock 模式：界面、工作流、工具调用全部可用，只是回复是确定性假文本。在 Settings 里配置密钥即可切到真实模型（密钥存 OS keyring，不落盘）。

**Q: 我的数据存在哪里？怎么备份？**
A: 全部在本机 SQLite 单文件（默认在系统数据目录，Windows 为 `%APPDATA%\MiniMaxCode\`）。备份有三种方式：Settings → Data 标签页一键备份（SQLite 在线快照）；导出 JSON 信封（跨 schema 可移植）；或直接复制数据目录（需先停 agent）。

**Q: 换机器怎么迁移？**
A: 旧机器 Settings → Data 导出 JSON → 新机器安装启动后在同一入口导入。导入是替换式整体事务（all-or-nothing），失败自动回滚。

**Q: 启动 agent 提示 dist 缺失？**
A: `pnpm start` 自带构建（等价于 `pnpm build` 后启动）。如果你直接跑 `pnpm dev:agent` 而构建产物不存在，agent 会友好报错并指引——先执行 `pnpm build`，或直接改用 `pnpm start`。

**Q: 8765 端口被占用？**
A: `MINIMAX_CODE_HTTP_PORT=8899 pnpm dev:agent` 换端口启动 agent，前端开发模式下用 `VITE_AGENT_URL=http://127.0.0.1:8899 pnpm dev:web` 指向它。

**Q: 如何升级到新版本？**
A: `git pull && pnpm install && cd agent && uv sync && cd .. && pnpm build`。SQLite 迁移在下次启动时自动执行（幂等、事务化，失败整体回滚）。

**Q: 怎么跑测试？**
A: 三层：`pnpm py:test`（Python 单元）、`pnpm test`（前端单元）、`pnpm test:e2e`（Playwright 跨栈，需先起 dev 服务）。另有 `tests/e2e/` 下 6 个 Python 黑盒 smoke（subprocess 驱动 stdio 模式，需空 workdir 隔离数据）。

**Q: 会话数据会被上传吗？**
A: 不会。除调用你配置的 LLM API 外，所有数据只在本地；agent 默认只绑定 `127.0.0.1`，不对外网监听。

## 架构

```
Browser (Vite/React SPA)
  |  HTTP POST /rpc (JSON-RPC 2.0) + WebSocket /ws (流式事件)
  v
Python Agent (FastAPI + asyncio, 127.0.0.1:8765)
  |- AgentCore（对话循环 + LLM 流式）
  |- ToolRegistry / SkillRuntime / SubAgentRuntime / Scheduler
  |- SQLite 存储（24 张表 + FTS/向量虚表，幂等事务化迁移）
  |- PermissionStore（工具调用授权）+ OS keyring（密钥）
```

| 层 | 技术 |
|---|---|
| 前端 | React 18 + Vite + TypeScript + Tailwind + Zustand（`web/src/`） |
| 通信 | HTTP + WebSocket，JSON-RPC 2.0（生产模式同端口同源） |
| Agent | Python 3.11+ asyncio；stdio 模式保留给测试 |
| 存储 | SQLite（aiosqlite）+ APScheduler 持久化 cron |
| 测试 | pytest（10100+）+ vitest（600+）+ Playwright e2e（10 specs） |

契约与设计文档见 [`docs/architecture.md`](docs/architecture.md) / [`docs/ipc-contract.md`](docs/ipc-contract.md) / [`docs/storage-schema.md`](docs/storage-schema.md)，面向使用者的完整操作手册见 [`docs/user-guide.md`](docs/user-guide.md)。

## 目录结构

```
.
├── docs/                  # 架构 / IPC 契约 / 存储模型 / 部署 / 设计文档
├── web/                   # React + Vite 前端（components / stores / ipc / ui）
├── agent/                 # Python agent 核心
│   ├── minimax_code/      # ipc / agent / tools / skills / storage / scheduler
│   │                      # orchestrator / auth / mobile / progress / secrets
│   └── tests/             # pytest 单元
├── e2e/                   # Playwright 跨栈 e2e（含生产模式 spec）
├── tests/e2e/             # Python 黑盒 smoke（agent stdio 模式）
├── scripts/               # dev.mjs / start-agent.* 等开发辅助
├── CHANGELOG.md           # 版本变更历史
└── README.md
```

## License

Internal use only.
