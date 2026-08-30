[根目录](../CLAUDE.md) > **web**

# Web 前端模块

## 模块职责

MiniMax Code 的前端界面。基于 React 18 + Vite + TypeScript + Tailwind CSS 构建的单页应用（SPA）。通过 HTTP + WebSocket 与 Python agent 通信，使用 Zustand 管理前端状态。支持 Mock 模式在无 agent 环境下独立运行。

## 入口与启动

- **开发模式**：`pnpm dev` 或 `pnpm dev:web` → Vite dev server on `localhost:5173`
- **构建**：`pnpm build` → `tsc -b && vite build`，输出到 `dist/`
- **入口文件**：`src/main.tsx` → 渲染 `<App />`
- **App 组件**：`src/App.tsx` — 三栏布局，启动时初始化所有 stores

### 布局结构

```
+--------------------------------------------------------------------+
|  TopBar (命令面板 Ctrl+K · 预览 · Git 状态 · 通知 · 主题 · 设置)       |
+----------+------------------------------------------+--------------+
| Sidebar  | Main Content                             | RightPanel   |
| (项目分组 | - ChatPanel（消息流 + MessageInput 输入框）| 检查器 11 tab |
|  任务列表)| - SettingsPage（13 tab，覆盖层）          | 可折叠        |
|          | - SkillsPanel / CodeReview（覆盖层）       |              |
+----------+------------------------------------------+--------------+
```

组件按功能域分目录：`layout/`（骨架壳）、`chat/`（对话主区）、`settings/`（设置页 14 个 tab）、`panels/`（覆盖面板）、`modals/`（对话框）、`right-panel/`（检查器）。面向用户的文案统一来自 `src/ui/strings.ts`（中文单一来源），不直接在组件里写英文句子。

## 对外接口

### IPC Client（`src/ipc/` 四文件分层）

前端通过 `IPCClient` 单例与 agent 通信：

- **`client.ts`**：transport 层——HTTP `POST /rpc`（请求/响应）+ `WebSocket /ws`（流式事件）+ `ipc` 单例导出
- **`typed.ts`**：`TypedIPC` 类型化 API 层（`listSessions`、`sendMessage`、`listModels` 等全部 170 个 RPC 方法的签名）
- **`mock.ts`**：mock backend `mockHandle`——agent 不可达或 `VITE_AGENT_MODE=mock` 时自动降级，必须覆盖所有 IPC 方法
- **`mockData.ts`**：mock 模式的数据与状态
- **WebSocket 重连**：指数退避（250ms -> 500ms -> 1s -> 2s，上限 5s），断线重连后按 `?since=` 重放错过的广播

### TypedIPC 方法列表

完整签名见 `src/ipc/typed.ts`（服务端 170 个注册方法，命名空间总表见根目录 CLAUDE.md 与 `docs/ipc-contract.md` Appendix A）。常用入口示例：

| 方法 | IPC 方法 | 功能 |
|------|----------|------|
| `listSessions()` | `session.list` | 会话列表 |
| `sendMessage()` | `agent.send_message` | 发送消息（触发流式响应） |
| `listModels()` | `model.list` | 模型列表 |
| `invokeSkill()` | `skill.invoke` | 调用技能 |
| `spawnSubagent()` | `agent.spawn_subagent` | 生成子 agent |
| `gitStatus()` / `gitDiff()` / `gitLog()` | `git.*` | Git 三件套 |

## 关键依赖与配置

### 依赖（package.json）

| 包 | 用途 |
|---|------|
| `react` + `react-dom` | UI 框架（v18） |
| `zustand` | 状态管理（v4） |
| `lucide-react` | 图标库 |
| `react-markdown` + `remark-gfm` | Markdown 渲染 |
| `shiki` | 代码语法高亮 |
| `tailwindcss` | CSS 框架 |
| `vite` | 构建工具 |
| `vitest` | 单元测试 |
| `@testing-library/react` | 组件测试 |
| `eslint` + `@typescript-eslint` | 代码检查 |
| `playwright` | e2e 测试（根级依赖） |

### 配置文件

| 文件 | 用途 |
|------|------|
| `vite.config.ts` | Vite 配置（端口 5173、路径别名 `@/`、vitest 配置） |
| `tsconfig.json` | TypeScript 配置（strict、ES2020 target、bundler moduleResolution） |
| `tailwind.config.js` | Tailwind 自定义主题（minimax 颜色体系） |
| `.eslintrc.cjs` | ESLint 配置（TypeScript + React Hooks 规则） |
| `.env.development` | 开发环境变量（`VITE_AGENT_URL`、`VITE_AGENT_MODE`） |
| `postcss.config.js` | PostCSS 配置 |

### 路径别名

- `@/*` → `./src/*`

## 数据模型

前端不直接操作数据库。所有数据通过 IPC 从 agent 获取，类型定义在 `src/types/ipc.ts`。

核心类型：
- `Session` — 会话（id、title、archived、timestamps）
- `Message` — 消息（id、role、text、streaming、tool metadata）
- `ModelInfo` — 模型信息
- `SkillInfo` — 技能信息
- `ScheduledJob` — 定时任务
- `SubAgentProgress` / `SubAgentRun` — 子 agent 状态
- `PermissionRule` — 权限规则
- `GitStatusResult` / `GitDiffResult` / `GitLogEntry` — Git 数据

流式事件类型（`StreamEvent` 枚举，16 个，与 `agent/tests/test_ipc_contract_doc.py` 锁死同步）：
- `agent.message_chunk` / `agent.status` / `agent.tool_call` / `agent.tool_result`
- `agent.ask_user` — 模型挂起等待澄清回答（v1.1.1 问答卡）
- `permission.request` / `permission.resolved`
- `task.progress` / `agent.subagent_progress` / `agent.team_progress`
- `notification.new` / `notification.read`
- `run.created` / `run.step.started` / `run.step.completed` / `run.completed`

另有两个无 seq 的协议级帧：`agent.ready`（握手）、`agent.ping`（心跳）。

## 测试与质量

| 类型 | 工具 | 位置 |
|------|------|------|
| 单元测试 | vitest (jsdom) | `src/**/*.test.ts(x)`（组件/store/hook 就近放置，含 `src/stores/__tests__/`、`src/ipc/__tests__/`） |
| 无障碍回归 | vitest | icon-only 按钮 aria-label 静态扫描 + WCAG AA 对比度 60 断言（`src/ui/` 相关测试） |

运行命令：
```bash
pnpm test                # vitest run
pnpm --filter @minimax/web test  # 同上（从根目录）
pnpm lint                # ESLint
```

## 常见问题 (FAQ)

**Q: Mock 模式如何工作？**
A: `IPCClient` 在构造时检测 `VITE_AGENT_MODE`。启动时调 `/health` 探针，成功则走 HTTP/WS，失败则降级到内置 `mockHandle`。Mock backend 在内存中模拟所有 IPC 方法，支持前端独立开发和测试。

**Q: 如何新增一个 Zustand store？**
A: 1) 在 `src/stores/` 下创建新文件；2) 导出 `useXxxStore` hook；3) 在 `src/stores/index.ts` 中导出。

**Q: 颜色主题怎么改？**
A: 编辑 `tailwind.config.js` 中的 `minimax` 颜色定义（bg、panel、border、fg、muted、accent）。

## 相关文件清单

### 组件（`src/components/`，按功能域分目录）

- `layout/` — 骨架壳：`Sidebar.tsx`（项目分组任务列表 + 连接手机入口）、`TopBar.tsx`、`CommandPalette.tsx`（Ctrl+K）、`GitStatusBar.tsx`、`NotificationCenter.tsx`、`WorkspaceSwitcher.tsx`、`ConnectionBanner.tsx` / `StorageBanner.tsx` / `ProviderReadinessBanner`（在 chat/）等状态横幅、`ShortcutsOverlay.tsx`、`ErrorBoundary.tsx`
- `chat/` — 对话主区：`ChatPanel.tsx`、`MessageList.tsx` / `MessageItem.tsx` / `MessageInput.tsx`（@提及/附件/语音）、`MarkdownBody.tsx` / `CodeBlock.tsx` / `MermaidBlock.tsx`、`ModelSelector.tsx`、`MessageActionMenu.tsx` 等
- `settings/` — 设置页：`SettingsPage.tsx`（**13 tab 分 4 组**：核心 models/providers/permissions/mcp-servers/memory/plugins/data、自动化 scheduled/workflows/webhooks、Agent agents/teams、治理 audit）+ 每 tab 一个组件（`ModelsTab.tsx`、`ProvidersTab.tsx`、`DataTab.tsx` 等）
- `panels/` — 覆盖面板：`SkillsPanel.tsx`、`CodeReviewPanel.tsx`、`PatchPreviewPanel.tsx`（+ `PatchFileCard` / `PatchHunkCard`）、`PreviewPanel.tsx`
- `modals/` — 对话框：`PermissionRequestModal.tsx`（允许/拒绝 + 总是允许开关）、`MobilePairingModal.tsx`、`GitViewerModal.tsx`、`CrashRecoveryPrompt.tsx`、`ConfirmationDialog.tsx`
- `right-panel/` — 检查器：`tabs.tsx` 注册 **11 个 tab**（timeline/diff/progress/checkpoints/agents/subagents/review/teamruns/terminal/runner/codebase），`ProgressPanel.tsx`、`SubAgentPanel.tsx`、`TeamRunPanel.tsx`、`TerminalPanel.tsx`、`RunnerPanel.tsx`、`CodebasePanel.tsx`、`CheckpointPanel.tsx`、`RunTimelinePanel.tsx` 等
- `RightPanel.tsx` / `StructuredErrorCallout.tsx` / `index.ts` — 面板壳 + barrel export

### Stores（`src/stores/`，30 个）

- 对话域：`chat.ts`（消息流 + 发送）、`subAgent.ts`、`taskStore.ts`
- 会话域：`sessionStore.ts`
- 模型域：`modelStore.ts`、`providerStore.ts`、`secretStore.ts`
- Git/补丁域：`git.ts`、`patchPreviewStore.ts`、`codeReviewStore.ts`
- 检查器域：`runStore.ts`、`runnerStore.ts`、`teamRunStore.ts`、`teamStore.ts`、`terminalStore.ts`、`codebaseStore.ts`、`agentStore.ts`、`checkpoint.ts`
- 自动化域：`scheduleStore.ts`、`workflowStore.ts`、`webhookStore.ts`
- 系统域：`permissionStore.ts`、`auditStore.ts`、`memoryStore.ts`、`notificationStore.ts`、`mobileStore.ts`、`crashRecoveryStore.ts`、`previewStore.ts`、`skillStore.ts`、`themeStore.ts`
- `index.ts` — Store barrel export

### IPC
- `src/ipc/client.ts` — transport + `ipc` 单例
- `src/ipc/typed.ts` — TypedIPC 类型化 API 层
- `src/ipc/mock.ts` / `src/ipc/mockData.ts` — mock backend 与数据
- `src/ipc/index.ts` — barrel export
- `src/types/ipc.ts` — 所有 IPC 类型定义（`StreamEvent` 枚举权威来源）

### 其他
- `src/ui/` — `strings.ts`（中文文案单一来源，layout/chat/settings/panels/modals/right-panel 六域）+ 基础组件库（`Button.tsx`、`IconButton.tsx`、`Modal.tsx`、`Input.tsx`、`Badge.tsx` 等）
- `src/lib/` — 工具函数与组合式 hooks（`time.ts`、`workspace.ts`、`useClickOutside.ts`、`useMessageWindow.ts` 消息窗口虚拟化等）
- `src/index.css` — Tailwind 基础样式
- `index.html` — HTML 入口

## 变更记录 (Changelog)

- **2026-08-30** — v1.7.0（backlog B4：移动端适配收口，web 侧）——① **实测修复**：TopBar Inspector 按钮补默认 `hidden`（原 `md:inline-flex lg:hidden` 在 &lt;768px 两断点类都不激活，按钮误现于手机）；② **新 e2e spec** `e2e/smoke-mobile-viewport.spec.ts`（第 11 个）：375×667 手机档（hamburger/Sidebar 抽屉开合/Inspector 按钮隐藏/无横向溢出/composer 可用）+ 820×1180 平板档（hamburger 隐藏/InspectorDrawer 开合/无溢出）；③ **jsdom** `tests/app-mobile.test.tsx`（3 用例：App 级抽屉交互语义）；④ 溢出审计结论：双档零溢出，骨架（viewport meta、&lt;768 抽屉、md–lg InspectorDrawer、max-w 弹性、overflow-auto）健康零修复需求。vitest 782（+3）/ mobile e2e 6/6 / 桌面 boot 5/5
- **2026-08-30** — v1.7.0（backlog B3：进度面板重设计，web 侧）——ProgressPanel 六项升级：① `TaskProgressEntry` 扩展 `title`/`session_id`/`created_at`（`taskStore.entryFromRow` 取 ledger 行；live 事件 spread-prev 保留）；② 卡片标识 title 主行、mono task_id 降次行；③ `partitionTasks()` 分「运行中」/「最近完成」两小节（各按 updated_at 降序）；④ 状态徽章中文化（`strings.ts statusLabel` 5 态映射）；⑤ 相对时间 + 终态耗时（新 `lib/time.ts formatDuration`：<1h `MM:SS`、≥1h `X 小时 Y 分`）；⑥ result/error 详情 `line-clamp-2` 可展开 + 进度条百分数。testId 契约保持（`pp`/`task-row-<id>`/`task-status-<status>`），列表拆 `-task-list-live`/`-task-list-settled`。`tests/progress-panel.test.tsx` 14 用例（6 更新 + 8 新增），vitest 779 全绿
- **2026-08-30** — v1.7.0（迭代优化计划 R3：前端体验打磨，web 侧）——① **right-panel 硬编码文案收敛**（切片 A）：5 组件 10 处硬编码中文迁入 `strings.ts` rightPanel 域既有分组（AgentTeamList/ProgressPanel/CodebasePanel/SubAgentPanel/CheckpointPanel），文案原文不变仅搬家；SubAgentPanel @general 召唤文案拆 idleHintPre/idleHintPost 两 key（内嵌 font-mono span）；② **TeamRunPanel 沙盒可视化**（切片 B）：RunCard 消费 `result.sandbox`/`sandbox_summary`——沙箱徽章 + 「沙盒已回收 N 个运行 · 合并落盘 M 个文件」统计行 + skipped/errors 黄色 AlertTriangle 警示行（同 conflicts 视觉级），5 个新 key 入 teamRuns 组；`tests/team-run-panel.test.tsx` +3 用例，vitest 771 全绿
- **2026-08-29** — v1.7.0（迭代优化计划 R1：演进缺口收尾，web 侧）——① **checkpoint 前端状态 store 化**：新 store `checkpoint.ts`（`useCheckpointStore`，stores 29→30，检查器域）——列表 per-session 缓存 / diff per-id 缓存 / 展开态、加载态、错误态上移；`CheckpointPanel.tsx` 改薄组件（表单草稿留 useState），RightPanel tab 切换不再丢状态或重复请求，create/delete 后 `invalidate` 强刷 + 陈旧 diff 剪除；② **SubAgentPanel 重启回填**：`subAgent.ts` 新增 `hydrate()`（`init()` 订阅后调 `run.list {mode:"subagent"}` 从 `agent_runs` 回填；终态直映、非终态→`"started"`、ISO→epoch、title 去前缀、`metadata.result_text`/`parent_session_id` 回填；live 事件优先不覆盖、fail-open）；③ **契约对齐**：`AgentRun.mode` 补 `"subagent"`（migration 028 漂移修正）、`typed.ts` `listRuns` 补 `mode`、`mock.ts` `run.list` 补 mode 过滤；新增 `stores/__tests__/checkpoint-store.test.ts`（9）+ `stores/__tests__/subagent-hydrate.test.ts`（8），vitest 768 全绿
- **2026-08-24** — v1.3.0：Per-Project Workspace Root 专项（web 侧）——`Project` 类型加 `root_path: string`；`typed.ts` `createProject`/`updateProject` 加 `root_path?`，`gitStatus`/`gitDiff`/`gitLog`、codebase 4 方法、patch 3 方法、`createWorktreeSession` 加可选 `project_id`（`mock.ts`/`mockData.ts` 双面契约同步）；`sessionStore.createProject(name, desc?, rootPath?)`、`createWorktree` wire 传 `project_id: currentProjectId ?? "inbox"`（去硬编码）；`git.ts`/`codebaseStore.ts`/`patchPreviewStore.ts` action 内自动注入 `useSessionStore.getState().currentProjectId`（未选项目不带键 = 后端 legacy 行为，组件层零改动）；Sidebar 新建项目 Modal 加「根目录」可选 Input（绝对路径 placeholder + 必须存在的提示）；`WorkspaceSwitcher` 项目条目 tooltip 显示 root_path；`strings.ts` layout 域 +5 文案；新增 `stores/__tests__/project-root-scope.test.ts`（8 测试，importOriginal 单点 mock `ipc/client` + `bindTypedIPC(spyClient)` 一箭双雕锁 typed wire 契约与 store 注入）+ workspace-switcher tooltip 断言，web 侧 +9 测试（vitest 756）
- **2026-08-23** — v1.2.2：多 Agent 协作与系统稳定性专项（web 侧）——WS 客户端 seq 纪元对齐：`agent.ready` 帧读 `next_seq` 锚点，`next_seq <= wsLastSeq` 时重置水位并主动 `close(1000, "seq-epoch-reset")` 重连获全量重放（agent 重启后新纪元历史此前永久无法重放）；WorkspaceSwitcher 重写接真实 `workspace.*` IPC、删除死代码 `lib/workspace.ts`、文案入 `strings.ts`；`ipc-client.test.ts` +6 纪元重置测试
- **2026-08-23** — v1.2.0：全局审计修复（web 侧）——chat 五订阅 `isCurrentSessionEvent` 守卫 + `disposeChatSubscriptions()` 导出、teamRunStore 读 `env.data`、invokeSkill 对象 args 平铺到 wire 顶层 + codeReviewStore 读平铺 reply + mock 同步、runStore 双守卫（run.created 过滤 / run.completed 孤儿丢弃）+ loadRuns stale/replace、ProgressPanel 展示 done 任务 result；新增 `session-guards.test.ts`（9）+ `typed-skills.test.ts`（5）
- **2026-08-23** — v1.1.3：`ContextIndicator` 语义修正——取最后一条 assistant 消息的 `tokens_in + tokens_out`（当前上下文真实占用）替代累加（历史重复计数、系统性虚高）
- **2026-08-23** — v1.1.1：新增 `AskUserCard` 问答卡（chat 域）与 `agent.ask_user` 事件（StreamEvent 16 个）、`agent.answer_user` IPC；IPC 方法 170
- **2026-08-21** — R43 对账同步：组件清单子目录化、设置页 3 tab→14 tab 4 组、检查器 11 tab、stores 10→29、IPC 分层 4 文件、StreamEvent 8→15、新增 strings.ts/hooks 条目
- **2026-06-04** — 初始化 web 模块 CLAUDE.md
