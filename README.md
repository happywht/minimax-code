# MiniMax Code

![version](https://img.shields.io/badge/version-1.6.0-blue) ![python](https://img.shields.io/badge/python-3.11%2B-3776ab) ![node](https://img.shields.io/badge/node-20%2B-339933) ![local-first](https://img.shields.io/badge/local--first-SQLite-8b5cf6)

本地优先的个人 AI 编码 Agent。多轮对话、代码工具、技能系统、定时任务、多 Agent 协作、授权管理、Git 与 Code Review 工作流、数据导出备份——全部跑在你自己的机器上。

当前包版本：**v1.6.0，另有未发布修复**。本地 Web SPA + Python Agent 架构，默认只监听 `127.0.0.1`。会话、配置和任务元数据在本机 SQLite；模型请求及外部工具可能把选中的内容发送给配置的服务。

当前定位为个人开发预览版。实际能力及限制见 [能力基线](docs/capability-baseline.md)，后续实施顺序见 [个人生产路线图](docs/production-roadmap.md)，测试证据规则见 [分层验证](docs/verification.md)。测试通过不代表真实模型任务或无人值守生产能力已经验收。

## 快速开始

### 个人使用（推荐）

```bash
# 首次：安装依赖
pnpm install
cd agent && uv sync && cd ..
pnpm build

# 之后：启动已准备好的版本（不重新构建或安装依赖）
pnpm start
```

浏览器打开 <http://127.0.0.1:8765>。Python Agent 直接托管构建后的前端，不需要 Vite。首次进入后在 Settings 中配置模型 Provider 和 API Key；默认 MiniMax 配置没有密钥时使用 mock 回复，可检查界面与受控流程，不能验证真实任务质量。

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
| `pnpm start` | 检查已安装环境和前端产物，启动完整产品（生产模式） |
| `pnpm build` | 只构建前端生产版本（`web/dist/`） |
| `pnpm dev` | 开发模式：同时起 agent + Vite |
| `pnpm dev:web` | 只起 Vite 前端 |
| `pnpm dev:agent` | 只起 agent |
| `pnpm test` | 前端单元测试（vitest） |
| `pnpm lint` | 前端 ESLint 检查 |
| `pnpm py:test` | Python 单元测试（pytest） |
| `pnpm test:e2e` | Playwright 自动启动隔离服务，使用受控模型响应 |
| `pnpm verify:baseline` | lint、构建及连续三轮前端/后端核心/协议回放/跨栈回归 |
| `pnpm verify:full` | 独立后端全量回归；日志写入 `.verification/` |

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
A: 可以使用其他已配置 Provider。默认 MiniMax 无 Key 时使用确定性 mock 回复；前端独立 mock 需设置 `VITE_AGENT_MODE=mock`。这些模式不证明真实工具任务成功。密钥通过服务商设置管理，使用 OS keyring 或环境变量。

**Q: 我的数据存在哪里？怎么备份？**
A: 会话与配置等元数据在系统数据目录中的 SQLite；项目文件、工作区输出和凭据分开保存。Settings → Data 提供数据库在线快照及 JSON 导出，但不等于完整工作区和凭据备份。完整备份恢复流程将在 M7 验收。

**Q: 换机器怎么迁移？**
A: 旧机器 Settings → Data 导出 JSON → 新机器安装启动后在同一入口导入。导入是替换式整体事务（all-or-nothing），失败自动回滚。

**Q: 启动 agent 提示 dist 缺失？**
A: 先执行一次 `pnpm build`，再运行 `pnpm start`。生产启动不会重新构建或安装依赖；缺少前端产物时会给出准备命令。开发模式的 `pnpm dev:agent` 仍允许只启动 API。

**Q: 8765 端口被占用？**
A: `MINIMAX_CODE_HTTP_PORT=8899 pnpm dev:agent` 换端口启动 agent，前端开发模式下用 `VITE_AGENT_URL=http://127.0.0.1:8899 pnpm dev:web` 指向它。

**Q: 如何升级到新版本？**
A: `git pull && pnpm install && cd agent && uv sync && cd .. && pnpm build`。SQLite 迁移在下次启动时自动执行（幂等、事务化，失败整体回滚）。

**Q: 怎么跑测试？**
A: 推荐先按 [分层验证](docs/verification.md) 安装开发依赖，再运行 `pnpm verify:baseline`。Playwright 会自动启动独立端口和数据目录的服务，不需要预先启动开发环境。全量 Python 回归用 `pnpm verify:full`，真实模型评测独立管理，不混入默认基线。

**Q: 会话数据会被上传吗？**
A: 配置的模型 API 会接收生成所需的上下文；MCP、Hooks、终端和联网工具也可能向外部服务传输内容。本地优先是存储与运行架构，不是断网承诺。Agent 默认只绑定 `127.0.0.1`。

## 架构

```
Browser (Vite/React SPA)
  |  HTTP POST /rpc (JSON-RPC 2.0) + WebSocket /ws (流式事件)
  v
Python Agent (FastAPI + asyncio, 127.0.0.1:8765)
  |- AgentCore（对话循环 + LLM 流式）
  |- ToolRegistry / SkillRuntime / SubAgentRuntime / Scheduler
  |- SQLite 存储（递增迁移 + FTS/向量虚表）
  |- PermissionStore（工具调用授权）+ OS keyring（密钥）
```

| 层 | 技术 |
|---|---|
| 前端 | React 18 + Vite + TypeScript + Tailwind + Zustand（`web/src/`） |
| 通信 | HTTP + WebSocket，JSON-RPC 2.0（生产模式同端口同源） |
| Agent | Python 3.11+ asyncio；stdio 模式保留给测试 |
| 存储 | SQLite（aiosqlite）+ APScheduler 持久化 cron |
| 测试 | pytest + vitest + Playwright；当前实际结果以 `.verification/` 报告为准 |

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
