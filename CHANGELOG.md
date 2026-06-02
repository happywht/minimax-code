# Changelog

All notable changes to MiniMax Code are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.2]: #012---2026-06-03
[0.1.1]: #011---2026-06-03
[0.1.0]: #010---2026-06-02
