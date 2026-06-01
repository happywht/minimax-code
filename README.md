# MiniMax Code

桌面端 AI 编码 Agent 复刻项目。对标 MiniMax Code 全量功能：多轮对话、技能系统、定时任务、多 Agent 协作、移动互联、授权管理、进度面板。

## 当前状态

- **Phase 1（基础闭环）**：✅ 已完成 — Tauri 2.x 桌面壳 + React 18 前端 + Python agent 核心 + SQLite 存储 + 技能系统
- **Phase 2a（授权 / 调度 / 进度）**：✅ 已完成
- **Phase 2b（会话历史 / 移动配对 / 多 Agent）**：✅ 已完成 — 49 个单测全过 + 17 步集成端到端 smoke 全过
- **Phase 3（端到端 chat + 文档）**：
  - ✅ `agent.send_message` 真接通 AgentCore + mock LLM + 消息持久化
  - ⚠️ Tauri release build 卡在你的开发机环境（见"已知限制"），源码本身完整

## 架构

| 层 | 技术 | 备注 |
|---|---|---|
| 桌面壳 | Tauri 2.x (Rust) | sidecar 模式 spawn Python |
| 前端 | React 18 + Vite + TypeScript + Tailwind + Zustand | 见 `web/src/` |
| Agent 核心 | Python 3.11+ (asyncio) | JSON-RPC 2.0 over stdio |
| LLM 客户端 | httpx (async) | MiniMax API；mock mode（无 KEY 时降级） |
| 存储 | SQLite (aiosqlite) | 8 张表（sessions / messages / tasks / skills / scheduled_jobs / permission_rules / mobile_devices / agents） |
| 调度 | APScheduler | 持久化 cron |
| 测试 | pytest + pytest-asyncio | 单元 + subprocess 端到端 smoke |

详细见 [`docs/architecture.md`](docs/architecture.md)。

## 目录结构

```
.
├── docs/                  # 架构 / IPC 契约 / 构建报告
├── src-tauri/             # Tauri Rust 壳（main.rs / lib.rs / ipc.rs / commands.rs）
├── web/                   # React + Vite 前端
│   └── src/
│       ├── components/    # Sidebar / ChatPanel / MessageInput / ProgressPanel ...
│       ├── stores/        # Zustand stores
│       ├── ipc/           # IPC client (Tauri invoke/listen 包装)
│       └── types/         # 共享类型
├── agent/                 # Python agent 核心
│   ├── minimax_code/
│   │   ├── ipc/           # asyncio JSON-RPC server + handlers
│   │   ├── agent/         # AgentCore + MiniMaxClient (LLM)
│   │   ├── tools/         # file_ops / terminal / edit / search / skill
│   │   ├── skills/        # SKILL.md loader + registry + runtime
│   │   ├── storage/       # SQLite + 8 个 DAO + migrations
│   │   ├── scheduler/     # APScheduler
│   │   ├── orchestrator/  # SubAgentRuntime
│   │   ├── auth/          # PermissionStore
│   │   ├── mobile/        # PairingManager
│   │   └── progress/      # ProgressTracker
│   └── tests/             # pytest 单元
├── tests/                 # e2e subprocess smoke（black-box）
│   └── e2e/
│       ├── smoke_sessions.py
│       ├── smoke_mobile.py
│       ├── smoke_agents.py
│       ├── smoke_phase2b.py
│       └── smoke_chat.py
├── package.json
├── pnpm-workspace.yaml
└── README.md
```

## 前置环境

- **Node.js 20+** (测试用 22.18)
- **pnpm 9+** — `npm i -g pnpm`
- **Python 3.11+** (测试用 3.12)
- **uv** — `pip install uv` 或下载 [astral-sh/uv](https://docs.astral.sh/uv/)
- **Rust 1.77+** + Tauri 工具链（见"已知限制"）

## 快速启动（dev mode）

```bash
# 1. 装 JS 依赖
pnpm install

# 2. 装 Python 依赖（agent 端）
cd agent
uv pip install -e .        # 或 pip install -e .
cd ..

# 3. 三个终端，分别跑：
# 终端 1 — Python agent（IPC server）
cd agent
python -m minimax_code
# 或：uv run python -m minimax_code

# 终端 2 — Vite 前端 dev server
cd web
pnpm dev
# 监听 http://localhost:5173

# 终端 3 — Tauri 桌面壳（用 webview 打开 web 端 + 调 Python agent）
cd src-tauri
cargo tauri dev
```

> Tauri 的 `invoke` / `listen` API **只在 Tauri webview 里可用** — 真要看完整 demo 必须用 `cargo tauri dev`，普通浏览器看 `localhost:5173` 只能用 stub IPC。

## 测试

```bash
# Python 单元（49 个 case）
cd agent
pytest -q

# e2e subprocess smoke（每个 smoke 是独立的 black-box 端到端）
# 都遵循统一约定：spawn 真实 agent 进程、跑 IPC、读磁盘
cd tests
python e2e/smoke_sessions.py  <python> <agent_dir> <workdir>
python e2e/smoke_mobile.py    <python> <agent_dir> <workdir>
python e2e/smoke_agents.py    <python> <agent_dir> <workdir>
python e2e/smoke_phase2b.py   <python> <agent_dir> <workdir>
python e2e/smoke_chat.py      <python> <agent_dir> <workdir>
```

> 跑 smoke 时 `<workdir>` 必须是**新创建的**空目录 — 所有 smoke 内部用 `MINIMAX_CODE_DATA_DIR=<workdir>` 隔离 DB，避免相互污染。

## 打包发布

```bash
cd src-tauri
cargo tauri build
# 产出：src-tauri/target/release/bundle/{nsis,msi,dmg,deb,appimage}/
```

> 前提：开发机已装对应平台的工具链（见"已知限制"）。

## 已知限制

### 1. Tauri release build 需要完整平台工具链

当前 `src-tauri/` 源码完整（~320 LOC，5 个 IPC handler / JSON-RPC bridge / Tauri 2.x setup），但 `cargo tauri build` 在开发机上失败 — 缺 MSVC 工具链 / rustup / MinGW。详细：

[`docs/build-reports/2026-06-02-tauri-build-blocked.md`](docs/build-reports/2026-06-02-tauri-build-blocked.md)

修复路径（二选一）：

- **Windows** — 装 [Visual Studio Build Tools](https://visualstudio.microsoft.com/visual-studio-build-tools/) "Desktop development with C++" workload
- **跨平台** — 装 [rustup](https://rustup.rs/) 然后 `rustup default stable-gnu` + MinGW-w64

### 2. Sub-agent LLM 走 mock mode

`SubAgentRuntime` 当前用 `AgentCore(llm=None)` — LLM 调用是 stub，返回确定性文本。Phase 4 会切真 MiniMax API（设置 `MINIMAX_API_KEY` env 即可启用）。

主 chat 链路（`agent.send_message`）的 mock mode 触发条件是 `MINIMAX_API_KEY` 为空 — 走 `MiniMaxClient` 的内置 fixture，方便本地/CI 跑端到端。

### 3. Phase 1.x 已 documented 的旧 README 段落

仓库原 README（Phase 1 skeleton 时代）里"Tasks ahead"等段已过时 — 当前所有 Phase 1+2a+2b+3 任务都已完成。本 README 是最终态。
