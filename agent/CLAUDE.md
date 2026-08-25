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
| `MINIMAX_CODE_TEAM_MAX_CONCURRENCY` | `4` | 单次团队运行的最大并发子 agent 数（v1.2.2；`<=0` 不设限） |
| `MINIMAX_CODE_SUBAGENT_TIMEOUT_S` | `600` | 子 agent 墙钟超时秒数（v1.2.2 team 路径；v1.4.0 起工具路径 `spawn_subagent` 同受管辖并豁免 `tool_timeout`；`<=0` 禁用） |

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

迁移策略：前向迁移（NNN_name.py，当前 001-028 共 28 个），无回滚，`migrate()` 在显式事务内逐个执行（失败整体回滚）。WAL 模式，foreign_keys ON。

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
- `minimax_code/agent/tools/` — 12 个工具模块 + base（file_ops、edit、search、glob、terminal、subagents、artifacts、sandbox、codebase_search、codebase_summarize、codebase_find_symbol、codebase_navigate）
- `minimax_code/agent/skills/` — 技能系统（loader、registry、runtime）
- `minimax_code/agent/skills/_builtin/` — 内置技能工具

### 存储层
- `minimax_code/storage/db.py` — 同步/异步 Database wrapper
- `minimax_code/storage/migrations/` — 迁移文件（001-028 共 28 个，前向幂等）
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

- **2026-08-25** — v1.5.0：写安全专项（三层）——① CAS 乐观锁：`file_sha256()` helper（流式磁盘 bytes）；`read_file` 输出 `sha256`（truncated → null）；`write_file`/`edit_file` 收可选 `expected_sha256`（不匹配 fail 带 `current_sha256`，edit 校验在 backup 前被挡不产生 backup），输出 `previous_sha256`/`sha256`。② opt-in per-run 沙盒：`spawn_subagent(sandbox=True)` 写入透明重定向 `<root>/.minimax/sandboxes/<run_id>/`（COW `_base/` 基线 + `overlay_read_target()` 覆盖 read/edit 两处读 + `.minimax/` pass-through + fail-closed 建目录）；`workspace_ctx.py` 新增 `_current_sandbox` ContextVar 三件套；新模块 `tools/sandbox.py`（工具模块 11→12）；`SANDBOX_PROTOCOL_PROMPT` 拼接最尾。③ `collect_subagent(run_id, on_conflict=fail|skip|overwrite)` 三方对比合并（三 sha 全报不静默 clobber；`.merged` marker 幂等；in-flight 拒绝；fs_bus cause=`collect_subagent`）+ `files_written` 五处传播（result / completion_metadata 落 run 行 / `_snapshot` / wait=true envelope / `_lookup_finished_run`）。37 个新测试（`test_file_cas.py` 12 + `test_subagent_sandbox.py` 14 + `test_subagent_collect.py` 14，全走 dispatch），pytest 10415；已知限制：exec_command 绕过沙盒、search/glob 不 overlay、team 路径不在本期、沙盒不 prune
- **2026-08-25** — v1.4.2：任务优先级注入——`subagents.py` 新增 `TASK_PRECEDENCE_PROMPT`（当前任务优先于常设角色、workspace 文件仅 context、冲突跟随任务并在上报注明），spawn system_prompt 拼接顺序「agent 模板 → 优先级声明 → completion 协议」；修并发压测实测的子 agent 叛变（whiteboard-render-engineer 跟随旧 CONTRACT.md 重写 wb_render.js）；`test_tool_dispatch_kwargs.py` +2 测试，pytest 10375
- **2026-08-25** — v1.4.1：dispatch 路由修复——`tools/base.py` 删除 `uses_legacy_args_dict` 启发式（零真实 legacy 工具，单参数工具被误传整个 args dict：`check_subagent` 崩 / `list_subagents` 静默错），一律 `run(**args)`；`subagents.py` `REPORT_PROTOCOL_PROMPT` 加 Budget rule（交付物落盘即 report，防 iteration 耗尽软强制失效）；新测试 `test_tool_dispatch_kwargs.py`（7 个，全走 dispatch 全链路），pytest 10373
- **2026-08-25** — v1.4.0：子 Agent 生命周期专项（agent 侧）——① 止血：`Tool.dispatch_timeout` 豁免属性（`base.py`）+ `core.py` `_execute_tool_call` 按工具实例取 effective timeout；`subagent_wall_clock_s()` 从 team_orchestrator 提取为公开函数（单一实现两处对齐），`spawn_subagent` run() 实例级赋值（env 旋钮动态生效）；invoke envelope 透传 `usage`/`cancelled`/`truncated`。② 状态机：migration 028（`agent_runs` mode CHECK 加 `'subagent'`，四步表重建 + `_v28` 后缀索引名防 021 RENAME 残留同名索引连删）+ DAO `_VALID_RUN_MODES`/`list_runs(mode=)`；`orchestrator/subagent.py` 加 `_current_parent_session` ContextVar + `_SUBAGENT_EVENT_ROUTES` 事件路由表（builtins send_message 注册 / finally pop，工具层 fail-open 查表 emit）；工具路径 spawn 全链路：`_ensure_session` 补 FK 行 → `create_run(mode='subagent')` → `_ACTIVE_RUNS` 注册（IPC `agent.cancel_subagent` 与进程关停打通）→ started/实时 tool_call/tool_result/completed/failed/cancelled 事件（wire 形状同 `_emit_subagent_progress`，必带 `parent_session_id`）。③ 异步化：`wait=false` 后台 task（模块级 `_BACKGROUND_RUNS` 强引用 + done_callback 异常记录）+ `check_subagent`/`wait_subagent` 新工具（wait 超时不杀 task）；wait=true shield 墙钟超时 → `core.cancel()` 协作收尾保 partial（`metadata.partial=true`）。④ artifact 协议：新模块 `tools/artifacts.py`（spawn 写 `BRIEF.md` + `report_completion` per-run 注入工具（克隆 registry + allowlist 追加，写 `COMPLETION.md`/`REPORT.json`）+ `read_artifact` containment 锚定）+ 子 agent system_prompt 拼协议段 + 软强制（envelope `reported` 字段，未上报 warning 不失败）；新增 `test_tool_dispatch_timeout.py`/`test_subagent_lifecycle_runs.py`/`test_subagent_async.py`/`test_subagent_artifacts.py`，Python 侧 +48 测试（pytest 10366）
- **2026-08-24** — v1.3.0：Per-Project Workspace Root 专项（agent 侧）——migration 026（`projects.root_path TEXT NOT NULL DEFAULT ''`，PRAGMA 列守卫）+ 027（`codebase_chunks` 加 `root` 列 + `codebase_file_meta` 重建为 `(root, file_path)` 复合 PK，同事务防重入，存量行 root='' 零重索引）；新模块 `workspace_ctx.py`（ContextVar 根解析「worktree > 项目根 > env > cwd」+ `set/reset_current_root` token 式 + `session_root_scope` asynccontextmanager，项目根目录缺失时 warning 回退）；`file_ops._default_workspace()` 单点接线（10 工具 + 8 builtin 技能工具 + BackupManager 零签名生效，严格 containment 越界拒绝）；`builtins.py` send_message 注入根 + system prompt 条件化「当前项目工作区根」提示；`handlers_agents.py`/`handlers_teams.py` 子 agent 与团队 scope 包装（并发天然隔离）；codebase per-root（store 全方法 `root` keyword + `_CODEBASE_INDEXERS` dict 按根缓存，修跨根增量误删 + `_WORKSPACE_DIR` 索引根错位存量 bug，deprecated warning）；`git.*`/`patch.*`/`codebase.*`/`terminal.start`/`workspace.create_worktree_session` 可选 `project_id`（`_validate_project_id` 未知 id 快速失败，显式 cwd containment 仅在带 project root 时启用）；checkpoint create/restore 后端自解析会话根；新增 `test_migration_026` / `test_workspace_ctx` / `test_workspace_scope_runs` / `test_codebase_multi_root`（A/B 双根互不可见）/ `test_patch_project_root` 等，Python 侧 +78 测试（pytest 10318）
- **2026-08-23** — v1.2.2：多 Agent 协作与系统稳定性专项（agent 侧）——`teams.spawn` 注入 `get_subagent_llm()`（`TeamOrchestrator._llm` 不再恒 None）；`agent.invoke`/`spawn_subagent` 配置透传（`_config_from_row`：`max_iterations`/`temperature`）；调度器 `_spawn_fire` 强引用 + `_done` 异常回调 + 收尾每步守卫；权限 gater 注册表（`register_gater`/`unregister_gater`/`resolve_any_gater` 按 session，legacy 单槽兼容）；终端进程树杀（Windows `taskkill /F /T` / POSIX `killpg` + 回退）；WS ready 帧 `next_seq` 锚点（seq 纪元重置检测）；team 并发 Semaphore + 子 agent 墙钟超时 + `_merge_texts` 部分失败 advisory（env `MINIMAX_CODE_TEAM_MAX_CONCURRENCY` / `MINIMAX_CODE_SUBAGENT_TIMEOUT_S`）
- **2026-08-23** — v1.2.1：修复长中文 write/edit 工具调用截断——`AgentConfig.max_output_tokens`（默认 32768，env `MINIMAX_CODE_MAX_OUTPUT_TOKENS`）、`_stream_turn` 透传 max_tokens、anthropic transport 兜底 4096→32768、finish_reason=length 截断 warning、malformed JSON 错误附恢复指引；9 个回归测试（`test_output_token_budget.py`）
- **2026-08-23** — v1.2.0：全局审计修复（agent 侧）——scheduler `_dispatch_prompt_job` 真实跑 prompt 任务往 session 发消息、025 migration（`scheduled_jobs.result_persist` 列）+ TasksDAO 结果读写、`permission.request` 广播补 session_id 归属、teams handler `emit_event=ctx.emit` 净化（metadata keyword-only）、技能调用结果落库 skill_runs
- **2026-08-23** — v1.1.3：修复 context 指示器恒 0——core 持久化带 metadata 副本、builtins `_persist` 传 `tokens_in`/`tokens_out` 列；前端 ContextIndicator 取最新占用而非累加
- **2026-08-23** — v1.1.2：修复长会话历史口癖污染——`_build_system_prompt_extra` 前置 stale-note advisory、nudge 防复述指令 + context-pressure 节流（`context_nudge_fired` / `compacted_this_iteration`）
- **2026-08-23** — v1.1.1：ask_user 工具（第 10 工具 + agent.answer_user + agent.ask_user 事件）、max_iterations 12→200（env `MINIMAX_MAX_ITERATIONS`）、subagent 默认 50；IPC 170 方法 / 36 前缀 / 事件 16
- **2026-08-21** — R43 对账同步：24 实体表/167 IPC 方法 35 前缀/31 handler 文件/10 工具模块/20 DAO/25 迁移/12 技能/6 smoke，事件 7→15，环境变量补 CORS/LOG_FILE
- **2026-06-06** — 新增 [`minimax_code/SELF.md`](minimax_code/SELF.md)，定义主 agent 行为准则（边界地图、自进化层定位、给下一个对话窗口的开局指引）。是 `CLAUDE.md` 的人本补充，新会话开局请优先阅读。
- **2026-06-04** — 初始化 agent 模块 CLAUDE.md
