# Changelog

All notable changes to MiniMax Code are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[0.1.0]: #010---2026-06-02
