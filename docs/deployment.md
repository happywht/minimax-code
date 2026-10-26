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
cd agent && uv sync   # Python 依赖
cd ..
pnpm build           # 首次准备 / 升级版本时构建
pnpm start           # 只启动已准备好的版本
```

启动后浏览器打开 **http://127.0.0.1:8765**。检查就绪：

```bash
curl http://127.0.0.1:8765/health
# {"ok":true,"db":true,"version":"1.6.0","uptime_s":3,"web":true,"data_dir":"MiniMaxCode"}
```

- `web: true` —— 构建产物 `web/dist` 已被本进程服务（生产模式生效）
- `db: true` —— SQLite 可写
- `data_dir` —— 数据目录名（basename，不含用户路径）

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
