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

### IPC 命名空间（34 个前缀 / 170 个方法）

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
| `MINIMAX_CODE_SANDBOX_DEFAULT` | 空（false） | `spawn_subagent` 省略 `sandbox` 参数时的进程级默认（v1.5.2；优先级：显式传参 > env > false） |
| `MINIMAX_SOFT_LIMIT_REMAINING` | `8` | 子 agent 剩余迭代预算低于该值时注入 handoff nudge（v1.6.1；clamp 下限 1；budget 本身默认 100） |

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

迁移策略：前向迁移（NNN_name.py，当前 001-029 共 29 个），无回滚，`migrate()` 在显式事务内逐个执行（失败整体回滚）。WAL 模式，foreign_keys ON。

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
- `minimax_code/agent/tools/` — 15 个工具模块 + base（file_ops、edit、search、glob、terminal、subagents、artifacts、sandbox、ask_user、codebase_search、codebase_summarize、codebase_find_symbol、codebase_navigate、shared_memory、verification；v1.6.1 实测对账，此前口径漏数 ask_user）
- `minimax_code/agent/skills/` — 技能系统（loader、registry、runtime）
- `minimax_code/agent/skills/_builtin/` — 内置技能工具

### 存储层
- `minimax_code/storage/db.py` — 同步/异步 Database wrapper
- `minimax_code/storage/migrations/` — 迁移文件（001-029 共 29 个，前向幂等）
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

- **2026-08-30** — Unreleased（迭代优化计划 R2：多 Agent 深化·team 路径沙盒化，agent 侧）——v1.5.0 四项写安全已知限制的最后一项收口：① **`collect_subagent` 核心提取**：`tools/sandbox.py` 新增模块级 `async def collect_sandbox_run(run_id, *, on_conflict, prune) -> (ok, error, report)`，合并循环/receipt/prune 原样迁入；`CollectSubagentTool.run` 尾部改委托，壳只留参数校验 + in-flight guard，wire 形状与消息字符串逐字不变；② **`team_orchestrator.py` 沙盒贯穿**：`TeamOrchestrator(sandbox=)` / `run(sandbox=)` 参数链；`_run_single_agent` 派生确定性 run id `team_<task_id>_<idx>_<name>`（`_sandbox_run_id` helper：re 消毒 agent 名防路径逃逸、review 的 reviewer 落独立 index）+ fail-closed 建目录 + `set_current_run_id`/`set_sandbox` ContextVar 激活 + finally 按工具路径同序释放（`release_run_writes` → run id → sandbox）+ `SANDBOX_PROTOCOL_PROMPT` 前置 request（不 mutate 共享 config 的 system_prompt，防 review 重跑叠加）；**自动 collect（skip 策略）**——`_collect_one` advisory 合并（拒绝/崩溃计入 summary errors，永不杀 run），sequential 成员间即时收（后继可见前驱写入）、review writers 全收后跑 reviewer、其余模式 `_run_body` 末尾扫尾（`collect_state["processed"]` 防双收）；`AgentRunResult.run_id` / `TeamRunResult.sandbox_summary` 新字段，`_result_to_dict` 条件序列化保 legacy 字节可比；③ **`handlers_teams.py`**：`team.spawn` 收 `sandbox`（缺省 `_sandbox_default()` env 梯子）+ reply 增 `sandbox`/条件 `sandbox_summary`/`agents_run[].run_id`；+10 测试 `tests/test_team_sandbox.py`（预置 mirror + 进度 hook 时序断言，无真实 LLM 驱动全链路）
- **2026-08-29** — v1.6.1：债务清偿发版（agent 侧）——① **多 Agent 协作优化 v1**（`b001a63`）：新工具模块 `tools/shared_memory.py`（`shared_memory_put/get/list`，`.minimax/shared_memory/store.json`，workspace/run 双 scope，文件锁，损坏 store 隔离）与 `tools/verification.py`（`verify_subagent` 无头验收器：workspace 根 `is_relative_to` 锚定、超时进程树杀 `taskkill /F /T` / `killpg`）；iteration budget 默认 8→100 全链路（DAO upsert / `_config_from_row` / skills runtime；migration 029 上提 migration-010 时代卡 8 的存量行，只动 `=8` 不碰显式 10 与用户调优值，不改列 DEFAULT；handoff nudge 改 env `MINIMAX_SOFT_LIMIT_REMAINING` 默认 8、硬下限 1）；`depends_on` DAG 门控（后台 spawn 等上游 run id，未知/已回收依赖视为满足，修空转到 600s 墙钟；`waiting_deps` 走统一投影）；`build_subagent_status` 统一投影（running / waiting_deps / completed / cancelled / failed，`_snapshot` 超集，finished 不再误报 running）；+34 测试（4 文件全走 dispatch）；② **定时任务 command 载荷分支**（`d8c08b2`）：`{command, cwd?, timeout_s?}` 经 `create_subprocess_shell` 主循环执行、per-stream 20 KB 封顶；`error` 键 = 基础设施失败（spawn 失败/超时 → task 行 failed），非零退出码 = 命令结果（`ok=False` 无 `error`）；③ **修复**：`AgentDAO.upsert` 补 `enabled` 写路径（启停开关此前静默无效，`ca50935`）；`model.set_current` 省略 `provider_id` 时反查属主 provider（`32655f2`）；`AgentConfig.model` 从 LLM 单例 `default_model` 镜像（修第三方 provider 1214，`9806848`）；openai transport `_usage_to_dict` 共享 mapper + finish chunk 挂 usage 形状（修 compat provider tokens 恒 0 + `reasoning_content` 思考流丢弃，`#138`）；`secrets.py` legacy 全局 key → per-provider 槽一次性迁移（清除不再复活，`#137`）；④ 对账：工具模块 12→15（补漏 ask_user + 新增 shared_memory/verification）、migration 28→29、IPC 前缀 36→34（方法数 170 不变）、env 表补 `MINIMAX_SOFT_LIMIT_REMAINING`
- **2026-08-26** — v1.6.0：Sandbox Deepening 专项（v1.5.0 四项已知限制收掉三项，team 路径独立立项不做）——① **exec_command 逃逸检测（advisory）**：沙盒 run 的 exec 前后各 walk 一次 workspace（`(mtime_ns, size)` 快照，`asyncio.to_thread`；窄排除表不含 build/dist——构建产物正是逃逸形态），diff 后减去 fs_bus 窗口内其他 run 的写入（防并发误报），剩余以 `sandbox_escape: {changed(≤50), changed_count, warning}` 注入正常与 timed_out 双输出路径 + emit 合法枚举 kind（cause=`exec_command_sandbox_escape` + run_id）→ 主 agent v1.5.1 notes 轮询天然可见；主 agent 零开销；`SANDBOX_PROTOCOL_PROMPT` 第三 bullet 改写（见 warning 用 write_file 重写交付物）。② **search/find/list overlay + 剪枝修复**：`sandbox.py` 新增共享 helpers `sandbox_mirror_files`/`mirror_children`；`search_files` 沙盒激活强制 python 引擎 + rel-posix key union（同名沙盒版覆盖）+ 读取走 `overlay_read_target`（含 single_file 分支）；`find_files` walk 后 mirror union（前缀过滤 + rel 投影 + depth + 覆盖）；`list_directory` entries 并入 mirror children（`path` 投影回 workspace 地址 + `sandboxed: true` + `_base`/marker 隐藏）+ 沙盒-only 目录放行；**存量修复**——search python 剪枝表补 `.minimax`（此前主 agent 能搜到 backups/sandboxes 内部）、`find_files` 的 `.minimax` 硬排除（用户显式覆盖 `exclude_dirs` 不再泄漏）；第四 bullet 改 overlay 语义。③ **collect prune + receipt 新家**：receipt 一律写 `<root>/.minimax/sandboxes/.collected/<run_id>.json`（flat sibling，`sandbox_files_written` 永不扫到），`prune` 参数（默认 true，schema 同步）在 receipt 落盘且无 conflicts/errors/skipped 时 rmtree 沙盒树（`onexc`/`onerror` 双兼容 + `_chmod_retry` 清只读位）；保守门——receipt 没写成不删（无凭证不删数据）、conflicts+fail 永不 prune 永不写 receipt、skipped/errors 非空保留；rmtree 失败 advisory（`prune_error`）+ already-collected 分支重试 prune 自愈；legacy `sb_dir/.merged` marker 仍读（升级前沙盒照常识别），命中且 prune 时先迁移 receipt 再删树（迁移失败取消 prune），`prune=false` 时 legacy 布局原样；already-collected 检查前置到 in-flight guard 后。40 个新测试（`test_exec_sandbox_escape.py` 10 + `test_sandbox_overlay_views.py` 16 + `test_collect_prune.py` 14，全走 dispatch；`test_subagent_collect.py` 五处旧 marker 钉子同步迁移），pytest 10519 / vitest 758 全绿
- **2026-08-26** — v1.5.2：第四轮压测三项残留收口——① `MINIMAX_CODE_SANDBOX_DEFAULT` env 旋钮：`subagents.py` 新增 `_sandbox_default()` helper（truthy `1/true/yes/on`），`spawn_subagent` 的 `sandbox` 参数改 `bool | None = None` 顶部归一化（优先级：显式传参 > env > false，默认 false 不变）；② `append_file` 工具（file_ops.py，工具模块 12 个、内置工具第 13 个）：尾部 verbatim 追加（UTF-8 字节保真不注入分隔符、missing 带父目录创建），CAS `expected_sha256` / backup / `concurrent_writer` advisory / fs_bus 归因（cause=`append_file` + `run_id` attribute）全继承；**沙盒镜像 seeding**——`redirect_write_target` 只 COW `_base/` 不 seed 镜像，append 打开前检测 `write_target != read_target` 且原件存在且镜像不存在 → `shutil.copy2` 原件进镜像（copy 失败 fail-closed），否则 collect 三方对比会把不完整镜像 merge 回 workspace（静默数据丢失）；③ `report_progress` per-run 注入工具（artifacts.py：`PROGRESS_NAME` 常量 + `read_progress`/`progress_summary` helper + `ReportProgressTool`；subagents.py：`PROGRESS_PROTOCOL_PROMPT` + `_clone_registry_with_report` 扩为双工具注入）：子 agent 里程碑级 `report_progress(note, percent?)` → `.minimax/artifacts/<run_id>/PROGRESS.jsonl` JSON 行账本 → `check_subagent` running / `wait_subagent` timeout / `_snapshot` 与 finished-run envelope 的 `progress` key（`{total, recent[], latest_percent}`）+ live `agent.subagent_progress` 事件（`status="thinking"` + `summary` + `progress` 分数，复用前端闭合 union）；prompt 拼接顺序：agent 模板 → 任务优先级 → completion → progress → 沙盒；41 个新测试（`test_append_file.py` 15 + `test_report_progress.py` 13 + `test_sandbox_env_default.py` 13，全走 dispatch），pytest 10479 / vitest 758 全绿
- **2026-08-26** — v1.5.1：共享 workspace 并发感知（第三轮压测 3 项残留收口，全 advisory 零写路径行为变化）——① **A**：`spawn_subagent` 工具 description 加并发写决策引导（写文件且并行 → `sandbox=true` + `collect_subagent` 合并；只读保持 false）；② **B**：新模块 `fsnotify/notes.py`——`AgentCore` 每 iteration 以 seq 高水位轮询 `bus.recent()`（首次 poll 初始化水位不回放历史；轮询非 subscribe 零生命周期管理），其他 in-flight run 的写入聚合为 `[system note] Files changed by other agents` 追加 LLM payload 尾部（ephemeral 不持久化禁 acknowledge，去重 ≤8 行，沙盒路径投影 `src/a.py (sandboxed by run_x)`，fail-open）；自过滤按 `run_id` attribute，子 agent 天然见主 agent 的写；③ **C**：`file_ops._INFLIGHT_WRITES`（normcase key → run_id）+ `workspace_ctx._current_run_id` ContextVar 三件套（`_drive_run` 顶部发布 / finally 释放 claims 再 reset）；带 run id 的 write/edit claim 触碰路径，主 agent 只查警；rival 命中写照常成功但 output 带 `concurrent_writer` + warning。23 个新测试（`test_write_registry.py` 10 + `test_fs_change_notes.py` 12 + sandbox description 钉 1），pytest 10438 / vitest 758 全绿
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
