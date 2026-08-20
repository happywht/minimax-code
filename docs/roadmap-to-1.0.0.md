# Roadmap: v0.11.0 → v1.0.0

> 目标：把 MiniMax Code 从"功能完备的复刻"推进到"生产级 1.0"。
> 节奏：54 轮迭代，每轮一个可验证切片（实施 → 测试 → commit），里程碑处 bump 版本。
> 本文档同时是**轮次账本**：每完成一轮，在对应条目标注 `[done @ <commit>]`。

## 1.0.0 的验收标准

1. **单进程生产模式可靠**：`pnpm start` 一条命令起完整产品（agent 静态服务 web/dist），e2e 覆盖。
2. **性能有基线**：启动时间、RPC 延迟、长会话渲染有测量数字，无 P0 级退化。
3. **安全有边界**：CORS 可配置、secrets 不落日志、权限默认最小化。
4. **数据可移植**：全量导出/导入（JSON），随时备份恢复。
5. **UI 文案统一**：面向用户文案统一中文，无中英混杂。
6. **无障碍达标**：键盘可达、aria 补全、焦点管理。
7. **文档完备**：README 快速上手 + 用户手册 + 契约文档与代码一致。
8. **可诊断**：一键导出诊断包（脱敏）。
9. **全量绿**：pytest + vitest + 15 e2e specs + 生产模式 e2e，flaky 清零。

## 里程碑与轮次账本

### M1 · v0.12.0 生产单进程模式收口（R2–R8）
- [x] R2 生产模式 e2e：`pnpm build` → agent 服务 dist → 验证 SPA 首页 / `/health` / `/rpc` / WS 全通 `[done @ 3055d57]`
- [x] R3 `pnpm start` 打磨：dist 缺失时友好报错并指引 build `[done @ b0fdf27]`
- [x] R4 同源验证：生产模式下前端与 API 同端口（8765），CORS 不再参与；dev 模式白名单不变 `[done @ a97171e]`
- [x] R5 日志：`MINIMAX_CODE_LOG_FILE` 落盘选项 + 轮转；生产默认 INFO `[done @ 2759faf]`
- [x] R6 `/health` 扩展：`web_dist` 是否挂载、数据目录路径（脱敏） `[done @ 2e810b0]`
- [x] R7 文档：`docs/deployment.md` 生产部署指南（build/start/端口/数据目录） `[done @ 5bc00f4]`
- [x] R8 版本 bump 0.12.0 + CHANGELOG `[done @ 5686e16]`

### M2 · v0.13.0 性能基线（R9–R14）
- [x] R9 基准脚本 `agent/tests/bench_startup.py`：冷启动到 /health OK 的耗时基线 `[done @ ddc62ec]`（本机 3 轮：2.574 / 2.586 / 2.607 s）
- [x] R10 SQLite 索引审计：messages(session_id) 等热点查询 EXPLAIN QUERY PLAN 逐条过 `[done @ 288778a]`（15 条热点零裸表扫，含 LIKE；索引集已完备，测试钉死）
- [x] R11 前端首屏：生产 build 产物分析，lazy 路由确认无大块同步加载 `[done @ 9d158fb]`（首屏 JS 123.9 KB gzip / CSS 8.2 KB；346 个懒 chunk 2.8 MB gzip 按需加载；`pnpm bundle:report` 预算审计落地，超限 exit 1）
- [x] R12 长会话：500+ 消息渲染冒烟（jsdom 计时）+ 虚拟化窗口断言 `[done @ c7fa8c3]`（500 条消息仅挂载 34 行 DOM（两层窗口：useMessageWindow 50/页 + useVirtualizer），jsdom 渲染 164ms；hook 层 renderHook 直测分页逻辑）
- [x] R13 WS 心跳/重连：断连恢复事件流不丢（补 e2e 断言）`[done @ d9db747]`（广播单调 seq + 512 深度历史环 + `?since=` 重连重放；agent 6 单测 + e2e smoke-ws-resume，全量 21 spec 绿）
- [x] R14 版本 bump 0.13.0 + 基线数字写入 docs `[done @ 3f92b42]`（新增 `docs/performance-baseline.md`：启动 2.586s / 零裸表扫 / 首屏 123.9KB gzip / 500 条 34 行 164ms / WS 重放环 512，含复测命令与预算）

### M3 · v0.14.0 安全加固（R15–R20）
- [x] R15 CORS 允许列表环境变量化 `MINIMAX_CODE_CORS_ORIGINS`（默认不变）`[done @ b8bece3]`（功能已存在，本轮钉死安全边界：拒绝未列 origin / env 只追加不替换 / 无效项丢弃，4 新测试；architecture/ipc-contract/agent CLAUDE.md 三处陈旧说法同步）
- [x] R16 RPC 防护：畸形请求（非 JSON-RPC/超大 payload）4xx 拒绝测试 `[done @ 2a715c9]`（5 新测试钉死：缺 method 信封 INVALID_REQUEST / 超限 body 在 dispatch 之前被拒（spy 零调用）/ 恰好 10MB 边界不误杀 / GET 4xx / WS 垃圾帧静默丢弃不断连）
- [x] R17 secrets 脱敏审计：日志与错误响应不包含 API key 片段 `[done @ 2349fc9]`（审计挖出真 P1：SanitizerFilter 挂 root logger 对子 logger 传播记录零生效=业务日志消毒全空转；改挂 handler 级修复；另修 secrets.status str(exc) 直通信封；test_secret_audit.py 6 测试：传播回归+挂载契约+RPC 往返不回显+参数分支不反射）
- [x] R18 权限默认策略审查：工具默认 ask 清单与文档一致 [done @ fe62aec]
- [x] R19 安全回归测试集中化 `agent/tests/test_security.py` [done @ bf4499e]
- [x] R20 版本 bump 0.14.0 [done @ 516b957]

### M4 · v0.15.0 数据可移植（R21–R26）
- [x] R21 IPC `data.export`：全部业务表 → 单 JSON（含 schema_version）[done @ 90144d8]
- [x] R22 IPC `data.import`：校验 + 幂等导入（事务内）[done @ fbd13b1]
- [x] R23 备份：`data.backup`（SQLite backup API 到指定目录）[done @ 0e5e031]
- [x] R24 前端设置入口：Settings 新 DataTab（导出/导入/备份按钮 + 状态反馈）[done @ 2c1adde]
- [x] R25 端到端测试：导出→清库→导入→数据等价 [done @ 04165dd]
- [x] R26 版本 bump 0.15.0 [done @ f86ac2b]

### M5 · v0.16.0 UI 文案统一（R27–R34）
- [x] R27 建 `web/src/ui/strings.ts`（中文文案单一来源）+ layout 域迁移 [done @ 98bab19]
- [x] R28 chat 域迁移 [done @ 7f430d4]
- [x] R29 settings 域迁移 [done @ 849235f]
- [x] R30 panels 域迁移 [done @ ea58f0b]
- [x] R31 modals 域迁移 [done @ 379f5f5]
- [x] R32 right-panel 域迁移 [done @ 1b11b58]
- [x] R33 全量 grep 清零英文面向用户文案（技术术语保留原文） [done @ b56c0d8]
- [x] R34 版本 bump 0.16.0 [done @ 905017f]

### M6 · v0.17.0 无障碍与键盘（R35–R39）
- [x] R35 Modal 焦点陷阱审计与修复 `[done @ dabe3bc]`（APG 对照审计修 4 缺陷：document-capture 逃逸拉回 / container 自身 Shift+Tab 防漏 / hidden·aria-hidden·布局不可见过滤（jsdom 兼容）/ contenteditable + [data-autofocus]；5 使用者零改动受益，测试 9+3，全量 550/550）
- [x] R36 aria-label / role 补全（icon-only 按钮全覆盖） `[done @ 02b2753]`（108 生产文件全扫：IconButton 61 处类型强制 + 原生 60 button 中 4 个 icon-only，唯一漏网 TeamRunPanel 移除按钮已补 strings.rightPanel.teamRuns.removeAria；新增静态审计测试进 CI 防回归——dotall tempered token 扫多行属性，sr-only 文本计为 label；552/552）
- [x] R37 列表键盘导航（会话列表 roving tabindex） `[done @ 14cc261]`（单 tab stop + fallback 链 rovingId→currentId→首行；ArrowUp/Down clamp、Home/End 端点；导航按渲染 DOM 序跨项目组；7 测试，559/559）
- [x] R38 对比度抽查（accent/ink tokens 对 WCAG AA） `[done @ 13c9b39]`（两主题 29 组前景×背景全算 WCAG 2.1 公式，挖出 3 处不达标并修：dark/light ink-2 各 ~3.1-3.3:1→4.7:1、light status-error 3.9→6.5:1；60 断言守卫测试进 CI 防回归，619/619）
- [x] R39 版本 bump 0.17.0 `[done @ ea2c1b9]`（6 代码位 + 3 文档版本行 + CHANGELOG M6 全条目 + uv lock；test_version 16/16 一致性通过）

### M7 · v0.18.0 文档完备（R40–R44）
- [x] R40 README 重写（快速开始/常见命令/FAQ） `[done @ 0c0fc4c]`（-135/+92 行：pnpm start 主线 + 命令速查表 + 8 环境变量表 + 8 问 FAQ；修 4 处陈旧事实——smoke 6 个非 7、Playwright spec 在根级 e2e/ 10 个、26 张表非 8、thinking_count 早已实现；删 Phase1-6/Tauri 考古；文档链接与 npm scripts 逐一验证存在）
- [x] R41 `docs/user-guide.md` 用户手册 `[done @ 2721343]`（198 行 19 节：五分钟上手/三栏布局/任务与项目/对话与工具授权/模型密钥分工/命令面板与快捷键/技能/多 Agent/检查器 11 tab/自动化三件套/Memory/MCP 与插件/Git+Review+Patch Studio/数据导出导入备份/权限审计/移动配对/无障碍/异常横幅；全部交互主张对照组件源码核实——修 4 处初稿幻觉：权限弹窗实为允许/拒绝+总是允许开关、移动配对入口在侧栏非顶栏、任务重命名是双击、删未核实的归档；README 加手册链接）
- [x] R42 `docs/ipc-contract.md` 与代码对账（方法数/事件数核对脚本或清单） `[done @ 205949b]`（对账挖出真缺口：167 个注册方法 84 个无文档锚点——补 Appendix A 总表 81 方法×16 命名空间；修 1 处事件名笔误 agent.permission_request→permission.request；新增 6 测试双向守护：注册方法必须见于文档/文档 token 必须可解析（事件+协议帧+crash.install 白名单）/事件清单与前端 StreamEvent 枚举锁死；坑：doc token 裸文本无反引号、camelCase 需 [A-Za-z_0-9]、多段 token 完整匹配防 run.step 截断；10109 passed）
- [x] R43 CLAUDE.md / AGENTS.md / web+agent 模块文档同步 `[done @ a92bbc9]`（五文档对账：24 实体表/167 方法 35 前缀/31 handler/10 工具模块/20 DAO/12 技能/6 smoke/10 spec/settings 14 tab/inspector 11 tab/stores 29/事件 15，全部 registry+filesystem 实测；自查抓 3 处幻觉：sessions→progress、src/hooks/ 不存在（实在 lib/）、26 表→24 实测口径；数字二次复核 31 handlers/20 DAO；顺带补勾 R41/R42 复选框）
- [x] R44 版本 bump 0.18.0 `[done @ 0ef6477]`（6 代码位 katex 依赖除外——web/package.json `"version"` 字段精确替换 + 3 文档版本行 + CHANGELOG M7 全条目 + uv lock 0.17.0→0.18.0；test_version 16/16 一致性通过）

### M8 · v0.19.0 诊断工具（R45–R48）
- [x] R45 IPC `diag.export`：版本/平台/配置（脱敏）/日志尾/表行数 → JSON 下载 [done @ 4ab4eb3]
- [x] R46 前端诊断入口（Settings About 区"导出诊断包"） [done @ 78840db — 落地为 Data tab 第四面板，理由见 commit note]
- [x] R47 诊断测试（脱敏断言：无 keyring 值、无绝对用户路径） [done @ 6499d7a]
- [x] R48 版本 bump 0.19.0 [done @ 25f5990]

### M9 · 1.0.0-rc（R49–R52）
- [x] R49 全量回归：pytest + vitest + 15 e2e + 生产模式 e2e [done @ e126472 — 实际口径 10 spec/21 用例；挖出 R32 断言债 8 处修复 + dist 过期重建]
- [x] R50 flaky 清零：全部 e2e 连跑 3 轮零失败；性能基线复测 [done @ 1c26773 — 21/21 × 3（31.0/31.3/31.7s）；streaming-follow 竞态根因修复（虚拟化余震翻 following flag，poll 内重设 scrollTop）；发布数字：冷启动 2.589s（+0.1%）、首屏 JS 136.7KB（+10.3% 预算内）；ESLint 0 errors/0 warnings]
- [x] R51 CHANGELOG 汇总 1.0.0 全部条目 [done @ 0db57a3 — [Unreleased] 顶部新增 1.0.0 总览：M1-M9 里程碑表 + rc 质量数字 + R50 性能数字 + 规模数字（registry 实测纠正 35→36 命名空间，diag.* 是第 36 个前缀）]
- [x] R52 版本 bump 1.0.0-rc.1 [done @ 6f0b6e2 — 首个 pre-release 挖出 PEP 440 ↔ semver 规范碰撞：metadata 报 `1.0.0rc1` 连字符被吃、`installed_semver()` 在自家版本上抛错——桥接修复 + diag.export 改报 semver 形态 + 4 新测试；13 文件，10129 pytest / 622 vitest / ruff / eslint 0/0 全绿]

### M10 · 1.0.0 正式（R53–R54）
- [x] R53 发布公告 `docs/release-1.0.0.md` + README 徽章/版本终稿 [done @ aa83a7c — 公告按九条验收标准逐条对账 + 核心数字速览 + 八版本表 + 0.x 升级说明；README 四静态徽章（version/python/node/local-first）+ 版本行指向公告]
- [x] R54 版本 bump **1.0.0** 🎉 `[done @ 343dad2 — 6 代码位 + CLAUDE/AGENTS 版本行 + CHANGELOG（1.0.0 总览移入正式节 + M10 行 + 质量数字 10129）+ uv lock；stable bump 推进 live-metadata 断言 v.pre is None；10129 pytest / 622 vitest / ruff / ESLint 0/0 全绿，54/54 收官]`

## 轮次执行规约

1. 每轮唤醒后：读本文档找第一个未勾选项 → 实施 → 相关测试 → 精确 git-add → commit → 勾选并标注 commit hash。
2. 测试红线：任何一轮不得让全量套件变红；涉及 IPC 契约必须同步 `docs/ipc-contract.md` + `web/src/types/ipc.ts` + mock。
3. 版本 bump 轮固定动作：6 处代码位 + CHANGELOG + 3 文档版本行 + `uv lock`。
4. 中途发现上游 bug：修复并入当轮 commit，CHANGELOG 记 Fixed。
