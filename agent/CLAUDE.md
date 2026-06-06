[根目录](../CLAUDE.md) > **agent**

# Agent 模块

## 模块职责

Python agent 是 MiniMax Code 的后端核心。它是一个 asyncio 进程，对外暴露 JSON-RPC 2.0 接口（HTTP + WebSocket 或 stdio），对内管理 LLM 对话循环、SQLite 存储、技能系统、定时任务、多 Agent 协作、权限管理、Git 集成等全部后端逻辑。

## 入口与启动

- **HTTP 模式（默认）**：`python -m minimax_code` — 启动 FastAPI server，绑定 `127.0.0.1:8765`
- **stdio 模式**：`python -m minimax_code --stdio` — 行分隔 JSON-RPC over stdin/stdout（供测试和 CLI 调试）
- **入口函数**：`minimax_code/__main__.py:cli_entry()` → `amain()` → `IPCServer` + `build_app()`
- **Handler 注册**：`minimax_code/app.py:register_app_handlers(server)` 注册所有 IPC 命名空间

启动流程：
1. 解析命令行参数（`--http` / `--stdio` / `--http-port`）
2. 加载配置（`Config.from_env()`）
3. 初始化运行时单例（`init_runtime()`）：Storage、ProgressTracker、SessionsDAO、SubAgentLLM
4. 创建 `IPCServer` 并注册所有 handlers
5. HTTP 模式下启动 uvicorn；stdio 模式下运行 `run_forever()`

## 对外接口

### HTTP + WebSocket 端点（v0.2.0 默认）

| 端点 | 方法 | 用途 |
|------|------|------|
| `/health` | GET | 存活探针，返回 `{ok, version, uptime_s}` |
| `/rpc` | POST | JSON-RPC 2.0 请求/响应 |
| `/ws` | GET | WebSocket，server-push 流式事件 |

CORS：仅允许 `http://localhost:5173` / `http://127.0.0.1:5173`。

### IPC 命名空间（10 个）

| 前缀 | 方法数 | Handler 文件 | 主要功能 |
|------|--------|-------------|----------|
| `agent.*` | 8+ | `builtins.py`, `handlers_agents.py` | 消息发送、子 agent spawn |
| `session.*` | 5 | `handlers_sessions.py` | 会话 CRUD、归档 |
| `message.*` | 1 | `handlers_sessions.py` | 消息列表 |
| `model.*` | 3 | `handlers_model.py` | 模型列表/切换 |
| `skill.*` | 5 | `handlers_skills.py` | 技能管理/调用 |
| `schedule.*` | 6 | `handlers_scheduled.py` | 定时任务 CRUD/启停 |
| `permission.*` | 5 | `handlers_permissions.py` | 权限规则管理/解析 |
| `task.*` | 6 | `handlers_tasks.py` | 进度追踪 |
| `mobile.*` | 5 | `handlers_mobile.py` | 设备配对/推送 |
| `secrets.*` | 3 | `handlers_secrets.py` | API 密钥管理 |
| `git.*` | 3 | `handlers_git.py` | Git 状态/差异/日志 |

### 流式事件（WebSocket push）

- `agent.message_chunk` — LLM 流式输出 + metadata（thinking_count）
- `agent.tool_call` / `agent.tool_result` — 工具调用中间状态
- `agent.status` — Agent 状态变化
- `agent.subagent_progress` — 子 agent 生命周期进度
- `permission.request` / `permission.resolved` — 权限弹窗
- `task.progress` — 任务进度

## 关键依赖与配置

### 依赖（pyproject.toml）

| 包 | 用途 |
|---|------|
| `pydantic>=2.7` | 数据验证、Config 模型 |
| `httpx>=0.27` | 异步 HTTP 客户端（LLM API） |
| `aiosqlite>=0.20` | 异步 SQLite |
| `apscheduler>=3.10` | 定时任务调度 |
| `platformdirs>=4.0` | 跨平台数据目录 |
| `keyring>=24` | OS keyring 存储 API 密钥 |
| `fastapi>=0.110` | HTTP + WebSocket transport |
| `uvicorn[standard]>=0.27` | ASGI server |

### 配置（环境变量）

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `MINIMAX_API_KEY` | 空（mock mode） | MiniMax API 密钥 |
| `MINIMAX_CODE_LOG_LEVEL` | `INFO` | 日志级别 |
| `MINIMAX_CODE_ENV` | `development` | 环境 |
| `MINIMAX_CODE_HTTP_PORT` | `8765` | HTTP 端口 |
| `MINIMAX_CODE_HTTP_HOST` | `127.0.0.1` | 绑定地址 |
| `MINIMAX_CODE_DATA_DIR` | platformdirs | 数据库位置 |
| `MINIMAX_CODE_NO_DB` | 空 | 设为 `1` 跳过数据库 |
| `MINIMAX_CODE_SKILLS_DIR` | `agent/skills/` | 技能根目录 |

## 数据模型

SQLite 数据库，8 张主表 + 1 张迁移记录表。位置由 `platformdirs` 决定（Windows: `%APPDATA%\MiniMaxCode\data.db`）。

| 表 | 用途 | DAO 文件 |
|----|------|---------|
| `sessions` | 会话 | `storage/dao/sessions.py` |
| `messages` | 聊天消息 | `storage/dao/messages.py` |
| `tasks` | 任务进度 | `storage/dao/tasks.py` |
| `skills` | 技能注册 | `storage/dao/skills.py` |
| `scheduled_jobs` | 定时任务 | `storage/dao/scheduled_jobs.py` |
| `agents` | 子 Agent 配置 | `storage/dao/agents.py` |
| `permission_rules` | 权限规则 | `storage/dao/permissions.py` |
| `mobile_devices` | 移动设备 | `storage/dao/mobile_devices.py` |
| `schema_migrations` | 迁移记录 | — |

迁移策略：前向迁移（NNN_name.py），无回滚。WAL 模式，foreign_keys ON。

## 测试与质量

| 测试类型 | 工具 | 位置 | 数量 |
|----------|------|------|------|
| 单元测试 | pytest + pytest-asyncio | `tests/` | 15+ 文件（IPC、Storage、Agent Core、Tools、Skills、Scheduler、Permissions、Mobile、Sessions、Model、Secrets、HTTP Server、Git handlers 等） |
| 黑盒 smoke | subprocess | `tests/e2e/` | 7 个 smoke（sessions、mobile、agents、phase2b、chat、progress、model） |

运行命令：
```bash
cd agent && uv run pytest           # 全部单元测试
cd agent && uv run pytest -x        # 失败即停
```

Lint：
```bash
cd agent && uv run ruff check .
```

## 常见问题 (FAQ)

**Q: 为什么有两种 transport（HTTP 和 stdio）？**
A: HTTP + WebSocket 给浏览器前端用；stdio 给测试 subprocess 和 CLI 调试用。它们共享同一份 handler registry，不是两份平行实现。不要在同一进程同时运行两种模式。

**Q: Mock mode 是什么？**
A: 当 `MINIMAX_API_KEY` 为空时，`MiniMaxClient` 进入 mock mode，返回确定性文本而不调用真实 API。用于开发和 CI 环境。

**Q: 如何新增一个 IPC 方法？**
A: 1) 在对应的 `handlers_*.py` 中实现 handler 函数；2) 在 `app.py` 的 `register_app_handlers` 中注册；3) 同步更新前端的 `TypedIPC` 接口和 `mockHandle`。

## 相关文件清单

### 核心入口
- `minimax_code/__main__.py` — 入口（HTTP/stdio 模式选择）
- `minimax_code/app.py` — Handler 注册 + 运行时单例管理
- `minimax_code/config.py` — 配置模型（Pydantic）
- `minimax_code/http_server.py` — FastAPI 薄 transport

### IPC 层
- `minimax_code/ipc/server.py` — IPCServer + Context（核心调度）
- `minimax_code/ipc/protocol.py` — 消息类型定义（Request/Response/Event/Notification）
- `minimax_code/ipc/builtins.py` — ping/status/shutdown + agent.send_message
- `minimax_code/ipc/handlers_*.py` — 各命名空间 handler（11 个文件）

### Agent 核心
- `minimax_code/agent/core.py` — AgentCore 对话循环
- `minimax_code/agent/llm.py` — MiniMaxClient（httpx async + mock）
- `minimax_code/agent/prompts.py` — System prompt 模板
- `minimax_code/agent/tools/` — 6 个工具（file_ops、terminal、edit、search、base、skill）
- `minimax_code/agent/skills/` — 技能系统（loader、registry、runtime）
- `minimax_code/agent/skills/_builtin/` — 内置技能工具（commit_helper、test_generator）

### 存储层
- `minimax_code/storage/db.py` — 同步/异步 Database wrapper
- `minimax_code/storage/migrations/` — 迁移文件（001_initial、002_model_prefs）
- `minimax_code/storage/dao/` — 8 个 DAO 类

### 其他模块
- `minimax_code/scheduler/` — APScheduler 集成
- `minimax_code/orchestrator/subagent.py` — SubAgentRuntime
- `minimax_code/permissions/` — PermissionStore
- `minimax_code/mobile/` — PairingManager
- `minimax_code/progress/` — ProgressTracker
- `minimax_code/secrets.py` — OS keyring + env-var fallback
- `minimax_code/perm_consent.py` — 权限同意流程
- `minimax_code/logging_setup.py` — 日志配置

### 技能文件
- `skills/commit-helper/SKILL.md`
- `skills/code-review/SKILL.md`
- `skills/test-generator/SKILL.md`

### 配置
- `pyproject.toml` — 依赖、构建、pytest/ruff 配置
- `uv.lock` — 锁定文件

## 变更记录 (Changelog)

- **2026-06-06** — 新增 [`minimax_code/SELF.md`](minimax_code/SELF.md)，定义主 agent 行为准则（边界地图、自进化层定位、给下一个对话窗口的开局指引）。是 `CLAUDE.md` 的人本补充，新会话开局请优先阅读。
- **2026-06-04** — 初始化 agent 模块 CLAUDE.md
