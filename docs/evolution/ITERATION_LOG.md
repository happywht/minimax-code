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
