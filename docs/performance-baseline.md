# 性能基线（v0.13.0）

> v0.13.0 里程碑（roadmap R9–R14）建立的性能基线。1.0.0 验收标准第 2 条：
> "性能有基线：启动时间、RPC 延迟、长会话渲染有测量数字，无 P0 级退化。"
> 本文档是这些数字的**单一来源**——回归复测时对照本文更新，并在 CHANGELOG 记录。

测量环境：Windows 11 Home，开发机（结果因机器而异；基线看数量级与相对变化，
不做跨机横向比较）。**2026-08-29 起测量环境切换为 Linux 容器**（Python 3.12.3 /
Node 22 / uv loop 同口径命令），跨环境数字不可直接横向比较——v1.6.1 列是新环境
的**新锚点**，后续复测对照 v1.6.1 列而非 R50 列。

**1.0.0-rc 发布复测（R50，2026-08-21）**：冷启动 median 2.589 s（+0.1%）、
首屏 JS 136.7 KB / CSS 8.2 KB gzip、SQLite 零裸表扫与 WS 重放回归全绿、
e2e 21/21 × 3 连跑零失败。详见各节"R50 复测"列与 CHANGELOG 0.19.0 后续记录。

**v1.6.1 发布复测（2026-08-29，Linux 容器新锚点）**：冷启动 median 2.291 s、
首屏 JS 144.8 KB / CSS 8.3 KB gzip（预算内）、SQLite 索引审计 15 passed
零裸表扫。v1.5→v1.6 的沙盒/overlay/CAS 等重功能落地后无 P0 级退化。

**Linux 容器平台差异测试（2026-08-30 B1 收口，全量 10611 passed / 9 skipped / 0 failed）**：
v1.6.1 曾备案 7 个 Windows 语义用例在 Linux 容器红（10595 passed / 7 failed）。
B1 逐项排查后全部收口——单平台全量全绿不再需要豁免清单：

| 测试 | 原备案根因 | B1 实查与处置 |
|------|-----------|--------------|
| `test_write_registry.py::test_registry_folds_path_case` | normcase 大小写折叠不生效 | 属实（Windows FS 语义）→ `skipif` 非 win32 显式声明 |
| `test_xai_codebase_graph_index_manager_types.py::test_symbol_location_as_path_handles_backslash_separator` | 反斜杠分隔符仅 Windows 合法 | 属实 → `skipif` 非 win32 |
| `test_terminal_hardening.py::test_path_prefix_stripped` | 路径前缀剥离按 Windows 形态断言 | **实为产品缺陷**：`_is_dangerous_cmd` 扩展名剥离被 `sys.platform == "win32"` 门住，Linux 上 `python.exe` 不剥导致危险命令漏检 → 实现修复（`.exe/.bat/.cmd/.com/.ps1` 全平台剥离）+ 新增跨平台用例 |
| `test_workspace_ctx.py::test_exec_default_cwd_is_session_root` | 会话根解析的盘符语义 | 实为环境差异（容器无 `python` 命令）→ 测试改 `sys.executable`，两平台都跑 |
| `test_hunks_types.py::test_hunk_value_equality` | Linux 文件系统时钟精度 | 属实但属测试设计脆弱（值相等断言依赖时钟粒度）→ `created_at` 固定字面值 |
| `test_handlers_mcp.py::test_mcp_add_and_list_server` | 容器内有 npx → `connected=True` | 属实（add 真实 attach）→ 断言环境自适应（无 npx 必 False，有 npx 验 bool 形态） |
| `test_exec_sandbox_escape.py::test_attributed_foreign_write_not_flagged` | fs `mtime_ns` 时序窗口更细 | **备案定性有误**：实为测试时序竞态——`sleep(0.05)` 注入相对子进程生命周期两头都可能出窗（快机晚于减除扫描、朴素哨兵早于 watermark）→ 两级握手（ready→go）钉死 emit 落入归因窗口 |

## 1. Agent 冷启动（R9）

脚本：`agent/tests/bench_startup.py`（冷启动 → `GET /health` 返回 `ok:true` 的
墙钟耗时；每次独立临时数据目录）。

| 指标 | 基线（2026-08-20） | R50 复测（2026-08-21） | v1.6.1 复测（2026-08-29，Linux 容器） |
|------|--------------------|------------------------|--------------------------------------|
| 冷启动到 healthy | **2.586 s**（3 轮：2.574 / 2.586 / 2.607） | **2.589 s**（3 轮：2.619 / 2.589 / 2.579，+0.1%） | **2.291 s**（3 轮：3.092 / 2.142 / 2.291） |

构成：Python import + FastAPI app 构建 + 29 个 SQLite 迁移 + skills 加载
（12 个）+ web/dist 挂载判定。预算：**≤ 5 s**（超 2 倍基线视为退化）。

复测：`cd agent && uv run python tests/bench_startup.py`（回归测试
`agent/tests/test_bench_startup.py`）

## 2. SQLite 热点查询（R10）

`messages(session_id)` 等 15 条热点查询逐条 `EXPLAIN QUERY PLAN` 审计：
**零裸表扫**（含 LIKE 前缀查询），索引集已完备。回归由
`agent/tests/test_index_audit.py` 钉死（索引缺失即测试失败）。

## 3. 前端首屏产物（R11）

生产构建（`pnpm build`）首屏加载分析：

| 指标 | 基线（2026-08-20） | R50 复测（2026-08-21） | v1.6.1 复测（2026-08-29） |
|------|--------------------|------------------------|--------------------------|
| 首屏 JS（gzip） | **123.9 KB** | **136.7 KB**（+10.3%，v0.14→v0.19 六版本功能增长，预算 200 内） | **144.8 KB**（较 R50 +5.9%，v1.2→v1.6 功能增长，预算 200 内） |
| 首屏 CSS（gzip） | **8.2 KB** | **8.2 KB**（持平） | **8.3 KB**（+1.2%） |
| 懒加载 chunk | 346 个，共 2.8 MB gzip（按需，不阻塞首屏） | 346 个，共 2852.4 KB gzip（持平） | 347 个，共 2864.5 KB gzip（持平） |

预算审计已落地：`web` 包的 `pnpm bundle:report`（`scripts/bundle-report.mjs`）
实测 gzip 字节并对首屏预算硬断言，超限 exit 1。
预算：首屏 JS ≤ **200 KB** / CSS ≤ **50 KB** gzip。

复测：`pnpm build && pnpm --filter @minimax/web bundle:report`

## 4. 长会话渲染（R12）

500 条消息的会话，MessageList 两层窗口（`useMessageWindow` 50 条/页 +
`useVirtualizer` DOM 虚拟化）：

| 指标 | 基线（2026-08-20） |
|------|--------------------|
| 挂载 DOM 行数 | **34 行**（全量的 6.8%） |
| jsdom 渲染耗时 | **164 ms**（MessageItem stub 后的列表层） |

回归由 `web/tests/message-list-perf.test.tsx` 钉死（挂载行数 ≤ 50 且全部
来自尾部窗口；耗时仅日志输出，不做硬断言——墙钟随机器浮动）。

## 5. WS 广播与断连恢复（R13）

| 指标 | 基线（2026-08-21） |
|------|--------------------|
| 广播事件 seq 打标 | 每 fan-out +1 字段，无额外拷贝路径 |
| 断连重放 | `?since=` 重连后按序重放 ≤ **512** 条历史 |
| 首屏重放 | 首连不带 cursor（`wsLastSeq=0` 不请求历史，避免与 RPC 拉取的持久化状态重复） |

回归由 `agent/tests/test_ws_replay.py`（6 单测）与
`e2e/smoke-ws-resume.spec.ts`（断线 → 重连 → seq 递增重放断言）钉死。

## 复测节奏

- 每个里程碑收口轮（版本 bump 轮）复测全部 5 项，数字变化写入 CHANGELOG。
- 1.0.0-rc（R50）做一次全量复测作为发布数字。
