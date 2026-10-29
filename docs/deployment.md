# 部署指南（生产单进程模式）

CLI 对每个受信本地数据目录持有一个排他服务锁；重复启动立即拒绝，不能通过删除锁文件
解决争用。正常关闭须等待任务、终端、MCP 和数据库释放，未确认关闭保留恢复标记。
嵌入式调用的责任、故障验证和回退说明见 [服务生命周期](service-lifetime.md)。

`a775619` 已在真实 Linux 的隔离完整依赖环境通过六模块生命周期回归：106 项通过、
无跳过，含实际 stdio CLI 和 HTTP/ASGI lifespan；固定归档/uv/lock 与日志 SHA 均有记录。
该环境保留供 L1 联调，未安装长驻产品服务。随后启动器追加 12 项通过、2 项
Windows 专用跳过，含真实 HTTP/SPA HTML/认证；未替代容器、冷启动或完整安装/
恢复验证，具体范围与证据见下文和同一生命周期说明。

> MiniMax Code v0.12.0 起，`pnpm start` 使用同一 HTTP 服务提供前端与 API：
> agent（FastAPI，默认 `127.0.0.1:8765`）同时服务 API 与构建后的前端 SPA。
> 正常启动只使用预构建前端和已安装的 Python 环境；安装及构建是显式准备步骤。
> 本文档描述该模式的配置项与运维要点。

## 快速开始

```bash
pnpm install          # JS 依赖
cd agent && uv sync --frozen --extra attachments   # Python 及办公/附件依赖
cd ..
pnpm build           # 首次准备 / 升级版本时构建
pnpm start           # 只启动已准备好的版本
```

办公成果生成以及 PDF、DOCX、XLSX 附件解析需要 `attachments` 依赖组。仅运行
`uv sync` 会安装基础功能，但不能视为这些办公功能已就绪；添加依赖后须重启服务。
Linux 用户服务也应在新版本的独立环境中执行上述安装，再按升级流程切换，避免
在运行中的虚拟环境里替换依赖。

启动后浏览器打开 **http://127.0.0.1:8765**。检查就绪：

```bash
curl http://127.0.0.1:8765/health
# {"ok":true,"db":true,"version":"1.6.0","uptime_s":3,"web":true,"data_dir":"MiniMaxCode"}
```

- `web: true` —— 构建产物 `web/dist` 已被本进程服务（生产模式生效）
- `db: true` —— SQLite 可写
- `data_dir` —— 数据目录名（basename，不含用户路径）

Windows 上完成上述准备后，可双击发布目录根部的 `Start-MiniMax-Code.cmd`，或为它创建快捷方式；启动器会切换到自身目录并直接运行现有生产启动器，不需要每次输入 `pnpm start`。保留窗口打开，在浏览器访问 Agent 输出的回环地址；关闭窗口可能中断运行。Node.js 不在 PATH 或环境缺失时窗口会保留错误提示，需自行修复准备环境；它不安装、构建、更新或覆盖配置和数据，也不自动连接已经占用端口的其他服务。相对工作区环境变量按启动器所在目录解析。该入口尚未经过安装演练。

生产启动入口为 `scripts/start-production.mjs`，不运行构建、安装或 `uv sync`。
默认使用 `agent/.venv` 中的平台 Python；设置 `UV_PROJECT_ENVIRONMENT` 时使用该环境
（相对路径以 `agent/` 为基准），也可用绝对路径 `MINIMAX_CODE_PYTHON` 指定已安装的解释器。
启动前检查前端 `index.html`、解释器和 Agent 模块依赖，缺失时停止并显示准备命令。
成功创建进程不代表就绪，应以上述 `/health` 的实际响应为准。

默认工作区为仓库根目录；显式 `MINIMAX_CODE_WORKSPACE` 保持生效，其相对路径以启动
命令的当前目录为基准。目录不存在时拒绝启动，不回退到其他目录。
显式 `MINIMAX_CODE_WEB_DIST` 的相对路径以 `agent/` 为基准；生产启动会拒绝无效产物，
而直接启动开发 Agent 仍可只服务 API。

Windows 生产启动复用现有 Job 进程所有权，控制启动器、Agent 及其后代生命周期；
关闭启动器可能强制中断运行，重新启动后按持久恢复流程核对，不能将其视为已完成。
POSIX 监护启动器父进程并转发信号到 Agent 进程组，超时后停止该组。服务创建的独立
工具进程组不在此监护范围内，不能据此保证整个工具树已停止；Linux 完整后代保障需
systemd/cgroup 或受限 runner 实测。无人值守受限执行与完整安装/升级/恢复仍须单独验收。

### 准备版本化搬运包

在已构建前端的可信本机，可用 Python 3.11+ 和 Git 将指定提交的程序文件与预构建前端打成版本化 ZIP：

```powershell
python scripts/prepare-release.py --commit HEAD --web-dist "$PWD/web/dist" --output-dir "D:/minimax-releases"
```

`--commit` 可为 `HEAD` 或完整提交 SHA；只从提交收集受限程序文件，**不会带走工作树未提交或未跟踪修改**。包名含提交中的版本标签与短 SHA，包内清单记录完整提交及每个文件的 SHA-256。必须由使用者另行确认所选前端由对应提交构建，并在打包前审阅前端内容，避免其中包含构建时注入的敏感值；脚本无法判断预构建资源是否含密钥，也不证明代码或包可信。输入目录须为可信、操作期间不变的本机目录，前端只接受常用静态资源，遇到不支持文件会停止。输出目录须已存在；已有同名包时拒绝覆盖。

搬运包不采集数据目录、用户配置、系统密钥环、Python/Node 运行时、虚拟环境或依赖缓存，也不创建或覆盖安装。目标机器需在独立新目录解压，并自行准备合适版本的 Node、Python、uv 和锁定依赖；完全离线使用还需预先提供目标平台所需的依赖缓存或安装介质。之后可用 `node scripts/start-production.mjs` 或包内 Windows 启动入口启动；Linux 用户服务可使用包内生成器与安装脚本。不要把旧环境直接复制成可移植环境。包内校验值用于搬运核对，不是签名；数据迁移另走数据备份流程。此功能尚未进行跨机搬运、安装或离线依赖演练。

### Linux 用户服务配置生成（实验）

在 Linux 上准备好固定版本、已安装依赖的虚拟环境和预构建前端后，可生成配置供审查：

```bash
python3 scripts/generate-linux-user-service.py \
  --python "$PWD/agent/.venv/bin/python" \
  --workspace "$PWD" \
  --data "$HOME/.local/share/minimax-code" \
  --web-dist "$PWD/web/dist" --port 8765
```

数据目录须已存在、属于当前用户且权限为 `0700`；所有路径必须是绝对路径。
生成器只写标准输出，**不会安装、启用或启动服务**。它保留虚拟环境解释器的符号链接，
固定监听 `127.0.0.1`，并明确指定数据、工作区和前端目录。不要把 API 密钥或认证
token 放入 unit 文件或命令参数。应先检查已有同名用户服务和升级前备份，再由操作者
将审查过的输出保存到用户 systemd 配置目录，使用 `systemctl --user daemon-reload`、
`systemctl --user start ...` 及 `/health` 验证；启动成功不等于数据库或任务验收通过。

#### 首次安装新用户服务（显式操作）

在服务所在 Linux 主机上，先按上面的准备步骤创建环境、前端、私有数据目录，并确保 `~/.config/systemd/user`（或 `XDG_CONFIG_HOME/systemd/user`）已存在且由当前用户持有、不可由组或其他用户写入。以下命令默认只输出 unit，供审阅，不修改文件或 systemd：

```bash
python3 scripts/install-linux-user-service.py \
  --unit minimax-code.service \
  --python "$PWD/agent/.venv/bin/python" \
  --workspace "$PWD" --data "$HOME/.local/share/minimax-code" \
  --web-dist "$PWD/web/dist" --port 8765
```

审阅后对相同参数追加 `--install` 才创建新 unit 并执行用户级 daemon-reload；再显式追加 `--enable` 可设置用户管理器启用，追加 `--start` 可请求启动，二者可分别选择。只接受当前用户配置目录中的简单 unit 名，已有文件、已加载的同名服务或 drop-in 会拒绝，决不覆盖；不安装依赖、不创建或清理数据，也不连接远端。安装需独占人工操作，期间不要同时修改服务、目录或重命名路径。

工具会显示准备的 Python 和前端路径，但不证明两者版本一致；应自行核对版本和配置。若任何步骤失败，它报告已创建的 unit 和已完成步骤，保留文件和 systemd 状态供检查，不自动回滚或重试。`--start` 仅报告 systemd 状态，不代表应用已就绪；随后按实际需要检查服务状态和 `/health`。这些功能尚未在个人 Linux 主机上进行安装、登出持续运行或故障恢复演练。

登出后用户服务是否继续运行取决于该主机的用户管理器和 linger 配置；此生成器不更改
linger。关闭服务时 systemd 管理该服务的控制组，但独立容器或其他控制组的生命周期
仍由各自执行器管理；断电、异常重启、任务恢复、服务更新与回退必须单独验证。此功能
尚未在个人 Linux 主机上安装或通过长驻验收，也不提供 SSH 隧道或自动部署。

## 工作原理

### 应用认证（M1，开发分支）

`/health` 和 SPA 静态资源可用于就绪探测；`/rpc`、`/ws`、预览、补全和文件接口需要认证。服务默认生成仅本次进程有效的随机 token，也可通过 `MINIMAX_CODE_AUTH_TOKEN` 注入固定 token。不要把它写入仓库、URL 或前端构建变量。CLI 请求使用 `Authorization: Bearer <token>`。

本机受信浏览器页面通过 `POST /auth/session` 获取会话：请求必须带受信 Origin 和 `X-Minimax-Client: web`，且连接来自回环地址；远端客户端不能自行领取 token。浏览器 token 仅在内存中，HTTP 使用 Bearer，WebSocket 使用 `minimax-auth.<token>` 子协议。服务另设 HttpOnly、SameSite=Strict cookie，名称按端口区分，避免独立预览服务覆盖主服务会话。进程重启后前端重新握手。

预览 iframe 保持 `sandbox=allow-scripts`，不授予同源权限。由于沙盒子资源不会携带 Strict cookie，前端先使用应用认证调用 `POST /preview/access`，取得绑定当前工作目录、最多 15 分钟有效的只读预览路径。页面、CSS、模块脚本和预览事件通过该路径读取；它不能用于 RPC、签发访问凭据或写入。目录重新绑定、失效或服务重启后需重新打开预览。这个临时路径具有读取所选目录预览文件的能力，不等同应用 token；不要公开分享。官方服务入口关闭访问日志；独立托管预览时也应关闭访问日志。签发及文件响应通过 `no-store` 和 `no-referrer` 避免缓存与引用来源传播。

开发环境中，前端会将本地 `localhost`/`127.0.0.1` 服务地址统一到当前浏览器的回环主机名，保留服务端口和协议；远程地址保持原样。独立预览服务也提供公开的 `/health` 认证能力探测，文件读取仍需应用认证或上述只读路径。

所有 HTTP/WS 请求检查 Host，默认只接受 `localhost`、`127.0.0.1`、`::1`；需要额外名称时用 `MINIMAX_CODE_ALLOWED_HOSTS` 明确列出，禁止通配符。`MINIMAX_CODE_CORS_ORIGINS` 是可调用应用的受信页面列表，不要添加不受控站点。认证边界信任本机操作系统账户；远程访问继续采用 SSH 隧道，不应公开监听服务。

预览文件复用文件工具的敏感路径和目录边界检查。HTML/SVG 等响应带 CSP sandbox，预览脚本不能继承应用源或调用 RPC。开发时前端与后端应使用相同的主机名（例如都用 `127.0.0.1`），避免浏览器拒绝跨站预览 cookie。生产环境使用同源 SPA。

当前会话的预览使用 `/preview/session/{session_id}/`；会话不存在、绑定目录丢失时返回 409，并要求重新绑定。无会话时的旧 `/preview/` 入口仍使用默认工作区。切换项目后预览状态随会话更新。

此批没有数据库迁移。回退时可恢复代码及前端构建，但会同时撤销认证和执行安全修复；不要把旧服务暴露给远端客户端。

agent 在构建 FastAPI app 时探测 `web/dist`（见 `http_server.py` 的 `_web_dist_dir()`）：

1. 优先取环境变量 `MINIMAX_CODE_WEB_DIST` 指定的目录；
2. 否则取仓库内 `web/dist`；
3. 目录存在且含 `index.html` 才挂载（`StaticFiles(html=True)`，挂在 `/`，API 路由保持优先）；
4. 前端生产构建里 `runtimeAgentBaseUrl()` 回退到 `window.location.origin` —— SPA 与 API 天然同源，**不经过 CORS**。

没有 `web/dist` 时（纯开发流）agent 正常运行，只是不服务页面，日志会提示 `run pnpm build first`。

## 配置项（环境变量）

| 变量 | 默认 | 说明 |
|------|------|------|
| `MINIMAX_CODE_HTTP_PORT` | `8765` | HTTP 端口 |
| `MINIMAX_CODE_HTTP_HOST` | `127.0.0.1` | 绑定地址。**只监听本机**，切勿改 `0.0.0.0` 暴露到局域网 |
| `MINIMAX_CODE_DATA_DIR` | `%APPDATA%\MiniMaxCode`（Windows） | SQLite 数据库存放目录 |
| `MINIMAX_CODE_WEB_DIST` | `<repo>/web/dist` | SPA 构建产物目录（绝对路径；须含 index.html） |
| `MINIMAX_CODE_PYTHON` | 项目环境 Python | 生产启动专用：已安装 Python 的绝对路径，不自动下载或安装依赖 |
| `UV_PROJECT_ENVIRONMENT` | `agent/.venv` | 生产启动使用的已安装环境；相对路径以 `agent/` 为基准 |
| `MINIMAX_CODE_LOG_LEVEL` | `INFO` | 日志级别（stderr + 文件） |
| `MINIMAX_CODE_LOG_FILE` | 空（仅 stderr） | 日志文件。绝对路径原样使用；相对路径解析到数据目录下。5 MB × 3 份轮转 |
| `MINIMAX_CODE_CORS_ORIGINS` | 空 | 追加受信跨源（逗号分隔，如反代场景）。默认白名单只有 Vite dev 端口 |
| `MINIMAX_CODE_NO_DB` | 空 | 设为 `1` 跳过数据库（诊断用；前端会显示存储降级横幅） |

## 前后端分离部署（可选）

如果想让 Vite dev server 独立跑（开发流）：

```bash
# 终端 1
cd agent && uv run python -m minimax_code
# 终端 2
pnpm dev:web   # http://localhost:5173，跨源访问 8765（默认白名单已含 5173）
```

反向代理场景把代理域名加入 `MINIMAX_CODE_CORS_ORIGINS`。

## 数据与备份

- 会话、运行和业务元数据位于 SQLite 文件：`<数据目录>/data.db`（WAL 模式，同目录可能有 `-wal`/`-shm`）。工作区文件、检查点副本和其他外部文件不包含在该数据库中。
- 在线数据库备份已提供：通过受认证 RPC 调用 `data.backup`，参数为 `{}` 或 `{"target_dir":"绝对目录"}`，返回备份的 `path` 与 `bytes`。实现使用 SQLite backup API，包含当时的已提交 WAL 数据、表结构及数据库索引；不要用运行中直接复制 `data.db` 代替它。
- 冷备份：先停止 agent 并等待任务及子进程清理完成，再复制整个数据目录。项目工作区及存放在其他位置的文件需要另行备份；操作系统凭据库和环境变量需要独立恢复，勿将凭据写入仓库。
- 数据目录迁移：停止 agent 后复制目录，再设 `MINIMAX_CODE_DATA_DIR` 指向新位置。升级回退使用旧应用及对应的升级前数据库备份；单独的数据库备份不等同完整产物备份，完整安装与恢复演练仍是 M7 验收内容。

## 故障排查

| 症状 | 处置 |
|------|------|
| 打开 8765 是 404 | `web/dist` 缺失 —— 先 `pnpm build`；或用 `MINIMAX_CODE_WEB_DIST` 指向产物目录 |
| 页面报无法连接 agent | 确认端口未被改过（`MINIMAX_CODE_HTTP_PORT`）；同源模式下无需任何 CORS 配置 |
| `db: false` | 数据目录不可写或迁移失败 —— 看 stderr / 日志文件；`MINIMAX_CODE_NO_DB=1` 时属预期（诊断模式） |
| 想看持久日志 | 设 `MINIMAX_CODE_LOG_FILE=agent.log`，文件落在数据目录，自动轮转 |

## 相关测试

- `e2e/production-mode.spec.ts` —— SPA 服务 + `/health` + `/rpc` + WS + UI 聊天全链路（Playwright）
- `agent/tests/test_http_server.py` —— web dist 覆盖/缺失、同源 CORS 契约、health 字段
- `node --test scripts/tests/start-production.test.mjs` —— 无网络、无需 Python 的生产启动单元检查；已加入 baseline 验证入口。
- `node --test scripts/tests/start-production.integration.mjs` —— 已安装 Python 的真实进程检查。
  `MINIMAX_START_TEST_PYTHON` 可指定测试解释器；设置 `MINIMAX_START_TEST_SOURCE` 为含 Agent
  源码与预构建 `web/dist` 的隔离目录后，还会执行真实健康、SPA、认证及 CLI 检查。
  测试使用临时数据/工作区、强制 mock 和空 keyring，不读取个人模型凭据或调用付费模型。

2026-10-26 Windows 启动追加验证：14 项检查通过（18.49 秒），其中包括缺少产物/
解释器、身份检查失败、非零退出、依赖导入失败、导入阻塞期间父进程突退与超时、
实际后代心跳停止、stdout/stderr 各 1 MiB 的末尾诊断排空，以及上一已验收提交
`2dfe7bd` 的隔离源码真实健康/SPA/认证与 CLI。日志为
`.verification/production-start-frozen-final.log`，隔离源与前端校验值为
`production-start-snapshot.json`。两次健康样本为 3594.8 / 3665.8 毫秒，缓存已热，
不作为固定验收机冷启动或 M7 性能门槛通过的证明。

Linux 追加检查使用提交 `c7c0029` 的四个脚本文件归档，在隔离临时目录运行，传输
前后 SHA-256 一致。Ubuntu 主机 Python 3.12.3 / Node 22.22.2 实测 **10 项通过、
4 项跳过**（3.46 秒）：六项单元检查及四项真实合成进程检查，包括导入失败、双流
尾部排空、正常停止与父进程突退后的心跳停止。两项 Windows Job 场景按平台跳过，
两项真实 Agent 健康/SPA/认证检查因未提供隔离预构建后端跳过。证据为
`.verification/production-start-linux.log` 与 `production-start-linux.json`。
本次没有安装依赖、修改服务配置或调用模型，不能据此证明 Linux 完整工具树清理、
真实产品服务、容器执行适配、冷启动或 Windows 断线后任务继续。

准备上述 `a775619` 完整后端后，使用其启动脚本内容（Windows checkout 的 CRLF
副本，与 Git blob 归一换行后一致）及已验收的预构建 `web/dist` 追加实机检查：
**12 项通过、2 项 Windows 专用跳过、0 失败**（8.69 秒）。413 个上传文件均按原字节
manifest 核验。两项真实 Agent 测试分别运行 supervisor 与 CLI，验证 health、SPA HTML、
未经认证 RPC 返回 401、认证 ping；暖启动到健康样本为 2010.7 / 1959.0 毫秒。
证据为 `.verification/linux-product-launcher-full.{log,json,exit}`，资产归档 SHA-256：
`1d2a377e4b107b2ad4d63cdc87ab9eaa61d3738845055d867737211b459584e2`。
复用前端产物不等于本次构建或浏览器渲染；暖样本不等于冷启动门槛。临时环境保留，
测试等待自身进程退出，但未据此宣称全系统无残留、容器、systemd 或完整 M7 已完成。

本批不改数据库结构。回退启动入口时先停止原进程并保留运行恢复证据，再恢复匹配的
脚本和 `package.json`；原启动命令会重新执行构建。用户数据、附件和数据库完整恢复
仍按各自备份流程处理，这些启动检查不能替代安装、升级、回退和恢复演练。

### Linux 用户服务版本升级（实验）

`scripts/upgrade-linux-user-service.py` 升级**已安装且正在运行**的生成器用户服务；它不下载安装依赖、不创建版本目录、不覆盖已有版本或数据目录。先将旧版和新版分别完整准备在两个互不重叠的固定目录，包含各自可用的虚拟环境 Python、已安装的 agent 和构建好的 web；不要在升级期间修改它们。现有 unit 必须逐字等于上节生成器以旧版路径、同一工作区、数据目录及端口生成的输出；loaded fragment 必须与指定文件一致，不能有 drop-in 或待 reload 修改。自定义 unit 使用人工流程，不要删除自定义配置来绕过检查。

**操作员必须先停止接单并确认所有活动任务已完成或安全终止**，检查外部容器及其他执行器；`--quiesced` 只是操作员的明确确认，工具不能证明任务已完成。确保只有此工具操作服务，期间无人手动启动、修改 unit 或使用同一数据目录；租约不防止非 CLI 嵌入或不同路径硬链接。数据、工作区、版本目录与备份目录需在可信本地文件系统；备份目录须预先存在、属当前用户且为 0700，且不能与前三者相互包含。留足空间，并单独保全工作区、未登记文件、外部配置、私有 marker、安装身份、系统钥匙串和其他凭据。离线 bundle 不包含这些内容，且数据库可能含敏感信息。

例如（路径仅作示例）：

```bash
python3 scripts/upgrade-linux-user-service.py \
  --unit minimax.service --unit-file "$HOME/.config/systemd/user/minimax.service" \
  --old-release "$HOME/apps/minimax/1.0" --old-python "$HOME/apps/minimax/1.0/agent/.venv/bin/python" --old-web "$HOME/apps/minimax/1.0/web/dist" \
  --new-release "$HOME/apps/minimax/1.1" --new-python "$HOME/apps/minimax/1.1/agent/.venv/bin/python" --new-web "$HOME/apps/minimax/1.1/web/dist" \
  --workspace "$HOME/projects/example" --data "$HOME/.local/share/minimax-code" \
  --backups "$HOME/minimax-backups" --port 8765 --quiesced
```

工具停止 unit 并要求状态为 inactive，使用旧版程序创建和校验离线 bundle，再保留旧 unit、原子切换 unit、reload、start，检查 active、systemd MainPID 和 `/proc` 中的 TCP 监听套接字：健康检查前后都要求该主进程持有回环端口的监听套接字，且没有它未持有的可达监听套接字；同时检查进程启动时间及网络命名空间，并要求回环 `/health` 返回 HTTP 200 和 `ok`、`db`、`web`、`auth_required` 均为 true（不跟随重定向）。无法读取或归属这些信息时升级失败；只检查主进程，不将子进程或容器内进程视为其所有者。新旧虚拟环境必须已安装 bundle 支持且旧 bundle writer 支持当前 schema；不支持时停止并保留原数据。每次交易建立独立私有目录，保留 `state.json`、旧 unit 和 bundle。锁只协调遵守该工具协议的同 unit 升级。这些是基本就绪检查，不证明数据库、任务或工作区验收通过。套接字与进程检查是采样，无法消除检查间的竞争或证明共享套接字的独占性；应在可信且操作已静默的本机环境中运行。

**失败和中断：** 工具不会自动恢复旧数据库或用旧程序启动已升级数据。切换过程中或之后失败时，它尽力停止服务并保留新版 unit、原数据和备份；停止失败、超时、SIGKILL、断电或进程崩溃后的实际状态可能未知，不能根据记录推定服务已停止或切换未发生。先检查 systemd、进程及数据目录租约，确认完全停止；保全升级后的数据目录、WAL/SHM、marker、日志和工作副本作为证据。若备份尚未成功校验，不应把它用于恢复。若需要回退，使用**旧版**的 `bundle verify` 和 `bundle restore --new-data-dir` 恢复到全新路径，按 [bundle 恢复边界](data-vault-bundle.md) 另行核对独立文件、marker、身份和凭据；用旧版生成指向新恢复目录的 unit，经检查后由操作者手动部署并启动。不得用旧版直接打开已迁移的新库，也不得覆盖现有数据。失败前停止而尚未切换时同样应先确认实际状态；此流程不自动重启。

此工具的受控测试不等于真实主机的 systemd、断电恢复、完整备份或 M7 部署验收；首次安装、全量系统恢复和自动回滚仍需单独操作和验证。

### Inbound webhook delivery safety

Inbound hooks require a configured HMAC secret; previously configured unsigned hooks now return 403 and do not dispatch. The signature covers the body, not provider headers. The receiver durably claims the SHA-256 digest per hook before dispatch. GitHub or Gitee delivery IDs are untrusted correlation hints. A completed claim with the same ID, body, source and interpreted event returns a duplicate success without dispatch. Changed events and older completed claims without an event fingerprint return 409 for review; a matching fingerprint does not authenticate the event header. The same body with a different or missing ID, or the same ID with a different body, also returns 409. Identical bodies from independent events can require review. Failed or interrupted dispatch remains blocked, because notification and session writes can have partially succeeded. Inspect `webhook_deliveries` state, error ID and stage and reconcile side effects before any manual intervention; no automatic retry or resolution is provided. Unsigned requests never reserve IDs. Online JSON import rejects webhook configuration and the delivery ledger. Backup restoration reverts this ledger to the snapshot and cannot protect against deliveries executed after that snapshot; reconcile before reopening inbound hooks after restore.


### PDF 报告生成

安装报告与附件依赖：在 `agent` 目录执行 `uv sync --frozen --extra attachments`。报告生成自动尝试常见 Windows/Linux 中文字体并嵌入 PDF；如果字体缺失、格式不支持或无法覆盖报告字符，安装可用的中文 TrueType 字体并在启动服务前设置 `MINIMAX_CODE_PDF_FONT` 为字体文件的绝对路径，重启服务后重试。字体文件不会被修改。PDF 文字可解析不代表排版视觉、事实或业务已验收。

报告面板可选择“DOCX + PDF”，一次提交相同内容并逐格式保存。若某种格式未确认，先刷新项目附件确认其是否已保存；确认缺失后选择该单一格式重试。不要在结果不明时重新生成两种格式，否则可能产生重复版本。

报告面板可添加、删除参考来源，填写标题、HTTP(S) 网址和可选访问日期或带时区时间；正文可用 `[1]`、`[2]` 对应来源顺序。生成文档在文末列出可复制网址，不会访问或核实所填来源。普通聊天 Agent 可传入同一结构的来源列表，须以实际获取或用户提供的信息填写。
