# 部署指南（生产单进程模式）

> MiniMax Code v0.12.0 起，`pnpm start` 一条命令即可把整个产品跑在一个进程里：
> agent（FastAPI，默认 `127.0.0.1:8765`）同时服务 API 与构建后的前端 SPA。
> 本文档描述该模式的全部可配置项与运维要点。

## 快速开始

```bash
pnpm install          # JS 依赖
cd agent && uv sync   # Python 依赖
cd ..
pnpm start            # = pnpm build && pnpm dev:agent
```

启动后浏览器打开 **http://127.0.0.1:8765**。检查就绪：

```bash
curl http://127.0.0.1:8765/health
# {"ok":true,"db":true,"version":"0.11.0","uptime_s":3,"web":true,"data_dir":"MiniMaxCode"}
```

- `web: true` —— 构建产物 `web/dist` 已被本进程服务（生产模式生效）
- `db: true` —— SQLite 可写
- `data_dir` —— 数据目录名（basename，不含用户路径）

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

- 所有业务数据在单个 SQLite 文件：`<数据目录>/data.db`（WAL 模式，同目录可能有 `-wal`/`-shm`）。
- 冷备份：停 agent 后复制整个数据目录；或等 v0.15.0 的 `data.backup` IPC。
- 数据目录迁移：复制目录后设 `MINIMAX_CODE_DATA_DIR` 指向新位置。

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
