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

CORS：默认允许 `http://localhost:5173` / `http://127.0.0.1:5173`；`MINIMAX_CODE_CORS_ORIGINS`（逗号分隔）可追加受信 origin（解析见 `http_server.py` `_cors_allow_origins`，无效项 warning 忽略）。

### IPC 命名空间（36 个前缀 / 170 个方法）

完整前缀×方法数×handler 文件总表见根目录 CLAUDE.md「IPC 命名空间」节；方法级清单见 `docs/ipc-contract.md` Appendix A（由 `agent/tests/test_ipc_contract_doc.py` 双向守护）。高频命名空间：

| 前缀 | 方法数 | Handler 文件 | 主要功能 |
|------|--------|-------------|----------|
| `agent.*` | 12 | `builtins.py`, `handlers_agents.py` | 消息发送、续跑、ask_user 应答、子 agent spawn |
| `session.*` | 12 | `handlers_sessions.py` | 会话 CRUD、归档、批量 |
| `message.*` | 3 | `handlers_sessions.py` | 消息列表/编辑/删除 |
| `model.*` | 4 | `handlers_model.py` | 模型列表/切换 |
| `skill.*` | 7 | `handlers_skills.py` | 技能管理/调用 |
| `schedule.*` | 6 | `handlers_scheduled.py` | 定时任务 CRUD/启停 |
| `permission.*` | 6 | `handlers_permissions.py` | 权限规则管理/解析 |
| `task.*` | 6 | `handlers_tasks.py` | 进度追踪 |
| `mobile.*` | 7 | `handlers_mobile.py` | 设备配对/推送 |
| `secrets.*` | 3 | `handlers_secrets.py` | API 密钥管理 |
| `git.*` | 3 | `handlers_git.py` | Git 状态/差异/日志 |
| `provider.*` | 7 | `handlers_providers.py` | 多 Provider 接入 |
| `patch.*` | 8 | `handlers_patch.py` | Patch Studio 应用/回退/快照 |
| `data.*` | 3 | `handlers_data.py` | 全量导出/导入/备份 |

另有 `audit` / `checkpoint` / `codebase` / `crash` / `diag` / `mcp` / `memory` / `notification` / `plugins` / `project` / `run` / `runner` / `runtime` / `team` / `telemetry` / `terminal` / `webhook` / `workflow` / `workspace` 共 19 个命名空间，以及无点号 built-in `ping` / `status` / `shutdown`。

### 流式事件（WebSocket push，16 个）

权威清单 = 前端 `web/src/types/ipc.ts` 的 `StreamEvent` 枚举，由 `tests/test_ipc_contract_doc.py` 锁死同步：

- `agent.message_chunk` — LLM 流式输出 + metadata（thinking_count）
- `agent.tool_call` / `agent.tool_result` — 工具调用中间状态
- `agent.ask_user` — 结构化澄清问题（v1.1.1；UI 渲染内联问答卡，经 `agent.answer_user` 应答）
- `agent.status` — Agent 状态变化
- `agent.subagent_progress` / `agent.team_progress` — 子 agent / 团队进度
- `permission.request` / `permission.resolved` — 权限弹窗
- `task.progress` — 任务进度
- `notification.new` / `notification.read` — 通知中心
- `run.created` / `run.step.started` / `run.step.completed` / `run.completed` — Run 生命周期

另有协议级帧（无 seq）：`agent.ready`（握手）、`agent.ping`（心跳）。广播带单调 seq + 512 深度历史环，断线重连按 `?since=` 重放。

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
| `MINIMAX_MAX_ITERATIONS` | `200` | Agent 迭代安全阀（v1.1.1；clamp [1, 10000]） |
| `MINIMAX_CODE_MAX_OUTPUT_TOKENS` | `32768` | 单次 LLM 调用输出预算（v1.2.1；clamp [1024, 131072]，防长中文 write/edit 参数截断） |
| `MINIMAX_CODE_LOG_LEVEL` | `INFO` | 日志级别 |
| `MINIMAX_CODE_ENV` | `development` | 环境 |
| `MINIMAX_CODE_HTTP_PORT` | `8765` | HTTP 端口 |
| `MINIMAX_CODE_HTTP_HOST` | `127.0.0.1` | 绑定地址 |
| `MINIMAX_CODE_DATA_DIR` | platformdirs | 数据库位置 |
| `MINIMAX_CODE_NO_DB` | 空 | 设为 `1` 跳过数据库 |
| `MINIMAX_CODE_SKILLS_DIR` | `agent/skills/` | 技能根目录 |
| `MINIMAX_CODE_CORS_ORIGINS` | dev 白名单 | 追加受信 CORS origin（逗号分隔，无效项忽略） |
| `MINIMAX_CODE_LOG_FILE` | 空（仅控制台） | 日志落盘路径（带轮转） |

## 数据模型

SQLite 数据库，**24 张表**（23 张业务表 + `schema_migrations` 迁移记录；代码库索引与长期记忆另有 FTS / sqlite-vec 虚表及影子表，`sqlite_master` 合计 41 个表对象）。位置由 `platformdirs` 决定（Windows: `%APPDATA%\MiniMaxCode\data.db`）。完整 ER 图与索引策略见 `docs/storage-schema.md`。

核心表（完整清单见 schema 文档）：

| 表 | 用途 | DAO 文件 |
|----|------|---------|
| `sessions` | 会话（含 project_id 分组） | `storage/dao/sessions.py` |
| `messages` | 聊天消息 | `storage/dao/messages.py` |
| `tasks` | 任务进度 | `storage/dao/tasks.py` |
| `skills` | 技能注册 | `storage/dao/skills.py` |
| `scheduled_jobs` | 定时任务 | `storage/dao/scheduled_jobs.py` |
| `agents` / `agent_teams` / `agent_runs` | 子 Agent / 团队 / 运行 | `storage/dao/agents.py` 等 |
| `permission_rules` | 权限规则 | `storage/dao/permissions.py` |
| `mobile_devices` | 移动设备 | `storage/dao/mobile_devices.py` |
| `codebase_chunks`（含 FTS/向量） | 代码库索引 | `storage/dao/` codebase 相关 |

DAO 层共 20 个模块（`storage/dao/`）。

迁移策略：前向迁移（NNN_name.py，当前 001-025 共 25 个），无回滚，`migrate()` 在显式事务内逐个执行（失败整体回滚）。WAL 模式，foreign_keys ON。

## 测试与质量

| 测试类型 | 工具 | 位置 | 数量 |
|----------|------|------|------|
| 单元测试 | pytest + pytest-asyncio | `tests/` | 15+ 文件（IPC、Storage、Agent Core、Tools、Skills、Scheduler、Permissions、Mobile、Sessions、Model、Secrets、HTTP Server、Git handlers 等） |
| 黑盒 smoke | subprocess | `tests/e2e/` | 6 个 smoke（agents、chat、mobile、model、phase2b、progress） |
| 安全回归 | pytest `-m security` | 8 个域文件 + `tests/test_security.py` 集中入口 | 151 测试（权限/脱敏/secrets/终端加固/注入防护/审计/RPC 拒绝/CORS）；`tests/test_security.py` 持套件 floor（130）防安全覆盖静默蒸发 |

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
- `minimax_code/ipc/handlers_*.py` — 各命名空间 handler（32 个文件：agents、audit、checkpoint、codebase、crash、data、diag、git、mcp、memory、mobile、model、notifications、patch、permissions、plugins、projects、providers、runner、runs、runtime、scheduled、secrets、sessions、skills、tasks、teams、telemetry、terminal、webhooks、workflows、workspace）

### Agent 核心
- `minimax_code/agent/core.py` — AgentCore 对话循环
- `minimax_code/agent/llm.py` — MiniMaxClient（httpx async + mock）
- `minimax_code/agent/prompts.py` — System prompt 模板
- `minimax_code/agent/tools/` — 10 个工具模块 + base（file_ops、edit、search、glob、terminal、subagents、codebase_search、codebase_summarize、codebase_find_symbol、codebase_navigate）
- `minimax_code/agent/skills/` — 技能系统（loader、registry、runtime）
- `minimax_code/agent/skills/_builtin/` — 内置技能工具

### 存储层
- `minimax_code/storage/db.py` — 同步/异步 Database wrapper
- `minimax_code/storage/migrations/` — 迁移文件（001-025 共 25 个，前向幂等）
- `minimax_code/storage/dao/` — 20 个 DAO 模块

### 其他模块
- `minimax_code/scheduler/` — APScheduler 集成
- `minimax_code/orchestrator/subagent.py` — SubAgentRuntime
- `minimax_code/permissions/` — PermissionStore（R18 起含代码级出厂默认：`exec_*` → ask；用户规则优先，删除用户规则即回退出厂默认，DB 零写入）
- `minimax_code/mobile/` — PairingManager
- `minimax_code/progress/` — ProgressTracker
- `minimax_code/codebase/` — Codebase RAG（indexer、store；增量索引 + FTS/sqlite-vec 混合检索）
- `minimax_code/mcp/` + `mcp_adapter/` — MCP 服务器接入
- `minimax_code/plugins/` — 插件系统
- `minimax_code/secrets.py` — OS keyring + env-var fallback
- `minimax_code/perm_consent.py` — 权限同意流程
- `minimax_code/logging_setup.py` — 日志配置

### 技能文件（`agent/skills/`，12 个）
- `commit-helper` / `code-review` / `test-generator` / `codebase-indexer` / `coding-standards` / `context-aware-chat` / `dependency-analyzer` / `doc-generator` / `refactor-assistant` / `security-audit` / `smart-debug` / `workspace-understanding`

### 配置
- `pyproject.toml` — 依赖、构建、pytest/ruff 配置
- `uv.lock` — 锁定文件

## 变更记录 (Changelog)

- **2026-08-23** — v1.2.1：修复长中文 write/edit 工具调用截断——`AgentConfig.max_output_tokens`（默认 32768，env `MINIMAX_CODE_MAX_OUTPUT_TOKENS`）、`_stream_turn` 透传 max_tokens、anthropic transport 兜底 4096→32768、finish_reason=length 截断 warning、malformed JSON 错误附恢复指引；9 个回归测试（`test_output_token_budget.py`）
- **2026-08-23** — v1.2.0：全局审计修复（agent 侧）——scheduler `_dispatch_prompt_job` 真实跑 prompt 任务往 session 发消息、025 migration（`scheduled_jobs.result_persist` 列）+ TasksDAO 结果读写、`permission.request` 广播补 session_id 归属、teams handler `emit_event=ctx.emit` 净化（metadata keyword-only）、技能调用结果落库 skill_runs
- **2026-08-23** — v1.1.3：修复 context 指示器恒 0——core 持久化带 metadata 副本、builtins `_persist` 传 `tokens_in`/`tokens_out` 列；前端 ContextIndicator 取最新占用而非累加
- **2026-08-23** — v1.1.2：修复长会话历史口癖污染——`_build_system_prompt_extra` 前置 stale-note advisory、nudge 防复述指令 + context-pressure 节流（`context_nudge_fired` / `compacted_this_iteration`）
- **2026-08-23** — v1.1.1：ask_user 工具（第 10 工具 + agent.answer_user + agent.ask_user 事件）、max_iterations 12→200（env `MINIMAX_MAX_ITERATIONS`）、subagent 默认 50；IPC 170 方法 / 36 前缀 / 事件 16
- **2026-08-21** — R43 对账同步：24 实体表/167 IPC 方法 35 前缀/31 handler 文件/10 工具模块/20 DAO/25 迁移/12 技能/6 smoke，事件 7→15，环境变量补 CORS/LOG_FILE
- **2026-06-06** — 新增 [`minimax_code/SELF.md`](minimax_code/SELF.md)，定义主 agent 行为准则（边界地图、自进化层定位、给下一个对话窗口的开局指引）。是 `CLAUDE.md` 的人本补充，新会话开局请优先阅读。
- **2026-06-04** — 初始化 agent 模块 CLAUDE.md
