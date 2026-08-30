# Changelog

All notable changes to MiniMax Code are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **team 路径沙盒化**（迭代优化计划 R2：多 Agent 深化，v1.5.0 写安全专项公示的第四项已知限制就此收口）：`team.spawn` 收可选 `sandbox: boolean`（缺省梯子：显式传参 > `MINIMAX_CODE_SANDBOX_DEFAULT` env > false，与 `spawn_subagent` 同规）。开启后每个成员写入各自的确定性 run 沙盒树 `team_<task_id>_<idx>_<name>`（复用 v1.5.0 全套底座：COW `_base/` 快照 + overlay 读 + 写重定向 + fail-closed 建目录 + `SANDBOX_PROTOCOL_PROMPT` 前置注入 request——team config 是共享对象，review 会重跑同一 config，mutate system_prompt 会逐次叠加）；`_run_single_agent` 以 `set_current_run_id`/`set_sandbox` ContextVar 激活，finally 按工具路径同序释放（write claims → run id → sandbox）。**编排器自动 collect**（skip 策略，advisory 永不杀 run）：sequential 每成员跑完即收（后继 overlay 得见前驱写入）、review 模式 writers 全收后再跑 reviewer（reviewer 读 workspace，合并必须先行）、其余模式团队末尾统一扫尾；`collect_subagent` 核心提取为 `sandbox.py` 模块级 `collect_sandbox_run()`（工具壳留参数校验 + in-flight guard，wire 形状与消息字符串逐字不变）。reply / `agent_runs.metadata.team_result` 增 `sandbox` + 条件性 `sandbox_summary`（collected/merged_files/conflicts/errors/skipped_runs/skipped_files）与 `agents_run[].run_id`（仅沙盒成员携带，legacy 元数据字节可比）；冲突/跳过时 `merged_text` 前置 `> ⚠ sandbox collect` advisory。前端契约同步：`typed.ts` `spawnTeam` opts/返回类型、`mock.ts`、`teamRunStore`（result 存 `sandbox`/`sandbox_summary`，R3 TeamRunPanel 消费候选）。+10 测试（`agent/tests/test_team_sandbox.py`：run_id 确定性与名字消毒 / 默认零痕迹 / 预置 mirror 全链路 collect+prune / skip 冲突保 workspace+advisory / sequential 成员间时序 / review writers 先于 reviewer / ContextVar 复位 / handler env 缺省与显式覆盖 / reply 形状）。
- **Checkpoint 前端状态 store 化**（迭代优化计划 R1：演进缺口收尾）：新 store `web/src/stores/checkpoint.ts`（`useCheckpointStore`，stores 29→30）——checkpoint 列表按 session 缓存（re-mount 零请求）、diff 文本按 checkpoint id 缓存、展开态/加载态/错误态全部上移；CheckpointPanel 改薄组件（label/message 表单草稿留 useState），RightPanel tab 切换不再丢状态、切回不再重复请求；create/delete 成功后 `invalidate(sessionId)` 强刷列表并清掉已删 checkpoint 的 diff 缓存；加载失败写 per-session `loadError`（内联横幅 + 重试），不再把空列表误读为「无 checkpoint」。+9 测试（`stores/__tests__/checkpoint-store.test.ts`：缓存命中不发请求 / invalidate 强刷 + 陈旧 diff 剪除 / diff 缓存与错误粘滞 / 无 diff 哨兵）。
- **SubAgentPanel 重启回填**（v1.4.0 已知限制「agent 重启后事件态丢失」收口）：`useSubAgentStore` 新增 `hydrate()`——`init()` 订阅事件后调 `run.list {mode: "subagent"}` 从 `agent_runs` 落库行回填面板。映射规则：终态（completed/failed/cancelled）直映，非终态（running/planning/awaiting_approval）→ `"started"`（agent 重启后残留的 running 行几乎必是孤儿，灰色 idle pill 比永不推进的 spinner 诚实）；ISO 时间戳 → epoch 毫秒；title 去 `[subagent] ` 前缀作 summary；`metadata.result_text` 回填详情 text、`metadata.parent_session_id` 供会话过滤（subagent 行挂独立 sub_session）。**live 事件优先**：已存在同 run_id 的行不覆盖；回填失败 fail-open（事件流照常）。+8 测试（`stores/__tests__/subagent-hydrate.test.ts`：wire 形状 / 三类终态与非终态映射 / 无 metadata 容错 / live 优先 / parent 过滤 / fail-open）。

### Fixed

- **手机视口下检查器按钮误现**（backlog B4：移动端适配·实测发现）：TopBar 的 Inspector 按钮类名 `md:inline-flex lg:hidden` 缺默认 `hidden`——&lt;768px 手机视口两个断点类都不激活，按钮回落默认显示；点击后 InspectorDrawer 虽有 `lg:hidden` guard 会 CSS 隐藏，但按钮本身就不该出现在手机上（检查器是 md–lg 平板区间的专属逃生门）。修复：补 `hidden md:inline-flex lg:hidden`。由新 e2e 移动视口 spec 抓出（375 宽断言按钮 hidden 失败定位）。
- **危险命令检测的跨平台漏检**（backlog B1：平台差异测试收口·实现项）：`terminal.py` `_is_dangerous_cmd` 的扩展名剥离被 `sys.platform == "win32"` 门住——Linux/macOS 上跑 agent 时，Windows 风格 argv（如 `C:\Python312\python.exe -c ...`）剥路径后仍是 `python.exe`，不匹配危险词表即漏检。修复：`.exe/.bat/.cmd/.com/.ps1` 已知 Windows 可执行扩展**全平台**剥离（新 `_strip_executable_name` helper），Windows 上任意其他带点名保留原剥离语义；POSIX 点缀名（`python3.12`）不受影响。+1 直测用例（Linux 命中 python.exe/python.bat/sudo.cmd + python3.12 放行）。
- **7 项 Windows 语义用例跨平台收口**（backlog B1：测试项，v1.6.1 备案的 Linux 容器 7 failed 清零，全量 10611 passed / 9 skipped / 0 failed 首次单平台全绿）：① `test_registry_folds_path_case` / ② `test_symbol_location_backslash`——纯 Windows FS 语义（normcase 折叠 / 反斜杠分隔符），`skipif` 非 win32 显式声明；③ `test_path_prefix_stripped`——随上述实现修复转绿，双断言全平台跑；④ `test_exec_default_cwd_is_session_root`——容器无 `python` 命令的环境差异（非平台语义），改 `sys.executable` 两平台都跑；⑤ `test_hunk_value_equality`——值相等断言不该依赖时钟粒度（Windows 粗粒度两次 `now()` 同值、Linux 高精度必不等），`created_at` 固定字面值；⑥ `test_mcp_add_and_list_server`——add_server 真实 attach（spawn npx），断言环境自适应（无 npx 必 False，有 npx 验 bool 形态）；⑦ `test_attributed_foreign_write_not_flagged`——**根因更正**：非备案的「mtime_ns 精度」，实为注入 `sleep(0.05)` 相对子进程生命周期的双向时序竞态（快机 emit 晚于减除扫描；朴素哨兵又早于 watermark 读取），改两级握手（子进程落 workspace 外 ready 文件 → injector 见 ready 写 go + emit → 子进程过关卡写文件）把 emit 确定性钉入归因窗口 `(watermark, post-walk)`，连跑 3 次稳定。`docs/performance-baseline.md` 平台差异备案段同步重写为处置对照表（含 ⑦ 根因更正）。

### Changed

- **进度面板重设计**（backlog B3：ProgressPanel 信息架构与体验升级，六项）：① **title 主行**——后端 `TaskRow.title` 前端首次消费（`TaskProgressEntry` 扩展 `title`/`session_id`/`created_at`，`entryFromRow` 取值；live `task.progress` 事件不携带这些字段，upsert 的 spread-prev 与 result 同模式保留不被 clobber），卡片标题从 mono task_id 换为人类可读 title（无 title 回退 task_id），task_id 降为次行元信息；② **分区列表**——`partitionTasks()` 纯函数把任务分「运行中」（running/pending）与「最近完成」（done/error/cancelled）两小节，各自按 updated_at 降序，不再混排；③ **状态徽章中文化**——5 态映射入 `strings.ts`（运行中/等待中/已完成/失败/已取消），消除组件内英文 status 直出（与 strings 规范对齐）；④ **时间信息**——每卡片次行显示相对时间（`formatRelative`）+ 终态耗时（新 `lib/time.ts` `formatDuration`：<1h 输出 `MM:SS`、≥1h 输出 `X 小时 Y 分`，锚点为 ledger 的 created_at→updated_at）；⑤ **详情可展开**——result/error 正文从 `max-h-16` 死截断改为 `line-clamp-2` + 点击展开/收起（`aria-expanded`），error 的 message 同样进详情区；⑥ **进度百分数**——进度条右端显示 `NN%`。testId 契约保持（`pp` 命名空间 / `task-row-<id>` / `task-status-<status>`），列表 testid 拆 `-task-list-live`/`-task-list-settled`。+8 测试（title 展示与回退 / 分区归属与顺序 / 小节内排序 / 耗时 / 中文徽章全覆盖 / 百分数 / 展开-收起）。
- **right-panel 硬编码文案收敛**（迭代优化计划 R3：前端体验打磨·切片 A，规范修复）：right-panel 5 组件的 10 处硬编码中文文案迁入 `strings.ts` rightPanel 域既有分组（agents/progress/codebase/subagents/checkpoint），文案原文不变仅搬家，对齐「面向用户文案统一来自 src/ui/strings.ts」的模块规范（web/CLAUDE.md）。SubAgentPanel 空闲召唤文案因内嵌 `font-mono` 的 `@general` span 拆 idleHintPre/idleHintPost 两 key（strings.ts 保持纯字符串模块）。
- **TeamRunPanel 沙盒运行可视化**（R3·切片 B）：RunCard 消费 R2 落地的 `result.sandbox`/`sandbox_summary`——① 沙盒运行的卡片标题旁渲染「沙箱」徽章（accent 描边小标签）；② 合并统计行「沙盒已回收 N 个运行 · 合并落盘 M 个文件」（collected/merged_files）；③ skipped_runs 非空或 errors>0 时渲染黄色 AlertTriangle 警示行（与 conflicts 同视觉级；后端 collect 为 skip 策略 advisory，黄色而非红色符合「运行成功但有文件滞留沙盒」语义）。文案 5 key 全入 strings.ts teamRuns 组。HistoryRow 不动（`run.list` 数据源无 sandbox_summary 字段）。+3 测试（徽章+统计行 / skipped·errors 警示行 / 无 summary 零渲染）。
- **evolution 账本归档对齐**（R1 三标的之一）：`docs/evolution/ITERATION_LOG.md` 尾部追加归档声明——回合制账本（R1→R310，Grok 融合专项，基准 v0.8.0 → 目标 v0.9.0）就此封卷，v1.0.0 起权威变更账本 = 根 `CHANGELOG.md`，附回合↔版本对照表与 v1.x 变更查询路径；`docs/evolution/EVOLUTION_ROADMAP.md` 头部标注只读历史档案。消除「evolution 目录像是仍在活跃维护」的误导。
- **移动视口 e2e 回归**（backlog B4：移动端适配·验证收口）：新 spec `e2e/smoke-mobile-viewport.spec.ts`（第 11 个 e2e spec）——375×667 手机档（hamburger 开 Sidebar 抽屉 + 遮罩关闭 / Inspector 按钮隐藏 / chat 主区与 composer 无横向溢出且可输入）+ 820×1180 平板档（hamburger 隐藏 / Inspector 按钮开合 InspectorDrawer + backdrop 关闭 / 无横向溢出）。溢出审计结论：双档 `scrollWidth ≤ clientWidth` 全过，布局骨架（viewport meta、&lt;768px 抽屉、md–lg InspectorDrawer、max-w-[780px] 弹性约束、CodeBlock/Mermaid overflow-auto）健康，零溢出修复需求。配套 jsdom `tests/app-mobile.test.tsx`（3 用例：抽屉默认不渲染 / hamburger 开 + 遮罩关 / 抽屉内导航自动收抽屉并开 overlay）。

（R1 全量验证：vitest 768 全绿（基线 751 + 17 新增）、ESLint 零告警、`tsc -b` 零错误；后端零改动。）

（R2 全量验证：pytest 10605 passed + 7 个既有 Linux 平台差异红（与 `docs/performance-baseline.md` v1.6.1 备案清单逐项一致，基线 10595 + 10 新增）、ruff 全绿、`tsc -b` 零错误、vitest 768 全绿。）

（R3 全量验证：vitest 771 全绿（基线 768 + 3 新增）、`tsc -b` 零错误、ESLint 零告警、`grep -nP` 中文扫描 right-panel 组件目录零硬编码残留（仅 `__tests__` 断言文件命中，属正常）；期间字号守卫测试（P2#26 最小 11px）抓到沙盒徽章初版 `text-[10px]`，已即时修正。）

（B1 全量验证：pytest 10611 passed / 9 skipped / 0 failed——单平台全量首次全绿，v1.6.1 备案的 7 项 Linux 平台差异红清零；安全回归套件 `-m security` 157 passed 确认 `terminal.py` 危险命令修复零回归。）

（B2 性能专项复测（backlog 第 2 项，2026-08-30）：五项基线全项无 P0 退化——冷启动 median **2.138 s**（v1.6.1 锚点 2.291 s 的 −6.7%，预算 5 s 余量 57%）；首屏 JS **145.7 KB** / CSS **8.3 KB** gzip（预算 200/50 内，R3 增量 +0.9 KB）；懒加载 347 chunks 2865.2 KB 持平；索引审计 15 + WS 重放 8 + 长会话渲染 2 显式复跑全绿。`python -X importtime` profile：冷启动 import 大头 fastapi 425 ms（`openapi.models` 174 ms）属 transport 必要成本，**判定无优化必要**（lazy-import 拆分否决：余量充足，拆分只增复杂度）。数字入 `docs/performance-baseline.md` B2 复测列。）

（B3 全量验证：vitest 779 全绿（基线 771 + 8 新增）、`tsc -b` 零错误、ESLint 零告警；后端零改动、IPC 契约零变更（TaskRow 既有字段纯前端消费）。）

（B4 全量验证：移动视口 e2e 6/6 + 桌面 smoke-boot e2e 5/5（含 narrow viewport 用例，TopBar 修复零回归）、vitest 782 全绿（779 + 3 新增 app-mobile）、`tsc -b` 零错误、ESLint 零告警；溢出审计零修复需求（唯一实测缺陷 = Inspector 按钮误现，已修）。）

## [1.6.1] - 2026-08-29

### Added

- **多 Agent 协作优化 v1**（`b001a63`，optimization_v1 清单 P0-2/3/4/5 + P1-3，审计修复后落地）：① **`shared_memory_put/get/list` 跨 agent 交接存储**——`.minimax/shared_memory/store.json`，workspace + run 双 scope，文件锁互斥，损坏 store 进隔离区而非静默重写；② **iteration budget 运行时默认 8→100 全链路**（DAO upsert / `_config_from_row` / skills runtime，migration 029 上提 migration-010 时代卡在 8 的存量行；handoff nudge 改 env 旋钮 `MINIMAX_SOFT_LIMIT_REMAINING`，默认 8、硬下限 1）；③ **`depends_on` DAG 门控**——后台 spawn 等待上游 run id；未知/已回收依赖视为满足而非死等（修空转到 600s 墙钟）；`waiting_deps` 经统一投影输出；④ **`verify_subagent` 无头验收器**——锚定 workspace 根（`is_relative_to` 而非 `startswith`），超时进程树杀（`taskkill /F /T` / `killpg`），孤儿子进程不再握住管道卡死回收；⑤ **`build_subagent_status` 统一投影**——check/wait envelope 共用一份（running / waiting_deps / completed / cancelled / failed），是 legacy `_snapshot` 形状的超集；finished run 不再误报 running。审计修复：cwd 前缀绕过、双重投影、脏 import、未回收 kill、损坏 store 重写、`__all__` 泄漏。34 个新测试（4 文件，全走 `registry.dispatch`），pytest 10553 / vitest 758 全绿。
- **定时任务 command 载荷分支**（`d8c08b2`）：payload 形如 `{command, cwd?, timeout_s?}` 经 `create_subprocess_shell` 在主事件循环执行，per-stream 输出 20 KB 封顶。失败模型——`error` 键 = 调度基础设施失败（spawn 失败/超时 → `_fire` 置 task 行 failed）；非零退出码是命令结果（`ok=False` 且无 `error`，与 `terminal.*` 同哲学）。需要「做事」的定时任务（跑迭代脚本、刷新索引）不再只能走纯 LLM 对话的 prompt 分支。

### Removed

- **退役设置页「API 密钥」tab**（#137）：legacy 全局 MiniMax key 的 UI 入口撤下，密钥管理统一收敛到「服务商」tab 的 per-provider 体系（builtin-minimax 也是一个 provider，一样配 key）。Web 侧六连删——`SettingsPage.tsx` 的 tab 类型/导航/渲染分支、`ApiKeyTab.tsx` 组件删除、settings barrel export、`strings.ts` 的 `settings.apiKey` 域（26 行）与 `tabApiKey`、命令面板 `设置：API 密钥` 入口；测试同步清理（`settings-split.test` barrel 断言 10→9、`settings-page.test` 删 5 个 api-key tab 测试与 secret mock）。**保留兼容**：`secrets.*` IPC 三方法、`mock.ts` 对应分支、`secretStore.ts` 原样保留（Providers tab 的 key 徽章仍走 `secrets.has_provider_key` 底座）。

### Changed

- **legacy 全局 key → per-provider 槽一次性迁移**（`secrets.py`）：`get_provider_key("builtin-minimax")` 此前每次动态 fallback 读 legacy 全局 key（`minimax-code/api_key` 槽 + env），导致用户在「服务商」tab 点「清除密钥」后 per-provider 槽被删、下次查询又从 legacy 槽把 key 迁回来——**清除操作静默失效**。现在：legacy keyring 槽有值 → 首次读取时拷贝进 per-provider 槽（迁移 copy fail-open，写失败仍返回 legacy 值，下次幂等重试；legacy 条目保留不销毁）；`clear_provider_key("builtin-minimax")` 连带清 legacy 槽（防复活闭环，清理失败 advisory）；env var 保持动态 fallback 永不写盘（部署层显式配置，持久化会冻结 env 变更语义）。5 个新测试进 `test_secrets.py`（迁移拷贝 / 迁移幂等 / 写失败 fail-open / clear 连坐且清除终局 / custom provider clear 不动 legacy）。

### Fixed

- **发版版本锚遗漏（第 7 处）**：`minimax_code/__init__.py` 硬编码 `__version__` 此前不在 bump 清单里（v1.6.0 的 "version bump x6" 漏数），与 pyproject/dist-info 版本脱钩——`test_diag` 的 envelope 断言（`bundle["version"] == __version__`）在任何 bump 之后必红。本次随 v1.6.1 一并 bump，diag 套件 15 passed。
- **子 agent 启用/停用开关静默无效**（`ca50935`）：设置页开关发 `agent.update {name, enabled: false}` 回复成功但行从未变化——`handle_agent_update` 无 `enabled` 分支、`AgentDAO.upsert` 无写路径（`mock.ts` 实现了、真后端从未跟上）。修复：`AgentDAO.upsert` 加 `enabled` 参数（None = 保持不变）+ INSERT 列与 UPDATE SET；`handle_agent_create/update` 对称收参分支。
- **#138 OpenAI transport 对 compat provider 的流式 usage 解析为空（智谱 GLM 路径 tokens 恒 0）**：模型切换链路本身完全正常（切换生效、真 API 调用成功、正文解析正常），但 `openai_transport.py` 的流解析只认 OpenAI 官方的「尾部 empty-choices chunk 带 usage」形状——智谱 GLM（及 DeepSeek 等 compat provider）把 `usage` 挂在**带 `finish_reason` 的同一个 chunk** 上，finish 分支 yield 了硬编码的 `usage={}` → 每条 GLM 回复 `tokens_in/tokens_out` 恒 0、context 指示器归零，用户侧表现为「切了模型没反应」。同时 `delta.reasoning_content`（GLM 思考流字段）被静默丢弃、usage 的 `completion_tokens_details.reasoning_tokens` 从未被提取 → thinking_count 恒 0。修复：新增共享 `_usage_to_dict` mapper（SDK 对象与 raw dict 双形状；`reasoning_tokens` → `thinking_tokens` 键，与 anthropic transport 同约定），finish 分支与 empty-choices 分支统一走它；实测智谱 glm-5.3 从 `{TEXT 空、usage None、thinking 0}` 修复为 `{TEXT '收到'、usage {16,72,88}、thinking 69}`。新测试文件 `test_openai_usage_stream.py`（8 个，用真实 openai SDK + httpx MockTransport 复刻两家 provider 的原始 SSE 报文，防 SDK parse 形状漂移）。
- **1214 modelCode 不存在（v1.6.1 的读侧续集）**：切到第三方 provider（如智谱 GLM）后发消息报 `400 {"code": "1214", "message": "modelCode：不存在"}`，且**无论切什么模型都报同样的错**。v1.6.1 修复了写侧（`model.set_current` 落库 `provider_id`，LLM 单例 rebuild 后 protocol/base_url/key/model 全部正确），但 `agent.send_message` 构造 `AgentConfig` 时不传 `model` → 运行循环每次 stream 都盖 dataclass 默认值 `"MiniMax-M3"` 的章（`core.py: model=self.config.model`）→ 智谱端点收到 MiniMax 的模型名 → 1214。修复：`AgentConfig.model` 与 `context_window` 同源，从 LLM 单例的 `default_model` 镜像（`builtins.py`，`rebuild_subagent_llm()` 每次切模型都同步两者）。回归测试 2 个进 `test_model_provider_routing.py`（mock AgentCore + side_effect 捕获真实 `AgentConfig`，断言单例指向 `glm-5.3` 时 config.model 跟随而非默认值；无单例 fallback 路径填 `default_model()` 永不为空）。
- （v1.6.1 收编）切模型不切 provider：`model.set_current` 省略 `provider_id` 时反查模型属主 provider 落库，第三方模型不再静默记到 `builtin-minimax` 名下烧 MiniMax 配额（`32655f2`）。
- **迁移语义破坏测试隔离点的回归**：`test_ipc.py` 的 token-usage 集成测试红——其 fixture 靠 patch `secrets.get_api_key` 强制 mock mode，但迁移改造后 `get_provider_key("builtin-minimax")` 的 fallback 链改走 `_read_keyring` / `_read_keyring_username` 私有直读（旧实现是 `return get_api_key()`，patch 一个函数名挡住整条链），send_message 路径经 `get_subagent_llm()` → `get_provider_key()` 读到开发机真 keyring 值 → 走真协议 → usage 恒 0。修复：fixture 补 patch 两个内部读函数，测试意图（无凭据端到端）恢复完整。
- **Linux 下调度命令超时会杀死 agent 自身（P1，进程组自杀）**：`self_evolution/payload.py` 的 `command` 载荷分支 spawn 子进程时未开新会话——POSIX 上 `create_subprocess_shell` 的子进程默认继承 agent 的进程组，超时路径的 `_kill_process_tree` 调 `killpg(os.getpgid(proc.pid))` 时把 agent 自己（或 pytest 宿主）一并 SIGKILL。实测症状：`test_scheduler_command_payload` 的超时用例一跑就把 pytest 整个杀掉（exit 137，伪装成 OOM——此前全量套件在 67% 处静默中断的真凶）。修复：spawn 加 `start_new_session=True`（Windows no-op，与 `verify_subagent` 同模式），超时用例 11 passed、全量套件恢复可跑完。Windows 开发机走 `taskkill /F /T` 不受影响，故存量基线从未暴露。
- **`test_send_message_multimodal` 测试顺序依赖（隔离缺陷）**：3 个用例用 `patch.dict(sys.modules, {"minimax_code.agent": MagicMock…})` 顶替真包，但 handler 的 lazy import 链在 mock 域里没有对应的 `agent.tools`（+4 个 codebase 工具子模块）与 `codebase` 包（其 import 期反向依赖 `agent.perception`）的 sys.modules 键——只有当更早的测试已把真实模块预热进缓存时才碰巧通过。全量套件顺序下绿、单文件跑必红（Linux 容器分段跑暴露）。修复：mock 域补 6 个键，单文件 4 passed，用例不再依赖运行顺序。

**Linux 容器测量基线附注**：v1.6.1 全量 pytest 为 7 failed / 10595 passed / 7 skipped——7 个失败全部为断言 Windows 语义的用例（normcase no-op、盘符路径、时钟精度、npx 可用性等），逐项清单与根因见 `docs/performance-baseline.md`「Linux 容器已知平台差异测试」，不计为回归；Windows 全量基线全绿。

## [1.6.0] - 2026-08-26

### Added — Sandbox Deepening 专项（v1.5.0 四项已知限制收掉三项）

v1.5.0 写安全专项发布时公示的四项沙盒已知限制，本期收掉三项（exec 绕过 → 逃逸检测、search/glob/list 不 overlay → overlay 视图、沙盒不 prune → 保守 prune）；第四项 team 路径沙盒牵扯 `team_orchestrator.py` 的 run_id 贯穿改造，独立立项（本期不做）。三子项各自独立成切片可单独 revert，无 DB migration，全部 advisory / 向后兼容（legacy v1.5.x 沙盒目录与 `.merged` marker 原样兼容）：

- **① `exec_command` 沙盒逃逸检测（advisory，不 fail）**：shell 写入（`echo >`、脚本产物、构建输出）绕过沙盒写重定向直接落 workspace，且 `collect_subagent` 三方对比看不到——静默漏合并，子 agent 以为交付了、主 agent 合并不到。现在 `current_sandbox() is not None` 时（主 agent 零开销）：exec 前 pre-walk workspace（`(mtime_ns, size)` 快照，`asyncio.to_thread` 不卡事件循环，窄排除表 `.git/.venv/node_modules/__pycache__/.pytest_cache/.minimax`——不排除 build/dist，构建产物正是要检测的逃逸形态），exec 后 post-walk diff 出 added/modified/removed；**fs_bus 窗口差集减除**防并发误报——水位之后的 `write_file/edit_file/append_file/collect_subagent` 事件且 `run_id != 当前 run` 的路径从 diff 减去（多 run 并行是本专项立标场景）。剩余非空 → 两个返回路径（正常 + timed_out）的 output 均注入 `sandbox_escape: {changed(≤50), changed_count, changed_truncated, warning}`，并按 diff 类别逐路径 emit 合法枚举 kind（created/modified/removed，cause=`exec_command_sandbox_escape` + run_id attribute）→ 主 agent 的 v1.5.1 notes 轮询天然看到。`SANDBOX_PROTOCOL_PROMPT` 第三 bullet 同步改写：逃逸写入不进 collect，见 `sandbox_escape` warning 用 write_file 重写交付物再上报。
- **② search / find / list overlay（消灭视图分裂）+ 存量剪枝修复**：此前子 agent 搜不到自己刚写的沙盒文件（协议教「用 read_file 验证」，视图分裂有界但反直觉）。现在三个只读工具经 `sandbox.py` 新共享 helpers（`sandbox_mirror_files` / `mirror_children`，单点导出）把沙盒镜像并进 workspace 视图：**`search_files`** 沙盒激活时强制 python 引擎（rg 看不到 mirror）、候选集 rel-posix key 去重 union（同名沙盒版覆盖——子 agent 的写是更新的真相）、读取统一走 `overlay_read_target`、single_file 分支同样 overlay；**`find_files`**（glob）walk 后做 mirror union（path 前缀过滤 + rel 投影 + max_depth + 同名覆盖）；**`list_directory`** 单层 entries 并入 mirror children（name 冲突取沙盒版、`path` 投影回 workspace 地址——绝不泄漏沙盒绝对路径、加 `sandboxed: true`、`_base`/marker 隐藏）、沙盒-only 目录放行（`target.exists() or mirror.is_dir()`）。所有结果地址一律 workspace 地址。**存量 bug 顺手修复**：python 引擎剪枝表此前缺 `.minimax`（主 agent 会搜到 backups/sandboxes 内部文件），rg 本就跳隐藏目录故只在 python 路径触发；`find_files` 的 `.minimax` 改为硬排除（walk always-prune + 结果过滤双保险——用户显式传 `exclude_dirs` 整体覆盖默认表时不再泄漏）。`SANDBOX_PROTOCOL_PROMPT` 第四 bullet 从「search/find/list 显示 workspace 视图，看不到你的沙盒写入」改写为 overlay 语义。
- **③ `collect_subagent` prune + receipt 新家（磁盘不再无限增长）**：此前 collect 合并后沙盒镜像目录永不清理。现在：receipt 一律写新家 `<root>/.minimax/sandboxes/.collected/<run_id>.json`（flat sibling 目录——`sandbox_files_written` 锚定 run 目录永不扫到，`run_<hex12>` id 不与点号目录撞名），写成功且 merge 干净（无 conflicts-errors-skipped）则 `shutil.rmtree` 沙盒树（`prune=true` 默认，schema 已同步——`additionalProperties: False`）。**保守门设计**：receipt 没写成不删（无凭证不删数据——删树无 receipt = 毁幂等证据 + 楔死后续 collect 为 "no sandbox found"）；conflicts+fail 永不 prune 永不写 receipt（重跑可能）；skipped 非空保留（沙盒版本是该文件唯一副本）；errors 非空保留。rmtree 失败 advisory（`prune_error` 进 report，merge 仍 ok）+ **already-collected 分支重试 prune 自愈**（防锁文件后残留树）。**legacy 迁移**：v1.5.x 的 `sb_dir/.merged` marker 仍被读取（升级前 collect 的沙盒照常识别为 already-collected）；命中且 prune 时先迁移 receipt 到新家再删树（迁移失败取消 prune——凭证必须活得比树久）；`prune=false` 时 legacy 布局原样保留。already-collected 检查统一前置到 in-flight guard 之后，命中返回 `{**prior, "already_collected": true}`。

### Changed

- `docs/agent-core.md`：§2 工具目录表（`exec_command` 加 sandbox_escape、`search_files`/`find_files`/`list_directory` 加 overlay、`collect_subagent` 加 prune 与 receipt home；`find_files` 此前从未进过工具表，本次补行）；§8a Mechanics 补三条 v1.6 bullet + Known limitations 收窄为两项（exec 不重定向本质未变——advisory 可见但永不合并；team 路径）；§8c 高安全模板 collect 步补 prune 说明；错误矩阵 conflict 行措辞 `.merged` → receipt + 沙盒保留语义。
- `SANDBOX_PROTOCOL_PROMPT` 第三、四 bullet 改写（逃逸重写指引 + overlay 视图语义）。

### 回归测试（40 个新测试；pytest 10519 / vitest 758 全绿）

- `test_exec_sandbox_escape.py`（10）：逃逸检出（python 写文件 → changed）/ 无沙盒零开销（monkeypatch `_scan_workspace` 计数=0）/ 无写入无警 / 窗口内其他 run 写入不误报（fs_bus 差集减除）/ removed 检出 / 窄表钉子（`.minimax` 不报、build 产物报）/ timed_out 路径也带 / emit 形状（FakeBus + 合法枚举 + cause + run_id）/ 50 条截断 / prompt 第三 bullet 钉。
- `test_sandbox_overlay_views.py`（16）：find_files 三例（union / 同名覆盖 / 前缀+深度）+ exclude_dirs 硬排除钉 + search 四例（强制 python 引擎 / mirror 命中去重 / single_file overlay / 主路径 `.minimax` 剪枝回归）+ list_directory 三例（新增投影 sandboxed / 同名取 mirror / 沙盒-only 目录）+ 无沙盒零变化回归钉 + prompt 第四 bullet 钉 + 剪枝表单元 + helper 单元。
- `test_collect_prune.py`（14）：默认 prune 删树 + receipt 形状 / 二次 collect 重放 / prune=false 保留 / conflicts·skip·errors 三门保留 / rmtree 失败 advisory + 二次不重合并（mtime 钉）/ 失败 prune 自愈重试 / receipt 写失败阻塞 prune（无凭证不删数据钉）/ legacy 识别（prune=false 原样）/ legacy 迁移后 prune + 迁移后幂等 / dispatch 接受 prune 参数（schema 钉，bogus 仍拒）/ marker 路径形状 / `sandbox_files_written` 不含 `.collected`。
- `test_subagent_collect.py` 同步迁移五处旧 marker 位置钉子到新家。

## [1.5.2] - 2026-08-26

### Added — 第四轮压测三项残留收口（env 默认 + append 路径 + 进度可见性）

第四轮压测验证报告实测通过 3 项（沙盒完整工作流、in-flight 警告、write_file 增强 return），仍列 3 项未修：沙盒默认 false 每次要显式传参、无 append_file 大文件分片路径、后台子 agent 无进度可见性。本版以三项小改收口，全部 opt-in / 向后兼容，无 DB migration，41 个新测试全走 `registry.dispatch`：

- **① `MINIMAX_CODE_SANDBOX_DEFAULT` env 旋钮**：并行写负载的运维可以把进程级沙盒默认打开，不必教每个调用方拼 `sandbox=true`。优先级：显式传参 > env > false。默认仍 false——静默默认开会把单 agent 快路径变成陷阱（未 collect 的产出物 + 不 prune 的沙盒目录），opt-in 契约不变。`subagents.py` 新增 `_sandbox_default()` helper（truthy 拼写 `1/true/yes/on`），`spawn_subagent` 的 `sandbox` 参数改为 `bool | None = None`，顶部归一化。
- **② `append_file` 工具（写安全三层全接入 + 沙盒镜像 seeding）**：向文件尾部 verbatim 追加（UTF-8 字节保真、不注入分隔符——换行归调用方管；文件不存在则带父目录创建）。继承全部写防护：CAS `expected_sha256`（不匹配 fail 带 `current_sha256`）、BackupManager 快照、`concurrent_writer` advisory、fs_bus 归因（cause=`append_file`，带 `run_id` attribute）。**沙盒镜像 seeding 是 append 特有的正确性关键步**：`redirect_write_target` 只 COW 原件进 `_base/` 不拷贝到镜像路径——"a" 模式打开全新镜像会从空文件起步，后续 `collect_subagent` 三方对比看到 workspace == base 走 merge 分支，不完整镜像覆盖 workspace = 静默数据丢失。`append_file` 打开前先 seed：`write_target != read_target` 且原件存在且镜像不存在 → `shutil.copy2` 原件进镜像；copy 失败 fail-closed（此步非 advisory）。大输出推荐形态：`write_file` 第一片 + `append_file` 后续，防单次工具调用截断。
- **③ `report_progress` 子 agent 进度上报（per-run 注入工具）**：与 `report_completion` 同一注入路径（克隆 registry + allowlist 追加 + system prompt 协议段，主 registry 永不触碰）。子 agent 在**里程碑**（非每步）调 `report_progress(note, percent?)`——percent clamp [0,100]——向 run 的 artifact 目录追加 `PROGRESS.jsonl` JSON 行账本。账本三处可见：`check_subagent` running 响应 / `wait_subagent` 超时响应 / `_snapshot` 与 finished-run envelope 的 `progress` key（`{total, recent[], latest_percent}` 投影）；同时经 `_SUBAGENT_EVENT_ROUTES` 路由推 live `agent.subagent_progress` 事件（复用前端闭合 status union：`status="thinking"` + `summary=note` + `progress` 分数，前端 SubAgentPanel 零改动）。进度是中期、完成是最终——协议段明示不替代 `report_completion`。prompt 拼接顺序变为：agent 模板 → 任务优先级 → completion 协议 → progress 协议 → 沙盒段。
- **④ 高安全并发模板收编文档**：`docs/agent-core.md` 新增 §8c——spawn(sandbox=true) → check/wait → collect 三步编排 + `read_file` → `write_file(expected_sha256)` CAS 环 + `write_file`/`append_file` 分片模板，即第四轮报告验证收敛出的组合用法；§8a 补 append 镜像 seeding 语义；§2 工具目录表同步。

### 回归测试（41 个新测试；pytest 10479 / vitest 758 全绿）

- `test_append_file.py`（15）：tail 追加 + sha 往返 + 字节保真 / create missing + parents / verbatim 无分隔符 / CAS 四路（mismatch 带 current_sha 磁盘不变、match、deleted、malformed）/ 参数与路径校验 / rival 警告不阻塞 + 同 run 静默 / backup / fs_bus cause + run_id / **沙盒 seed 钉**（镜像 = 原内容+append、`_base` = 原内容、workspace 不动）/ 沙盒新文件 / overlay 读反映 append / **collect 端到端**（合并落地原内容+tail）。
- `test_report_progress.py`（13）：JSONL 追加与条目形状 / 校验三路（空 note、坏 percent、clamp）/ 无 root 跳过 / live 事件推送（路由注册 + `_emit_safe` monkeypatch，断言 status/summary/progress/agent_id/parent_session_id）/ 主 registry 无泄漏 / 脏行跳过 / summary 投影形状 / `_snapshot` 注入与 bare 无 key / prompt 顺序源码钉 / 克隆含双工具 / allowlist 源码钉。
- `test_sandbox_env_default.py`（13）：truthy 九拼写参数化 + unset / 行为级 spawn 三例（省略参数继承 env 开 + 沙盒目录真建、显式 false 击败 env、显式 true 存活 env off）。

## [1.5.1] - 2026-08-26

### Added — 共享 workspace 并发感知（第三轮压测 3 项残留收口）

v1.5.0 三层写安全发布后的第三轮并发压测确认主体修复全部实测通过，残留三项（P0-2 沙盒非默认、P2-6 写前无 in-flight 检查、P2-7 主 agent 无 file:modified 感知）以小专项收口。三层全部 advisory——不改任何写路径行为，只补感知面，无 DB migration：

- **A — `spawn_subagent` 工具级沙盒引导（P0-2 的 prompt 侧收口）**：工具 description（LLM 的 spawn 决策入口）从纯功能描述扩为并发写决策指引——子 agent 将写文件（尤其与其他 run 并行编辑同一 workspace）→ `sandbox=true`，写入落私有沙盒零竞态，事后 `collect_subagent(run_id)` 三方对比合并（冲突三 sha 全报不静默覆盖）；只读工作保持 `sandbox=false`。机制 v1.5.0 已就绪且仍默认 false，缺的正是 LLM 在决策点上「知道何时该开」。
- **B — fs_bus → 主 agent ephemeral 通知桥（P2-7）**：新模块 `fsnotify/notes.py`（纯函数）。`AgentCore` 每 iteration LLM 调用前以 seq 高水位轮询 `bus.recent()`（首次 poll 将水位初始化为当前 max——run 从「现在」开始永不回放历史；选轮询而非 subscribe queue：零生命周期管理，无需 try/finally 包裹 run 主体），其他 in-flight run 的文件写入聚合为一条 `[system note] Files changed by other agents` 追加进该次 LLM payload 尾部。契约照抄 v1.1.2 nudge：ephemeral——只进 payload 不进 messages 不持久化，措辞明示模型不得 acknowledge（防历史口癖污染）；同 path 多事件去重保最新，≤8 行 + 溢出行；沙盒镜像路径投影回工作区相对形（`src/a.py (sandboxed by run_x)`，不向 prompt 泄漏沙盒布局）；全程 fail-open（感知永不破坏 run loop）。自过滤按 fs_bus 事件的 `run_id` attribute：双方 None = 主 agent 自己的写（隐藏）；子 agent 天然看到主 agent 的写（双向感知白送）。压测场景「主 agent 正在编辑 `wb_render.js`、子 agent 同时重写它」从此在下一轮 payload 即可见，不再是静默 last-write-wins。
- **C — in-flight 写登记 + `concurrent_writer` 警告（P2-6）**：`file_ops._INFLIGHT_WRITES`（进程级 dict，`os.path.normcase` 折叠 key → run_id，Windows 大小写不敏感）。`workspace_ctx.py` 新增 token 式 `_current_run_id` ContextVar 三件套；`_drive_run` 顶部无条件发布 run_id、finally 先 `release_run_writes(run_id)` 再 reset token（顺序防竞态；挂死 run 合法保留 claim——它可能还在写）。带 run id 的 `write_file`/`edit_file` 顺手 claim 各自触碰的路径；主 agent（run id None）只查警不登记。写前查 rival claim：命中则写**照常成功**，但 output 带 `concurrent_writer: "<run_id>"` + warning（纯 advisory 不阻塞——协调是 LLM 的决策，配合 B 的 note 与 CAS 的 `expected_sha256` 三位一体）。登记 key 用 workspace 原路径：沙盒 run 的合并目标（`collect_subagent` 拷回 workspace）同样是冲突面。

### 回归测试（23 个新测试）

- `test_write_registry.py`（10，全走 `registry.dispatch`）：rival 警告不阻塞 / 同 run 二写静默 / 主 agent 见子 agent claim / release 只清自己 / normcase 折叠 / edit 同款 / 无关文件隔离 / 主 agent 不登记 / fs_bus emit 带 `run_id` attribute（主 agent 写无此 attr）/ `_drive_run` 集成（invoke 内快照断言 claim 归属 run_id、run 结束登记表清空 + ContextVar 复位）。
- `test_fs_change_notes.py`（12）：纯函数面（watermark/自过滤/双方 None/子 agent 见主 agent 写/全过滤仍推水位/去重保最新/沙盒路径投影含反斜杠/8 行截断/空事件）+ drain（水位初始化不产 note / bus None / 异常 fail-open）+ AgentCore 集成（FakeLLM 两轮：note 只出现在第二轮 payload 尾部、persisted 消息零污染）。
- `test_subagent_sandbox.py` +1：description 沙盒引导源码钉（`sandbox=true` / `collect_subagent` / write 三锚点）。

## [1.5.0] - 2026-08-25

### Added — 写安全专项（CAS 乐观锁 + per-run 子 Agent 沙盒 + collect 合并）

并发压测（3 子 agent 并行共享一个 workspace 根）暴露的两项写安全 backlog 独立排期做透（v1.4.2 评估已公示立项承诺）。三层递进，每层独立 opt-in、可独立回退，无 DB migration（沙盒状态纯文件系统，`sandbox`/`files_written` 骑 JSON metadata 列）：

- **层 1 — CAS 乐观锁（全局生效，per-call 可选）**：新 helper `file_sha256(path)`（file_ops.py，流式 1MiB 分块，OSError → None 不抛）。`read_file` 输出加 `sha256`（未截断时 = 全文件磁盘 hash；truncated → null，大文件 CAS 不可用）；`write_file` / `edit_file` 收可选 `expected_sha256`（64-hex）——提供则校验写前磁盘 hash，不匹配 fail 带 `current_sha256` + "re-read the file" 指引（磁盘字节不变），目标已不存在 fail（`file_exists: false`），malformed 提前 fail；输出统一加 `previous_sha256`（覆盖时写前 hash）+ `sha256`（写后）。**所有 sha256 一律从磁盘 bytes 算**——`edit.py` 的 `write_text` 无 newline 参数，Windows 换行翻译使内存字符串 ≠ 磁盘 bytes，内存口径会全盘错位。`edit_file` 的校验插在 read 之后、backup 之前（被挡的 edit 不产生 backup）。TOCTOU 窗口仍在（乐观锁本质，narrowed not eliminated——同 run 并行工具批靠 CAS 二次确认）。
- **层 2 — opt-in per-run 沙盒（`spawn_subagent(sandbox: bool = False)`）**：sandboxed 子 agent 经 `write_file`/`edit_file` 的全部写入透明重定向到 `<root>/.minimax/sandboxes/<run_id>/`，共享 workspace 在主 agent 合并前零污染。三条设计裁决：① **透明 COW 重定向 > 硬拒绝**（硬拒绝对「子 agent 编辑既有文件」主场景不可用，COW 零 prompt 协作成本）——首次触碰 workspace 既有文件时原件 copy2 进 `<sandbox>/_base/<rel>` 作合并基线（O(touched files)，失败降级为按新文件合并）；`.minimax/` 路径 pass-through（防嵌套/自撞）；**fail-closed**：无 workspace root 或沙盒目录创建失败 → `ToolResult.fail`（静默回退共享 workspace 等于没修）。② **overlay 读覆盖 `read_file` 与 `edit_file` 内部读两处**（一个 `overlay_read_target()` helper）——否则 edit 二次读 workspace 原件会静默丢掉第一次沙盒编辑（正是要消灭的 clobber 类）；重定向时 output 加 `sandboxed: true` + `sandbox_path`（`path` 保持 LLM 视角）。③ `_base/` COW 快照作冲突基线（整库 manifest O(repo)、BackupManager 全局非 run 级、git merge-base 依赖 VCS，全不取）。实现：`workspace_ctx.py` 新增 token 式 `_current_sandbox` ContextVar 三件套（同构 `_current_root`）；新模块 `agent/tools/sandbox.py`（沙盒 lookup helpers，绝不 import 兄弟工具模块防循环）；`subagents.py` `SANDBOX_PROTOCOL_PROMPT` 拼接在 completion 协议之后（教子 agent：写入已沙盒化、read_file 反映自己的写入、优先 write_file/edit_file 而非 shell 写）；`_drive_run` 顶部 set / finally reset token——wait=true inline 与 wait=false `create_task` context 拷贝链全覆盖。默认 false，存量 spawn 零行为变化。
- **层 3 — `collect_subagent(run_id, on_conflict)` 合并工具（主 agent 侧，子 agent 不可见）**：对每个沙盒文件三方对比（`_base/` 快照 vs workspace 现值 vs 沙盒镜像，全 `file_sha256` 字节级——二进制天然支持，全程无 read_text）：workspace 未动 → merge；两侧一致 → noop；workspace 独立变更 → **冲突**，三 sha + reason 全报绝不静默 clobber；workspace 被删 → 冲突；独立创建 → 冲突。`on_conflict`：`fail`（默认，拒绝整个 collect、不写 marker——人工解决后重跑幂等，已 merge 文件降级 noop）/ `skip`（保留 workspace 版）/ `overwrite`（应用沙盒版，标 `conflict_resolved: "overwrite"`）。in-flight 拒绝（先 wait_subagent）；`.merged` marker 幂等（二次 collect 返回原 report + `already_collected: true`，不重拷贝）；fs_bus 按 created/modified 批量 emit（cause=`collect_subagent`，fail-open）；per-file OSError 进 `errors` 清单 walk 永不中断。
- **`files_written` 清单五处传播**：`_drive_run` 收尾（root context 仍活时）`sandbox_files_written(run_id)`（fail-open）→ result dict、`completion_metadata`（落 run 行，agent 重启后存活）、`_snapshot`、wait=true envelope、`_lookup_finished_run`——全部条件注入（非沙盒 run 无此 key，输出零噪声）。

### 回归测试（37 个新测试；pytest 10415 / vitest 756 全绿）

- `test_file_cas.py`（12）、`test_subagent_sandbox.py`（14，含 wait=false 后台 ContextVar 链 + fail-closed 双路）、`test_subagent_collect.py`（14，含二进制字节级、fs_bus cause、run 行 files_written、双 collect mtime 不变）。全部走 `registry.dispatch` 全链路（v1.4.1 铁律）。

### 已知限制（本版不做，评估已裁决）

- `exec_command`（shell 写）绕过沙盒重定向——SANDBOX_PROTOCOL 教导优先 write_file/edit_file，不强制（YAGNI）；search/glob/list_directory 不 overlay（视图分裂有界：SANDBOX_PROTOCOL 教「用 read_file 验证」）；team 路径（`teams.spawn` 每 member 新建 runtime、无 run_id）不在本期（后续专项）；沙盒目录合并后不清理（marker 防重复工作，prune 列 v1.6 候选）。

## [1.4.2] - 2026-08-25

### Fixed — 并发压测回报的 system prompt 劫持（子 agent 叛变跟随常设角色）

- **`TASK_PRECEDENCE_PROMPT` 注入**：并发压测实测——`whiteboard-render-engineer`（agents 表 system prompt 写死「实现渲染引擎」）被派发一次性任务「写 B_*.txt」，因 workspace 里的旧 CONTRACT.md「本能被触发」，跑去重写 17KB 的 `wb_render.js`。根因：spawn 注入段只有 completion 协议、没有任务优先级声明，常设角色模板压过首条 user message 的一次性任务。现在拼接顺序为 `agent system_prompt → TASK_PRECEDENCE_PROMPT（当前任务优先于常设角色，workspace 文件只是 context，冲突时跟随当前任务并在上报中注明）→ REPORT_PROTOCOL_PROMPT`。两个新回归测试（常量内容锚点 + 注入顺序），pytest 10375。
- **并发压测其余发现定性**（未修，记录结论）：write-write last-write-wins 属实但已有 `overwritten` 返回标志 + BackupManager 预写备份（`.minimax/backups/` 10 份/文件）兜底；`previous_sha`/CAS/per-run 沙盒/deadlock 检测为架构级 backlog（CAS 立项时一并做内容指纹，避免读了没人消费的字段）；`file:modified` 事件在进程内 fs_bus 已存在，主 agent LLM 订阅面缺失。

## [1.4.1] - 2026-08-25

### Fixed — 真实环境压测（3 sub-agent 并行协作白板）回报的 dispatch 路由 bug

- **`ToolRegistry.dispatch` 删除 legacy args-dict 误判分支**：原启发式「`run` 恰好声明单个位置参数 = legacy 工具收整个参数 dict」在全库**零真实使用者**（builtin 技能工具 / MCP 桥接 / codebase 工具全是 `**kwargs` 约定），唯一效果是误伤恰好单参数的工具——`check_subagent(run_id)` 被 LLM 调用时收到 `{"run_id": ...}` 整个 dict，`run_id.strip()` 直接抛 `'dict' object has no attribute 'strip'`（压测实锤）；`list_subagents(include_disabled)` 更阴险——dict 恒 truthy，静默变成「永远列出 disabled」。现 dispatch 一律 `tool.run(**args)`。48 个 v1.4.0 测试没抓到的原因：测试直接调 `tool.run(...)` 绕过了路由层——新回归测试全部走 `registry.dispatch` 全链路。
- **`REPORT_PROTOCOL_PROMPT` 新增 Budget rule**：教子 agent「核心交付物落盘即调 `report_completion`，打磨前先报，`status: partial` 早报好过不报」——压测暴露 iteration 预算耗尽时子 agent 没机会上报、软强制失效（agent 定义 `max_iterations=8` 配置过小时尤甚；预算配置在 agents 表，UI 可调）。

## [1.4.0] - 2026-08-25

### Added — Sub-Agent Lifecycle 专项（工具路径子 agent 完整生命周期）

13 条使用反馈收敛的三大缺口独立排期做透：此前**工具路径**（主 agent 调 `spawn_subagent`）spawn 的子 agent 被 `tool_timeout=120s` 一刀切罩死（超 2 分钟必超时）、不落库（agent 重启后无迹可查）、不发事件（SubAgentPanel 完全不可见）、超时丢 partial、结果只活在 envelope 字符串里没有交接物。v1.4.0 三层递进修复，**前端 SubAgentPanel 零改动**复用显示（事件 wire 形状与 IPC 路径完全一致，必带 `parent_session_id`）。

- **层 1 止血（超时豁免 + usage 冒泡）**：`Tool` 基类新增 `dispatch_timeout: float | None = None` 声明式豁免（None = 走 `config.tool_timeout`），core `_execute_tool_call` 按工具实例解析生效超时；`SpawnSubagentTool.run()` 开头实例级赋值 `subagent_wall_clock_s()`（env `MINIMAX_CODE_SUBAGENT_TIMEOUT_S`，默认 600s，动态读取）。`subagent.py` 的 `invoke` envelope 透传 `usage` / `cancelled` / `truncated`（此前拿在手边却丢弃）。
- **层 2a 状态机（落库 + 事件可见）**：migration 028（`agent_runs.mode` CHECK 加宽 `'subagent'`，四步重建 + `_v28` 后缀全新索引名——021 RENAME 残留同名索引会让 `IF NOT EXISTS` 静默跳过随后被 DROP 连删，重建后表裸奔 SCAN）；DAO `_VALID_RUN_MODES` + `list_runs(mode=)` 过滤（`run.list` 收可选 `mode`）；每次 spawn 生成 `run_id` → 补 session 行（NOT-NULL FK）→ `create_run(mode='subagent')` → 注册 `_ACTIVE_RUNS`（IPC `agent.cancel_subagent` + 进程关停扫杀打通）→ 终态落库。事件路由：`_SUBAGENT_EVENT_ROUTES` 按 session 注册表（`builtins.send_message` 注册 / finally 清），工具路径 emit `agent.subagent_progress` 四段（started/completed/failed/cancelled）+ 实时桥接 core 回调（tool_call/tool_result），全链路 fail-open（emit 不可达 = 静默，绝不阻塞运行）。
- **层 2b 异步化 + partial + 强引用**：`spawn_subagent(wait=False)` 后台运行（模块级 `_BACKGROUND_RUNS` 强引用 + done-callback reap，GC 不可回收、异常有日志）；新增 `check_subagent(run_id)`（不 await 探状态，task 消失后从库取件）+ `wait_subagent(run_id, timeout_s=120)`（shield 等待，超时**不杀** run）。墙钟 partial：`ensure_future` → `shield` → 超时 `core.cancel()` → `await` 协作收尾（run() 检查点返回已累积 text/tool_calls），envelope 带 `cancelled=True` + `partial=True`，落库 status='cancelled' + `metadata.partial`。
- **层 3 artifact 交接协议**：每次 spawn 在 `<workspace_root>/.minimax/artifacts/<run_id>/` 锚定交接目录（root 取 `workspace_ctx.current_root()`，v1.3.0 天然 per-project；无 root = fail-open 跳过）：**BRIEF.md**（spawn 时写任务简报）+ **COMPLETION.md / REPORT.json**（子 agent 经注入的 `report_completion` 工具写结构化交接 {status, files, gaps, next_steps}）。`report_completion` 每次 spawn 经克隆 registry 注入（全局 registry 零污染）、allowlist 模式自动追加、子 agent system_prompt 拼协议段；**软强制**：收尾探测 COMPLETION.md，缺失 → envelope `reported=False` + 主 agent 侧警示（不失败）。新增 `read_artifact(run_id, rel_path?)`（containment 锚定该 run 目录，`..`/绝对路径越界拒绝）。
- **工具面**：主 agent 新增 3 个工具（`check_subagent` / `wait_subagent` / `read_artifact`），子 agent 注入 1 个（`report_completion`，仅克隆 registry 可见）；`wait` 默认 true 存量 prompt/技能零破坏，异步是 opt-in 能力。

### Fixed

- migration 028 索引丢失隐患：版本后缀唯一索引名 + `sqlite_master` 索引存在性防回归断言（`test_index_audit` 全量红暴露的 RENAME 交互坑）。

### 已知限制（本版不做）

- 子 agent 面板 UI 不持久——agent 重启后事件态丢失（run 数据在 `agent_runs` 表可查，`check_subagent` 查库可恢复终态）。
- stub（无 API key mock）路径的子 agent 永远 `reported=False`（mock 不调工具，警示语义正确但常驻）。

### Added — 回归测试（48 个新测试，Python 侧；pytest 10366 / vitest 756 全绿）

- `test_tool_dispatch_timeout.py`（层 1）、`test_subagent_lifecycle_runs.py`（migration 028 + 落库 + 事件路由 + cancel）、`test_subagent_async.py`（异步生命周期 + partial + GC 存活 + 实时事件，10 个）、`test_subagent_artifacts.py`（BRIEF/read_artifact/越界/report 注入与软强制，17 个）。

## [1.3.0] - 2026-08-24

### Added — Per-Project Workspace Root（每项目独立工作区根，严格隔离）

v1.2.2 评估总榜第 ⑧ 项（唯一遗留）独立排期做透。此前所有会话共享一个进程级工作区根（env `MINIMAX_CODE_WORKSPACE` 或进程 cwd），「项目」只是纯标签分组——项目 A 的会话可自由读写项目 B 的文件，代码索引全局一份互相覆盖。v1.3.0 起每个 project 可绑定 `root_path`，会话运行时按「会话 → 项目 → root」解析工作区，**严格隔离**（相对路径锚定项目根，绝对路径也必须在项目根内，越界一律 `PathSecurityError` / INVALID_PARAMS；跨项目需求 = 把项目根设为共同父目录）。**存量零破坏**：root_path 为空 ≡ v1.2.2 行为（回退 env/cwd），containment 仅在显式 project_id 存在时启用，system prompt 条件化注入。

- **数据层（migration 026）**：`projects.root_path TEXT NOT NULL DEFAULT ''`（PRAGMA 守卫幂等）；`ProjectsDAO.create/update` 透传（`""` 清除、None 不改）、`_hydrate` 防御式回退；`project.create/update` 收 `root_path`，`_normalize_root()`（strip → expanduser → resolve），非空时 `is_dir()` 校验否则 INVALID_PARAMS。
- **根解析核心（`workspace_ctx.py`）**：优先级 `session.workspace_path`（worktree 会话）> `project.root_path`（存在且 is_dir）> `MINIMAX_CODE_WORKSPACE` > cwd；root 指向不存在目录 → warning + 回退（用户删目录不炸会话）。`ContextVar` per-task 隔离 + token 式 `set/reset`；`session_root_scope(session_id)` asynccontextmanager。`file_ops._default_workspace()` 首行改读 current_root——**10 个工具 + 8 个 builtin 技能工具 + BackupManager 一次全部生效，零签名改动**，safe_resolve 三层防护（拒 `..`/containment/敏感目录黑名单）自动跟随新根；ExecTool 缺省 cwd 同步接入。
- **run 入口 + 子 agent/团队**：`agent.send_message` 解析 session 后 `set_current_root`（既有 finally reset 同层）；system prompt 仅当根与进程根不同源时插一行工作区根声明；`agent.invoke` / `agent.spawn_subagent` / `teams.spawn` 自行包 `session_root_scope`（gather 并发子 agent 天然继承父根且互不污染）；repo-map indexer 按 root 缓存。
- **codebase per-root（migration 027）**：`codebase_chunks.root` 列 + `codebase_file_meta` 重建为 `(root, file_path)` 复合 PK（同事务四步重建，不触碰 FTS/vec 虚表）；store 全部读写方法加 `root` keyword；indexer 增量 diff 只看本根（**修复跨根 build 误删对方 chunks 的存量正确性 bug**）；`_CODEBASE_INDEXERS` 按 root 字典缓存；`codebase.*` 四 handler 收可选 `project_id`；顺带修 dev 脚本三个 env 拼写不一（`_WORKSPACE`/`_WORKSPACE_ROOT`/`_WORKSPACE_DIR`）导致索引根错落 `agent/` 子目录的隐性 bug（`_WORKSPACE_DIR` 保留兼容 + DeprecationWarning）。
- **git / terminal / patch / checkpoint per-project**：共享 `handler_utils`（`project_root_from_params` + `ensure_cwd_within_root` 越界 INVALID_PARAMS）；git 三 handler / patch 八 handler / terminal.start 收可选 `project_id`（缺省 cwd：显式存在性校验 + 有 project root 时 containment）；checkpoint create/restore 后端自解析会话根（前端零改动）。
- **前端 + worktree 归档**：`Project.root_path` 类型必填（`""` = 未绑定）；git/codebase/patch 三 store action 时刻快照 `currentProjectId` 注入 wire（未选项目 = 不带键，legacy 行为）；新建项目 Modal 增「根目录」可选输入（绑定后无法访问根外路径的说明文案）；WorkspaceSwitcher 选项 tooltip 显示绑定根；`workspace.create_worktree_session` 收可选 `project_id`（ghost id 快速失败防孤儿 checkout），`createWorktree` 按 `currentProjectId ?? "inbox"` 归档（去硬编码）。

### 已知限制（本版不做）

- PreviewState（预览面板）仍锚定进程级 env 根。
- 切换项目后 git 状态栏 / codebase 面板靠下一轮轮询（≤2s）自动带上新 project_id 恢复，无即时 store 重置桥接（避免 sessionStore→git/codebase 反向 import 循环）。
- `_CODEBASE_INDEXERS` 无 LRU 逐出（项目手建数量有限）。

### Added — 回归测试（87 个新测试：Python 78 + web 9）

`test_migration_026.py`（升级路径/幂等/DAO roundtrip/handler 校验）、`test_workspace_ctx.py`（优先级四分支/目录缺失回退/ContextVar 并发隔离/跨根拒绝/exec 缺省 cwd）、`test_workspace_scope_runs.py`（run 入口/子 agent/团队 scope 泄漏）、`test_codebase_multi_root.py`（A/B 双根 build 后 search 互不可见；增量 build A 不删 B 的 chunks——现状必挂的正确性回归）、`test_patch_project_root.py` / `test_git.py` / `test_checkpoint.py` 扩展（双 repo 双 project / 无 project_id 回归 / 显式 cwd 越界拒绝）、`test_workspace_worktrees.py` +2（project_id 归档 / ghost id 快速失败）；web `project-root-scope.test.ts`（8 测试：真实 typed bindings 对 spied `client.request` 锁 wire 契约 + store 注入双层，含 legacy 无键分支）、`workspace-switcher.test.tsx` +1（root_path tooltip / 未绑定无 title）。

**质量数字**：pytest **10318 passed / 15 skipped**（v1.2.2 基线 10240 + 78 Python 侧含 flaky 修复）；vitest **756/756（96 文件）**；tsc 0 错误；ESLint 0/0；ruff 全绿。

## [1.2.2] - 2026-08-23

### Fixed — 多 Agent 协作与系统稳定性专项（评估发现的 8 项全量闭环）

起因于用户要求「举一反三：评估团队管理 / 子 agent 管理 / 各项能力稳定性 / 系统稳定性 / 项目切换」。全局评估产出 8 项修复性价比总榜（🥇 team.spawn LLM 注入 → ⑧ per-project workspace root），本轮全量闭环前 7 项 + 辅助加固项；⑧（per-project workspace root）为架构级改动，单独请示后排期。

- **① team.spawn LLM 注入（agent，最高优先）**：`teams.spawn` 构造 `TeamOrchestrator` 时漏传 `llm=`，`_llm` 永远 None——每个成员 SubAgentRuntime 落入 canned stub 路径（"stub: agent xxx would handle..."），**团队运行从不产生真实回答**。修复：注入进程级 `get_subagent_llm()`（None 时保留 stub 供测试）。
- **② WorkspaceSwitcher 重接（web）**：前端工作区切换器与真实 IPC 脱节。重写组件接 `workspace.*` 三方法、删除死代码 `lib/workspace.ts`、文案入 `strings.ts` 单一来源。
- **③ invoke/spawn 配置透传（agent）**：`agent.invoke` / `agent.spawn_subagent` 此前只透传 `system_prompt`/`tool_allowlist`/`model`，丢弃持久化的 `max_iterations` / `temperature`。抽 `_config_from_row` helper 统一透传。
- **④ 调度器收尾保护（agent）**：fire-and-forget `create_task` 结果无强引用（asyncio 经典坑——任务可被 GC、异常无人见）；收尾 bookkeeping 一步抛错即中断后续（task 永卡 "running"）。修复：`_spawn_fire` 持强引用 + `_done` 回调记录异常；completed/failed/last_run 每步独立守卫。
- **⑤ 权限 gater 注册表（agent）**：legacy 单槽 `server._permission_gater` 被每个新 `agent.send_message` 覆盖——并发 run 的同意弹窗永远无人应答、超时被拒。修复：`register_gater` / `unregister_gater` / `resolve_any_gater` 按 session_id 注册（legacy 槽保留兼容，`unregister` 条件清理）；run 结束 `finally` 注销防死 gater。
- **⑥ 终端进程树杀（agent）**：`exec` 工具超时只 `proc.kill()` 主进程——Windows/POSIX 下孙进程成孤儿继续跑。修复：`_child_spawn_kwargs`（POSIX `start_new_session`）+ `_signal_process_tree`（Windows `taskkill /F /T`、POSIX `killpg`，失败回退 `proc.kill`）。
- **⑦ WS seq 纪元对齐（agent + web）**：agent 重启后 seq 计数归 1，前端 `wsLastSeq` 仍持旧进程高水位（如 47）——重连 `?since=47` 在新纪元 replay 全空，**新纪元历史永久无法重放且永不自愈**。修复：ready 帧加 `next_seq` 锚点（下一条广播的 seq）；前端收到 `agent.ready` 判 `next_seq <= wsLastSeq` 即重置水位并主动 `close(1000, "seq-epoch-reset")` 触发无 cursor 重连，获得全量 512 环重放（新纪元已追平旧水位时旧 cursor 天然有效，故用比较而非"检测重启"驱动重置；无循环——重连后 ready 到达时水位已为 0）。
- **辅助｜team 运行稳定性三旋钮（agent）**：裸 `gather` 无并发上限（大团队瞬间 fan 出 N 个 LLM 循环）→ `asyncio.Semaphore`（env `MINIMAX_CODE_TEAM_MAX_CONCURRENCY`，默认 4，`<=0` 不设限）；`runtime.invoke` 无超时（挂死的子 agent 拖死整个团队 run）→ `asyncio.wait_for` 墙钟超时（env `MINIMAX_CODE_SUBAGENT_TIMEOUT_S`，默认 600s，`<=0` 禁用，TimeoutError 显式分支给友好 error）；`success = any(...)` 部分失败被静默掩盖 → `_merge_texts` 统一合并（失败时前置 `> ⚠ N of M agents failed: ...` advisory，review 模式 writer 失败同样可见）；模块 docstring 的 round-robin「first successful result wins」承诺与实现不符 → 如实改为「当前与 parallel 一致，保留为扩展点」。

### Added — 回归测试（46 个新测试：Python 36 + web 10）

`test_handlers_teams.py`（LLM 注入）、`test_subagent_config_passthrough.py`（配置透传）、`test_scheduler_hardening.py`（强引用 + 收尾守卫）、`test_gater_registry.py`（6 测试：注册/解析/注销/legacy 兼容）、`test_process_tree_kill.py`（7 测试：进程树信号/spawn kwargs/回退）、`test_ws_replay.py` +2（ready 锚点 / 纪元重置暴露）、`ipc-client.test.ts` +6（锚点≤水位重置 / 同进程不动 / 追平不动 / 首连不动 / 无锚点兼容 / live 推进）、`test_team_orchestrator.py` +7（env 解析 ×2 / 并发上限=2 / 默认全并发 / 墙钟超时 / 部分失败 advisory ×2）、`workspace-switcher.test.tsx` 重写（+4）。

**质量数字**：pytest **10240 passed / 15 skipped**（v1.2.1 基线 10204 + 36 新增）；vitest **747/747（95 文件）**；ESLint 0/0；ruff 全绿。

## [1.2.1] - 2026-08-23

### Fixed — 长中文 write/edit 工具调用被截断（max_tokens 硬编码 4096）

用户实测反馈「每次长 edit/write 都会被截在某个中文位置……改用 exec_command 写文件绕过」。取证确认根因不在工具，而在 LLM 输出预算：`AgentCore._stream_turn` 调 `stream_chat` 不传 `max_tokens`，anthropic transport 的兜底 `max_tokens or 4096` 硬编码 4096——300 行中文文档轻松 5-8k tokens，tool_use 的 `input_json_delta` 流被拦腰截断（`stop_reason=max_tokens` → `finish_reason="length"`），arguments JSON 不完整，`_prepare_tool_call` 里 `json.loads` 抛错，工具以「malformed JSON args」失败。中文 token 边界最密集，所以截断点几乎总落在中文位置。openai transport 不传不设限（无此问题），**anthropic 是唯一硬编码点**。

- **输出预算配置化（agent core）**：`AgentConfig.max_output_tokens` 新字段，默认 **32768**（8× 提升），env 旋钮 `MINIMAX_CODE_MAX_OUTPUT_TOKENS`（clamp [1024, 131072]，垃圾值 warning 回退——仿 `MINIMAX_MAX_ITERATIONS` 既有模式）；`_stream_turn` 显式透传 `max_tokens=config.max_output_tokens`。
- **transport 兜底对齐（anthropic）**：`max_tokens or 4096` → `or 32_768`（Anthropic 协议 max_tokens 必填所以兜底必须存在；与 core 默认一致，直连 transport 的调用者同享新余量）。
- **截断可观测性 + 自愈提示**：run 循环检测 `finish_reason == "length"` 且有 pending tool_calls 时打 loud warning（指向 env 旋钮）；malformed JSON 工具错误信息从裸 parse error 增强为指明「参数被输出上限截断——改小 payload（分段写）或调大 `MINIMAX_CODE_MAX_OUTPUT_TOKENS`」，模型下一轮可据此自愈而非重复失败。
- **回归测试（9 个，`test_output_token_budget.py`）**：env 旋钮（默认/覆盖/clamp/垃圾）、AgentConfig 显式值、core 透传（async-generator spy 捕获 kwargs）、anthropic 兜底 32768 锁死（防回退 4096）+ 显式值优先、openai 双向 parity（显式发出/缺省不设键）、malformed 错误含恢复指引。4 个存量测试 fake 的 `stream_chat` 签名补 `max_tokens` 形参（R55 补 `reasoning_effort` 同款适配）。

**质量数字**：pytest **10204 passed / 15 skipped**（10195 基线 + 9 新增）；ruff 全绿。

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
