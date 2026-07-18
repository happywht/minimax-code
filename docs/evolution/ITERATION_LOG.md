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

---

## R16 — 单一因果文件变更流：FsEventBus（fuse grok xai-fsnotify）（阶段 B 第 6 轮）

**本轮目标**：grok-build 的 `xai-fsnotify` 是一套"OS 文件监视器 → 防抖窗口 → git 锁
状态机 → 因果广播通道"的四段管线，目的是在**外部编辑器**（人类/IDE）改动文件时给出
稳定的语义事件流。MiniMax 的根因差异在于——**我们自己就是变更源**：`write_file` /
`edit_file` 这两个工具的写入是原子的、自归因的、本进程内的，不存在"OS 事件风暴待平
息"。本轮从 grok 抽取**真正的因果支柱**（单一因果流 + 纯数据事件 + 序列单调顺序 +
fail-open 铁律），砍掉**只服务于外部观察者的表衣**（OS watcher / 防抖 / 锁状态机 /
git 丰富化），落地一个进程内的 `FsEventBus`，并把两个写工具接上——让每一次成功的
文件写入都成为因果流上的一颗 `FsEvent`。

### 融合结论（因果支柱 1:1 保留，外部观察者表衣 YAGNI 砍净）

- ✅ **保持**：单一因果流——`FsEventBus` 是进程内唯一广播通道，`emit()` 既
  append 到 `_recent` 环形缓冲（`deque(maxlen=capacity)`），又 fan-out 到全部订阅者
  queue；一份事件，两条消费路径（recent 回看 / 实时订阅），互不干扰。
- ✅ **保持**：纯数据事件——`FsEvent` 是 `@dataclass(frozen=True)`，无 I/O、无
  asyncio、无包内依赖（`events.py` 只 import stdlib）。冻结保证事件不可变（消费者
  无法篡改因果流），可整体 lift 到未来的 `-types` 包做跨进程契约。
- ✅ **保持**：序列单调因果顺序——`itertools.count(1)` 递增 `seq`，`recent()` 按入队
  顺序回看，订阅者按 `put_nowait` 顺序收。消费者**必须**依赖 `seq` 而非 wall-clock
  （`monotonic_ns` 会抖动），这是因果序的语义核心。
- ✅ **保持**：fail-open 铁律——`emit()` 外层裸 `try/except Exception` + `# noqa:
  BLE001`，任何故障（recent 损坏 / 订阅者 queue 爆 / 类型错误）都返回 `None`、绝不
  raise；订阅者 fan-out 内层再 try/except（broken subscriber 不连累其它订阅者）；
  `recent()` 读路径同样 fail-open（best-effort 返回 []）。镜像 `TelemetryEngine`
  契约——**总线坏绝不让写入工具崩**。
- ✅ **三件套单例**：`app.py` 的 `get_fs_bus` / `set_fs_bus` / `ensure_fs_bus` 与
  `ensure_telemetry_engine` 完全对齐——`ensure_fs_bus()` 惰性构建一次，环境开关
  `MINIMAX_CODE_FS_BUS`（"0/false/off/no" 禁用），构建失败 log + 返回 None（"disabled,
  zero overhead"）；写工具 `if bus: bus.emit(...)` 短路。循环引用安全：app.py 顶部只
  import skills 不 import tools；tools 在 `run()` 内 `from ...app import ensure_fs_bus`
  惰性导入。
- ❌ **放弃**：grok 的 OS 文件监视器（`notify`/watch 树）——MiniMax 是变更源不是观察
  者，自己的工具调用就是唯一的"事件源"，监视 OS 是观察外部编辑器用的，对本进程写入
  多此一举。
- ❌ **放弃**：grok 的防抖窗口（debounce）——OS 事件风暴需要时间沉淀（一次保存触发
  created→modified→modified...）；工具写入是原子的单次操作，无风暴可平，防抖只会
  增加无谓延迟。
- ❌ **放弃**：grok 的 git 锁状态机——grok 用它区分"用户编辑"vs"git 操作"两类事件
  源；MiniMax 的 git handlers 是只读子进程（status/diff/log），不产生写入，无需状态
  机区分。
- ❌ **放弃**：grok 的 git 丰富化（把 commit/branch 信息塞进事件）——`FsEvent.cause`
  已归因到具体工具（write_file/edit_file），YAGNI，待真实需求出现再加。

### 交付

- `minimax_code/fsnotify/events.py`（新模块，纯数据层）：`FsEventKind`（StrEnum：
  created/modified/removed/renamed，identity-map grok 的 wire 值）+ `FsEvent`
  （frozen dataclass：kind/paths/cause/seq/monotonic_ns + 可选 session_id/
  tool_call_id/attributes 元组）+ `attribute()` 读方法。
- `minimax_code/fsnotify/bus.py`（新模块，总线）：`FsEventBus`——`__init__`（capacity
  /subscriber_maxsize 两道 ≥1 守卫）+ `emit`（fail-open，构建+发布）+ `subscribe`/
  `unsubscribe`（bounded queue 订阅/退订）+ `recent`（kind/cause/session_id 三路过滤，
  best-effort）+ `buffered_count`/`subscriber_count`/`clear` 属性。
- `minimax_code/fsnotify/__init__.py`（新包）：re-export `FsEvent`/`FsEventBus`/
  `FsEventKind`，`__all__` 三件。
- `minimax_code/app.py`（编辑）：`_FS_BUS` 模块全局 + `get_fs_bus`/`set_fs_bus`/
  `ensure_fs_bus` 三件套（镜像 telemetry，惰性构建 + 环境开关 + fail-open 返回 None）；
  `__all__` 加三件导出。
- `minimax_code/agent/tools/file_ops.py`（编辑）：`WriteFileTool.run` 在
  `target.stat().st_size` 后、return 前，惰性 `ensure_fs_bus()` + `bus.emit("created"
  if not existed else "modified", [str(target)], "write_file", size_bytes=size)`，
  外层 try/except BLE001 fail-open。
- `minimax_code/agent/tools/edit.py`（编辑）：`EditFileTool.run` 在统一 diff 算完后、
  return 前，惰性 `ensure_fs_bus()` + `bus.emit("modified", [str(target)],
  "edit_file", lines_added=added, lines_removed=removed)`，同样 fail-open。
- `tests/test_fsnotify.py`（新，11 测）：`_reset_fs_bus` autouse fixture 隔离单例 +
  `workspace` fixture 复用 test_tools 的路径策略。覆盖——frozen 纯数据（FrozenInstance
  Error）、emit→recent+返回值、因果 seq 单调、emit fail-open（_BrokenRecent 注入）、
  订阅 fan-out、慢订阅者 drop-not-block（subscriber_maxsize=1）、recent 三路过滤、
  unsubscribe 停投递、capacity 守卫、write_file 集成（CREATED + cause=write_file +
  size_bytes=5）、edit_file 集成（MODIFIED + cause=edit_file）、路径拒绝不发事件。

### 验证

- ✅ `ruff check`（fsnotify/ + app.py + file_ops.py + edit.py + test_fsnotify.py）：
  **All checks passed**（首轮 4 处告警：bus.py 的 UP037 引号类型提示 ×2、events.py 的
  UP042 StrEnum、test_fsnotify.py 的 B017 盲 Exception——已全部修正）。
- ✅ `pytest -q` 全套：**1043 passed in 92.25s**，零失败、零回归——R16 新增 11 测无
  缝融入 1043 总量，证明三件套单例不影响 agent 启动、写工具 emit 不破坏 read/list/
  edit 既有行为、fail-open 路径在总线缺失时正确短路。

### YAGNI 边界（本轮不做）

- ❌ 不做 OS 文件监视（inotify/ReadDirectoryChanges）——自己是变更源，无外部观察者需求。
- ❌ 不做 git 丰富化（commit/branch 入事件）——`cause` 字段已归因到工具，commit 级
  归因待真实消费者出现。
- ❌ 不做事件持久化（写 SQLite/磁盘）——`_recent` 内存环形缓冲已满足回看；持久化是
  audit log 的职责（已有 telemetry 通道），不混入因果流。
- ❌ 不做跨进程广播（IPC/网络）——`FsEventBus` 是进程内单例；跨进程需求出现时再加
  transport 层，事件契约（`FsEvent`）已是纯数据可整体 lift。
- ❌ 不做 REPL/重放（把 recent 当 event sourcing 回放）——`recent()` 是 best-effort
  回看窗口（capacity 上限会丢早期事件），不是持久化日志，不做重放语义保证。
- ❌ 不做 fsnotify→telemetry 自动桥接——两套通道各自独立；桥接（把 FsEvent 喂给
  TelemetryEngine）是独立决策，留待 R17+。
- ❌ 不给 list_directory/read_file 也发事件——只对**写**操作（write/edit）发，读
  操作不改变因果状态。

### Commit

`feat(platform): R16 single-causal file-change stream (fuse grok xai-fsnotify)`

## R17 — 调用级熔断器：CircuitBreaker（fuse grok xai-circuit-breaker）（阶段 B 第 7 轮）

**本轮目标**：grok-build 的 `xai-circuit-breaker` 是一套保护下游（LLM API、外部工具）
免遭雪崩重试的三态熔断器——closed 累积样本 → error_rate 越阈 trip → open 冷却拒流 →
half-open 探测恢复，外加滑动窗口、租约防卡死、Observer 钩子、RetryPolicy 分类器。MiniMax
的 `agent/core.py` 调 LLM 和工具时，下游连续失败会无脑重试拖垮体验（也白烧 token）。本轮
把熔断器作为**平台公共组件**落地（grok 的 `crates/common` 对应 MiniMax 的基础层），让下游
调用在连续失败时**快速失败**（open 态直拒），半开探测恢复，全程 fail-open（断路器自身故障
绝不阻塞调用方）。与 R16 同构——抽取因果支柱（三态机 + 滑动失败计数 + 冷却 + 半开探测 +
租约），砍掉 Rust 生态表衣（原子/CAS/锁——Python GIL 单线程下纯属累赘）。

### 融合结论（因果支柱 1:1 保留，Rust 并发表衣 GIL 下砍净）

- ✅ **保持**：三态机因果核——`BreakerState`（StrEnum：closed/open/half_open）+
  `Outcome`（success/failure）+ `BreakerOpen` 信号（带 `retry_after` 秒）。`check()` 三态
  调度（open→raise / half_open→claim probe / closed→放行），`record()` 反馈结果驱动转换。
  identity-map grok 的 `BreakerState=0/1/2` wire 值（语义保留，序列形式无关紧要）。
- ✅ **保持**：滑动窗口 + min_samples 双闸——`SlidingWindow` 是 `deque[(ts,is_failure)]` +
  增量 `_failures` 计数，`error_rate()` O(1)（热路径 record 每次读，绝不扫描）。trip 条件
  `sample_count >= min_samples AND error_rate >= threshold`，样本不足时不跳闸（避免冷启动
  误杀）。`MAX_WINDOW_ENTRIES=10_000` 安全帽防高压爆内存。
- ✅ **保持**：冷却时间窗——trip 时记 `opened_until = now + open_duration`，`state` property
  惰性检查 `now >= opened_until` 转 half_open（读路径即转换，无需额外 tick）。
- ✅ **保持**：半开探测 + 租约回收——`half_open_max_probes` 槽位，probe 在 `check` 中
  `+=1`，在 close/trip 中 reset 0（镜像 grok，record 不递减）。**租约铁律**：被放弃的 probe
  （取消的 future / 忘了 record）会在 `probe_claimed_at + open_duration` 后被回收，断路器
  永远不会卡死在 half_open 无槽可用——这是 grok 防卡死的关键机制，1:1 保留。
- ✅ **保持**：fail-open 铁律——`guard()` 同步上下文管理器：pre-check 的 `BreakerOpen` 直
  传播；断路器**自身**内部故障（坏 Observer / clock 抛错）被 `except Exception: # noqa:
  BLE001` 吞掉降级（log debug），**业务调用照常执行**。业务异常记 FAILURE 并 re-raise。
  镜像 R16 的 emit fail-open 契约——**断路器坏绝不让业务调用崩**。
- ✅ **保持**：Observer 钩子——`on_state_change(old,new,reason)` + `on_outcome(outcome)`，
  `NoopObserver` 默认。给遥测/日志留扩展点（未来 R18+ 把状态转换喂给 TelemetryEngine）。
- ✅ **保持**：RetryPolicy 分类器——`Disposition`（retryable/auth_refresh/terminal）+
  `server()`（429+5xx retry）/ `client_storage()`（401 auth-refresh / 400-404 terminal /
  其余 retry）预设 + `classify(status)`（2xx 返回 None）。把"这个状态码怎么办"的决策收口，
  调用方不再各自重写状态码表。
- ✅ **三件套单例**：`app.py` 的 `get_breaker_registry` / `set_breaker_registry` /
  `ensure_breaker_registry` 与 `ensure_fs_bus` / `ensure_telemetry_engine` 完全对齐——
  `ensure_breaker_registry()` 惰性构建一次，`BreakerConfig.from_env()` 读 `MINIMAX_CODE_CB_*`
  开关，环境开关 `MINIMAX_CODE_BREAKER`（"0/false/off/no" 禁用），构建失败 log + 返回 None
  （"running unprotected"）；调用方 `reg = ensure_breaker_registry(); br = reg.get(key) if
  reg else None`，`None` 即"无保护，照常调用"。
- ❌ **放弃**：grok 的全部原子操作（`AtomicUsize`/`AtomicBool`/`fetch_add` CAS）——Python
  asyncio 单线程 + GIL，状态突变无竞争，普通属性访问即 race-free。这是前向迁移的**最大简化
  红利**：grok 的 `is_open_fast: AtomicBool` 镜像、probe 槽 CAS、OnceLock 初始化全部不需要。
- ❌ **放弃**：grok 的 `Clock` trait + `SystemClock`/`MockClock` 类型层级——Python 直接用
  `Callable[[], float]` 类型别名（默认 `time.monotonic`），测试注入 `MockClock` 可调用对象，
  无需 trait 仪式。`opened_until` 存单调秒偏移（非 wall-clock），免 NTP 漂移——这条**保留**。
- ❌ **放弃**：grok 的多 probe 并发 CAS 语义（`half_open_max_probes > 1` 时 N 个 probe 全成功
  才 close）——默认 `max_probes=1`，单 probe 即决定（成功→close / 失败→trip）。多 probe 并发
  在 Python 单线程 guard（无 await 点）下不会发生；YAGNI，待真实多 probe 需求再加成功计数。
- ❌ **放弃**：grok 的 `on_probe_admission(allowed)` 钩子——本平台暂无探测准许事件的消费者，
  YAGNI 砍掉，只保留 `on_state_change` + `on_outcome` 两个有实际消费者的钩子。

### 交付

- `minimax_code/resilience/state.py`（新模块，纯数据层）：`BreakerState`（StrEnum：
  closed/open/half_open）+ `Outcome`（StrEnum：success/failure）+ `BreakerOpen`（异常，
  带 `retry_after: float`，`__init__` clamp `>=0`）。
- `minimax_code/resilience/window.py`（新模块）：`SlidingWindow`——`__slots__`（_entries/
  _failures）+ `push`（超 MAX_WINDOW_ENTRIES 先驱逐最旧）+ `evict`（按 window 时窗驱逐）+
  `error_rate`（O(1) 增量）+ `sample_count`/`clear`；模块常量 `MAX_WINDOW_ENTRIES=10_000`。
- `minimax_code/resilience/config.py`（新模块）：`BreakerConfig`（frozen dataclass：
  window_duration/min_samples/error_rate_threshold/open_duration/half_open_max_probes/
  failure_codes/enabled）+ `server()`/`client()` 类方法预设 + `from_env(prefix=
  "MINIMAX_CODE_CB_")`（WINDOW_SECS/MIN_SAMPLES/ERROR_RATE_THRESHOLD/OPEN_DURATION_SECS/
  HALF_OPEN_MAX_PROBES/FAILURE_CODES/ENABLED，坏值 log + 回落默认）+ `is_failure_status` +
  `with_half_open_floor`；模块级 `parse_failure_codes`（逗号分隔，坏项静默丢弃）。
- `minimax_code/resilience/retry_policy.py`（新模块）：`Disposition`（StrEnum：
  retryable/auth_refresh/terminal）+ `RetryPolicy`（frozen dataclass：retryable/auth_refresh/
  terminal frozensets + default）+ `server()`/`client_storage()` 预设 + `classify(status)`（2xx
  返回 None，否则 auth_refresh > terminal > retryable ∨ 5xx > default）+ `should_retry`。
- `minimax_code/resilience/breaker.py`（新模块，核心）：`Clock` 类型别名 + `Observer`/
  `NoopObserver`（on_state_change/on_outcome 钩子）+ `CircuitBreaker`——`state`/`config`/
  `is_open`/`error_rate`/`sample_count` 只读属性 + `check()`（三态调度 + half_open probe
  claim + 租约回收）+ `record()`（half_open probe 决定 / closed 累积 + 阈值 trip / open 防御
  不累积）+ `_check_open`/`_try_half_open_probe`/`_trip`/`_close`/`_set_state` 私有转换 +
  `guard()` 同步上下文管理器（fail-open 铁律）。
- `minimax_code/resilience/registry.py`（新模块）：`CircuitBreakerRegistry`——`config`/
  `enabled` 属性 + `get(key)`（惰性创建同 key 同实例，禁用返回 None）+ `keys`/`known_keys`/
  `clear`。
- `minimax_code/resilience/__init__.py`（新包）：re-export 15 个公共符号，`__all__` 登记。
- `minimax_code/app.py`（编辑）：`_BREAKER_REGISTRY` 模块全局 + `get_breaker_registry`/
  `set_breaker_registry`/`ensure_breaker_registry` 三件套（镜像 fs_bus，惰性构建 +
  `BreakerConfig.from_env()` + 环境开关 `MINIMAX_CODE_BREAKER` + fail-open 返回 None）；
  `__all__` 加三件导出。
- `tests/test_resilience.py`（新，33 测）：`MockClock`（确定性时钟，`__call__` 返回 `t`，
  `advance` 推进）+ `hot_config` 工厂（min_samples=4 易 trip）。覆盖——状态机（trip at
  threshold / 不足 min_samples 不 trip / error_rate 不足不 trip / open→half_open at 边界 /
  open sheds via check / half_open probe 成功 close+清窗 / half_open probe 失败 reopen / 槽位
  耗尽 / **租约回收** / disabled 永不 shed）、滑动窗口（增量 error_rate / evict 旧样本 /
  MAX cap / clear 重置失败计数）、注册表（同 key 同实例 / 异 key 异实例 / 禁用 None / clear）、
  RetryPolicy（server 429+5xx retry+400 terminal / client_storage 401 auth+404 terminal+5xx
  retry+default retry）、guard fail-open（记 success / 记 failure+reraise / BreakerOpen
  传播不进 block / Observer 故障 fail-open）、Config（server/client 预设 / from_env 解析 /
  坏值回落 / parse_failure_codes / is_failure_status）、app 三件套（set/get / env 禁用 None /
  惰性构建缓存）。

### 验证

- ✅ `ruff check`（resilience/ + app.py + test_resilience.py）：**All checks passed**——
  7 个新模块 + app.py 编辑 + 33 测一次过检（BLE001 双 noqa 已在 guard fail-open 处标注、
  `collections.abc.Callable` 避免 UP045、StrEnum 满足 UP042、`X | None` 满足 UP037）。
- ✅ `pytest tests/test_resilience.py -q`：**33 passed in 0.64s**——状态机全路径、租约回收、
  guard fail-open、env 解析全部绿灯。
- ✅ `pytest -q` 全套：**1076 passed in 84.97s**（1043 + 33 新增），零失败、零回归——R17
  新增 33 测无缝融入，证明三件套单例不影响 agent 启动、resilience 包无副作用污染既有 1043 测。

### YAGNI 边界（本轮不做）

- ❌ 不接 LLM transport——`agent/llm.py` 的 `AnthropicTransport`/`OpenAITransport` 状态处理
  在传输层，连接断路器需改传输内部（多传输实现各有状态机），是独立决策，留待 R18。
- ❌ 不做 async guard——`guard()` 是同步上下文管理器（无 await 点），await 调用点应手动
  `check()` + `await call` + `record()`。async with 变体待 transport 连接时按需再加。
- ❌ 不做断路器状态持久化——状态全在内存（进程重启即 reset 为 closed）。持久化（跨重启
  保持 open 态）是独立需求，且会引入时序复杂度，待真实场景驱动。
- ❌ 不做多 probe 成功计数——默认 `half_open_max_probes=1`，单 probe 决定。多 probe
  （N 个连续成功才 close）语义待真实并发探测需求再加 `_half_open_successes` 计数器。
- ❌ 不做 metrics/observability 端点——Observer 钩子已留（on_state_change/on_outcome），
  把状态转换喂给 TelemetryEngine 是 R18+ 的桥接工作，不混入熔断器内核。
- ❌ 不做断路器手动远程控制（强制 open/close 的 RPC）——当前无运维面板需求，YAGNI。
- ❌ 不把 RetryPolicy 接进 transport 重试循环——`RetryPolicy.classify` 已就绪，但 transport
  现有 `max_retries` 逻辑未消费它；接入是 transport 连接轮（R18）的一部分。

### Commit

`feat(platform): R17 circuit breaker (fuse grok xai-circuit-breaker)`

## R18 — 熔断器接入 LLM 传输热路径（fuse grok xai-circuit-breaker）（阶段 B 第 8 轮）

**本轮目标**：R17 把 CircuitBreaker 内核作为平台公共组件落地，但 YAGNI 边界明确"不接
LLM transport"。本轮把熔断器接进 LLM 传输热路径——`AnthropicTransport` /
`OpenAITransport` 的 `stream_chat` 流式生成器。grok-build 的 `xai-circuit-breaker` 用
同步 `guard()` 上下文管理器包裹调用；但 MiniMax 的 `stream_chat` 是**异步生成器**
（`yield` 跨 await 点），sync `with` 进不来。本轮的核心工程抉择：放弃同步 guard 包裹，
改用**手动三步式**——pre-check（`check_or_raise`）→ stream → record_outcome，全程
fail-open（断路器自身故障绝不阻塞 LLM 调用）。与 R16/R17 同构——抽取因果支柱
（fail-open 铁律 + BreakerOpen→服务不可用信号 + 基于状态码的样本过滤 + 双传输对称），
砍掉同步 guard 仪式（async generator 场景下根本套不上）。

### 融合结论（因果支柱 1:1 保留，sync guard 仪式 async 场景下砍净）

- ✅ **保持**：fail-open 铁律——R17 `guard()` 的 fail-open 契约（断路器坏绝不阻塞业务）
  在异步生成器场景下用三个独立辅助函数手动复现。`resolve_breaker` 任何故障（导入错/
  注册表 None/构建失败/`get` 故障）→ None；`check_or_raise`/`record_outcome` 拿到
  None→无操作。业务路径**照常执行而非不执行**——断路器是观测与保护，永不是依赖。
- ✅ **保持**：BreakerOpen → LLMError(503) 信号转换——断路器 open 态（或 half_open 无
  探测槽）的 `BreakerOpen` 信号翻译成传输层已有的 `LLMError(status_code=503)`。调用方
  看到的"服务不可用"与上游真实 503 无法区分，**错误契约不破**——core.py 的现有错误
  处理无需任何改动就能吃下熔断信号。check 抛的 `LLMError(503)` 在 try 块**之外**，所以
  不会被 `except anthropic.APIError` 误捕（类型不同，且语义上 check 是 pre-gate）。
- ✅ **保持**：基于状态码的样本过滤——镜像 grok 的"客户端错误不污染健康窗口"原则。成功
  流总记 SUCCESS；失败流仅当 `status_code ∈ config.failure_codes` 时记 FAILURE（500 等
  是下游病了）；客户端错误（400/401/404）是调用方自己的锅，**中性跳过**（非健康样本，
  不进窗口）——避免自伤型 400 把断路器误跳闸；未知状态（None = 连接断/超时被 SDK 压平）
  **保守记 FAILURE**（可能是下游病了，min_samples/error_rate 双闸吸收孤立抖动）。
- ✅ **保持**：双传输对称——Anthropic + OpenAI transport 完全镜像接入（相同 import、
  相同 try/except/else 三段式、相同的 pre-check 放在 `self._thinking_count = 0` 之后）。
  注册表 key 分别 `llm:anthropic` / `llm:openai`，`CircuitBreakerRegistry.get` 按 key
  各自惰性创建独立实例——两个下游的健康状态互不污染。
- ✅ **保持**：惰性 app 导入规避循环——`resolve_breaker` 在**函数体内**
  `from ...app import ensure_breaker_registry`（而非模块顶层），规避 transports →
  app → core → llm → transports 循环导入。与工具层 `ensure_fs_bus` 同款手法，已验证
  无副作用。
- ❌ **放弃**：同步 `guard()` 包裹——async generator 跨 await 点，sync `with`
  context manager 根本套不进来（`__enter__`/`__exit__` 不感知 await）。改手动
  `check_or_raise` + `record_outcome`，逻辑等价但贴合流式语义，且让 pre-check 处于
  try 块之外（避免 check 的 503 被 APIError except 误捕）——这是比 guard 更精确的
  安放位置。
- ❌ **放弃**：部分消耗流的 outcome 记录——流跑到一半消费者取消（`GeneratorExit`），
  既不进 `except APIError`（类型不对）也不进 `else`（没正常跑完），即**不记 outcome**
  （中性）。这是正确的：半截流既非成功也非明确的下游故障，记任何样本都会污染窗口。
  grok 的 guard 在 block 异常时记 FAILURE，但 async 取消不是 block 异常，语义不同。

### 交付

- `minimax_code/agent/transports/_breaker.py`（新模块，~117 行，连接 R17 内核与传输层）：
  模块 docstring 阐明 fail-open 铁律 + 异步生成器适配理由。三个故障开放适配器——
  `resolve_breaker(key)`（惰性导入 `app.ensure_breaker_registry`，三段 try 各自
  `# noqa: BLE001` fail-open 返回 None）+ `check_or_raise(breaker)`（None→无操作；
  `breaker.check()`；`BreakerOpen`→`raise LLMError(..., status_code=503) from exc`；
  其他异常吞掉）+ `record_outcome(breaker, *, success, status_code=None)`（None→无操作；
  SUCCESS 总记；FAILURE 仅在 `is_failure_status(status_code)` 时记，None 状态保守记
  FAILURE；记录故障吞掉）。`__all__` 登记三符号。
- `minimax_code/agent/transports/anthropic_transport.py`（编辑，3 处）：① 加 import
  `from ._breaker import check_or_raise, record_outcome, resolve_breaker`；② `stream_chat`
  起头（`self._thinking_count = 0` 之后）加 `breaker = resolve_breaker("llm:anthropic");
  check_or_raise(breaker)` + 注释；③ `except anthropic.APIError` 块加 `record_outcome(
  breaker, success=False, status_code=getattr(exc,"status_code",None))`，新增 `else:
  record_outcome(breaker, success=True)`。
- `minimax_code/agent/transports/openai_transport.py`（编辑，3 处）：与 anthropic 完全
  对称（`openai.APIError` + `"llm:openai"` key + `"OpenAI API error"` 消息）。
- `tests/test_transport_breaker.py`（新，16 测）：① 辅助函数单元测（12 个）——`resolve_breaker`
  （registry None→None / 同 key 同实例 / registry 故障 fail-open）、`check_or_raise`
  （None 无操作 / open→503 翻译 / 内部故障 fail-open）、`record_outcome`（None 无操作 /
  SUCCESS 记 / FAILURE+failure_status 记 / 客户端 400 跳过 / None 状态保守记 / 内部故障
  fail-open）；② OpenAITransport 集成测（4 个，伪造 SDK client）——成功流记 SUCCESS /
  open breaker 返回 503 / APIError 触发 `record_outcome(False, 500)`（spy 监视）/ registry
  None 时无保护照常跑（fail-open 铁律端到端证明）。关键伪造：`_FakeAPIError(openai.APIError)`
  绕过 SDK 初始化签名挂 `status_code`；`_FakeStream`（`__aiter__`/`__anext__`）；
  `_FakeClient.chat.completions.create` 返回伪造流或抛错。

### 验证

- ✅ `ruff check`（`_breaker.py` + 2 transport + test_transport_breaker.py）：
  **All checks passed**——`_breaker.py` 导入顺序 `ruff --fix` 自动修（相对导入按点级别
  降序：`...resilience`（3 级）在 `..types`（2 级）前，同级别按名称排），4 文件一次过检。
- ✅ `pytest tests/test_transport_breaker.py -q`：**16 passed**——辅助函数 fail-open 全路径、
  状态码过滤、503 翻译、双传输集成全绿灯。
- ✅ `pytest -q` 全套：**1092 passed**（1076 + 16 新增），零失败、零回归——证明传输层
  惰性激活熔断器不影响 agent 启动、不影响既有 1076 测、fail-open 设计在无注册表环境下
  完全透明（registry None 时业务路径零感知）。

### YAGNI 边界（本轮不做）

- ❌ 不接 `core.py` 重试策略——`agent/core.py` 的 LLM 调用重试目前不区分"熔断 503"与
  "上游 503"，可能对已熔断的 503 仍重试（白白烧配额、延长用户感知故障）。`RetryPolicy
  .classify` 已就绪（R17），接入 core 重试循环让其尊重断路器 503（不重试）是独立的
  core 层改动，留待 R19。
- ❌ 不接 `MockTransport`——MockTransport 总成功，无失败可记，不需要熔断器。YAGNI。
- ❌ 不接 metrics/observability——Observer 钩子（`on_state_change`/`on_outcome`）已留，
  把状态转换喂给 `TelemetryEngine` 是独立的桥接工作（R20+），不混入传输接入。
- ❌ 不做断路器配置暴露 RPC——运维面板手动调参（强制 open/close、改阈值）当前无需求，
  YAGNI。
- ❌ 不持久化断路器状态——状态全在内存（进程重启 reset 为 closed），跨重启保持 open 态
  会引入时序复杂度，待真实场景驱动。
- ❌ 不做 Anthropic 传输集成测——OpenAI 集成测已证传输层三步式（pre-check/except/else）
  正确，Anthropic 是镜像（同款 import + 同款结构），由对称性 + 辅助函数单元测覆盖。
  重复一份 Anthropic 集成测是冗余，违背 DRY。

### Commit

`feat(platform): R18 wire circuit breaker into LLM transports (fuse grok xai-circuit-breaker)`

## R19 — 重试尊重熔断信号：breaker_open 标记（fuse grok xai-circuit-breaker）（阶段 B 第 9 轮）

**本轮目标**：R18 把熔断器接进传输层，OPEN 时 `check_or_raise` 抛 `LLMError(503)`。但
core 的 `with_retry` 把 503 判 `RETRYABLE` 无脑重试 3 次——**熔断器说"别打我了"，
with_retry 却当普通 5xx 反复撞门**。这是因果矛盾：熔断的整个意义就是"快速失败、保护下游"，
重试层却把这个信号抹平成普通瞬时错误。本轮 fuse grok-build 的"``BreakerOpen`` 是 terminal
disposition"语义——grok 用独立异常类型让 `classify_exception` 一眼识别；但 R18 已决定把
`BreakerOpen` 翻译成 `LLMError(503)`（保持传输错误契约），类型信息被抹平。本轮的工程抉择：
**不推翻 R18 的翻译，而在 LLMError 上叠加 `breaker_open` 布尔标记重新打通信号**。熔断器
拒流时置 True，`classify_exception` 检测到判 TERMINAL，`with_retry` 立即放弃。向后兼容
（默认 False）、因果正确、最小改动。

### 融合结论（因果支柱 1:1 保留，类型识别改为标记识别）

- ✅ **保持**：grok 的 "BreakerOpen = terminal" 语义——grok 用独立异常类型让 `with_retry`
  的分类器识别熔断信号判 terminal（不重试）；我们用 `breaker_open` 标记（因 R18 已翻译成
  LLMError），**因果等价**：都是"熔断器拒流 → 不重试 → 快速失败给上层"。
- ✅ **保持**：标记穿透翻译层——R18 的 `BreakerOpen → LLMError(503)` 翻译保留了传输错误
  契约（调用方统一 `except LLMError`），但抹平了类型。`breaker_open` 标记**叠加在翻译之上**
  重新打通熔断信号，**无需推翻 R18 的决定**。标记是 LLMError 的可选字段，向后兼容（默认
  False），既有 LLMError 零感知。
- ✅ **保持**：classify_exception 优先级铁律——`breaker_open` 检查在 `status_code` 检查
  **之前**（最高优先级）。即使熔断 LLMError 带的 503 平时是 RETRYABLE，`breaker_open=True`
  直接判 TERMINAL，覆盖 status 矩阵。这条优先级是熔断"快速失败"意图穿透重试层的关键。
- ✅ **保持**：双熔断点一致标记——transport 层 `check_or_raise`（`llm:openai`/`llm:anthropic`）
  与 core 层 `_call_llm_with_resilience` 的 `"llm"` 熔断器 check，**两处** `BreakerOpen →
  LLMError` 都置 `breaker_open=True`。core 层 check 虽在 `with_retry` 外（本就不重试），但
  标记保持"凡熔断信号皆带标记"的语义一致 + 防御未来把 check 移进重试循环的重构。顺带补齐
  core 层 LLMError 原本缺失的 `status_code=503`（之前是 None，语义不准）。
- ❌ **放弃**：统一 `agent/reliability` 与 `minimax_code/resilience`——两套可靠性栈并存是
  历史结果（R13 建 `agent/reliability` 给 core 用，R17 建 `minimax_code/resilience` 给
  transport 用）。统一需迁移 core.py + 所有调用方，是跨多轮重构，违背轮次独立。R19 通过
  **共享的 `LLMError`**（两栈都用的类型）打通信号，不碰包结构。
- ❌ **放弃**：退回让 BreakerOpen 直接冒泡——R18 的 `LLMError(503)` 翻译契约保留（不退回
  原始 BreakerOpen 异常冒泡）。`breaker_open` 标记是叠加而非替换，R18 的"调用方统一处理
  服务不可用"决定不动摇。

### 交付

- `minimax_code/agent/types.py`（编辑）：`LLMError.__init__` 加 `breaker_open: bool = False`
  参数 + 赋值 + docstring 扩展（说明 R19 用途："endpoint not sick in a way another attempt
  can fix, it has been told to back off"，fuse grok "BreakerOpen = terminal disposition"）。
- `minimax_code/agent/reliability/retry.py`（编辑）：① 模块 docstring 分类矩阵顶部加
  `LLMError with breaker_open=True → TERMINAL` 条目；② `classify_exception` 的 LLMError
  分支最前面加 `if getattr(exc, "breaker_open", False): return Disposition.TERMINAL`——
  在 `status_code` 检查之前，最高优先级，带 R19 注释。
- `minimax_code/agent/transports/_breaker.py`（编辑）：`check_or_raise` 的 `raise LLMError(...)`
  加 `breaker_open=True`（保留 `status_code=503`）。
- `minimax_code/agent/core.py`（编辑）：`_call_llm_with_resilience` 的 core 层 `BreakerOpen
  → LLMError` 加 `status_code=503, breaker_open=True`（补齐原本缺失的 status_code + 一致
  标记）。
- `tests/test_reliability.py`（编辑）：① `test_classify_breaker_open_is_terminal_regardless_of_status`
  ——breaker_open+503→TERMINAL / 无 flag 的 503 仍 RETRYABLE / breaker_open 无 code→TERMINAL
  （覆盖优先级铁律）；② `test_breaker_open_error_is_not_retried`——`with_retry` 端到端：factory
  抛 breaker_open LLMError → 只调 1 次（`calls == 1`）、不 sleep（`sleeps == []`），证明 fast-fail。
- `tests/test_transport_breaker.py`（编辑）：`test_check_or_raise_translates_open_to_503` +
  `test_transport_open_breaker_returns_503` 各加 `assert ei.value.breaker_open is True`，锁定
  传输层熔断信号带标记的契约。

### 验证

- ✅ `ruff check`（types.py + retry.py + _breaker.py + core.py + 2 测试文件）：
  **All checks passed**——6 文件一次过检（LLMError 的 keyword-only `breaker_open` 满足规则、
  classify_exception 的 `getattr` 防御未设属性场景）。
- ✅ `pytest tests/test_reliability.py tests/test_transport_breaker.py -q`：**46 passed**
  （含 2 个 R19 新测）——classify 优先级铁律、with_retry fast-fail 全绿灯。
- ✅ `pytest -q` 全套：**1094 passed**（1092 + 2 新增），零失败、零回归——证明 breaker_open
  标记向后兼容（既有 LLMError 默认 False 不影响现有重试行为）、classify 优先级调整不破坏
  既有 RETRYABLE/TERMINAL 分类。

### YAGNI 边界（本轮不做）

- ❌ 不统一 `agent/reliability` 与 `minimax_code/resilience`——两套栈并存，统一是跨轮重构。
  R19 通过共享 LLMError 打通信号已足够消除当前痛点，包结构留给未来真实需求驱动。
- ❌ 不给 `LLMStreamTimeout` 加 breaker_open——超时是"下游慢"（RETRYABLE），不是"熔断器拒流"
  （TERMINAL），语义不同；超时仍重试。
- ❌ 不把 breaker_open 暴露给前端——前端 `agent.status` 事件不携带 breaker_open（它是内部重试
  分类信号）。前端 UI 显示熔断态（"模型服务暂不可用，N 秒后重试"）是独立的可观测性工作
  （Observer 钩子 → TelemetryEngine → 前端事件桥接），留待 R20+。
- ❌ 不做熔断器手动重置 RPC——熔断态自动恢复（half_open 探测成功→close），运维面板手动
  force-open/close/reset 当前无需求，YAGNI。
- ❌ 不改 `with_retry` 的 `on_retry` 回调签名——breaker_open 错误 fast-fail 根本不触发
  `on_retry`（没重试），回调签名不变。
- ❌ 不做 RetryPolicy 持久化——`RetryPolicy.llm()` / `.tool()` 预设是代码常量，运行时调参
  （改 max_attempts/backoff）当前无配置入口需求。

### Commit

`feat(platform): R19 retry respects breaker-open signal (fuse grok xai-circuit-breaker)`

## R20 — 可靠性栈可观测性闭环：熔断/重试事件→TelemetryEngine（fuse grok xai-circuit-breaker Observer + xai-grok-telemetry）（阶段 B 第 10 轮）

**本轮目标**：R17-R19 把熔断器 + 重试建得功能完备但**完全静默**——熔断 OPEN/恢复、
重试在哪一拨放弃，这些关键事件**一个都没**流入 R11 建好的 TelemetryEngine。运维盲区：
熔断器何时 trip、何时恢复、重试在哪一拨失败，全靠日志肉眼 grep。本轮 fuse grok 的
`xai-circuit-breaker` Observer 三件套（`on_state_change`/`on_outcome`/`attach`）+
`xai-grok-telemetry` 类型化事件集——给 `reliability` 栈的 CircuitBreaker 加观察者接口，
状态转换走**稳定 reason 闭集**（`trip`/`open_elapsed`/`probe_success`/`probe_failure`），
适配器把转换桥到 TelemetryEngine 的 `CIRCUIT_BREAKER` 事件；`with_retry` 的 `on_retry`
钩子 emit `RETRY` 事件（携带 session_id）。**端点级 vs 轮次级作用域**是核心区分：熔断
`session_id=None`（一个 `llm` 熔断器影响所有会话），重试 `session_id=_current_session_id`
（归因到对话）。本轮收尾阶段 B（安全与可观测 R11-R20），主路径因果闭环。

### 融合结论（因果支柱 1:1 保留，ambient 上下文改为闭包）

- ✅ **保持**：grok Observer 三件套接口——`on_state_change(old,new,reason)` /
  `on_outcome(outcome)` / `attach_observer`。接口**与 `resilience.breaker` 完全一致**，
  故单个适配器未来可桥接任一栈。`NoopObserver` 默认零开销（telemetry 关闭时无任何
  分配/调用）。
- ✅ **保持**：grok 稳定 reason 闭集——`"trip"` / `"open_elapsed"` / `"probe_success"` /
  `"probe_failure"`（小写）。稳定字符串让前端/dashboard 把 reason 映射到 UI 不需解析
  自由文本。**四个转换点全部接线**（`_trip` / `check` 的 OPEN→HALF_OPEN / `record` 的
  probe_success / probe_failure）。
- ✅ **保持**：grok 类型化事件集——`CIRCUIT_BREAKER` + `RETRY` 两个 EventType 加入闭合
  StrEnum。`CIRCUIT_BREAKER` payload=`{breaker,old,new,reason}`；`RETRY`
  payload=`{attempt,max_attempts,delay_s,error_type,breaker_open}`（`breaker_open` 让
  dashboard 区分"重试撞熔断 TERMINAL" vs "普通瞬时重试"）。
- ✅ **保持**：fail-open **双层防御**——CircuitBreaker `_notify_state_change`/`_notify_outcome`
  包 try/except（适配器故障绝不破坏状态机）；适配器自身 `engine is None` 短路（telemetry
  off 零开销）+ `_on_llm_retry` 再包一层（telemetry 不破坏重试循环，`with_retry` 本身也包）。
  belt and braces——可观测性任何环节挂掉都不影响可靠性。
- ✅ **保持**：grok "endpoint-scoped vs turn-scoped" 作用域区分——`CIRCUIT_BREAKER`
  `session_id=None`（端点级：一个 `llm` 熔断器影响所有会话，归因到任一会话都误导
  dashboard）；`RETRY` `session_id=_current_session_id`（轮次级：重试归因到对话）。这是
  grok `tokio::task_local` ambient `TelemetryCtx` 的 Python 等价——**闭包读
  `_current_session_id` + engine_getter 惰性 callable，避开 contextvars 仪式**。
- ❌ **放弃**：contextvars 全栈贯通——grok 用 ambient context 让任意深度调用拿到
  engine+session。Python 等价是 contextvars，但熔断端点级（session_id=None）、重试
  `_current_session_id` 闭包，**两者正确归因都不需要 contextvars**。引入它是为未来"每
  会话独立熔断器"铺路，当前一个共享 `llm` 熔断器，YAGNI。
- ❌ **放弃**：`resilience` 栈（transport）同步接线——`reliability` 栈（core 主路径）已
  闭环因果。transport 的 `resilience.breaker` 也有 Observer 接口（R17 建的），但传输层
  熔断事件桥接留 **R21**（对称性收尾）。本轮聚焦 core 主路径。
- ❌ **放弃**：`on_outcome` emit 事件——`on_outcome` 每次调用一个事件（高频低价值），
  event stream 会被淹没。指标层（`MetricsRegistry`）更适合 outcome 频率。适配器
  `on_outcome` 是 no-op（接口保留为 grok 兼容）。

### 交付

- `telemetry/events.py`（编辑）：EventType 加 `CIRCUIT_BREAKER` + `RETRY`（闭合 StrEnum
  扩展），带 R20 注释详述作用域（CIRCUIT_BREAKER 端点级 session_id=None / RETRY 轮次级
  session_id=_current_session_id）+ fuse 来源（xai-circuit-breaker Observer + xai-grok-telemetry）。
- `agent/reliability/circuit_breaker.py`（编辑）：① `Observer` ABC + `NoopObserver`
  （接口与 resilience.breaker 一致，docstring 锁定"方法不得 raise"+稳定 reason 闭集文档）；
  ② `CircuitBreaker.__init__` 加 `observer` 参数（默认 NoopObserver）+ `attach_observer`
  （幂等，core 每轮 re-attach 同一观察者不重复事件）+ `_notify_state_change`/`_notify_outcome`
  （fail-open，`# noqa: BLE001`）；③ **四转换点接线稳定 reason**——`check()` OPEN→HALF_OPEN
  `"open_elapsed"`，`record()` HALF_OPEN FAILURE→`_trip(reason="probe_failure")` /
  SUCCESS→CLOSED `"probe_success"`，`_trip(old→OPEN)` reason 默认 `"trip"`。
- `agent/reliability/__init__.py`（编辑）：导出 `Observer` + `NoopObserver`（import + `__all__`）。
- `telemetry/observer_adapter.py`（新建）：`ReliabilityTelemetryObserver`——
  `engine_getter` 惰性 callable（解决 engine 在构造后从 app.py 注入的时序，避免持有陈旧
  引用）+ `name` 参数；`on_state_change` emit `CIRCUIT_BREAKER`（severity: OPEN→ERROR /
  恢复→INFO，`session_id=None`）；`on_outcome` no-op。模块 docstring 详述端点级作用域
  决策 + lazy getter 的 Python-ambient 等价论证。
- `agent/core.py`（编辑）：① 构造函数**延迟导入** + 创建 `self._llm_breaker_observer`
  （`lambda: self.telemetry_engine` 闭包，规避循环导入 + 解决 engine 后期注入——与同文件
  `_record_audit` 的延迟导入风格一致）；② `_call_llm_with_resilience` 接线
  `breaker.attach_observer(self._llm_breaker_observer)`（每轮幂等 re-attach，捕获
  late-arriving engine）+ `with_retry(..., on_retry=self._on_llm_retry)`；③ 新增
  `_on_llm_retry(attempt, exc, delay)` 方法 emit `RETRY` 事件（`session_id=_current_session_id or None`,
  `severity=WARN`，payload 含 `attempt`/`max_attempts`/`delay_s`/`error_type`/`breaker_open`）。
- `telemetry/__init__.py`（编辑）：导出 `ReliabilityTelemetryObserver`（公开 API 完整性，
  其他消费者可从顶层包导入）。
- `tests/test_reliability.py`（编辑）：import 加 `Observer`；追加 `_RecordingObserver` +
  **8 个 observer 测试**——4 转换点 reason 正确（trip/open_elapsed/probe_success/probe_failure）、
  `on_outcome` 每次记录触发、默认 `NoopObserver` 零开销、observer 故障 fail-open（`_Boom`
  不破坏状态机）、`attach_observer` 替换活跃观察者。
- `tests/test_telemetry_observer.py`（新建）：**6 个适配器契约测试**——trip→ERROR 事件契约
  （payload/severity/`session_id=None`）、恢复转换→INFO、probe_failure→ERROR、engine None
  零开销不报错、`on_outcome` no-op 不 emit、多 breaker 按 `name`/`payload.breaker` 区分。

### 验证

- ✅ `ruff check`（events.py + circuit_breaker.py + reliability/__init__.py +
  observer_adapter.py + core.py + telemetry/__init__.py + 2 测试文件）：
  **All checks passed**——8 文件一次过检（Observer ABC + NoopObserver、四转换点稳定 reason、
  `_notify_*` 的 `# noqa: BLE001` fail-open 注解、`_on_llm_retry` 闭包、`_Boom` 局部类
  `# noqa: ANN001` 防御）。
- ✅ `pytest tests/test_reliability.py tests/test_telemetry_observer.py tests/test_telemetry.py -q`：
  **60 passed**（含 14 个 R20 新测）——4 转换点 reason 闭集、适配器事件契约、severity 策略
  （OPEN→ERROR/恢复→INFO）、fail-open（observer 故障不破坏状态机）、engine None 零开销、
  多 breaker 区分全绿灯。
- ✅ `pytest -q` 全套：**1108 passed**（1094 R19 基线 + 14 新增：8 observer 触发 + 6 适配器
  契约），零失败、零回归——证明 Observer 接口向后兼容（默认 NoopObserver 不影响既有熔断
  行为）、EventType 闭合扩展不破坏既有事件、core 接线不改变 LLM 调用因果、延迟导入规避
  循环成功（core 仍正常加载）。

### YAGNI 边界（本轮不做）

- ❌ 不接 `resilience` 栈（transport）——`reliability` 栈（core）已闭环主路径因果；
  transport 的 `resilience.breaker` 桥接留 **R21**（对称性收尾，单适配器桥接）。
- ❌ 不加 contextvars——熔断端点级（`session_id=None`）、重试 `_current_session_id` 闭包，
  两者正确归因都不需要 contextvars。引入它是为"每会话独立熔断器"铺路，当前一个共享 `llm`
  熔断器，YAGNI。
- ❌ 不把熔断态暴露到前端 UI——`CIRCUIT_BREAKER` 事件进了 TelemetryEngine（in-memory bus），
  但前端 `agent.status` 事件未桥接。前端横幅（"模型服务暂不可用，N 秒后重试"）是独立 UI
  工作，留 **R22**。
- ❌ 不 emit `on_outcome` 事件——outcome 高频低价值（每次调用一个），event stream 会被
  淹没。指标层（`MetricsRegistry`）更适合 outcome 频率，留待真实 dashboard 需求。
- ❌ 不做 `on_probe_admission` 钩子——grok Observer 有 `on_probe_admission`（HALF_OPEN
  探测准入回调），当前 `half_open_max_probes=1` 单探测，准入逻辑无外部决策需求，YAGNI。
- ❌ 不做熔断指标聚合 RPC——`TelemetryEngine.metrics()` 已有 per-session 指标；熔断转换
  计数（trip 次数 / OPEN 累计时长）当前无 dashboard 消费需求。

### Commit

`feat(platform): R20 reliability telemetry loop (fuse grok xai-circuit-breaker Observer)`

## R21 — 传输层熔断可观测性对称接线：resilience 栈 Observer→TelemetryEngine（fuse grok xai-circuit-breaker Observer 对称收尾）（阶段 C 第 1 轮）

**本轮目标**：R20 给 `reliability` 栈（core 主路径）的 CircuitBreaker 闭环了可观测性，
但**留了 R21**——`resilience` 栈（LLM 传输层，R17 建）的同步 CircuitBreaker 还是**完全
静默**。传输层熔断 OPEN/恢复事件同样进不了 TelemetryEngine，可观测性盲区只补了一半。本轮
对称收尾：给 `resilience` 栈的同步 CircuitBreaker 加与 reliability **同构**的 Observer 接口
（`attach_observer` + fail-open `_notify_*`），用**同一个** `ReliabilityTelemetryObserver`
适配器接线，让两条可靠性栈的事件都流入 TelemetryEngine 且按 `name` 区分（reliability 熔断
= `"llm"`，transport 熔断 = endpoint key 如 `"llm:anthropic"`）。**核心发现**：R17 早已在
`resilience.breaker` 建了 Observer ABC + `NoopObserver` + `CircuitBreaker(observer=)`，但缺
`attach_observer` 且 `_set_state`/`record` 直调 `self._observer.on_*`（无 fail-open 包装）；
适配器原用 `new is BreakerState.OPEN`——而 `resilience.BreakerState.OPEN is
reliability.BreakerState.OPEN` 为 `False`（不同类！），跨栈桥接存在**潜在 bug**。本轮改
`is`→`==`（StrEnum 值比较跨类有效），单适配器真正桥接两栈。开局阶段 C（智能体协作与感知
R21-R30）。

### 融合结论（对称性收尾，单适配器双栈）

- ✅ **保持**：grok Observer 对称三件套——`resilience` 栈的 `attach_observer` /
  `_notify_state_change` / `_notify_outcome` 与 `reliability` 栈**方法签名、fail-open 语义、
  `# noqa: BLE001` 注解逐字一致**（reliability/circuit_breaker.py:194-221 的镜像）。`Observer`
  ABC + `NoopObserver` 默认零开销不变。
- ✅ **保持**：grok 稳定 reason 闭集——`resilience` 栈的转换 reason（`"error-rate threshold
  breached"` / `"cool-down elapsed"` / `"half-open probe succeeded"` / `"half-open probe
  failed"`）原样透传到适配器，无重写。reason 闭集让前端映射 UI 不需解析自由文本。
- ✅ **保持**：grok "registry-as-factory" 模式——`CircuitBreakerRegistry.attach_observer_factory`
  在**一个注入点**给整支熔断器舰队挂观察者（新建 breaker 工厂注入 + 已有 breaker **retrofit**
  回溯接线），是 `xai-circuit-breaker` `ObserverRegistry` 的 Python 等价。app.py 用这一个 hook
  把所有传输层熔断器指向共享 telemetry 观察者。
- ✅ **保持**：fail-open **双层防御**——breaker `_notify_*` 包 try/except（观察者故障绝不破坏
  状态机）+ 适配器 `engine is None` 短路 + app `_wire_breaker_telemetry` 再包一层（telemetry
  接线失败不阻断 registry 启动）。belt and braces——可观测性任何环节挂掉都不影响可靠性。
- ✅ **保持**：grok "endpoint-scoped session_id=None" 作用域——传输层熔断 `session_id=None`
  （端点级，同 R20 reliability 栈语义），事件按 `name` 区分两栈（`llm` vs `llm:anthropic`）。
- ❌ **放弃**：`new is BreakerState.OPEN` 身份比较——**R21 关键修正**。`resilience` 栈有独立
  `BreakerState` StrEnum（同 wire 值 `"open"`，但**不同类**），跨栈 `is` 为 `False`。StrEnum
  `==` 比较底层 str，跨类有效。改 `is`→`==` 让**同一个**适配器实例桥接任一栈——这是"单适配器
  双栈"的语义基石（已 Bash 验证：`resilience.BreakerState.OPEN == reliability.BreakerState.OPEN`
  → `True`）。
- ❌ **放弃**：contextvars 全栈贯通——传输层熔断端点级（`session_id=None`），适配器 `name`
  参数承载端点 key，正确归因**不需要 contextvars**。YAGNI（当前一个共享传输熔断器配置）。
- ❌ **放弃**：`on_outcome` emit 事件——同 R20，outcome 高频低价值（每次调用一个），event
  stream 会被淹没。适配器 `on_outcome` no-op（接口保留为 grok 兼容）。指标层更适合 outcome
  频率，留待真实 dashboard 需求。

### 交付

- `telemetry/observer_adapter.py`（编辑）：① 模块 docstring 泛化为"either circuit-breaker
  stack → telemetry"，列举两栈 + 鸭子类型解释；② **`is`→`==` 关键修正**——`severity =
  Severity.ERROR if new == BreakerState.OPEN` 带详注（跨栈 StrEnum 身份 vs 值比较，R21 bridge
  基石）；③ docstring 加"Two-stack bridge (R21)"段——鸭子类型子类化作类型指南 + 跨栈 `==`
  论证。
- `agent/minimax_code/resilience/breaker.py`（编辑）：① `Observer` docstring 更新（移除"direct
  callers do not [swallow]"，因 R21 在 `_notify_*` 层加 fail-open）；② 加 `attach_observer`
  （幂等替换，registry factory 用）+ `_notify_state_change`/`_notify_outcome`（fail-open，
  `# noqa: BLE001`，镜像 reliability/circuit_breaker.py:194-221）；③ `_set_state` 改调
  `_notify_state_change`、`record` 改调 `_notify_outcome`（替换原裸 `self._observer.on_*`）。
- `agent/minimax_code/resilience/registry.py`（编辑）：① imports 加 `Callable` +
  `Observer`；② `__init__` 加 `_observer_factory` 字段；③ `attach_observer_factory(factory)`
  （安装工厂 + retrofit 已有 breaker）；④ `get` 在创建时注入 observer
  （`CircuitBreaker(key, cfg, observer=observer)`）。
- `agent/minimax_code/app.py`（编辑）：① 新增 `_wire_breaker_telemetry(registry)` helper
  （**惰性 import** `ReliabilityTelemetryObserver` 防循环 + fail-open `try/except`，工厂闭包
  用 `ensure_telemetry_engine` 作 engine_getter）；② `ensure_breaker_registry` 构建后调用
  `_wire_breaker_telemetry(_BREAKER_REGISTRY)`——传输层熔断器舰队自动可观测。
- `tests/test_resilience.py`（编辑）：追加 `_RecordingObserver`（子类 `NoopObserver`）+
  **7 个 R21 测试**——4 个 breaker 级（trip/outcome 触发、HALF_OPEN 恢复转换、`attach_observer`
  替换活跃观察者、observer 故障 fail-open 不破坏状态机）+ 3 个 registry factory 级（新建
  breaker 注入、retrofit 已有 breaker、`None` 解除接线）。
- `tests/test_telemetry_observer.py`（编辑）：追加 **2 个跨栈桥接测试**——纯适配器（resilience
  `BreakerState` 跨栈 `==` 正确映射 OPEN→ERROR）+ 端到端（resilience registry factory 接
  telemetry observer，trip 时引擎收到 `CIRCUIT_BREAKER` ERROR 事件，按 `name="llm:anthropic"`
  区分）。

### 验证

- ✅ `ruff check`（breaker.py + registry.py + observer_adapter.py + app.py +
  test_resilience.py + test_telemetry_observer.py）：**All checks passed**——6 文件一次过检
  （`_notify_*` 的 `# noqa: BLE001` fail-open 注解、`_Boom`/`_RecordingObserver` 局部类
  `# noqa: ANN001`、`Callable`/`Observer` 导入排序、`is→==` 修正）。`test_telemetry_observer.py`
  有一处 aliased import 被 isort 拆块（项目规则），功能无影响。
- ✅ `pytest tests/test_resilience.py tests/test_telemetry_observer.py tests/test_transport_breaker.py -q`：
  **64 passed**（含 9 个 R21 新测：7 resilience observer/registry + 2 跨栈桥接）——
  breaker 级 fail-open、registry factory 注入/retrofit/解除、跨栈 `==` 严重性映射、端到端
  registry factory→telemetry 闭环全绿灯；`test_transport_breaker.py` 零回归证明 registry
  接口扩展（可选 factory）向后兼容。
- ✅ `pytest -q` 全套：**1117 passed**（1108 R20 基线 + 9 新增），零失败、零回归——证明
  resilience Observer 扩展向后兼容（默认 NoopObserver 不影响既有熔断行为）、registry factory
  可选注入不破坏传输层、adapter `is→==` 不影响 reliability 栈（同栈 `==` 与 `is` 等价）、
  app.py 惰性 import 规避循环成功（registry 仍正常初始化）。

### YAGNI 边界（本轮不做）

- ❌ 不把熔断态暴露到前端 UI——`CIRCUIT_BREAKER` 事件进了 TelemetryEngine（in-memory bus），
  但前端 `agent.status` 事件未桥接。前端横幅（"模型服务暂不可用，N 秒后重试"，区分 reliability
  vs transport 熔断）是独立 UI 工作，留 **R22**（AgentStatusData 联合类型扩展）。
- ❌ 不为 reliability registry 加对称 factory——reliability 栈由 core.py **每 turn** re-attach
  （捕获 late-arriving engine），是**不同的接线模式**（turn-scoped vs endpoint-scoped registry）。
  强行对称会引入两套 registry 语义，YAGNI。
- ❌ 不加 contextvars——传输层熔断端点级（`session_id=None`），适配器 `name` 参数承载端点 key，
  正确归因不需要 contextvars。引入它是为"每会话独立熔断器"铺路，YAGNI。
- ❌ 不 emit `on_outcome` 事件——同 R20，outcome 高频低价值，event stream 会被淹没。指标层
  更适合 outcome 频率。
- ❌ 不做熔断指标聚合 RPC——`TelemetryEngine.metrics()` 已有 per-session 指标；熔断转换计数
  （两栈分别 trip 次数 / OPEN 累计时长）当前无 dashboard 消费需求。

### Commit

`feat(platform): R21 transport breaker telemetry symmetry (fuse grok xai-circuit-breaker Observer)`

## R22 — 子 agent 配置解析层与 capability 过滤（fuse grok xai-grok-subagent-resolution）（阶段 C 第 2 轮）

**本轮目标**：R21 闭合了两条可靠性栈的可观测性。本轮切入**死代码复活**主线——
`SubAgentRuntime.build()` 接受 `SubAgentConfig.tool_allowlist` 但**完全忽略它**：sub-agent
声明的"只读"能力形同虚设，core 始终对全局 registry 构建，能看见每一个工具。这是 orchestrator
最大的死代码面。本轮融合 grok-build `xai-grok-subagent-resolution`（纯逻辑"resolve"阶段
crate），其 `lib.rs:17-26` 明确列出 `resolve_subagent_spec()` 组合 API + 能力过滤作为**未来
工作**——**R22 完成此设计意图**：建立无副作用的解析层把 sub-agent 配置（model + allowlist）
解析成冻结 `ResolvedSpec`，再映射成 `FilteredToolRegistry` 只读视图，让 agent loop **字面上
无法**看到或 dispatch 受限工具。**关键简化**：grok 四维优先级链（explicit > role default >
persona default > None）在 MiniMax 无 persona/role/isolation 概念，折叠为 `explicit config >
parent default`；**关键 DRY 发现**：`build()` 已做 `base_registry = registry or
get_default_registry()`（父级继承语义），所以 handler 传 `registry=None` 的当前路径自动以全局
registry 为父级，**handlers_agents.py 无需改动**——过滤在 build() 内自然解析。这是阶段 C 第 2
轮（智能体协作与感知 R21-R30）。

### 融合结论（纯逻辑解析 + 只读视图，复活 allowlist 死代码）

- ✅ **保持**：grok "pure-logic resolve 阶段分离"——`resolve_subagent_spec()` 是无 I/O、无全局的
  纯函数，读 config + 调用方提供的"可用工具名快照"，返回 `ResolvedSpec` 冻结数据类。解析器不
  持有 registry 引用，trivially testable（grok crate charter 的核心诉求）。
- ✅ **保持**：grok 优先级链（折叠）——`explicit config field > parent default`。grok 的四维链
  （explicit > role > persona > None）在 MiniMax 无 persona/role/isolation-worktree 等价物
  （YAGNI），折叠成二维。model: `config.model` or 父级默认（`"MiniMax-M3"`）。
- ✅ **保持**：grok `SubagentCapabilityMode` 二值——`ALL`（继承父级全量 surface，无 allowlist 时
  的 parent-inheritance fallback）/ `ALLOWLIST`（仅 config 命名的工具）。
- ✅ **保持**：grok "fail-soft 配置"——allowlist 命名父级实际不存在的工具时**静默丢弃**（不中止
  spawn），映射 grok 非致命错误配置策略（typo / 版本偏移不应让整个 spawn 失败）。
- ✅ **保持**：grok "schema 级过滤是最确定的门"——`FilteredToolRegistry.to_llm_functions()` 从
  function-calling schema 移除受限工具签名，**模型连看都看不到**受限工具，是最可靠的预防（比
  仅 dispatch 拦截更彻底）。
- ✅ **保持**：grok "filter live registry object"——MiniMax 不过滤静态 config struct（grok 的
  `filter_tool_config`），而是过滤 agent loop 实际持有的**实时 registry 引用**（鸭子类型视图），
  更贴合 MiniMax registry-as-object 模型。
- ❌ **放弃**：grok 四维优先级链的 role default / persona default / isolation-worktree 三个
  维度——MiniMax 无 persona 文件、无 role 系统、无 worktree 隔离，YAGNI。强行引入会制造无消费方
  的抽象。
- ❌ **放弃**：grok persona 文件加载、resume identity 校验、`EffectiveRuntimeConfig` 复合结构体
  的全部字段——MiniMax `ResolvedSpec` 仅保留 `model` + `capability_mode` + `allowed_tools` 三
  字段 + `is_restricted` 属性，最小够用。

### 交付

- `agent/minimax_code/orchestrator/resolution.py`（新建）：纯逻辑解析层。① `CapabilityMode`
  枚举（ALL/ALLOWLIST）；② `ResolvedSpec` 冻结数据类（`model` + `capability_mode` +
  `allowed_tools` + `is_restricted` 属性，frozen=True 防 spawn 中途篡改）；③
  `resolve_subagent_spec(config, *, available_tool_names, parent_model="MiniMax-M3")` 纯函数
  ——model 优先级 + available 去重保序 + allowlist 与 available 交集（fail-soft 丢弃未知名，
  顺序跟随 allowlist 调用方意图）+ 空 allowlist/None → ALL 模式；④ `FilteredToolRegistry` 只读
  鸭子类型视图——`base`/`allowed` 暴露属性（测试/运维内省），`get`/`has`/`list`/`names`/
  `to_llm_functions`/`to_openai_tools` 全过滤，`dispatch` 双重门（先 allowlist 检查短路
  `ToolResult.fail`，再委托 base），`register`/`unregister`/`clear` 抛 `NotImplementedError`
  （只读视图契约）。模块 docstring 详述 charter + 优先级模型 + fail-soft 策略。
- `agent/minimax_code/orchestrator/subagent.py`（编辑）：① 模块 docstring "(in a future phase)
  a tool registry filtered..." → "(since R22) a tool registry filtered to the row's
  ``tool_allowlist``"；② `build()` 重写——lazy import `AgentConfig`/`AgentCore` +
  `get_default_registry` + `FilteredToolRegistry`/`resolve_subagent_spec`；
  `base_registry = registry or get_default_registry()`（**父级继承语义**——handler 传 None 时
  全局默认即父级 surface）；`spec = resolve_subagent_spec(config,
  available_tool_names=base_registry.names())`；`spec.is_restricted` →
  `FilteredToolRegistry(base_registry, spec.allowed_tools)` 包裹，否则原样继承 base；
  `AgentConfig(model=spec.model, ...)` 用 R22 解析的有效模型；`AgentCore(llm, registry=effective,
  config)`；③ build() docstring 重写详述 R22 解析+映射流程；④ **顺带清理** TYPE_CHECKING 块未用
  的 `AgentCore` 导入（F401）——本文件正在编辑，预存 lint 债一并清掉。
- `agent/tests/test_subagent_resolution.py`（新建，**20 测试**）：`_FakeTool`/`_FakeRegistry`
  鸭子类型替身。覆盖——① resolve 优先级模型（ALL 继承父级全量、显式 model 覆盖父级默认、
  ALLOWLIST 切换 + 交集、未知名 fail-soft 丢弃、调用方顺序保持、`ResolvedSpec` 冻结不可变）；②
  `FilteredToolRegistry` 视图（get 隐藏受限、has 双条件真值、list+names 排除受限、to_llm_schema
  省略受限签名、dispatch 阻止受限 + 委托允许、register/unregister/clear 三方法抛
  `NotImplementedError`、base+allowed 暴露）；③ `build()` 集成（allowlist → filtered view 包裹、
  无 allowlist → base 原样继承、`registry=None` → 全局默认作父级、空 allowlist → ALL 非"零工具"）。

### 验证

- ✅ `ruff check`（resolution.py + subagent.py + test_subagent_resolution.py）：**All checks
  passed**——含 **1 手动修**（`test:166` B017 盲异常 `pytest.raises(Exception)` →
  `pytest.raises(AttributeError)`，因 `FrozenInstanceError` 在所有 Python 版本都是
  `AttributeError` 子类）+ **8 自动修**（resolution.py UP037 去引号；subagent.py I001 导入排序 +
  F401 删未用 `AgentCore` + UP037×4 去引号；test I001 导入排序）。subagent.py 的预存 lint 债
  （UP037 引号 / F401 未用导入 / I001 排序）顺带清掉——本文件正在编辑，规则允许。
- ✅ `pytest tests/test_subagent_resolution.py -q`：**20 passed in 0.76s**——resolve 优先级模型 +
  fail-soft 交集 + 调用方顺序 + 冻结不可变；FilteredToolRegistry 全过滤契约 + read-only 抛错 +
  dispatch 双重门；build() 三路径（allowlist 包裹 / 全量继承 / registry=None 全局默认）全绿灯。
- ✅ `pytest -q` 全套：**1137 passed**（1117 R21 基线 + 20 新增），98.96s，**零失败、零回归**——
  证明 build() 重写向后兼容（无 allowlist 路径原样继承、registry=None 仍走全局默认）、
  resolution.py 新模块不影响既有导入链、FilteredToolRegistry 鸭子类型能完整替换 ToolRegistry
  喂给 AgentCore、subagent.py docstring/导入清理不破坏运行时。

### YAGNI 边界（本轮不做）

- ❌ 不做 grok 四维优先级链的 role/persona/isolation 维度——MiniMax 无 persona 文件、role 系统、
  worktree 隔离概念，强行引入会制造无消费方的抽象。留待 sub-agent 真有 persona 需求时再扩。
- ❌ 不接前端 UI 暴露 allowlist 编辑——`FilteredToolRegistry` 已在后端生效，但前端 sub-agent
  配置面板尚未渲染 tool_allowlist 多选。这是独立 UI 工作（AgentConfigPanel 扩展），留 **R23+**。
- ❌ 不做 allowlist 与技能（skills）的交叉过滤——config 同时有 `skills` 和 `tool_allowlist`，当前
  allowlist 只作用于工具，技能调度独立。两者交叉（"只读 agent 只能用只读技能"）需技能 capability
  元数据，当前技能无此字段，YAGNI。
- ❌ 不做 registry 变更时 filtered view 热更新——`FilteredToolRegistry` 在 build() 时快照
  `allowed`，运行期 base registry 若 register 新工具，view 不自动纳入（除非新工具名在 allowlist）。
  sub-agent 生命周期内 base 极少变更，YAGNI。
- ❌ 不做 allowlist 校验 RPC（"这个 allowlist 引用了哪些不存在的工具"）——fail-soft 已静默丢弃，
  运维内省用 `view.allowed` / `view.base.names()` 手动比对即可，无独立 RPC 需求。

### Commit

`feat(platform): R22 subagent resolution + capability filter (fuse grok xai-grok-subagent-resolution)`

`feat(platform): R21 transport breaker telemetry symmetry (fuse grok xai-circuit-breaker Observer)`

## R23 — 两阶段并行工具调度（fuse grok xai-tool-runtime + xai-tool-protocol + xai-tool-types 并发模型）（阶段 C 第 3 轮）

**本轮目标**：R22 复活了 allowlist 死代码（sub-agent 配置解析 + capability 过滤）。本轮切入
**串行→并行**主线——`AgentCore` 的工具循环原本**严格串行**（`for call: await _dispatch_tool(call)`），
LLM 一轮返回多个 tool_calls 时逐个 await，I/O 完全不重叠（两个各 0.25s 的读 = 0.5s 等待）。
grok-build 的 `xai-tool-runtime` + `xai-tool-protocol` + `xai-tool-types` 三件套用**两阶段模型**：
阶段 1 串行 `prepare_tool_call`（参数解析 / 权限 / 计划模式 / 钩子——必须有序的），阶段 2 并行
execute（`FuturesUnordered`）。**关键规则**："仅当写入工具指向同一文件路径时才序列化"——
`tokio::sync::Mutex` 按 `lock_path_for_args` 键控（读第一个 `file_path` / `path` / `target_file`），
其他全并发。**R23 把此并发模型前向迁移到 Python asyncio**：`_dispatch_tool` 200 行单体拆成
`_prepare_tool_call`（串行阶段 1）+ `_execute_tool_call`（并行阶段 2，per-file `asyncio.Lock`）+
`_run_tool_batch`（批处理调度器，`asyncio.gather` 保序）。**关键复用**：R17 的 `CircuitBreaker`
已用 `async with self._lock` 保护 check / record 状态转换（`circuit_breaker.py:258/299`），阶段 2
并行 `breaker.record` 天然安全，**无需额外锁**。**关键保序**：`asyncio.gather` 在返回列表中保留
调用顺序（非完成顺序），故工具消息与 LLM 的 tool_calls 仍 1:1 对齐。这是阶段 C 第 3 轮
（智能体协作与感知 R21-R30）。

### 融合结论（两阶段拆分 + per-file 锁 + gather 保序，串行循环→并行批处理）

- ✅ **保持**：grok 两阶段拆分——prepare（串行：参数解析 / 权限 consent / pre 钩子 / 断路器
  entry / `tool_call`+`tool_running` 状态发出——这些必须有序，并发 consent 会和 UI 抢占）+
  execute（并行：per-file 锁定的注册表 dispatch + truncate + `post` 钩子 + audit——每调用独立）。
- ✅ **保持**：grok "仅同文件写入序列化"——per-file `asyncio.Lock`，键 = `file_path` / `path` /
  `target_file`（MiniMax 内置 write_file / edit_file 均用 `path`），`contextlib.nullcontext` 让
  无锁路径零开销。读 / 搜索 / 终端 / 无路径写入全部完全并发。
- ✅ **保持**：grok `gather` 保序——`asyncio.gather` 在返回列表中保留**调用顺序**（非完成顺序），
  故 OpenAI 风格 tool_call↔tool_result 配对完整，尽管执行重叠。
- ✅ **保持**：grok 断路器并发安全复用——R17 的 `CircuitBreaker` 已 `async with self._lock` 保护
  check / record 状态转换，阶段 2 同一工具的多个并行调用 `breaker.record` 不会撕裂状态机，无需
  新增同步原语。
- ✅ **保持**：grok 短路语义——任何拒绝（deny / user_deny / hook_block / circuit_open /
  malformed / cancel）在 prepare 设置 `short_circuit`（已发出 + 已审计），execute 直接返回
  不重复发出，避免双发。
- ❌ **放弃**：grok `ToolStream` / `ToolStreamItem`——Python async generator / yield 已等价提供
  流式工具输出，移植是重复造轮子。
- ❌ **放弃**：grok `ToolDyn` / `TypedToolOutput` / `Arc<dyn ToolDyn>`——Rust trait object 动态
  分发机制，Python 鸭子类型天然支持，无等价物也不需要。
- ❌ **放弃**：grok `max_concurrency` 信号量——当前无消费方（无"某工具最多 N 并发"需求），全
  并发 + per-file 锁已足够，YAGNI。
- ❌ **放弃**：grok `ToolCapabilities` / `ToolScope` / `HookKind` 线协议——MiniMax `HookManager`
  已是单类型，R22 `FilteredToolRegistry` 已做 capability 过滤，细粒度 scope 划分无消费方。
- ❌ **放弃**：grok `ToolFamily` / `ToolVariant`——工具家族 / 变体元数据，MiniMax 无工具分类 UI
  需求。
- ❌ **放弃**：grok `as_completed` 增量返回——`gather` 一次性保序返回更贴合 OpenAI 配对语义；
  增量返回需额外重排缓冲，无消费方。
- ❌ **放弃**：grok 权限拒绝时整批短路——MiniMax 保持每个工具独立（一个被 deny 不阻塞兄弟），
  更符合"工具独立"心智 + 被 isolation 测试钉死。

### 交付

- `agent/minimax_code/agent/core.py`（编辑：4 处导入/字段 + 1 处 run() 循环 + 1 处方法体替换）：
  ① 导入 `from contextlib import nullcontext`（无锁路径零开销包装）；② 模块级 `_PreparedToolCall`
  数据类（`call_log` / `name` / `args` / `action` / `breaker` / `short_circuit`——阶段 1 → 阶段 2
  的传递载体）+ `_WRITE_TOOLS = frozenset({"write_file", "edit_file"})`；③ `__init__` 新增
  `self._write_locks: dict[str, asyncio.Lock] = {}`（per-file 写锁字典，惰性填充）；④ `run()` 工具
  循环：串行 `for call: await self._dispatch_tool(call)` → `results = await self._run_tool_batch(
  tool_calls)` + `zip(tool_calls, results, strict=True)` 保序消费（strict 安全——`_run_tool_batch`
  用 `_cancel_prepared` 填充取消尾部保证等长）；⑤ **核心替换**：`_dispatch_tool`（原 678-874，
  200 行单体 11 步调度器）→ 向后兼容壳（`prepared = await self._prepare_tool_call(call); return
  await self._execute_tool_call(prepared)` 两行委托）+ **5 个新方法**：
    - `_prepare_tool_call`（串行阶段 1）：参数 JSON 解析 + malformed 短路 + 权限 `_check_rule`
      deny / `request_consent` user_deny + `pre_tool_use` 钩子 hook_block + 发出 `tool_call` +
      断路器 `get_or_create` / `check` circuit_open + 发出 `tool_running` → 返回 `_PreparedToolCall`
      （拒绝时带 `short_circuit`，就绪时带 breaker + action 审计追踪）；
    - `_execute_tool_call`（并行阶段 2）：`short_circuit` 非空直接返回不重复发出 + `async with
      (lock or nullcontext())` per-file 锁定 + `start_span` + `asyncio.wait_for(registry.dispatch,
      tool_timeout)` + TimeoutError / crash `breaker.record(FAILURE)` + 成功 `breaker.record(SUCCESS)`
      + `_truncate_result` + 发出 result + `post_tool_use` 钩子 fail-open + `_record_audit`；
    - `_run_tool_batch`（批处理调度器）：循环 prepare（`self.cancelled` 时尾部追加
      `_cancel_prepared`），再 `asyncio.gather(*(self._execute_tool_call(p) for p in prepared))`
      保序返回；
    - `_cancel_prepared`：构造 `action="cancelled"` + `ToolResult.fail("cancelled")` 的
      short_circuit（不发 `tool_call` / `tool_result` 事件，但调用方仍持久化 tool 消息保持
      OpenAI 配对完整）；
    - `_write_lock_for`（纯函数）：`name not in _WRITE_TOOLS` → None；读 `file_path` / `path` /
      `target_file` 路径键；惰性创建 / 返回 `self._write_locks[path]` 的 `asyncio.Lock`，无路径
      返回 None。
- `agent/tests/test_tool_batch_parallel.py`（新建，**9 测试**）：`_SlowTool`（sleep 可观测并行 /
  串行）+ `_WriteTool`（enter / exit bracket 可观测序列化顺序）+ `_PingTool`（isolation 测试的
  第二工具名——`_check_rule` 按 tool name 键控，isolation 需两个不同 name）+ `_DenyStore`（鸭子
  类型 PermissionStore，仅 `lookup` 被 `_check_rule` 读，无需 DB / DAO）+ `_FakeLLM`（构造 core
  用，stream_chat 不触发）。覆盖——① `_write_lock_for` 纯逻辑（读 / 搜索 / 终端 → None、写工具
  同路径共享锁跨 write_file + edit_file、不同路径独立锁、无路径 → None）；② **并行计时**（两个
  0.25s slow 工具总耗时 < 0.45s，串行 ≥ 0.5s，Windows 计时器余量内决断）；③ **保序**（首调用
  0.30s 慢于次调用 0.05s，结果仍按提交顺序 first / second）；④ **同路径写序列化**（bracket
  tags = `enter, exit, enter, exit` 严格嵌套，非交错）；⑤ **跨路径写并发**（两个 0.1s write
  总耗时 < 0.16s，全局锁会 ≥ 0.2s）；⑥ **权限拒绝隔离**（ping 允许 + slow deny，兄弟正常执行）；
  ⑦ **取消尾部填充**（cancel 后 3 个 call 全 cancelled，每个 tool_call id 仍有 result，配对完整）。

### 验证

- ✅ `ruff check minimax_code/agent/core.py tests/test_tool_batch_parallel.py`：**All checks
  passed**——含 **1 手动修**（run() 循环 `zip(tool_calls, results)` B905 → 加 `strict=True`；因
  `_run_tool_batch` 用 `_cancel_prepared` 填充取消尾部保证等长，strict 语义安全）。全套 154 个
  ruff 错误均为**预存的其他文件 lint 债务**（如 `storage/dao/__base.py` 的 `rows_to_dicts` 未用
  导入），与 R23 无关——轮次独立原则不动。
- ✅ `pytest tests/test_tool_batch_parallel.py -v`：**9 passed in 1.78s**——并行计时（< 0.45s）+
  保序（first/second 不随完成顺序翻转）+ 同路径序列化（严格嵌套）+ 跨路径并发（< 0.16s）+ 权限
  拒绝隔离 + 取消尾部填充 + `_write_lock_for` 三路径纯逻辑全绿灯。
- ✅ `pytest -q` 全套：**1146 passed in 87.71s**（1137 R22 基线 + 9 新增），**零失败、零回归**——
  证明 `_dispatch_tool` 两阶段拆分**向后兼容**（壳委托 prepare + execute，单调用路径行为不变，
  test_agent_core 全套绿灯）、per-file 锁不破坏既有 write_file / edit_file 测试、`_run_tool_batch`
  保序保证 tool 消息 1:1 对齐 LLM tool_calls、断路器并行 `record` 安全（R17 `async with self._lock`
  复用）、`zip(strict=True)` 等长保证成立。

### YAGNI 边界（本轮不做）

- ❌ 不做 grok `ToolStream` / `ToolStreamItem` 流式工具输出协议——Python async generator 已等价，
  移植是重复造轮子。
- ❌ 不做 grok `max_concurrency` 每工具并发上限信号量——当前无场景需"某工具最多 N 并发"，全并发
  + per-file 锁已足够，留待有真实限流需求。
- ❌ 不做 grok `ToolCapabilities` / `ToolScope` 细粒度能力线协议——MiniMax 工具是单类型，R22
  `FilteredToolRegistry` 已做 capability 过滤，再叠 scope 划分是过度设计。
- ❌ 不做 grok `as_completed` 增量结果返回——`gather` 保序返回更贴合 OpenAI tool_call↔tool_result
  配对；增量返回需重排缓冲，无消费方。
- ❌ 不做 sub-agent 真实 LLM 流式连线（`invoke` 仍 stub）——本轮聚焦工具调度并发，sub-agent LLM
  wiring 是独立工作（R24+）。
- ❌ 不做前端 UI"工具并行执行"可视化——后端已并行，前端 timeline 仍逐条渲染（并行工具的时间轴
  重叠展示是独立 UX 工作）。

### Commit

`feat(platform): R23 two-phase parallel tool dispatch (fuse grok xai-tool-runtime concurrency)`

## R24 — 回合中断/插话缓冲层（fuse grok xai-interjection-core）（阶段 C 第 4 轮）

### 本轮目标

把 grok-build 的 `xai-interjection-core` crate（用户在 agent 回合进行中插入消息、
而不打断对话的机制）前向迁移到 Python，接入 `AgentCore` 的 run loop。核心契约：
生产者把 out-of-band 插话 push 进缓冲区，run loop 在**安全点**（工具回合之间，
绝不在工具调用中途）一次性 drain，每条插话框成一条合成 `user` 消息，供模型下一轮
迭代看到——绝不合并、绝不丢、绝不撕裂在途工具调用。

### 融合结论

✅ **保持**：
- `EventQueue<E>` 共享 FIFO 队列语义——`clone()` 返回共享同一后备存储的新句柄
  （grok `Arc<Mutex<Vec<E>>>` 契约）。Python 用 `_SharedState` 后备 + `EventQueue`
  薄句柄实现；`push` / `push_capped(max)` / `drain_matching(pred)` / `drain_all` /
  `clear` / `snapshot` 全套。
- `format_interjection` + `user_query` + `LARGE_PROMPT_THRESHOLD=25_000`——
  `<user_query>` 信封 + "The user sent a message while you are working:" 中段提示，
  无延迟指令（让模型自行权衡）。超阈值截断保护 prompt 预算。
- `PendingInterjection` / `FormattedInterjection` + `drain_formatted`——一次性 drain，
  每条独占一条合成消息（never merged），`sanitize_text` 先于框成在**原始文本**上运行，
  `attachments` 原样透传（核心从不读取）。
- **安全 drain 点**对齐 grok 的 post-tool hook：`core.py` run loop 中
  `_run_tool_batch` 完成 + 取消检查之后、"Loop back to call the LLM" 之前（534 行）。

❌ **放弃（适配差异）**：
- `threading.Lock` 替代 `asyncio.Lock`——队列是纯内存 list 操作无 await，生产者可能
  来自任意线程（IPC handler / watchdog），同步锁语义等价 grok `Arc<Mutex<>>` 且免 await。
- **字节边界截断 → 代码点边界截断**：grok 在 UTF-8 `char_indices` 字节边界截断（Rust
  字符串是字节缓冲）；Python 字符串是代码点序列，`text[:threshold]` 天然 UTF-8 安全、
  永不会切坏多字节字符。阈值不变 25_000。语义等价。
- **poisoned-mutex 恢复无 Python 对应**：grok `unwrap_or_else(|e| e.into_inner())`
  从中毒锁抢救内层状态；Python `threading.Lock` 不会中毒，无此路径。

### 交付

| 文件 | 类型 | 内容 |
|------|------|------|
| `agent/minimax_code/agent/interjection.py` | 新建（252 行） | 三段式纯逻辑：format.rs / events.rs(EventQueue) / buffer.rs(drain_formatted) |
| `agent/minimax_code/agent/core.py` | 修改 | ① import interjection 公共 API；② `__init__` 加 `self._interjection_buffer`；③ `queue_interjection` / `drain_interjections` API；④ run loop 安全 drain 点（534 行）插话→合成 user message→persist |
| `agent/tests/test_interjection.py` | 新建（29 测试） | format 边界 4 + EventQueue 11 + drain_formatted 7 + AgentCore API 5 + run-loop 端到端 2 |

### 验证

- `ruff check`：3 个 R24 文件全部通过（I001 import 排序已 `--fix`）。
- `pytest tests/test_interjection.py`：**29 passed in 0.72s**。
- 全量 `pytest`：**1175 passed in 89.57s**，零失败、零回归（R23=1146 → R24=1175，
  +29 完全吻合新增测试数）。
- 端到端 run-loop 测试 `test_run_loop_drains_interjection_as_synthetic_user_message`
  钉死：回合前 queue 的插话，在工具回合后的 drain 点变成合成 `user` 消息，出现在
  LLM 第二次调用的 payload 里，携带 `<user_query>` 信封 + 中段提示；drain 后缓冲区清空。
- 回归守卫 `test_run_loop_without_interjection_is_unchanged`：无插话时 run loop 行为
  与 R24 前完全一致，无幽灵合成消息。

### YAGNI 边界

- ❌ 不做完整 IPC 推送路径（`agent.*` handler 把在途消息转 push 进缓冲区）——本轮聚焦
  缓冲层纯逻辑 + run-loop 安全 drain 接线；IPC 推送的回合 hook 由 **R25 生命周期
  贡献者**（TurnLifecycleContributor）正式化，避免本轮硬编码 `if` 分支。
- ❌ 不做自动 drain 的 watchdog 定时器——当前 drain 由 run loop 工具回合间自然触发，
  已覆盖主路径；独立 watchdog 仅在"长工具无回合"场景才需要，留待真实需求。
- ❌ 不接 R15 出站脱敏——R15 是模型→用户方向（出站），插话 sanitize 是用户→模型方向
  （入站），方向不同，默认 identity sanitize；主机按需注入（如 strip 图片占位路径），
  不强行复用 R15 pipeline。
- ❌ 不做 grok 的"延迟指令"（defer instruction）——grok 刻意把"如何权衡插话 vs 在途
  工作"的判断留给模型，不命令"放下一切"；移植保持这一克制。
- ❌ 不做 attachments 的核心侧渲染——`PendingInterjection.attachments` 是主机定义的
  （内联图、资产 ID），核心只透传到 `FormattedInterjection`，渲染归前端/调用方。

### Commit

`feat(platform): R24 mid-turn interjection buffer (fuse grok xai-interjection-core)`

## R25 — 生命周期贡献者 hook 框架（fuse grok xai-agent-lifecycle）（阶段 C 第 5 轮）

### 本轮目标

把 grok-build 的 `xai-agent-lifecycle` crate（评分 9/10，16 文件 ~600 LOC，
仅依赖 `async-trait` + `tracing`）前向迁移到 Python，落地一个**安装时能力注入
框架**：4 个 contributor family（TurnLifecycle / SessionLifecycle / TurnInput /
Command），每个 family 是一组带默认实现的 hook，宿主在 run loop 边界处按注册顺序
分发。核心契约（grok 原话）——"纯数据输入 / 安装时能力注入 / 绝不接管循环控制"。
R24 的 YAGNI 伏笔（"IPC 推送回合 hook 由 R25 生命周期贡献者正式化"）在本轮兑现为
`TurnLifecycleContributor` 框架。

### 融合结论

✅ **保持**：
- **4 family trait 切片**——Turn/Session/TurnInput/Command contributor 各自独立
  注册、独立冻结 tuple，互不干扰（grok 四 trait 对应四 family）。
- **trait 默认方法语义**——Rust trait 每方法带默认实现，Python 用 ABC + 非抽象
  方法等价（子类只 override 关心的 hook，其余继承空默认）。这正是 ruff B024/B027
  警告的模式，文件级 `# noqa` 标注"这是刻意的 trait 模式，非遗漏 @abstractmethod"。
- **注册顺序分发**——grok `Vec` 顺序、非链式、无短路；Python tuple 同序遍历。
- **命令名优先注册者获胜**——grok 命令字典 first-write-wins（debug panic /
  release log）；Python `add_command` 重复名 warn + 丢弃第二个 spec，contributor
  本身保留（其独有名仍可解析）。
- **唯一终态保证**——每个 turn 恰好触发 done/abort/error 之一（grok 设计）。

❌ **放弃（适配差异）**：
- **双变体 trait（thread-local + `'static Clone`）**——grok 因 Rust borrow
  checker 拆两份；Python GIL + 引用语义让第二份冗余，**一个 ABC per family** 即
 忠实移植（4 文件而非 16）。
- **panic 传播 → fail-open**——grok debug panic / release log；MiniMax 全程
  fail-open（对齐 core.py 现有 `_emit_*` 模式：`except Exception: # noqa: BLE001`
  + `logger.exception`），一个坏扩展杀不死一个回合。
- **turn_input / command 宿主分发**——grok 自己也**从未在 host 调用**这两个 trait
  （crate 9/10 评分的扣分点）；MiniMax 同样只集成 TurnLifecycle 的 4 个 hook
  （grok 实际连线的 2 个边界），framework 就位但 host 接线推迟 R26+，无成熟先例
  可移植。
- **`TurnAbortReason(str, Enum)` → `StrEnum`**——UP042 现代化（Python 3.11+，
  项目最低版本）；`.value` 行为不变，测试断言 `"interrupted"` 不受影响。

### 交付

| 文件 | 类型 | 内容 |
|------|------|------|
| `agent/minimax_code/lifecycle/types.py` | 新建 | 13 个 frozen dataclass 值对象 + `TurnAbortReason(StrEnum)` + `CommandAction = CommandRewrite \| CommandActed` 联合类型 |
| `agent/minimax_code/lifecycle/contributors.py` | 新建 | 4 个 ABC：`TurnLifecycleContributor`(4 hook) / `SessionLifecycleContributor`(1) / `TurnInputContributor`(1) / `CommandContributor`(2)，全默认实现；文件级 `# noqa: B024,B027` 标注 trait 模式 |
| `agent/minimax_code/lifecycle/registry.py` | 新建 | `ExtensionRegistry`(frozen, `__slots__`) + `ExtensionRegistryBuilder`(链式 `add_*` + `build`)；命令优先注册者获胜 + 重复 spec 丢弃 + `all_advertised_commands` 去重（与 `command_owner` 对齐） |
| `agent/minimax_code/lifecycle/__init__.py` | 新建 | 公共导出 4 contributor + 2 registry + 13 类型 |
| `agent/minimax_code/agent/core.py` | 修改（+81 行） | ① import lifecycle 公共 API；② `__init__` 加 `self.lifecycle: ExtensionRegistry \| None = None`；③ 5 个 `_fire_turn_*` 方法 + `_fire_lifecycle` 分发器（fail-open）；④ run() 5 注入点（start / done / max_iter-abort / cancelled-abort / error）；⑤ 唯一终态漏斗（cancelled 在 return 前单点 dispatch） |
| `agent/tests/test_lifecycle.py` | 新建（21 测试） | registry/builder 4 + command 策略 3 + ABC defaults 4 + 数据类型 3 + AgentCore 集成 7（done / max_iter-abort / cancel-abort / LLMError-error / fail-open / 顺序 / 无 registry 回归） |

### 验证

- ✅ `ruff check`：**All checks passed**——7 个 import 排序 `--fix` 自动修 + 1 个
  UP042（`StrEnum`）手动修 + B024/B027 文件级 `# noqa` 标注 trait 默认实现意图。
- ✅ `pytest tests/test_lifecycle.py`：**21 passed in 0.83s**。
- ✅ 全量 `pytest`：**1196 passed in 87.57s**（R24=1175 → R25=1196，+21 完全吻合
  新增），**零失败、零回归**——证明 `lifecycle=None`（默认）时所有 `_fire_lifecycle`
  早返回、run loop 行为与 R25 前完全一致；core.py +81 行纯新增、0 删除。
- ✅ **唯一终态钉死**：done→`[start, done]`；max_iterations→`[start, abort]`；
  cancelled→`[start, abort]`；LLMError→`[start, error]`；每测试额外断言其他终态
  未泄漏（`not any(e[0] in (...))`）。
- ✅ **fail-open 钉死**：contributor `on_turn_done` 抛 `RuntimeError`，turn 仍正常
  完成（`result.final_text == "hi"`），先注册的 recorder 仍收到 done。
- ✅ **lifecycle=None 回归守卫**：`test_run_without_registry_is_noop` 钉死无
  registry 时 run 与 R25 前一致。

### YAGNI 边界（本轮不做）

- ❌ 不做 turn_input 宿主分发——grok 自己从未在 host 调用 `contribute_turn_input`
  （crate 9/10 扣分点）；framework 已就位（`TurnInputContributor` +
  `registry.turn_input` tuple），host 接线推迟到有真实 fragment 注入需求（R26+）。
- ❌ 不做 command 宿主路由——同上，grok 的 command trait 从未被 host 调用；
  framework 就位（`command_owner` + `all_advertised_commands`），slash 命令解析/
  路由推迟。
- ❌ 不做 session_idle 宿主触发——grok 的 `on_session_idle` 无成熟触发点；推迟到
  有真实"会话空闲"语义需求。
- ❌ 不做 contributor 优先级 / 依赖排序——grok 是纯注册顺序；加权或 DAG 依赖是
  过度设计，无场景。
- ❌ 不做 contributor 动态注册 / 热插拔——registry 一次 `build()` 冻结（对齐 grok
  "install-time capability injection"）；运行时增删 contributor 无需求。
- ❌ 不把 R24 interjection 缓冲重构成 contributor——R24 YAGNI 提到"IPC 推送 hook
  由 R25 正式化"，本轮交付了 framework + TurnLifecycle 接线；把 R24 的
  `queue_interjection` 调用点迁移成 `TurnLifecycleContributor` 是独立 wiring 工作
  （R26+），避免本轮 scope 蔓延。

### Commit

`feat(platform): R25 lifecycle contributor hook framework (fuse grok xai-agent-lifecycle)`

## R26 — plan mode 状态机（fuse grok xai-grok-*-plan_mode）（阶段 C 第 6 轮）

### 本轮目标

把 grok-build 的 plan-mode 纯状态机核心——`xai-grok-shell::session::plan_mode`
（1226 行，含 ~40 测试）+ `xai-grok-workspace-types::types::plan_mode`（75 行线
格式枚举）——前向迁移到 Python。grok 模块文档字符串明确自述："设计为可独立
测试——无 SessionActor / 对话历史 / 异步 I/O 引用，纯状态机逻辑"。本轮严格守住
这一纯粹性：交付一个**零 IO、零 async、可独立测试**的 `PlanModeTracker`
四状态机 + 可持久化快照，host 集成（注入提示 / 屏蔽写工具 / 持久化）推迟后续轮次。

### 融合结论

✅ **保持**：
- **4 状态机** `PlanModeState`（Inactive / Pending / Active / ExitPending）——语义
  1:1 移植：Inactive 常态；Pending 客户端开但模型未知；Active 写工具屏蔽（除
  plan 文件）；ExitPending turn 飞行中关闭、等 turn 结束干净退出。
- **全 transition 方法契约**——`enter_pending` / `activate` / `activate_mid_turn`
  / `activate_from_tool` / `deactivate_approved` / `user_exit(turn_in_flight)` /
  `complete_deferred_exit` / `queue_exit_reminder` / `record_reminder_injected` /
  `clear_pending_exit_reminder` / `reset_after_compaction`，含返回值（是否实际
  改变状态）与 no-op 守卫，全部对齐 grok。
- **中途 toggle 缓冲 + 回滚**——`activate_mid_turn` 预渲染提醒并缓冲（`PendingActivation`），
  `take_pending_activation` 单次取走；`user_exit` 检测到未投递缓冲时回滚 Inactive 并
  **恢复 `prior_was_previously_active`**（而非伪造 reentry）——grok 的关键不变量，
  专测 `test_withdrawal_preserves_real_reentry_flag` 钉死。
- **提示交替**（even=full / odd=sparse）+ **reentry 检测**（`was_previously_active
  && Pending`）+ **ExitPending 重入直接回 Active**（模型已有 plan-mode 上下文）。
- **瞬态折叠快照**——`Pending`/`ExitPending` 依赖 in-flight 客户端/turn 交互，重启
  无法存活：`from_snapshot` 把 Pending→Inactive、ExitPending→Inactive+exit_reminder；
  `awaiting_plan_approval` 通过默认值往返存活（对齐 grok `#[serde(default)]`）。
- **序列化 parity**——grok `PlanModeState` 保留 serde 默认 PascalCase 标签
  （`"Active"`），snapshot 字段名 snake_case；Python `StrEnum` 值精确匹配，跨实现
  持久化 round-trip。`PromptMode` snake_case（`"agent"`/`"ask"`/`"plan"`），`from_meta_str`
  大小写敏感（仅小写命中，未知→Agent），对齐 grok。
- **plan 文件编辑放行 + markdown 后缀识别**——`should_auto_approve_edit`
  （Active 且精确匹配 plan 路径）、`is_plan_file_write`（精确相等）、
  `is_markdown_file_path`（6 后缀大小写不敏感，Unicode 安全）。
- **5 提示模板**（full/sparse/reentry/exit_reminder/edit_rejected）逐字移植，**零硬编码
  工具名**（全 `${{ tools.by_kind.X }}` 占位符），专测 `test_templates_have_no_hardcoded_tool_names`
  守护。

❌ **放弃（适配差异 / YAGNI）**：
- **MiniJinja 渲染**——grok 用 `${{ }}` + `${%- if %}` 通过 TemplateRenderer；
  MiniMax 无 Jinja 依赖，模板原文保留（占位符完整），新增轻量 `render_reminder` 用
  正则解析 `plan_path` / `plan_has_content` 条件 / `tools.by_kind.X`，未知 kind 留原样
  （可见而非静默清空）。不引入新依赖。
- **tokio::fs 磁盘 IO**——grok `plan_file_has_content` 调 `tokio::fs::metadata`；
  本轮纯状态机零 IO，`plan_file_path` 只做路径计算（`session_dir/plan.md`），磁盘
  内容检测留 host 集成轮次。
- **Mutex**——grok 因 SessionActor 并发调用用 `Mutex<PlanModeTracker>`；MiniMax
  asyncio 单线程，锁冗余，直接持有裸实例。
- **SessionActor host 集成**——grok 在 `handle_session_mode`/`handle_prompt`/
  `handle_completion`/`run_compact` 调用 tracker；本轮只交付纯核心 + 测试，host
  接线（提示注入对话、写工具屏蔽、snapshot 持久化）推迟，与 R25 lifecycle 框架
  "framework 先行、host 接线后续"模式一致。
- **EventBus 广播**——grok 注释明确"plan_mode 转换不上 EventBus"，无需移植。

### 交付

| 文件 | 类型 | 内容 |
|------|------|------|
| `agent/minimax_code/plan_mode/tracker.py` | 新建（462 行） | `PlanModeState`(StrEnum, 4 值 PascalCase) + `PromptMode`(snake_case, from_meta_str/default/is_read_only) + `PendingActivation` + `PlanModeSnapshot`(to_dict/from_dict, legacy 宽容) + `PlanModeTracker`(11 transition + 8 query + new/from_snapshot 构造) + `is_plan_file_write`/`is_markdown_file_path` |
| `agent/minimax_code/plan_mode/templates.py` | 新建（132 行） | 5 个模板常量（原文，含 `${{ }}`/`${%- if %}` 占位符）+ `render_reminder`（正则解析条件 + plan_path + tools.by_kind，未知 kind 留原样）|
| `agent/minimax_code/plan_mode/__init__.py` | 新建（52 行） | 公共导出 7 类型 + 5 模板 + render_reminder |
| `agent/tests/test_plan_mode.py` | 新建（61 测试） | 核心生命周期 8 + 中途缓冲 6 + no-op 边界 11 + ExitPending 重入 3 + snapshot 7 + awaiting 3 + 辅助函数 7 + PromptMode 5 + 模板渲染 10 + PendingActivation 1 |

### 验证

- ✅ `ruff check`：**All checks passed**——1 个 UP035（`typing.Mapping` →
  `collections.abc.Mapping`）手动修，无残留。
- ✅ `pytest tests/test_plan_mode.py`：**61 passed in 0.22s**。
- ✅ 全量 `pytest`：**1257 passed in 88.61s**（R25=1196 → R26=1257，+61 完全吻合
  新增测试数），**零失败、零回归**——plan_mode 是纯新建包，0 修改既有文件，
  既有 1196 测试纹丝不动。
- ✅ **中途缓冲回滚钉死**：`test_user_exit_withdraws_undelivered_activation`
  （回滚后 reentry 为 False）+ `test_withdrawal_preserves_real_reentry_flag`
  （真实先验激活不被缓冲覆盖）双测守护 grok 关键不变量。
- ✅ **快照折叠钉死**：`test_snapshot_pending_collapses_to_inactive` +
  `test_snapshot_exit_pending_collapses_to_inactive_with_reminder` +
  `test_snapshot_without_awaiting_field_defaults_false`（legacy JSON 容忍）。
- ✅ **序列化 parity 钉死**：`test_snapshot_to_dict_from_dict_roundtrip` 断言 5 个
  snake_case key + PascalCase `"Active"` state 值，与 grok 持久化格式跨实现对齐。
- ✅ **模板零硬编码守护**：`test_templates_have_no_hardcoded_tool_names` 遍历 5 模板
  × 6 客户端工具名，断言无硬编码（全占位符）。

### YAGNI 边界（本轮不做）

- ❌ 不接 AgentCore host 集成——tracker 是纯核心，run loop 边界处注入提示 /
  屏蔽写工具 / 持久化 snapshot 的接线推迟（与 R25 lifecycle "framework 先行"对齐），
  避免本轮 scope 蔓延；纯核心 + 61 测试已可独立验证契约正确性。
- ❌ 不做磁盘 `plan_file_has_content`——需要 `tokio::fs::metadata` 等价物（async
  stat），属 host 集成范畴；本轮 `plan_file_path` 只做路径计算，内容检测留真实需求。
- ❌ 不做完整 MiniJinja 语法——`${%- if %}` 条件已支持，trim_blocks / 环路 / 过滤器
  等高级语法无场景，`render_reminder` 用正则覆盖现有 5 模板的全部占位符。
- ❌ 不做 `exit_plan_mode` 工具 UI——`awaiting_plan_approval` flag + snapshot 字段
  已就位，但审批 UI chrome（grok 的 `PlanModeDecision::Approve/Reject/Defer` 往返）
  留 host 集成轮次。
- ❌ 不做 EventBus 广播——grok 明确 plan_mode 转换不上 EventBus，无需移植。
- ❌ 不接 R24 interjection / R25 lifecycle——plan_mode 是独立状态机，与回合生命周期
  的交叉（如 plan mode 下如何处理插话）留后续轮次显式设计。

### Commit

`feat(platform): R26 plan mode state machine (fuse grok xai-grok-*-plan_mode)`

---

# R27 — 纯 token 估算原语（fuse grok xai-token-estimation）

**日期**：2026-07-19
**来源**：`grok-build/crates/codegen/xai-token-estimation`（单文件 `src/lib.rs`，255 行，`[dependencies]` 为空）
**提交**：见本节末

## 本轮目标

移植 grok 上下文窗口算术的**单一真相源**——纯 token 估算原语——到 Python。这是 R28
对话压缩的硬前置依赖（"压缩到目标 token 数"必须先会数 token）。9 个公开函数 + 2 常量，
零 async / 零 IO / 零外部依赖，与 Rust 原版同样可独立验证。一轮可交付，为 R28 compaction
（30+ 文件多模块 crate）打地基。

## 融合结论

- ✅ **2 常量**：`BYTES_PER_TOKEN = 4`、`IMAGE_TOKEN_ESTIMATE = 765`。
- ✅ **9 纯函数 1:1 移植**：`estimate_tokens` / `estimate_chars` / `estimate_image_tokens` /
  `usage_percentage` / `usage_percentage_u8` / `usage_percentage_truncated_u8` /
  `free_tokens` / `exceeds_threshold` / `exceeds_threshold_with_headroom`。
- ✅ **签名映射**：`u64` → Python `int`（任意精度，`saturating_mul` 自然恒等）；
  `&str` → `str`；返回 `u8` → `int`（值域 0–100）。

## 关键移植契约（1:1 锁定）

1. **UTF-8 字节计数（非码点）**——本轮最重要发现。Rust `&str::len` 是字节数，
   Python `len` 是码点数；`estimate_tokens` 必须用 `len(s.encode("utf-8"))`。
   守护测试 `test_estimate_tokens_uses_bytes_not_codepoints`（CJK 字符 "日本" = 6 字节
   → 1 token）第一次运行即捕获此 bug——docstring 已写 "UTF-8 bytes" 但实现误用码点 len，
   测试暴露代码-文档矛盾，修复后两者一致。grok 原测试全 ASCII 故未暴露此差异。
2. **round-half-up（非 Python banker's rounding）**——`usage_percentage_u8` 镜像
   Rust `f64::round`（away-from-zero）。Python 内置 `round` 是 round-half-to-even，
   会让 `42.5 → 42` 而非 grok 的 `43`。用 `math.floor(x + 0.5)` 复现非负 half-up。
   守护测试 `test_usage_percentage_u8_rounds_half_up`（85/200→43、7/8→88）锁定方向。
3. **`>=` 整数阈值边界**——`exceeds_threshold(850, 1000, 85)` 必须 fire
   （`850*100 == 1000*85`），比旧版 `>` 提前 1 token。测试
   `test_exceeds_threshold_fires_on_strict_boundary` 锁定 850/849 与 950/949 两组边界。
4. **saturating-sub**——`exceeds_threshold_with_headroom` 右侧 `cw*pct - headroom*100`
   用 `max(0, ...)` 钳到 0（镜像 Rust `saturating_sub`）；钳到 0 时任何非负 used 都满足。
   守护测试 `..._headroom_larger_than_threshold_saturates`（headroom 1M on 100K×85%
   → used=0 也 fire）锁定。
5. **截断 vs 四舍五入刻意区分**——`usage_percentage_truncated_u8` 整数 `//` 截断
   （85/200→42），与 `usage_percentage_u8`（→43）不同；保证
   `exceeds_threshold(used,cw,p)` 与 `usage_percentage_truncated_u8(used,cw) >= p`
   同步跨越阈值。

## 交付

| 文件 | 行数 | 说明 |
|------|------|------|
| `agent/minimax_code/token_estimation.py` | 174 | 2 常量 + 9 纯函数 + `_round_half_up` 辅助；模块 docstring 记录两处 parity 陷阱（字节计数 / round 方向） |
| `agent/tests/test_token_estimation.py` | 237 | 27 测试：镜像 grok 全部 `#[test]` + Python 特定守护（字节计数 / round-half-up / 零 total / 饱和减 / u64::MAX 路径） |

## 验证

- ✅ **ruff**：`All checks passed`（E/F/W/I/B/UP，line-length 100）。
- ✅ **新测试**：27 passed in 0.07s。
- ✅ **完整套件零回归**：1284 passed in 87.37s = 1257 基线 + 27 新增，**精确匹配**。
- ✅ **网格一致性**：`exceeds_threshold_with_headroom(*,*,*,0) == exceeds_threshold(*)`
  在 10 cw × 6 pct × 7 used = 420 组合参数化验证，含非圆整窗口（101/1024/128001/1000001）。
- ✅ **u64::MAX 路径**：`usage_percentage_truncated_u8(18446744073709551615, 1) == 100`
  镜像 Rust 饱和乘 + `min(100)` 钳制。

## YAGNI 边界（本轮不做）

- ❌ 不接 AgentCore / LLM 真实上下文计数——本轮是纯算术原语，把 messages[] token 求和、
  对系统提示 + 工具定义预算、对图像附件计费留给 host 集成（R28+）。
- ❌ 不引入真实分词器（tiktoken 等）——grok 本身就是 bytes/4 启发式，保持 parity；
  精确分词与 R28 压缩质量优化同步考虑。
- ❌ 不做 `saturating_mul` 显式包装——Python int 不溢出，`tokens*4` / `count*765` /
  `used*100` 在 0–100 输出域内与 Rust 饱和乘恒等；仅 `exceeds_threshold_with_headroom`
  的 `saturating_sub` 因可观测地改变布尔结果而显式镜像（`max(0, ...)`）。
- ❌ 不做 IPC 暴露（`/context` handler）——百分比 renderer 层留 host 集成轮次。
- ❌ 不接 R28 compaction——compaction crate 是 30+ 文件多模块（code/intra/inter/history），
  本轮只交付它的 token 计数地基；压缩策略 MVP 留 R28。

## Commit

`feat(platform): R27 token estimation (fuse grok xai-token-estimation)`

# R28 — intra-compaction 配置 + 触发决策（fuse grok xai-grok-compaction）

**日期**：2026-07-19
**来源**：`grok-build/crates/common/xai-grok-compaction/src/intra_compaction/{config.rs, trigger.rs, mod.rs}`（crate 共 30+ 文件多模块，本轮交付第一个可独立验证的纯逻辑切片）
**提交**：见本节末

## 本轮目标

移植 grok intra-compaction 的**配置层 + 触发决策层**——纯决策函数 `should_compact` 决定"现在该压
缩吗"。这是 R27 token 估算的**直接下游消费者**（`percent` 字段复用 R27 的
`usage_percentage_truncated_u8`），形成 R27→R28 复用闭环。零 async / 零 LLM 调用 / 零 IO，
compaction crate 30+ 文件中**第一个可独立交付的纯逻辑切片**，为后续 select/sample/apply 执行层打
配置地基。

## 融合结论

- ✅ **2 枚举**：`IntraCompactionMode`（4 变体 StrEnum，snake_case）+ `IntraSummarizer`（2 变体 StrEnum）。
- ✅ **1 配置 dataclass**：`IntraCompactionConfig`（15 字段 + Default + `effective_compaction_model_name` +
  `from_dict` / `to_dict` serde parity）。
- ✅ **1 结果 dataclass**：`IntraCompactionTrigger`（4 字段：last_prompt_tokens / context_window /
  percent / step）。
- ✅ **1 纯决策函数**：`should_compact(policy, last_prompt_tokens, context_window, current_step) → Trigger | None`。
- ✅ **1 常量**：`DEFAULT_COMPACTION_MODEL_NAME = "grok-4.20"`。
- ❌ **不移植**：`IntraCompactionError` / `IntraCompactionResult`（compact 执行层，本轮只做触发决策）、
  `compact.rs` / `sampler.rs` / `select.rs` / `observer` / `traits`（host 集成层）。

## 关键移植契约（1:1 锁定）

1. **严格 `>` 边界（vs R27 的 `>=`）**——本轮最重要契约。grok trigger 是严格大于：
   `last_prompt_tokens <= threshold` 返回 `None`，`threshold = context_window *
   trigger_threshold_percent // 100`。因此**不能**直接复用 R27 的 `exceeds_threshold`（那是 `>=`），
   `should_compact` 内联 threshold 计算 + `<=` 比较。守护测试
   `test_boundary_exact_threshold_does_not_trigger` 锁定：cw=100_000/pct=85 → threshold=85_000，
   85_000 不触发（`<=`），85_001 触发——刻意与 R27 的 `exceeds_threshold(850,1000,85)=True` 区分。
2. **R27 复用闭环**——`percent` 字段直接调用
   `minimax_code.token_estimation.usage_percentage_truncated_u8(last_prompt_tokens, context_window)`，
   保证渲染百分比与阈值跨越同步。守护测试 `test_percent_matches_r27_usage_percentage` 在 4 组
   （含非整除 250_000/256_000 → 97）断言 `t.percent == usage_percentage_truncated_u8(used, cw)`。
   这是 R27 价值的真实兑现：跨模块纯函数复用，无重复算术。
3. **FullReplace 忽略 min_steps**——默认模式 `FullReplace` 只看 token 阈值，`min_steps_before_compact`
   字段保留但触发时不检查（大首步 prompt 也能压缩）。Partial 模式（StepsOnly / HistoryOnly /
   HistoryThenSteps）才 gate `current_step < min_steps_before_compact`。守护测试
   `test_full_replace_keeps_field_but_ignores_min_steps`（step=0/2 仍触发）+
   `test_partial_modes_enforce_min_steps`（3 partial 模式 × step=2/3 参数化）。
4. **snake_case serde parity**——两枚举的 `StrEnum` 值 = grok `#[serde(rename_all = "snake_case")]`
   字符串（`full_replace` / `steps_only` / `history_only` / `history_then_steps` / `shared` / `legacy`），
   Rust 实现持久化的 JSON 在此往返。守护测试 `test_mode_serde_round_trip` / `test_summarizer_serde_round_trip`
   断言 `mode.value == "full_replace"` 且 `IntraCompactionMode("full_replace") is mode` + config 往返。
5. **`#[serde(default)]` parity**——`from_dict` 每字段 `.get(..., default)`，部分 JSON 缺字段时填默认；
   未知 enum 字符串回退该字段默认（`_enum_or_default` 辅助）。守护测试
   `test_from_dict_partial_json_fills_defaults`（`{"enabled": true, "trigger": 80}` → 其余默认）+
   `test_from_dict_unknown_enum_falls_back_to_default`（`mode: "nonsense"` → FullReplace）+
   `test_from_dict_explicit_null_compaction_model_name`（JSON null → None → effective() 回退默认）。
6. **`effective_compaction_model_name`**——镜像 grok `as_deref().map(trim).filter(non-empty).unwrap_or(DEFAULT)`：
   `None` / `""` / `"   "` → `DEFAULT_COMPACTION_MODEL_NAME`，非空白 → trimmed 值。参数化守护测试
   4 组输入。

## 交付

| 文件 | 行数 | 说明 |
|------|------|------|
| `agent/minimax_code/compaction/__init__.py` | 30 | 包入口，re-export 6 个公共符号 |
| `agent/minimax_code/compaction/config.py` | 226 | 2 枚举 + Config dataclass（15 字段 + Default + effective + from_dict/to_dict）+ DEFAULT 常量 + `_enum_or_default` 辅助 |
| `agent/minimax_code/compaction/trigger.py` | 104 | Trigger dataclass（4 字段）+ `should_compact` 纯决策（复用 R27）|
| `agent/tests/test_intra_compaction.py` | 296 | 30 测试：镜像 grok config 8 + trigger 8 + Python 守护（R27 复用 / strict-boundary / serde roundtrip / unknown enum / null cmn）|

## 验证

- ✅ **ruff**：`All checks passed`（E/F/W/I/B/UP，line-length 100；初始 import 排序 1 处由 `--fix` 自动修复）。
- ✅ **新测试**：30 passed in 0.10s。
- ✅ **完整套件零回归**：1314 passed in 89.46s = 1284 基线 + 30 新增，**精确匹配**。
- ✅ **边界锁定**：`should_compact(p, 85000, 100000, 10) is None` 且 `should_compact(p, 85001, 100000, 10) is not None`
  ——严格 `>` 契约，单 token 跨越。
- ✅ **percent 钳制**：`should_compact(p, 200000, 100000, 10).percent == 100`（超窗口仍报 100，非 >100）。
- ✅ **R27 复用**：trigger 的 percent 在 4 组（含非整除 250_000/256_000→97）与
  `usage_percentage_truncated_u8` 直接调用逐位相等。
- 🐛 **测试数据 bug（已修）**：初版 `test_percent_matches_r27_usage_percentage` 用 `(128_001, 256_000)`
  作用例——仅 50% 用量，低于 85% 阈值不触发，`should_compact` 正确返回 `None`，测试断言 `is not None`
  失败。**实现无误**（boundary 测试全过），测试数据选错；改为 `(250_000, 256_000)`（97.6% > 85%）。

## YAGNI 边界（本轮不做）

- ❌ 不移植 `IntraCompactionError`（11 变体）/ `IntraCompactionResult`——属于 compact 执行层
  （采样失败、降级、拒绝），本轮只做触发决策；错误矩阵留执行层轮次。
- ❌ 不移植 `compact.rs` / `sampler.rs` / `select.rs` / `observer` / `traits`——host 集成层
  （真实 LLM 采样、item 选择、状态提交、CompactionItem/ItemTokenCounter trait），依赖 host item 类型，
  本轮纯逻辑无 host 依赖。
- ❌ 不接 AgentCore / 不在对话循环中调用 `should_compact`——本轮交付策略 + 决策核心，host 接线
  （每步检查触发、压缩后重建上下文）留后续轮次。
- ❌ 不做 IPC 暴露（`compaction.config` / `compaction.status` handler）——配置持久化 + 前端
  百分比/触发事件渲染留 host 集成轮次。
- ❌ 不实现 4 模式的差异化压缩行为——本轮只锁配置 + 触发；FullReplace/StepsOnly/HistoryOnly/
  HistoryThenSteps 的实际 compact 策略留执行层轮次（每模式独立切片）。

## Commit

`feat(platform): R28 intra-compaction trigger (fuse grok xai-grok-compaction)`

---

## R29 — 压缩后活动状态提醒格式化（fuse grok `xai-grok-compaction/reminder`）

### 本轮目标

融合 `grok-build/crates/common/xai-grok-compaction/src/reminder.rs`（crate root，520 行，纯格式化，
12 个 `#[test]`）。这是 compaction 链路的**输出端**：R28 决定**何时**压缩，R29 保证压缩后模型**仍知
道自己在做什么**——把 running background tasks / TODO list / running subagents 三段状态包成
`<system-reminder>` 块追加到摘要，防止压缩丢失活动上下文（在跑的任务、待办、子 agent）。

**产品融合点（不只是移植）**：三段正好映射 MiniMax Code 现有原语——
`BackgroundTask` ↔ 后台终端任务、`TodoItem` ↔ `TaskCreate`/`TaskList`、`RunningSubagent` ↔
`SubAgentRuntime` 子 agent。本轮交付纯格式化层 + 测试，host 接线（从真实 store/runtime 取状态）留后续。

纯字符串格式化：零 async、零 LLM、零 IO、零 host 依赖。Rust 的 `&'a` 借阅视图在 Python 用 dataclass
持 `str`/`list` 引用表达——语义同"不持有世界的私有副本"，仅以引用代替生命周期。

### 融合结论（✅ 保持 / ❌ 放弃）

- ✅ **`TodoStatus` 4 变体 Enum**（Pending/InProgress/Completed/Cancelled）+ `is_actionable()`
  （Pending|InProgress 为 True）+ `tag()`（`[pending]`/`[in_progress]`/`[completed]`/`[cancelled]`）。
- ✅ **5 个 dataclass 借阅视图**：`SubagentToolNames`(poll/cancel)、`TodoItem`(id/content/status)、
  `BackgroundTask`(task_id/command/status/tool_name?)、`RunningSubagent`(subagent_id/type?/desc?/elapsed)、
  `ActiveAgentReminderState`(三段 list + `is_empty`/`has_actionable_todos`)。
- ✅ **8 个纯函数**：3 个 section 格式器 + `format_active_agent_sections` + `wrap_system_reminder` +
  `format_active_agent_reminder` + `append_reminder_block`（+ 2 私有 `_format_*_line`/`_todo_trailer`）。
- ❌ **不移植 harness-only 段**（files / AGENTS.md / skills / MCP / memory）——grok `reminder.rs` 里
  这些段依赖 host 文件系统与 manifest，本轮只做活动 agent 状态三段；其余段属 host 集成层。
- ❌ **不接 compact.rs 注入**——`append_reminder_block` 已交付纯函数，但不把它接进（尚未存在的）
  compact 执行层把 reminder 注入真实摘要；host 接线留后续。

### 交付

- `agent/minimax_code/compaction/reminder.py`（288 行，新建）：`TodoStatus` Enum + 5 dataclass +
  8 公开函数 + 2 私有辅助。`from __future__ import annotations` + `collections.abc.Iterable`。
- `agent/tests/test_compaction_reminder.py`（306 行，新建）：**25 个测试** = 12 个 grok 镜像
  （empty_state_is_none / missing_tool_names_omits_subagent_section_only / chat-style verbatim /
  build-style type / renamed tools verbatim / background tasks / todo list / only-completed-is-none /
  section order / wrap joins+skips / appends blank-line / append noop）+ 8 个 Python 守护
  （TodoStatus.is_actionable ×4 参数化 / TodoStatus.tag ×4 参数化 / state 默认空 / completed-only 空 /
  bg section 空 None / todo trailer 四分支组合 / subagent section 空 None）。
- `agent/minimax_code/compaction/__init__.py`（+33 行）：re-export reminder 全部公开符号 +
  `__all__` 扩展，crate-level discoverability。

**11 个关键移植契约**（测试逐条锁定）：

1. **节顺序固定** bg→todo→subagent（`test_section_order_background_todo_subagent` 用 `out.index` 比较）。
2. **空状态→None**（`test_empty_state_is_none`：`format_active_agent_reminder(空, tools) is None`）。
3. **subagent 工具名缺失只省略 subagent 段**——bg/todo 仍渲染
  （`test_missing_tool_names_omits_subagent_section_only`：`subagent_tools=None` 时 subagents 段消失）。
4. **subagent_id verbatim**——chat-style UUID（`019ea7f0-...`）原样渲染，不合成、不加 type；
  build-style 在 id 与 task 间插 `type: \`explore\``。
5. **重命名工具名 verbatim**——manifest 改名（`get_command_or_subagent_output`）原样插值，不硬编码
  `get_task_output`。
6. **TODO 折叠** completed/cancelled 成计数 trailer（`_todo_trailer` 四分支：无/仅 completed/仅
  cancelled/两者），只列 actionable（pending/in_progress）。
7. **wrap_system_reminder** 跳过空白段 + 双换行连接，全空→None
  （`test_wrap_system_reminder_joins_and_skips_blank`：`["## A\nx","","  ","## B\ny"]` →
  `<system-reminder>\n## A\nx\n\n## B\ny\n</system-reminder>`）。
8. **append_reminder_block**：None/空白→summary 原样；非空→`summary\n\nreminder`。
9. **em dash `—`（U+2014）保真**——`section_todo_list` 文本"compacted — it is still active"保真，非 ASCII `-`。
10. **`TodoStatus.is_actionable`** 决定列出 vs 折叠——pending/in_progress 列行，completed/cancelled 计数。
11. **`ActiveAgentReminderState.is_empty`**：completed-only todos 算空（无 actionable），`has_actionable_todos` 为准。

### 验证

- ✅ **ruff**：`All checks passed`（E/F/W/I/B/UP，line-length 100；初始 2 处——`Iterable` 应从
  `collections.abc` 导入 UP035 + 测试 import 块排序 I001——由 `--fix` 自动修复）。
- ✅ **新测试**：25 passed in 0.10s。
- ✅ **完整套件零回归**：1339 passed in 88.47s = 1314 基线 + 25 新增，**精确匹配**。
- ✅ **节顺序锁定**：`out.index("## Running Background Tasks") < out.index("## TODO List") <
  out.index("## Running Subagents")`。
- ✅ **wrap 双换行连接**：精确字符串断言 `<system-reminder>\n## A\nx\n\n## B\ny\n</system-reminder>`。
- ✅ **TODO trailer 四分支**：`(0,0)→""` / `(c,0)→"\n(c completed)"` / `(0,k)→"\n(k cancelled)"` /
  `(c,k)→"\n(c completed, k cancelled)"` 全覆盖。
- ✅ **verbatim 守护**：chat UUID 原样渲染 + 断言 `task-019ea7f0`/`type:` 不出现（防合成）。

### YAGNI 边界（本轮不做）

- ❌ **不做 host 接线**——不从 `TaskStore`/`SubAgentRuntime`/后台任务管理器取真实状态构造
  `ActiveAgentReminderState`；纯格式化层 + 构造式测试交付，host 适配层留后续轮次。
- ❌ **不移植 harness-only 段**——files-in-flight / AGENTS.md / skills / MCP servers / memory 段
  依赖 host 文件系统与 manifest，属 host 集成层，本轮只做活动 agent 状态三段。
- ❌ **不接 compact 执行层**——`append_reminder_block` 是纯函数，但不把它注入（尚未实现的）compact
  流水线把 reminder 追加到真实 LLM 摘要；待 compact.rs 移植轮次。
- ❌ **不做 IPC 暴露**——不新增 `compaction.reminder` handler / 前端渲染；压缩后提醒的可视化留 host 集成。
- ❌ **不做 TodoStatus 序列化**——grok 的 serde 在 Python 用 Enum 值字符串即可，本轮无持久化需求，
  不加 `to_dict`/`from_dict`（与 R28 config 不同，reminder 是纯内存视图）。

### Commit

`feat(platform): R29 post-compaction reminder formatting (fuse grok xai-grok-compaction)`

---

## R30 — 压缩可观测性接缝层（fuse grok `xai-grok-compaction` observers）

### 本轮目标

融合 `grok-build/crates/common/xai-grok-compaction` 的**三个接缝类型**——
`intra_compaction/traits.rs::CompactionTarget` + `intra_compaction/observer.rs::IntraCompactionObserver`
+ `inter_compaction/observer.rs::InterCompactionObserver`。这是 compaction 链路的**度量端**：
R28 决定何时压缩、R29 保证压缩后上下文不丢、**R30 让压缩过程本身可观测**——每次 pass 的
成功/失败/token 缩减/耗时、每次 inter 流水线的 re-compaction/chunk 采样事件，都通过观察者回调上报，
而共享压缩引擎本身**零度量后端依赖**。

**产品融合点（不只是移植）**：这是 **R20-R21 resilience observer 栈的 compaction 侧对称镜像**——
R20-R21 把 circuit-breaker / retry 事件通过 observer 接入 `TelemetryEngine`，R30 用完全相同的
"backend-free 观察者基类 + NULL 单例"模式为 compaction 预留同一接入点。未来一个 host-wiring 轮次把
两套 observer 都路由进同一个 `TelemetryEngine`，compaction 事件就能直达进度面板。

### 融合结论（✅ 保持 / ❌ 放弃）

- ✅ **`CompactionTarget` 3 成员 Enum**（STEPS/HISTORY/FULL_REPLACE）+ `label()` 返回稳定低基数标签
  （`"steps"`/`"history"`/`"full_replace"`，与 `value` 一致）——作度量维度，观察者永不触碰枚举本身。
- ✅ **`IntraCompactionObserver` 普通基类**（非 ABC）：`on_error(status:str)` + `on_success(target,
  tokens_before, tokens_after, turns_compacted, elapsed:float)` 全默认 no-op，可实例化。
- ✅ **`InterCompactionObserver` 普通基类**（非 ABC）：`on_recompaction(strategy:str)` +
  `on_chunk_sampled(success:bool, elapsed:float)` + `on_chunk_count(num_chunks:int)` 全默认 no-op。
- ✅ **`NULL_INTRA_OBSERVER` / `NULL_INTER_OBSERVER` 单例**——grok `impl … for ()` 的 Python 模拟：
  无度量后端的 harness / 测试直接传单例满足观察者参数，无需子类化。
- ✅ **`on_recompaction` 接 `strategy: str`**（稳定标签），**不接 `CompactionStrategy` 枚举**——接缝
  刻意避免依赖该枚举，保持引擎无 host 耦合。
- ❌ **不移植 `CompactionStreamProc`**——host `Item` 泛型 trait，属 host 集成层（执行流水线），本轮只做
  接缝；执行层留 compact.rs 移植轮次。
- ❌ **不移植 `CompactionStrategy` 枚举**——仅作 `on_recompaction` 的字符串标签来源被引用，本身
  （basic/divide_and_conquer/...）留执行层轮次。
- ❌ **不接 `TelemetryEngine`**——交付纯接缝 + NULL 单例；把回调路由进 R20-R21 的 `TelemetryEngine`
  留 host-wiring 轮次（届时 resilience + compaction 两套 observer 一起接线）。

### 交付

- `agent/minimax_code/compaction/observers.py`（131 行，新建）：`CompactionTarget` Enum + 2 观察者
  基类 + 2 NULL 单例。`from __future__ import annotations` + `enum.Enum`。模块 docstring 详述
  trait→base-class 映射决策。
- `agent/tests/test_compaction_observers.py`（166 行，新建）：**12 个测试**（10 函数，`label()` 参数化
  展开 +2）= 4 个 Target 守护（label×3 参数化 / 三成员 / 可哈希 / 包级 re-export）+ 3 个 Intra 守护
  （默认 no-op / NULL 单例 / 子类 override-on_success-继承-on_error）+ 3 个 Inter 守护
  （默认 no-op / NULL 单例 / 子类 override 全三事件）。
- `agent/minimax_code/compaction/__init__.py`（重写）：re-export config(R28) + observers(R30) +
  reminder(R29) + trigger(R28)，`__all__` 按 R 分组标注，crate-level discoverability。

**核心设计决策——trait → 普通基类（非 ABC）**：

grok trait 是"**带默认实现的接口**"，不是纯抽象接口——harness 可只 override 关心的事件，单位类型
`()` 通过吃掉所有默认实现满足整个 trait。其忠实 Python 模拟是**默认 no-op 的普通基类**
（`logging.Handler` / `BaseHTTPRequestHandler` / Django signal receiver 同款模式）：无需 override 即可
实例化，子类选择性 override。因此**刻意不继承 `abc.ABC`**——那会强制 `@abstractmethod`，从而禁止
grok `impl … for ()` 存在的理由——null observer 用例。ruff 的 **B024**（ABC 无 abstractmethod）+
**B027**（ABC 空方法无装饰器）正是此决策的印证：初版误用 ABC 触发 9 个 ruff 错误，去 ABC 后全部消失。

**12 个关键移植契约**（测试逐条锁定）：

1. **`CompactionTarget` 恰三成员**（`test_compaction_target_has_three_members`）——Target(哪段, 3) ≠
   Mode(怎么压, 4)，无 "combined" target。
2. **`label()` == `value`**（`test_compaction_target_label` 参数化 3）——稳定低基数度量维度。
3. **Target 可哈希**、可做 dict key / set 成员（`test_compaction_target_is_hashable_and_distinct`）。
4. **包级 re-export**——`from minimax_code.compaction import CompactionTarget` 等价于 observers 模块
   （`test_compaction_target_reexported_from_package`：`ReexportedTarget is CompactionTarget`）。
5. **Intra 默认 no-op 可实例化**（`test_intra_observer_defaults_are_noops`：bare `IntraCompactionObserver()`
   全方法返回 None，从不 raise）。
6. **`NULL_INTRA_OBSERVER` 是基类实例**（`test_null_intra_observer_is_singleton_instance`：`isinstance` + 全 no-op）。
7. **子类 template-method 语义**（`test_intra_subclass_records_success_and_inherits_error`：override
   `on_success` 仍继承 `on_error` no-op）。
8. **Inter 默认 no-op 可实例化**（`test_inter_observer_defaults_are_noops`：三方法全 None）。
9. **`NULL_INTER_OBSERVER` 是基类实例**（`test_null_inter_observer_is_singleton_instance`）。
10. **`on_recompaction` 接 `str` 策略标签**（`test_inter_subclass_records_all_three_events`：传
    `"basic"` / `"divide_and_conquer"`，非枚举实例）——接缝不依赖 `CompactionStrategy` 枚举。
11. **`on_chunk_sampled(success: bool, elapsed: float)`** 签名（`(True, 2.5)` / `(False, 0.1)`）。
12. **`on_chunk_count(num_chunks: int)`** 签名（含 `0` 边界）。

### 验证

- ✅ **ruff**：`All checks passed`（E/F/W/I/B/UP，line-length 100；初版 9 错误 = B024×2 + B027×5 +
  I001×2，**全部由"去 ABC + `--fix` 排序"根除**，零 noqa 污染）。
- ✅ **新测试**：12 passed（含 `label()` 参数化 3 case）。
- ✅ **完整套件零回归**：1351 passed in 90.89s = 1339 基线 + 12 新增，**精确匹配**。
- ✅ **NULL 单例语义**：`isinstance(NULL_INTRA_OBSERVER, IntraCompactionObserver)` +
  `isinstance(NULL_INTER_OBSERVER, InterCompactionObserver)` 双断言。
- ✅ **Target ≠ Mode 边界**：三成员集合断言锁定（防误加第四个 "combined" target）。
- ✅ **接缝无枚举耦合**：`on_recompaction` 测试用裸字符串标签，证明观察者可在 `CompactionStrategy`
  枚举移植前独立工作。

### YAGNI 边界（本轮不做）

- ❌ **不移植 `CompactionStreamProc`**——host `Item` 泛型执行 trait（select/sample/guard/commit 流水线），
  属 host 集成层；本轮只做度量接缝，执行层留 compact.rs 移植轮次。
- ❌ **不移植 `CompactionStrategy` 枚举**——仅以字符串标签形式被 `on_recompaction` 引用；枚举本体
  （basic/divide_and_conquer/...）及其 `label()` 留执行层轮次。
- ❌ **不接 `TelemetryEngine`**——交付纯接缝 + NULL 单例；resilience（R20-R21）+ compaction（R30）
  两套 observer 一起路由进 `TelemetryEngine` 留 host-wiring 轮次。
- ❌ **不做 IPC 暴露**——不新增 `compaction.observer` handler / 前端度量面板；compaction 事件可视化
  留 host 集成。
- ❌ **不做 async/并发观察者**——grok 观察者是同步回调（`&self` 方法），本轮保持同步；async 派发
  （若未来 TelemetryEngine 需要）留接线轮次。
- ❌ **不做 observer 注册表/链**——单 observer 参数（grok 模式），不做多播链；harness 需多播时自建组合
  observer（`__init__` 持 list，每方法 fan-out）。

### Commit

`feat(platform): R30 compaction observability seam (fuse grok xai-grok-compaction)`

---

## R31 — STT 语言代码映射层（fuse grok `xai-grok-voice/language`）

### 本轮目标

融合 `grok-build/crates/codegen/xai-grok-voice/src/language.rs`（311 行，纯数据+纯函数，
6 个 `#[test]`）。**D 阶段（多模态与交互）开局第一轮**——为 MiniMax Code 语音输入打语言地基。
这是 xAI STT 端点（`api.x.ai/v1/stt`）`language` 参数的**唯一真相源**：25 语言目录 +
把用户/配置串（含 BCP-47 / POSIX locale / 别名）规范化成 wire code 的纯函数。

**产品融合点（不只是移植）**：语音输入链路（mic → 流式 STT → prompt box）的语言识别是第一关。
本轮交付**目录 + 规范化层**，是 MiniMax Code 全新 `voice/` 包的基石。且目录与规范化器是
**语言无关的通用构件**——BCP-47 / locale 解析、Tagalog→Filipino 别名映射——可复用于任何多语言
场景（不止 STT）。重 IO 切片（音频捕获 `audio`、流式 STT 客户端 `stt`、认证 `auth`、流水线
`pipeline`、麦克风探测 `probe`）是 host 集成层，留后续轮次。

纯数据+纯函数：零 async、零网络、零音频、零 host 依赖。Rust 的 `&'static str` 静态生命周期在
Python 用 intern 字符串字面量表达，`Copy + Eq` derive 用 `@dataclass(frozen=True, slots=True)`
等价（不可变、可哈希、值相等）。

### 融合结论（✅ 保持 / ❌ 放弃）

- ✅ **`SttLanguage` frozen+slots dataclass**（`code: str` + `name: str`）—— Rust `Copy + Eq`
  struct 的忠实模拟：不可变、可哈希、值相等、pass-by-reference 带值语义。
- ✅ **25 语言目录 `STT_LANGUAGES: tuple[SttLanguage, ...]`**（按 English name 排序）—— 不可变
  tuple 对应 Rust `&[SttLanguage]` 切片引用，pinned 到 docs.x.ai 公开目录。
- ✅ **2 常量**：`STT_LANGUAGE_AUTO = "auto"`（client-only 哨兵，永不发 wire）+
  `STT_LANGUAGE_DEFAULT = "en"`（unset/未识别默认）。
- ✅ **3 公开函数**：`stt_language_by_code`（精确大小写敏感查找）+ `canonicalize_stt_language`
  （用户串→code，含 BCP-47/locale/别名解析）+ `language_for_api`（解析 auto→系统 locale）。
- ✅ **4 私有辅助**：`_system_stt_language`（POSIX 优先级 locale 解析）+
  `_primary_language_subtag`（`_`/`-`/`.` 切首段）+ `_match_supported_code`（大小写不敏感匹配）+
  `_alias_to_supported`（`tl`→`fil`）。
- ❌ **不移植 `audio` / `stt` / `auth` / `pipeline` / `probe`** —— 音频硬件捕获、流式 STT 客户端、
  认证、流水线、麦克风探测全属 host 集成层，本轮只做语言层。
- ❌ **不接真实 STT API** —— 不调 xAI/MiniMax STT 端点；`language_for_api` 交付纯解析函数，
  wire 调用留 host 集成。
- ❌ **不做 Windows 原生 locale** —— 忠实移植 POSIX 优先级（`LC_ALL`>`LC_MESSAGES`>`LANG`）；
  Windows 上 `GetUserDefaultLocaleName` 扩展留后续（YAGNI，POSIX 变量在 `os.environ` 仍可读写）。

### 交付

- `agent/minimax_code/voice/language.py`（~195 行，新建）：`SttLanguage` dataclass + 25 语言 tuple
  + 2 常量 + 3 公开函数 + 4 私有辅助。`from __future__ import annotations` + `dataclasses` +
  `os` + `re`。模块 docstring 详述 struct→frozen-dataclass 映射决策。
- `agent/minimax_code/voice/__init__.py`（新建）：re-export 全部 7 个公开符号，
  crate-level discoverability，docstring 标注 R31 范围与未移植切片。
- `agent/tests/test_voice_language.py`（~195 行，新建）：**13 函数 / 33 测试 item** =
  6 个 grok 镜像（catalog 匹配 docs / code 唯一+name 非空+无连字符 / 按英文名排序 /
  canonicalize 16 case 参数化 / language_for_api 永不返回 auto / lookup 精确大小写敏感 4 case）
  + 7 个 Python 守护（5 个 monkeypatch locale 解析：resolves_auto / empty_lcall_falls_through /
  lcall_beats_lang / posix_is_default / alias_locale + lookup 返回 entry + frozen-hashable +
  tuple 类型/长度 + 常量）。

**核心设计决策——struct → frozen+slots dataclass**：

grok `SttLanguage` derive `Copy + Eq`（值拷贝、值相等）。Python 等价是 `@dataclass(frozen=True,
slots=True)`：frozen 给不可变 + 可哈希 + 值相等（`__eq__`/`__hash__` 自动生成），slots 给内存紧凑
+ 阻止新增属性。模块级 `STT_LANGUAGES` tuple 持 intern 字符串字面量，所以 `lang.code` 返回给调用方
的就是 Rust `&'static str` 的稳定等价物——无需显式 interning。

**15 个关键移植契约**（测试逐条锁定）：

1. **25 语言精确匹配 docs.x.ai**（`test_catalog_matches_public_docs_exactly`：`DOCS_CODES` frozenset
   pin，防目录漂移）。
2. **code 唯一 + name 非空 + code 无 `-`**（仅 primary，`test_catalog_codes_are_unique_and_names_nonempty`）。
3. **按 English name 排序**（`test_catalog_sorted_by_english_name`：`names == sorted(names)`）。
4. **canonicalize 16 case**（参数化）：`None`/`""`/`"  "`→`en`；`"en"`→`en`；`"ES"`→`es`；
   `"  fr "`→`fr`；`"auto"`/`"AUTO"`→`auto`；`"en-US"`→`en`；`"pt_BR.UTF-8"`→`pt`；`"fil"`→`fil`；
   `"tl"`/`"tl-PH"`→`fil`；`"zh"`/`"zh-Hans"`/`"nope"`→`en`。
5. **`language_for_api` 永不返回 `auto`**（`test_language_for_api_never_returns_auto`：无 locale→`en`）。
6. **POSIX locale 优先级** `LC_ALL` > `LC_MESSAGES` > `LANG`（`test_language_for_api_lcall_beats_lang`）。
7. **空 var 视为 unset**（`test_language_for_api_empty_lcall_falls_through_to_lang`：空 `LC_ALL` 不遮蔽 `LANG`）。
8. **`C`/`POSIX` locale → 默认**（`test_language_for_api_posix_locale_is_default`）。
9. **别名 locale 解析**（`test_language_for_api_alias_locale`：`tl_PH`→`fil`）。
10. **`stt_language_by_code` 精确大小写敏感**（`"EN"`/`"auto"`/`"zh"`→`None`，参数化 4 case）。
11. **lookup 返回完整 entry**（`test_lookup_returns_matching_entry`：code+name）。
12. **`SttLanguage` frozen+hashable**（`test_stt_language_is_frozen_and_hashable`：值相等、hash 相等、
    set 去重、赋值 raise `AttributeError`）。
13. **`STT_LANGUAGES` 是 25 元素 tuple**（不可变序列，`test_stt_languages_is_immutable_tuple_of_25`）。
14. **常量值锁定**（`STT_LANGUAGE_AUTO=="auto"`、`STT_LANGUAGE_DEFAULT=="en"`）。
15. **`auto` 是 client-only 哨兵**——`canonicalize` 可返回 `auto`，但 `language_for_api` 必解析之，
    永不上 wire（契约 5 的双面）。

### 验证

- ✅ **ruff**：`All checks passed`（E/F/W/I/B/UP，line-length 100，零错误零 noqa）。
- ✅ **新测试**：33 passed in 0.08s（13 函数，canonicalize 16 + lookup 4 参数化展开）。
- ✅ **完整套件零回归**：1384 passed in 84.92s = 1351 基线 + 33 新增，**精确匹配**。
- ✅ **locale 解析确定性**：5 个 monkeypatch 测试覆盖 POSIX 优先级全分支
  （`_no_posix_locale` fixture 剥离环境变量，Windows 主机亦确定）。
- ✅ **frozen 语义**：`with pytest.raises((AttributeError, TypeError)): a.code = "fr"` 锁定不可变。
- ✅ **目录防漂移**：`DOCS_CODES` frozenset 与 `STT_LANGUAGES` 双向集合相等断言。

### YAGNI 边界（本轮不做）

- ❌ **不移植 host IO 切片** —— `audio`（麦克风捕获）/ `stt`（流式 STT 客户端）/ `auth`（认证）/
  `pipeline`（`run_voice_pipeline`）/ `probe`（麦克风探测）全属 host 集成层，本轮只做语言层。
- ❌ **不接真实 STT API** —— 不调 xAI/MiniMax STT 端点；`language_for_api` 是纯解析函数，wire 调用
  留 host 集成轮次。
- ❌ **不做 Windows 原生 locale** —— 忠实移植 POSIX `LC_ALL`/`LC_MESSAGES`/`LANG` 优先级；
  `GetUserDefaultLocaleName` 扩展留后续（POSIX 变量在 `os.environ` 仍可读写，逻辑可测）。
- ❌ **不做 IPC 暴露** —— 不新增 `voice.language` handler / 前端语音设置 UI；语言选择的可视化留
  host 集成。
- ❌ **不做 TTS 语言映射** —— STT 不接受 `auto`（本轮契约），TTS 接受；TTS 目录与映射是独立 crate，
  留后续（若 MiniMax Code 引入语音输出）。
- ❌ **不做 catalog 持久化/序列化** —— 目录是编译期常量（Rust `&'static`），Python 模块级 tuple；
  无 `to_dict`/`from_dict`（与 R28 config 不同，language 是纯静态目录）。

### Commit

`feat(platform): R31 STT language code mapping (fuse grok xai-grok-voice)`

## R32 — voice 事件+错误类型层（融合 grok `xai-grok-voice` event/error）

### 本轮目标

**D 阶段（多模态与交互）第二轮**——延续 R31 开启的 voice 包，补全 voice 的
"类型词汇层"：流式事件（`VoiceEvent`）+ 错误层次（`VoiceError`）。两者都是 grok
`xai-grok-voice` 的纯枚举文件（`event.rs` 12 行 / `error.rs` 24 行），无 IO、无
async、无 grok 测试模块——零主机依赖、零集成成本，为未来 voice pipeline 驱动器
（麦克风捕获 → STT WebSocket）铺好"信号 + 失败方式"的类型契约。

本轮一次性移植两个互补的小文件：事件 = 流式信号（驱动 UI），错误 = 异常路径
（驱动现有 MiniMax Code 按异常类型分发的错误处理）。同属 voice 类型层，合为
一个"类型词汇"轮次，避免过度拆分。

### 融合结论

✅ **保持：**
- **event.rs → tagged union of frozen dataclass**（`InterimTranscript` /
  `UtteranceFinal` / `VoiceEventError`）+ `VoiceEvent` Union 别名。Rust
  enum-with-struct-variant 的忠实 Python 等价；`isinstance` / `match` 分发
  复刻 Rust `match`。frozen+slots 复刻 `Debug + Clone + PartialEq + Eq`。
- **error.rs → Exception 层次**（`VoiceError` 基 + 4 子类），而非值枚举。关键
  判断：grok 用 `thiserror`（`#[derive(Error)]`），明示这是"错误类型"；Python
  错误流过 `raise`/`except`，异常层次是天然对应，比值枚举地道得多。每子类
  `_prefix` 类属性复刻 `#[error("prefix: {0}")]` 的显示格式。
- **event 的 Error 变体 vs error 的 VoiceError 显式分离**——grok 也是两个东西
  （`event.rs::VoiceEvent::Error { message }` 持裸字符串 vs `error.rs::VoiceError`
  是 thiserror 错误）。命名上 `VoiceEventError`（事件）≠ `VoiceError`（异常）避免混淆。

✅ **产品融合点：**
- `InterimTranscript` / `UtteranceFinal` 直接映射 MiniMax Code 现有流式消息通道
  （`agent.message_chunk` 事件）——语音驱动 prompt 只是另一个流式生产者。
- `VoiceError` 异常层次接入 MiniMax Code 现有"按异常类型路由错误"的处理路径，
  无需字符串匹配消息。

❌ **放弃：**
- ❌ **不做 pipeline 驱动器**——`run_voice_pipeline`（mic 捕获 + STT WebSocket 流式）
  是主机集成层（音频硬件 + 网络），留后续轮次。本轮纯类型。
- ❌ **不做 audio/auth/stt/probe 切片**——都是主机 IO 层。
- ❌ **VoiceError 不接入现有 agent 错误 MRO**——本轮仅定义类型；接线是后续轮次
  （避免本轮跨层耦合）。
- ❌ **不做 VoiceEvent 的 serde/JSON 序列化**——Rust derive 但 grok 内部无实际
  跨进程用途；未来 IPC 跨进程时再加（YAGNI）。

### 交付

| 文件 | 行数 | 内容 |
|------|------|------|
| `agent/minimax_code/voice/event.py` | +79 | `VoiceEvent` tagged union（3 frozen dataclass + Union 别名），含 enum→union 映射文档 |
| `agent/minimax_code/voice/error.py` | +78 | `VoiceError` Exception 基类 + 4 子类（Config/Stt/Auth/WebSocket），`_prefix` 复刻 thiserror |
| `agent/minimax_code/voice/__init__.py` | +35 | 重导出 R32 共 9 符号（event 4 + error 5） |
| `agent/tests/test_voice_event.py` | +92 | 11 测试项（union 成员、字段、frozen+hashable、isinstance/match 分发） |
| `agent/tests/test_voice_error.py` | +92 | 14 测试项（前缀/display、issubclass、raise/catch、args 一致性） |

契约要点：
1. `VoiceEvent = InterimTranscript | UtteranceFinal | VoiceEventError`（PEP 604 union）
2. `VoiceEvent.__args__` = `{InterimTranscript, UtteranceFinal, VoiceEventError}`
3. `str(VoiceSttError("x")) == "STT: x"`（复刻 thiserror 前缀）
4. `exc.message == "x"`（裸载荷在 `.message`，格式化串在 `args[0]`）
5. 所有变体 `issubclass(_, VoiceError)`（异常层次单一根）

### 验证

- `ruff check minimax_code/voice/ tests/test_voice_*.py` → **All checks passed!**（零 noqa）
- `pytest tests/test_voice_event.py tests/test_voice_error.py -q` → **25 passed**（event 11 + error 14）
- 完整套件 `pytest -q` → **1409 passed in 88s**（R31 1384 → R32 1409，+25 精确，零回归）

### YAGNI 边界

- ❌ **不做 VoiceEvent 序列化**——Rust 有 `derive(Serialize)` 但 grok 内部无实际
  跨进程序列化用途；MiniMax Code 前端是 TS，未来若 IPC 传事件再加（YAGNI）。
- ❌ **不做 VoiceError 的 `__cause__` 链/`from_other` 构造器**——thiserror 的
  `#[from]` 在 grok VoiceError 里**没有**使用（全是 `String` 变体），本轮忠实于
  源头，不加。
- ❌ **VoiceEvent 不带 metadata（时间戳/序列号）**——grok 的 `VoiceEvent` 只有
  `text`/`message` 两类字段；加 metadata 是过度设计。
- ❌ **不合并 event.py 与 error.py**——grok 分文件，Python 也分（关注点分离：
  事件流 vs 异常路径）。

### Commit

`feat(platform): R32 voice event/error types (fuse grok xai-grok-voice)`

## R33 — voice config 表 + TLS-only URL 构造器（融合 grok `xai-grok-voice` config）

### 本轮目标

**D 阶段第三轮**——voice 主机无关切片的**收官**：移植 `config.rs`（186 行）的
`VoiceConfig` 传输参数表 + `ws_url` TLS-only 构造器。这一轮把 R31（language 目录）
和 R32（error 层次）串起来——`VoiceConfig` 用 `STT_LANGUAGE_DEFAULT` 做语言默认值，
`ws_url` 用 `VoiceConfigError` 报不安全端点——交付一个完整的"配置 → 安全 URL +
语言码"的纯逻辑闭环，留给未来 streaming-STT 驱动器接线。

本轮的核心价值是**两个安全不变量**，必须逐字保留。

### 融合结论

✅ **保持：**
- **TLS-only 强制（不降级）**——`http://`/`ws://` `api_base` 被**拒绝**
  （raise `VoiceConfigError`），绝不静默降级为 `wss://`。原因：bearer token 走这个
  WebSocket 连接，绝不能明文传输。这是安全关键，不是偏好。
- **反欺骗身份字段**——`client_identifier`/`user_agent` 是运行时身份（host 在解析后
  盖戳），**不是用户配置**：`from_config_table` 即使在 `[voice]` 表里看到它们也
  刻意忽略（grok `#[serde(skip)]`），用户无法伪造归因 header。
- **legacy 字段容忍（无 deny_unknown_fields）**——已移除的本地开关 `enabled`、
  未知键 `push_to_talk` 等被静默丢弃，旧配置仍能加载（向后兼容）。
- **struct → 普通 dataclass（非 frozen）**——grok `#[derive(..., PartialEq)]` 无
  `Eq`/`Hash`，是可变 serde 目标；Python 用普通 `@dataclass`（可变，非 frozen）。
- **全字段默认值**——grok `#[serde(default)]` → dataclass 字段默认；`[voice]` 表可选。

✅ **产品融合点：**
- `VoiceConfig` 接入 MiniMax Code 现有 config 加载（pydantic Config 模型之外的可选
  `[voice]` 表），`from_config_table` 接受 `Mapping`（解耦具体 TOML 解析器）。
- TLS-only 不变量复用项目已有的"明文端点零容忍"安全姿态（R15 出站脱敏同源）。

❌ **放弃：**
- ❌ **不做 streaming-STT 驱动器**——`run_voice_pipeline`（mic 捕获 + WebSocket 流式）
  是主机集成层（音频硬件 + 网络），留后续。
- ❌ **不接入 pydantic Config**——本轮纯 dataclass，忠实 grok struct；pydantic 整合
  是接线轮次（避免跨层耦合 + 避免 pydantic 运行时验证改变 serde 语义）。
- ❌ **不做 config 热重载/变更通知**——grok config 是启动期解析一次；本轮忠实。

### 交付

| 文件 | 行数 | 内容 |
|------|------|------|
| `agent/minimax_code/voice/config.py` | +130 | `VoiceConfig` dataclass（8 字段全默认）+ `ws_url`（TLS-only）+ `from_config_table`（anti-spoof + legacy 容忍） |
| `agent/minimax_code/voice/__init__.py` | +14 | 重导出 R33 共 3 符号，docstring 加 R33 段 |
| `agent/tests/test_voice_config.py` | +119 | 13 测试（7 grok 逐字移植 + 6 Python 专属守护） |

契约要点：
1. `ws_url("https://api.x.ai", "/v1/stt") == "wss://api.x.ai/v1/stt"`
2. `ws_url("http://...", ...)` raise `VoiceConfigError`（不降级）
3. `ws_url("api.x.ai", ...)` scheme-less → `wss://`（默认 TLS）
4. `from_config_table({"voice": {...}})` 忽略 `client_identifier`/`user_agent`（anti-spoof）
5. `from_config_table({})` → 全默认（`[voice]` 表可选）
6. 默认 `api_base="https://api.x.ai"`, `language=STT_LANGUAGE_DEFAULT`("en"), `sample_rate=16000`

### 验证

- `ruff check minimax_code/voice/ tests/test_voice_config.py` → **All checks passed!**（I001 由 `--fix` 自动修：tomllib 标准库 / pytest 第三方分组）
- `pytest tests/test_voice_config.py -q` → **13 passed**
- 完整套件 `pytest -q` → **1422 passed in 87s**（R32 1409 → R33 1422，+13 精确，零回归）

### YAGNI 边界

- ❌ **不做 pydantic 校验**——grok serde 保证类型；Python dataclass 不做运行时类型
  强制（忠实 grok struct 语义）。pydantic 接入是接线轮次。
- ❌ **不做 `stt_ws_url` 的 query param 注入**——grok 的 `stt_ws_url()` 只拼 host+path，
  language/token 是连接时 header，不在 URL；本轮忠实。
- ❌ **不缓存 URL 构造**——`stt_ws_url()` 每次重算（grok 也是），config 低频读。
- ❌ **不做 `__post_init__` 校验 sample_rate 范围**——grok 无此校验（serde 接受任意
  u32）；过度设计。未来 STT 驱动器若需要再 add。

### Commit

`feat(platform): R33 voice config + TLS-only ws_url (fuse grok xai-grok-voice)`
