# Changelog

All notable changes to MiniMax Code are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.2.0] - 2026-08-23

### Fixed — 全局审计修复：跨会话串台、幽灵时间线、技能契约断裂（14 项，5 刀 + P3）

起因于用户实测反馈「代码审查的后台对话信息会输出到当前会话里」。全局检索发现根因是**后端把所有 WebSocket 事件广播给所有客户端，而前端 store 大多不校验 session 归属**——任何后台任务（技能调用、定时任务、其他标签页）的流式事件都会污染当前打开的会话。本次以 5 刀主修复 + P3 杂项闭环全部 14 项审计问题。

- **第 1 刀｜chat 五订阅 session 守卫（web）**：`message_chunk` / `tool_call` / `tool_result` / `agent.status` / `ask_user` 五个订阅全部加 `isCurrentSessionEvent` 守卫（`session_id === currentSessionId` 才放行）；`send()` 回填竞态守卫（响应返回时 session 已切换则不回填本地占位）。导出 `disposeChatSubscriptions()` 供测试拆卸。
- **第 2 刀｜teamRunStore envelope 解包（web）**：`agent.team_progress` 事件处理把广播 envelope 直接 cast 成 payload——所有字段 undefined 产生幽灵条目。修复为读 `env.data`，空 data 跳过。
- **第 3 刀｜invokeSkill 契约对齐（web，wire/output/mock 三方）**：后端要求 `skill_id` + `request` 必填、特殊路由键（`diff`）在 params 顶层——typed 层旧实现只传 `{skill_id, request}` 导致代码审查技能收到 dict-repr 而非指令。修复：对象 args 平铺到 wire 顶层；`codeReviewStore` 读取平铺 reply（`text`/`output`/`comments`/`stats`）；mock backend 同步说 wire 形状。
- **第 4 刀｜runStore session 过滤 + stale 守卫（web）**：`run.created` 按 session 过滤；`run.completed` 孤儿守卫（未见过 created 且 session 不匹配则丢弃，不再追加幽灵时间线条目）；`loadRuns` 加 stale 守卫 + replace 语义（切会话竞态不再混合两个会话的 runs）。
- **第 5 刀｜scheduler 真实 prompt runner（agent）**：定时任务此前只跑技能/工作流模板，`prompt` 类型任务不产生任何输出。修复：真实 prompt 任务走 `agent.send_message` 往指定 session 发消息（`_dispatch_prompt_job`），新增 `025` migration 给 `scheduled_jobs.result_persist` 持久化结果列 + `tasks.result` IPC 返回最新结果；前端 `ProgressPanel` 展示 `done` 任务的 result 摘要。
- **P3 杂项**：`permission.request` 五文件补 `session_id` 归属（前端弹窗不再跨会话误弹）；`teams.spawn` 系列 handler 的 `emit_event=ctx.emit` 净化（metadata keyword-only 签名对齐）；技能调用结果落库（`skill_runs` 表此前不写）；ProgressPanel 最小字号 11px 合规（a11y 规则）。

### Added — 回归测试网（防串台复发）

14 个新回归测试 + 存量测试适配：`session-guards.test.ts`（9 测试覆盖第 1/2/4 刀——事件总线注入模式，双模块路径 mock）、`typed-skills.test.ts`（5 测试覆盖第 3 刀——wire 平铺 + mock reply 形状）、`progress-panel.test.tsx` 补 result 展示断言；`chat-watchdog` / `chat-thinking-count` 存量测试适配 session 守卫（事件注入补 `session_id`）。

**质量数字**：pytest **10195 passed / 15 skipped**；vitest **737/737（95 文件）**；ESLint 0/0；ruff 全绿；tsc 干净。

## [1.1.3] - 2026-08-23

### Fixed — 上下文指示器恒显示 0（tokens 从未持久化）

用户实测反馈：会话底部的 context 指示器长聊后仍显示 `0/1M`。取证（真实库只读）发现**整个数据库所有消息的 `tokens_in`/`tokens_out` 均为 0、`metadata` 均为 NULL**——不是显示问题，是持久化链路三层叠加缺陷：

- **持久化副本缺 metadata（core，主因）**：常规完成路径持久化的是 `response.message`，而 `response.metadata`（`tokens_in`/`tokens_out`/`thinking_count`）只挂在 LLMResponse 对象上、从未合并进持久化消息——唯一带 metadata 落库的路径是迭代超限截断。实时 done chunk 其实带 usage（前端内存有值），但任何刷新 / 切会话都从 DB 读 → 归零。修复：持久化时写入带 metadata 的**副本**（`{**assistant_msg, "metadata": response.metadata}`），原消息保持协议干净（不向下一轮 LLM API payload 泄漏非协议键）。
- **DAO tokens 列从未写入（builtins `_persist`）**：`msg_dao.create(...)` 调用从未传 `tokens_in`/`tokens_out` 参数（默认 0）——`session.stats` 的 `SUM(tokens_in)` 汇总也恒 0。修复：从 metadata 提取（`int()` 容错）镜像到专用列。
- **指示器聚合语义错误（web ContextIndicator）**：旧实现**累加**所有 assistant 消息的 `tokens_in`——但 `tokens_in` 是该次 LLM 调用看到的完整 prompt（已含全部历史 + system prompt），累加会把历史重复计 N 次、系统性虚高。修复：取**最后一条**带 usage 的 assistant 消息的 `tokens_in + tokens_out`（最近一次调用的完整上下文占用 + 本轮输出，即进入下一轮时的上下文大小）。

新增回归测试：`test_loop_persists_usage_metadata_on_regular_completions`（副本带 metadata 且 LLM payload 无污染）、`test_agent_send_message_persists_token_usage`（端到端落库断言）、前端 2 个语义测试（取最新而非累加 / 含 tokens_out）。

## [1.1.2] - 2026-08-23

### Fixed — 长会话「每轮回复都带收尾表态」的历史口癖污染

用户实测反馈：同一窗口多轮聊天后，后续每条回复都以「收到，立刻收尾，不开新工作」开头。取证（真实库只读）显示两层根因——v1.1.0 旧 nudge（12 轮预算 + 诱导文案）曾让模型在回复中确认收尾，这些确认随消息**持久化**；v1.1.1 部署后 nudge 两触发器（90% 窗口压力 / 200 轮安全阀）均已够不着，但模型仍在**模仿历史中的旧表态**（会话 38 条消息仅 14KB，远低于 200k 窗口的 90% 门槛；iteration 0 无注入却已带「收到」口癖即为铁证）。三层修复：

- **stale-note advisory（builtins）**：`_build_system_prompt_extra` 无条件前置恒真声明——历史中「收尾 / 预算归零 / 不开新工作」类表态针对的是已过期的临时系统注记，约束不再适用，禁止模仿、按当前消息本身的诉求作答。清除存量污染，~60 token/轮。
- **nudge 防复述指令（core）**：两个模式分支的 directive 统一追加「This note is informational: do not mention it, acknowledge it, or announce wrapping up in your reply」——nudge 本身 ephemeral 不落库，但模型的应答会落库；此指令从源头阻断新一轮污染。
- **context-pressure nudge 节流 + 滞后修正（core）**：`context_nudge_fired` run 级标志——上下文压力提醒每 run 只注入一次（重复注入只会训练模型每轮开头复读确认）；`compacted_this_iteration` 守卫——刚执行完压缩的那轮跳过提醒（`last_prompt_tokens` 仍是压缩前的过时值，压缩已缓解压力）。迭代安全阀（剩余 ≤2 轮）保持每轮注入（构造上最多 2 次，是最终交接信息）。

## [1.1.1] - 2026-08-23

### Added — ask_user 工具全链路（v1.1.1）

补齐「模型能力与工具目录不匹配」的最后一块：模型有澄清意愿却没有提问通道，现在有了。

- **agent 侧（core + builtins）**：新内置工具 `ask_user`——模型以结构化问卷（`summary` + 多个 `question`：单选 `options` / 多选 `multi` / 自由文本 `allow_other`）向用户提问，循环在 `_execute_tool_call` 层**挂起**（`ASK_USER_TIMEOUT_S=600s`），经模块级路由表 `_ASK_USER_ROUTES` 唤醒，答案作为 tool result 回填继续循环。超时 / 取消 / 会话清理均有兜底。第 10 个内置工具。
- **IPC**：`agent.answer_user`（第 170 个 IPC 方法）——按 `request_id` 提交 `answers`，校验必答项与选项合法性，唤醒挂起循环。
- **流式事件**：`agent.ask_user`（第 16 个事件）——挂起瞬间广播完整问卷 payload；三处契约（registry ↔ `web/src/types/ipc.ts` ↔ `docs/ipc-contract.md`）由 `test_ipc_contract_doc.py` 锁死同步。
- **前端问答卡（chat）**：`AskUserCard` 内联渲染于消息流——单选 radio、多选 checkbox（上限校验）、「其他」自填输入、必答校验、禁用态回显；`chatStore.answerAskUser()` 走 TypedIPC 全链路。11 个组件测试 + 10 个 store 测试。

### Changed — 迭代预算与交接提醒重构（v1.1.1）

回应「1M 上下文配 12 轮×5 块太保守」：预算本质是**失控保险丝**，不是任务天花板。

- **`max_iterations` 12 → 200**：单块默认预算提升 16.7 倍；env 旋钮 `MINIMAX_MAX_ITERATIONS`（clamp [1, 10000]，垃圾值回退 200 + warning）。1M 窗口 + auto-continue 块循环下，实际任务上限 = 200 × 块数，预算只兜「工具死循环」。
- **subagent 默认迭代 8 → 50**：五处对齐（orchestrator/subagent.py、tools/subagents.py、skills/runtime.py、team_orchestrator.py、dao/agents.py）。DB schema DEFAULT 8 保留（SQLite 无法 ALTER 列 DEFAULT；生产插入全走 DAO，重建表成本高收益零）。
- **nudge 重写为「诚实交接」**：双触发——(a) context 压力（prompt 用量 ≥90% 窗口，压缩闸门之后的兜底提醒）；(b) 安全阀（剩余 ≤2 轮）。**模式感知文案**：auto-continue 开 → 「Keep working... runtime automatically continues with a fresh block」；关 → 诚实交接三段式（done / remaining / next step）。旧文案「produce a final answer now」已删除——它诱导模型交纯文本 final → `truncated=False` → auto-continue 只续跑 truncated runs，**续跑被提醒亲手杀死**，这是 v1.1.0「未触发 auto-continue」的根因。新措辞契约写入代码注释与 `docs/agent-core.md`。
- **文档**：`docs/agent-core.md`（循环预算、Handoff nudge 段、config 表）、三份 CLAUDE.md（170 方法 / 36 前缀 / agent.* 12 / 事件 16）、环境变量表补 `MINIMAX_MAX_ITERATIONS`。

**质量数字**：pytest 10180 passed / 15 skipped（+24 新测试）；vitest 720/720（93 文件，+21）；ESLint 0/0；ruff 全绿；tsc 干净。`tests/app.test.tsx` 补 fetch stub——本机 8765 活 agent 会劫持 `/health` 探针把 IPC client 切到 HTTP 模式，mock 断言全灭还白耗真实 LLM 调用。

## [1.1.0] - 2026-08-20

### Added — 从固定轮数天花板到 context 驱动的长任务循环（v1.1.0）

把「12 轮跑满即强杀」的单块硬顶，升级为**块预算 + 压缩 + 续跑**的长任务三件套：

- **P0 管道**：
  - `TurnAbortReason.MAX_ITERATIONS`（lifecycle）：截断与用户取消分离——生命周期观察者可区分「预算耗尽」与「停止按钮」，二者不再共享 INTERRUPTED。
  - `context_window` 接线（builtins）：send-message 构造 `AgentConfig` 时从模型目录解析真实上下文窗口（随 `model.set_current` 同步）；`compaction_threshold=0.8` 默认启用——压缩闸门从「永不打开」变为真实生效。
  - 循环内 compaction 管道合并（core）：每轮 LLM 调用前用上一轮**真实 token 用量**对照 `should_compact` 决策、`compact_history`（keep_recent=4）执行，`compactions` 计数进 `AgentRunResult` 与消息 metadata——长任务不再因历史膨胀而撞墙。
- **收敛与续跑**：
  - 收敛提醒（core）：预算只剩 ≤2 轮时向 LLM 追加 ephemeral user note（不持久化、不进历史），催促收尾出最终答案——从「被动截断」变「主动收敛」。
  - 块预算语义 + `agent.continue_run`（第 169 个 IPC 方法）：`max_iterations` 是**单块**预算；截断 run 的 assistant 消息带 `truncated: true`，新 handler 校验 session 最近 run 确为截断后 mark `continued` 并以固定 `[continue]` prompt 复用 send-message 全链路（7 个 handler 测试）。
  - 前端续跑闭环：truncated 消息渲染「迭代预算已用尽」badge + 压缩次数 + 「继续执行」按钮（chatBusy 禁用）→ `chatStore.continueRun` → IPC → WS 流式新气泡（6 个组件测试）；reply envelope 新增 `truncated` 字段。
- **可观测与自动化**：
  - `HookEvent` 新增 `pre_loop_iteration` / `post_loop_iteration`（v1.1.0 第 7/8 个 hook 事件）：pre 在每轮 LLM 调用前、post 在工具批次后（final-answer 轮不 fire，避免冗余尾事件）；纯通知 payload 带 0-based `iteration`——长任务观察者获得轮级粒度（echo-stdin 端到端测试）。
  - **auto-continue（goal/loop 最小形态）**：`MINIMAX_AUTO_CONTINUE=1` + `MINIMAX_AUTO_CONTINUE_MAX_BLOCKS`（默认 5）——send-message 管线在块截断后自动注入续跑 prompt 进下一块，直到模型出最终答案 / 用户取消 / 块上限；块间取消即时生效（`_ACTIVE_RUNS` 全程在册）。reply envelope 新增 `blocks` / `compactions`；run 记录 metadata 同步累计 iterations / compactions / blocks（遥测按 send-message 聚合）。9 个新测试覆盖 env 解析与块循环（禁用单块 / 续到答案 / 上限耗尽仍 truncated 交还手动按钮 / 取消截停）。
- **文档**：`docs/ipc-contract.md`（send_message envelope + continue_run 行）、`docs/agent-core.md`（块预算语义 + 六事件 hooks 表 + config 表 4 个新旋钮）。

**质量数字**：pytest 10156 passed / 15 skipped（+24 新测试）；vitest 699/699（91 文件，+6）；ESLint 0/0；ruff 全绿；tsc 干净。

## [1.0.0] - 2026-08-21

### 1.0.0 总览（R51 汇总 · R54 转正——0.12.0 → 1.0.0，十里程碑 54 轮迭代收官）

1.0.0 是 v0.11.0 之上连续八个功能版本 + rc 收口 + 正式发布的成果。各版本详录见下方分节，此处一屏总览：

| 里程碑 | 版本 | 主题 | 代表性成果 |
|--------|------|------|-----------|
| M1 | 0.12.0 | 生产单进程模式 | agent 同源托管 web/dist、production-mode e2e、文件日志、部署文档 |
| M2 | 0.13.0 | 性能基线 | 五项基线（冷启动 / 索引 / 首屏 / 长会话 / WS 重放）+ 预算硬断言进 CI |
| M3 | 0.14.0 | 安全加固 | CORS 白名单、RPC 畸形防护、权限出厂默认、`-m security` 151 测试 |
| M4 | 0.15.0 | 数据可移植 | `data.export` / `data.import`（全事务替换式）/ `data.backup`（在线快照）+ 灾难恢复 e2e |
| M5 | 0.16.0 | UI 文案统一 | `strings.ts` 中文单一来源 1100+ 行，全量英文清零 |
| M6 | 0.17.0 | 无障碍与键盘 | WAI-ARIA 焦点陷阱、roving tabindex、icon 按钮可访问名静态审计、WCAG AA 60 断言 |
| M7 | 0.18.0 | 文档完备 | README 重写、198 行用户手册、IPC 文档对账 + 双向守护测试、五文档全量对账 |
| M8 | 0.19.0 | 诊断工具 | `diag.export` 脱敏诊断包（无 keyring 值 / 无绝对用户路径，故障态降级可用）+ 前端导出入口 |
| M9 | 1.0.0-rc | 质量收口 | 四面全量回归、R32 断言债清偿、flaky 清零（e2e 三连跑）、rc 发布性能数字复测 |
| M10 | 1.0.0 | 正式发布 | 发布公告 `docs/release-1.0.0.md`（九条验收标准逐条对账）、README 徽章终稿、版本转正 |

**1.0.0 发布质量数字**：pytest 10129 passed / 15 skipped；vitest 622/622（77 文件）；e2e 21/21（10 spec，三连跑 31.0 / 31.3 / 31.7 s 零失败）；ESLint 0 errors / 0 warnings；`-m security` 151 测试。
**性能基线（R50 发布复测）**：冷启动 median 2.589 s（预算 ≤5 s）；首屏 JS 136.7 KB / CSS 8.2 KB gzip（预算 200 / 50 KB）；500 条消息长会话仅挂载 34 行 DOM；WS 断连重放 ≤512 条；SQLite 热点查询零裸表扫。详见 `docs/performance-baseline.md`。
**规模**：168 个 IPC 方法（36 命名空间）、24 张实体表（+FTS/vec 虚表，25 个迁移）、29 个前端 stores、设置页 14 tab、检查器 11 tab、12 个内置技能。

### Changed
- 版本号 1.0.0-rc.1 → **1.0.0**（R54 · 54 轮收官：6 处代码位 + CLAUDE.md / AGENTS.md 版本行 + `uv lock`；README 版本行与徽章已于 R53 终稿）。stable bump 将 `test_installed_semver_live_metadata_is_parseable` 断言推进为 `v.pre is None`——稳定版无 prerelease 即契约（rc.1 时代断言 `"rc.1"`）。发布公告见 `docs/release-1.0.0.md`。

## [1.0.0-rc.1] - 2026-08-21

### Fixed — 1.0.0-rc 质量收口（v0.19.0 后，R49–R50）
- **e2e 断言债清偿（R49）**：R32 文案中文化迁移时 e2e 断言未同步，挖出 8 处断言债修复——placeholder 英文残留（smoke-chat / smoke-thinking-count / smoke-subagent / production-mode 共 5 处改中文）、`getByLabel("决策")` / `getByRole("移除")` 默认子串匹配与中文化 aria-label 撞车（2 处加 `{ exact: true }`）、codebase 统计文案 `Files:/Chunks:` → `文件：/分块：` 与 `Sources` → `来源`、subagent 状态 `completed|failed` → `已完成|失败`。附带发现 production-mode 用的 `web/dist` 过期 4 小时（R32 之前构建），强制重建后验证诊断功能与中文文案均入包。
- **streaming-follow e2e 竞态修复（R50）**：`smoke-chat` streaming 跟随断言偶发收到距离 941（期望 ≤50）——根因是种子消息后虚拟化行高持续变化，`scrollHeight` 抖动触发原生 scroll 事件把 `useSmartScroll` 的 following 标志翻 false，随后 chunk 到达走"不跟随"分支。修法为 expect.poll 每轮重设 `scrollTop` 并重发 scroll 事件、断言钉住距离 ≤50 才放行——三连跑 21/21 × 3（31.0 / 31.3 / 31.7 s）零失败。
- **ESLint warnings 清零（R50）**：`taskStore.cancel` 未用 catch 绑定改 optional catch binding；`CheckpointPanel.load` 包 `useCallback` 补齐 effect 依赖。lint 达 0 errors / 0 warnings。
- **PEP 440 ↔ semver 版本桥接（R52 bump 挖出）**：`1.0.0-rc.1` 是项目首个 pre-release 版本号，暴露 Python 包元数据与 semver 的规范差异——`importlib.metadata` 报 PEP 440 规范化形态 `1.0.0rc1`（连字符被吃掉），`Version.parse` 按 semver.org 拒绝，公开 API `installed_semver()` 在自家版本号上抛 `not a valid semver`。`installed_semver()` 现将 PEP 440 prerelease 形态（`rc/a/b/c` 紧跟数字与 `.devN`）桥接回 semver 形态再解析；纯稳定版不经桥接、`.post`（semver 无对应）仍抛错。连带 `diag.export` 的 version 字段改报 semver 形态（`1.0.0-rc.1`，与 pyproject/CHANGELOG/UI 字面一致，此前会报 `1.0.0rc1`）。4 个新测试钉死桥接矩阵与"真实 metadata 永不抛错"契约。

### Changed — 1.0.0-rc 发布性能数字（R50 复测，详见 `docs/performance-baseline.md`）
- 冷启动 median 2.589 s（3 轮 2.619 / 2.589 / 2.579；基线 2.586 s，**+0.1%**，预算 ≤5 s）。
- 首屏 JS 136.7 KB / CSS 8.2 KB gzip（基线 123.9 / 8.2 KB——JS +10.3%，v0.14→v0.19 六个版本功能增长的量，预算 200 / 50 KB 内）；懒加载 346 chunk 2852.4 KB 持平。
- SQLite 零裸表扫、长会话渲染（500 条 34 行 DOM）、WS 重放 ≤512 由全量回归覆盖（10125 pytest + 622 vitest + 21 e2e 全绿）。
- 版本号 0.19.0 → **1.0.0-rc.1**（6 处代码位 + CLAUDE.md / AGENTS.md / README.md 版本行 + `uv lock`；CHANGELOG `[Unreleased]` 转正）。

## [0.19.0] - 2026-08-21

### Added — 诊断工具（v0.19.0 Milestone 8）
- **IPC `diag.export`（R45）**：第 168 个 IPC 方法——组装脱敏诊断包（单一 JSON RPC result，前端转为下载文件）：版本 / 平台（system / release / machine / python / pid）/ 运行时长 / 配置（**仅枚举、数字、布尔、计数**——绝不读 keyring、绝不含 CORS origin 值；路径只留 basename，沿用 `/health` 先例）/ 各表行数（复用 `data.export` 的虚表排除集）/ 已消毒日志尾（200 行）。**无数据库时降级为 `storage: {db_available: false}` 而非报错**——诊断工具在故障时也必须可用。`logging_setup` 新增 `_MemoryTailHandler` 内存环形缓冲（deque maxlen=200），挂 root logger 且与 stderr / 文件 sink 同享 handler 级 `SanitizerFilter`；单表 COUNT 失败记 -1 不致沉包。
- **前端诊断入口（R46）**：Settings → 数据 tab 第四面板「诊断包」——`DiagnosticBundle` 类型（storage 为 NO_DB 降级 union）、`TypedIPC.exportDiagnostic()`、mock 同形 bundle、`downloadJson()` 共享下载助手（export / diag 双复用，DRY）。操作互斥禁用；622/622 vitest 全绿。roadmap 原文「Settings About 区」落地为 Data tab 面板（设置页无 About tab，为单按钮新增第 15 个 tab 属过度设计——决策记入 commit note）。
- **脱敏保证断言测试（R47）**：4 个测试钉死 bug 报告安全契约——序列化 bundle 永不含 `MINIMAX_API_KEY` 值与绝对 `MINIMAX_CODE_DATA_DIR`（data dir 只留 basename）；CORS origins 只报计数；任何字段不含用户 home（与 env 无关）；日志尾经 `handler.handle()` 真实 dispatch 路径验证 `sk-` 形状消毒与 home 相对路径折叠。`_json_literal()` 处理 JSON 转义匹配（Windows 反斜杠泄漏不静默漏检）；`redact_paths` 的边界语义（值即路径整体折叠、非散文子串替换）写入测试 docstring。开发中借测试失败反向验证了 R45 挂载设计：直接 `emit()` 绕过 `handle()` 的 filter 检查会误报未消毒，真实路径上消毒有效。

### Changed
- 版本号 0.18.0 → 0.19.0（6 处代码位 + CLAUDE.md / AGENTS.md / README.md 版本行 + `uv lock`）。

## [0.18.0] - 2026-08-21

### Added — 文档完备（v0.18.0 Milestone 7）
- **README 全面重写（R40）**：-135/+92 行——快速开始以 `pnpm start` 单进程主线（构建 + agent 托管 web/dist）、命令速查表、8 个环境变量表、8 问 FAQ（无 Key 可用性 / 数据位置与备份 / 换机迁移 / dist 缺失 / 端口占用 / 升级 / 测试 / 数据不上传）。修正 4 处陈旧事实（smoke 6 个非 7、Playwright spec 在根级 `e2e/` 10 个、SQLite 实为 24 张实体表、thinking_count 早已实现）；删除 Phase1-6 / Tauri 考古章节；全部文档链接与 npm scripts 逐一验证存在。
- **`docs/user-guide.md` 用户手册（R41）**：198 行 19 节面向最终用户——五分钟上手、三栏布局导览、任务与项目管理、对话与工具授权、模型与密钥分工（模型供应商 vs 密钥管理）、命令面板与快捷键、技能系统、多 Agent 协作、检查器 11 tab、自动化三件套（定时 / 工作流 / Webhook）、Memory、MCP 与插件、Git + Code Review + Patch Studio、数据导出导入备份、权限与审计、移动配对、无障碍特性、异常横幅。全部交互主张对照组件源码逐一核实（修正 4 处初稿幻觉：权限弹窗实为允许 / 拒绝 + 总是允许开关、移动配对入口在侧栏非顶栏、任务重命名是双击、删未核实的归档说法）。README 与 CLAUDE.md / AGENTS.md 挂手册链接。
- **`docs/ipc-contract.md` 与代码对账（R42）**：registry 实测对账挖出真缺口——167 个注册方法中 84 个无文档锚点。补 **Appendix A 方法总表**（81 方法 × 16 命名空间，含一句话用途）；修 1 处事件名笔误（`agent.permission_request` → `permission.request`）。新增 `agent/tests/test_ipc_contract_doc.py` **6 个双向守护测试**：每个注册方法必须见于文档 / 文档 token 必须可解析为真实方法（事件 + 协议帧 + 白名单兜底）/ 事件清单与前端 `StreamEvent` 枚举锁死同步——此后新增 handler 不写文档锚点直接测试红。
- **CLAUDE.md / AGENTS.md / 模块文档全量对账（R43）**：五个文档（根 CLAUDE.md、AGENTS.md、web/CLAUDE.md、agent/CLAUDE.md、README.md）+216/-143 行，全部数字 registry + filesystem 当场实测——24 张实体表（+FTS/vec 虚表）、10 个工具模块、**35 个 IPC 前缀 / 167 个方法**（命名空间全量表）、31 个 handler 文件、20 个 DAO 模块、25 个迁移、12 个技能、6 个 smoke、10 个 e2e spec、设置页 14 tab 4 组、检查器 11 tab、29 个 stores、15 个流式事件；修正技能目录路径错误（`minimax_code/skills/_builtin` → `minimax_code/agent/skills/_builtin`）；环境变量补 `MINIMAX_CODE_CORS_ORIGINS` / `MINIMAX_CODE_LOG_FILE`；文档结构表补 user-guide / deployment / performance-baseline / roadmap 四行。

### Changed
- 版本号 0.17.0 → 0.18.0（6 处代码位 + CLAUDE.md / AGENTS.md / README.md 版本行 + `uv lock`）。

## [0.17.0] - 2026-08-21

### Added — 无障碍与键盘（v0.17.0 Milestone 6）
- **Modal 焦点陷阱审计与修复（R35）**：`useFocusTrap` 按 WAI-ARIA APG 对照修复 4 缺陷——document 捕获阶段拦截 Tab 逃逸（焦点跑到容器外时 preventDefault 拉回，Shift+Tab 回最后一个可聚焦元素）；容器自身 Shift+Tab 防漏；`isActuallyFocusable` 过滤 hidden / aria-hidden / 布局不可见（`checkVisibility` 存在性检测，jsdom 无布局自动跳过）；FOCUSABLE_SELECTOR 补 contenteditable，初始焦点支持 `[data-autofocus]` 显式标记（React `autoFocus` prop 不反射为 DOM attribute）。5 个使用者（DropdownMenu、Modal、ConfirmationDialog、PermissionRequestModal、SettingsPage）零改动受益。12 测试（9 hook + 3 modal 契约）。
- **icon-only 按钮可访问名全覆盖（R36）**：108 个生产 tsx 全量静态扫描——IconButton 组件 `"aria-label"` 必填 prop 类型强制 61 处；原生 button 60 个中 4 个 icon-only，唯一漏网的 TeamRunPanel 移除按钮补 `strings.rightPanel.teamRuns.removeAria`。新增静态审计测试进 CI（dotall tempered token 正则跨多行属性匹配、sr-only 文本计为 label），未来新增 icon-only 按钮漏 label 直接挂测试。
- **会话列表键盘导航 roving tabindex（R37）**：列表恒有恰一个 `tabIndex=0` 的 tab stop（fallback 链 rovingId → currentId → 首个可见行，搜索/过滤切换后不失效）；ArrowUp/Down 边界 clamp、Home/End 跳端点；导航按渲染 DOM 序（`querySelectorAll`）跨项目组正确移动——`filteredSessions` 时间序与分组渲染序不一致的坑由 DOM 查询天然规避。NavItem 透传 `tabIndex`/`onKeyDown`，SessionRow 标记 `data-session-row`。7 测试（单 stop / fallback / 跨组移动 / clamp / Home+End / stop 持久性 / Enter 选中）。
- **设计 token WCAG AA 对比度守卫（R38）**：`tests/design-tokens-contrast.test.ts` 直接解析 `index.css` 两个主题块（`:root` / `:root.light`），按 WCAG 2.1 相对亮度公式复算 29 组前景×背景配对 × 2 主题 = 60 断言常驻 CI；含 token 存在性守卫防空扫假绿。配对覆盖 ink-0/1/2 × 四层 surface、accent 三态 × 三层 surface、accent-contrast on accent、四种 status 色 × 两层 surface；11-13px UI 文本不主张大字豁免，一律 4.5:1。

### Fixed — 无障碍与键盘（v0.17.0 Milestone 6）
- **3 处 sub-AA 颜色 token（R38 审计挖出）**：dark `--ink-2` `#5d6679`（≈3.3:1）→ `#76839d`（≈4.7:1）；light `--ink-2` `#8a93a3`（≈3.1:1）→ `#66707e`（≈4.7:1）——时间戳、placeholder 等 11px 信息文本恢复可读；light `--status-error` `#dc2626`（≈3.9:1 on #fff）→ `#b91c1c`（≈6.5:1，Tailwind red-700），`--status-error-subtle` rgba 字面量同步。

### Changed
- 版本号 0.16.0 → 0.17.0（6 处代码位 + CLAUDE.md / AGENTS.md / README.md 版本行 + `uv lock`）。

## [0.16.0] - 2026-08-21

### Added — UI 文案统一（v0.16.0 Milestone 5）
- **strings.ts 集中文案层（R27–R32）**：新建 `web/src/ui/strings.ts` 作为前端唯一文案源（`as const`，1100+ 行）——静态文案普通属性、插值文案带类型箭头函数、全角标点、技术术语保留原文。六大域分轮迁移完成：layout（R27）、chat（R28）、settings（R29）、panels（R30）、modals（R31）、right-panel（R32，12 子域约 110 条）。组件内不再有硬编码 UI 文案，改文案只动一处；每轮同步对应测试断言。
- **`strings.a11y` 命名空间（R33，预热 M6 无障碍）**：navigation / closeOverlay / closeDialog / loading 四条基础无障碍标签，App.tsx、Modal、Spinner 接线。

### Changed — UI 文案统一（v0.16.0 Milestone 5）
- **全量英文清零（R33）**：`formatRelative` 六分支中文化（刚刚 / N 分钟前 / N 小时前 / N 天前 / M月D日 / YYYY年M月D日，删除 MONTH_NAMES）；repo mention 选项中文化（「当前仓库」，searchText 双语命中）。终扫 JSX 文本 / title / aria-label / placeholder 零英文残留（保留清单：Base URL、Cron 表达式、kbd 键名、协议枚举直出、mock 层模拟数据、日期固定格式等）。4 个单元测试文件 + 2 个 e2e spec 断言同步中文。
- 版本号 0.15.0 → 0.16.0（6 处代码位 + CLAUDE.md / AGENTS.md / README.md 版本行 + `uv lock`）。

## [0.15.0] - 2026-08-21

### Added — 数据可移植（v0.15.0 Milestone 4）
- **IPC `data.export`（R21）**：全部业务表一键导出为自描述 JSON 信封（即 RPC result 本体）——`format` / `schema_version` / `app_version` / `exported_at` 头部 + 每表行列表与行数。表集合从 `sqlite_master` 动态发现（未来迁移新表自动纳入，零硬编码清单）；`schema_migrations` 排除（应用版本收敛到顶层 `schema_version` 一处）；FTS/vec 虚表排除（派生态，导入端 R22 靠触发器/重建恢复）；存储未初始化（`MINIMAX_CODE_NO_DB=1`）回 `-32603` 而非空导出。新增 `agent/tests/test_data_portability.py`（6 测试：信封元数据、核心 12 表覆盖 pin、系统/虚表排除、行级往返保真、空库语义、IPC 端到端）。契约文档补 `data.*` 详述段与总表行。
- **IPC `data.import`（R22）**：校验 + **替换式**导入，整体单事务（`BEGIN IMMEDIATE`）保证 all-or-nothing——任何行失败即回滚到导入前状态。校验层拒绝坏信封（非对象 / 错 `format` / `tables` 形状非法 / 未来 `schema_version`，全部 `-32602`）。安全面：列名取 `PRAGMA table_info` 白名单交集（漂移列丢弃、缺失列走 SQL DEFAULT）、值全参数化绑定、信封里的未知表记 `skipped_tables` 跳过而非报错；事务外 `PRAGMA foreign_keys=OFF/ON` 包裹（SQLite bulk-load 惯用法）。**幂等**：重复导入同一信封结果一致（envelope 表全量 DELETE 后回填，envelope 外的表保持不动）。新增 11 测试（校验 4 + 替换语义/幂等/回滚/未知表/列漂移 5 + IPC 端到端 2）。契约文档补 `data.import` 方法行与语义段。
- **IPC `data.backup`（R23）**：SQLite 在线 backup API 文件级**完整快照**——schema、WAL 内容、FTS 索引与 vec 影子表全含，不阻塞读、源库只读不写（与 JSON 导出互补：备份是文件级全量，导出是跨 schema 可移植）。`target_dir` 可选（缺省 `<数据目录>/backups/`，目录不存在自动创建），文件名带 UTC 毫秒时间戳（`minimax-code-backup-YYYYMMDD-HHMMSS-mmm.db`）重复备份永不互相覆盖。返回 `{path, bytes}`。新增 4 测试（快照可独立打开且数据/迁移版本等价、缺省目录落位、重复备份不同文件、IPC 端到端）。契约文档补 `data.backup` 方法行与语义段。
- **Settings Data 标签页（R24）**：前端三面板数据可移植 UI——导出（信封 Blob 下载，文件名带本地时间戳 `minimax-code-export-YYYYMMDD-HHMMSS.json`）、导入（文件选择器 → FileReader → `format` 前置校验 → 破坏性操作确认弹窗（明示替换语义与行数）→ summary 反馈含 skipped 表数）、备份（落盘路径 + 人性化体积）。三操作各自 busy 态互斥、共享成功/错误反馈行（`role="status"`）。契约层：`TypedIPC` 新增 `exportData`/`importData`/`backupData`，`types/ipc.ts` 新增 `DataExportEnvelope`/`DataImportSummary`/`DataBackupResult`；**mock backend 全覆盖**（内存信封回声 + format 校验镜像真实 INVALID_PARAMS），浏览器无 agent 模式照常可用。SettingsPage Core 组新增 "Data" 入口。新增 6 组件测试（双 happy path、双失败路径、拒绝确认零调用）。前端全量 545 vitest 通过（+6）。
- **灾难恢复端到端测试（R25）**：钉死用户恢复序列——导出（RPC）→ 清空全部业务表（FK off 清扫，镜像导入自身的 bulk-load 惯用法）→ 导入（RPC）→ 再导出的 `tables`/`counts` 与原信封逐行等价；第二场景跨连接周期（close → reopen 同一数据库文件，即 agent 重启）用纯 dump/restore 函数复验。文件内累计 23 测试，全量 10103 通过（+2）。

### Changed
- 版本号 0.14.0 → 0.15.0（6 处代码位 + CLAUDE.md / AGENTS.md / README.md 版本行 + `uv lock`）。

### Fixed — 数据可移植（v0.15.0 Milestone 4）
- **异步连接隐式事务缺陷（R22 顺带修根）**：`AsyncDatabase.connect` 漏传 `isolation_level=None`，与同步侧（注释明说 "we manage transactions explicitly"）不一致——legacy 隐式模式下裸 `execute()` 的 DML 永不提交（close 时**静默丢数据**，同连接读未提交才显得"成功"），且留下挂起事务令后续 `BEGIN IMMEDIATE` 炸 `cannot start a transaction within a transaction`。补传后：裸写即刻落盘、`transaction()` 不再撞挂起事务。DAO 写路径本就全走显式 `transaction()`，零行为影响（全量 10086+ 回归验证）。

## [0.14.0] - 2026-08-21

### Added — 安全加固（v0.14.0 Milestone 3）
- **CORS 白名单安全边界（R15）**：`MINIMAX_CODE_CORS_ORIGINS` 环境变量化（默认不变）——拒绝未列 origin / env 只追加不替换默认集 / 无效项静默丢弃；architecture、ipc-contract、agent CLAUDE.md 三处陈旧说法同步修正（4 新测试）。
- **RPC 畸形请求防护（R16）**：5 新测试钉死四条拒绝路径——缺 `method` 信封回 INVALID_REQUEST / 超限 body（10MB+1）在 dispatch 之前被拒（spy handler 零调用）/ 恰好 10MB 边界不误杀 / GET 4xx / WS 垃圾帧静默丢弃不断连。
- **权限出厂默认（R18）**：高危工具在**代码级**出厂 gated——`DEFAULT_RULES` 常量 `exec_*` → ask，`lookup`/`list_rules`/`get` 在用户规则 miss 后回退默认。零 DB 写入（不 seed 用户库）、用户规则永远优先、删除用户规则即回退出厂默认（无"重启重置"缺陷）；默认规则经 `permission.list`/`get` 携带 `origin: "default"` 标记，UI 徽标显示且不可删除（只能用选择器覆盖）。新增 9 个后端测试（`TestFactoryDefaults` + IPC 默认上报）。契约文档补 `permission.*` 详述段（方法表 + 默认策略语义）。
- **安全回归套件集中化（R19）**：注册 `security` pytest marker，`uv run pytest -m security` 一键跑完整安全面——**151 测试**覆盖 9 个安全面（权限规则+出厂默认、日志/RPC 脱敏、secrets 存储/RPC、终端进程加固、memory 注入防护、审计日志、RPC 畸形拒绝、CORS 白名单）。7 个纯安全测试文件打文件级 marker，混合文件 `test_http_server.py` 的 12 个安全函数逐个打装饰器。新增 `agent/tests/test_security.py` 作为集中入口：模块 docstring 即安全测试地图 + 三重护栏（套件收集 floor ≥ 130 防 marker/文件静默失联、逐文件 AST 计数 floor 防安全文件被清空、`test_http_server.py` 12 函数 marker 存在性 AST 校验）+ 2 个跨切面冒烟（R18 出厂默认 ask→deny 遮蔽→删除回退全链路；redact_value 对 10 种凭据形态消毒 + URL userinfo 剥离 + 嵌套结构遍历 + 输入不可变）。

### Changed
- 版本号 0.13.0 → 0.14.0（6 处代码位 + CLAUDE.md / AGENTS.md / README.md 版本行 + `uv lock`）。

### Fixed — 安全加固（v0.14.0 Milestone 3）
- **前端 wire 映射修复（R18）**：`bindTypedIPC` 新增 `backendRuleToFrontend` 翻译层——后端 wire 是 `{tool_pattern, action, created_at: ISO-string}`，前端 `PermissionRule` 是 `{tool, pattern, decision, created_at: ms}`，此前 `listRules`/`setRule` 原样透传导致真实 agent 下 Settings 权限 tab 静默断链（mock 两头说前端形状掩盖了断链）；mock backend 改为在 typed 层之下说 wire 形状。新增 `web/src/ipc/__tests__/typed-permission.test.ts`（6 测试：字段映射、wire 参数断言、mock 契约）。
- **日志密钥脱敏对子 logger 失效**（R17 审计发现）：`SanitizerFilter` 此前挂在 root logger 上，而 logger 级 filter 只对 root 直接 emit 的记录生效——`minimax_code.*` 子 logger 传播来的记录（即全部业务日志）不经消毒直写 stderr/日志文件。改为在 `configure_logging` 中把 filter 附加到**每个 handler**（handler 级 filter 对传播记录同样生效）。新增 `agent/tests/test_secret_audit.py`（6 测试）：子 logger 传播脱敏回归、handler 挂载契约、`secrets.*` RPC 往返不回显 key 明文（含 key 主体子串断言）、INVALID_PARAMS 分支不反射 payload、LLM 错误形状可脱敏。
- `secrets.status` 防御分支的 `error: str(exc)` 直通 RPC 信封改为经 `redact_value` 消毒（RPC 错误响应不经日志管道）。

## [0.13.0] - 2026-08-21

### Added — 性能基线（v0.13.0 Milestone 2）
- **WS 断连事件重放**：广播事件带单调顶层 `seq`，agent 侧保留最近 **512** 条历史环；客户端重连携带 `?since=<last seq>` 即按序重放断线期间错过的事件（`agent.ready` 等生命周期帧无 `seq`、永不重放）。此前广播为 fire-and-forget，断连期间的事件永久丢失。新增 `agent/tests/test_ws_replay.py`（6 单测）与 `e2e/smoke-ws-resume.spec.ts`（断线 → 重连 → seq 递增重放断言）。前端仅在已见过 seq（`wsLastSeq > 0`）时携带 cursor，首连不重放，避免与 RPC 拉取的持久化状态重复。
- **启动基准脚本**：`agent/tests/bench_startup.py`——冷启动到 `/health` OK 墙钟耗时（基线 2.586 s，3 轮 2.574/2.586/2.607）。
- **首屏产物预算审计**：`web/scripts/bundle-report.mjs`（`pnpm --filter @minimax/web bundle:report`）实测 gzip 字节并对首屏预算硬断言（JS ≤ 200 KB / CSS ≤ 50 KB，超限 exit 1）。基线：首屏 JS 123.9 KB / CSS 8.2 KB gzip，346 个懒 chunk 2.8 MB gzip 按需加载。
- **长会话渲染冒烟**：`web/tests/message-list-perf.test.tsx`——500 条消息仅挂载 34 行 DOM（两层窗口：`useMessageWindow` 50 条/页 + `useVirtualizer`），jsdom 渲染 164 ms。
- **SQLite 索引审计**：15 条热点查询 `EXPLAIN QUERY PLAN` 逐条过，零裸表扫；`agent/tests/test_index_audit.py` 钉死索引集。
- **性能基线文档**：`docs/performance-baseline.md`——启动/索引/首屏/长会话/WS 五项基线数字与复测命令的单一来源。

### Changed
- 版本号 0.12.0 → 0.13.0（6 处代码位 + CLAUDE.md / AGENTS.md / README.md 版本行 + `uv lock`）。

## [0.12.0] - 2026-08-20

### Added — 生产单进程模式收口（v0.12.0 Milestone 1）
- **生产模式 e2e 套件**：新增 `e2e/production-mode.spec.ts`（4 specs）——SPA 首页同源启动、`/health` 与 `/rpc` 对 SPA mount 的路由优先级、同源 WS 升级、生产模式下 UI 聊天全链路；`e2e/global-setup.ts` 在 `web/dist` 缺失时自动执行 `pnpm build`（180s 超时 + 产物校验）。
- **web dist 挂载测试与指引**：`MINIMAX_CODE_WEB_DIST` 覆盖目录含 `index.html` 才挂载（缺 index.html 静默忽略不 crash）；未挂载时日志输出 `run pnpm build first` 指引。
- **同源契约测试**：生产模式 SPA 与 API 同源，POST `/rpc` 携带自身 Origin 不依赖 CORS 白名单（单元 + e2e 双保险）。
- **文件日志**：`MINIMAX_CODE_LOG_FILE` 落盘选项——绝对路径原样使用、相对路径解析到数据目录；RotatingFileHandler 5 MB × 3 份（utf-8）；不可写路径降级 stderr-only 不 crash；SanitizerFilter 同样作用于文件 sink。
- **`/health` 扩展**：新增 `web`（web/dist 是否挂载）与 `data_dir`（数据目录 basename，不含用户路径，脱敏）字段。
- **部署文档**：新增 `docs/deployment.md`——快速开始、mount 工作原理、8 个环境变量配置表、前后端分离部署、数据备份、故障排查。

### Changed
- 版本号 0.11.0 → 0.12.0（6 处代码位 + CLAUDE.md / AGENTS.md / README.md 版本行）。

## [0.11.0] - 2026-08-20

### Added — 后端
- **代码库 RAG（v0.11.0 Milestone 2）**：
  - 新增 `agent/minimax_code/codebase/` 包：`CodebaseIndexer` 项目级索引器 + `CodebaseChunksDAO` chunk 持久化，复用已有 `perception/indexer.py` 与 `xai_codebase_graph/` 能力。
  - 新增 `codebase_chunks` 表与 `codebase_chunks_fts` FTS5 全文索引（迁移 `018_codebase_chunks.py`）。
  - 新增 `agent/minimax_code/ipc/handlers_codebase.py`，注册 `codebase.*` IPC 命名空间：`codebase.status` / `codebase.build_index` / `codebase.search` / `codebase.summarize`。
  - `app.py` 初始化 `CodebaseIndexer` / `CodebaseChunksDAO` 单例并注册 handlers。
  - 新增 `agent/tests/test_codebase_indexer.py`、`test_codebase_store.py`、`test_handlers_codebase.py` 覆盖索引、存储与 IPC。
  - `search_codebase` / `summarize_codebase` / `find_symbol` 工具返回结果新增 `source` 字段（`path#Lstart-end` 或 `path#Lline`），并更新系统提示词要求模型在代码块 fence info 中标注来源，使前端 `CodeBlock` 自动渲染 source chip。
  - `agent.send_message` 在最终 assistant message 的 `metadata.sources` 中汇总本轮 codebase 工具来源，供前端展示引用面板。
- **MCP 基础设施（v0.11.0 Milestone 1）**：
  - 新增 `mcp_servers` 表与 `McpServersDAO`，持久化外部 MCP 服务器配置（name/transport/command/url/env/enabled）。
  - 新增 `agent/minimax_code/ipc/handlers_mcp.py`，注册 `mcp.*` IPC 命名空间：`mcp.list_servers` / `mcp.add_server` / `mcp.update_server` / `mcp.remove_server` / `mcp.list_tools` / `mcp.invoke_tool`。
  - `MCPRegistry` 新增 `list_server_tools` / `call_tool`，并在 `app.py` 启动时加载已启用服务器到全局单例。
  - 新增 `agent/tests/test_handlers_mcp.py` 与 `agent/tests/test_mcp_servers_dao.py` 覆盖 IPC 与 DAO。
- **MCP 集成增强（v0.11.0 Milestone 1 完成）**：
  - 新增 SSE 传输：`mcp.transport.SSETransport` 基于 `httpx.AsyncClient` + SSE endpoint，支持自定义 `headers`、`bearer_token` 与 endpoint 发现流程。
  - 迁移 `024_mcp_auth_tool_states.py` 为 `mcp_servers` 表增加 `bearer_token`、`headers`、OAuth（`oauth_client_id`/`oauth_client_secret_env_var`/`oauth_scopes`）以及 `tool_states` 列。
  - `McpServersDAO` 与 `MCPServerConfig` 持久化/加载上述认证与授权字段。
  - `MCPRegistry` 根据 `tool_states` 映射过滤每个桥接工具，缺省为启用；桥接工具对外名称统一为 `mcp__<server>__<tool>`。
  - `mcp.add_server` / `mcp.update_server` 支持 SSE 与 stdio、认证参数及 `tool_states` 更新；失败保持 fail-open，不影响 agent 启动。
  - 新增 `agent/tests/test_mcp_sse_transport.py`、`agent/tests/test_mcp_registry_tools.py`，并扩展 `test_handlers_mcp.py` 覆盖 SSE、认证与 per-tool 状态。
- **项目/任务分层（v0.10.0）**：
  - 新增 `projects` 表与 `ProjectsDAO`，支持创建、更新、归档、删除项目。
  - `sessions` 表新增 `project_id` 列；未指定项目的会话默认归属 `id="inbox"` 的“收件箱”。
  - 新增 `project.*` IPC 命名空间：`project.list` / `project.create` / `project.update` / `project.delete` / `project.archive` / `project.unarchive`。
  - 删除项目时，其下会话自动移回收件箱，避免误删。
- 消息级持久化能力：`MessagesDAO.update()` 支持更新内容与元数据。
- 新增 IPC 方法 `message.update` 与 `message.delete`：编辑/删除单条消息。
- **长期记忆（v0.11.0 Milestone 3）**：
  - 新增 `agent/minimax_code/memory/` 包：`MemoriesDAO` 持久化记忆、`MemoryExtractor` 轻量事实抽取、`MemoryInjector` 按 project/session 检索并注入系统提示词。
  - 新增 `memories` 表（迁移 `019_memories.py`），支持 `preference` / `decision` / `lesson` / `fact` 四类记忆。
  - 新增 `agent/minimax_code/ipc/handlers_memory.py`，注册 `memory.*` IPC 命名空间：`memory.list` / `memory.add` / `memory.delete` / `memory.search` / `memory.extract`。
  - `app.py` 注册 `memory.*` handlers；`agent.send_message` 根据当前 session 的 `project_id` / `session_id` 拉取相关记忆，注入 `## Relevant memories` 系统提示词上下文。
- **批量会话操作（v0.10.3）**：新增 `session.batchArchive` / `session.batchUpdateProject` IPC 方法及 `SessionsDAO` 批量接口，支持事务级归档与跨项目移动。

### Added — 前端
- **Right Panel Codebase 标签页**：新增 `web/src/components/right-panel/CodebasePanel.tsx`，支持查看索引状态、触发构建索引、FTS 搜索代码库并展示结果；`tabs.tsx` / `InspectorContent.tsx` 注册 `codebase` tab。
  - 扩展 `web/src/types/ipc.ts`、`web/src/ipc/typed.ts`、`web/src/ipc/mock.ts` 的 Codebase RAG 类型与桩实现。
- **消息来源面板**：`MessageItem` 新增可折叠 `SourcesPanel`，展示 assistant message `metadata.sources` 中汇集的 codebase 来源；新增 `SourceAnnotation` 类型并扩展 `MessageMetadata`。
- **Settings MCP Servers 标签页**：新增 `web/src/components/settings/McpServersTab.tsx`，支持添加/删除 stdio MCP 服务器、查看连接状态；同步更新 `SettingsPage` tab 路由与 mock backend。
  - 扩展 `McpServersTab`：支持 stdio/SSE 传输切换、命令行/URL/env/headers 输入、bearer token、OAuth clientId/scopes，以及基于 `listMcpTools` 的 per-tool 启用开关；工具状态变更通过 `updateMcpServer` 持久化。
  - `ToolCallCard` 新增 MCP 桥接标识，当工具名为 `mcp__<server>__<tool>` 时渲染 server/tool badge。
  - 同步扩展 `web/src/types/ipc.ts`、`web/src/ipc/typed.ts`、`web/src/ipc/mock.ts` 的 MCP 认证、OAuth 与 `tool_states` 类型/桩实现。
- **Settings Memory 标签页**：新增 `web/src/components/settings/MemoryTab.tsx`，支持查看、搜索、添加、删除长期记忆；同步更新 `SettingsPage` tab 路由与图标。
- 扩展 `web/src/types/ipc.ts`、`web/src/ipc/typed.ts`、`web/src/ipc/mock.ts` 的 MCP 与 Memory 类型与桩实现。
- **Sidebar 项目化**：会话按项目分组展示；收件箱默认展开置顶，普通项目可折叠，归档项目沉底。
- 项目行支持新建、重命名、归档/取消归档、删除；删除时提示其下会话将移回收件箱。
- 新建任务默认写入当前选中的项目（或收件箱）。
- 用户消息气泡新增操作菜单（复制/编辑/删除）。
- `MessageItem` 支持内联编辑，保存后同步更新后端并刷新本地消息列表。

### Fixed
- 修复 TopBar `overflow-hidden` 导致 workspace/git/notification 下拉菜单被裁切、点击后无法查看的问题。
- `ConnectionBanner` 增加关闭按钮，非按钮区域改为 `pointer-events-none`，避免遮挡顶部下拉菜单。
- `TopBar` 的 Command Palette 按钮改为直接调用 `toggle()`，不再模拟键盘事件。
- `ComposerToolbar` 在桌面端改为单行布局，减少输入区视觉噪音。

### Changed
- **Sidebar Phase 1 体验优化**：
  - 项目行操作从 hover-only 按钮改为常驻 `⋯` 下拉菜单，兼容触控设备。
  - 搜索命中时自动展开所属项目，避免结果折叠隐藏。
  - 「新任务」按钮旁新增当前目标项目选择器，明确任务落点。
  - 统一空状态中文文案：`暂无任务`、`无匹配任务`。
- 为顶部 icon-only 按钮（Sidebar hamburger、Command Palette、Notifications）及右侧面板 collapse 按钮补充 `title` tooltip。
- **Sidebar 批量操作（v0.10.3）**：会话行支持复选框多选；选中后顶部显示批量工具栏，支持全选可见任务、批量归档/取消归档、批量移动到项目。

### Fixed
- 移除前端剩余 `window.prompt/confirm`：项目创建/重命名/删除与技能移除统一使用 Aurora `Modal`。
- **存储层可靠性加固**：迁移 016 重写为幂等迁移——`PRAGMA table_info` 列守卫替代裸 `ALTER TABLE ... REFERENCES`（SQLite 在 FK 开启且表有数据行时拒绝该语法，存量库升级必失败）；迁移 024 的 7 条裸 `ALTER` 同样加列守卫；迁移 020/021 重建路径先清理 `_new` 残留表。
- **`Database.migrate()` 改为显式事务**：`BEGIN IMMEDIATE` → 执行 → 记录版本 → `COMMIT`，失败整体 `ROLLBACK`，杜绝半途失败留下脏 schema；同时清除全部 22 处迁移 `executescript`（其隐式 COMMIT 会破坏外层事务），改用 `run_script` helper 逐句执行。
- 存量"脏库"下次启动自动自愈：versions 1-15 已记录但 16-24 缺失的库，幂等重放一次补齐（已在真实库副本上验证：123 条会话完整保留、`project_id` 全部归位 `inbox`、`foreign_key_check` 零违规）。
- 存储降级时前端显示持久琥珀色 `StorageBanner`（探测 `/health` 的 `db` 标志；agent 不可达视为"未知"不误报，连接问题仍归 `ConnectionBanner` 管）。
- 修复侧栏消息计数停留在「0 消息」：stats 计算补充消息数与对话状态依赖，新消息即时刷新。
- 版本号 6 处代码位统一 bump 至 0.11.0（含 `version.py` 发行包名 `minimax-code` → `minimax-code-agent` 修正）。

### Tests
- 新增 Codebase RAG 相关测试：`agent/tests/test_codebase_indexer.py`、`agent/tests/test_codebase_store.py`、`agent/tests/test_handlers_codebase.py`。
- 新增 MCP 相关测试：`agent/tests/test_handlers_mcp.py`、`agent/tests/test_mcp_servers_dao.py`、`agent/tests/test_mcp_sse_transport.py`、`agent/tests/test_mcp_registry_tools.py`、`web/src/components/settings/McpServersTab.test.tsx`、`web/src/components/chat/ToolCallCard.test.tsx`。
- 新增 `agent/tests/test_projects.py`，覆盖项目 DAO 与删除归位逻辑。
- 新增/更新 `web/tests/sidebar.test.tsx`、`web/tests/sidebar-history.test.tsx`、`web/tests/chat-panel.test.tsx`，适配项目分组与 `project_id` 传参。
- 放宽 `agent/tests/test_connection.py::test_interval_keeps_global_timeline_across_loops` 的容差，消除 Windows/高负载下时序抖动导致的偶发失败。
- 前端 vitest：529 tests 全绿。
- Playwright e2e：15 specs 全绿。
- Python pytest：10017 passed，15 skipped。

## [0.9.1] - 2026-07-27

**前端交互增强 + 后端会话能力扩展。** 在 v0.9.0 重构基础上继续迭代。

### Added — 后端
- 新增 IPC 方法 `session.stats`：返回总会话数、归档会话数、总消息数。
- 新增 IPC 方法 `session.export`：把单个会话的全部消息导出为 Markdown 文件。
- `SessionsDAO.stats()` 聚合查询与对应的 pytest 覆盖。

### Added — 前端
- **全局 Command Palette（Cmd/Ctrl+K）**：快速搜索最近会话、打开设置页、切换 Preview、新建任务。
- **会话统计**：Sidebar 底部展示当前总会话数和总消息数。
- **会话导出**：Composer 工具栏新增下载按钮，一键导出当前会话为 `.md`。
- **空状态打磨**：MessageList 空状态统一使用 `EmptyState` 原语，并替换剩余 `minimax-*` tokens。

### Tests
- 前端 vitest：61 files / 483 tests 全绿。
- Playwright e2e：15 specs 全绿。
- Python `tests/test_sessions.py`：27 passed。

## [0.9.0] - 2026-07-26

**前端全面重构与视觉刷新。** 引入统一设计系统，拆分巨型文件，按域重组组件目录，
修复存量类型错误，全量测试保持通过。

### Added — 设计系统（Design System）
- 新 CSS 变量体系：`surface-0/1/2/3`、`line`/`line-strong`、`ink-0/1/2`、
  `accent` 状态、语义化 `status-*`。
- 新增 `web/src/ui/` 共享原语组件：`Button`、`IconButton`、`Input`、`Textarea`、
  `Badge`、`Panel`、`Modal`、`Spinner`、`EmptyState`。
- Tailwind 配置扩展新 tokens，同时保留 `minimax-*` 作为兼容别名。

### Changed — 组件结构
- `components/` 按域拆分为 `layout/`、`chat/`、`right-panel/`、`modals/`、
  `panels/`、`settings/`。
- `MessageInput.tsx`（30KB）拆分为 `chat/` 下的 hook + 子组件，使用 UI 原语。
- `MessageItem.tsx`（26KB）拆分为 `chat/` 下的 markdown/code/mermaid/工具卡/操作栏等模块。
- `RightPanel.tsx`（20KB）拆分为 `right-panel/` 下的 Inspector chrome + 各区块组件。
- `ProvidersTab.tsx`/`TeamsTab.tsx`/`ModelsTab.tsx`/`WebhooksTab.tsx` 拆分为容器 +
  hook + 展示组件。
- `PatchPreviewPanel.tsx` 拆分为共享工具、文件卡、hunk 卡组件。
- `ErrorBoundary.tsx` 中 toast 子系统抽出 `layout/Toast.tsx`。

### Changed — IPC 客户端结构
- `web/src/ipc/client.ts`（98KB）拆分为 `client.ts`（transport）、`typed.ts`、
  `mock.ts`、`mockData.ts`。
- 修复了 `CrashHistoryEntry` 未使用、`TypedIPC` 缺少崩溃恢复方法、
  `JsonRpcId` 未允许 `null` 等存量 tsc 错误。

### Changed — 视觉
- Sidebar/TopBar/NavItem 应用新设计系统：accent rail 选中态、统一表单原语、
  现代卡片阴影。
- Settings、Modals、Panels、CodeReview、Git diff 等全部迁移到新 tokens 与原语。
- 深色/浅色主题同步刷新，尊重 `prefers-reduced-motion`。

### Tests
- 前端 vitest：60 files / 479 tests 全绿。
- Playwright e2e：15 specs 全绿。
- `pnpm build` 通过，生产包正常输出。

## [0.8.0] - 2026-06-07

**企业多Agent (Enterprise Multi-Agent)。** 四大模块：Agent 团队模板系统、多Agent
并行编排+冲突检测、中文编码规范+双语文档生成、Code Review 多维度升级。

### Added — Stage 1: Agent 团队模板系统
- **`agent_teams` 表**（migration 010）：团队名称、描述、图标、颜色、编排模式
  （parallel/sequential/round-robin）、关联 agents JSON。
- **`agents` 表扩展**（migration 010）：新增 description、enabled、icon、color、
  category、tags、team_id、skills、max_iterations、temperature 列。
- **`AgentTeamDAO`**：CRUD + enable/disable + 分页列表。
- **`handlers_teams.py`**：7 个 IPC 方法（team.list/create/get/update/delete/
  enable/disable）。
- **前端 TeamsTab**：SettingsPage 新 Tab，团队列表 + 创建表单 + Agent 分配。
- **增强 AgentsTab**：description textarea、category 下拉、skills 多选。

### Added — Stage 2: 多Agent 并行编排 + 冲突检测
- **`TeamOrchestrator`**：三种编排模式（sequential 串行传递、parallel 并发合并、
  round-robin 均分），内置文件写入冲突检测。
- **`team.spawn` IPC**：团队级 spawn，发射 `agent.team_progress` 流式事件。
- **前端 `TeamRunPanel`**：并行运行可视化，分支进度 + 冲突警告。
- **`@team:` 触发器**：MessageInput 支持 `@team:` 前缀快速选择团队。

### Added — Stage 3: 中文编码规范 + 双语文档生成
- **`coding-standards` 技能**：check_style（ruff/AST 回退）、check_naming
  （PascalCase/snake_case）、check_docstring（覆盖率 + Google/NumPy/Sphinx 验证）。
- **`doc-generator` 技能**：extract_api_signatures（Python AST 签名提取）、
  generate_doc（双语 Markdown 文档生成，含 TOC）。

### Added — Stage 4: Code Review 多维度升级
- **`SecurityScanTool`**：SEC001-006 六项 AST 检测（硬编码密钥、SQL 注入、
  eval/exec、shell=True、pickle、弱哈希）。bandit 可用时自动升级。
- **`PerformanceCheckTool`**：PERF001-004（async 阻塞、循环字符串拼接、
  不必要拷贝、N+1 查询模式）。
- **`TypeCheckTool`**：mypy → pyright → AST 注解覆盖率三级回退。TYPE001/002。
- **`TestCoverageTool`**：pytest --cov → heuristic 文件映射回退。
- **预置审查 Agent**（migration 011）：security-reviewer（red）、
  performance-reviewer（yellow）、style-reviewer（blue）。
- **`CodeReviewPanel` 多维度 Tab**：Overview | Security | Performance | Style，
  "Run All" 按钮并行触发三项检查。
- **`codeReviewStore` 扩展**：`dimensions` 分组 + `runAllChecks()` 流式编排。

### Tests
- Stage 1: 30+ 新增（DAO CRUD、IPC handlers、扩展 agents 字段）
- Stage 2: 23+ 新增（TeamOrchestrator 三模式、冲突检测、team.spawn）
- Stage 3: 25 新增（coding_standards 14 + doc_generator 11）
- Stage 4: 15 新增（security 5 + performance 4 + type_check 2 + coverage 1
  + migration 1 + provider 2）
- 全量：Python 751 passed / Frontend 208 passed

## [0.3.2] - 2026-06-05

**IPC 契约修复 + 功能补齐。** v0.3.1 补齐了 7 项前端功能，本轮修复了全部
IPC 方法名不匹配和缺失的后端 handler，新增 Git 查看器和 `schedule.run_now`。

### Fixed
- **IPC 方法名修复**（P0）：`agent.list_agents` → `agent.list`、
  `permission.list_rules` → `permission.list`、`permission.set_rule` →
  `permission.set`、`permission.delete_rule` → `permission.delete`。
  前端 binding + mockHandle 同步更新，消除生产环境调用失败。
- **IPC 参数键修复**（P0）：`permission.delete` 从 `{rule_id}` 改为
  `{tool_pattern}`，`skill.invoke` 从 `{args}` 改为 `{request}`。
- **幽灵方法清理**（P0）：移除前端 `sendToDevice`（`mobile.send` 无后端实现）。
- **RightPanel DOM 嵌套警告**：Section header 从 `<button>` 改为
  `<div role="button" tabIndex={0}>`，消除 `validateDOMNesting` 警告。

### Added
- **`agent.cancel` handler**（P0）：后端 `builtins.py` 新增 `_ACTIVE_CORES`
  字典追踪运行中的 AgentCore，`agent.cancel` handler 调用 `core.cancel()`
  设置 asyncio.Event 通知 LLM 循环中止。前端 Stop 按钮终于生效。
- **Git Diff/Log 查看器**（P1）：新增 `GitViewerModal` 组件，双 Tab
  （Diff 按 +行绿/-行红渲染、Log 列出最近 commits）。`GitStatusBar`
  popover 底部新增 "View Diff" / "View Log" 按钮。
- **`schedule.run_now` 前端绑定**（P1）：TypedIPC 新增 `runNowJob`，
  scheduleStore 新增 `runNow()` 方法，Settings 页 ScheduledJobRow
  新增 Play 图标按钮。

### Changed
- 版本号统一升级到 `0.3.2`（pyproject.toml、__init__.py、package.json ×2、
  version.ts）。

## [0.3.0] - 2026-06-03

**Four feature tracks land together.** v0.2.0 把项目从 Tauri 桌面壳切到
web SPA + 本地 Python agent，但 chat 流里只能看到 LLM 的最终答案 —— 看
不到"它想了多少次 / 它调了哪些子 agent / 它看过的代码改动"这些过程信息。
v0.3.0 起把这四块补齐，让一次 chat 真的能"看进去"。

### Added
- **`thinking_count` 通道**（agent: `core.py` / `llm.py` / `builtins.py` /
  `server.py`）：`agent.message_chunk` 事件在每个 turn 收尾的那条 chunk 上
  携带 `data.metadata = {thinking_count, tokens_in, tokens_out}`。Mock
  模式每调一次 LLM 自增 1，真实模式从 upstream `usage.thinking_tokens` 读。
  Web 端 `MessageItem` 把这个数渲染成 "思考 N 次" 摘要行。详细见
  [`docs/v0.3.0-design.md`](docs/v0.3.0-design.md) §1。
- **Sub-Agent UI**（agent: `handlers_agents.py` + web: `SubAgentPanel` /
  `SubAgentResultCard` / `subAgent.ts`）：chat 输入框新增 `@agent` 触发器
  下拉，敲 Enter / Tab 选中 sub-agent 后 prime marker 并触发
  `agent.spawn_subagent`。后端 handler 跑
  `SubAgentRuntime.invoke` 时按 `started → thinking → (tool_call →
  tool_result)? → completed` 节奏推 `agent.subagent_progress` 事件流；失败
  时最后一个 `failed` 事件先于 error reply 发出，UI 不会卡在"运行中"。
  Right rail 新增 "Sub-agents" 段，inflight 进度条 + 完成后折叠卡片
  回填到主消息流。
- **Code Review**（agent: `code_review._review_diff` + `handlers_skills` 短
  路径 + web: `CodeReviewPanel` / `DiffView`）：`skill.invoke` 收到
  `params.diff` 时直接走 `_review_diff(diff)` 而非 LLM 工具循环。Mock
  analyser 走 unified diff 的 hunks，给每文件最多 3 条 `severity=info`
  评论（行长 >120 字符的升 `warning`）。`git.diff` 拿到的真实 diff 直接
  喂进 skill，UI 在 CodeReviewPanel 里渲染 file:line 锚定评论。
- **Git 集成**（agent: `handlers_git.py` + web: `GitStatusBar` /
  `TopBar` / `git.ts`）：新增 `git.status` / `git.diff` / `git.log` 三个
  JSON-RPC handler。Read-only（不替用户 commit / push），每次调用
  `subprocess.run` 跑 5s timeout。`status` 解析 `--porcelain=v2 -z` 把
  paths 分到 `modified` / `untracked` / `staged` 三桶。Top bar 替换
  旧 `WorkspaceSwitcher` 为 `TopBar`，渲染分支 + 干净/脏指示灯 +
  popover 列出改动的文件路径。E2E `smoke-boot` 加一条
  `[data-testid=git-status-bar]` 存在性断言。
- **设计文档** [`docs/v0.3.0-design.md`](docs/v0.3.0-design.md)：四块
  feature 的 IPC 契约 / store API / 组件边界 / sequencing 的 source of
  truth，所有 v0.3.0 worker 开工前先读。

### Test coverage added
- `agent/tests/test_thinking_count.py` — 6 cases
  （LLM counter / _stream_turn 注入 / Context.emit metadata / IPC round-trip）
- `agent/tests/test_subagent_spawn.py` — 4 cases
  （happy path / 缺 name / 未知 agent / runtime 失败 → failed 事件）
- `agent/tests/test_git_handlers.py` — 18 cases
  （status 3 buckets / diff 4 个 scope / log 字段 / not-a-repo / review-diff）
- `web/tests/chat-thinking-count.test.ts` — 4 cases
  （捕获 / 渲染 / 缺失时降级）
- `web/tests/subagent-store.test.ts` + `subagent-panel.test.tsx`
  （store 状态机 / panel 渲染）
- `web/tests/git-store.test.ts` + `git-status-bar.test.tsx`
  （store API / 组件渲染）
- `e2e/smoke-subagent.spec.ts` + `e2e/smoke-thinking-count.spec.ts`
  （端到端真浏览器跑通）
- `e2e/smoke-boot.spec.ts` 扩展 git-status-bar 断言

### Known limitations
- **`_review_diff` 是 mock-mode analyser** —— 当前实现是确定性 diff walker，
  不调真 LLM。wire shape 稳定所以可以无破坏地升级到 LLM 路径，留作 v0.3.1。
- **`git.*` 是 read-only** —— 不暴露 `git add` / `commit` / `push`，
  这些仍走用户自己 shell。

## [0.2.0] - 2026-06-03

**Drop Tauri, go full web.** v0.1.x 阶段项目以 Tauri 2.x 桌面壳为承载
（NSIS / MSI 双包，启动期踩过 `app.manage()` race / sidecar 路径 /
`tokio pipe flush` 三个 bug，详见 v0.1.1 / v0.1.2 / v0.1.3 三段 hotfix）。
v0.2.0 起切换为 **web SPA + 本地 Python agent** 形态：

- **为什么删 Tauri**：v0.1.x 的桌面壳没有真正的安装必要性 —— 项目本来就是
  内部工具（只 LAN 内几个人用），Tauri 带来的"轻量 webview 桌面壳"价值
  不如它引入的"必须出 MSI / NSIS / 必须装 Rust 工具链 / 启动 race 难调"
  成本。直接打开浏览器跑 `localhost:5173` 更省事，热重载和调试都更标准。
- **保留什么**：v0.1.x 阶段实现的 Phase 1–6 全部功能（多轮对话 / 技能
  系统 / 定时任务 / 多 Agent / 移动配对 / 授权管理 / 进度面板 / 端到端
  chat / 模型选择 / 子 Agent 真 LLM / 设置页 / 权限真弹窗 / 密钥 keyring
  / 前端三栏布局）一字不动地进入 v0.2.0。Python agent 核心 / SQLite 存储
  / 7 个 e2e smoke / IPC handler registry 全部保留。
- **改了 transport**：从前端通过 Tauri `invoke` 调 Rust 再 stdio 转 Python
  sidecar，改成前端直接 `fetch POST /rpc` + `WebSocket /ws` 打到 Python
  agent 的 FastAPI 薄 transport。两套 transport **共享同一份 handler
  registry**（`IPCServer.handle_request` + `IPCServer.register_listener`），
  不是平行两份实现。Stdio 模式保留给 tests + CLI（`--stdio` flag）。

### Added
- **Agent HTTP + WebSocket transport**（`agent/minimax_code/ipc/http_server.py`）：
  FastAPI 薄 transport，bind `127.0.0.1:8765`，暴露 `POST /rpc` /
  `GET /ws` / `GET /health`（SSE fallback `GET /events`）。CORS allow-list
  仅 `http://localhost:5173`（Vite dev）。复用 `IPCServer` 的 handler
  registry —— POST 收到 envelope 后直接 `await server.handle_request(env)`，
  流式事件经 `register_listener(cb)` 推到所有 WebSocket 客户端。
- **Web IPC client 重写**（`web/src/ipc/client.ts`）：`IPCClient.request` /
  `IPCClient.on` / `IPCClient.ping` 内部切到 `fetch` + `WebSocket`，
  公开 `TypedIPC` 形状（`ping` / `listSessions` / `createSession` /
  `sendMessage` / `listModels` 等）零变化，React stores 一行未动。WS
  reconnect 走指数退避（250ms → 500ms → 1s → 2s, cap 5s）。`useMock`
  fallback 走 `/health` 200ms 探针 + 既有 in-process `mockHandle`。
- **Playwright 跨栈 e2e**（`tests/e2e-web/` + `pnpm test:e2e`）：接替
  v0.1.x "Tauri 桌面端 e2e 未在已安装包上跑" 这个 known limitation。真
  浏览器（Chromium）跑 web 端，真起 agent（HTTP 模式），完整覆盖会话
  创建 → 消息发送 → 流式响应 → 状态更新链路。
- **设计文档** [`docs/v0.2.0-web-architecture.md`](docs/v0.2.0-web-architecture.md)：
  v0.2.0 切换的 API 契约（端点表 / 错误码 / CORS / 环境变量 / dev workflow
  / 删什么 / 留什么）的 source of truth，所有平行 worker（HTTP server /
  web client / e2e / docs）开工前先读。
- **`dev.mjs` 双模式**：`pnpm dev` 默认通过 `concurrently` 同时拉起
  agent + Vite；`AGENT_SKIP=1 pnpm dev` 只起 Vite（agent 单独跑）。

### Removed
- **Tauri 桌面壳**（`src-tauri/`）：整个目录已删 —— Rust 源码、
  `Cargo.toml`、`tauri.conf.json`、icons、`target/`、`tauri.conf.json`
  配置、`build.rs`、`src/main.rs` / `ipc.rs` / `commands.rs`。v0.1.0 →
  v0.1.3 的 4 个 NSIS / MSI 安装包随 `src-tauri/` 一起从源码树消失；
  历史 artifact 留在 git 历史（如需可 `git show v0.1.3:src-tauri/...` 找回）。
- **`@tauri-apps/api` / `@tauri-apps/cli`** 依赖：从根 `package.json` 和
  `web/package.json` 删除。
- **`tauri:dev` / `tauri:build` / `tauri`** npm scripts。
- **Tauri Rust 工具链**作为前置条件（Visual Studio Build Tools / WiX
  3.x / `cargo install tauri-cli@^2`）—— 普通用户 clone 仓库 + 两终端
  命令即可。
- **Known limitation #0**（"v0.1.0 / v0.1.1 / v0.1.2 启动 race"）和
  **Known limitation #1**（"Tauri 端到端 e2e smoke 未在已安装包上跑"）
  —— 前者随 Tauri 删除一并消失；后者被 Playwright 跨栈 e2e 接替（见
  Added）。

### Changed
- **Dev workflow 从 3 终端简化为 2 终端**：
  - 终端 1（agent）：`cd agent && uv run python -m minimax_code`
    → `agent server listening on http://127.0.0.1:8765`
  - 终端 2（web）：`pnpm dev` → `vite ready`
  - 浏览器开 `http://localhost:5173`
  - 原来的终端 3（`cd src-tauri && cargo tauri dev`）整个消失。
- **IPC 契约**（`docs/ipc-contract.md`）：Transport 段重写为双模式
  （stdio 给 tests + CLI；HTTP + WebSocket 给 web client）；端点表
  加 `GET /health` / `POST /rpc` / `GET /ws` / `GET /events`；lifecycle
  段从 "Tauri-level `ipc:sidecar` 事件" 改为 "WebSocket `agent.ready` +
  `/health` 探针 + stdio EOF"；test surface 表加 Playwright 行；version
  对齐从"Tauri `Cargo.toml` + agent `__init__.py` 两处"改为
  "root `package.json` + `web/package.json` + agent `__init__.py` 三处"。
- **架构文档**（`docs/architecture.md`）：技术栈表删 Tauri 行，加
  "Transport: HTTP + WebSocket (FastAPI on agent)" 行；架构图把
  "Tauri Window (WebView)" 换成 "Browser (Chromium / Firefox / Edge) +
  Vite-served React SPA"；目录树删 `src-tauri/`；关键技术决策加
  "打包 = 无（内部 web 工具）"；Phase 划分加 "v0.2.0 切换（删 Tauri）"
  段。
- **README**：头部 banner 改为"v0.2.0 内部版 — 全面 web 化，丢掉 Tauri
  桌面打包"；"安装"段换成"快速启动（dev mode — 两终端）"；"前置环境"
  段去掉 Rust / Tauri CLI / Visual Studio / WiX；"测试"段加
  `pnpm test:e2e`（Playwright）；"出包"段改为"内部无打包流程；要分发
  就 git clone + 两终端"；Known limitations 段重排版（剩 3 条：
  `thinking_count` / sub-agent LLM mock / 旧 README 收尾）。

### Notes
- **不动 git tag**：`v0.1.0` / `v0.1.1` / `v0.1.2` / `v0.1.3` 四个 tag
  保留在 git 历史作为"v0.1.x Tauri 时代的归档"。本 changelog 的 v0.1.x
  段也是历史记录。**`v0.2.0` tag 由 owner 决定何时打，team 不会写**。
- **不动 Python 代码 / web 代码**：本轮 docs 切换只改 4 个文件
  （README / architecture / ipc-contract / 本文件）。其他 4 个 worker
  分头实现 HTTP server / web client / Playwright e2e / 删 `src-tauri/`。
- **CORS / auth**：v0.2.0 不做多用户 / 远程；单用户、本机、`127.0.0.1`
  only，无 token 鉴权。需要时再加。

## [0.1.3] - 2026-06-03

**v0.1.2 hotfix.** Fixes the `tokio::process::ChildStdin::flush()` race
that broke first-launch IPC in v0.1.2. **Internal users who installed
v0.1.2 must reinstall v0.1.3** — the first batch of IPC calls
(session.list, model.list, agent.list, permission.list) failed on
mount with `failed to flush agent stdin: 管道正在被关闭 (os error 232)`.
UI rendered + chat.send_message worked later, but the data-loading
toasts persisted and the agent list stayed on "Loading agents...".

### Fixed
- **`send_to_agent` removed `stdin.flush().await`.** `tokio::process::ChildStdin`
  wraps a raw OS pipe with no userspace buffer; `write_all` is sufficient.
  The `flush` call triggered an internal sync that saw the child end
  as "closing" during the brief window between sidecar process spawn
  and the asyncio IPC server reaching "waiting for messages" state
  (~50 ms). Bytes had already been written to the kernel pipe buffer
  but `flush` returned `ERROR_NO_DATA` (os error 232, "pipe is being
  closed"). The protocol framing (`\n` terminator) and the OS pipe
  buffer (≥ 4 KB) handle any transient backpressure.

### Changed
- `tauri.conf.json` + `src-tauri/Cargo.toml` version bumped `0.1.2`
  → `0.1.3`.
- README install section updated with v0.1.3 SHA-256 + file sizes.
- "Known limitations" expanded to cover the v0.1.0 / v0.1.1 / v0.1.2
  chain (manage race → sidecar path → pipe flush race), with v0.1.3
  fixing all three.

### Artifacts
- MSI: `MiniMax Code_0.1.3_x64_en-US.msi` (3.93 MB / 4,124,672 B)
  — SHA-256 `74FA6F5983D13EB129F8187D53C86C18DC66973D4CF6F4DA4F48684BED766F84`
- NSIS: `MiniMax Code_0.1.3_x64-setup.exe` (3.18 MB / 3,331,122 B)
  — SHA-256 `088D6D0DCE5393787C5901F64D4445B038BC500B5E142B1E26E773CB1F92B32A`

## [0.1.2] - 2026-06-03

**v0.1.1 hotfix.** Fixes the sidecar path resolution that made v0.1.1
white-screen and exit on launch. **Internal users who installed v0.1.0
or v0.1.1 must reinstall v0.1.2** — both earlier builds are unusable
in production.

### Fixed
- **Sidecar path resolution in `ipc::sidecar_command`.** Tauri 2.x
  `externalBin` configuration places the bundled sidecar at the
  resource root (install dir on Windows), named WITHOUT the
  target-triple suffix. The previous code looked for
  `binaries/minimax-code-agent.exe` (with `binaries/` prefix), which
  does not exist in the bundle. In production, `app.path().resolve(...)`
  failed, and the code fell through to the dev-mode Python fallback —
  but production users don't have `python` in PATH and the agent
  module isn't installed. `init_agent_bridge` returned `Err`,
  `setup()` returned `Err`, Tauri refused to start the app, and the
  user saw a white window that exited immediately. Fix: resolve
  `minimax-code-agent.exe` (no prefix) at `BaseDirectory::Resource`,
  which matches Tauri 2.x's `externalBin` bundling convention.

### Changed
- `tauri.conf.json` + `src-tauri/Cargo.toml` version bumped `0.1.1`
  → `0.1.2`.
- README install section updated with v0.1.2 SHA-256 + file sizes.
- "Known limitations" expanded to cover both the v0.1.0 manage() race
  and the v0.1.1 sidecar path bug, with a single combined
  "v0.1.0 / v0.1.1 launch crash" section explaining how v0.1.2 fixes
  both.

### Artifacts
- MSI: `MiniMax Code_0.1.2_x64_en-US.msi` (3.93 MB / 4,124,672 B)
  — SHA-256 `1DAA16151C6BF5F36180728F59ED0BD467C131A93E489D74D52D9A45FC10E32E`
- NSIS: `MiniMax Code_0.1.2_x64-setup.exe` (3.18 MB / 3,333,019 B)
  — SHA-256 `2DD04D11B24AD7D58A7B989F3D6634E3D49587AA351B7253020DCE7C54216C2D`

## [0.1.1] - 2026-06-03

**v0.1.0 hotfix.** Fixes the Tauri 2.x `app.manage()` race that broke
first-launch IPC in v0.1.0. **Internal users who installed v0.1.0 must
reinstall v0.1.1** — the v0.1.0 build is unusable out of the box.

### Fixed
- **Tauri `app.manage()` race on first launch.** `src-tauri/src/lib.rs`
  `setup()` originally called `ipc::spawn_agent_sidecar(handle)`,
  which fire-and-forget spawned a background task that eventually
  called `app.manage(AppState { ... })`. The webview mounted before
  the task reached the `manage` line, so the React app's first batch
  of IPC calls (`session.list`, `agent.list`, `model.list`,
  `permission.list`, etc.) all failed with `state not managed for
  field 'state' on command 'ipc_request'`. Fix: replaced
  `spawn_agent_sidecar` + `run_bridge` with `init_agent_bridge` —
  synchronously spawns the child, calls `app.manage(...)` in the
  setup closure, then spawns a long-lived `pump_stdio` task. The
  state is registered before the setup closure returns.
- Removed unused `tauri::Manager` import in `lib.rs` (post-fix
  cleanup).
- Removed unused `std::sync::Mutex` import in `ipc.rs` (pre-existing
  dead import, cleaned up alongside the refactor).

### Changed
- `tauri.conf.json` + `src-tauri/Cargo.toml` version bumped `0.1.0`
  → `0.1.1`.
- README install section updated with v0.1.1 SHA-256 + file sizes.
- Tauri release e2e "known limitation" softened: v0.1.1 is now
  manually verified on installed package (the bug above was the
  only thing blocking the installed-package smoke); Playwright /
  WebDriver automation still v0.1.2 scope.

### Artifacts
- MSI: `MiniMax Code_0.1.1_x64_en-US.msi` (3.93 MB / 4,124,672 B)
  — SHA-256 `F8C8840AB8EB3858E56F40728F38F4431E08224349C0E50798072870DE7562EB`
- NSIS: `MiniMax Code_0.1.1_x64-setup.exe` (3.18 MB / 3,332,302 B)
  — SHA-256 `DC544C02ACA9C955A869C325D45247FFE27F5B7DD22855F939FBBD088F525F7B`

## [0.1.0] - 2026-06-02

**First internal release.** Full Phase 1 → Phase 6 implementation. Windows
NSIS + MSI installers built and SHA-256 verified. Tauri 2.x desktop shell +
React 18 frontend + Python asyncio agent core + SQLite storage + 8 DAOs + 7
IPC namespaces + 7 e2e smokes (all green).

### Added

#### Storage & data layer (Phase 1)
- 8 SQLite tables: `sessions`, `messages`, `tasks`, `skills`, `scheduled_jobs`,
  `permission_rules`, `mobile_devices`, `agents`
- Migration runner + initial migration `001_initial.py`
- DAOs: `SessionDAO`, `MessageDAO`, `TaskDAO`, `SkillDAO`,
  `ScheduledJobDAO`, `PermissionRuleDAO`, `MobileDeviceDAO`, `AgentConfigDAO`
- Singleton-DAO pattern with sync/async dual-path support

#### Skills system (Phase 1)
- `SKILL.md` loader + registry + runtime
- 3 builtin skills
- `skill.list / skill.invoke / skill.refresh` IPC handlers
- 27 unit tests for skills

#### Agent core (Phase 1)
- `MiniMaxClient` (httpx async) with mock-mode fallback
- Conversation loop with tool-call dispatch
- 6 tools: `file_ops`, `terminal`, `edit`, `search`, `skill`, `meta`
- JSON-RPC 2.0 over stdio IPC server
- `agent.send_message / agent.cancel / agent.history` IPC handlers

#### Auth & permissions (Phase 2a)
- `PermissionStore` with consent flow
- `permission.list / permission.request / permission.set / permission.delete`
  IPC handlers
- Cache + restart-persistence

#### Scheduler (Phase 2a)
- APScheduler integration with persistence
- `schedule.list / schedule.create / schedule.delete / schedule.toggle`
  IPC handlers

#### Progress tracking (Phase 2a)
- `TaskDAO` + `ProgressTracker` (3-layer: track → step → task)
- `task.list / task.get / task.subscribe` IPC handlers
- Streaming progress events over IPC

#### Sessions & history (Phase 2b)
- `SessionDAO` with `list / count / create / get / archive / search`
- Filter-aware `count()` so `list.total` matches the page
- Explicit `session.create` IPC handler
- 17-step Phase 2b integration e2e smoke

#### Mobile pairing (Phase 2b)
- Pairing code generation + device registry
- Long-lived auth tokens
- `mobile.pair / mobile.list_devices / mobile.revoke` IPC handlers
- Mobile handler uses singleton DAO

#### Multi-agent (Phase 2b)
- `SubAgentRuntime` with config DAO
- Sub-agent `invoke` IPC handler + streaming events
- Race-safe `upsert` with tests
- Real-LLM injection support: `inject_llm(MiniMaxClient)`

#### End-to-end chat (Phase 3)
- `agent.send_message` real wire-up to `AgentCore`
- Mock LLM mode triggered by empty `MINIMAX_API_KEY`
- Message persistence
- `smoke_chat` e2e test (subprocess-driven)

#### Model selection (Phase 4)
- `ModelPrefsDAO` + migration `002_model_prefs.py`
- 3 candidate models hardcoded in handler (PoC phase)
- `model.list / model.get_current / model.set_current` IPC handlers
- 16 unit tests

#### Sub-agent LLM (Phase 4)
- `SubAgentRuntime` accepts injected `MiniMaxClient`
- `smoke_agents` accepts sub-agent's real-LLM mock response
- Tauri app icon assets (SVG + generated PNG/ICO)

#### Settings page (Phase 5)
- Settings page with 3 tabs: Model / Permission / Schedule
- API Key tab with keyring round-trip
- Tool-call consent modal end-to-end (real-time permission flow)
- `permissionStore.upsertRule` return-shape fix

#### OS keyring (Phase 5)
- `secrets` module: OS keyring with env-var fallback
- `handlers_secrets` IPC + 10 unit tests
- Frontend `secretStore` Zustand store

#### UI panels & layout (Phase 6)
- Skills panel in sidebar
- Redesigned task history sidebar
- Three-pane layout: chat | progress | agent team
- Workspace switcher in top bar
- Per-turn summary in chat
- Always-allow inline button in tool-call consent
- Model picker inline in input
- Floating chat input

#### Tauri release build (Phase 6)
- `cargo tauri build` produces both NSIS and MSI installers
- Windows MSI: `MiniMax Code_0.1.0_x64_en-US.msi` (3.86 MB)
- Windows NSIS: `MiniMax Code_0.1.0_x64-setup.exe` (3.18 MB)
- SHA-256 verified for both artifacts

### Changed
- README rewritten for end-of-Phase-3 (and again for v0.1.0)
- Project layout documented for multi-DAO + multi-IPC-namespace scale
- `count()` semantics now consistent with `list()` filter args (Phase 2b fix)

### Fixed
- `count()` honors search filter (Phase 2b) — `list.total` is now filter-aware
- `upsert` race condition in `AgentConfigDAO` (Phase 2b)
- Singleton-DAO enforcement in mobile handler (Phase 2b)
- `permissionStore.upsertRule` return shape regression (Phase 5)
- Tauri build linker issue resolved by switching to a Windows host with
  MSVC Build Tools (was a previous Phase 3 environmental blocker)

### Known limitations
- **Tauri release e2e not run on installed package.** All 7 black-box
  subprocess smokes pass against the dev agent, but `MiniMax Code_0.1.0_x64-setup.exe`
  was not installed + Playwright-automated on a clean machine. PoC decision:
  defer release-verification to first install user / v0.2.
- **`thinking_count` metadata field not implemented.** Web-side
  `MessageMetadata.thinkingCount` is reserved in the type but the agent does
  not emit it; UI falls back to 0. Phase 7 will wire real MiniMax thinking-token
  counts.
- **Sub-agent default is mock mode.** `SubAgentRuntime` default is
  `AgentCore(llm=None)`. Tests and `smoke_agents` use `inject_llm()` to
  supply a real `MiniMaxClient`. Production deployment will set the default
  to use the user-configured API key.
- **No remote / no auto-update.** This is an internal v0.1.0; no GitHub
  release artifacts, no Tauri updater wired. v0.2 plan: add GitHub Releases
  + Tauri auto-update.

[0.2.0]: #020---2026-06-03
[0.1.3]: #013---2026-06-03
[0.1.2]: #012---2026-06-03
[0.1.1]: #011---2026-06-03
[0.1.0]: #010---2026-06-02
