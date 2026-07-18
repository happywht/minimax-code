# 融合演进迭代日志

> 每回合追加一条，**不覆写**历史记录。格式见 [`ROUND_TEMPLATE.md`](./ROUND_TEMPLATE.md)。
> 路线图见 [`EVOLUTION_ROADMAP.md`](./EVOLUTION_ROADMAP.md)。

---

## R1 — 演进控制中心 + 路线图奠基

- **回合序号**：1 / 50+
- **所属阶段**：A（融合地基与平台内核）
- **开始时间**：2026-07-18
- **状态**：✅ 完成

### 本轮目标

建立 50+ 回合迭代的**控制中心与治理框架**，让后续每个回合都有清晰的航向、模板与日志归宿。具体：

1. 盘点 Grok Build 核心能力 → MiniMax Code 差距，确定融合优先级。
2. 制定 5 阶段 50+ 回合路线图。
3. 建立迭代日志机制（本文件）与回合模板。
4. 用 Task 系统跟踪 5 个阶段。

### 设计决策

- **理念融合而非代码移植**：Grok 是 Rust workspace，移植不现实；我们用 Python/TS 在 MiniMax 架构上重新实现等价能力。
- **三大平台支柱**：MCP（协议扩展）+ Hooks（生命周期）+ Plugins（市场），这是"平台型产品"与"单体工具"的分水岭。
- **契约优先**：每个新能力先落 IPC 契约三方（`ipc-contract.md`、`ipc.ts`、`protocol.py`），再实现 handler，避免前后端漂移。
- **回合独立可验证**：每回合产出一个 commit，含目标/实现/验证/产出四要素。

### 实现 / 产出

- 新建 `docs/evolution/` 目录。
- `EVOLUTION_ROADMAP.md`：愿景、融合原则、Grok 能力盘点差距表、5 阶段 50+ 回合明细、验收标准。
- `ITERATION_LOG.md`（本文件）：回合追加式日志。
- `ROUND_TEMPLATE.md`：回合记录模板。
- 建立 5 个阶段 Task（#1–#5），阶段 A 标记 in_progress。

### 验证

- ✅ 路线图涵盖 Grok 全部 22 项核心能力的差距分析与落点安排。
- ✅ 5 阶段任务在 Task 系统中可追踪。
- ✅ 文档结构清晰，后续回合可直接套模板。
- ⏭️ 下一回合（R2）将做逐项架构差距分析报告，作为阶段 A 实现前的基线。

### Commit

`docs(evolution): R1 establish fusion evolution control center and 50-round roadmap`

---

## R2 — 架构差距分析报告

- **回合序号**：2 / 50+
- **所属阶段**：A
- **开始时间**：2026-07-18
- **状态**：✅ 完成

### 本轮目标

产出阶段 A 实现前的基线测绘：逐项对照 Grok 与 MiniMax 架构，明确每个融合项的现状、差距、融合策略与落地模块。

### 设计决策

- 阅读 Grok 三大支柱 crate 入口（`xai-grok-mcp`/`xai-grok-hooks`/`xai-grok-plugin-marketplace`/`xai-hooks-plugins-types`），提炼设计模式而非移植代码。
- 关键洞察：**MiniMax 是"封闭单体"，Grok 是"开放平台"**；融合本质 = 给 MiniMax 装上扩展面。
- 关键约束：Hooks 与现有 `permission.*` 协同（叠加而非替代）；Hooks 与 `workflow.py` 互补（守卫 vs 多步动作）。

### 实现 / 产出

- 新建 `docs/evolution/gap-analysis.md`：
  - P0 三大支柱（MCP/Hooks/Plugins）逐维度对照 + 融合策略 + IPC 契约。
  - P1 安全可观测（Sandbox/Checkpoint/Telemetry/Token/Crash）。
  - P2 协作感知（Codebase Graph/Memory/Subagent/Lifecycle/Sampler/PromptQueue）。
  - P3 多模态发布。
  - 6 条融合实施红线（无 Rust、契约三方同步、前向迁移、mock 覆盖、fail-open、回合独立）。

### 验证

- ✅ 覆盖路线图全部 22 项能力，每项有现状 + 策略 + 落点。
- ✅ 阶段 A 落地顺序经差距分析复核，确认 MCP 优先（是 Plugins/ComputerUse 协议底座）。
- ⏭️ R3 进入 MCP 类型契约实现。

### Commit

`docs(evolution): R2 add Grok vs MiniMax architecture gap analysis`

---

## R3 — MCP 协议类型契约

- **回合序号**：3 / 50+
- **所属阶段**：A
- **开始时间**：2026-07-18
- **状态**：✅ 完成

### 本轮目标

落地 MCP 协议层的**类型契约**——平台扩展的协议底座。建立 `minimax_code.mcp` 子包的常量层与 Pydantic 领域模型，为 R4（transport/client/server）和 R5（registry 桥接）打基础。

### 设计决策

- **镜像 MCP spec `2024-11-05`**：方法名、错误码、版本号逐字对齐公开规范，未来上游 SDK 可无缝替换。
- **复用项目 Pydantic 风格**：`_Base(extra="allow")` 与 IPC 层一致，前向兼容未知字段。
- **Content 用 discriminated union**：基于 `type` 字段的 PEP 604 `|` 写法 + `Field(discriminator=...)`，既满足 ruff 又保证类型安全。
- **协议常量无依赖**：`protocol.py` 仅用 stdlib，client/server 两半都能 import。
- **错误码不与 IPC 冲突**：MCP 应用码用 -32100 段，避开 IPC 的 -3200x 段。

### 实现 / 产出

- `minimax_code/mcp/__init__.py`：包入口 + scope 说明。
- `minimax_code/mcp/protocol.py`：协议版本、26 个方法名常量、`REQUEST_METHODS` frozenset、JSON-RPC 标准码 + MCP 域码（-32100 段）。
- `minimax_code/mcp/types.py`：16 个领域模型——Implementation、Client/ServerCapabilities、TextContent/ImageContent/EmbeddedResource（+ Content union）、Tool/ToolAnnotations、CallToolResult、Resource/ResourceTemplate/ResourceContents、ReadResourceResult、Prompt/PromptArgument/PromptMessage、GetPromptResult、InitializeRequestParams/InitializeResult、List* 结果集。
- `tests/test_mcp_types.py`：10 个单元测试。

### 验证

- ✅ `ruff check`：All checks passed（修复了 PEP 604 union + 具体 ValidationError 两处）。
- ✅ `pytest tests/test_mcp_types.py`：**10 passed in 0.17s**。
- 覆盖：版本钉死、握手默认值、工具 schema 默认、content union 解析与 round-trip、未知类型拒绝、能力 extra 前向兼容、列表分页、请求/通知方法集分离。

### Commit

`feat(mcp): R3 add MCP protocol types and constants layer`

---

## R4 — MCP Transport + Client（连接外部 MCP server）

- **回合序号**：4 / 50+
- **所属阶段**：A
- **开始时间**：2026-07-18
- **状态**：✅ 完成

### 本轮目标

实现 stdio/in-process transport + JSON-RPC client 握手，让 MiniMax 能**连接外部 MCP server**。先写 transport 层（字节管道），再写 client（握手 + request/response 去复用 + 通知分发），最后用进程内成对 transport 验证全链路。完成后 R5 即可把外部 MCP 工具桥接进现有 `ToolRegistry`。

### 设计决策

- **Transport 抽象统一**：`MCPTransport` ABC 暴露 `start/send/messages/close` 四方法；`StdioTransport`（子进程 + 换行分隔 JSON）和 `InProcessTransport`（双 asyncio.Queue 成对管道）共享同一接口，client 完全 transport 无关。
- **请求/响应去复用**：client 后台一个 reader task，按 `id` 把 response 匹配到 `asyncio.Future`；通知（无 `id`）转发到可选 handler；传输关闭时把所有 pending future 失败，避免调用方挂死。
- **握手严格对齐 spec**：`initialize` → 校验 `InitializeResult` → 回发 `notifications/initialized` 通知；超时用 `asyncio.wait_for`，错误码经 JSON-RPC error 通道传播为 `MCPClientError`。
- **进程内成对 transport 是测试基石**：`make_in_process_pair()` 返回两个互连 transport，测试无需 spawn 子进程即可端到端驱动 client ↔ mock server。
- **统一现代化导入**：`AsyncIterator/Awaitable/Callable` 从 `collections.abc` 引入，`asyncio.TimeoutError` 用内置 `TimeoutError`（ruff UP035/UP041）。

### 实现 / 产出

- `minimax_code/mcp/transport.py`：
  - `MCPTransport`（ABC）、`MCPTransportError`。
  - `StdioTransport`：`asyncio.create_subprocess_exec` 拉起 server，stdin/stdout 走换行分隔 JSON；带队列的 `_read_loop` 读取 task，`None` 作流结束哨兵；`close()` 先 terminate 再 kill（best-effort）。
  - `InProcessTransport` + `make_in_process_pair()`：双 `asyncio.Queue` 成对管道。
- `minimax_code/mcp/client.py`：
  - `MCPClient`：`initialize()`（握手 + 启 reader）、`ping()`、`list_tools/call_tool/list_resources/read_resource/list_prompts`、`close()`（幂等，失败所有 pending）。
  - `MCPClientError`（协议层错误）。
  - 后台 `_reader_loop` 做 response 去复用 + notification 分发。
- `minimax_code/mcp/__init__.py`：导出 transport/client 全部公共符号。
- `tests/test_mcp_client.py`：9 个端到端测试（用 `make_in_process_pair` + mock server）。

### 验证

- ✅ `ruff check`：All checks passed（自动修复 7 处 UP035/UP041/I001 现代化导入）。
- ✅ `pytest tests/test_mcp_types.py tests/test_mcp_client.py`：**19 passed in 0.21s**（含 R3 的 10 + R4 的 9）。
- 覆盖：握手完成且记录 server_info、list_tools 返回工具、call_tool 文本内容、ping 存活、服务端 -32601 错误传播为 MCPClientError、通知转发到 handler、close 后请求快速失败、服务端静默时请求超时、close 幂等。
- ⏭️ R5 进入 MCP registry：把外部 MCP 工具桥接进现有 `ToolRegistry`，让 agent 能调用任意 MCP server 的工具。

### Commit

`feat(mcp): R4 add MCP transport (stdio + in-process) and JSON-RPC client`

---

## R5 — MCP Registry（外部工具桥接进 ToolRegistry）

- **回合序号**：5 / 50+
- **所属阶段**：A
- **开始时间**：2026-07-18
- **状态**：✅ 完成

### 本轮目标

让 MiniMax agent 能**调用任意外部 MCP server 的工具**。实现 `MCPRegistry`：管理多 server 连接，把每个 server 的工具包装成 `Tool` 注册进现有 `ToolRegistry`，命名空间隔离，fail-open 启动。完成后 agent 对话循环无需改动即可看到并调用 MCP 工具（通过 `registry.dispatch`）。

### 设计决策

- **命名空间隔离**：桥接工具命名 `mcp__<server>__<tool>`，双下划线前缀 + server/tool token 清洗（`[a-z0-9_]`），绝不与内置工具冲突。
- **适配现有契约**：`_BridgedTool(Tool)` 直接复用 `ToolRegistry.register/dispatch`；`parameters` 取自 MCP `inputSchema`（补 `type/properties` 默认），`run` 转发到 `MCPClient.call_tool`。对话循环零改动。
- **CallToolResult → ToolResult 转换**：text content 拼成 `output["text"]`，image/resource 进 `output["content"]`；`isError=True` → `ToolResult.fail(error=首条 text)`，metadata 带 server/tool/isError。
- **fail-open 三层**：握手失败、list_tools 失败、工具名冲突 —— 一律 log + 跳过，**永不炸 agent 启动**。
- **测试可注入**：`add_server`（建 StdioTransport）与 `_attach_client`（握手+桥接）拆分，测试用进程内 transport pair 注入 client，无需 spawn 子进程。
- **不做的事（YAGNI）**：本轮不接 IPC handler（R9 统一做）、不持久化 server 配置（R9 迁移）、不做资源/prompt 桥接（只桥接 tools，最高频）。

### 实现 / 产出

- `minimax_code/mcp/registry.py`：
  - `bridged_name()`、`_sanitize()` 命名工具。
  - `MCPServerConfig`（name/command/env/cwd/enabled dataclass）。
  - `_BridgedTool(Tool)`：MCP 工具 → MiniMax 工具适配器。
  - `_to_tool_result()`：CallToolResult → ToolResult 转换器。
  - `MCPRegistry`：`add_server`/`_attach_client`/`remove_server`/`add_many`/`shutdown`/`is_connected`/`list_servers`。
  - `_ServerConn` 连接记录。
- `minimax_code/mcp/__init__.py`：导出 registry 全部公共符号。
- `tests/test_mcp_registry.py`：8 个端到端测试（进程内 mock server）。

### 验证

- ✅ `ruff check`（仅 R5 文件）：All checks passed（自动修复 test 的 import 排序 + 删未用 pytest）。
- ✅ `pytest tests/test_mcp_registry.py + client + types`：**27 passed in 0.99s**。
- ⚠️ 边界发现：`ruff check tests/`（全目录）暴露大量**项目历史遗留**问题（smoke_*.py / test_handler_utils.py 等老文件的 UP041/I001）——**非 R5 引入**，严格按"回合独立"红线不顺手修，留待后续专门清理回合。
- 覆盖：命名清洗、attach 桥接、dispatch 转发、isError→fail、remove 注销、冲突跳过、list_servers 状态、disabled 跳过。
- ⏭️ R6 进入 Hooks：文件发现的 JSON 生命周期钩子（session_start/pre_tool_use/post_tool_use/session_end），与现有 permission 协同。

### Commit

`feat(mcp): R5 add MCP registry bridging external tools into ToolRegistry`

---

## R6 — Hooks：生命周期钩子数据层 + 执行器（阶段 A）

- **阶段**：A（平台内核）
- **日期**：2026-07-18
- **状态**：✅ 已完成并提交

### 本轮目标

落地三大平台支柱之二的**生命周期钩子（Hooks）**的数据层与执行器。融合 Grok `xai-grok-hooks` crate 的理念与 Claude-Code hooks 社区惯例（JSON 文件、按事件分组、stdin 上下文、stdout JSON 决策），形成文件发现、子进程执行、fail-open 的 4 事件钩子系统。本轮聚焦：类型 + 注册表 + 执行器；下一轮（R7）再绑定到 AgentCore 生命周期并接入 IPC。

### 设计决策

- **四事件模型**：`session_start` / `pre_tool_use` / `post_tool_use` / `session_end`。仅 `pre_tool_use` 可阻塞（唯一 gate 事件），其余纯通知。
- **fail-open 铁律**：钩子 spawn 失败、超时、退出非 0、stdout 非 JSON —— 一律记录到 `HookExecutionResult` 但**绝不抛异常**。Agent 永远能继续；只有显式 `{"block": true}` 才真正阻断。这保护了 agent 启动与对话循环的鲁棒性。
- **JSON 契约兼容社区**：`load_dict` 同时接受 Claude 风格 `{"hooks": {<event>: [...]}}` 与裸 `{<event>: [...]}`，最大化复用既有社区 hook 脚本。
- **stdin/stdout 协议**：钩子通过 stdin 收到 `HookContext`（JSON：event/session_id/tool_name/tool_input/tool_output），通过 stdout 输出 `HookDecision`（block/block_reason/suppress_tool）。
- **容错解析**：`_parse_decision` 容忍 stdout 前导日志行（取最后一个 `{...}` JSON 对象），对社区脚本友好。
- **StrEnum 升级**：`HookEvent(StrEnum)` 而非 `(str, Enum)`，遵循 ruff UP042 + Python 3.11+ 项目惯例。
- **与 permission 协同定位**：本轮 hook 是"保护性 gate/通知"，与 `permission.*`（同意弹窗）正交叠加，不替换 —— R7 绑定时会明确两者执行顺序（hook 先于/后于 permission，待 R7 定）。
- **YAGNI**：不做 hook 链的短路聚合（pre_tool_use 顺序执行、首个 block 即停，足够）；不做 hook 修改 tool_input（`suppress_tool` 字段预留但本轮执行器不消费，留给 R7）。

### 实现 / 产出

- `minimax_code/hooks/types.py`：`HookEvent`(StrEnum)、`TOOL_EVENTS`、`HookMatcher`（glob/list glob）、`HookConfig`、`HookContext`、`HookDecision`。全部 `ConfigDict(extra="allow")` 前向兼容。
- `minimax_code/hooks/registry.py`：`HookRegistry.add/load_dict/load_file/for_event/all/count/clear`。`for_event` 按 tool_name glob 过滤；tool 事件缺 matcher 默认 match-all。
- `minimax_code/hooks/executor.py`：`HookExecutor.run()` 子进程执行 + `HookExecutionResult` + `_parse_decision()`。`asyncio.wait_for` 超时 → kill + wait，返回 `timed_out=True`。
- `minimax_code/hooks/__init__.py`：统一导出 + `HOOK_EVENTS` 便利列表。
- `tests/test_hooks.py`：12 个测试（注册表 6 + 执行器 6），执行器测试 spawn 真实 `python -c` 子进程覆盖 block 决策解析、空输出、超时 fail-open、spawn 失败 fail-open、非零退出、容错前导日志。

### 验证

- ✅ `ruff check minimax_code/hooks tests/test_hooks.py`：All checks passed（自动修复 I001 排序 + 删未用 import asyncio/pytest/HookDecision；手动升级 StrEnum）。
- ✅ `pytest tests/test_hooks.py + mcp 三件套回归`：**39 passed in 1.77s**（hooks 12 + mcp registry 8 + client 9 + types 10）。
- 覆盖：Claude 风格加载、裸映射、未知事件跳过、glob 过滤（单/多/全匹配）、文件 round-trip、block 解析、空输出无决策、超时 fail-open、spawn 失败 fail-open、非零退出、前导日志容错。
- ⏭️ R7：把 `HookExecutor`/`HookRegistry` 绑定到 AgentCore 生命周期（session 开始/结束 + 每次 tool dispatch 前后），接入 `hooks.*` IPC 命名空间，并确定 hook 与 permission 的执行顺序。

### Commit

`feat(hooks): R6 add lifecycle hooks data layer, registry, and fail-open executor`

---

## R7 — Hooks 绑定 AgentCore 生命周期（阶段 A）

- **阶段**：A（平台内核）
- **日期**：2026-07-18
- **状态**：✅ 已完成并提交

### 本轮目标

把 R6 的 `HookRegistry`/`HookExecutor` 真正**绑到 AgentCore 的生命周期**。引入 `HookManager` 门面聚合决策，并在工具调度路径 `_dispatch_tool` 注入 `pre_tool_use`（gate）/`post_tool_use`（通知）两个切点。让钩子从"独立模块"升级为"对话循环一等公民"，同时**零侵入**：`hooks=None` 时 core 行为与 R6 完全一致。

### 设计决策

- **门面模式（HookManager）**：AgentCore 不直接碰 registry/executor，只调 `fire_pre_tool_use` → 拿一个 `PreToolOutcome`（聚合所有匹配 hook 的裁决）。`PreToolOutcome.first-block-wins`：首个 `{"block": true}` 即短路，后续 hook 不跑，避免无谓执行 + 决定性强。
- **最小侵入 + 向后兼容**：`AgentCore.__init__` 加可选 `hooks: HookManager | None = None`；`_dispatch_tool` 两处注入都用 `if self.hooks is not None` 守卫。现有 16 个 agent_core 测试无需改动全绿 —— 证明 `None` 路径零开销零行为漂移。
- **permission 优先于 hook**：`pre_tool_use` gate 注入在 permission `ask`/`deny` 分支**之后**。即被 permission 拒绝的工具**绝不浪费一次 hook 子进程**。顺序：JSON args 解析 → permission(deny/ask) → pre_tool_use hook → 真实 dispatch → post_tool_use hook → audit。
- **session 引用**：`run()` 开头 `self._current_session_id = session_id`，`_dispatch_tool` 读它。不复用 `_audit_session_id`（语义不同：audit 是外部按 session 设置，hook 需要每轮当前值）。
- **fail-open 双层**：executor 层（R6）已兜底 spawn/timeout/garbage；manager 层再 `except Exception` 兜底 executor 自身异常 —— hook 永远不可能让对话循环崩。
- **block 语义映射**：被 hook 阻断时返回 `ToolResult.fail(block_reason, permission="hook_block")` + emit `hook_blocked` 状态 + audit `status="blocked"`，与现有 permission_deny 路径对称（前端可统一处理 gate 类拒绝）。
- **session_start/end 定位**：本轮 `fire_session_start/end` 作为 manager 方法暴露，但**未**自动接 `run()`（run 是"每轮"非"session 起止"）。真实接线留 R10 集成（在 session handler/app 生命周期调用）。pre/post_tool_use 已自动生效 —— 这是确定性最高、价值最大的两个事件。
- **YAGNI**：不消费 `suppress_tool`（字段预留，R8+ 视需求）；不做 hook 修改 tool_input；不做 hook 链并行（串行 + first-block-wins 足够，且串行保证 hook 间因果可观测）。

### 实现 / 产出

- `minimax_code/hooks/manager.py`：`HookManager`（4 个 `fire_*` + `load_hooks`/`load_hooks_file`）+ `PreToolOutcome`（results/blocked/block_reason/should_run）。`_fire` 内部统一 fail-open。
- `minimax_code/hooks/__init__.py`：导出 `HookManager`、`PreToolOutcome`。
- `minimax_code/agent/core.py`（6 处最小侵入）：
  1. `from ..hooks import HookManager`
  2. `__init__` 加 `hooks: HookManager | None = None`
  3. 字段 `self.hooks` + `self._current_session_id`
  4. `run()` 设 `self._current_session_id = session_id`
  5. `_dispatch_tool` permission 后注入 `pre_tool_use` gate（block → `ToolResult.fail` + `hook_blocked` 状态 + audit `blocked`）
  6. `_dispatch_tool` dispatch 后注入 `post_tool_use` 通知（fail-open try/except）
- `tests/test_hooks_manager.py`：8 个测试 —— 无 hook 不 block、block 决策、first-block-wins 短路、pass hook、matcher 跳过无关工具、post 通知、session 起止、crash fail-open。
- `tests/test_hooks_integration.py`：4 个 AgentCore 集成测试（直驱 `_dispatch_tool`）—— 无 hook 行为不变、block 短路工具不执行、pass+post 触发、permission deny 优先于 hook。

### 验证

- ✅ `ruff check`（hooks 包 + core.py + 3 测试文件）：All checks passed（修了 2 个：test 未用 `HookEvent` import、`%` 格式化 → f-string）。
- ✅ `pytest test_hooks + test_hooks_manager + test_hooks_integration + test_agent_core`：**40 passed in 4.72s**（hooks 12 + manager 8 + integration 4 + **agent_core 16 回归全绿**）。
- 关键回归证明：core.py 6 处改动对 `hooks=None` 路径零影响 —— 16 个既有 agent_core 测试（含 cancellation、tool dispatch、permission、compaction）无一失败。
- ⏭️ R8 进入 Plugins：插件清单（manifest）+ 加载器（文件发现 + 沙箱边界），三大平台支柱之三。

### Commit

`feat(hooks): R7 bind HookManager into AgentCore lifecycle with pre/post_tool_use gates`

---

## R8 — 插件清单 + 加载器 + 注册表（阶段 A）

- **阶段**：A（平台内核）
- **日期**：2026-07-19
- **状态**：✅ 已完成并提交

### 本轮目标

落地**三大平台支柱之三 —— Plugins**：一个插件就是一个带 `plugin.json` 清单的目录，声明它要贡献什么（生命周期 hooks / MCP servers / 权限）。本轮交付数据层：`PluginManifest` 模型 + `PluginLoader`（目录递归发现 + fail-open 解析）+ `PluginRegistry`（索引 + 把贡献"灌"进 `HookRegistry`）。让"装一个插件就自动获得它的钩子"成为现实，且**一个坏插件绝不拖垮其余插件**。

### 设计决策

- **声明式 bundle**：插件 = 目录 + JSON 清单。`PluginManifest` 字段沿用既有子系统格式，插件是**薄声明包装**而非新协议：`hooks` 直接是 Claude 风格 `{<event>: [spec]}`（与 `HookRegistry.load_dict` 同构）、`mcp_servers` 复用 MCPServerConfig dict、`permissions` 是字符串列表。零学习成本，最大复用。
- **forward-compat（extra="allow"）**：manifest 解析允许未知键（`ConfigDict(extra="allow")`）。新版插件加字段不会让旧 agent 拒绝加载 —— 平台型产品的兼容性铁律。
- **fail-open 加载链**：`PluginLoader._load_one` 对 unreadable JSON / 非 dict 根 / schema 校验失败三种情况，**不抛异常**，而是返回一个带 `error` 字段的 `Plugin`。`discover` 对整个目录树失败隔离 —— 一个坏清单不影响兄弟插件的发现。
- **name 兜底**：清单缺 `name` 或读不出时，回退到父目录名。即使最坏情况，插件也始终有可标识的 name，registry 能报告它失败。
- **Plugin 记录分离 ok/error**：`Plugin.ok` 属性区分"健康"与"加载失败"，registry 用 `failed()` / `enabled()` 分别过滤 —— 前端可在 UI 里列出坏插件供用户排查。
- **apply_hooks 贡献桥**：`PluginRegistry.apply_hooks(HookRegistry)` 把所有 `enabled()` 插件的 hooks 灌进 agent 活跃的 hook 表。**disabled / 无 hooks / failed 的插件一律跳过** —— 用户在清单里 `"enabled": false` 即可停用，无需删目录。这把"插件 hooks → agent 活跃"压缩成一次调用。
- **YAGNI**：`entry`（Python 模块动态导入）字段本轮**声明但不执行**（动态 import 是沙箱安全的大坑，留 R11 沙箱回合）。`mcp_servers` 贡献本轮也只建模不接线（MCP 是 async 子进程，留 R9 IPC 回合统一接）。

### 实现 / 产出

- `minimax_code/plugins/manifest.py`：`PluginManifest`（name/version/description/author/homepage/enabled + hooks/mcp_servers/permissions/entry）+ `normalized_hooks()` 适配 HookRegistry 入参。
- `minimax_code/plugins/loader.py`：`Plugin` dataclass（manifest/path/loaded_at/error/metadata + ok/name 属性）+ `PluginLoader`（`discover` 递归 rglob + 排序、`discover_many` 多根去重、`load_dir`/`load_file` 单点加载、`_load_one` fail-open 三态兜底）。
- `minimax_code/plugins/registry.py`：`PluginRegistry`（add/add_many 去重、remove/clear、get/has/list/names/count、failed/enabled 过滤、`apply_hooks` 贡献桥）。
- `minimax_code/plugins/__init__.py`：统一导出 5 个符号。
- `tests/test_plugins.py`：13 个测试 —— manifest 默认值/extra 键、单插件发现、嵌套排序、坏 JSON fail-open、坏 schema 记录错误、缺根返回空、load_dir 直载、registry 去重/移除、add_many 计数、failed/enabled 过滤、apply_hooks 灌入 HookRegistry（含 disabled+empty 跳过）。

### 验证

- ✅ `ruff check minimax_code/plugins/ tests/test_plugins.py`：All checks passed（删未用 `Field` + 未用 `pytest` import）。
- ✅ `pytest test_plugins + test_hooks×3`：**37 passed in 2.59s**（plugins 13 + hooks 12 + manager 8 + integration 4）。
- 覆盖：声明式 bundle 解析、目录递归发现、fail-open 三态（unreadable/non-dict/invalid-schema）、多根去重、贡献桥把插件 hooks 灌成 agent 活跃 hooks、enabled 闸门。
- ⏭️ R9：Plugins IPC + 持久化迁移 —— `plugins.*` 命名空间（list/enable/disable/info）+ 3 文件契约同步（protocol.py / ipc.ts / ipc-contract.md）+ `MINIMAX_CODE_PLUGINS_DIR` 配置 + app 启动时自动发现并 apply_hooks。

### Commit

`feat(plugins): R8 add plugin manifest, fail-open loader, and hook-contributing registry`

---

## R9 — 插件 IPC 命名空间 + 配置接入 + 运行时启停（阶段 A）

- **阶段**：A（平台内核）
- **日期**：2026-07-19
- **状态**：✅ 已完成并提交

### 本轮目标

把 R8 落地的 `PluginRegistry` **暴露成 `plugins.*` IPC 命名空间**，让前端能列出/查看/启用/禁用/热重载插件，并完成**启动时自动发现 + 配置接入**（`MINIMAX_CODE_PLUGINS_DIR`）。至此**三大平台支柱（MCP / Hooks / Plugins）全部对前端可见**，平台型产品的"可观测、可控制"闭环成立。本轮交付：5 个 IPC 方法 + 注册表运行时启停能力 + app 单例接入 + 4 文件契约同步 + 7 个测试。

### 设计决策

- **运行时覆盖（in-memory only）**：`Plugin.runtime_enabled: bool | None`，`None` 表示"遵循清单"。`effective_enabled` 属性 = 单一事实来源（运行时覆盖优先于 manifest）。覆盖仅存内存 —— "重启回到磁盘真相"。持久化留后续轮次（YAGNI）。
- **权威启停定义对齐**：`registry.enabled()` 要求 `p.ok AND effective_enabled` —— 一个坏插件即使清单标志为 True 也永远不会"活跃"。`plugins.list` 的 `enabled_only` 过滤器必须镜像这一语义（`i["enabled"] and i["ok"]`），否则会把 `ok=False` 的插件漏进"活跃"列表。这是本轮发现并修复的真实语义 bug。
- **fail-open 单例**：`ensure_plugin_registry()` 包住磁盘发现 —— 发现链任何环节抛异常都返回**空注册表**，agent 永远能启动（已用 import smoke 验证 `registry count = 0`）。
- **延迟工厂闭包**：handlers 通过 `_make_registry_factory(registry)` 闭包解析注册表 —— 注入非 None 则直接用（测试路径），None 则首次调用惰性 `ensure_plugin_registry()` 并缓存。避免顶层循环依赖（`app.py` 在 `register_app_handlers` 里 import 本模块）。
- **新错误代码 `NOT_FOUND = -32006`**：`protocol.py` 仅含信封类型 + 错误码（无方法枚举），平台支柱需要"资源未找到"语义（插件不存在）。落在 JSON-RPC 应用定义服务器错误范围（-32000~-32099）内，前向兼容（新常量）。
- **reload 重置覆盖**：`reload` = `discover_plugins() → clear() → add_many()`。新 Plugin 对象 `runtime_enabled=None` → 设计上"reload 即回到磁盘真实状态"。
- **配置接入走环境变量模式**：与既有 `_default_skills_root()` 同构 —— `_default_plugins_root()` 读 `MINIMAX_CODE_PLUGINS_DIR`，不扩展 `Config` 类。app 单例三件套（`get_/set_/ensure_plugin_registry`）+ `discover_plugins()` + `register_plugin_handlers(server)` + `__all__` 导出。
- **轮次独立**：app.py / protocol.py 中既有的 ruff 遗留问题（I001/F401）非本轮引入，不动；前端 `client-pending-mode.test.ts:415` 的 `JsonRpcId` 既有 tsc 错误与 plugins 无关，不动。

### 实现 / 产出

- `minimax_code/plugins/loader.py`：`Plugin.runtime_enabled: bool | None = None` 字段 + `effective_enabled` 属性（运行时覆盖优先于 manifest）。
- `minimax_code/plugins/registry.py`：`_effective_enabled` 委托到 Plugin 记录（DRY，单一事实来源）+ `set_enabled(name, bool)` 方法（未知 name 返回 False no-op）。
- `minimax_code/ipc/protocol.py`：新增 `NOT_FOUND = -32006`（应用定义服务器错误范围）。
- `minimax_code/ipc/handlers_plugins.py`（新）：5 个 IPC 方法 —— `plugins.list`（带 `include_failed` / `enabled_only` 过滤）、`plugins.info`、`plugins.enable` / `plugins.disable`（运行时覆盖）、`plugins.reload`（重发现 + 重建索引）。`_to_info(plugin)` 序列化 15 字段（含 `enabled`(=effective_enabled) / `enabled_on_disk`(=manifest) / `ok` / `error` / `has_hooks` / `has_mcp` / `has_permissions`）。延迟工厂闭包解析 registry。
- `minimax_code/app.py`：全局 `_PLUGIN_REGISTRY` + `_default_plugins_root()`（读 `MINIMAX_CODE_PLUGINS_DIR`）+ `discover_plugins()`（fail-open，吞异常返回空列表）+ 单例三件套（`get_/set_/ensure_plugin_registry`，init 包 try/except 返回空 registry）+ `register_app_handlers` 注册 `register_plugin_handlers(server)` + 日志计数 `+ 5 plugins.*` + `__all__` 导出 4 符号。
- `web/src/types/ipc.ts`：`PluginInfo`（15 字段对齐 `_to_info`）+ `ListPluginsParams` / `ListPluginsResult` / `PluginInfoResult` / `PluginToggleResult` / `PluginReloadResult`。
- `web/src/ipc/client.ts`：`TypedIPC` 接口 + `bindTypedIPC` 5 绑定 + `mockPlugins`（2 mock 插件：健康启用 + 坏插件）+ `mockHandle` 5 案例（enable/disable 翻转 `enabled`，reload 返回重新加载计数）。覆盖 mock backend 完整性铁律。
- `docs/ipc-contract.md`：方法表新增 `plugins.*` 行。
- `tests/test_handlers_plugins.py`（新）：7 个测试 —— list 全量带状态 / list 双过滤 / info 已知 / info 未知 NOT_FOUND / enable+disable 翻转运行时覆盖（断言 manifest 不变）/ enable 未知 NOT_FOUND / reload 重建索引（monkeypatch `app.discover_plugins`）。

### 验证

- ✅ `ruff check handlers_plugins.py + test_handlers_plugins.py`：All checks passed（修了 UP035 `Callable` 须来自 `collections.abc` + I001 导入排序）。
- ✅ `pytest test_handlers_plugins + test_plugins`：**20 passed in 0.73s**（R9 新 7 + R8 回归 13）。
- ✅ 后端 import smoke：`import OK, NOT_FOUND = -32006` / `registry count = 0`（fail-open 空注册表工作正常）。
- ✅ 前端 `tsc --noEmit`：仅 1 个**既有无关错误**（`client-pending-mode.test.ts:415` 的 `JsonRpcId` null 赋值，非 plugins、非本轮引入）；client.ts/ipc.ts 改动编译干净。
- 覆盖：5 个 IPC 方法端到端、运行时覆盖语义（manifest 不变）、reload 重建索引、fail-open 单例、双过滤语义对齐 `registry.enabled()`。
- ⏭️ R10：阶段 A 集成验证 —— 把 `fire_session_start/end` 接到会话/应用生命周期、把插件 hooks 接到活跃 agent、三大支柱端到端打通。

### Commit

`feat(plugins): R9 expose plugin registry via plugins.* IPC namespace`

---

## R10 — 三大支柱端到端打通（阶段 A 收官）

- **阶段**：A（平台内核）— **收官回合**
- **日期**：2026-07-19
- **状态**：✅ 已完成并提交

### 本轮目标

把 R1-R9 分头落地的 **MCP / Hooks / Plugins 三大平台支柱**接成**一个运行中的系统**：进程级 `HookManager` 单例 + 启动时把所有启用插件的 hooks 灌进去（`apply_hooks`）+ 主 agent（`agent.send_message`）复用单例并 fire `session_start`/`session_end` 生命周期。至此"装一个带 hook 的插件 → agent 调度工具 → hook 生效"零接线成本，平台型产品的**内核闭环**成立。本轮交付：HookManager 单例三件套 + 主 agent 接线（hooks 注入 + 生命周期 fire）+ 4 个真实子进程端到端测试。**阶段 A（R1-R10）完成。**

### 设计决策

- **集成 seam = `ensure_hook_manager()`**：进程级单例，构建一次后 `registry.apply_hooks(manager.registry)` 把所有启用插件的 hooks 灌进同一个 `HookManager`。从此**任何复用该单例的 `AgentCore`** 都自动获得插件 hooks —— "一次构建，处处生效"。这是三大支柱从孤岛变系统的关键粘合剂。
- **三层 fail-open**：discover 失败 → 空 registry → manager 仍建好；apply 失败 → 吞异常 → 空 hooks manager；fire 失败 → 吞异常 → 主流程不阻塞。agent 永远能启动、永远能跑完一轮（与 `_maybe_open_db` / `ensure_plugin_registry` 同防御姿态）。
- **session 生命周期对称接线**：`fire_session_start` 在 `core.run()` 前，`fire_session_end` 在 `finally`（异常路径也触发）。hook 是通知类（不返决策），fail-open 不阻塞 turn。语义：每次 agent run = 一次会话活跃周期，plugin 的 session_start/end hook 在此激活。
- **单例缓存复用**：`builtins.py` 每次 `send_message` 调 `ensure_hook_manager()` —— 单例已建则 O(1) 返回，避免每轮重 apply。AgentCore 本身仍 per-request 新建（承载 callbacks/state），但 hooks 单例共享。
- **lazy import 防循环**：`ensure_hook_manager` 内部 `from .hooks import HookManager`（app.py 顶层不 import hooks）；`builtins.py` 函数内 `from ..app import ensure_hook_manager`。两处 lazy import 保证模块加载顺序无关，import smoke 验证 `hook_manager OK, total hooks = 0`。
- **YAGNI**：session_start/end 本轮接 per-run（每次 send_message 触发），不追踪"会话是否首次"跨消息状态 —— 简单且 hook 立刻可用。语义在文档说明，后续轮次按需精化。
- **轮次独立**：app.py 既有 I001（handler import 块未排序）非本轮引入，不碰；前端既有的 `JsonRpcId` tsc 错误与 R10 无关，不碰。

### 实现 / 产出

- `minimax_code/app.py`：全局 `_HOOK_MANAGER` 单例槽 + `get_/set_/ensure_hook_manager` 三件套（构建时 `ensure_plugin_registry().apply_hooks(manager.registry)`，三层 try/except fail-open，日志报告贡献 hook 数）+ `__all__` 导出 3 符号。
- `minimax_code/ipc/builtins.py`：主 agent AgentCore 构造传 `hooks=hook_manager`（fail-open 解析，None-safe）+ `core.run()` 前 `fire_session_start` / `finally` 里 `fire_session_end`（两处 try/except fail-open，异常路径也触发 session_end）。
- `tests/test_integration_platform.py`（新）：4 个**真实子进程**端到端测试 —— (1) apply 桥（plugin.json hook → ensure_hook_manager → registry.count==1）；(2) pre_tool_use 决策回路（plugin block decision → `fire_pre_tool_use` → outcome.blocked==True，`sys.executable` 跑 inline Python 输出 JSON decision）；(3) session_start 真实触发（plugin hook 写 marker 文件，env 传路径跨平台）；(4) fail-open（坏 manifest → 空 manager，不崩溃）。autouse fixture 每测 reset 两个单例。

### 验证

- ✅ `ruff check builtins.py + test_integration_platform.py`：All checks passed（修了 test 的 I001 import 排序）。
- ✅ `pytest test_integration_platform + test_plugins + test_hooks_integration + test_hooks_manager`：**29 passed in 2.37s**（R10 新 4 + plugins 13 + hooks_integration 4 + hooks_manager 8）。
- ✅ 后端 import smoke：`hook_manager OK, total hooks = 0`（无插件目录 fail-open 空 manager）+ `__all__` 导出 `ensure_hook_manager`/`ensure_plugin_registry` 均为 True + 无循环依赖。
- 覆盖：插件 manifest → discover → apply → HookManager registry → 子进程执行 → JSON decision 解析 → block 回路；session_start 真实副作用；坏 manifest fail-open；三大支柱（MCP/Hooks/Plugins）通过 ensure_hook_manager 单例合流。
- 🎉 **阶段 A（R1-R10）完成**：融合地基 + 三大平台支柱 + 端到端打通。
- ⏭️ R11（阶段 B 起点）：安全与可观测 —— 沙箱（plugin entry 动态 import 的安全执行边界）/ 检查点 / 遥测。

### Commit

`feat(platform): R10 wire plugins+hooks into agent lifecycle via ensure_hook_manager singleton`

---

## R11 — 遥测引擎：内存事件总线 + 三层脱敏（阶段 B 起始）

- **阶段**：B（安全与可观测）— **起始回合**
- **日期**：2026-07-19
- **状态**：✅ 已完成并提交

### 本轮目标

构建 `minimax_code/telemetry/` 遥测引擎子系统，深度融合 grok-build 的 telemetry 设计理念（`TelemetryEvent` 特征 + `redact_common` 三层脱敏）到 Python 架构。**内存中、fail-open、零侵入**的事件总线：会话生命周期、工具分发、hook fire、permission 决策。与既有 `audit_log`（磁盘持久化、仅工具分发）**解耦** —— 平行通道，不耦合、不重复造轮子。本轮交付：6 个 telemetry 模块（events/redact/ringbuffer/metrics/engine/__init__）+ app.py 单例三件套 + builtins.py 生命周期接线 + core.py 工具分发镜像 + 3 个只读 IPC handler + 17 单测。**阶段 B（R11-R20）开启。**

### 设计决策

- **关注点分离**：`audit_log`（磁盘持久化、仅工具分发、`handlers_audit`）vs `TelemetryEngine`（内存实时、全事件类型）—— 平行通道非耦合。audit 已覆盖磁盘审计，R11 是用**内存事件总线**升级实时可观测性，而非重复造轮子。两者时间戳共用 `now_iso()`，时间线可 join。
- **三层脱敏**（移植 grok `redact_common`）：secret-shape scrub（`sk-…`/`Bearer …`/`key=value`）→ user-path collapse（home→`~`）→ URL-origin reduction（scheme://host[:port]）。`redact_value` 是**单一隐私 chokepoint**，每字节进 ring buffer / metrics 前必过；返回新结构，输入永不 mutate。
- **跨平台分隔符无关**：`redact_paths` 同时匹配 `/` 和 `\`，Unix 路径在 Windows 上也能脱敏（payload 来源混杂 —— 工具参数、错误信息、配置）。这是测试驱动发现的硬伤修复。
- **app.py 单例三件套**（与 R10 `hook_manager` 完全对齐）：`_TELEMETRY_ENGINE` 槽 + `get_/set_/ensure_telemetry_engine`，lazy import 防循环。`ensure` 加 **env 开关 `MINIMAX_CODE_TELEMETRY`**（`0`/`false`/`off`/`no`）表达"disabled" —— 否则 `set(None)` 后 `ensure` 又重建，无法真正关闭；沙箱禁可观测时返回 `None` 不缓存。
- **fail-open 契约**："遥测永不破坏 agent" —— `emit()` 包 try/except 只 `logger.debug`；`ensure` 失败返回 None；builtins/core 所有 emit 点都包 try/except。与 R10 三层 fail-open 同防御姿态。
- **会话生命周期对称**（与 R10 hook fire 对齐）：`core.run()` 前 emit `SESSION_START`，`finally` 块 emit `SESSION_END`（异常路径也触发）。
- **工具分发镜像**：`_record_audit` 在 dao 持久化**前**向引擎 emit `TOOL_CALL`（含脱敏参数/权限/状态/exit_code/duration/error）—— 实时观测独立于磁盘持久化，两通道并行不阻塞。
- **StrEnum（Python 3.11+）**：`EventType`/`Severity` 用 `enum.StrEnum` 替代 `str + Enum`（ruff UP042）。成员 `==` 字符串值，过滤 API 既接受枚举也接受 `"tool_call"` 字符串。
- **有界内存**：`RingBuffer`（`deque(maxlen)` + `threading.Lock`，热路径同步）+ `MetricsRegistry`（`OrderedDict` LRU 驱逐 `max_sessions=64`）+ latency 滑窗（最近 200 样本 p50/p95）—— 长跑进程内存不无限增长；`_GLOBAL` 模块级单例记跨会话 severity（驱逐不丢）。
- **YAGNI**：`EventType` 闭环枚举只列 MiniMax 当前真正 emit 的 10 种（session 生命周期/turn/tool/hook/permission/plugin/error），grok 的 doom-loop/trace-upload 等待相应子系统存在再加入。
- **轮次独立**：不动 app.py 既有 I001；不碰前端既有 tsc 错误；不修复无关遗留。

### 实现 / 产出

- `minimax_code/telemetry/events.py`：`EventType`（10 值 StrEnum）+ `Severity`（INFO/WARN/ERROR StrEnum）+ `TelemetryEvent`（BaseModel，`ConfigDict(extra="allow")`，`ts` 默认 `now_iso()`）。
- `minimax_code/telemetry/redact.py`：`redact_secrets`（3 模式，key 后 `["']*` 匹配 JSON 引号）/ `redact_paths`（home→`~`，`/`+`\` 分隔符无关）/ `url_origin`（scheme://netloc）/ `redact_value`（递归 dict/list/tuple/str，返回新结构）。
- `minimax_code/telemetry/ringbuffer.py`：`RingBuffer`（`deque` + Lock，`append`/`recent`/`clear`/`__len__`/`capacity`，坏容量 ValueError）。
- `minimax_code/telemetry/metrics.py`：`SessionMetrics`（计数器 + latency 窗口，`as_dict` 折叠 p50/p95/max）/ `MetricsRegistry`（LRU 驱逐）/ `_GLOBAL` 跨会话 severity / `global_snapshot()`。
- `minimax_code/telemetry/engine.py`：`TelemetryEngine`（`emit`→脱敏→buffer+metrics，`recent` 按 type/session 过滤，`metrics` 快照，`clear`，`buffered_count`，fail-open）。
- `minimax_code/telemetry/__init__.py`：导出公共符号。
- `minimax_code/app.py`：`_TELEMETRY_ENGINE` 槽 + `get/set/ensure` 三件套（ensure 带 env 开关 + fail-open）+ `__all__` 导出 3 符号 + `register_app_handlers` 注册 telemetry handlers（统计串 `+ 3 telemetry.*`）。
- `minimax_code/ipc/builtins.py`：`telemetry_engine` 解析（fail-open）→ 注入 `core.telemetry_engine` + `SESSION_START`/`SESSION_END` 生命周期 emit（`fire_session_*` 后，`core.run` 前 / `finally`）。
- `minimax_code/agent/core.py`：`telemetry_engine` 字段 + `_record_audit` 镜像 emit `TOOL_CALL`（dao 持久化前，severity 随 error 状态）。
- `minimax_code/ipc/handlers_telemetry.py`（新）：`telemetry.recent`/`metrics`/`clear` 三个只读+管理 handler（None-safe 降级 `enabled: False`，无 DAO 工厂，亚毫秒）。
- `tests/test_telemetry.py`（新，13 测）：redact 三层 + 递归遍历 + RingBuffer 容量/recency/clear/坏容量 + MetricsRegistry 计数/latency/LRU 驱逐 + Engine emit→脱敏→buffer→metrics/过滤/fail-open。
- `tests/test_handlers_telemetry.py`（新，4 测）：recent/metrics/clear + env-disabled 降级（`_CapturedReply` 仿 ctx，注入真引擎）。

### 验证

- ✅ `ruff check telemetry/ + app.py + handlers_telemetry + builtins + core + 2 test`：**All checks passed**。
- ✅ `pytest test_telemetry + test_handlers_telemetry`：**17 passed in 0.80s**。
- ✅ import smoke：`engine: TelemetryEngine` 构建成功，全链路 import（app/builtins/handlers_telemetry/core/telemetry）无循环依赖。
- 🐛 **测试驱动修复的 5 个真实 bug**（这正是单测的价值）：
  1. `redact` 正则漏匹配 JSON `"password": "…"`（key 后引号未处理）→ key 与 `[:=]` 间加 `["']*`。
  2. `redact_paths` 用 `os.sep` → Windows 上 Unix 路径（`/home/bob/x`）永不脱敏 → 改为 `/`+`\` 分隔符无关。
  3. `ensure_telemetry_engine` 永远重建 → 加 env 开关 `MINIMAX_CODE_TELEMETRY`，`set(None)`+env=0 才真禁用。
  4. redact 测试用硬编码 `/Users/alice`（与真实 home 不符）→ monkeypatch `_home` 跨平台可移植。
  5. `Severity`/`EventType` `str + Enum` → `StrEnum`（ruff UP042，Python 3.11+ 原生）。
- 覆盖：脱敏三层 + 递归遍历不 mutate + RingBuffer 容量丢弃 + MetricsRegistry LRU 驱逐 + Engine 全链路（emit→redact→buffer→metrics）+ fail-open（buffer 爆炸不抛）+ handler 降级 + env-disabled。
- ⏭️ R12（阶段 B 继续）：沙箱（plugin entry 动态 import 的安全执行边界）/ 检查点 / 令牌估算 / 压缩 / 崩溃处理程序。

### Commit

`feat(platform): R11 telemetry engine — in-memory event bus with three-layer redaction`

## R12 — 崩溃检测 + 孤儿 run 恢复 + 遥测接入（阶段 B 第 2 轮）

**本轮目标**：聚焦 grok-build 可靠性理念里 MiniMax 完全缺失、价值最高
的生命线——进程意外死亡（OOM / segfault / `kill -9`）后，in-flight 的
agent run 永远卡在 `running`，前端时间线假死。融合 grok-build 的
`xai-crash-handler`（分离 crate + `install()` 入口 +
`check_previous_crash()` 下次启动）+ `cleanup_stale_sessions`
（ORPHAN_RECOVERED 语义）+ `RewindMarker`（append-only 审计），落到
MiniMax 的 asyncio + SQLite 架构上。

### 融合结论（侦察驱动：保持语义层，放弃文件系统层）

- ✅ **保持**：marker-file 协议（dirty start + `atexit` clean exit）；
  faulthandler 预打开 sink 保 fd；ORPHAN_RECOVERED 状态翻转；append-only
  recovery step（RewindMarker）；fail-open 全链路；遥测事件接入（R11）。
- ❌ **放弃**：临时+重命名写入、FNV-1a 目录、断尾修复、sidecar 锁、
  `io_lock`——全部被 SQLite WAL + 事务取代。
- ⚠️ **警告**：data dir 不可在网络文件系统上（NFS + WAL = SIGBUS；
  marker 的 temp+rename 在 NFS 上也会腐化）。

### 交付

- `minimax_code/runtime/crash_detect.py`（新，独立模块）：
  `CrashReport` dataclass + `mark_dirty_start`/`mark_clean_exit`/
  `check_previous_crash`（marker 一次性消费）+ `install_faulthandler`
  （预打开 `_FAULT_SINK` 保 fd 不被 GC）+ `crash_report_to_dict`。
- `minimax_code/storage/dao/runs.py`：`AgentRunsDAO.recover_orphans()`
  ——翻转 planning/running/awaiting_approval → failed（`COALESCE`
  completed_at 幂等）+ 每个孤儿追加 `status` recovery step（单行失败
  不阻塞其余）。空 DB 幂等返回 0。
- `minimax_code/app.py`：`_RECOVERY_RESULT` 槽 + `get/_set_recovery_result`
  + `_run_crash_recovery(db)`（编排 marker 检测 + orphan 恢复 + 遥测
  emit，三段各自 fail-open）+ `_maybe_open_db` 在 `return db` 前调用
  （外层 try/except 双保险）+ `register_app_handlers` 注册 runtime
  handlers + `__all__` 导出 `get_recovery_result`。
- `minimax_code/ipc/handlers_runtime.py`（新）：`runtime.recovery_status`
  只读 handler（`available:false` 降级当 recovery 未跑）。
- `minimax_code/__main__.py`：`cli_entry` 在 configure_logging 后安装
  faulthandler + mark_dirty_start + `atexit.register(mark_clean_exit)`
  （整段 fail-open，storage 缺失也不阻塞启动）。顺手补
  `from typing import Any`（预存 F821，因本轮已触碰该文件）。
- 前端契约同步（`web/src/types/ipc.ts` + `client.ts` 四处：
  import/TypedIPC/impl/mock）：`RuntimeRecoveryResult` +
  `runtimeRecoveryStatus()`；mock 返回 clean start。
- `docs/ipc-contract.md`：新增 `### runtime.*` 小节（方法表 + 生命周期 +
  NFS 警告）。
- `tests/test_crash_recovery.py`（新，8 测）：marker 写/读/消费/clean；
  recover_orphans 翻转+append step / 只翻转 in-flight / 双恢复幂等 / 空 DB。

### 验证

- ✅ `ruff check` 6 个 R12 文件：**All checks passed**。
- ✅ `pytest test_crash_recovery.py`：**8 passed in 0.43s**。
- ✅ `eslint client.ts + ipc.ts`：无输出（干净）。
- ✅ `tsc -b`：仅预存的 `client-pending-mode.test.ts(415)` JsonRpcId 错误
  （R11 时就在，非 R12 引入）。

### YAGNI 边界（本轮不做）

- ❌ 不建 `workspace_checkpoints` 表（MiniMax 已有 `compaction.py` 的
  token 估算 + 压缩，检查点不是真空白）。
- ❌ 不做 NFS 运行时检测（警告写进 docstring + 文档即可）。
- ❌ 不把压缩/令牌统计搭进 recovery（解耦，各自独立演进）。

### Commit

`feat(platform): R12 crash detection + orphan run recovery`

---

## R13 — LLM/工具调用可靠性网关：重试退避 + 断路器（阶段 B 第 3 轮）

**本轮目标**：补齐 MiniMax 最大的运行时缺口——一次 429/503 就终止整轮对话、
一个 buggy 工具被 LLM 反复调用直到 `max_iterations` 烧光。融合 grok-build 的
`xai-circuit-breaker`（三态机 + 滑动窗口错误率 + Registry 隔离）+
`RetryPolicy`（Disposition 分类 + 指数退避抖动），落到 MiniMax 的
asyncio + JSON-RPC 架构上，让 LLM 调用与工具调度都套上应用级可靠性网关。

### 融合结论（语义层 1:1 保留，并发原语 asyncio 化）

- ✅ **保持**：`BreakerConfig` 字段对字段（window_duration/min_samples/
  error_rate_threshold/open_duration/half_open_max_probes/failure_codes/
  enabled）；`.server()`/`.client()` 预设；`.from_env(prefix)`；
  `CircuitBreakerRegistry` 按 key 惰性创建；`BreakerState`/`Outcome` 枚举；
  `BreakerOpen` 携带 `retry_after`；`check()`→`record(Outcome)` 协议；
  状态码强制转换（健康码不熔断）；HALF_OPEN 探针并发上限。
- ✅ **保持**：`RetryPolicy`（max_attempts/base_delay/max_delay/
  backoff_factor/jitter_factor）+ `.llm()`/`.tool()` 预设；
  `Disposition {Retryable | Terminal}`（删除 grok 的 AuthRefresh——
  MiniMax transports 不做 token 刷新）；指数退避 + 对称抖动；
  on_retry 回调（sync/async 兼容、异常吞咽）；穷尽时重抛原始异常。
- ❌ **放弃**：`Arc<RwLock>`→`asyncio.Lock`；`VecDeque<(Instant,Outcome)>`
  →`collections.deque` 的 `(time.monotonic(), Outcome)`；枚举改 `StrEnum`
  替代 `(str, Enum)`（ruff UP042）。
- ⚠️ **根因修复**：transports 把 `APIError` 扁平化为 `LLMError` 时丢失
  `status_code`——R13 给 `LLMError` 加 `status_code` 字段，两个 transport
  用 `getattr(exc, "status_code", None)` 透传，否则 classify 永远走
  no-code 分支。

### 交付

- `minimax_code/agent/reliability/circuit_breaker.py`（新）：
  `BreakerState`/`Outcome`(StrEnum) + `DEFAULT_FAILURE_CODES` +
  `BreakerOpen(retry_after, state)` + `BreakerConfig`(.server()/.client()/
  .from_env()) + `CircuitBreaker`(async check/record, .state, .is_open(),
  .error_rate(), _prune, _trip) + `CircuitBreakerRegistry`
  (async get_or_create, get, all_states)。三态机增量评估（每次 record
  都检查阈值），OPEN→HALF_OPEN 在 check 时按 open_duration 转换。
- `minimax_code/agent/reliability/retry.py`（新）：
  `RETRYABLE_STATUS`/`TERMINAL_STATUS` + `Disposition`(StrEnum) +
  `RetryExhausted` + `classify_exception`（LLMStreamTimeout/Timeout/
  ConnectionError→RETRYABLE；429/5xx→RETRYABLE；4xx 终端→TERMINAL；
  无 code→RETRYABLE）+ `RetryPolicy`(.llm()/.tool()) + `_delay_for`
  (指数退避+对称抖动) + `with_retry`(coro_factory, policy, on_retry,
  可注入 sleep)。
- `minimax_code/agent/reliability/__init__.py`（新）：桶式导出全部
  公共符号。
- `minimax_code/agent/types.py`：`LLMError` 加 `status_code: int | None`
  关键字参数（默认 None，向后兼容）。
- `minimax_code/agent/transports/openai_transport.py` +
  `anthropic_transport.py`：`APIError` 捕获处用
  `status_code=getattr(exc, "status_code", None)` 透传给 `LLMError`。
- `minimax_code/agent/core.py`（六刀接入）：
  1. import reliability 全部符号；
  2. `AgentConfig` 加 `llm_retry_policy`/`llm_breaker_config`/
     `tool_breaker_config` 三字段（None ⇒ 用预设）；
  3. `__init__` 建 `self._breakers = CircuitBreakerRegistry()`；
  4. 新方法 `_call_llm_with_resilience`——check → with_retry → record；
  5. LLM 调用点 `_stream_turn` → `_call_llm_with_resilience`；
  6. 工具调度加 per-tool 熔断（`tool:<name>` key），崩溃/超时才 trip，
     业务级 ToolResult.fail（如 file not found）记 SUCCESS 防误熔断。
- `tests/test_reliability.py`（新，28 测）：BreakerConfig presets/from_env/
  defaults；CircuitBreaker 全状态机（closed/trip/不熔/rate/状态转换/
  探针上限/码强制/disabled）；Registry key 隔离 + 只首次建；classify
  矩阵；with_retry 成功/穷尽/终端/on_retry(sync/async/吞异常)/默认/
  抖动边界。

### 验证

- ✅ `ruff check` 全部 R13 文件：**0 errors**（UP042 StrEnum × 3、
  UP041 builtin TimeoutError、I001 import 排序，均 ruff --fix 修复）。
- ✅ `pytest tests/test_reliability.py`：**28 passed in 1.09s**。
- ✅ `pytest tests/test_agent_core.py tests/test_agent_cancel.py`：
  **21 passed in 9.95s**（零回归——证明 `_call_llm_with_resilience` +
  per-tool 熔断接入不破坏对话循环/取消逻辑）。

### YAGNI 边界（本轮不做）

- ❌ 不做三层准入信号量（grok 的 token bucket / 并发令牌）——MiniMax
  单进程 asyncio，串行 LLM 调用，无并发风暴需要节流。
- ❌ 不做 token / $ 消耗上限——需先有计费通道，留给后续轮。
- ❌ 不做 RPM/TPM 限流——同上，依赖未实现的配额层。
- ❌ 不做 OS 级沙箱（grok 的 Landlock/Seatbelt）——Windows 不可用。
- ❌ 不做前端熔断面板 / IPC 界面——遥测已 emit status，UI 留待阶段 D。
- ❌ 遥测发射降级为 logger（fail-open），不搭 R11 的 event bus 接线。

### Commit

`feat(platform): R13 reliability — circuit breaker + retry/backoff`

## R14 — 结构化追踪层：span/trace 因果模型（阶段 B 第 4 轮）

**本轮目标**：补齐 R11 遥测引擎的最后一块拼图——扁平事件流能回答
"一个 turn 发生了什么"，但回答不了"这个 turn 的 3.2s 花在哪：LLM 2.1s
还是 tool.search 0.8s"。融合 grok-build 的 `xai-tracing`（span/trace 因果
语义 + 父子链），落到 MiniMax 的单进程 asyncio 上，让每个 turn 的耗时
分解可观测。每关闭一个 span 就向 R11 engine emit 一条 `SPAN` 事件，
`telemetry.trace` 重建 trace 树。

### 融合结论（语义层 1:1 保留，分布式基础设施 YAGNI 砍净）

- ✅ **保持**：`Span`（name/trace_id/span_id/parent_id/start_ms/end_ms/
  status/error/attributes）——字段对字段对齐 OTel 语义；async context
  manager（`__aenter__` 计时 + 入栈，`__aexit__` 计时 + 捕获异常 status +
  出栈 + fire on_close）；`SpanStatus {OK | ERROR}`（OTel UNSET→OK/ERROR
  的简化）；`Tracer.start_span` 自动从 contextvar 取 parent 串 trace_id。
- ✅ **保持**：父子链走 `contextvars.ContextVar`——asyncio 每 task 独立
  上下文，并发 turn / sub-agent 的 span 树天然隔离，永不串线（这是 grok
  用 fastrace + task-local 达到的效果，Python 用 contextvars 白拿）。
- ✅ **保持**：`build_tree`（flat span records → parent→children forest，
  每层按 start_ms 排序，orphan parent 提升为 root，浅拷贝不污染输入）。
- ❌ **放弃**：OTLP/gRPC exporter（`opentelemetry-otlp` + `tonic`）——
  MiniMax 单进程，无 collector 可投递；`fastrace` 宏/层——Python 用
  `async with` 更直接；`reqwest-middleware` 跨服务 propagation——无下游
  服务；sampling 策略——内存 ring buffer 已自带容量上限（500）；tower
  layer / interceptor 链——Python 中间件用装饰器/async with 即可。
- ⚠️ **接线修复**：`_default_emit` 三层 lazy + try/except（import engine
  失败 / engine=None / emit 抛异常全 fail-open）——tracing 必须在 engine
  尚未 boot、被禁用、事件模型 import 失败三种早启动场景下都不打断 agent。

### 交付

- `minimax_code/telemetry/tracing.py`（新）：`SpanStatus`(StrEnum) +
  `_new_id` + `_current_span`(ContextVar) + `Span`(`__slots__`，duration_ms
  property，`set()`/`to_record()`，async CM) + `Tracer`(start_span 串父，
  `current()` 静态) + `_default_emit`(lazy 接 R11 engine) + `get_tracer`/
  `set_tracer`(单例 + 测试注入) + `build_tree`(flat→forest)。
- `minimax_code/telemetry/events.py`：`EventType` 加 `SPAN = "span"`。
- `minimax_code/telemetry/engine.py`：加 `trace_spans(trace_id)`——扫
  ring buffer 过滤 `type==SPAN` 且 `payload.trace_id` 匹配，返回 payload
  dict 列表（含 span_id/parent_id/duration_ms/status/attributes）。
- `minimax_code/telemetry/__init__.py`：导出 tracing 全部公共符号。
- `minimax_code/ipc/handlers_telemetry.py`：加 `telemetry.trace` handler
  （engine.trace_spans → build_tree → reply `{trace_id, spans, tree,
  span_count, enabled}`；空/非 str trace_id 报 INTERNAL_ERROR）。
- `minimax_code/agent/core.py`（三处 span 接入）：
  1. `__init__` 建 `self._tracer = get_tracer()`；
  2. **root span**：`send_message` 主循环 `for...else` 整体包进
     `agent.turn` span（session_id/user_message/max_iterations 属性），
     所有 LLM/tool span 自动 nest；
  3. **llm span**：`_call_llm_with_resilience` 的 with_retry 包进
     `llm.stream` span（model 属性）；
  4. **tool span**：`_dispatch_tool` 的 registry.dispatch 包进
     `tool.<name>` span（tool/tool_call_id 属性），只计时真实执行不含
     audit 写入。
- `tests/test_tracing.py`（新，15 测）：span 计时/OK/ERROR+error 字段/
  属性 round-trip/`set()` advisory；嵌套 span 共享 trace_id + parent 链 +
  `current()` 栈；并发 task 树隔离；`build_tree` forest/orphan/排序/不
  变异输入；`_default_emit` 发 SPAN 事件 + parent 链 + engine=None/
  engine 抛异常双 fail-open；get_tracer 单例 + set_tracer 注入。
- 前端契约同步：`web/src/types/ipc.ts` 加 `TelemetrySpanRecord`/
  `TelemetrySpanNode`/`TelemetryTraceResult`；`web/src/ipc/client.ts`
  四处接入（import + TypedIPC + impl + mockHandle）；`docs/ipc-contract.md`
  加 `telemetry.trace` 方法行。

### 验证

- ✅ `ruff check`（tracing.py + core.py + test_tracing.py + engine.py +
  events.py + __init__.py + handlers_telemetry.py）：**All checks passed**。
- ✅ `pytest tests/test_tracing.py`：**15 passed in 0.93s**。
- ✅ 回归 `pytest tests/test_agent_core.py tests/test_reliability.py
  tests/test_telemetry.py`：**57 passed in 9.74s**（零回归——证明三处
  span 接入不破坏对话循环/可靠性网关/R11 遥测）。

### YAGNI 边界（本轮不做）

- ❌ 不做 OTLP/gRPC/collector 投递——单进程内存 bus 已够。
- ❌ 不做 sampling 策略——ring buffer 容量上限即天然采样。
- ❌ 不做跨进程 trace propagation（W3C traceparent / B3）——无下游服务。
- ❌ 不做前端 waterfall UI 组件——后端 `telemetry.trace` 契约已就绪，
  可视化留待阶段 D（多模态与交互面板）。
- ❌ 不做 span 采样概率配置 / OpenTelemetry Collector 兼容——YAGNI。
- ❌ 不给 skill / hook / permission 决策单独开 span——本轮只覆盖三大
  热路径（turn/llm/tool），其余事件仍走 R11 扁平事件流。

### Commit

`feat(platform): R14 structured tracing — span/trace causal model`

## R15 — 出站数据脱敏强化：凭据 shape 扩展 + URL userinfo 修复 + 日志 filter（阶段 B 第 5 轮）

**本轮目标**：R11 已建了 telemetry 管线的 `redact_value` 脱敏闸门，但对照 grok-build
的 `xai-grok-secrets`（纯出站数据擦除器）发现两道真实漏洞 + 一处覆盖缺口——
(1) `url_origin` 把 `https://user:pass@host` 的 netloc 原样保留，**userinfo 泄露**；
(2) 只认 `sk-/Bearer/api_key=` 三种 shape，**漏掉 AWS/GitHub/Slack/Google/JWT** 五类
主流凭据；(3) 脱敏只挂在 telemetry emit 路径上，**普通 `logger.info()` 日志照样裸写到
stderr**。本轮把脱敏从"遥测专属"升级为"遥测 + 日志"统一安全闸门。

### 融合结论（grok 纯正则/JSON-walk 哲学 1:1 保留，Rust 生态依赖 YAGNI 砍净）

- ✅ **保持**：`redact_value` 递归 walk（dict/list/tuple/str 四分支）——grok 的
  serde_json walk 在 Python 用 isinstance 分发白拿；三 scrubber 顺序 cheapest-first
  （secrets → paths → url）；保守 shape 原则（只匹配"几乎确定是凭据"的形状，绝不
  误伤 bare word，如 "the api key is missing" 不动）。
- ✅ **保持**：fail-open 铁律——脱敏失败绝不阻塞调用方（engine.emit 已 try/except，
  SanitizerFilter 同样 try/except + `return True` 永远放行）。
- ✅ **接线修复（漏洞 #1）**：`url_origin` 用 `rsplit("@", 1)[1]` 剥离 userinfo——
  在最后一个 `@` 切，正确处理密码本身含 `@` 的情形（host 永不含 `@`，最后一 @ 必
  是 userinfo/host 分隔）。
- ✅ **扩展（漏洞 #2）**：`_SECRET_PATTERNS` 加 5 类 provider shape——AWS `AKIA`+16、
  GitHub `gh[pousr]_`+36 与 `github_pat_`+40、Slack `xox[baprs]-`+10、Google `AIza`+35、
  JWT `eyJ.….…`（eyJ 锚定 header，避免误伤普通 base64）。
- ✅ **新增（缺口 #3）**：`SanitizerFilter`（`logging.Filter` 子类）——在 record
  format 前对 `msg`（str）+ `args`（dict/tuple）跑 `redact_value`，挂 root logger
  一次即被所有子 logger 继承；`configure_logging` 用 `isinstance` 守卫保证幂等
  （重复调用不叠加 filter）。
- ❌ **放弃**：grok 的 `url` crate（URL 凭据脱敏）——Python 标准库 `urllib.parse.
  urlsplit` 已够，不拉 `furl`；grok 的 `regex` crate——Python `re` 已够；grok 的
  Sentry/Mixpanel/产品事件清洗专路——MiniMax 无这些远端 sink，统一走 telemetry +
  logging 两路即可。

### 交付

- `minimax_code/telemetry/redact.py`：
  1. `_SECRET_PATTERNS` 加 5 条 provider 正则（AWS/GitHub×2/Slack/Google/JWT）；
  2. `url_origin` 加 `@` userinfo 剥离（`rsplit("@", 1)`）；
  3. 新 `SanitizerFilter`（`logging.Filter`，filter() 改写 record.msg/args + 永远
     `return True` + try/except fail-open）；
  4. `import logging`；`__all__` 加 `SanitizerFilter` 并按字母序排。
- `minimax_code/telemetry/__init__.py`：re-export `SanitizerFilter`（import 行 +
  `__all__`），保持包级 import 一致性。
- `minimax_code/logging_setup.py`：`configure_logging` 在 `root.setLevel` 后、
  `isinstance(f, SanitizerFilter)` 守卫下 `root.addFilter(SanitizerFilter())`。
- `tests/test_telemetry.py`：+3 测——`test_url_origin_strips_userinfo`（普通
  userinfo + 密码含 @ 双例）、`test_redact_secrets_scrubs_provider_shapes`（AWS/
  GitHub/Slack/Google/JWT 五类各一断言）、`test_sanitizer_filter_scrubs_log_records`
  （msg 脱敏 + args tuple 脱敏 + URL userinfo 随 args 一并清）。

### 验证

- ✅ `ruff check`（redact.py + __init__.py + logging_setup.py + test_telemetry.py）：
  **All checks passed**。
- ✅ `pytest tests/test_telemetry.py tests/test_agent_core.py tests/test_reliability.py`：
  **60 passed in 9.28s**（R14 基线 57 + R15 新增 3 测，零回归——证明 5 类新正则
  不误伤现有 shape 测试、url_origin 改动不破坏 path/query 剥离、SanitizerFilter
  接入 logging_setup 不影响 agent 启动路径）。

### YAGNI 边界（本轮不做）

- ❌ 不拉 `furl`/`url` 依赖——stdlib `urllib.parse` 已够。
- ❌ 不做独立 `redact_text` 函数——`redact_secrets` 已覆盖纯文本场景。
- ❌ 不做 secrets store——`secrets.py`（keyring + env）是密钥**存储**，与**出站
  数据脱敏**是两件事，本轮只管后者。
- ❌ 不做规则配置文件/TOML——规则集硬编码，对齐 R11 简单风格；配置化留待真实多租户
  需求出现。
- ❌ 不做 per-field 白名单 / 保留字段——保守 shape 匹配已足够，精细控制 ROI 低。
- ❌ 不给每个 logger 单独挂 filter——root logger 一处即全局生效。
- ❌ 不做 redact 的性能 benchmark——`redact_value` 是叶子递归 + 编译期 regex，
  telemetry emit 已是 fire-and-forget 非热路径。

### Commit

`feat(platform): R15 out-bound redaction hardening — secrets/log/url`
