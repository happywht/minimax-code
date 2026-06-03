# Changelog

All notable changes to MiniMax Code are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.3.0] - 2026-06-03

**Four feature tracks land together.** v0.2.0 把项目从 Tauri 桌面壳切到
web SPA + 本地 Python agent，但 chat 流里只能看到 LLM 的最终答案 —— 看
不到"它想了多少次 / 它调了哪些子 agent / 它看过的代码改动"这些过程信息。
v0.3.0 起把这四块补齐，让一次 chat 真的能"看进去"。

### Added
- **`thinking_count` 通道**（agent: `core.py` / `llm.py` / `builtins.py` /
  `server.py`）：`agent.message_chunk` 事件在每个 turn 收尾的那条 chunk 上
  携带 `data.metadata = {thinking_count, tokens_in, tokens_out}`。Mock
  模式每调一次 LLM 自增 1，真实模式从 upstream `usage.thinking_tokens` 读。
  Web 端 `MessageItem` 把这个数渲染成 "思考 N 次" 摘要行。详细见
  [`docs/v0.3.0-design.md`](docs/v0.3.0-design.md) §1。
- **Sub-Agent UI**（agent: `handlers_agents.py` + web: `SubAgentPanel` /
  `SubAgentResultCard` / `subAgent.ts`）：chat 输入框新增 `@agent` 触发器
  下拉，敲 Enter / Tab 选中 sub-agent 后 prime marker 并触发
  `agent.spawn_subagent`。后端 handler 跑
  `SubAgentRuntime.invoke` 时按 `started → thinking → (tool_call →
  tool_result)? → completed` 节奏推 `agent.subagent_progress` 事件流；失败
  时最后一个 `failed` 事件先于 error reply 发出，UI 不会卡在"运行中"。
  Right rail 新增 "Sub-agents" 段，inflight 进度条 + 完成后折叠卡片
  回填到主消息流。
- **Code Review**（agent: `code_review._review_diff` + `handlers_skills` 短
  路径 + web: `CodeReviewPanel` / `DiffView`）：`skill.invoke` 收到
  `params.diff` 时直接走 `_review_diff(diff)` 而非 LLM 工具循环。Mock
  analyser 走 unified diff 的 hunks，给每文件最多 3 条 `severity=info`
  评论（行长 >120 字符的升 `warning`）。`git.diff` 拿到的真实 diff 直接
  喂进 skill，UI 在 CodeReviewPanel 里渲染 file:line 锚定评论。
- **Git 集成**（agent: `handlers_git.py` + web: `GitStatusBar` /
  `TopBar` / `git.ts`）：新增 `git.status` / `git.diff` / `git.log` 三个
  JSON-RPC handler。Read-only（不替用户 commit / push），每次调用
  `subprocess.run` 跑 5s timeout。`status` 解析 `--porcelain=v2 -z` 把
  paths 分到 `modified` / `untracked` / `staged` 三桶。Top bar 替换
  旧 `WorkspaceSwitcher` 为 `TopBar`，渲染分支 + 干净/脏指示灯 +
  popover 列出改动的文件路径。E2E `smoke-boot` 加一条
  `[data-testid=git-status-bar]` 存在性断言。
- **设计文档** [`docs/v0.3.0-design.md`](docs/v0.3.0-design.md)：四块
  feature 的 IPC 契约 / store API / 组件边界 / sequencing 的 source of
  truth，所有 v0.3.0 worker 开工前先读。

### Test coverage added
- `agent/tests/test_thinking_count.py` — 6 cases
  （LLM counter / _stream_turn 注入 / Context.emit metadata / IPC round-trip）
- `agent/tests/test_subagent_spawn.py` — 4 cases
  （happy path / 缺 name / 未知 agent / runtime 失败 → failed 事件）
- `agent/tests/test_git_handlers.py` — 18 cases
  （status 3 buckets / diff 4 个 scope / log 字段 / not-a-repo / review-diff）
- `web/tests/chat-thinking-count.test.ts` — 4 cases
  （捕获 / 渲染 / 缺失时降级）
- `web/tests/subagent-store.test.ts` + `subagent-panel.test.tsx`
  （store 状态机 / panel 渲染）
- `web/tests/git-store.test.ts` + `git-status-bar.test.tsx`
  （store API / 组件渲染）
- `e2e/smoke-subagent.spec.ts` + `e2e/smoke-thinking-count.spec.ts`
  （端到端真浏览器跑通）
- `e2e/smoke-boot.spec.ts` 扩展 git-status-bar 断言

### Known limitations
- **`_review_diff` 是 mock-mode analyser** —— 当前实现是确定性 diff walker，
  不调真 LLM。wire shape 稳定所以可以无破坏地升级到 LLM 路径，留作 v0.3.1。
- **`git.*` 是 read-only** —— 不暴露 `git add` / `commit` / `push`，
  这些仍走用户自己 shell。

## [0.2.0] - 2026-06-03

**Drop Tauri, go full web.** v0.1.x 阶段项目以 Tauri 2.x 桌面壳为承载
（NSIS / MSI 双包，启动期踩过 `app.manage()` race / sidecar 路径 /
`tokio pipe flush` 三个 bug，详见 v0.1.1 / v0.1.2 / v0.1.3 三段 hotfix）。
v0.2.0 起切换为 **web SPA + 本地 Python agent** 形态：

- **为什么删 Tauri**：v0.1.x 的桌面壳没有真正的安装必要性 —— 项目本来就是
  内部工具（只 LAN 内几个人用），Tauri 带来的"轻量 webview 桌面壳"价值
  不如它引入的"必须出 MSI / NSIS / 必须装 Rust 工具链 / 启动 race 难调"
  成本。直接打开浏览器跑 `localhost:5173` 更省事，热重载和调试都更标准。
- **保留什么**：v0.1.x 阶段实现的 Phase 1–6 全部功能（多轮对话 / 技能
  系统 / 定时任务 / 多 Agent / 移动配对 / 授权管理 / 进度面板 / 端到端
  chat / 模型选择 / 子 Agent 真 LLM / 设置页 / 权限真弹窗 / 密钥 keyring
  / 前端三栏布局）一字不动地进入 v0.2.0。Python agent 核心 / SQLite 存储
  / 7 个 e2e smoke / IPC handler registry 全部保留。
- **改了 transport**：从前端通过 Tauri `invoke` 调 Rust 再 stdio 转 Python
  sidecar，改成前端直接 `fetch POST /rpc` + `WebSocket /ws` 打到 Python
  agent 的 FastAPI 薄 transport。两套 transport **共享同一份 handler
  registry**（`IPCServer.handle_request` + `IPCServer.register_listener`），
  不是平行两份实现。Stdio 模式保留给 tests + CLI（`--stdio` flag）。

### Added
- **Agent HTTP + WebSocket transport**（`agent/minimax_code/ipc/http_server.py`）：
  FastAPI 薄 transport，bind `127.0.0.1:8765`，暴露 `POST /rpc` /
  `GET /ws` / `GET /health`（SSE fallback `GET /events`）。CORS allow-list
  仅 `http://localhost:5173`（Vite dev）。复用 `IPCServer` 的 handler
  registry —— POST 收到 envelope 后直接 `await server.handle_request(env)`，
  流式事件经 `register_listener(cb)` 推到所有 WebSocket 客户端。
- **Web IPC client 重写**（`web/src/ipc/client.ts`）：`IPCClient.request` /
  `IPCClient.on` / `IPCClient.ping` 内部切到 `fetch` + `WebSocket`，
  公开 `TypedIPC` 形状（`ping` / `listSessions` / `createSession` /
  `sendMessage` / `listModels` 等）零变化，React stores 一行未动。WS
  reconnect 走指数退避（250ms → 500ms → 1s → 2s, cap 5s）。`useMock`
  fallback 走 `/health` 200ms 探针 + 既有 in-process `mockHandle`。
- **Playwright 跨栈 e2e**（`tests/e2e-web/` + `pnpm test:e2e`）：接替
  v0.1.x "Tauri 桌面端 e2e 未在已安装包上跑" 这个 known limitation。真
  浏览器（Chromium）跑 web 端，真起 agent（HTTP 模式），完整覆盖会话
  创建 → 消息发送 → 流式响应 → 状态更新链路。
- **设计文档** [`docs/v0.2.0-web-architecture.md`](docs/v0.2.0-web-architecture.md)：
  v0.2.0 切换的 API 契约（端点表 / 错误码 / CORS / 环境变量 / dev workflow
  / 删什么 / 留什么）的 source of truth，所有平行 worker（HTTP server /
  web client / e2e / docs）开工前先读。
- **`dev.mjs` 双模式**：`pnpm dev` 默认通过 `concurrently` 同时拉起
  agent + Vite；`AGENT_SKIP=1 pnpm dev` 只起 Vite（agent 单独跑）。

### Removed
- **Tauri 桌面壳**（`src-tauri/`）：整个目录已删 —— Rust 源码、
  `Cargo.toml`、`tauri.conf.json`、icons、`target/`、`tauri.conf.json`
  配置、`build.rs`、`src/main.rs` / `ipc.rs` / `commands.rs`。v0.1.0 →
  v0.1.3 的 4 个 NSIS / MSI 安装包随 `src-tauri/` 一起从源码树消失；
  历史 artifact 留在 git 历史（如需可 `git show v0.1.3:src-tauri/...` 找回）。
- **`@tauri-apps/api` / `@tauri-apps/cli`** 依赖：从根 `package.json` 和
  `web/package.json` 删除。
- **`tauri:dev` / `tauri:build` / `tauri`** npm scripts。
- **Tauri Rust 工具链**作为前置条件（Visual Studio Build Tools / WiX
  3.x / `cargo install tauri-cli@^2`）—— 普通用户 clone 仓库 + 两终端
  命令即可。
- **Known limitation #0**（"v0.1.0 / v0.1.1 / v0.1.2 启动 race"）和
  **Known limitation #1**（"Tauri 端到端 e2e smoke 未在已安装包上跑"）
  —— 前者随 Tauri 删除一并消失；后者被 Playwright 跨栈 e2e 接替（见
  Added）。

### Changed
- **Dev workflow 从 3 终端简化为 2 终端**：
  - 终端 1（agent）：`cd agent && uv run python -m minimax_code`
    → `agent server listening on http://127.0.0.1:8765`
  - 终端 2（web）：`pnpm dev` → `vite ready`
  - 浏览器开 `http://localhost:5173`
  - 原来的终端 3（`cd src-tauri && cargo tauri dev`）整个消失。
- **IPC 契约**（`docs/ipc-contract.md`）：Transport 段重写为双模式
  （stdio 给 tests + CLI；HTTP + WebSocket 给 web client）；端点表
  加 `GET /health` / `POST /rpc` / `GET /ws` / `GET /events`；lifecycle
  段从 "Tauri-level `ipc:sidecar` 事件" 改为 "WebSocket `agent.ready` +
  `/health` 探针 + stdio EOF"；test surface 表加 Playwright 行；version
  对齐从"Tauri `Cargo.toml` + agent `__init__.py` 两处"改为
  "root `package.json` + `web/package.json` + agent `__init__.py` 三处"。
- **架构文档**（`docs/architecture.md`）：技术栈表删 Tauri 行，加
  "Transport: HTTP + WebSocket (FastAPI on agent)" 行；架构图把
  "Tauri Window (WebView)" 换成 "Browser (Chromium / Firefox / Edge) +
  Vite-served React SPA"；目录树删 `src-tauri/`；关键技术决策加
  "打包 = 无（内部 web 工具）"；Phase 划分加 "v0.2.0 切换（删 Tauri）"
  段。
- **README**：头部 banner 改为"v0.2.0 内部版 — 全面 web 化，丢掉 Tauri
  桌面打包"；"安装"段换成"快速启动（dev mode — 两终端）"；"前置环境"
  段去掉 Rust / Tauri CLI / Visual Studio / WiX；"测试"段加
  `pnpm test:e2e`（Playwright）；"出包"段改为"内部无打包流程；要分发
  就 git clone + 两终端"；Known limitations 段重排版（剩 3 条：
  `thinking_count` / sub-agent LLM mock / 旧 README 收尾）。

### Notes
- **不动 git tag**：`v0.1.0` / `v0.1.1` / `v0.1.2` / `v0.1.3` 四个 tag
  保留在 git 历史作为"v0.1.x Tauri 时代的归档"。本 changelog 的 v0.1.x
  段也是历史记录。**`v0.2.0` tag 由 owner 决定何时打，team 不会写**。
- **不动 Python 代码 / web 代码**：本轮 docs 切换只改 4 个文件
  （README / architecture / ipc-contract / 本文件）。其他 4 个 worker
  分头实现 HTTP server / web client / Playwright e2e / 删 `src-tauri/`。
- **CORS / auth**：v0.2.0 不做多用户 / 远程；单用户、本机、`127.0.0.1`
  only，无 token 鉴权。需要时再加。

## [0.1.3] - 2026-06-03

**v0.1.2 hotfix.** Fixes the `tokio::process::ChildStdin::flush()` race
that broke first-launch IPC in v0.1.2. **Internal users who installed
v0.1.2 must reinstall v0.1.3** — the first batch of IPC calls
(session.list, model.list, agent.list, permission.list) failed on
mount with `failed to flush agent stdin: 管道正在被关闭 (os error 232)`.
UI rendered + chat.send_message worked later, but the data-loading
toasts persisted and the agent list stayed on "Loading agents...".

### Fixed
- **`send_to_agent` removed `stdin.flush().await`.** `tokio::process::ChildStdin`
  wraps a raw OS pipe with no userspace buffer; `write_all` is sufficient.
  The `flush` call triggered an internal sync that saw the child end
  as "closing" during the brief window between sidecar process spawn
  and the asyncio IPC server reaching "waiting for messages" state
  (~50 ms). Bytes had already been written to the kernel pipe buffer
  but `flush` returned `ERROR_NO_DATA` (os error 232, "pipe is being
  closed"). The protocol framing (`\n` terminator) and the OS pipe
  buffer (≥ 4 KB) handle any transient backpressure.

### Changed
- `tauri.conf.json` + `src-tauri/Cargo.toml` version bumped `0.1.2`
  → `0.1.3`.
- README install section updated with v0.1.3 SHA-256 + file sizes.
- "Known limitations" expanded to cover the v0.1.0 / v0.1.1 / v0.1.2
  chain (manage race → sidecar path → pipe flush race), with v0.1.3
  fixing all three.

### Artifacts
- MSI: `MiniMax Code_0.1.3_x64_en-US.msi` (3.93 MB / 4,124,672 B)
  — SHA-256 `74FA6F5983D13EB129F8187D53C86C18DC66973D4CF6F4DA4F48684BED766F84`
- NSIS: `MiniMax Code_0.1.3_x64-setup.exe` (3.18 MB / 3,331,122 B)
  — SHA-256 `088D6D0DCE5393787C5901F64D4445B038BC500B5E142B1E26E773CB1F92B32A`

## [0.1.2] - 2026-06-03

**v0.1.1 hotfix.** Fixes the sidecar path resolution that made v0.1.1
white-screen and exit on launch. **Internal users who installed v0.1.0
or v0.1.1 must reinstall v0.1.2** — both earlier builds are unusable
in production.

### Fixed
- **Sidecar path resolution in `ipc::sidecar_command`.** Tauri 2.x
  `externalBin` configuration places the bundled sidecar at the
  resource root (install dir on Windows), named WITHOUT the
  target-triple suffix. The previous code looked for
  `binaries/minimax-code-agent.exe` (with `binaries/` prefix), which
  does not exist in the bundle. In production, `app.path().resolve(...)`
  failed, and the code fell through to the dev-mode Python fallback —
  but production users don't have `python` in PATH and the agent
  module isn't installed. `init_agent_bridge` returned `Err`,
  `setup()` returned `Err`, Tauri refused to start the app, and the
  user saw a white window that exited immediately. Fix: resolve
  `minimax-code-agent.exe` (no prefix) at `BaseDirectory::Resource`,
  which matches Tauri 2.x's `externalBin` bundling convention.

### Changed
- `tauri.conf.json` + `src-tauri/Cargo.toml` version bumped `0.1.1`
  → `0.1.2`.
- README install section updated with v0.1.2 SHA-256 + file sizes.
- "Known limitations" expanded to cover both the v0.1.0 manage() race
  and the v0.1.1 sidecar path bug, with a single combined
  "v0.1.0 / v0.1.1 launch crash" section explaining how v0.1.2 fixes
  both.

### Artifacts
- MSI: `MiniMax Code_0.1.2_x64_en-US.msi` (3.93 MB / 4,124,672 B)
  — SHA-256 `1DAA16151C6BF5F36180728F59ED0BD467C131A93E489D74D52D9A45FC10E32E`
- NSIS: `MiniMax Code_0.1.2_x64-setup.exe` (3.18 MB / 3,333,019 B)
  — SHA-256 `2DD04D11B24AD7D58A7B989F3D6634E3D49587AA351B7253020DCE7C54216C2D`

## [0.1.1] - 2026-06-03

**v0.1.0 hotfix.** Fixes the Tauri 2.x `app.manage()` race that broke
first-launch IPC in v0.1.0. **Internal users who installed v0.1.0 must
reinstall v0.1.1** — the v0.1.0 build is unusable out of the box.

### Fixed
- **Tauri `app.manage()` race on first launch.** `src-tauri/src/lib.rs`
  `setup()` originally called `ipc::spawn_agent_sidecar(handle)`,
  which fire-and-forget spawned a background task that eventually
  called `app.manage(AppState { ... })`. The webview mounted before
  the task reached the `manage` line, so the React app's first batch
  of IPC calls (`session.list`, `agent.list`, `model.list`,
  `permission.list`, etc.) all failed with `state not managed for
  field 'state' on command 'ipc_request'`. Fix: replaced
  `spawn_agent_sidecar` + `run_bridge` with `init_agent_bridge` —
  synchronously spawns the child, calls `app.manage(...)` in the
  setup closure, then spawns a long-lived `pump_stdio` task. The
  state is registered before the setup closure returns.
- Removed unused `tauri::Manager` import in `lib.rs` (post-fix
  cleanup).
- Removed unused `std::sync::Mutex` import in `ipc.rs` (pre-existing
  dead import, cleaned up alongside the refactor).

### Changed
- `tauri.conf.json` + `src-tauri/Cargo.toml` version bumped `0.1.0`
  → `0.1.1`.
- README install section updated with v0.1.1 SHA-256 + file sizes.
- Tauri release e2e "known limitation" softened: v0.1.1 is now
  manually verified on installed package (the bug above was the
  only thing blocking the installed-package smoke); Playwright /
  WebDriver automation still v0.1.2 scope.

### Artifacts
- MSI: `MiniMax Code_0.1.1_x64_en-US.msi` (3.93 MB / 4,124,672 B)
  — SHA-256 `F8C8840AB8EB3858E56F40728F38F4431E08224349C0E50798072870DE7562EB`
- NSIS: `MiniMax Code_0.1.1_x64-setup.exe` (3.18 MB / 3,332,302 B)
  — SHA-256 `DC544C02ACA9C955A869C325D45247FFE27F5B7DD22855F939FBBD088F525F7B`

## [0.1.0] - 2026-06-02

**First internal release.** Full Phase 1 → Phase 6 implementation. Windows
NSIS + MSI installers built and SHA-256 verified. Tauri 2.x desktop shell +
React 18 frontend + Python asyncio agent core + SQLite storage + 8 DAOs + 7
IPC namespaces + 7 e2e smokes (all green).

### Added

#### Storage & data layer (Phase 1)
- 8 SQLite tables: `sessions`, `messages`, `tasks`, `skills`, `scheduled_jobs`,
  `permission_rules`, `mobile_devices`, `agents`
- Migration runner + initial migration `001_initial.py`
- DAOs: `SessionDAO`, `MessageDAO`, `TaskDAO`, `SkillDAO`,
  `ScheduledJobDAO`, `PermissionRuleDAO`, `MobileDeviceDAO`, `AgentConfigDAO`
- Singleton-DAO pattern with sync/async dual-path support

#### Skills system (Phase 1)
- `SKILL.md` loader + registry + runtime
- 3 builtin skills
- `skill.list / skill.invoke / skill.refresh` IPC handlers
- 27 unit tests for skills

#### Agent core (Phase 1)
- `MiniMaxClient` (httpx async) with mock-mode fallback
- Conversation loop with tool-call dispatch
- 6 tools: `file_ops`, `terminal`, `edit`, `search`, `skill`, `meta`
- JSON-RPC 2.0 over stdio IPC server
- `agent.send_message / agent.cancel / agent.history` IPC handlers

#### Auth & permissions (Phase 2a)
- `PermissionStore` with consent flow
- `permission.list / permission.request / permission.set / permission.delete`
  IPC handlers
- Cache + restart-persistence

#### Scheduler (Phase 2a)
- APScheduler integration with persistence
- `schedule.list / schedule.create / schedule.delete / schedule.toggle`
  IPC handlers

#### Progress tracking (Phase 2a)
- `TaskDAO` + `ProgressTracker` (3-layer: track → step → task)
- `task.list / task.get / task.subscribe` IPC handlers
- Streaming progress events over IPC

#### Sessions & history (Phase 2b)
- `SessionDAO` with `list / count / create / get / archive / search`
- Filter-aware `count()` so `list.total` matches the page
- Explicit `session.create` IPC handler
- 17-step Phase 2b integration e2e smoke

#### Mobile pairing (Phase 2b)
- Pairing code generation + device registry
- Long-lived auth tokens
- `mobile.pair / mobile.list_devices / mobile.revoke` IPC handlers
- Mobile handler uses singleton DAO

#### Multi-agent (Phase 2b)
- `SubAgentRuntime` with config DAO
- Sub-agent `invoke` IPC handler + streaming events
- Race-safe `upsert` with tests
- Real-LLM injection support: `inject_llm(MiniMaxClient)`

#### End-to-end chat (Phase 3)
- `agent.send_message` real wire-up to `AgentCore`
- Mock LLM mode triggered by empty `MINIMAX_API_KEY`
- Message persistence
- `smoke_chat` e2e test (subprocess-driven)

#### Model selection (Phase 4)
- `ModelPrefsDAO` + migration `002_model_prefs.py`
- 3 candidate models hardcoded in handler (PoC phase)
- `model.list / model.get_current / model.set_current` IPC handlers
- 16 unit tests

#### Sub-agent LLM (Phase 4)
- `SubAgentRuntime` accepts injected `MiniMaxClient`
- `smoke_agents` accepts sub-agent's real-LLM mock response
- Tauri app icon assets (SVG + generated PNG/ICO)

#### Settings page (Phase 5)
- Settings page with 3 tabs: Model / Permission / Schedule
- API Key tab with keyring round-trip
- Tool-call consent modal end-to-end (real-time permission flow)
- `permissionStore.upsertRule` return-shape fix

#### OS keyring (Phase 5)
- `secrets` module: OS keyring with env-var fallback
- `handlers_secrets` IPC + 10 unit tests
- Frontend `secretStore` Zustand store

#### UI panels & layout (Phase 6)
- Skills panel in sidebar
- Redesigned task history sidebar
- Three-pane layout: chat | progress | agent team
- Workspace switcher in top bar
- Per-turn summary in chat
- Always-allow inline button in tool-call consent
- Model picker inline in input
- Floating chat input

#### Tauri release build (Phase 6)
- `cargo tauri build` produces both NSIS and MSI installers
- Windows MSI: `MiniMax Code_0.1.0_x64_en-US.msi` (3.86 MB)
- Windows NSIS: `MiniMax Code_0.1.0_x64-setup.exe` (3.18 MB)
- SHA-256 verified for both artifacts

### Changed
- README rewritten for end-of-Phase-3 (and again for v0.1.0)
- Project layout documented for multi-DAO + multi-IPC-namespace scale
- `count()` semantics now consistent with `list()` filter args (Phase 2b fix)

### Fixed
- `count()` honors search filter (Phase 2b) — `list.total` is now filter-aware
- `upsert` race condition in `AgentConfigDAO` (Phase 2b)
- Singleton-DAO enforcement in mobile handler (Phase 2b)
- `permissionStore.upsertRule` return shape regression (Phase 5)
- Tauri build linker issue resolved by switching to a Windows host with
  MSVC Build Tools (was a previous Phase 3 environmental blocker)

### Known limitations
- **Tauri release e2e not run on installed package.** All 7 black-box
  subprocess smokes pass against the dev agent, but `MiniMax Code_0.1.0_x64-setup.exe`
  was not installed + Playwright-automated on a clean machine. PoC decision:
  defer release-verification to first install user / v0.2.
- **`thinking_count` metadata field not implemented.** Web-side
  `MessageMetadata.thinkingCount` is reserved in the type but the agent does
  not emit it; UI falls back to 0. Phase 7 will wire real MiniMax thinking-token
  counts.
- **Sub-agent default is mock mode.** `SubAgentRuntime` default is
  `AgentCore(llm=None)`. Tests and `smoke_agents` use `inject_llm()` to
  supply a real `MiniMaxClient`. Production deployment will set the default
  to use the user-configured API key.
- **No remote / no auto-update.** This is an internal v0.1.0; no GitHub
  release artifacts, no Tauri updater wired. v0.2 plan: add GitHub Releases
  + Tauri auto-update.

[0.2.0]: #020---2026-06-03
[0.1.3]: #013---2026-06-03
[0.1.2]: #012---2026-06-03
[0.1.1]: #011---2026-06-03
[0.1.0]: #010---2026-06-02
