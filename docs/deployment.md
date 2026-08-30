# 部署指南（生产单进程模式）

> MiniMax Code v0.12.0 起，`pnpm start` 一条命令即可把整个产品跑在一个进程里：
> agent（FastAPI，默认 `127.0.0.1:8765`）同时服务 API 与构建后的前端 SPA。
> 本文档描述该模式的全部可配置项与运维要点，以及 **v1.8.0 起的远程部署与访问令牌鉴权**。

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
# {"ok":true,"db":true,"version":"1.8.0","uptime_s":3,"web":true,"data_dir":"MiniMaxCode"}
```

- `web: true` —— 构建产物 `web/dist` 已被本进程服务（生产模式生效）
- `db: true` —— SQLite 可写
- `data_dir` —— 数据目录名（basename，不含用户路径）

## 工作原理

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
| `MINIMAX_CODE_HTTP_HOST` | `127.0.0.1` | 绑定地址。本机模式保持默认；远程部署见下节 |
| `MINIMAX_CODE_HTTP_TOKEN` | 空（**不启用鉴权**） | v1.8.0 访问令牌。设置后 `/rpc` `/ws` `/preview` 全部要求凭据；**公网部署必设**。见下节 |
| `MINIMAX_CODE_DATA_DIR` | `%APPDATA%\MiniMaxCode`（Windows） | SQLite 数据库存放目录 |
| `MINIMAX_CODE_WEB_DIST` | `<repo>/web/dist` | SPA 构建产物目录（绝对路径；须含 index.html） |
| `MINIMAX_CODE_LOG_LEVEL` | `INFO` | 日志级别（stderr + 文件） |
| `MINIMAX_CODE_LOG_FILE` | 空（仅 stderr） | 日志文件。绝对路径原样使用；相对路径解析到数据目录下。5 MB × 3 份轮转 |
| `MINIMAX_CODE_CORS_ORIGINS` | 空 | 追加受信跨源（逗号分隔，如反代场景）。默认白名单只有 Vite dev 端口 |
| `MINIMAX_CODE_NO_DB` | 空 | 设为 `1` 跳过数据库（诊断用；前端会显示存储降级横幅） |

## 远程部署（v1.8.0，公网 / 局域网）

默认绑定 `127.0.0.1` 意味着只有本机浏览器能访问。要让其他设备访问，需要两步：
**绑定 `0.0.0.0` + 启用访问令牌**。缺第二步等于把一个无鉴权的 Agent（含终端执行、文件读写）
暴露在网络上是不可接受的——这是 v1.8.0 加 token 鉴权的直接动因。

### 1. 生成并配置令牌

```bash
# 生成一个足够长的随机令牌（别用弱口令）
openssl rand -hex 32
```

把令牌写进服务环境。systemd 场景（Ubuntu 服务器）：

```ini
# /etc/systemd/system/minimax-code.service
[Service]
Environment="MINIMAX_CODE_HTTP_HOST=0.0.0.0"
Environment="MINIMAX_CODE_HTTP_TOKEN=<上面生成的令牌>"
# 其余按既有 unit 保持（WorkingDirectory / ExecStart / 日志等）
```

```bash
sudo systemctl daemon-reload && sudo systemctl restart minimax-code
```

> 令牌也可以放进 `EnvironmentFile=/etc/minimax-code.env`（权限 `600`）避免出现在 unit 文件里。

### 2. 鉴权语义（设置后生效）

| 端点 | 凭据携带方式 | 失败行为 |
|------|--------------|----------|
| `POST /rpc` | HTTP 头 `Authorization: Bearer <token>` | `401` + JSON-RPC error 帧 |
| `GET /ws` | query 参数 `?token=<token>`（浏览器 WS API 无法带自定义 header） | 握手拒绝，close code `4401` |
| `GET /preview/*` | Bearer 头或 `?token=` query | `401` |
| `GET /health` | **匿名放行**（探针语义，只暴露 ok/db/version/uptime，无敏感数据） | — |

令牌比较使用 `hmac.compare_digest`（常数时间，防时序侧信道）。不设置该 env 时全部行为与 v1.7.1 一致（本地模式零破坏）。

### 3. 浏览器侧配置

打开页面后进入 **设置 → 数据 → 访问令牌**，粘贴令牌保存。前端会：

- 所有 RPC 请求自动注入 `Authorization: Bearer` 头；
- WebSocket 与预览面板 URL 自动附加 `?token=`；
- 令牌持久化在浏览器 localStorage（`minimax_token`），刷新不丢；「清除」按钮可移除；
- 收到 401 时提示「服务端已启用鉴权，请在设置中配置访问令牌」。

curl 验证（无凭据被拒 / 带凭据通过）：

```bash
curl -s http://<host>:8765/rpc -d '{"jsonrpc":"2.0","id":1,"method":"ping"}' | head -c 200
# → 401 / -32002 语义（未带 token）

curl -s http://<host>:8765/rpc \
  -H "Authorization: Bearer <令牌>" \
  -d '{"jsonrpc":"2.0","id":1,"method":"ping"}'
# → {"jsonrpc":"2.0","id":1,"result":{"pong":...}}
```

### 4. 网络层收敛建议

- 云服务器（如火山云）：安全组只放行需要的端口（8765），来源 IP 尽量收窄；
- 有条件的话套一层反向代理加 TLS（nginx/caddy），令牌之外再获得传输加密——纯 HTTP 下令牌会明文过线；
- token 是全共享的单密钥模型（所有持令牌者权限等同），多用户/细粒度授权不在当前设计内。

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
- 冷备份：停 agent 后复制整个数据目录；或用设置页「数据」tab 的备份功能（`data.backup` IPC）。
- 数据目录迁移：复制目录后设 `MINIMAX_CODE_DATA_DIR` 指向新位置。

## 故障排查

| 症状 | 处置 |
|------|------|
| 打开 8765 是 404 | `web/dist` 缺失 —— 先 `pnpm build`；或用 `MINIMAX_CODE_WEB_DIST` 指向产物目录 |
| 页面报无法连接 agent | 确认端口未被改过（`MINIMAX_CODE_HTTP_PORT`）；同源模式下无需任何 CORS 配置 |
| 页面提示「服务端已启用鉴权」 | 服务端设了 `MINIMAX_CODE_HTTP_TOKEN` —— 设置 → 数据 → 访问令牌里配好同一令牌 |
| 配了令牌仍 401 | 令牌不一致（注意首尾空白）；或 WS 场景前端版本 < v1.8.0（不支持 `?token=`） |
| `db: false` | 数据目录不可写或迁移失败 —— 看 stderr / 日志文件；`MINIMAX_CODE_NO_DB=1` 时属预期（诊断模式） |
| 想看持久日志 | 设 `MINIMAX_CODE_LOG_FILE=agent.log`，文件落在数据目录，自动轮转 |

## 相关测试

- `e2e/production-mode.spec.ts` —— SPA 服务 + `/health` + `/rpc` + WS + UI 聊天全链路（Playwright）
- `agent/tests/test_http_server.py` —— web dist 覆盖/缺失、同源 CORS 契约、health 字段
- `agent/tests/test_http_auth.py` —— v1.8.0 token 鉴权回归（未启用零变化 + 启用后 401/close 4401/带凭据全通/health 匿名）
