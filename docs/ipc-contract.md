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
  默认 `http://localhost:5173` / `http://127.0.0.1:5173`（`MINIMAX_CODE_CORS_ORIGINS`
  可追加受信 origin，v0.14.0 起有拒绝路径测试钉死），本机单用户，**不鉴权**。

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
| -32003 | LLMError          | upstream API failure or stalled LLM stream            |
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
| `agent.send_message`       | req/res   | Streams events; the HTTP client keeps no fixed run deadline. |
| `agent.cancel`             | notify    | Cancel the current agent loop for a session.       |
| `run.list`                 | req/res   | List persisted agent runs for a session.           |
| `run.steps`                | req/res   | Load one run with its ordered timeline steps.      |
| `patch.preview`            | req/res   | Return structured file/hunk preview for a git diff scope. |
| `patch.apply_hunk`         | req/res   | Stage one working-tree hunk after validating the current diff. |
| `patch.revert_hunk`        | req/res   | Discard one working hunk or unstage one staged hunk. |
| `patch.apply_file`         | req/res   | Apply all hunks of a single file to the index. |
| `patch.revert_file`        | req/res   | Revert all hunks of a single file. |
| `patch.apply_all`          | req/res   | Apply every file in the current scope to the index. |
| `patch.revert_all`         | req/res   | Revert every file in the current scope. |
| `patch.save_snapshot`      | req/res   | Stash the current working tree before applying patches. |
| `terminal.start`           | req/res   | Start a lightweight command session. |
| `terminal.read`            | req/res   | Read incremental stdout/stderr chunks for a session. |
| `terminal.stop`            | req/res   | Stop a running command session. |
| `terminal.list`            | req/res   | List recent in-memory terminal sessions. |
| `runner.list`              | req/res   | List product-facing runner adapters. |
| `runner.start`             | req/res   | Start a runner; `native` delegates to a terminal session. |
| `session.create`           | req/res   | Create a session; optionally reuse the selected empty local session. Accepts `project_id` (defaults to `inbox`). |
| `session.list`             | req/res   | Reserved (storage-layer).                          |
| `session.archive`          | req/res   | Reserved.                                          |
| `session.delete`           | req/res   | Reserved.                                          |
| `session.updateProject`    | req/res   | Move a session to a different project (including `inbox`). |
| `session.batchArchive`     | req/res   | Archive or unarchive multiple sessions at once.    |
| `session.batchUpdateProject` | req/res | Move multiple sessions to the same project at once. |
| `project.list`             | req/res   | List projects; optional `archived` filter.         |
| `project.create`           | req/res   | Create a project with name and optional description. |
| `project.update`           | req/res   | Rename or update a project's description.          |
| `project.delete`           | req/res   | Delete a project and move its sessions to `inbox`. |
| `project.archive`          | req/res   | Archive a project.                                 |
| `project.unarchive`        | req/res   | Unarchive a project.                               |
| `workspace.create_worktree_session` | req/res | Create an isolated Git worktree-backed session. |
| `workspace.list_worktrees` | req/res | List sessions whose `workspace_mode` is `worktree`. |
| `workspace.delete_worktree` | req/res | Remove a managed worktree and mark the session local. |
| `message.list` / `message.update` / `message.delete` | req/res | List messages in a session; update or delete a single message. |
| `skill.list` / `skill.install` / `skill.uninstall` / `skill.enable` / `skill.disable` / `skill.invoke` | req/res | List, import, remove, configure, and invoke skills. |
| `scheduler.*`              | req/res   | Reserved.                                          |
| `agent.list` / `agent.spawn_subagent` | req/res | Sub-agent list and spawn. `agent.spawn_subagent` is keyed by `name` (`agents.name`); clients may also pass legacy `agent_id`, and the backend resolves by name first, then id. |
| `mobile.*`                 | req/res   | Phase 2.                                            |
| `permission.*`             | req/res   | Tool-call consent rules; ships `exec_*` → ask factory defaults in code (R18). See §6 for the full method table. |
| `model.list` / `model.get_current` / `model.set_current` / `model.set_reasoning_effort` | req/res | Dynamic model list + current selection + reasoning-effort override. `model.list` entries may carry optional reasoning-effort meta (R58); the `model.list` and `model.get_current` responses echo the user's persisted `reasoning_effort` override (R61 read-back). |
| `plugins.list` / `plugins.info` / `plugins.enable` / `plugins.disable` / `plugins.reload` | req/res | Platform pillar #3 — discover, inspect, toggle, and hot-reload runtime plugins (fail-open discovery; runtime enable override is in-memory). |
| `mcp.list_servers` / `mcp.add_server` / `mcp.update_server` / `mcp.remove_server` / `mcp.list_tools` / `mcp.invoke_tool` | req/res | MCP server management and tool invocation (v0.11.0). |
| `codebase.status` / `codebase.build_index` / `codebase.search` / `codebase.summarize` | req/res | Codebase indexing and retrieval (v0.11.0 Milestone 2). |
| `memory.list` / `memory.add` / `memory.delete` / `memory.search` / `memory.extract` | req/res | Long-term memory management (v0.11.0 Milestone 3). |
| `data.export`               | req/res   | Data portability (R21): dump every business table into one self-describing JSON envelope. See §6 for the envelope shape. |
| `data.import`               | req/res   | Data portability (R22): validate + replace-import such an envelope in one transaction (idempotent). See §6. |
| `data.backup`               | req/res   | Data portability (R23): online file-level snapshot via the SQLite backup API. See §6. |

### `model.list` response — reasoning-effort fields (R58)

Each entry in `model.list`'s `models` array is enriched with optional
reasoning-effort fields **only when the model's catalog meta declares them**
(zero regression — a model that declares none gets no new keys, so every
existing MiniMax model passes through byte-identically):

```json
{
  "id": "grok-1",
  "name": "Grok-1",
  "provider_id": "provider-...",
  "protocol": "anthropic",
  "supports_reasoning_effort": true,
  "reasoning_effort_default": "high",
  "reasoning_effort_options": [
    {"value": "low", "id": "low", "label": "Low", "description": null, "default": false},
    {"value": "medium", "id": "medium", "label": "Medium", "description": null, "default": false},
    {"value": "high", "id": "high", "label": "High", "description": null, "default": true}
  ]
}
```

* `supports_reasoning_effort`: `true` — only when the catalog declares a truthy
  `supportsReasoningEffort`.
* `reasoning_effort_default`: the canonical wire token (`"medium"` / `"high"` /
  `"xhigh"` …) — only when the catalog `reasoningEffort` resolves to a known
  tier (the `max` CLI alias normalises to `"xhigh"`; the emit-seam mapping to
  Anthropic `"max"` / OpenAI `"high"` is a separate wire layer, R56/R57).
* `reasoning_effort_options`: the selectable menu — only when the catalog
  `reasoningEfforts` array yields a non-empty list.

### `model.set_reasoning_effort` — reasoning-effort override write-back (R61)

The write companion to the R58 read-side enrich above. When the frontend
persists the user's reasoning-effort choice it calls:

```json
{"method":"model.set_reasoning_effort","params":{"reasoning_effort":"high"}}
```

Response:

```json
{"ok": true, "reasoning_effort": "high"}
```

Semantics:

* `reasoning_effort` is validated strictly via the R53 reasoning
  vocabulary (`parse_effort_strict`). An unknown token surfaces as
  `-32602 INVALID_PARAMS` (not stored as garbage) so the frontend can
  flag a typo. The canonical tokens are `none`, `minimal`, `low`,
  `medium`, `high`, `xhigh`; the `max` CLI/UX alias is honoured and
  **canonicalised on write** (`"max"` is stored as `"xhigh"`).
* `null` (or an empty/whitespace string, or a missing key) **clears**
  the override — the next turn falls back to the model's own default
  effort (the pre-R61 state).
* The override is independent of the model selection: switching models
  leaves the stored effort intact, and vice versa.

Read-back — the persisted override is echoed alongside the model
selection wherever the current preference is read, so the UI can show
the chosen effort without a separate round-trip:

* `model.list` response gains a top-level `reasoning_effort` field
  (next to `current`).
* `model.get_current` response gains a `reasoning_effort` field (next to
  `model`). Both are `null` when no override is stored.

Storage: the override lives in a new nullable `model_prefs.
reasoning_effort` column (migration 014; back-filled `NULL` for existing
rows — backward compatible). The runtime effect — forwarding the stored
effort into the LLM call via `rebuild_subagent_llm` — is deferred to a
later round; this contract only covers persistence + read-back.

Session records may include workspace metadata:

```json
{
  "id": "ses_1234",
  "title": "Worktree task",
  "workspace_mode": "worktree",
  "workspace_path": "C:\\Users\\me\\AppData\\Roaming\\MiniMaxCode\\worktrees\\ses_1234",
  "worktree_branch": null,
  "base_branch": "HEAD"
}
```

### 6.0.1 `workspace.create_worktree_session`

Creates a Git worktree under the MiniMax Code data directory and persists a
session bound to that worktree.

Request:

```json
{"method":"workspace.create_worktree_session","params":{"title":"Worktree task","base_ref":"HEAD"}}
```

Response:

```json
{
  "session_id": "ses_1234",
  "session": {"id":"ses_1234","workspace_mode":"worktree"},
  "worktree_path": ".../worktrees/ses_1234",
  "base_branch": "HEAD"
}
```

`workspace.delete_worktree` accepts `{ "session_id": "ses_1234" }`, removes
only managed worktrees under the data-dir `worktrees/` root, and marks the
session back to `workspace_mode: "local"`.

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

### 6.3 `patch.apply_hunk` / `patch.revert_hunk` — hunk operation

These methods intentionally operate only on the current `working` or
`staged` diff. `branch` / explicit `ref` previews are read-only.

`patch.apply_hunk` stages a single hunk from the working-tree diff.
`patch.revert_hunk` discards a hunk from the working-tree diff, or
unstages a hunk from the staged diff.

Request:
```json
{
  "jsonrpc":"2.0",
  "id":"patch-op-1",
  "method":"patch.revert_hunk",
  "params":{
    "scope":"working",
    "file_path":"app.py",
    "hunk_index":0,
    "old_start":1,
    "new_start":1
  }
}
```

The backend re-reads the current diff, verifies `file_path`,
`hunk_index`, and optional line anchors, builds a single-hunk patch,
runs `git apply --check`, then performs the operation. Binary files,
renames, added/deleted file partial operations, and stale anchors return
an error instead of attempting a risky patch.

Response:
```json
{
  "jsonrpc":"2.0",
  "id":"patch-op-1",
  "result":{
    "ok":true,
    "operation":"revert_hunk",
    "scope":"working",
    "file_path":"app.py",
    "hunk_index":0
  }
}
```

### 6.4 `patch.apply_file` / `patch.revert_file` — file-level operations

`patch.apply_file` stages every hunk of a single file from the working-tree diff.
`patch.revert_file` discards every hunk of a single file from the working-tree
 diff, or unstages every hunk from the staged diff.

Request:
```json
{
  "jsonrpc":"2.0",
  "id":"patch-file-1",
  "method":"patch.apply_file",
  "params":{"scope":"working","file_path":"app.py"}
}
```

Response:
```json
{
  "jsonrpc":"2.0",
  "id":"patch-file-1",
  "result":{
    "ok":true,
    "operation":"apply_file",
    "scope":"working",
    "file_path":"app.py"
  }
}
```

### 6.5 `patch.apply_all` / `patch.revert_all` — scope-level operations

`patch.apply_all` applies every file in the current scope to the index.
`patch.revert_all` reverts every file in the current scope. Both return a
partial-success result listing every file that succeeded or failed.

Request:
```json
{
  "jsonrpc":"2.0",
  "id":"patch-all-1",
  "method":"patch.apply_all",
  "params":{"scope":"working"}
}
```

Response:
```json
{
  "jsonrpc":"2.0",
  "id":"patch-all-1",
  "result":{
    "ok":true,
    "operation":"apply_all",
    "scope":"working",
    "applied":["app.py","utils.py"],
    "failed":[]
  }
}
```

### 6.6 `patch.save_snapshot` — pre-apply stash

Before applying a large patch, the UI can save a snapshot so the user can
recover the original working tree. Returns `clean: true` when there is nothing
to stash.

Request:
```json
{
  "jsonrpc":"2.0",
  "id":"patch-snap-1",
  "method":"patch.save_snapshot",
  "params":{}
}
```

Response:
```json
{
  "jsonrpc":"2.0",
  "id":"patch-snap-1",
  "result":{
    "ok":true,
    "snapshot_ref":"abc123…",
    "clean":false
  }
}
```

### 6.7 `terminal.*` — lightweight command sessions

`terminal.*` is a command-session runner for the inspector panel. It is
not a full interactive PTY yet: clients start a command, poll output
chunks, and stop a running process.
When `cwd` is omitted, the backend uses `MINIMAX_CODE_WORKSPACE` or
`MINIMAX_CODE_WORKSPACE_ROOT` when set, then falls back to the nearest
parent with `pnpm-workspace.yaml`, `AGENTS.md`, or `.git`.

Start request:
```json
{
  "jsonrpc":"2.0",
  "id":"term-1",
  "method":"terminal.start",
  "params":{
    "command":"pnpm test",
    "cwd":"D:\\repo",
    "timeout_s":600,
    "session_id":"ses_current"
  }
}
```

When `session_id` is provided, the agent also creates an `execute`
run timeline entry for the command and streams `run.*` events over the
existing event channel. If storage is unavailable, the terminal command
still runs; timeline tracking is skipped.

Start response:
```json
{
  "jsonrpc":"2.0",
  "id":"term-1",
  "result":{
    "session":{
      "id":"term_abcd1234",
      "command":"pnpm test",
      "cwd":"D:\\repo",
      "session_id":"ses_current",
      "run_id":"run_abc123def456",
      "status":"running",
      "started_at":1781020000.0,
      "updated_at":1781020000.0,
      "completed_at":null,
      "exit_code":null,
      "error":null,
      "next_seq":1
    }
  }
}
```

Read request:
```json
{"method":"terminal.read","params":{"session_id":"term_abcd1234","after_seq":12}}
```

Read response:
```json
{
  "session":{"id":"term_abcd1234","status":"completed","exit_code":0},
  "chunks":[
    {"seq":13,"stream":"stdout","text":"ok\\n","received_at":1781020001.0}
  ]
}
```

`terminal.stop` accepts `{ "session_id": "term_abcd1234" }` and returns
the updated session. Sessions are process-local and intentionally
in-memory; after an agent restart the UI should treat the list as empty.

### 6.5 `runner.*` — product-facing runner adapters

`runner.*` is the adapter layer above terminal sessions. It lets the UI
show a stable set of execution engines while the backend probes whether
each engine is actually executable.

List request:
```json
{"jsonrpc":"2.0","id":"runner-1","method":"runner.list","params":{}}
```

List response:
```json
{
  "runners":[
    {
      "id":"native",
      "label":"Native shell",
      "kind":"native",
      "available":true,
      "command":null,
      "version":null,
      "reason":null,
      "supports_prompt":false,
      "supports_terminal":true
    },
    {
      "id":"codex-cli",
      "label":"Codex CLI",
      "kind":"external_cli",
      "available":false,
      "command":null,
      "version":null,
      "reason":"not runnable: permission denied",
      "supports_prompt":true,
      "supports_terminal":true
    }
  ]
}
```

Start request:
```json
{
  "jsonrpc":"2.0",
  "id":"runner-2",
  "method":"runner.start",
  "params":{
    "runner_id":"native",
    "command":"pnpm test",
    "cwd":"D:\\repo",
    "session_id":"ses_current",
    "timeout_s":600,
    "sandbox_mode":"workspace-write",
    "approval_policy":"never",
    "permission_mode":"acceptEdits"
  }
}
```

Start response:
```json
{
  "runner":{"id":"native","label":"Native shell"},
  "session":{"id":"term_abcd1234","command":"pnpm test","run_id":"run_abc123def456"}
}
```

`available` means the executable was found and its version probe
completed successfully. A PATH hit that fails to execute is returned as
`available:false` with a `reason`.

`runner.start` behavior:

- `native`: treats `command` as a shell command.
- `codex-cli`: treats `command` as a prompt and launches
  `codex exec --sandbox <sandbox_mode> --ask-for-approval <approval_policy> <prompt>`.
- `claude-code-cli`: treats `command` as a prompt and launches
  `claude --print --permission-mode <permission_mode> <prompt>`.

Runner safety options:

- `sandbox_mode` is Codex-only. Allowed values: `read-only`,
  `workspace-write`, `danger-full-access`. Default:
  `workspace-write`.
- `approval_policy` is Codex-only. Allowed values: `untrusted`,
  `on-failure`, `on-request`, `never`. Default: `never`.
- `permission_mode` is Claude-only. Allowed values: `default`,
  `acceptEdits`, `bypassPermissions`, `plan`. Default:
  `acceptEdits`. When set to `default`, the backend omits
  `--permission-mode`.

All runner starts return a terminal session and use the same stdout,
stderr, cancel, timeout, and run timeline plumbing as `terminal.start`.
When `cwd` is omitted, runners use the same workspace-root default as
`terminal.start`.

### `telemetry.*` — in-memory observability bus (R11)

A fire-and-forget event bus that fuses grok-build's `TelemetryEvent`
trait into MiniMax's asyncio runtime. Every observable agent moment —
session lifecycle, turn boundaries, tool dispatch, hook fire, permission
decision — is redacted through three layers (secret shapes → user paths
→ URL origins) and buffered in bounded memory (ring buffer + LRU
metrics). Telemetry never breaks the agent: `emit()` failures are
swallowed and logged; `ensure_telemetry_engine()` returns `None` when
disabled.

| Method | Params | Result | Notes |
|--------|--------|--------|-------|
| `telemetry.recent` | `{ limit?: int, event_type?: str, session_id?: str }` | `{ events: TelemetryEventRecord[], total, enabled, buffered }` | `limit` default 100, capped at 1000. `event_type` accepts the enum value or member (e.g. `"tool_call"`). Returns `enabled:false` when the bus is off — never raises. |
| `telemetry.metrics` | `{ session_id?: str }` | `{ enabled, buffered?, global?, per_session?, latency? }` | With `session_id`: per-session counters + latency p50/p95. Without: global rollup only. All metric fields absent when disabled. |
| `telemetry.clear` | `{}` | `{ ok, cleared, enabled }` | Drains the ring buffer and resets metrics counters in place. |
| `telemetry.trace` | `{ trace_id: str }` | `{ trace_id, spans: SpanRecord[], tree: SpanNode[], span_count, enabled }` | Reconstructs one trace's span tree (R14). `spans` is the flat payload list (trace_id/span_id/parent_id/duration_ms/status/error/attributes); `tree` is the parent→children forest from `build_tree`, each level sorted by `start_ms`. Returns `enabled:false` (and empty lists) when the bus is off — never raises. |

Env switch: `MINIMAX_CODE_TELEMETRY=0|false|off|no` disables the engine
without a restart; the handlers then report `enabled:false` rather than
raising.

Separation of concerns: this bus is in-memory and real-time for all
event types; `audit.*` (`handlers_audit`) is disk-persistent and
restricted to tool dispatch. The two channels run in parallel and are
deliberately uncoupled — disabling one never touches the other.

### `runtime.*` — boot-time crash-recovery diagnostics (R12)

A read-only surface for the boot-time crash-recovery pipeline, fusing
grok-build's `xai-crash-handler` (separate crate, `install()` at entry,
`check_previous_crash()` on next boot) and `cleanup_stale_sessions`
(ORPHAN_RECOVERED semantics) into MiniMax's asyncio runtime. The
marker-file protocol detects unclean exits (OOM / segfault / `kill -9`
— anything that skips `atexit`); `AgentRunsDAO.recover_orphans()` then
flips every in-flight run to `failed` while appending a recovery
`status` step (grok `RewindMarker` append-only audit). The whole
pipeline is fail-open: a recovery fault never blocks boot.

| Method | Params | Result | Notes |
|--------|--------|--------|-------|
| `runtime.recovery_status` | `{}` | `{ available, clean_start?, previous_crash?, crash?, recovered_runs?, run_ids?, sessions?, reason? }` | `available:false` before recovery has run this boot. `clean_start:true` when the previous boot exited cleanly and nothing was recovered. `crash` carries `{crashed_pid, started_at, detected_at}` from the consumed marker. `reason` is set only when recovery could not import its deps (fail-open). |

Lifecycle: `install_faulthandler()` + `mark_dirty_start()` run once in
`cli_entry()` before the event loop; `mark_clean_exit()` is armed via
`atexit`. On the next boot, `_run_crash_recovery()` (called from
`_maybe_open_db`) consumes the marker, recovers orphans, snapshots the
result into a process singleton, and emits a `WARN`-severity telemetry
event (R11) when recovery did something.

Caveat: the data dir must NOT live on a network filesystem —
SQLite-over-NFS plus the marker's temp+rename can corrupt both. Local
disk only.

### `crash.*` — previous-crash report consumption surface (R231)

Read-only access to the persisted crash-report files written by the
R225-R230 recovery layer (the write half: `crash.install` arms the
CrashBlob capture at boot; `check_previous_crash` renders the previous
session's crash into `crashes/last-crash-report.txt` and archives it
under `crashes/history/crash-<timestamp>.txt`). This namespace is the
terminal product surface that closes the loop — the frontend recovery
prompt reads these files to tell the user "your last session crashed"
without re-running the one-shot `check_previous_crash` (which consumes
and deletes `last-crash.json`).

All three methods are **stateless and fail-open**: a missing crash dir,
an unreadable report, or a half-written file collapses to the honest
"nothing available" shape rather than a JSON-RPC error, so a flaky
filesystem never breaks the recovery UI. (This differs from `git.*`,
which surfaces a namespace error on failure — git being unavailable is
actionable; a missing crash report is the normal steady state.) The
only `HandlerError` raised is `INVALID_PARAMS` if a caller passes an
unexpected param shape. `crash_dir` resolves to `<data_dir>/crashes`
(honouring `MINIMAX_CODE_DATA_DIR`), mirroring the R230 startup wiring.

| Method | Params | Returns | Notes |
|--------|--------|---------|-------|
| `crash.previous_report` | `{}` | `{available, report_text}` | `available:false` when `last-crash-report.txt` is absent (no previous crash, or already dismissed). `report_text` is the human-readable rendered report. |
| `crash.history` | `{}` | `{entries: [{filename, timestamp, report_text}]}` | Lists `history/crash-<epoch>.txt`, newest first. `timestamp` is the epoch-seconds parsed from the filename. Non-`crash-<digits>.txt` files are skipped; the list is capped at 50 entries. |
| `crash.dismiss` | `{}` | `{dismissed}` | Removes `last-crash-report.txt` so the recovery prompt hides. The `history/` archive is untouched. `dismissed:false` when there was nothing to remove or the file could not be deleted. |

### `mcp.*` — MCP server management (v0.11.0)

| Method | Params | Result | Notes |
|--------|--------|--------|-------|
| `mcp.list_servers` | `{}` | `{servers: [{id, name, transport, command?, url?, env?, enabled, connected, bearer_token?, headers?, oauth_client_id?, oauth_client_secret?, oauth_scopes?, oauth_callback_port?, tool_states?}]}`] | Returns all persisted server configs plus live connection status. |
| `mcp.add_server` | `{id, name, transport, command?, url?, env?, enabled?, bearer_token?, headers?, oauth_client_id?, oauth_client_secret?, oauth_scopes?, oauth_callback_port?, tool_states?}` | `{server}` | Validates `transport` is `stdio` or `sse`, persists the configuration, and immediately connects the server. `command` (argv list) is required for `stdio`; `url` is required for `sse`. |
| `mcp.update_server` | `{server_id, name?, transport?, command?, url?, env?, enabled?, bearer_token?, headers?, oauth_client_id?, oauth_client_secret?, oauth_scopes?, oauth_callback_port?, tool_states?}` | `{server}` | Updates a persisted server config and re-attaches it when runtime fields change. `tool_states` is a map `{tool_name: enabled}` used to disable individual bridged tools. |
| `mcp.remove_server` | `{server_id}` | `{ok, server_id}` | Disconnects and deletes the persisted server. |
| `mcp.list_tools` | `{server_name}` | `{server_name, tools: [{name, description?, inputSchema?}]}` | Lists tools exposed by a single connected server. |
| `mcp.invoke_tool` | `{server_name, tool_name, arguments?}` | `{ok, server_name, tool_name, text?, content?, isError}` | Calls a tool on the named server. Returns the tool result or a structured error. |

### `codebase.*` — codebase indexing and retrieval (v0.11.0 Milestone 2)

| Method | Params | Result | Notes |
|--------|--------|--------|-------|
| `codebase.status` | `{}` | `{status, processed, total, percent, message, error, stats: {total_chunks, total_files, latest_updated_at}}` | Returns the current lifecycle status of the indexer plus aggregate stats. |
| `codebase.build_index` | `{force?}` | `{status, processed, total, percent, message, error, stats}` | Starts a full index build in the background. If already indexing, returns the current progress. `force=true` clears the existing index first. |
| `codebase.search` | `{query, file_pattern?, limit?, offset?}` | `{query, file_pattern, total, results: [{chunk_id, file_path, start_line, end_line, snippet, language, rank, symbols}]}` | Keyword search over indexed file contents and paths. `file_pattern` is a SQL `LIKE` pattern. |
| `codebase.summarize` | `{path}` | `{path, kind, language, total_lines, symbols, snippet, file_count}` | Returns a structured summary for a file or directory prefix. |

### `memory.*` — long-term memory (v0.11.0 Milestone 3)

| Method | Params | Result | Notes |
|--------|--------|--------|-------|
| `memory.list` | `{project_id?, session_id?, category?, limit?, offset?}` | `{memories: [{id, project_id, session_id, content, category, confidence, source, created_at, updated_at}], total}` | List memories with optional filters. |
| `memory.add` | `{content, category?, confidence?, project_id?, session_id?, source?}` | `{memory}` | Create a new memory. `category` defaults to `fact`; must be one of `preference`, `decision`, `lesson`, `fact`. |
| `memory.delete` | `{id}` | `{ok, id}` | Hard-delete a memory by id. |
| `memory.search` | `{query, project_id?, session_id?, category?, limit?}` | `{memories, total}` | Substring search over memory content, scoped by optional filters. |
| `memory.extract` | `{text}` | `{facts: [{content, category, confidence}]}` | Extract candidate memory facts from raw text without persisting them. |

Memories matching the current session's `project_id` or `session_id` are injected into the system prompt via `## Relevant memories` so the agent can recall prior preferences, decisions, lessons, and facts.

### `permission.*` — tool-call consent rules (R18 defaults)

| Method | Params | Result | Notes |
|--------|--------|--------|-------|
| `permission.list` | `{}` | `{rules: [{id, tool_pattern, action, scope, created_at, origin?}]}` | User rules first, then factory defaults not shadowed by a user rule for the same `tool_pattern`. |
| `permission.get` | `{tool_pattern}` | `{rule}` | Exact-pattern lookup: user rule first, factory default as fallback, `null` when neither exists. |
| `permission.set` | `{tool_pattern, action, scope?}` | `{rule}` | Upsert keyed by `tool_pattern`. `action` ∈ `allow` / `deny` / `ask`; `scope` defaults to `global`. |
| `permission.delete` | `{tool_pattern}` | `{ok, deleted}` | Deletes the *user* rule only — the factory default (if any) becomes effective again. |
| `permission.check` | `{tool_name, scope?}` | `{allowed, action?}` | Resolves a concrete tool name against user rules then factory defaults. `allowed` is `false` only for an explicit `deny`; `ask` reports `allowed: true` + `action: "ask"` (the agent-loop gater turns it into a `permission.request` prompt). |

**Factory defaults (R18).** High-risk tools ship gated in code, not in the
database — nothing is written to `permission_rules` on install:

| Pattern | Action | Origin |
|---------|--------|--------|
| `exec_*` | `ask` | `default` |

Semantics:

* A user rule for the same pattern always wins (DB rules are scanned before defaults).
* Deleting the user rule falls back to the factory default — "delete" means "back to factory", never "silently allow".
* Defaults appear in `permission.list` / `permission.get` with `origin: "default"` and `created_at: ""` so the UI can badge them; they can be overridden via `permission.set` but not removed via `permission.delete`.
* Tools with no matching rule (user or default) are default-allow at the store layer — the caller decides whether to prompt.

The wire shape is `{tool_pattern, action}`; the frontend `TypedIPC` layer
translates to/from its own `{tool, pattern, decision}` shape, and the mock
backend speaks the wire shape (below the typed layer) so mock mode mirrors
real behaviour.

### `data.*` — data portability (R21+)

| Method | Params | Result | Notes |
|--------|--------|--------|-------|
| `data.export` | `{}` | envelope (below) | Dump every business table into a single JSON document; the frontend turns it into a downloaded file. |
| `data.import` | `{envelope}` | `{imported: {table: rows}, skipped_tables: [...]}` | Validate and replace-import an export envelope inside one transaction. |
| `data.backup` | `{target_dir?}` | `{path, bytes}` | Online file-level snapshot via the SQLite backup API; default target is `<data_dir>/backups/`. |

**Export envelope** (the RPC result itself):

```json
{
  "format": "minimax-code-export",
  "schema_version": 24,
  "app_version": "0.14.0",
  "exported_at": "2026-08-21T00:00:00+00:00",
  "counts": {"sessions": 2, "messages": 3, "...": 0},
  "tables": {"sessions": [{...}, {...}], "messages": [{...}, {...}]}
}
```

Semantics:

* Table set is discovered from `sqlite_master` at export time — future
  migrations that add tables are picked up automatically, no hardcoded list.
* `schema_migrations` is excluded; the applied version travels once in the
  top-level `schema_version` field.
* Virtual tables (FTS / vec shadows) are excluded — they are derived state,
  rebuilt on the target by triggers / re-indexing after `data.import` (R22).
* Rows serialise as plain `{column: value}` dicts; `counts[table]` is the
  row count and `set(counts) == set(tables)` always holds.
* Storage not initialised (e.g. `MINIMAX_CODE_NO_DB=1`) replies
  `-32603` with a "storage not initialised" message instead of an empty dump.

**`data.import` semantics (R22):**

* Validation first, `-32602` on failure: envelope must be an object with
  `format == "minimax-code-export"`, `tables` mapping to lists of row
  objects, and an integer `schema_version` **no newer than** this
  install's (importing a future export is rejected — upgrade first).
* **Replace, not merge** — each envelope table is fully `DELETE`-d then
  refilled; tables absent from the envelope keep their current rows.
  Re-importing the same file is therefore idempotent.
* **Column whitelist** — row keys are intersected with the target
  table's `PRAGMA table_info` columns (drifted columns dropped, absent
  columns fall back to SQL defaults); values are always bound
  parameters. Envelope tables unknown to this install are reported in
  `skipped_tables`, not fatal.
* **All-or-nothing** — the whole import runs inside one
  `BEGIN IMMEDIATE` transaction with `PRAGMA foreign_keys=OFF` around it
  (SQLite bulk-load idiom); any failure rolls back to the pre-import
  state. FTS indexes stay in sync via the existing triggers on
  `memories`; vec indexes are rebuilt by re-indexing.

**`data.backup` semantics (R23):**

* File-level **complete snapshot** via the SQLite online backup API —
  schema, WAL contents, FTS indexes and vec shadows included — taken
  without blocking readers. Complements the JSON export (which is
  portable across schema versions but excludes derived state).
* `target_dir` is created if missing; omitted → `<data_dir>/backups/`.
  The filename embeds a UTC timestamp with milliseconds
  (`minimax-code-backup-YYYYMMDD-HHMMSS-mmm.db`), so repeated backups
  never overwrite each other.
* The source database is only ever read, never written.

## 7. Event names

All push events use the prefix `agent.`, `task.`, or `permission.`
so the frontend can route them by name without a regex.

| Event name              | Data                                                |
|-------------------------|-----------------------------------------------------|
| `agent.message_chunk`   | `{session_id, message_id, delta, done}`             |
| `agent.tool_call`       | `{session_id, tool_call_id, name, args, message_id}` (Phase 1.2) |
| `agent.tool_result`     | `{session_id, tool_call_id, name?, result, error?, message_id}` |
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

#### Event replay on reconnect (v0.13.0)

Every broadcast event carries a monotonic top-level `seq` field:

```json
{"jsonrpc":"2.0","method":"agent.message_chunk","seq":42,"params":{...}}
```

The server keeps the last **512** sequenced events in a bounded
history ring. A client reconnecting with a resume cursor —

```
GET /ws?since=<last seq seen>
```

— receives, right after `agent.ready`, all retained events with
`seq > since` replayed in order through the same stream. This closes
the "fire-and-forget" gap: events published while the browser was
reconnecting are no longer silently lost.

Rules:

- Lifecycle frames (`agent.ready`, `agent.ping`) carry **no `seq`**
  and are never replayed.
- `seq` is optional on old envelopes; clients that ignore it behave
  exactly as before (backward compatible).
- If `since` is ahead of the ring (server restarted), nothing is
  replayed — the client detects the seq jump on the next live
  broadcast.

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
  with exponential backoff (see §8.1) and reconnects with
  `?since=<last seq>` so missed events are replayed (v0.13.0, §8.1).

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
