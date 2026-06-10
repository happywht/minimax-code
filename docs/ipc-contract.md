# IPC Contract — `docs/ipc-contract.md`

> Authoritative description of the wire format between the React UI
> (web SPA) and the Python agent. v0.2.0 起 project 从 Tauri 桌面壳切到
> web 形态，原 Tauri 章节作为历史保留。Any change to message types or
> framing MUST be reflected here and bump the protocol version in
> `agent/minimax_code/ipc/protocol.py`.

## 0. Transport（v0.2.0 起）

JSON-RPC 2.0 在 v0.2.0 跑在 **两个 transport** 上，**共享同一份 handler
registry**（`agent/minimax_code/ipc/server.py:IPCServer`）：

- **stdio JSON-RPC 2.0** — 给 tests（`tests/e2e/` 下的 Python 黑盒 smoke）
  和 CLI 调试。命令：`python -m minimax_code --stdio`。
- **HTTP + WebSocket** — 给 web 客户端（Vite-served React SPA）。命令：
  `python -m minimax_code`（默认）。Agent bind `127.0.0.1:8765`
  （override：`--http-port` 或 `MINIMAX_CODE_HTTP_PORT`），CORS allow-list
  仅 `http://localhost:5173` / `http://127.0.0.1:5173`，本机单用户，**不鉴权**。

> 这不是平行两份实现。HTTP server 是**薄 transport**：`POST /rpc` 把
> envelope 反序列化后直接 `IPCServer.handle_request(env)`，再把响应
> 序列化回 HTTP body；流式事件经 `IPCServer.register_listener(cb)` 注入
> 到所有 `GET /ws` 连接。详见 [`docs/v0.2.0-web-architecture.md`](v0.2.0-web-architecture.md)
> 的 "Request routing (server side)" 段。

## 1. Topology

### 1.1 HTTP + WebSocket（v0.2.0 默认 — web 客户端）

```
┌────────────────┐  POST /rpc (JSON envelope)   ┌─────────────────┐
│  React SPA     │ ───────────────────────────▶│  Agent HTTP     │
│  (web/, Vite)  │                              │  (FastAPI,      │
│                │ ◀───────────────────────────│   127.0.0.1:8765)│
│  WSClient.on() │  WS frames (JSON envelopes)  │                 │
│                │ ◀════════════════════════════│                 │
└────────────────┘                              └─────────────────┘
       JSON-RPC 2.0                                thin transport
                                                   (reuses IPCServer)
```

| Endpoint                | Direction       | Purpose                                       |
|-------------------------|-----------------|-----------------------------------------------|
| `GET  /health`          | client → server | Liveness probe — `{"ok": true, "version": "0.2.0", "uptime_s": N}` |
| `POST /rpc`             | client → server | JSON-RPC 2.0 request → response (one shot)    |
| `GET  /ws`              | client ↔ server | WebSocket: server-push events（`agent.message_chunk` 等）|
| `GET  /events`（备用）   | client → server | SSE fallback（WebSocket 不可用时）|

- `POST /rpc` 永远返回 HTTP `200`；失败走 JSON-RPC `error` envelope
  （`code` / `message` / `data`）。`500` 仅用于"agent 自己崩了"或"请求
  无法反序列化"。
- `GET /ws` 连接成功后，服务端立刻发 `{"method":"agent.ready","params":{"server":"minimax-code-agent","version":"0.2.0"}}`
  一次，然后开始推流。Agent 关闭时干净 close。
- 请求示例：
  ```json
  {"jsonrpc":"2.0","id":"r1","method":"session.list","params":{"archived":false,"limit":50,"offset":0}}
  ```
- 响应示例：
  ```json
  {"jsonrpc":"2.0","id":"r1","result":{"sessions":[]}}
  ```
- 错误示例：
  ```json
  {"jsonrpc":"2.0","id":"r1","error":{"code":-32601,"message":"unknown method: foo.bar"}}
  ```
- WebSocket push 示例：
  ```json
  {"jsonrpc":"2.0","method":"agent.message_chunk","params":{"session_id":"ses_39c7c685","message_id":"msg_295faab6","delta":"Hello","done":false}}
  ```
  事件无 `id`，`method` 字段直接是事件名（`agent.message_chunk` /
  `agent.tool_call` / `agent.status` / `task.progress` /
  `agent.permission_request` / `agent.permission_resolved` 等）。

### 1.2 stdio（tests + CLI — v0.2.0 保留）

```
┌────────────────┐  write JSON+\n   ┌─────────────────┐
│  test driver   │ ────────────────▶│  Python agent   │
│  / CLI         │                  │  (asyncio,      │
│                │ ◀────────────────│   IPCServer)    │
└────────────────┘  read JSON+\n    └─────────────────┘
       JSON-RPC 2.0                       JSON-RPC 2.0
```

stdio mode **不经过** HTTP server，直接走 `IPCServer.run_forever()`。
所有 `tests/e2e/smoke_*.py` 都用这条 transport。HTTP/WS mode 与 stdio
mode **不能**同时跑（同一 handler registry + event listeners，dual-mode
会 interleave stdout 与 HTTP 响应）。

## 2. Framing（stdio 模式）

- **Encoding:** UTF-8, **no BOM** (the server strips a leading BOM for
  host compatibility, but producers must not emit one).
- **Delimiters:** each message is a single line of JSON terminated by
  `\n` (LF). Producers MUST NOT embed raw `\n` inside the JSON body —
  JSON's own `\n` escapes (inside string values) are fine.
- **Max message size:** `MINIMAX_CODE_MAX_MESSAGE_BYTES` (default 8 MiB).
  Anything larger is rejected with `PARSE_ERROR` (`-32700`).
- **Concurrency:** the writer side is serialized by a single
  `asyncio.Lock`; the reader side processes one line at a time.
  Handlers can be `async` and may use `ctx.emit` freely.

> HTTP mode 不需要行分隔符 framing — 单条 request 是一整个 HTTP body，
> response 是一整个 HTTP body，event push 是单个 WebSocket text frame。
> 同样遵守"JSON envelope 内不嵌裸 `\n`"和"BOM 兼容"约束（应用层）。

## 3. JSON-RPC 2.0 conformance

We use stock JSON-RPC 2.0 with one extension.

| Message         | Direction                | Required fields               |
|-----------------|--------------------------|-------------------------------|
| Request         | web → Python             | `id`, `method`, `params?`     |
| Response        | Python → web             | `id`, `result` XOR `error`    |
| Notification    | web → Python (or vv)     | `method`, `params?` (no `id`) |
| **Event (ext.)**| Python → web (push)      | `event`, `data?` (no `id`)    |

The "Event" shape is the SSE-like extension: when the server wants to
push streaming data (e.g. partial LLM output) without a request, it
emits `{"jsonrpc":"2.0","event":"agent.message_chunk","data":{...}}`
on the appropriate transport:
- **stdio mode**: event written to stdout
- **HTTP mode**: event pushed as a WebSocket text frame on every open
  `GET /ws` connection; the `event` field doubles as the `method` name
  in v0.2.0 (so the frontend can route by `method` without unwrapping
  a nested `data` field)

The frontend `IPCClient.on(event, cb)` registers a WebSocket handler
that dispatches by `method` / `event` name.

## 4. Standard error codes

| Code   | Name              | When                                                  |
|--------|-------------------|-------------------------------------------------------|
| -32700 | ParseError        | malformed JSON, line too large, BOM remaining         |
| -32600 | InvalidRequest    | missing `method`, non-object payload, etc.            |
| -32601 | MethodNotFound    | no handler registered for `method`                    |
| -32602 | InvalidParams     | params failed schema / type check                     |
| -32603 | InternalError     | handler raised an unhandled exception                 |
| -32001 | ToolExecutionError| a tool call inside the agent loop failed              |
| -32002 | PermissionDenied  | user denied the tool call                             |
| -32003 | LLMError          | upstream MiniMax API error                            |
| -32004 | StorageError      | SQLite / DAO error                                    |
| -32005 | NotImplemented    | method is reserved for a later task                   |

All errors carry an optional `data` field with structured context.

## 5. Built-in methods

These are always available — they are registered by
`agent/minimax_code/ipc/builtins.py` and survive across tasks.

### 5.1 `ping` — request → response

Request:
```json
{"jsonrpc":"2.0","id":"r1","method":"ping"}
```

Response:
```json
{"jsonrpc":"2.0","id":"r1","result":{"pong":1780281824.4,"uptime_s":0.01,"server":"minimax-code-agent"}}
```

### 5.2 `status` — request → response

Response (skeleton):
```json
{
  "jsonrpc":"2.0",
  "id":"r2",
  "result":{
    "uptime_s": 12.34,
    "python": "3.12.10",
    "agent": "minimax-code-agent",
    "version": "0.1.0",
    "active_sessions": 0
  }
}
```

### 5.3 `shutdown` — request → response (then EOF)

```json
{"jsonrpc":"2.0","id":"r3","method":"shutdown"}
```
The server replies `{"ok":true}`, then stops reading stdin and exits
on the next `readline() == ""`.

## 6. Application methods (Phase 1)

| Method                     | Direction | Notes                                              |
|----------------------------|-----------|----------------------------------------------------|
| `agent.send_message`       | req/res   | Streams `agent.message_chunk` events.              |
| `agent.cancel`             | notify    | Cancel the current agent loop for a session.       |
| `run.list`                 | req/res   | List persisted agent runs for a session.           |
| `run.steps`                | req/res   | Load one run with its ordered timeline steps.      |
| `patch.preview`            | req/res   | Return structured file/hunk preview for a git diff scope. |
| `session.create`           | req/res   | Reserved (storage-layer).                          |
| `session.list`             | req/res   | Reserved (storage-layer).                          |
| `session.archive`          | req/res   | Reserved.                                          |
| `session.delete`           | req/res   | Reserved.                                          |
| `message.list`             | req/res   | Reserved.                                          |
| `skill.list` / `skill.enable` / `skill.disable` / `skill.invoke` | req/res | Reserved (skills-system). |
| `scheduler.*`              | req/res   | Reserved.                                          |
| `agent.list_agents` / `agent.spawn_subagent` | req/res | Phase 2. |
| `mobile.*`                 | req/res   | Phase 2.                                            |
| `permission.*`             | req/res   | Phase 1.4 (ui-shell).                              |
| `model.list` / `model.set_current` | req/res | Reserved.                                   |

### 6.1 `agent.send_message` — the streaming example

Request:
```json
{
  "jsonrpc":"2.0",
  "id":"hello-1",
  "method":"agent.send_message",
  "params":{"content":"hello","session_id":null}
}
```

Stream (Python → frontend, three `agent.message_chunk` events):
```json
{"jsonrpc":"2.0","event":"agent.message_chunk","data":{"session_id":"ses_39c7c685","message_id":"msg_295faab6","delta":"Hello from Pytho","done":false}}
{"jsonrpc":"2.0","event":"agent.message_chunk","data":{"session_id":"ses_39c7c685","message_id":"msg_295faab6","delta":"n agent! session","done":false}}
{"jsonrpc":"2.0","event":"agent.message_chunk","data":{"session_id":"ses_39c7c685","message_id":"msg_295faab6","delta":"=ses_39c7c685","done":false}}
{"jsonrpc":"2.0","event":"agent.message_chunk","data":{"session_id":"ses_39c7c685","message_id":"msg_295faab6","delta":"","done":true}}
```

Final response:
```json
{"jsonrpc":"2.0","id":"hello-1","result":{"session_id":"ses_39c7c685","message_id":"msg_295faab6","text":"Hello from Python agent! session=ses_39c7c685"}}
```

### 6.2 `patch.preview` — structured diff preview

Request:
```json
{
  "jsonrpc":"2.0",
  "id":"patch-1",
  "method":"patch.preview",
  "params":{"scope":"working"}
}
```

`scope` accepts `"working"`, `"staged"`, or `"branch"`; an explicit
`ref` string overrides `scope` and is passed to `git diff --no-color -M`.
The method is read-only. It keeps `git.diff` unchanged and returns the
same raw diff plus a UI-friendly structure:

```json
{
  "jsonrpc":"2.0",
  "id":"patch-1",
  "result":{
    "scope":"working",
    "ref":null,
    "diff":"diff --git a/app.py b/app.py\n...",
    "stats":{"files":1,"additions":2,"deletions":1},
    "files":[
      {
        "path":"app.py",
        "old_path":"app.py",
        "new_path":"app.py",
        "status":"modified",
        "additions":2,
        "deletions":1,
        "binary":false,
        "hunks":[
          {
            "old_start":1,
            "old_lines":2,
            "new_start":1,
            "new_lines":3,
            "header":"",
            "lines":[
              {"kind":"delete","old_line":1,"new_line":null,"content":"old"},
              {"kind":"add","old_line":null,"new_line":1,"content":"new"}
            ]
          }
        ]
      }
    ]
  }
}
```

## 7. Event names

All push events use the prefix `agent.`, `task.`, or `permission.`
so the frontend can route them by name without a regex.

| Event name              | Data                                                |
|-------------------------|-----------------------------------------------------|
| `agent.message_chunk`   | `{session_id, message_id, delta, done}`             |
| `agent.tool_call`       | `{session_id, tool_call_id, name, args}` (Phase 1.2)|
| `agent.tool_result`     | `{session_id, tool_call_id, result, error?}`        |
| `agent.status`          | `{session_id, status, detail?}`                     |
| `task.progress`         | `{task_id, progress, message?}`                     |
| `permission.request`    | `{request_id, tool, args}` — modal triggers         |
| `permission.resolved`   | `{request_id, decision}`                            |
| `run.created`           | `{run}`                                              |
| `run.step.started`      | `{run_id, step}`                                    |
| `run.step.completed`    | `{run_id, step}`                                    |
| `run.completed`         | `{run}`                                              |

## 8. Lifecycle events

### 8.1 WebSocket lifecycle (HTTP mode)

On `GET /ws` connect, the server pushes one envelope:

```json
{"jsonrpc":"2.0","method":"agent.ready","params":{"server":"minimax-code-agent","version":"0.2.0"}}
```

On agent shutdown, the server cleanly closes the WebSocket. The
frontend `IPCClient` uses this to gate the UI (e.g. disable Send if
the agent is down). Reconnect on close is exponential backoff:
250ms → 500ms → 1s → 2s, cap at 5s.

### 8.2 Health probe (HTTP mode)

`GET /health` returns `{"ok": true, "version": "<agent version>",
"uptime_s": <int>}` as long as the agent is alive. Web client uses
this for the "is the agent running?" badge (with a 200ms timeout
fallback to in-process mock).

### 8.3 stdio lifecycle (legacy, preserved for tests)

For stdio mode the Python server treats `stdin.readline() == ""`
(EOF) as a clean shutdown signal. There is no separate "goodbye"
message — EOF is the goodbye.

## 9. Process & connection lifecycle

- **HTTP mode (default)**: agent process runs as a long-lived
  foreground command. `Ctrl+C` in terminal 1 triggers
  `KeyboardInterrupt` → `cli_entry` exits with `130`. Active
  WebSocket connections receive a clean close. No external supervisor.
- **stdio mode (`--stdio`)**: same as above; EOF on stdin triggers
  `server.run_forever()` return; the CLI exits with `0`.
- **Web client reconnect**: on `GET /ws` close, `IPCClient` retries
  with exponential backoff (see §8.1). The HTTP server doesn't
  queue events for disconnected clients.

## 10. Buffering & encoding

- **HTTP mode**: `POST /rpc` body is a single JSON envelope, framed
  by HTTP itself. WebSocket frames are JSON text frames (no
  fragmentation expected at this scale). The server reads request
  bodies with FastAPI's `await request.json()` and serializes
  responses with `json.dumps(..., ensure_ascii=False)`.
- **stdio mode (legacy)**: the Python agent sets
  `PYTHONUNBUFFERED=1` and `PYTHONIOENCODING=utf-8` (via
  `scripts/dev-agent.mjs`) so stdout/stderr flush line-by-line.
- All modes: **UTF-8, no BOM**. Producers must not emit BOM. The
  server strips a leading BOM for compatibility, but consumers are
  not guaranteed to.

## 11. Test surface

| Layer             | Test file / runner                                | What it covers                                  |
|-------------------|---------------------------------------------------|-------------------------------------------------|
| Python IPC (unit) | `agent/tests/test_ipc.py`                         | `ping`, `status`, parse error, stream           |
| Python unit       | `agent/tests/test_*.py` (per module)              | per-module                                      |
| Frontend          | `web/tests/*.test.ts(x)` (`pnpm test`)            | type contracts + component smoke (vitest)       |
| Python e2e        | `tests/e2e/smoke_*.py`                            | subprocess-driven, agent stdio mode             |
| Web e2e           | `tests/e2e-web/*.spec.ts` (`pnpm test:e2e`)       | Playwright 跨栈 e2e — 浏览器真跑 + agent 真起   |

## 12. Versioning

The protocol version lives in `agent/minimax_code/__init__.py`
(`__version__`) and is mirrored in the root `package.json`
(`version` field) and `web/package.json`. A breaking change to the
IPC contract MUST bump the **minor** version of the agent and
document the migration in this file.

> v0.2.0 起不再有 Tauri `Cargo.toml` — version 三处对齐（agent Python
> package / root `package.json` / `web/package.json`）。
