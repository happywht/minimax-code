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
+---------------------------------------------------+
|  TopBar (workspace + git status + settings)        |
+--------+---------------------------+---------------+
| Sidebar | Main Content            | RightPanel    |
| (240px) | - ChatPanel             | (280px)       |
|         | - MessageInput          | - Progress    |
|         | - SettingsPage          | - Sub-agents  |
|         | - SkillsPanel           |               |
+---------+-------------------------+---------------+
```

## 对外接口

### IPC Client（`src/ipc/client.ts`）

前端通过 `IPCClient` 单例与 agent 通信：

- **`typedIPC`**：类型安全的高层 API（`ping`、`listSessions`、`sendMessage`、`listModels` 等）
- **Transport**：HTTP `POST /rpc`（请求/响应）+ `WebSocket /ws`（流式事件）
- **Mock 模式**：当 agent 不可达或 `VITE_AGENT_MODE=mock` 时，自动降级到内置 mock backend
- **WebSocket 重连**：指数退避（250ms -> 500ms -> 1s -> 2s，上限 5s）

### TypedIPC 方法列表

| 方法 | IPC 方法 | 功能 |
|------|----------|------|
| `ping()` | `GET /health` | 存活检测 |
| `listSessions()` | `session.list` | 会话列表 |
| `createSession()` | `session.create` | 创建会话 |
| `sendMessage()` | `agent.send_message` | 发送消息（触发流式响应） |
| `listModels()` | `model.list` | 模型列表 |
| `setCurrentModel()` | `model.set_current` | 切换模型 |
| `listSkills()` | `skill.list` | 技能列表 |
| `invokeSkill()` | `skill.invoke` | 调用技能 |
| `listJobs()` | `schedule.list` | 定时任务列表 |
| `spawnSubagent()` | `agent.spawn_subagent` | 生成子 agent |
| `gitStatus()` | `git.status` | Git 状态 |
| `gitDiff()` | `git.diff` | Git 差异 |
| `gitLog()` | `git.log` | Git 日志 |
| `getSecretStatus()` | `secrets.status` | API 密钥状态 |
| ... | ... | 完整列表见 `src/ipc/client.ts:TypedIPC` |

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

流式事件类型（`StreamEvent` 枚举）：
- `agent.message_chunk` / `agent.status` / `agent.tool_call` / `agent.tool_result`
- `permission.request` / `permission.resolved`
- `task.progress` / `agent.subagent_progress`

## 测试与质量

| 类型 | 工具 | 位置 |
|------|------|------|
| 单元测试 | vitest (jsdom) | `src/**/*.test.ts(x)` |
| IPC 测试 | vitest | `src/ipc/__tests__/client-http.test.ts` |

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

### 组件
- `src/components/Sidebar.tsx` — 左侧边栏（导航 + 会话列表）
- `src/components/ChatPanel.tsx` — 主聊天区域
- `src/components/MessageList.tsx` / `MessageItem.tsx` — 消息列表/单项
- `src/components/MessageInput.tsx` — 浮动输入框（含模型选择器、@agent 触发器）
- `src/components/TopBar.tsx` — 顶部栏（workspace + GitStatusBar + 设置）
- `src/components/GitStatusBar.tsx` — Git 状态指示器
- `src/components/RightPanel.tsx` — 右侧面板（进度 + 子 agent）
- `src/components/SubAgentPanel.tsx` / `SubAgentResultCard.tsx` — 子 agent 进度/结果
- `src/components/SkillsPanel.tsx` — 技能面板
- `src/components/SettingsPage.tsx` — 设置页（3 tab）
- `src/components/PermissionRequestModal.tsx` — 权限弹窗
- `src/components/ProgressPanel.tsx` — 任务进度面板
- `src/components/ModelSelector.tsx` — 模型选择下拉
- `src/components/WorkspaceSwitcher.tsx` — 工作区切换
- `src/components/ErrorBoundary.tsx` — 错误边界
- `src/components/index.ts` — 组件 barrel export

### Stores
- `src/stores/chat.ts` — 聊天消息流 + 发送
- `src/stores/sessionStore.ts` — 会话列表/当前会话
- `src/stores/modelStore.ts` — 模型列表/选择
- `src/stores/skillStore.ts` — 技能列表
- `src/stores/scheduleStore.ts` — 定时任务
- `src/stores/permissionStore.ts` — 权限规则
- `src/stores/taskStore.ts` — 任务进度
- `src/stores/subAgent.ts` — 子 agent 管理
- `src/stores/git.ts` — Git 状态
- `src/stores/secretStore.ts` — API 密钥
- `src/stores/index.ts` — Store barrel export

### IPC
- `src/ipc/client.ts` — IPCClient + TypedIPC + mock backend
- `src/ipc/index.ts` — barrel export
- `src/types/ipc.ts` — 所有 IPC 类型定义

### 其他
- `src/lib/time.ts` — 时间工具
- `src/lib/workspace.ts` — 工作区工具
- `src/index.css` — Tailwind 基础样式
- `index.html` — HTML 入口

## 变更记录 (Changelog)

- **2026-06-04** — 初始化 web 模块 CLAUDE.md
