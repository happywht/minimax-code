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

## R34 — MCP-over-ACP wire 常量 + OAuth 配置形状（融合 grok `xai-grok-mcp`）

### 本轮目标

**D 阶段第四轮**——开启 MCP 子系列。移植 grok `xai-grok-mcp` 的 `wire.rs`（ACP 线路
常量）+ `oauth_config.rs`（OAuth 配置类型）。这两个是 grok mcp crate 的**协议骨架
切片**：纯常量 + 纯数据，零主机 IO，是 MCP 集成层最干净的入门切片。

本轮的**关键认知**：现有 `mcp/` 包（阶段 A 的 R3-R5）已持有**公开 MCP 规范**协议常量
（`protocol.py`：`2024-11-05` 版本的标准 JSON-RPC 方法如 `tools/list`、`ping`）。
grok 的 `wire.rs` 是 **xAI 私有的 ACP-over-MCP 扩展**字符串（`x.ai/mcp/*`：
agent↔SDK 反向通道的 method / `_meta` 键）——正交层，零语义重叠。本轮**补全**现有
mcp 包缺的两个切片（ACP 扩展 wire + OAuth 配置形状），而非重建。

### 融合结论

✅ **保持：**
- **wire 常量集中定义（单一真相源）**——4 个 `x.ai/mcp/*` 字符串作为常量，避免 magic
  string 散落在 handler 各处。agent 与 SDK peer 共享同一组定义，不会漂移。
- **call vs sdk_call 显式区分**——正向（client→agent：`MCP_CALL = x.ai/mcp/call`）与
  反向（agent→SDK in-process：`MCP_SDK_CALL = x.ai/mcp/sdk_call`）用不同 method 字符串，
  metrics/tracing 能区分两套不相交的 schema。
- **MCP_SDK initialize 能力旗标 + MCP_SERVERS session/new `_meta` 键**——分别在握手
  advertise 和会话创建时列出 in-process SDK MCP servers。
- **oauth_config 是纯形状**——`McpOAuthConfig` 只持 `client_id`/`client_secret`/
  `scopes`/`callback_port`，零网络、零 token 交换；`is_configured()` 单一谓词
  （`client_id` 在场 = 运营者意图 OAuth）。grok `#[derive(Debug, Clone, Default)]`
  → 普通 dataclass（可变，全默认）。
- **`HashMap<String, McpOAuthConfig>` → `McpOAuthConfigMap: TypeAlias = dict[str, McpOAuthConfig]`**。

✅ **产品融合点：**
- wire 常量复用现有 mcp 包（R3-R5）的协议层；ACP 扩展与标准 MCP 协议**共存**于同一包，
  `__init__.py` docstring 明确区分（`protocol` = spec namespace，`wire` = ACP extension
  namespace）。
- oauth_config 的凭证*值*未来交给项目已有 secrets 层（R15 脱敏 + OS keyring）；本模块
  只是*形状*——what fields a server's OAuth block carries。

❌ **放弃：**
- ❌ **不做 OAuth 流程**（浏览器握手、token 交换）——主机层，留后续切片。
- ❌ **不做 MCP server 连接/credentials/liveness**——主机 IO，留后续切片。
- ❌ **不接 rmcp（Rust MCP SDK）**——grok 用 rmcp 做标准 MCP，我们用现有 `protocol.py`
  （R3）已等价；无需 Rust 工具链。

### 交付

| 文件 | 行数 | 内容 |
|------|------|------|
| `agent/minimax_code/mcp/wire.py` | +45 | 4 个 `x.ai/mcp/*` ACP 常量 + forward/reverse 语义注释 |
| `agent/minimax_code/mcp/oauth_config.py` | +51 | `McpOAuthConfig`（4 可选字段 + `is_configured`）+ `McpOAuthConfigMap` 别名 |
| `agent/minimax_code/mcp/__init__.py` | +25 | 重导出 6 符号（4 wire + 2 oauth），docstring 加 R34 scope 段（wire/oauth_config 与 protocol 正交说明） |
| `agent/tests/test_mcp_wire.py` | +54 | 8 测试（重导出 + 4 精确值 + namespace 前缀 + call/sdk_call 区分 + str 类型） |
| `agent/tests/test_mcp_oauth_config.py` | +65 | 7 测试（重导出 + 默认未配置 + client_id 单信号 + 全配置 + map 别名 + 可变性） |

契约要点：
1. `MCP_CALL == "x.ai/mcp/call"`（client→agent 正向）
2. `MCP_SDK_CALL == "x.ai/mcp/sdk_call"`（agent→SDK 反向，与 `MCP_CALL` 不相交）
3. `MCP_SERVERS == "x.ai/mcp/servers"`（`session/new` `_meta` 键）
4. `MCP_SDK == "x.ai/mcp/sdk"`（`initialize` 能力旗标）
5. `McpOAuthConfig().is_configured() == False`（默认未配置）
6. `McpOAuthConfig(client_id="x").is_configured() == True`（`client_id` 是单一谓词，secret 单独不算）

### 验证

- `ruff check minimax_code/mcp/ tests/test_mcp_wire.py tests/test_mcp_oauth_config.py` → **All checks passed!**（I001 由 `--fix` 自动修：wire 测试两个 `minimax_code.mcp` 导入分组）
- `pytest tests/test_mcp_wire.py tests/test_mcp_oauth_config.py -q` → **15 passed**
- 完整套件 `pytest` → **1437 passed in 89s**（R33 1422 → R34 1437，+15 精确，零回归）

### YAGNI 边界

- ❌ **不做 OAuth 流程模块**——oauth_config 是形状，token 交换是主机层（浏览器回调），
  留后续切片。
- ❌ **不做 `MCP_SERVERS` 的 `_meta` 序列化**——本轮只定义键名常量，序列化/解析由未来
  session handler 接线。
- ❌ **不做 wire 常量的 Enum**——grok 是 `pub const &str`，Python 模块级 `str` 常量更
  idiomatic（可 f-string 拼接、零开销、与 grok 同形）。
- ❌ **不做 oauth_config 的 pydantic 校验**——纯 dataclass 忠实 grok struct；接线轮次
  再考虑。
- ❌ **不做 `MCP_SDK` capability 协商逻辑**——本轮只定义旗标字符串，协商由未来
  initialize handler。

### Commit

`feat(platform): R34 MCP wire + oauth config (fuse grok xai-grok-mcp)`

## R35 — MCP liveness 决策层（融合 grok `xai-grok-mcp` liveness）

### 本轮目标

**D 阶段第五轮**——MCP 子系列继续。移植 grok `xai-grok-mcp/liveness.rs` 的**纯决策层**：
`ClientStateKind`（状态投影）+ `LivenessCheck`（三态决策）+ `classify_liveness` 状态机纯
函数 + `DEFAULT_POLL_INTERVAL_MS` + `McpClientEventKind`（coalescing discriminant）。

本轮的**关键剥离**：`liveness.rs` 重度依赖 tokio 任务 + Arc/Mutex 槽 + DropGuard 取消
（主机运行时），但其核心 `liveness_check` 是一个**纯 match**（async 只包 mutex lock）——
把 IO 抽象成入参 `transport_closed: bool`，决策逻辑零依赖。

**关键映射决策**：grok 的**无负载 unit 枚举**（`#[derive(Copy, PartialEq, Eq)]` +
`Hash`）映射到 Python `enum.Enum`（+ `@unique`），而非 R32 的 frozen-dataclass 联合——
**负载决定映射**：unit 变体无负载可携，Enum 给单例值语义 + 可哈希，精确匹配 grok 的
`Copy`/`Hash`。这是有意识的策略分化，不是不一致。

### 融合结论

✅ **保持：**
- **`classify_liveness` 纯状态机**——5 行决策表的纯函数：`Ready + open → Healthy`，
  `Ready + closed → TransportClosed`，`非 Ready → Transient`。grok `McpClient::liveness_check`
  的纯投影（去掉 mutex acquire）。
- **false-positive 防护不变量**——非 Ready 状态（含 Initializing + 瞬时 closed transport）
  一律 Transient，**绝不上报 TransportClosed**。这正是 grok liveness 模块要修的 bug：
  re-handshake 时不误报。
- **`ClientStateKind` 4 变体 Copy 投影**——Empty/Pending/Initializing/Ready，
  payload-stripped（grok 丢掉 PendingTransport/McpService 负载，仅留状态标签）。
- **`LivenessCheck` 3 决策**——Healthy/TransportClosed/Transient。
- **`McpClientEventKind` 7 变体可哈希 discriminant**——grok dispatcher 的 coalescing key
  `(server, kind)` 第二半；distinct from 带 payload 的 `McpClientEvent`（payload 不参与
  equality/hashing）。`#[derive(Hash)]` → Enum 可作 dict key。
- **`DEFAULT_POLL_INTERVAL_MS = 500`**——grok `Duration::from_millis(500)`，平均检测延迟 < 1s。

✅ **产品融合点：**
- 填补现有 mcp 包（R3-R5 + R34）**缺失的传输健康故事**——当前连接的 server 静默断开后
  与存活 server 不可区分，直到 tool call 失败。本模块是未来 liveness watcher 每 tick 调用
  的纯谓词。
- 集中决策表于此 → 未来 watcher 接线是 `classify_liveness + sleep` 薄循环，而非重写状态机。
- `McpClientEventKind` 与 R34 wire 常量同源（ACP `x.ai/mcp/server_status` push 的 kind 维度）。

❌ **放弃：**
- ❌ **不做 tokio watcher 任务/spawn/Arc/Mutex 槽/DropGuard 取消**——主机运行时集成，留接线轮。
- ❌ **不做 `McpClientEvent`（带负载大枚举）**——依赖 `McpServerName` + `u64` client_id +
  `Vec`，留下一轮（事件层）。
- ❌ **不做 50ms coalescing 窗口/dispatcher**——本模块只提供 key 的 discriminant，窗口留接线。

### 交付

| 文件 | 行数 | 内容 |
|------|------|------|
| `agent/minimax_code/mcp/liveness.py` | +155 | `DEFAULT_POLL_INTERVAL_MS` + 3 个 `@unique Enum`（ClientStateKind/LivenessCheck/McpClientEventKind）+ `classify_liveness` 纯函数 |
| `agent/minimax_code/mcp/__init__.py` | +12 | 重导出 5 符号，docstring scope 加 liveness 段 |
| `agent/tests/test_mcp_liveness.py` | +113 | 16 测试（重导出 + 间隔 + 3 枚举成员数 + classify 决策表 param 展开 + 可哈希 + 单例 + 返回类型） |

契约要点：
1. `DEFAULT_POLL_INTERVAL_MS == 500`（int）
2. `ClientStateKind` 4 成员（EMPTY/PENDING/INITIALIZING/READY）
3. `LivenessCheck` 3 成员（HEALTHY/TRANSPORT_CLOSED/TRANSIENT）
4. `McpClientEventKind` 7 成员（含 TRANSPORT_CLOSED）
5. `classify_liveness(READY, False) is HEALTHY`
6. `classify_liveness(READY, True) is TRANSPORT_CLOSED`
7. `classify_liveness(<非Ready>, *) is TRANSIENT`（false-positive 防护，3 状态 × 2 closed = 6 param）
8. `McpClientEventKind` 成员可作 dict key（Hash）
9. Enum 成员 `is` 同一性（Copy 语义）

### 验证

- `ruff check minimax_code/mcp/ tests/test_mcp_liveness.py` → **All checks passed!**（I001 由 `--fix` 自动修：as-别名导入拆单行块）
- `pytest tests/test_mcp_liveness.py -v` → **16 passed**
- 完整套件 `pytest` → **1453 passed in 89s**（R34 1437 → R35 1453，+16 精确，零回归）

### YAGNI 边界

- ❌ **不做 watcher 任务/spawn**——主机运行时（asyncio task），留接线轮。
- ❌ **不做 `McpClientEvent` 带 payload 枚举**——依赖 `McpServerName` 别名 + `u64` client_id +
  `Vec`，留下一轮。
- ❌ **不做 50ms coalescing 窗口**——本模块只给 discriminant，窗口是 dispatcher 接线。
- ❌ **不做 Enum 的 wire 序列化**（PascalCase vs snake_case）——grok 实际 ACP 序列化形式未定；
  本轮 Enum 值用 snake_case 字符串（Python 惯例），序列化映射留接线。
- ❌ **不做 `is_healthy` 谓词**——grok `is_healthy` = `state is Ready and not transport_closed`，
  等价于 `classify == HEALTHY`；不重复暴露（调用方用 classify 返回值判断）。
- ❌ **不做 `state_kind` 投影函数**——依赖内部 ClientState 状态机；本轮只移植 Kind 枚举，投影留接线。

### Commit

`feat(platform): R35 MCP liveness decision layer (fuse grok xai-grok-mcp)`

## R36 — MCP client 事件层（融合 grok `xai-grok-mcp` servers）

> 本轮目标：把 grok `xai-grok-mcp/servers.rs` 的 `McpServerName` 别名 +
> `McpClientEvent` **带 payload 枚举**移植成 Python——这是 R35
> `McpClientEventKind` discriminant 的"另一半"：Kind 是可哈希的 coalescing
> key，Event 携带真实负载（server 名、client_id、失败原因、config diff）。
> 本轮只做纯数据形状 + `server_name` accessor，把 MCP 包的"事件类型骨架"
> 补完。dispatcher（50ms 合并窗口、ConfigDiff 扇出）留接线轮。承接 R35 YAGNI
> 里"❌ 不做 McpClientEvent 带 payload 枚举——留下一轮"的承诺。

### 融合结论

✅ **保持**
- `McpServerName: TypeAlias = str`（grok `pub type McpServerName = String`）
- 8 变体 `McpClientEvent` 联合，用 **frozen=True, slots=True dataclass 联合**
  （PEP 604 `A | B | C`，isinstance/match 分发）——复用 R32 VoiceEvent 确立的
  **"带 struct payload 的 Rust 枚举 → frozen dataclass 联合"** 策略。
- `server_name(event) -> str | None` 纯函数（grok
  `McpClientEvent::server_name()`）：7 个带 server 变体返回 server，
  `ConfigDiff` 返回 None（dispatcher 会把它扇出成 per-server 子事件）。
- grok `Vec<McpServerName>` → `list[str]`（mutable，忠实 grok 无 Hash derive；
  ConfigDiff 不参与哈希）。

❌ **放弃（YAGNI / 主机运行时）**
- dispatcher 接线（50ms tumbling 合并窗口 + ConfigDiff→ConfigAdded/Removed 扇出）
- `event_kind(event) -> McpClientEventKind` 投影（grok 没有；dispatcher 需要时
  现场用 isinstance match）
- ACP `x.ai/mcp/server_status` push 序列化
- `ConfigDiff` 用 tuple 替换 list 以获得可哈希性（grok 用 Vec，忠实可变语义）

### 交付

**新增 `agent/minimax_code/mcp/events.py`（148 行）**
- `McpServerName: TypeAlias = str`
- 8 个 `@dataclass(frozen=True, slots=True)`：
  `TransportClosed{server, client_id:int}`、`HandshakeFailed{server, reason:str}`、
  `ToolsChanged{server}`、`ResourcesChanged{server}`、`Ready{server}`、
  `ConfigDiff{added:list[str], removed:list[str]}`、`ConfigAdded{server}`、
  `ConfigRemoved{server}`
- `McpClientEvent = TransportClosed | ... | ConfigRemoved`（8 路 PEP 604 联合）
- `server_name(event)` accessor（ConfigDiff → None）
- 模块 docstring 记录三事件来源（liveness watcher / client handler /
  session-config 层）+ 映射决策 + 不移植边界

**新增 `agent/tests/test_mcp_events.py`（13 函数，19 pytest items）**
1. `test_reexport`（包 vs 模块 is 同一性）
2. `test_mcp_server_name_is_str_alias`
3. `test_transport_closed_carries_client_id`（client_id int 类型）
4. `test_handshake_failed_carries_reason`
5. `test_config_diff_carries_lists`（list 字段）
6. `test_single_server_variants_construct`（5 个单 server 变体）
7. `test_server_name_returns_server_for_payload_variants`（7 param）
8. `test_server_name_returns_none_for_config_diff`
9. `test_union_has_eight_frozen_variants`（8 个 dataclass）
10. `test_frozen_immutable`（FrozenInstanceError on 写）
11. `test_value_equality`（== / != 值相等）
12. `test_isinstance_dispatch`（match 等价）
13. `test_every_variant_is_part_of_union`（8 样本 isinstance 联合）

**修改 `agent/minimax_code/mcp/__init__.py`**
- 新增 `from .events import (...)`（11 符号，按字母序插在 .client 与 .liveness 之间）
- docstring scope 加 R36 `.events` 行（紧跟 R35 `.liveness`）
- `__all__` 加 `# events (R36)` 块（11 符号）

### 契约

- IPC 协议：**无变更**（纯内部类型层，不触 JSON-RPC）
- 公开 API：mcp 包 `__all__` 新增 11 个符号（McpServerName + 8 变体 +
  McpClientEvent 联合 + server_name 函数）
- 跨文档：无（IPC/存储契约未动）

### 映射决策（R32 策略重申）

grok 的 `McpClientEvent` 是 **带 struct-variant payload 的枚举**，derive 只有
`Debug + Clone`（无 `PartialEq`/`Eq`/`Hash`）。按 R32 确立的政策：带 payload
的 Rust 枚举 → **frozen=True, slots=True dataclass 联合**（不是 R35 的
`enum.Enum`）。payload 决定映射：

- 单元枚举（无 payload，`Copy + Hash`）→ `@unique enum.Enum`（R35）
- struct-variant 枚举（带 payload）→ frozen dataclass 联合（R32/R36）

这是有意识的策略分歧——payload 决定映射，不是不一致。`frozen=True, slots=True`
镜像 `Debug + Clone`（不可变实例、紧凑布局）；Python dataclass 默认 `eq=True`
给了 grok 没有的值相等，但这是 Python 惯例且 dispatcher 去重需要（可接受偏离）。

`ConfigDiff` 的 `list[str]` 字段使实例不可哈希（调用 `__hash__` 会 TypeError），
但 grok 整体无 `Hash` derive——忠实语义。测试不哈希 ConfigDiff。

### 验证

- `ruff check minimax_code/mcp/events.py __init__.py tests/test_mcp_events.py`
  → **All checks passed!**（一次通过，零 I001——import 已手动排好序）
- `pytest tests/test_mcp_events.py + test_mcp_liveness.py + test_mcp_wire.py +
  test_mcp_oauth_config.py -q` → **50 passed**（R34+R35+R36 mcp 层全绿）
- 完整套件 `pytest` → **1472 passed in 90s**（R35 1453 → R36 1472，+19 精确，
  零回归）

### YAGNI 边界

- ❌ **不做 dispatcher 接线**——50ms tumbling 合并窗口 + ConfigDiff 扇出 +
  (server, kind) coalescing，是主机运行时接线轮。
- ❌ **不做 `event_kind()` 投影**——grok 没有；dispatcher 需要时用 isinstance match。
- ❌ **不做 ACP `server_status` push 序列化**——wire 形式未定，留接线。
- ❌ **不为 ConfigDiff 可哈希改用 tuple**——grok 用 Vec（可变），忠实语义；
  事件消费靠迭代不靠哈希。
- ❌ **不做 PartialEq 的精确镜像**——Python frozen dataclass 默认 eq=True 给值相等，
  是可接受的偏离（dispatcher 去重受益）。

### Commit

`feat(platform): R36 MCP client event layer (fuse grok xai-grok-mcp)`

---

## R37 — markdown 渲染保真度分析层（融合 `xai-grok-markdown-core`）

锚定提交：`a3b7601`（R36）

### 本轮目标

从 grok 引入一个 D 阶段交互主题的**纯逻辑切片**（Computer Use / markdown
渲染 / pager 分页 / mermaid 可视化之一），移植其 host-agnostic 核心，**不引入
主机运行时**。本轮选择 `xai-grok-markdown-core`——grok 用它审计模型 markdown
输出的渲染保真度。目标是把"渲染"故事补成"渲染 + 审计"：前端在 React 里渲染
markdown，后端能审计它是否静默降级。

### 融合结论

- ✅ **`MarkdownStats`**（21 个元素计数器：h1–h6、tables、fenced/indented/inline
  code、strong/emphasis/strikethrough、links/images、blockquotes、thematic_breaks、
  inline/display_math、task_list_items/list_items）+ 派生 `headings()` 总和 +
  `as_pairs()` 22 条固定顺序投影（序列化的单一事实来源）。
- ✅ **`StructuralIssue`** 唯一枚举（2 变体：`MALFORMED_TABLE`、
  `UNTERMINATED_CODE_BLOCK`）+ `as_str()`（snake_case 稳定日志键）。
- ✅ **`MarkdownAnalysis`** 容器（`(stats, issues)`，`Default` 给空 stats + 无 issues）。
- ✅ **4 个纯源码谓词**（`detect.py`，零 markdown 库依赖）：
  `strip_block_prefix`、`is_table_delimiter_line`、`line_looks_like_header`、
  `fenced_block_is_unterminated`。
- ❌ **放弃 `analyze()` 主循环**——遍历 `pulldown-cmark` 事件流填充计数器，是
  Rust 解析器绑定，留到选 Python markdown 解析器的整合轮。
- ❌ **放弃 `detect_malformed_tables`**——需要解析器产出的 `parsed_spans`（真实
  table/code block 的字节范围）来排除"合法 table 内的 `|---|`"，是解析器接线。
- ❌ **放弃 `DoubleTildeOnlyStrike` 过滤器 + `parser_options`/`offset_events`**——
  依赖 pulldown 的 `Event` 类型和 GFM option 结构，纯 Rust 绑定。

### 映射决策（payload 决定映射树，第三次重申）

`xai-grok-markdown-core` 三种类型走三条已确立的政策路径：

- `MarkdownStats` / `MarkdownAnalysis`：`#[derive(Debug, Default, Clone, PartialEq,
  Eq)]` + `#[non_exhaustive]` → **普通（非 frozen）dataclass**（R33 策略：mutable
  聚合 + `Default` 语义 + `eq=True` 值相等）。`#[non_exhaustive]` 无 Python 等价物，
  docstring 注明字段集开放，`as_pairs` 是手动同步的单一事实来源。`u32` → `int`，
  `Vec<StructuralIssue>` → `list[StructuralIssue]`（`field(default_factory=list)`）。
- `StructuralIssue`：**单元枚举** `#[derive(Debug, Clone, Copy, PartialEq, Eq)]`
  （无 `Hash`）→ **`@unique enum.Enum`**（R35 策略：单例值语义）。Enum 天生可哈希
  是免费副能力，不违背 grok 无 `Hash` derive 的语义（grok 不需要哈希，Python 给了
  也不冲突）。
- 4 个谓词：`&str`/`&'static str` → `str`；`char` 集合判断 → Python `in`/`all`；
  `trim_start_matches(['>', ' ', '\t'])` → `lstrip("> \t")`（等价：剥除任意前导
  这些字符的 run）。

三条路径再次印证：**payload 决定映射**——mutable 聚合 → 普通 dataclass；单例值 →
Enum。这是有意识的策略分歧，不是不一致。

### 产品融合

MiniMax Code 的前端在 React 中渲染 markdown，但**后端没有渲染保真度故事**。当
模型吐出一个分隔符列数与表头不匹配的表格，或一个缺闭合围栏的代码块（吞掉消息
剩余部分），用户看到的是静默降级输出（"画了表格但没显示" / "代码块吞了后半段"）。
本轮移植的类型 + `detect` 谓词是这个审计层的核心：未来接线轮在模型流完成后调用，
把这类失败提升为**结构化 issue**（`MALFORMED_TABLE` / `UNTERMINATED_CODE_BLOCK`），
而不是让它们隐形。谓词零依赖、可跑在任何后端甚至前端，是 host-agnostic 的纯逻辑。

### 验证

- `ruff check minimax_code/markdown/ tests/test_markdown_stats.py
  tests/test_markdown_detect.py` → **All checks passed!**（1 个 I001 自动修复 +
  1 个函数名连字符语法错手动修复后全绿）
- `pytest tests/test_markdown_stats.py tests/test_markdown_detect.py -q` →
  **50 passed in 0.11s**（stats 13 函数 + detect 4 个参数化展开 + fenced 边界
  用例全绿）
- 完整套件 `pytest` → **1522 passed in 89s**（R36 1472 → R37 1522，**+50 精确**，
  零回归）

### YAGNI 边界

- ❌ **不接 Python markdown 解析器**——`analyze()` 循环 + `detect_malformed_tables`
  需要解析器的 `parsed_spans`（真实结构字节范围），是整合轮的接线决策（选
  `markdown-it-py` 还是 `mistune`），本轮只移植不依赖解析器的核心。
- ❌ **不做 `DoubleTildeOnlyStrike` 过滤器**——它操作 pulldown 的 `Event` 流（把
  单 `~` 删除线重映射为字面文本），需要解析器事件类型，留整合轮。
- ❌ **不做 issue → 前端告警的 IPC 通道**——`agent.message_chunk` 事件尚未携带
  `issues` 字段，是 IPC 契约变更轮。
- ❌ **不为 `MarkdownStats` 加 frozen**——grok `Default + Clone` 语义需要可变构造
  （解析器逐字段累加），frozen 会强迫每步重建实例，违背使用模式。
- ❌ **不做 `headings` 字段化**——它是 `h1..=h6` 的派生值，grok 用方法（非字段），
  本轮 `headings()` 方法忠实镜像；`as_pairs` 首项用 `self.headings()` 派生。

### Commit

`feat(platform): R37 markdown analysis types + predicates (fuse grok xai-grok-markdown-core)`

## R38 — mermaid 渲染守卫与类型层（融合 grok `xai-grok-mermaid`）

锚定 R37 提交 `f5ff916`。

### 本轮目标

从 grok 的 `xai-grok-mermaid` 引入 mermaid 渲染的**类型 + 限制 + 错误守卫层**——这是
host-agnostic 核心，为未来 Python mermaid 渲染接线程（`mmdc` CLI / `mermaid.js` over
headless browser）奠基。**不引入渲染引擎本身**（dagre 布局 + SVG 光栅化是 Rust 渲染栈，
推迟到接线轮）。本轮交付的是词汇表 + 守卫契约：任何未来引擎实现都被 `render_checked`
不变地包裹。

### 融合结论

✅ **类型层（lib.rs）**：`Rgba`（frozen dataclass，`r/g/b/a: u8`）、`MermaidTheme`
（`@unique Enum`，LIGHT/DARK，`#[default] Light` → `DEFAULT_THEME`）、`RenderParams`
（frozen dataclass，theme/target_width_px/max_height_px/scale/min_width_px/background +
`for_os_viewer` 工厂类方法）、`RenderedDiagram`（frozen dataclass，`png: bytes` +
width/height）。`LIGHT_SURFACE`/`DARK_SURFACE` 单一事实来源常量。`surface_background()`
方法 + `to_hex()` → 不透明 `#RRGGBB`（alpha 忽略，镜像 grok）。

✅ **错误分类法（engine.rs `MermaidError` thiserror）**：6 个 Rust 变体 → Exception
层次结构（R32 策略）：`MermaidError` 基类 + `MermaidParseError`/`MermaidLayoutError`/
`MermaidRasterizeError`/`MermaidTimeoutError`/`MermaidUnsupportedError`/`MermaidPanicError`。
`Variant(String)` 载荷 → `_MessageError` 基类持 `message` 属性 + `__str__` 插值
（`f"mermaid {kind} error: {msg}"`）；`Timeout` 无载荷，固定 `__str__`。每个子类
`_KIND` 字段对应 grok `Display` 的区分词。

✅ **守卫层（engine.rs `RenderLimits` + `trait MermaidEngine` + `render_checked`）**：
`RenderLimits`（frozen dataclass，`max_source_bytes: int = 64*1024`，grok 64 KiB 上限）；
`MermaidEngine`（`@runtime_checkable Protocol`，镜像 grok `trait: Send + Sync`，GIL 下
Send/Sync 无意义，duck-typed 契约 + 测试用 isinstance 分发）；`render_checked`：
**字节长度**检查（`len(source.encode("utf-8"))` 镜像 Rust `str::len()` 字节语义，非
字符数）→ 超限 raise `MermaidUnsupportedError`（**引擎不运行**）→ 否则
`catch_unwind` 映射：`except MermaidError: raise`（"预期"通道透传）+ `except Exception
as exc: raise MermaidPanicError(str(exc)) from exc`（"恐慌"通道隔离）；`BaseException`
子类（KeyboardInterrupt/SystemExit）**故意不捕获**（镜像 `catch_unwind` 不拦截 `abort`）。

❌ **渲染引擎本身**——grok `PureRustEngine`/`rasterize`/`mmdc`/subprocess 引擎是 Rust
渲染栈（vendored `mermaid-to-svg` dagre 布局 + `resvg`/`usvg`/`tiny-skia` 光栅化），
推迟到接线轮选 Python 实现。

❌ **壁钟超时**——grok 在 pager 子进程外强制超时（每图 spawn 短命子进程），是接线关注点，
不在本守卫层。

❌ **输出像素面积/高度上限**——在光栅化器内部（未移植）。

### 交付

- `agent/minimax_code/mermaid/types.py`（新）— 4 frozen dataclass + 1 Enum + 3 常量 +
  `for_os_viewer` 工厂，`__all__` 7 符号。
- `agent/minimax_code/mermaid/errors.py`（新）— 7 类异常层次结构，`__all__` 7 符号。
- `agent/minimax_code/mermaid/engine.py`（新）— `RenderLimits` + `MermaidEngine` Protocol
  + `render_checked`，`__all__` 3 符号。
- `agent/minimax_code/mermaid/__init__.py`（新）— 重导出 17 符号（engine 3 + errors 7 +
  types 7），分组 `__all__`，docstring 声明范围与未移植边界。
- `agent/tests/test_mermaid_types.py`（新）— 15 测试：重导出、`to_hex` 不透明 #RRGGBB
  （含 alpha 忽略 + DARK_SURFACE→`#18181B`）、值相等、frozen（`FrozenInstanceError`）、
  可哈希、主题 surface 亮/暗差异、surfaces 匹配常量、两变体、默认 light、默认参数
  target-width-driven、`for_os_viewer` 工厂、参数值相等/frozen、`RenderedDiagram` 值
  相等/可哈希。
- `agent/tests/test_mermaid_engine.py`（新）— 16 测试：重导出、`RenderLimits` 默认 64KiB/
  值相等/frozen、Protocol runtime-checkable、成功透传、超限拒收（引擎不运行）、
  错误消息报字节数、限值处接受、**字节长度 vs 字符数**（`"ä"`/`"äb"`/`"äbc"` UTF-8
  字节验证）、恐慌→PanicError、`__cause__` 链保留、`BaseException` 不捕获
  （KeyboardInterrupt）、5 个预期错误透传、全子类是 `MermaidError`、`Display` 描述性。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

映射决策树第四次重申（payload 决定映射）：**struct-variant 载荷枚举 → frozen dataclass
联合类型**；**单元枚举 `Debug+Clone+Copy+PartialEq+Eq` → `@unique Enum`**；
**thiserror 枚举 → Exception 层次结构**。frozen=True+slots=True = Rust
`Debug+Clone+Copy+PartialEq+Eq`（不可变 + 值相等 + 紧凑 + 可哈希）。`@runtime_checkable
Protocol` = Rust `&dyn Trait` 动态分发。

### 验证

- `ruff check minimax_code/mermaid/ tests/test_mermaid_types.py
  tests/test_mermaid_engine.py` → **All checks passed!**（4 个 I001 自动修复 + 3 个
  B017 手动修复——`pytest.raises(Exception)` → `pytest.raises(FrozenInstanceError)`
  具体异常类型，禁止盲目异常）。
- `pytest tests/test_mermaid_types.py tests/test_mermaid_engine.py -q` →
  **31 passed in 0.20s**（types 15 + engine 16，全绿）。
- 完整套件 `pytest -q` → **1553 passed in 89.13s**（R37 1522 → R38 1553，
  **+31 精确**，零回归）。

### YAGNI 边界

- ❌ **不接渲染引擎**——dagre 布局 + SVG 光栅化是 Rust 栈；选 `mmdc` CLI subprocess
  还是 `mermaid.js` over headless browser 是接线轮决策，本轮只定 `MermaidEngine`
  Protocol 契约。
- ❌ **不做壁钟超时守卫**——grok 在 pager 子进程外强制（每图短命子进程），属接线层，
  不在本进程内守卫。
- ❌ **不做输出像素面积上限**——`max_height_px`/pixmap area 在光栅化器内部强制，
  光栅化器未移植。
- ❌ **不做 render worker → IPC 事件接线**——`agent.message_chunk` 尚未携带渲染结果，
  是 IPC 契约变更轮。
- ❌ **不为 `RenderParams` 加可变性**——grok 字段默认 + 构造模式需要 frozen 值类型
  （`for_os_viewer` 工厂返回新实例），frozen 镜像 `Copy` 语义；mutable 会破坏
  "参数即值"契约。

### Commit

`feat(platform): R38 mermaid render guard + types (fuse grok xai-grok-mermaid)`

## R39 — hunk 追踪 diff 计算原语（融合 grok `xai-hunk-tracker`）

锚定 R38 提交 `91230de`。

### 本轮目标

从 grok 的 `xai-hunk-tracker` 引入 **diff patch 生成的纯计算原语 + 资源守卫**
（`generate_unified_patch` / `generate_hunk_patch` / `compute_hunks` +
`MAX_DIFF_FILE_SIZE` / `DIFF_TIMEOUT` 守卫）——这是 hunk 追踪的计算核心，
host-agnostic，依赖仅 `similar`（→ Python `difflib` 标准库，零新依赖）。**不引入
actor 层**（tokio async mpsc channel）和 **git 集成层**（gix status/index）——那两
层是 Rust 平台栈，推迟到未来接线轮选 asyncio actor + Python git 绑定。

### 融合结论

✅ **类型层（types.rs）**：`HunkId`（frozen dataclass，包装 `value: str`，
`new()`/`from_string()`/`as_str()` 类方法，`Arc<str>` → 不可变 `str`）、
`HunkLineInfo`（frozen dataclass，`old_start/old_count/new_start/new_count`，`__str__`
emit 统一 diff 头 `@@ -{os},{oc} +{ns},{nc} @@`）、`Hunk`（9 字段 frozen dataclass：
`id/path/line_info/source/old_text/new_text/patch/created_at/selected`）+
`file_created()` 工厂（`old_*` 归零，`new_start=1`，`new_count=max(lines, 1)`）。
`DateTime<Utc>` → tz-aware `datetime.datetime`；`Uuid::new_v4` → `uuid.uuid4()`。

✅ **混合枚举 HunkSource → frozen-dataclass 联合**（决策树新应用）：grok `HunkSource`
是**混合枚举**——1 个 struct 变体 `AgentEdit{prompt_index}` + 2 个单元变体
`ExternalEditOnAgentFile` / `External`。不同于 R35/R37/R38 的纯单元枚举（→ `Enum`），
混合枚举映射为**全部 frozen dataclass 形成单一 PEP 604 联合类型**
（`HunkSource = AgentEdit | ExternalEditOnAgentFile | External`）；单元变体成为无字段
frozen dataclass，保持值相等 + 可哈希（镜像 Rust 单元变体 `PartialEq + Eq`）。grok
`match` 穷尽分发 → Python `isinstance` 分发。这是 R32（VoiceEvent）混合枚举策略的
第二次应用。

✅ **diff 引擎（diff.rs → difflib）**：`similar::TextDiff::configure().timeout().diff_lines()`
+ `iter_all_changes()` over `ChangeTag::Equal/Delete/Insert` →
`difflib.SequenceMatcher(autojunk=False).get_opcodes()`（equal/delete/insert/replace）。
**`autojunk=False` 是强制的**——默认 `True` 把频繁行当"垃圾"静默改变大文件差异，
`similar` 无此启发式，必须 opt-out。**Replace = delete+insert 对合并进同一 hunk**
（镜像 grok 单 `HunkBuilder` 累加器跨 Equal 界限的行为）。**行号证明**：opcode
`i1`/`j1` 是 0 索引，grok `old_start`/`new_start` 是 1 索引 → `old_start = i1+1`、
`new_start = j1+1`；已证明等价于 grok 光标跟踪（到 opcode `(i1,i2,j1,j2)` 时，前序
opcode 恰消耗 `i1` 旧行 / `j1` 新行，故光标正是 `i1+1`/`j1+1`）。

✅ **Splitlines 双策略**：`compute_hunks` 用 `splitlines(keepends=True)`（`\n` 保留进
`old_text`/`new_text`，镜像 grok `change.value()` 带 `\n`）；渲染函数
（`format_unified_diff`/`generate_hunk_patch`）用普通 `splitlines()`（镜像 grok
`str::lines()` 去 `\n` 用于显示）。这精确镜像 grok 内部（similar value 带 `\n`）vs
显示（`.lines()` 去 `\n`）的区分。

✅ **守卫层**：`MAX_DIFF_FILE_SIZE = 1 MiB`（字节长度检查 `len(s.encode("utf-8"))`
镜像 Rust `str::len()` 字节语义，非字符数）超限短路返回空列表（grok warn + `vec![]`）。
`generate_unified_patch` 用 `difflib.unified_diff(n=CONTEXT_LINES=3, lineterm="")` +
`"\n".join() + "\n"`。`patch_lines` 行级补丁通过 `content.endswith("\n")` 保留尾换行。
`hunks_overlap` 含纯插入特例（两插入仅同位置重叠；单插入落入 `[start,end]` 重叠）。

❌ **actor 层（`HunkTrackerActor` over tokio mpsc）**——Rust async 平台栈；未来接线
轮选 asyncio actor（`asyncio.Queue` + 任务）或同步 tracker。

❌ **git 集成层（`gix` status/index）**——Rust git 栈；未来接线轮选 Python git 绑定
（`pygit2` / `GitPython`）或 shell out 到 `git`。

❌ **`file_deleted` 工厂**——grok 字段语义需接线轮确认（删除是否生成空 new_text 的
特殊 hunk），YAGNI 推迟。

❌ **DIFF_TIMEOUT 壁钟强制**——`difflib` 同步无超时参数，常量文档化但不强制；调用者
（async 接线轮）用 `asyncio.wait_for(compute_hunks(...), DIFF_TIMEOUT)` 包裹。同 R38
壁钟超时推迟到接线层的形状。

### 交付

- `agent/minimax_code/hunks/types.py`（新）— `HunkId`/`HunkLineInfo`/`AgentEdit`/
  `ExternalEditOnAgentFile`/`External`/`Hunk`（6 frozen dataclass）+ `HunkSource` 联合
  + `file_created` 工厂，`__all__` 7 符号。
- `agent/minimax_code/hunks/diff.py`（新）— 3 常量（`CONTEXT_LINES`/`MAX_DIFF_FILE_SIZE`
  /`DIFF_TIMEOUT`）+ 12 纯函数（`generate_unified_patch`/`compute_hunks`/
  `_HunkBuilder`/`generate_hunk_patch`/`format_unified_diff`/`patch_lines`/
  `hunks_match_content`/`hunk_moved`/`hunks_overlap`/`calculate_overlap_size`/
  `find_matching_old_hunk`/`find_overlapping_hunks`），`__all__` 14 符号。
- `agent/minimax_code/hunks/__init__.py`（新）— 重导出 21 符号（types 7 + diff 14），
  分组 `__all__`，docstring 声明范围与未移植边界（actor/git 是接线轮关注点）。
- `agent/tests/test_hunks_types.py`（新）— 18 测试：重导出、`HunkId` 唯一性/round-trip/
  值相等/frozen（`FrozenInstanceError`）/可哈希、`HunkLineInfo` `__str__` 头/值相等/
  frozen、`AgentEdit` prompt_index、单元变体值相等、联合 `isinstance` 分发、单元变体
  可哈希、`Hunk` 默认/值相等/frozen、`file_created` 行数标记/空内容 1 行。
- `agent/tests/test_hunks_diff.py`（新）— 31 测试：无变更空、超限守卫、**字节长度 vs
  字符数**（`"ä"` 多字节验证）、`single_line_modification`/`insertion`/`deletion`/
  `multiple_hunks`（镜像 grok）、source 归因、**`autojunk=False` 重复行干净 diff**、
  replace 单 hunk、`generate_unified_patch` None/头/超限、`format_unified_diff`（镜像
  grok）、`generate_hunk_patch` 头+变更+上下文、`patch_lines` 替换/插入/删除/尾换行/
  无尾换行、`hunks_match_content`/`hunk_moved`/`hunks_overlap`（常规/不相交/异路径/
  两插入同位/两插入异位）、`find_matching_old_hunk`（相同内容不同位置/最佳重叠回退/
  无匹配 None）、`find_overlapping_hunks`。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

映射决策树第五次重申（payload 决定映射）：**混合枚举（部分 struct + 部分单元）→ 全部
frozen dataclass 形成联合类型**（单元变体 = 无字段 frozen dataclass）——区别于纯单元
枚举（→ `Enum`）。`similar` crate（Myers diff）→ Python `difflib` 标准库（零新依赖）。
frozen=True+slots=True = Rust `Debug+Clone+Copy+PartialEq+Eq`。

**质量记录（透明披露）**：`diff.py` 的 `compute_hunks` 首次写入时混入了离奇的占位符
代码（虚构的 `_i2_eq`/`_j2_eq` 辅助函数 + 形如 `old_lines[i1 : i1 + (i1.__class__(0)
or 0)]` 的损坏切片），在交付测试前自捕并单次 Edit 修复为正确的 opcode 循环
（`for tag, i1, i2, j1, j2 in matcher.get_opcodes()` + 干净切片 `old_lines[i1:i2]` /
`new_lines[j1:j2]`）。无占位符代码进入仓库；修复发生在验证流水线运行前。教训：大块
代码写入后必须立即回读核对，不能依赖"写完即对"。

### 验证

- `ruff check minimax_code/hunks/ tests/test_hunks_types.py tests/test_hunks_diff.py`
  → **All checks passed!**（3 个 I001 导入排序自动修复，0 remaining）。
- `pytest tests/test_hunks_types.py tests/test_hunks_diff.py -v` →
  **49 passed in 0.27s**（types 18 + diff 31，全绿，含 grok 8 个镜像用例 + Python 特有
  autojunk/字节长度/frozen/联合分发断言）。
- 完整套件 `pytest` → **1602 passed in 101.92s**（R38 1553 → R39 1602，**+49 精确**，
  零回归）。

### YAGNI 边界

- ❌ **不接 actor 层**——`HunkTrackerActor` over tokio mpsc 是 Rust async 平台栈；选
  asyncio actor（`asyncio.Queue`）还是同步 tracker 是接线轮决策，本轮纯函数被任一实现
  不变调用。
- ❌ **不接 git 集成**——`gix` status/index 是 Rust git 栈；选 `pygit2` / `GitPython` /
  shell out `git` 是接线轮决策。
- ❌ **不做 `file_deleted` 工厂**——grok 删除语义（是否空 new_text 特殊 hunk）需接线轮
  确认，本轮只移植 `file_created`。
- ❌ **不强制 DIFF_TIMEOUT 壁钟**——`difflib` 同步无超时参数；接线轮用
  `asyncio.wait_for` 包裹。常量文档化接线轮须遵守的预算（同 R38 壁钟推迟形状）。
- ❌ **不为 `Hunk` 加可变性**——grok accept/reject 生命周期不在 `Hunk` 字段（在 tracker），
  frozen 值类型 + 替换/丢弃建模生命周期（非原地突变），frozen 镜像 `Clone` 语义。

### Commit

`feat(platform): R39 hunk diff compute primitives + types (fuse grok xai-hunk-tracker)`

---

## R40 — prompt 队列 wire 类型契约（融合 grok `xai-prompt-queue`）（阶段 D 收官）

锚定 R39 提交 `29f3fd3`。

### 本轮目标

从 grok 的 `xai-prompt-queue` 引入 **prompt 队列的 wire 类型契约**——
`QueueEntryMeta`（actor 内部 frozen 值类型）+ `QueueEntryWire` / `QueueChanged`
（JSON-RPC 广播载荷，pydantic v2 BaseModel）。这是 **R 系列首次将 wire-types crate
映射到 pydantic**（此前 frozen-dataclass 移植均针对非序列化值类型）——映射决策树的新
分支应用：**(de)serialization 面 → pydantic；纯值相等 → frozen dataclass**。host-agnostic
纯数据契约，依赖仅 serde（→ 项目既有 pydantic v2 栈，零新依赖）。**不引入 session actor**
（tokio）和 **shell/pager 消费端**——那是接线轮。

### 融合结论

✅ **`QueueEntryMeta`（无 Serialize）→ frozen=True, slots=True dataclass**：grok
`Clone + Debug + PartialEq + Eq`（无 `Serialize` / `Deserialize`）——actor 内部状态，从不上线。
6 字段 `id/version/owner/last_editor/kind/text`；`version: u64` 无默认（必填），
`owner/last_editor: Option<String>`。这是 R32 起所有非序列化值类型的同一映射。

✅ **`QueueEntryWire` + `QueueChanged`（Serialize+Deserialize+camelCase）→ pydantic v2 BaseModel**：
决策树的**新分支**——之前 frozen-dataclass 移植（R32 `VoiceEvent` / R35 R37 R38 单元枚举 /
R39 `HunkSource`）全是非序列化值类型；本轮是**首个 wire-types crate**，承载
(de)serialization 契约，故映射 pydantic（项目既有 IPC 数据模型栈）。pydantic 一一对应
serde 行为：

  - `#[serde(rename_all = "camelCase")]` → `alias_generator=to_camel` + `populate_by_name=True`
    （Python 字段 snake_case，wire 用 `lastEditor` / `runningPromptId` / `sessionId` 别名）。
  - `#[serde(default)]` → pydantic 字段默认值（`version=0` / `kind=""` / `text=""` /
    `position=0` / `entries=[]`）。
  - `#[serde(default, skip_serializing_if = "Option::is_none")]` → `Optional` 字段默认 `None`
    + 序列化 `exclude_none=True`（`to_wire_json()` 封装
    `model_dump_json(by_alias=True, exclude_none=True)`）。
  - 未知 JSON 字段忽略（`extra="ignore"`，pydantic 默认）。
  - `frozen=True` → wire 值不可变（grok 编辑 = 构造新实例，非原地突变）。

✅ **`QueueChanged::default()`（Rust `#[derive(Default)]`）→ `default()` 类方法**：程序构造
`session_id=""`（空会话 / 无条目 / 无运行）。**关键非冲突**：`default()` 程序构造
`session_id=""`，而反序列化仍**要求** `sessionId` 存在（字段无默认）——前者是构造器，后者
是解析要求，两者不矛盾，精确镜像 grok（serde "required" 字段 vs `Default` derive）。

❌ **session actor（tokio mpsc）**——Rust async 平台栈；接线轮选 asyncio actor。

❌ **shell/pager 消费端**——grok 把这两个 wire 消费者放在 host 平台栈；接线轮接前端队列面板
+ agent 对话循环。

❌ **meta→wire 投影辅助**——grok 把 `QueueEntryMeta` → `QueueEntryWire` 转换放在 actor 内；
本轮只移植纯类型契约，不添加新函数（严格 YAGNI，保持忠实）。

### 交付

- `agent/minimax_code/prompt_queue/types.py`（新）— `QueueEntryMeta`（frozen dataclass，
  6 字段）+ `QueueEntryWire` / `QueueChanged`（pydantic v2 BaseModel，camelCase alias +
  默认值 + `to_wire_json()` exclude_none + `QueueChanged.default()` 类方法），`__all__` 3 符号。
- `agent/minimax_code/prompt_queue/__init__.py`（新）— 重导出 3 符号，docstring 声明范围
  （actor / 消费者是接线轮关注点）。
- `agent/tests/test_prompt_queue.py`（新）— 13 测试：grok 6 镜像（round-trip / golden wire
  JSON / requires session_id / sparse defaults / extra fields ignored / derives default）
  + Python 特有（wire None 排除 / snake_case 构造 / 必填 id 校验 / camelCase 别名解析 /
  Meta 值相等 / frozen / 可哈希）。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

映射决策树**第六次重申**（payload 决定映射）：**(de)serialization 面（Serialize+Deserialize+
serde 属性）→ pydantic v2 BaseModel**（本轮新分支，首次用于真实 IPC wire 契约；R34
MCP-over-ACP 常量是 dataclass 未用 pydantic 序列化面）；**纯值相等无序列化
（Clone+PartialEq+Eq）→ frozen=True, slots=True dataclass**；**纯单元枚举 → `Enum`**；
**混合枚举 → 全 frozen dataclass 联合**。一条规则、四个分支，payload 决定走哪条。

### 验证

- `ruff check --fix` → All checks passed!（0 自动修复，I001 排序已干净）。
- `ruff check` → **All checks passed!**
- `pytest tests/test_prompt_queue.py -q` → **13 passed in 0.11s**（grok 6 镜像 + 7 Python 特有）。
- 完整套件 `pytest` → **1615 passed in 108.00s**（R39 1602 → R40 1615，**+13 精确**，零回归）。

### YAGNI 边界

- ❌ **不接 session actor**——grok `QueueActor` over tokio mpsc 是 Rust async 平台栈；选 asyncio
  actor（`asyncio.Queue` + 任务）还是同步队列是接线轮决策，本轮类型契约被任一实现不变消费。
- ❌ **不接 shell/pager 消费端**——grok 把广播消费者放在 host 平台栈；接线轮接前端队列面板
  + agent 对话循环的 prompt 排空逻辑。
- ❌ **不加 meta→wire 投影函数**——grok 把 `QueueEntryMeta` → `QueueEntryWire` 转换放在 actor
  内（含 position=index 投影逻辑）；本轮只移植纯类型，接线轮在 actor 边界做投影。
- ❌ **不为 wire 模型加可变性**——`frozen=True` 镜像 grok 值语义；编辑（version bump）=
  `model_copy(update={...})` 构造新实例，非原地突变。
- ❌ **不接 `x.ai/queue/changed` 通知广播**——这是 transport 层订阅模型，接线轮在 WS server
  接 fan-out 路由（`session_id` 驱动）。

### Commit

`feat(platform): R40 prompt queue wire types (fuse grok xai-prompt-queue)` (`5de390a`)

---

## R41 — 跨平台系统睡眠/唤醒通知抽象（融合 grok `xai-system-power`）（阶段 E 首轮）

### 本轮目标

从 grok 的 `xai-system-power`（743 行：lib.rs 180 + windows.rs 103 + linux.rs 93 + macos.rs 367）引入**跨平台系统睡眠/唤醒（挂起/恢复）通知抽象层**：`PowerEvent`（`WillSleep`/`DidWake` 单元枚举）+ `PowerState`（`FullWake`/`DarkWake`/`Unknown` 单元枚举）+ `PowerCallback` 类型别名 + `SystemPowerListener`（RAII，`start → Optional`，`Drop` 清理）+ `current_power_state()` 同步查询。

**阶段 E 平台化首轮**。产品价值：MiniMax Code 的 agent 跑长生命周期循环——LLM 流式、APScheduler cron、secret 刷新、多轮工具；OS 挂起会令 asyncio 定时器停转、在途网络响应丢失（grok 的原始动机：OIDC refresh-token 轮换响应在挂起边界丢失，客户端持有死 token，用户被迫重登）。`WillSleep` 让 sleep-gate 推迟*发起*不可逆操作；`DidWake` 让它补偿（重取/重排）。本轮交付词汇表 + 通知原语，消费端（AuthManager-style sleep gate、scheduler 挂起感知 deferral）是接线轮。

本轮实施：host-agnostic 抽象 + Windows ctypes 实现（`PowerRegisterSuspendResumeNotification` + `DEVICE_NOTIFY_CALLBACK`）+ no-op fallback（其他平台 `start → None`）。镜像 grok 的 `#[cfg(target_os)]` 编译时平台拆分 + no-op 模块。

### 融合结论

**✅ 保持（映射到 Python）**：
- `PowerEvent`（2 单元变体，`Copy+PartialEq+Eq`）→ `@unique enum.Enum`（决策树第三分支，同 R35/R37/R38）。
- `PowerState`（3 单元变体，`Copy+PartialEq+Eq`）→ `@unique enum.Enum`。
- `PowerCallback`（`Box<dyn Fn(PowerEvent) + Send + Sync + 'static>`）→ `Callable[[PowerEvent], None]` 类型别名。
- `SystemPowerListener`（RAII + `start → Option<Self>` + `Drop` 释放 OS 资源）→ Python 类（`__slots__` 持 stop 闭包 + `start` classmethod → `Optional[Self]` + `close()` 幂等 + `__del__` best-effort）；平台 impl 返回 stop 闭包，`close` 调用之。
- 跨平台 `#[cfg(target_os)]` **编译时**平台分发 → Python `sys.platform` **运行时**分发 + 平台模块隔离（`_windows` 仅 win32 导入——`ctypes.wintypes` 在非 win32 不可导入，天然隔离）。
- Windows FFI（`PowerRegisterSuspendResumeNotification` + `DEVICE_NOTIFY_CALLBACK` 回调 + `PowerUnregisterSuspendResumeNotification`）→ ctypes（`WINFUNCTYPE` 回调签名 + `_DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS` Structure + 回调对象/结构体由 stop 闭包捕获 pin，`stop` 调用后置 `None` 释放）。
- `current_power_state()` Windows 返回 `Unknown`（grok windows.rs 奇偶）→ 保持。

**❌ 放弃（YAGNI，环境扩展轮）**：
- macOS IOKit 端口（grok `macos.rs` 367 行，需 pyobjc）。
- Linux logind D-Bus 端口（grok `linux.rs` 93 行，需 dbus）。
- macOS dark-wake 真实查询（`current_power_state` 的 `FullWake`/`DarkWake` 区分）——依赖 macOS 端口，本轮全平台返回 `Unknown`（grok Windows/Linux 已如此）。

### 交付

- `agent/minimax_code/system_power/types.py`（新）— `PowerEvent`（2 变体）+ `PowerState`（3 变体）`@unique Enum` + `PowerCallback` 别名 + 映射/产品文档，`__all__` 3 符号。
- `agent/minimax_code/system_power/_windows.py`（新）— ctypes 实现：`_DEVICE_NOTIFY_CALLBACK_T`（`WINFUNCTYPE(ULONG, LPVOID, ULONG, LPVOID)`）+ `_DEVICE_NOTIFY_SUBSCRIBE_PARAMETERS` Structure + `start()` 注册（pin cb/params 于 stop 闭包，`PBT_APMSUSPEND`→WillSleep / `PBT_APMRESUMEAUTOMATIC`+`PBT_APMRESUMESUSPEND`→DidWake，幂等双 fire 不去重）+ `current_power_state()` 返回 `Unknown`。仅 win32 导入。
- `agent/minimax_code/system_power/_fallback.py`（新）— no-op：`start` 返回 `None`、`current_power_state` 返回 `Unknown`。
- `agent/minimax_code/system_power/listener.py`（新）— `SystemPowerListener` RAII 类（`__slots__=("_stop",)` + `start` classmethod → `Optional` + `close()` 幂等 + `__del__` best-effort）+ `current_power_state()` 分发；`sys.platform` 选 `_impl`。
- `agent/minimax_code/system_power/__init__.py`（新）— 重导出 5 符号 + 范围 docstring（含"不在这里"声明：macOS/Linux 端口推迟）。
- `agent/tests/test_system_power.py`（新）— 11 测试：grok 镜像（`power_event_copy_eq`、`start_and_drop_is_clean`）+ Python 特有（PowerEvent/PowerState members、PowerState distinct、PowerCallback 别名、`current_power_state` 返回 PowerState、Unknown everywhere、fallback start→None、fallback Unknown、listener close 幂等、win32_only 真实注册清理）。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

映射决策树**第七次重申**（payload 决定映射）：本轮两个**纯单元枚举**（`Copy+PartialEq+Eq` 无 payload）→ `@unique Enum`（第三分支）。**新增首次遇到平台 FFI crate**——Rust `#[cfg(target_os)]` 编译时平台分发 → Python `sys.platform` 运行时分发 + 平台模块隔离；Rust `Box<dyn Fn + Send + Sync + 'static>` callback → `Callable` 别名（OS 线程调用）；Rust `Box::into_raw` 堆 pin（OS 持原始指针）→ ctypes 回调对象 + Structure 由 stop 闭包捕获 pin（`nonlocal cb, params` 绑定入闭包单元格，`stop` 调用后置 `None` 释放，镜像 grok `Drop` 回收堆）。

### 验证

- `ruff check --fix` → Found 1 error (1 fixed, 0 remaining)（I001 导入排序 listener.py）。
- `ruff check` → **All checks passed!**
- `pytest tests/test_system_power.py -q` → **11 passed in 0.05s**（grok 2 镜像 + 9 Python 特有，win32_only 在本机真跑 ctypes 注册）。
- 完整套件 `pytest` → **1626 passed in 103.23s**（R40 1615 → R41 1626，**+11 精确**，零回归）。

### YAGNI 边界

- ❌ **不迁移 macOS IOKit 端口**——grok `macos.rs` 367 行，需 pyobjc 绑定；非当前开发平台，`_fallback` 给干净降级（`start→None`），macOS agent 不受影响。
- ❌ **不迁移 Linux logind D-Bus 端口**——grok `linux.rs` 93 行，需 dbus；同上。
- ❌ **不实现 macOS dark-wake 真实查询**——`current_power_state` 全平台返回 `Unknown`（grok Windows/Linux 已如此）；`FullWake`/`DarkWake` 区分推迟到 macOS 端口轮。
- ❌ **不接 AuthManager-style sleep gate**——本轮只交付词汇表 + 通知原语；消费端（gate lower/raise、唤醒补偿重取/重排）是接线轮，挂 secret 刷新到 `WillSleep`/`DidWake`。
- ❌ **不接 APScheduler suspend-aware 调度**——同上，挂 cron 的挂起感知 deferral 到接线轮。

### Commit

`feat(platform): R41 system power sleep/wake listener (fuse grok xai-system-power)` (`01bb00e`)

---

## R42 — 版本管理词汇表 + 零依赖 semver（融合 grok `xai-grok-version`）

### 本轮目标

从 grok 的 `xai-grok-version`（75 行：lib.rs + build.rs）引入**版本管理词汇表**：`VERSION` 编译时版本常量 + `TEST_VERSION_ENV` 测试覆盖钩子 + 零依赖 `Version` semver.org 解析（frozen dataclass）+ `display_version` / `display_version_with_commit` channel-label 格式化。

**阶段 E 平台化/发布主题**。产品价值：MiniMax Code 的 `/health` 已返回版本、前端已展示，但版本管理无统一词汇表——自更新检查（installed vs latest release）、CHANGELOG 展示、channel-aware UI 字符串（`"0.8.0 [stable]"` vs `"0.8.0 [alpha]"`）都各写各的。本模块是这些场景的叶子原语；update-check + channel-label 来源（`xai-grok-update` 消费端）是接线轮。

### 融合结论

**✅ 保持（映射到 Python）**：
- `VERSION`（编译时 `env!("CARGO_PKG_VERSION")` + `option_env!("GROK_VERSION")` 覆盖）→ 运行时 `importlib.metadata.version("minimax-code")`（单一真相源 = `pyproject.toml`）+ `_FALLBACK_VERSION` 硬编码（unpackaged 上下文，如裸 checkout）。Python 无编译时 `env!`，包元数据**即**唯一真相源。
- `TEST_VERSION_ENV`（grok `GROK_TEST_VERSION`）→ `MINIMAX_CODE_TEST_VERSION`，`installed()` **call 时**覆盖（测试钩子，模拟升级场景而无需改包元数据）。
- `semver::Version`（外部 crate 依赖）→ 零依赖 `Version` frozen dataclass（major/minor/patch + pre + build），regex 解析 semver.org。`packaging` 非 MiniMax Code 依赖，自带轻量解析器避免引入。
- `installed()` / `installed_semver()` / `display_version()` / `display_version_with_commit()` → 直接移植（纯函数/方法）。

**❌ 放弃（YAGNI）**：
- build.rs `rerun-if-env-changed=GROK_VERSION` 机制——Python 无构建步骤，`importlib.metadata` 运行时读取代之。
- `option_env!("GROK_VERSION")` 编译时 env 覆盖——Python 无对等，包元数据是真相源。

### 交付

- `agent/minimax_code/version.py`（新）— `VERSION` 常量（`_resolve_compiled_version()` importlib.metadata + fallback）+ `TEST_VERSION_ENV` + `Version` frozen dataclass（5 字段 + `parse()` classmethod regex + `__str__` 往返）+ `installed()`（TEST_VERSION_ENV 覆盖 + trim）+ `installed_semver()` + `display_version()` / `display_version_with_commit()`，`__all__` 7 符号。
- `agent/tests/test_version.py`（新）— 16 测试：grok 矩阵镜像（`display_version_with_commit_matrix` 4 case alpha/stable/empty + `display_version_appends_label`）+ Python 特有（installed env 覆盖/trim/fallback/nonempty + Version parse simple/pre/build/both/invalid/str-roundtrip/equality/frozen + installed_semver returns/invalid-raises）。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

映射决策树**第七次重申**（payload 决定映射）：本轮 `semver::Version`（结构体 + 值相等 + **无序列化面**）→ `frozen=True, slots=True` dataclass（第二分支，同 R40 `QueueEntryMeta`）。**新增首次遇到编译时常量 crate**——Rust `env!("CARGO_PKG_VERSION")` 编译时从 Cargo.toml 注入 → Python `importlib.metadata.version()` 运行时从 pyproject.toml 元数据读（无编译步骤，包元数据是唯一真相源，编辑 pyproject.toml 即更新）；Rust `option_env!` 编译时 env 覆盖无 Python 对等 → 移除（包元数据即真相源）；Rust `build.rs` `rerun-if-env-changed` 重编译触发器 → Python 无构建步骤，不适用；Rust `semver::Version`（外部 crate）→ 零依赖 frozen dataclass + regex（`packaging` 非 MiniMax Code 依赖，自带解析器避免引入依赖，保持平台 crate 零额外依赖原则）。

### 验证

- `ruff check --fix` → Found 2 errors (2 fixed, 0 remaining)（I001 `importlib.metadata` 拆分导入）。
- `ruff check` → **All checks passed!**
- `pytest tests/test_version.py -q` → **16 passed in 0.07s**（grok 矩阵镜像 + 14 Python 特有）。
- 完整套件 `pytest` → **1642 passed in 104.64s**（R41 1626 → R42 1642，**+16 精确**，零回归）。

### YAGNI 边界

- ❌ **不实现 pre-release 有序比较**（semver.org §11：`0.8.0-alpha < 0.8.0`）——无消费端（更新检查未接），`Version` 仅值相等；比较逻辑推迟到 update-check 接线轮。
- ❌ **不接 channel_label 来源**（grok `xai-grok-update::channel_label()`）——`display_version` 接受外部 label 字符串，来源是 update crate 接线轮。
- ❌ **不实现 update-check**（对比 GitHub release latest vs installed）——`installed_semver()` 提供比较原语，检查逻辑是 update crate 接线轮。
- ❌ **不接 http_server/前端版本展示**——本轮是词汇表层；接线轮把 `/health` 和前端版本号指向 `version.VERSION` / `display_version`。
- ❌ **不迁移 build.rs**——Python 无编译时 `env!` 机制，`importlib.metadata` 运行时读取代之。

### Commit

`feat(platform): R42 version vocabulary + zero-dep semver (fuse grok xai-grok-version)` (`19e27a3`)

---

## R43 — 后端环境预设 + EnvVarGuard（融合 grok `xai-grok-env`）

### 本轮目标

从 grok 的 `xai-grok-env`（197 行单文件 lib.rs）引入**后端环境预设词汇表**：`BuildEndpoints` frozen 端点束（5 字段）+ `BuildEnvironment` 单成员环境 enum + `resolve` env-var 覆盖机制 + `EnvVarGuard` RAII 测试 guard。

**阶段 E 平台化/发布主题**——与 R42 `version` 对称的"平台基础词汇表层"。产品价值：MiniMax Code 当前 `llm.py` 硬编码 API base URL、`http_server` 硬编码 CORS origin，无统一的"后端环境预设 + env 覆盖"层。本模块把 grok 的多服务 endpoint 预设架构迁移过来，`api_base_url` 是 MiniMax Code 当前活跃消费端（llm.py 接线轮），其余 endpoint 保留为 grok 云架构的扩展槽——**平台型产品保留可扩展结构**。

### 融合结论

**✅ 保持（映射到 Python）**：
- `GrokBuildEndpoints`（struct of `&'static str`，`Debug+Clone+Copy+PartialEq+Eq` 无 `Serialize`）→ `BuildEndpoints` frozen dataclass（第二分支，同 R40 `QueueEntryMeta` / R42 `Version`）；`&'static str` → `str`（不可变，无生命周期）。
- `GrokBuildEnvironment`（单变体 `Production` enum，`Copy+PartialEq+Eq` + `#[default]` + 方法）→ `BuildEnvironment` `@unique` 单成员 `enum.Enum`（第三分支，同 R41 `PowerState`）；`from_flags` 是 public-build no-op 恒返回 Production（Dev/Staging 编译出）。
- `unsafe { std::env::set_var / remove_var }`（Rust 2024 标记 `unsafe`，因 `std::env` 进程全局）→ `os.environ[key] = v` / `os.environ.pop`（Python 无 `unsafe` 标记，但进程全局风险相同 → `_ENV_LOCK` 串行化）。
- `EnvVarGuard`（`#[cfg(test)]` RAII + `Drop` 恢复快照 + `ENV_LOCK` 串行）→ `EnvVarGuard` context manager（`__enter__`/`__exit__` + `close`/`__del__`），模块级 `threading.Lock`。
- env-prefix `GROK_PRODUCTION` → `MINIMAX_PRODUCTION`（品牌化）；per-endpoint 后缀（`_CLI_CHAT_PROXY_BASE_URL` 等，操作接口）逐字保留。
- endpoint 值 `grok.com` 域 → `api.minimax.chat` 域（产品本地化）。
- `PROD_*` 顶层常量别名 + `Display` → `__str__` + `indicator` / `is_production` / `endpoints` / `resolve` / 5 个 per-endpoint 方法：全部直接移植。

**❌ 放弃（YAGNI）**：
- `url` / `tracing` crate 依赖（grok lib.rs 未实际使用）→ 零非标准库依赖（仅 `os`/`threading`/`dataclasses`/`enum`）。
- Dev/Staging 环境（grok public build 也只有 Production）→ 仅 Production 单成员。
- 真实 endpoint URL 校验（`url` crate 解析）→ URL 即 `str`，校验是消费端职责。
- 接线 `llm.py`/`http_server` 读取 endpoint → 本轮是词汇表层，消费端接线是后续轮。

### 交付

- `agent/minimax_code/env_presets.py`（新）— `BuildEndpoints` frozen dataclass（5 字段）+ `PRODUCTION_ENDPOINTS` 常量 + 5 个 `PROD_*` 别名 + `BuildEnvironment` `@unique` enum（单成员 Production + `from_flags`/`indicator`/`is_production`/`env_prefix`/`endpoints`/`_resolve`/5 per-endpoint 方法 + `__str__`）+ `EnvVarGuard` context manager（`set`/`remove` classmethod + `set_value` + `close`/`__exit__`/`__del__` 幂等 + `_ENV_LOCK`），`__all__` 9 符号。
- `agent/tests/test_env_presets.py`（新）— 17 测试：grok 4 测试镜像（env-prefix operator interface / set_value 更新+恢复 / relay≠gateway / from_flags）+ Python 映射锁定（5 字段协议前缀 / PROD_* 别名 / frozen / equality / 单成员 / is_production+indicator+str / resolve 默认+override / per-endpoint 隔离 / guard with-restore / close-idempotent / set_value-after-close-raises）。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

映射决策树**第八次重申**（payload 决定映射）：本轮 `GrokBuildEndpoints`（纯值相等无序列化）→ frozen dataclass（第二分支）；`GrokBuildEnvironment`（纯单元 enum）→ `@unique Enum`（第三分支）。**新增两点**：(1) Rust `unsafe { std::env::set_var }`（2024 process-global 标记）→ Python `os.environ`（无 `unsafe` 标记但风险相同，故 `threading.Lock` 串行化，镜像 grok `ENV_LOCK`）；Rust `Drop` 确定性释放 → Python `__exit__`/`close`/`__del__` 三重 + 幂等 `_closed` 标志（Python 无确定性 Drop，`__del__` best-effort）。(2) Rust `#[cfg(test)]` 编译时测试隔离 → Python 无条件定义 + docstring 标注"测试专用"。

**坑（自发现，已修复）**：首次测试 `test_env_var_guard_remove_restores_a_prior_value` **嵌套两层 guard**（外层 `set` 持 `_ENV_LOCK`，内层 `remove` 再 acquire 不可重入的 `Lock` → 死锁，14 点后卡住，`TaskStop` 终止）。grok `std::sync::Mutex` 同样不可重入，guard 设计上**不可嵌套**——这是忠实性，不是 bug。修复：测试改用 `monkeypatch.setenv` 预设值（绕过 Lock，单层 `remove` guard），**不换 RLock**（grok 是 Mutex，YAGNI 保持忠实）。

### 验证

- `ruff check` → **All checks passed!**（0 fixed，导入顺序本就正确）。
- `pytest tests/test_env_presets.py -q` → **17 passed in 0.08s**（修复死锁后）。
- 完整套件 `pytest` → **1659 passed in 101.55s**（R42 1642 → R43 1659，**+17 精确**，零回归）。

### YAGNI 边界

- ❌ **不接线 `llm.py`/`http_server` 读取 endpoint**——本轮是词汇表层；消费端（让 `llm.py` 读 `BuildEnvironment.Production.cli_chat_proxy_base_url()` 替代硬编码）是后续轮。
- ❌ **不实现 Dev/Staging 环境**——grok public build 也只有 Production（Dev/Staging 编译出），enum 单成员忠实 grok 现状。
- ❌ **不做真实 endpoint URL 校验**（grok `url` crate）——URL 即 `str`，校验是消费端职责；`url`/`tracing` 依赖 grok lib.rs 未使用，直接丢弃。
- ❌ **不换 `RLock` 支持嵌套 guard**——grok `std::sync::Mutex` 不可重入，guard 设计不可嵌套（忠实性）；测试用 `monkeypatch` 避免嵌套。
- ❌ **不迁移 `#[cfg(test)]` 编译隔离**——Python 无编译时条件编译，`EnvVarGuard` 无条件定义 + docstring 标注测试专用。

### Commit

`feat(platform): R43 backend env presets + EnvVarGuard (fuse grok xai-grok-env)`

## R44 — 类型安全路径包装器 + 词法规范化（融合 grok `xai-grok-paths`）

> 锚定 R43（`1b47006`）。

### 本轮目标

从 grok 的 `xai-grok-paths`（575 行 lib.rs）引入**类型安全路径包装词汇表**：`AbsPathBuf` / `RelPathBuf` 构造即验证 newtype（绝对 vs 相对）+ `normalize_lexically` 零 FS 词法 `.`/`..` 解析器 + `contains_path` 工作区逃逸防护 + `ToAbsPath` trait 六实现统一分发。

**阶段 E 平台化/发布主题**——与 R42 `version` / R43 `env_presets` 对称的"平台基础词汇表层"。产品价值：MiniMax Code 的文件工具（`file_ops` / `edit` / `search`）当前接受裸 `file_path: str`，无类型级绝对性保证，也无 `..` 路径逃逸防护。`AbsPathBuf` 让"构造即绝对"成为可执行不变量，`contains_path` 是工作区围栏 / 路径逃逸检查——agent 文件工具可拒绝 LLM 生成路径经 `..` 逃逸项目根，即使词法归一化后。本轮是该词汇表；文件工具入口接线是后续轮。

### 融合结论

**✅ 保持（映射到 Python）**：
- `camino::Utf8PathBuf`（UTF-8 保证路径）→ `pathlib.Path`（Python `str` / `Path` 原生 Unicode，故 grok `NotUtf8` 变体无 Python 对应，删除）。
- `AbsPathBuf`（newtype，`Clone+Debug+Eq+PartialEq+Ord+PartialOrd+Hash`，**无** `Serialize`，lib.rs:84 仅 derive 值 trait）→ `AbsPathBuf` frozen+slots dataclass 包 `Path`（第二分支，同 R40 `QueueEntryMeta` / R42 `Version` / R43 `BuildEndpoints`）。grok `Ord` / `PartialOrd` 推迟（无调用方需有序路径，YAGNI）；`__eq__` / `__hash__` 覆盖 `Eq` / `Hash`。
- `RelPathBuf`（newtype，同值 trait **外加** `Serialize` / `Deserialize` + `#[serde(try_from="String", into="String")]`）→ `RelPathBuf` 同 frozen+slots dataclass 形状。serde 面保持为 `from_str`（`try_from`）+ `__str__` / `into_string`（`into`）：Python 字符串即 JSON wire 面，故不引入 pydantic model（pydantic 序列化为 dict，会偏离 grok 裸字符串往返；Config 路径字段的 pydantic 集成是后续轮）。
- `AbsPathError` / `RelPathError`（`thiserror::Error` enum，`NotAbsolute` / `NotRelative` + 不可达 `NotUtf8`）→ `ValueError` 子类带 `input` 字段（Python 折叠不可能的 `NotUtf8` 变体）。
- `ToAbsPath` trait（六实现：`AbsPathBuf` / `&AbsPathBuf` / `&Path` / `&PathBuf` / `&str` / `String`）→ `to_abs_path` 单自由函数 `isinstance` 分发（abs 输入忽略 `root`；rel 输入 join `root`）。
- `to_relative_path` / `from_relative_path`（自由函数）→ 同名模块级函数；`strip_prefix` + `unwrap_or_else` → `relative_to` + `except ValueError` 回退。
- `normalize_lexically`（自由函数；`Component` 遍历无 FS）→ `normalize_lexically` 手写 `PurePath.parts` 遍历，镜像 grok component 语义（`.` 丢；`..` pop normal / root anchor clamp / 否则 push）。
- 测试矩阵（unix/windows `cfg` 分离 + contains_path 逃逸 + 词法归一化 + serde 往返）→ `skipif` 平台分离 + 同名测试镜像。

**❌ 放弃（YAGNI）**：
- `camino` / `serde` / `thiserror` crate 依赖 → 零非标准库依赖（仅 `os` / `re` / `dataclasses` / `pathlib`）。
- grok `NotUtf8` 变体 → Python `str` / `Path` 原生 Unicode，UTF-8 不变量无需强制。
- grok `Ord` / `PartialOrd`（路径字典序）→ 无调用方需有序路径。
- `RelPathBuf` 的 pydantic model wire 面 → 当前无 Config 路径字段消费它，裸 `str` 往返忠实 grok。
- 接线文件工具入口（`file_ops` / `edit` / `search`）→ 本轮是词汇表层，消费端接线是后续轮。

### 交付

- `agent/minimax_code/paths.py`（新，~370 行）— `AbsPathError` / `RelPathError`（`ValueError` 子类 + `input` 字段）+ `normalize_lexically`（零 FS `PurePath.parts` 遍历，clamp-at-root 语义，`_is_root_anchor` 区分 `/` / `C:\\` vs `C:`）+ `to_relative_path` / `from_relative_path` + `AbsPathBuf` frozen+slots dataclass（`new` 验证绝对 + `as_path` / `as_str` / `to_path_buf` / `into_string` / `join` / `is_dir` / `contains_path` + `__str__` / `__fspath__`）+ `RelPathBuf` 同形状 + `from_str` / `from_absolute` / `to_absolute` + `to_abs_path` isinstance 分发（六实现→一函数），`__all__` 8 符号。零非标准库依赖。
- `agent/tests/test_paths.py`（新，~245 行，27 测试，17 在 win32 跑 + 10 posix `skipif`）— grok 测试矩阵镜像：AbsPathBuf new / 相对失败 / as_str+to_path_buf+fspath / join 保持绝对 / 值相等+frozen / contains_path windows / contains_path posix（逃逸 + 往回归一化）/ stored root 不归一化；normalize 词法 dot 段 posix / windows 前缀 / empty→`.`；RelPathBuf new / 绝对失败 / from_absolute posix / 不在 root 下 posix / to_absolute 跨平台 / conversions+serde 往返 / serde 绝对失败 / 值相等+frozen；to_relative_path under / not under / exact root（`.`）/ from_relative 相对 / 已绝对；to_abs_path 分发 / str 绝对忽略 root；errors 携带 `input` 字段。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

映射决策树**第九次重申**（payload 决定映射）：本轮 `AbsPathBuf`（纯值相等无序列化）→ frozen+slots dataclass（第二分支）；`AbsPathError` / `RelPathError` thiserror enum → `ValueError` 子类 + `input` 字段（保留变体 payload）。**一条规则、四个分支，payload 决定走哪条**：(a) 序列化层 → pydantic v2 BaseModel；(b) 纯值相等无序列化 → frozen+slots dataclass（**本轮 AbsPathBuf**）；(c) 纯单元 enum → `@unique Enum`；(d) 混合 enum → 全部 frozen dataclass + PEP 604 联合。

**坑（自发现，已修复）**：`pathlib` 在 Windows 上**规范化分隔符**——`str(Path("src/main.rs"))` 返回 `"src\\main.rs"`（`/` → `\\`），而 grok `camino` **逐字保留输入字节**（`/`）。首次重点 pytest 27 测试 **2 FAILED**（`test_rel_path_buf_new_valid` / `test_rel_path_buf_conversions_and_serde_roundtrip` 断言 `== "src/main.rs"`）。这是真实的平台忠实差异，非 bug。修复：测试改用 `Path("src/main.rs")` 相等比较（`rel.as_path() == Path(...)`）+ `expected = str(Path("src/main.rs"))` 预期值，注释说明 pathlib vs camino 忠实差异 + serde 字节保真 YAGNI（当前无 Config 路径字段消费裸分隔符）。修复后 **17 passed, 10 skipped**。

### 验证

- `ruff check` → **All checks passed!**（导入顺序 / 行长 100 正确）。
- 重点 `pytest tests/test_paths.py -q` → **17 passed, 10 skipped in 0.09s**（修复 pathlib 分隔符后；10 skip 是 posix 测试在 win32 正确跳过）。
- 完整套件 `pytest -q` → **1676 passed, 10 skipped in 104.11s**（R43 1659 → R44 1676，**+17 精确**，零回归）。

### YAGNI 边界

- ❌ **不接线文件工具入口**（`file_ops` / `edit` / `search`）——本轮是词汇表层；让文件工具入口接受 `AbsPathBuf` + `contains_path` 逃逸防护是后续轮。
- ❌ **不迁移 grok `Ord` / `PartialOrd`**——无调用方需有序路径；`__eq__` / `__hash__` 覆盖 `Eq` / `Hash` 足够。
- ❌ **不引入 pydantic wire 面给 `RelPathBuf`**——grok serde 是裸 `String` 往返，Python `str` 即等价；pydantic 序列化为 dict 会偏离，且当前无 Config 路径字段消费。
- ❌ **不做 serde 分隔符字节保真**——pathlib 规范化平台分隔符（`/` → `\\` on win32）是 Python 原生忠实；grok camino 逐字保真是 Rust 字节忠实，两者各异，测试用 `Path` 相等 + `str(Path(...))` 适配。
- ❌ **不强制 UTF-8（grok `NotUtf8` 变体）**——Python `str` / `Path` 原生 Unicode，UTF-8 不变量无需运行时强制。

### Commit

`feat(platform): R44 type-safe path wrappers (fuse grok xai-grok-paths)`

## R45 — 默认模型 ID 词汇表（融合 grok `xai-grok-models`）

> 锚定 R44（`33aafda`）。

### 本轮目标

从 grok 的 `xai-grok-models`（71 行 lib.rs + `default_models.json`）引入**数据驱动的默认模型 ID 词汇表**：内嵌 JSON 文档（`include_str!` 编译时嵌入）→ `DefaultModels`/`DefaultModelEntry` serde 反序列化 → `LazyLock` 单例 + `default ∈ models` 不变量 assert → 4 个场景化默认访问函数（`default` + `web_search`/`image_description`/`session_summary`，后三者 `unwrap_or(&default)` 回退）。

**阶段 E 平台化/发布主题**——与 R42 `version` / R43 `env_presets` / R44 `paths` 对称的"平台基础词汇表层"。产品价值：MiniMax Code 的 `handlers_model` 当前硬编码 `CANDIDATE_MODELS`/`MODEL_META` 向后兼容常量，缺数据驱动的默认模型清单层。本模块把"默认模型 + 场景化分流（搜索用旗舰 / 摘要用快速档）+ default∈models 不变量"做成单一内嵌文档，为 `model.get_current` 回退链奠基。本轮是该词汇表；`handlers_model` 接线优先级链是后续轮。

### 融合结论

**✅ 保持（映射到 Python）**：
- `DEFAULT_MODELS_JSON`（`include_str!("../default_models.json")`，编译时嵌入二进制）→ `DEFAULT_MODELS_JSON` 模块级三引号字符串常量。Python 无编译时文件嵌入；源码级常量是最忠实的运行时等价（编辑常量 + 重启 ≈ 编辑 JSON + 重编译）。MiniMax 本地化模型 ID（grok `grok-build`/`grok-4.20-multi-agent` → MiniMax `MiniMax-M3`/`MiniMax-M3-fast`/`MiniMax-Code`）；场景化分流：`web_search`/`image_description` 用旗舰 `MiniMax-M3`（合成需强模型），`session_summary` 用 `MiniMax-M3-fast`（摘要轻量，快速档够用且省成本）。
- `DefaultModels`（`#[derive(serde::Deserialize)]`，`default: String` + 3 `Option<String>` + `models: Vec<DefaultModelEntry>`）→ `DefaultModels` pydantic v2 `BaseModel`（**分支 a，序列化层**，同 R40 `QueueEntryMeta`）。serde 默认忽略多余字段 → pydantic v2 默认 `extra="ignore"`：JSON 的 `name`/`description`/`context_window`/`temperature`/`top_p` 元数据在文档中携带但此处不建模，忠实 grok struct 只读 `model`。
- `DefaultModelEntry`（`#[derive(serde::Deserialize)]`，单 `model: String`）→ `DefaultModelEntry` pydantic v2 `BaseModel`（多余字段忽略，镜像 grok struct）。
- `static DEFAULTS: LazyLock<DefaultModels>`（`serde_json::from_str` 一次 + `assert!(default ∈ models)` + 线程安全）→ `_load_defaults()` `@lru_cache(maxsize=1)` 包装（单次初始化，GIL 下线程安全，忠实 `LazyLock` 语义）。`assert!` panic → `assert` 语句（`AssertionError`，developer error，忠实 grok panic）。
- 4 访问函数（`default_model` + 3 场景 `Option::unwrap_or(&default)`）→ 同名模块级函数；`unwrap_or(&default)` → `or d.default`。
- grok `serde`/`serde_json` crate → `json`（stdlib）+ `pydantic`（已有 agent 依赖）。

**❌ 放弃（YAGNI）**：
- `serde`/`serde_json` crate 依赖 → 仅 `json` + `pydantic`（零新增非标准库依赖）。
- 解析优先级链（grok doc 注释：CLI 标志 > ENV > config.toml > 远程设置 > 这些默认值）→ grok 此 crate **不实现**（在 `agent::config`），本模块是默认值叶子；优先级接线是后续轮。
- grok `api_backend`/`supported_in_api` 字段（xAI responses API 专用）→ MiniMax 用 chat completions，无对应。
- 接线 `handlers_model` 优先级链（`model.get_current` 回退 `default_model()`）→ 本轮是词汇表层，消费端接线是后续轮。
- 模型展示元数据建模（`name`/`context_window` 等进 pydantic 字段）→ grok struct 只读 `model` ID，忠实；MiniMax 元数据已在 `handlers_model.MODEL_META`（单一职责，不重复）。
- `importlib.resources` 外部 JSON 文件分发 → Python 无编译时嵌入保证，模块级常量是最忠实 `include_str!` 等价（避免打包复杂度 + 文件漂移）。

### 交付

- `agent/minimax_code/models.py`（新，186 行）— `DEFAULT_MODELS_JSON` 模块级三引号常量（MiniMax 三模型 + 场景化分流）+ `DefaultModelEntry`（pydantic，单 `model`）+ `DefaultModels`（pydantic，`default` + 3 `Optional` + `models`）+ `_load_defaults()`（`@lru_cache(maxsize=1)` + `assert default ∈ models` 不变量）+ 4 模块级访问函数（`default_model`/`default_web_search_model`/`default_image_description_model`/`default_session_summary_model`，后 3 `or default` 回退），`__all__` 7 符号。依赖：`json` + `functools`（stdlib）+ `pydantic`。
- `agent/tests/test_models.py`（新，160 行，12 测试）— grok 契约镜像（baked JSON 合法 / 本地化三模型集 / `default_model` 返回 default / 3 场景读 JSON 值 / 场景回退 default via `monkeypatch` + `cache_clear` / `default∈models` 不变量成立 / `default∉models` 触发 `AssertionError` via `monkeypatch` + `cache_clear`）+ Python 映射锁定（pydantic 缺 `default`/`models` 字段 → `ValidationError` / `DefaultModelEntry` 忽略多余字段 `extra='ignore'` `not hasattr` / 3 `Optional` 默认 `None` / `lru_cache` 单例 `is` 同一实例）。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

映射决策树**第十次重申**（payload 决定映射）：本轮 `DefaultModels`/`DefaultModelEntry`（serde Deserialize，JSON 反序列化层）→ pydantic v2 `BaseModel`（**分支 a，序列化层**——阶段 E 首次走分支 a；R40 `QueueEntryMeta` wire 类型也走分支 a）。**一条规则、四个分支，payload 决定走哪条**：(a) 序列化层 → pydantic v2 BaseModel（**本轮**）；(b) 纯值相等无序列化 → frozen+slots dataclass；(c) 纯单元 enum → `@unique Enum`；(d) 混合 enum → 全部 frozen dataclass + PEP 604 联合。**本轮新增两点**：(1) Rust `include_str!`（编译时文件嵌入二进制）→ Python 模块级字符串常量（Python 无编译时；源码嵌入是最忠实运行时等价，避免 `importlib.resources` 打包复杂度）；(2) Rust `LazyLock`（线程安全单次初始化）→ Python `@lru_cache(maxsize=1)`（GIL 下线程安全 + 惰性单次，忠实 LazyLock 语义）。

**坑（自发现，已修复）**：ruff `I001` 报 `test_models.py` 导入块未排序，`--fix` 自动修复 1（isort 标准化第三方组 `pytest`/`pydantic` 与 first-party `minimax_code` 间的空行）。修复后 `All checks passed!`。无运行时错误——重点 pytest 12 测试一次通过。

### 验证

- `ruff check` → **All checks passed!**（`--fix` 修 I001 后；`models.py` 本就干净）。
- 重点 `pytest tests/test_models.py -v` → **12 passed in 0.14s**（一次通过）。
- 完整套件 `pytest` → **1688 passed, 10 skipped in 103.85s**（R44 1676 → R45 1688，**+12 精确**，零回归）。

### YAGNI 边界

- ❌ **不接线 `handlers_model` 优先级链**——本轮是默认值叶子；让 `model.get_current` 回退 `default_model()` / `default_session_summary_model()` 是后续轮。
- ❌ **不建模模型展示元数据**（`name`/`context_window`/`temperature`/`top_p`）——grok struct 只读 `model` ID，忠实；MiniMax 元数据已在 `handlers_model.MODEL_META`，单一职责不重复。
- ❌ **不迁移 grok `api_backend`/`supported_in_api`**——xAI responses API 专用，MiniMax chat completions 无对应。
- ❌ **不做 `importlib.resources` 外部 JSON 文件**——Python 无编译时嵌入保证；模块级字符串常量是最忠实 `include_str!` 等价（避免打包复杂度 + 文件/常量漂移）。
- ❌ **不实现解析优先级链**（CLI > ENV > config.toml > remote > defaults）——grok 此 crate 不实现（在 `agent::config`），本模块是默认值叶子。

### Commit

`feat(platform): R45 default model vocabulary (fuse grok xai-grok-models)`

---

## R46 — storage DEFAULT_MODEL 单一来源接线（融合 grok xai-grok-models 消费端）

> 锚定 R45（`51b0bab`）。

### 本轮目标

R45 交付了默认值**词汇表**（`models.py`：`DEFAULT_MODELS_JSON` 文档 + `default_model()` / 3 场景访问器 + `default ∈ models` 不变量），但词汇表本身没有消费端 —— 项目里仍散落着 **2 处硬编码 `DEFAULT_MODEL = "MiniMax-M3"`**（`storage/dao/model_prefs.py:34` + `agent/llm.py:32`）以及 7+ 处散落 `"MiniMax-M3"` 字面量 fallback。本轮是 R45 之后的**第一轮接线（wiring round）**：把 `model_prefs.py` 的 `DEFAULT_MODEL` 从硬编码字面量改为 `default_model()` 求值，让 storage 种子 fallback、DAO None-fallback、IPC 层三者共享 R45 那份单一 baked-in 文档（DRY），并加一条不变量测试**锁死**这个接线 —— 任何回退到硬编码字面量的回归都会立即破坏测试。这是 R45 词汇表的**第一个真实消费端**，平台化「单一事实来源」的关键一步，也为 R47+（`llm.py:32`、`CANDIDATE_MODELS` 推导）扫清模式。

### 融合结论

- ✅ **保留**：`DEFAULT_MODEL = default_model()` 单一来源接线（`storage/dao/model_prefs.py`）。这是真正的 DRY 修复 —— 让 storage 层从 R45 词汇表取默认值，而非自己再硬编码一遍。
- ✅ **保留**：`test_default_model_is_storage_single_source` 不变量测试 —— 锁死接线，防回归。
- ❌ **放弃（自发现，关键转向）**：原始 R46 计划是把 `handle_model_get_current` 接线成 `model is None → default_model()` fallback。**读 `model_prefs.py:59-79` 后发现 `ModelPrefsDAO.get_current()` 永远不会返回 None**（行 69-70 缺行即回退 `DEFAULT_MODEL`，行 77 空值亦回退 `DEFAULT_MODEL`）—— 因此 IPC 层 None fallback 是**死代码**，接线它毫无意义。转向 storage 层 `DEFAULT_MODEL` 单一来源，这才是真问题（两个独立硬编码，违反 DRY）。

### 交付

- `agent/minimax_code/storage/dao/model_prefs.py`（改，159→164 行）— (1) 新增导入 `from ...models import default_model`（ruff `--fix` 后排序在 `from ._base import now_iso, row_to_dict` 之前——多点相对导入 `...models` 优先于单点 `._base`，I001 合规）；(2) `DEFAULT_MODEL = "MiniMax-M3"` → `DEFAULT_MODEL = default_model()`，更新注释说明来源是 R45 词汇表、修改 `DEFAULT_MODELS_JSON` 即可改全局默认、migration SQL 种子是一次性快照。**值不变**（`default_model() == "MiniMax-M3"`）。
- `agent/tests/test_models.py`（改，160→177 行，12→13 测试）— 新增 `test_default_model_is_storage_single_source`：`from minimax_code.storage.dao.model_prefs import DEFAULT_MODEL as storage_default; assert storage_default == M.default_model()`。锁定接线，任何回退硬编码字面量即破坏此测试。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

### 映射决策树（本轮无新映射 —— 消费端接线轮，非词汇表层）

本轮不引入新枚举/类型，仅让 storage 消费 R45 的 `default_model()` 函数（已在 R45 走过分支 a：序列化层 → pydantic）。**接线轮的本质**：R45 词汇表层是「低风险新增模块」，R46 接线轮是「修改现有消费端使词汇表生效」—— 后者是平台化关键（无词汇表的词汇表是死代码），但带回归风险，需完整 pytest 把关。本轮证明接线零回归（1689 = 1688+1 精确），为 R47+ 接线（`llm.py:32`、`CANDIDATE_MODELS`）建立**安全接线模式**：(1) 先读消费端确认目标非死代码；(2) 值保持不变（`== DEFAULT_MODEL` 断言自动跟随）；(3) 加不变量测试锁死；(4) 重点 + 完整双 pytest。

**坑（自发现，已修复）**：(1) **死代码陷阱** —— 原始计划（`get_current` None fallback）在读消费端源码后被判定为死代码并放弃；这是「先读后写」原则的价值，避免了接线一段永远不会执行的代码。(2) ruff `I001` 报 `model_prefs.py` 导入块未排序（新增 `from ...models import default_model` 后），`--fix` 自动修复 1（多点相对 `...models` 排在单点 `._base` 之前）。修复后 `All checks passed!`。无运行时错误。

### 验证

- `ruff check` → **All checks passed!**（`--fix` 修 I001 后）。
- 重点 `pytest tests/test_model.py tests/test_models.py tests/test_handlers_providers.py -v` → **31 passed in 2.77s**（16+13+2，零中断——`test_model.py` 全部 `== DEFAULT_MODEL` 值断言自动跟随，因为 `default_model() == "MiniMax-M3"` 值未变）。
- 完整套件 `pytest` → **1689 passed, 10 skipped in 102.68s**（R45 1688 → R46 1689，**+1 精确**，零回归）。

### YAGNI 边界

- ❌ **不接线 `agent/llm.py:32` `DEFAULT_MODEL = "MiniMax-M3"`** —— 第二个硬编码，但那是 agent 核心层（LLM client 默认模型），影响面大，留 R47 单独接线 + 完整回归。
- ❌ **不迁移 7+ 散落 `"MiniMax-M3"` 字面量 fallback** —— 散落在 handlers/agent 各处，逐轮消化（每轮 1-2 处），本轮只接 storage 这一处（最高频、最关键）。
- ❌ **不推导 `CANDIDATE_MODELS` / `MODEL_META` 从词汇表** —— `handlers_model.py` 硬编码候选集 + 元数据，是独立的接线面（且涉及 backward-compat with `ProviderDAO`），留后续轮。
- ❌ **不改 migration SQL 种子**（`002_model_prefs` 里的 `'MiniMax-M3'`）—— 一次性快照，已部署的 DB 不会重跑迁移；与 `DEFAULT_MODEL` 的 drift 可接受（migration 是 one-shot，`DEFAULT_MODEL` 是运行时回退，语义不同）。
- ❌ **不接线 `handle_model_get_current` None fallback** —— 死代码（`get_current` 永不返回 None），见融合结论 ❌。

### Commit

`feat(platform): R46 wire default_model to storage DEFAULT_MODEL (fuse grok xai-grok-models)`

---

## R47 — LLM client DEFAULT_MODEL 单一来源接线（融合 grok xai-grok-models 消费端）

> 锚定 R46（`98eb5b9`）。

### 本轮目标

R46 接了 **storage 层**的 `DEFAULT_MODEL`（`model_prefs.py`），消除了两处硬编码 `DEFAULT_MODEL = "MiniMax-M3"` 中的第一处。本轮接**第二处、也是最后一处核心硬编码**：`agent/llm.py:32` 的 `DEFAULT_MODEL`，它是 `MiniMaxClient.__init__(model: str = DEFAULT_MODEL)` 的默认参数 —— LLM client 的模型回退值。接线后，storage 种子、storage DAO None-fallback、**LLM client 默认**三者全部从 R45 的 `default_model()` 取值，R45 词汇表成为整个模型默认值的**单一事实来源**（DRY 达成）。本轮是 R46 安全接线模式的**第二次应用**（巩固模式：先读消费端确认非死代码 → 值保持不变 → 加不变量测试锁死 → 重点+完整双 pytest），证明该模式可复用于 agent 核心层。

### 融合结论

- ✅ **保留**：`llm.py` `DEFAULT_MODEL = default_model()` 单一来源接线 —— 消除最后一个核心硬编码，让 LLM client 默认模型与 R45 词汇表一致。
- ✅ **保留**：`test_default_model_is_llm_client_single_source` 不变量测试 —— 锁死接线，防回归（任何回退硬编码字面量即破坏此测试）。
- ❌ **无死代码陷阱**（本轮 R46 模式直接复用，无废弃转向）：`llm.py:32` 的 `DEFAULT_MODEL` 是 `__init__` 真实默认参数（第 79 行 `model: str = DEFAULT_MODEL`），非死代码，接线即生效。

### 交付

- `agent/minimax_code/agent/llm.py`（改）— (1) 新增导入 `from ..models import default_model`（ruff 一次通过，自动排在 `from .. import secrets` 与 `from .transports import LLMTransport` 之间——两点相对 `..models` 排序合规）；(2) `DEFAULT_MODEL = "MiniMax-M3"` → `DEFAULT_MODEL = default_model()`，加 6 行注释说明来源是 R45 词汇表、这是两处硬编码的第二处（R46 修 model_prefs、R47 修 llm）、改 `DEFAULT_MODELS_JSON` 即改全局默认。**值不变**（`default_model() == "MiniMax-M3"`），第 79 行 `__init__` 默认参数自动跟随。
- `agent/tests/test_models.py`（改，177→194 行，13→14 测试）— 新增 `test_default_model_is_llm_client_single_source`：`from minimax_code.agent.llm import DEFAULT_MODEL as llm_default; assert llm_default == M.default_model()`。与 R46 的 storage 测试并列，两个消费端接线各有独立不变量测试。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

### 映射决策树（本轮无新映射 —— 第二次消费端接线，复用 R46 安全模式）

本轮不引入新枚举/类型，仅让 LLM client 消费 R45 的 `default_model()`（已在 R45 走分支 a）。R46 建立的**安全接线模式**本轮第二次应用并验证：(1) **先读消费端** —— grep `DEFAULT_MODEL` 确认 llm.py 仅 3 处引用（定义/`__init__` 默认参数/`__all__`），非死代码；(2) **导入循环排查** —— `models.py` 同在顶层包 `minimax_code`，与已导入的 `secrets` 同级，`models.py` 只依赖 stdlib+pydantic，单向无循环；(3) **值保持不变** —— `default_model() == "MiniMax-M3"`，所有 `== DEFAULT_MODEL` / `model == DEFAULT_MODEL` 断言自动跟随；(4) **不变量测试锁死**；(5) **双 pytest**（重点覆盖消费路径 + 完整套件）。模式的可复用性是平台化的关键 —— R48+ 的散落字面量迁移可照此推进。

**坑（自发现，已绕过）**：(1) **测试文件名误判** —— 原计划跑 `test_llm.py`，实际不存在（Glob 仅 `test_code_review_llm.py`）；改为跑覆盖 `DEFAULT_MODEL` 实际消费路径的重点集（`test_completion` + `test_agent_core` + `test_chat` + `test_model` + `test_models` + `test_handlers_providers` = 78 passed），这些测试通过 `MiniMaxClient()` 默认构造间接消费 `DEFAULT_MODEL`，零中断证明接线值不变。(2) ruff `I001` 本轮**未触发**（`from ..models import default_model` 插入位置天然合规，一次通过）—— 对比 R46 需 `--fix`，说明 R46 的排序经验已内化。无运行时错误。

### 验证

- `ruff check minimax_code/agent/llm.py` → **All checks passed!**（一次通过，无需 `--fix`）。
- 重点 `pytest tests/test_completion.py tests/test_agent_core.py tests/test_chat.py tests/test_model.py tests/test_models.py tests/test_handlers_providers.py -q` → **78 passed in 13.13s**（覆盖 DEFAULT_MODEL 全部消费路径，零中断）。
- 完整套件 `pytest` → **1690 passed, 10 skipped in 107.22s**（R46 1689 → R47 1690，**+1 精确**，零回归）。

### YAGNI 边界

- ❌ **不推导 `handlers_model.CANDIDATE_MODELS` 从 R45 词汇表** —— R48 候选。预研已完成：`CANDIDATE_MODELS[0] == DEFAULT_MODEL`（test_model.py:314）约束由 R45 JSON `models[0] == "MiniMax-M3"` 天然满足，推导安全；但需新增 R45 访问器 `default_model_ids()` 或在 handlers 直接推导，留 R48 决策。
- ❌ **不推导 `MODEL_META` 从 R45 词汇表** —— R45 `DefaultModelEntry` 只建模 `model`（extra="ignore" 丢弃 `context_window`/`name`），推导 `MODEL_META` 需**先扩展 R45 entry** 建模展示元数据（独立的词汇表层演进，R49+）。
- ❌ **不迁移 7+ 散落 `"MiniMax-M3"` 字面量 fallback** —— 散落 handlers/agent 各处，逐轮消化。
- ❌ **不改 migration SQL 种子**（`002_model_prefs` 的 `'MiniMax-M3'`）—— 一次性快照，与运行时 `DEFAULT_MODEL` 语义不同，drift 可接受。

### Commit

`feat(platform): R47 wire default_model to LLM client DEFAULT_MODEL (fuse grok xai-grok-models)`

---

## R48 — CANDIDATE_MODELS 从 R45 词汇表推导（融合 grok xai-grok-models 消费端）

> 锚定 R47（`e083693`）。

### 本轮目标

R46/R47 接了两处 `DEFAULT_MODEL` 硬编码（storage + LLM client）。本轮攻克**第三个硬编码面** —— `handlers_model.CANDIDATE_MODELS`（候选模型 tuple），它是 `is_valid_model` 的校验集 + test_model.py 多处断言的基准。原硬编码 `("MiniMax-M3", "MiniMax-M3-fast", "MiniMax-Code")` 与 R45 `DEFAULT_MODELS_JSON` 的 `models` 列表重复（DRY）。本轮是**混合轮**：先在 R45 词汇表新增 `default_model_ids()` 访问器（词汇表层演进 —— 让词汇表 owning 有序 ID 列表），再把 `handlers_model.CANDIDATE_MODELS` 从硬编码 tuple 改为 `default_model_ids()` 推导（消费端接线）。预研关键约束：`test_model.py:314` `CANDIDATE_MODELS[0] == DEFAULT_MODEL` 由 R45 JSON `models[0] == "MiniMax-M3" == default` 天然满足 —— 推导后值与顺序完全不变，所有 `CANDIDATE_MODELS` 断言自动通过。

### 融合结论

- ✅ **保留**：R45 `models.py` 新增 `default_model_ids() -> tuple[str, ...]` 访问器 —— 词汇表层演进，返回 `_load_defaults().models` 的有序 model IDs tuple，docstring 明示 `[0] == default_model()` 不变量。
- ✅ **保留**：`handlers_model.CANDIDATE_MODELS = default_model_ids()` 接线 —— 消费端从硬编码 tuple 改为词汇表推导，候选集与默认值词汇表共享单一 baked-in 文档。
- ✅ **保留**：3 个不变量测试（有序三模型集 / `[0]==default` / 接线锁死）。
- ❌ **无死代码陷阱**：预研（R47 轮已读 test_model.py 断言 + R48 读 handlers_model.py）确认 CANDIDATE_MODELS 是 `is_valid_model` 真实校验集 + 测试基准，非死代码。

### 交付

- `agent/minimax_code/models.py`（改，186→200 行）— R45 词汇表新增 `default_model_ids()` 访问器（在 `default_session_summary_model` 之后），返回 `tuple(entry.model for entry in _load_defaults().models)`，10 行 docstring 说明有序性 + `[0]==default_model()` 不变量 + 消费接缝（handlers R48 接线）；`__all__` 加入 `default_model_ids`（字母序）。
- `agent/minimax_code/ipc/handlers_model.py`（改）— (1) 新增导入 `from ..models import default_model_ids`（ruff `--fix` 后排在 `from .handler_utils`/`from .protocol`/`from .server` 之前——两点相对 `..models` 优先于单点相对）；(2) `CANDIDATE_MODELS` 从硬编码 3 元素 tuple 改为 `default_model_ids()`，加 9 行注释说明来源（R45 词汇表）+ `[0]==default_model()` 不变量 + backward-compat 语义（live list 仍动态来自 `ProviderDAO`）。**值与顺序不变**。
- `agent/tests/test_models.py`（改，194→222 行，14→17 测试）— 新增 3 测试：`test_default_model_ids_returns_ordered_three_model_set`（`== ("MiniMax-M3","MiniMax-M3-fast","MiniMax-Code")` + `isinstance tuple`）/ `test_default_model_ids_first_element_is_default`（`ids[0] == default_model()`）/ `test_candidate_models_derived_from_vocabulary`（`handlers.CANDIDATE_MODELS == M.default_model_ids()` + `[0]==default`）。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

### 映射决策树（本轮混合 —— 词汇表层演进 + 消费端接线，无新枚举映射）

本轮不引入新枚举/类型，是 R45 词汇表**纵向演进**（加第 5 个访问器 `default_model_ids`，与 R45 已有的 4 个 `default_*_model` 同构）+ handlers 消费端接线。决策树四分支本轮无新增（`default_model_ids` 是普通函数，非 enum/dataclass/pydantic 新类型）。**混合轮的合法性**：词汇表层演进（R45 加访问器）与消费端接线（handlers 用它）强耦合 —— 没有访问器就无法接线 CANDIDATE_MODELS，放一轮避免「加了访问器但无人用」的中间态。这与 R46/R47 纯接线轮（消费已有 R45 函数）不同，但耦合性证明同轮合理。

**坑（自发现，已修复）**：(1) ruff `I001` handlers_model.py 导入块未排序（加 `from ..models import default_model_ids` 后），`--fix` 修复（两点相对排单点相对之前）。(2) ruff `F841 ×3` —— `handlers_model.py` 第 240/253/302 行 `except Exception as exc:` 的 `exc` 未使用（model.list/get_current/set_current 的防御性 except，只用 `logger.exception()`，不需 `exc`）。这是**预存 lint**（非 R48 引入），但按 CLAUDE.md「修复正在编辑文件的 ruff 错误可以」顺手清理（行为保持：`logger.exception()` 自动从 `sys.exc_info()` 取，移除 `as exc` 无影响）。`--fix` 一次修全部 4 个，重检 `All checks passed!`。无运行时错误——重点 pytest 35 测试一次通过。

### 验证

- `ruff check` → **All checks passed!**（`--fix` 修 I001 + F841×3 后；models.py/test_models.py 本就干净）。
- 重点 `pytest tests/test_model.py tests/test_models.py tests/test_handlers_providers.py -q` → **35 passed in 2.83s**（test_model 16 + test_models 17 + test_handlers_providers 2，R48 新增 3 测试全过，`CANDIDATE_MODELS` 全部消费断言自动跟随）。
- 完整套件 `pytest` → **1693 passed, 10 skipped in 103.67s**（R47 1690 → R48 1693，**+3 精确**，零回归）。

### YAGNI 边界

- ❌ **不推导 `MODEL_META` 从词汇表** —— R45 `DefaultModelEntry` 只建模 `model`（extra="ignore" 丢弃 `name`/`context_window`/`description`/`temperature`/`top_p`）。推导 `MODEL_META`（含 `context_window`/`name`/`provider`/`supports_tools`/`is_default`）需**先扩展 R45 entry** 建模展示元数据 —— 独立的词汇表层演进（R49）。
- ❌ **不迁移 5 处散落 `"MiniMax-M3"` fallback** —— `app.py:404` / `completion_routes.py:121` / `core.py:200` / `runtime.py:358` / `resolution.py:136`（债务地图已建立，逐轮消化，R50+）。
- ❌ **不改 migration SQL 种子**（`005_providers.py:44` 的模型 JSON）—— 一次性快照，已部署 DB 不重跑；与运行时词汇表 drift 可接受（语义不同）。
- ❌ **不扩展 `DefaultModelEntry` 建模展示元数据** —— R49 词汇表层演进，为 MODEL_META 推导铺路。

### Commit

`feat(platform): R48 derive CANDIDATE_MODELS from model vocabulary (fuse grok xai-grok-models)`

---

## R49 — 迁移散落 MiniMax-M3 fallback 至 R45 词汇表（融合 grok xai-grok-models 消费端）

> 锚定 R48（`ac4c105`）。

### 本轮目标

R46/R47/R48 接了 3 处 `DEFAULT_MODEL` / `CANDIDATE_MODELS` 硬编码。本轮转向**债务地图** —— 全仓 5 处散落的 `"MiniMax-M3"` 字面量 fallback。开局调研 grok 的 `ModelMetadata`（`xai-chat-state/commands.rs:17`）发现它只有 `resolved_model_id` + `model_fingerprint` 两字段，是**运行时解析结果**，**不是** baked-in 展示元数据（context_window/name）—— grok 根本没有 context_window/name 的结构化建模。故**放弃**"扩展 `DefaultModelEntry` 建模展示元数据"方向（会偏离 grok 的 serde 对等：grok 的 entry 故意只读 `model`，元数据留给其他消费者），改为迁移**最低风险的 2 处边缘 fallback**：`resolution.resolve_subagent_spec` 的 `parent_model` 默认参数（纯函数 leaf）+ `skills._build_agent_core` 的 `model or "MiniMax-M3"` fallback（技能 runtime）。复用 R46/R47/R48 已建立的单来源接线模式，值不变 `"MiniMax-M3"`。

### 融合结论

- ✅ **保留**：`resolution.py` `parent_model: str = default_model()` —— 顶层导入 `from ..models import default_model`，默认参数在定义时求值（与 R46 storage / R47 llm 的 `DEFAULT_MODEL = default_model()` 同构），纯函数 leaf 无循环风险。
- ✅ **保留**：`runtime.py` `_build_agent_core` 的 `model=model or default_model()` —— `default_model` **懒加载**在函数内（`from ...models import default_model`，与既有 `from ..core import AgentConfig, AgentCore  # lazy: avoids circular import` 同位置），因为该文件显式声明"lazy to avoid circular import on cold start"。
- ✅ **保留**：2 个不变量测试（resolution `inspect.signature` 默认参数锁死 + runtime `monkeypatch` AgentConfig/AgentCore 捕获 fallback 与 passthrough）。
- ❌ **放弃**：扩展 `DefaultModelEntry` 建模 name/context_window —— grok 的 `ModelMetadata` 是运行时解析（resolved_id+fingerprint），非 baked-in 展示元数据；grok 的 serde entry 故意只读 `model`。扩展会破坏 R45 的 serde 对等契约 + 偏离 grok 哲学。

### 交付

- `agent/minimax_code/orchestrator/resolution.py`（改）— (1) 导入块加 `from ..models import default_model`（first-party，stdlib 组后空行分隔）；(2) `resolve_subagent_spec` 默认参数 `parent_model: str = "MiniMax-M3"` → `default_model()` + 行内注释；(3) 两处 docstring 更新（模块顶 line 50-51 + 函数 line 155-157）说明来源从字面量改为 `default_model` 词汇表，避免 doc drift。
- `agent/minimax_code/agent/skills/runtime.py`（改）— (1) `_build_agent_core` 懒加载 `from ...models import default_model`（在 `from ..core` 之前，ruff 多点相对排序）；(2) `model=model or "MiniMax-M3"` → `model=model or default_model()`。**值不变**。ruff `--fix` 顺手清理 3 处预存 lint（顶层导入块 I001 排序 + 2× UP037 `"SkillToolProvider"` 冗余引号 —— `from __future__ import annotations` 下引号多余，去引号行为保持）。
- `agent/tests/test_models.py`（改，222→276 行，17→19 测试）— 新增 2 测试：`test_resolve_subagent_parent_model_default_from_vocabulary`（`inspect.signature` 读 `parent_model` 默认值 `== default_model()`）/ `test_build_agent_core_model_fallback_from_vocabulary`（monkeypatch `AgentConfig`/`AgentCore` 捕获 `model`，测 `model=None` → `default_model()` + `model="explicit-model"` → passthrough）。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

### 映射决策树（本轮纯消费端接线，无新类型/枚举）

本轮不引入新枚举/类型，是 R45 词汇表的**第三、四处消费端接线**（R46 storage / R47 llm / R48 CANDIDATE_MODELS / R49 resolution+runtime）。决策树四分支本轮无新增。**接线模式复用**：resolution（纯函数 leaf）用顶层导入 + `default_model()` 直接作默认参数（R46/R47 同构）；runtime（声明冷启动循环风险）用函数内懒加载导入（与既有 `from ..core import` 同模式）。两种导入策略的选择依据是**消费模块的循环风险**，非任意。

**坑（自发现，已修复）**：ruff 报 4 错（全在 runtime.py）—— 1× I001 我的懒加载导入排序（`...models` 三点应在 `..core` 两点之前，多点相对优先）+ 3× 预存（顶层导入块 I001 + 2× UP037 冗余引号）。按 R48 既定模式 + CLAUDE.md「修复正在编辑文件的 ruff 错误」授权，`--fix` 一次清理全部 4 个（行为保持），重检 `All checks passed!`。无运行时错误——重点 pytest 89 测试一次通过。

### 验证

- `ruff check` → **All checks passed!**（`--fix` 修 I001×2 + UP037×2 后；resolution.py 本就干净）。
- 重点 `pytest tests/test_subagent_resolution.py tests/test_skills.py tests/test_v060_skills.py tests/test_models.py -q` → **89 passed in 2.34s**（resolution + skills runtime 全路径 + models，R49 接线零破坏）。
- `pytest tests/test_models.py -q` → **19 passed**（R48 的 17 + R49 的 2 新测试全过）。
- 完整套件 `pytest` → **1695 passed, 10 skipped in 98.53s**（R48 1693 → R49 1695，**+2 精确**，零回归）。

### YAGNI 边界

- ❌ **不迁移剩余 3 处散落 fallback** —— `completion_routes.py:121` / `app.py:404` / `core.py:200`（`AgentCore.__init__` 默认参数，**核心层最高风险**），留 R50+ 逐轮消化；core.py:200 单独一轮（核心层接线需最完整 pytest + 不变量测试）。
- ❌ **不扩展 `DefaultModelEntry` 建模展示元数据** —— grok 的 `ModelMetadata` 是运行时解析（resolved_id+fingerprint），serde entry 只读 `model`；扩展破坏 R45 serde 对等。
- ❌ **不推导 `MODEL_META` 从词汇表** —— 仍硬编码（`handlers_model.py:68`），R50+ 需先决定 MiniMax 展示层策略（grok 无对应物，是 MiniMax 前端独有需求）。
- ❌ **不改 migration SQL 种子**（`002_model_prefs.py` / `005_providers.py`）—— 已部署快照，不重跑。

### Commit

`feat(platform): R49 migrate scattered MiniMax-M3 fallbacks to model vocabulary (fuse grok xai-grok-models)`

---

## R50 — 核心层 AgentConfig.model 单一来源接线（融合 grok xai-grok-models 消费端，里程碑）

> 锚定 R49（`8885a7b`）。**本轮达成「至少迭代超 50 回合」里程碑（第 50 轮）** —— 单来源迁移链路完整闭环：R46 storage → R47 llm → R48 candidate set → R49 resolver/runtime → **R50 AgentConfig（核心层 capstone）**。

### 本轮目标

R46/R47/R48/R49 五处消费端已接 R45 词汇表，剩**核心层最高风险**的一处 —— `agent/core.py` 的 `AgentConfig.model` dataclass 字段默认值（原硬编码 `model: str = "MiniMax-M3"`）。这是**对话循环的中央配置**：每个 `AgentCore` 实例（主 agent + 每个技能子 agent）的模型由它决定，是整个系统最敏感的单一字段。本轮把它从字面量改为 `default_model()` —— 在 dataclass **类定义时**求值（与函数默认参数同语义：字段默认值在类体执行时求值，模块导入即定型）。预研关键事实：`models.py` 仅导入 `functools`/`json`/`pydantic.BaseModel`（零 minimax_code 子模块），`core.py` 已通过 R47 接入 `..models` 的同层 first-party 导入（`.llm`）—— 新增 `from ..models import default_model` 不引入循环。值保持 `"MiniMax-M3"`，行为完全保持。

### 融合结论

- ✅ **保留**：`core.py` 导入 `from ..models import default_model` —— 插在 `lifecycle` 块与 `telemetry` 块之间（first-party 同层，ruff I001 一次干净，无 `--fix` 需要）。
- ✅ **保留**：`AgentConfig.model: str = default_model()` —— dataclass 字段默认值在类定义时求值，与 R46 `DEFAULT_MODEL = default_model()` / R47 / R49 `parent_model = default_model()` 同构（顶层/定义时求值模式）。
- ✅ **保留**：1 个不变量测试（`dataclasses.fields` 读字段 `default` 本身 + `AgentConfig().model` 默认构造双锁死 —— 防止退化为 `default_factory`）。
- ❌ **放弃**：无（本轮是 R49 YAGNI 边界明确点名的「core.py:200 单独一轮」，无替代方向）。

### 交付

- `agent/minimax_code/agent/core.py`（改）— (1) 导入块加 `from ..models import default_model`（54 行，`lifecycle` 块 `(...TurnStartInput,)` 之后、`telemetry` 块 `from ..telemetry.tracing import ...` 之前）；(2) `AgentConfig` dataclass 字段 `model: str = "MiniMax-M3"` → `model: str = default_model()` + 行内注释 `# R50: was "MiniMax-M3" literal, now from vocabulary`。**值不变**。
- `agent/tests/test_models.py`（改，276→326 行，19→20 测试）— 新增 `test_agent_config_model_default_from_vocabulary`：(1) `dataclasses.fields(AgentConfig)` 找 `model` 字段，断言 `.default == default_model()`（锁死字段默认值本身，非 `default_factory`）；(2) `AgentConfig().model == default_model()`（默认构造反映词汇表值）。docstring 标注这是 R46-R50 单来源迁移链路的**核心层 capstone**，退化到字面量即破坏此测试。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目（含里程碑标注）。

### 映射决策树（本轮纯消费端接线，无新类型/枚举）

本轮不引入新枚举/类型，是 R45 词汇表的**第五处消费端接线**（R46 storage / R47 llm / R48 CANDIDATE_MODELS / R49 resolution+runtime / R50 AgentConfig）。决策树四分支本轮无新增。**接线模式复用**：core.py 是 agent 包内模块，`AgentConfig.model` 字段默认值在类定义时求值 —— 与函数默认参数语义相同，故用顶层导入 + `default_model()` 直接作字段默认（R46/R47/R49-parent_model 同构的「定义时求值」模式，非 runtime 的懒加载）。循环风险评估：`models.py` 是零依赖叶（仅 stdlib+pydantic），`core.py` 已 first-party 导入 `..models` 同层（R47 的 `.llm` 已接），新增导入不触发循环。

**坑（自发现，已修复）**：无。`ruff check core.py tests/test_models.py` 首次即 **All checks passed!**（无需 `--fix`）。导入插入位置（lifecycle 与 telemetry 块之间）天然满足 I001 first-party 字母/层级排序。无运行时错误 —— 重点 pytest 65 测试一次通过（agent_core + models + completion + chat 全消费路径）。

### 验证

- `ruff check core.py tests/test_models.py` → **All checks passed!**（首次干净，无 `--fix`）。
- 重点 `pytest tests/test_agent_core.py tests/test_models.py tests/test_completion.py tests/test_chat.py -q` → **65 passed in 11.39s**（AgentConfig.model 核心字段全部消费路径零破坏）。
- `pytest tests/test_models.py -q` → **20 passed**（R49 的 19 + R50 的 1 新测试全过）。
- 完整套件 `pytest` → **1696 passed, 10 skipped in 104.26s**（R49 1695 → R50 1696，**+1 精确**，零回归）。

### YAGNI 边界

- ❌ **不迁移剩余 2 处散落 fallback** —— `completion_routes.py:121`（路由层）/ `app.py:404`（启动层），留 R51+ 逐轮消化。核心层（core.py）已闭环，剩余 2 处是边缘启动/路由路径，风险递减。
- ❌ **不推导 `MODEL_META` 从词汇表** —— 仍硬编码（`handlers_model.py:68`），需先决定 MiniMax 展示层策略（grok 无 context_window/name 结构化建模，是 MiniMax 前端独有需求）。
- ❌ **不扩展 `DefaultModelEntry` 建模展示元数据** —— grok serde entry 只读 `model`（R45 对等契约）；扩展破坏对等。
- ❌ **不改 migration SQL 种子**（`002_model_prefs.py` / `005_providers.py`）—— 已部署快照，不重跑。

### Commit

`feat(platform): R50 wire AgentConfig.model to model vocabulary (fuse grok xai-grok-models)`

---

## R51 — completion_routes 路由层 fallback 迁移至 R45 词汇表（融合 grok xai-grok-models 消费端）

> 锚定 R50（`5dd7629`）。

### 本轮目标

R50 闭环了核心层（`AgentConfig.model`）。本轮转向债务地图剩余 2 处之一 —— `agent/completion_routes.py:121` 的 `_build_llm_client` provider 分支 `model=model_name or "MiniMax-M3"`（路由层 fallback）。这是 inline code completion 路由（`POST /complete`，绕过 IPCServer 直调 MiniMaxClient 追求最低延迟）的模型 fallback。本轮迁移为 `model_name or default_model()`，值保持 `"MiniMax-M3"`。预研关键事实：`_build_llm_client` 已用**函数内懒加载导入**（`from ..app import get_db` / `ModelPrefsDAO` / `ProviderDAO`），其中 `from ..app import get_db` 触发 app.py 导入链（app.py 反导入 completion_routes），形成 cold-start 循环 —— 故 `default_model` 必须**同函数内懒加载**（跟随既有 cold-start-cycle-avoidance 模式，非顶层导入），与 R49 runtime 的懒加载策略同构（循环风险模块 → 函数内导入）。

### 融合结论

- ✅ **保留**：`_build_llm_client` 函数内懒加载 `from ..models import default_model`（在既有 3 个懒加载导入块中，ruff 重排后 `app → models → storage` 字母序）—— 跟随既有 cold-start-cycle-avoidance 模式。
- ✅ **保留**：`model=model_name or "MiniMax-M3"` → `model=model_name or default_model()` + 行内注释。**值不变**。
- ✅ **保留**：1 个不变量测试（monkeypatch DAO/provider 链 + MiniMaxClient，捕获 fallback + passthrough 双分支，锁死 `or` 语义）。
- ❌ **放弃**：无（本轮是 R50 YAGNI 边界明确点名的 completion_routes 迁移）。

### 交付

- `agent/minimax_code/agent/completion_routes.py`（改）— (1) `_build_llm_client` 函数内加 `from ..models import default_model` 懒加载（与 `get_db`/`ModelPrefsDAO`/`ProviderDAO` 同块）；(2) 122 行 `model=model_name or "MiniMax-M3"` → `model=model_name or default_model()`。**值不变**。ruff `--fix` 顺手清理 4 处 lint：1× I001（函数内导入块重排 app→models→storage）+ 3× UP037 预存冗余引号（31/32/95 行 `"fastapi.FastAPI"`/`"IPCServer"` —— `from __future__ import annotations` 下引号多余，去引号行为保持：注解 PEP 563 字符串化，运行时不求值）。
- `agent/tests/test_models.py`（改，326→~380 行，20→21 测试）— 新增 `test_build_llm_client_model_fallback_from_vocabulary`：monkeypatch `routes.MiniMaxClient`（捕获 kwargs）+ `app_mod.get_db`（返回非 None）+ `model_prefs.ModelPrefsDAO`（可控 `get()` 返回）+ `providers.ProviderDAO`（`get_active()` 返回 provider dict），驱动 provider 分支：Branch 1 `model_name=None` → `default_model()` / Branch 2 `model_name="explicit-model"` → passthrough。锁死 `or` 双语义。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

### 映射决策树（本轮纯消费端接线，无新类型/枚举）

本轮不引入新枚举/类型，是 R45 词汇表的**第六处消费端接线**（R46 storage / R47 llm / R48 CANDIDATE_MODELS / R49 resolution+runtime / R50 AgentConfig / R51 completion_routes）。决策树四分支本轮无新增。**接线模式复用**：completion_routes 的 `_build_llm_client` 已声明 cold-start 循环（`from ..app import get_db` 触发 app 反导 routes），故 `default_model` 用**函数内懒加载**（与 R49 runtime `_build_agent_core` 同模式：循环风险模块 → 函数内导入）。两种导入策略（顶层 vs 函数内）的选择依据始终是**消费模块的循环风险**，非任意 —— 本轮再次验证该决策树。

**坑（自发现，已修复）**：ruff 报 4 错（全在 completion_routes.py）—— 1× I001 我的导入块未排序（函数内连续导入块，ruff **确实检查**，R49 先例已验证）+ 3× UP037 预存引号。按 R48/R49 既定模式 + CLAUDE.md「修复正在编辑文件的 ruff 错误」授权，`--fix` 一次清理全部 4 个（行为保持），重检 `All checks passed!`。无运行时错误——重点 pytest 35 测试一次通过。

### 验证

- `ruff check completion_routes.py` → **All checks passed!**（`--fix` 修 I001 + UP037×3 后；test_models.py 首次干净）。
- 重点 `pytest tests/test_models.py tests/test_completion.py -q` → **35 passed in 2.76s**（test_models 21 含 R51 新增 + test_completion 14，completion 路由全消费路径零破坏）。
- `pytest tests/test_models.py -q` → **21 passed**（R50 的 20 + R51 的 1 新测试全过）。
- 完整套件 `pytest` → **1697 passed, 10 skipped in 108.72s**（R50 1696 → R51 1697，**+1 精确**，零回归）。

### YAGNI 边界

- ❌ **不迁移剩余 1 处散落 fallback** —— `app.py:404`（启动层两处：`pref.get("model_id", "MiniMax-M3")` + `else "MiniMax-M3"`），留 R52 最后一轮消化。
- ❌ **不推导 `MODEL_META` 从词汇表** —— 仍硬编码（`handlers_model.py:69`），需先决定 MiniMax 展示层策略（grok 无 context_window/name 结构化建模）。
- ❌ **不扩展 `DefaultModelEntry` 建模展示元数据** —— grok serde entry 只读 `model`（R45 对等契约）。
- ❌ **不改 migration SQL 种子**（`005_providers.py:44`）—— 已部署快照，不重跑。

### Commit

`feat(platform): R51 migrate completion_routes model fallback to vocabulary (fuse grok xai-grok-models)`

---

## R52 — app.py 启动层 fallback 迁移至 R45 词汇表（融合 grok xai-grok-models 消费端，债务地图清零）

> 锚定 R51（`e6fefc7`）。**本轮是第 7 处、也是最后一处散落 fallback 迁移 —— 迁移完成后「MiniMax-M3」字面量债务地图正式清零**。R46→R52 单来源迁移链路完整闭环：R46 storage → R47 llm → R48 candidate set → R49 resolver+runtime → R50 AgentConfig → R51 completion_routes → **R52 app 启动层（debt-map zeroed）**。

### 本轮目标

R51 消化了路由层（completion_routes）。本轮收尾债务地图**最后 1 个函数** —— `app.py:405` 的 `_rebuild_subagent_llm` 启动层 fallback，该单行内含**两个**「MiniMax-M3」文字出现：(1) dict-get 默认 `pref.get("model_id", "MiniMax-M3")`；(2) 非 dict 的 else 分支 `else "MiniMax-M3"`。这是**子 agent LLM 客户端重建**（`model.set_current` / `provider.*` 变更后调用）的模型 fallback，发生在 app 启动/热重建路径。本轮将两处合并迁移为 `default_model()`，值保持 `"MiniMax-M3"`。预研关键事实：`_rebuild_subagent_llm` 已用**函数内懒加载导入**（`from . import secrets` / `MiniMaxClient` / `ModelPrefsDAO` / `ProviderDAO`），其中 `from .agent.llm import MiniMaxClient` 触发 agent 包导入链（app.py 自身被 agent 包内多处导入）—— 故 `default_model` 必须**同函数内懒加载**（跟随既有 cold-start-cycle-avoidance 模式），与 R49 runtime / R51 completion_routes 的懒加载策略同构（循环风险模块 → 函数内导入）。

### 融合结论

- ✅ **保留**：`_rebuild_subagent_llm` 函数内懒加载 `from .models import default_model`（在既有 4 个懒加载导入块中，ruff 字母序 `.agent.llm` → `.models` → `.storage.dao.*`，首次执行即干净）—— 跟随既有 cold-start-cycle-avoidance 模式。
- ✅ **保留**：第 405 行 `pref.get("model_id", "MiniMax-M3") if isinstance(pref, dict) else "MiniMax-M3"` → `pref.get("model_id", default_model()) if isinstance(pref, dict) else default_model()` + 行内注释，**一处合并迁移两个文字出现**。**值不变**。
- ✅ **保留**：1 个不变量测试（monkeypatch secrets / MiniMaxClient / DAO 链，三分支全锁死：dict 无 model_id → default_model() / dict 有 model_id → passthrough / 非 dict None → default_model()）。
- ❌ **放弃**：无（本轮是 R51 YAGNI 边界明确点名的「app.py 启动层，留 R52 最后一轮消化」）。

### 交付

- `agent/minimax_code/app.py`（改）— (1) `_rebuild_subagent_llm` 函数内加 `from .models import default_model` 懒加载（与 `secrets`/`MiniMaxClient`/`ModelPrefsDAO`/`ProviderDAO` 同块，399 行）；(2) 405 行两个 `"MiniMax-M3"` → `default_model()`（dict-get 默认 + else 分支，单行合并）。**值不变**。ruff `app.py` 首次即 **All checks passed!**（字母序正确，无 `--fix` 需要）。
- `agent/tests/test_models.py`（改，21→22 测试）— 新增 `test_rebuild_subagent_llm_model_fallback_from_vocabulary`：monkeypatch `llm_mod.MiniMaxClient`（捕获 kwargs）+ `secrets_mod.get_provider_key` + `model_prefs.ModelPrefsDAO`（可控 `get_current()` 返回）+ `providers.ProviderDAO`（`get()` 返回非 None provider dict 触发捕获分支），驱动三分支：Branch 1 `pref={}` 无 model_id → `default_model()` / Branch 2 `pref={"model_id":"explicit-id"}` → passthrough / Branch 3 `pref=None` 非 dict → `default_model()`。锁死第 405 行 if/else 双分支 + dict.get 默认三路径。ruff `--fix` 顺手清理 1× I001（测试函数内导入块重排：`minimax_code` app/secrets 同包 → `minimax_code.agent` → `minimax_code.storage.dao` 字母序），重检 **All checks passed!**。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目（含债务清零标注）。

### 映射决策树（本轮纯消费端接线，无新类型/枚举）

本轮不引入新枚举/类型，是 R45 词汇表的**第七处、也是最后一处消费端接线**（R46 storage / R47 llm / R48 CANDIDATE_MODELS / R49 resolution+runtime / R50 AgentConfig / R51 completion_routes / R52 app 启动层）。决策树四分支本轮无新增。**接线模式复用**：app.py 的 `_rebuild_subagent_llm` 已声明 cold-start 循环（`from .agent.llm import MiniMaxClient` 触发 agent 包导入链，而 app.py 自身被 agent 包内多处导入），故 `default_model` 用**函数内懒加载**（与 R49 runtime / R51 completion_routes 同模式：循环风险模块 → 函数内导入）。**两种导入策略（顶层 vs 函数内）的选择依据始终是消费模块的循环风险，非任意** —— R46/R47/R48/R49-parent_model/R50 用顶层导入（叶模块或无冷启动循环），R49-runtime/R51/R52 用函数内懒加载（声明了 cold-start 循环）；本轮收尾后该决策树在该维度不再有新用例。

**坑（自发现，已修复）**：app.py 无 ruff 错（首次干净，字母序 `.agent.llm`/`.models`/`.storage.dao.*` 正确）；test_models.py ruff 报 1× I001（我的 R52 测试函数内导入块 4 行未排序）。按 R48-R51 既定模式 + CLAUDE.md「修复正在编辑文件的 ruff 错误」授权，`--fix` 一次清理（纯 import 排序，行为保持），重检 `All checks passed!`。无运行时错误 —— 完整套件零回归。

### 验证

- `ruff check app.py` → **All checks passed!**（首次干净，字母序正确）。
- `ruff check tests/test_models.py` → **All checks passed!**（`--fix` 修 I001×1 后）。
- 完整套件 `pytest` → **1698 passed, 10 skipped in 105.24s**（R51 1697 → R52 1698，**+1 精确**，零回归）。

### YAGNI 边界（债务清零总结）

**「MiniMax-M3」字面量债务地图自此清零** —— R46→R52 共迁移 **9 处文字出现 / 7 个函数位置**：R46 storage `DEFAULT_MODEL` / R47 llm `DEFAULT_MODEL` / R48 `CANDIDATE_MODELS` / R49 resolution `parent_model` + runtime `_build_agent_core` / R50 `AgentConfig.model` / R51 `_build_llm_client` / R52 `_rebuild_subagent_llm`（dict.get 默认 + else 双出现）。此后系统所有默认模型决策**单一来源**于 `models.DEFAULT_MODELS_JSON`（R45 词汇表），改一处全局传播。

- ❌ **不推导 `MODEL_META` 从词汇表** —— 仍硬编码（`handlers_model.py:69`），需先决定 MiniMax 展示层策略（grok 无 context_window/name 结构化建模，是 MiniMax 前端独有需求，非债务）。
- ❌ **不扩展 `DefaultModelEntry` 建模展示元数据** —— grok serde entry 只读 `model`（R45 对等契约）；扩展破坏对等。
- ❌ **不改 migration SQL 种子**（`002_model_prefs.py` / `005_providers.py:44`）—— 已部署快照，不重跑。
- ❌ **不动 R45 词汇表 JSON 源** / **handlers_model `MODEL_META` 显示层** / **migrations 002,005 SQL 种子** / **R46-R52 所有 `# Rxx: was "MiniMax-M3"` 接线注释** —— 均为合法非债务出现（数据源 / 显示层 / 已部署快照 / 审计轨迹）。

### Commit

`feat(platform): R52 migrate app.py subagent_llm fallback to vocabulary (fuse grok xai-grok-models)`

---

## R53 — 推理努力类型层 ReasoningEffort（融合 grok xai-grok-sampling-types，模型+推理双轴）

> 锚定 R52（`6149e7f`）。R45-R52 完成「模型 ID 词汇表」的建立 + 7 处消费端单来源接线（债务清零）。**本轮切换轨道 —— 从消费端接线回到新类型层融合**，引入 LLM 调用的第二大配置轴：**推理努力（reasoning effort）**。模型回答「用谁」，推理努力回答「想多深」—— 二者共同定义一次 LLM 调用的资源配置。源 crate `xai-grok-sampling-types`（grok 采样/补全 API 纯数据类型层，7 个源文件无 I/O），本轮聚焦其中自洽的 `ReasoningEffort` 子集（types.rs:762-1008）：枚举 + 方法 + 3 个常量 + 8 个纯元解析函数 + `ReasoningEffortOption` 结构体 + `humanize_effort_id`。与 MiniMax v0.3.0 的 thinking_count 通道天然配对（reasoning_effort 是控制 thinking 强度的配置轴）。

### 本轮目标

R52 债务清零后，本轮在 sampling-types crate 内选一个**自洽、高价值、无 I/O** 的类型子集做正向迁移。选择 `ReasoningEffort` 块（types.rs:762-1008）的理由：(1) 与 R45-R52 模型词汇表构成「模型 + 推理强度」两大配置维度，认知对称；(2) 与 v0.3.0 thinking_count 通道天然配对；(3) 纯数据类型无 I/O（完美匹配 crate "no I/O" 设计）；(4) pydantic 友好的 serde 对等（serde rename_all lowercase → StrEnum；untagged Bare/Full → before+after 双验证器）。本轮交付**新模块** `agent/minimax_code/agent/reasoning.py`（纯类型层，无 I/O，无接线，不被任何现有模块导入 —— 零循环风险，零回归面），完整映射 grok 的 ReasoningEffort 枚举（6 变体，serde lowercase，Medium 默认）、`max` CLI 别名（仅 token 解析器接受）、Anthropic Messages API 方言（none/minimal 省略，xhigh→"max"）、8 个纯元解析函数（absent/wrong-type/unknown 三态坍缩为 None + 跳过无效数组项 + warn）、`ReasoningEffortOption`（untagged Bare/Full 反序列化）。

### 融合结论

- ✅ **保留**：`ReasoningEffort(StrEnum)` —— 6 变体（none/minimal/low/medium/high/xhigh），serde `rename_all="lowercase"` 对等（StrEnum 成员值即小写 wire token，pydantic `model_dump(mode="json")` 输出小写）。**遵循项目 StrEnum 惯例**（compaction/hooks/plan_mode/resilience/telemetry/lifecycle/fsnotify/agent-reliability 共 11 处 StrEnum，零处 `(str, Enum)`）。
- ✅ **保留**：`default()`→MEDIUM（grok `#[default]`）、`as_str()`→value（grok `as_str`）、`to_messages_api()`（grok `to_messages_api`：none/minimal→None，xhigh→"max"，其余 passthrough）。**移除冗余 `__str__` 重写**（StrEnum 默认 str() 已返回 value == as_str()，KISS + 项目惯例一致）。
- ✅ **保留**：`parse_effort_token`（grok `parse_canonical_effort_token`，max→XHIGH，unknown→None，不 raise）/ `parse_effort_strict`（grok `FromStr`，max→XHIGH，invalid→ValueError 消息含有效 token 列表）。**max 别名仅 token 解析器接受** —— 与 grok 的「serde rename 拒绝 max / FromStr 接受 max」双重语义对等。
- ✅ **保留**：3 个常量（`REASONING_EFFORT_META_KEY`/`SUPPORTS_REASONING_EFFORT_META_KEY`/`REASONING_EFFORTS_META_KEY`）+ 8 个纯元解析函数（supports / parse_reasoning_effort_meta / reasoning_effort_meta_value / parse_reasoning_effort_options[带 `# noqa: BLE001`] / parse_reasoning_efforts_meta / reasoning_efforts_meta_value）—— 三态坍缩（absent/wrong-type/unknown→None）+ 跳过无效数组项 + warn，前向兼容（新 tier 的未知变体不覆盖已持久化的用户偏好）。
- ✅ **保留**：`ReasoningEffortOption(BaseModel)` —— `model_config=ConfigDict(extra="ignore")`，字段 `value: ReasoningEffort`（必填）/ `id`/`label`/`description`/`default`；`@model_validator(mode="before") _accept_bare`（裸字符串 → 完整字典，走 parse_effort_strict 接受 max）+ `@model_validator(mode="after") _fill_defaults`（id 默认 value.as_str()，label 默认 humanize(id)）—— 复制 grok untagged Bare/Full 反序列化 + `unwrap_or` 默认回填。
- ❌ **放弃**：`to_responses_api` / `from_responses_api`（grok 用于 async-openai Responses API 转换）—— MiniMax 侧无 Python async-openai 对等物（YAGNI；v0.3.0 用 thinking_count 通道，非 Responses API）。

### 交付

- `agent/minimax_code/agent/reasoning.py`（新，291 行）— 完整 ReasoningEffort 类型层：模块 docstring（grok crate 行号锚定）+ 3 常量 + `_MAX_ALIAS`/`_VALID_EFFORTS` 私有 + `ReasoningEffort(StrEnum)`（6 变体 + default/as_str/to_messages_api）+ `parse_effort_token`/`parse_effort_strict` + `_humanize_effort_id` + 6 个元解析纯函数 + `ReasoningEffortOption(BaseModel)`（before+after 双验证器）+ 2 个 list 序列化函数。**无 I/O，无被导入**（零回归面）。
- `agent/tests/test_reasoning.py`（新，31 测试）— 15 个段落覆盖：meta keys 常量 / 枚举值+wire / default+as_str / to_messages_api（none+minimal→None，xhigh→"max"，中间 passthrough）/ str / **StrEnum serde 对等**（model_dump mode=json 输出小写 token）/ parse_effort_token（canonical+大小写+max 别名+unknown→None）/ parse_effort_strict（canonical+max+invalid→ValueError 消息含 token 列表）/ supports_reasoning_effort_meta（True/False/缺失/非 bool）/ parse_reasoning_effort_meta（canonical/缺失/非字符串/未知）/ reasoning_effort_meta_value / ReasoningEffortOption 裸字符串（derives id+label + max 别名）/ 完整表（仅 value 回填 + 显式 id/label/description/default passthrough + extra ignore）/ **对象 value 拒绝 max**（grok serde rename 对等，bare 接受 vs object 拒绝）/ parse_reasoning_effort_options（跳过无效保序）/ parse_reasoning_efforts_meta（数组/缺失/非数组/空/全无效→None）/ reasoning_efforts_meta_value（JSON 原生列表）。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

### 映射决策树（本轮新枚举 + 新未标记联合）

本轮是**新类型层迁移**（非消费端接线），决策树两分支落地：

- **分支 (e) 带 serde 小写有线格式的枚举 → `str` 枚举**：grok `ReasoningEffort` serde `rename_all="lowercase"` → Python **`StrEnum`**（非 `(str, Enum)`）。关键洞察：grok serde Deserialize 在 object 上下文拒绝 "max"，但 `FromStr` 接受 "max"→Xhigh（仅 CLI）。Python StrEnum 天然复制此双重语义 —— pydantic 强制 `ReasoningEffort` 字段接受枚举值（none..xhigh）但拒绝 "max"（enum 值集合不含 max），而裸字符串路径走 `parse_effort_strict`（接受 max）。**无需特殊分支**，str-enum 语义即对等。**项目惯例修正**：首版我写 `(str, Enum)`（决策树字面分支 (e)），ruff UP042 报错；grep 确认项目 **11 处全用 StrEnum**（compaction/hooks/plan_mode/resilience/telemetry/lifecycle/fsnotify/agent-reliability），零处 `(str, Enum)` —— 改 StrEnum 回归项目惯例（Python 3.11+ 现代等价，`requires-python>=3.11`/`target-version=py311` 支持）。
- **分支 (f) 未标记的 Bare/Full 反序列化 → pydantic BaseModel + `before`(裸字符串) + `after`(填充默认值) 双重验证器**：`ReasoningEffortOption` 的 `@model_validator(mode="before") _accept_bare`（裸字符串 → 完整字典，复用 parse_effort_strict）+ `@model_validator(mode="after") _fill_defaults`（id/label 默认回填），精确复制 grok `RawReasoningEffortOption` 的 untagged `Bare`/`Full` 反序列化 + `unwrap_or` 默认。**extra="ignore"** 复制 grok serde「忽略未知字段」。

**坑（自发现，已修复）**：首版 reasoning.py ruff 报 2 错：(1) **UP035** `from typing import Mapping` → 应 `from collections.abc import Mapping`（Python 3.9+ 弃用 typing.Mapping）；(2) **UP042** `class ReasoningEffort(str, Enum)` → 应 `StrEnum`。两个都是 UP 现代化规则，CLAUDE.md「修复正在编辑文件的 ruff 错误」授权内。修复：UP035 改导入源（typing 只留 Any），UP042 改 StrEnum 并移除冗余 `__str__` 重写（StrEnum 默认 str()==value）。重检 `All checks passed!`，31 测试无回归（StrEnum 默认行为与重写的 `__str__` 等价）。

### 验证

- `ruff check minimax_code/agent/reasoning.py tests/test_reasoning.py` → **All checks passed!**（首版 2× UP 错，修 UP035+UP042 后干净）。
- `pytest tests/test_reasoning.py` → **31 passed in 1.56s**（修 UP042 前后均 31 passed，StrEnum 改造零回归）。
- 完整套件 `pytest` → **1729 passed, 10 skipped in 104.96s**（R52 1698 → R53 1729，**+31 精确**，零回归，10 skip 与 R52 一致）。

### YAGNI 边界

- ❌ **不实现 `to_responses_api`/`from_responses_api`** —— grok 用于 async-openai Responses API；MiniMax 侧无 Python 对等物（v0.3.0 走 thinking_count 通道）。新增即死代码。
- ❌ **不接线 `reasoning.py` 到任何现有模块** —— 本轮是**类型层**（纯数据 + 解析），消费者端接线（类比 R45→R46 两阶段模式）留后续轮次。提前接线 = 在无消费者时绑定 API，违反 YAGNI。
- ❌ **不迁移 sampling-types crate 的其余类型**（ChatCompletionRequest/SamplingConfig/ApiBackend/Role/ToolChoice/Usage/SearchParameters/Compaction* 等 types.rs 64-1135）—— 本轮聚焦自洽的 ReasoningEffort 块；其余类型规模大且需先决定 ChatCompletion wire 契约，留独立轮次。
- ❌ **不引入 `humanize_effort_id` 的公开别名** —— grok `humanize_effort_id` 是私有用例（label 默认回填），保持 `_` 前缀私有。
- ❌ **不建模 `ReasoningEffortOption.description` 为非 Optional** —— grok 是 `Option<String>`，Optional 对等。

### Commit

`feat(platform): R53 reasoning effort type layer (fuse grok xai-grok-sampling-types)`

---

## R54 — reasoning_effort 管道传输（融合 grok xai-grok-sampling-types 消费端，模型+推理双轴接线）

> 锚定 R53（`086e521`）。R53 建立了 `ReasoningEffort` 纯类型层（R45 对应物）。**本轮是 R53 的消费端接线**（R46 对应物）—— 两阶段迁移模式（R45→R46 类型层→接线）在推理努力轴上的重演。R53 的类型层不被任何模块导入（零回归面），本轮把它**正向接到 LLM 传输管道**：reasoning_effort 从 `MiniMaxClient.stream_chat/chat` 流到 transport 层，被 R54 新增的 `coerce_effort` 运行时规范化器标准化，记录到 3 个 transport 的 `last_reasoning_effort` 观察属性 + `MiniMaxClient.last_reasoning_effort` 公开属性。**精确边界 = 管道传输（pipe-through），不注入 wire**：3 个 transport 接收 reasoning_effort kwarg 但**不发送任何线路信号**（MiniMax/xAI/OpenAI 三方 effort wire 契约未定，盲目注入 = 不可接受的回归），默认 None = 零线路字节变更 = 零行为回归。这是 R46 式的「单点接线、零线路影响」。

### 本轮目标

R53 类型层已就位但无消费者（YAGNI：提前接线 = 在无消费者时绑定 API）。本轮在**不破坏任何现有 LLM 调用**的前提下，铺设 reasoning_effort 的端到端管道，为后续轮次（R55+ 线路注入、AgentConfig.reasoning_effort → AgentCore → stream_chat 调用点）提供可观察、可测试的接缝。**核心设计决策：管道传输而非线路注入**。预研识别致命风险 —— 三方 effort wire 契约各异且未定：(1) OpenAI 官方 `reasoning_effort` 仅接受 minimal/low/medium/high（拒绝 none/xhigh/max，会抛 SDK/API 错误）；(2) Anthropic 用 `thinking: {type, budget_tokens}` 机制而非 effort 字段；(3) MiniMax/智谱端点 effort 字段支持未知。**盲目注入任一 = 破坏现有调用 = 不可接受**。故 R54 仅做：接收 → 规范化 → 记录 → 观察，**线路注入推迟到 wire 契约确认后**。新增运行时规范化器 `coerce_effort`（R53 的 parse seam 的运行时入口），3 个 transport 的 stream_chat 加 kwarg + 规范化记录 + 属性，MiniMaxClient stream_chat/chat 透传 + 同步属性。

### 融合结论

- ✅ **保留**：新增 `coerce_effort(value: ReasoningEffort | str | None) -> ReasoningEffort | None` —— transport 调用点的运行时规范化器。三源坍缩：`None`→`None`（不发送，默认）/ `ReasoningEffort`→自身（已规范化，**typed 分支优先于 str 解析**，因 StrEnum 成员也是 str 实例）/ `str`→`parse_effort_token`（大小写不敏感，max→XHIGH，unknown→None 不 raise，typo 降级为「不发送」而非崩溃回合）。镜像 grok 宽松输入面（CLI 同时接受 typed enum 和 max 别名字符串），保持 wire 层严格枚举边界 —— **coerce_effort 是 parse seam（R54 调用），`to_messages_api` 是 emit seam（R54 不调用，留 R55+）**。
- ✅ **保留**：`LLMTransport.stream_chat` ABC 签名加 `reasoning_effort: ReasoningEffort | str | None = None` kwarg（TYPE_CHECKING 导入 ReasoningEffort，仅注解）+ docstring 说明 R54 管道传输不注入 wire。
- ✅ **保留**：3 个 transport（Mock/Anthropic/OpenAI）的 `stream_chat` 加同 kwarg + 正文开头 `self._last_reasoning_effort = coerce_effort(reasoning_effort)` + `__init__` 加 `self._last_reasoning_effort` 存储 + `last_reasoning_effort` 只读属性。**3 个 transport 必须都接受该 kwarg**（MiniMaxClient 多态调用 `self._transport.stream_chat(..., reasoning_effort=...)`，缺一即 TypeError）。每个 transport 的属性 docstring 各自点名其 wire 契约的未定点（Mock：纯观察；Anthropic：thinking budget vs output_config.effort；OpenAI：官方字段拒绝 none/xhigh/max + 兼容端点可能整体拒绝）。
- ✅ **保留**：`MiniMaxClient` TYPE_CHECKING 导入 ReasoningEffort + `__init__` 加 `self._last_reasoning_effort` 存储 + `stream_chat`/`chat` 加 kwarg + 透传给 transport + stream 结束后 `self._last_reasoning_effort = self._transport.last_reasoning_effort`（镜像既有 thinking_count 同步模式）+ 公开只读属性 `last_reasoning_effort`。
- ❌ **放弃**：**不在任一 transport 注入 wire 信号** —— 留明确 TODO 锚点（Anthropic：thinking budget/output_config.effort 待定；OpenAI：kwargs["reasoning_effort"] 条件设置待定）。**默认 None = 零线路字节变更**，所有现有调用零感知。
- ❌ **放弃**：不连接 AgentConfig.reasoning_effort → AgentCore → stream_chat 调用点（端到端管道的「上游」）—— 留 R55+，本轮仅做 client→transport 半段（下游）。

### 交付

- `agent/minimax_code/agent/reasoning.py`（改）— 在 `parse_effort_strict` 之后、`_humanize_effort_id` 之前新增 `coerce_effort`（27 行，含完整 docstring 说明三源坍缩语义 + parse/emit seam 双接缝定位）。复用 R53 既有 `parse_effort_token`（不重复解析逻辑，DRY）。
- `agent/minimax_code/agent/transports/__init__.py`（改）— (1) `from typing import Any` → `from typing import TYPE_CHECKING, Any`；(2) `from ..types import StreamChunk` 后加 `if TYPE_CHECKING: from ..reasoning import ReasoningEffort`（仅注解导入）；(3) `LLMTransport.stream_chat` ABC 签名加 `reasoning_effort` kwarg + docstring R54 边界说明（不注入 wire，默认 None 字节不变）。
- `agent/minimax_code/agent/transports/mock_transport.py`（改）— 导入 `from ..reasoning import ReasoningEffort, coerce_effort`；`__init__` 加 `self._last_reasoning_effort`；`last_reasoning_effort` 属性（纯观察，mock 不发 wire）；`stream_chat` 加 kwarg + 正文 `self._thinking_count = 1` 后规范化记录。
- `agent/minimax_code/agent/transports/anthropic_transport.py`（改）— 导入加 `ReasoningEffort, coerce_effort`；`__init__` 加 `self._last_reasoning_effort`；`last_reasoning_effort` 属性（docstring 点名 thinking budget vs output_config.effort 待定）；`stream_chat` 加 kwarg + 正文 `self._thinking_count = 0` 后规范化记录 + TODO 注释（emit via thinking budget / output_config.effort 待 wire 契约确认）。**`client.messages.stream(...)` kwargs 与 R54 前字节一致**。
- `agent/minimax_code/agent/transports/openai_transport.py`（改）— 导入加 `ReasoningEffort, coerce_effort`；`__init__` 加 `self._last_reasoning_effort`；`last_reasoning_effort` 属性（docstring 点名官方字段拒绝 none/xhigh/max + 兼容端点可能整体拒绝）；`stream_chat` 加 kwarg + 正文 `self._thinking_count = 0` 后规范化记录 + TODO 注释（kwargs["reasoning_effort"] 条件设置待确认端点接受性 + 变量子集）。**kwargs 构建逻辑与 R54 前一致**。
- `agent/minimax_code/agent/llm.py`（改）— TYPE_CHECKING 导入 ReasoningEffort；`MiniMaxClient.__init__` 加 `self._last_reasoning_effort`；`stream_chat` 加 kwarg + 透传 + 流后同步 `self._last_reasoning_effort = self._transport.last_reasoning_effort`（与 thinking_count 同步对称）；`chat` 加 kwarg + 透传给 stream_chat（单一代码路径）；公开只读属性 `last_reasoning_effort`（docstring 说明 None = 零行为变更 + 观察接缝）。
- `agent/tests/test_reasoning.py`（改，31→35 测试）— 在 `parse_effort_strict` 段落后新增 coerce_effort 段落（4 测试）：none passthrough / typed enum passthrough（锁 StrEnum 也是 str → typed 分支优先序）/ string 解析含 max 别名 / unknown string → None。
- `agent/tests/test_reasoning_wiring.py`（新，10 测试）— 端到端管道测试，两层：(a) MockTransport 直接（max→XHIGH / 默认 None / typed 透传 / turbo→None）；(b) MiniMaxClient(mock) 公开面（"high"→HIGH / 默认 None 零行为变更 / typed MEDIUM 透传 / "max"→XHIGH / chat() 非流式路径同样透传 "xhigh"→XHIGH / chat() 默认 None）。聚焦管道传输契约（非 wire 发射），coerce_effort 本身在 test_reasoning 已单测，此处证其**被接线**。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

### 映射决策树（本轮纯消费端接线 + 一个新运行时函数）

本轮是 R53 类型层的**第一处消费端接线**（类比 R45→R46 两阶段模式的接线轮）。决策树四分支本轮无新增枚举/联合 —— `coerce_effort` 是纯函数（非类型），复用 R53 既有的 `ReasoningEffort` 枚举 + `parse_effort_token` 解析器。**接线模式复用**：transport 委托架构（`_build_transport` 按 protocol 选实现，`stream_chat`/`chat` 委托 `self._transport.stream_chat`，`thinking_count` 从 transport 同步）—— R54 的 `reasoning_effort` + `last_reasoning_effort` 完全镜像既有 `thinking_count` 同步模式（流后 `self._x = self._transport.x`），认知对称。**ABC 参数兼容性**：`@abstractmethod` 具体实现可加带默认值的额外 kwarg，但 MiniMaxClient 多态调用要求**所有 3 个 transport 都接受**该 kwarg（不能只改 Mock）—— 故 4 文件（ABC + 3 transport）同步修改。

**坑（自发现，已修复）**：无。8 个编辑步骤全部 `ruff check` 首次即 **All checks passed!**（无需 `--fix`）—— 导入插入位置天然满足 I001（`..reasoning` 在 `..types` 前/后的字母序，各 transport 一致）；TYPE_CHECKING 块格式遵循既有惯例。无运行时错误 —— 新测试 14 个一次通过（10 wiring + 4 coerce_effort），完整套件零回归。

### 验证

- `ruff check` R54 全部 8 文件（reasoning.py / llm.py / transports/__init__.py + mock/anthropic/openai + 2 测试）→ **All checks passed!**（首次干净，无 `--fix`）。
- `pytest tests/test_reasoning.py tests/test_reasoning_wiring.py -q` → **45 passed in 5.04s**（R53 的 35 含 R54 新增 4 coerce_effort + R54 新增 10 wiring）。
- 完整套件 `pytest` → **1743 passed, 10 skipped in 109.18s**（R53 1729 → R54 1743，**+14 精确**，零回归，10 skip 与 R53 一致）。

### YAGNI 边界

- ❌ **不注入任何 wire 信号** —— 三方 effort wire 契约未定（OpenAI 拒绝 none/xhigh/max；Anthropic 用 thinking budget 而非 effort；MiniMax/智谱未知），盲目注入破坏现有调用。每个 transport 留明确 TODO 锚点，待 R55+ 契约确认后逐 transport 注入。本轮默认 None = 零线路字节 = 零回归。
- ❌ **不连接上游 AgentConfig.reasoning_effort → AgentCore → stream_chat 调用点** —— 本轮仅做 client→transport 下游半段；上游配置流入留 R55+（端到端管道完成轮）。
- ❌ **不修改 to_messages_api 的调用点** —— R53 的 emit seam 本轮不被调用（线路注入推迟）；coerce_effort 是 parse seam 的运行时入口，两者分工明确，不在无 wire 消费者时调用 emit。
- ❌ **不给 reasoning_effort 加 MiniMaxClient 构造期默认值** —— 仅 per-call kwarg（默认 None）；构造期默认 = 隐式全局策略，应由 AgentConfig 显式表达（R55+）。
- ❌ **不迁移 sampling-types crate 其余类型** —— 继续 R53 的聚焦策略，本轮只接 ReasoningEffort 的下游管道。

### Commit

`feat(platform): R54 reasoning_effort pipe-through (fuse grok xai-grok-sampling-types)`

---

## R55 — reasoning_effort 上游接线（融合 grok xai-grok-sampling-types 配置端，双轴汇合）

> 锚定 R54（`44a7212`）。R54 铺设了 reasoning_effort 的**下游半段**（client → transport：coerce + 记录 + 观察属性）。**本轮是 R54 的上游半段** —— 把 reasoning_effort 从单一配置源 `AgentConfig` 流经 `AgentCore._stream_turn` 接到 `self.llm.stream_chat()` 调用点。至此 R53（类型层）+ R54（下游 client→transport）+ R55（上游 config→core→client）= **完整端到端 reasoning_effort 管道**，仍是 pipe-through-only（不注入 wire，待 R56+ 三方契约确认）。**双轴汇合**：`AgentConfig.model`（R50，谁回答）+ `AgentConfig.reasoning_effort`（R55，思考多深）现在在同一个 dataclass 相邻并列，从配置到调用点全程对称流动 —— 一次 LLM 调用的两个配置轴首次在 AgentConfig 层统一表达，镜像 grok sampling-types 里 model + reasoning_effort 在 ChatCompletionRequest 里的并列关系。

### 本轮目标

R54 下游半段已就位（client.stream_chat 接受 reasoning_effort），但**没有配置入口**（YAGNI：管道有出口无入口 = 半截管）。本轮在**零行为回归**前提下闭合上游：AgentConfig 新增 `reasoning_effort` 字段（默认 None），AgentCore._stream_turn 在 stream_chat 调用点透传 `self.config.reasoning_effort`。**核心设计决策**：与 R54 完全对称的 pipe-through 边界 —— 字段是被动载体，默认 None = 零线路字节变更 = 所有现有回合字节级一致。**双轴设计**：reasoning_effort 字段紧邻 R50 的 model 字段（同一 AgentConfig，同一调用点），形成「谁回答 + 思考多深」的配置双轴。

### 融合结论

- ✅ **保留**：`AgentConfig.reasoning_effort: ReasoningEffort | str | None = None` —— 新增配置字段，紧邻 `model` 字段之后。接受 R54 client/transport 同样的宽松输入面（typed enum | wire-token str | None），运行时规范化由 transport 的 coerce_effort（R54）负责。默认 None = pipe-through-only，无 wire 发射。docstring 明确双轴定位（model = who answers，reasoning_effort = how deeply）+ R54 coerce 依赖 + 零回归保证。
- ✅ **保留**：`AgentCore._stream_turn` 的 `self.llm.stream_chat(...)` 调用点加 `reasoning_effort=self.config.reasoning_effort` kwarg（紧邻 temperature 之后）。透传配置到 client，transport（R54）coerce + 记录但不发射。默认 None 路径与 R55 前字节一致。
- ✅ **保留**：core.py TYPE_CHECKING 导入 `ReasoningEffort`（仅注解，运行时零开销）—— core.py 有 `from __future__ import annotations`（类型注解字符串化），镜像 llm.py（R54）/ transports/__init__.py（R54）的 TYPE_CHECKING 模式。
- ✅ **保留**：5 个测试 FakeLLM 的 stream_chat 签名对齐 `reasoning_effort: Any = None` —— AgentCore 多态调用点新增 kwarg 后，任何**显式参数列表**的测试替身会 TypeError。grep agent/tests 后定位 5 个类别 A FakeLLM（显式参数，需对齐）vs 6 个类别 B（`**kwargs`/`**_`，自动吸收）。5 个对齐是 R55 的**直接必要成本**（非「修预存债务」），因这些 FakeLLM 是 MiniMaxClient 的测试替身，偏离完整签名。
- ❌ **放弃**：**不注入任何 wire 信号** —— R55 仅闭合上游配置流入，wire 注入仍由 R54 的 transport 层 TODO 锚点持有，待 R56+ 三方契约确认。默认 None = 零线路字节 = 零回归。
- ❌ **放弃**：**不通过 IPC 暴露 reasoning_effort**（agent.send_message / agent.* handlers）—— 前端按会话设置 effort 留后续轮次（需 IPC 契约变更三同步：docs/ipc-contract.md + web/src/types/ipc.ts + protocol.py）。本轮聚焦 AgentCore 内部管道闭合。
- ❌ **放弃**：**不给 AgentConfig.reasoning_effort 加构造期非 None 默认** —— 仅 None 默认；构造期默认 = 隐式全局策略，应由显式配置/IPC 提供。

### 交付

- `agent/minimax_code/agent/core.py`（改，4 处）— (1) `from typing import Any` → `from typing import TYPE_CHECKING, Any`；(2) `from .types import LLMStreamTimeout` 后加 `if TYPE_CHECKING: from .reasoning import ReasoningEffort`（仅注解导入，docstring 说明 R55 wiring + R54 coerce 依赖 + 零回归）；(3) AgentConfig `model` 字段后、`max_iterations` 前加 `reasoning_effort: ReasoningEffort | str | None = None`（docstring 双轴定位 + R54 coerce + 默认 None 字节不变）；(4) `_stream_turn` 的 stream_chat 调用点 temperature 后加 `reasoning_effort=self.config.reasoning_effort`（注释 R54 coerce + None 字节不变）。
- `agent/tests/test_reasoning_wiring.py`（改，2 处）— (1) 导入块加 `AgentConfig, AgentCore`（from core）+ `ToolRegistry`（from tools），按字母序插入；(2) 文件末尾追加「Upstream half: AgentConfig.reasoning_effort → AgentCore → MiniMaxClient (R55)」段落 + 4 个端到端测试：bare string "high"→HIGH / 默认 None 零回归 / typed LOW 透传 / "max"→XHIGH 别名端到端。用真实 `MiniMaxClient(mock=True)` + 直接 `await core._stream_turn(_PING)`（最轻量验证 config→core→client 接缝）。
- `agent/tests/test_chat.py`（改，1 处）— FakeLLM.stream_chat 签名 temperature 后加 `reasoning_effort: Any = None`（# R55 注释）。
- `agent/tests/test_agent_core.py`（改，2 处，replace_all）— 2 个 FakeLLM stream_chat 签名同上对齐。
- `agent/tests/test_thinking_count.py`（改，2 处，replace_all）— 2 个 stream_chat 签名（_RecordingLLM + _Fake）同上对齐。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

### 映射决策树（本轮上游半段接线 + FakeLLM 兼容性预判）

本轮是 R54 下游半段的**上游对称**（双阶段管道的下半截）。决策树无新增枚举/联合 —— 复用 R53 ReasoningEffort + R54 coerce_effort。**接线模式对称**：R54 在 client→transport 接缝加 kwarg，R55 在 config→core→client 接缝加同 kwarg，两端用同一份 coerce_effort 规范化（单一规范化点 = DRY）。**双轴并列**：AgentConfig.reasoning_effort 紧邻 model 字段，_stream_turn 调用点 reasoning_effort 紧邻 model/temperature，认知对称。**TYPE_CHECKING 对称**：core.py / llm.py / transports/__init__.py 三处都用 TYPE_CHECKING 导入 ReasoningEffort（均因 `from __future__ import annotations`，类型字符串化，运行时零开销）。

**坑（自发现，已预判修复）**：**FakeLLM TypeError 预判** —— 在 _stream_turn 调用点加 `reasoning_effort=...` kwarg **之前**，先 grep 了 agent/tests 所有 `def stream_chat`，识别两类替身：类别 A（显式参数列表：test_chat/test_agent_core/test_thinking_count 共 5 处）会因未知 kwarg TypeError；类别 B（`**kwargs`/`**_`：6 处）自动吸收。结论：5 个类别 A 对齐是 R55 的**直接必要成本**（这些 FakeLLM 是 MiniMaxClient 替身，签名偏离真实 stream_chat 完整面）。用 `reasoning_effort: Any = None`（匹配既有 `tool_choice: Any = None` 风格，无需额外导入，ruff 对测试 `Any` 宽容）。orchestrator/subagent.py 不定义 stream_chat（用真实 MiniMaxClient，不受影响）。**结果**：5 处对齐后，受影响测试子集 88 passed，全套件零回归 —— 预判正确，无运行时意外。

**预存债务（非本轮引入，按轮次独立性保留）**：test_chat.py 报 I001（import 块）+ F401（unused `uuid`/`AgentConfig`）+ B010（6 处 setattr）；test_thinking_count.py 报 I001（import 块）。这些**全部是 R55 前预存债务**（R55 仅加 `reasoning_effort: Any = None` 一行，未触及 import 块或 setattr），R54 提交（44a7212）时即存在。R55 改动本身 ruff 干净（无新错误）。按 CLAUDE.md「轮次独立性：不修复不相关预存 lint」原则保留 —— 扩大改动修这些会模糊 R55 的「上游接线」清晰边界，违背 YAGNI。

### 验证

- `ruff check` R55 改动的 5 文件（core.py + 4 测试）→ **R55 新增代码零 ruff 错误**（5 文件 check 报的 10 错全部是 test_chat/test_thinking_count 的预存 I001/F401/B010，非 R55 引入；core.py / test_reasoning_wiring.py / test_agent_core.py 干净）。
- `pytest tests/test_reasoning_wiring.py tests/test_reasoning.py tests/test_agent_core.py tests/test_thinking_count.py tests/test_chat.py -q` → **88 passed in 19.08s**（含 R55 新增 4 端到端测试 + R54 既有 10 wiring + 全部 FakeLLM 对齐后零 TypeError）。
- 完整套件 `pytest` → **1747 passed, 10 skipped in 106.05s**（R54 1743 → R55 1747，**+4 精确**，零回归，10 skip 与 R54 一致）。

### YAGNI 边界

- ❌ **不注入 wire 信号** —— R55 仅闭合上游配置流入；wire 注入仍由 R54 transport 层 TODO 持有，待 R56+ 三方契约确认（OpenAI 拒绝 none/xhigh/max；Anthropic thinking budget；MiniMax/智谱未知）。默认 None = 零线路字节 = 零回归。
- ❌ **不通过 IPC 暴露 reasoning_effort** —— 前端按会话设置 effort 需 IPC 契约三同步（docs + types + protocol），留独立轮次。本轮聚焦 AgentCore 内部管道闭合。
- ❌ **不加 AgentConfig.reasoning_effort 构造期非 None 默认** —— 仅 None；构造期默认 = 隐式全局策略，应由显式配置/IPC 提供。
- ❌ **不迁移 sampling-types crate 其余类型** —— 继续 R53/R54 聚焦策略，本轮只闭合 ReasoningEffort 的上游管道。
- ❌ **不修复 test_chat/test_thinking_count 的预存 ruff 债务**（I001/F401/B010）—— 非本轮引入，按轮次独立性保留，避免模糊 R55 边界。

### Commit

`feat(platform): R55 reasoning_effort upstream wiring (fuse grok xai-grok-sampling-types)`

## R56 — reasoning_effort 首次真实 wire 发射（融合 grok xai-grok-sampling-types OpenAI 发送接口，pipe-through 闭环）

> 锚定 R55（`f458b20`）。R53（类型层）+ R54（下游 client→transport coerce/record）+ R55（上游 config→core→client）铺设了 reasoning_effort 的完整 pipe-through 管道，但**三阶段始终未发射任何 wire 信号** —— transport 只 coerce + 记录到 `last_reasoning_effort` 观察属性，wire 注入由 R54 两个 transport TODO 锚点持有。**本轮是管道的首个真实 wire 发射**：为 OpenAI-compatible transport（grok/xAI 的原生合约 —— grok-build 整个 sampling-types crate 就是为 xAI OpenAI 兼容端点服务）闭合「记录但不发射」循环。新增 `to_openai_effort_token()` emit seam（reasoning.py，与 R53 的 Anthropic 发送接口 `to_messages_api` 对称），openai_transport.stream_chat 在 kwargs 中有条件注入 `reasoning_effort` 字段。**双 emit seam 设计落地**：Anthropic 发送接口（R53，搁置待 R57）+ OpenAI 发送接口（R56，本轮发射）—— 两者不同是因为 wire 合约不同（OpenAI 字段接受 minimal/low/medium/high，拒绝 none/xhigh/max；Anthropic 接受 max 但丢弃 minimal）。

### 本轮目标

R55 闭合上游后，reasoning_effort 已从 `AgentConfig` 流到 transport 的 `last_reasoning_effort`，但**从未到达 wire**（YAGNI：管道有完整路径却没有出口 = 半截管）。本轮在**零行为回归**前提下发射首个 wire 信号：选 OpenAI transport（而非 Anthropic）是因为 grok-build 是 xAI = OpenAI 兼容，OpenAI wire 是 grok 的原生合约 —— 最直接的融合实现。**核心设计决策**：双 emit seam —— `to_messages_api()`（Anthropic，R53 已建但未调用）对称地新增 `to_openai_effort_token()`（OpenAI，本轮调用）；两者的语义分歧点（MINIMAL：Anthropic 丢弃 vs OpenAI 保留；XHIGH：Anthropic→"max" vs OpenAI→"high" 降级）是**有意的**，因为各自 wire 合约的最优保真不同。**零回归保证链**：默认 `AgentConfig.reasoning_effort=None` → `coerce_effort(None)=None` → 调用点 `is not None` 保护短路 → `to_openai_effort_token` 从不被调用 → kwargs 不获得 `reasoning_effort` key → 字节级与 R55 前请求一致。

### 融合结论

- ✅ **保留**：`ReasoningEffort.to_openai_effort_token()` —— OpenAI chat-completions `reasoning_effort` token 发送接口，与 R53 的 `to_messages_api()`（Anthropic 发送接口）对称。语义映射：`None` 变体→`None`（不注入，字段无 none 层级）；`Minimal`→`"minimal"`（**保留**，与 Anthropic 发送接口丢弃 minimal 不同 —— OpenAI 字段接受 minimal 作为最低层级）；`Xhigh`→`"high"`（**降级**，与 Anthropic 发送接口→"max" 不同 —— OpenAI 最高层级是 high，xhigh 映射到最接近的可支持 token = 尽力而为语义保真，比丢弃信号更好）；`Low`/`Medium`/`High`→原值（pass-through）。docstring 明确双发送接口对称 + 三处语义分歧点 + 零回归保证。
- ✅ **保留**：`openai_transport.stream_chat` kwargs 注入 —— 在 `max_tokens` 条件之后、`try:` 之前注入 `_effort_token`（`self._last_reasoning_effort.to_openai_effort_token()` if `is not None` else None），仅在 token 非 None 时设置 `kwargs["reasoning_effort"]`。注释说明 emit seam 丢弃 none / 降级 xhigh，None effort（AgentConfig 默认）让 kwargs 字节不变。
- ✅ **保留**：`last_reasoning_effort` property docstring 更新 —— 移除 R54 的「Nothing is emitted yet」措辞，改为「R54 记录强制转换后的值；R56 通过 OpenAI `reasoning_effort` 字段在 wire 上发送它」。
- ✅ **保留**：8 个新测试 —— 3 个 reasoning.py emit-seam 单元测试（NONE 省略 / XHIGH 降级 high / MINIMAL 保留 + 中间变体 pass-through）+ 5 个 test_reasoning_wiring.py 端到端 wire emission 测试（通过 `_KwargsCapturingClient` fake 捕获 `chat.completions.create(**kwargs)` 的 kwargs 字典，断言默认零回归无 key / HIGH→"high" / XHIGH→"high" 降级 / MINIMAL→"minimal" 保留 / NONE 变体无注入）。
- ❌ **放弃**：**不发射 Anthropic thinking-budget wire** —— 仍由 R54 `anthropic_transport.py` 第 427-428 行 TODO 锚点持有（通过 `thinking={"type":"enabled","budget_tokens":N}` 或 `output_config.effort` 发射），待 R57。本轮聚焦 OpenAI（grok 原生合约）。
- ❌ **放弃**：**不通过 IPC 暴露 reasoning_effort**（agent.send_message 参数）—— 前端按会话设置 effort 需 IPC 契约三同步（docs/ipc-contract.md + web/src/types/ipc.ts + protocol.py），留独立轮次。
- ❌ **放弃**：**不迁移 sampling-types crate 其余类型**（ChatCompletionRequest/SamplingConfig/ToolChoice/Role/Usage）—— 继续 R53/R54/R55/R56 聚焦策略，本轮只闭合 ReasoningEffort 的 wire 发射。

### 交付

- `agent/minimax_code/agent/reasoning.py`（改，1 处）— 在 `to_messages_api()` 之后（第 94 行之后）、`parse_effort_token` 之前加 `to_openai_effort_token` 方法。docstring 明确：OpenAI 发送接口 = Anthropic 发送接口的对应物；两者不同因 wire 合约不同；三处语义分歧点（NONE→None / MINIMAL→保留 / XHIGH→降级）逐条列出 + 与 `to_messages_api` 的对比。
- `agent/minimax_code/agent/transports/openai_transport.py`（改，3 处）— (1) `last_reasoning_effort` property docstring 更新（移除「Nothing is emitted yet」，改为 R54 记录 + R56 wire 发射）；(2) R54 TODO 注释升级为 R56 注释（「R54 coerce + R56 emit: normalise the runtime effort once, up-front, then read it back below to emit the OpenAI token」）；(3) `max_tokens` 条件后、`try:` 前注入 wire 发射块（`_effort_token` 计算 + 非 None 时设置 `kwargs["reasoning_effort"]`）。
- `agent/tests/test_reasoning.py`（改，1 处）— 在 `test_effort_to_messages_api_xhigh_maps_to_max` 之后、`test_effort_str_is_wire_token` 之前加 3 个 emit-seam 单元测试（NONE 省略 / XHIGH 降级 high / MINIMAL 保留 + LOW/MEDIUM/HIGH pass-through）。
- `agent/tests/test_reasoning_wiring.py`（改，导入块 + 末尾追加）— (1) 导入块加 `SimpleNamespace`（types）+ `Any`（typing）+ `pytest` + `OpenAITransport`（transports.openai_transport）；(2) 文件末尾追加「OpenAI transport wire emission (R56)」段落：`_EmptyStream`（空异步迭代器，测试只查 kwargs 不查 chunk）+ `_KwargsCapturingClient` fake（嵌套 `_Completions.create(**kwargs)` 捕获 kwargs）+ `openai_transport` pytest fixture（返回 `(transport, fake)` 元组，monkeypatch `app.ensure_breaker_registry = lambda: None` 使熔断器 fail-open）+ 5 个端到端测试。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

### 映射决策树（本轮 OpenAI wire 发射 + 双 emit seam 设计 + kwargs 捕获测试模式）

本轮是 R53/R54/R55 三阶段 pipe-through 管道的**出口闭合**（首个真实 wire 发射）。决策树无新增枚举 —— 复用 R53 ReasoningEffort + R54 coerce_effort + R54 `last_reasoning_effort` 观察属性。**新增的是 emit seam**：`to_openai_effort_token()` 与 R53 的 `to_messages_api()` 形成**双发送接口**，各自服务一个 wire 合约。**双 emit seam 的语义分歧点矩阵**：

| 变体 | `to_messages_api`（Anthropic，R53） | `to_openai_effort_token`（OpenAI，R56） | 分歧原因 |
|---|---|---|---|
| `NONE` | `None`（省略） | `None`（省略） | 一致 —— 两端都无 none 层级 |
| `MINIMAL` | `None`（省略） | `"minimal"`（**保留**） | OpenAI 字段接受 minimal 为最低层级；Anthropic 丢弃 |
| `LOW`/`MEDIUM`/`HIGH` | 原值 | 原值 | 一致 —— pass-through |
| `XHIGH` | `"max"` | `"high"`（**降级**） | Anthropic 有 max 层级；OpenAI 最高是 high，降级保语义 |

**坑 1（自发现，已预判修复）**：**kwargs 捕获测试模式** —— 断言 wire 发射需要看到 `chat.completions.create(**kwargs)` 收到的确切 kwargs 字典。镜像 `test_transport_breaker.py` 的 `_FakeClient`/`_FakeChat`/`_FakeCompletions.create(**kwargs)` 模式（第 154-198 行），特化为 kwargs 捕获：`_KwargsCapturingClient` 嵌套 `_Completions.create` 把 `dict(kwargs)` 存到 `outer.captured_kwargs`。pytest fixture `openai_transport` 返回 `(transport, fake)` 元组 + monkeypatch `app.ensure_breaker_registry = lambda: None`（熔断器 fail-open：`resolve_breaker("llm:openai")` 返回 None → `check_or_raise` 无操作）。测试解构 `transport, fake = openai_transport` 后断言 `fake.captured_kwargs`。**结果**：5 个端到端测试一次通过，预判正确。

**坑 2（自发现）**：**MINIMAL 语义分歧是双 emit seam 的关键测试点** —— 必须显式断言 OpenAI 保留 `"minimal"`（`test_effort_to_openai_token_minimal_kept_unlike_anthropic` + `test_openai_transport_minimal_emits_minimal`），否则两个 seam 的差异会模糊成「都 pass-through」。XHIGH 降级同理（`test_effort_to_openai_token_xhigh_degrades_to_high` + `test_openai_transport_xhigh_degrades_to_high`）。这两个分歧点是双 emit seam 存在的**全部理由** —— 若两端映射相同，一个发送接口就够（DRY），无需双 seam。

**预存债务（非本轮引入，按轮次独立性保留）**：完整套件 `test_hunks_types.py::test_hunk_value_equality` 偶发失败 —— 两个 `_make_hunk()` 调用的 `created_at` datetime 微秒漂移（~1ms，440447 vs 441449）。**隔离重跑 `pytest tests/test_hunks_types.py::test_hunk_value_equality` → 1 passed in 0.11s**，确认是 time-sensitive flake（R39 xai-hunk-tracker 预存债务：`_make_hunk()` 两次调用 `datetime.now()`，亚毫秒漂移致 datetime 不等）。**R56 未触及任何 hunk 代码**（reasoning.py / openai_transport.py / 2 个 reasoning 测试文件，零 hunk 相关）。按 CLAUDE.md「轮次独立性：不修复不相关预存 lint/错误」原则保留 —— 在 R56 修 R39 的 time-sensitive 测试会模糊本轮「wire 发射」清晰边界，违背 YAGNI。

### 验证

- `ruff check` R56 改动的 4 文件（reasoning.py + openai_transport.py + test_reasoning.py + test_reasoning_wiring.py）→ **All checks passed!**（R56 新增代码零 ruff 错误）。
- `pytest tests/test_reasoning.py tests/test_reasoning_wiring.py tests/test_transport_breaker.py -q` → **73 passed**（含 R56 新增 8 测试：3 emit-seam 单元 + 5 wire emission 端到端；test_transport_breaker 作为 kwargs 捕获模式参考零回归）。
- 完整套件 `pytest` → **1754 passed, 10 skipped, 1 failed**（R55 1747 → R56 1755 总数 = 1754 通过 + 1 flaky；**+8 精确**，10 skip 与 R55 一致；唯一失败 = `test_hunks_types.py::test_hunk_value_equality`，R39 预存 flake，隔离重跑 1 passed，非 R56 引入）。

### YAGNI 边界

- ❌ **不发射 Anthropic thinking-budget wire** —— 仍由 R54 `anthropic_transport.py` TODO 持有（待 R57，通过 `thinking={"type":"enabled","budget_tokens":N}` 或 `output_config.effort`）。本轮聚焦 OpenAI（grok 原生合约）。
- ❌ **不通过 IPC 暴露 reasoning_effort** —— 前端按会话设置 effort 需 IPC 契约三同步（docs + types + protocol），留独立轮次。
- ❌ **不修复 test_hunks_types 的预存 flake**（created_at 微秒漂移）—— R39 预存 time-sensitive 债务，非 R56 引入，按轮次独立性保留。
- ❌ **不迁移 sampling-types crate 其余类型**（ChatCompletionRequest/SamplingConfig/ToolChoice/Role/Usage）—— 继续聚焦 ReasoningEffort。
- ❌ **不给 reasoning_effort 加构造期非 None 默认** —— 仅 None；构造期默认 = 隐式全局策略，应由显式配置/IPC 提供。
- ❌ **不合并双 emit seam 为单函数** —— `to_messages_api` 与 `to_openai_effort_token` 的语义分歧（MINIMAL/XHIGH）是各自 wire 合约的最优保真，合并会丢失保真度或引入 wire 错误。

### Commit

`feat(platform): R56 OpenAI transport reasoning_effort wire emission (fuse grok xai-grok-sampling-types)`

## R57 — reasoning_effort 双 emit seam 的 Anthropic 半边闭合（融合 grok xai-grok-sampling-types Anthropic 发送接口，双 seam 对称闭环）

> 锚定 R56（`57db9c2`）。R53 定义了 Anthropic 发送接口 `to_messages_api()` 但**从未调用**（搁置待「Anthropic wire 合约落定」）；R56 为 OpenAI 闭合了首个真实 wire 发射（`to_openai_effort_token()`），双 emit seam 的 OpenAI 半边落地。**本轮闭合 Anthropic 半边**：`anthropic_transport.stream_chat` 通过官方 `output_config.effort` 参数（SDK 0.105.2 一级参数，非 `extra_body` hack）有条件注入 effort token。至此 R53（类型层）+ R54（下游 coerce/record）+ R55（上游 config→core→client）+ R56（OpenAI wire）+ R57（Anthropic wire）= reasoning_effort 从 `AgentConfig` 到双协议 wire 的完整双向闭环，**双 emit seam 对称闭环**：两 seam 的语义分歧（MINIMAL：Anthropic 丢弃 vs OpenAI 保留；XHIGH：Anthropic→"max" vs OpenAI→"high"）各自是其 wire 合约的最优保真。

### 本轮目标

R56 发射了 OpenAI wire，但 Anthropic transport 仍只 coerce + 记录（R54 TODO 锚点持有）。本轮在**零行为回归**前提下闭合 Anthropic wire 发射：选官方 `output_config.effort` 参数（`inspect.signature(messages.stream)` 实证为一级参数 + `OutputConfigParam.effort` Literal 是 `low/medium/high/xhigh/max`，与 `to_messages_api()` 输出完美对齐）而非 `extra_body` hack。**核心设计决策**：双 emit seam 的语义分歧是**有意的** —— Anthropic 接受 `xhigh`+`max`（XHIGH→"max" 保留最高层级），OpenAI 最高是 `high`（XHIGH→"high" 降级）；Anthropic 无 `minimal` 层级（MINIMAL→None 丢弃），OpenAI 接受 `minimal`（MINIMAL→"minimal" 保留）。**零回归保证链**：默认 `AgentConfig.reasoning_effort=None` → `coerce_effort(None)=None` → `_effort_token` 由 `is not None` 保护为 None → `output_config=anthropic.NOT_GIVEN` → 字节级与 R56 前请求一致。

### 融合结论

- ✅ **保留**：`anthropic_transport.stream_chat` 的 `output_config` 注入 —— 在 `_ensure_client()` 之后、`try:` 之前计算 `_effort_token`（`self._last_reasoning_effort.to_messages_api()` if `is not None` else None），在 `client.messages.stream(...)` 参数列表注入 `output_config=({"effort": _effort_token} if token is not None else anthropic.NOT_GIVEN)`。注释说明 emit seam 丢弃 none/minimal、XHIGH→"max"，None effort（默认）→ NOT_GIVEN 字节不变。
- ✅ **保留**：`last_reasoning_effort` property docstring 更新 —— 移除 R54 的「Nothing is emitted yet」/TODO，改为「R54 记录强制转换后的值；R57 通过 Anthropic `output_config.effort` 字段在 wire 上发送它」。
- ✅ **保留**：R54 TODO 注释升级为「R54 coerce + R57 emit」注释（与 R56 OpenAI transport 对称）。
- ✅ **保留**：`to_messages_api()` docstring 增强 —— 加 SDK 实证（`OutputConfigParam.effort` Literal = low/medium/high/xhigh/max）+ 与 `to_openai_effort_token` 的对称文档（逐条列出双 seam 的三处语义分歧点）。函数体不变（R53 已正确实现）。
- ✅ **保留**：5 个新测试 —— test_reasoning_wiring.py 的「Anthropic transport wire emission (R57)」段落：`_AnthropicFakeStream`（async context manager + async iterator，模拟 `messages.stream` 返回的 ACM）+ `_AnthropicKwargsCapturingClient` fake（嵌套 `_Messages.stream(**kwargs)` 捕获 kwargs）+ `anthropic_transport` fixture（monkeypatch breaker fail-open）+ 5 测试（默认 NOT_GIVEN / HIGH→"high" / XHIGH→"max" 关键分歧 / MINIMAL→NOT_GIVEN 关键分歧 / NONE 变体→NOT_GIVEN）。
- ✅ **保留（环境修复，非代码）**：`uv sync --extra dev` 把 pytest/pytest-asyncio/ruff 装入 venv —— 修复 R1-R56 一直潜伏的双环境债务（详见坑 1）。
- ❌ **放弃**：**不通过 IPC 暴露 reasoning_effort** —— 前端按会话设置 effort 需 IPC 契约三同步（docs + types + protocol），留独立轮次。
- ❌ **放弃**：**不迁移 sampling-types crate 其余类型**（ChatCompletionRequest/SamplingConfig/ToolChoice/Role/Usage）—— 继续聚焦 ReasoningEffort。
- ❌ **放弃**：**不给 R57 代码做 SDK 版本兼容**（检测 output_config 是否在签名再 fallback 到 extra_body）—— 生产/开发环境用 venv 0.105.2（支持 output_config），版本兼容会掩盖环境债务且违背「用官方参数」设计。

### 交付

- `agent/minimax_code/agent/reasoning.py`（改，1 处）— `to_messages_api()` docstring 增强（加 SDK 实证 + 双 seam 对称文档），函数体不变。
- `agent/minimax_code/agent/transports/anthropic_transport.py`（改，3 处）— (1) `last_reasoning_effort` property docstring（移除 TODO，声明 R57 wire 发射）；(2) R54 TODO 注释升级为「R54 coerce + R57 emit」；(3) `try:` 前注入 `_effort_token` 计算 + `messages.stream(...)` 参数列表加 `output_config`（NOT_GIVEN / `{"effort": token}` 三元）。
- `agent/tests/test_reasoning_wiring.py`（改，导入块 + 末尾追加）— (1) 导入块加 `import anthropic` + `AnthropicTransport`；(2) 文件末尾追加「Anthropic transport wire emission (R57)」段落（fake stream ACM + kwargs 捕获 client + fixture + 5 测试）。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

### 映射决策树（本轮 Anthropic wire 发射 + 双 emit seam 对称闭环 + 双环境债务破案）

本轮是 R53-R56 管道的**第二出口闭合**（Anthropic wire 发射），双 emit seam 对称闭环。**双 emit seam 完整分歧矩阵**（R56 表 + R57 列实测）：

| 变体 | `to_messages_api`（Anthropic，R53/R57 发射） | `to_openai_effort_token`（OpenAI，R56 发射） | 分歧原因 |
|---|---|---|---|
| `NONE` | `None` → NOT_GIVEN | `None`（省略） | 一致 —— 两端都无 none 层级 |
| `MINIMAL` | `None` → NOT_GIVEN（**丢弃**） | `"minimal"`（保留） | Anthropic 无 minimal 层级；OpenAI 接受 |
| `LOW`/`MEDIUM`/`HIGH` | 原值 → `{"effort": 原值}` | 原值 | 一致 —— pass-through |
| `XHIGH` | `"max"` → `{"effort": "max"}`（**保留最高**） | `"high"`（降级） | Anthropic 有 max 层级；OpenAI 最高是 high |

**坑 1（本轮最大破案，预先存在债务被 R57 暴露并修复）**：**`uv run pytest` 双环境陷阱**。R57 代码 + ruff 全过 + 目标套件 62 passed，但完整套件 3 个 `test_agent_core` 测试失败，报 `TypeError: AsyncMessages.stream() got an unexpected keyword argument 'output_config'`。**三轮排查的矛盾**：(a) 裸 `uv run python -c` 调 `inspect.signature(AsyncMessages.stream)` → 版本 **0.105.2** @ `.venv\...`，参数**包含 `output_config`**；(b) 完全模拟测试环境（MockTransport + max_retries + async with + output_config dict/NOT_GIVEN）→ 全部 ENTERED ok；(c) 但 pytest 同一 venv 同一测试报 unexpected kwarg。**破案探针**：写临时 `test_probe_anthropic.py` 在 pytest 下 `import anthropic; inspect.signature(...)` → 版本 **0.75.0** @ `C:\Users\...\Programs\Python\Python312\Lib\site-packages\anthropic\`（**系统 Python！**），参数**无 output_config**。**根因**：`pytest` 声明在 `[project.optional-dependencies] dev`，但 CLAUDE.md 前置环境只写 `uv sync`（无 `--extra dev`）→ **venv 从未安装 pytest** → `uv run pytest` fallback 到**系统全局 Python**（装了 pytest + 旧 anthropic 0.75.0）。**R1-R56 一直在系统 Python 跑测试**，因不碰 output_config 所以一直绿；R57 第一次需要 0.105.2 的参数就炸。**修复**：`uv sync --extra dev` 装 pytest 9.0.3 + pytest-asyncio 1.4.0 + ruff 0.15.15 到 venv → `uv run pytest` 用 venv python（0.105.2）→ **1760 全绿**。**双铁证**：(1) 插件列表从 12 个（`langsmith`/`logfire`/`seleniumbase` 等系统插件）→ 2 个（`anyio`/`asyncio` 纯净 venv）；(2) `uv.lock` **未变**（dev deps 早已锁定，只是没 sync 到 venv）—— 环境修复纯粹是 venv 本地装包，零代码/零 lock 污染。

**坑 2（自发现，已预判修复）**：**Anthropic fake stream 是 async context manager 而非 awaitable**。OpenAI 的 `chat.completions.create(**kwargs)` 返回 awaitable（R56 `_KwargsCapturingClient._Completions.create` 是 async def）；但 Anthropic 的 `client.messages.stream(...)` 返回 **async context manager**（`async with ... as stream:`），且 stream 本身是 async iterator。R57 的 `_AnthropicFakeStream` 必须同时实现 `__aenter__`/`__aexit__`（ACM 协议）+ `__aiter__`/`__anext__`（迭代协议），`_AnthropicKwargsCapturingClient._Messages.stream` 是**同步方法**返回 fake stream（kwargs 在调用时捕获，不等 `__aenter__`）。**结果**：5 个端到端测试一次通过，ACM 协议预判正确。

**预存债务（R57 已修复）**：R39 `test_hunks_types.py::test_hunk_value_equality` 的 created_at 微秒漂移 flake —— 在系统 Python 环境（R56）偶发失败，**venv 环境（R57）稳定通过**（1760 passed, 0 failed）。推测系统 Python 的 datetime 精度/调度行为与 venv 不同，venv 环境更稳定。此债务随双环境修复一并消解，无需单独处理。

### 验证

- `ruff check` R57 改动的 3 文件（reasoning.py + anthropic_transport.py + test_reasoning_wiring.py）→ **All checks passed!**
- `pytest tests/test_probe_anthropic.py tests/test_reasoning_wiring.py + 3 个原失败 test_agent_core` → **28 passed**（探针确认 venv 0.105.2 + R57 新增 5 + 原 3 失败现在全过；探针即删）。
- 完整套件 `pytest` → **1760 passed, 10 skipped, 0 failed**（R56 1754+1flake → R57 1760，**+5 精确**为 R57 新增；**0 failed** —— R39 flake 在 venv 环境稳定通过，双环境修复的额外红利）。

### YAGNI 边界

- ❌ **不通过 IPC 暴露 reasoning_effort** —— 前端按会话设置 effort 需 IPC 契约三同步（docs + types + protocol），留独立轮次（R58 候选）。
- ❌ **不迁移 sampling-types crate 其余类型**（ChatCompletionRequest/SamplingConfig/ToolChoice/Role/Usage）—— 继续聚焦 ReasoningEffort。
- ❌ **不给 R57 做 SDK 版本兼容**（output_config 签名检测 + extra_body fallback）—— 生产/开发用 venv 0.105.2，兼容会掩盖刚修复的环境债务。
- ❌ **不在 R57 单独修 R39 hunk flake** —— 随双环境修复已自然消解（venv 下稳定通过），无需单独 commit。
- ❌ **不合并双 emit seam 为单函数** —— `to_messages_api` 与 `to_openai_effort_token` 的语义分歧（MINIMAL/XHIGH）是各自 wire 合约的最优保真（与 R56 一致）。
- ❌ **不更新 CLAUDE.md 的 `uv sync` → `uv sync --extra dev`** —— 文档变更留独立提交（避免与 R57 代码 commit 混淆；当前 ITERATION_LOG 已充分记录此坑）。

### Commit

`feat(platform): R57 Anthropic transport reasoning_effort wire emission (fuse grok xai-grok-sampling-types)`

## R58 — reasoning_effort 元数据读取器的首个真实消费者（融合 grok xai-grok-sampling-types catalog-read 流，model.list 驱动前端 effort 选择器）

> 锚定 R57（`04e6157`）。R53-R57 闭合了 reasoning_effort 从 `AgentConfig` 到双协议 wire 的**写入管道**（类型层 → 下游 coerce/record → 上游 config→core→client → OpenAI wire → Anthropic wire）。但 R53 还埋了**另一组孤儿**：五个模型元数据读取器（`supports_reasoning_effort_meta` / `parse_reasoning_effort_meta` / `parse_reasoning_efforts_meta` / `reasoning_effort_meta_value` / `reasoning_efforts_meta_value`）—— 定义后被单元测试覆盖，但**从未被生产代码调用**。**本轮把第一个消费者接上**：`enrich_model_reasoning_meta` 纯函数把模型目录条目的原始 `reasoningEffort` / `reasoningEfforts` / `supportsReasoningEffort` 元数据（grok 的 per-model catalog 词汇表，`xai-grok-sampling-types` 在 catalog seam 消费）读取为规范化的 snake_case 字段，注入 `model.list` IPC 响应，让前端无需每个调用者重解析 meta 即可渲染一个 effort 选择器。至此 reasoning_effort 的**读取半边**（catalog → IPC 呈现）与写入半边（config → wire）对称起步。

### 本轮目标

R53 的元数据读取器定义了**如何从模型字典里安全地读出 reasoning_effort 元数据**（absent / wrong-type / unknown-variant 三种坏值都坍缩到单一 fallback 路径），但只活在单元测试里。本轮给它们接上第一个真实消费者：`model.list` 响应里的每个模型条目，凡声明了 reasoning_effort 元数据的，都附带规范化字段（`supports_reasoning_effort` / `reasoning_effort_default` / `reasoning_effort_options`），供前端渲染 effort 选择器。**核心设计决策**：(1) **模型字典即元数据容器**——flat 字段，非 nested `meta` sub-object（匹配 `ProviderDAO.list_models()` 如何扁平化每个 provider 的存储 model JSON）；(2) **零回归**——附件**仅在对应 meta 存在且可解析时**添加，未声明 reasoning_effort 元数据的模型不获得任何新 key（浅拷贝，与 pre-R58 dict 的 keys 字节相同）；(3) **snake_case 命名**——匹配现有 `model.list` 响应字段（`provider_id` / `protocol` 约定）；(4) **catalog 存规范层级**——catalog `reasoningEffort: "max"` → reader 解析为 XHIGH → 增强的默认值是规范化的 `"xhigh"`（emit seam 在 wire 上的 `to_messages_api` 映射是独立的层，由 R53/R57 持有）。

### 融合结论

- ✅ **保留**：`enrich_model_reasoning_meta` 纯函数 —— 在 `reasoning_efforts_meta_value` 之后（reasoning.py 末尾）。读取模型字典的原始 `reasoningEffort` / `reasoningEfforts` / `supportsReasoningEffort` 元数据，附件 `supports_reasoning_effort`（True，仅当 `supports_reasoning_effort_meta` 读到 truthy）/ `reasoning_effort_default`（规范 wire token，仅当 `parse_reasoning_effort_meta` 解析到已知层级）/ `reasoning_effort_options`（菜单列表，仅当 `parse_reasoning_efforts_meta` 产出非空列表）。返回输入的**浅拷贝**（从不原地修改）。
- ✅ **保留**：`handle_model_list` 集成 —— 在 `prov_dao.list_models()` 之后、`try:` 块内，用列表推导 `[enrich_model_reasoning_meta(m) for m in models]` 增强每个模型。注释说明「R53 元数据读取器的首个消费者；未声明元数据的模型零回归（无新 key）」。
- ✅ **保留**：`handlers_model.py` 导入 —— `from ..agent.reasoning import enrich_model_reasoning_meta`（第 39 行）。
- ✅ **保留**：9 个 enrich 纯函数测试（test_reasoning.py 末尾）—— 空模型 / 无 meta 透传 / 不修改输入 / 仅 supports 标志 / 默认 effort 规范+max 别名 / 未知忽略 / 选项菜单规范化 / 空/无效忽略 / 完整 meta（三字段全附）。
- ✅ **保留**：2 个 handler 集成测试（test_model.py TestModelIPC）—— `test_list_enriches_reasoning_effort_meta`（用 `ProviderDAO.create(name="xAI", protocol="anthropic", base_url="https://api.x.ai", models=[{...grok-1 with reasoningEffort meta}])` 种入，断言 `model.list` 返回的 grok-1 条目带 `supports_reasoning_effort=True` / `reasoning_effort_default="high"` / `reasoning_effort_options` 值 == ["low","medium","high"]）+ `test_list_without_reasoning_meta_is_zero_regression`（断言 MiniMax-M3 不获取三新 key）。
- ✅ **保留（环境修复，非本轮引入债务）**：`ruff check --fix tests/test_model.py` 自动修复 5 个**预先存在** lint（I001 import 块未排序 + 4× B010 `setattr` 常量属性）。详见坑 2。
- ❌ **放弃**：**不同步前端 `web/src/types/ipc.ts` 模型类型** —— 加可选 `supports_reasoning_effort` / `reasoning_effort_default` / `reasoning_effort_options` 字段需 IPC 契约同步（types + docs + mock backend），留 R59。
- ❌ **放弃**：**不同步 `docs/ipc-contract.md`** —— 同上，留独立轮次。
- ❌ **放弃**：**不迁移 sampling-types crate 其余类型**（ChatCompletionRequest/SamplingConfig/ToolChoice/Role/Usage）—— 继续聚焦 ReasoningEffort。

### 交付

- `agent/minimax_code/agent/reasoning.py`（改，1 处）— 在 `reasoning_efforts_meta_value` 之后、文件末尾加 `enrich_model_reasoning_meta(model: Mapping[str, Any]) -> dict[str, Any]`。docstring 明确：首个消费者、模型字典即 meta 容器（flat 字段）、附件仅在 meta 存在且可解析时添加（零回归）、snake_case 命名匹配 `provider_id`/`protocol`、返回浅拷贝（不原地修改）。
- `agent/minimax_code/ipc/handlers_model.py`（改，2 处）— (1) 第 39 行导入 `from ..agent.reasoning import enrich_model_reasoning_meta`；(2) `handle_model_list` 在 `models = await prov_dao.list_models()` 之后、try 块内加 `models = [enrich_model_reasoning_meta(m) for m in models]`（含 R53 元数据读取器首个消费者注释）。
- `agent/tests/test_reasoning.py`（改，1 处）— 在 `test_reasoning_efforts_meta_value_emits_json_native_list` 之后、文件末尾追加 9 个 enrich 纯函数测试。
- `agent/tests/test_model.py`（改，2 处）— (1) `ruff --fix` 自动修 5 个预先存在 lint（I001 import 排序 + 4× B010 setattr→赋值，运行时等价，项目用 ruff 不用 mypy）；(2) TestModelIPC 在 `test_error_code_is_invalid_params` 之后加 2 个 R58 handler 集成测试（enrich + zero-regression），含 R53 元数据读取器首个消费者的锚点注释。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

### 映射决策树（本轮 catalog-read 首个消费者 + 模型字典即元数据容器 + 零回归附件策略 + max 别名分层）

本轮是 R53 元数据读取器组的**第一个真实消费者**。决策树无新增枚举 —— 复用 R53 的五个读取器。**新增的是 catalog-read 消费模式**：模型字典本身即 meta 容器（flat 字段），读取器直接吃 `Mapping[str, Any]`，附件策略是「仅当存在且可解析」。

**R53 孤立读取器 → R58 消费者矩阵**：

| R53 读取器（孤立，单元测试覆盖） | R58 消费点（首个生产调用） | 附件字段 |
|---|---|---|
| `supports_reasoning_effort_meta(model)` | `if supports_reasoning_effort_meta(model):` | `supports_reasoning_effort: True` |
| `parse_reasoning_effort_meta(model)` | `default_effort = parse_reasoning_effort_meta(model); if default_effort is not None:` | `reasoning_effort_default: reasoning_effort_meta_value(...)` |
| `parse_reasoning_efforts_meta(model)` | `options = parse_reasoning_efforts_meta(model); if options is not None:` | `reasoning_effort_options: reasoning_efforts_meta_value(...)` |
| `reasoning_effort_meta_value(effort)` | 序列化 default 为 wire token | 内联于上 |
| `reasoning_efforts_meta_value(opts)` | 序列化 options 菜单为 JSON-native dict 列表 | 内联于上 |

**坑 1（自发现，已预判修复）**：**模型字典即元数据容器（flat 字段，非 nested meta sub-object）**。第一直觉可能是把元数据读成 `model["meta"]["reasoningEffort"]`（nested），但 `ProviderDAO.list_models()` 把每个 provider 的存储 model JSON 直接 `dict(m)` 扁平化 + 注入 `provider_id`/`provider_name`/`provider`/`protocol`。所以 `reasoningEffort`/`reasoningEfforts`/`supportsReasoningEffort` 直接在 model dict 顶层。`enrich_model_reasoning_meta` 的签名 `model: Mapping[str, Any]` 直接读顶层 key，匹配 grok 的 catalog-read flow（raw meta 留在 model dict 作 source of truth，规范化字段是 derived view）。**预判正确**：`test_list_enriches_reasoning_effort_meta` 用 `ProviderDAO.create(models=[{...flat reasoningEffort keys}])` 种入，`model.list` 直接读到顶层 key。

**坑 2（自发现，预先存在 lint）**：**test_model.py 的 5 个预先存在 lint 被 R58 编辑暴露**。R58 在 TestModelIPC 加了 2 个集成测试，ruff 重新扫描整个 test_model.py 暴露了 R58 之前就存在的 5 个 lint：I001（import 块未排序/未格式化，第 18 行）+ 4× B010（`setattr(client.server, "_model_prefs_dao", prefs_dao)` 等常量属性 setattr，第 197-200 行的 `_make_client_with_model_handlers`）。**全部 `[*]` 可自动修复**：`ruff check --fix` 把 I001 的 import 块重排（加空行分组：future / stdlib / third-party / first-party），把 B010 的 `setattr(server, "_xxx", val)` 改为 `server._xxx = val`（运行时等价——项目用 ruff 不用 mypy，无类型检查会因 IPCServer 无该属性而报错）。**风险评估**：原作者用 setattr 是为绕过 mypy/IDE 的属性存在性检查；既然项目无 mypy，赋值等价。`IPCServer` 若有 `__slots__` 会炸——但 setattr 在 `__slots__` 下同样炸（Python 不区分），所以赋值不引入新风险。**验证**：`ruff --fix` 后 `All checks passed!`，`pytest tests/test_model.py` 全过（setattr→赋值未破坏 DAO 注入）。按轮次独立性，**仅修正在编辑的文件（test_model.py）的 lint**，不触碰其他文件的预先存在 lint。

**坑 3（自发现，已预判修复）**：**catalog `max` 别名 → 增强默认值是规范化 `"xhigh"`，非 wire token `"max"`**。catalog 里 `reasoningEffort: "max"` 是 grok 的 CLI/UX 别名（`parse_effort_token` 把 `"max"` 解析为 `XHIGH`）。`enrich_model_reasoning_meta` 读到它后，`reasoning_effort_meta_value(XHIGH)` = `XHIGH.as_str()` = `"xhigh"`（规范 wire token），**不是** `"max"`。这是**有意的分层**：catalog 存规范层级（source of truth），emit seam（`to_messages_api` 把 XHIGH→`"max"` on Anthropic wire，`to_openai_effort_token` 把 XHIGH→`"high"` on OpenAI wire）是独立的、由 R53/R56/R57 持有的层。catalog 层不该预判 wire 合约。**测试覆盖**：`test_enrich_default_effort_normalizes_max_alias` 断言 `reasoningEffort: "max"` → `reasoning_effort_default == "xhigh"`。

### 验证

- `ruff check` R58 改动的 4 文件（reasoning.py + handlers_model.py + test_reasoning.py + test_model.py）→ 初次报 test_model.py 5 个预先存在 lint（I001 + 4× B010），`ruff --fix` 后 → **All checks passed!**（R58 新增代码零 ruff 错误 + 编辑文件的预存 lint 一并修复）。
- `pytest tests/test_reasoning.py tests/test_model.py -q` → **65 passed**（含 R58 新增 11 测试：9 enrich 纯函数 + 2 handler 集成）。
- 完整套件 `pytest` → **1771 passed, 10 skipped, 0 failed**（R57 1760 → R58 1771，**+11 精确**为 R58 新增；**0 failed** 零回归铁证）。

### YAGNI 边界

- ❌ **不同步前端 `web/src/types/ipc.ts` 模型类型** —— 加可选 `supports_reasoning_effort` / `reasoning_effort_default` / `reasoning_effort_options` 需 IPC 契约三同步（types + docs + mock backend），留 R59（前端能渲染 effort 选择器的下一步）。
- ❌ **不同步 `docs/ipc-contract.md`** —— 同上，留独立轮次。
- ❌ **不透传 mock backend** —— `web/src/ipc/client.ts` 的 `mockHandle` 暂不需新增字段（后端 enrich 是附加可选字段，mock 不带也合法）；待 R59 前端类型同步时一并处理。
- ❌ **不让 reasoning_effort 流入 done chunk 元数据** —— 那是 write 路径的观察点，与 R58 的 read/catalog 半边正交，留独立轮次。
- ❌ **不迁移 sampling-types crate 其余类型**（ChatCompletionRequest/SamplingConfig/ToolChoice/Role/Usage）—— 继续聚焦 ReasoningEffort。
- ❌ **不给 enrich 加原地修改选项** —— 永远返回浅拷贝（纯函数，调用者输入不变）；原地修改是过早优化，违背纯函数契约。

### Commit

`feat(platform): R58 reasoning_effort meta readers first consumer (fuse grok xai-grok-sampling-types)`

## R59 — reasoning_effort enrich 字段 IPC 契约三同步（融合 grok xai-grok-sampling-types，前端 types + mock + docs 对齐 R58 catalog-read 消费面）

> 锚定 R58（`7744a4a`）。R58 把 reasoning_effort 元数据读取器的首个消费者接上后端 `model.list`——`enrich_model_reasoning_meta` 把 catalog 的 raw `reasoningEffort` / `reasoningEfforts` / `supportsReasoningEffort` 元数据规范化为 snake_case 字段注入 IPC 响应。但 R58 **只动了后端**：前端 `web/src/types/ipc.ts` 的 `ModelInfo` 还不认这三个新字段，`docs/ipc-contract.md` 的 `model.list` 仍标着 "Reserved."，mock backend 也没有能展示 enrich 的模型。**本轮闭合契约三同步**：前端类型加可选字段（向后兼容，零回归）+ mock backend 加一个带 reasoning meta 的 grok 模型（结构与真实后端 enrich 输出一致）+ docs 补 `model.list` 响应的 reasoning_effort 字段说明。`protocol.py` 无需改——enrich 是 handler 层业务字段扩展，不是协议信封（Request/Response/Event 封装）变更。至此 R53–R58 的 reasoning_effort 管道（config→wire 写入半边 + catalog→IPC 读取半边）**两侧的 IPC 呈现面对前端完整可用**。

### 本轮目标

R58 给后端 `model.list` 注入了三个可选的 reasoning_effort 字段，但前端类型契约没跟上。CLAUDE.md 明确规定：「IPC 合约变更必须同步：`docs/ipc-contract.md` + `web/src/types/ipc.ts` + `agent/minimax_code/ipc/protocol.py`」。R58 的 enrich 是 handler 业务字段扩展（非协议信封），所以 `protocol.py` 不需要改，但 types + docs + mock backend 三处必须同步。**核心设计决策**：(1) **三字段全部可选（`?`）**——向后兼容零回归，未声明 reasoning meta 的模型（全部现有 MiniMax 模型）不获得这些 key，前端 `ModelInfo` 的旧消费者不受影响；(2) **`ReasoningEffortOption` 镜像后端 pydantic 输出**——`{value, id, label, description, default}` 与后端 `reasoning_efforts_meta_value` 的 dict 结构字节一致；(3) **mock backend 直接构建 snake_case 字段**——mock 绕过后端 enrich（直接返回 `ModelInfo`），所以 mock 的 grok 模型必须**手动带上规范化字段**，与真实后端经 enrich 后的结构一致（mock 模式下前端也能渲染 effort 选择器，验证 mock 与真实后端结构等价）；(4) **docs 聚焦 reasoning_effort 子集**——按轮次独立性 + YAGNI，不完整记录整个 `ModelInfo`（那会引入与 reasoning 无关的字段文档债），只补本轮引入的三个可选字段。

### 融合结论

- ✅ **保留**：`web/src/types/ipc.ts` —— `ModelInfo` 之前新增 `ReasoningEffortOption` interface（`{value, id, label, description: string | null, default: boolean}`，docstring 说明镜像后端 pydantic model）；`ModelInfo` 加 `supports_reasoning_effort?: boolean` / `reasoning_effort_default?: string` / `reasoning_effort_options?: ReasoningEffortOption[]` 三个可选字段（均带 R58 enrich 注释）。三字段全部可选 = 向后兼容。
- ✅ **保留**：`web/src/ipc/client.ts` —— `mockModels` 加第 4 个 `grok-1` 条目（`provider: "xAI"` / `provider_id` / `protocol: "anthropic"` / `supports_reasoning_effort: true` / `reasoning_effort_default: "high"` / `reasoning_effort_options: [low/medium/high with default:true on high]`）；3 个 MiniMax 模型保留不带 meta（零回归对照，exercise「未声明 meta 不获得新 key」路径）。
- ✅ **保留**：`docs/ipc-contract.md` —— 命名空间表的 `model.list` 行从 "Reserved." 改为描述句（「Dynamic model list + current selection; entries may carry optional reasoning-effort meta (R58)」）；表格后插入 `### model.list response — reasoning-effort fields (R58)` 小节（JSON 示例 + 三字段语义 + max 别名分层引用 R56/R57 emit seam）。
- ✅ **保留（验证确认无需改）**：`agent/minimax_code/ipc/protocol.py` —— grep 无 `ModelInfo`/`reasoning`/`context_window`/`supports_tools` 匹配；protocol.py 只定义 Request/Response/Event/Notification **信封**，业务字段不在协议层定义。enrich 是 handler 业务字段扩展，不触碰信封。与 R58 判断一致（R58 也未改 protocol.py）。
- ❌ **放弃**：**不做前端 effort 选择器 UI 组件** —— 消费 R58/R59 字段的 React 组件（`ModelSelector` 扩展或新组件），留 R60+。本轮只保证契约就位。
- ❌ **放弃**：**不完整记录 `ModelInfo` 所有字段** —— docs 只补 reasoning_effort 三字段，不引入 `provider_id`/`protocol`/`context_window` 等无关字段文档债（YAGNI）。
- ❌ **放弃**：**不修复预先存在前端债务** —— `client-pending-mode.test.ts:415` JsonRpcId null TSC 错误 + `message-list.test.tsx:80` findByText 超时（stash 验证铁证：R58 `7744a4a` 状态下两者仍存在，与 R59 无关；留独立轮次）。

### 交付

- `web/src/types/ipc.ts`（改，2 处）— (1) `ModelInfo` interface 之前新增 `ReasoningEffortOption` interface（`{value: string; id: string; label: string; description: string | null; default: boolean}`，含镜像后端 pydantic model 的 docstring）；(2) `ModelInfo` 加 `supports_reasoning_effort?: boolean` / `reasoning_effort_default?: string` / `reasoning_effort_options?: ReasoningEffortOption[]` 三可选字段（含 R58 enrich 注释）。
- `web/src/ipc/client.ts`（改，1 处）— `mockModels` 在 `minimax-M2.7-pro` 之后追加 `grok-1` 条目（全套 reasoning meta + provider_id/protocol），含「mock 与真实后端 enrich 输出结构一致；3 个 MiniMax 模型不带 meta exercise 零回归路径」注释。
- `docs/ipc-contract.md`（改，2 处）— (1) 命名空间表 `model.list` 行 "Reserved." → 描述句；(2) 表格后插入 `### model.list response — reasoning-effort fields (R58)` 小节（JSON 示例 + 三字段语义说明 + max→xhigh 分层 + emit seam 引用 R56/R57）。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

### 映射决策树（本轮 catalog-read 消费面的前端契约镜像 + mock 直构 snake_case + protocol.py 不动）

本轮是 R58 catalog-read 消费面的**前端契约镜像**。决策树无新增枚举——复用 R58 的三字段。**新增的是「契约三同步」执行模式**：types 加可选字段（向后兼容）+ mock 直构规范化字段（绕过 enrich）+ docs 补字段语义。

**R58 后端 enrich → R59 前端契约镜像矩阵**：

| R58 后端 enrich 字段（snake_case） | R59 `types/ipc.ts` | R59 mock backend（`client.ts`） | R59 docs（`ipc-contract.md`） |
|---|---|---|---|
| `supports_reasoning_effort: True` | `ModelInfo.supports_reasoning_effort?: boolean` | grok: `supports_reasoning_effort: true` | 「仅当 catalog `supportsReasoningEffort` truthy」 |
| `reasoning_effort_default: "high"` | `ModelInfo.reasoning_effort_default?: string` | grok: `reasoning_effort_default: "high"` | 「规范 wire token；`max`→`xhigh`（emit seam 在 R56/R57）」 |
| `reasoning_effort_options: [{value,id,label,description,default}]` | `ModelInfo.reasoning_effort_options?: ReasoningEffortOption[]` + 新 interface | grok: `reasoning_effort_options: [low/medium/high]` | 「仅当 `reasoningEfforts` 非空」 |

**坑 1（自发现，已预判修复）**：**mock backend 直接构建 `ModelInfo`，绕过后端 enrich——所以 mock 的 grok 模型必须手动带 snake_case 规范化字段**。第一直觉可能是 mock 里也存 raw `reasoningEffort`/`reasoningEfforts`（camelCase catalog 词汇表），但 mock 不经过 `enrich_model_reasoning_meta`，前端拿到的是 mock 直出的 dict。**预判正确**：mock 的 grok 模型直接写 `supports_reasoning_effort` / `reasoning_effort_default` / `reasoning_effort_options`（snake_case），与真实后端经 enrich 后的结构字节一致——这样 mock 模式下前端 effort 选择器也能渲染，证明 mock 与真实后端结构等价。3 个 MiniMax 模型保留不带 meta，exercise「未声明 meta 不获得新 key」的零回归路径。

**坑 2（自发现，预先存在前端债务）**：**R59 首次重新跑前端验证（tsc + vitest），暴露两个预先存在失败**。(1) `client-pending-mode.test.ts:415` TSC 错误 `Type 'null' is not assignable to type 'JsonRpcId'`（`requestId` 类型标注含 null，而 `JsonRpcResponse.id` 是 `JsonRpcId` = string|number）—— JSON-RPC 协议层债务；(2) `message-list.test.tsx:80` vitest `findByText("read_file")` 超时——消息渲染/工具调用 UI 测试。**stash 验证铁证**：`git stash` 掉 R59 的 types + client 改动后（纯 R58 `7744a4a` 状态），TSC 错误**仍然存在**（`TSC_ON_STASH_EXIT=1`）。两个失败都与 `ModelInfo`/`reasoning_effort` 业务字段**完全无关**（协议层 id / 消息渲染 UI vs 模型业务字段）。按轮次独立性，R59 **不修复**这些预先存在债务——记录在案，留独立轮次。**根因**：R58 之前只验证 Python pytest（1771 passed），从未跑前端 tsc/vitest，所以这些债务一直潜伏；R59 是首个跑前端验证的轮次，等于把它们"曝光"。

**坑 3（自发现，已预判修复）**：**`protocol.py` 是否需要改？** CLAUDE.md 规定 IPC 合约变更必须同步 protocol.py。**预判正确**：grep `protocol.py` 无 `ModelInfo`/`reasoning`/`context_window`/`supports_tools` 匹配——`protocol.py` 只定义 Request/Response/Event/Notification **信封**（`jsonrpc`/`id`/`method`/`params`/`result`/`error`），**业务字段（`ModelInfo` 的具体 keys）不在协议层定义**，而是 handler 层返回的 dict。enrich 是 handler 业务字段扩展，不触碰信封，所以 `protocol.py` **无需改**。这与 R58 的判断一致（R58 也未改 protocol.py）。CLAUDE.md 的「三同步」规则在业务字段扩展场景退化为「两同步 + 验证 protocol.py 无需改」。

### 验证

- `pnpm lint`（ESLint `src --ext .ts,.tsx`）→ **零错误**（client.ts grok 条目 + types/ipc.ts 新 interface/字段 lint 干净）。
- `tsc -b`（前端类型检查）→ 唯一错误 `client-pending-mode.test.ts:415 JsonRpcId null` 是**预先存在**（stash 验证铁证：`TSC_ON_STASH_EXIT=1` on R58 状态）；R59 改动**零新增类型错误**（types 三字段全部可选，向后兼容；mock grok 条目结构合法匹配 ModelInfo）。
- `pnpm test`（vitest）→ **444 passed / 2 failed**（57 文件，1 failed）。2 个失败（`message-list.test.tsx` `findByText("read_file")` 超时）是**预先存在** UI 测试债务，与 R59 无关；R59 相关的 444 个测试全过。
- **零回归**：types 三字段全部可选（`?`），未声明 meta 的 MiniMax 模型不获得新 key——前端旧消费者字节不变。

### YAGNI 边界

- ❌ **不做前端 effort 选择器 UI 组件** —— 消费 R58/R59 字段的 React 组件（`ModelSelector` 扩展或新组件），留 R60+。本轮只保证契约就位。
- ❌ **不完整记录 `ModelInfo` 所有字段** —— docs 只补 reasoning_effort 三字段，不引入 `provider_id`/`protocol`/`context_window` 等无关字段文档债。
- ❌ **不修复 `client-pending-mode.test.ts` JsonRpcId null** —— 预先存在协议层债务（stash 验证铁证），与 R59 无关，留独立轮次。
- ❌ **不修复 `message-list.test.tsx` findByText 超时** —— 预先存在 UI 测试债务，与 R59 无关。
- ❌ **不让 reasoning_effort 流入 done chunk 元数据** —— write 路径观察点，与 R59 的 read/catalog 半边正交，留独立轮次。
- ❌ **不迁移 sampling-types crate 其余类型**（ChatCompletionRequest/SamplingConfig/ToolChoice/Role/Usage）—— 继续聚焦 ReasoningEffort。

### Commit

`feat(platform): R59 reasoning_effort enrich fields IPC contract sync (fuse grok xai-grok-sampling-types)`

## R60 — reasoning_effort 前端 effort badge UI（融合 grok xai-grok-sampling-types，消费 R58/R59 ModelInfo 可选字段，闭合 catalog→IPC→UI 读取链路）

> 锚定 R59（`8428df1`）。R53–R59 把 reasoning_effort 管道铺到了「config→wire 写入半边」（R53-R57）+「catalog→IPC 读取半边」（R58 后端 enrich → R59 前端契约 types/mock/docs），但**最后一公里——用户能在 UI 上看到某个模型支持 reasoning effort、默认是哪个 effort、可选哪些 effort——还没接上**。本轮补这一公里：新建纯展示组件 `ReasoningEffortBadge`，消费 `ModelInfo` 的三个可选字段（R59 定义），集成进 `ModelSelector` 的 trigger（default 变体显示当前模型 effort）+ 模型项（每个模型名字旁的 effort 徽标，tooltip 列全部可选菜单），并加 barrel export + 6 个 vitest 用例。**核心设计决策**：(1) **纯展示、零写入**——badge 只读 `ModelInfo`，不调任何 IPC、不写任何 store；切换 effort（write-back 到 agent）需要新 IPC `model.set_reasoning_effort` + 后端持久化（调研 `AgentConfig.reasoning_effort` 如何存储），grep 确认 `agent/minimax_code/ipc` 下**零** reasoning_effort handler，留 R61；(2) **不支持时返回 null**——`supports_reasoning_effort` falsy（全部 MiniMax 模型）时组件什么都不渲染，与 R60 前字节一致，零回归；(3) **tooltip 列全部 options**——用户不打开切换器就能看到 `{value, default}` 菜单，为 R61 切换器预热心智模型；(4) **`Pick<ModelInfo, ...>` props**——badge 接受完整 `ModelInfo` 或任意 Pick，调用方（ModelSelector）无需 reshape 数据。

### 本轮目标

R59 把 reasoning_effort 的 IPC 契约（前端 types + mock + docs）铺好了，但前端没有任何组件**消费**这三个新字段——等于管道接到了家门口却没拧开水龙头。本轮目标：闭合 catalog→IPC→UI 的**读取链路可见化**——让用户在 `ModelSelector` 里直观看到「这个 grok 模型支持 reasoning effort，默认 high，可选 low/medium/high」。**严格边界（YAGNI + 轮次独立性）**：(1) 只做**展示**，不做**切换**——切换需要新 IPC `model.set_reasoning_effort`（合约扩展：protocol.py + handlers_model.py + 前端 typedIPC + store 状态 + 后端持久化），是一个独立的、跨栈的较大轮次，留 R61；(2) 不修预先存在的 tsc/vitest 债务（R59 已 stash 证明 `client-pending-mode.test.ts:415` JsonRpcId null 与 `message-list.test.tsx` findByText 超时是预先存在）；(3) 零回归——未声明 reasoning meta 的模型（全部 MiniMax）UI 字节不变。

### 融合结论

- ✅ **新增**：`web/src/components/ReasoningEffortBadge.tsx` —— 纯展示组件，单一职责（SRP）。props 接受 `Pick<ModelInfo, "supports_reasoning_effort" | "reasoning_effort_default" | "reasoning_effort_options">`（兼容完整 ModelInfo，无需 reshape）；`supports_reasoning_effort` falsy 时返回 `null`（零回归）；否则渲染紫色 mono 徽标 `effort: {default}`，`title` tooltip 列全部 options（`{value}{(default)?}`）。内部 helper `formatOptionsTooltip` 处理 options 缺失/非空两分支；`DEFAULT_FALLBACK = "auto"` 处理「声明 supports 但无 default」的退化情形。docstring 标注它是 reasoning_effort 管道的**读取侧终点**（catalog meta → R53 readers → R58 enrich → R59 IPC contract → 此 badge）。
- ✅ **集成**：`web/src/components/ModelSelector.tsx`（改 3 处）—— (1) import `ReasoningEffortBadge`；(2) trigger 按钮（default 变体）在 protocol badge 后追加 `{!isInline && currentModel && <ReasoningEffortBadge model={currentModel} />}`（inline 变体跳过，避免浮动组合器过载）；(3) 模型项名字行从 `<span className="block truncate ...">{m.name}</span>` 改为 `<span className="flex items-center gap-1">` 包裹名字 + badge（badge `shrink-0` 防挤压）。menu 副标题（context/tools）保留不变。
- ✅ **barrel export**：`web/src/components/index.ts` —— `ModelSelector` 行后追加 `export { ReasoningEffortBadge }` + `export type { ReasoningEffortBadgeProps }`（匹配现有桶导出模式，测试套件也从此导入）。
- ✅ **测试**：`web/src/components/ReasoningEffortBadge.test.tsx`（新，6 用例）—— (1) 无 meta 渲染 null（零回归）；(2) `supports_reasoning_effort: false` 渲染 null；(3) 支持时渲染 `effort: high`；(4) tooltip 含全部 options + default 标记；(5) 声明 supports 但无 default 时 fallback `auto`；(6) 无 options 时 tooltip 仅 default token。helper `slice()` 把 `Partial<ModelInfo>` 安全窄化成 Pick 类型。
- ❌ **放弃**：**不实现 effort 切换 IPC** —— `model.set_reasoning_effort`（write-back 到 agent）需要 protocol.py 契约扩展 + handlers_model.py handler + 前端 typedIPC 方法 + modelStore 状态 + 后端持久化（`AgentConfig.reasoning_effort` 存储位置调研）。grep 确认 `agent/minimax_code/ipc` 下零 reasoning_effort handler——这是全新 IPC 面，跨栈较大轮次，留 R61。
- ❌ **放弃**：**不修复预先存在前端债务** —— `client-pending-mode.test.ts:415` JsonRpcId null TSC 错误（R59 stash 已证明 R58 状态下仍存在）；本轮 R60 未触碰该文件（git status 确认仅改 ModelSelector/index/新建组件），零新增类型错误。按轮次独立性留独立轮次。

### 交付

- `web/src/components/ReasoningEffortBadge.tsx`（新建）— 纯展示组件：`Pick<ModelInfo, ...>` props / 不支持返回 null / 紫色 mono 徽标 + tooltip 列 options / `formatOptionsTooltip` helper / `auto` fallback / docstring 标注读取侧终点。
- `web/src/components/ReasoningEffortBadge.test.tsx`（新建）— 6 vitest 用例覆盖零回归/null/false/默认 token/tooltip 菜单/auto fallback/无 options 退化。
- `web/src/components/ModelSelector.tsx`（改 3 处）— import + trigger（default 变体）集成 + 模型项名字行 flex 包裹 badge。
- `web/src/components/index.ts`（改 1 处）— barrel export 加 ReasoningEffortBadge + props 类型。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

### 映射决策树（本轮 = catalog→IPC→UI 读取链路的 UI 终点 + 纯展示零写入边界 + 零回归 null 守卫）

本轮是 R58/R59 catalog-read 消费面的 **UI 终点**。决策树无新增枚举——消费 R59 定义的三可选字段。**新增的是「读取链路可见化」+「纯展示 vs 切换的 YAGNI 边界」**。

**R58 enrich → R59 契约 → R60 UI 消费 三段链路矩阵**：

| 链路段 | 位置 | 字段 | R60 消费方式 |
|---|---|---|---|
| catalog meta（raw） | `reasoningEffort` / `reasoningEfforts` / `supportsReasoningEffort` | camelCase | R58 enrich 已规范化（R60 不见 raw） |
| R58 enrich（后端） | `enrich_model_reasoning_meta` | snake_case 注入 IPC 响应 | R60 不动后端 |
| R59 契约（前端 types） | `ModelInfo.supports_reasoning_effort?` / `reasoning_effort_default?` / `reasoning_effort_options?` | 可选 snake_case | R60 `Pick<ModelInfo, ...>` 直接消费 |
| **R60 UI 终点** | `ReasoningEffortBadge` | 读取 → 渲染徽标 + tooltip | **本轮闭合** |

**坑 1（自发现，已预判修复）**：**纯展示 vs 切换的边界——切换需新 IPC，本轮不做**。第一直觉可能是「既然能显示 effort，那就让用户点 badge 切换」。但 grep `agent/minimax_code/ipc` 下**零** `reasoning_effort` handler——切换 effort 是 write-back 到 agent 的全新 IPC 面（`model.set_reasoning_effort`：protocol.py 信封 + handlers_model.py handler + modelStore 状态 + 后端 `AgentConfig.reasoning_effort` 持久化），是一个跨栈较大轮次。**预判正确**：R60 严格只做展示（零 IPC 调用、零 store 写入），badge 是只读组件。切换留 R61。`modelStore.setCurrent(id)` 当前只接收模型 ID（无 effort 参数），也佐证切换是新面。

**坑 2（自发现，已预判修复）**：**零回归守卫——不支持时返回 null**。`supports_reasoning_effort` 是可选字段，全部 MiniMax 模型不声明（R58 enrich 只在 catalog 声明时注入）。**预判正确**：badge 第一行 `if (!model.supports_reasoning_effort) return null;`——未声明/为 false 时组件什么都不渲染，`ModelSelector` 集成点视觉与 R60 前字节一致。vitest 用例 1+2 显式 exercise 这条路径。`ModelInfo` 三字段全部可选（R59 设计）+ null 守卫 = 双保险零回归。

**坑 3（自发现，设计决策）**：**tooltip 列全部 options 而非只显示 default**。badge 本体窄（`effort: high`），但 reasoning effort 的可选项（low/medium/high/max/xhigh）对用户决策很重要。**决策**：本体只显示 default token（保持紧凑，适配 inline 菜单项），`title` tooltip 列全部 options（`{value}{(default)?}`），用户 hover 即见全貌——为 R61 切换器预热心智模型，且零额外渲染成本（title 属性）。`formatOptionsTooltip` 处理 options 缺失（退化到仅 default token）与非空（join 全部）两分支。

**坑 4（自发现，预先存在债务）**：**tsc 唯一错误 `client-pending-mode.test.ts:415 JsonRpcId null` 是预先存在**（R59 stash 已证明 R58 `7744a4a` 状态下仍存在）。本轮 R60 **未触碰**该文件（git status 确认仅改 `ModelSelector.tsx` / `index.ts` / 新建组件），tsc 输出与 R59 完全一致——零新增类型错误。按轮次独立性，R60 不修复该协议层债务。`ReasoningEffortBadge.tsx` 的 `JSX.Element | null` 返回类型 + `Pick` props 全部类型安全（tsc 零错误）。

### 验证

- `npx vitest run src/components/ReasoningEffortBadge.test.tsx` → **6/6 passed**（55ms；零回归路径 + default 渲染 + tooltip 菜单 + auto fallback + 无 options 退化全覆盖）。
- `pnpm lint`（ESLint `src --ext .ts,.tsx`）→ **零错误零警告**（新组件 + ModelSelector 集成 + barrel 导出 lint 干净；test 文件 `no-explicit-any` 不触发——`slice()` helper 用 `Partial<ModelInfo>` 而非 any）。
- `npx tsc -b` → 唯一错误 `client-pending-mode.test.ts(415,9) Type 'null' is not assignable to type 'JsonRpcId'` 是**预先存在**（R59 stash 验证铁证 + R60 未触碰该文件）；R60 改动**零新增类型错误**（`Pick<ModelInfo, ...>` props 类型安全；ModelSelector 集成点 `currentModel && <ReasoningEffortBadge model={currentModel} />` 经 `&&` 收窄为 ModelInfo，兼容 Pick props）。
- **零回归**：`supports_reasoning_effort` falsy 时 badge 返回 null——MiniMax 模型 UI 字节不变；vitest 用例 1+2 显式验证。

### YAGNI 边界

- ❌ **不实现 effort 切换 IPC** —— `model.set_reasoning_effort`（write-back）需 protocol.py + handler + 前端 typedIPC + store + 后端持久化，跨栈较大轮次，留 R61。本轮 badge 严格只读。
- ❌ **不给 inline 变体 trigger 加 badge** —— 浮动组合器（MessageInput）空间窄，badge 会挤压；default 变体（页脚）trigger 已加。inline 菜单项仍显示 badge（模型项集成点不受 variant 影响）。
- ❌ **不修复 `client-pending-mode.test.ts:415` JsonRpcId null** —— 预先存在协议层债务（R59 stash 验证铁证），R60 未触碰该文件，留独立轮次。
- ❌ **不修复 `message-list.test.tsx` findByText 超时** —— 预先存在 UI 测试债务，与 R60 无关。
- ❌ **不让 reasoning_effort 流入 done chunk 元数据** —— write 路径观察点，与 R60 的 read/UI 半边正交，留独立轮次。
- ❌ **不迁移 sampling-types crate 其余类型**（ChatCompletionRequest/SamplingConfig/ToolChoice/Role/Usage）—— 继续聚焦 ReasoningEffort。
- ❌ **不做 effort 切换器的交互态设计**（选中高亮/键盘导航/确认）—— 那是 R61 切换器的 UI 范畴，R60 badge 只预热 tooltip 菜单心智。

### Commit

`feat(platform): R60 reasoning_effort frontend effort badge UI (fuse grok xai-grok-sampling-types)`

## R61 — reasoning_effort 切换 IPC 后端写存储面（融合 grok xai-grok-sampling-types，新增 model.set_reasoning_effort handler + ModelPrefsDAO 持久化 + 回读暴露，闭合写入半边的存储接缝）

> 锚定 R60（`76e1ed9`）。R53–R60 把 reasoning_effort 管道的**读取侧**完整铺通：catalog meta（R53 readers）→ R58 后端 enrich → R59 前端契约三同步 → R60 UI badge 展示。但 R60 的 badge 是**纯展示零写入**——用户看到 grok 模型「默认 high、可选 low/medium/high」却**无法切换**，因为切换需要一个全新的 **write-back IPC**（`model.set_reasoning_effort`）+ 后端持久化列 + DAO 写方法 + handler 验证。R60 在 YAGNI 边界里明确把这个「切换面」留给了「下一轮」。**本轮就是那一轮**：闭合写入半边的**存储接缝**——新增迁移 014（`model_prefs.reasoning_effort` 列）+ `ModelPrefsDAO.set_reasoning_effort` 写方法 + `model.set_reasoning_effort` IPC handler（严格校验 + 写入规范化）+ `model.list`/`model.get_current` **回读暴露**（前端无需单独往返即可回显选择）+ 11 个测试。**核心设计决策**：(1) **严格校验而非宽松丢弃**——`parse_effort_strict`（未知 token → `ValueError` → `-32602`）而非 `parse_effort_token`（未知 → `None` 静默），让 typo 干净报错而非存为垃圾；(2) **写入即规范化**——`canonical.as_str()` 把 `"max"` 别名落库为 `"xhigh"`（规范 wire token），读者无需 re-parse；(3) **空值 = 清除语义**——`None`/空白字符串不是错误，是「清除 override、恢复模型默认 effort」（pre-R61 行为），与 `set_current` 的非空校验刻意不同；(4) **回读暴露对称 R58**——R58 是 catalog-read（模型**支持**什么）注入 `model.list`，R61 是 prefs-read（用户**选了**什么）注入 `model.list`/`model.get_current`，两者并列字段、互不覆盖；(5) **拆分：本轮只做写存储表面 + IPC，不做运行时接线**——`rebuild_subagent_llm` 今天**不转发** effort 到 LLM 调用（grep 铁证：`_rebuild_subagent_llm` 构造 `MiniMaxClient` 时不读 effort），handler 里保留防御性 `rebuild_subagent_llm()` 调用仅为**预热接线 + 保持 client fresh**（镜像 `set_current`），**有效运行时效果留 R62**。

### 本轮目标

R60 把 UI badge 做成纯展示后，「切换 effort」就成了 reasoning_effort 管道最后一座未越的山头。但切换是 **write-back 到 agent 持久层** 的全新 IPC 面——它不能只加一个 handler，必须同时解决三件事：(1) **存哪里**——`model_prefs` 表当前只有 `current_model`/`provider_id`，没有 effort 列；(2) **怎么校验**——effort token 必须落在 R53 的规范集（none/minimal/low/medium/high/xhigh），`max` 别名要兑现；(3) **怎么回显**——前端切换后需要立即知道「当前选了什么」，不能等下一次 `model.list` 往返。本轮目标：闭合**写入半边的存储接缝**——迁移加列 + DAO 写方法 + handler（校验+规范化+持久化+回读）+ 测试。**严格边界（YAGNI + 轮次独立性）**：(1) **不做运行时接线**——存储的 effort 今天**不影响** LLM 调用（`_rebuild_subagent_llm` 不转发 effort），那是主 agent `AgentConfig.reasoning_effort`（R55 已存在的 config 字段）↔ 子 agent LLM 构造的运行时联动，是独立且更微妙的轮次，留 R62；(2) **不做前端 typedIPC/store/切换器 UI**——`web/src/types/ipc.ts` 加方法签名 + `modelStore` 加 effort 状态 + `ModelSelector` 加切换器，是前端轮次，留 R62；(3) **不修预先存在的 tsc/vitest 债务**（R59 stash 已证明 `client-pending-mode.test.ts:415` JsonRpcId null 与 `message-list.test.tsx` findByText 超时是预先存在）；(4) **零回归**——新列可空、无默认，所有现有行回填 `NULL`，未迁移的库行为不变。

### 融合结论

- ✅ **新增**：`agent/minimax_code/storage/migrations/014_model_prefs_reasoning_effort.py` —— `ALTER TABLE model_prefs ADD COLUMN reasoning_effort TEXT`（VERSION=14，`run(conn)` 调 `executescript`）。列**可空、无默认**：`NULL` = 无 override（pre-R61 行为），SQLite `ALTER TABLE ADD COLUMN` 自动给所有现有行回填 `NULL`——向后兼容零迁移数据。docstring 标注它是 R58 read-side seam（catalog meta → `model.list` enrich）的**对称写侧**：R58 透出「模型支持什么」，R61 持久化「用户选了什么」。
- ✅ **改**：`agent/minimax_code/storage/dao/model_prefs.py` —— (1) `get_current()` SELECT 加 `reasoning_effort` 列，返回字典加 `reasoning_effort` 键（`None` 兜底），Row 解析用 `hasattr(row, "keys") and "reasoning_effort" in row.keys()` 兼容 sqlite3.Row 与 tuple；返回类型 `dict[str, str]` → `dict[str, Any]`（因 effort 可 None）；(2) 新增 `async set_reasoning_effort(effort: str | None) -> dict[str, Any]`——`async with self._db.transaction()` + `UPDATE model_prefs SET reasoning_effort = ?, updated_at = ? WHERE id = ?`，`stored = str(effort) if effort else None`（空字符串归一为 None），返回 `get_state()`（镜像 `set_current` 的「写 + touch updated_at + 返回完整行」契约）；(3) `get_current_sync` 同步读取侧镜像（加列 + 返回键）；(4) 新增 `set_reasoning_effort_sync` 镜像异步路径（CLI/脚本）；(5) `__all__` 加 `set_reasoning_effort_sync`。
- ✅ **改**：`agent/minimax_code/ipc/handlers_model.py` —— (1) 导入 `parse_effort_strict`（R53 严格解析器）；(2) 模块 docstring 的 Endpoints/Validation 章节补 `model.set_reasoning_effort` 语义（严格校验 + `max`→`xhigh` 规范化 + None 清除）；(3) `handle_model_list` 读取 pref 的 `reasoning_effort`，响应从 `{models, current}` → `{models, current, reasoning_effort}`（回读暴露）；(4) `handle_model_get_current` 响应从 `{model}` → `{model, reasoning_effort}`；(5) 新增 `handle_model_set_reasoning_effort` + 注册——`check_params(expected_keys=set())` → 取 `params.get("reasoning_effort")` → None/空白→清除；非空则 `parse_effort_strict`（ValueError→`HandlerError(INVALID_PARAMS, ...)` 即 `-32602`）→ `canonical.as_str()` 规范化落库 → `dao.set_reasoning_effort(effort)` → 防御性 `rebuild_subagent_llm()`（try/except 吞错，注释标明今天无运行时效果、为 R62 预热）→ `ctx.reply({ok, reasoning_effort})`。
- ✅ **改**：`agent/tests/test_model.py` —— (1) 导入加 `set_reasoning_effort_sync`；(2) **修复 2 个被新字段打破的精确断言**——`test_get_current_default` 与 `test_set_current_persists` 原本 `pref == {"model_id": ..., "provider_id": ...}` 精确字典 `==`，现在返回多了 `reasoning_effort` 键 → 主动转为键检查（`assert pref["model_id"] == ...` / `assert pref.get("reasoning_effort") is None`），既适配新结构又显式验证默认 None；(3) 新增 **4 个 DAO 测试**（默认 None / 往返持久化 / None 清除 / 独立于模型选择）；(4) 新增 **1 个 sync 测试**（`set_reasoning_effort_sync` 往返）；(5) 新增 **6 个 IPC 测试**（持久化 / `max`→`xhigh` 规范化 / None 清除 / 大小写不敏感 / 未知 token 拒绝 `-32602` / 缺 key 清除）。错误断言模式：`with pytest.raises(RuntimeError) as ei: ...; err = ei.value.args[0]; assert isinstance(err, dict); assert err.get("code") == -32602`（具体异常类型，符合 B017）。
- ✅ **改**：`docs/ipc-contract.md` —— (1) 方法表第 221 行合并 `model.set_reasoning_effort` 进 `req/res` 行；(2) R58 小节后追加 `### model.set_reasoning_effort — reasoning-effort override write-back (R61)` 章节——请求/响应 JSON 示例 + 语义（严格校验 / `-32602` 拒绝 / `max`→`xhigh` 规范化 / null 清除 / 独立于模型）+ 回读（`model.list`/`model.get_current` 增字段）+ 存储（迁移 014，可空，运行时效果延后 R62）。
- ❌ **放弃**：**不做运行时接线** —— `_rebuild_subagent_llm` 构造 `MiniMaxClient` 时**不读**存储的 effort、不转发给 `stream_chat`；主 agent `AgentConfig.reasoning_effort`（R55）也**不从**存储回填。grep 铁证：存储的 effort 今天**只写不读运行时**。这是独立且需谨慎的轮次（主 agent config ↔ 子 agent LLM 构造的双路径），留 R62。
- ❌ **放弃**：**不做前端 typedIPC/store/切换器 UI** —— `web/src/types/ipc.ts` 加 `model.set_reasoning_effort` 签名 + `mockHandle` 覆盖 + `modelStore` 加 effort 状态 + `ModelSelector` 把 R60 badge 升级为可点切换器，是前端轮次，留 R62。
- ❌ **放弃**：**不修复预先存在前端债务** —— `client-pending-mode.test.ts:415` JsonRpcId null（R59 stash 验证铁证）+ `message-list.test.tsx` findByText 超时；R61 纯后端轮次未触碰前端，零新增债务。留独立轮次。

### 交付

- `agent/minimax_code/storage/migrations/014_model_prefs_reasoning_effort.py`（新建）— `ALTER TABLE model_prefs ADD COLUMN reasoning_effort TEXT`（VERSION=14，可空无默认，docstring 标注 R58 对称写侧）。
- `agent/minimax_code/storage/dao/model_prefs.py`（改 5 处）— `get_current()` 读列+返回键（Row/tuple 兼容）；新增 `set_reasoning_effort` async 写方法；`get_current_sync` 镜像；新增 `set_reasoning_effort_sync`；`__all__` 补项。返回类型 `dict[str,str]`→`dict[str,Any]`。
- `agent/minimax_code/ipc/handlers_model.py`（改 5 处）— 导入 `parse_effort_strict`；docstring Endpoints/Validation 章节；`handle_model_list` 回读 `reasoning_effort`；`handle_model_get_current` 回读 `reasoning_effort`；新增 `handle_model_set_reasoning_effort` + 注册。
- `agent/tests/test_model.py`（改）— 导入 + 2 个精确断言修复（键检查）+ 4 DAO 测试 + 1 sync 测试 + 6 IPC 测试（共 11 新增）。
- `docs/ipc-contract.md`（改 2 处）— 方法表 + R61 write-back 章节。
- `docs/evolution/ITERATION_LOG.md`（改）— 本条目。

### 映射决策树（本轮 = 写入半边的存储接缝 + 严格校验 vs 宽松丢弃 + 写入即规范化 + 回读对称 R58 + 拆分 R62 运行时接线）

本轮是 R58 catalog-read 消费面的**对称写侧**。决策树无新增枚举——复用 R53 的 `ReasoningEffort` 规范集与 `parse_effort_strict` 严格解析器。**新增的是「写入存储接缝」+「严格校验语义」+「写入即规范化」+「回读暴露」四条执行决策**。

**reasoning_effort 管道全链路矩阵（R53–R61，标注本轮闭合段）**：

| 链路半边 | 轮次 | 段 | 方向 | R61 角色 |
|---|---|---|---|---|
| **写入半边（config→wire）** | R53 | 类型层 `ReasoningEffort` + `parse_effort_*` | 类型 | 复用 `parse_effort_strict` |
| | R54 | `stream_chat` 接收 effort | 下游传输 | 不动 |
| | R55 | `AgentConfig.reasoning_effort` + core→client | 上游半段 | **R62 接线目标** |
| | R56 | OpenAI wire emit | 线缆 | 不动 |
| | R57 | Anthropic wire emit | 线缆 | 不动 |
| **读取半边（catalog→IPC→UI）** | R58 | 后端 enrich `model.list` | catalog-read | 不动（R61 prefs-read 与之并列） |
| | R59 | 前端契约 types/mock/docs | 契约 | 不动 |
| | R60 | UI badge 纯展示 | UI-read | 不动（R62 升级为切换器） |
| **写入半边（prefs→存储）** | **R61** | **迁移 014 + DAO 写 + handler + 回读** | **prefs-write 存储** | **本轮闭合** |
| | R62（未来） | `rebuild_subagent_llm` 转发 + 前端切换器 | 运行时接线 | 留下一轮 |

**坑 1（自发现，已预判修复）**：**严格校验 vs 宽松丢弃的语义选择**。R53 提供两个解析器：`parse_effort_token`（宽松：未知 token → `None`，`max` → XHIGH）与 `parse_effort_strict`（严格：未知 token → `ValueError` 列出合法集）。第一直觉可能是宽松（容错好），但**写入路径必须严格**——如果用户/前端传了 `"hgh"`（typo），宽松会把 `None` 当合法值存下（清除 override），用户以为设了 high 实际没设，**静默错误最难排查**。**预判正确**：选 `parse_effort_strict`，`ValueError` → `HandlerError(INVALID_PARAMS=-32602, str(exc))`，前端拿到清晰错误可提示「合法值: none, minimal, low, medium, high, xhigh, max」。`max` 别名仍兑现（strict 也认 `max`→XHIGH），只是落库前 `canonical.as_str()` 归一为 `"xhigh"`。

**坑 2（自发现，已预判修复）**：**写入即规范化——`max` 别名存为 `xhigh` 而非 `max`**。用户传 `"max"`（grok CLI 别名），`parse_effort_strict("max")` → `ReasoningEffort.XHIGH`，然后 `canonical.as_str()` = `"xhigh"`（规范 wire token）。**预判正确**：落库 `"xhigh"` 而非 `"max"`，读者（`model.list` 回读、R62 运行时）拿到的是规范 token，**无需 re-parse 别名**——单一规范化点在写入侧，消除读取侧的分支负担。这与 R58 enrich 的分层一致（catalog 层存规范层级，emit seam 在 R56/R57 独立持有 wire 映射）。

**坑 3（自发现，已预判修复）**：**空值 = 清除语义，与 `set_current` 的非空校验刻意不同**。`set_current` 要求 `model_id` 非空（`INVALID_PARAMS` if empty），但 `set_reasoning_effort` 的 `None`/空白**不是错误**——它是「清除 override、恢复模型默认 effort」。第一直觉可能是「也要求非空」，但 effort override 是**可选的**（模型自己有默认 effort，R58 catalog 已透出），用户应能「取消选择」回到默认。**预判正确**：handler 里 `raw = params.get("reasoning_effort")` → None 走清除分支；非空则 `str(raw).strip() or None`（空白归一 None 也清除）；只有**非空且解析失败**才报 `-32602`。DAO 层 `stored = str(effort) if effort else None` 双保险。

**坑 4（自发现，已预判修复）**：**`get_current()` 返回类型变更打破精确字典断言**。原 `get_current()` 返回 `dict[str, str]`（`{model_id, provider_id}`），两个旧测试 `test_get_current_default`/`test_set_current_persists` 用 `pref == {"model_id": ..., "provider_id": ...}` 精确 `==`。加了 `reasoning_effort` 键后这个 `==` 必然失败。**预判正确**：主动把两个断言从精确字典 `==` 转为键检查（`assert pref["model_id"] == ...` + `assert pref.get("reasoning_effort") is None`），既适配新结构又**显式验证默认值是 None**（多一层断言价值）。返回类型 `dict[str, str]` → `dict[str, Any]`（effort 可 None，str 类型不够）。

**坑 5（自发现，拆分决策）**：**`rebuild_subagent_llm()` 调用今天无运行时效果——保留它为 R62 预热**。`set_current` 调 `rebuild_subagent_llm()` 是因为切换模型后子 agent LLM 要重建。`set_reasoning_effort` 也调它看似多余（effort 今天不流入 LLM），但**预判保留**：(1) 镜像 `set_current` 的调用形状，保持两个 setter 的结构对称；(2) `rebuild_subagent_llm` 内部重建 client，保持 client fresh 无害；(3) **为 R62 预热**——R62 让 `_rebuild_subagent_llm` 读存储 effort 并转发后，这个调用**立即生效**，无需再改 handler。try/except 吞错确保即使 rebuild 失败也不影响持久化（存储是 source of truth，rebuild 是衍生）。注释明确标注「No-op today... kept here to prime that wiring」。

### 验证

- `cd agent && uv run pytest tests/test_model.py -q` → **29 passed**（含 R61 新增 11 测试：4 DAO + 1 sync + 6 IPC；2 个断言修复后的旧测试仍绿）。
- `cd agent && uv run pytest tests/test_storage.py -q` → **27 passed**（迁移 014 在 discover_migrations 自动发现链路里无回归；VERSION=14 与文件名数字一致，无 RuntimeError）。
- `cd agent && uv run ruff check minimax_code/storage/migrations/014_model_prefs_reasoning_effort.py minimax_code/storage/dao/model_prefs.py minimax_code/ipc/handlers_model.py tests/test_model.py` → **All checks passed!**（R61 新增/编辑代码零 ruff 错误；E/F/W/I/B/UP 规则集全过；行长度 100）。
- **零回归**：迁移列可空无默认，所有现有行回填 `NULL`；`get_current()` 的 `reasoning_effort` 默认 None = pre-R61 行为；未迁移的库（`MINIMAX_CODE_DATA_DIR` 临时库）首次启动自动跑 014，无数据丢失。

### YAGNI 边界

- ❌ **不做运行时接线** —— `_rebuild_subagent_llm` 不转发 effort 到 `stream_chat`；主 agent `AgentConfig.reasoning_effort`（R55）不从存储回填。存储的 effort 今天**只写不读运行时**（grep 铁证）。主 agent config ↔ 子 agent LLM 双路径联动留 R62。
- ❌ **不做前端 typedIPC/store/切换器 UI** —— `types/ipc.ts` 方法签名 + `mockHandle` 覆盖 + `modelStore` effort 状态 + `ModelSelector` 切换器，前端轮次留 R62。
- ❌ **不修复预先存在前端债务** —— `client-pending-mode.test.ts:415` JsonRpcId null + `message-list.test.tsx` findByText 超时（R59 stash 验证铁证），与 R61 无关，留独立轮次。
- ❌ **不让 reasoning_effort 流入 done chunk 元数据** —— write 路径观察点，与 R61 的 prefs-write 存储半边正交，留独立轮次。
- ❌ **不迁移 sampling-types crate 其余类型**（ChatCompletionRequest/SamplingConfig/ToolChoice/Role/Usage）—— 继续聚焦 ReasoningEffort。
- ❌ **不给 `model.set_reasoning_effort` 加 model 关联校验** —— 不校验「当前模型是否 supports_reasoning_effort」（用户可能对一个不支持的模型设 effort）。effort override 是**全局用户偏好**，独立于模型（docstring 明示），R62 运行时接线时若模型不支持可降级为忽略，但存储层不该拒绝——保持存储的模型无关性。
- ❌ **不做迁移 014 的回滚** —— 前向迁移策略（无回滚），与 001–013 一致。

### Commit

`feat(platform): R61 reasoning_effort set IPC backend storage layer (fuse grok xai-grok-sampling-types)`

---

## R62 — reasoning_effort 后端运行时接线（rebuild 转发存储值 → AgentConfig）

**锚定**：`555e5bd` (R61)　|　**状态**：✅ 闭合回路　|　**测试**：12 新增 / 1794 全过

### 本轮目标

闭合 R61 打开的回路。R61 把 `reasoning_effort` 持久化进 `model_prefs` 表（迁移 014 + DAO 写 + `model.set_reasoning_effort` handler），但 R61 的 YAGNI 边界明示**「只写不读运行时」**——存进去的值没有任何构造点读回来。grep 铁证：R61 之前 `AgentConfig.reasoning_effort`（R55 已定义并接好 config→core→client→transport）永远是 `None`，前端切换 effort 后**下一次 LLM 调用根本不生效**。

R62 让存储的 effort 真正流入 LLM 调用：引入进程级单例 `_REASONING_EFFORT_OVERRIDE`，由 `_rebuild_subagent_llm`（异步，唯一能 `await` DAO 的写点）从 `model_prefs` 读取并写入单例，再由两个同步构造点读单例注入各自 `AgentConfig`——子 agent `SubAgentRuntime.build` + 主 agent `builtins.py:AgentCore(config=AgentConfig(...))`。下游 R55（config→core→client）早已就绪，R62 只补**上游来源**这一段。

### 融合结论

✅ **回路闭合**。grok `xai-grok-sampling-types` 的「采样配置驱动 LLM 调用」理念在 MiniMax Code 端到端落地：

```
前端切换 → R61 存储(model_prefs) → R62 单例转发 → R55 config→core→client
        → R54 stream_chat(reasoning_effort=) → R56/R57 wire emit
```

12 个新测试（5 组：单例缓存 / rebuild 写单例 / SubAgentRuntime 注入 / 时序不变式 / 端到端）全过；全量回归 **1794 passed, 10 skipped, 0 failed**——零回归。effort 默认 `None` = pre-R62 行为，未存 override 时字节级不变。

### 交付

| 文件 | 改动 |
|------|------|
| `orchestrator/subagent.py` | `__init__` 加 `reasoning_effort: str \| None = None` 构造参数 + `set_reasoning_effort(effort)` setter（热替换，无需重建 client）；`build()` 的 `AgentConfig(...)` 注入 `reasoning_effort=self._reasoning_effort` |
| `app.py` | 新增 `_REASONING_EFFORT_OVERRIDE` 进程级单例 + `get_reasoning_effort_override()` getter（公开）+ `_set_reasoning_effort_override(effort)` 内部 setter；`_set_subagent_llm(client)` 构造 `SubAgentRuntime(llm=client, reasoning_effort=<singleton>)` 并 `set_subagent_runtime(...)`；`_rebuild_subagent_llm(db)` 在提取 model_id/provider_id 后、provider 查找前 `effort = pref.get("reasoning_effort")` → `_set_reasoning_effort_override(effort)` |
| `ipc/builtins.py` | 主 agent 构造点读单例：`main_reasoning_effort = get_reasoning_effort_override()`（lazy import 规避循环依赖，`except Exception` 兜底 None）→ `AgentConfig(reasoning_effort=main_reasoning_effort, ...)` |
| `ipc/handlers_model.py` | 更新 R61 的「today no-op」注释为「R62 loop closed」（`set_reasoning_effort` 末尾的 `rebuild_subagent_llm()` 调用现在真生效） |
| `tests/test_reasoning_runtime.py` | 12 测试 / 5 组（新增） |

### 映射决策树 + 坑

**决策树（数据流）**：

```
model_prefs.reasoning_effort (R61 存)
  └─> ModelPrefsDAO.get_current()["reasoning_effort"]
        └─> _rebuild_subagent_llm(db) [异步·唯一写点]
              └─> _set_reasoning_effort_override(effort)  ──> _REASONING_EFFORT_OVERRIDE 单例
                    ├─> _set_subagent_llm(client) [同步·读] ─> SubAgentRuntime(reasoning_effort=...)
                    │     └─> SubAgentRuntime.build() ─> AgentConfig.reasoning_effort ─> R55 core→client
                    └─> builtins.py 主 agent [同步·读] ─> AgentConfig.reasoning_effort ─> R55 core→client
                                                                                              └─> R54 stream_chat ─> R56/R57 wire
```

**坑 1（架构铁律，决定单例存在）**：**`SubAgentRuntime.build` 是同步方法，不能 `await` DAO**。第一直觉可能是「build 时直接 `await dao.get_current()` 读 effort」——但 build 不可能是 async（调用方 `handlers_agents.py` 全链路同步持 handle）。这就是进程级单例存在的根本理由：**异步 `_rebuild_subagent_llm` 写，同步 `build`/`_set_subagent_llm` 读**，把异步读取的值「冻结」进同步可见的 global。预判正确：单例是最小改动 + 零回归（None 兜底）+ 无循环依赖（getter 函数内 lazy import）的解。

**坑 2（自发现，已预判修复）**：**effort 提取必须放在 provider 查找之前**。`_rebuild_subagent_llm` 在 provider 为 None（临时库无 provider 行）时**提前 return 默认 client**。如果把 effort 提取放 provider 查找后，provider 缺失会让 effort 永远不写单例——**缓存与 DB 失配**，用户设了 effort 但 provider 没配时单例停留在旧值。**预判正确**：提取紧贴 `pref = dao.get_current()` 之后、`secrets.get_provider_key()` 之前，保证无论走哪条 return 路径，单例都已刷新。测试 `test_rebuild_effort_set_even_when_provider_missing` 专门钉这条不变式。

**坑 3（架构事实，决定单例不入 client）**：**`MiniMaxClient` 没有 `reasoning_effort` 构造参数**。effort 是 `stream_chat(reasoning_effort=...)` 的**调用级参数**（R54 定义），不是 client 状态——同一 client 在不同 turn 可能用不同 effort（如果未来支持 per-turn override）。所以单例不能塞进 client，必须存独立 global，由 config 层（AgentConfig）消费。这也解释了 `set_reasoning_effort` setter 为何「热替换无需重建 client」——client 是 effort-agnostic 的，换 effort 不动 httpx 连接池。

**坑 4（时序铁律，无需同步原语）**：**启动 `_set_subagent_llm(await _rebuild_subagent_llm(db))` 的 Python 求值顺序保证正确**。`await _rebuild_subagent_llm(db)` 作为参数表达式**先求值**（写单例），求值完成后才调用 `_set_subagent_llm(client)`（读单例）。`rebuild_subagent_llm()` public 路径（`handlers_model.py` 切换后调）同样按此两步顺序。asyncio 单线程，无并发写竞争，**不需要锁**。测试 `test_end_to_end_stored_effort_reaches_subagent_config` 真实跑这条启动链路验证时序。

**坑 5（自发现，已预判修复）**：**lazy import 打破循环依赖**。`builtins.py` 顶层 `from ..app import ...` 会循环（`app.py` → `register_app_handlers` → `builtins.py`）。**预判正确**：getter 调用点内 `from ..app import get_reasoning_effort_override`，运行时才解析，环被切断。外层 `try/except Exception` 兜底（极端情况下 app 模块不可导入时退回 None = pre-R62 行为，不阻塞主 agent 启动）。

### 验证

- `cd agent && uv run pytest tests/test_reasoning_runtime.py -v` → **12 passed**（5 组全覆盖：单例缓存 3 + rebuild 写单例 3 + SubAgentRuntime 注入 3 + 时序不变式 2 + 端到端 1）。
- `cd agent && uv run pytest` → **1794 passed, 10 skipped**（零回归；新增 12 测试 + 原有 1782 全绿）。
- `cd agent && uv run ruff check minimax_code/app.py minimax_code/ipc/builtins.py minimax_code/ipc/handlers_model.py minimax_code/orchestrator/subagent.py tests/test_reasoning_runtime.py` → **All checks passed!**（R62 新增/编辑代码零 ruff 错误；E/F/W/I/B/UP 规则集全过；行长度 100）。
- **零回归机制**：`reasoning_effort` 默认 `None` → `AgentConfig(reasoning_effort=None)` → `_stream_turn`（R55）原样透传 None → `stream_chat(reasoning_effort=None)` → transport 层 None 即「不附加 effort 字段」（R56/R57 已处理）= pre-R62 行为。未迁移的库首次启动自动跑 014，effort 列 NULL，单例 None，全链路无感知。

### YAGNI 边界

- ❌ **不做前端 typedIPC / store / 切换器 UI** —— `types/ipc.ts` 的 `model.set_reasoning_effort` 方法签名 + `mockHandle` 覆盖 + `modelStore` effort 状态 + 把 R60 的纯展示 badge 升级为可点切换器。后端回路已闭合（R62），前端是独立的读取半边升级，留 R63。
- ❌ **不修复预先存在的前端债务** —— `client-pending-mode.test.ts` JsonRpcId null + `message-list.test.tsx` findByText 超时（R59 stash 验证铁证），与 R62 无关，留独立轮次。
- ❌ **不做模型 `supports_reasoning_effort` 运行时降级** —— 不在 wire emit 时检测「当前模型是否支持 effort」并降级为忽略。effort 是**全局用户偏好**（R61 docstring 明示模型无关），传输层（R56/R57）已按 provider 协议决定是否附加字段；模型不支持时由 provider 端忽略或报错，不在 R62 这层加检测——保持运行时接线的单一职责（转发存储值）。
- ❌ **单例不做线程安全加锁** —— asyncio 单线程事件循环，`_rebuild_subagent_llm` 是唯一写点且 `await` 期间无并发写；同步读点不会读到半写状态。加锁是过度设计（YAGNI）。
- ❌ **不做 per-turn / per-request effort override** —— 单例是进程级全局偏好，所有 turn 共享。未来若要 per-request effort（如 sub-agent 各自不同），需扩展为 request-scoped context，但当前需求是全局偏好，YAGNI。
- ❌ **不把 effort 流入 done chunk / tool_result 元数据** —— 观察点（write path observability）与 R62 的运行时接线（read path 生效）正交，留独立轮次。

### Commit

`feat(platform): R62 reasoning_effort backend runtime wiring singleton (fuse grok xai-grok-sampling-types)`

## R63 — reasoning_effort 前端切换器 UI（融合 grok xai-grok-sampling-types，升级 R60 只读 badge 为可点切换器 + typedIPC + modelStore 状态，闭合前端写入链路）

**锚定**：`d2e0351` (R62)　|　**状态**：✅ 前端写入链路闭合　|　**测试**：9 新增 / 461 全过

### 本轮目标

闭合 R62 打开的前端半边。R62 让存储的 `reasoning_effort` 真正流入 LLM 调用（单例转发 → AgentConfig → R55 core→client → R54 stream_chat → wire），但 R62 的 YAGNI 边界（第 4576 行）明示**「后端回路已闭合，前端切换器留 R63」**——用户当前**没有任何 UI 入口**切换 effort，R60 的 badge 是纯只读展示（`effort: high` 文本，无交互）。要让 reasoning_effort 真正「用户可操作」，必须补前端写入链路：typedIPC 方法签名 + mock 覆盖 + modelStore 状态 + 把 R60 badge 升级为可点下拉切换器。

R63 的核心约束是**零破坏**：R60 的 6 个只读测试 + 所有现有调用点（`ModelSelector` trigger / menu item）必须字节级不变。解法是**双模式 badge**——`onEffortChange` 回调可选：省略时走 R60 只读分支（`<span>` 纯文本，字节不变），传入时升级为 `<button>` + 下拉 menu（Default 行 + 每个选项）。`ModelSelector` 两处调用点都接入 store 传 `currentEffort`/`onEffortChange`，让用户在模型选择器 trigger 和 menu item 两个位置都能点开切换。

### 融合结论

✅ **前端写入链路闭合**。grok `xai-grok-sampling-types` 的「采样配置用户可调」理念在前端落地，与 R62 后端回路对接成完整环：

```
用户点切换器 → R63 modelStore.setReasoningEffort → R63 typedIPC model.set_reasoning_effort
            → R61 handler 持久化(model_prefs) → R62 单例转发 → R55 config→core→client
            → R54 stream_chat(reasoning_effort=) → R56/R57 wire emit
            ↓ (回读对称)
R63 modelStore.refresh ← R63 typedIPC model.list(reasoning_effort 回读) ← R61 read-back
```

9 个新测试（切换器契约：trigger 渲染 / override 显示 / menu 展开 / 选 option 回调 / 选 Default 回调 null / override 高亮 / Default 高亮 / 选后关闭 / 零破坏只读）全过；全量回归 **461 passed / 0 failed**（58 文件）——零回归。省略 `onEffortChange` 时 6 个 R60 测试字节级通过。

### 交付

| 文件 | 改动 |
|------|------|
| `web/src/types/ipc.ts` | `ListModelsResult` 加 `reasoning_effort?: string \| null`（消费 R61 read-back）；新增 `SetReasoningEffortResult { ok: true; reasoning_effort: string \| null }` |
| `web/src/ipc/client.ts` | (1) import `SetReasoningEffortResult`；(2) `IPCClient` 接口加 `setReasoningEffort(effort: string \| null): Promise<SetReasoningEffortResult>`；(3) typedIPC 实现 `model.set_reasoning_effort` 调用；(4) mock 状态 `let mockReasoningEffort: string \| null = null`；(5) mockHandle `model.list` 回读 `reasoning_effort: mockReasoningEffort` + 新增 `model.set_reasoning_effort` case（trim/空串归一 null，读写对称） |
| `web/src/stores/modelStore.ts` | `ModelState` 加 `reasoningEffort: string \| null` + `setReasoningEffort` action；初始 `null`；`refresh` 读 `r.reasoning_effort ?? null`；action 调 typedIPC 后 `set({ reasoningEffort: r.reasoning_effort })` |
| `web/src/components/ReasoningEffortBadge.tsx` | 双模式：`!onEffortChange` 走 R60 只读 `<span>`（字节不变）；传入时渲染 `EffortSwitcher` 子组件（trigger button + 下拉 menu：Default 行 + options）；`useState(open)` + `useRef` + `useEffect` 外部点击/Esc 关闭；3 处 `e.stopPropagation()`；effort 显示优先级 `currentEffort > modelDefault > "auto"`；字体 `text-[9px]`/`text-[10px]` → `text-[11px]`（合规），`text-[8px]` ▾ 保留 |
| `web/src/components/ModelSelector.tsx` | store 解构加 `reasoningEffort` + `setReasoningEffort`；trigger badge（原 139 行）+ menu item badge（原 208 行）两处都传 `currentEffort={reasoningEffort}` + `onEffortChange={setReasoningEffort}` |
| `web/src/components/ReasoningEffortBadge.test.tsx` | 导入改 vitest + fireEvent + 类型 import；保留 6 个 R60 只读测试；新增 `describe("switcher (R63)")` 9 测试 |

### 映射决策树 + 坑

**决策树（前端写入 + 回读对称）**：

```
用户点 option "low"
  └─> EffortSwitcher.choose("low")
        └─> onEffortChange("low")  ──> ModelSelector 传入的 setReasoningEffort
              └─> modelStore.setReasoningEffort("low")
                    └─> typedIPC.setReasoningEffort  ──> model.set_reasoning_effort IPC
                          ├─> [真 agent] R61 handler 持久化 → R62 单例 → wire 生效
                          └─> [mock]      mockReasoningEffort = "low"
                    └─> set({ reasoningEffort: "low" })  ──> badge trigger 立即显示 "effort: low"

用户点 "Default"
  └─> choose(null) → onEffortChange(null) → ... → mockReasoningEffort = null
        └─> badge 回退显示 model.reasoning_effort_default（如 "high"）= pre-R61 状态

刷新（refresh）
  └─> typedIPC.listModels() → r.reasoning_effort  ──> set({ reasoningEffort })  [回读对称]
```

**坑 1（DOM 冒泡铁律，决定 stopPropagation 必须）**：**badge 嵌在父 `<button>` 内**。`ModelSelector` 的 trigger 是 `<button onClick={() => setOpen(...)}>`，badge 作为子元素渲染其中；menu item 同理是 `<button onClick={选模型}>`。如果 badge 的点击 handler 不 `stopPropagation`，点击 effort 选项会**冒泡到父 button**——trigger 处会误开/误关模型菜单，menu item 处会**误触模型切换**（用户想改 effort 却换了模型）。**预判正确**：trigger / Default 选项 / 每个 option 三处 onClick 全加 `e.stopPropagation()`，点击被 badge 完全消费，不冒泡。

**坑 2（零破坏铁律，决定双模式分支）**：**R60 的 6 个测试 + 现有调用点不能动**。第一直觉可能是「直接把 badge 改成切换器」——但 R60 测试断言 `<span>` + `title` tooltip + `effort: high` 文本，且不支持 reasoning 的模型要 `return null`。**预判正确**：`onEffortChange` 可选，`!onEffortChange` 时走只读分支（`<span className=... text-[11px]>effort: {displayDefault}</span>`，与 R60 字节一致除字体合规修复）。传入时才渲染 `EffortSwitcher`。测试 `stays read-only when onEffortChange is omitted` 专门钉这条不变式。

**坑 3（显示优先级，决定三段 fallback）**：**effort 显示什么有三种来源**。(1) 用户 override（`currentEffort`，非空）——优先；(2) 模型自己的默认（`reasoning_effort_default`）——次之；(3) 都没有——`"auto"` fallback。只读分支用 `displayDefault = modelDefault ?? "auto"`；切换器分支用 `displayEffort = currentEffort?.trim() ? currentEffort : displayDefault`。**预判正确**：测试 `reflects the currentEffort override instead of the model default` 钉 override 优先（`currentEffort="low"` 时显示 `effort: low` 而非模型的 `high`）。

**坑 4（菜单高亮，决定 effectiveSelection vs Default 分离）**：**option 行和 Default 行的高亮逻辑不同**。option 行高亮条件 = `effectiveSelection === o.value`，其中 `effectiveSelection = currentEffort ?? modelDefault`（override 优先，否则模型默认 option 高亮）。但 Default 行的高亮条件是 `currentEffort == null`（**仅当无 override 时** Default 高亮，即使模型默认是 "high" 也不算 Default 选中——Default 意为「清除 override，用模型默认」）。**预判正确**：测试 `highlights Default when no override is active`（`currentEffort=null` + 模型默认 "high" → Default 行 ✓，high 行无 ✓）+ `highlights the active override option`（`currentEffort="low"` → low 行 ✓）钉两条不变式。

**坑 5（字体合规，自发现已修复）**：**`min-font-size.test.ts` 禁止 `text-[9px]`/`text-[10px]`**（P2#26 可访问性规则，正则 `/text-\[(9|10)px\]/`）。R60 遗留只读 badge 用了 `text-[9px]`，R63 新增 trigger 照搬 `text-[9px]`、menu 用 `text-[10px]`——全量测试 460/461，min-font-size 1 失败报 3 处违规。**预判正确**：`replace_all` 把 `text-[9px]`→`text-[11px]`（2 处：只读 badge + trigger）、`text-[10px]`→`text-[11px]`（menu）。`text-[8px]`（▾ 箭头）不在检查范围，保留。符合「修复正在编辑文件的错误」原则（R63 正在重写此文件）。

**坑 6（mock 对称，决定 mockReasoningEffort 单例）**：**mock 模式下 model.list 必须回读 set 写入的值**。第一直觉可能是「model.list 固定返回 null」——但这样 mock 下切换 effort 后刷新会丢状态，store 与 mock 失配。**预判正确**：mock 顶层 `let mockReasoningEffort` 单例，`model.set_reasoning_effort` case 写入（trim/空串归一 null），`model.list` case 回读 `reasoning_effort: mockReasoningEffort`——与 R61 后端 read-back 语义完全对称。

### 验证

- `cd web && pnpm test -- --run src/components/ReasoningEffortBadge.test.tsx` → **15 passed**（6 R60 只读 + 9 R63 切换器契约全覆盖）。
- `cd web && pnpm test -- --run` → **Test Files 58 passed / Tests 461 passed**（零回归；新增 9 测试 + 原有 452 全绿；min-font-size 修复后通过）。
- `cd web && pnpm lint` → **零错误**（R63 新增/编辑代码 ESLint + @typescript-eslint 全过）。
- **零回归机制**：`onEffortChange` 省略 → 走 R60 只读分支 → `<span>` 字节级一致（除字体 9→11px 合规修复）→ 6 个 R60 测试全过。不支持 reasoning 的模型仍 `return null`（`supports_reasoning_effort` 假值），视觉与 pre-R60 一致。
- **IPC 合约同步**：`docs/ipc-contract.md` 已在 R61 记录 `model.set_reasoning_effort` 方法（221/256 行）+ `model.list`/`model.get_current` 的 `reasoning_effort` read-back（289/291 行）；R63 前端消费侧 `types/ipc.ts` 加 `SetReasoningEffortResult` + `ListModelsResult.reasoning_effort`——三处契约一致，无需补充文档。

### YAGNI 边界

- ❌ **不做键盘导航（arrow up/down 进 menu）** —— 切换器当前是鼠标点击 + Esc 关闭。完整 a11y 键盘导航（arrow / home / end / type-ahead）是独立增强，当前需求是「可点切换」，YAGNI。
- ❌ **不做 per-model effort** —— effort 是**全局用户偏好**（R61 docstring 明示模型无关，存储层 `model_prefs` 不关联 model_id）。切换模型时 effort 保持，与 R61/R62 语义一致。per-model effort 需扩展 schema，YAGNI。
- ❌ **不修复预先存在的前端债务** —— `client-pending-mode.test.ts` JsonRpcId null + `message-list.test.tsx` findByText 超时（R59/R62 stash 验证铁证），与 R63 无关，留独立轮次。
- ❌ **不把 effort 流入流式 UI 观察点** —— done chunk / tool_result 元数据不携带 effort（与 R62 YAGNI 一致）。观察点（write path observability）与 R63 的前端写入（user → store）正交，留独立轮次。
- ❌ **badge 不在 inline 变体渲染** —— `ModelSelector` 的 inline 变体（`MessageInput` composer 内 slim pill）R60 就不渲染 badge（`!isInline` 守卫），R63 保持一致。inline 场景空间局促，effort 切换入口放在 default 变体（footer）即可，YAGNI。
- ❌ **切换器不做动画/过渡** —— menu 展开/收起是硬切（`open && <ul>`）。CSS transition / framer-motion 是视觉打磨，当前是功能闭合，YAGNI。

### Commit

`feat(platform): R63 reasoning_effort frontend switcher UI (fuse grok xai-grok-sampling-types, upgrade R60 badge to clickable switcher + typedIPC + modelStore state)`

## R64 — 钩子/插件管理平面 wire DTO 类型层（融合 grok xai-hooks-plugins-types，补全 R25 钩子框架的类型词汇表半边，四层扩展架构第四块落地）

**锚定**：`a97b12f` (R63)　|　**状态**：✅ 管理平面 wire DTO 层落地（纯类型，零 I/O）　|　**测试**：52 新增 / 1846 全过

### 本轮目标

补全 R25 留下的类型半边。R25 落地了钩子**执行**模型（4 事件配方 + stdin/stdout 子进程），但 grok `xai-hooks-plugins-types` crate（1219 行单文件）定义的是**管理平面** wire 契约——shell 与 pager 之间交换的"钩子/插件/MCP/市场"列表、动作、聚合视图的 JSON DTO。这两套类型职责正交：R25 是"怎么跑钩子"，R64 是"怎么列出/操作钩子"。未来 IPC handler（`hooks.list` / `plugins.list` / `mcp.list` / `marketplace.list` / `*.action`）需要这套 DTO 作为输入输出契约，R64 先打类型基础，**零 I/O**（无 subprocess / 文件系统 / 网络）。

R64 同时闭合 MiniMax Code **四层扩展架构**的最后一块：(1) `hooks/` = 钩子执行（R25）；(2) `plugins/` = 插件声明加载（`plugin.json`）；(3) `mcp/` = MCP 客户端运行时；(4) `extensions/` = 管理平面 wire DTO（R64）。核心约束是**零回归**——新增独立模块，不动任何现有代码；以及 **wire 保真**——struct 走 camelCase、tagged union 标签走 snake_case 但变体字段保持 snake_case（serde 枚举 `rename_all` 只重命名变体名，不重命名结构体变体字段）。

### 融合结论

✅ **管理平面 wire DTO 层落地**。grok `xai-hooks-plugins-types` 的 16 个 struct + 3 个动作枚举 + 9 个简单枚举全部正向迁移到 Python（pydantic v2 + StrEnum），无 Rust 工具链依赖：

```
grok Rust serde DTO                       →  R64 Python pydantic v2
──────────────────────────────────────────────────────────────────
camelCase struct (rename_all="camelCase") →  _Wire(BaseModel) + to_camel alias
tagged union (tag="type", snake_case)     →  _Variant(BaseModel) + Literal 判别
serde(other) forward-compat               →  parse_plugin_origin → UnknownOrigin
#[derive(Default)] 可空构造               →  全字段默认值
char::is_control + bidi strip             →  strip_control_chars (unicodedata Cc)
char_indices().nth() 码点截断             →  s[:max_chars] (Python 码点边界)
parts.join(" · ")                         →  " · ".join(parts)
```

52 个新测试全过（枚举 snake_case / sanitize 安全核心 / tagged union 往返 / serde(other) 降级 / 嵌套 union 解析 / camelCase struct wire）；全量回归 **1846 passed / 10 skipped**（后端 pytest，零回归，R64 三文件 ruff 全绿）。

### 交付

| 文件 | 改动 |
|------|------|
| `agent/minimax_code/extensions/__init__.py` | 新建 barrel 导出：3 常量 + `strip_control_chars`/`truncate_chars` + `hook_event_display` + 4 `parse_*` 函数 + 9 StrEnums + 4 union 别名 + 10 PluginOrigin 变体 + 16 顶层 struct |
| `agent/minimax_code/extensions/types.py` | 新建 ~933 行纯类型层：sanitize 辅助（Cc + bidi/零宽/BOM 过滤 + 码点截断）；9 StrEnums（PluginScope/HookEvent(14 变体)/HookHandlerType/HookStatus/McpStatus/OutcomeStatus/McpServerSource/McpSessionStatus/ComponentCategory）；ComponentItem + PluginComponents（categories/is_empty/summary_line 单复数+中点/sanitize 50 上限）；4 tagged union（PluginOrigin+serde(other)/HooksAction/PluginsAction/MarketplaceAction）+ 手动 parse 分发；16 camelCase struct（HookInfo/HooksListResponse/PluginInfo/PluginsListResponse/McpToolInfo/McpServerInfo/McpServersListResponse/HooksActionRequest/PluginsActionRequest/ActionOutcome/MarketplacePluginEntry/MarketplaceScanResult/MarketplaceListResponse/MarketplaceActionRequest） |
| `agent/tests/test_extensions_types.py` | 新建 52 测试：常量 / 枚举 wire / hook_event_display / sanitize helpers / ComponentItem new+sanitize+default / PluginComponents categories+is_empty+summary_line+sanitize / PluginOrigin 往返+serde(other)降级+非字典拒绝 / 3 动作 union 往返+未知 ValueError+嵌套请求 / 顶层 struct camelCase wire / PluginInfo 未知 origin 降级 / MarketplaceListResponse.sanitize 递归+None 跳过 |

### 映射决策树 + 坑

**决策树（wire 形状分发）**：

```
grok DTO 反序列化
  ├─ camelCase struct (HookInfo/PluginInfo/...)
  │     └─> _Wire: alias_generator=to_camel + populate_by_name
  │           └─> wire {"timeoutMs":5000} ⇄ Python timeout_ms=5000
  │
  ├─ tagged union 变体 (HooksAction::Enable{hook_name})
  │     └─> _Variant: NO alias generator (serde rename_all ≠ 字段重命名)
  │           └─> wire {"type":"enable","hook_name":"..."} ⇄ Python hook_name
  │                 (注意：不是 hookName！变体字段保持 snake_case)
  │
  ├─ 嵌套 union 字段 (PluginInfo.origin / *ActionRequest.action)
  │     └─> model_validate 类方法重写：拦截嵌套 dict → parse_* 路由 → 父验证
  │
  └─ 未知标签
        ├─ PluginOrigin → parse_plugin_origin → UnknownOrigin (serde(other) 容错)
        └─ *Action     → parse_*_action → ValueError (请求不容错)
```

**坑 1（tagged union 变体字段 snake_case，决定双基类）**：**serde 枚举 `#[serde(rename_all="snake_case")]` 只重命名变体名（标签值），不重命名结构体变体的字段**。所以 `HooksAction::Enable { hook_name }` 序列化为 `{"type":"enable","hook_name":"..."}`，**不是** `hookName`。第一直觉可能是所有模型都用 `_Wire`（camelCase alias）——但这样变体字段会被错误地 alias 成 camelCase，wire 失真。**预判正确**：双基类——`_Wire`（`alias_generator=to_camel`，给 struct 用）+ `_Variant`（**无** alias generator，给 tagged union 变体用）。测试 `test_hooks_action_toggle_source_keeps_snake_case_fields`（`hook_names`/`disable` 保持 snake_case）+ `test_plugin_origin_struct_variant_keeps_snake_case_fields`（`source_name`/`git_url` 保持 snake_case）钉这条不变式。

**坑 2（serde(other) 前向兼容，决定 parse_plugin_origin 降级 vs parse_*_action 报错）**：**PluginOrigin 有 `#[serde(other)]`，动作枚举没有**。PluginOrigin 是"数据来源描述"——未来 grok 可能新增 origin 类型，旧 pager 应容错（降级为 UnknownOrigin，不崩）。但 HooksAction/PluginsAction/MarketplaceAction 是"用户请求"——未知动作是协议错误，必须报错。**预判正确**：`parse_plugin_origin` 未知标签 → `UnknownOrigin()`（数据丢弃，wire `{"type":"unknown"}`）；`parse_*_action` 未知标签 → `ValueError`。测试 `test_plugin_origin_serde_other_degrades_unknown_to_unknown`（`{"type":"cloud_install"}` → UnknownOrigin）+ 三个 `*_unknown_tag_raises` 钉两种语义。

**坑 3（嵌套 union 解析，决定 model_validate 重写）**：**pydantic v2 的 tagged union 机制处理嵌套 union dict 时，未知标签会让整个父模型验证失败**。PluginInfo.origin 是 `PluginOrigin | None`，如果用 pydantic 原生 union 解析，一个未知的 origin tag 会让整个 PluginInfo 验证失败——但坑 2 要求 origin 容错。**预判正确**：4 个含嵌套 union 的 struct（PluginInfo/HooksActionRequest/PluginsActionRequest/MarketplaceActionRequest）重写 `model_validate` 类方法，拦截嵌套 dict（`obj["origin"]` / `obj["action"]`），先通过 `parse_*` 路由（应用 serde(other) 降级），再调 `super().model_validate`。测试 `test_plugin_info_degrades_unknown_origin_on_validate`（PluginInfo 带未知 origin 不崩，origin → UnknownOrigin）钉这条。

**坑 4（HookEvent 命名冲突，决定文档警告）**：**`extensions.types.HookEvent`（14 变体，管理平面 wire 词汇表）与 `hooks.types.HookEvent`（4 变体，可执行子集）同名不同义**。两者在不同命名空间，但容易混淆。**预判正确**：`extensions.types.HookEvent` 的 docstring 明示"this is the wire vocabulary for listing/management; the executable subset is `hooks.types.HookEvent` — a different type in a different namespace. Do not confuse the two."。模块 docstring 也强调 extensions 是"aggregated view layer, distinct from hooks execution"。

**坑 5（ComponentItem.sanitize 不 trim 空白，决定 is_empty 而非 trim）**：**grok 的 sanitize 用 `.filter(|d| !d.is_empty())`，不是 `.trim()`**。第一直觉可能是"description 纯空白应归 None"——但 grok 的 `strip_control_chars` 只过滤 Cc + bidi（`char::is_control`），**不过滤空格**（空格不是 control char）。所以 `"  "`（纯空白）保留为 `"  "`，只有 `""`（纯空）才 → None。**预判正确**：Python `strip_control_chars` 用 `unicodedata.category(c) != "Cc"`（与 Rust `is_control` 等价），`sanitize` 用 `cleaned or None`（空字符串 falsy → None，空白 truthy → 保留）。测试 `test_component_item_new_strips_control_chars_and_drops_empty_description`（`""` → None）+ `test_component_item_new_preserves_whitespace_only_description`（`"  "` → `"  "`）钉两种语义。Grep grok 源码（lib.rs:374-381）逐字符确认。

**坑 6（__init__.py 残留 MarketPluginInstall，自发现已修复）**：**barrel 导出 `__all__` 列表引用了未导入的名字 `MarketPluginInstall`**（早期命名歧义：插件来源变体最初叫 `MarketPluginInstall`，易与 `MarketplaceAction::Install` 混淆，改名为 `MarketplaceInstallOrigin`，但 `__all__` 残留旧名）。这会让 `from minimax_code.extensions import *` 抛 `AttributeError`。**预判正确**：Edit 移除 `__all__` 中的 `"MarketPluginInstall"`（import 块本就没有它）。修复后 `import *` 正常，52 测试全过。

### 验证

- `cd agent && uv run pytest tests/test_extensions_types.py -v` → **52 passed in 0.30s**（枚举 wire 9 + sanitize helpers 3 + ComponentItem 6 + PluginComponents 5 + PluginOrigin 8 + 3 动作 union 9 + 顶层 struct 6 + MarketplaceListResponse.sanitize 2 + 常量 1，全覆盖）。
- `cd agent && uv run pytest` → **1846 passed, 10 skipped**（后端全量回归，零回归；R64 新增 52 测试融入）。
- `cd agent && uv run ruff check minimax_code/extensions/ tests/test_extensions_types.py` → **All checks passed!**（R64 三文件全绿；`--fix` 修复 2 个 I001 import 排序后干净）。
- **wire 保真交叉验证**：Grep grok `lib.rs` 源码（strip_control_chars/truncate_chars/ComponentItem::sanitize/summary_line/MarketplaceListResponse::sanitize）逐行确认 Python 实现语义一致——`char::is_control`=Cc、`char_indices().nth()`=码点边界、`.filter(!is_empty)`=空字符串过滤、`parts.join(" \u{b7} ")`=`" · "`。
- **零回归机制**：R64 是全新独立模块（`extensions/` 目录 + 1 测试文件），不动任何现有代码；`extensions.types.HookEvent` 与 `hooks.types.HookEvent` 命名空间隔离，互不影响。

### YAGNI 边界

- ❌ **不做 IPC handler** —— R64 只落地类型层（wire DTO）。`hooks.list` / `plugins.list` / `marketplace.list` / `*.action` 的 handler 实现（读配置、跑子进程、聚合视图）是独立轮次，需要 I/O 层设计。R64 提供 handler 的输入输出契约。
- ❌ **不做前端 UI** —— 钩子/插件/市场管理面板（pager 视图）是独立前端轮次。R64 的 wire DTO 是未来前端 IPC client 的类型来源，但 UI 本身 YAGNI。
- ❌ **不接入现有 hooks/plugins/mcp 运行时** —— R64 的 DTO 与 R25 hooks 执行、plugins 声明加载、mcp 客户端**类型独立**。adapter（运行时对象 → wire DTO）留独立轮次，避免本轮耦合 I/O。
- ❌ **不用 pydantic 原生 tagged union 机制** —— 手动 `parse_*` 分发（注册表查找 + Literal 判别）而非 pydantic `Annotated[Union, Discriminator]`。原因：需要在 parse 层注入 serde(other) 降级逻辑（坑 2/3），pydantic 原生机制不容错未知标签。手动分发 + model_validate 重写是可控的。
- ❌ **不修复预存 ruff 债务** —— 全量 `ruff check .` 报 339 错误（test_ws_heartbeat.py 等），均为预存，与 R64 无关。R64 三文件 ruff 全绿即可（轮次独立原则）。
- ❌ **ComponentItem 不加 trim** —— grok 用 `is_empty` 不用 `trim`，R64 忠实复刻（坑 5）。加 trim 会偏离 grok wire 语义，YAGNI。

### Commit

`feat(platform): R64 hooks/plugins management-plane wire DTO type layer (fuse grok xai-hooks-plugins-types, 9 enums + 4 tagged unions + 16 camelCase structs + sanitize security core, zero-regression standalone module)`

---

## R65 — 工具 schema 类型契约层(融合 grok xai-tool-types)

锚点:R64 `c7bb600`

### 本轮目标

融合 grok `xai-tool-types` crate(`types.rs` 1049 行 + `schema_utils.rs` 660 行)→ MiniMax Code **工具平面 schema 词汇表**。这是与 R64(管理平面 wire DTO)对称的第二个"类型契约"表面:

- **R64** 回答 *shell 如何被扩展*(hooks / plugins / MCP / marketplace wire DTO)。
- **R65** 回答 *有哪些工具*(tool / argument / type-tag / JSON-Schema 词汇)。

两者共同闭合平台外壳所需的两个类型表面,为未来的 IPC handler 与 agent tool registry 提供消费/生产的纯类型契约。落地 `agent/minimax_code/tool_types/` 新顶层模块(与 `extensions/` 并行),pure types + pure logic 零 I/O,前向迁移到 Python(pydantic v2 + StrEnum + 普通类 + dataclass),不需要 Rust 工具链。

### 融合结论 ✅

完整迁移成功。`xai-tool-types` 的 6 个核心类型 + 1 个 JSON Schema 解析器全部从 Rust 前向迁移到 Python,保真度通过 72 个测试钉死:

- `ArgumentType`(JSON Schema type tag)→ `StrEnum`,wire value == member value,自然 round-trip。
- `SchemaType`(serde `untagged`: string | array)→ 普通类 + `_types` 元组 + `_variant` 标志,精确复刻 `Single` / `Multiple` 语义。
- `ValidationError` / `ValidationErrors` → `@dataclass`,空 == ok(Rust `Result<(), ValidationErrors>` 语义)。
- `ToolArgument` / `ToolDescription` → pydantic v2 `BaseModel` + `arbitrary_types_allowed=True`,手写 `to_wire()` 复刻 `skip_serializing_if` 条件省略。
- `parse_arguments_from_schema_lossy` → 纯函数 JSON Schema 展平器,解析 schemars 生成的 `anyOf` / `$ref` / `$defs` / `oneOf` 两种 enum 形态。

零回归:全量 1918 passed(R64 的 1846 + R65 新增 72)。

### 交付

| 文件 | 行数 | 职责 |
|------|------|------|
| `agent/minimax_code/tool_types/__init__.py` | 50 | barrel 出口,仿 R64 `extensions/__init__.py` |
| `agent/minimax_code/tool_types/types.py` | 587 | `ArgumentType` / `SchemaType` / `ValidationError` / `ValidationErrors` / `ToolArgument` / `ToolDescription` |
| `agent/minimax_code/tool_types/schema_parser.py` | 309 | `parse_arguments_from_schema_lossy` + 4 个 helper(`_infer_arg_type` / `_extract_enum_from_def` / `_resolve_ref_type` / `_resolve_ref_in_defs`)+ `_number` |
| `agent/tests/test_tool_types.py` | — | 72 测试(ArgumentType 8 + SchemaType 21 + ValidationErrors 3 + ToolArgument 11 + ToolDescription 11 + schema_parser 15 + 端到端 3) |

### 映射决策树 + 坑

```
grok xai-tool-types
  ├─ ArgumentType 枚举 (rename_all="lowercase")
  │     └─> StrEnum (wire value == member value, 自然 round-trip)
  │           └─> wire "string" ⇄ ArgumentType.STRING (== 比较成立)
  │
  ├─ SchemaType (serde untagged: string | array)
  │     └─> 普通类 + __slots__=("_types","_variant")
  │           ├─ Single(t)   → _variant="single", _types=(t,)
  │           ├─ Multiple([t,...]) → _variant="multiple"
  │           │     (单元素 Multiple 保留,serde 反序列化保真)
  │           └─ from_value: 单元素数组归一化为 Single
  │                 (区别于 serde 反序列化,手动解析路径)
  │
  ├─ ToolArgument / ToolDescription struct
  │     └─> pydantic v2 BaseModel + arbitrary_types_allowed=True
  │           └─ to_wire() 手写(skip_serializing_if 精确语义)
  │                 ├─ required=True → 省略
  │                 ├─ allowed_values=[] → 省略
  │                 └─ Optional None → 省略
  │
  ├─ validate() → Result<(), ValidationErrors>
  │     └─> ValidationErrors dataclass (empty==ok, 不 raise)
  │
  └─ extra: Extensions 字段
        └─> ❌ YAGNI 省略 (TypeId 类型擦除, #[serde(skip)] 从不上 wire)
```

**坑 1 — `SchemaType` untagged union 的 `_variant` 标志**
grok `serde(untagged)` 下,`"string"` 与 `["string"]` 反序列化结果不同(前者 `Single`,后者 `Multiple`)。普通 Python 类若只用元组长度区分,会丢失"单元素 Multiple"信息。**预判**:round-trip 测试会发现 `SchemaType.multiple([STRING]).to_schema_value()` 必须返回 `["string"]` 而非 `"string"`。**钉死**:`test_multiple_variant_preserves_array_shape` + `test_single_equals_bare_argument_type`。

**坑 2 — `to_wire` 不能用 `model_dump`**
pydantic v2 `model_dump` 无法精确复刻 Rust `skip_serializing_if`(`required=True` 省略、空 list 省略、`None` 省略)。**预判**:若用 `model_dump`,会泄漏 `required: true` / `allowed_values: []` 到 wire。**钉死**:`test_to_wire_omits_required_when_true` + `test_to_wire_omits_empty_allowed_values` + `test_to_wire_emits_required_false_only`。

**坑 3 — `validate_identifier` 必须 ASCII 限定**
Rust 用 `char::is_ascii_alphanumeric | '_' | '-'`;Python `str.isalnum` 是 Unicode 宽(会误放行 `"中文"`)。**预判**:CJK 标识符会绕过校验。**钉死**:`test_validate_rejects_cjk_identifier` + `test_validate_accepts_dash_and_underscore`。

**坑 4 — `extra: Extensions` 字段 YAGNI 省略**
Rust `Extensions` 是 `TypeId`-keyed 类型擦除 map,`#[serde(skip)]` 从不上 wire,Python 无等价物,且其唯一行为效果是让 `PartialEq` 忽略它(字段移除后 moot)。**预判**:若强行迁移会产生死代码 + 引入 typing 复杂度。**决策**:YAGNI 省略,在 docstring 标注。

**坑 5 — 循环依赖(`ToolDescription` ↔ `schema_parser`)**
`schema_parser.py` 导入 `types.py`(`ToolArgument`);若 `types.py` 顶层导入 `schema_parser`(为 `to_arguments_lossy`),则成循环。**预判**:import 时报 `ImportError`。**修复**:`to_arguments_lossy` 方法内部延迟 `from .schema_parser import ...`。**钉死**:`test_to_arguments_lossy_end_to_end`。

**坑 6 — `schema` 字段 shadow pydantic 警告**
grok 字段名 `schema` 与 pydantic v1 历史 `BaseModel.schema()` 方法冲突,v2 已移除该方法(改 `model_json_schema()`),故为 false positive。**预判**:测试输出污染 `UserWarning`。**修复**:模块级 `warnings.filterwarnings("ignore", message=...)`,带详细注释说明 grok wire 保真理由。**钉死**:R65 测试套件 0 警告。

**坑 7 — `default` 字段 `None` 歧义**
Python `None` 既是缺失也是 JSON null,无法区分(对应 Rust `Option<Value>` 的 `Some(None)` vs `None`)。**务实决策**:用 `is not None` 检查(与 Rust `None` 对应),显式 null 默认值的 case 在测试场景(enum first variant 非 null)不存在。记录为 YAGNI 边界。

### 验证

三重验证全绿:

```bash
# 1. ruff lint(行长 100,E/F/W/I/B/UP)
cd "/d/工作/城建院/mm code/agent" && uv run ruff check minimax_code/tool_types/ tests/test_tool_types.py
# → All checks passed!

# 2. R65 专项测试(0 警告)
cd "/d/工作/城建院/mm code/agent" && uv run pytest tests/test_tool_types.py -q
# → 72 passed in 0.35s

# 3. 全量回归(零回归)
cd "/d/工作/城建院/mm code/agent" && uv run pytest -q
# → 1918 passed, 10 skipped, 1 warning in 106.70s
#    (唯一 warning 是 fastapi/httpx 弃用,与 R65 无关)
```

测试增长:R64 的 1846 → R65 的 1918(+72 R65 新增,零回归)。

### YAGNI 边界

本轮明确不做:

- ❌ `extra: Extensions` 字段(TypeId 类型擦除,无 wire 存在,无 Python 等价物)。
- ❌ 完整 JSON Schema 支持(`allOf` / `if-then-else` / `pattern` / `items` / 嵌套 properties)— grok 源 crate 同样 `# Not supported`,render tools 只需扁平顶层参数。
- ❌ `default` 显式 null vs 缺失的区分(Python None 二义性,测试场景无此 case)。
- ❌ 把 `tool_types/` 接入 IPC handler 或 agent tool registry(类型层先行,消费端留待后续轮次)。
- ❌ 前端 `web/src/types/` 镜像(纯后端类型契约,无 wire 事件,前端暂不需要)。

### Commit

`feat(platform): R65 tool schema vocabulary type layer (fuse grok xai-tool-types, ArgumentType + SchemaType + ToolArgument + ToolDescription + ValidationErrors + lossy JSON Schema parser, 72 tests zero-regression)`

---

## R66 — 核心运行时配置类型契约层(融合 grok xai-grok-config-types)

锚点:R65 `069b3f3`

### 本轮目标

融合 grok `xai-grok-config-types` crate(`flags.rs` + `permission.rs` + `pool.rs` + `memory.rs` + `mcp.rs` + `lib.rs` 共 2741 行)→ MiniMax Code **运行时配置类型契约层**。这是与 R64(管理平面 wire DTO)、R65(工具平面 schema 词汇表)对称的第三个"类型契约"表面,三者共同构成平台外壳的**类型契约三件套**:

- **R64** 回答 *shell 如何被扩展*(hooks / plugins / MCP / marketplace wire DTO)。
- **R65** 回答 *有哪些工具*(tool / argument / type-tag / JSON-Schema 词汇)。
- **R66** 回答 *如何被配置*(运行时 `[section]` 叶子配置值类型 + `RemoteSettings` 代理 payload)。

落地 `agent/minimax_code/config_types/` 新顶层模块(与 `extensions/`、`tool_types/` 并行),pure types + pure logic 零 I/O(唯一副作用是 `env_bool` / `RelaySyncConfig.is_enabled` / `resolve_oauth_client_secret` 读 `os.environ`,那正是其职责)。前向迁移到 Python(pydantic v2 + StrEnum + dataclass),不需要 Rust 工具链。这是 R66 源 crate 在 grok 中存在的理由——**依赖倒置**:类型层不依赖 shell,shell 依赖类型层。

### 融合结论 ✅

完整迁移成功。`xai-grok-config-types` 的 6 个源文件全部前向迁移到 7 个 Python 模块文件,保真度通过 104 个测试钉死:

- `flags.rs` → `flags.py`:`ConfigSource` StrEnum(strum `snake_case` 分割 CamelCase)+ `Resolved[T]` + `env_bool`(从父 crate 内联)+ `resolve_bool_flag` 七级优先链 + `BoolFlag` builder + `LazinessDetectorPerModelConfig`。
- `permission.rs` → `permission.py`:`PatternMode` / `RuleAction`(CWE-1188 默认 DENY)/ `ToolFilter`(`WebFetch` → `"webfetch"` 不分割)+ `PermissionRule` + `PermissionConfig`。
- `pool.rs` → `pool.py`:`PoolConfig`(4 字段全 `#[serde(default)]`,空表全默认)。
- `memory.rs` → `memory.py`:12 个结构体(index/embedding/search/temporal_decay/mmr/injection/session/dream/watcher/gc/flush/pruning)+ `DEFAULT_RECENCY_DECAY` + `_clamp_unit` + `effective_half_life_days` 三路优先。
- `mcp.rs` → `mcp.py`:`StdioTransport` / `StreamableHttpTransport`(untagged 两臂)+ `McpJsonOAuthBlock`(camelCase)+ `McpServerConfig`(flatten + untagged + `_split_transport` + `to_wire` + `expand_strings`)+ `RelaySyncConfig`(env 覆盖)+ `McpConfig`(alias `mcpServers`)+ `resolve_oauth_client_secret`。
- `lib.rs` → `types.py`:`CampaignOverride`(flatten catch-all patch)+ `DoomLoopRecoverySettings`(skip-None)+ `DisplayRefreshSettings`(tolerant bool/u32 + extra 保留)+ `ContextualHintsRemote` + `GoalRoleModel` + `RemoteAnnouncement`(跨 crate 内联)+ `RemoteSettings`(143 字段全 `Option` + `#[serde(default)]` + 3 个 tolerant deserialiser)。
- `__init__.py`:barrel facade,37 个导出符号按子模块分组。

零回归:全量 **2022 passed / 10 skipped**(R65 的 1918 + R66 新增 104,完美对账)。

### 交付

| 文件 | 行数 | 职责 |
|------|------|------|
| `agent/minimax_code/config_types/__init__.py` | 130 | barrel facade,37 符号按子模块分组导出 |
| `agent/minimax_code/config_types/flags.py` | 234 | `ConfigSource`(9 变体)+ `Resolved[T]` + `env_bool` + `resolve_bool_flag` + `BoolFlag` builder + `LazinessDetectorPerModelConfig` |
| `agent/minimax_code/config_types/permission.py` | 118 | `PatternMode` / `RuleAction`(默认 DENY)/ `ToolFilter`(`webfetch`)+ `PermissionRule` + `PermissionConfig` |
| `agent/minimax_code/config_types/pool.py` | 43 | `PoolConfig`(4 字段 + `default()`) |
| `agent/minimax_code/config_types/memory.py` | 341 | 12 结构体 + `DEFAULT_RECENCY_DECAY=0.95` + `_clamp_unit` + `effective_half_life_days` 三路优先 |
| `agent/minimax_code/config_types/mcp.py` | 339 | 2 transport 臂 + OAuth block + `McpServerConfig`(flatten+untagged+to_wire+expand_strings)+ `RelaySyncConfig` + `McpConfig` + `resolve_oauth_client_secret` |
| `agent/minimax_code/config_types/types.py` | 571 | `CampaignOverride` / `DoomLoopRecoverySettings` / `DisplayRefreshSettings` / `ContextualHintsRemote` / `GoalRoleModel` / `RemoteAnnouncement` / `RemoteSettings`(143 字段) |
| `agent/tests/test_config_types.py` | 612 | 104 测试(16 类场景:env_bool 解析 / ConfigSource wire / resolve 优先级 7 分支 / BoolFlag builder / ToolFilter `webfetch` / Permission 往返 / PoolConfig 空/部分/默认 / memory 12 默认 / MMR+flush clamp / effective_half_life 三路 / MCP stdio/http 分发+to_wire+expand / OAuth camelCase / RelaySync env 覆盖 / RemoteSettings 空+往返+3 tolerant / DisplayRefresh tolerant+extra / Campaign patch / DoomLoop skip-None) |

### 映射决策树 + 坑

```
grok xai-grok-config-types(serde → pydantic v2)
  │
  ├─ StrEnum 枚举(rename_all / serialize_all)
  │     ├─ serde "lowercase"(不分割)→ ToolFilter WebFetch="webfetch" ❗
  │     └─ strum "snake_case"(分割)  → ConfigSource SystemManagedConfig="system_managed_config"
  │
  ├─ #[serde(default)] + #[derive(Default)]
  │     └─> pydantic 字段默认值 + default() classmethod
  │           └─ 空 {} 表 → 全字段默认(PoolConfig/memory 12 struct)
  │
  ├─ deserialize_clamped_unit(f32 → [0,1])
  │     └─> _clamp_unit() + field_validator(mode="after")
  │           └─ MmrConfig.lambda_ / MemoryFlushConfig.semantic_dedup_threshold
  │
  ├─ de_opt_bool_tolerant / de_opt_u32_tolerant
  │     └─> _tolerant_bool() / _tolerant_u32() + field_validator(mode="before")
  │           └─ DisplayRefresh 字段错误类型 → None(不崩)
  │
  ├─ serde untagged(McpServerTransportConfig)
  │     └─> model_validator(mode="before") 按键存在性分发
  │           ├─ command → StdioTransport
  │           └─ url     → StreamableHttpTransport
  │
  ├─ serde flatten(transport 进 McpServerConfig)
  │     └─> _split_transport(读时拆)+ to_wire(写时合并)
  │
  ├─ serde flatten catch-all Map(patch / extra)
  │     └─> pydantic extra="allow" + model_extra 读取
  │           └─ CampaignOverride.patch / DisplayRefreshSettings.extra
  │
  ├─ serde tolerant array deserialiser(announcements / skeptic_models)
  │     └─> field_validator(mode="before") 逐项 try/except 丢弃坏项
  │           └─ 一个坏项不污染整个 payload
  │
  └─ 跨 crate 依赖分类
        ├─ xai_grok_config::env_bool          → 内联(flags.py)
        ├─ indexmap                            → Python dict 有序
        ├─ serde / serde_json                  → pydantic v2
        ├─ strum                               → StrEnum
        ├─ tracing::warn!                      → 类型层省略
        ├─ acp::McpServer(to_acp_mcp_server)   → YAGNI(无 ACP 消费端)
        ├─ McpOAuthConfig(oauth_config)        → YAGNI(OAuth 运行时在客户端层)
        └─ RemoteAnnouncement                  → 内联简化 pydantic(跨 crate)
```

**坑 1 — serde `lowercase`(不分割)vs strum `snake_case`(分割)**
两个枚举用了**不同**的序列化风格,但都把 CamelCase 变小写——区别在是否分割:`serde rename_all="lowercase"` 只整体小写,`strum serialize_all="snake_case"` 在词边界插下划线。**预判**:若一刀切用同一个规则,`WebFetch` 会错成 `"web_fetch"` 或 `SystemManagedConfig` 错成 `"systemmanagedconfig"`。**钉死**:`test_tool_filter_webfetch_is_not_split`(`WebFetch` → `"webfetch"`)+ `test_snake_case_splits_camel_case`(`SystemManagedConfig` → `"system_managed_config"`)。Grep grok 源码(permission.rs `rename_all="lowercase"` vs flags.rs `serialize_all="snake_case"`)逐行确认。

**坑 2 — CWE-1188 `RuleAction` 默认 DENY**
Rust `PermissionRule.action` 字段**无** `#[serde(default)]`(必填),但 `RuleAction` 自身 `#[derive(Default)]` 且 `Default = DENY`。安全语义:省略 action 绝不能静默产生 catch-all allow。**预判**:若 pydantic 给 action 加默认值 `ALLOW`,会引入安全漏洞。**钉死**:`test_action_is_required`(`PermissionRule()` 无参抛错)+ `RuleAction` docstring 明示 CWE-1188。

**坑 3 — Python `lambda` 是关键字**
Rust `MmrConfig { lambda: f32 }` 的 `lambda` 在 Python 是保留字,不能做属性名。**预判**:直接 `lambda: float` 是语法错误。**修复**:字段名 `lambda_`,wire alias `"lambda"`(`populate_by_name=True`),clamp validator 作用在 `lambda_`。**钉死**:`test_alias_lambda_wire`(`{"lambda": 0.4}` ⇄ `lambda_==0.4`)。

**坑 4 — serde flatten + untagged 三重组合(McpServerConfig)**
Rust `McpServerConfig` 用 `#[serde(flatten)] transport: McpServerTransportConfig`(untagged enum),传输字段在顶层与其他配置字段混合。pydantic 无直接等价物。**预判**:`model_validate({"command":"npx","enabled":false})` 必须既能解析出 StdioTransport 又能保留 enabled。**修复**:`_split_transport` model_validator(mode="before")按键存在性分发(command→Stdio / url→Http),拆出传输键后剩 config 键;`to_wire` 反向合并。**钉死**:`test_stdio_dispatch_by_command` + `test_http_dispatch_by_url` + `test_stdio_reflatten` + `test_http_reflatten`。

**坑 5 — serde flatten catch-all Map → pydantic extra="allow"**
`CampaignOverride.patch` 是 `serde_json::Map<String, Value>`(flatten,任意键),`DisplayRefreshSettings.extra` 同理。pydantic 等价物是 `extra="allow"` + `model_extra` 属性(dict 或 None)。**预判**:`model_validate({"campaign_id":"x","tips":["hi"]})` 的 `tips` 必须落到 patch 而非被拒。**钉死**:`test_patch_captures_extra_keys` + `test_to_wire_flattens_patch` + `test_extra_keys_preserved`。

**坑 6 — tolerant deserialiser 的 bool 是 int 子类**
Rust `de_opt_u32_tolerant` 的 `visit_bool` arm 返回 None(bool 不是 u32)。Python `bool` 是 `int` 子类,`isinstance(True, int)` 为真——若不显式排除,True/False 会被 u32 解析器当成 1/0 放行。**预判**:`_tolerant_u32(True)` 若不先 `isinstance(v, bool)` 检查会返回 1。**修复**:`_tolerant_u32` 和 `_tolerant_bool` 都先 `isinstance(v, bool)` 短路。**钉死**:`test_tolerant_bool_true_passes` + `test_tolerant_u32_in_range`。

**坑 7 — pydantic v2 lax 模式 int→str 强转(测试断言修正)**
Rust serde 对 `Option<String>` 收到 int 会失败 → tolerant deserialiser 丢弃。但 pydantic v2 默认 lax 模式会把 int **强转**为 str(`{"id":999}` → `id="999"`,不抛错)。**预判**:`test_one_bad_item_does_not_poison` 若用 `{"id":999}` 当坏项,断言会错(项不会被丢弃)。**修复**:改用 `{"message":{"nested":"dict"}}`(`str | None` 收到 dict 必抛,可靠触发丢弃)。这条是**测试侧**的 pydantic 行为对齐,非实现侧——实现忠实复刻 Rust tolerant 语义(逐项 try/except 丢弃)。

**坑 8 — 5 个 skip_serializing_if 字段的字节差异(YAGNI)**
`RemoteSettings` 有 5 个 goal 字段 + 1 个 vec 在 Rust 用 `skip_serializing_if`(空时省略),pydantic 默认 dump 会发 `null` / `[]`。这是**on-the-wire 字节差异**,但**往返语义相同**(每个字段都有 `#[serde(default)]`,缺失与 null 反序列化等价)。**决策**:不追求 143 字段手写 `to_wire`(YAGNI),在 types.py docstring 记录该差异。`RemoteSettings` 几乎只在**反序列化**侧消费(解析代理响应),从不持久化。

### 验证

三重验证全绿:

```bash
# 1. ruff lint(行长 100,E/F/W/I/B/UP)
cd "/d/工作/城建院/mm code/agent" && uv run ruff check minimax_code/config_types/ tests/test_config_types.py
# → All checks passed!(`--fix` 修复 2 个 I001 import 排序 + 1 个 F401 未用导入后干净)

# 2. R66 专项测试
cd "/d/工作/城建院/mm code/agent" && uv run pytest tests/test_config_types.py -v
# → 104 passed in 0.57s(修正 1 个测试断言后全绿:tool=EDIT 而非 ANY)

# 3. 全量回归(零回归)
cd "/d/工作/城建院/mm code/agent" && uv run pytest
# → 2022 passed, 10 skipped in 93.39s
#    (R65 的 1918 + R66 新增 104,完美对账,零回归)
```

测试增长:R65 的 1918 → R66 的 2022(+104 R66 新增,零回归)。

**wire 保真交叉验证**:Grep grok 源码(permission.rs `rename_all="lowercase"`、flags.rs `serialize_all="snake_case"`、lib.rs `deserialize_tolerant_*`、mcp.rs `#[serde(flatten)]`、memory.rs `deserialize_clamped_unit`)逐行确认 Python 实现语义一致。

### YAGNI 边界

本轮明确不做:

- ❌ **`RemoteSettings` 143 字段手写 `to_wire`** —— 5 个 skip_serializing_if 字段的字节差异往返语义相同(坑 8),`RemoteSettings` 几乎只在反序列化侧消费,从不持久化。手写 143 字段 to_wire 是过度工程。
- ❌ **`to_acp_mcp_server(name)`** —— 依赖 `agent-client-protocol` crate,本项目无 ACP 消费端。
- ❌ **`oauth_config() -> McpOAuthConfig`** —— 依赖 `xai_grok_mcp::oauth_config`,OAuth 运行时在 MCP 客户端层,不在类型层。
- ❌ **`tracing::warn!` 调用** —— 类型层纯逻辑,日志属于消费层。tolerant deserialiser 的 warn 在 Rust 里记丢弃事件,Python 侧静默丢弃(语义已通过测试覆盖)。
- ❌ **接入 IPC handler 或 config 加载器** —— 类型层先行。`MemoryConfig::resolve()`(依赖 toml + shell flag 解析)留在 shell 层,本轮只迁移叶子 struct。
- ❌ **前端 `web/src/types/` 镜像** —— 纯后端类型契约,无 wire 事件,前端暂不需要。
- ❌ **不修复预存 ruff 债务** —— 全量 `ruff check .` 报 337 错误(预存,与 R66 无关)。R66 八文件 ruff 全绿即可(轮次独立原则)。

### Commit

`feat(platform): R66 runtime config type contract layer (fuse grok xai-grok-config-types, 6 source files 2741 lines → 7 modules: flags/permission/pool/memory/mcp/types + facade, ConfigSource+ToolFilter wire fidelity + CWE-1188 Deny default + serde flatten/untagged/tolerant/clamped patterns + 143-field RemoteSettings, 104 tests zero-regression)`

## R67 — 远程 workspace API 叶子 wire 类型契约层(融合 grok xai-grok-workspace-types 叶子层)

锚点:R66 `4aa9236`

### 本轮目标

融合 grok `xai-grok-workspace-types` crate 的**叶子层**(types/ 14 文件 1168 行 + lib.rs 116 + error.rs 593 + identity.rs 163 + metadata.rs 243 = **2283 行 Rust**)→ MiniMax Code **远程工作区 API wire 类型契约层**。这是与 R64(管理平面 hooks/plugins DTO)、R65(工具平面 schema 词汇表)、R66(运行时配置类型)对称的**第四个**"类型契约"表面,四者共同构成平台外壳的**类型契约四件套**:

- **R64** 回答 *shell 如何被扩展*(hooks / plugins / MCP / marketplace wire DTO)。
- **R65** 回答 *有哪些工具*(tool / argument / type-tag / JSON-Schema 词汇)。
- **R66** 回答 *如何被配置*(运行时叶子配置值 + RemoteSettings 代理 payload)。
- **R67** 回答 *远程如何被表达*(workspace session / tool call / hunk / chunk / progress / error 的 wire 线格式)。

落地 `agent/minimax_code/workspace_types/` 新顶层模块(与 `extensions/`、`tool_types/`、`config_types/` 并行),pure types + pure logic 零 I/O。前向迁移到 Python(pydantic v2 + StrEnum + 透明 str 新类型 + 自定义 BTreeMap dict 子类 + adjacent-tagged enum 基类),不需要 Rust 工具链。

叶子层先行:rpc/ 14 文件(4379 行,请求/响应 envelope)和 tests/wire_round_trip.rs(959 行)留待 R68+ 分轮迁移——它们依赖叶子层先就位。

### 融合结论 ✅

完整迁移成功。`xai-grok-workspace-types` 叶子层(types/ + lib.rs + error.rs + identity.rs + metadata.rs)2283 行 Rust 前向迁移到 21 个 Python 文件(2071 行),保真度通过 62 个测试钉死:

- `identity.rs` → `identity.py`:`SessionId` / `ToolCallId` / `HunkId` 三个 `#[serde(transparent)]` String 新类型(str 子类,自定义 `__new__` + `__get_pydantic_core_schema__` pydantic hook,序列化为裸字符串)。
- `metadata.rs` → `metadata.py`:`Metadata`(`BTreeMap<String,String>` 透明新类型,dict 子类 + `to_wire` 排序)+ 6 个 header 常量(`x-workspace-session-id` 等)+ `STANDARD_META_KEYS`。
- `error.rs` → `errors.py`:`WorkspaceError` 13 变体(adjacent-tagged)+ 13 工厂方法 + Display 模板(每个变体一行格式化串)。
- `lib.rs` ChunkKind → `chunk_kind.py`:29 变体 StrEnum(标记 chunk 类型)。
- `types/` 13 leaf → `types/` 13 模块 + barrel:`config` / `files` / `git` / `hunk` / `interaction` / `memory` / `permission` / `plan_mode` / `plugins` / `search` / `session` / `skills` / `tools`。
  - `tools.rs`:`ToolOutputChunk`(bytes_as_base64)+ `ToolProgress`(adjacent-tagged started/status/percent)+ `ToolCallResult` + `ToolDef`。
  - `interaction.rs`:`UserQuestionOption`(preview skip_serializing_if,to_wire 覆盖)+ `UserQuestion`。
  - `session.rs` / `hunk.rs` / `permission.rs`:各含一个 adjacent-tagged enum + 线格式 struct。
- 新增 `_wire.py`(共享 `WireModel` + `sort_mappings`)+ `_tagged.py`(共享 `AdjacentTagged` 基类):消重 13+ 个 adjacent-tagged enum 的样板。

零回归:全量 **2084 passed / 10 skipped**(R66 的 2022 + R67 新增 62,完美对账)。

### 交付

| 文件 | 行数 | 职责 |
|------|------|------|
| `agent/minimax_code/workspace_types/__init__.py` | 163 | barrel facade,按子模块分组导出(身份/元数据/错误/chunk/types) |
| `agent/minimax_code/workspace_types/_wire.py` | 72 | `WireModel`(populate_by_name + to_wire + default)+ `sort_mappings`(递归 BTreeMap 排序) |
| `agent/minimax_code/workspace_types/_tagged.py` | 98 | `AdjacentTagged` 基类(adjacent `{"type","data"}` 线格式 + variant 校验 + 工厂命名空间) |
| `agent/minimax_code/workspace_types/identity.py` | 95 | `SessionId`/`ToolCallId`/`HunkId` 透明 str 新类型 + pydantic core_schema hook |
| `agent/minimax_code/workspace_types/metadata.py` | 104 | `Metadata`(BTreeMap dict 子类 + to_wire 排序)+ 6 header 常量 |
| `agent/minimax_code/workspace_types/errors.py` | 289 | `WorkspaceError` 13 变体 + 13 工厂 + Display 模板 |
| `agent/minimax_code/workspace_types/chunk_kind.py` | 80 | `ChunkKind` 29 变体 StrEnum |
| `agent/minimax_code/workspace_types/types/__init__.py` | 122 | types barrel facade |
| `agent/minimax_code/workspace_types/types/config.py` | 122 | workspace config wire struct |
| `agent/minimax_code/workspace_types/types/files.py` | 52 | 文件操作 wire struct |
| `agent/minimax_code/workspace_types/types/git.py` | 90 | git 操作 wire struct |
| `agent/minimax_code/workspace_types/types/hunk.py` | 69 | `Hunk` + `HunkAction` adjacent-tagged |
| `agent/minimax_code/workspace_types/types/interaction.py` | 97 | `UserQuestion` + `UserQuestionOption`(preview skip) |
| `agent/minimax_code/workspace_types/types/memory.py` | 32 | memory wire struct |
| `agent/minimax_code/workspace_types/types/permission.py` | 71 | permission wire struct + enum |
| `agent/minimax_code/workspace_types/types/plan_mode.py` | 74 | plan mode wire struct |
| `agent/minimax_code/workspace_types/types/plugins.py` | 56 | plugins wire struct |
| `agent/minimax_code/workspace_types/types/search.py` | 91 | search wire struct |
| `agent/minimax_code/workspace_types/types/session.py` | 120 | session wire struct + enum |
| `agent/minimax_code/workspace_types/types/skills.py` | 37 | skills wire struct |
| `agent/minimax_code/workspace_types/types/tools.py` | 137 | `ToolOutputChunk`(base64)+ `ToolProgress` + `ToolCallResult` + `ToolDef` |
| `agent/tests/test_workspace_types.py` | 543 | 62 测试(身份裸字符串/pydantic 字段往返、Metadata BTreeMap 排序、ChunkKind 29 变体、各 adjacent-tagged enum 拒绝+相等+哈希、WorkspaceError 13 工厂+Display、ToolOutputChunk base64 往返+epoch 默认、13 types 模块往返、UserQuestionOption preview 跳过) |

### 映射决策树 + 坑

```
grok xai-grok-workspace-types 叶子层(serde → pydantic v2 + 自定义基类)
  │
  ├─ #[serde(transparent)] String 新类型
  │     └─> str 子类 + 自定义 __new__ + __get_pydantic_core_schema__
  │           └─ SessionId / ToolCallId / HunkId → 裸字符串(坑 1)
  │
  ├─ #[serde(transparent)] BTreeMap<String,String>
  │     └─> dict 子类 + to_wire() 排序键
  │           └─ Metadata(坑 4 三元反转)
  │
  ├─ #[serde(tag="type", content="data")] adjacent tagging
  │     └─> AdjacentTagged 基类(_VARIANTS 元组 + 工厂 classmethod)
  │           ├─ WorkspaceError 13 变体
  │           ├─ ToolProgress 3 变体
  │           ├─ HunkAction 3 变体
  │           └─ session / permission / plan_mode 内嵌 enum 等
  │
  ├─ #[derive(Default)] + #[serde(default)] struct
  │     └─> WireModel + default() classmethod
  │           └─ 必填字段 default() 提供 0 值("" / ToolCallId(""))
  │
  ├─ bytes_as_base64 自定义 serde module
  │     └─> field_serializer(when_used="json") + field_validator(mode="before")
  │           └─ ToolOutputChunk.bytes(坑 2 字段名遮蔽 + 坑 3 UTF-8 默认)
  │
  ├─ skip_serializing_if
  │     └─> 子类覆盖 to_wire()
  │           └─ UserQuestionOption.preview(空时省略)
  │
  ├─ strum EnumVariants + Display
  │     └─> StrEnum + 工厂 classmethod + Display 模板字符串
  │           └─ ChunkKind 29 变体 / WorkspaceError 13 变体
  │
  └─ 跨 crate 依赖分类
        ├─ serde / serde_json     → pydantic v2
        ├─ bytes                  → base64 自定义序列化
        ├─ chrono DateTime<Utc>   → datetime + UTC epoch 默认
        ├─ BTreeMap               → dict 子类 + sort
        ├─ indexmap               → dict 有序
        └─ rpc/ + tests/          → R68+ 后续轮次(YAGNI 本轮)
```

**坑 1 — pydantic v2 不支持带自定义 `__new__` 的 str 子类**
三个透明 String 新类型用 `__new__` 保持 str 值不可变(并加 `__slots__ = ()`)。但 pydantic v2 **不**自动支持这种子类——收集模型时抛 `PydanticSchemaGenerationError: Unable to generate pydantic-core schema for HunkId`。**预判**:`ToolOutputChunk.call_id: ToolCallId` 字段会让整个模型无法构建。**修复**:`__get_pydantic_core_schema__` 类方法返回 `core_schema.no_info_after_validator_function(cls, core_schema.str_schema())`——先按 str 校验输入,再用 `cls` 重包装;底层 `str_schema` 驱动序列化,所以 wire 仍是裸字符串。**钉死**:`test_identity_round_trips_as_bare_string`(json `"s1"` ⇄ SessionId)。

**坑 2 — PEP 563 注解字符串化下的 `bytes: bytes` 字段名/类型遮蔽**
`from __future__ import annotations` 把所有注解字符串化。`ToolOutputChunk` 有个字段名叫 `bytes`(Rust 线键 `bytes: Vec<u8>`),注解也是 `bytes`——但字段的默认值 `b""` 是**类属性**,会遮蔽 `bytes` 类型名,pydantic 解析时拿到 `unevaluable-type-annotation` / `PydanticUserError: field name clashing with type annotation`。**诊断**:坑 1 修复后 call_id 解析成功,错误前移到 bytes 字段——证明 hook 生效。**修复**:模块级别名 `_RawBytes = bytes`,字段写成 `bytes: _RawBytes = b""`(线键 `bytes` 保留,类型解析正确)。**钉死**:`test_tool_output_chunk_field_named_bytes`。

**坑 3 — pydantic v2 bytes 字段 json 模式默认 UTF-8 解码,不是 base64**
Rust `ToolOutputChunk.bytes` 用自定义 `bytes_as_base64` serde module(线格式是 RFC-4648 base64 字符串,不是 int 数组)。但 pydantic v2 的 bytes 字段在 `model_dump(mode="json")` 下是 **UTF-8 解码**——非 UTF-8 payload(如 `b"\x80"`)会抛 `UnicodeDecodeError: 'utf-8' codec can't decode byte 0x80`。**诊断**:最小复现确认默认行为是 UTF-8;`_wire.py` docstring 原本错误地声称 bytes 自动 base64。**修复**:`field_validator("bytes", mode="before")`(json 路径收到 base64 `str` 就 `b64decode`,dict 路径收到原始 bytes 放行)+ `field_serializer("bytes", when_used="json")`(`b64encode` → ascii `str`)。同时修正 `_wire.py` docstring 澄清"bytes 默认非 base64,仅 ToolOutputChunk 覆盖"。**钉死**:`test_tool_output_chunk_bytes_base64_round_trip`(随机非 UTF-8 bytes 往返)。

**坑 4 — Metadata `__init__` 三元表达式反转**
`Metadata.__init__(items=None)` 初版写成 `super().__init__(items if items is None else dict(items))`——逻辑反了,当 `items=None` 时把 `None` 传给 `dict.__init__`,抛 `TypeError: 'NoneType' object is not iterable`。**修复**:`items if items is not None else ()`(None 时传空元组)。**钉死**:`test_metadata_empty_construction`(`Metadata()` 不抛)。

**坑 5 — BTreeMap 键排序 vs dict 插入序**
Rust `Metadata` 是 `BTreeMap<String,String>`,线格式按键字典序排序。Python dict 保插入序,直接 dump 会乱序——字节哈希不稳定。**修复**:`Metadata.to_wire()` 返回 `{k: self[k] for k in sorted(self.keys())}`;`WireModel.to_wire()` 用 `sort_mappings` 递归排序所有嵌套 map(保留 list 序)。**钉死**:`test_metadata_keys_sorted_lexicographically`(`traceparent` < `x-workspace-prompt-index` < `x-workspace-session-id`)。

### 验证

三重验证全绿:

```bash
# 1. ruff lint(行长 100,E/F/W/I/B/UP)
cd "/d/工作/城建院/mm code/agent" && uv run ruff check minimax_code/workspace_types/ tests/test_workspace_types.py
# → All checks passed!(`--fix` 修复 I001 导入排序 + F401 未用 Field/AdjacentTagged + UP017 timezone.utc→UTC 后干净)

# 2. R67 专项测试
cd "/d/工作/城建院/mm code/agent" && uv run pytest tests/test_workspace_types.py -q
# → 62 passed in 0.21s(修正 identity pydantic hook + bytes base64 序列化 + Metadata 三元反转后全绿)

# 3. 全量回归(零回归)
cd "/d/工作/城建院/mm code/agent" && uv run pytest -q
# → 2084 passed, 10 skipped in 93.94s
#    (R66 的 2022 + R67 新增 62,完美对账,零回归)
```

测试增长:R66 的 2022 → R67 的 2084(+62 R67 新增,零回归)。

**wire 保真交叉验证**:Grep grok 源码(identity.rs `#[serde(transparent)]`、metadata.rs `BTreeMap`、error.rs `#[serde(tag="type", content="data")]`、tools.rs `bytes_as_base64` 自定义 module、interaction.rs `skip_serializing_if`)逐行确认 Python 实现语义一致;`tests/wire_round_trip.rs`(959 行)的线格式断言作为对照基准。

### YAGNI 边界

本轮明确不做:

- ❌ **rpc/ 14 文件(4379 行)迁移** —— 请求/响应 envelope 依赖叶子层先就位,留 R68+。本轮只迁移叶子类型(types/ + identity + metadata + error + chunk_kind + lib)。
- ❌ **tests/wire_round_trip.rs(959 行)移植** —— grok 端的跨 struct 往返测试,作为 Python 侧 62 测试的对照基准,不直接移植(已有等价覆盖)。
- ❌ **requests/ + events/ + chunks/ 完整 struct 体** —— 部分嵌套 struct 留待 rpc/ 一并迁移时补全。
- ❌ **接入 IPC handler 或 workspace transport** —— 类型层先行。wire DTO 的消费端(远程 workspace client)在 shell 层,本轮只迁移线格式类型。
- ❌ **前端 `web/src/types/` 镜像** —— 纯后端类型契约,无 wire 事件广播到前端,暂不需要。
- ❌ **不修复预存 ruff 债务** —— 全量 ruff 报预存错误(与 R67 无关)。R67 21 文件 ruff 全绿即可(轮次独立原则)。

### Commit

`feat(platform): R67 remote workspace API leaf wire type contract layer (fuse grok xai-grok-workspace-types leaf tier, types/ 14 files 1168 + lib 116 + error 593 + identity 163 + metadata 243 = 2283 lines → 21 modules: identity/metadata/errors/chunk_kind + 13 leaf types + _wire/_tagged bases, transparent str newtype pydantic hook + bytes_as_base64 + BTreeMap sorted dict + adjacent-tagged enum base, 62 tests zero-regression)`

---

## R68 — 远程 workspace RPC 地基 wire 契约层(融合 grok xai-grok-workspace-types rpc/ 地基)

锚点:R68-1 af3270f

### 本轮目标

融合 workspace-types `rpc/` **地基层**(mod.rs 49 + envelope.rs 112 + session.rs 89 + agents_md.rs 59 = **309 行 Rust**)→ 新子包 `agent/minimax_code/workspace_types/rpc/`(4 文件)。这是 R67 叶子层(types/identity/metadata/error)之后的 **RPC 消费层起步**:定义 `WorkspaceRpc` 协议(METHOD ClassVar + Response 关联类型)+ `RpcEnvelope[T]` **externally-tagged** 响应信封(`{"ok":T}`/`{"err":{code,message}}`,区别于 R67 的 adjacent-tagged `{"type","data"}`)+ 两个最小业务 RPC(session prompt 追踪 3 方法 + agents_md 发现)。

rpc/ 剩余 10 文件(fs 754 / git 1077 / hooks 230 / hunks 413 / search 226 / skills 275 / workspace 271 / worktree 406 / code_nav 125 / deploy 100 = 3877 行)留 R69+。

### 融合结论

R67 是 wire DTO 的**原子**(叶子类型);R68 是消费这些原子的 **RPC 请求/响应骨架**。两者构成 xai-grok-workspace-types 的"类型契约四件套"(扩展 R64 / 工具类型 R65 / 配置类型 R66 / workspace 叶子 R67)之后的第五个表面——**RPC 消费层**。

**trait → Protocol 映射**:Rust `WorkspaceRpc` trait(associated const `METHOD: &'static str` + associated `type Response: Serialize + DeserializeOwned + Send`)映射为 Python `@runtime_checkable class WorkspaceRpc(Protocol)`(声明 `METHOD: ClassVar[str]`)+ 每个请求 struct 的 `Response: ClassVar[type]` 类属性(如 `BeginPromptReq.Response is type(None)`、`RewindToReq.Response is FileRewindResponse`、`DiscoverAgentsMdReq.Response == list[AgentConfigFile]`)。客户端/服务器共用同一 struct 表示同一方法的设计得以保留。

**envelope 表示抉择**:Rust `RpcEnvelope<T>` 是 serde 默认的 **externally-tagged** enum(variant 名是唯一 key)。这与 R67 的 adjacent-tagged `#[serde(tag="type", content="data")]` 本质不同——判别前者是 key 本身,后者是内部字段。pydantic discriminated union 需要内部 discriminator 字段,不支持 externally-tagged,故 R68 用独立泛型类(`Generic[T]` + 手写 `to_wire`/`from_wire`)而非复用 R67 的 `AdjacentTagged` 基类。

### 交付

4 文件,309 行 Rust → 约 290 行 Python + 48 个专项测试:

| 文件 | Rust 源 | 行数 | Python 实现 |
|------|---------|------|------------|
| `rpc/__init__.py` | `rpc/mod.rs` | 49 | `WorkspaceRpc` Protocol(METHOD ClassVar)+ 4 tool ID 常量(workspace_rpc / workspace_events / workspace_tool_notifications / workspace_client_ext_notifications)+ barrel 重导出 |
| `rpc/envelope.py` | `rpc/envelope.rs` | 112 | `RpcEnvelope[T]` Generic(externally-tagged `{"ok"}`/`{"err"}}`)+ `RpcError`(code+message + is_turn_active + `__str__`)+ `TURN_ACTIVE` 常量 + `_dump_payload` 递归(BaseModel/list/tuple/Mapping)+ `ok`/`err_parts`/`err`/`into_result`/`to_wire`/`from_wire` |
| `rpc/session.py` | `rpc/session.rs` | 89 | `ConflictType` StrEnum(snake_case 3 变体)+ `FileRewindConflict` + `FileRewindResponse`(success/target_prompt_index/Vec 字段必填 + error Optional)+ `BeginPromptReq`/`EndPromptReq`/`RewindToReq`(METHOD + Response ClassVar) |
| `rpc/agents_md.py` | `rpc/agents_md.rs` | 59 | `AgentConfigFile`(forward-compat 忽略未知字段)+ `DiscoverAgentsMdReq`(空 body,derive Default,Response=list[AgentConfigFile]) |

测试 `tests/test_rpc.py`:48 个,覆盖 tool ID(4)+ RpcError(4)+ envelope ok wire(6)+ envelope err wire(4)+ envelope from_wire(6)+ envelope round trip(2)+ WorkspaceRpc 协议(2)+ session 方法(8)+ ConflictType(3)+ FileRewindResponse(3)+ agents_md(6)。

### 映射决策树 + 坑

**决策树**:
- `WorkspaceRpc` trait(associated const + associated type)→ `@runtime_checkable Protocol`(METHOD ClassVar)+ 每个 Req 的 `Response: ClassVar[type]` 类属性(Python 关联类型用类属性承载)。
- `RpcEnvelope<T>` externally-tagged enum → 独立 `Generic[T]` 类 + 手写 wire(pydantic 不支持 externally-tagged discriminated union)。`into_result()` 返回 `tuple[Any, RpcError | None]`(Rust `Result<T,RpcError>` 的显式对偶)。
- `()`(Rust unit type)→ `type(None)`(NoneType);`TypeAdapter(type(None))` 验证 null payload。
- `Vec<T>` Response(如 `Vec<AgentConfigFile>`)→ `list[T]` + `TypeAdapter(list[AgentConfigFile])` 批量验证。
- 空 struct `DiscoverAgentsMdReq {}`(derive Default)→ pydantic 空模型(`default()` 成功,`to_wire()` → `{}`)。
- `#[serde(rename_all="snake_case")]` enum(`ConflictType`)→ `StrEnum` 成员值即 snake_case 字符串。

**坑 1 — externally-tagged vs adjacent-tagged(serde 表示本质区别)**
初版想复用 R67 的 `AdjacentTagged`(`{"type","data"}`)基类实现 `RpcEnvelope`。但 Rust 源码 `#[serde(rename_all="snake_case")] pub enum RpcEnvelope<T> { Ok(T), Err(RpcError) }` **无** `tag`/`content` 属性——这是 serde **默认的 externally-tagged** 表示(variant 名 `ok`/`err` 是 wire 顶层唯一 key,如 `{"ok": <value>}` / `{"err": {...}}`)。adjacent-tagged 的判别是内部 `type` 字段,externally-tagged 的判别是顶层 key 本身。pydantic v2 的 `Field(discriminator=...)` 需要内部字段,无法表达 externally-tagged。**修复**:独立 `Generic[T]` 类 + 手写 `to_wire()`(`{"ok": _dump_payload(self._ok)}` 或 `{"err": {"code":..,"message":..}}`)+ `from_wire(data, response_type)`(按 key 分发)。

**坑 2 — `WorkspaceRpc` Protocol 非方法成员:issubclass 被 typing 禁止**
`WorkspaceRpc` 的 `METHOD` 是 `ClassVar[str]`(数据成员,非方法)。`@runtime_checkable` Protocol 对**非方法成员**的 `issubclass()` 直接抛 `TypeError: Protocols with non-method members don't support issubclass()`。同时请求 struct 有必填字段(session_id/prompt_index),`req()` 实例化抛 ValidationError,`isinstance(req(), WorkspaceRpc)` 也走不通。**修复**:协议契约验证改用 `hasattr(req, "METHOD")` + `isinstance(req.METHOD, str)` + `req.METHOD.startswith("workspace.")` 直接检查类属性。`isinstance(NoMethod(), WorkspaceRpc)` 仍可用于"无 METHOD 的类不满足协议"的反例测试(runtime_checkable 的 isinstance 检查实例属性存在,对非方法成员支持)。

**坑 3 — `RpcEnvelope` 泛型 `T` 运行时不可恢复**
Rust 在编译期通过 associated `type Response` 绑定 `T`,`into_result()` 静态返回 `Result<T, _>`。Python 泛型 `Generic[T]` 在运行时擦除——`from_wire({"ok": {...}}, ?)` 不知道把 dict 反序列化成什么类型。**修复**:`from_wire(data, response_type)` 要求调用方显式传 Rust 的 `type Response`(如 `FileRewindResponse`、`list[AgentConfigFile]`、`type(None)`),用 `pydantic.TypeAdapter(response_type).validate_python(data["ok"])` 处理 struct / Vec / unit / primitive 四种形状。这是 Rust 编译期类型绑定在 Python 的"调用点显式传类型"忠实映射。

**坑 4 — `list[X] is list[X]` 为 `False`(GenericAlias 不 intern)**
初版测试 `assert DiscoverAgentsMdReq.Response is list[AgentConfigFile]` 失败:`list[AgentConfigFile]` 每次 `__class_getitem__` 构造新的 `GenericAlias` 对象,CPython 不缓存,`is` 比较为 `False`。**修复**:改用值比较 `==`(GenericAlias 的 `__eq__` 比较 `(origin, args)`)。`list[AgentConfigFile] == list[AgentConfigFile]` → `True`。

**坑 5 — `RpcError` 不能继承 `Exception`(pydantic 与 Exception 元类冲突)**
Rust `RpcError` 实现 `std::error::Error`(可 `?` 传播 + `Display`)。初版想让 Python `RpcError` 同时是 pydantic 模型 + `Exception` 子类(以便 `raise`)。但 pydantic `BaseModel` 的 `ModelMetaclass` 与 `Exception` 的 `type` 元类不兼容(多继承元类冲突)。**修复**:`RpcError` 保持纯 pydantic 数据模型,`__str__` 实现 Display 契约;`RpcEnvelope.into_result()` 返回 `tuple[Any, RpcError | None]` 显式对偶(而非 raise),调用方模式匹配而非 `except`。

### 验证

三重验证全绿:

```bash
# 1. ruff lint(行长 100,E/F/W/I/B/UP)
cd "/d/工作/城建院/mm code/agent" && uv run ruff check minimax_code/workspace_types/rpc/ tests/test_rpc.py
# → All checks passed!(`--fix` 修复 I001 导入排序 + 修正 list 闭合括号手误后干净)

# 2. R68 专项测试
cd "/d/工作/城建院/mm code/agent" && uv run pytest tests/test_rpc.py -q
# → 48 passed in 0.26s(修正 Protocol 非方法成员 issubclass 禁止 + GenericAlias 不 intern 后全绿)

# 3. 全量回归(零回归)
cd "/d/工作/城建院/mm code/agent" && uv run pytest -q
# → 2132 passed, 10 skipped in 104.19s
#    (R67 的 2084 + R68 新增 48,完美对账,零回归)
```

测试增长:R67 的 2084 → R68 的 2132(+48 R68 新增,零回归)。

**wire 保真交叉验证**:Grep grok 源码(envelope.rs `#[serde(rename_all="snake_case")] pub enum RpcEnvelope<T>` 无 tag/content = externally-tagged、session.rs `ConflictType` snake_case、agents_md.rs 空 struct derive Default + 忽略未知字段测试)逐行确认 Python 实现语义一致。

### YAGNI 边界

本轮明确不做:

- ❌ **rpc/ 剩余 10 文件(3877 行)迁移** —— fs/git/hooks/hunks/search/skills/workspace/worktree/code_nav/deploy,每个文件有自己的请求/响应 struct 群,留 R69+(本轮只迁地基层 mod + envelope + 两个最小业务 RPC)。
- ❌ **`tests/wire_round_trip.rs`(959 行)移植** —— grok 端跨 struct 往返测试,作为 Python 侧 48 测试的对照基准,不直接移植。
- ❌ **接入 IPC handler 或 workspace transport** —— 类型契约层先行。wire DTO 的消费端(远程 workspace client)在 shell 层,本轮只迁移请求/响应骨架类型,不接 IPC。
- ❌ **requests/ + events/ + chunks/ 完整 struct 体** —— 部分 RPC 的嵌套请求/响应 struct 留待对应 rpc 文件迁移时补全。
- ❌ **前端 `web/src/types/` 镜像** —— 纯后端 RPC 类型契约,无 wire 事件广播到前端,暂不需要。
- ❌ **`RpcEnvelope` 的 `into_result()` raise 语义** —— Rust `Result` 的 `?` 传播在 Python 用显式 tuple 对偶表达(RpcError 非 Exception),不引入额外 Result 包装类型。

### Commit

`feat(platform): R68 remote workspace RPC foundation wire contract layer (fuse grok xai-grok-workspace-types rpc/ root, mod.rs 49 + envelope.rs 112 + session.rs 89 + agents_md.rs 59 = 309 lines → 4 modules: rpc/__init__ WorkspaceRpc Protocol + 4 tool IDs + envelope RpcEnvelope[T] externally-tagged Generic + RpcError + TURN_ACTIVE + session 3 prompt RPCs + ConflictType StrEnum + FileRewindResponse + agents_md discovery RPC + AgentConfigFile forward-compat, externally-tagged vs adjacent-tagged distinction + Protocol non-method-member issubclass ban + Generic[T] runtime type-erasure from_wire response_type param, 48 tests zero-regression)`

---

## R69 — 远程 workspace RPC envelope 双侧消费层(融合 grok xai-grok-workspace-types rpc/ code_nav + deploy)

锚点:R69-1 566324f

### 本轮目标

迁移 `code_nav.rs`(125L)+ `deploy.rs`(100L)= **225 行 Rust** → 2 个新模块,作为 R68 envelope 的**首批业务消费者**,双侧对称:

- **Ok 侧 — code_nav**:5 个 `workspace.code_*` RPC(goto_definition / goto_references / find_definitions / find_references / index_status)+ 4 个响应 struct(CodeNavResponse[Vec locations] / CodeIndexStatusResponse[Option 字段] / CodeNavLocation[skip_serializing_if] / CodeIndexStats)。
- **Err 侧 — deploy**:`DeployError` 15 码词汇表(URL_CONFLICT…FAILED_PRECONDITION),`wire_code()`/`from_wire_code()` 双向转换 + `ALL` 穷举常量,是 `RpcError.code` 开放字符串的闭合子词汇表。

R68 定义了 envelope 骨架(Ok/Err 两臂 + WorkspaceRpc 协议)和 2 个简单 RPC;**R69 把 envelope 的成功/错误两个消费模式用真实业务类型完整展示**。rpc/ 剩余 8 文件(fs 754 / git 1077 / hooks 230 / hunks 413 / search 226 / skills 275 / workspace 271 / worktree 406 = ~3230 行)留 R70+。

### 融合结论

R68 是 envelope 的**生产者**(定义 Ok/Err wire 形状);R69 是 envelope 的**首批消费者**,双侧互补证明 envelope 设计的正确性:

- **Ok 臂承载结构化业务响应**:code_nav 的 5 RPC 各自绑定 `Response: ClassVar[type]`(CodeNavResponse × 4 导航方法 + CodeIndexStatusResponse × 1 状态探测),展示 envelope Ok 侧的三种 payload 形状(嵌套 struct / Vec / Option 字段 struct)。
- **Err 臂承载错误码 + message**:deploy 的 15 码是 `RpcError.code` 这个**开放字符串字段**的**闭合子词汇表**——不是所有 Err 都是 deploy 错误(还有 hub_error / session_not_found / turn_active 等),但 deploy 域内是穷举闭合的。`wire_code()`/`from_wire_code()` 让闭合词汇表与开放 code 字段双向桥接。

code_nav(成功响应)+ deploy(错误码)共同回答了"envelope 两臂分别承载什么"——这是 R68 留下的开放问题,本轮用真实业务类型闭合。

### 交付

2 源文件 + barrel 扩展 + 测试扩展,225 行 Rust → 约 235 行 Python + 22 个新增专项测试:

| 文件 | Rust 源 | 行数 | Python 实现 |
|------|---------|------|------------|
| `rpc/code_nav.py` | `rpc/code_nav.rs` | 125 | 5 Req(METHOD + Response ClassVar)+ `CodeNavLocation`(symbol `skip_serializing_if` → 覆盖 `to_wire` 用 `exclude_none`)+ `CodeNavResponse`(locations Vec)+ `CodeIndexStats` + `CodeIndexStatusResponse`(active 必填 + file_count/stats Optional) |
| `rpc/deploy.py` | `rpc/deploy.rs` | 100 | `DeployError` Enum(**15 变体,value 即 wire code,非 StrEnum**)+ `wire_code()`(= self.value)+ `from_wire_code()`(cls(code) try/except → None)+ `ALL`(类后赋值 tuple) |
| `rpc/__init__.py` | mod.rs barrel | — | 重导出 code_nav 9 符号 + DeployError(barrel + __all__ 同步) |
| `tests/test_rpc.py` | — | — | +22 测试:`TestCodeNav`(13,method 常量/response 类型/wire/skip_serializing_if/envelope Ok 集成)+ `TestDeployError`(9,wire_code/from_wire_code 往返/ALL 穷举/拒绝未知码/非 StrEnum/envelope Err 集成) |

测试增长:R68 的 48 → R69 的 70(+22),全量 R68 的 2132 → R69 的 2154(+22,零回归)。

### 映射决策树 + 坑

**决策树**:
- `#[serde(skip_serializing_if = "Option::is_none")]`(CodeNavLocation.symbol)→ 覆盖 `to_wire()` 用 `exclude_none=True`(R67 已确立的 `UserQuestionOption.preview` 先例,文档化在 `_wire.py:13-15`)。WireModel 基类 `to_wire` 默认输出所有字段含 null;有 skip 的 struct 覆盖。
- `DeployError` enum → **普通 `Enum`** 而非 `StrEnum`:成员名(`UrlConflict`)与 wire code(`deploy_url_conflict`)是 grok `wire_code()` 的**显式 match 映射**,非名字推导。故 value 直接存 wire code,`wire_code()` = `self.value`,`from_wire_code()` = `cls(code)` + try/except `ValueError` → `None`。
- `Option<PathBuf>`(code_nav 的 root)→ `str | None`(wire 上 serde 把 PathBuf 序列化为路径字符串,Python 用 `str` 承载,不用 `pathlib.Path`——wire 是 JSON 字符串)。`#[serde(default)]` → 默认 `None`。
- `usize`(line/col/file_count/files/definitions/references)→ `int`。
- `DeployError::ALL`(Rust `pub const ALL: [DeployError; 15]`)→ **类后赋值** `DeployError.ALL = tuple(DeployError)`(Enum 类体内不能 `tuple(cls)`,需类体闭合后;模块级语句合法)。

**坑 1 — `skip_serializing_if` 与 `exclude_none` 的等价边界**
CodeNavLocation.symbol 的 `#[serde(skip_serializing_if = "Option::is_none")]` 要求 None 时 wire 不输出 symbol key。pydantic 基类 `to_wire()` 默认输出 `"symbol": null`。覆盖用 `exclude_none=True`——但这会排除**所有** None 字段。CodeNavLocation 只有 symbol 是 Optional(path/line 必填),所以 `exclude_none` 与逐字段 skip 等价,安全。**前提**:覆盖前确认模型内所有 Optional 字段都想要 skip 行为(R67 的 `_wire.py` 文档已约束:只有这种 struct 才覆盖)。

**坑 2 — `DeployError` 误用 `StrEnum`(UP042 边界)**
ruff UP042 规则要求 `(str, Enum)` 混入用 `StrEnum`。初版可能想让 `DeployError` 继承 `StrEnum`。但:(a) `DeployError(Enum)` 是纯 Enum(value 是 str),**不**继承 str,**不触发** UP042;(b) 语义上成员名 `URL_CONFLICT` ≠ wire code `deploy_url_conflict`(显式映射,非名字推导),`StrEnum` 要求 name-value 可推导,不适用;(c) `StrEnum` 成员 `isinstance(kind, str)` 为 True,会误导(部署错误码不是字符串,是枚举值)。**修复**:保持普通 `Enum`,value 存 wire code,`isinstance(kind, str)` 为 False(测试 `test_is_plain_enum_not_strenum` 锁定)。

**坑 3 — `DeployError.ALL` 类体内定义失败**
初版想在 Enum 类体内写 `ALL = tuple(cls)`。但:(a) 类体求值时 `cls`(DeployError)尚未闭合,`tuple(cls)` 无法迭代未完成的 Enum;(b) 即使能跑,`ALL = (...)` 在 Enum 类体内会被**误当成员定义**。**修复**:类体闭合后模块级 `DeployError.ALL = tuple(DeployError)`(普通属性赋值,合法,类型用 `# type: ignore[attr-defined]` 标注)。

**坑 4 — import 排序:`CodeIndexStats` vs `CodeIndexStatusReq`**
手写 import 顺序误把 `CodeIndexStatusReq` 排在 `CodeIndexStats` 前。ruff isort 按字母序:`CodeIndexS-t-a-t-s` < `CodeIndexS-t-a-t-u-s-Req`(第 5 字符 's' < 'u'),故 `CodeIndexStats` 在前。**修复**:`ruff check --fix` 自动修正(test_rpc.py + __init__.py 两处)。教训:import 排序交给 ruff,手写易错。

### 验证

三重验证全绿:

```bash
# 1. ruff lint(E/F/W/I/B/UP,行长 100)
cd "/d/工作/城建院/mm code/agent" && uv run ruff check minimax_code/workspace_types/rpc/ tests/test_rpc.py
# → All checks passed!(`--fix` 修复 2 处 I001 import 排序后干净)

# 2. R69 专项测试
cd "/d/工作/城建院/mm code/agent" && uv run pytest tests/test_rpc.py -q
# → 70 passed in 0.40s(R68 的 48 + R69 新增 22,精确对账)

# 3. 全量回归(零回归)
cd "/d/工作/城建院/mm code/agent" && uv run pytest -q
# → 2154 passed, 10 skipped in 111.99s
#    (R68 的 2132 + R69 新增 22,完美对账,零回归)
```

**wire 保真交叉验证**:Grep grok 源码(code_nav.rs `#[serde(skip_serializing_if = "Option::is_none")]` on symbol、deploy.rs `wire_code()` 显式 match + `from_wire_code` `Option<Self>` + `ALL: [DeployError; 15]`)逐行确认 Python 实现语义一致。`test_envelope_ok_wraps_code_nav_response` + `test_envelope_err_carries_deploy_code` 两个集成测试闭合了"业务类型 ↔ envelope"的完整往返。

### YAGNI 边界

本轮明确不做:

- ❌ **rpc/ 剩余 8 文件(~3230 行)迁移** —— fs/git/hooks/hunks/search/skills/workspace/worktree,各有请求/响应 struct 群,留 R70+(本轮只迁 code_nav + deploy 这两个 envelope 双侧最小消费示例)。
- ❌ **code_nav 实际 LSP 索引/goto 引擎实现** —— 本轮仅 wire 类型契约,真正的代码索引引擎是运行时能力,不在类型层。
- ❌ **deploy 实际部署流程(构建/上传/发布)** —— 本轮仅错误码词汇表,部署编排是运行时能力。
- ❌ **接入 IPC handler 或远程 workspace transport** —— 类型契约层先行,wire DTO 的消费端(远程 workspace client)在 shell 层。
- ❌ **前端 `web/src/types/` 镜像** —— 纯后端 RPC 类型契约,无 wire 事件广播到前端。
- ❌ **`DeployError` 与其他 RpcError code 词汇表统一注册表** —— 各域错误码词汇表(session/hub/turn_active 等)独立迁移,本轮不做跨域注册表。

### Commit

`feat(platform): R69 workspace RPC envelope dual-side consumer layer (fuse grok xai-grok-workspace-types rpc/ code_nav + deploy, code_nav.rs 125 + deploy.rs 100 = 225 lines → 2 modules: code_nav 5 code_* RPCs Ok-side + CodeNavResponse/CodeIndexStatusResponse + CodeNavLocation skip_serializing_if exclude_none override + CodeIndexStats + deploy DeployError 15-code Err-side vocabulary plain Enum value=wire_code + wire_code/from_wire_code + ALL class-post-assign, skip_serializing_if exclude_none equivalence boundary + DeployError non-StrEnum explicit name↔code mapping + ALL class-body tuple(cls) ban, 22 new tests zero-regression)`


## R70 — 远程 workspace RPC search 混合命名空间层(融合 grok xai-grok-workspace-types rpc/ search.rs)

锚点:R70-1 f3d8be1

### 本轮目标

迁移 `search.rs`(226L)= **1 个混合命名空间文件** → 1 个新模块,攻克 rpc/ 层迄今最密集的 **4+1 个 serde 模式**:

- **内容搜索(`workspace.ripgrep`)**:camelCase 命名空间下的 `ContentSearchRequest`(custom-default `respect_gitignore` + 多 Option/bool 默认)+ 4 个响应 struct(`ContentMatch`[skip_serializing_if span] / `ContentMatchFile`[new 构造器推导 name] / `ContentSearchData`[Vec + 计数] / `ClientId`[camelCase 路由身份])。
- **模糊文件搜索(4 个 `workspace.fuzzy_*`)**:snake_case 命名空间下的 `FuzzyOpenReq`(携带 `TargetClientId` untagged 枚举)/ `FuzzyChangeReq` / `FuzzyCloseReq`(Response=bool)/ `FuzzyStatusReq`(Response=任意 JSON)。
- **`TargetClientId`**:`#[serde(untagged)]` 枚举(null → None 变体 / {instanceId, connId} → ClientId 变体),是本轮 serde 难度最高的类型。

R68/R69 已确立 envelope + Ok/Err 双侧消费;**R70 把 wire 层剩余 4 个 serde 模式(camelCase / untagged / custom-default / Value Response)一次性攻克**,为剩余 7 文件(尤其同样 camelCase 的 fs/git/hunks/worktree)建立可复用模板。rpc/ 剩余 7 文件(fs 754 / git 1077 / hooks 230 / hunks 413 / skills 275 / workspace 271 / worktree 406 = ~3326 行)留 R71+。

### 融合结论

R69 用 code_nav(Ok)+ deploy(Err)证明 envelope 双侧能承载结构化业务;**R70 把 wire 序列化的 4 个新模式一次性落地,证明 WireModel 基类 + pydantic v2 能完整覆盖 serde 的复杂属性**:

- **camelCase `rename_all`**:grok 内容搜索侧用 `#[serde(rename_all = "camelCase")]`,Python 侧 `ConfigDict(alias_generator=to_camel, populate_by_name=True)`——`to_wire` 的 `by_alias=True` dump 让字段名序列化为 camelCase,同时 `populate_by_name` 让 snake_case 也能解析。**关键**:`to_camel` 来自 `pydantic.alias_generators`,无需手写转换。
- **untagged 枚举**:Python 无第一类 untagged 枚举;`RootModel[ClientId | None]` + `is_none()` 方法复刻 `TargetClientId::is_none` API。wire `null` ↔ None 变体,`{instanceId, connId}` ↔ ClientId 变体,`#[default]` → 默认 None 变体。
- **custom-default fn**:grok `#[serde(default = "default_respect_gitignore")]`(返回 true,而同族 bool 默认 false)→ Python 侧直接 `respect_gitignore: bool = True`(Python 不需要默认函数,字段默认值即等价)。
- **原始/Value Response**:`type Response = String/bool/serde_json::Value` → `Response: ClassVar[type] = str/bool` 与 `Response: ClassVar = Any`;envelope `_dump_payload` 透传原始类型(scalar 原样返回),`from_wire` 用 `TypeAdapter(Any)` 解析任意 JSON。

**+1 嵌套 skip_serializing_if(关键新洞察)**:`ContentMatch.match_start/match_end` 嵌套在 `ContentSearchData.files[].matches[]` 内,但 `#[serde(skip_serializing_if)]` 在**任意嵌套深度**都生效。R67 的 `to_wire` 覆盖(exclude_none)只作用于顶层序列化,嵌套路径无效 → 本轮引入**普通 `@model_serializer`**(非 wrap 模式)手工构建 camelCase 字典,跳过 None 的 span——model_serializer 是类型级钩子,适用于所有序列化路径(含嵌套)。这是本轮最重要的架构决策。

### 交付

1 源文件 + barrel 扩展 + 测试扩展,226 行 Rust → 约 260 行 Python + 25 个新增专项测试:

| 文件 | Rust 源 | 行数 | Python 实现 |
|------|---------|------|------------|
| `rpc/search.py` | `rpc/search.rs` | 226 | 5 Req(METHOD + Response ClassVar:ripgrep→ContentSearchData / fuzzy_open→str / fuzzy_change→bool / fuzzy_close→bool / fuzzy_search→Any)+ `ClientId`(camelCase)+ `TargetClientId`(RootModel[ClientId\|None] + none()/is_none())+ `ContentMatch`(@model_serializer 嵌套 skip span)+ `ContentMatchFile`(new() 推导 name + `.`/`..` 回退)+ `ContentSearchData`(Vec + 计数) |
| `rpc/__init__.py` | mod.rs barrel | — | 重导出 search 10 符号(barrel + __all__ 同步) |
| `tests/test_rpc.py` | — | — | +25 测试:`TestSearch`(method 常量/response 类型/camelCase wire + 解析/ContentMatch 顶层+嵌套 skip/ContentMatchFile.new 三场景/ClientId 往返/TargetClientId untagged 四场景/FuzzyOpenReq snake+嵌套 camelCase/envelope 包装 str/bool/任意 JSON/ContentSearchData) |

测试增长:rpc 专项 R69 的 70 → R70 的 95(+25);全量 R69 的 2154 → R70 的 2179(+25,零回归,完美对账)。

### 映射决策树 + 坑

**决策树**:
- `#[serde(rename_all = "camelCase")]`(内容搜索 4 struct + ClientId)→ `_CAMEL = ConfigDict(populate_by_name=True, alias_generator=to_camel)`,`to_camel` 从 `pydantic.alias_generators` 导入。有效是因为 `WireModel.to_wire` 已用 `by_alias=True`。
- `#[serde(untagged)] enum TargetClientId { #[default] None, ClientId(ClientId) }` → `TargetClientId(RootModel[ClientId | None])`,`root: ClientId | None = None` + `none()` classmethod + `is_none()` 方法 + 覆盖 `to_wire`(sort_mappings 透传 None)。
- `#[serde(default = "default_respect_gitignore")]`(respect_gitignore=true)→ 简单 `respect_gitignore: bool = True`(Python 字段默认值即 grok 默认函数的等价物)。
- `type Response = String/bool/serde_json::Value` → `Response: ClassVar[type] = str/bool` 与 `Response: ClassVar = Any`(serde_json::Value 是任意 JSON)。
- `#[serde(default, skip_serializing_if = "Option::is_none")]`(ContentMatch span)→ 普通 `@model_serializer` 手工 camelCase 字典 + 条件输出 span(非 R67 的 to_wire 覆盖,因需嵌套生效)。
- `ContentMatchFile::new(path)` 的 `Path::file_name()` 推导 → `PurePosixPath(path).name`,`.`/`..`/空 回退到完整路径(`Path::file_name` 对这些返回 None)。
- `Vec<String>`(include_globs/exclude_globs)→ `Field(default_factory=list)`(避免可变默认值)。
- `#[derive(Default)]`(ContentSearchRequest/FuzzyStatusReq)的必需 String 字段 → 重写 `default()` classmethod 提供 `""`。

**坑 1 — 嵌套 skip_serializing_if,R67 模式失效(本轮最大坑)**
初版想用 R67 的 `to_wire` 覆盖(exclude_none)处理 `ContentMatch.match_start/match_end` 的 skip。但:(a) R67 覆盖只作用于**顶层**序列化;(b) `ContentMatch` 嵌套在 `ContentSearchData.files[].matches[]` 中,顶层是 ContentSearchData,ContentMatch 的 to_wire 覆盖在父级 `model_dump` 时**不被调用**;(c) `exclude_none` 在 ContentSearchData 顶层调用会误伤 files/total_matches 等字段语义。**修复**:在 ContentMatch 上用**普通 `@model_serializer`**(非 wrap 模式)手工构建 wire 字典——model_serializer 是**类型级**钩子,pydantic 在序列化 ContentMatch 实例时(无论嵌套多深)都调用它,跳过 None span。测试 `test_content_match_skip_applies_when_nested` 锁定该行为。

**坑 2 — `@model_serializer(mode="wrap")` 调用约定的不确定性**
设计阶段考虑 wrap 模式(可在 handler 基础上增删字段),但 pydantic v2 wrap-handler 的调用约定(`handler()` vs `handler(self)`)在不同子类/版本下行为有差异,确定性不足。**修复**:用**普通非 wrap** `@model_serializer`——完全手工构建字典,不依赖 handler,行为确定且冒烟测试一次通过。

**坑 3 — untagged 枚举无第一类 Python 等价物**
grok `#[serde(untagged)] enum TargetClientId` 是枚举(null/ClientId 两变体,无 tag)。Python 无 untagged 枚举;`Enum` + 自定义序列化过于繁琐。**修复**:`RootModel[ClientId | None]`——Union 类型本身在 wire 上就是 untagged(null → None,{instanceId, connId} → ClientId),`is_none()` 方法保留源 API 语义。`to_wire` 覆盖确保 `model_dump` 的 None 直接输出为 `null`(而非 `{"root": null}`)。

**坑 4 — 混合命名空间(snake_case 外壳 + camelCase 内核)**
`FuzzyOpenReq` 无 `rename_all`(snake_case),但其 `target_client_id` 字段的值是 `TargetClientId`(→ camelCase ClientId)。wire 形如 `{"target_client_id": {"connId": "c", "instanceId": "i"}}`——**外层 snake_case key + 内层 camelCase dict**。**关键**:这不是冲突,因 camelCase 只在 `TargetClientId`/`ClientId` 的 `_CAMEL` ConfigDict 上生效,FuzzyOpenReq 本身无 ConfigDict(默认 snake_case)。测试 `test_fuzzy_open_req_carries_client_in_target` 锁定。

**坑 5 — `root` 作为 BaseModel 字段名**
`FuzzyOpenReq.root: Option<PathBuf>` 用 `root` 作字段名。担心与 `RootModel.root` 冲突或被屏蔽。**验证**:pydantic v2 BaseModel 上 `root` 是合法字段名(无屏蔽,屏蔽只在 RootModel 子类)。冒烟测试 `FuzzyOpenReq(root="/r").to_wire() == {"root": "/r", ...}` 确认。

**坑 6 — import 排序:ruff isort 全量手写易错**
test_rpc.py 导入块需按 ruff isort 规则插入 10 个新符号(PascalCase 字母序:`ClientId` 在 `Code*` 前[C-l < C-o],`ConflictType` 在 `ContentMatch` 前[n < t],`ContentMatch` < `ContentMatchFile` < `ContentSearchData` < `ContentSearchRequest`)。**修复**:先手写粗排序,`ruff check --fix` 自动校准(本轮手写一次通过,但教训仍是交给 ruff)。

### 验证

三重验证全绿:

```bash
# 1. ruff lint(E/F/W/I/B/UP,行长 100)
cd "/d/工作/城建院/mm code/agent" && uv run ruff check minimax_code/workspace_types/rpc/search.py tests/test_rpc.py --fix
# → All checks passed!(search.py + test_rpc.py 一次干净,无残留)

# 2. R70 专项测试
cd "/d/工作/城建院/mm code/agent" && uv run pytest tests/test_rpc.py -q
# → 95 passed in 0.32s(R69 的 70 + R70 新增 25,精确对账)

# 3. 全量回归(零回归)
cd "/d/工作/城建院/mm code/agent" && uv run pytest -q
# → 2179 passed, 10 skipped in 102.79s
#    (R69 的 2154 + R70 新增 25,完美对账,零回归)
```

**wire 保真交叉验证**:对照 grok `search.rs` 源码逐行确认——`#[serde(rename_all = "camelCase")]` 覆盖范围(内容搜索 4 struct + ClientId)、`default_respect_gitignore() -> bool { true }`、`#[serde(untagged)] enum TargetClientId` 的 `#[default] None` + `ClientId` 变体 + `is_none()` 方法、`ContentMatch.match_start/match_end` 的 `#[serde(default, skip_serializing_if = "Option::is_none")]`、`ContentMatchFile::new` 的 `Path::file_name` 推导、4 个 grok 测试(method_constants / target_client_id_untagged_round_trip / content_match_file_new_derives_name / content_search_request_defaults)全部在 TestSearch 中复刻并扩展。`test_content_match_skip_applies_when_nested`(嵌套 skip)+ `test_envelope_ok_wraps_content_search_data`(envelope Ok 往返)+ `test_envelope_ok_wraps_arbitrary_json_response`(Value Response)三个集成测试闭合了"业务类型 ↔ 嵌套序列化 ↔ envelope"的完整链路。

### YAGNI 边界

本轮明确不做:

- ❌ **rpc/ 剩余 7 文件(~3326 行)迁移** —— fs/git/hooks/hunks/skills/workspace/worktree,留 R71+(本轮只迁 search 这一个 serde 模式最密集的混合命名空间文件)。
- ❌ **实际 ripgrep/fuzzy 搜索引擎实现** —— 本轮仅 wire 类型契约,真正的内容搜索(调用 ripgrep 子进程)与模糊搜索(文件索引/fzf 引擎)是运行时能力,不在类型层。
- ❌ **TargetClientId 跨域路由的实际 relay** —— `ClientId(instance_id, conn_id)` 是 wire 路由身份,实际的多 client 转发逻辑在 shell 扩展层。
- ❌ **接入 IPC handler 或远程 workspace transport** —— 类型契约层先行,wire DTO 的消费端在 shell 层。
- ❌ **前端 `web/src/types/` 镜像** —— 纯后端 RPC 类型契约,无 wire 事件广播到前端。
- ❌ **ClientId 与 shell-extension crate 的 ClientId 去重** —— 源码注释明确 Phase-1 独立(各 crate 各自定义),去重在 shell 集成阶段处理。

### Commit

`feat(platform): R70 workspace RPC search mixed-namespace layer (fuse grok xai-grok-workspace-types rpc/ search.rs 226 lines → 1 module: 5 RPCs workspace.ripgrep[→ContentSearchData] + fuzzy_open[→str]/fuzzy_change[→bool]/fuzzy_close[→bool]/fuzzy_search[→Any], lands 4+1 serde patterns new to layer: camelCase rename_all=ConfigDict(to_camel) + untagged TargetClientId=RootModel[ClientId|None]+is_none() + custom-default respect_gitignore=True + primitive/Value Response=str/bool/Any + nested skip_serializing_if via plain @model_serializer non-wrap, mixed snake_case-outer/camelCase-inner namespace + PurePosixPath name derivation + Field default_factory, 25 new tests zero-regression)`

## R71 — 远程 workspace RPC hooks 前向容忍枚举层(融合 grok xai-grok-workspace-types rpc/ hooks.rs)

锚点:R71-1 9629f9b

### 本轮目标

迁移 `hooks.rs`(230L)= **1 个文件** → 1 个新模块,攻克 **4 个新 serde 模式** + 1 个本轮决定性架构问题:

- **前向容忍枚举(本轮核心)**:`HookEventNameWire` 是 grok 手写 `Serialize`/`Deserialize` 的枚举,15 已知变体(`SessionStart`→`session_start` … `PostCompact`→`post_compact`)+ `Unknown(String)` 全捕获,用于部署倾斜容忍(新 server 事件解码不失败,不同 unknown 事件保持不同 map key)。
- **`#[serde(skip)]` 字段省略**:`matcher`(编译的正则,永不在线)从 `HookSpecWire` 完全省略——不是 `skip_serializing_if`,是**从不序列化/反序列化**。
- **枚举作为 JSON map key**:`HookRegistryWire` 携带 `HashMap<HookEventNameWire, Vec<HookSpecWire>>`,枚举序列化为 plain string → 原生 JSON object key。
- **空参数请求 struct**:`HookRegistryReq` 无字段(`METHOD="workspace.hook_registry"`,`Response=HookRegistryWire`)。

**决定性架构问题**:`Unknown(String)` 是**开放词汇表**(`Enum` 无法表达携带动态字符串的变体)→ `HookEventNameWire` 必须是 **str 子类**;str 子类在 pydantic v2 字段类型 + dict key 中工作需要 `__get_pydantic_core_schema__`。这与 R69 `DeployError`(封闭词汇表 15 码,用 plain `Enum`)形成鲜明对比——**开放 vs 封闭决定枚举形状**。

rpc/ 剩余 6 文件(fs 754 / git 1077 / hunks 413 / skills 275 / workspace 271 / worktree 406 = ~3096 行)留 R72+。

### 融合结论

R69 用 `DeployError` 证明封闭词汇表枚举(`Enum` + `value=wire_code`);**R71 用 `HookEventNameWire` 证明开放词汇表枚举(str 子类 + pydantic core schema)同样可被 `WireModel` 基类承载**,且能原生作为 JSON map key:

- **开放词汇表枚举形状**:grok `enum HookEventNameWire { SessionStart, …, Unknown(String) }` 的 `Unknown` 携带动态字符串——Python `enum.Enum` 无法表达(枚举变体值必须是编译期常量)。str 子类则天然表达:任何 str 实例都是合法 `HookEventNameWire`(`Unknown` 情况就是任意其他字符串)。
- **str 子类 + pydantic v2**:pydantic v2 默认不识别 str 子类作为字段类型(报 `PydanticSchemaGenerationError`)。`__get_pydantic_core_schema__` 返回 `no_info_after_validator_function(cls, str_schema())`——告诉 pydantic 验证为 str 后强制转换为子类;序列化下沉到 `str_schema` → plain string。无需 `arbitrary_types_allowed`。
- **原生 JSON map key**:str 子类序列化为 plain string,`dict[HookEventNameWire, list[HookSpecWire]]` 在 `model_dump(mode="json")` 后键自然是 string——**无需自定义 key 序列化器**(`Enum` 作 dict key 通常需要)。
- **15 已知变体作类常量**:grok 的 `as_str` match(15 臂)→ class-body 后用 `_KNOWN_HOOK_EVENTS` dict + `setattr` 循环附加(`SESSION_START="session_start"` 等),清理 `del _attr, _wire`。

### 交付

1 源文件 + barrel 扩展 + 测试扩展,230 行 Rust → 约 150 行 Python + 14 个新增专项测试:

| 文件 | Rust 源 | 行数 | Python 实现 |
|------|---------|------|------------|
| `rpc/hooks.py` | `rpc/hooks.rs` | 230 | `HookEventNameWire`(str 子类 + `__get_pydantic_core_schema__` + `as_str()` + 15 class-post 常量)+ `HookSpecWire`(snake_case,matcher `#[serde(skip)]` 省略,`extra_env` 必填 HashMap)+ `HookRegistryWire`(`dict[HookEventNameWire, list[HookSpecWire]]` + Field default_factory)+ `HookRegistryReq`(空参数 + METHOD + Response ClassVar) |
| `rpc/__init__.py` | mod.rs barrel | — | 重导出 hooks 4 符号(barrel + __all__ 同步 + docstring 更新) |
| `tests/test_rpc.py` | — | — | +14 测试:`TestHooks`(method 常量/response 类型/str 子类 + as_str/15 已知常量/15 变体往返/Unknown 开放词汇/matcher 省略/snake_case keys/event 验证为子类/枚举作 map key/server json 往返/空默认值 + default/空参数请求/envelope Ok 往返) |

测试增长:rpc 专项 R70 的 95 → R71 的 109(+14);全量 R70 的 2179 → R71 的 2193(+14,零回归,完美对账)。

### 映射决策树 + 坑

**决策树**:
- `enum HookEventNameWire { …15…, Unknown(String) }`(手写 Serialize/Deserialize)→ `class HookEventNameWire(str)` + `__get_pydantic_core_schema__`(返回 `no_info_after_validator_function(cls, str_schema())`)+ 15 已知变体 class-body 后 setattr。Unknown 情况 = 任意 str 实例,无需 catch-all 常量。
- grok `as_str(&self) -> &str`(15 臂 match,Unknown 返回内部 String)→ `def as_str(self) -> str: return str.__str__(self)`(str.__str__ 避免子类覆盖,值就是 wire string)。
- `#[serde(skip)] matcher: Option<Regex>`(HookSpecWire)→ 字段**完全不声明**(非 skip_serializing_if——从不序列化/反序列化)。
- `HashMap<HookEventNameWire, Vec<HookSpecWire>>`(HookRegistryWire)→ `dict[HookEventNameWire, list[HookSpecWire]]`,str 子类原生作 JSON key。
- 空参数 `struct HookRegistryReq` → 无字段 WireModel 子类;`#[derive(Default)]` → 基类 `default()` 即 `cls()`。
- `extra_env: HashMap<String, String>`(无 `#[serde(default)]`)→ 必填 `extra_env: dict[str, str]`(无默认)。

**坑 1 — PydanticSchemaGenerationError,str 子类不被识别(本轮最大坑,决定性架构决策)**
初版 `HookEventNameWire(str)` 直接用于 `event: HookEventNameWire` 字段和 `dict[HookEventNameWire, …]`,冒烟测试报 `Unable to generate pydantic-core schema for <class 'HookEventNameWire'>. Set arbitrary_types_allowed=True or implement __get_pydantic_core_schema__`。**两个选项**:(a) 模型设 `arbitrary_types_allowed=True`(全局放宽,丢失 str 验证);(b) 在 HookEventNameWire 上实现 `__get_pydantic_core_schema__`。**选 (b)**(更精确):返回 `core_schema.no_info_after_validator_function(cls, core_schema.str_schema())`——pydantic 验证为 str,然后 after-validator 强制转换为子类;序列化下沉到 str_schema → plain string。这同时让字段值类型 + dict key 类型都工作。冒烟测试 7 机制全绿。`# noqa: ANN001, ANN206`(handler 参数 + 返回类型由 pydantic 签名约定固定)。

**坑 2 — 开放词汇表(Unknown)决定枚举形状(Enum 不可用)**
设计阶段首想用 `Enum`(对标 R69 DeployError)。但 grok 的 `Unknown(String)` 携带动态字符串——`enum.Enum` 变体值必须是编译期常量,无法表达"任意未识别 server 字符串"。**修复**:str 子类——任何 str 实例即合法 HookEventNameWire(Unknown 情况就是任意其他字符串),且天然作 dict key。**对比**:R69 DeployError 是**封闭**词汇表(15 码穷举)→ plain Enum + value=wire_code;R71 HookEventNameWire 是**开放**词汇表(15 已知 + ∞ unknown)→ str 子类。开放 vs 封闭是决定枚举形状的关键判据。

**坑 3 — class-body 内不能引用自身实例作常量**
15 已知变体想作类常量(`SESSION_START = HookEventNameWire("session_start")`),但类 body 执行时 `HookEventNameWire` 尚未定义完毕,无法在 body 内构造自身实例。**修复**:类 body 后用 `_KNOWN_HOOK_EVENTS` dict + `for _attr, _wire in …: setattr(HookEventNameWire, _attr, HookEventNameWire(_wire))` 循环附加,再 `del _attr, _wire` 清理命名空间(对标 R69 DeployError.ALL = tuple(cls) 的 class-post 模式)。

**坑 4 — 枚举作 JSON map key 通常需自定义序列化器,但 str 子类不需要**
grok 的 `HashMap<HookEventNameWire, _>` 在 serde 中枚举作 map key 需 `key = "string"` 或手写。Python 侧 `dict[HookEventNameWire, list[…]]` 在 `model_dump(mode="json")` 时——str 子类的值就是 string,键自然序列化为 JSON object key,**无需自定义 key serializer**。这是 str 子类形状的额外红利(Enum 作 dict key 通常需要 `@field_serializer` 处理)。

**坑 5 — `as_str()` 必须用 `str.__str__` 避免子类覆盖**
初版 `def as_str(self): return str(self)` 似乎等价,但若子类(或未来子类)覆盖 `__str__`,`str(self)` 会调用覆盖版,破坏 wire 契约。**修复**:`return str.__str__(self)`——绕过任何子类覆盖,直接返回底层 str 值。

**坑 6 — 必填字段跟在可选字段后(dataclass 禁忌,pydantic v2 允许)**
HookSpecWire 字段顺序:必填 name → 必填 event → 必填 handler_type → **可选** configured_matcher(=None)→ **必填** enabled → … dataclass 禁忌必填跟在可选后(报 "non-default argument follows default argument")。**验证**:pydantic v2 BaseModel **无此限制**(字段顺序由声明顺序决定,默认值不影响构造签名排序)——`HookSpecWire(name=…, event=…, handler_type=…, enabled=…, timeout_ms=…, source_dir=…, extra_env=…)` 直接工作,可选 configured_matcher 在中间。grok 原顺序保留。

### 验证

三重验证全绿:

```bash
# 1. ruff lint(E/F/W/I/B/UP,行长 100)
cd "/d/工作/城建院/mm code/agent" && uv run ruff check minimax_code/workspace_types/rpc/ tests/test_rpc.py --fix
# → All checks passed!

# 2. R71 专项测试
cd "/d/工作/城建院/mm code/agent" && uv run pytest tests/test_rpc.py -q
# → 109 passed in 0.35s(R70 的 95 + R71 新增 14,精确对账)

# 3. 全量回归(零回归)
cd "/d/工作/城建院/mm code/agent" && uv run pytest -q
# → 2193 passed, 10 skipped in 104.83s
#    (R70 的 2179 + R71 新增 14,完美对账,零回归)
```

**wire 保真交叉验证**:对照 grok `hooks.rs` 源码逐行确认——`HookEventNameWire` 手写 Serialize/Deserialize 的 15 已知变体 + `Unknown(String)` + `as_str()` 方法、`HookSpecWire` 的 `#[serde(skip)] matcher` + snake_case 字段 + `extra_env` 必填 HashMap + `command`/`source_dir` 作 PathBuf→str、`HookRegistryWire` 的 `HashMap<HookEventNameWire, Vec<HookSpecWire>>` + derive Default、`HookRegistryReq` 空参数 + METHOD + Response。4 个 grok 测试(method_constant / hook_event_name_wire_snake_case_round_trip / hook_event_name_wire_unknown_round_trips_losslessly / hook_registry_wire_round_trips_server_json)全部在 TestHooks 中复刻并扩展(15 变体 round trip 覆盖全部而非抽样)。`test_hook_event_name_snake_case_round_trip_all_variants`(15 变体 validate+dump)+ `test_hook_registry_wire_round_trips_server_json`(含 known + unknown 事件的完整往返)+ `test_envelope_ok_wraps_hook_registry_wire`(envelope Ok 往返)闭合了"开放词汇枚举 ↔ map key 序列化 ↔ envelope"完整链路。

### YAGNI 边界

本轮明确不做:

- ❌ **rpc/ 剩余 6 文件(~3096 行)迁移** —— fs/git/hunks/skills/workspace/worktree 留 R72+(本轮只迁 hooks 这一个开放词汇表枚举文件)。
- ❌ **实际 hook 执行引擎** —— 本轮仅 wire 类型契约,真正的 hook 匹配(matcher 编译)+ 执行(命令/URL 回调)是运行时能力,不在类型层。
- ❌ **matcher 正则重编译** —— HookSpecWire 省略了 matcher 字段(永不在线),消费端(客户端)从 configured_matcher 重新编译——这是消费端职责,不在 wire 契约。
- ❌ **接入 IPC handler 或远程 workspace transport** —— 类型契约层先行,wire DTO 的消费端在 shell 层。
- ❌ **前端 `web/src/types/` 镜像** —— 纯后端 RPC 类型契约,无 wire 事件广播到前端。
- ❌ **HookEventNameWire 与 hooks-plugins-types crate 的 HookEvent 去重** —— 各 crate 各自定义,去重在跨 crate 集成阶段处理。

### Commit

`feat(platform): R71 workspace RPC hooks forward-tolerant enum layer (fuse grok xai-grok-workspace-types rpc/ hooks.rs 230 lines → 1 module: workspace.hook_registry RPC → HookRegistryWire{dict[HookEventNameWire, list[HookSpecWire]]}, lands 4 serde patterns new to layer: #[serde(skip)] matcher field elision + forward-tolerant str-subclass HookEventNameWire[15 known + Unknown open-vocabulary via __get_pydantic_core_schema__ no_info_after_validator_function(cls, str_schema)] + enum as native JSON map key[str-subclass serializes to plain string] + empty-parameter HookRegistryReq, open-vs-closed vocabulary enum shape decision[str-subclass vs R69 plain Enum] + class-post setattr 15 constants + as_str via str.__str__, 14 new tests zero-regression)`


## R72 — 远程 workspace RPC workspace 元数据/配置/admin 层(融合 grok xai-grok-workspace-types rpc/ workspace.rs)

锚点:R72-1 3bcc850

### 本轮目标

迁移 `workspace.rs`(271L)= **1 个文件** → 1 个新模块,14 个 `workspace.*` 方法 + 4 辅助/响应结构体 + 1 typed shape,攻克 **3 个新 serde 模式** + 本轮决定性架构问题:

- **`skip_serializing_if = "String::is_empty"`(本轮核心)**:`UpdateToolConfigReq.caller_session_id` 与 `DropSessionReq.caller_session_id` 两个 *deprecated 自证字段*——空串 = 缺省,序列化时省略。区别于 R71 `#[serde(skip)]`(字段彻底不存在)和 R70 `Option::is_none`(None 省略):**这里是必填 str 在等于空串时省略**。
- **批量 `Response = serde_json::Value`**:14 方法中 11 个响应是任意 JSON(服务端定义形状,本 crate 不类型化)→ 统一 `Response: ClassVar = Any`。
- **typed shape 与 raw Value 响应并存**:`WorkspaceInfo{os,shell,cwd}` 是 `workspace.info` raw `Value` 的 typed shape,但 `WorkspaceInfoReq.Response` 仍是 `Any`(传输层保留原始契约);客户端按需 `model_validate`。

**决定性架构问题**:两个 Req(`UpdateToolConfigReq`/`DropSessionReq`)共享 deprecated `caller_session_id` 字段 + 相同省略语义 → 抽 **`_OmitsEmptyCallerSessionId` mixin**(`caller_session_id: str = ""` + `@model_serializer(mode="wrap")` 删空串键),子类继承 mixin 添加各自字段。wrap 模式 `handler(self)` 返回默认 dump,再 `pop` 空 caller——避免递归(plain 模式调 `self.model_dump()` 会无限递归)。

rpc/ 剩余 5 文件(fs 754 / git 1077 / hunks 413 / skills 275 / worktree 406 = ~2925 行)留 R73+。

### 融合结论

R70 用 `model_serializer`(plain)省略 None(match_start/match_end);**R72 用 `model_serializer(mode="wrap")` mixin 省略空串 str 字段**,且跨两个 Req 复用:

- **非可选 str 的空值省略**:grok `#[serde(default, skip_serializing_if = "String::is_empty")]`——字段是 `String`(非 Option),默认 "",序列化时若为 "" 则省略键。Python 侧 `caller_session_id: str = ""`(有默认) + wrap serializer 在 dump 后 `if raw.get("caller_session_id") == "": raw.pop(...)`。
- **wrap vs plain model_serializer**:R70 `ContentMatch` 用 plain 模式(手工逐字段建 dict,因 camelCase 键)。R72 mixin 用 **wrap 模式**——`handler(self)` 拿到默认 dump(含子类所有字段,JSON 安全),仅删一个键。优势:子类(`UpdateToolConfigReq` 带 `new_config: Any`、`DropSessionReq` 带 `session_id`)无需各自重写序列化,mixin 自动覆盖全部子类字段。关键坑:wrap 内**不可调 `self.model_dump()`**(递归),必须用 handler。
- **mixin 继承 + 子类字段**:`_OmitsEmptyCallerSessionId(WireModel)` 定义 `caller_session_id` 字段 + wrap serializer;`UpdateToolConfigReq(_OmitsEmptyCallerSessionId)` / `DropSessionReq(_OmitsEmptyCallerSessionId)` 各加自己的字段。pydantic v2 合并父+子字段,model_serializer 随继承链生效。
- **`Response: ClassVar = Any` 批量化**:11 个方法的 `type Response = serde_json::Value` → `Response: ClassVar = Any`(`typing.Any`)。R70 已为单例 `FuzzyStatusReq` 建立 `Response: ClassVar = Any` 先例,R72 批量复制。
- **纯辅助 typed shape**:`WorkspaceInfo` 不挂任何 Req.Response(它对应的方法 `WorkspaceInfoReq.Response = Any`),是独立 `WireModel`,客户端可选 `model_validate`。grok 源码注释明确"Response stays the raw Value to preserve the contract; WorkspaceInfo is the typed shape of that value"。

### 交付

1 源文件 + barrel 扩展 + 测试扩展,271 行 Rust → 约 295 行 Python + 29 个新增专项测试:

| 文件 | Rust 源 | 行数 | Python 实现 |
|------|---------|------|------------|
| `rpc/workspace.py` | `rpc/workspace.rs` | 271 | `WorkspaceInfo`(typed shape,3 必填,无 Default)+ `BackgroundTaskSummaryWire`(tool_name `skip_serializing_if=Option::is_none` 省略,plain model_serializer)+ `ListBackgroundTasksResponse`/`ListTodosResponse`(tasks/todos list)+ `TodoSummaryWire`(id/content/status)+ `_OmitsEmptyCallerSessionId` mixin(wrap serializer 删空 caller)+ 14 Req(6 空参数 + 5 带参 Response=Any + 2 继承 mixin + 2 list typed Response) |
| `rpc/__init__.py` | mod.rs barrel | — | 重导出 workspace 18 符号(barrel + __all__ 同步 + docstring 更新) |
| `tests/test_rpc.py` | — | — | +29 测试:`TestWorkspace`(13 method 常量/11 Value→Any/2 typed Response/WorkspaceInfo 3 测试/6 空参数 default/带参 Req/caller 省略 3×2/default/tool_name 省略/list default+round-trip/envelope Ok 往返) |

测试增长:rpc 专项 R71 的 109 → R72 的 138(+29);全量 R71 的 2193 → R72 的 2222(+29,零回归,完美对账)。

### 映射决策树 + 坑

**决策树**:
- 6 个空参数 struct(`WorkspaceInfoReq`/`LoadProjectConfigReq`/`LoadPermissionsReq`/`LoadEnvrcReq`/`InstallPluginReq`/`RefreshPluginsReq`)→ 无字段 WireModel;`#[derive(Default)]` → 基类 `default()` = `cls()`,`to_wire()` = `{}`。
- `type Response = serde_json::Value`(11 个)→ `Response: ClassVar = Any`(`typing.Any`)。
- `type Response = ListBackgroundTasksResponse`/`ListTodosResponse`(2 个 typed)→ `Response: ClassVar[type] = ListXxxResponse`。
- `WorkspaceInfo{os,shell,cwd}`(derive PartialEq,Eq,**无 Default**)→ 独立 WireModel,3 必填,无 default() override(基类 cls() 会失败,符合"不 derive Default")。
- `BackgroundTaskSummaryWire.tool_name`(`#[serde(default, skip_serializing_if = "Option::is_none")]`)→ plain `model_serializer` 手工建 dict(None 时省略 tool_name),对标 R70 ContentMatch。
- `caller_session_id: String`(`#[serde(default, skip_serializing_if = "String::is_empty")]`,2 个 Req 共享)→ `_OmitsEmptyCallerSessionId` mixin:`caller_session_id: str = ""` + wrap model_serializer 删空键。
- `new_config`/`mcp_servers: serde_json::Value`(`#[derive(Default)]` → Value::Null)→ `Any` 字段 + override `default()` 返回 `cls(new_config=None)`(None = JSON null = Value::Null)。
- 必填 String 字段 + `#[derive(Default)]`(`session_id`/`refs`/`task_id`/`command`/`id`/`content`/`status`)→ override `default()` 返回 `cls(field=""/[])`(对标 R70 FuzzyStatusReq.search_id→"")。

**坑 1 — wrap model_serializer 内不可调 self.model_dump()(递归,本轮决定性)**
mixin 首版 `@model_serializer(mode="wrap")` 内用 `raw = self.model_dump(mode="json")` 拿 dump 再删空键——但 `model_dump()` 触发 model_serializer 自身(它定义了 dump 行为),**无限递归 + RecursionError**。**修复**:wrap 模式必须用 `handler(self)`——pydantic 注入的 handler callable,返回默认序列化结果(子类全部字段已 JSON 安全),不触发自定义 serializer。这是 wrap vs plain 的核心区别:plain 手工建(无 handler),wrap 包裹默认(handler)。

**坑 2 — mixin 字段被子类继承,model_serializer 随继承链生效**
担心 `_OmitsEmptyCallerSessionId` 的 wrap serializer 在子类 `UpdateToolConfigReq`/`DropSessionReq` 中不生效。**验证**:pydantic v2 类构建时合并父+子 model_fields,model_serializer 作为 schema 钩子随继承传递——`handler(self)` 返回的 dump 含子类字段(`session_id`/`new_config`),删空 caller 后正确。两个子类各自仅声明独有字段 + METHOD + Response + default() override,序列化逻辑全部继承 mixin。

**坑 3 — skip_serializing_if="String::is_empty" vs Option::is_none vs #[serde(skip)] 三种省略辨析**
本轮第 5 种(累计)省略模式,易混:(a) R71 `#[serde(skip)] matcher`——字段**彻底不存在**(Python 不声明);(b) R70 `skip_serializing_if="Option::is_none"`(match_start/tool_name)——**Option 字段**,None 省略(plain model_serializer 建 dict 时 if not None);(c) R72 `skip_serializing_if="String::is_empty"`(caller_session_id)——**非 Option 的 String 字段**,默认 "",等于 "" 省略(wrap serializer handler dump 后 pop)。三者字段类型 + 触发条件 + 实现机制全不同。

**坑 4 — Response: ClassVar = Any 的 Any 是单例,可 is 比较**
11 个 `Response: ClassVar = Any`。测试 `assert cls.Response is Any`——担心 `Any` 不是单例。**验证**:`typing.Any` 在运行时返回同一对象(Python 3.11+ 单例),`Response: ClassVar = Any` 存储即 Any 本身,`is` 比较为 True。无需用 `==` 或字符串比对。

**坑 5 — WorkspaceInfo 不 derive Default,基类 default() 必然失败**
grok 的 `WorkspaceInfo` derive `Debug, Clone, PartialEq, Eq, Serialize, Deserialize`(**无 Default**)。若基类 `default()` 返回 `cls()` 会因 3 必填字段报 ValidationError。测试 `test_workspace_info_no_default` 用 `pytest.raises(Exception)` 断言 `WorkspaceInfo.default()` 失败(`# noqa: B017`,因 ruff B017 禁止 `pytest.raises(Exception)`,用显式 noqa)——反向验证"不 derive Default"契约。

**坑 6 — Any 字段(None = Value::Null)在 to_wire 中保留**
`UpdateToolConfigReq.new_config`/`ConfigureMcpReq.mcp_servers` 是 `serde_json::Value`,`#[derive(Default)]` → Value::Null → JSON null。Python `Any` 字段为 None 时 `model_dump(mode="json")` 返回 None(JSON 安全),to_wire 输出 `"new_config": null`。wrap serializer 不删 None(只删空 caller),`test_update_tool_config_req_default` 断言 `{"new_config": None, "session_id": ""}`。

### 验证

三重验证全绿:

```bash
# 1. ruff lint(E/F/W/I/B/UP,行长 100)
cd "/d/工作/城建院/mm code/agent" && uv run ruff check minimax_code/workspace_types/rpc/ tests/test_rpc.py
# → All checks passed!

# 2. R72 专项测试
cd "/d/工作/城建院/mm code/agent" && uv run pytest tests/test_rpc.py -q
# → 138 passed in 0.46s(R71 的 109 + R72 新增 29,精确对账)

# 3. 全量回归(零回归)
cd "/d/工作/城建院/mm code/agent" && uv run pytest -q
# → 2222 passed, 10 skipped in 112.13s
#    (R71 的 2193 + R72 新增 29,完美对账,零回归)
```

**wire 保真交叉验证**:对照 grok `workspace.rs` 源码逐行确认——3 个 grok 测试(`workspace_info_deserializes_server_shape`/`workspace_info_ignores_unknown_fields`/`method_constant` 11 个方法)全部在 TestWorkspace 中复刻并扩展(method 常量补全至 13 含 list_*、WorkspaceInfo 加 no_default 反向断言)。`test_update_tool_config_req_omits_empty_caller`/`test_drop_session_req_omits_empty_caller`(空 caller 省略)+ `test_update_tool_config_req_keeps_nonempty_caller`/`test_drop_session_req_keeps_nonempty_caller`(非空保留)闭合 caller 省略双路径;`test_list_background_tasks_response_round_trip`(tool_name 嵌套省略)+ `test_envelope_ok_wraps_list_background_tasks`(envelope Ok 往返 typed Response)闭合"空串省略 ↔ Option 省略 ↔ envelope typed Response"完整链路。

### YAGNI 边界

本轮明确不做:

- ❌ **rpc/ 剩余 5 文件(~2925 行)迁移** —— fs/git/hunks/skills/worktree 留 R73+(本轮只迁 workspace 这一个元数据/配置文件)。
- ❌ **实际 workspace handler 实现** —— 本轮仅 wire 类型契约,真正的 workspace.info/load_project_config/tool_definitions 执行是运行时能力,不在类型层。
- ❌ **接入 IPC handler 或远程 workspace transport** —— 类型契约层先行,wire DTO 的消费端在 shell 层。
- ❌ **前端 `web/src/types/` 镜像** —— 纯后端 RPC 类型契约,无 wire 事件广播到前端。
- ❌ **caller_session_id 鉴权语义实现** —— 字段标 deprecated(自证不再可信,server 从 envelope 推导 caller),本轮仅忠实迁移省略语义,鉴权是 server 端职责。
- ❌ **UpdateToolConfigReq 的 TURN_ACTIVE 重试逻辑** —— grok 注释提到该方法在 session 有 active turn 时被 TURN_ACTIVE wire code 拒绝(可重试),但重试调度是客户端运行时能力,wire 层只携带 TURN_ACTIVE 常量(R69 已定义)。

### Commit

`feat(platform): R72 workspace RPC metadata/admin layer (fuse grok xai-grok-workspace-types rpc/ workspace.rs 271 lines → 1 module: 14 workspace.* methods[6 empty-param + 5 param Response=Value + 2 typed Response] + WorkspaceInfo typed shape[alongside raw Value response] + BackgroundTaskSummaryWire/TodoSummaryWire[skip_serializing_if=Option::is_none plain model_serializer] + ListBackgroundTasks/ListTodos responses, lands 3 serde patterns new to layer: skip_serializing_if=String::is_empty non-optional str elision via _OmitsEmptyCallerSessionId mixin[@model_serializer mode=wrap handler(self) pops empty caller, shared by UpdateToolConfigReq+DropSessionReq, avoids self.model_dump recursion] + bulk Response=serde_json::Value→Any[11 methods] + typed WorkspaceInfo shape alongside raw Value response, 3 skip-elision variants distinguished[#[serde(skip)] vs Option::is_none vs String::is_empty], 29 new tests zero-regression)


## R73 — 远程 workspace RPC skills 发现/枚举层(融合 grok xai-grok-workspace-types rpc/ skills.rs)

锚点:R73-1 c8fc94a

### 本轮目标

迁移 `skills.rs`(275L)= **1 个文件** → 1 个新模块,2 个 `workspace.discover_*` 方法 + `SkillScope` 枚举 + `SkillInfo` 26 字段发现载荷,攻克 **3 个新 serde 模式** + 本轮决定性架构问题:

- **批量 `Option::is_none` 省略 via wrap exclude_none(本轮核心)**:`SkillInfo` 带 18 个 `Option` 字段,每个 `#[serde(default, skip_serializing_if = "Option::is_none")]`。R70 的 plain `model_serializer` 手建 dict 只适合 2 字段(`match_start`/`match_end`),26 字段手建太冗长 → 改用 **wrap `@model_serializer` + `handler(self)` 拿默认 dump + `{k:v for k,v in raw.items() if v is not None}` 推导式**一次性过滤全部 None。
- **`default = "default_true"` bool 字段**:`user_invocable`/`enabled` 两个 bool 缺省为 True(grok `#[serde(default = "default_true")]`),区别于 R70/R72 的 `#[serde(default)]` bool(缺省 False)。Python `field: bool = True`,True/False 都输出(无 `skip_serializing_if`)。
- **裸列表 `Response = Vec<Value>` / `Vec<SkillInfo>`**:`DiscoverPluginsReq` 响应是任意 JSON 列表(`list[Any]`),`DiscoverSkillsReq` 是 typed 列表(`list[SkillInfo]`)。envelope 包 `{"ok": [...]}`,区别于 R72 的 wrapped-struct 列表响应(`ListBackgroundTasksResponse` = `{"tasks": [...]}`)和 R72 的 scalar `Response = Any`。

**决定性架构问题**:18 个 Option 字段一次性省略——既不能手建 26 键 dict(冗长易漏键),也不能逐个 `pop`(18 个调用)。**wrap `model_serializer` 的 `handler(self)` 返回默认 dump(26 字段全在,JSON 安全),推导式过滤 None** 是唯一可扩展方案,且复用 R72 wrap mixin 的"handler 默认 dump 后处理"范式(只是 pop 单键→批量 filter)。

剩余 4 文件(fs 754 / git 1077 / hunks 413 / worktree 406 = ~2650 行)留 R74+。

### 融合结论

R70 plain `model_serializer` 省 2 字段,R72 wrap mixin 省 1 个空串 str 键,**R73 wrap exclude_none 批量省 18 Option**——Option 省略的第 3 种实现,把 R72 的"handler dump 后处理"从单键 pop 升级为批量 filter:

- **批量 Option::is_none 省略**:grok `#[serde(default, skip_serializing_if = "Option::is_none")]` × 18 字段。Python 侧 `@model_serializer(mode="wrap")` + `handler(self)` 拿默认 dump + `{k: v for k, v in raw.items() if v is not None}`。关键:`v is not None` 只过滤 None,**保留空 vec(`[]`)/空 map(`{}`)/False bool**——与 grok `Option::is_none` 语义一致(只 None 省略)。
- **Option 省略 3 实现谱系**:R70 plain 手建 dict(2 字段,camelCase 键需手工排);R72 wrap mixin pop 单键(空串 str caller);R73 wrap exclude_none 推导式批量(18 Option)。三者都是 `skip_serializing_if="Option::is_none"` 的实现,选型看字段数:少→plain,单键特殊→wrap pop,批量→wrap exclude_none。
- **`default_true` bool**:grok `#[serde(default = "default_true")]`——反序列化缺省 True,序列化无 skip 所以 True/False 都输出。Python `field: bool = True`(简单)。区别于 `#[serde(default)]` bool(缺省 False,`has_user_specified_description`/`disable_model_invocation`)。
- **SkillScope 复用 R71 形状**:R71 `HookEventNameWire`(15 known + Unknown,str-subclass,作 map key);R73 `SkillScope`(6 known + Unknown,str-subclass,作字段值)。形状一致(`__get_pydantic_core_schema__` + `no_info_after_validator_function(cls, str_schema())` + 类后 `setattr` 常量 + `as_str`),仅变体数 + 用途不同。str-subclass 在 map key 和字段值两种位置都原生工作。
- **裸列表 Response 无需改 envelope**:`envelope._dump_payload` 已有 `list`/`tuple` 分支(逐元素递归 dump);`from_wire` 用 `TypeAdapter(response_type).validate_python(data["ok"])` 支持 `list[SkillInfo]`/`list[Any]`。R70/R72 已为 scalar/primitive 建立 envelope 支持先例,R73 裸列表零改 envelope。

### 交付

1 源文件 + barrel 扩展 + 测试扩展,275 行 Rust → 约 182 行 Python + 21 个新增专项测试:

| 文件 | Rust 源 | 行数 | Python 实现 |
|------|---------|------|------------|
| `rpc/skills.py` | `rpc/skills.rs` | 275 | `SkillScope`(str-subclass 前向容忍枚举,6 known + Unknown,复用 R71 `__get_pydantic_core_schema__` 形状)+ `SkillInfo`(26 字段:4 必填 + 18 Option[wrap exclude_none 批量省略] + 4 bool[2 default_true `user_invocable`/`enabled` + 2 default False `has_user_specified_description`/`disable_model_invocation`],无 Default)+ `DiscoverSkillsReq`(Response=list[SkillInfo])+ `DiscoverPluginsReq`(Response=list[Any]) |
| `rpc/__init__.py` | mod.rs barrel | — | 重导出 skills 4 符号(barrel + __all__ 同步 + docstring 加 R73 段落) |
| `tests/test_rpc.py` | — | — | +21 测试:`TestSkills`(2 method 常量/2 Response 类型[typed list + Any list]/2 empty default/SkillScope 6 测试[known decode/class attrs/unknown round-trip/known round-trip/as_str/str-subclass]/SkillInfo 9 测试[minimal payload/full round-trip/omit_none 8 键/default_true True/false bool 存活/no_default/ignores unknown/empty map 存活/empty vec 存活]/envelope Ok 往返 typed list + Any list) |

测试增长:rpc 专项 R72 的 138 → R73 的 159(+21);全量 R72 的 2222 → R73 的 2243(+21,零回归,完美对账)。

### 映射决策树 + 坑

**决策树**:
- 2 个空参数 struct(`DiscoverSkillsReq`/`DiscoverPluginsReq`)→ 无字段 WireModel + METHOD + Response ClassVar;`#[derive(Default)]` → 基类 `default()` = `cls()`,`to_wire()` = `{}`。
- `type Response = Vec<SkillInfo>` → `Response: ClassVar[type] = list[SkillInfo]`;`Vec<Value>` → `Response: ClassVar[type] = list[Any]`(envelope `TypeAdapter` 原生支持两者)。
- `SkillScope` 枚举(手写 serde,6 known + Unknown(String))→ str-subclass + `__get_pydantic_core_schema__`(复用 R71),6 常量类后 `setattr`,无 catch-all 常量(Unknown = 任意 str)。
- `SkillInfo` 4 必填字段(`name`/`description`/`path`/`scope`)→ 无默认 str + SkillScope。
- `SkillInfo` 18 Option 字段(`#[serde(default, skip_serializing_if = "Option::is_none")]`)→ `X | None = None` + wrap `model_serializer` exclude_none 推导式批量省略。
- `SkillInfo` 2 `default_true` bool(`user_invocable`/`enabled`)→ `bool = True`(总是输出)。
- `SkillInfo` 2 default bool(`has_user_specified_description`/`disable_model_invocation`)→ `bool = False`(总是输出)。
- `config_source: Option<Value>` → `Any | None`(grok 不结构化 `ConfigSource` tagged enum,RPC 客户端不解释)。
- `SkillInfo` derive `Debug, Clone, PartialEq`(无 Default,4 必填)→ 不 override `default()`,基类 `cls()` 必失败。

**坑 1 — 18 Option 字段省略不可手建 dict,改 wrap exclude_none 推导式(本轮决定性)**
首版考虑 R70 plain `model_serializer` 手建 26 键 dict——但 18 个 Option 各写 `if self.x is not None: out["x"] = self.x` 太冗长易漏键。**改用 wrap `@model_serializer(mode="wrap")`**:`handler(self)` 拿默认 dump(pydantic 注入,返回全部 26 字段 JSON 安全),`{k: v for k, v in raw.items() if v is not None}` 一次性过滤。这与 R72 wrap mixin 同范式(handler 默认 dump 后处理),只是 pop 单键→批量 filter。关键:`is not None` 只过滤 None,空 vec/空 map/False bool 保留(对标 grok `Option::is_none`)。

**坑 2 — `default_true` bool:True 和 False 都输出(无 skip_serializing_if)**
grok `#[serde(default = "default_true")]` 只控反序列化缺省值(→ True),序列化无 skip 所以 True/False 都在 wire。Python `field: bool = True`。`test_skill_info_false_bools_survive_serialization` 断言 `user_invocable=False`/`enabled=False` 仍在 dump(omit_none 不过滤 False,因 `False is not None`)。区别于 R70/R72 的 `#[serde(default)]` bool(缺省 False)。

**坑 3 — SkillScope 复用 R71 形状,字段值 vs map key 同形**
R71 `HookEventNameWire` 作 `HookRegistryWire` 的 map key(`dict[HookEventNameWire, list[HookSpecWire]]`);R73 `SkillScope` 仅作 `SkillInfo.scope` 字段值。str-subclass + `__get_pydantic_core_schema__` 在两种位置都原生工作(validate as str→coerce subclass,serialize→plain string)。唯一差异:变体数(15→6)和 wire 值表。复用代码逐字照搬,仅改 `_KNOWN_*` 表和类名。

**坑 4 — 裸列表 Response,envelope 零改**
担心 `Response = list[SkillInfo]`/`list[Any]` 需要 envelope 特殊处理。**验证**:`envelope._dump_payload` 已有 `list`/`tuple` 分支(逐元素递归,`BaseModel`→`sort_mappings(model_dump)`,`Mapping`→递归);`from_wire` 的 `TypeAdapter(response_type).validate_python(data["ok"])` 原生支持 `list[SkillInfo]`(逐元素 validate)/`list[Any]`(透传 dict)。R73 零改 envelope,`test_envelope_ok_wraps_discover_skills`/`test_envelope_ok_wraps_discover_plugins` 验证往返。

**坑 5 — omit_none 保留空 vec/空 map(只过滤 None)**
`SkillInfo` 的 `metadata: {}`(空 map)/`paths: []`(空 vec)在 `Option::is_none` 语义下**不省略**(只有 None 省略)。wrap 推导式 `v is not None` 正确保留 `{}`/`[]`。`test_skill_info_empty_map_survives`/`test_skill_info_empty_vec_survives` 断言空集合在 wire。`test_skill_info_omits_none_optionals` 断言仅 8 键(4 必填 + 4 bool,18 Option 全省略)。

**坑 6 — SkillInfo 不 derive Default,基类 default() 必失败**
grok `SkillInfo` derive `Debug, Clone, PartialEq, Serialize, Deserialize`(**无 Default**,4 必填)。基类 `default()` = `cls()` 会因 4 必填报 ValidationError。`test_skill_info_no_default` 用 `pytest.raises(Exception)` 断言失败(`# noqa: B017`,对标 R72 WorkspaceInfo/test_workspace_info_no_default)。

### 验证

三重验证全绿:

```bash
# 1. ruff lint(E/F/W/I/B/UP,行长 100)
cd "/d/工作/城建院/mm code/agent" && uv run ruff check minimax_code/workspace_types/rpc/skills.py minimax_code/workspace_types/rpc/__init__.py tests/test_rpc.py
# → All checks passed!

# 2. R73 专项测试
cd "/d/工作/城建院/mm code/agent" && uv run pytest tests/test_rpc.py -q
# → 159 passed in 0.58s(R72 的 138 + R73 新增 21,精确对账)

# 3. 全量回归(零回归)
cd "/d/工作/城建院/mm code/agent" && uv run pytest -q
# → 2243 passed, 10 skipped in 113.20s
#    (R72 的 2222 + R73 新增 21,完美对账,零回归)
```

**wire 保真交叉验证**:对照 grok `skills.rs` 源码逐行确认——7 个 grok 测试(`skill_info_deserializes_minimal_payload`/`skill_info_deserializes_full_payload`[round-trip]/`skill_scope_known_values`/`skill_scope_unknown_value_round_trips_losslessly`/`skill_scope_known_values_round_trip`/`skill_info_ignores_unknown_fields`/`method_constant`[2 methods])全部在 TestSkills 中复刻并扩展(method 常量补全至 2 个 discover_*、SkillScope 加 class_attrs/as_str/str-subclass 3 测试、SkillInfo 加 default_true/false bool/omit_none/empty map/empty vec/no_default 6 测试)。`test_skill_info_full_payload_round_trip`(26 字段全填,`to_wire() == sort_mappings(raw)`)闭合"18 Option 省略 ↔ 4 bool 总输出 ↔ 4 必填"完整链路;`test_skill_info_omits_none_optionals`(8 键 = 4 必填 + 4 bool)+ `test_skill_info_false_bools_survive_serialization`/`test_skill_info_empty_map_survives`/`test_skill_info_empty_vec_survives`(False bool/空 map/空 vec 不过滤)闭合"omit_none 只过滤 None"边界。

### YAGNI 边界

本轮明确不做:

- ❌ **rpc/ 剩余 4 文件(~2650 行)迁移** —— fs 754 / git 1077 / hunks 413 / worktree 406 留 R74+(本轮只迁 skills 这一个发现/枚举文件)。
- ❌ **实际 discover_skills/discover_plugins handler 实现** —— 本轮仅 wire 类型契约,真正的技能/插件发现扫描文件系统是运行时能力,不在类型层。
- ❌ **接入 IPC handler 或远程 workspace transport** —— 类型契约层先行,wire DTO 的消费端在 shell 层。
- ❌ **前端 `web/src/types/` 镜像** —— 纯后端 RPC 类型契约,无 wire 事件广播到前端。
- ❌ **SkillInfo.config_source 结构化** —— grok 留 `Option<Value>`(对应 `xai-grok-tools` 的 `ConfigSource` tagged enum,RPC 客户端不解释结构),本轮忠实保留 `Any | None`,tagged enum 解析是工具层职责。
- ❌ **plugin 字段(plugin_name/version/root/data)结构化** —— 4 个 str Option 字段在 plugin scope 下有意义,但本轮不建 PluginInfo 子结构(grok 源码就是平铺字段),保持与源 1:1。

### Commit

`feat(platform): R73 skills RPC discovery layer (fuse grok xai-grok-workspace-types rpc/ skills.rs 275 lines → 1 module: 2 workspace.discover_* methods[discover_skills Response=list[SkillInfo] + discover_plugins Response=list[Any]] + SkillScope forward-tolerant str-subclass enum[6 known + Unknown, reuses R71 __get_pydantic_core_schema__ shape, field-value vs R71 map-key] + SkillInfo 26-field payload[4 required + 18 Option + 4 bool, lands 3 serde patterns new to layer: default="default_true" bool[user_invocable/enabled=True, both True and False always emitted] + bulk Option::is_none elision via @model_serializer mode=wrap handler(self) + {k:v for k,v in raw.items() if v is not None}[3rd Option-elision impl after R70 plain 2-field dict + R72 wrap-mixin single-key pop, scales to 18 fields without hand-building 26-key dict, preserves empty vec/map/False bool] + bare-list Response=Vec<Value>/Vec<SkillInfo> surfaced as list[Any]/list[SkillInfo][envelope already supports bare lists via _dump_payload list branch + TypeAdapter, no envelope change]], SkillInfo no-Default → base default() raises[pytest.raises + noqa:B017], 21 new tests zero-regression)``


---

## R74 — 远程 workspace RPC git 命名空间依赖根层(融合 xai-grok-workspace-types rpc/ git.rs 1077 行)

锚点:R74-1 52c67b3

### 本轮目标

本轮迁移 grok `xai-grok-workspace-types::rpc::git`(1077 行,crate 内**最大**且**依赖根**的 RPC 文件)。git.rs 是依赖根:仅 `use serde/serde_json/super::WorkspaceRpc`,而被 worktree.rs(R75)/hunks.rs(R76)前向依赖(共用 `ChangeType`/`GitFileChange`)。故重排迁移序为依赖优先:R74=git → R75=worktree → R76=hunks → R77=fs,锁定 git 类型层为后续两轮的复用底座。

本轮目标:一次性迁移 20 个 `workspace.git_*` + `workspace.detect_vcs_kind` 方法 + 4 枚举(VcsKind camelCase+is_jj/is_repo 方法 / ChangeType lowercase **无** Default / GitStatusFormat / DiscardScope)+ ~22 个 wire struct,**落地 5 个对本层全新的 serde 模式**:① per-field `rename="type"` 键覆盖(在 camelCase rename_all 之上);② 非 Option 空集合省略(`skip_serializing_if="Vec::is_empty"`);③ 混合 skip 矩阵(同 struct 内部分 Option 保 null、部分省略);④ 手写 Deserialize 兼容旧版扁平 payload(model_validator(before) rewrap);⑤ Option 形状 Response(`str|None`/`GitInfoData|None`/`VcsKind`)。零回归闭合。

### 融合结论

git.rs 是 R74-R77 四轮迁移的**依赖拓扑起点**:它的 `ChangeType`/`GitFileChange` 被 worktree(变更文件列表)/hunks(diff hunk)直接引用。先把 git 类型契约锁死,R75+ 直接 `from ...rpc.git import ChangeType, GitFileChange` 复用,避免跨轮重复定义。这与 R45(DEFAULT_MODEL 词汇表→R46-R52 六轮消费端接线)的"先定义词汇表再扩散消费"同构——git.rs 是"先定义类型根再扩散 wire 消费"。

5 个新 serde 模式扩展了层内已有的 Option 省略谱系(R70 plain dict / R72 wrap-mixin 单键 pop / R73 wrap 推导式 18 键),并新增"混合 skip"(同 struct 双语义)与"手写 Deserialize 兼容"(版本偏移 robustness),补全 Rust serde → pydantic 的最后一类硬骨头。

### 交付

| 符号 | Rust 源(git.rs) | Python 实现(git.py) | 说明 |
|------|-----------------|---------------------|------|
| `VcsKind` | `enum` camelCase + `#[default]` GIT | `StrEnum` + `is_jj()`/`is_repo()` | camelCase 变体;Default → 类方法 |
| `ChangeType` | `enum` lowercase | `StrEnum`,**无** default() | derive 无 Default → 不挂 default() |
| `GitStatusFormat`/`DiscardScope` | `enum` lowercase + Default | `StrEnum` | STRUCTURED/PROMPT、BOTH/STAGED/UNSTAGED |
| `GitFileChange` | rename_all=camel, `rename="type"`, 7 Option skip | `_CamelOmitNone` + `Field(alias="type")` | per-field 别名覆盖 alias_generator |
| `GitInfoData` | 混合 skip 矩阵 | `_CAMEL` + wrap serializer | current_branch null 保留 / default_branch+vcs_kind 省略 |
| `RepoInfo` | `#[serde(default)] bool is_detached` | `_CamelOmitNone` | False 总输出(False is not None) |
| `GitStatusExtResponse` | 手写 Deserialize + Serialize | `model_validator(before)` + 类方法 | 兼容旧版扁平 → 信封 rewrap |
| `CommitWithPatchData`/`UncommittedChangesData` | `Vec::is_empty` 省略 | wrap serializer pop 空 camelCase 键 | 非 Option 空列表省略 |
| 20 Req struct | 20 个 `impl WorkspaceRpc` | `METHOD`/`Response` ClassVar | Value→Any / ()→None / Option→X|None |
| `_CamelOmitNone` | — | `WireModel` 子类(`_CAMEL` + wrap `_omit_none`) | 本轮新建基类 |

**文件**:新增 `agent/minimax_code/workspace_types/rpc/git.py`(1066 行);改 `rpc/__init__.py`(barrel 导入 46 符号 + `__all__` git 段 + docstring R74 段);改 `tests/test_rpc.py`(追加 TestGit 类 37 测试 + import 块)。

### 映射决策树 + 坑

**决策树 —— git.rs serde 模式 → pydantic 映射**

```
git.rs struct
├─ rename_all = "camelCase" 全字段
│  └─ model_config = _CAMEL (ConfigDict populate_by_name=True, alias_generator=to_camel)
├─ 某字段 rename = "type"(单字段覆盖)
│  └─ Field(alias="type")(显式别名覆盖 alias_generator)
├─ 所有 Option 都 skip_serializing_if = "Option::is_none"
│  └─ _CamelOmitNone 基类(wrap serializer: {k:v for k,v in raw if v is not None})
├─ 混合 skip(部分 Option 保 null)
│  └─ 自定义 wrap serializer,只 pop 指定 camelCase 键(GitInfoData)
├─ 非 Option 空集合 skip(Vec::is_empty)
│  └─ 自定义 wrap serializer,pop 空 list 键
├─ 手写 Deserialize(兼容旧版扁平)
│  └─ model_validator(mode="before") rewrap
└─ derive Default(空 struct 或自定义 Default impl)
   └─ 类方法 default()(bool 字段填 True/None)
```

**坑 1 — per-field `rename="type"` 覆盖 alias_generator**
`GitFileChange` 用 `#[serde(rename_all="camelCase")]` 但单字段 `change_type` 带 `#[serde(rename="type")]`(Rust 关键字冲突,字段名 change_type 但 wire 键是 type)。pydantic `alias_generator=to_camel` 会把 `change_type` 变成 `changeType`,但**显式 `Field(alias="type")` 优先级高于 alias_generator**,故 wire 键是 `type`。`test_git_file_change_type_alias_override` 三重断言:`"type"` 在 dump、`"changeType"`/`"change_type"` 不在。验证时 snake_case 名(`change_type=...`)与 wire 别名(`type=...`)都能构造。

**坑 2 — 混合 skip 矩阵(GitInfoData 同 struct 双语义)**
`GitInfoData`: `current_branch` **无** skip → None 时 wire 保 `null`;`default_branch`/`vcs_kind` **有** skip → None 时省略。不是 R73 的"全 Option 都省略"统一矩阵,而是同 struct 内逐字段不同。解法:自定义 wrap serializer,`handler(self)` 拿默认 dump 后**只 pop** `defaultBranch`/`vcsKind`(当 None),保留 `currentBranch:null`。`test_git_info_data_mixed_skip_matrix` 双断言:currentBranch=None → `"currentBranch":null` 在 dump;defaultBranch=None → 键不存在。

**坑 3 — `Vec::is_empty` 非 Option 空集合省略**
`CommitWithPatchData.binary_files` / `UncommittedChangesData.staged_binary_files`/`unstaged_binary_files` 是 `Vec<...>`(非 Option)+ `#[serde(default, skip_serializing_if="Vec::is_empty")]`:空列表省略,非空列表输出。这是**非 Option** 的省略(R70-R73 全是 Option 省略)。解法:wrap serializer pop 空 camelCase 键(`binaryFiles`/`stagedBinaryFiles`/`unstagedBinaryFiles`)。`test_commit_with_patch_data_empty_vec_omitted` + `test_uncommitted_changes_data_empty_vecs_omitted` 双断言:空 → 键省略;非空 → 键在。

**坑 4 — 手写 Deserialize 兼容旧版扁平 payload**
`GitStatusExtResponse` 在 Rust 只有 `#[derive(Serialize)]` + 手写 `Deserialize`(信封 `{format,data?,prompt?}`,旧版是扁平 `GitStatusData`)。pydantic 用 `model_validator(mode="before")` `_wrap_legacy_flat`:若输入是 `Mapping` 且非空且**不含** `{format,data,prompt}` 任一键 → 判定为旧版扁平,rewrap 为 `{"format":"structured","data":<扁平>,"prompt":null}`;否则透传(信封或空 cls())。`test_git_status_ext_response_legacy_flat_rewrap` 断言扁平输入 → 结构化信封;`test_empty_mapping_not_miswrapped` 断言空映射不被误包。配合类方法 `structured(data)`/`with_prompt(text)`/`default()`(`with_prompt` 而非 `prompt` 以避 F811 字段名冲突)。

**坑 5 — `RepoInfo.is_detached` 是 `#[serde(default)] bool` 非 Option**
`is_detached: bool`(无 skip_serializing_if)→ False 总在 wire。`_CamelOmitNone` 的 `v is not None` 推导式**保留 False**(`False is not None`)。`test_repo_info_is_detached_false_emitted` 断言 `"isDetached":false` 在 dump。这是"bool 总输出"与"Option None 省略"在同一基类下自然共存的验证。

**坑 6 — Option 形状 Response,envelope TypeAdapter 原生处理**
20 个方法 Response 形状:Value→`Response: ClassVar = Any`;()→`Response: ClassVar[type] = type(None)`;`Option<PathBuf>`→`str|None`;`Option<String>`→`str|None`;`Option<GitInfoData>`→`GitInfoData|None`;`VcsKind`→`VcsKind`;struct→`Response: ClassVar[type] = StructName`。`envelope.from_wire` 的 `TypeAdapter(response_type).validate_python(data["ok"])` 原生支持 `str|None`(some/none)/`GitInfoData|None`/`VcsKind`(StrEnum)。`test_envelope_git_option_responses` 覆盖 4 种 Option 形状往返。零改 envelope。

### 验证

三重验证全绿:

```bash
# 1. ruff lint(E/F/W/I/B/UP,行长 100)
cd "/d/工作/城建院/mm code/agent" && uv run ruff check minimax_code/workspace_types/rpc/git.py minimax_code/workspace_types/rpc/__init__.py tests/test_rpc.py
# → All checks passed!(编辑后 3 处修复:I001 import 排序自动 / F401 补 CheckoutCommitResponse 到 __all__ / F811 方法重命名 prompt→with_prompt)

# 2. R74 专项测试
cd "/d/工作/城建院/mm code/agent" && uv run pytest tests/test_rpc.py -q
# → 196 passed(R73 的 159 + R74 新增 37,精确对账)

# 3. 全量回归(零回归)
cd "/d/工作/城建院/mm code/agent" && uv run pytest -q
# → 2280 passed, 10 skipped in 112.56s
#    (R73 的 2243 + R74 新增 37,完美对账,零回归)
```

**wire 保真交叉验证**:对照 grok `git.rs` 源码逐行确认——TestGit 37 测试覆盖:20 method 常量、Response ClassVar 形状矩阵、4 枚举(VcsKind camelCase + is_jj/is_repo / ChangeType lowercase 无 default / GitStatusFormat / DiscardScope)、`rename="type"` 覆盖、混合 skip 矩阵(currentBranch null 保留 vs defaultBranch/vcsKind 省略)、Vec::is_empty 省略、GitStatusExtResponse(构造/结构化往返/prompt 往返/默认仅 format/新信封透传/旧版扁平 rewrap/空映射不误包)、RepoInfo is_detached=False 输出、GitError null path 保留、GitStatusExtReq 默认往返(camelCase + True bools)、GitCollectChangesReq 默认值、GitDiffReq `from_` 别名输出 `"from"`、CheckoutCommitResponse/CommitResult null 保留、UNTRACKED_CONTENT_THRESHOLD == 1024*1024、4 种 Option 响应 envelope 往返。5 个新 serde 模式 + 3 个编辑后 ruff 修复全部锁定。

### YAGNI 边界

本轮明确不做:

- ❌ **rpc/ 剩余 3 文件(~1573 行)迁移** —— worktree 406(R75)/ hunks 413(R76)/ fs 754(R77)留后续;本轮锁定依赖根 git 类型层供复用。
- ❌ **ChangeType/GitFileChange 在 worktree/hunks 的消费接线** —— 类型层先行,跨文件 import 在 R75+。
- ❌ **实际 git handler 实现** —— 本轮仅 wire 类型契约,真正的 git 子进程调用是运行时能力(handlers_git.py 已有 3 个 git.* 方法,与远程 workspace.git_* 是不同命名空间)。
- ❌ **接入 IPC handler 或远程 workspace transport** —— 类型契约层先行,wire DTO 消费端在 shell 层。
- ❌ **GitStatusExtResponse 其他 format 变体扩展** —— 本轮忠实复刻 grok 的 STRUCTURED/PROMPT 两态,不预判新 format。
- ❌ **前端 `web/src/types/` 镜像** —— 纯后端 RPC 类型契约,无 wire 事件广播到前端。

### Commit

`feat(platform): R74 git RPC namespace dependency-root layer (fuse grok xai-grok-workspace-types rpc/ git.rs 1077 lines → 1 module: 20 workspace.git_*/detect_vcs_kind methods + 4 enums[VcsKind camelCase + is_jj/is_repo, ChangeType lowercase no-Default, GitStatusFormat, DiscardScope] + ~22 wire types, lands 5 serde patterns new to layer: rename="type" Field alias override on camelCase + Vec::is_empty non-Option empty-collection elision + mixed skip matrix[GitInfoData current_branch-null-kept vs default_branch/vcs_kind-omitted] + manual Deserialize legacy-flat rewrap via model_validator(before) + Option Response shapes[str|None/GitInfoData|None/VcsKind via envelope TypeAdapter], _CamelOmitNone base camelCase+bulk None elision, dependency root for R75+ worktree/hunks ChangeType/GitFileChange reuse, 37 new tests zero-regression)`
## R75 — 远程 workspace RPC worktree 生命周期层(融合 grok xai-grok-workspace-types rpc/ worktree.rs 406 行)

锚点:R75-1 65ced8c

### 本轮目标

本轮迁移 grok `xai-grok-workspace-types::rpc::worktree`(406 行),是 R74 依赖根 git.rs 的**第一个消费者**:`from ...rpc.git import ChangeType, GitFileChange`(FileConflict 复用 ChangeType 的 `rename="type"` 覆盖;ApplyWorktreeResponse 嵌入 `list[GitFileChange]`)。依赖序 R74=git → R75=worktree → R76=hunks → R77=fs 本轮兑现第二步。

本轮目标:一次性迁移 11 个 `workspace.worktree_*`/`create_worktree`/`remove_worktree`/`apply_worktree` 方法 + 3 枚举(WorktreeType/WorktreeCopyMode/ApplyMode 均 lowercase + `#[default]`)+ ~16 个 wire struct,**落地 5 个对本层全新的 serde 模式**:① 内部标记枚举联合 `#[serde(tag="status")]`(CreateWorktreeResponse + ApplyWorktreeResponse);② 透明 newtype `#[serde(transparent)]`(WorktreeCreateSyncReq);③ 非透明 `{inner:...}` wrapper(CreateWorktreeFromWorktreeSyncReq);④ 自定义 default 函数 `default_copy_mode`(枚举默认,类 R73 default_true bool);⑤ 混合 skip 矩阵(recurring)。零回归闭合。

### 融合结论

R75 验证了 R74 依赖根决策的回报:`ChangeType`/`GitFileChange` 直接 import 复用,FileConflict 的 `change_type: ChangeType = Field(alias="type")` 与 R74 GitFileChange 完全同构,ApplyWorktreeResponse 两变体嵌入 `list[GitFileChange]` 零重复定义。这与 R45→R52 模型词汇表扩散、R64→R66 类型契约层扩散同构——"先定义类型根再扩散 wire 消费"在 RPC 层再次兑现。

5 个新 serde 模式中,**内部标记枚举联合**是层内首例(R70 TargetClientId 是 untagged、R74 GitStatusExtResponse 是手写 rewrap),用 pydantic `Annotated[Union[A,B], Field(discriminator="status")]` + 每变体 `Literal["<tag>"]` 字段精确复刻 Rust `#[serde(tag="status")]`。**透明 newtype**(子类化继承字段 + 仅重指 METHOD/Response ClassVar)vs **非透明 wrapper**(普通 struct + `inner` 字段)的对比,锁定 serde `transparent` 语义在 pydantic 的两种映射路径。

### 交付

| 符号 | Rust 源(worktree.rs) | Python 实现(worktree.py) | 说明 |
|------|------|------|------|
| `WorktreeType`/`WorktreeCopyMode`/`ApplyMode` | `enum` lowercase + `#[default]` | `StrEnum` + `default()` 类方法 | Linked/Dirty/Overwrite |
| `_default_copy_mode` | `fn default_copy_mode() -> Dirty` | 模块函数 | 枚举默认(R73 default_true 的枚举版) |
| `CreateWorktreeRequest` | rename_all=camel + `default="default_copy_mode"` | `_CAMEL` + `Field(default_factory=_default_copy_mode)` | Option 字段保 null |
| `WorktreeCreateSyncReq` | `#[serde(transparent)]` newtype | 子类化 `CreateWorktreeRequest` | wire 与内层字节同(无 inner 键) |
| `CreateWorktreeResponse` | `#[serde(tag="status")]` 联合 | `Annotated[Union, Field(discriminator="status")]` | creating/exists 两变体,各带 `Literal["<tag>"]` |
| `CreateWorktreeFromWorktreeSyncReq` | **非透明** `{inner:...}` | 普通 struct + `inner` 字段 | snake_case 外键 + camelCase 内层 |
| `FileConflict` | `rename="type"` + 3 Option 无 skip | `_CAMEL` + `Field(alias="type")` | 复用 R74 ChangeType |
| `ApplyWorktreeResponse` | `#[serde(tag="status")]` 联合 | `Annotated[Union, Field(discriminator="status")]` | 嵌入 `list[GitFileChange]`,success/conflicts |
| `WorktreeGcReq` | `max_age_secs` 无 default | `max_age_secs: int \| None`(必填) | 构造缺失抛 ValidationError |
| `WorktreeListReq` | 无 rename_all + `rename="type"` | snake_case + `Field(alias="type")` | include_all 保 snake_case |
| 11 Req struct | 11 个 `impl WorkspaceRpc` | `METHOD`/`Response` ClassVar | Value->Any / struct->type |

**文件**:新增 `agent/minimax_code/workspace_types/rpc/worktree.py`(502 行);改 `rpc/__init__.py`(barrel 导入 28 符号 + `__all__` worktree 段 + docstring R75 段);改 `tests/test_rpc.py`(追加 TestWorktree 类 30 测试 + import 块)。

### 映射决策树 + 坑

**决策树 —— worktree.rs serde 模式 -> pydantic 映射**

```
worktree.rs 项
├─ enum lowercase + #[default]
│  └─ StrEnum + default() 类方法(返回默认变体)
├─ struct rename_all="camelCase" 全字段
│  └─ model_config = _CAMEL(alias_generator=to_camel)
├─ 字段 #[serde(default="default_copy_mode")](枚举默认)
│  └─ Field(default_factory=_default_copy_mode)
├─ 内部标记联合 #[serde(tag="status")]
│  └─ Annotated[Union[A,B], Field(discriminator="status")]
│     每变体 status: Literal["<tag>"] 字段
├─ 透明 newtype #[serde(transparent)]
│  └─ 子类化内层 struct(继承字段 + model_config,仅重指 METHOD/Response)
├─ 非透明 wrapper(无 transparent)
│  └─ 普通 struct + inner: InnerStruct 字段(snake_case 外键)
├─ 字段 rename="type"(per-field 覆盖)
│  └─ Field(alias="type")(复用 R74 ChangeType)
└─ skip_serializing_if="Option::is_none"(recurring)
   └─ 自定义 wrap model_serializer,pop 指定 camelCase 键
```

**坑 1 — 内部标记枚举联合(层内首例 tagged union)**
`CreateWorktreeResponse`/`ApplyWorktreeResponse` 用 `#[serde(tag="status")]`:Rust 每变体扁平展开为 `{"status":"<tag>",...变体字段...}`。pydantic 映射:`Annotated[CreateWorktreeResponseCreating | CreateWorktreeResponseExists, Field(discriminator="status")]`,每变体是 WireModel 子类带 `status: Literal["creating"]/["exists"]` 字段。判别往返用 `TypeAdapter(CreateWorktreeResponse).validate_python({...})`:带 `"status":"creating"` -> Creating 变体;`"status":"exists"` -> Exists 变体。与 R70 untagged(无 discriminator)/R74 手写 rewrap 不同,这是 serde `tag` 语义的第一次精确复刻。两变体的 `sourceGitRoot` 各带 wrap serializer pop None(混合 skip)。

**坑 2 — 透明 newtype vs 非透明 wrapper(serde `transparent` 两态)**
grok 同文件给出对比:`WorktreeCreateSyncReq(pub CreateWorktreeRequest)` 带 `#[serde(transparent)]` -> wire 与内层字节同(无 "inner" 键);`CreateWorktreeFromWorktreeSyncReq { inner: ... }` **无** transparent -> wire 保 `{"inner":{...}}`。pydantic:透明态用**子类化**(`class WorktreeCreateSyncReq(CreateWorktreeRequest):` 继承所有字段 + camelCase model_config,仅重指 METHOD/Response ClassVar);非透明态用**普通 struct + `inner` 字段**。测试 `test_worktree_create_sync_req_is_transparent` 断言 `json["sessionId"]=="s1"` 且 `"inner" not in json`;`test_create_worktree_from_worktree_sync_req_keeps_inner_wrapper` 断言 `inner["sourceWorktreePath"]=="/src"`、`inner["copyMode"]=="dirty"`、无 `cancellationToken`/`resolvedDestPath`(serde(skip) 字段已从 wire 缺席)。

**坑 3 — `default_copy_mode` 枚举默认(R73 default_true 的枚举版)**
`copy_mode: WorktreeCopyMode` 带 `#[serde(default="default_copy_mode")]`(默认 Dirty)。pydantic `Field(default_factory=_default_copy_mode)`,模块函数返回 `WorktreeCopyMode.DIRTY`。与 R73 的 `default=True` bool 默认同构,只是值类型换成枚举。两处复用(`CreateWorktreeRequest` + `CreateWorktreeFromWorktreeRequestWire`)共享同一工厂函数。`test_create_worktree_request_copy_mode_defaults_dirty` 断言默认 dump 出 `"copyMode":"dirty"`。

**坑 4 — ClassVar 引用顺序(WorktreeDbPathResponse 前置)**
`WorktreeDbPathReq.Response: ClassVar[type] = WorktreeDbPathResponse` 需在被引用前定义。Python 模块级符号顺序敏感,故把 `WorktreeDbPathResponse`(响应)定义在 `WorktreeDbPathReq`(请求)**之前**(grok 源码顺序相反:Req 在前 Response 在后,但 Rust impl 块允许前向引用)。pydantic ClassVar 求值在类体执行期,必须符号已存在。这个重排是 Rust->Python 的固有迁移差异。

**坑 5 — `WorktreeListReq` 无 rename_all(测试断言陷阱)**
`WorktreeListReq` 在 grok **无** `#[serde(rename_all="camelCase")]`(L285-292),仅 `types` 字段带 `rename="type"`。故 wire 上 `repo`/`include_all` 保 snake_case,只有 `types`->`type`。pydantic:不加 `_CAMEL`,仅 `types: list[str] = Field(alias="type")`。初版测试误断言 `wire["includeAll"]`(误以为全 camelCase),实际应为 `wire["include_all"]`——这是 rename_all 缺席的精确语义,被 `test_worktree_list_req_types_alias_and_empty_vec` 锁定(修正断言 + 注释引用源码行号)。

**坑 6 — WorktreeGcReq 必填字段构造即抛**
`max_age_secs: Option<i64>` **无** `#[serde(default)]` -> key 必填(value 可 null)。pydantic `max_age_secs: int | None`(无默认)-> 构造 `WorktreeGcReq()` 缺该字段抛 `ValidationError`。`test_worktree_gc_req_missing_max_age_secs_raises` 锁定。dry_run/force 默认 False 不受影响。

### 验证

三重验证全绿:

```bash
# 1. ruff lint
cd "/d/工作/城建院/mm code/agent" && uv run ruff check --fix minimax_code/workspace_types/rpc/worktree.py minimax_code/workspace_types/rpc/__init__.py tests/test_rpc.py
# -> Found 4 errors (4 fixed, 0 remaining) — I001 import 排序自动;再 ruff check -> All checks passed!

# 2. R75 专项测试
cd "/d/工作/城建院/mm code/agent" && uv run pytest tests/test_rpc.py -q
# -> 225 passed(R74 的 195 + R75 新增 30 TestWorktree,精确对账)

# 3. 全量回归(零回归)
cd "/d/工作/城建院/mm code/agent" && uv run pytest -q
# -> 2309 passed, 10 skipped in 114.75s(零回归)
```

**wire 保真交叉验证**:对照 grok `worktree.rs` 源码逐行确认——TestWorktree 30 测试覆盖:11 method 常量、Response ClassVar 形状、3 枚举 default()(Linked/Dirty/Overwrite)+ WorktreeType from_str(linked/standalone/git OK + bogus 抛 ValueError)、透明 newtype(无 inner 键)、非透明 wrapper(inner 键在 + 无 cancellationToken/resolvedDestPath)、CreateWorktreeResponse 标记联合(creating/exists 双变体 + TypeAdapter 判别 + sourceGitRoot None 省略)、ApplyWorktreeResponse 标记联合(success/conflicts + 复用 GitFileChange/ChangeType + `wire["files"][0]["type"]=="edit"`)、FileConflict null 保留、RemoveWorktreeResponse resolvedPath 省略、CreateWorktreeFromWorktreeResponse 三 Option 省略、copy_mode 默认 dirty、apply mode 默认 overwrite、WorktreeGcReq max_age_secs 必填抛 ValidationError、WorktreeListReq types 别名 + snake_case include_all、空 struct(DbRebuild/DbPath/DbStats)。5 个新 serde 模式 + 1 个测试断言修正全部锁定。

### YAGNI 边界

本轮明确不做:

- ❌ **rpc/ 剩余 2 文件(~1167 行)迁移** —— hunks 413(R76)/ fs 754(R77)留后续。
- ❌ **实际 worktree handler 实现** —— 本轮仅 wire 类型契约,真正的 git worktree 子进程调用是运行时能力。
- ❌ **接入 IPC handler 或远程 workspace transport** —— 类型契约层先行,wire DTO 消费端在 shell 层。
- ❌ **WorktreeType/ApplyMode 其他变体扩展** —— 本轮忠实复刻 grok 的 linked/standalone/git 与 overwrite/merge,不预判新变体。
- ❌ **前端 `web/src/types/` 镜像** —— 纯后端 RPC 类型契约,无 wire 事件广播到前端。
- ❌ **PrepareWorktreeFromWorktreeResponse 的 response 字段强类型化** —— grok 源码该字段是 `Option<serde_json::Value>`(序列化的 CreateWorktreeResponse),保 `Any | None` 忠实复刻,不做变体收窄。

### Commit

`feat(platform): R75 worktree lifecycle RPC layer (fuse grok xai-grok-workspace-types rpc/ worktree.rs 406 lines -> 502-line module: 11 workspace.worktree_*/create_worktree/remove_worktree/apply_worktree methods + 3 enums[WorktreeType/WorktreeCopyMode/ApplyMode lowercase + #[default]] + ~16 wire types, first consumer of R74 dependency root[FileConflict reuses ChangeType Field(alias="type"), ApplyWorktreeResponse embeds list[GitFileChange]], lands 5 serde patterns new to layer: internally tagged enum union #[serde(tag="status")] via Annotated[Union, Field(discriminator="status")] + Literal status variant[CreateWorktreeResponse creating/exists + ApplyWorktreeResponse success/conflicts, layer-first tagged union vs R70 untagged + R74 manual rewrap] + transparent newtype WorktreeCreateSyncReq via subclass[wire byte-identical to inner, no inner key] + non-transparent {inner:...} wrapper CreateWorktreeFromWorktreeSyncReq[explicit transparent counterpart] + custom default_copy_mode enum default[enum analogue of R73 default_true bool] + recurring mixed skip matrix, 30 new TestWorktree tests 225 passed zero-regression 2309 total)`

## R76 — hunks.rs -> hunks.py（diff hunk wire 层，10 RPC + 3 enum + 18 struct，5 个 serde 新模式）

锚点:R76-1 7453e40

### 本轮目标

正向迁移 grok `xai-grok-workspace-types::rpc::hunks`（413 行 Rust）到 `agent/minimax_code/workspace_types/rpc/hunks.py`，落地 10 个 `workspace.hunk_*` / `workspace.get_all_hunks` / `workspace.get_session_summary` RPC + 3 枚举 + ~18 wire 类型，闭合 rpc/ 命名空间第 9 个文件（仅剩 fs.rs 留 R77）。本轮的核心挑战不在结构数量，而在 5 个对本层全新的 serde 模式的精确 pydantic 复刻——其中两个是 pydantic 无原生对应的 Rust serde 特性（`#[serde(other)]` 前向容错标记枚举 + 手写 `Deserialize` 前向容错字符串枚举），必须用自定义 schema 重新建模，且要解决一个 pydantic 版本兼容性坑。

### 融合结论

hunks.rs 是 diff hunk 追踪的 **wire 形状层**，刻意不导入 R39 `xai-hunk-tracker` 的 diff 计算原语——grok 在 lean crate 里镜像这些类型（源码 L111-114 注释），避免把 `gix` 重依赖拉进 workspace-types。我们忠实保留这个职责分离：R39 拥有 diff **计算**，R76 拥有 diff **wire 形状**。hunks.rs 与 R74 git.rs 无类型依赖（仅 `use super::WorkspaceRpc;` + chrono + serde），所以本轮是纯叶节点，不消费 R74 依赖根（区别于 R75 worktree 消费 ChangeType/GitFileChange）。10 个方法里两个**故意**省略 `hunk_` 前缀（`workspace.get_all_hunks` / `workspace.get_session_summary`），通过 `METHOD` ClassVar 逐字复刻，不"修正"成一致命名——这是 grok 客户端与服务端的既有契约。

### 交付

**hunks.rs -> hunks.py serde 模式映射**

| grok 项 | Rust serde | pydantic 映射 | 关键点 |
|---|---|---|---|
| `HunkActionKind` | `rename_all="lowercase"` **无** `#[default]` | StrEnum ACCEPT/REJECT | 请求专用,无 default()(区别 R75 三枚举) |
| `HunkSourceWire` | `tag="type"` + `rename_all="camelCase"` + `#[serde(other)]` | 平铺 WireModel `type:str` + `prompt_index:int\|None` + wrap serializer pop None | 前向容错:未知 type 逐字往返 |
| `FileContentStatusWire` | 手写 `Deserialize`(L225-239) | StrEnum 7 成员 + `__get_pydantic_core_schema__` after-validator | 未知串 -> UNKNOWN;default() MISSING |
| `IsoUtc` (`DateTime<Utc>`) | chrono RFC3339 `Z` 后缀 | `Annotated[datetime, PlainSerializer(_dt_to_wire)]` | `+00:00` 重写为 `Z`,微秒 rstrip("0") |
| `HunkWire.path` 等 | `PathBuf` | `str` / `list[str]` | 层内首个 PathBuf wire 字段 |
| `HunkLineInfoWire`/`HunkWire`/`SessionStatsWire`/`TurnSummaryWire`/`SessionSummaryWire`/`FileContentEntryWire` | `rename_all="camelCase"` | `model_config = _CAMEL` | 词法排序往返用键索引断言 |
| `FileContentViewWire` | status 必发 + byte_len/content `Option::is_none` | wrap serializer pop byteLen/content | 混合 skip 矩阵 |
| `old_text`/`patch` | `Option<String>` **无** skip | `str \| None = None`,保 null | null 保留(无 skip_serializing_if) |
| `BulkHunkActionResponse.affected` | `Vec<String>` 无 skip + derive Default | `affected: list[str] = []` | 空串发 `"affected":[]` |
| `FilteredHunksResponse` | `Vec<HunkWire>` + `usize` 无 skip + Default | `hunks: list[HunkWire] = []`, `total: int = 0` | 空发 `[]` / `0` |
| `HunkGetFilteredHunksReq` | 两 Option `#[serde(default)]` 无 skip | `path/source: str \| None = None`,保 null | None 发 null |
| 5 empty Req | 空 struct derive Default | WireModel 无字段,`to_wire()=={}` | Default 镜像 |
| 10 Req | 10 `impl WorkspaceRpc` | `METHOD`/`Response` ClassVar | list response 用 `ClassVar = list[X]` |

**文件**:新增 `agent/minimax_code/workspace_types/rpc/hunks.py`(454 行,18 类型 + 3 枚举);改 `rpc/__init__.py`(barrel 导入 25 符号 + `__all__` hunks 段 + docstring R76 段);改 `tests/test_rpc.py`(追加 TestHunks 类 ~25 测试 + datetime import + barrel import 块)。

### 映射决策树 + 坑

**决策树 —— hunks.rs serde 模式 -> pydantic 映射**

```
hunks.rs 项
├─ enum lowercase 无 #[default](HunkActionKind)
│  └─ StrEnum,无 default() 类方法(请求专用)
├─ 内部标记枚举 + #[serde(other)](HunkSourceWire)
│  └─ 平铺 WireModel:type:str + prompt_index:int|None
│     + wrap serializer(prompt_index None 时 pop)
│     [pydantic 联合无 #[serde(other)] catch-all -> 平铺模型兜底]
├─ 手写 Deserialize 字符串枚举(FileContentStatusWire)
│  └─ StrEnum + __get_pydantic_core_schema__
│     after-validator over str_schema:未知值 -> UNKNOWN
├─ DateTime<Utc> RFC3339 Z 后缀
│  └─ Annotated[datetime, PlainSerializer(_dt_to_wire)]
├─ PathBuf / Vec<PathBuf>
│  └─ str / list[str]
├─ camelCase 嵌套 struct
│  └─ model_config = _CAMEL;往返用键索引(词序 vs 字段序)
├─ Option<String> 无 skip_serializing_if
│  └─ str | None = None,null 保留
├─ status 必发 + 其他 Option::is_none(FileContentViewWire)
│  └─ wrap serializer pop 指定 camelCase 键
└─ derive Default 的 Response 容器(Bulk/Filtered)
   └─ 字段赋默认值([]/0)使 default() 可用
```

**坑 1 — `#[serde(other)]` 前向容错标记枚举(层内首例 + pydantic 无原生对应)**
`HunkSourceWire` 用 `#[serde(tag="type", rename_all="camelCase")]` + `#[serde(other)] Unknown` 兜底。关键语义:`rename_all` 重命名变体**名**(`AgentEdit`->`agentEdit`)但**不**重命名 struct-variant **字段**(`prompt_index` 保 snake_case)。pydantic 判别联合(`Annotated[Union, Field(discriminator)]`)**无** `#[serde(other)]` catch-all 分支——未知标签直接 decode 失败。解法:建模为**平铺 WireModel**(`type: str` + `prompt_index: int | None`),任何未知 type 字符串都 decode 成功并逐字往返(`HunkSourceWire(type="futureSource").to_wire()["type"]=="futureSource"`)。这比 grok 的 `Unknown->"unknown"` 有损映射更**无损**(保留原 type 值),且 `prompt_index` 用 wrap serializer 在 None 时省略。`prompt_index` 嵌套保 snake_case 是测试断言陷阱(见坑 3)。

**坑 2 — 手写 Deserialize 字符串枚举 + pydantic API 版本坑(FileContentStatusWire)**
`FileContentStatusWire` 是普通 camelCase 字符串枚举,grok **手写** `Deserialize`(L225-239):未知状态串 decode 成 `Unknown` 而非整个结构响应失败。serde 的 `#[serde(other)]` **不允许**用在普通字符串枚举上(只允许内部/相邻标记枚举),故需手写 impl。pydantic 解法:StrEnum 7 成员 + 自定义 `__get_pydantic_core_schema__`。**初版用 `core_schema.no_info_plain_validator_function(..., json_schema=core_schema.str_schema())`**——直接 `TypeError: ... got an unexpected keyword argument 'json_schema'`。当前 pydantic 版本的 `no_info_plain_validator_function` **不接受** `json_schema` 参数。修正为 **after-validator over str_schema**:`core_schema.no_info_after_validator_function(_coerce, core_schema.str_schema(), serialization=...)`——`str_schema` 作基础 schema 提供 JSON "string" 类型 + 验证输入是字符串,`_coerce` after-validator 把字符串映射到枚举成员(`cls(value)` 失败 -> `cls.UNKNOWN`)。这是版本稳定的双全方案(前向容错 decode + 真实 JSON schema)。与 R71 `HookEventNameWire`(str-subclass 用 `no_info_after_validator_function(cls, str_schema())` 逐字保留未知值)对比——本轮是带显式 UNKNOWN 成员的封闭枚举。`TypeAdapter(FileContentStatusWire).validate_python("bogus") is FileContentStatusWire.UNKNOWN`(必须用 TypeAdapter 触发自定义 schema,直接 `FileContentStatusWire("bogus")` 走 StrEnum 构造会抛 ValueError)。

**坑 3 — `prompt_index` 嵌套保 snake_case(测试断言陷阱)**
`HunkSourceWire` 嵌套在 `HunkWire`(用 `_CAMEL`)里时,`prompt_index` 字段在 wire 上**保持 snake_case**(不是 `promptIndex`)。原因:`HunkSourceWire` 自己的 `model_config` 是 `ConfigDict(populate_by_name=True)` **无** `alias_generator`,字段无别名;外层 `HunkWire` 的 `by_alias=True` 不传染内层模型的字段别名(每模型用各自配置)。这**忠实复刻** grok 的 `rename_all` 语义(只重命名变体名,不重命名变体字段,源码 L111-114 注释)。初版测试误断言 `wire["source"]["promptIndex"]` -> KeyError;修正为 `wire["source"]["prompt_index"]` 并加注释引用源码行号。`type` 字段同理无别名保原样。

**坑 4 — DateTime<Utc> Z 后缀 + 微秒精度**
`HunkWire.created_at` 是 `chrono::DateTime<Utc>`,serde 发 RFC3339 带 `Z` 后缀(`2026-06-23T00:00:00Z`)。pydantic 默认 datetime 序列化用 `+00:00`。`IsoUtc = Annotated[datetime, PlainSerializer(_dt_to_wire, return_type=str)]`,`_dt_to_wire` 先 `astimezone(timezone.utc)`,微秒态用 `strftime("%Y-%m-%dT%H:%M:%S.%f").rstrip("0").rstrip(".")`(去尾零 + 去孤立点),整秒态用无微秒格式,统一加 `"Z"`。`test_hunk_wire_microsecond_created_at` 断言 `500000us -> "2026-06-23T12:30:45.5Z"`(尾零剥离)。

**坑 5 — camelCase 词法排序往返 vs serde 字段序(键索引断言)**
`WireModel.to_wire()` = `sort_mappings(model_dump(by_alias=True))`,递归 BTreeMap 词法排序键。grok serde 按字段声明序输出,两者**不一致**。故 camelCase 嵌套 struct(`HunkWire`/`SessionStatsWire`/`TurnSummaryWire` 等)往返断言用**键索引访问**(`wire["createdAt"]`/`wire["acceptedHunks"]`)而非整字典全等。这与 R75 worktree 同策略,是 WireModel 契约的固有约束。

### 验证

三重验证(全绿 + 1 预存 flaky 无关):

```bash
# 1. ruff lint(R76 三文件)
cd "/d/工作/城建院/mm code/agent" && uv run ruff check minimax_code/workspace_types/rpc/hunks.py minimax_code/workspace_types/rpc/__init__.py tests/test_rpc.py
# -> All checks passed!(全目录 ruff --fix 修 325 处 import 排序,但 R76 三文件零残留)

# 2. R76 专项测试
cd "/d/工作/城建院/mm code/agent" && uv run pytest tests/test_rpc.py -q
# -> 253 passed(R75 的 225 + R76 新增 TestHunks ~25 + barrel 扩展,含修 1 处 promptIndex 断言)

# 3. 全量回归
cd "/d/工作/城建院/mm code/agent" && uv run pytest -q
# -> 2336 passed, 10 skipped, 1 failed in 117.85s
#    唯一失败 = test_reliability::test_open_to_half_open_after_cooldown
#    (R17 熔断器时间敏感 cooldown 窗口 flaky, BreakerOpen retry after 0.0s, 与 R76 hunks 完全无关, 轮次独立不碰)
```

**wire 保真交叉验证**:对照 grok `hunks.rs` 源码逐行确认——TestHunks ~25 测试覆盖:10 method 常量(含 2 个无 `hunk_` 前缀的 `get_all_hunks`/`get_session_summary`)、Response ClassVar 形状(list response `list[str]`/`list[FileSummary]`/`list[HunkWire]`/`list[FileContentEntryWire]` + typed response HunkActionResponse/BulkHunkActionResponse/FilteredHunksResponse/SessionSummaryWire)、HunkActionKind 小写无 default + from_str(accept/reject OK + bogus 抛 ValueError)+ `hasattr(HunkActionKind,"default") is False`、HunkSourceWire 前向容错(type 逐字 + prompt_index None 省略 + 未知 type 不抛)、FileContentStatusWire 7 known 值 + default() MISSING + TypeAdapter 已知/未知 decode(unknown->UNKNOWN)+ 序列化 dump 成员值、HunkWire 往返(path str + createdAt Z 后缀 + oldText/patch null 保留 + lineInfo/source 嵌套 + prompt_index 保 snake_case)、微秒 createdAt(`.5Z`)、FileContentViewWire 混合 skip(status 必发 + byteLen/content None 省略 + missing baseline 无字段)、FileContentEntryWire 往返(baseline/current 嵌套 + isAgentFile/staged)、SessionStatsWire/TurnSummaryWire camelCase(Vec<PathBuf>->list[str])、BulkHunkActionResponse/FilteredHunksResponse default([]/0)、FileSummary snake_case、HunkSingleActionReq 嵌套 snake_case、HunkGetFilteredHunksReq 两 Option null 保留、5 empty Req `to_wire()=={}`。5 个新 serde 模式 + 2 个测试断言修正(promptIndex->prompt_index + FileContentStatusWire TypeAdapter)全部锁定。

### YAGNI 边界

本轮明确不做:

- ❌ **rpc/ 剩余 1 文件(fs.rs 754 行)迁移** —— 留 R77(本轮最后一次迭代)。
- ❌ **导入 R39 xai-hunk-tracker 原语** —— 刻意镜像而非导入,保留 grok 的 lean crate/gix 分离(R39 计算层,R76 wire 层)。
- ❌ **实际 hunk handler 实现** —— 本轮仅 wire 类型契约,真正的 diff 应用/accept/reject 运行时能力在 shell 层。
- ❌ **接入 IPC handler 或远程 workspace transport** —— 类型契约层先行,wire DTO 消费端在后续。
- ❌ **FileContentStatusWire 新状态变体扩展** —— 忠实复刻 grok 的 7 成员(missing/binary/tooLarge/lfsPointer/symlink/full/unknown),不预判新状态。
- ❌ **修正 `workspace.get_all_hunks`/`get_session_summary` 命名一致性** —— 逐字保留无 `hunk_` 前缀,这是 grok 客户端/服务端既有契约。
- ❌ **前端 `web/src/types/` 镜像** —— 纯后端 RPC 类型契约,无 wire 事件广播到前端。

### Commit

`feat(platform): R76 diff hunk wire layer (fuse grok xai-grok-workspace-types rpc/ hunks.rs 413 lines -> 454-line module: 10 workspace.hunk_*/get_all_hunks/get_session_summary methods[2 deliberately drop hunk_ prefix verbatim] + 3 enums[HunkActionKind lowercase no-Default request-only + HunkSourceWire tagged-enum #[serde(other)] flat-model fallback + FileContentStatusWire hand-written Deserialize StrEnum with __get_pydantic_core_schema__ after-validator unknown->UNKNOWN] + ~18 wire types[HunkWire/HunkLineInfoWire/FileContentViewWire/FileContentEntryWire/SessionStatsWire/TurnSummaryWire/SessionSummaryWire camelCase + BulkHunkActionResponse/FileSummary/FilteredHunksResponse snake_case], pure leaf no R74 dependency[only WorkspaceRpc+chrono+serde, vs R75 consumes ChangeType/GitFileChange], deliberately mirrors not imports R39 diff primitives[lean crate gix separation preserved], lands 5 serde patterns new to layer: #[serde(other)] forward-tolerant tagged enum via flat type:str model[lossless verbatim round-trip vs grok Unknown->unknown lossy] + hand-written Deserialize forward-tolerant string enum via StrEnum __get_pydantic_core_schema__ after-validator over str_schema[VERSION PITFALL: no_info_plain_validator_function has no json_schema kwarg in this pydantic release, after-validator+str_schema is version-stable dual: forward-tolerant decode + real JSON schema] + DateTime<Utc> RFC3339 Z suffix via PlainSerializer[+00:00 rewrite, microsecond rstrip0] + PathBuf->str natural mapping[layer-first PathBuf wire fields] + camelCase lexical-sort round trips asserted by key index, ~25 new TestHunks tests 253 passed 1 pre-existing flaky[test_reliability cooldown window, unrelated] 2336 total)`

## R77 — fs.rs -> fs.py（文件 I/O wire 层，10 RPC + 5 enum + ~24 struct，5 个 serde 新模式，rpc/ 命名空间全部闭合）

锚点:R77-1 961933e

### 本轮目标

正向迁移 grok `xai-grok-workspace-types::rpc::fs`（754 行 Rust）到 `agent/minimax_code/workspace_types/rpc/fs.py`，落地 10 个 `workspace.put_files` / `get_files` / `workspace.fs_*` / `workspace.client_fs_*` RPC + 5 枚举 + ~24 wire 类型，**闭合 rpc/ 命名空间全部 10 个文件**（envelope/session/agents_md/code_nav/deploy/search/hooks/workspace/skills/git/worktree/hunks/fs，R68-R77 十轮完成整个 `rpc/` 目录）。本轮核心创新不在结构数量，而在一个真正的 DRY 抽象——`_DropNoneWire` 泛型基类（层内首个泛型 None 省略基类，取代 R74/R75/R76 每类手写 pop 列表的重复模式）——以及三个 serde 不对称的精确复刻（`rename="type"` 作用在 String 而非 enum / Req-snake vs Res-camelCase 同族不对称 / `Response = ()` 单元类型）。

### 融合结论

fs.rs 是文件 I/O 的 **wire 表面层**，分三族方法：(1) service-level `put_files`/`get_files`（snake_case 两端，扁平 entries 数组）；(2) `fs_*` 扩展操作（**Req snake / Res camelCase 不对称**——请求无 `rename_all`，响应用 `_CAMEL`）；(3) `client_fs_*` 只读客户端操作（camelCase 两端）。本轮的抽象贡献是 `_DropNoneWire`：一个继承 `WireModel` 的基类，挂 `@model_serializer(mode="wrap")`，dump 后用 dict comprehension 弹出**所有** None 值键。8 个 Response/Data 子类继承它，冒烟测试验证基类 serializer 透传到每个子类。这取代了 R74 `_CamelOmitNone`（camelCase + 硬编码键列表）、R75/R76 逐类 `for key in (...)` pop 的重复——**真正的 DRY**：只要 struct 的每个 Option 字段都带 `skip_serializing_if`，就继承基类即可，零手写。关键的 serde 不对称：`ClientFsStatRes.node_type` **无** `rename="type"`（wire key `nodeType`），而 `ClientFsListNode.node_type` / `ClientFsReadFileRes.content_type` **有**（wire key `type`）——grok 源 fs.rs L466-481 确认，忠实复刻不"修正"成一致。`Response = ()`（Rust 单元类型）复刻为 `Response: ClassVar = type(None)`，是本层首个真正无负载的响应。

### 交付

**fs.rs -> fs.py serde 模式映射**

| grok 项 | Rust serde | pydantic 映射 | 关键点 |
|---|---|---|---|
| `_DropNoneWire` (基类) | 多 struct 共有 `skip_serializing_if="Option::is_none"` 全字段 | `class _DropNoneWire(WireModel)` + `@model_serializer(mode="wrap")` dict-comp pop 全 None 键 | 层内首个泛型 None 省略基类;8 子类继承,取代 R74/R75/R76 逐类 pop |
| `FsListNode.node_type` | `rename="type"` on **String** | `node_type: str = Field(alias="type")` | 覆盖 `_CAMEL` 的 `to_camel`->`nodeType`,wire key `type` |
| `FsReadFileData.content_type` | 同上 | `content_type: str = Field(alias="type")` | String 非枚举的 rename="type" |
| `ClientFsListNode.node_type` | `rename="type"` on enum | `node_type: FsNodeType = Field(alias="type")` | wire key `type` |
| `ClientFsReadFileRes.content_type` | `rename="type"` on enum | `content_type: FsContentType = Field(alias="type")` | wire key `type` |
| `ClientFsStatRes.node_type` | **无** rename,仅 `skip_serializing_if` | `node_type: FsNodeType \| None = None` | wire key **`nodeType`**(不对称!源 L466-481 确认) |
| `FsWriteFileReq`/`FsDeleteFileReq` | `Response = ()` 单元 | `Response: ClassVar = type(None)` | 层内首个无负载响应 |
| `FsListReq` 等 service/fs request | `#[serde(default)]` **无** skip | 普通 `WireModel`,None 发 null | Request Option 必发 null,不能用 `_DropNoneWire` |
| fs_* Response / Data | `skip_serializing_if="Option::is_none"` | 继承 `_DropNoneWire` | None 省略 |
| fs_* Request | 无 `rename_all` | 普通 WireModel(snake) | Req-snake / Res-camel 不对称 |
| client_fs_* 两端 | `rename_all="camelCase"` | `model_config = _CAMEL` 两端 | camelCase 两端对称 |
| `mtime_ms` (`i64`) | epoch 毫秒 | `int` | 区别 `modified_at` RFC3339 串 |
| `u64`/`usize`/`u32` | — | `int` | 全映射 int |
| 6 default fn | `default="fn"` | `_default_true/_default_depth/_default_limit/_default_max_bytes/_default_client_depth/_default_client_limit` | 镜像 grok 默认函数 |

**文件**:新增 `agent/minimax_code/workspace_types/rpc/fs.py`（631 行,5 枚举 + 24 类型 + `_DropNoneWire` 基类 + 6 默认函数）;改 `rpc/__init__.py`（barrel 导入 32 符号 + `__all__` fs 段 30 符号 + docstring R77 段）;改 `tests/test_rpc.py`（追加 TestFs 类 ~25 测试 + barrel import 块 30 符号）。

### 映射决策树 + 坑

**决策树 —— fs.rs serde 模式 -> pydantic 映射**

```
fs.rs 项
├─ struct 全字段 skip_serializing_if="Option::is_none"(Response/Data)
│  └─ 继承 _DropNoneWire 基类[wrap serializer dict-comp pop 全 None]
│     [DRY:取代 R74/R75/R76 逐类 pop 列表]
├─ rename="type" on String(FsListNode.node_type/FsReadFileData.content_type)
│  └─ Field(alias="type") 覆盖 _CAMEL to_camel
├─ rename="type" on enum(ClientFsListNode/ClientFsReadFileRes)
│  └─ Field(alias="type")
├─ 无 rename 的 node_type(ClientFsStatRes)
│  └─ 普通 _CAMEL -> wire key nodeType[不对称!]
├─ Request Option 仅 #[serde(default)] 无 skip
│  └─ 普通 WireModel,None 发 null(不能用 _DropNoneWire)
├─ Response = () 单元
│  └─ Response: ClassVar = type(None)
├─ fs_* Req(无 rename_all) vs Res(camelCase)
│  └─ 不对称:Req 普通 WireModel / Res _CAMEL
├─ client_fs_* 两端 rename_all="camelCase"
│  └─ _CAMEL 两端
├─ i64/u64/usize/u32
│  └─ int[mtime_ms epoch 毫秒 vs modified_at RFC3339 串]
└─ default 函数
   └─ _default_* 模块函数,Field(default_factory=...)
```

**坑 1 — `_DropNoneWire` 泛型 None 省略基类（层内首个真 DRY 抽象）**
R74 的 `_CamelOmitNone` 是 camelCase + 硬编码键 pop；R75/R76 每个混合 skip struct 手写 `for key in ("byteLen","content")` pop 列表——重复且易漏键。本轮 fs.rs 有 8 个 Response/Data struct，**每个字段都带** `skip_serializing_if="Option::is_none"`（纯 Option struct），这正好是泛型抽象的甜区：`class _DropNoneWire(WireModel)` 挂 `@model_serializer(mode="wrap") def _drop_none(self, handler): raw = handler(self); return {k: v for k, v in raw.items() if v is not None}`。8 子类继承，零手写 pop。冒烟测试 `test_drop_none_wire_inherited_by_subclasses` 遍历 8 子类验证 None 字段全部省略。**适用边界**:仅当 struct 的**每个** Option 字段都 skip 时才正确——若混入必发字段或 null 保留字段（如 Request Option），则不能用基类,必须普通 WireModel（见坑 4）。

**坑 2 — `rename="type"` 作用在 String 而非 enum（覆盖 `_CAMEL` 的 `to_camel`）**
`FsListNode.node_type` 在 grok 是 `node_type: String` + `#[serde(rename="type")]`（非枚举！区别 R74 `ChangeType` 枚举、R75 `FileConflict.change_type` 枚举）。在 `_CAMEL`（`alias_generator=to_camel`）下，`node_type` 默认别名是 `nodeType`。要用 `Field(alias="type")` **显式覆盖** `to_camel` 生成器——pydantic 的显式 `Field(alias=...)` 优先于 `alias_generator`。`FsReadFileData.content_type` 同理。这是层内首次 `rename="type"` 作用在原始 `String` 上。

**坑 3 — `ClientFsStatRes.node_type` **无** `rename="type"`（serde 不对称,测试断言陷阱）**
最隐蔽的坑。`ClientFsListNode.node_type`（源 fs.rs L423）和 `ClientFsReadFileRes.content_type`（L535）都有 `#[serde(rename="type")]` -> wire key `type`。但 `ClientFsStatRes.node_type`（源 L466-481）**只有** `skip_serializing_if="Option::is_none"`，**无** rename -> 在 `rename_all="camelCase"` 下 wire key 是 **`nodeType`**。初版 TestFs 三个测试断言用错 key:`test_client_fs_stat_res_full` 误断言 `wire["type"]=="file"`,实际是 `wire["nodeType"]=="file"`;`test_client_fs_list_req_defaults`/`test_client_fs_read_file_req_defaults` 用 snake_case key 访问 camelCase wire(`include_hidden`->`includeHidden`/`max_bytes`->`maxBytes` 等)。**实现 fs.py 正确**（忠实复刻 grok 不对称），测试断言错。修正:camelCase 键 + `ClientFsStatRes` 用 `nodeType` 并注释引用源行号。单词字段（path/depth/limit/offset/size/hash/content/exists/encoding）camelCase 与 snake 相同,无误。

**坑 4 — Req-snake / Res-camelCase 不对称 + Request Option 必发 null**
fs_* 请求（`FsListReq.cwd`/`FsReadFileReq.offset` 等）仅 `#[serde(default)]` **无** `skip_serializing_if` -> None 发 `null`。这意味着**不能用** `_DropNoneWire`（否则 None 被省略,破坏 wire 契约）。这些 Req 用普通 `WireModel` 无 `model_config`,保 snake_case + None 发 null。响应则用 `_CAMEL` + 继承 `_DropNoneWire`。同族 Req/Res 的不对称（snake/camel + null/skip）是本轮最易出错的迁移点。service-level `put_files`/`get_files` 两端 snake;client_fs_* 两端 camel。

**坑 5 — `Response = ()` 单元 + Response 先行定义顺序 + u64/i64→int**
`FsWriteFileReq`/`FsDeleteFileReq` 的 `Response = ()` 复刻为 `Response: ClassVar = type(None)`（层内首个真正无负载响应;R76 empty Req 的 Response 是 list/typed）。Python ClassVar 在类体求值时立即解析,故 Response 类型必须在引用它的 Req 类型**之前**定义（`FsWriteFileReq.Response = type(None)` 无前置依赖,安全;但 `FsListReq.Response = FsListData` 要求 `FsListData` 先定义）。`mtime_ms` 是 `i64` epoch 毫秒（`int`）,`modified_at` 是 RFC3339 串（`str`）——不可混淆。

### 验证

三重验证(全绿,连 R17 flaky 本次都通过):

```bash
# 1. ruff lint(R77 三文件,精确 --fix 仅 R77 两文件避免附带损坏)
cd "/d/工作/城建院/mm code/agent" && uv run ruff check --fix minimax_code/workspace_types/rpc/__init__.py tests/test_rpc.py
# -> Found 2 errors (2 fixed, 0 remaining)[I001 import 排序,__init__ 与 test_rpc 追加的 fs 块归位]
cd "/d/工作/城建院/mm code/agent" && uv run ruff check minimax_code/workspace_types/rpc/fs.py minimax_code/workspace_types/rpc/__init__.py tests/test_rpc.py
# -> All checks passed!

# 2. R77 专项测试
cd "/d/工作/城建院/mm code/agent" && uv run pytest tests/test_rpc.py -q
# -> 284 passed(R76 的 253 + R77 新增 TestFs ~31,含修 3 处 camelCase/type 断言)

# 3. 全量回归
cd "/d/工作/城建院/mm code/agent" && uv run pytest -q
# -> 2368 passed, 10 skipped, 1 warning in 114.01s
#    零失败(连 R17 test_reliability cooldown flaky 本次都稳过)
```

**wire 保真交叉验证**:对照 grok `fs.rs` 源码逐行确认——TestFs ~31 测试覆盖:10 method 常量(service/fs_*/client_fs_* 三族)、`_DropNoneWire` 8 子类继承验证（None 全省略）、Response ClassVar 形状（`type(None)` 单元 + list[PutFileResult]/list[GetFileEntry]/FsListData/FsExistsData/FsReadFileData + ClientFsListRes/StatRes/ReadFileRes）、`rename="type"` on String（FsListNode.node_type/FsReadFileData.content_type wire key `type`）+ on enum（ClientFsListNode/ClientFsReadFileRes wire key `type`）、**ClientFsStatRes 无 rename**（wire key `nodeType`,不对称源 L466-481）、Req-snake/Res-camel 不对称（FsListReq snake None-null vs FsListData camelCase None-skip）、client_fs_* 两端 camelCase（includeHidden/followSymlinks/respectGitIgnore/includeGlobs/excludeGlobs/maxBytes camelCase 键 + depth/limit/offset 单词）、6 默认函数（default_true/default_depth=1/default_limit=1000/default_max_bytes=1048576/default_client_depth=1/default_client_limit=1000）、mtime_ms int epoch 毫秒、PutFileEntry/GetFileEntry 扁平 entries、FsReadEncoding 枚举（utf8 默认）、FsExistsData（exists bool + node_type/size/mtime_ms None 省略）。3 个测试断言修正（camelCase 键 + ClientFsStatRes nodeType）全部锁定。**rpc/ 命名空间 10 文件全部闭合**。

### YAGNI 边界

本轮明确不做:

- ❌ **rpc/ 命名空间已全部闭合（10/10 文件）** —— R68-R77 十轮完成整个 `rpc/` 目录迁移,本轮是最后一次迭代。
- ❌ **实际文件 I/O handler 实现** —— 本轮仅 wire 类型契约,真正的 put_files/get_files/fs_*/client_fs_* 运行时能力在 shell 层。
- ❌ **接入 IPC handler 或远程 workspace transport** —— 类型契约层先行,wire DTO 消费端在后续。
- ❌ **`_DropNoneWire` 推广回填 R74/R75/R76** —— 轮次独立,不碰既有迭代;新基类供后续新 struct 使用。
- ❌ **修正 `ClientFsStatRes.node_type` 命名一致性** —— 逐字保留无 rename（wire `nodeType`）,这是 grok 客户端/服务端既有契约,源 fs.rs L466-481 确认。
- ❌ **前端 `web/src/types/` 镜像** —— 纯后端 RPC 类型契约,无 wire 事件广播到前端。
- ❌ **`xai-grok-workspace-types` crate 其余模块（非 rpc/）** —— rpc/ 是本轮融合范围的闭合边界。

### Commit

`feat(platform): R77 file I/O wire layer closes rpc/ namespace (fuse grok xai-grok-workspace-types rpc/ fs.rs 754 lines -> 631-line module: 10 methods[workspace.put_files/get_files service-level snake + 5 workspace.fs_* extension[Req-snake/Res-camelCase asymmetry] + 3 workspace.client_fs_* read-only client[camelCase both sides]] + 5 enums[FsNodeType/FsContentType/FsReadEncoding + ...] + ~24 wire types, lands 5 serde patterns new to layer: generic _DropNoneWire base class[@model_serializer wrap dict-comp pops ALL None keys, first generic None-elision base supplants R74 _CamelOmitNone + R75/R76 per-class pop lists, 8 Response/Data subclasses inherit verified by smoke] + rename="type" on String not enum[Field(alias=type) overrides _CAMEL to_camel nodeType, FsListNode.node_type + FsReadFileData.content_type] + Req-snake/Res-camelCase asymmetry[fs_* request no rename_all None-emits-null vs fs_* response _CAMEL + _DropNoneWire, client_fs_* camelCase both sides] + Response=() unit type via type(None)[FsWriteFileReq/FsDeleteFileReq, layer-first no-payload response] + u64/i64/usize/u32 all -> int[mtime_ms epoch millis vs modified_at RFC3339 string], KEY PITFALL: ClientFsStatRes.node_type has NO rename="type"[wire key nodeType] vs ClientFsListNode.node_type/ClientFsReadFileRes.content_type DO[wire key type], grok source fs.rs L466-481 confirms asymmetry faithfully mirrored, ~31 new TestFs tests 284 passed test_rpc 2368 total zero-regression R17 flaky passing this run, rpc/ namespace 10/10 files closed R68-R77)`

## R78 — request.rs -> request.py（RequestMessage<T> 泛型 wire 信封，crate 顶层调度层入口，4 模块第 1 个）

锚点:R78-1 7d5a42c

### 本轮目标

正向迁移 grok `xai-grok-workspace-types::request`（141 行 Rust）到 `agent/minimax_code/workspace_types/request.py`，落地 crate 顶层调度层的第 1 个模块（共 4 个：`request` → `requests` → `events` → `chunks`）。`RequestMessage<T>` 是 crate 的根调度信封——每个 workspace RPC 在运行时层包装成 `RequestMessage` 后才上线路（运行时再加 cancellation + extensions map，那些不是 wire 关注点）。本轮是 **crate 顶层调度层**的起点：R67（叶子层）+ R68-R77（`rpc/` 10 文件）是这个信封承载的叶子负载，R78 落地信封本身。本轮核心创新不在行数（141 行是最小的迁移源之一），而在 **层内首个 pydantic v2 泛型 wire 模型**——`RequestMessage(WireModel, Generic[T])`，参数化 `RequestMessage[str]`/`RequestMessage[int]` 让 pydantic 为每个 payload 类型生成独立 schema，忠实复刻 Rust `RequestMessage<T>` 无类型擦除。

### 融合结论

request.rs 是 crate 的 **顶层调度信封层**：`RequestMessage<T>` 包装 typed payload（`message`）+ per-call `metadata`（`Metadata`，`#[serde(default)]` 即便空也发）+ 可选绝对 `deadline`（`Option<DateTime<Utc>>`，`#[serde(default, skip_serializing_if="Option::is_none")]` None 时省略）。构建器 `new`/`with_metadata`/`with_deadline`/`map` 复刻 Rust move 语义。本轮的抽象贡献是 **层内首个通用 wire 模型**：`RequestMessage` 继承 `WireModel` 并 `Generic[T]`，`message: T` 字段在 `RequestMessage[str]` / `RequestMessage[int]` 订阅时生成 per-parameter schema——pydantic v2 原生支持泛型模型，无需 Python 侧类型擦除 hack。`IsoUtc` 类型别名（`Annotated[datetime, PlainSerializer(_dt_to_wire)]`）发 chrono 兼容的 `Z` 后缀 RFC3339，复制自 R76 `rpc/hunks.py`（刻意不 import `rpc/` 以保持顶层信封零 `rpc/` 依赖；未来轮次可提升到 `_wire.py`）。`to_wire()` 重写基类：`deadline is None` 时 pop 掉 key（精准单字段重写，比 R77 通用 `_DropNoneWire` 简单——本 struct 只有一个 Option）。

### 交付

**request.rs -> request.py serde 模式映射**

| grok 项 | Rust serde | pydantic 映射 | 关键点 |
|---|---|---|---|
| `RequestMessage<T>` 泛型 | `struct RequestMessage<T>` | `class RequestMessage(WireModel, Generic[T])` | 层内首个泛型 wire 模型;`RequestMessage[str]`/`[int]` per-param schema |
| `message: T` | typed payload | `message: T`(`T` = TypeVar) | 订阅时绑定 |
| `metadata: Metadata` | `#[serde(default)]` 无 skip | `metadata: Metadata = Field(default_factory=Metadata)` | 即便空也发 `"metadata": {}` |
| `deadline: Option<DateTime<Utc>>` | `#[serde(default, skip_serializing_if="Option::is_none")]` | `deadline: IsoUtc \| None = None` + `to_wire()` 重写 pop | None 时省略;有值时 Z 后缀 |
| `DateTime<Utc>` | chrono serde `Z` 后缀 | `IsoUtc = Annotated[datetime, PlainSerializer(_dt_to_wire)]` | 复制自 R76 rpc/hunks;+00:00 重写为 Z |
| `new`/`with_metadata`/`with_deadline` | `mut self` builder | classmethod + in-place mutate return self | Rust move 语义 |
| `map<U>(self, f)` | `FnOnce(T) -> U` | `model_construct` 绕过验证 | [KEY PITFALL:见坑] |
| `Metadata` 作 pydantic 字段 | `#[serde(transparent)]` over BTreeMap | `__get_pydantic_core_schema__` after-validator over dict_schema | [KEY PITFALL:见坑,改 metadata.py] |

**文件**:新增 `agent/minimax_code/workspace_types/request.py`（~143 行,泛型模型 + `IsoUtc` + `_dt_to_wire` + 4 builder/map + `to_wire` 重写）;改 `metadata.py`（Metadata 加 `__get_pydantic_core_schema__` after-validator over `dict_schema(str,str)`,让 Metadata 成 pydantic 一等字段,R78 RequestMessage 复用,镜像 R76 FileContentStatusWire）;改顶层 `workspace_types/__init__.py`（barrel `from .request import RequestMessage` + `__all__` "request envelope (R78)" 段 + docstring R78 段）;改 `tests/test_workspace_types.py`（追加 TestRequestMessage 类 8 测试 + import 块加 `RequestMessage`/`warnings`）。

### 映射决策树 + 坑

**决策树 —— request.rs serde 模式 -> pydantic 映射**

```
request.rs 项
├─ RequestMessage<T> 泛型 wire 模型
│  └─ class RequestMessage(WireModel, Generic[T]);T = TypeVar
│     RequestMessage[str]/[int] 订阅生成 per-param schema
│     [层内首个泛型 wire 模型]
├─ metadata: Metadata #[serde(default)] 无 skip
│  └─ Field(default_factory=Metadata);to_wire 不省略 -> 总发 {}
├─ deadline: Option<DateTime<Utc>> #[serde(default, skip_serializing_if=Option::is_none)]
│  └─ deadline: IsoUtc | None = None + to_wire() 重写 pop
│     [精准单字段重写,比 R77 _DropNoneWire 简单]
├─ DateTime<Utc> Z 后缀
│  └─ IsoUtc = Annotated[datetime, PlainSerializer(_dt_to_wire)]
│     复制自 R76 rpc/hunks(刻意不 import rpc/ 保持零 rpc 依赖)
├─ new/with_metadata/with_deadline builder(mut self)
│  └─ classmethod + in-place mutate return self
└─ map<U>(self, f: FnOnce(T)->U)
   └─ RequestMessage.model_construct(message=f(..), metadata=Metadata(self.metadata), deadline=self.deadline)
      [KEY PITFALL:绕过验证,见下]
```

**坑 1 —— Metadata 作 pydantic 字段触发 PydanticSchemaGenerationError**

`RequestMessage` 把 `metadata: Metadata`（dict 子类）作为 pydantic 字段。pydantic v2 **无法为任意 dict 子类自动生成 schema** -> `PydanticSchemaGenerationError: Unable to generate pydantic-core schema for <class '...Metadata'>`。修复：在 `metadata.py` 给 Metadata 加 `__get_pydantic_core_schema__` classmethod,返回 `core_schema.no_info_after_validator_function(cls, core_schema.dict_schema(str_schema, str_schema))` —— 先按 `dict[str, str]` 验证,再 after-validator 把 dict 重新包成 Metadata 实例。这让 wire 读回的 metadata 保留 sorted-map 方法（`iter_sorted`/`to_wire`）。镜像 R76 `FileContentStatusWire.__get_pydantic_core_schema__`。选这个方案（而非 `metadata: dict[str, str]` 字段类型）因为它是 grok-faithful（Metadata 是一等类型）且 R79+ 的 request/event 模型可复用同一个 schema 路由。

**坑 2 —— 未参数化 `RequestMessage(...)` 构造把 kwargs 误路由进 metadata dict 字段**

`map` 第一版直接 `RequestMessage(message=f(self.message), metadata=Metadata(self), deadline=self.deadline)` -> pydantic 报 3 个 `metadata.message` / `metadata.metadata` / `metadata.deadline` 错误,所有 kwargs 被当作 **metadata dict 字段** 的键值对验证（pydantic 泛型 + Metadata 的 dict_schema 交互:T 未绑定时 `message: T`=Any 与 metadata dict_schema 的组合让构造把多余 kwargs 喂给 dict）。关键观察:参数化构造（`RequestMessage[str].new(...)` / `RequestMessage[int].new(...)`）正常工作,只有 map 内**未参数化** `RequestMessage(...)` 失败。第二版用 `model_copy(update={"message": f(self.message)})` 修复了构造,但引入新问题:实例保留源 schema（`RequestMessage[int]`）,message 值已变 str,`to_wire` 的 `model_dump` 触发 `PydanticSerializationUnexpectedValue` UserWarning（int schema 收 str 值）。最终版用 `RequestMessage.model_construct(message=f(..), metadata=Metadata(self.metadata), deadline=self.deadline)` —— `model_construct` 绕过验证,结果带**未参数化** schema（T=Any）,接受任意 payload 类型无警告。这是 Python 泛型无法在运行时改变类型参数（grok `map<U>` 返回 `RequestMessage<U>`）的务实近似:wire 行为完全正确（map 后 `to_wire` 输出 `{message: "1", metadata: {k:v}, deadline: ...Z}`）,`-W error::UserWarning` 下零警告。test `test_map_emits_no_serializer_warning` 用 `warnings.simplefilter("error", UserWarning)` 钉死这条契约。

**坑 3 —— `metadata` 字段类型从 `dict` 收紧为 `Metadata`**

`RequestMessage.metadata` 类型注解是 `Metadata`（不是裸 `dict`）。这要求 `__get_pydantic_core_schema__`（坑 1）正确路由,否则 pydantic 拒绝生成 schema。验证:smoke step 2 确认 `model_validate(wire)` 读回的 `back.metadata` 是 `Metadata` 实例（`isinstance` 检查通过）,保留 sorted-map 方法。

### 验证

- **冒烟测试**(heredoc 直跑):5 个断言全 GREEN —— string 往返 + deadline 省略 + metadata 总发 + metadata 读回 rewrap 成 Metadata + deadline Z 往返 + map 变换 payload 保留 metadata/deadline + map wire 正确重 dump + `-W error::UserWarning` 下 map 零序列化警告 + mapped wire 经 `RequestMessage[str].model_validate` 往返。
- **pytest `tests/test_workspace_types.py`**:**70 passed**(R78 新增 TestRequestMessage 8 测试:string 往返 / metadata 总发 / metadata 往返 rewrap / deadline Z 往返 / builder in-place mutate / map 保留 + 新 Metadata 不别名 / map 零警告 / map 无 metadata/deadline 往返)。
- **ruff**:4 文件全 clean(request.py/metadata.py/__init__.py/test_workspace_types.py)。
- **全回归**:`2376 passed, 10 skipped, 1 warning`(1 warning 是 fastapi starlette TestClient 弃用,与 R78 无关)。零回归。

### YAGNI 边界

- **未迁移 `requests` / `events` / `chunks`**:本轮只迁 request.rs（crate 顶层调度层 4 模块的第 1 个）。`requests/mod.rs` 的 `WorkspaceRequest` 外部信封（相邻标记 `Tool`/`Ops`/`Session`）+ `events` + `chunks` 留给 R79+/R80+。
- **`IsoUtc` 复制自 `rpc/hunks` 未提升**:顶层信封刻意零 `rpc/` 依赖,`_dt_to_wire` + `IsoUtc` 从 R76 `rpc/hunks.py` 复制到 `request.py`。两份相同实现是已知 DRY 债,但提升到 `_wire.py` 会改变 `_wire.py` 的职责（当前只放 `WireModel`/`sort_mappings`）,留待 DateTime 类型在顶层出现 3+ 消费者时再统一提升。
- **`map` 用 `model_construct` 而非真泛型订阅**:Python 泛型运行时无法表达 grok `map<U> -> RequestMessage<U>` 的类型变换。`model_construct` 带未参数化 schema（T=Any）是务实近似 —— wire 完全正确,类型参数静态层面不可表达但不影响运行时。未来若需要保留强类型,可探索 `map(self, f) -> RequestMessage[Any]` 显式标注或运行时泛型工厂。
- **不引运行时 cancellation/extensions**:request.rs 的 `RequestMessage` 只有 wire 部分（message/metadata/deadline）;运行时层加的 cancellation token + extensions map 是进程内关注点,不上线。YAGNI:本轮不实现运行时信封,只迁 wire 契约。
- **`metadata` 字段类型收紧**:从宽松 `dict` 收紧到 `Metadata` 是 R78 驱动（让信封携带 typed metadata），不是修复 R67 bug。R67 的 Metadata 已是 dict 子类,R78 只是让它成为 pydantic 一等字段。

### Commit

`feat(platform): R78 request.rs -> request.py（RequestMessage<T> 泛型 wire 信封, crate 顶层调度层入口 4 模块第 1 个）[新增 request.py 143 行: RequestMessage(WireModel, Generic[T]) 层内首个泛型 wire 模型, message:T + metadata:Metadata Field(default_factory) #[serde(default)] 总发 + deadline:IsoUtc|None + to_wire 重写 pop #[skip_serializing_if Option::is_none] + IsoUtc=Annotated[datetime,PlainSerializer(_dt_to_wire)] Z 后缀复制自 R76 rpc/hunks 刻意零 rpc 依赖 + new/with_metadata/with_deadline builder in-place mutate + map<U> 用 model_construct 绕过验证; 增强 metadata.py: Metadata 加 __get_pydantic_core_schema__ after-validator over dict_schema(str,str) 让 dict 子类成 pydantic 一等字段镜像 R76 FileContentStatusWire; barrel __init__.py 导出 RequestMessage + docstring R78 段; TestRequestMessage 8 测试; KEY PITFALL 1: Metadata 作 pydantic 字段触发 PydanticSchemaGenerationError 需 __get_pydantic_core_schema__ 路由; KEY PITFALL 2: map 未参数化 RequestMessage(...) 构造把 kwargs 误路由进 metadata dict 字段(message/metadata/deadline 3 验证错误) -> model_copy 修复但留源 schema RequestMessage[int] 收 str 值触发 PydanticSerializationUnexpectedValue UserWarning -> 最终 model_construct 带 T=Any 未参数化 schema 零警告, Python 泛型运行时无法表达 map<U> RequestMessage<U> 的务实近似, test_map_emits_no_serializer_warning 用 warnings.simplefilter error 钉契约; 验证 ruff 4 文件 clean + pytest test_workspace_types.py 70 passed + 全回归 2376 passed 10 skipped 1 warning 零回归, 锚点 R78-1 7d5a42c]`


## R79 — requests.rs -> requests.py（WorkspaceRequest 外部信封，crate 顶层调度层第 2 模块，4 个相邻标记枚举共享 AdjacentTagged 基类）

锚点:R79-1 a3c8675

### 本轮目标

迁移 grok `xai-grok-workspace-types::requests` 子目录（`mod.rs` / `ops.rs` / `session.rs` / `tool.rs` 4 文件）→ 单文件 `requests.py`，落地 crate 顶层调度层的**第 2 个模块**（R78 是第 1 个 `request` 信封，R79 是它携带的**请求鉴别器**）。交付 4 个相邻标记（adjacent-tagged）枚举 `WorkspaceRequest` / `WorkspaceOpsRequest` / `SessionLifecycleRequest` / `ToolRequest` + 1 个 struct `ToolCallArgs`，共 29 个 wire 变体（18 ops + 8 session + 2 tool + 3 outer）。所有 4 枚举共享同一个 serde 形状 `#[serde(tag="type", content="data", rename_all="snake_case")]`，因此全部复用 R67 `AdjacentTagged` 基类（与 `WorkspaceError` 同构），本轮**零新 serde 模式**——是对既定相邻标记配方的一次机械展开。

### 融合结论

本轮的工程价值不在引入新模式，而在**两点统一**：

1. **4 枚举共享 `AdjacentTagged` 基类**：grok 用 `#[serde(tag, content, rename_all)]` 宏让 4 个独立 enum 共享序列化形状；Python 侧 R67 的 `AdjacentTagged` 基类（`_VARIANTS` + 每变体工厂 classmethod + `to_wire`/`from_wire`）正是这个宏的等价抽象。`WorkspaceError`（13 变体，R67）已验证此配方；R79 把它扩展到 18+8+2+3=31 个变体（含 outer 三向派发），配方零修改可复用。这是 R67 基类设计的**正向回报**——一次抽象，四处复用。

2. **`_payload()` 统一 newtype 变体负载强制转换**：grok 相邻标记的 newtype 变体（`MemoryWrite(String)` / `GitStatus(GitStatusOpts)` / `ActOnHunk(HunkAction)` / `Tool(ToolRequest)` 等）在 serde `content` 槽里放 inner 值，serde 递归调用 inner 的 `Serialize`。Python 侧 `AdjacentTagged.payload` 持有 as-typed 的 inner 值，工厂必须把它降为 wire-ready 形式。4 种 inner 值类型（相邻标记嵌套枚举 / WireModel struct / 透明 str-newtype / 原始标量）由一个 `_payload(obj)` 辅助统一处理：`hasattr(obj, "to_wire")` 路由前两种（委托各自的 `to_wire`），后两种原样直通。这是 DRY 的典范——一个 3 行辅助替代 18 个变体各自的 `isinstance` 分支。

### 交付

- **`agent/minimax_code/workspace_types/requests.py`（312 行，新建）**：
  - `_payload(obj)` 辅助：`if hasattr(obj, "to_wire"): return obj.to_wire(); return obj`。
  - `ToolCallArgs(WireModel)`：4 字段 plain wire struct（`session: SessionId` / `tool_name: str` / `input_json: str = ""` `#[serde(default)]` / `call_id: ToolCallId`）。
  - `ToolRequest(AdjacentTagged)`：`_VARIANTS = ("call", "definitions")`，2 工厂（`call(args)` newtype over ToolCallArgs / `definitions()` unit）。
  - `WorkspaceOpsRequest(AdjacentTagged)`：18 变体 `_VARIANTS`（git_status/git_diff/git_branch_info/git_metadata/list_hunks/act_on_hunk/ripgrep/fuzzy_search/discover_skills/discover_plugins/load_project_config/load_permissions/load_envrc/resolve_file_refs/memory_search/memory_write/install_plugin/refresh_plugins），18 工厂方法。
  - `SessionLifecycleRequest(AdjacentTagged)`：8 变体 `_VARIANTS`（fork/destroy/list/apply_worktree/begin_prompt/end_prompt/rewind/get_rewind_points），8 工厂方法。
  - `WorkspaceRequest(AdjacentTagged)`：`_VARIANTS = ("tool", "ops", "session")`，3 工厂（每变体 newtype over 对应子枚举，`_payload` 委托）。
- **`agent/minimax_code/workspace_types/__init__.py`（barrel 更新）**：docstring 加 R79 段落（4 请求鉴别器）；import 块加 5 符号；`__all__` 加 `# request discriminators (R79 ...)` 块。
- **`agent/tests/test_workspace_types.py`（+19 测试）**：5 个新测试类——`TestToolCallArgs`（2：全字段往返 + input_json 默认 ""）、`TestToolRequest`（2：call 包装 ToolCallArgs wire dict + definitions unit）、`TestWorkspaceOpsRequest`（7：9 unit 变体发 null data / 4 struct-newtype 变体委托 inner to_wire / act_on_hunk 嵌套 AdjacentTagged / 2 string-newtype 裸字符串 / resolve_file_refs 裸 list / memory_search struct 变体 u32 强制 / **18 变体穷举 from_wire 往返**）、`TestSessionLifecycleRequest`（4：list unit + 3 SessionId newtype 裸字符串 + fork 委托 AgentSessionConfig + struct 变体 u64 强制 + **8 变体穷举 from_wire 往返**）、`TestWorkspaceRequest`（3：3 变体嵌套子枚举 + 嵌套往返 + from_wire 拒绝未知变体 "events"）。

### 映射决策树 + 坑

```
requests.rs（grok requests/ 子目录 4 文件）
├─ mod.rs WorkspaceRequest { Tool/Ops/Session }（相邻标记）
│  └─ WorkspaceRequest(AdjacentTagged) _VARIANTS=("tool","ops","session")
│     └─ 每变体 newtype over 子枚举 -> _payload 委托子枚举 to_wire
├─ tool.rs ToolRequest { Call(ToolCallArgs)/Definitions }（相邻标记）
│  ├─ ToolRequest(AdjacentTagged) _VARIANTS=("call","definitions")
│  └─ ToolCallArgs struct（session/tool_name/input_json/call_id）
│     └─ WireModel #[serde(default)] input_json -> Field default=""
├─ ops.rs WorkspaceOpsRequest 18 变体（相邻标记）
│  └─ WorkspaceOpsRequest(AdjacentTagged) 18 _VARIANTS
│     ├─ unit 变体（9：git_branch_info 等）-> cls(kind, None) data=null
│     ├─ struct-newtype 变体（4：git_status(GitStatusOpts) 等）
│     │  └─ _payload(opts) -> opts.to_wire()（WireModel BTreeMap 排序）
│     ├─ 嵌套枚举 newtype 变体（1：act_on_hunk(HunkAction)）
│     │  └─ _payload(action) -> action.to_wire() -> {"type":"accept","data":"h1"}
│     ├─ string-newtype 变体（2：memory_write/install_plugin）-> str(text) 裸字符串
│     ├─ Vec<String> newtype 变体（1：resolve_file_refs）-> [str(r) ...] 裸 list
│     └─ struct 变体（1：memory_search{query,limit}）-> 手建 dict（grok 内联字段，无命名 struct）
│        └─ u32 limit -> int(limit) 防御强制（bool/数值子类不扩 wire 类型）
├─ session.rs SessionLifecycleRequest 8 变体（相邻标记）
│  └─ SessionLifecycleRequest(AdjacentTagged) 8 _VARIANTS
│     ├─ unit 变体（1：list）-> cls("list", None)
│     ├─ AgentSessionConfig newtype 变体（1：fork）-> _payload(config) 委托
│     ├─ SessionId newtype 变体（3：destroy/apply_worktree/get_rewind_points）
│     │  └─ _payload(session) -> SessionId 无 to_wire 直通为裸 str 子类
│     └─ struct 变体（3：begin_prompt/end_prompt/rewind）
│        └─ {session: _payload(session), idx/target: int(..)} u64 防御强制
└─ 全部复用 R67 AdjacentTagged 基类（与 WorkspaceError 同构，零新 serde 模式）
```

**坑 1 —— 单文件 vs 子目录（grok 是 `requests/` 子目录 4 文件）**

grok 把 4 个 enum 拆到 `requests/{mod,ops,session,tool}.rs` 子模块（可见性 + 编译单元隔离的 Rust 惯例）。Python 侧选**单文件 `requests.py`**（312 行）而非镜像子目录，匹配 R78 `request.py` 的既定模式（R78 也是单文件信封）。理由：4 个 enum 高度耦合（outer `WorkspaceRequest` 派发到 3 个子枚举，`ToolRequest.call` 依赖 `ToolCallArgs`），拆 4 文件会引入 4 处循环 import 风险 + 4 个小模块（最小 `tool` 仅 2 变体 1 struct）。单文件让 4 枚举 + struct + `_payload` 辅助共处一文件，依赖图扁平，与 R78 `request.py`（envelope + IsoUtc + 泛型逻辑共处）一致。barrel `__init__.py` 仍统一导出 5 符号，对外 API 不受内部文件组织影响。

**坑 2 —— `_payload()` 统一异构 newtype 负载（R79 核心创新）**

grok 相邻标记 newtype 变体的 serde `content` 槽持有 inner 值，serde 递归调 inner 的 `Serialize`。Python 侧 `AdjacentTagged.payload` 持 as-typed inner，工厂须降为 wire-ready。4 种 inner 类型：(1) 相邻标记嵌套枚举（`HunkAction`）有 `to_wire`；(2) `WireModel` struct（`ToolCallArgs`/`AgentSessionConfig`/`GitStatusOpts`/`GitDiffArgs`/`RipgrepArgs`/`FuzzySearchArgs`/子枚举）有 `to_wire`；(3) 透明 str-newtype（`SessionId`/`ToolCallId`）**无** `to_wire`（pydantic core schema 序列化为裸 str）；(4) 原始 `str`/`list`/`int` 无 `to_wire`。关键洞察：(1)(2) 与 (3)(4) 的分界恰是 `hasattr(obj, "to_wire")`——`_payload(obj)` 一个 3 行辅助统一 4 类，前两类委托各自 `to_wire`，后两类原样直通。这替代了每变体各自的 `isinstance`/类型分派，是 DRY 的纯粹体现。验证：`act_on_hunk` 嵌套 `HunkAction` → `_payload` 调其 `to_wire` → `{"type":"act_on_hunk","data":{"type":"accept","data":"h1"}}`（两层相邻标记嵌套）；`destroy(SessionId)` → `_payload` 直通 str 子类 → `{"type":"destroy","data":"s1"}`。

**坑 3 —— `AgentSessionConfig()` 无参构造失败（agent_id 必填）**

测试首版用 `AgentSessionConfig()` 无参构造 `fork` 变体负载，pydantic 报 `agent_id Field required`。查 `types/config.py`：`AgentSessionConfig.agent_id: str` 是必填字段（grok `agent_id` 无 `#[serde(default)]`），但有 `.default()` 工厂返回 `cls(agent_id="")`（R67 既定约定，镜像 Rust `Default`）。修复：2 处 `AgentSessionConfig()` → `AgentSessionConfig.default()`。委托断言 `req.to_wire() == {"type":"fork","data":cfg.to_wire()}` 不关心具体值，只比较两侧相等，所以 `.default()` 的 `agent_id=""` 完全 OK。教训：写测试前应先确认目标类型的可构造性——数据驱动（pytest 失败）抓住了这个真实约束。

**坑 4 —— `list()` 工厂方法名遮蔽内置**

`SessionLifecycleRequest.list()` 工厂方法名与 Python 内置 `list` 同名。ruff 规则集（E/F/W/I/B/UP）**不含 `flake8-builtins`**，所以 `A003`（class-attribute-shadowing-builtin）不触发。方法体内不使用 `list` 内置，故运行时无副作用。保留 `list` 命名以匹配 grok 变体的 snake_case wire tag `"list"`（工厂方法名 == wire tag 是 AdjacentTagged 配方约定）。docstring 显式记录这个遮蔽决策与规则集依据，避免未来误判。

### 验证

- **ruff**：3 文件全 clean（`requests.py` / `__init__.py` / `test_workspace_types.py`）。`All checks passed!`
- **pytest `tests/test_workspace_types.py`**：**89 passed**（R79 新增 5 类 19 测试 + 既有 70）。覆盖 18 ops 变体穷举 + 8 session 变体穷举 + 3 outer 变体嵌套 + ToolCallArgs struct 往返 + from_wire 拒绝未知变体。
- **全回归**：`2395 passed, 10 skipped, 1 warning`（1 warning 是 fastapi starlette TestClient 弃用，与 R79 无关）。R78 是 2376，R79 +19 测试 → 2395，**零回归**。

### YAGNI 边界

- **未迁 `events` / `chunks`**：本轮只迁 `requests/`（crate 顶层调度层 4 模块的第 2 个）。`events/`（mod/lag/workspace 3 文件，订阅事件流）+ `chunks/`（mod/ops/session/tool 4 文件，29 变体 ChunkKind 的 RPC 响应分块）留给 R80/R81。`WorkspaceRequest` 三向派发 `Tool`/`Ops`/`Session` 刻意不含 `Events`（grok `Events` 是独立订阅类型，非请求变体），测试 `test_unknown_variant_rejected_by_from_wire` 用 `"events"` 钉死这条边界。
- **struct 变体内联手建 dict 不引入命名 struct**：grok 的 `MemorySearch { query, limit }` / `BeginPrompt { session, idx }` 等是变体内联字段，无独立 struct 类型。Python 侧在工厂里手建 dict（`{"query": str(query), "limit": int(limit)}`），不引入新 `WireModel` 子类。理由：这些字段组合只在单一变体出现一次，命名 struct 是过度设计（YAGNI）；手建 dict 直接镜像 grok 内联声明，零抽象层。
- **u32/u64 防御 `int()` 强制转换**：`memory_search` 的 `limit`（u32）+ `begin_prompt`/`end_prompt` 的 `idx` + `rewind` 的 `target`（u64）都在工厂里 `int(...)` 强制。这是 lib.rs wire-stability rationale（`usize` 主机相关，统一 `u64`/`u32`）的 Python 侧防御——防止 `bool`（`True`→1）或数值子类意外扩宽 wire 类型。不做范围校验（grok serde 也不校验 u32 溢出，序列化层信任上游）。
- **不引运行时 RPC 调度**：`requests.py` 只迁 wire 契约（4 枚举 + struct 的序列化形状），不实现实际的 tool/ops/session RPC 分派逻辑（那是 runtime transport 层关注点，R68-R77 已迁 rpc/ 地基，调度接线留给后续消费轮）。
- **`_payload` 辅助不提升为 AdjacentTagged 方法**：`_payload` 是模块级函数而非 `AdjacentTagged` 方法/staticmethod。理由：它是工厂内部的负载归一化原语，不暴露给外部调用者；提升为基类方法会扩大 `AdjacentTagged` 的公共 API 面。若未来 R80/R81 的 events/chunks 也需类似归一化，可再评估提升。

### Commit

`feat(platform): R79 requests.rs -> requests.py（WorkspaceRequest 外部信封 crate 顶层调度层第 2 模块 4 相邻标记枚举共享 AdjacentTagged 基类）[新增 requests.py 312 行: _payload(obj) 3 行辅助 hasattr to_wire 统一 4 类 newtype 负载(相邻标记嵌套枚举 HunkAction + WireModel struct 委托 to_wire / 透明 str-newtype SessionId + 原始 str|list|int 直通) + ToolCallArgs(WireModel) 4 字段 #[serde(default)] input_json + ToolRequest(AdjacentTagged) 2 变体 call|definitions + WorkspaceOpsRequest(AdjacentTagged) 18 变体(9 unit null data + 4 struct-newtype 委托 + act_on_hunk 嵌套 HunkAction + memory_write|install_plugin 裸 str + resolve_file_refs 裸 list + memory_search 手建 dict u32 强制) + SessionLifecycleRequest(AdjacentTagged) 8 变体(fork 委托 AgentSessionConfig + destroy|apply_worktree|get_rewind_points SessionId 裸 str + begin_prompt|end_prompt|rewind 手建 dict u64 强制 + list unit) + WorkspaceRequest(AdjacentTagged) 3 变体 tool|ops|session newtype over 子枚举; barrel __init__.py 导出 5 符号 + docstring R79 段; 5 测试类 19 测试(18 ops 变体穷举 + 8 session 变体穷举 + 3 outer 嵌套 + from_wire 拒绝 events); KEY 决策 1: 4 枚举共享 R67 AdjacentTagged 基类与 WorkspaceError 同构零新 serde 模式; KEY 坑 1: 单文件 vs 子目录选单文件匹配 R78 request.py 避免 4 文件循环 import; KEY 坑 2: _payload 统一异构 newtype 负载是 R79 核心创新 hasattr to_wire 分界; KEY 坑 3: AgentSessionConfig 无参构造失败 agent_id 必填 -> .default() 工厂; KEY 坑 4: list 工厂遮蔽内置 ruff 规则集无 flake8-builtins A003 不触发; 验证 ruff 3 文件 clean + pytest test_workspace_types.py 89 passed + 全回归 2395 passed 10 skipped 1 warning 零回归, 锚点 R79-1 a3c8675]`


## R80 — events.rs -> events.py（订阅事件流，crate 顶层调度层第 3 模块，3 种 serde 模式落地透明 u32 位掩码 newtype）

锚点:R80-1 20d8bbf

### 本轮目标

迁移 grok `xai-grok-workspace-types::events` 子目录（`mod.rs` / `lag.rs` / `workspace.rs` 3 文件）→ 单文件 `events.py`，落地 crate 顶层调度层的**第 3 个模块**（R78 信封 → R79 请求鉴别器 → R80 订阅事件流 → R81 响应分块）。交付 4 个类型：相邻标记事件联合 `WorkspaceEvent`（12 变体）+ 单变体 lag 信号 `EventLag` + 普通 snake_case 枚举 `WorkspaceTopic`（7 变体）+ 透明 u32 位掩码 newtype `WorkspaceTopicSet`。本轮落地 **3 种 serde 模式**——其中 2 种复用既定配方（相邻标记 + StrEnum），1 种是本层**首个透明非字符串 newtype**（`WorkspaceTopicSet` 序列化为裸 int 位掩码）。

### 融合结论

本轮的工程价值在 **serde 模式谱系的补全** + **crate 设计意图的忠实还原**：

1. **3 种 serde 模式同台**：grok `events/` 一个子目录就用了 3 种 serde 派生——`WorkspaceEvent`/`EventLag` 是相邻标记（`#[serde(tag,content,rename_all)]`，复用 R67 `AdjacentTagged`）；`WorkspaceTopic` 是普通 `#[serde(rename_all="snake_case")]` 外部标记枚举（序列化为裸 `"fs"` 字符串，复用 `enum.StrEnum`）；`WorkspaceTopicSet` 是 `#[serde(transparent)]` over `u32`（序列化为裸整数位掩码，**新模式**）。三种模式覆盖了 Rust enum 序列化的主要派生形态，Python 侧分别用 3 种既有原语承接，仅 WorkspaceTopicSet 需直接 to_wire/from_wire（无新基类）。

2. **透明非字符串 newtype = 新 serde 形态**：R67 已有透明**字符串** newtype（`SessionId` 经 pydantic core schema 序列化为裸 str）。`WorkspaceTopicSet` 是透明**非字符串** newtype（u32 → 裸 int），且携带方法（empty/all/with_topic/contains）+ 从不是 `WireModel` 字段，故不能复用 `SessionId` 的 core-schema 路径——直接实现 `to_wire() -> int` / `from_wire(data: int)`。这补全了"透明 newtype"的两种形态（str / int），为后续可能的透明数值 newtype（如 `u64` token 计数）立了先例。

3. **无 SessionEvent 设计的忠实还原**：grok `events/mod.rs` docstring 明确——EventBus 只携带 `WorkspaceEvent`（工作区**观察到的外部状态**）；sampler 引起的状态（prompt 边界、工具调用生命周期、plan-mode、subagent、compaction）**不经 EventBus**，而是经 chunk 流（R81）。Python 侧如实记录这条设计边界，不为对称性臆造 `SessionEvent`——这是"迁移而非重构"纪律的体现。

### 交付

- **`agent/minimax_code/workspace_types/events.py`（428 行，新建）**：
  - 模块 docstring 记录 3 种 serde 模式 + 无 SessionEvent 设计 + `with`→`with_topic` 重命名 + `_dt_to_wire` 第 3 使用者。
  - `_payload(obj)` 辅助（复制自 R79，3 行）：`hasattr(obj, "to_wire")` 路由 AdjacentTagged/WireModel 委托，StrEnum/裸标量直通。
  - `WorkspaceTopic(StrEnum)`：7 变体（Fs/Vcs/Discovery/Servers/Index/Config/Tools = `"fs"`/`"vcs"`/`"discovery"`/`"servers"`/`"index"`/`"config"`/`"tools"`）。
  - `_TOPIC_INDEX` 字典（7 topic → 稳定 bit 0-6）+ `_ALL_TOPIC_BITS = (1<<7)-1 = 127`。
  - `WorkspaceTopicSet`：`__slots__=("bits",)`，`__init__(bits=0)` u32 掩码钳制（`int(bits) & 0xFFFFFFFF`），`to_wire() -> int` 裸整数，`from_wire(cls, data)` 拒绝 bool/非 int，`empty()`/`all()` 工厂，`with_topic(topic)` 原位 `|=` 返回 self，`contains(topic)`/`is_empty()`，`__eq__`/`__hash__`/`__repr__`。
  - `WorkspaceEvent(AdjacentTagged)`：12 变体 `_VARIANTS`（fs_changed/git_head_changed/git_lock_held/skills_changed/plugins_changed/hooks_changed/mcp_server_state_changed/lsp_server_state_changed/codebase_index_updated/project_config_changed/permission_policy_changed/tools_changed），12 工厂 classmethod，`topic() -> WorkspaceTopic` match 语句分类。
  - `EventLag(AdjacentTagged)`：单变体 `_VARIANTS=("lagged",)`，`lagged(cls, n)` u64 强制，`__str__` 镜像 `thiserror #[error("lagged by {0} events")]`。
- **`agent/minimax_code/workspace_types/__init__.py`（barrel 更新）**：docstring 加 R80 段落（3 种 serde 模式 + 透明非字符串 newtype）；import 块加 4 符号（isort 字母序，errors 后 identity 前）；`__all__` 加 `# event stream (R80 ...)` 块。
- **`agent/tests/test_workspace_types.py`（+32 测试）**：4 个新测试类——`TestEventLag`（4：lagged wire 形状 + u64 bool 强制 + `__str__` thiserror Display + from_wire 往返）、`TestWorkspaceTopic`（3：7 snake_case wire 值 + 7 变体计数 + 裸字符串 JSON）、`TestWorkspaceTopicSet`（10：empty/all 覆盖全 topic + with_topic 位掩码构建 + contains + 透明 wire 裸 int 非 dict + from_wire 往返 + 拒绝 bool + 拒绝非 int + eq/hash + u32 钳制负数/溢出）、`TestWorkspaceEvent`（15：每变体 wire 形状含 fs_changed `_payload` 委托 + git_head 分支/detached + git_lock `_EPOCH` Z 后缀 + skills/plugins/hooks 嵌套 WireModel 列表 + mcp/lsp 别名 ServerStatus + codebase u64 + 2 unit 变体 None + tools_changed + **12 变体穷举 from_wire 往返** + **topic 映射穷举**）。

### 映射决策树 + 坑

```
events.rs（grok events/ 子目录 3 文件）
├─ mod.rs（EventBus 设计意图：只携带 WorkspaceEvent，无 SessionEvent）
├─ workspace.rs
│  ├─ WorkspaceEvent 12 变体（相邻标记）
│  │  └─ WorkspaceEvent(AdjacentTagged) 12 _VARIANTS
│  │     ├─ struct 变体（fs_changed/git_head_changed/skills_changed/...）
│  │     │  └─ 手建 dict + _payload 委托（StrEnum 字段直通 / WireModel 列表元素委托）
│  │     ├─ DateTime<Utc> 字段（git_lock_held.until）
│  │     │  └─ _dt_to_wire(until) RFC3339 Z 后缀（R78 导入，第 3 使用者）
│  │     ├─ u64 字段（codebase_index_updated.files_indexed）-> int() 防御强制
│  │     ├─ unit 变体（project_config_changed/permission_policy_changed）-> cls(kind, None)
│  │     └─ topic() -> WorkspaceTopic（match 语句分类，每变体→1 topic）
│  ├─ WorkspaceTopic 7 变体（普通 rename_all snake_case，非相邻标记）
│  │  └─ WorkspaceTopic(StrEnum) 裸字符串 "fs"/"vcs"/...（JSON 安全）
│  └─ WorkspaceTopicSet（transparent over u32，新模式）
│     └─ 直接 to_wire()->int / from_wire(data:int)
│        ├─ bits 位掩码（_TOPIC_INDEX 稳定 bit 0-6）
│        ├─ empty()/all()/with_topic()/contains()/is_empty()
│        ├─ u32 钳制 int(bits) & 0xFFFFFFFF
│        └─ from_wire 拒绝 bool（int 子类）+ 非 int
└─ lag.rs EventLag（单变体 lagged(u64)，相邻标记）
   └─ EventLag(AdjacentTagged) _VARIANTS=("lagged",)
      ├─ lagged(n) int(n) u64 强制
      └─ __str__ "lagged by {n} events"（镜像 thiserror Display）
```

**坑 1 —— 透明非字符串 newtype 是新 serde 模式（WorkspaceTopicSet）**

`WorkspaceTopicSet` 是 `#[serde(transparent)]` over `bits: u32`，序列化为裸整数（无字段名包装）。这与 R67 的透明**字符串** newtype（`SessionId`，经 pydantic core schema 序列化为裸 str）形态不同：(1) 它是非字符串（u32→int）；(2) 它携带方法（empty/all/with_topic/contains），不是纯 newtype；(3) 它从不是 `WireModel` 字段（wire 形状是裸 int，非排序 dict），故不能走 WireModel/SessionId 的 core-schema 路径。决策：直接在类上实现 `to_wire() -> int` / `from_wire(cls, data: int)`，不继承 WireModel，不用 `__get_pydantic_core_schema__`。`from_wire` 显式拒绝 bool（`isinstance(data, bool)`）——因为 bool 是 int 子类，Rust u32 反序列化器拒绝 bool，Python 侧必须显式排除。u32 范围在 `__init__` 用 `int(bits) & 0xFFFFFFFF` 钳制（负数 → 补码，>u32 → 截断），镜像 Rust u32 wrap 语义。这补全了透明 newtype 的两种形态，为后续透明数值 newtype 立先例。

**坑 2 —— `with` 关键字冲突（Rust `with` → Python `with_topic`）**

grok `WorkspaceTopicSet::with(mut self, topic) -> Self` builder 方法名 `with` 是 Python 关键字（`with` 语句），不能作为方法名（语法错误）。重命名为 `with_topic`，docstring 显式记录重命名理由。语义保留：grok `mut self`（move + 原位修改）→ Python 原位 `self.bits |= ...` 返回 `self`（与 R78 `with_metadata`/`with_deadline` 同一模式）。

**坑 3 —— `WorkspaceTopic` 是普通枚举非相邻标记（serde 形态辨识）**

grok `WorkspaceTopic` 用 `#[serde(rename_all = "snake_case")]` **但无** `#[serde(tag, content)]`——它是**外部标记/普通枚举**，序列化为裸 `"fs"` 字符串，而非相邻标记的 `{"type":"fs","data":...}`。辨识关键：相邻标记需要 `tag`+`content` 两个属性；只有 `rename_all` 是普通枚举的 snake_case 值重命名。Python 侧用 `enum.StrEnum`（成员即 str 子类，JSON 安全，`==` 比较与裸字符串一致）承接，不走 AdjacentTagged。这个辨识是 R80 的核心 serde 判断——若误判为相邻标记会生成错误的 wire 形状。

**坑 4 —— `_dt_to_wire` 第 3 使用者（rpc/hunks, request, events）**

`GitLockHeld.until: DateTime<Utc>` 需 RFC3339 Z 后缀序列化。`_dt_to_wire` 已存在两份（`rpc/hunks.py` R76 + `request.py` R78），events 是第 3 使用者。决策：从 `request.py` 导入（`from minimax_code.workspace_types.request import _dt_to_wire`）而非第 3 次复制——3 个使用者的去重收益已超过"模块自包含"的偏好。提升到 `_wire.py` 仍延迟（YAGNI，待第 4 使用者或专门轮）。`_payload` 辅助则相反——复制（3 行）不导入，保持与 R78 `_dt_to_wire` 复制先例的模块自包含一致性；两者的不同选择反映了"辅助越短越倾向复制，跨模块依赖越重越倾向导入"的实用判断。

### 验证

- **ruff**：3 文件全 clean（`events.py` / `__init__.py` / `test_workspace_types.py`）。`All checks passed!`
- **pytest `tests/test_workspace_types.py`**：**121 passed**（R80 新增 4 类 32 测试 + 既有 89）。覆盖 12 事件变体穷举 + topic 映射穷举 + WorkspaceTopicSet 透明 wire/bool 拒绝/u32 钳制 + EventLag thiserror Display。
- **全回归**：`2427 passed, 10 skipped, 1 warning`（1 warning 是 fastapi starlette TestClient 弃用，与 R80 无关）。R79 是 2395，R80 +32 测试 → 2427，**零回归**。

### YAGNI 边界

- **未迁 `chunks`**：本轮只迁 `events/`（crate 顶层调度层 4 模块的第 3 个）。`chunks/`（mod/ops/session/tool 4 文件，29 变体 ChunkKind 的 RPC 响应分块）留给 R81——4 模块的最后一个。
- **不臆造 SessionEvent**：grok `events/mod.rs` 明确 EventBus 只携带 WorkspaceEvent；sampler 状态经 chunk 流（R81）。Python 侧如实记录，不为对称性新增 `SessionEvent`。
- **`_dt_to_wire` 不提升到 `_wire`**：3 使用者（rpc/hunks, request, events）已去重到 request.py 导入，但提升到公共 `_wire.py` 仍延迟——需专门的迁移轮处理 3 处导入点更新 + 测试，避免本轮范围蔓延（YAGNI，待第 4 使用者触发）。
- **`_payload` 复制不导入**：3 行辅助，复制保持 events.py 模块自包含（与 R78 复制 `_dt_to_wire` 先例一致）。`_dt_to_wire` 选导入是因为它有跨模块稳定契约（datetime→RFC3339），`_payload` 是内部归一化原语无外部契约。
- **WorkspaceTopicSet 不实现 `__or__`/`__and__`/`__iter__`**：grok 只暴露 empty/all/with/contains/is_empty 5 个方法。Python 集合协议（`|`/`&`/迭代）是过度设计——订阅位掩码的典型用法是 `Set.empty().with_topic(t)` 构建 + `contains(t)` 查询，不需要集合代数。YAGNI。
- **不实现实际 EventBus 订阅运行时**：`events.py` 只迁 wire 契约（4 类型的序列化形状 + topic 分类），不实现事件总线/订阅/背压逻辑（runtime transport 层关注点，留给后续消费轮）。

### Commit

`feat(platform): R80 events.rs -> events.py（订阅事件流 crate 顶层调度层第 3 模块 3 种 serde 模式落地透明 u32 位掩码 newtype）[新增 events.py 428 行: _payload(obj) 3 行辅助复制自 R79 + WorkspaceTopic(StrEnum) 7 变体 fs|vcs|discovery|servers|index|config|tools 普通非相邻标记 + _TOPIC_INDEX 稳定 bit 0-6 + _ALL_TOPIC_BITS 127 + WorkspaceTopicSet 透明 u32 位掩码 newtype(新模式 to_wire()->int 裸整数 from_wire 拒绝 bool|非 int u32 钳制 int&0xFFFFFFFF empty|all|with_topic|contains|is_empty __slots__ eq|hash|repr) + WorkspaceEvent(AdjacentTagged) 12 变体(fs_changed|git_head_changed|git_lock_held|skills_changed|plugins_changed|hooks_changed|mcp_server_state_changed|lsp_server_state_changed|codebase_index_updated|project_config_changed|permission_policy_changed|tools_changed) 12 工厂 + topic() match 分类 + EventLag(AdjacentTagged) 单变体 lagged(u64) __str__ thiserror Display; _dt_to_wire 从 request.py 导入第 3 使用者; barrel 导出 4 符号 + docstring R80 段; 4 测试类 32 测试(12 事件变体穷举 + topic 映射穷举 + WorkspaceTopicSet 透明 wire bool 拒绝 u32 钳制 + EventLag Display); KEY 决策 1: 3 种 serde 模式同台(相邻标记复用 + StrEnum 复用 + 透明非字符串 newtype 新模式); KEY 决策 2: 无 SessionEvent 设计忠实还原 EventBus 只携带 WorkspaceEvent; KEY 坑 1: 透明非字符串 newtype 是新 serde 形态直接 to_wire|from_wire 不继承 WireModel bool 显式排除; KEY 坑 2: with 关键字冲突 -> with_topic 重命名; KEY 坑 3: WorkspaceTopic 普通枚举非相邻标记 rename_all 无 tag|content; KEY 坑 4: _dt_to_wire 第 3 使用者选导入 _payload 选复制; 验证 ruff 3 文件 clean + pytest test_workspace_types.py 121 passed + 全回归 2427 passed 10 skipped 1 warning 零回归, 锚点 R80-1 20d8bbf]`


## R81 — chunks.rs → chunks.py（响应分块 crate 顶层调度层第 4 模块收官 4 相邻标记枚举 3 种变体形态 + chunk_kind 重命名 + SessionAck 歧义陷阱）

锚点:R81-1 9f055c8

### 本轮目标

迁移 grok-build `xai-grok-workspace-types::chunks/`（mod/ops/session/tool 4 文件）→ 单文件 `chunks.py`，落地 crate 顶层调度层 4 模块的第 4 个（最后一个）：`request`（R78）→ `requests`（R79）→ `events`（R80）→ `chunks`（R81）。这是 workspace-types 顶层 wire 契约的收官轮——前 3 轮已落地请求信封、请求鉴别器、订阅事件流；R81 落地**响应分块**：每个 workspace RPC 返回的响应流（工具输出/进度/终态、ops 查询结果、session 生命周期结果、以及 Need* 双向握手请求 + ToolResponse 回复）。本轮后，crate 的 4 个顶层调度模块全部闭合，grok-build workspace-types crate 的核心 wire 层迁移视为完工，可转向其他 crate。

### 融合结论

**高度进化：4 相邻标记枚举同台 + 3 种变体形态 + 双向握手契约完整还原。** grok 的 `chunks/` 子目录 4 文件在 Python 侧塌缩为单文件 4 类，全部复用 R67 `AdjacentTagged` 基类（与 R79 4 请求枚举、R80 事件联合同构）。本轮的工程密度体现在：(1) **3 种变体形态**（newtype/struct/unit）混编于同一枚举，`_payload` 辅助统一 newtype 委托；(2) **`chunk_kind()` 重命名**——grok 给每个 chunk 枚举 `kind() -> ChunkKind` 方法，但 `AdjacentTagged.kind` 已是属性（wire tag 字符串如 "ack"），命名冲突 → 重命名 + `_KIND_MAP` ClassVar 字典显式映射；(3) **SessionAck 歧义陷阱**——两个 chunk 枚举（OpsChunk/SessionChunk）都有 `Ack` arm 但 wire tag 都是 "ack"，映射到**不同** ChunkKind 变体（`Ack`="ack" vs `SessionAck`="session_ack"），不能用 `ChunkKind(self.kind)` 反查，必须 `_KIND_MAP` 显式穷举；(4) **双向握手**——ToolChunk 的 3 个 Need* 变体（need_permission/need_user_answer/need_plan_mode_change）是 struct variant 带 `req_id` correlation id，ToolResponse 的 3 个回复变体 echo req_id 闭合握手，ToolResponse 作为响应方向**不参与** ProtocolMismatch 错误 → 无 `chunk_kind()`/`_KIND_MAP`。可通用性：这 4 个枚举是 workspace RPC 响应的唯一 wire 表面，任何接入 grok 协议的客户端/服务端都消费它们；3 种变体形态 + `_payload` 模式是 R79 已建立的相邻标记配方在第 4 类场景的再次验证，配方稳定性得到证明。

### 交付

- **新增 `agent/minimax_code/workspace_types/chunks.py`（526 行）**：
  - imports：`from __future__ import annotations`、`from typing import Any, ClassVar`、`AdjacentTagged` from `_tagged`、`ChunkKind` from `chunk_kind`、`SessionId` from `identity`、26 types from `types`
  - `__all__ = ["OpsChunk", "SessionChunk", "ToolChunk", "ToolResponse"]`
  - `_payload(obj: Any) -> Any` 3 行辅助（复制自 R79/R80）：`if hasattr(obj, "to_wire"): return obj.to_wire(); return obj`
  - **`OpsChunk(AdjacentTagged)`** 17 变体：`_VARIANTS` 17 元组（git_status, git_diff, git_branch_info, git_metadata, hunks, skills, plugins, project_config, permissions, envrc, resolved_files, memory_chunks, plugin, ack, fuzzy_match, ripgrep_hit, ripgrep_done），`_KIND_MAP` 17 条目（ack→ChunkKind.Ack），17 工厂（newtype 委托 / struct 单例 / Vec list / unit None / Option None / BTreeMap 排序），`chunk_kind()` 方法
  - **`SessionChunk(AdjacentTagged)`** 5 变体：`_VARIANTS`（session_id, session_info, rewind_result, rewind_points, ack），`_KIND_MAP` 5 条目（ack→ChunkKind.SessionAck 带 NOTE 注释），5 工厂，`chunk_kind()`
  - **`ToolChunk(AdjacentTagged)`** 7 变体：`_VARIANTS`（output, progress, final, definitions, need_permission, need_user_answer, need_plan_mode_change），`_KIND_MAP` 7 条目，7 工厂（4 newtype + 3 struct Need*），`chunk_kind()`
  - **`ToolResponse(AdjacentTagged)`** 3 变体：`_VARIANTS`（permission, user_answer, plan_mode_change），3 struct-variant 工厂（均带 req_id + typed payload），**无 `_KIND_MAP`/`chunk_kind`**
- **更新 barrel `__init__.py`**：docstring 加 R81 段（chunk_kind 重命名说明）、import 4 符号、`__all__` 加 R81 块
- **新增 R81 测试（test_workspace_types.py，4 类 31 测试方法）**：
  - `TestOpsChunk`：vcs/git_metadata Option/Vec/singleton+unit/envrc BTreeMap 排序/streaming newtype 等形状 + `_KIND_MAP` 穷举性 + `chunk_kind()` 映射 + 17 变体 round-trip
  - `TestSessionChunk`：5 变体形状 + `_KIND_MAP` 穷举性 + **SessionAck 陷阱**（ack→SessionAck 非 Ack，ChunkKind.Ack != SessionAck）+ 5 变体 round-trip
  - `TestToolChunk`：7 变体形状（4 newtype + 3 Need* struct 带 req_id）+ `_KIND_MAP` 穷举性 + 7 变体 round-trip
  - `TestToolResponse`：3 struct 变体（echo req_id）+ **无 chunk_kind 验证**（`hasattr(resp, 'chunk_kind') is False` + `not hasattr(ToolResponse, '_KIND_MAP')`）+ 3 变体 round-trip
  - imports 块 4 处字母序精确插入：OpsChunk/SessionChunk/ToolChunk/ToolResponse

### 映射决策树+坑

```
grok xai-grok-workspace-types::chunks/
├─ ops.rs OpsChunk（17 变体，VCS/hunks/search/discovery/config/memory/marketplace 响应分块）
│  └─ OpsChunk(AdjacentTagged) _VARIANTS=17
│     ├─ newtype 委托: git_status(GitStatus)/git_diff(GitDiff)/git_branch_info(GitBranchInfo)/hunks(Vec<Hunk>)/skills(Vec<SkillInfo>)/plugins(Vec<PluginInfo>)/plugin(PluginInfo)/project_config(ProjectConfig)/permissions(PermissionPolicy)/resolved_files(Vec<ResolvedFile>)/memory_chunks(Vec<MemoryChunk>)/fuzzy_match(FuzzyMatch)/ripgrep_hit(ContentMatch)/ripgrep_done(RipgrepStats)
│     ├─ Option<T>: git_metadata(GitMetadata | None) _payload(None)->None data slot null
│     ├─ BTreeMap: envrc(dict[str,str]) {k: str(env[k]) for k in sorted(env)} 键排序
│     ├─ unit: ack() cls("ack", None)
│     ├─ _KIND_MAP 17 条目 ack->ChunkKind.Ack
│     └─ chunk_kind() -> ChunkKind = self._KIND_MAP[self.kind]
├─ session.rs SessionChunk（5 变体）
│  └─ SessionChunk(AdjacentTagged) _VARIANTS=5
│     ├─ newtype: session_id(SessionId)/session_info(AgentSessionInfo)/rewind_result(RewindResult)/rewind_points(Vec<RewindPoint>)
│     ├─ unit: ack() cls("ack", None)
│     ├─ _KIND_MAP 5 条目 ack->ChunkKind.SessionAck 【陷阱】
│     └─ chunk_kind()
├─ tool.rs ToolChunk（7 变体，工具输出/进度/终态 + Need* 握手请求）
│  └─ ToolChunk(AdjacentTagged) _VARIANTS=7
│     ├─ newtype: output(ToolOutputChunk)/progress(ToolProgress)/final(ToolCallResult)/definitions(Vec<ToolDef>)
│     ├─ struct Need*: need_permission(req_id, PermissionRequest)/need_user_answer(req_id, Vec<UserQuestion>)/need_plan_mode_change(req_id, PlanModeTransition) {req_id, <field>}
│     ├─ _KIND_MAP 7 条目
│     └─ chunk_kind()
└─ tool.rs ToolResponse（3 变体，Need* 的回复方向 echo req_id）
   └─ ToolResponse(AdjacentTagged) _VARIANTS=3
      ├─ struct: permission(req_id, PermissionDecision)/user_answer(req_id, Vec<UserAnswer>)/plan_mode_change(req_id, PlanModeDecision) {req_id, decision|answers}
      └─ 【无 _KIND_MAP 无 chunk_kind】响应方向不参与 ProtocolMismatch
```

**坑 1 —— `chunk_kind()` 重命名（grok `kind()` vs AdjacentTagged.kind 属性冲突）**

grok 给每个 chunk 枚举一个 `kind() -> ChunkKind` 方法返回类型化鉴别器。但 R67 的 `AdjacentTagged` 基类已定义 `.kind` 为**属性**（存 wire tag 字符串如 "ack"/"git_status"）。方法名 `kind` 与属性 `kind` 冲突——Python 中方法会遮蔽属性，且语义完全不同（属性是 wire tag str，方法是 ChunkKind 枚举）。决策：重命名为 `chunk_kind()`，用 `_KIND_MAP: ClassVar[dict[str, ChunkKind]]` 字典做 tag→ChunkKind 映射，`def chunk_kind(self) -> ChunkKind: return self._KIND_MAP[self.kind]`。这避免了 `kind` 双关（属性 vs 方法），且 `_KIND_MAP` 是显式映射表而非 `ChunkKind(self.kind)` 反查（见坑 2）。barrel docstring 显式记录重命名理由。

**坑 2 —— SessionAck 歧义陷阱（两 chunk 枚举 Ack arm 映射不同 ChunkKind）**

OpsChunk 和 SessionChunk 都有 `Ack` 变体，wire tag 都是 `"ack"`，但映射到**不同** ChunkKind 成员：`OpsChunk.ack()` → `ChunkKind.Ack`（value `"ack"`），`SessionChunk.ack()` → `ChunkKind.SessionAck`（value `"session_ack"`）。若用 `ChunkKind(self.kind)` 反查（即 `ChunkKind("ack")`），两个枚举的 ack 都会返回 `ChunkKind.Ack`——SessionChunk 侧错误。ChunkKind 是 StrEnum，`ChunkKind("ack")` 只能匹配 value="ack" 的成员。决策：每个 chunk 枚举维护**显式** `_KIND_MAP` 字典，穷举每个 tag 对应的 ChunkKind 成员，SessionChunk 的 `"ack"` 显式映射到 `ChunkKind.SessionAck`（带 NOTE 注释）。测试 `test_ack_maps_to_session_ack_not_ack` 专门覆盖此陷阱：`SessionChunk.ack().chunk_kind() is ChunkKind.SessionAck` 且 `ChunkKind.Ack != ChunkKind.SessionAck` 且 `ChunkKind.Ack.value == "ack"` / `SessionAck.value == "session_ack"`。这是本轮最隐蔽的 bug 源——若不显式映射，SessionChunk.Ack 的 chunk_kind 会静默返回错误的 OpsAck 变体。

**坑 3 —— ToolResponse 无 chunk_kind（响应方向不参与 ProtocolMismatch）**

grok 不给 `ToolResponse` 实现 `kind() -> ChunkKind`——它是响应方向（ToolChunk Need* 请求的回复），不是会与流契约不匹配的 chunk，因此不参与 `ProtocolMismatch` 错误（该错误用 ChunkKind 鉴别不期望的 chunk 类型）。决策：ToolResponse 只有 3 个 struct-variant 工厂（permission/user_answer/plan_mode_change，均 echo req_id），**无** `_KIND_MAP`、**无** `chunk_kind()` 方法。测试 `test_no_chunk_kind_accessor` 验证：`not hasattr(ToolResponse.permission(...), 'chunk_kind')` 且 `not hasattr(ToolResponse, '_KIND_MAP')`。这忠实还原了 grok 的非对称设计——3 个 chunk 枚举有 kind，1 个 response 枚举没有 kind，反映请求/响应方向在协议错误处理中的不同角色。

**坑 4 —— 3 种变体形态混编 + `_payload` 统一委托**

同一个枚举内混合 3 种 serde 变体形态：(1) **newtype** `cls("tag", _payload(arg))`——单值包装，`_payload` 委托 WireModel/AdjacentTagged 的 `to_wire`，原始 str/list/int/透明 newtype 直通；(2) **struct** `cls("tag", {"req_id": str(req_id), "field": _payload(val)})`——手建内层 dict（grok 内联字段，无具名 struct 类型可委托）；(3) **unit** `cls("tag", None)`——data slot 为 null。`_payload(obj)` 统一 newtype/struct 嵌套值的委托逻辑（3 行：`if hasattr(obj, "to_wire"): return obj.to_wire(); return obj`），是 R79/R80 已建立配方的第 3 次复制（保持模块自包含，与 R78 `_dt_to_wire` 复制先例一致）。Vec<T> newtype：`[_payload(x) for x in items]` 逐元素委托。Option<T>：`_payload(None)` → None → data slot 为 JSON null。Envrc BTreeMap：`{k: str(env[k]) for k in sorted(env)}` 键排序保证 wire 字节确定性（与 Metadata 同理）。3 种形态 + 4 种容器（单值/Vec/Option/BTreeMap）的组合是本轮变体形态密度的来源，但全部由 `_payload` + 手建 dict 两种机制覆盖，无新 serde 模式。

### 验证

- **ruff**：3 文件全 clean（`chunks.py` / `__init__.py` / `test_workspace_types.py`）。`All checks passed!`
- **pytest `tests/test_workspace_types.py`**：**152 passed**（R81 新增 4 类 31 测试 + 既有 121）。覆盖：OpsChunk 17 变体穷举（newtype/Option None/Vec/struct/unit/envrc 排序/streaming）+ `_KIND_MAP` 穷举性 + `chunk_kind()` 映射；SessionChunk 5 变体 + **SessionAck 陷阱**断言；ToolChunk 7 变体（4 newtype + 3 Need* struct req_id）+ 穷举；ToolResponse 3 struct 变体 + **无 chunk_kind 验证**；4 枚举全变体 round-trip via `from_wire`。
- **全回归**：`2458 passed, 10 skipped, 1 warning`（R80 是 2427，本轮 +31 测试 → 2458，**零回归**）。1 warning 是 fastapi starlette TestClient 弃用，与 R81 无关。

### YAGNI 边界

- **4 顶层模块收官，workspace-types crate 核心 wire 层完工**：R78-R81 落地 crate 全部 4 个顶层调度模块（request/requests/events/chunks）。grok workspace-types 的 leaf types（R67）+ rpc/（R68-R77）+ 顶层调度（R78-R81）三部分已全部迁移。后续可转向 grok-build 其他 crate（如 workspace 运行时 transport、secrets、fsnotify 等的非 wire 部分）。
- **`_payload` 不提升到 `_wire`**：3 行辅助，第 4 次复制（R79 requests + R80 events + R81 chunks + R78 request 的 `_dt_to_wire` 是另一条线）。保持模块自包含，与既有先例一致。提升到公共 `_wire.py` 需专门的迁移轮处理多模块导入点 + 测试，避免本轮范围蔓延（YAGNI，待统一清理轮）。
- **ToolResponse 不加 chunk_kind**：忠实还原 grok 非对称设计。即使为"一致性"给 ToolResponse 加 `_KIND_MAP` 也是过度设计——它从不在 ProtocolMismatch 错误路径中被查询，加它只会引入死代码 + 维护负担（3 变体映射到哪个 ChunkKind？grok 根本没定义这层映射）。
- **`_KIND_MAP` 显式穷举不反查**：即使 SessionChunk 只在 ack 上有歧义，仍对全部 chunk 枚举用显式 `_KIND_MAP` 字典而非混合策略（部分反查 + 歧义手写）——一致性 > 微优化，且显式表可审计（一眼看出每个 tag → ChunkKind 映射）。
- **不实现实际 chunk 流运行时**：`chunks.py` 只迁 wire 契约（4 枚举的序列化形状 + chunk_kind 分类），不实现 chunk 流的发送/接收/背压/ProtocolMismatch 错误逻辑（runtime transport 层关注点，留给后续消费轮）。Need* 握手的 req_id correlation 也只记录契约，不实现匹配逻辑。

### Commit

`feat(platform): R81 chunks.rs -> chunks.py（响应分块 crate 顶层调度层第 4 模块收官 4 相邻标记枚举 3 变体形态 chunk_kind 重命名 SessionAck 陷阱）[新增 chunks.py 526 行: _payload(obj) 3 行辅助复制自 R79/R80 + OpsChunk(AdjacentTagged) 17 变体(git_status|git_diff|git_branch_info|git_metadata|git_metadata(None)|hunks|skills|plugins|project_config|permissions|envrc|resolved_files|memory_chunks|plugin|ack|fuzzy_match|ripgrep_hit|ripgrep_done) 17 工厂(newtype 委托 + Option None + Vec list + struct 单例 + BTreeMap envrc 键排序 + unit ack None) + _KIND_MAP 17 条目 + chunk_kind() + SessionChunk(AdjacentTagged) 5 变体(session_id|session_info|rewind_result|rewind_points|ack) 5 工厂 + _KIND_MAP 5 条目(ack->SessionAck 陷阱) + chunk_kind() + ToolChunk(AdjacentTagged) 7 变体(output|progress|final|definitions 4 newtype + need_permission|need_user_answer|need_plan_mode_change 3 struct Need* req_id 握手) 7 工厂 + _KIND_MAP 7 条目 + chunk_kind() + ToolResponse(AdjacentTagged) 3 变体(permission|user_answer|plan_mode_change struct echo req_id 无 _KIND_MAP 无 chunk_kind); barrel 导出 4 符号 + docstring R81 段(chunk_kind 重命名说明); 4 测试类 31 测试(OpsChunk 17 变体穷举 + SessionChunk SessionAck 陷阱断言 + ToolChunk 7 变体含 Need* req_id + ToolResponse 无 chunk_kind 验证 + 4 枚举全变体 from_wire round-trip); KEY 决策 1: 4 相邻标记枚举同台复用 R67 AdjacentTagged 与 R79/R80 同构零新 serde 模式; KEY 决策 2: 3 种变体形态(newtype|struct|unit)混编 _payload 统一委托; KEY 坑 1: chunk_kind 重命名 grok kind() 方法 vs AdjacentTagged.kind 属性冲突 -> _KIND_MAP 字典; KEY 坑 2: SessionAck 歧义两 chunk 枚举 Ack arm wire tag 都 ack 映射不同 ChunkKind(Ack=ack vs SessionAck=session_ack) 不能 ChunkKind(self.kind) 反查必须显式穷举; KEY 坑 3: ToolResponse 无 chunk_kind 响应方向不参与 ProtocolMismatch; KEY 坑 4: 3 变体形态 + 4 容器(单值|Vec|Option|BTreeMap)由 _payload + 手建 dict 两种机制覆盖; 验证 ruff 3 文件 clean + pytest test_workspace_types.py 152 passed + 全回归 2458 passed 10 skipped 1 warning 零回归, 锚点 R81-1 9f055c8]`


## R82 — xai-tool-protocol 地基层（ids + handshake + error_codes + connection，开启 tool-protocol crate 4/16 模块）

锚点:R82-1 666e03b

### 本轮目标

开启 `xai-tool-protocol` crate（grok computer-hub 工具服务器线路协议 DTO）迁移。该 crate 6613 行 / 16 模块，是**纯线路 DTO crate**（零 I/O、零 env 读），位于 `xai-tool-types`（R65，工具 schema）与 `xai-tool-runtime`（R23，并发模型）之间。R82 落地 4 个依赖无关的地基模块（ids 241 + handshake 52 + connection 38 + error_codes 300 ≈ 631 行），建立 `tool_protocol` 包骨架；12 个较大模块（envelope / methods / capabilities / registration / frames(1549) / session_event(404) / turn_hook(700) / error_wire / output_wire / notification_wire / hook / registry_error）推迟至 R83+。这是继 `xai-grok-workspace-types`（R67-R81，rpc/ 10 模块收官）之后第二个进入迁移的大型 wire crate。

### 融合结论

正向迁移 Rust→Python，零 rust 工具链。crate 是纯线路 DTO，迁移为 `minimax_code.tool_protocol` 包，依赖仅 `pydantic_core`（newtype schema 钩子）+ 标准库（dataclass / StrEnum / Sequence）。**不依赖** `workspace_types._wire.WireModel`（保持 crate 自包含、可独立测试）；结构体用 dataclass + 手写 `to_wire`/`from_wire` 以精确映射 serde `skip_serializing_if` 语义（`Option::is_none` / `Vec::is_empty`）。透明 String newtype 模式复用 R67 `identity.py`，但抽出 `_OpaqueId` 基类 + `_EXTRA_VALIDATOR` ClassVar 钩子消除 7 个 newtype 的重复（DRY）。

### 交付

- `ids.py`（~383 行）：`IdError` 层级（基类 `ValueError` + `EmptyIdError` / `InvalidFormatIdError(value)` / `ReservedPrefixIdError(value)`，复现 Rust 3 变体 `thiserror` enum）+ 验证原语 `_is_id_char` / `_is_valid_segment` / `_ensure_non_empty` + `_opaque_str_schema` pydantic 钩子 + `_OpaqueId(str)` 基类（`_EXTRA_VALIDATOR: ClassVar` 钩子 + `__new__` 三段验证：isinstance str → 非空 → 额外验证器）+ 7 个 String newtype（`SessionId`/`UserId`/`ConnectionId`/`RequestId`/`ToolCallId` 纯非空；`ServerId` 拒 `auto:` 前缀 + `synthesize_for_tool` 绕过；`ToolId` 段格式验证）+ `FrameSeq` u64 newtype（`__slots__=("_value",)` + `new`/`get`/`to_wire`/`from_wire`(bool 拒)/`__int__`/`__index__`/`__eq__`/`__hash__`/`__lt__`/`__le__`/`__repr__`/`__str__` + pydantic `int_schema` 钩子）。
- `connection.py`（~140 行）：`ConnectionKind` StrEnum（`Harness`/`ToolServer`，snake_case 裸串）+ `ToolDefinitionMode` **内部标记枚举**（`tag=mode`，`full` 单值 `{"mode":"full"}` / `concise` 携 `meta_search`+`meta_call`，`__slots__` + `full()`/`concise()` 工厂 + `to_wire`/`from_wire` + dunder）—— **crate 首个内部标记枚举**（workspace-types 层全用相邻标记）。
- `handshake.py`（~129 行）：`PROTOCOL_VERSION="1.0.0"` + `HelloMsg` dataclass（`protocol_version`/`kind`/`server_id?`/`description?`/`metadata=Any`；`to_wire` 跳 None；`from_wire`）+ `HelloAckMsg` dataclass（`connection_id`/`user_id`/`computer_hub_version`/`supported_protocol_versions`/`capabilities?=None`；`to_wire` 跳空 capabilities `Vec::is_empty`；`from_wire`）。
- `error_codes.py`（~211 行）：`ERROR_CODES` 28 条目元组（numeric↔string 双列唯一）+ `numeric_for`/`string_for` 线性扫描（未知→None）+ `WORKSPACE_UNAVAILABLE_SUBCODE`/`MESSAGE`/`JSONRPC_CODE` 三常量 + `WorkspaceGoneReason`/`WorkspaceGonePhase` StrEnum（`from_wire` 全捕获→`Unknown`，复现 `#[serde(other)]`）+ `WorkspaceUnavailableDetails` dataclass（`to_wire`/`from_wire`，reason/phase 容忍解析）。
- `__init__.py`（~100 行）：barrel 导出 27 符号 + docstring 记录 R82 地基层 + 推迟模块清单。
- `test_tool_protocol.py`（~560 行）：11 测试类 123 测试（`TestOpaqueIds` 5 newtype 参数化穷举 + `TestToolId` 段格式正反例 + `TestServerId` 保留前缀+绕过 + `TestFrameSeq` u64+bool 拒+排序 + `TestConnectionKind`/`TestToolDefinitionMode` 内部标记往返 + `TestHandshake` serde skip + `TestErrorCodesTable` 28 条目双射 + `TestWorkspaceUnavailableContract` `#[serde(other)]` 容忍 + `TestPackageSurface` barrel）。

### 映射决策树 + 坑

**KEY 决策 1 — 透明 String newtype 抽基类（DRY）：** R67 `identity.py` 每个 newtype 各写一份 `__new__`+`__get_pydantic_core_schema__`。R82 抽出 `_OpaqueId(str)` 基类 + `_opaque_str_schema` 辅助 + `_EXTRA_VALIDATOR: ClassVar[Callable[[str],None]|None]` 钩子：基类 `__new__` 跑三段（isinstance str → `_ensure_non_empty` → 可选 `_EXTRA_VALIDATOR`），7 个 newtype 只需声明 `_EXTRA_VALIDATOR`（ServerId/ToolId）或什么都不写（5 个纯非空）。`__get_pydantic_core_schema__` 在基类一次性定义。

**KEY 决策 2 — FrameSeq 首个透明非串 newtype（u64）：** crate 第一个透明-*非字符串* newtype。独立类（非 str 子类），`__slots__=("_value",)`，独立 `to_wire`/`from_wire` + pydantic `int_schema` 钩子。**bool 拒绝**（bool 是 int 子类，会拓宽 u64 线路类型）：构造器 `__init__` 抛 `TypeError`，`from_wire` 抛 `ValueError`（与 Rust `u64` 反序列化器对齐）。

**KEY 决策 3 — 内部标记枚举内联实现（YAGNI）：** `ToolDefinitionMode`（`tag=mode`）是 crate 首个内部标记枚举（workspace-types 全用相邻标记 `AdjacentTagged`）。决定内联实现（plain class + `to_wire`/`from_wire`）而非抽共享基类——YAGNI，直到出现第二个内部标记枚举再抽象。`full` 单值变体 → `{"mode":"full"}`；`concise` struct 变体 → 携 `meta_search`/`meta_call`。

**KEY 决策 4 — `#[serde(other)]` 容忍枚举：** `WorkspaceGoneReason`/`WorkspaceGonePhase`。`Unknown` 是**命名变体**（值 `"unknown"`），所以能通过正常 `cls(value)` 查找往返；`from_wire` 用 `try/except ValueError` 全捕获未知串→`Unknown`，复现 Rust `#[serde(other)]` 吸收新 peer 未知值的向前兼容语义。

**KEY 决策 5 — dataclass + 手写 to_wire（serde skip 精确控制）：** 结构体选 dataclass 而非 pydantic BaseModel，保持 crate 自包含（不跨 crate 依赖 `WireModel`）+ 精确映射 serde `skip_serializing_if`：`HelloMsg` 的 `server_id`/`description`/`metadata` 跳 None（`Option::is_none`）；`HelloAckMsg.capabilities` 跳 None 与 `[]`（`Vec::is_empty`）。

**KEY 坑 1 — `_is_id_char` 运算符优先级：** `c.isascii() and c.isalnum() or c == "_" or c == "-"`。Python 中 `and` 优先级高于 `or`，等价 `(isascii and isalnum) or _ or -`。**必须限 ASCII**（否则 Unicode 数字/字母会拓宽段字母表，普通 `str.isalnum` 不会限）。

**KEY 坑 2 — `str.split(":", 2)` 复现 `splitn(3, ':')`：** 最多 3 段，第 3 段（第 2 个冒号）→ `False`。`len==1` 单段、`len==2` 双段（两段都需 `_is_valid_segment`）、`len==3` 拒绝。`:name`（空首段）/`ns:`（空尾段）→ 第二段 `_is_valid_segment("")` False → `InvalidFormatIdError`。

**KEY 坑 3 — `ServerId.synthesize_for_tool` 绕过验证：** 用 `str.__new__(cls, f"auto:tool:{tool_id}")` 跳过 `_OpaqueId.__new__`（跳过 `auto:` 前缀检查），复现 Rust 私有构造器。`connection_id` 在签名中（调用者不能省略连接作用域）但当前编码不混入——两连接注册同 `tool_id` 共享合成 id 但在注册表 `(connection_id, tool_id)` 主键表里保持独立。

**KEY 坑 4 — `error_codes.py` 的 `Any` 导入 E402 陷阱：** 初始版本在 `from_wire(cls, data: dict[str, Any])` 用 `Any` 但顶部未导入（放文件末尾 `# noqa: E402` 临时方案，ruff E402 风险）。修复：改 `dict[str, object]`（`from __future__ import annotations` 使注解延迟求值，`object` 作运行时注解有效）+ `str()` 强制转换字段，删除 `Any` 导入。

**KEY 坑 5 — `FrameSeq.__eq__` 返回 `NotImplemented` 非 `False`：** 对非 FrameSeq 返回 `NotImplemented`（让 Python 反射机制尝试右操作数 `__eq__`，最终回退 `is`）。`__hash__` 显式定义（否则定义 `__eq__` 后 Python 3 默认设 `__hash__=None` 不可哈希）。`__lt__`/`__le__` 定义，`__gt__`/`__ge__` 靠反射（`a>b` → `b.__lt__(a)`）。

**KEY 坑 6 — 测试临时 `UTC` noqa F401 删除：** 初始 `test_tool_protocol.py` 有 `from datetime import UTC  # noqa: F401` 无用导入（"保持 tz-import idiom 统一"是误判，测试根本不用 UTC），违反项目代码规范，删除。

### 验证

`cd agent && uv run ruff check minimax_code/tool_protocol tests/test_tool_protocol.py` → **All checks passed!**（6 文件 clean）。`uv run pytest tests/test_tool_protocol.py -q` → **123 passed in 0.19s**。全量回归 `uv run pytest -q` → **2581 passed, 10 skipped, 1 warning in 102.25s**（2458 + 123 = 2581，零回归；唯一 warning 是 fastapi httpx 弃用提示，预先存在与 R82 无关）。锚点 R82-1 666e03b。

### YAGNI 边界

- **`ToolCallId.new_v7`（UUID v7 工厂）推迟** — Python stdlib `uuid` 无 v7 生成器，需 `uuid6` 依赖评估，后续回合处理。
- **`from_tool_error_wire` / `workspace_unavailable_wire` 推迟** — 依赖 `error_wire::ToolErrorWire`（R83+ 的 `error_wire` 模块）。本回合已落地它们组合的全部零件（表、查找、常量、两容忍枚举、`WorkspaceUnavailableDetails`），后续回合接线时无需回改契约。
- **12 个较大模块推迟** — envelope / methods / capabilities / registration / frames(1549) / session_event(404) / turn_hook(700) / error_wire / output_wire / notification_wire / hook / registry_error。R83+ 按 `lib.rs` `pub use` 依赖序逐模块迁移。
- **内部标记枚举共享基类推迟** — 仅 `ToolDefinitionMode` 一个内部标记枚举，YAGNI 直到出现第二个再抽象（workspace-types 的 `AdjacentTagged` 是相邻标记，不可复用）。
- **`ERROR_CODES` HashMap 优化推迟** — 28 条目线性扫描足够快（Rust 同理，rationale 一致），无需 dict 索引。
- **pydantic BaseModel for 结构体推迟** — dataclass + 手写 to_wire 已满足 serde skip 精确控制；若后续回合需要 pydantic 校验链路再升级。

### Commit

`feat(platform): R82 xai-tool-protocol 地基层（ids + handshake + error_codes + connection，开启 tool-protocol crate 4/16 模块，crate 首个内部标记枚举 + 首个透明非串 u64 newtype）[新增 tool_protocol/ 包 5 模块: ids.py(~383) IdError 层级(基类 ValueError+Empty/InvalidFormat(value)/ReservedPrefix(value)) + _is_id_char/_is_valid_segment/_ensure_non_empty + _opaque_str_schema + _OpaqueId(str) 基类(_EXTRA_VALIDATOR ClassVar 钩子+__new__ 三段验证) + 7 String newtype(5 纯非空 + ServerId 拒 auto: 前缀+synthesize_for_tool str.__new__ 绕过 + ToolId 段格式 split(':',2)) + FrameSeq u64 newtype(__slots__+bool 拒 TypeError 构造/ValueError from_wire+pydantic int_schema) | connection.py(~140) ConnectionKind StrEnum(harness/tool_server) + ToolDefinitionMode 内部标记枚举(crate 首个 tag=mode full 单值/concise 携 meta_search+meta_call) | handshake.py(~129) PROTOCOL_VERSION 1.0.0 + HelloMsg/HelloAckMsg dataclass(Option::is_none 跳 + Vec::is_empty 跳 capabilities) | error_codes.py(~211) ERROR_CODES 28 条目双列唯一 + numeric_for/string_for 线性扫描(未知 None) + WORKSPACE_UNAVAILABLE_SUBCODE/MESSAGE/JSONRPC_CODE 3 常量 + WorkspaceGoneReason/Phase StrEnum(from_wire try/except 全捕获 #[serde(other)] Unknown 命名变体能往返) + WorkspaceUnavailableDetails dataclass | __init__.py barrel 27 符号 + 推迟模块清单 docstring; test_tool_protocol.py 11 类 123 测试(TestOpaqueIds 5 newtype 参数化 + TestToolId 段格式正反例 + TestServerId 保留前缀+绕过 + TestFrameSeq u64 bool 拒+排序+NotImplemented + TestConnectionKind + TestToolDefinitionMode 内部标记往返 + TestHandshake serde skip + TestErrorCodesTable 28 双射 + TestWorkspaceUnavailableContract other 容忍 + TestPackageSurface barrel); KEY 决策 1: 透明 String newtype 抽 _OpaqueId 基类+_EXTRA_VALIDATOR 钩子消除 7 newtype 重复 DRY 复用 R67 identity 模式; KEY 决策 2: FrameSeq 首个透明非串 u64 newtype 独立类 bool 拒绝拓宽 u64; KEY 决策 3: 内部标记枚举内联非共享基类 YAGNI(workspace-types 全相邻标记); KEY 决策 4: serde(other) Unknown 命名变体能往返+from_wire 全捕获; KEY 决策 5: dataclass+手写 to_wire 精确 serde skip 保持 crate 自包含不依赖 WireModel; KEY 坑 1: _is_id_char 运算符优先级 and 高于 or 限 ASCII; KEY 坑 2: str.split(':',2) 复现 splitn(3) len 3 拒; KEY 坑 3: synthesize_for_tool str.__new__ 绕过验证复现 Rust 私有构造; KEY 坑 4: error_codes Any 导入 E402 坑改 dict[str,object]+str() 强转; KEY 坑 5: FrameSeq __eq__ NotImplemented 非 False+__hash__ 显式+__gt__ 反射; KEY 坑 6: 测试临时 UTC noqa F401 删除; 验证 ruff 6 文件 clean + pytest test_tool_protocol.py 123 passed 0.19s + 全回归 2581 passed 10 skipped 1 warning 102s 零回归, 锚点 R82-1 666e03b]`


## R83 — xai-tool-protocol wire 枚举层（error_wire + output_wire + notification_wire 三枚举落地，crate 第 2/3 个内部标记枚举 + 首个相邻标记 kind/value + 首个相邻标记 shape/value + 首个 forward-compat Custom 仿冒防护，R82 backfill 接线）
锚点:R83-1 b9ffd11

### 本轮目标

继续 R82 的 `xai-tool-protocol` crate 前向迁移。R82 落了依赖-free 地基层（ids + connection + handshake + error_codes 表与查找），但 `error_codes.py` 的两个组合 helper（`from_tool_error_wire` / `workspace_unavailable_wire`）依赖 `error_wire::ToolErrorWire` 而 R82 末推迟。R83 落地 crate 的 **wire 枚举层**——三个 serde 标记枚举（`ToolErrorWire` / `ToolOutputWire` / `WireToolNotification`）——并把 R82 推迟的两个 helper 接上线，闭合 crate 的错误侧契约。

本轮交付：4 个新模块（`error_wire` 15 变体内部标记 / `output_wire` 相邻标记+内部标记 McpBlock / `notification_wire` 相邻标记+forward-compat Custom）+ `error_codes.py` backfill 两 helper + barrel 扩展 + 7 测试类 26 测试函数。三枚举覆盖 crate 的 4 种 serde 形态中的 3 种（内部标记 tag-in-content × 2 + 相邻标记 tag-beside-content × 2 形态），仅 untagged 未触（grok crate 此层无 untagged 枚举）。

### 融合结论

`xai-tool-protocol` crate 的 wire 枚举层是 JSON-RPC `error.data` / 工具结果 / 通知流三方向稳定载荷的序列化形态。与 MiniMax Code 现有 IPC 层（`ipc/protocol.py` 的 pydantic 消息模型）不同，wire 枚举层强调 **跨对等端稳定**——序列化键名与标签必须与 Rust 端逐字节一致（5 处 serde rename override + Display 字符串逐字保真），而非 pydantic 的 Python 习惯命名。

融合落点：`tool_protocol/` 包内自包含的 dataclass + 手写 `to_wire`/`from_wire`，不依赖 `WireModel`（workspace-types crate 的相邻标记基类）也不引入 pydantic 校验链路，保持 crate 的纯 DTO 性质。forward-compat `Custom` 变体（错误侧 + 通知侧各一）为未来未知类型留逃生口，仿冒防护（`check_custom_kind`）阻止自定义 kind 阴影已知 PascalCase 变体——这是 crate 设计中唯一在 emit 时（而非注册时）跑校验的安全检查，迁移时必须忠实保留。

### 交付

- **`error_wire.py`（~433 行）** — `ToolErrorWire` 内部标记枚举（`#[serde(tag = "code", rename_all = "snake_case")]`），crate 第 2 个内部标记枚举（R82 `ToolDefinitionMode` 之后）。15 变体：`ToolNotFound` / `SessionMismatch` / `PermissionDenied`(→`forbidden`) / `TransportClosed`(→`connection_lost`) / `Timeout` / `Cancelled` / `InvalidArguments`(→`invalid_params`) / `Execution` / `UnsupportedProtocolVersion` / `PayloadTooLarge`(→`frame_too_large`) / `BehaviorVersionUnsupported` / `RenderLimited` / `TerminalError` / `Internal`(→`internal_error`) / `Custom`。`_ToolErrorWireBase` 基类持有 `to_wire`（反射 `dataclasses.fields` 跳 `None` 复现 `skip_serializing_if = "Option::is_none"`）+ `code` ClassVar；每变体独立 `__str__` 复现 `thiserror` `#[error("...")]` Display 字符串。`_VARIANTS` 注册表（`code` → 变体类，rename 自动落）+ `_coerce_field`（`ToolId`/`RequestId` 透明 str newtype 重包裹）+ `from_wire`（dispatch on `data["code"]`，absent Option 默认 None，未知 code raise ValueError）。
- **`output_wire.py`（~229 行）** — `ToolOutputWire` 相邻标记枚举（`#[serde(tag = "kind", content = "value", rename_all = "snake_case")]`），crate 首个相邻标记 kind/value 形态。3 变体：`Text`（裸 string payload）/ `Json`（opaque JSON）/ `Mcp`（嵌套 `{"blocks": [...]}`）。`McpBlock` 内部标记枚举（`#[serde(tag = "type")]`），crate 第 3 个内部标记枚举，3 变体：`TextBlock` / `ImageBlock` / `ResourceBlock`（`mime_type`/`text` Option 跳 None）。`from_wire` + `mcp_block_from_wire` 两 dispatch。
- **`notification_wire.py`（~213 行）** — `WireToolNotification` 相邻标记枚举（`#[serde(tag = "shape", content = "value")]`），crate 首个相邻标记 shape/value 形态。2 变体：`Known`（opaque JSON，判别在 value 内部）/ `Custom`（forward-compat 逃生口）。`WireCustomNotification` payload 结构（`kind` + `payload`，两者恒在）。`KnownVariantCollision` thiserror（`{kind:?}` Debug 格式 → Python `repr` 引号）。`KNOWN_NOTIFICATION_KINDS` 19 条目 PascalCase 元组 + `known_notification_kinds()` const fn 包装 + `check_custom_kind()` emit 时仿冒防护（命中已知变体 raise，未知返回 None）。
- **`error_codes.py`（R83 backfill）** — `from_tool_error_wire(err)` 基于 `err.code` 字符串 dispatch（`_VARIANT_CODE_TO_NUMERIC` dict，15→numeric，rename-aware 无每类型 import）+ `workspace_unavailable_wire(reason, phase)` 构造 `Custom`（subcode=`WORKSPACE_UNAVAILABLE_SUBCODE`，details.code 镜像 subcode，retryable=True）。
- **`__init__.py` barrel** — 导出全部 R83 符号（error_wire 15 变体 + output_wire 8 符号 + notification_wire 7 符号 + backfill 2 helper）；`from_wire`/`to_wire` **不导出**（镜像 Rust `lib.rs` `pub use`，调用方走子模块）。
- **`test_tool_protocol.py`（+~440 行，7 新测试类 26 测试函数）** — `TestToolErrorWire`（15 变体 to_wire 形态含 5 rename + 参数化 code-tag + 22 case 参数化 round-trip 含全 Option 组合 + from_wire 重包裹 ToolId/RequestId + absent Option 默认 None + 未知 code raise + 7 变体 Display 保真含 `behavior_version unsupported` 下划线/`internal error` 条件/`custom: my.x — hi` em-dash）/ `TestToolOutputWire`（Text/Json/Mcp to_wire + Mcp 嵌套 + round-trip + 未知 kind raise）/ `TestMcpBlock`（3 变体 to_wire + ResourceBlock None 跳 + round-trip + 未知 type raise）/ `TestWireToolNotification`（Known/Custom to_wire + WireCustomNotification round-trip + 未知 shape raise）/ `TestKnownNotificationKinds`（19 条目长度 + tuple 返回 + PascalCase + 参数化 check_custom_kind 拒 19 已知 raise KnownVariantCollision + 接受未知 + 碰撞消息 repr 引号）/ `TestErrorCodesBackfill`（参数化 from_tool_error_wire 15→numeric + workspace_unavailable_wire 6 reason × 4 phase Custom 形态 + message 常量 + round-trip）/ `TestPackageSurfaceR83`（barrel 导出 + from_wire 不导出）。

### 映射决策树 + 坑

**KEY 决策 1 — 15 变体内部标记枚举每变体独立 dataclass 子类：** `ToolDefinitionMode`（2 变体）用单引用类 + 命名工厂方法可行，但 15 变体太多不适合。每变体是 `_ToolErrorWireBase` 的 `@dataclass` 子类，`code` 作 ClassVar（5 处 rename override 显式赋值），`to_wire` 共享基类（反射 fields 跳 None），`__str__` 各自实现（Display 保真）。dispatch 在模块级 `from_wire` 经 `_VARIANTS` 注册表，注册表从每变体 `.code` ClassVar 构建，rename 自动落。

**KEY 决策 2 — `to_wire` 反射 + 跳 None 共享基类：** `_ToolErrorWireBase.to_wire` 用 `dataclasses.fields(self)` 遍历，`None` 跳过（复现 `skip_serializing_if = "Option::is_none"`）。透明 str newtype（`ToolId`/`RequestId`）裸 string 序列化无特殊处理，opaque JSON（`Any`）透传。这样新增变体只写 dataclass + `__str__`，不重复序列化逻辑（DRY）。

**KEY 决策 3 — Display 字符串每变体 `__str__` 而非共享 `message()` helper：** 4 变体（`InvalidArguments`/`Execution`/`TerminalError`/`Custom`）有 `message` **数据字段**，共享 `message()` 方法会与字段撞名。因此 Display 暴露走 `__str__`（thiserror Display 语义），字段名保留 `message` 以匹配 wire 键。

**KEY 决策 4 — 通知选相邻标记而非 untagged + `check_custom_kind` 仿冒防护：** `#[serde(untagged)]` 会让 `Custom` payload 静默匹配已知 PascalCase 变体（仿冒风险）。相邻标记（判别在 payload 旁）+ `check_custom_kind` 在 **emit 时**（非注册时）拒绝自定义 kind 阴影已知变体，raise `KnownVariantCollision`。这是 crate 唯一 emit 时校验，忠实保留。

**KEY 决策 5 — `from_tool_error_wire` 用 `err.code` 字符串 dispatch 而非 isinstance 链：** 基于 `err.code`（变体的 wire tag ClassVar）作 dict key（`_VARIANT_CODE_TO_NUMERIC`），复用 `error_wire` 的 rename-aware tags，无需每类型 import。15 变体一处 dict 完成全部映射。

**KEY 决策 6 — barrel `from_wire`/`to_wire` 不导出：** 镜像 Rust `lib.rs` `pub use` 集合——导出类型名 + `check_custom_kind`/`known_notification_kinds` helper，但不导出转换。调用方走子模块（`tool_protocol.error_wire.from_wire`）。`notification_wire.Custom` 不进 barrel（与 `error_wire.Custom` 撞名）。

**KEY 坑 1 — 5 变体 serde rename override：** wire `code` tag ≠ `snake_case(VariantName)` 的 5 处：`PermissionDenied`→`forbidden`、`TransportClosed`→`connection_lost`、`InvalidArguments`→`invalid_params`、`PayloadTooLarge`→`frame_too_large`、`Internal`→`internal_error`。每变体 `code` ClassVar 显式赋覆盖值，`_VARIANTS` 注册表从 ClassVar 构建自动正确。

**KEY 坑 2 — Display 字符串逐字保真 3 处：** `BehaviorVersionUnsupported` 用下划线 `behavior_version unsupported`（**非空格**）；`Internal` 条件后缀——`detail=None` → `"internal error"` 仅此，`detail=Some` → `"internal error: {detail}"`；`Custom` 用 em-dash `—`（U+2014）`custom: {subcode} — {message}`。逐字复现 `thiserror` `#[error("...")]`。

**KEY 坑 3 — `from __future__ import annotations` 使 `Field.type` 是字面注解字符串：** `_coerce_field` 收到的 `type_str` 是 `"ToolId"` / `"RequestId | None"`（注解延迟求值）。split `"|"` 取首段 strip 后查 `_ID_FIELD_TYPES`，非 id 类型透传（opaque JSON）。

**KEY 坑 4 — dataclass 字段顺序规则（非 default 在 default 前）：** `RenderLimited` Rust 顺序 `tool_id, card_id, reason`，但 `card_id` 是 Option（default None），Python dataclass 规则强制 `tool_id, reason, card_id=None`。wire 键顺序与 Rust 不同，但 JSON 对象键序无语义影响。

**KEY 坑 5 — 透明 str newtype dataclass `__init__` 不调 `ToolId(value)`：** dataclass 生成的 `__init__` 直接赋原始值不触发 newtype 构造，`from_wire` 必须 `_coerce_field` 显式重包裹 `ToolId(value)`/`RequestId(value)`，否则字段值是裸 str 非 newtype 实例（虽然 `isinstance` 仍 True 因 str 子类，但语义保真要求重包裹）。

**KEY 坑 6 — `error_wire.Custom` 与 `notification_wire.Custom` 同名：** 两模块各有一个 `Custom` 变体。barrel 只导 `error_wire.Custom`；测试导 notification 的 `Custom` 用 `NotificationCustom` 别名避撞。

**KEY 坑 7 — UP007 作用于运行时模块级别名赋值（非仅注解）：** `ToolErrorWire = Union[...]` 等 4 处模块级别名赋值被 UP007 标记，必须 `X | Y` 形式（运行时 `types.UnionType`，Python 3.10+，项目 3.11+ 合法）**且**删除 `typing` 中现在未用的 `Union` 导入。UP007 **非自动修复**，手动 Edit 4 文件 7 处。

**KEY 坑 8 — ruff isort `order-by-type` 拆子模块 import 块：** 测试文件子模块 import 块混 CONSTANT/Class/function 名（含别名）时，ruff 拆成 per-type `from X import (...)` 块。接受为标准行为，功能无影响。

### 验证

`cd agent && uv run ruff check minimax_code/tool_protocol tests/test_tool_protocol.py` → **All checks passed!**（6 文件 clean，4 处 UP007 手动修 + 4 F401/1 I001 自动修）。`uv run pytest tests/test_tool_protocol.py -q` → **268 passed in 0.35s**（R82 242 + R83 26 新测试函数）。全量回归 `uv run pytest -q` → **2726 passed, 10 skipped, 1 warning in 107.49s**（2458 + 268 = 2726，零回归；唯一 warning 是 fastapi httpx 弃用提示，预先存在与 R83 无关）。锚点 R83-1 b9ffd11。

### YAGNI 边界

- **`InternallyTagged` 共享基类推迟** — 现有 3 个内部标记枚举（`ToolDefinitionMode` 2 变体 / `ToolErrorWire` 15 / `McpBlock` 3）形态差异足够大（tag 字段名 `mode`/`code`/`type` 不同，payload 形态各异），共享基类成本高于收益。R82 决策树标"出现第二个再抽象"，R83 复核后**继续推迟**——形态不收敛。单调用方场景 YAGNI。
- **9 个较大模块推迟** — envelope / methods / capabilities / registration / frames(1549 行) / session_event(404) / turn_hook(700) / hook / registry_error。R84+ 按 `lib.rs` `pub use` 依赖序逐模块迁移。
- **`Custom.subcode` schema 校验推迟** — 生产者负责 subcode 非空与格式，crate 不校验（`check_custom_kind` 只查碰撞不查格式）。迁移忠实保留。
- **pydantic BaseModel for 结构体推迟** — dataclass + 手写 to_wire 已满足 serde skip 精确控制 + Display 保真；若后续回合需 pydantic 校验链路再升级。
- **`KNOWN_NOTIFICATION_KINDS` 自动同步上游推迟** — 当前 19 条目硬编码 + 审计测试（每变体往返断言其 `type` 判别在此列表），上游新增触发测试失败而非静默漂移。无 build-time 自动抓取（无上游 git 依赖）。

### Commit

`feat(platform): R83 xai-tool-protocol wire 枚举层（error_wire + output_wire + notification_wire 三枚举落地，crate 第 2/3 个内部标记枚举 + 首个相邻标记 kind/value + 首个相邻标记 shape/value + 首个 forward-compat Custom 仿冒防护，R82 backfill 接线）[新增 tool_protocol/ 3 模块 + error_codes backfill: error_wire.py(~433) ToolErrorWire 内部标记枚举(tag=code) 15 变体(_ToolErrorWireBase 基类 to_wire 反射 fields 跳 None 复现 Option::is_none + code ClassVar + 每变体 __str__ thiserror Display) + 5 rename override(forbidden/connection_lost/invalid_params/frame_too_large/internal_error ClassVar 显式) + _VARIANTS 注册表(rename 自动落) + _coerce_field(ToolId/RequestId 透明 str newtype 重包裹 split |) + from_wire(dispatch code absent Option None 未知 raise) | output_wire.py(~229) ToolOutputWire 相邻标记(kind/value) 3 变体 Text/Json/Mcp(Mcp 嵌套 blocks) + McpBlock 内部标记(type) 3 变体 TextBlock/ImageBlock/ResourceBlock(Option mime_type/text 跳) + from_wire + mcp_block_from_wire | notification_wire.py(~213) WireToolNotification 相邻标记(shape/value) 2 变体 Known(opaque)/Custom(forward-compat) + WireCustomNotification payload(kind+payload 恒在) + KnownVariantCollision thiserror({kind:?} repr 引号) + KNOWN_NOTIFICATION_KINDS 19 PascalCase 元组 + known_notification_kinds() const fn + check_custom_kind() emit 时仿冒防护(命中已知 raise 接受未知 None) | error_codes.py backfill from_tool_error_wire(err.code 字符串 dispatch _VARIANT_CODE_TO_NUMERIC 15->numeric rename-aware 无 import) + workspace_unavailable_wire(reason,phase) 构造 Custom(subcode 镜像 details.code retryable True) | __init__.py barrel 导出全 R83 符号 from_wire/to_wire 不导出(镜像 Rust pub use) notification Custom 不导出避撞; test_tool_protocol.py +7 类 26 测试(TestToolErrorWire 15 变体 to_wire 含 5 rename + 参数化 code-tag + 22 case round-trip 全 Option 组合 + from_wire 重包裹 + absent None + 未知 raise + 7 Display 保真 behavior_version 下划线/internal 条件/custom em-dash U+2014 + TestToolOutputWire Text/Json/Mcp + Mcp 嵌套 + 未知 raise + TestMcpBlock 3 变体 + ResourceBlock None 跳 + 未知 raise + TestWireToolNotification Known/Custom + 未知 raise + TestKnownNotificationKinds 19 长度 + tuple + PascalCase + 参数化 check_custom_kind 拒 19/接受未知/碰撞 repr 引号 + TestErrorCodesBackfill 参数化 15->numeric + 6 reason x 4 phase Custom + message + round-trip + TestPackageSurfaceR83 barrel 导出 from_wire 不导出); KEY 决策 1: 15 变体内部标记每变体 _ToolErrorWireBase dataclass 子类(非命名工厂 变体太多) code ClassVar + _VARIANTS 注册表 rename 自动; KEY 决策 2: to_wire 反射 fields 跳 None 共享基类 DRY 新变体只写 dataclass+__str__; KEY 决策 3: Display 每变体 __str__ 非 message() helper(4 变体有 message 数据字段撞名); KEY 决策 4: 通知相邻标记非 untagled + check_custom_kind emit 时仿冒防护(非注册时); KEY 决策 5: from_tool_error_wire err.code 字符串 dispatch 非 isinstance 链(复用 rename-aware tags 无 import); KEY 决策 6: barrel from_wire/to_wire 不导出 镜像 Rust lib.rs pub use; KEY 坑 1: 5 serde rename override ClassVar 显式; KEY 坑 2: Display 逐字保真 behavior_version 下划线非空格/internal 条件后缀/em-dash U+2014; KEY 坑 3: from __future__ annotations 使 Field.type 字面注解字符串 split |; KEY 坑 4: dataclass 字段顺序非 default 在 default 前 RenderLimited card_id 最后 wire 键序不同 JSON 无语义影响; KEY 坑 5: 透明 str newtype dataclass __init__ 不调 ToolId(value) from_wire 须 _coerce_field 重包裹; KEY 坑 6: error_wire.Custom 与 notification_wire.Custom 同名 barrel 只导 error 测试 NotificationCustom 别名; KEY 坑 7: UP007 运行时模块级别名赋值 X|Y 形式 + 删 unused Union 非 auto 手动 4 文件; KEY 坑 8: ruff isort order-by-type 拆子模块 import 块接受; 验证 ruff 6 文件 clean + pytest test_tool_protocol.py 268 passed 0.35s + 全回归 2726 passed 10 skipped 1 warning 107s 零回归, 锚点 R83-1 b9ffd11]`



## R84 — xai-tool-protocol envelope.rs（JSON-RPC 2.0 信封层落地，crate 首个 untagged 枚举 JsonRpcId，闭合 4 种 serde 形态全覆盖里程碑）
锚点:R84-1 8567189

### 本轮目标

继续 R82/R83 的 `xai-tool-protocol` crate 前向迁移。R82 落地依赖-free 地基层，R83 落地三个 wire 枚举（内部标记 ×3 + 相邻标记 ×2），但 crate 的 4 种 serde 形态中 **untagged** 仍未触——R83 末明确标注"grok crate 此层无 untagged 枚举"，而 envelope.rs 的 `JsonRpcId`（`#[serde(untagged)]` over String|Number）正是 crate 首个 untagged 枚举。R84 落地 crate 的 **JSON-RPC 2.0 信封层**——request/notification/response/error 四种信封 + 严格 `"2.0"` 协议版本标记 + 首个 untagged 枚举，闭合 crate 的 4 种 serde 形态全覆盖里程碑（内部标记 / 相邻标记 / untagged / 透明 newtype）。

本轮交付：1 个新模块 `envelope.py`（~534 行，7 个 Rust 符号 + Python 特有变体）+ barrel 扩展（12 符号）+ 8 测试类 48 测试函数。response 信封的 `result` XOR `error` 不变量通过 custom serde（Rust Flat 结构体模式）在 `from_wire` 强制。

### 融合结论

envelope.rs 是 JSON-RPC 2.0 协议的信封层——所有 request/response/notification 在 wire 上的外层包装。与 MiniMax Code 现有 IPC 层（`ipc/protocol.py` 的 pydantic `Request`/`Response`/`Notification`）不同，wire 信封层强调 **跨对等端稳定 + 协议字面量严格**：`jsonrpc` 字段必须是字面量 `"2.0"`（custom serde 拒绝任何其他值），`id` 字段是 string OR number（untagged dispatch），response 必须 result XOR error（custom serde 不变量）。

融合落点：`tool_protocol/envelope.py` 自包含的 dataclass + 手写 `to_wire`/`from_wire`，复用 R82 的 `RequestId`/`SessionId`/`FrameSeq` newtype（跨层数据流：envelope `id` ↔ `RequestId`，envelope `session_id` ↔ `SessionId`，notification `seq` ↔ `FrameSeq`）。泛型参数（`JsonRpcRequest<P>` 等）通过 `TypeVar` + `Any` 实现——`from_wire` 返回 `JsonRpcRequest[Any]`，调用方可 pin 具体参数 schema（如未来 frames 的 `ToolCallParams`）而不失信封不变量。`session_id` 是 Grok 路由/健全性扩展（与 `params` 内的 `session_id` 是独立两层，无 `#[serde(flatten)]`），忠实保留分层。

### 交付

- **`envelope.py`（~534 行）** — 7 个 Rust 符号 + Python 特有变体：
  - `JsonRpcVersion`（unit struct + custom serde）— `to_wire` 恒返回 `"2.0"`，`from_wire` 仅接受字面量字符串 `"2.0"`（非 str 或其他字符串 raise `JsonRpcVersionError`），`__slots__ = ()` + `__eq__`/`__hash__` 单例语义（所有实例相等且 hash 一致，可作 dict key）。
  - `JsonRpcId`（untagged 枚举，crate 首个）— `JsonRpcIdString` / `JsonRpcIdNumber` 两 dataclass 变体，`JsonRpcId = JsonRpcIdString | JsonRpcIdNumber` 辨别联合别名。`jsonrpc_id_from_wire(data)` 按 Python 类型 dispatch：`bool` 最先报错（`ValueError`，bool 是 int 子类但 serde 不 coerce 到 i64）、`str` → String、`int`（非 bool）→ Number、其他 `ValueError`。`JsonRpcIdNumber.__post_init__` 拒 bool + i64 范围检查（`_I64_MIN`/`_I64_MAX`）。`as_request_id` 双向投影（Number(7) → RequestId "7" 字符串化，String ↔ RequestId 直接）。
  - `JsonRpcRequest[P]` / `JsonRpcNotification[P]`（泛型信封）— `Generic[P]`，`params: P`。`session_id`/`seq` Option 跳 None（`skip_serializing_if = "Option::is_none"`）。Request 有 `id`，Notification 无 `id`（`to_wire` 不产出 `id` 键）+ 可选 `seq`（`FrameSeq` 单调计数器）。`from_wire`/`to_wire` 经 `_payload_to_wire`（有 `to_wire` 调用，否则透传，复现 `serde_json::Value` 分支）处理 params。
  - `JsonRpcError`（错误对象）— `code`/`message`/`data`，`data` Option 跳 None。
  - `ResponseOutcome`（`ResponseResult[R]` | `ResponseError` 辨别联合）+ `JsonRpcResponse[R]`（response 信封）— `ok`/`err` 构造函数 + `with_session`（mut self 链式，修改并返回自身）。`to_wire` 经 `isinstance(outcome, ResponseResult)` 产出 `result` XOR `error`。`from_wire` 用 `data.get("result")`/`data.get("error")` 配合 `is not None`（复现 serde Option null→None 语义，非裸键存在检查）强制不变量：两者皆在 → `ValueError "... XOR ..., got both"`，两者皆无 → `ValueError "... result or error ..."`。
- **`__init__.py` barrel（重写 221 行）** — 导出 12 个 envelope 符号（`JsonRpcVersion`/`JsonRpcVersionError`/`JsonRpcId`/`JsonRpcIdString`/`JsonRpcIdNumber`/`JsonRpcRequest`/`JsonRpcNotification`/`JsonRpcError`/`JsonRpcResponse`/`ResponseOutcome`/`ResponseResult`/`ResponseError`）；`jsonrpc_id_from_wire` **不导出**（镜像 Rust `lib.rs` `pub use`，转换走子模块）。docstring 更新 R84 bullet + envelope 从推迟列表移除。import 块按字母序插入 envelope（connection 后）+ `__all__` 加 `# envelope (R84)` 段。
- **`test_tool_protocol.py`（+~290 行，8 新测试类 48 测试函数）** — `TestJsonRpcVersion`（to_wire 字面量 + str + from_wire 接受 + 参数化拒错字符串 6 case + 参数化拒非 str 6 case + 单例 eq/hash/dict key）/ `TestJsonRpcId`（string/number 往返 + 构造拒 bool + from_wire 拒 bool + 参数化拒其他类型 4 case + Number as_request_id 字符串化 + String as_request_id 往返 + i64 范围 3 case）/ `TestJsonRpcRequest`（session_id None 省略/Some 存在 + 往返）/ `TestJsonRpcNotification`（无 id 键 + Optionals 省略 + seq/session Some 存在 + 往返）/ `TestJsonRpcError`（data None 省略 + Some 存在 + 往返 + 无 data 往返）/ `TestJsonRpcResponseInvariant`（ok 仅 result + err 仅 error + ok 往返 + err 往返 + with_session 链式 is self + session_id None 省略 + 两者皆在拒 XOR + 两者皆无拒）/ `TestEnvelopeSessionIdIndependence`（envelope session_id vs params session_id 不 flatten 独立两层）/ `TestPackageSurfaceR84`（barrel 导出 12 符号 + jsonrpc_id_from_wire 不导出）。

### 映射决策树 + 坑

**KEY 决策 1 — `JsonRpcId` untagged 按 Python 类型 dispatch（bool 最先报错）：** serde `#[serde(untagged)]` 逐 arm 尝试 String/Number。Python 按有效负载形态 dispatch：`bool` 最先单独报错（bool 是 int 子类，必须先于 int 检查否则 `isinstance(True, int)` True 会误纳），`str` → String，`int`（非 bool）→ Number，其他 `ValueError`（复现 serde untagged 全 arm 失败）。

**KEY 决策 2 — `JsonRpcVersion` 严格字面量 unit struct + `__slots__ = ()` 单例：** custom serde 仅接受字面量 `"2.0"`。unit struct 用 `__slots__ = ()`（无实例状态）+ `__eq__`/`__hash__` 单例语义（所有实例相等、hash 一致，可作 dict key）。`from_wire` 拒非 str 或非 "2.0"，raise `JsonRpcVersionError`（消息 `expected jsonrpc "2.0", got {value!r}`，`{v:?}` Debug → repr）。

**KEY 决策 3 — `result` XOR `error` 不变量 via `from_wire` `is not None` 检查：** Rust 用 Flat 结构体 + custom serde 强制。Python `from_wire` 用 `data.get("result")`/`data.get("error")` + `is not None`——这复现 serde Option 的 null→None 语义（JSON `null` 与 absent 键都 surface 为 None），而非裸键存在检查。两者皆在 → "XOR" 报错，两者皆无 → "result or error" 报错，消息命名违例 arm 供调用方 assert。

**KEY 决策 4 — 泛型 via `TypeVar` + `Any`（`from_wire` 返回 `[Any]`）：** Rust `JsonRpcRequest<P>` 泛型。Python `Generic[P]` + `P = TypeVar("P")`，`params: P` 字段。`from_wire` 返回 `JsonRpcRequest[Any]`（wire dict 透传 params），调用方可 pin 具体参数 schema 而不失信封不变量。`_payload_to_wire(obj)` 辅助：有 `to_wire` 调用，否则透传（复现 `serde_json::Value` 分支，支持未来 frames 结构体）。

**KEY 决策 5 — 命名前缀变体避 Python builtin 冲突：** Rust `JsonRpcId::String`/`::Number` 和 `ResponseOutcome::Result`/`::Error` 与 Python builtin/常见名冲突。落地为 `JsonRpcIdString`/`JsonRpcIdNumber` 和 `ResponseResult`/`ResponseError`（前缀变体），`JsonRpcId`/`ResponseOutcome` 是辨别联合别名。barrel 导出变体（Python 用户需变体构造/匹配；Rust 经 `Enum::Variant` 语法访问）。

**KEY 决策 6 — envelope `session_id` 与 params `session_id` 独立两层（无 flatten）：** Grok envelope 的 `session_id` 是路由/健全性扩展，与 params 内的业务 `session_id` 是两层。`to_wire`/`from_wire` 不 flatten，各自独立序列化。测试 `TestEnvelopeSessionIdIndependence` 显式断言两层共存不互相覆盖。

**KEY 决策 7 — barrel `jsonrpc_id_from_wire` 不导出：** 镜像 Rust `lib.rs` `pub use`——导出类型名 + 变体，但不导出转换函数（`from_wire`/`to_wire`/`jsonrpc_id_from_wire`）。调用方走子模块（`tool_protocol.envelope.jsonrpc_id_from_wire`）。

**KEY 坑 1 — em-dash/反引号 Edit 匹配失败 → Write 重写整个 barrel：** barrel `__init__.py` 的 docstring 含 em-dash（U+2014）+ RST role 反引号（`:mod:`envelope``）。Edit 工具的 old_string 匹配 em-dash 时模型 tokenize 可能产出不同 Unicode 码点（U+2014 vs U+2015）导致 "String to replace not found"。Serena `replace_content` 回退也失败（无活动项目）。最终用 Write 重写整个 barrel（221 行）——import/`__all__` 是纯 ASCII 从 Read 精确复制，docstring 全新内容不依赖匹配。

**KEY 坑 2 — Serena MCP 项目未激活：** Serena 已知项目（`bilibili_search`/`remix_-muse_...`/`v4.2-demo`）不含工作目录；`mcp__serena__replace_content` 在 "No active project" 上失败。需先 `mcp__serena__activate_project`（提示用户选择）。本轮避用 Serena，全程 Edit/Write。

**KEY 坑 3 — `JsonRpcIdNumber` bool 拒绝 + i64 范围：** `bool` 是 `int` 子类，`isinstance(True, int)` True。`__post_init__` 必须 `isinstance(value, bool)` 先于 int 检查拒绝（serde 不 coerce bool 到 i64）。i64 范围检查（`_I64_MIN = -(2**63)`/`_I64_MAX = 2**63 - 1`）拒超出范围值。

**KEY 坑 4 — dataclass 字段顺序（非 default 在 default 前）vs wire 键序手工控制：** Rust 声明 `jsonrpc, id, session_id, method, params`，但 `session_id` 是 Option（default None），Python dataclass 规则强制 `session_id` 在最后。`to_wire` 手工控制键顺序匹配 Rust（jsonrpc/id/session_id?/method/params）。JSON 对象键序无语义影响。

**KEY 坑 5 — `from_wire` result/error 用 `is not None` 非键存在检查：** serde Option 将 JSON `null` 与 absent 键都 map 为 None。若用 `"result" in data` 检查，`{"result": null}` 会误判为"有 result arm"。`data.get("result") is not None` 正确复现 Rust `Option::is_some` 语义。

**KEY 坑 6 — F401 未使用别名删除：** barrel 导出 `JsonRpcId`/`ResponseOutcome` 辨别联合别名，但测试用具体变体（`JsonRpcIdString`/`ResponseResult` 等）+ isinstance，别名本身未直接引用。ruff F401 标记。删除两 import——barrel surface 测试经 `hasattr(pkg, name)` 字符串名已覆盖别名暴露性，删除安全（KISS）。

**KEY 坑 7 — R82 API 复用无 `.as_str()`：** `RequestId`/`SessionId` 是 str 子类，无 `.as_str()` 方法——用 `str(id)`。`FrameSeq.new(n)`/`.from_wire(int)`/`.to_wire()` → int（bool 拒绝）。envelope 经 `from_request_id`/`as_request_id` 双向投影。

### 验证

`cd agent && uv run ruff check minimax_code/tool_protocol tests/test_tool_protocol.py` → **All checks passed!**（envelope.py + __init__.py + test 3 文件 clean，2 处 F401 别名删除）。`uv run pytest tests/test_tool_protocol.py -q` → **316 passed in 0.47s**（R83 268 + R84 48 新测试函数）。全量回归 `uv run pytest -q` → **2774 passed, 10 skipped, 1 warning in 99.69s**（2726 + 48 = 2774，零回归；唯一 warning 是 fastapi httpx 弃用提示，预先存在与 R84 无关）。锚点 R84-1 8567189。

### YAGNI 边界

- **`JsonRpcId::new_uuid_v7` 推迟** — Python stdlib `uuid` 无 v7 生成器（同 R82 `ToolCallId::new_v7` 约束）。Rust `lib.rs` `pub use` 不导出，barrel 不受影响。
- **8 个较大模块推迟** — methods（方法目录）/ capabilities / registration / frames（1549 行 tool-server 帧协议）/ session_event（404）/ turn_hook（700）/ hook / registry_error。R85+ 按 `lib.rs` `pub use` 依赖序逐模块迁移。envelope 是 frames 的前置依赖（frames 的 `ToolCallParams` 等将 pin `JsonRpcRequest[P]` 的 `P`）。
- **frames 结构体参数化推迟** — 当前 `JsonRpcRequest.params: P` 经 `_payload_to_wire` 透传 dict/Any（复现 `serde_json::Value` 分支）。frames 落地后可 pin 具体结构体 schema，`_payload_to_wire` 已预留 `to_wire` 调用分支。
- **pydantic BaseModel 推迟** — dataclass + 手写 to_wire 已满足 custom serde 精确控制（字面量严格 / untagged dispatch / XOR 不变量）；若后续回合需 pydantic 校验链路再升级。
- **`JsonRpcVersion` 复用 `Literal["2.0"]` 推迟** — 当前 unit struct + custom serde 更贴近 Rust 的 visitor 模式（拒非 str + 非 "2.0" 两类错误）。`Literal` 类型注解无法区分"非 str"与"错误 str"两类拒绝，保真度不足。

### Commit

`feat(platform): R84 xai-tool-protocol envelope.rs（JSON-RPC 2.0 信封层，crate 首个 untagged 枚举 JsonRpcId，闭合 4 种 serde 形态全覆盖里程碑）[新增 tool_protocol/envelope.py(~534) 7 Rust 符号 + Python 变体: JsonRpcVersion unit struct(custom serde 仅接受字面量 "2.0" 拒非 str/错 str raise JsonRpcVersionError + __slots__() + __eq__/__hash__ 单例可作 dict key) | JsonRpcId untagged 枚举(crate 首个) JsonRpcIdString/JsonRpcIdNumber dataclass + 联合别名 + jsonrpc_id_from_wire(按 Python 类型 dispatch bool 最先报错/str->String/int->Number/else ValueError) + Number __post_init__(拒 bool 先于 int + i64 范围 _I64_MIN/MAX) + as_request_id 双向投影(Number 7->"7" 字符串化) | JsonRpcRequest[P]/JsonRpcNotification[P] Generic[P] params:P session_id/seq Option 跳 None(Request 有 id Notification 无 id 键) + _payload_to_wire(to_wire 或透传 serde_json::Value 分支) | JsonRpcError code/message/data Option 跳 | ResponseOutcome(ResponseResult[R]|ResponseError 联合) + JsonRpcResponse[R] ok/err 构造 + with_session(mut self 链式) + to_wire(isinstance 产 result XOR error) + from_wire(data.get is not None 复现 Option null->None 两者皆在->XOR 报错 两者皆无->result or error 报错) | __init__.py barrel 重写 221 行 导出 12 envelope 符号 jsonrpc_id_from_wire 不导出(镜像 Rust pub use) docstring R84 bullet + envelope 移出推迟 import 字母序 + __all__ # envelope (R84) 段; test_tool_protocol.py +8 类 48 测试(TestJsonRpcVersion 字面量+str+接受+参数化拒错 str 6+拒非 str 6+单例 eq/hash/dict key + TestJsonRpcId string/number 往返+构造拒 bool+from_wire 拒 bool+参数化拒其他 4+Number as_request_id 字符串化+String 往返+i64 范围 3 + TestJsonRpcRequest session_id None 省略/Some 存在+往返 + TestJsonRpcNotification 无 id 键+Optionals 省略+seq/session Some+往返 + TestJsonRpcError data None 省略/Some+往返+无 data 往返 + TestJsonRpcResponseInvariant ok 仅 result/err 仅 error/ok 往返/err 往返/with_session is self/session None 省略/两者皆在拒 XOR/两者皆无拒 + TestEnvelopeSessionIdIndependence envelope vs params session_id 不 flatten 独立两层 + TestPackageSurfaceR84 barrel 12 符号+jsonrpc_id_from_wire 不导出); KEY 决策 1: JsonRpcId untagged 按 Python 类型 dispatch bool 最先(bool 是 int 子类先于 int 检查); KEY 决策 2: JsonRpcVersion 严格字面量 unit struct+__slots__ 单例 custom serde 拒非 str/错 str; KEY 决策 3: result XOR error via from_wire is not None(复现 Option null->None 非键存在检查); KEY 决策 4: 泛型 TypeVar+Any from_wire 返回 [Any] _payload_to_wire 预留 frames to_wire; KEY 决策 5: 命名前缀变体避 builtin(JsonRpcIdString/Number ResponseResult/Error + 联合别名); KEY 决策 6: envelope session_id 与 params session_id 独立两层无 flatten; KEY 决策 7: barrel jsonrpc_id_from_wire 不导出镜像 Rust pub use; KEY 坑 1: em-dash/反引号 Edit 匹配失败(tokenize U+2014 vs U+2015)->Write 重写 barrel 221 行 import/__all__ 纯 ASCII 复制; KEY 坑 2: Serena MCP 项目未激活(已知项目不含工作目录)避用全程 Edit/Write; KEY 坑 3: Number bool 拒绝(bool int 子类先于 int)+i64 范围; KEY 坑 4: dataclass 字段顺序非 default 在 default 前 session_id 最后 to_wire 手工键序; KEY 坑 5: from_wire result/error is not None 非 in 检查(Option null->None); KEY 坑 6: F401 未使用别名 JsonRpcId/ResponseOutcome 删除(barrel surface hasattr 字符串已覆盖); KEY 坑 7: R82 API 复用无 as_str 用 str() FrameSeq.new/from_wire/to_wire; 验证 ruff 3 文件 clean + pytest test_tool_protocol.py 316 passed 0.47s + 全回归 2774 passed 10 skipped 1 warning 99s 零回归, 锚点 R84-1 8567189]`


---

## R85 — xai-tool-protocol methods.rs：JSON-RPC 方法目录枚举（35 变体单源宏 → Python StrEnum，envelope method 字段消费层）

锚点:R85-1 ae6e1a5

### 本轮目标

继续 `xai-tool-protocol` crate 的模块化迁移（R82 地基 → R83 wire 枚举 → R84 envelope → R85 methods）。本轮迁移 `crates/common/xai-tool-protocol/src/methods.rs`（209 行）：JSON-RPC 方法目录枚举 `Method`，作为 R84 `envelope.py` 的 `method` 字段消费层（envelope 的 `method` 是裸 `str`，由 `Method.as_wire_str()` 生产、`Method.from_wire_str()` 回收）。额外收获：把 R84 envelope 的保真度对照 Grok 原生 `tests/jsonrpc_envelope.rs`（13 个测试）做一次逐测试核对，确认 R84 无遗漏。

### 融合结论

- **单源宏 → 数据驱动 StrEnum**：Grok 用 `define_methods!` 宏从一张 `(name, wire)` 表生成枚举 + serde rename + `as_wire_str`/`from_wire_str`/`ALL` + Display。Python 用 `enum.StrEnum` 落同一纪律——成员值**就是** wire 字符串（匹配 `#[serde(rename = $wire)]`），`__str__` 返回值（匹配 Rust `Display` 委托 `as_wire_str`），JSON 序列化为带引号的 wire 字符串。无需手写 rename 表，成员声明即单一真理来源。
- **`from_wire_str` via `_value2member_map_`**：枚举内部「值→成员」字典，O(1) 查表，未知返回 `None`（匹配 Rust 可失败匹配返回 `None`，而非 panic）。
- **`Method.ALL = tuple(Method)`**：类主体后赋值（枚举无法在自身体内引用自身），`# type: ignore[attr-defined]`。匹配 Rust `pub const ALL: &[Method]` 切片；迭代顺序 = 声明顺序。
- **`UNKNOWN_METHOD_MSG_PREFIX` 固定**：`"unknown method \`"` ——OLD hub 拒绝未知 method 的消息前缀形状。当前客户端已改用 `hello_ack` 的 `capabilities` 广告探测 hub 版本，但这个前缀形状必须固定，因为 SDK 早期版本构建的终端二进制仍按此精确前缀做 OLD-hub 检测，它们仍在现网。**不要随意改动。**
- **10 个 `#[doc]` 变体语义保留**：Python `StrEnum` 成员无法在声明处携带 per-member docstring（与 Rust 不同），用 `_METHOD_DOCS` 字典 + `method_doc()` 访问器保留 10 个有语义价值的变体文档（SessionAttachServer / ToolCancel / HookReply / TracesDonate / LogsDonate / MetricsDonate / ServersList / Serve / SessionBind / SessionUnbind）。其中 `ToolCancel` 是 `Hook` + `HookEvent::Cancel` 的语法糖这种关键语义尤其要保留。
- **方向分组保留源序**：35 变体按方向分组（harness→service 18 / tool_server→service 6 / service→tool_server 1 / service→harness 3 / server-discovery 1 / tool_server status 3 / session lifecycle 3），声明顺序与 Grok 一致。枚举是扁平的——方向强制是 hub 的职责，不是协议 crate 的。

### 交付

| 文件 | 变更 | 说明 |
|---|---|---|
| `agent/minimax_code/tool_protocol/methods.py` | 新增 ~200 行 | `Method(StrEnum)` 35 变体 + `as_wire_str`/`from_wire_str`/`ALL` + `UNKNOWN_METHOD_MSG_PREFIX` + `_METHOD_DOCS`/`method_doc()` |
| `agent/minimax_code/tool_protocol/__init__.py` | 改 | barrel 加 `Method`/`UNKNOWN_METHOD_MSG_PREFIX` 导入 + `__all__` 段 + docstring R85 bullet（methods 从 deferred 移除） |
| `agent/tests/test_tool_protocol.py` | 改 | barrel 导入加 2 符号 + submodule 加 `method_doc`；追加 `TestMethod`（17 测试：ALL 计数/声明序、全 35 往返、value/wire、Display via `__str__`、未知返 None、特定 wire 串、serde 往返、PREFIX 固定、方向分组、method_doc 有/无、envelope 跨模块消费、StrEnum 子类、barrel 单源）+ `TestPackageSurfaceR85`（3 测试） |
| `docs/evolution/ITERATION_LOG.md` | 追加 | 本条目 |

### 映射决策树 + 坑

1. **枚举承载形态**：Rust `#[derive(Serialize, Deserialize)] enum Method` + `#[serde(rename = $wire)]` → Python `enum.StrEnum`（成员值 = wire 字符串）。决策：StrEnum 是 Rust「serde rename 枚举」的 Python 等价物——值即 wire 串，`__str__` 即 `as_wire_str`，JSON 序列化即带引号 wire 串。比「`(name, value)` 元组表 + 手写映射函数」省一个数据源（KISS/DRY）。UP042 合规（`(str, Enum)` 必须 StrEnum）。
2. **`from_wire_str` 实现**：候选 A `_value2member_map_.get(s)`（O(1) 查表，未知返 None）；候选 B 遍历比较。选 A——枚举内部字典正是为此而生，且 None 返回匹配 Rust 可失败语义。
3. **`ALL` 放哪**：候选 A 类内 `ALL = ...`（不可行——枚举体内无法引用自身）；候选 B 模块级 `Method.ALL = tuple(Method)` 类后赋值。选 B + `# type: ignore[attr-defined]`。
4. **per-member docstring**：Rust `#[doc]` 在变体上；Python StrEnum 成员无 per-member docstring 槽。决策：`_METHOD_DOCS` 字典 + `method_doc()` 函数，保留 10 个有语义的变体。不强行给全部 35 个加（YAGNI——其余 25 个名字自解释）。
5. **barrel 导入顺序**：ruff isort `order-by-type` 默认 true——SCREAMING_SNAKE 常量组内字母序（`UNKNOWN_METHOD_MSG_PREFIX` < `WORKSPACE_*`，U<W），PascalCase 组内字母序（`Mcp` < `Method`）。手写易错，用 `ruff check --fix`（仅对 R85 编辑的两个文件，无附带损害风险）一键修正。
6. **坑（em-dash / heredoc）**：barrel docstring 含 em-dash + RST 反引号，Edit 的 em-dash 匹配不可靠 → 用 Write 整文件重写 barrel（最干净，避开 7 个 Edit 的 em-dash 锚点风险）。ITERATION_LOG 条目含反引号 → Write 临时文件 + `cat >>`（避开 heredoc 反引号坑）。

### 验证

- `uv run ruff check minimax_code/tool_protocol tests/test_tool_protocol.py` → `All checks passed!`（isort `--fix` 后 CLEAN）
- `uv run pytest tests/test_tool_protocol.py -q` → **333 passed**（R84 的 316 + R85 新增 17）
- `uv run pytest -q`（全量回归）→ **2791 passed, 10 skipped**（2774 → 2791，+17 R85，零回归）
- **额外收获：R84 envelope 保真度核对**——对照 Grok 原生 `tests/jsonrpc_envelope.rs`（13 个测试：version literal、id string/number、bool 拒绝、i64 范围、request/notification 字段序、`session_id` skip、error `data` skip、response `result` XOR `error` 双臂/缺臂拒绝、`with_session` 链式、ok/err 构造器），逐测试与 R84 `envelope.py` 实现匹配——**保真度完美，无需修复**，确认 R84 正确。

### YAGNI 边界

- **未迁移** `Method` 的 `new_uuid_v7`（无对应，Rust 枚举本身不生成 id）；`UNKNOWN_METHOD_MSG_PREFIX` 的消息构造器（Grok 也没有，只导出前缀常量）；方向强制的 hub 逻辑（不在协议 crate 范围）。
- **barrel 不导出** `method_doc`（镜像 Rust `lib.rs` `pub use methods::{Method, UNKNOWN_METHOD_MSG_PREFIX}`——只 2 符号），`method_doc` 留在 submodule，测试从 submodule 直导。
- **docstring 概览标题**未加 R85（test 文件顶部 docstring 仍写「R82+R83+R84 envelope」）——methods 是 envelope 的消费层，语义归类进 envelope 段落合理，避免过度编辑标题（KISS）。
- **crate 延迟模块**（lib.rs 声明）：`capabilities`, `registration`, `frames`(1549 行), `session_event`(404), `turn_hook`(700), `hook`, `registry_error`。R86+ 按依赖序迁移。

### Commit

```
feat(platform): R85 迁移 xai-tool-protocol methods.rs（JSON-RPC 方法目录 StrEnum）

- Method(StrEnum) 35 变体：成员值=wire 字符串（匹配 #[serde(rename)]），
  __str__=as_wire_str（匹配 Rust Display 委托），JSON 序列化为带引号 wire 串
- as_wire_str / from_wire_str（via _value2member_map_，O(1) 查表，未知返 None
  匹配 Rust 可失败匹配）/ Method.ALL（类后赋值 tuple，声明序）
- UNKNOWN_METHOD_MSG_PREFIX 固定（fleet-compat：OLD-hub 检测前缀形状，
  SDK 早期终端二进制仍按此精确前缀，不要随意改）
- _METHOD_DOCS 字典 + method_doc()：保留 10 个 #[doc] 变体语义
  （StrEnum 成员无 per-member docstring 槽；ToolCancel=Hook+Cancel 糖等）
- 方向分组保留源序（扁平枚举，方向强制是 hub 职责）
- barrel 加 Method/UNKNOWN_METHOD_MSG_PREFIX 导入 + __all__ + docstring
- 测试：TestMethod（17）+ TestPackageSurfaceR85（3）
- 额外收获：R84 envelope 保真度对照 Grok 原生 jsonrpc_envelope.rs（13 测试）逐核对，完美无遗漏

验证：ruff CLEAN / tool_protocol 333 passed / 全量 2791 passed(+17) +10 skipped


## R86 — capabilities.rs（per-tool 能力位集，hello_ack.capabilities 类型 + registration 依赖）

锚点:R86-1 52fc5b5

### 本轮目标

迁移 grok `xai-tool-protocol/src/capabilities.rs`（106 行，lib.rs `pub use` 导出 5 符号）到 Python `tool_protocol/capabilities.py`。该模块是 **handshake `hello_ack.capabilities` 的 wire 类型**（R82 handshake 的 HelloAckMsg 携带 hub 能力通告）+ **registration 的依赖**（tool-server 注册载荷含 per-tool 能力位集）。作为依赖无关的叶子层，先于 registration 落地。serde 形态：普通结构体（mixed skip 语义）+ `#[serde(rename_all = "snake_case")]` 单元变体枚举（无 `#[serde(other)]`，未知拒绝）。

### 融合结论

5 符号全部迁移，serde 保真逐字段对照：

- `ToolCapabilities` → `@dataclass`，9 字段保守默认（`streaming=None` / `supports_cancel=False` / `max_concurrency=None` / `is_read_only=False` / `hooks=[]` / `behavior_version=None` / `max_frame_bytes=None` / `timeout_ms=None` / `tool_scope=None`），匹配 Rust `#[derive(Default)]`。**关键保真**：两个 bool 字段（`supports_cancel` / `is_read_only`）Rust 只给 `#[serde(default)]` 无 `skip_serializing_if` → `to_wire` 总在（即使 `False`）；其余 Option/Vec/HashMap 字段 None/空时省略。
- `StreamingSpec` → `@dataclass`，`subkind` 必需 + `max_delta_bytes: int | None`（None 省略）。
- `HookKind` → `StrEnum` 6 变体（成员值即 snake_case wire 串：`on_session_open` / `on_session_close` / `on_tool_call_start` / `on_tool_call_result` / `on_cancel` / `on_notification`），`to_wire`/`from_wire`，**未知 raise `ValueError`**（无 `#[serde(other)]` arm，严格拒绝而非吞掉）。
- `ToolScope` → `StrEnum` 2 变体（`read` / `write`），同 HookKind 严格拒绝未知。
- `NotificationSchemas` → `@dataclass`，2 `dict[str, Any]` 字段（`outbound` / `inbound`，空省略，缺失键 → `{}`）。

`to_wire`/`from_wire` 命名与 crate 其他 wire 模块（`error_wire` / `output_wire`）统一；barrel 不重导出它们（capabilities 无模块级 `from_wire` 函数，转换是每个类型的 classmethod，匹配 Rust lib.rs `pub use` 仅导出 5 类型名）。

### 交付

- `agent/minimax_code/tool_protocol/capabilities.py`（265 行，5 符号 + 严格 serde 保真）
- `agent/minimax_code/tool_protocol/__init__.py`（barrel：+capabilities import 块（isort 置于 connection 前）+ `__all__` `# capabilities (R86)` 组（methods 后、handshake 前）+ docstring R86 要点 + deferred 列表删 `capabilities`）
- `agent/tests/test_tool_protocol.py`（+29 测试：`TestHookKind` 5 / `TestToolScope` 4 / `TestStreamingSpec` 4 / `TestToolCapabilities` 9 / `TestNotificationSchemas` 4 / `TestPackageSurfaceR86` 3）
- `docs/evolution/ITERATION_LOG.md`（本条目）

### 映射决策树 + 坑

1. **StrEnum 完美匹配 rename_all snake_case 单元变体**：成员值=wire 串，JSON 序列化=带引号 wire，`__str__`=value（匹配 Rust `Display` 委托 as_wire_str）。R85（Method）已验证此模式，R86 再次确认（HookKind/ToolScope）。`from_wire` 用 `_value2member_map_.get(s)` O(1) 查找。
2. **无 `#[serde(other)]` → 未知拒绝**：R83 notification_wire 的 `Known` 有 `#[serde(other)]` 容忍未知；capabilities 的 HookKind/ToolScope **无此 arm**，严格拒绝。`from_wire` 通过 `get` + `None` 检查 + `raise ValueError` 实现（错误信息含 `!r` repr，匹配 Rust serde 拒绝语义）。
3. **bool 总在 vs Option/Vec/HashMap 省**：Rust 给 bool 字段 `#[serde(default)]` 无 `skip_serializing_if`，给 Option/Vec/HashMap 加 skip。Python `to_wire` 手控：bool 无条件写入 dict，Option/Vec/HashMap 条件写入。**坑**：容易把 bool 也写成条件（False 时省略），但 Rust 不省略。`test_bool_false_always_present` + `test_default_all_off_to_wire_only_bools` 钉死此语义。
4. **空 `{}` wire → 默认实例**：每个字段 `#[serde(default)]` 意味着缺失键用默认。Python `from_wire` 用 `data.get(key, default)`。`test_empty_wire_roundtrips_to_default` 钉死。
5. **`HashMap<String, serde_json::Value>` → `dict[str, Any]`**：value 是任意 JSON。`to_wire` 浅拷贝 `dict(self.outbound)`；`from_wire` `dict(data.get(...))`。`test_both_present` 用嵌套 list/dict 验证 opaque value 透传。
6. **isort**：`capabilities`（c-a-p）< `connection`（c-o-n），import 块置于 connection 前。`__all__` 组逻辑放置（methods R85 后、handshake R82 前，紧邻消费层 hello_ack）。**ruff 一次通过**（零 I001，无需 --fix）。
7. **barrel docstring 标题含 em-dash（—）→ Write 重写整个 barrel 而非 Edit**：em-dash 锚点 Edit 不可靠（LLM 重输入的 em-dash tokenize 可能不同，R85 已踩此坑）。本次 barrel 改动用 Write 整文件重写，em-dash 安全。
8. **测试 import 区 5 符号插入用 ASCII 锚点 Edit**：5 个分散插入点（HookKind/NotificationSchemas/StreamingSpec/ToolCapabilities/ToolScope），每个一行对（如 `    HelloMsg,\n    IdError,` → 插入 HookKind），互不重叠，ASCII 锚点 100% 可靠。

### 验证

- `uv run ruff check minimax_code/tool_protocol/capabilities.py minimax_code/tool_protocol/__init__.py tests/test_tool_protocol.py` → **All checks passed!**（零 E/F/W/I/B/UP）
- `uv run pytest tests/test_tool_protocol.py -q` → **362 passed**（333 R85 基线 + 29 R86 = 362）
- `uv run pytest -q`（全量回归）→ **2820 passed, 10 skipped, 1 warning**（2791 R85 + 29 R86 = 2820，零失败零附带损害，warning 是无关的 fastapi/httpx deprecation）

### YAGNI 边界

- `ToolCapabilities` Rust 仅有 struct + `#[derive(Default)]`，无业务方法 → Python 不加额外方法（`to_wire`/`from_wire` 是 wire 契约，非业务逻辑）。
- `StreamingSpec` 无 `new()` 构造器（Rust 直接结构体字面量）→ Python 用 dataclass 默认构造，不加工厂方法。
- `NotificationSchemas` 无 schema 验证逻辑（Rust 只是 HashMap 容器）→ Python 不加 JSON schema 验证（值是 opaque `serde_json::Value`，透传即可）。
- **未迁移 registration**（capabilities 的消费层，registration 载荷含能力位）→ 下一轮 R87 目标，capabilities 先落地作为依赖。
- lib.rs `pub use capabilities::{HookKind, NotificationSchemas, StreamingSpec, ToolCapabilities, ToolScope}` 5 符号精确匹配，barrel 不多不少。
- `HookKind`/`ToolScope` 的 `to_wire`/`from_wire` 是为接口一致性（与 error_wire/output_wire 统一命名），虽 StrEnum 可直接用 `.value`，但显式方法匹配 crate wire 模块约定且 `from_wire` 需承载未知拒绝逻辑。

### Commit

`feat(platform): R86 迁移 xai-tool-protocol capabilities.rs（per-tool 能力位集 ToolCapabilities/StreamingSpec/HookKind/ToolScope/NotificationSchemas，handshake hello_ack.capabilities 类型 + registration 依赖，bool 总在/Option·Vec·HashMap 省略 serde 保真，362+2820 测试通过）`



## R87 — registration.rs（工具服务器注册载荷，crate 首个 pydantic↔wire 桥 + 三态 sessions + String::is_empty skip）

锚点:R87-1 a4e237e

### 本轮目标

迁移 grok `xai-tool-protocol/src/registration.rs`（164 行，lib.rs `pub use` 导出 9 符号——`TransportKind` + 3 结构体 + `RegistrationOutcome` 联合别名 + 4 结果变体类）到 Python `tool_protocol/registration.py`。该模块是工具服务器向 computer-hub 注册自身（或单个工具）的 wire 载荷 + hub 报回的 per-tool 结果。消费 R82 ids（ToolId/SessionId/UserId/ServerId）、R86 capabilities（ToolCapabilities/NotificationSchemas/HookKind）、R65 tool_types（ToolDescription pydantic 模型）。**crate 首个在手控 `to_wire` dict 层内嵌 pydantic 模型的 wire DTO**，并落地两个新 serde 子形态（三态 `Option<Vec<SessionId>>` + 裸 `String` 的 `String::is_empty` skip）。

### 融合结论

9 符号全部迁移，serde 保真逐字段对照 registration.rs：

- `TransportKind` → `StrEnum` 2 变体（`local`/`remote`），`to_wire`/`from_wire`，未知 raise `ValueError`（无 `#[serde(other)]`，同 R86 HookKind/ToolScope 严格拒绝）。
- `ToolDescriptionWithSchema` → `@dataclass` 4 字段（`description: ToolDescription` 必需 + `input_schema`/`capabilities`/`notification_schemas` 三 Option 省）。`derive_tool_id()`：有命名空间 → `"{ns}:{name}"`，否则裸 `name`，经 `ToolId(...)` 构造（IdError 从构造函数传播，匹配 Rust `Result<ToolId, IdError>`）。`description` 序列化用 `_description_to_wire()` = `desc.model_dump(exclude_none=True)`——**crate 首个 pydantic↔wire 桥**：`xai_tool_types::ToolDescription` 每个非必需字段有 `skip_serializing_if = "Option::is_none"` + 丢弃的 `extra` 有 `#[serde(skip)]`，`exclude_none=True` 精确复现；往返用 `ToolDescription.model_validate(data["description"])`。
- `ToolRegistration` → `@dataclass` 11 字段（`tool_id`/`user_id`/`description`/`transport_kind` 必需 + `sessions`/`server_id`/`input_schema`/`capabilities`/`notification_schemas`/`if_match_generation`/`metadata` 七 Option）。**三态 `sessions: list[SessionId] | None`**：`None`=字段省略="无变化"（重新注册时保留既有绑定），`Some([])`=显式空数组="解绑所有会话"，`Some([...])`="替换为这些 id"。`to_wire` 用 `is not None`（显式 `[]` 序列化为空数组）；`from_wire` 用 `"sessions" in data`（缺失键→保持 None）。同样有 `derive_tool_id()`。手控 key 序（tool_id 先、transport_kind 后于 description，匹配 Rust 字段序；Python dataclass 强制非默认字段在前，但 wire 序手控）。
- `ToolServerRegistration` → `@dataclass` 9 字段（`server_id`/`user_id`/`tools` 必需 + `sessions`/`title`/`description`/`hooks`/`if_match_generation`/`metadata`）。`tools`（`Vec`）总序列化（即使空，无 skip）。`description: str = ""`——**第六种 serde 子形态**：裸 `String`（非 `Option<String>`）但有 `#[serde(default, skip_serializing_if = "String::is_empty")]`，空串省略、非空骑。`to_wire` 用 `if self.description:`；`hooks` 空 Vec 省略（`if self.hooks:`）。`sessions` 同 ToolRegistration 三态。
- `RegistrationOutcome` = `Registered | Updated | Shadowed | Rejected` 联合别名（内部标签 `#[serde(tag = "outcome", rename_all = "snake_case")]` 四结构体变体，同 R83 ToolErrorWire 的 `code` 标签 + R82 ToolDefinitionMode 的 `mode` 标签形态）。每变体 `@dataclass` + 自带 `to_wire`（stamps `"outcome": "<variant>"` 标签）。模块级 `registration_outcome_from_wire` 经 `_OUTCOME_VARIANTS` 字典按 `outcome` 标签调度（联合别名不能像具体类那样挂 classmethod，镜像 R84 `jsonrpc_id_from_wire`）。`Registered`/`Updated` 带 `generation`；`Shadowed` 带 `reason`；`Rejected` 带 `code`+`message`。

### 交付

- `agent/minimax_code/tool_protocol/registration.py`（540 行，9 符号 + 严格 serde 保真 + pydantic 桥 + 三态 sessions + String::is_empty skip + 内部标签联合调度）
- `agent/minimax_code/tool_protocol/__init__.py`（barrel：+registration import 块（isort 置于 output_wire 后）+ `__all__` `# registration (R87)` 组（9 名）+ docstring R87 要点 + deferred 列表删 `registration`）
- `agent/tests/test_tool_protocol.py`（+34 测试方法：`TestTransportKind` 4 / `TestToolDescriptionWithSchema` 5 / `TestToolRegistration` 7 / `TestToolServerRegistration` 5 / `TestRegistrationOutcome` 9 / `TestPackageSurfaceR87` 4）
- `docs/evolution/ITERATION_LOG.md`（本条目）

### 映射决策树 + 坑

1. **pydantic↔wire 桥（crate 首例）**：`ToolDescription` 是 pydantic BaseModel（R65 迁移的 `xai_tool_types`），其余 protocol 层全用手控 `to_wire` dict。桥接：出向 `model_dump(exclude_none=True)`（精确匹配 Rust 每 Option 字段 `skip_serializing_if` + `extra` 的 `#[serde(skip)]`），入向 `model_validate(data["description"])`。提取 `_description_to_wire()` 辅助函数保每处 serde 保真一致。**坑**：必须 `exclude_none=True`，否则 None 字段会序列化成 `"namespace": null`，wire 形态与 Rust（省略键）不一致。
2. **三态 `Option<Vec<SessionId>>`（第一个新子形态）**：`None`/`Some([])`/`Some([...])` 三态语义。Python 用 `list[SessionId] | None` + `is not None` 判断（**关键**：不能 `if self.sessions`，否则空 `[]` 被当 falsy 省略——必须 `is not None`）。`from_wire` 用 `"sessions" in data` 区分缺失键（None）与显式 `[]`。`test_sessions_none_is_omitted` + `test_sessions_empty_list_serialises` + `test_sessions_populated_serialises` 三测试钉死三态。
3. **`skip_serializing_if = "String::is_empty"` 裸 String（第二个新子形态，crate 第六种）**：`ToolServerRegistration.description: str = ""`，非 `str | None`。`to_wire` 用 `if self.description:`（空串 falsy 省略）。这是 crate 第六种 serde 子形态（前五种：bool 总在 R86、Option 省、Vec 空 R86、HashMap 空 R86、transparent-newtype R82 ids）。`test_empty_description_is_omitted` + `test_nonempty_description_rides` 钉死。
4. **内部标签联合的 from_wire 调度**：`RegistrationOutcome` 是 `Registered | Updated | Shadowed | Rejected` 联合别名。Python 联合不能像具体 dataclass 那样挂 `from_wire` classmethod（联合本身无类体）。决策：模块级 `registration_outcome_from_wire` + `_OUTCOME_VARIANTS` 字典（wire 标签 → 变体类）调度，镜像 R84 `jsonrpc_id_from_wire`（`JsonRpcId` 联合的首例）。`test_from_wire_rejects_unknown_tag` 钉死未知标签 `ValueError`。
5. **`derive_tool_id` 返回类型**：Rust `Result<ToolId, IdError>`；Python 直接返回 `ToolId`，让 `IdError` 从 `ToolId(...)` 构造函数传播（R82 已建立的 newtype 模式）。两个类（`ToolDescriptionWithSchema` + `ToolRegistration`）都有 `derive_tool_id()`（同逻辑，因 Rust 两个结构体各自 impl 此方法）。
6. **dataclass 字段序约束**：Python 强制非默认字段在默认字段前。Rust 字段序（`ToolRegistration`：tool_id, sessions, user_id, ...）与 Python dataclass 不兼容（sessions 有默认但排在 user_id 无默认前）。决策：Python 重排必需字段在前（tool_id/user_id/description/transport_kind），`to_wire` 手控 key 序匹配 Rust。JSON key 序语义无关，但手控保 wire 形态一致。
7. **坑（测试漏导入）**：barrel 导入块按字母序插入 R87 的 9 符号时分多次 ASCII 锚点 Edit。**踩坑**：第一次误判 `TransportClosed`/`UserId` 相邻（实际中间隔 `UnsupportedProtocolVersion`），Edit 失败。修正后重跑 ruff 发现 `Updated` + `ToolServerRegistration` 两个符号漏插入（按字母序 U 在 UnsupportedProtocolVersion/UserId 之间、ToolServerRegistration 在 ToolScope/TransportClosed 之间）——F821 暴露。补两次 Edit 修复。另外 `RegistrationOutcome` 在测试里只用 `pkg.RegistrationOutcome`（属性访问），barrel 直接导入未引用 → F401，移除该导入（测试通过 `import ... as pkg` 访问）。**教训**：批量按字母序插入多个符号时，先 grep 确认每个相邻锚点真实存在，避免基于记忆的相邻假设。
8. **IdError F401 清理**：registration.py docstring 引用 `:class:`~minimax_code.tool_protocol.ids.IdError``，但代码不直接用 `IdError`（它从 `ToolId(...)` 构造函数传播）。初版加 `_ = IdError` hack 抑制 F401；清理为删除导入（docstring 用完整路径，不需 import）。更干净。

### 验证

- `uv run ruff check minimax_code/tool_protocol tests/test_tool_protocol.py` → **All checks passed!**（零 E/F/W/I/B/UP；修复 2 个漏导入 F821 + 1 个未用 F401 后 CLEAN）
- `uv run pytest tests/test_tool_protocol.py -q` → **396 passed**（362 R86 基线 + 34 R87 = 396）
- `uv run pytest -q`（全量回归）→ **2854 passed, 10 skipped, 1 warning**（2820 R86 + 34 R87 = 2854，零失败零附带损害；warning 是无关的 fastapi/httpx deprecation）

### YAGNI 边界

- **未迁移** registration.rs 的 hub 端业务逻辑（注册路由、generation 单调递增、shadow 优先级仲裁）——不在协议 crate 范围，协议层只定义 wire DTO + 结果枚举。
- `RegistrationOutcome` 联合别名不挂 `from_wire` classmethod（联合无类体）——模块级 `registration_outcome_from_wire` 是 Rust enum `#[serde]` 的 Python 等价，不额外加工厂方法。
- `_description_to_wire()` 仅封装 `model_dump(exclude_none=True)`——看似一行，但语义重（crate 首个 pydantic 桥的 serde 契约），独立函数保每处调用一致 + 可单测。
- `ToolRegistration.derive_tool_id` 与 `ToolDescriptionWithSchema.derive_tool_id` 逻辑重复——但 Rust 两者都有此方法（结构体各自 impl），Python 保留两份匹配 Rust，不强行抽公共函数（DRY 让位于 wire 保真映射的 1:1 可追溯）。
- **crate 延迟模块**（lib.rs 声明）：`frames`(1549 行，工具服务器帧协议), `session_event`(404), `turn_hook`(700), `hook`(22), `registry_error`(47)。R88+ 按依赖序迁移（registry_error 最小 47 行可能先）。

### Commit

`feat(platform): R87 迁移 xai-tool-protocol registration.rs（工具服务器注册载荷 TransportKind/ToolDescriptionWithSchema/ToolRegistration/ToolServerRegistration/RegistrationOutcome 4 变体，crate 首个 pydantic↔wire 桥 + 三态 Option<Vec<SessionId>> sessions + 裸 String String::is_empty skip 第六种 serde 子形态 + 内部标签联合 registration_outcome_from_wire 调度，396+2854 测试通过）`


## R88 — registry_error.rs → registry_error.py（RegistryError 内部标签错误枚举 + 首个 per-variant rename 覆盖子形态）

锚点:R88-1 18db685

### 本轮目标

迁移 `grok-build/crates/common/xai-tool-protocol/src/registry_error.rs`（47 行）——
`RegistryError` 枚举，registry 层结构性 / 所有权失败的 wire DTO。这是 registry 的
`ToolErrorWire`（R83）对偶：`ToolErrorWire` 描述工具调用执行期的 wire 级错误，
`RegistryError` 描述注册期（`register_*` 批处理内部）的失败——session 不匹配、
server_id 冲突 / 占用、乐观并发 stale generation、描述结构校验失败。本轮在 R82-R87
的 serde 形态谱上落地 crate 的**第一个 per-variant `#[serde(rename)]` 覆盖**：
`AlreadyRegistered` 在 `#[serde(tag = "code", rename_all = "snake_case")]` 之上叠加
`#[serde(rename = "tool_already_registered")]`，使 wire 标签不是 `rename_all` 默认的
`"already_registered"` 而是 `"tool_already_registered"`。

### 融合结论

registry_error.rs 是一个 1 概念符号模块（1 枚举 = 6 变体 + 1 联合别名 + 1 模块级
from_wire 调度器），零外部依赖（仅用 R82 ids 的 `ServerId`/`SessionId`/`ToolId`
newtype）。融合动作：

1. **6 变体 dataclass + per-variant `to_wire`**：`AlreadyRegistered` / `SessionMismatch`
   / `ServerIdCollision` / `ServerIdInUse` / `InvalidDescription` / `StaleGeneration`，
   每个变体的 `to_wire` 硬编码自己的 wire 标签（捕获 rename 覆盖）+ 命名字段。
2. **`RegistryError = A | B | C | D | E | F` 联合别名**（UP007 合规，运行时 `|` 表达式）。
3. **模块级 `registry_error_from_wire` 调度器** + `_WIRE_TAG_TO_VARIANT` 字典在 `code`
   标签上分派——联合不能承载 classmethod，镜像 R84 `jsonrpc_id_from_wire` / R87
   `registration_outcome_from_wire`。
4. **Newtype 透明序列化**（R82 模式）：`str(self.tool_id)` 出，`ToolId(str(data["tool_id"]))` 入。
5. **严格拒绝**（无 `#[serde(other)]`）：未知标签 `raise ValueError`，匹配 crate
   惯例（`Method` / `HookKind` / `ToolScope` / `TransportKind` 同款）。

本轮确立了一个跨回合复用的** barrel 命名冲突解决模式**（见坑）。

### 交付

| 文件 | 动作 | 行数 | 说明 |
|------|------|------|------|
| `agent/minimax_code/tool_protocol/registry_error.py` | 新增 | 263 | 6 变体 dataclass + RegistryError 联合 + registry_error_from_wire 调度器 + _WIRE_TAG_TO_VARIANT 分派表 |
| `agent/minimax_code/tool_protocol/__init__.py` | 修改 | +3 | barrel 标题/摘要推进到 R88；新增 `registry_error` 导入块（仅 `RegistryError` 联合）+ `__all__` 条目 |
| `agent/tests/test_tool_protocol.py` | 修改 | +219 | 5 个 R88 测试类（28 测试方法）+ 顶部 registry_error import 块 |
| `docs/evolution/ITERATION_LOG.md` | 追加 | — | 本条目 |

测试新增（28 方法 / 5 类）：
- `TestRegistryErrorWireTags`（6）—— 每变体 wire 标签；**`AlreadyRegistered` 覆盖标签
  断言 `"tool_already_registered"` 且 `!= "already_registered"`**（钉死 rename 覆盖）。
- `TestRegistryErrorToWire`（6）—— 每变体 `to_wire` 完整 dict 等值。
- `TestRegistryErrorFromWire`（9）—— 6 变体往返 + 未知标签 `ValueError` + 缺 `code`
  `KeyError` + **`"already_registered"` 默认标签被拒绝**（rename 覆盖的反向钉死）。
- `TestRegistryErrorUnion`（2）—— `get_args(RegistryError)` == 6 变体；分派表键集。
- `TestPackageSurfaceR88`（5）—— barrel 暴露 `RegistryError`；barrel **不**导出 5 个
  非冲突变体；**关键回归 `pkg.SessionMismatch is error_wire.SessionMismatch`**（barrel
  未被 registry_error 污染）；barrel 不导出 `registry_error_from_wire`；子模块可达性。

### 映射决策树 + 坑

**Rust → Python 映射**（与 R83 error_wire / R87 registration 同族决策）：
- `#[serde(tag = "code", rename_all = "snake_case")]` 内部标签 → 每变体 `to_wire`
  硬编码 `code` 键 + snake_case 标签；from_wire 在 `code` 上分派。
- **per-variant `#[serde(rename = "...")]`**（本轮新形态）→ `_WIRE_TAG_TO_VARIANT`
  字典的键 + `AlreadyRegistered.to_wire` 的 `code` 值都写死覆盖后的标签
  `"tool_already_registered"`，不依赖 `rename_all` 默认推导。
- 命名字段 struct 变体 → `@dataclass` + 类型化字段。
- 无 `#[serde(other)]` → 未知标签 `ValueError`（不是静默吞掉）。

**坑 1（关键）—— barrel `SessionMismatch` 命名冲突**：
R83 `error_wire` 已有 `SessionMismatch` 变体（`ToolErrorWire` 的一个 arm），且已导出
到 barrel `__all__` 并被测试文件 line 88 导入。R88 `registry_error` **也有**一个
`SessionMismatch` 变体（registry 层的 session 不匹配）。若 barrel 同时导出两者，第二个
`from ... import SessionMismatch` 会**遮蔽**第一个，破坏 R83 的 `pkg.SessionMismatch`
语义。**解决**：barrel **仅导出 `RegistryError` 联合别名**（不导出 6 个变体），严格
遵循 Rust `lib.rs` 第 72 行 `pub use registry_error::RegistryError`（lib.rs 也只重导出
枚举名，不重导出变体）。变体通过 `from minimax_code.tool_protocol.registry_error
import AlreadyRegistered` 等子模块路径访问。这与 R87 不同——R87 把
`Registered`/`Updated`/`Shadowed`/`Rejected` 变体导出到 barrel，因为它们**不与**
现有 barrel 名字冲突。**决策树**：变体名与 barrel 现有导出无冲突 → 导出（R87 模式）；
有冲突 → 仅导出联合，变体留子模块（R88 模式）。`TestPackageSurfaceR88.test_barrel_session_mismatch_is_error_wire_not_registry`
是这条决策的永久回归守卫。

**坑 2 —— 测试文件 E402**：
R88 是 R82 以来首个需要在测试文件中**从子模块**（非 barrel）导入的轮次（因为坑 1
使变体不在 barrel）。最初把 `from ... registry_error import (...)` 放在文件中间
（R88 测试块前），触发 ruff E402（module-level import not at top of file）。
pyproject 无 per-file-ignores。**解决**：把 registry_error import 块移到顶部 import
区（`registration` 与 `tool_types` 之间，isort 正确位置），中间测试块只保留 section
注释。`SessionMismatch as RegistrySessionMismatch` 别名避免与顶部 line 88 的 barrel
`SessionMismatch` 同名冲突。

**坑 3 —— ruff isort 拆分别名 import**：
`ruff --fix` 处理 I001 时，把带 `as` 别名的 `SessionMismatch as RegistrySessionMismatch`
拆成独立 import 块（isort 对 `as` 别名 import 的标准行为）。功能等价，无需干预。

### 验证

```
cd agent
uv run ruff check minimax_code/tool_protocol/registry_error.py \
                   minimax_code/tool_protocol/__init__.py \
                   tests/test_tool_protocol.py
# → All checks passed!

uv run pytest tests/test_tool_protocol.py -q
# → 424 passed in 0.56s   (R87 基准 396 + R88 新增 28)

uv run pytest -q
# → 2882 passed, 10 skipped, 1 warning in 105.81s
#    (唯一 warning: fastapi/httpx StarletteDeprecationWarning，预先存在，与 R88 无关)
```

### YAGNI 边界

- **不把 6 变体导出到 barrel**（仅联合）—— 严格遵循 Rust lib.rs 的 `pub use` 集；
  且避免 `SessionMismatch` 遮蔽 R83 error_wire 的同名变体。未来若需在 barrel 用某
  变体，按"无冲突才导出"决策树逐个评估。
- **不实现 `Display`/`__str__`** —— Rust `RegistryError` 在 crate 中**无** `Display`
  impl（不像 R83 `ToolErrorWire` 有 `Display` 用于日志）；它只是 wire DTO，错误信息
  由消费方（registry / frame 层，后续回合）自行格式化。YAGNI。
- **不加 `#[serde(other)]` 前向兼容臂** —— crate 用严格拒绝；未知标签是协议 bug，
  应该失败而非静默吞掉。与 `Method`/`HookKind`/`ToolScope`/`TransportKind` 一致。
- **不做变体间的共同基类 / Protocol** —— 6 个 dataclass 各自独立，联合别名 + 调度器
  已足够；引入 `Protocol` 会过度设计，违背 KISS。
- **不迁移 `frames.rs`（1549 行）/ `session_event.rs` / `turn_hook.rs` / `hook.rs`** ——
  留待后续回合按依赖顺序逐个落地；本轮只闭合 registry_error 这一个叶子模块。

### Commit

`feat(platform): R88 migrate registry_error.rs (RegistryError enum + per-variant rename override)`




## R89 — 迁移 hook.rs（HookEvent 枚举，tag=type，4 单元 + Custom 前向兼容，crate 首个 PascalCase 标签 + 首个 unit+struct 混合内部标签枚举）

锚点:R89-1 232a8ab

### 本轮目标

迁移 `grok-build/crates/common/xai-tool-protocol/src/hook.rs`（22 行，crate 里最小的
wire 模块之一）→ `agent/minimax_code/tool_protocol/hook.py`。这是 harness → tool 方向
的 hook 事件载荷枚举 `HookEvent`：harness 把这些事件投递给绑定到某个 session 的 tool
server —— 取消一个 in-flight 调用（`Cancel`）、暂停/恢复流式（`Pause`/`Resume`）、广播
会话结束（`SessionEnded`）。`Custom` 是给"尚未命名的 hook kind"留的前向兼容逃生舱，
未知 kind 走 `Custom` 内部（作为 `kind` 字段），不作为顶层 tag。

### 融合结论

R89 一回合落地 **3 个 crate 级 serde 首创**，全部集中在 `HookEvent` 这一个枚举上：

1. **`#[serde(tag = "type")]` —— `"type"` 标签键的首次出现**。此前所有内部标签枚举用
   的是 `code`（R83 `ToolErrorWire` / R88 `RegistryError`）、`kind`（R83 `McpBlock` /
   R86 `HookKind`）、`shape`（R83 `WireToolNotification`）。`"type"` 是 crate 第 4 个
   标签键名，至此四种标签键全部覆盖。
2. **无 `rename_all` —— wire 标签是 PascalCase 原样**（`"Cancel"`/`"Pause"`/`"Resume"`/
   `"SessionEnded"`/`"Custom"`）。这是 crate **首个** PascalCase 标签的内部标签枚举
   （R83/R86/R88 全是 snake_case）。前向兼容的关键设计选择正源于此：未知 hook kind
   走 `Custom.kind` 字段而非顶层 tag，所以枚举能保持闭集同时可扩展。
3. **4 个单元变体 + 1 个结构变体（`Custom`）—— 首个 unit+struct 混合的内部标签枚举**。
   R83/R88 的变体全部带命名字段，没有单元变体。单元变体在 wire 上只序列化成
   `{"type": "<PascalCase>"}`（无额外字段），结构变体 `Custom` 才带 `kind` + `payload`。

### 交付

- **`agent/minimax_code/tool_protocol/hook.py`**（167 行，新建）—— 5 个 dataclass 变体
  （`Cancel`/`Pause`/`Resume`/`SessionEnded` 单元 + `Custom{kind, payload}` 结构）+ 每变体
  `to_wire` + `HookEvent` 联合别名 + `_WIRE_TAG_TO_VARIANT` PascalCase 分发表 +
  `_UNIT_VARIANTS` frozenset + 模块级 `hook_event_from_wire` 分发器（联合不能承载
  classmethod，复用 R84 `jsonrpc_id_from_wire` / R87 `registration_outcome_from_wire` /
  R88 `registry_error_from_wire` 的同一模式）。
- **`agent/minimax_code/tool_protocol/__init__.py`**（barrel，5 处编辑）—— 标题/摘要段更新
  到 R89；新增 R89 条目说明 3 个 serde 首创；延迟列表减去 `hook`（仅剩
  `frames, session_event, turn_hook`）；导入块在 `handshake` 与 `ids` 之间插入
  `from .hook import HookEvent`；`__all__` 在 `handshake` 组后加 `"HookEvent"`（带注释
  说明仅联合、变体留子模块、Custom barrel 冲突同 R88 模式）。
- **`agent/tests/test_tool_protocol.py`**（+27 测试 / 6 类）——
  `TestHookEventWireTags`（5：4 个单元 + Custom 的 PascalCase 标签断言，含
  `"Cancel" != "cancel"` / `"SessionEnded" != "session_ended"` 反向钉死）、
  `TestHookEventToWire`（3：单元仅 `{"type"}`、Custom 完整字典、Custom payload 任意
  JSON 6 种类型往返）、`TestHookEventFromWire`（9：5 变体往返 + 未知标签 ValueError +
  snake_case 标签全部 ValueError + 缺失 `type` KeyError + Custom 任意 payload 往返）、
  `TestHookEventUnitVariants`（3：无字段、`Cancel() == Cancel()` 值相等、
  `_UNIT_VARIANTS == 4`）、`TestHookEventUnion`（2：`get_args(HookEvent) == 5`、
  分发表 5 个 PascalCase 键）、`TestPackageSurfaceR89`（5：barrel 暴露 `HookEvent`、
  不暴露单元变体、`pkg.Custom is error_wire.Custom` 关键回归、不暴露 from_wire、
  子模块全符号可达）。

### 映射决策树 + 坑

**坑 1 —— `Custom` barrel 名称冲突（R88 模式复用，预设解决）**：在写 `hook.py` **之前**
就读了 Rust `lib.rs` 第 58 行 `pub use hook::HookEvent;`（仅枚举名，不 re-export 变体），
并预见到 `hook.Custom` 与 R83 `error_wire.Custom`（barrel 已导出的 wire 变体）同名。决策：
barrel **仅** re-export `HookEvent` 联合（不导出 5 个变体），精确镜像 Rust `pub use` 集，
同时避免遮蔽 R83 的 `Custom`。测试用 `pkg.Custom is WireCustom`（`from error_wire
import Custom as WireCustom`）钉死这个回归 —— 这是 R88 的 `SessionMismatch` 冲突解法的
直接复用，证明该模式已稳定成"无冲突才导出"的标准决策树。

**坑 2 —— 测试文件 E402（R88 模式复用）**：R89 的 hook 子模块 import 块若放在文件中间
会触发 E402（模块级 import 不在顶部）。复用 R88 解法：把 `from .hook import (Cancel,
HookEvent, Pause, Resume, SessionEnded, hook_event_from_wire)` + `from .hook import
(Custom as HookCustom)` 放进顶部导入区（`error_wire` 与 `methods` 之间，正确 isort 位置），
带 R89 注释解释 barrel 冲突理由。文件中间用纯 `# ---- R89` 段注释占位。

**坑 3 —— I001 import 排序（ruff --fix 精确作用域）**：追加 R89 测试后 ruff 报 I001
（导入块未排序）。仅对 `tests/test_tool_protocol.py` 跑 `ruff check --fix`（精确路径，
绝不附带损害 93 个预先存在的 `M` 文件）→ 修复 1 个。重验测试文件 451 passed，import
重排未破坏逻辑。

**坑 4 —— 单元变体分发（`variant()` 实例化）**：单元变体无构造字段，`hook_event_from_wire`
用 `variant()` 空参实例化；`Custom` 结构变体单独分支读 `kind` + `payload`。`isinstance`
dispatch 依赖每个变体是**独立** dataclass（不共享空基类），否则 `Cancel()` 与 `Pause()`
类型不可区分 —— 这是为什么 4 个单元变体各写一个空 dataclass 而非共用基类。

**坑 5 —— 严格拒绝（无 `#[serde(other)]`）**：未知标签抛 `ValueError`，与 crate 严格拒绝
约定一致（R85 `Method` / R86 `HookKind`/`ToolScope` / R87 `TransportKind` / R88
`RegistryError` 全部如此）。前向兼容**不在**顶层加 catch-all 臂，而是在 `Custom.kind`
字段内承载 —— 测试用 `test_from_wire_rejects_snake_case_tags` 钉死（snake_case 标签必须
全部 ValueError，因为无 `rename_all`）。

### 验证

```
cd agent
uv run ruff check minimax_code/tool_protocol/hook.py \
                   minimax_code/tool_protocol/__init__.py \
                   tests/test_tool_protocol.py
# → All checks passed!

uv run pytest tests/test_tool_protocol.py -q
# → 451 passed in 0.60s   (R88 基准 424 + R89 新增 27)

uv run pytest -q
# → 2909 passed, 10 skipped, 1 warning in 106.53s
#    (R88 基准 2882 + R89 新增 27 = 2909，数据自洽)
#    (唯一 warning: fastapi/httpx StarletteDeprecationWarning，预先存在，与 R89 无关)
```

### YAGNI 边界

- **不把 5 变体导出到 barrel**（仅联合）—— 严格遵循 Rust lib.rs 的 `pub use hook::HookEvent`
  （仅枚举名）；且避免 `Custom` 遮蔽 R83 error_wire 的同名变体。这是 R88 `SessionMismatch`
  模式的直接复用，模式已稳定。
- **不实现 `Display`/`__str__`** —— Rust `HookEvent` 在 crate 中**无** `Display` impl；
  它只是 wire DTO，事件语义由消费方（frame 层 / hook registry，后续回合）自行格式化。YAGNI。
- **不加 `#[serde(other)]` 前向兼容臂** —— crate 用严格拒绝；前向兼容通过 `Custom.kind`
  字段内承载，不在顶层加 catch-all。未知标签是协议 bug，应该失败而非静默吞掉。
- **不做变体间的共同基类 / Protocol** —— 4 单元 + 1 结构各自独立 dataclass，联合别名 +
  分发器已足够；引入 `Protocol` 会过度设计，违背 KISS。单元变体共享空基类会破坏
  `isinstance` 分发（见坑 4）。
- **`payload` 用 `Any` 而非具体 JSON 类型** —— 镜像 Rust `serde_json::Value`（任意 JSON），
  不引入 `JsonValue` 联合别名（dict/list/str/num/bool/None）增加复杂度；`Any` 足够且与
  crate 语义 1:1。测试用 6 种 payload 类型覆盖。
- **不迁移 `frames.rs`（1549 行）/ `session_event.rs` / `turn_hook.rs`** —— 留待后续回合
  按依赖/大小顺序逐个落地（下一个候选：`session_event.rs` 3 符号，或 `turn_hook.rs`
  pub mod 无重导出）；本轮只闭合 hook 这一个叶子模块。

### Commit

`feat(platform): R89 migrate hook.rs (HookEvent enum — PascalCase tags + unit/struct mix)`
## R90 — 迁移 session_event.rs + turn_hook.rs 叶子（SessionEvent 联合 + ToolCallOutcome/SessionPhase 容错枚举 + TurnHookOutcome 严格叶，crate 首个 #[serde(other)] 前向兼容 + 首个 #[serde(default)] 字段级默认 + 严格↔容错转折点）

锚点:R90-1 f877069

### 本轮目标

迁移 `grok-build/crates/common/xai-tool-protocol/src/session_event.rs`（405 行）→
`agent/minimax_code/tool_protocol/session_event.py`，并先落地其依赖叶子
`grok-build/crates/common/xai-tool-protocol/src/turn_hook.rs` 的最小切片 →
`agent/minimax_code/tool_protocol/turn_hook.py`。`session_event.rs` 第 9 行 `use
crate::turn_hook::TurnHookOutcome;` 把 `TurnHookOutcome` 拉成硬依赖，所以 turn_hook
必须**先于** session_event 落地（哪怕只是最小叶子）。

`SessionEvent` 是会话生命周期事件联合：回合开始/结束（`TurnStarted`/`TurnEnded`）、
工具调用开始/完成（`ToolCallStarted`/`ToolCallCompleted`）、阶段切换（`PhaseChanged`）。
它作为 `Custom` 通知（`kind = "session_event"`）搭载在 `ToolNotificationFrame` 里
（frame 层在后续回合落地）。`ToolCallOutcome`/`SessionPhase` 是两个容错字符串枚举
（带 `Unknown` catch-all），`TurnHookOutcome` 是严格字符串枚举（无 catch-all，未知抛错）。

### 融合结论

R90 一回合落地 **5 个 crate 级 serde 首创** + **crate 的严格↔容错转折点**：

1. **`#[serde(other)]` 前向兼容 catch-all —— crate 首次**。三个站点同回合落地：
   `SessionEvent::Unknown`（单元臂，未知 `event_type` 落到这里）、`ToolCallOutcome::UNKNOWN`、
   `SessionPhase::UNKNOWN`（两个字符串枚举的 `other` 臂）。旧消费者遇到新协议值反序列化成
   `Unknown` 而非失败，消费者**必须**静默忽略 `Unknown`。这是 crate 前向兼容能力的首次
   引入，与 R85-R89 全程的严格拒绝约定形成对称补充。
2. **`#[serde(default)]` 字段级默认 —— crate 首次**。`TurnStarted.yolo_mode` 在 wire 省略
   时默认 `False`。（序列化始终发射该字段；`default` 仅影响反序列化。）
3. **`event_type` 标签键 —— 第 5 个标签键名**。此前：`code`（R83/R88）、`kind`（R83/R86）、
   `shape`（R83）、`type`（R89）。`event_type` 是 crate 第 5 个内部标签键名，至此
   `xai-tool-protocol` 的 5 个标签键全部覆盖。
4. **嵌套枚举字段 —— 首个枚举字段嵌套在另一枚举的结构变体里**。`TurnEnded.outcome` 是
   `TurnHookOutcome`、`ToolCallCompleted.outcome` 是 `ToolCallOutcome`、`PhaseChanged.phase`
   是 `SessionPhase` —— R83-R89 的枚举变体字段都是基础类型/嵌套结构，从未嵌套过另一个
   serde 枚举。
5. **结构主导混合 + 单元 catch-all —— R89 unit+struct 混合的逆向**。5 个结构变体 + 1 个
   单元 `Unknown`（catch-all），正好是 R89 的 4 单元 + 1 结构（`Custom`）的反向形态。

**严格↔容错转折点**：同回合落地的两个几乎相同的结果枚举行为**完全相反** ——
`TurnHookOutcome`（turn_hook 模块）**严格**，无 `#[serde(other)]`，未知值抛 `ValueError`
（镜像 crate 自测 `from_value::<TurnHookOutcome>("timeout").is_err()`）；`ToolCallOutcome`/
`SessionPhase`（session_event 模块）**容错**，带 `Unknown` catch-all，未知值返回 `UNKNOWN`。
这标志着 crate 从"全程严格拒绝"转向"按语义选择性容错"。

### 交付

- **`agent/minimax_code/tool_protocol/turn_hook.py`**（77 行，新建）—— `turn_hook.rs`
  的最小叶子（全模块 700 行，`pub mod turn_hook` 无 `pub use`，符号不经 barrel）。仅落地
  `session_event` 依赖的 `TurnHookOutcome`（3 成员 StrEnum + `from_wire` 严格抛错）+
  `TURN_HOOK_KIND` 常量（`"turn_hook"`）。`TurnHookRequest`/`BeforeTurnPayload`/
  `AfterTurnPayload`/`InjectionRole`/... 全部延后。
- **`agent/minimax_code/tool_protocol/session_event.py`**（342 行，新建）—— 6 个 dataclass
  变体（`TurnStarted`/`TurnEnded`/`ToolCallStarted`/`ToolCallCompleted`/`PhaseChanged`/`Unknown`）+
  每变体 `to_wire` + `SessionEvent` 联合别名（UP007 `A | B | ...`）+ 两个容错 StrEnum
  （`ToolCallOutcome`/`SessionPhase`，带 `UNKNOWN` catch-all）+ 5 个私有 `_xxx_from_wire`
  构造器 + `_EVENT_TYPE_HANDLERS` 分发表 + 模块级 `session_event_from_wire` 分发器
  （handler 为 None 时返回 `Unknown()` —— `#[serde(other)]` catch-all）。
- **`agent/minimax_code/tool_protocol/__init__.py`**（barrel，5 处编辑）—— 标题/摘要段更新
  到 R90；新增 R90 条目说明 5 个 serde 首创；延迟列表减去 `session_event`（仅剩
  `frames, turn_hook 余量`）；导入块在 `registry_error` 与 `tool_types`（测试侧）之间插入
  `from .session_event import (SessionEvent, SessionPhase, ToolCallOutcome)`；
  `__all__` 在 `RegistryError` 组后加三个名字（带注释说明 turn_hook 是 `pub mod` 无
  `pub use` 故 `TurnHookOutcome`/`TURN_HOOK_KIND` 留在 barrel 之外）。
- **`agent/tests/test_tool_protocol.py`**（+54 测试 / 9 类）——
  `TestTurnHookOutcomeR90`（6：成员值/snake_case/str 实例/严格 from_wire 往返/拒绝未知 3 路/
  `TURN_HOOK_KIND` 常量）、`TestSessionEventWireTags`（7：6 变体 `event_type` 标签断言 +
  snake_case 非 Pascal 反向钉死）、`TestSessionEventToWire`（6：`yolo_mode` True/False 都发射/
  嵌套枚举字段序列化为字符串/键穷尽）、`TestSessionEventFromWire`（13：5 变体往返 +
  `yolo_mode` 默认 False/显式 False/`TurnEnded` 全 outcome 往返/未知 outcome 抛错/
  `ToolCallCompleted` 未知 outcome→UNKNOWN/`PhaseChanged` 未知 phase→UNKNOWN/零值边界/
  多余字段忽略）、`TestSessionEventOtherArm`（4：3 个未知 event_type→Unknown + 字面
  `"unknown"` 往返 + Unknown 最小 wire）、`TestToolCallOutcomeEnum`（4）、
  `TestSessionPhaseEnum`（3）、`TestStrictVsTolerantContrast`（4：严格抛 vs 容错降级，
  同输入对置）、`TestPackageSurfaceR90`（7：barrel 暴露联合 + 2 枚举、不暴露 6 变体/
  turn_hook 符号/from_wire、子模块全符号可达）。

### 映射决策树 + 坑

**坑 1 —— turn_hook 依赖预计算（写 session_event 之前先切最小叶子）**：在写 `session_event.py`
**之前**就读了 Rust `session_event.rs` 第 9 行 `use crate::turn_hook::TurnHookOutcome;`，
确认 `TurnHookOutcome` 是硬依赖。决策：先落地仅含 `TurnHookOutcome` + `TURN_HOOK_KIND` 的
最小 `turn_hook.py`（700 行模块的其余部分延后），镜像 Rust 的模块结构
（`turn_hook::TurnHookOutcome` 是独立模块路径）。`session_event.py` 通过
`from minimax_code.tool_protocol.turn_hook import TurnHookOutcome` 子模块路径导入（绕过 barrel）。

**坑 2 —— turn_hook barrel 排除（`pub mod` 无 `pub use`）**：读了 Rust `lib.rs`
`pub mod turn_hook;`（第 26 行，**无** `pub use`）→ 决定 turn_hook 符号**不**进 barrel，
精确镜像 Rust。`session_event.py` 用子模块限定的导入；测试用
`test_barrel_does_not_export_turn_hook_symbols` 钉死（barrel 不得有 `TurnHookOutcome`/
`TURN_HOOK_KIND`）。这是 R89 的 "Custom 冲突才排除" 决策树的变体 —— 这里无冲突，但仍排除，
纯粹因为 Rust 不 re-export。

**坑 3 —— 严格 vs 容错枚举实现（StrEnum + `from_wire` 双形态）**：`cls(value)` 在未知值时
天然抛 `ValueError`。容错枚举（`ToolCallOutcome`/`SessionPhase`）的 `from_wire` 用
`try: cls(value) except ValueError: return cls.UNKNOWN`；严格枚举（`TurnHookOutcome`）的
`from_wire` 直接 `return cls(value)` 重新抛。复用 Enum 内置行为，零样板。测试用
`TestStrictVsTolerantContrast.test_same_input_opposite_behaviour` 钉死：同输入
`"future_value"`，严格抛、容错降级。

**坑 4 —— `#[serde(other)]` 调度器（返回 `Unknown()` 而非抛）**：`session_event_from_wire`
在 `_EVENT_TYPE_HANDLERS.get(tag)` 为 None 时返回 `Unknown()` 而非抛 `ValueError` ——
与 R85-R89 全程的严格拒绝调度器（`hook_event_from_wire`/`registry_error_from_wire` 抛
`ValueError`）**根本不同**。字典里有意省略字面 `"unknown"` 键，让它也落入 `Unknown()` 后备。
测试用 `test_unknown_event_type_becomes_unknown`（3 个未知 tag）+
`test_literal_unknown_tag_round_trips` 钉死。

**坑 5 —— I001 import 排序（ruff --fix 精确作用域）**：追加 R90 测试后 ruff 报 I001
（2 个导入块未排序）。仅对 `tests/test_tool_protocol.py` 跑 `ruff check --fix`（精确路径，
绝不附带损害 93 个预先存在的 `M` 文件）→ 修复 2 个。重验测试文件 505 passed，import
重排未破坏逻辑。

### 验证

```
cd agent
uv run ruff check minimax_code/tool_protocol/turn_hook.py \
                   minimax_code/tool_protocol/session_event.py \
                   minimax_code/tool_protocol/__init__.py \
                   tests/test_tool_protocol.py
# → All checks passed!

uv run pytest tests/test_tool_protocol.py -q
# → 505 passed in 0.75s   (R89 基准 451 + R90 新增 54)

uv run pytest -q
# → 2963 passed, 10 skipped, 1 warning in 104.76s
#    (R89 基准 2909 + R90 新增 54 = 2963，数据自洽)
#    (唯一 warning: fastapi/httpx StarletteDeprecationWarning，预先存在，与 R90 无关)
```

### YAGNI 边界

- **不迁移 `turn_hook.rs` 剩余 ~650 行** —— `TurnHookRequest`/`BeforeTurnPayload`/
  `AfterTurnPayload`/`InjectionRole`/... 全部延后；本轮只落地 `session_event` 硬依赖的
  `TurnHookOutcome` + `TURN_HOOK_KIND` 最小叶子。镜像 Rust 模块结构，余量按依赖顺序后续回合。
- **不迁移 `frames.rs`（1549 行）** —— crate 里最大的模块，留待后续回合单独落地。
- **不实现 `Display`/`__str__`** —— Rust `SessionEvent`/`ToolCallOutcome`/`SessionPhase`/
  `TurnHookOutcome` 在 crate 中**无** `Display` impl；它们只是 wire DTO。枚举值序列化
  用 `str(member)`（StrEnum 自带），事件语义由消费方（frame 层，后续回合）自行格式化。YAGNI。
- **不做变体共同基类 / Protocol** —— 5 结构 + 1 单元各自独立 dataclass，联合别名 + 分发器
  已足够；引入 `Protocol` 会过度设计，违背 KISS。
- **`Unknown` 不保留原始 `event_type`** —— 镜像 Rust `SessionEvent::Unknown`（不存原始值）；
  消费者需在反序列化**前**检查原始 JSON 才能记录未知类型。docstring 已明确此契约。
- **turn_hook 不进 barrel** —— 镜像 Rust `pub mod turn_hook;`（无 `pub use`）；turn_hook
  符号（`TurnHookOutcome`/`TURN_HOOK_KIND`）仅子模块限定的访问。即便无 barrel 冲突也排除，
  纯粹遵循 Rust 重导出集。

### Commit

`feat(platform): R90 migrate session_event.rs + turn_hook leaf (serde(other) forward-compat + serde(default) field + strict↔tolerant turning point)`


## R91 — 迁移 turn_hook.rs 核心余量（serde default=fn + tag=phase + deny_unknown_fields，3 个 crate-first serde 形态）

锚点:R91-1 cef3d42

### 本轮目标

R90 只落了 `turn_hook.rs` 的最小叶子（`TurnHookOutcome` + `TURN_HOOK_KIND`），因为
`session_event::TurnEnded` 硬依赖 `TurnHookOutcome`。但 `turn_hook.rs` 是 turn 级 hook
协议的核心模块，剩余 **9 个符号 + 2 helper fn + 4 const** 构成完整的 request/reply
协议闭环（`BeforeTurnPayload` / `AfterTurnPayload` / `TurnHookRequest` / `HookReply` /
`HookInjection` / `InjectionRole` / `TurnControl` / `AfterTurnAckPayload` /
`AfterTurnAckStatus`）。R91 补全这些核心符号，**闭合整个 turn_hook 模块**，并落地 **3 个
crate 首次出现的 serde 子形态**（`#[serde(default = "fn")]` / `#[serde(tag = "phase")]` /
`#[serde(deny_unknown_fields)]`）。

### 融合结论

`turn_hook.rs` 余量**完整迁移**到 `turn_hook.py`（从 R90 的 77 行扩展到 ~530 行）。9 符号 +
2 helper + 4 const 全部落地。严格↔容错转折点在 turn_hook 模块内**保持一致**：所有
turn_hook 枚举（`TurnHookOutcome`/`InjectionRole`/`TurnControl`/`AfterTurnAckStatus`）+
`TurnHookRequest` 本身都是**严格**的（无 `#[serde(other)]`，未知值 `raise ValueError`），
延续 R90 转折点；`session_event` 保持容错（`#[serde(other)]`）。3 个 crate 首次 serde 形态
按 Rust 语义精确映射到 Python dataclass + from_wire 模式。

### 交付

- `agent/minimax_code/tool_protocol/turn_hook.py`（~530 行，从 R90 的 77 行扩展）：
  落地 9 符号 + 2 helper fn + 4 const。ruff **All checks passed!**（一次通过，无需 --fix）。
- `agent/tests/test_tool_protocol.py`：R91 导入块扩展（24 个 turn_hook 符号，isort
  order-by-type 排序：CONST → Classes → functions）+ **13 个 R91 测试类**（54 个测试方法）。
  覆盖常量+双层 kind/phase、BeforeTurnPayload（round-trip + 4 serde default）、
  AfterTurnPayload（round-trip + skip_serializing_if + opaque context + strict outcome）、
  InjectionRole、TurnControl、AfterTurnAckStatus、HookInjection（deny_unknown_fields）、
  AfterTurnAckPayload、HookReply（Default{} + round-trip + deny + 嵌套）、TurnHookRequest
  （Before/After arm round-trip + 未知 phase raise + phase≠kind）、deny_unknown_fields 专门
  对比、crate-first serde 形态对比、包表面 R91（barrel 仍不导出 turn_hook）。
- 验证：ruff All checks passed; `test_tool_protocol.py` **559 passed**（R90 基准 505 +
  R91 新增 54）；完全回归 **3017 passed, 10 skipped**（R90 基准 2963 + R91 新增 54）。

### 映射决策树+坑

**映射 1 —— `#[serde(default = "fn")]`（crate 首次）→ dataclass 字段默认值 = 常量 + 私有
helper fn 保留语义来源**。Rust 的 `default = "default_session_relationship"`（指向命名函数）
在 Python 用 `field(default=DEFAULT_SESSION_RELATIONSHIP)`（字段默认值 = 常量），同时保留
私有 `_default_session_relationship()` / `_default_schema_version()` 两个 helper fn —— 它们
返回同名常量，纯粹是为了**镜像 Rust 源码的 default-fn 语义来源**（让"默认值来自一个具名函数"
这个事实在 Python 侧也可追溯，而非凭空一个魔法字符串）。`BeforeTurnPayload` 的
`session_relationship` / `schema_version` 是 crate 首次落地此形态的字段。测试用
`test_session_relationship_default_fn` / `test_schema_version_default_fn` 钉死：wire 省略 →
`"primary"` / `"1.0"`，且 helper fn 返回同值。

**映射 2 —— `#[serde(tag = "phase", rename_all = "snake_case")]`（第 6 个 tag-key 名）→
wrapper dataclass（各持 payload 引用）+ to_wire 展开 payload 加 phase 标签**。前 5 个
tag-key：`code`(R83) / `kind`(R83) / `shape`(R83) / `type`(R89) / `event_type`(R90)。R91
加 `phase`（第 6）。Rust 的 `TurnHookRequest` 是内部标签枚举（`Before`/`After` tuple variant
包裹 payload），payload 字段在 wire 上**扁平化到顶层**与 `phase` 并列。Python 映射为两个
wrapper dataclass（`TurnHookRequestBefore` / `TurnHookRequestAfter`，各持 `payload` 引用），
`to_wire` 展开内部 payload 到顶层 + 加 `phase` 标签（`"before"`/`"after"`），避免字段重复；
`turn_hook_request_from_wire` 读 `phase` 调度到对应 wrapper。`TurnHookRequest` 是
`TurnHookRequestBefore | TurnHookRequestAfter` 别名。测试用
`test_before_arm_round_trip` / `test_after_arm_round_trip` 钉死：wire 无嵌套 `payload` key，
phase 值正确。

**坑 1 —— 双层 kind/phase 易混淆（R91 签名级陷阱）**：`BEFORE_TURN_KIND="before_turn"` /
`AFTER_TURN_KIND="after_turn"` 是**外层** `HookEvent::Custom { kind, payload }` 的 kind 值
（harness→tool 事件流的 kind）；而 `TurnHookRequest::Before`/`After` 的**内层** `phase` 标签
是 `"before"`/`"after"`（**无** `_turn` 后缀，`rename_all = "snake_case"`）。两者**绝不能
混淆**。测试 `test_outer_kind_differs_from_inner_phase` 专门钉死：
`BEFORE_TURN_KIND != "before"`、`AFTER_TURN_KIND != "after"`。to_wire 里加 phase 标签时
注释 `# NOT BEFORE_TURN_KIND ("before_turn")` 防呆。

**映射 3 —— `#[serde(deny_unknown_fields)]`（crate 首次）→ from_wire 检查多余键 frozenset →
raise ValueError**。Rust 的 `deny_unknown_fields`（拒绝未知字段）在 Python 用 from_wire 模块
函数检查 `set(data) - _KNOWN_FIELDS`，多余则 `raise ValueError`。`HookInjection`（role+content）
和 `HookReply`（injections+control+after_turn_ack）是 crate 首次落地此形态的结构。其余结构
（`BeforeTurnPayload`/`AfterTurnPayload`/`AfterTurnAckPayload`）容忍未知键（默认行为）。
测试用 `TestDenyUnknownFieldsContrastR91`（4 个测试）专门对比：HookInjection/HookReply 拒绝
多余键 vs BeforeTurnPayload/AfterTurnPayload 容忍。

**映射 4 —— 严格枚举 from_wire（`cls(value)` 重抛）**：4 个 turn_hook 枚举
（`InjectionRole`/`TurnControl`/`AfterTurnAckStatus`/`TurnHookOutcome`(R90)）全部严格 ——
`from_wire` 直接 `return cls(value)`，未知值由 StrEnum 内置 `ValueError` 抛出，无 try/except。
这与 R90 `ToolCallOutcome`/`SessionPhase`（容错，`try/except → UNKNOWN`）**根本不同**，延续
R90 严格↔容错转折点。`AfterTurnAckStatus` 是唯一**没有** `#[non_exhaustive]` 的 turn_hook
枚举（变体集封闭：Enqueued/Failed/Skipped）。测试每个枚举都有 `test_strict_unknown_raises`。

**映射 5 —— `skip_serializing_if = "Option::is_none"` → to_wire 中 `if x is not None`**。
`AfterTurnPayload.cancellation_category` / `.cancellation_context`、`AfterTurnAckPayload.error_message`、
`HookReply.after_turn_ack` 都是 `Option` + skip。Python 映射为 `X | None`，to_wire 用
`if x is not None: out["k"] = x`。`HookReply` 的 Default（`{}`）→ no-op：`injections=[]`、
`control=TurnControl.AUTO`（Default）、`after_turn_ack=None`（省略）。测试
`test_default_is_noop` / `test_default_to_wire`（`{"injections": [], "control": "auto"}`）钉死。

**坑 2 —— `cancellation_context` 是 `serde_json::Value`（opaque）→ `object | None`**。Rust 的
`serde_json::Value` 是任意 JSON 值，Python 映射为 `object | None`（opaque 透传，**不**
stringify）。from_wire 直接 `data.get("cancellation_context")`（已是 dict/list/primitive），
to_wire 直接放进 out。测试 `test_cancellation_context_opaque_round_trip` 钉死：嵌套
`{"nested": [1, 2, {"x": True}]}` 原样往返。

**坑 3 —— turn_hook barrel 规则（`pub mod` 无 `pub use` → 不进 barrel）**。Rust `lib.rs`
的 `pub mod turn_hook;` 没有 `pub use`，所以 turn_hook 符号**不在 barrel 重导出集**。所有
turn_hook 符号通过 `from minimax_code.tool_protocol.turn_hook import ...` 直接子模块导入。
R91 扩展了 turn_hook 符号集，但 barrel `__init__.py` **无需改动**（turn_hook 整个模块都不
进 barrel，R90 已正确）。`session_event.py` 通过 `from minimax_code.tool_protocol.turn_hook
import TurnHookOutcome` 导入 R90 依赖，R91 扩展不影响此导入。测试
`TestPackageSurfaceR91.test_barrel_still_excludes_turn_hook_symbols`（11 个符号全部不在 barrel）
+ `test_submodule_exposes_r91_core`（17 个符号全部在子模块）钉死。

**坑 4 —— `type: ignore[arg-type]`（ack_raw: object 传给期望 dict 的 from_wire）**。
`hook_reply_from_wire` 读 `data.get("after_turn_ack")`（类型 `object | None`），传给期望
`dict[str, object]` 的 `after_turn_ack_payload_from_wire`。mypy 会报 arg-type。用
`# type: ignore[arg-type]` 显式抑制（wire 层已知结构，运行时 from_wire 内部会做 `str()`/
`int()` 强转）。这是 wire DTO 层的常见妥协（参考 R90 `_turn_ended_from_wire` 的 outcome 透传）。

### 验证

```
cd agent
# 精确作用域 ruff（绝不附带损害 93 个预先存在的 M 文件）
uv run ruff check --fix tests/test_tool_protocol.py
# → All checks passed!

# turn_hook.py 在 R91 实现阶段已单独通过（一次过，无需 --fix）
uv run ruff check minimax_code/tool_protocol/turn_hook.py
# → All checks passed!

uv run pytest tests/test_tool_protocol.py -q
# → 559 passed in 0.69s   (R90 基准 505 + R91 新增 54)

uv run pytest -q
# → 3017 passed, 10 skipped, 1 warning in 103.43s
#    (R90 基准 2963 + R91 新增 54 = 3017，数据自洽)
#    (唯一 warning: fastapi/httpx StarletteDeprecationWarning，预先存在，与 R91 无关)
```

### YAGNI 边界

- **不迁移 `frames.rs`（1549 行）** —— crate 里最大的模块（tool-server frame 协议，
  `SessionEvent` 的消费者层），留待后续回合单独落地。turn_hook 闭合后，frames 是
  `xai-tool-protocol` crate 剩余的唯一大块。
- **不实现 `Display`/`__str__`** —— Rust 的 turn_hook 类型在 crate 中**无** `Display` impl；
  它们只是 wire DTO。枚举值序列化用 `str(member)`（StrEnum 自带），payload 用 `to_wire()`
  dict。语义格式化由消费方（frame 层，后续回合）自行处理。YAGNI。
- **不做变体共同基类 / Protocol** —— `TurnHookRequestBefore`/`After` 各自独立 wrapper
  dataclass，联合别名 + `turn_hook_request_from_wire` 分发器已足够；引入 `Protocol` 会
  过度设计，违背 KISS。
- **`TurnHookRequest` 不用 `Enum`** —— Rust 的 `TurnHookRequest` 是内部标签枚举（tuple
  variant 包裹 payload），Python 无需用 `Enum`（Enum 难以携带异构 payload）。两个 wrapper
  dataclass + 别名联合是更直接的映射。
- **`_default_session_relationship`/`_default_schema_version` 保持私有** —— 它们纯粹是
  Rust default-fn 的语义镜像，外部不应调用；字段默认值 = 常量已足够。加 `_` 前缀标记私有。
- **turn_hook 不进 barrel** —— 镜像 Rust `pub mod turn_hook;`（无 `pub use`）；所有 turn_hook
  符号仅子模块限定的访问。即便无 barrel 冲突也排除，纯粹遵循 Rust 重导出集（R90 已建立此规则，
  R91 扩展符号集不变）。
- **`AfterTurnAckStatus` 不加 `#[non_exhaustive]` 语义** —— 镜像 Rust 源码（唯一无
  non_exhaustive 的 turn_hook 枚举，变体集封闭）。Python 侧无需特殊标记，严格 from_wire
  已封闭。

### Commit

`feat(platform): R91 migrate turn_hook.rs core (serde default=fn + tag=phase + deny_unknown_fields)`
