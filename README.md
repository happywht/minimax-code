# MiniMax Code

桌面端 AI 编码 Agent 复刻项目。对标 MiniMax Code 全量功能：多轮对话、技能系统、定时任务、多 Agent 协作、移动互联、授权管理、进度面板。

> **v0.1.2 内部首发版。** v0.1.1 修复版 — 修了 sidecar 路径解析错（v0.1.1 装完白屏退出），详见下方"已知限制"段与 [`CHANGELOG.md`](CHANGELOG.md)。完整 Phase 1 → Phase 6 已完成（端到端 chat、技能系统、调度、进度、移动配对、子 Agent、模型选择、设置、权限真弹窗、密钥 keyring、三栏布局、工作区切换、per-turn 摘要）。

## 安装

### Windows 用户 — 下载安装包

`v0.1.2` 内部发布版提供两种 Windows 安装包（build by Tauri 2.x）：

| 类型 | 文件 | 大小 | SHA256 |
|------|------|-----:|--------|
| **NSIS 安装包**（推荐） | `MiniMax Code_0.1.2_x64-setup.exe` | 3.18 MB (3,333,019 字节) | `2DD04D11B24AD7D58A7B989F3D6634E3D49587AA351B7253020DCE7C54216C2D` |
| **MSI 包** | `MiniMax Code_0.1.2_x64_en-US.msi` | 3.93 MB (4,124,672 字节) | `1DAA16151C6BF5F36180728F59ED0BD467C131A93E489D74D52D9A45FC10E32E` |

校验：

```powershell
Get-FileHash "MiniMax Code_0.1.2_x64-setup.exe" -Algorithm SHA256
# 期望：2DD04D11B24AD7D58A7B989F3D6634E3D49587AA351B7253020DCE7C54216C2D

Get-FileHash "MiniMax Code_0.1.2_x64_en-US.msi" -Algorithm SHA256
# 期望：1DAA16151C6BF5F36180728F59ED0BD467C131A93E489D74D52D9A45FC10E32E
```

> 文件位置（开发机构建产物）：`src-tauri/target/release/bundle/{nsis,msi}/`。`v0.1.2` annotated tag 直接指向本 release commit；`v0.1.0` / `v0.1.1` tag 保留为历史记录（均已被 v0.1.2 取代）。

### 开发者 — 从源码构建

```bash
# 1. 装依赖
pnpm install
cd agent && uv pip install -e . && cd ..

# 2. 出 Windows 安装包（NSIS + MSI）
cd src-tauri
cargo tauri build
# 产物：src-tauri/target/release/bundle/{nsis,msi}/
```

> 前置：Node 20+、Python 3.11+、Rust 1.77+、Tauri 2.x 工具链（`cargo install tauri-cli@^2`）、Windows NSIS（自动随 Tauri 安装）、WiX 3.x（出 MSI 时需要）。

## 当前状态（v0.1.0）

- **Phase 1（基础闭环）**：✅ — Tauri 2.x 桌面壳 + React 18 前端 + Python agent 核心 + SQLite 存储 + 技能系统
- **Phase 2a（授权 / 调度 / 进度）**：✅
- **Phase 2b（会话历史 / 移动配对 / 多 Agent）**：✅ — 49+ 单测全过 + 17 步集成 e2e smoke 全过
- **Phase 3（端到端 chat + 文档）**：✅ — `agent.send_message` 真接通 AgentCore + mock LLM + 消息持久化
- **Phase 4（模型选择 + 子 Agent 真 LLM）**：✅ — `model.list/get_current/set_current` IPC + `SubAgentRuntime` 走真 MiniMax API（注入式）
- **Phase 5（设置页 + 权限真弹窗 + 密钥 keyring）**：✅ — Settings 三 Tab、tool-call 运行时授权弹窗、API Key 走 OS keyring
- **Phase 6（前端完成度 + Tauri release build）**：✅ — 技能面板 + 三栏布局 + 工作区切换 + per-turn 摘要 + Tauri NSIS/MSI 包成功出

## 架构

| 层 | 技术 | 备注 |
|---|---|---|
| 桌面壳 | Tauri 2.x (Rust) | sidecar 模式 spawn Python |
| 前端 | React 18 + Vite + TypeScript + Tailwind + Zustand | 见 `web/src/` |
| Agent 核心 | Python 3.11+ (asyncio) | JSON-RPC 2.0 over stdio |
| LLM 客户端 | httpx (async) | MiniMax API；mock mode（无 KEY 时降级） |
| 存储 | SQLite (aiosqlite) | 8 张表（sessions / messages / tasks / skills / scheduled_jobs / permission_rules / mobile_devices / agents） |
| 调度 | APScheduler | 持久化 cron |
| 测试 | pytest + pytest-asyncio | 单元 + subprocess e2e smoke |

详细见 [`docs/architecture.md`](docs/architecture.md)。

## 目录结构

```
.
├── docs/                  # 架构 / IPC 契约 / 构建报告
├── src-tauri/             # Tauri Rust 壳（main.rs / lib.rs / ipc.rs / commands.rs）
├── web/                   # React + Vite 前端
│   └── src/
│       ├── components/    # Sidebar / ChatPanel / MessageInput / ProgressPanel / SkillPanel / ...
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
│   │   ├── progress/      # ProgressTracker
│   │   └── secrets/       # OS keyring + env-var fallback
│   └── tests/             # pytest 单元
├── tests/                 # e2e subprocess smoke（black-box）
│   └── e2e/
│       ├── smoke_sessions.py
│       ├── smoke_mobile.py
│       ├── smoke_agents.py
│       ├── smoke_phase2b.py
│       ├── smoke_chat.py
│       ├── smoke_progress.py
│       └── smoke_model.py
├── package.json
├── pnpm-workspace.yaml
├── README.md
└── CHANGELOG.md
```

## 前置环境（仅源码构建 / 开发需要）

- **Node.js 20+** (测试用 22.18)
- **pnpm 9+** — `npm i -g pnpm`
- **Python 3.11+** (测试用 3.12)
- **uv** — `pip install uv` 或下载 [astral-sh/uv](https://docs.astral.sh/uv/)
- **Rust 1.77+** + Tauri 2.x CLI（`cargo install tauri-cli@^2`）
- **Windows** — Visual Studio Build Tools "Desktop development with C++" workload（出 MSI 还要 WiX 3.x）

> 终端用户直接用安装包，不需要上面这些。

## 快速启动（dev mode — 三终端）

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

> Tauri 的 `invoke` / `listen` API **只在 Tauri webview 里可用** — 普通浏览器看 `localhost:5173` 只能用 stub IPC。要看完整 demo 必须用 `cargo tauri dev`，或直接装 `MiniMax Code_0.1.0_x64-setup.exe` 跑 release 客户端。

## 测试

```bash
# Python 单元
cd agent
pytest -q

# e2e subprocess smoke（每个 smoke 是独立的 black-box 端到端）
# 都遵循统一约定：spawn 真实 agent 进程、跑 IPC、读磁盘
cd tests
python e2e/smoke_sessions.py   <python> <agent_dir> <workdir>
python e2e/smoke_mobile.py     <python> <agent_dir> <workdir>
python e2e/smoke_agents.py     <python> <agent_dir> <workdir>
python e2e/smoke_phase2b.py    <python> <agent_dir> <workdir>
python e2e/smoke_chat.py       <python> <agent_dir> <workdir>
python e2e/smoke_progress.py   <python> <agent_dir> <workdir>
python e2e/smoke_model.py      <python> <agent_dir> <workdir>
```

> 跑 smoke 时 `<workdir>` 必须是**新创建的**空目录 — 所有 smoke 内部用 `MINIMAX_CODE_DATA_DIR=<workdir>` 隔离 DB，避免相互污染。

## 出包（release build）

```bash
cd src-tauri
cargo tauri build
# 产物：src-tauri/target/release/bundle/{nsis,msi}/
```

> v0.1.0 已成功出包 — 见"安装"段的 SHA256 校验值。

## 已知限制

### 0. v0.1.0 / v0.1.1 启动崩溃（v0.1.2 已修）

**v0.1.0**: Tauri `setup()` 把 `app.manage(AppState)` 放在 fire-and-forget 的 async task 里跑 — webview 一 mount，前端 `session.list` 等首批 IPC 调用撞上 state 还没注册，5 个 toast 全是 `state not managed`。

**v0.1.1**: 修 race，setup 改成同步 `init_agent_bridge` + 立即 `app.manage(...)`，但**暴露了第二个 bug：sidecar 路径解析错**。`ipc::sidecar_command` 用了 `"binaries/{}"` 前缀，而 Tauri 2.x `externalBin` 在 production bundle 里把 sidecar 放到资源根目录（install dir）且**不带 target-triple 后缀**。结果 `app.path().resolve("binaries/minimax-code-agent.exe", Resource)` 失败，回退到 dev 分支调 `python -m minimax_code`，但 production 用户的 PATH 里通常没有 `python`，且 agent 模块也没装。setup 返回 Err，Tauri 白屏 + 立刻退出。

**v0.1.2**: 改成 `app.path().resolve("minimax-code-agent.exe", BaseDirectory::Resource)`（无 `binaries/` 前缀），命中 production sidecar 路径。dev fallback 保留。装完应该看到正常启动 + 5 个首批 IPC 不报错 + 列表加载。

### 1. Tauri 端到端 e2e smoke 未在已安装包上跑

代码层面所有 Phase 1–6 e2e smoke（sessions / mobile / agents / chat / progress / model）均通过，**v0.1.2 起需在装好的桌面端手动验证**，自动化（Playwright / WebDriver）放 v0.1.3。

### 2. `thinking_count` metadata 字段未实现

Web 端 `MessageMetadata` 类型里预留了 `thinkingCount: number` 字段，但当前 AgentCore 的 `metadata` payload 不发这个键。前端会安全地 fallback 到 `0`。Phase 7 计划接通真实 MiniMax API 的 thinking-token 计数。

### 3. Sub-agent LLM 走 mock mode（除注入式之外）

`SubAgentRuntime` 默认用 `AgentCore(llm=None)` — stub 模式返回确定性文本。主 chat 链路（`agent.send_message`）的 mock mode 触发条件是 `MINIMAX_API_KEY` 为空。Phase 4 已加 `SubAgentRuntime.inject_llm(client)` 入口：测试用真 LLM 时手动注入 `MiniMaxClient` 即可。`smoke_agents` 已支持这种注入式 e2e。

### 4. 旧 README "Tasks ahead" 段

仓库原 README（Phase 1 skeleton 时代）里"Tasks ahead"等段已过时 — 当前所有 Phase 1–6 任务都已完成。本 README 是 v0.1.0 终态。完整变更见 [`CHANGELOG.md`](CHANGELOG.md)。

## License

Internal use only.
