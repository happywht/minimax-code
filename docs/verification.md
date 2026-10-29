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
| `pnpm verify:attachments` | 安装锁定可选依赖并执行PDF/DOCX真实资源子进程/原件/HTTP/迁移/包兼容专项，依赖缺失报错而非跳过 | Windows/Ubuntu CI及本地附件变更 |
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

旧全量 CI 日志另确认文本成果升级夹具仍预期迁移至043；现显式补齐044，保留
旧项目行及表结构断言，该文件34项自然退出0。日志为
`.verification/ci-migration044-fixture.log`；不据此把已取消的旧全量作业改记为通过。

### 2026-10-28：双平台基线、Python 3.11 与 DAO 归属

提交 `0dd3a4f` 的 CI `40682997989` 已分别完成 Windows、Ubuntu 三轮基线，
两个 baseline job 均成功。Ubuntu 原始 artifact 已下载核对：16 个命令全部退出0，
每轮真实浏览器35项通过；独立附件套件191项通过、2个预期恶意ZIP警告。
后端全量 job 的退出结果另行验收，不能用基线绿色代替全量通过。

新增的原始源码退出诊断发现另一条缓存路径：workspace merge fixture 通过全局
ProjectsDAO 重新打开先前 IPC fixture 已关闭的数据库，留下非 daemon SQLite
线程。IPC fixture 现隔离并清理该 DAO，merge fixture 显式使用自己的 DAO，退出时
恢复调用方缓存后再关闭自有数据库；回归真实执行旧已关闭DB到merge的组合。
该修复仅测试归属，不改变生产检索或强制关闭外部线程。

同一原始源码的实际单例对照还发现 Python 3.11 流式预算上下文错误：逐片段
`wait_for(anext)` 在不同任务中推进生成器，跨yield的ContextVar token无法复位；
3.12 同例通过。产品改为 `asyncio.timeout` 内直接推进生成器，保留超时、取消及
finally/aclose，未吞掉预算错误。四个新回归在3.11修前均失败；修后包含两处fixture
及相邻模块的184项在3.11/3.12分别通过67.94/65.24秒，测试主函数和进程均自然0，
末尾无线程残留。169项较早组合与此重叠，不叠加计数。

命令、原始失败、源hash及自然退出证明保存在
`.verification/ci-0dd3a4f-thread-3b2158b2/` 的 `stream-context-evidence.json` 和
`stream-fixture-python{311,312}.{json,log}`。原 `0dd3a4f` 全量诊断仍绑定未修复
快照；不能把本批定向通过写成其全量结果。无schema变化，回退使用对应代码提交。

### 2026-10-28：0659456 双平台全量自然退出与前端基线待验

提交 `06594560f478680beaa490d07ac3b806dfbac3e3` 的
[CI 40688403895](https://github.com/happywht/minimax-code/actions/runs/40688403895)
已实际完成两平台后端全量，两个 job 均为 success。独立下载并交叉核对
`results.json`、JUnit 每个 testcase 和正文日志，结果如下：

| 平台 | 通过 | 跳过 | 失败 / 错误 | pytest 正文耗时 | runner 进程耗时 | 退出码 |
|---|---:|---:|---:|---:|---:|---:|
| Ubuntu | 12523 | 15 | 0 / 0 | 364.27 秒 | 370.340 秒 | 0 |
| Windows | 12518 | 20 | 0 / 0 | 1070.66 秒 | 1076.514 秒 | 0 |

两份 JUnit 均含12538个 testcase；两份结果均为 `dirty: false`、`status: passed`，
并记录 `full-1` 退出0和结束时间。runner 只在测试子进程 `close` 后写入该条结果，
因此此次证明自然退出，区别于旧提交正文已打印通过但进程未退出、最终被取消的情况。
Windows 全量证据在 `.verification/ci-0659456-backend-full-windows/`，Ubuntu 在
`.verification/ci-0659456-backend-full-ubuntu/`；独立统计与文件 SHA-256 保存在
`.verification/ci-0659456-independent-review/full-review.json`。

同轮新增的 Python 3.11 最低版本作业也在 Ubuntu、Windows 实际成功；两份 JUnit
各169项通过、0跳过、0失败/错误。它们覆盖流式上下文、超时、取消和预算相邻模块，
不是第二套后端全量。原件在 `.verification/ci-0659456-minimum-python-{ubuntu,windows}/`，
交叉统计在独立 review 目录的 `minimum-review.json`。

同提交 Ubuntu baseline 仍为 failure：第一轮前端898项通过、1项快捷键测试失败，
失败点是在 `?` 后同步读取懒加载的快捷键浮层。该测试现在先等待初始化、从已聚焦的
textarea 明确切换到既有非编辑按钮，断言焦点后使用真实键盘事件，并等待浮层加载；
Escape关闭、textarea接收字面 `?` 和焦点不被抢走的断言均保留。未修改产品快捷键、
可编辑元素保护或延长超时。原文件本机单跑也通过，未稳定复现CI竞争；当前源码未发现
composer启动自动聚焦路径，不能将失败确定归因为自动聚焦。

前端修复的相邻5文件34项通过（13.12秒）、一次完整前端115文件903项通过（45.29秒），
单文件ESLint退出0；结果不能替代新提交CI验收。原失败保留于
`.verification/ci-0659456-baseline-ubuntu/.verification/2026-10-28T12-33-47-728Z-baseline/5-web-1.log`，
本地前后结果、精确命令和源码hash在 `.verification/ci-065-app-focus/evidence.json`。
本记录不宣称本次双平台三轮baseline已通过，前端修复仍待新提交的CI确认。
截至2026-10-28 13:02:42 UTC，Windows baseline仍为 `in_progress`，按pending收口；
其最终结果与artifact待完成后另行核对。本次未取消或重跑该作业。最终作业快照保存在
`.verification/ci-0659456-independent-review/run-final.json`。

后续完成核对：Windows baseline于13:05:31 UTC成功结束，下载原件至
`.verification/ci-0659456-baseline-windows/`。16个命令均退出0，三轮各前端899项、
core1050通过/1跳过、协议回放35项和真实浏览器35项通过；未取消该作业。
这不会把同提交Ubuntu的快捷键失败改为通过。随后XLSX及停止竞态检查点
`d496176` 已推送，新CI `40693861165` 的结果须另行核验，不能继承旧提交的结论。

该新提交的 [CI 40693861165](https://github.com/happywht/minimax-code/actions/runs/40693861165)
随后全部成功：Windows、Ubuntu 两个平台各三轮 baseline 的16条命令均退出0；
两平台后端全量 JUnit 各12599项，Windows 12579通过/20跳过、Ubuntu 12584通过/15跳过，
零失败或错误，runner记录退出0。Python 3.11 两平台作业也成功。原始证据分别留在
`.verification/ci-d496176-baseline-{windows,ubuntu}/` 和
`.verification/ci-d496176-backend-full-{windows,ubuntu}/`。这些结果只针对该提交，
不替代后续 Linux 在途关停和真实模型任务验收。

官方 MCP SDK 既有传输提交 `5db8cb1` 的 CI `40713137920` 随后六项作业全部成功。
两平台三轮 baseline 各16条命令均退出0；每轮前端903项、协议回放35项、浏览器36项
通过，core Ubuntu 1045通过/6跳过、Windows 1050通过/1跳过；独立附件各226项通过。
后端全量 Ubuntu 12651通过/15跳过、Windows 12646通过/20跳过，均自然退出0；
Python 3.11 两平台各169项通过。原件与独立统计在 `.verification/ci-5db8cb1-*`。
后续 `a2832e9` 新增 Streamable HTTP 和迁移，其 CI 结果必须单独确认。

`a2832e9` 的 Ubuntu 后端全量 CI 中，12709项通过、15项跳过、1项失败，进程自然
退出1。唯一失败为旧 Schema040 升级测试仍预期迁移列表止于044；实际新增045。
修正预期后保留旧项目数据、成果表及再次迁移为空的断言，单例先红后绿；相邻五模块
72项通过，Schema045/bundle专项35项通过，均自然退出0。原失败及新日志见
`.verification/ci-a2832e9-failure-review/`；不将原CI改判为通过，修复提交须另跑CI。
同一轮 Windows 后端全量为12704通过、20跳过、1失败，唯一失败与 Ubuntu 相同；
两平台 baseline 均成功。Windows 原件和独立统计见同目录的 `windows-full-review.json`。

后续修复及窄范围执行候选 `78276c2` 的 CI `40724758863` 六项作业全部成功：
Windows、Ubuntu 三轮 baseline 各16条命令退出0；后端全量分别12714通过/20跳过、
12719通过/15跳过，零失败或错误并自然退出0；Python 3.11 两平台作业成功。原件在
`.verification/ci-78276c2-*`。后续办公成果提交 `9e7abb9` 的 CI 需另行核验。

`9e7abb9` 的 Ubuntu 后端全量作业报告32项失败，均位于办公备份测试：新夹具创建
的两个 vault 目录受默认 umask 影响，不满足已有的私有权限检查。原 JUnit 和日志在
`.verification/ci-9e7-ubuntu-full/`；其余作业仍需分别核验。后续修复让夹具显式
创建 0700 目录，保留生产权限检查；修复提交需要 Linux CI 重新验证，不能把原作业
改判为通过。Windows 本地办公备份测试126项通过。

`4cd39ff` 的 CI `40821743284`：Ubuntu 基线、两平台后端全量和 Python 3.11 均通过；
Windows 基线首轮前端 912 通过、1 失败，MCP 空状态测试错误等待英文加载文案，实际
界面为中文加载文案。后续改为等待实际文案消失并确认列表调用；定向三文件14项通过。
原 CI 不改判，修复提交仍需重新运行双平台 CI。
