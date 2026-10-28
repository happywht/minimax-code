# 分层验证与可信基线

M0 建立于 2026-10-22，起点提交 `78962fb`。测试结果证明对应受控场景，不能替代真实模型任务验收。本轮不使用个人模型凭据，不部署远程主机。

## 安装与运行

推荐 Node 22、pnpm 9.12.0、Python 3.12、uv 0.10.8；CI 使用相同主版本/工具版本。

```sh
pnpm install --frozen-lockfile
uv sync --frozen --extra dev --extra attachments --directory agent
pnpm exec playwright install --with-deps chromium
pnpm verify:baseline
```

| 入口 | 内容 | 频率 |
|---|---|---|
| `pnpm verify:baseline` | ESLint、Ruff、TypeScript/生产构建一次；前端单元、后端核心、协议回放、Playwright 连续三轮 | M0 验收及双平台 CI |
| `pnpm verify:core` | 生命周期、权限、路径、终端、调度、迁移、数据、契约等高风险回归 | 本地快速确认 |
| `pnpm verify:replay` | 真实协议解析器/SDK + 受控 HTTP/SSE/stdio 响应 | Provider/MCP 相关变更 |
| `pnpm verify:attachments` | 安装锁定可选依赖并执行DOCX真实资源子进程/原件/HTTP/迁移/包兼容专项，依赖缺失报错而非跳过 | Windows/Ubuntu CI及本地附件变更 |
| `pnpm verify:full` | `agent/tests/` 全量 pytest，独立任务 | 每次 CI，与核心基线分开报告 |
| `node scripts/verify.mjs --suite=baseline --repeat=1` | 单轮基线 | 日常迭代 |

具体文件清单在 [`scripts/verification-suites.json`](../scripts/verification-suites.json)。增加真实调用路径时再扩充核心清单；全量测试包含不少独立协议与算法辅助模块，其通过数量不等于产品完成度。

Playwright 自动启动 15173/18765 隔离服务（可用 E2E 专用环境变量修改端口），拒绝复用已有 Vite，数据写入 `e2e/.runtime/`。请不要同时运行两套使用相同端口的跨栈测试。基线先构建前端，保证 production-mode spec 使用最新 dist。

## 三种结果必须分开

1. **mock / 确定性**：固定模型响应、模拟工具或隔离工作区。前端单元和当前 Playwright 属于此层；跨栈是真浏览器和真后端，模型仍为受控响应。
2. **协议回放**：使用真实 SDK/协议解析器处理测试提供的响应，包括人工协议夹具；不声称所有夹具都来自生产流量录制，不连接真实服务。
3. **live 任务评测**：真实外部服务与模型，必须另列输入、预算、模型、结果和人工介入。当前尚未提供/执行路线图中的 40 项 live 任务集。

未来 live 用例必须标记 `pytest.mark.live`，并显式 `--run-live -m live`。仅传入 `--run-live` 不会关闭普通用例的隔离。当前若选择不到 live 用例，pytest 的“无测试”退出码不能记作验收通过。常规 runner 没有 live 开关，不能用它隐式发起付费评测。

## 默认测试隔离

- runner 清理 API key、token、secret、password 类环境变量及个人 `MINIMAX_CODE_*` / `VITE_AGENT_*` 运行配置，设置 null keyring 和独立数据目录；不打印环境变量值。
- `agent/tests/conftest.py` 对普通用例设置临时目录、空 keyring、清理 API 凭据环境变量。单个测试可以显式注入假凭据或 FakeKeyring。
- 测试进程拒绝外部 DNS 和非回环 TCP 连接；本机测试服务器及 httpx MockTransport 可用。不强制所有 SDK 走产品 mock，以免协议解析从未被测到。
- 这些是测试护栏，不是 OS 沙盒：socket monkeypatch 不约束独立子进程、UDP 或显式替换护栏的测试代码；测试模块导入前的环境由 runner 清理。新增外部进程用例必须使用受控响应与隔离配置，不能读取个人配置。
- `tests/e2e/` 的历史 Python stdio smoke 不在 `agent/tests` 默认发现路径内；不能把核心或全量 pytest 结果算作这些脚本已运行。

## 证据与三轮规则

每次运行生成 `.verification/<时间>-<suite>/results.json`，保存 Git HEAD、工作区是否有改动、源码 SHA-256、平台、Node 版本、开始/结束时间、每步命令/退出码/耗时与日志路径；后端另有 JUnit XML。

源码摘要覆盖 Git 跟踪及未忽略的新文件内容。每轮前与全部完成后检查摘要；运行期间修改源码会使基线失败。生成物、日志及测试缓存应位于 Git 忽略路径。不要在三轮期间编辑文档或代码。未提交时只能称“同一源码快照三轮通过”，不能声称“同一提交通过”。正式提交后的 CI 结果才满足同一提交的门槛。

任一步失败立即停止，不使用重试掩盖不稳定性。检查对应日志后修复，再重新完整运行三轮。Playwright 保留失败 trace/截图；CI 即使失败也上传证据。当前 workflow 为 Windows + Ubuntu 的三轮基线，另有两平台全量后端任务。配置文件存在不等于 Linux 已通过，验收必须引用实际作业结果。

## M0 两项问题的修复原理

- `smoke-chat` 向 `timeline-session` 注入事件前设置当前会话，并断言另一会话消息不出现。继续保留生产代码的会话隔离。
- MessageList 的真实 MessageItem 是懒加载模块，首次加载带有 Markdown 依赖转换；测试在 `beforeAll` 中预加载真实模块，渲染断言仍走真实组件和 Suspense。Vitest 最多四个 worker，降低大量 jsdom 实例竞争；没有提高渲染等待超时或删掉断言。

## 迁移与回退

M0 不改数据库 schema、RPC 协议或生产执行策略。回退时只撤销本批测试、CI、脚本及文档变更；无需逆向数据库迁移。`.verification/` 可保留作审计记录，不要随回退清除失败证据。后续阶段按 [路线图](production-roadmap.md) 各自记录迁移与恢复方法。


## 可信任务结果定向验证

`agent/tests/test_task_outcomes.py` 使用真实 RunService、SQLite 和受控 Native core，验证 shell 写入且无 FsBus 时的 chat/execute 校验、校验故障和取消、持久化失败、真实调度/取消与重开历史、035 已有数据迁移。`web/tests/task-outcomes.test.tsx` 检查任务与聊天的状态/证据、部分输出和取消确认竞态。日志 `.verification/task-outcome-*.log` 属于离线局部回归；底层进程树边界同时复验，不能据此宣称真实模型质量、调度 occurrence 幂等或共享硬预算完成。

## 2026-10-28：连接恢复与测试资源归属修复

最后一个 WebSocket 客户端断开后，HTTP app 继续持有唯一事件监听器，使断线期间
事件进入原有 512 条内存历史；app 关闭时注销监听器，并等待其 sender/heartbeat
任务结束。heartbeat 自身发起断开时不会等待或取消自己，重连后旧循环也不能接管
新循环。此改动不提供跨进程重启的 WebSocket 历史持久化。

HTTP 入口向 asyncio SIGTERM 注册提供正确的 signal/frame 参数；Windows fallback
与 asyncio 两条注册路径的测试均断言等待共享 runtime teardown。HTTP/背压/信号/
关闭定向集 50 项通过，最终 heartbeat/断线两项回归与其重叠；四个真实浏览器 spec
共 9 项通过，原 `smoke-ws-resume` 未改动。三个旧 E2E 夹具分别改为核对 v3 实际
host target/hash、定位用户消息气泡、显式选择独立 Native 源会话与子 Agent 面板；
子任务断言收紧为完成，不接受失败作为通过。

全量退出挂起另定位到 IPC 测试复用携带旧数据库的索引器。该 fixture 现在隔离
两处索引器缓存，并在关闭自有 SQLite 前取消、等待自己创建的延迟/实际索引任务；
真实聊天与索引回归断言不重新打开旧连接。IPC/相邻 43 项和 host/chat gate 167 项
分别自然退出 0、无残留线程。gate 夹具满足当前 Exec-only 契约；八个篡改反例仍
保留，并核对 gate 的原拒绝原因。没有放宽产品准入、关闭自动索引或强制退出测试。

证据在 `.verification/ci-baseline-repair-{e2e-final,ws-signals-heartbeat-python}.log`
及 `ci-baseline-repair-threads-{ipc-adjacent-final,gates-accepted-final}.log`。
这些是定向修复证据，完整双平台 CI 与同提交三轮仍须由新作业确认。本批不改
schema；回退用对应提交的普通代码回退，不做数据库逆向迁移。
