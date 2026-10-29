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
- `GET /ws` 连接成功后，服务端立刻发 `{"method":"agent.ready","params":{"server":"minimax-code-agent","version":"0.2.0","next_seq":<int>}}`（`next_seq` 为下一条广播事件的 seq 纪元锚点，v1.2.2）
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
  `permission.request` / `permission.resolved` 等）。

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

> **v1.3.0 — per-project scoping.** The methods marked **`project_id?`** below accept an
> optional `project_id` parameter. When the referenced project has a bound `root_path`
> (set via `project.create` / `project.update`), the call is anchored at that root:
> relative paths resolve against it and absolute paths must stay inside it (out-of-root
> access fails with `-32602`). When the parameter is omitted — or the project is
> unrooted — behaviour is identical to v1.2.2 (process root from
> `MINIMAX_CODE_WORKSPACE` / cwd). `agent.send_message` resolves the root implicitly
> from the session's project, so it needs no explicit parameter.

| Method                     | Direction | Notes                                              |
|----------------------------|-----------|----------------------------------------------------|
| `agent.send_message`       | req/res   | Streams events; the HTTP client keeps no fixed run deadline. v1.1.0 reply adds `truncated`, `blocks`, `compactions` (block accounting — when `MINIMAX_AUTO_CONTINUE=1` the turn auto-continues up to `MINIMAX_AUTO_CONTINUE_MAX_BLOCKS` blocks, default 5, summing iterations/compactions across blocks). |
| `agent.cancel`             | req/res   | Request cancellation of active and queued native work for a session; acknowledgement does not imply cleanup has finished. |
| `agent.continue_run`       | req/res   | Continue a truncated block (params: `session_id`, optional `run_id`). A pinned source has one durable continuation; retries join its worker or replay its receipt. Admission atomically marks the source `continued` and links the child. Omitting `run_id` selects the latest run for legacy clients. Invalid params use `-32602`; unavailable storage/non-truncated source returns `{"ok":false}`. |
| `agent.answer_user`        | req/res   | v1.1.1 Answer a pending `ask_user` request (params: `request_id`, `answers` — a non-empty array position-aligned with the questions; each entry is a label string or a list of label strings). Resolves the future the suspended tool call awaits; `{"ok":false}` on unknown/expired ids. |
| `run.list`                 | req/res   | List persisted agent runs for a session. v1.4.0 accepts an optional `mode` filter (`chat` / `plan` / `execute` / `team` / `subagent`) — `mode: "subagent"` returns runs spawned via the `spawn_subagent` tool path. |
| `run.steps`                | req/res   | Load one run, ordered steps and a consistent durable `cursor`. |
| `run.events`               | req/res   | Read persisted changes after a run-scoped sequence, independent of WS replay. |
| `run.start` | req/res | Durably admit a native request; return `{run, created}` immediately. Requires session, content and idempotency key. |
| `run.get` | req/res | Read `{run, result}`; result is a committed reply, an error envelope, or null. |
| `run.cancel` | req/res | Request cancellation of one run; `requested` is acknowledgement, not completed teardown. |
| `run.resume` | req/res | Create a linked run from saved input after review, using the original model and workspace; never replay tool steps. |
| `run.steer` | req/res | Record idempotent Native-only in-flight input against one run. |
| `run.child_workspace` | req/res | Review or explicitly collect a directly owned child workspace with binding and conflict checks. |
| `run.child_list` | req/res | Recover recent direct child runs scoped to the parent session, parent runs, project and service. |
| `run.output_page` | req/res | Read a bounded page of captured tool output bound to a run, session, project and service. |
| `run.output_inventory` | req/res | Inspect global usage and current-session captured-output records. |
| `run.output_clear` | req/res | Explicitly clear one finished run's captured output, retaining a tombstone. |
| `run.research_list` | req/res | List current project's recorded page sources with session and service binding. |
| `run.research_get` | req/res | Read a recorded page source with expected service identity. |
| `patch.preview`            | req/res   | Return structured file/hunk preview for a git diff scope. **`project_id?`** |
| `patch.apply_hunk`         | req/res   | Stage one working-tree hunk after validating the current diff. **`project_id?`** |
| `patch.revert_hunk`        | req/res   | Discard one working hunk or unstage one staged hunk. **`project_id?`** |
| `patch.apply_file`         | req/res   | Apply all hunks of a single file to the index. **`project_id?`** |
| `patch.revert_file`        | req/res   | Revert all hunks of a single file. **`project_id?`** |
| `patch.apply_all`          | req/res   | Apply every file in the current scope to the index. **`project_id?`** |
| `patch.revert_all`         | req/res   | Revert every file in the current scope. **`project_id?`** |
| `patch.save_snapshot`      | req/res   | Stash the current working tree before applying patches. **`project_id?`** |
| `terminal.start`           | req/res   | Start a lightweight command session. **`project_id?`** anchors the default cwd (an explicit `cwd` must then stay inside the root). |
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
| `project.create`           | req/res   | Create a project with name, optional description, and optional `root_path` (v1.3.0; must be an existing directory when non-empty — normalised via expanduser/resolve). |
| `project.update`           | req/res   | Rename or update a project; `root_path` may be set or cleared (`""` unroots the project, `null`/omitted leaves it unchanged). |
| `project.delete`           | req/res   | Delete a project and move its sessions to `inbox`. |
| `project.archive`          | req/res   | Archive a project.                                 |
| `project.unarchive`        | req/res   | Unarchive a project.                               |
| `workspace.create_worktree_session` | req/res | Create an isolated Git worktree-backed session. Optional `project_id` (v1.3.0) files the session under that project — unknown ids fail fast with `-32602` before any checkout is created; omitted → `inbox`. |
| `workspace.list_worktrees` | req/res | List sessions whose `workspace_mode` is `worktree`. |
| `workspace.delete_worktree` | req/res | Remove a managed worktree and mark the session local. |
| `workspace.merge_worktree` | req/res | M3 — commit pending worktree changes, run the project validate command inside the worktree, then `--no-ff` merge into the main checkout. Conflicts abort cleanly and report unmerged files; a failing validation stops the merge with evidence. |
| `engine.list` | req/res | M4 — capability cards for every registered engine (native / Codex), availability probed live; `default: "native"`. |
| `engine.status` | req/res | M4 — one engine's card by id. Unavailable engines carry `unavailable_reason` so the UI can disable them explicitly. |
| `host.create` | req/res | M5 — register a remote Linux run host (name/hostname/username/SSH port/remote_port/identity_file reference — credentials are never stored). Duplicate names and missing identity files fail fast. |
| `host.list` | req/res | M5 — list hosts, optional `enabled` filter. |
| `host.connect` | req/res | Start an owned loopback SSH forward to an existing remote agent using its saved `remote_port` (default 8765); an optional `remote_port` overrides it for this connection only. Returns connection status. |
| `host.disconnect` | req/res | Close and reap the host's SSH forward. |
| `host.connection` | req/res | Read the current forward status. |
| `host.get` | req/res | M5 — one host by id. |
| `host.update` | req/res | M5 — tri-state field update incl. enable/disable and `remote_port`; connection coordinate changes disconnect an existing tunnel. |
| `host.delete` | req/res | M5 — remove the host (its runs are history and survive). |
| `host.probe` | req/res | M5 — SSH connection diagnosis + version handshake (uname / python3 / codex), outcome persisted (`last_probe_at` / `last_probe_ok` / `agent_version`). |
| `host.exec` | req/res | M5 — execute one command on the remote host over ssh (BatchMode, bounded timeout); structured `{ok, exit_code, stdout, stderr, timed_out}`. Disabled hosts refuse. |
| `host.push` | req/res | M5 — stage a local file beside the remote destination, verify sha256 and destination identity, retain a verified backup, then atomically replace. Optional pre-recorded `transfer_id` supports later outcome queries; uncertain writes are never retried automatically. |
| `host.fetch` | req/res | M5 — scp a remote file to the local machine with the same sha256 verification. |
| `host.transfer_status` | req/res | Query one remote upload journal by `{host_id, remote_path, transfer_id}`; never transfers a payload or repeats publication. Missing journals and transport failures return `ok:false, publication_state:"unknown"`. |
| `message.list` / `message.update` / `message.delete` | req/res | List messages in a session; update or delete a single message. |
| `skill.list` / `skill.install` / `skill.uninstall` / `skill.enable` / `skill.disable` / `skill.invoke` | req/res | List, import, remove, configure, and invoke skills. |
| `scheduler.*`              | req/res   | Reserved.                                          |
| `agent.list` / `agent.spawn_subagent` | req/res | List configurations or admit a durable Native agent root. Spawn and invoke require explicit Native source `session_id`, stable `idempotency_key`, nonempty string `request`, and required `name` (which may itself carry a legacy ID), with optional compatibility `agent_id`. See the execution contract below. Internal tool children also retain progress events and their own persistent runs. |
| `mobile.*`                 | req/res   | Phase 2.                                            |
| `permission.*`             | req/res   | Tool-call consent rules; ships `exec_*` → ask factory defaults in code (R18). See §6 for the full method table. |
| `model.list` / `model.get_current` / `model.set_current` / `model.set_reasoning_effort` | req/res | Dynamic model list + current selection + reasoning-effort override. `model.list` entries may carry optional reasoning-effort meta (R58); the `model.list` and `model.get_current` responses echo the user's persisted `reasoning_effort` override (R61 read-back). |
| `plugins.list` / `plugins.info` / `plugins.enable` / `plugins.disable` / `plugins.reload` | req/res | Plugin toggles synchronise future hooks in active sessions; already started processes are not recalled. Runtime overrides are in memory and reload or restart uses manifest state. Hooks are not OS-sandboxed. |
| `mcp.list_servers` / `mcp.add_server` / `mcp.update_server` / `mcp.remove_server` / `mcp.list_tools` / `mcp.invoke_tool` | req/res | MCP server management and tool invocation (v0.11.0). |
| `codebase.status` / `codebase.build_index` / `codebase.search` / `codebase.summarize` | req/res | Codebase indexing and retrieval (v0.11.0 Milestone 2). **`project_id?`** (v1.3.0): indexes are keyed per project root and mutually isolated. |
| `memory.list` / `memory.add` / `memory.delete` / `memory.search` / `memory.extract` | req/res | Long-term memory management (v0.11.0 Milestone 3). |
| `data.export`               | req/res   | Data portability (R21): dump every business table into one self-describing JSON envelope. See §6 for the envelope shape. |
| `data.import`               | req/res   | Data portability (R22): validate + replace-import such an envelope in one transaction (idempotent). See §6. |
| `data.backup`               | req/res   | Data portability (R23): online file-level snapshot via the SQLite backup API. See §6. |
| `diag.export`               | req/res   | Diagnostics (R45): sanitized diagnostic bundle — version / platform / config (enums & counts only, never secret values) / per-table row counts / sanitized log tail. Degrades to `storage: {db_available: false}` when storage is down instead of erroring. |

### Sub-agent working copies (M1, additive result fields)

Public host `agent.invoke` and `agent.spawn_subagent` share the existing Native admitted executor.
A replay finds the source/key intent before live configuration/model lookup. Required
`name` (name or legacy ID) resolves first; optional `agent_id` is a compatibility identity
fallback. Changed input under that key is INVALID_PARAMS. Optional
`parent_session_id` is a source-session alias and must agree with `session_id` when both are
present. `context_message_id` and `display_name` are provenance/display fields. Client `run_id`
is only a receipt association; callers cannot supply family IDs, execution instructions or
internal profiles. Agent CRUD skills is an ordered list of unique non-blank strings. Execution resolves exact composite IDs first, or a unique short name; unknown/ambiguous/disabled/duplicate canonical dependencies fail before new root admission. New admissions save Agent profile v3, retaining v2's bindings v1 (canonical IDs/version/full frozen instructions/manifest and actual private exports/provider class/schema identity), and freeze the resolved execution target described below. Replay still precedes live resolution. None tool_allowlist freezes baseline plus selected provider candidates; [] permits no tools; explicit lists must include all skill-required tools and cannot add unknown names. Tool-name collisions and reserved runtime receipt names refuse. Skills share the root Native Core/client/model/budget/copy and do not change its generic result format. Current dependency/provider/allowlist/permission revocations tighten queued or reviewed recovery/continue. Known no-skills Agent v1 and valid v2 remain legacy-host compatible; damaged profiles fail closed without generic fallback. Skill bindings themselves add no migration; execution profiles use Schema039/040. Trusted descendants retain their own bindings and the parent's actual business tool cap; only server-constructed child-bound report_completion/report_progress channels are injected separately. See [Agent execution](agent-execution.md) and [Skill configuration](skills.md).

Admission atomically saves an independent execution session, queued run, new budget family,
event and schema038 source/key mapping. Only the created RunService owner prepares one working
copy, persists its identity and enters the Native core. Public roots do not inherit the source's
budget family. Trusted tool descendants inherit their actual durable parent's family.
`admission_only:true` returns the normal `{run,created}` admission envelope. Waiting calls retain
`agent`, `text`, `agent_run_id` and add `run_id`, execution `session_id`, `run_status`, `status`,
partial flags and workspace evidence; the durable result is saved before terminal completion.
Queries and replay return that same receipt, including after restart.

`agent.cancel_subagent` takes the durable `run_id` and returns request acknowledgement
(`cancel_requested`/compatibility `cancelled`) plus observed `run_status`; acknowledgement does
not certify cleanup finished. Read the run's actual terminal state. Reviewed `run.resume` and
`agent.continue_run` retain saved profile, prepared copy and cumulative family. Pending or
unconfirmed preparation cannot be rebuilt automatically. See [agent-execution.md](agent-execution.md)
for supported models, revocations, recovery and evidence boundaries.

Real `agent.invoke`, `agent.spawn_subagent`, `spawn_subagent` tool runs and
`team.spawn` members now use an independent working copy by default. Git roots
use detached worktrees; other roots use owned directories. `sandbox=false`
does not restore shared-directory execution. The explicit legacy
`sandbox=true` file overlay remains available and still forbids shell commands.
Public Native mock execution uses the same durable admission and working-copy contract.
The frontend-only mock backend simulates those receipts without filesystem isolation.

### Agent execution profile fields (Schema040 / Agent profile v3)

`agent.create` and `agent.update` accept persistent `execution_profile_id`: `null` or a
1–64 character ID matching `^[a-z][a-z0-9_-]*$`. Create omission/null means the default;
update omission preserves the saved value, while explicit null restores the default.
Agent definitions returned by list/get/create/update include this nullable field. Unknown
IDs are retained for configuration portability, but execution refuses an unknown,
disabled or unavailable ID. There is no per-invocation profile, image, resource or env override.

`agent.list` returns `{agents, execution_profiles}`. Each capability card has:

| Field | Type | Meaning |
|---|---|---|
| `id`, `label` | string | Server catalog identity and display name. |
| `mode` | `"host"` or `"podman"` | Actual execution mechanism. |
| `available` | boolean | Current service readiness, including ownership, extension and deadline gates. |
| `reason` | string or null | Why the profile cannot accept new work. |
| `unattended_supported` | false | L1 does not authorize scheduler/webhook/team execution. |
| `boundary_summary` | string | Visible scope of isolation. |

A public Native root with omitted/null configuration resolves to `native-host`. A trusted
child's omitted/null configuration inherits its parent's frozen target; an explicit different
ID must be rejected. This inheritance is a required contract; the restricted child/terminal
support chain remains unfinished in the current foundation checkpoint. Ordinary host execution
keeps its existing behavior.

Every new v3 snapshot saves `execution_target`, its canonical SHA-256
`execution_target_hash`, and `execution_installation_id` (null for host). Host target is
`{version:1, policy_version:1, mode:"host", profile_id:"native-host"}`. Podman target is
version 1/policy 2 with mode/profile/image ID, `memory_bytes`, `cpus_millis`, `pids_limit`,
`timeout_seconds`, `tmpfs_bytes`, `output_limit`, and a governor object containing
`kind:"pid1-boottime-v1"`, `guard_sha256` and `seccomp_sha256`. These are server-resolved
fields, not invocation input. Canonical JSON sorts keys, uses compact UTF-8, and rejects
NaN/infinity. Missing/malformed v3 fields cannot be treated as legacy host or upgraded on
recovery. Historical v1/v2 retain their legacy-host meaning without an implicit upgrade.

Completed v3 receipts include those same actual target/hash/installation fields; query/replay
uses the saved receipt. The UI displays the saved target rather than the agent's later setting.
The browser mock returns a simulated host receipt and marks Linux L1 unavailable. The current
product also keeps Linux L1 unavailable: the accepted governor pair set is empty. Limited
Linux candidates have separate source-scoped evidence; raw `88e0ceb` final Exec-only
wiring passed three finite integration cases. Public attach backpressure/drain and full
release acceptance remain pending. An unavailable profile always
refuses execution; it never falls back to host. L1 describes supervised process isolation,
not a complete filesystem sandbox or disk quota. See the
[profile contract](linux-execution-profile-contract.md) and
[foundation status](linux-process-adapter-design.md).

The current supervised Podman public candidate is `agent.invoke` only: exactly one actual
`ExecCommandTool`, frozen `tool_allowlist:["exec_command"]`, empty skills/bindings, hooks
disabled and permission version1. Text-only skills, Native file tools and the
`agent.spawn_subagent` alias are rejected. Admission, saved-spec restore, durable owner
authorization and active idempotent joins enforce that same shape; historical terminal
receipts remain readable. Existing private zero-model preflight may occur before skill/tool
rejection; this does not authorize user-model execution, workspace preparation or dispatch.
Host behavior is unchanged. No RPC method, input field or schema is added by this restriction.

Their result may include `workspace` (`ChildWorkspaceSummary` in Python/TS):

```json
{
  "run_id": "run_example",
  "path": "/project/.minimax/child-workspaces/run_example/workspace",
  "kind": "git_worktree",
  "state": "completed",
  "files_written": ["src/example.py"],
  "omitted": [],
  "collection_required": true
}
```

`kind` is `git_worktree` or `directory`. States are `preparing`, `running`,
`completed`, `failed`, `cancelled`, `needs_review`, `collected`. Team responses
and persisted `team.run.get` results carry the same optional field on each
`agents_run` member; tool-path run metadata and check/wait responses retain it.
`path` names an execution-host directory, not a browser download URL. Completion
does not mean edits were merged or independently verified.

`collect_subagent(run_id)` recognizes both new copies and legacy overlays.
It checks the original hashes against the current parent files, preflights
conflicts before a default `fail` collection, and atomically applies additions
and modifications with backups. Changed parent files, aliases, post-run child
edits and deletions are retained for review; `skip` can collect nonconflicting
edits but leaves `complete=false`. Successful receipts are replayable after a
restart. New copies/backups are retained even when `prune=true`; that parameter
only prunes legacy overlays. A process restart does not silently reclassify a
`running` manifest as finished (recovery remains an M2 task).

See [child-workspaces.md](child-workspaces.md) for limits and operational details.
No RPC is added or removed, and no SQLite migration is required.

### Durable Native skill execution (B1)

`skill.invoke` requires `{skill_id, session_id, idempotency_key, request}`. The
session must already exist and use Native; `request` is a non-empty string.
Owned skill/Agent tool dispatch checks the frozen provider provenance against
the current enabled skill, version, manifest tools and live provider object.
Revocation blocks later provider calls but cannot retract an in-flight call or
text already sent to a model. MCP bridge objects retain their live registry
identity when private Agent registries are cloned, so later disable/reconnect
checks are not detached by deepcopy. These checks do not provide OS sandboxing.
The legacy `SkillRuntime.invoke` also rechecks its skill/provider after its
per-skill wait and before provider installation. If configured with a shared
registry, its newly installed tool instances carry a live capability token
across copies; disable, provider replacement or installation teardown blocks
subsequent dispatch, and teardown removes only its own installed instances.
The default application runtime has no shared registry in this legacy path.
Optional `expected_service_id` and `expected_project_id` bind admission or replay
to the current service and session project; when the service expectation is
supplied, a null expected project means the unbound project. Legacy callers may
omit these fields. They do not alter the idempotency fingerprint.
Optional fields are `diff` (non-empty unified diff, code-review only),
`max_iterations` (1–100), `model` (must match the selected model),
`budget_tokens` (positive cumulative family ceiling), `expected_workspace`
(must match the resolved session directory), `label`, and `admission_only`.
Unknown fields, executable instructions, tool lists and family IDs are rejected.

`admission_only:true` returns `{run, created}` after SQLite admission. Otherwise
the handler waits for the retained worker and returns the saved skill receipt.
Disconnecting a waiter does not cancel execution. Same session/key + same intent
joins or replays the original run **before** consulting current skill/model
settings. A different skill, input, diff or option under that key returns
`-32602`. Admission-only mode does not change request identity.

The receipt is stored before terminal completion and is also the `result` from `run.get`:
normal Native fields plus `skill_id`, `output` (alias of `text`), `status`
(alias of `run_status`), `tool_calls`, `partial`, `budget_exhausted`, and for diff
reviews `stats:{files, additions, deletions}` and `comments:[]`. Real review text
does not imply parsed structured findings. Offline deterministic diff reviews
return findings from the actual local diff walker. Failed receipts preserve the
normal RPC error projection; inspect `run.get` for the full saved receipt.

`run.cancel`, `run.resume`, `agent.continue_run`, timeline/events and `budget.get`
apply to these runs. Resume/continue retain the saved skill profile and budget
family; they cannot turn into generic chat. Disabled/missing skills, changed
version/tool-provider identity or incompatible model-provider configuration
require a new task. Restart recovery preserves text/usage, projects status to
`interrupted`/`partial:true`, and never automatically replays writes.

`git.diff` additionally accepts `session_id` for an existing Native session and
returns `session_id` and resolved `workspace`. This binding cannot be mixed
with `cwd`/`project_id`. The review UI uses that same session/workspace for diff
and admission, retains uncertain request keys, and reloads durable results.
See [skill-execution.md](skill-execution.md) for ownership and permission limits.

For task review, `git.diff({run_id, session_id})` reads the **current** bound
workspace for a Native or Codex run and returns `{run_id, session_id, project_id,
workspace, scope:"current_workspace", diff, staged_diff, untracked}`. The two
diffs cover tracked unstaged and staged changes; `untracked` lists names only.
Git status paths retain whitespace, including leading and trailing spaces.
No committed changes are included. No other parameters may be supplied. The
run's recorded project, engine and workspace must still match the session, and
host restrictions apply. Runs without these bindings cannot use this view.
Results can include edits by the user or other runs and are not attribution to
the selected run. The separate Git reads are not an atomic snapshot.
In `git.status`, a rename is listed under its destination; its source is not a
separate entry.

`agent.send_message` and `run.start` accept optional boolean
`capture_file_changes` (default false, included in request identity when true).
When enabled, service-owned execution samples Git-visible regular files in the
bound workspace immediately before and after execution. A durable observation
step with `payload.source:"run_file_changes"` contains `available` and, when
available, `added`, `modified`, and `deleted` path lists. File contents and
hashes are not persisted. Sampling is bounded and best effort, not atomic or
proof of authorship; cancellation, restart, changed binding, unsupported paths,
read failure or limits can leave it unavailable. See the user guide for limits.

### Durable run journal

`run.steer({run_id, session_id, idempotency_key, text})` records at most 8000
characters against a live Native run. Repeating a key with identical text
returns its existing step; different text conflicts. At most ten inputs wait
per run. Pending means accepted but not used; completed means added to later
model context, not proof that a model received or obeyed it. Failed or
cancelled means use was not confirmed. The loop drains at a model boundary,
never inside a tool. A final answer, cancellation, budget stop or restart may
leave input unused; it is never automatically replayed. Codex is unsupported.
Run, session, project and session host policy are checked.

`run.steps({run_id})` now returns `{run, steps, cursor}` from one database
transaction. `cursor` is the highest persisted sequence for that run; an older
run without journal entries has cursor 0. Existing `run.list` callers are unchanged.
New Native generation-tool results may also add completed observation steps
with `payload.source:"run_attachment_v1"`, containing the verified attachment
ID, project and service IDs, SHA-256, filename and generating tool. These mean
the tool returned a confirmed project attachment; an idempotent call can return
an older attachment. Partial reports record only confirmed saved formats.
Existing unassociated outputs are not inferred; a saved file may lack a step
if recording failed. Preview and content APIs remain authoritative and clients
must recheck the association and byte identity before displaying or downloading.
For a run that has never started, `queued_input` is null or
`{text, truncated}`, projecting the saved string request up to 8000 characters.
It is cleared after the run starts; non-text inputs do not get this projection.
This permits recovery of accepted queued text after reconnect without treating
it as an in-flight instruction. Startup recovery may mark queued runs as
interrupted; it does not automatically execute them.
It also returns `pending_ask_user`, null or a Native question whose owning
in-process future is still pending for that run and session. This optional live
projection is not part of the database transaction or a restartable wait. Its
`remaining_ms` is sampled by the server; clients treat it as an estimate and
the answer endpoint remains authoritative on expiration and resolution.
`pending_native_permissions` similarly contains only pending Native consent
requests owned by this run and session, or an empty list. It is a live memory
projection, not persisted approval; `remaining_ms` is an estimate and
`permission.resolve` rejects expired or already resolved requests. The separate
`pending_codex_permissions` projects only Codex requests attached to this run,
session and still-live connection. A new internal request ID is bound to each
external request; the original JSON-RPC response retains its original ID.
Closing, cancellation, timeout and process loss invalidate the wait. Recovering
a request never auto-approves it; neither projection survives a process restart.

For a live service-owned run, `run.steps` may also include `live_stream`:
`{complete, messages:[{message_id, text, seq, done}]}`. It is a bounded
in-memory snapshot, not a durable transcript. Sequenced `agent.message_chunk`
events use `stream_seq`, monotonic per message; clients can ignore duplicates,
buffer gaps and request another snapshot. Snapshot and event transport are
independent, so consumers must compare sequence values before applying either.
At 2 MiB UTF-8, 20,000 chunks or 256 segments the snapshot becomes incomplete;
subsequent chunks carry `stream_unavailable`, and clients should signal that
live recovery is unavailable rather than invent missing text. These chunks also
carry a run-wide monotonic `stream_event_seq` so connected clients can continue
displaying new text without replaying duplicate events; missed text beyond the
limit cannot be reconstructed from this snapshot. Final persisted
history remains the authority after completion; interrupted unsaved Native
text and the in-memory snapshot do not survive an Agent process restart.

`run.events({run_id, after_seq?: 0, limit?: 100})` returns
`{events, next_seq, has_more}`. `after_seq` is a non-negative integer (at most
JavaScript's safe integer limit); `limit` is 1–500. Unknown runs, extra keys,
invalid types and cursors ahead of the run return `-32602`. A cursor ahead of the
database means the caller must reload the snapshot, for example after restoring
an older backup. Always take a new snapshot when reconnecting to a different
service or after an import; cursors are scoped to this execution database and run.

Each event has `{id, run_id, seq, type, created_at, data}`. IDs are persistent
opaque strings, sequences increase within one run, and times are UTC ISO strings.
Types are `created`, `status`, `step_created`, `step_updated`, and `recovered`.
The `data` object's `run` and/or `step` fields contain the row at that change,
not its later state.
Clients can take a snapshot, then page events strictly after its cursor until
`has_more` is false; repeated reads have stable IDs and sequence numbers.

Run/step changes and their events commit in the same transaction before existing
WS notifications. Event failure rolls back the state change. This journal covers
existing DAO-backed chat, team and tool-path sub-agent timelines, not streamed
text deltas, permission prompts or every external side effect. The browser's full
reconnect integration and RunService admission/state machine remain follow-up work.
Boot recovery marks unfinished runs `interrupted`. An unfinished tool step is
closed as `failed` to end its waiting state, while its payload records
`outcome: "unknown"` and `requires_review: true`; this does not establish whether
its side effects occurred. Original dictionary fields are retained; non-dictionary
payloads are retained under `original_payload`. The recovery event and step carry
the same evidence. Tools are not automatically retried. If the run was budget
paused before restart, resumption still requires an explicit `budget_tokens` value;
an unfinished tool additionally requires the caller to review its side effects.
Terminal run steps with `payload.source = "terminal"` periodically checkpoint a
bounded `output_tail` and stdout/stderr segments while running, then persist a
final tail on completion. `output_truncated` indicates whether earlier output was
omitted; `output_may_be_incomplete` warns that later output may not have been
saved. Older records may omit these flags. Checkpoints are at most once per second
and each appends a durable step event; output since the last commit can be lost.
This is not a complete output archive, and anonymous terminals have no durable run
record.

### Preview HTTP access (M1)

`POST /preview/access` requires application authentication and accepts
`{"session_id": "..."}` (or `null` for the configured default directory).
It resolves the current directory before issuing a bounded read capability:

```json
{"path": "/preview/view/<opaque-grant>", "expires_in": 899}
```

`PreviewAccessResult` is shared by Python and TypeScript. `path` is relative
to the service origin; `expires_in` is the remaining lifetime in seconds,
at most 900. Grants are in memory, reused for an unchanged binding while
sufficient lifetime remains, and capped at 128 active bindings. Application
tokens never enter these URLs. A grant authorizes GET/HEAD only under its
own `/preview/view/<grant>/` prefix; file containment and sensitive-path
checks still apply. It cannot authenticate RPC or another grant request.
The server rechecks the bound workspace for each file request. Missing or
expired access returns 401; a changed binding returns 409. Reopen the preview
to obtain new access. Existing authenticated `/preview/session/...` routes
remain supported for non-iframe clients.

Access issuance and file responses use `Cache-Control: no-store` and
`Referrer-Policy: no-referrer`; event streams use `Cache-Control: no-cache`.
Sandboxed module assets permit CORS `Origin: null` only on a valid read grant;
this exception does not apply to application endpoints. The iframe retains
its opaque origin, CSP blocks network connections and form submissions,
and application cookies remain HttpOnly/SameSite=Strict with port-specific
names. No JSON-RPC method or database schema changes in this addition.

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
  "title": "Worktree 任务",
  "workspace_mode": "worktree",
  "workspace_path": "C:\\Users\\me\\AppData\\Roaming\\MiniMaxCode\\worktrees\\ses_1234",
  "worktree_branch": null,
  "base_branch": "HEAD"
}
```

### 6.0.1 `workspace.create_worktree_session`

Creates a Git worktree under the MiniMax Code data directory and persists a
session bound to that worktree.

When `project_id` is supplied, its bound directory selects the repository.
An unavailable binding fails with `-32602`; it never falls back to the server
directory. Unbound inbox sessions retain the configured default repository.

Request:

```json
{"method":"workspace.create_worktree_session","params":{"title":"Worktree 任务","base_ref":"HEAD"}}
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
session back to `workspace_mode: "local"`. The target must be a direct child
of the managed root with no symlink redirection. Git refusal (including dirty,
untracked or locked worktrees) preserves files and session metadata; there is
no force-delete fallback.

Git and Patch diff `ref` values must be nonempty revisions and cannot begin
with `-`; command options such as `--output=...` are rejected with `-32602`.
Diff previews disable external diff/text conversion commands. Git operations
share bounded asynchronous process execution; truncated output is an error.

### 6.1 `agent.send_message` — the streaming example

Optional `mode` is `chat` (legacy default), `execute`, or `plan`. In `plan`,
the execution policy blocks file mutations, arbitrary subprocesses and tools
outside the built-in read-only allow-list, including MCP tools and subagent
spawning. This policy is inherited by async child tasks. The run stores its
mode and `agent.continue_run` preserves it. A missing explicitly bound project
or worktree directory rejects the request; it must never fall back to the
service process directory. Planning is a tool/process policy, not an OS sandbox.

Native chat/continue execution is owned by RunService, independently of the HTTP
or stdio request waiter. Same-session requests execute FIFO, including setup and
final persistence; different sessions can execute concurrently. Cancellation
stops active and queued work for that session and holds the execution lease until
cleanup finishes. A confirmed cancellation returns `cancelled: true`, an empty
text fallback and the message ID; a run ID exists only if its record was created.
Unavailable iteration/usage counts are omitted. If completion committed before
cancellation, the successful result remains authoritative. Failure to create the
run history returns storage error -32004 before model or tool execution.
Native admission and shared budget liability accounting are durable. Complete
cross-engine/child-task recovery and persisted approval enforcement remain pending.

Native requests capture the client, model, reasoning effort and context limit at
admission, before waiting for the session lease. Child tasks inherit that model
runtime; a later global selection cannot change it. Run metadata includes
`model_snapshot: {engine: "native", provider_id: string | null, model: string,
protocol: string | null, reasoning_effort: string | null, context_window: number}`
from creation through completion. It contains no credential or endpoint URL; old
runs may omit it. Missing/disabled providers and empty endpoint configuration reject execution with
-32003 instead of silently changing providers. Permission rules are not yet frozen.
The accepted workspace path and project are also
saved; execution validates the captured directory and never follows later rebinding.


A caller may supply `idempotency_key` (1–128 non-blank characters) with an explicit
`session_id` on `agent.send_message`; it is required on `run.start`. A duplicate key
with identical input joins the existing task or returns its stored result. Reusing
that key for different input is invalid; changing global settings does not cause
re-execution. Keys are scoped to a session and retained with the run. Model engine
selection on `session.create` accepts `native` (default) or `codex` and is immutable.
The `engine` parameter of `run.start` is optional and must match the session's pinned engine when supplied.
`agent.send_message` rejects Codex sessions. Empty-session reuse requires the same engine.
Session responses include `engine`, nullable `engine_thread_id`, and versioned `engine_state`;
generic `session.update` cannot write these fields. Codex supports text input, persistent
same-thread turns and reviewed recovery; steering remains disabled. Native model/permission
controls do not configure Codex, whose actual returned model/provider/effort are saved and
pinned on resume. The original reply endpoint
still waits for completion; transport loss never cancels an admitted request.

Codex receipts may include `run_status: "failed" | "interrupted"` and an `error`
object. Only a confirmed successful Codex terminal status is stored as completed;
unknown terminal states fail closed. `tokens_in` and `tokens_out` are nullable:
missing usage is unknown, not zero. Cumulative Codex usage notifications are charged
by their increase against durable input/output/total watermarks in the same transaction
as the assistant message and run snapshot. `usage_certainty` is `observed`, `unknown`,
`recovery_interval` or `uncertain`; post-crash or unobserved intervals cannot be attributed
precisely to a current turn. Decreasing counters preserve the previous watermark and
stop bounded execution as uncertain. History and token summaries retain unknown as null.
Its budget interruption is reactive to engine notifications,
not a guaranteed pre-inference hard cap; unknown usage with an enabled budget
pauses the run. Shared family/day liabilities are persisted by Schema037; enforcement
remains reactive for Codex and estimated for Native, with no billing hard-cap claim.
The admitted mode is preserved: plan requests read-only execution with no approval
escalation, while chat/execute request workspace-write and human approvals.
Workspace resolution errors reject admission; queued execution rechecks the directory.
These protocol policies do not by themselves certify OS sandbox enforcement on a host.

Admission atomically writes one linked user message, including queued cancellations.
Request-key replay precedes engine availability, workspace and budget checks; its fingerprint
includes engine, content, mode, explicit budget and recovery source. Each queued operation
re-reads the binding under the existing session lease. The first turn starts a thread;
subsequent turns resume that exact thread, validate workspace/settings/inactivity, and save
the binding before `turn/start`. Missing/mismatched bindings never create a replacement thread.
Ordered notifications persist assistant deltas before display. Cancellation, EOF and protocol
failure drain already received events, retaining partial text, usage and IDs with a nonsuccess
state. Local message edits/deletes are rejected for Codex because they would desynchronize
external context; create a new session with a handoff instead. Ambiguous legacy Codex histories
also require explicit handoff rather than selecting a recent thread.

`host.fetch` stages data in the destination directory, validates its checksum, and
publishes with atomic replacement only if the destination still matches its initial
fingerprint. Transfer failure, cancellation, checksum mismatch or a changed target
leaves the destination intact. The local SSH/SCP process is reaped on timeout/cancel;
this does not guarantee remote command termination.

`host.push` accepts `{host_id, local_path, remote_path, transfer_id?}`. The remote
parent must exist and resolve without symbolic links; the destination must be a
regular file or absent. Remote Python 3.11+ runs a standalone helper. SCP writes
only `.minimax-upload-<id>/payload` beside the destination. Uploads force SFTP
(`scp -s`) so remote path shell metacharacters remain literal. Unsupported clients
or unavailable SFTP subsystems fail closed without legacy SCP fallback. After sha256 validation,
the helper compares the destination's device/inode/size/timestamps and content to
the initial snapshot, copies and verifies `previous` when replacing a file, fsyncs
data/journal files, and atomically replaces the destination. POSIX directory fsyncs
order staging, journal and publication entries. Filesystem/hardware behavior still
determines power-loss durability; Windows unit tests do not verify POSIX fsync.
The destination's permission mode is preserved. Private staging/journals/backups
and lock files are retained for recovery; automatic retention/garbage collection
is not implemented.

A target-specific advisory lock excludes cooperating upload publishers and detects
changes made before the final fingerprint check. This is not an atomic compare-and-
swap against arbitrary noncooperating processes writing between that check and
rename, and is not an OS security sandbox against hostile local processes.

Push and status results contain `{ok, exit_code, stderr, remote_path, transfer_id,
publication_state, backup_path}`. `staged` means an existing pending transfer,
`aborted` means its uncommitted payload was removed, and `published` records confirmed
publication. `publishing` means the journal cannot confirm whether replacement
finished; `unknown` means the journal could not be read/validated. `not_published`
reports a failed payload transport. `backup_path` is supplied only for a verified
backup. Published history does not guarantee no later user edits.

For response loss/cancellation recovery, the caller must record a fresh lowercase
32-hex-character `transfer_id` and the host/target before requesting the push.
The ID is bound to the normalized target and expected content sha256. Reusing an
existing ID never starts another SCP or commit: an already published matching
transfer returns its receipt; pending/aborted transfers require status inspection
or a new ID. Different content/target reuse fails. Losing the commit response
causes one status query, never a blind write retry. If that query also fails,
`host.transfer_status` can later resolve the same journal; reconciliation checks
the renamed staged file's identity as well as its checksum. It never republishes.
Cancellation during payload transfer attempts to remove only the owned staged
payload. Cancellation after commit starts retains the journal/backup for querying;
a lost prepare receipt may also leave its private staging for inspection. Invalid
status parameters are JSON-RPC `-32602`; unavailable/missing journals return a
structured unsuccessful transfer result. Mock mode covers all host methods but
returns explicit SSH-unavailable failures for probes, execution and transfers.

Run states additionally include `queued`, `waiting_input`, `budget_paused` and
`interrupted`. Startup marks unfinished requests interrupted; a queued request
has not started, while a previously running request needs review. No request or
tool is automatically replayed. `run.resume` accepts `{run_id, idempotency_key,
reviewed?: boolean, budget_tokens?: positive integer}`; started requests require explicit
`reviewed: true` (including Codex budget pauses, whose external side effects need review).
Native clean budget boundaries retain their existing exemption. It creates
a new request with a recovery handoff and `resumed_from_id`; the original record
stays intact. The original provider/model/effort and workspace binding must still
match. Codex uses its saved thread/workspace/protocol rather than a Native model snapshot;
its recovery prompt asks to inspect prior side effects before continuing. An unresolved started
Codex run blocks ordinary new sends. Source recovery marking, source event and child admission
commit atomically; reviewed failed recovery children can themselves be recovered on the same
thread. Legacy runs without a saved request/context need a new task with a handoff.

The request, terminal result and journal survive restart. Private request bodies
and full results are excluded from timeline events and list records; `run.get`
returns the result separately. State and result/event writes commit atomically.
Run cancellation applies only to a service-owned task; it does not infer ownership
from a database row. `agent.cancel` still requests cancellation of the whole session.

Native history/evidence persistence errors stop the run with code `-32004` and
`data: {run_id, requires_review: true}` when the error receipt can be saved.
If saving termination also fails, no confirmed result is returned; inspect the
persisted state and server diagnostics before recovery. Ordinary notification
delivery failures do not change a durable result. Parallel tool siblings are
cancelled and drained before a fatal evidence error releases the session lease.

Native persisted messages include optional `metadata.run_id`. Assistant streaming
IDs are also the stored `message.list` IDs; the run's `assistant_message_id` and the final
receipt identify the last saved assistant segment, including after tool turns or
automatic continuation. The run's `user_message_id` identifies the first saved user
message for that run. Message insertion, pointer update and its journal event
commit together. Legacy messages can lack this linkage; an unpersisted partial
stream after cancellation is not a confirmed saved assistant message.

Native chat notifications (message chunks, tool calls/results, status and questions)
also include a top-level `run_id`; legacy emitters may omit it. Partial text chunks
include this identity in their metadata before final usage is known. After terminal
run recovery, the browser reads committed messages and ignores late packets for
those recovered runs. A new session, input or newer run invalidates an in-flight
history read. Browser-only provisional messages use a local `request_key` matching
the submitted idempotency key so confirmed rows replace their provisional copies;
unconfirmed local input and interrupted partial output remain visible. This key is
not a new persisted message field. Failed history reads preserve the current view.

When reviewed recovery encounters a recorded tool call without a saved result,
the model-history projection provides an explicitly **unknown outcome** placeholder
for protocol pairing. It does not execute the tool or save an invented result.
Malformed or unmatched tool evidence stops continuation for review. The original
conversation and timeline remain unchanged.

Continuation of a truncated block is different from reviewed recovery: clients
pass the source `run_id` on every retry. The server derives a stable per-source
idempotency key, includes the source in the request fingerprint, and links the new
run through `resumed_from_id`. Source marking, source event and child admission
commit in one transaction. Saved model/workspace snapshots must still match before
a **new** continuation starts; replaying an existing receipt does not require the
provider to be available. A failed/cancelled/interrupted continuation is not started
again by retrying its source; inspect its result and use reviewed recovery as needed.
Legacy runs without context snapshots retain their prior context-selection behavior.

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
Since v1.3.0 all `patch.*` methods accept an optional `project_id`
(see the table in section 6) anchoring the repo at that project's root.
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
| `runtime.service_identity` | `{}` | `{service_id}` | 当前数据存储身份；用于界面连接作用域确认。 |
| `runtime.research_get` | `{}` | `{saved_url, effective_url, source}` | 搜索地址及来源；环境变量值不回显。 |
| `runtime.workspace_check` | `{session_id,project_id}` | `{session_id,project_id,service_id,status,source,path,reason}` | 检查本机会话目录解析；不测试模型、写权限或远程执行。 |
| `runtime.research_set` | `{search_url: string|null}` | `{saved_url, effective_url, source}` | 原子保存公开 HTTPS /search 地址；null 清除并回退环境变量。拒绝 URL 凭据、查询及片段；不进行联网测试。 |

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
| `mcp.add_server` | `{id, name, transport, command?, url?, env?, enabled?, bearer_token?, headers?, oauth_client_id?, oauth_client_secret?, oauth_scopes?, oauth_callback_port?, tool_states?}` | `{server}` | Validates `transport` is `stdio`, `sse` or `streamable_http`, persists the configuration, and immediately connects the server. `command` (argv list) is required for `stdio`; `url` is required to connect `sse` or `streamable_http`; the two HTTP protocols are explicit and never auto-converted. |
| `mcp.update_server` | `{server_id, name?, transport?, command?, url?, env?, enabled?, bearer_token?, headers?, oauth_client_id?, oauth_client_secret?, oauth_scopes?, oauth_callback_port?, tool_states?}` | `{server}` | Updates a persisted server config and re-attaches it when runtime fields change. `tool_states` is a map `{tool_name: enabled}` used to disable individual bridged tools. |
| `mcp.remove_server` | `{server_id}` | `{ok, server_id}` | Disconnects and deletes the persisted server. |
| `mcp.list_tools` | `{server_name}` | `{server_name, tools: [{name, description?, inputSchema?}]}` | Lists tools exposed by a single connected server. |
| `mcp.invoke_tool` | `{server_name, tool_name, arguments?}` | `{ok, server_name, tool_name, text?, content?, isError, structuredContent?}` | Calls an enabled tool registered on the current live server connection. Disabled, missing, replaced or collision-skipped tools are rejected. Retains structured content (any JSON value, including null) when supplied; tool errors retain `isError`. |

Both direct invocation and Agent bridge dispatch check the live registered tool
and connection identity at the call boundary. Planning and restricted execution
policies also apply when such a scope exists. Revocation blocks new calls; a call
already sent to an external server may have effects that cannot be recalled.
An MCP server is external code, not an operating-system sandbox.

### `codebase.*` — codebase indexing and retrieval (v0.11.0 Milestone 2)

| Method | Params | Result | Notes |
|--------|--------|--------|-------|
| `codebase.status` | `{project_id?}` | `{status, processed, total, percent, message, error, stats: {total_chunks, total_files, latest_updated_at}}` | Returns the current lifecycle status of the indexer plus aggregate stats. With `project_id` (v1.3.0), reports the index keyed at that project's root. |
| `codebase.build_index` | `{force?, project_id?}` | `{status, processed, total, percent, message, error, stats}` | Starts a full index build in the background. If already indexing, returns the current progress. `force=true` clears the existing index first. Indexes are keyed per project root — building project A never touches project B's chunks. |
| `codebase.search` | `{query, file_pattern?, limit?, offset?, project_id?}` | `{query, file_pattern, total, results: [{chunk_id, file_path, start_line, end_line, snippet, language, rank, symbols}]}` | Keyword search over indexed file contents and paths. `file_pattern` is a SQL `LIKE` pattern. With `project_id` (v1.3.0), searches only that project's index. |
| `codebase.summarize` | `{path, project_id?}` | `{path, kind, language, total_lines, symbols, snippet, file_count}` | Returns a structured summary for a file or directory prefix. `path` resolves against the project root when `project_id` is given. |

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
| `data.backup` | `{target_dir?, require_existing_dir?}` | `{path, bytes}` | Online file-level snapshot via the SQLite backup API; default target is `<data_dir>/backups/`. |

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
  On Schema048, imports containing `webhooks` or `webhook_deliveries`
  are rejected before mutation, including older configuration exports.
  Other protected execution and accounting tables are also rejected;
  a complete JSON export is therefore not a supported full restore.
* **Column whitelist** — row keys are intersected with the target
  table's `PRAGMA table_info` columns (drifted columns dropped, absent
  columns fall back to SQL defaults); values are always bound
  parameters. Envelope tables unknown to this install are reported in
  `skipped_tables`, not fatal.
* **All-or-nothing** — the whole import runs inside one `BEGIN IMMEDIATE`
  transaction. The shared connection lock covers the temporary foreign-key
  toggle, transaction and restoration of its prior setting. Imports are
  rejected if the existing database has foreign-key violations or if the
  proposed import would create them. A failure before commit rolls back;
  cancellation after commit was queued may still leave the complete import
  committed, so inspect the result before retrying. FTS indexes stay in sync
  via the existing triggers on `memories`; vec indexes require re-indexing.

**`data.backup` semantics (R23):**

* File-level **complete snapshot** via the SQLite online backup API —
  schema, WAL contents, FTS indexes and vec shadows included. Backups share
  the connection lock with imports and other managed database operations;
  cancellation waits for the SQLite worker before closing the backup target.
  Complements the JSON export (which is
  portable across schema versions but excludes derived state).
* `target_dir` is created if missing; omitted → `<data_dir>/backups/`.
  When `require_existing_dir` is true, the target must instead be an existing
  absolute directory on the service host and is not created by this operation.
  The filename embeds a UTC timestamp and an exclusively created random suffix
  (`minimax-code-backup-YYYYMMDD-HHMMSS-<unique>.db`), so backups created
  in the same clock tick cannot overwrite an earlier snapshot.
* The source database is only ever read, never written.

## 7. Event names

All push events use the prefix `agent.`, `task.`, or `permission.`
so the frontend can route them by name without a regex.

| Event name              | Data                                                |
|-------------------------|-----------------------------------------------------|
| `agent.message_chunk`   | `{session_id, message_id, delta, done}`             |
| `agent.tool_call`       | `{session_id, tool_call_id, name, args, message_id}` (Phase 1.2) |
| `agent.tool_result`     | `{session_id, tool_call_id, name?, result, error?, message_id}` |
| `agent.ask_user`        | `{request_id, session_id, questions[], timeout_s}` — the model paused with structured clarification questions (v1.1.1); the UI renders an inline question card and answers via `agent.answer_user` |
| `agent.ask_user_closed` | `{request_id, session_id, run_id}` — a Native question wait ended, timed out or was cancelled; late snapshots must not revive it. |
| `permission.closed` | `{request_id, session_id, run_id}` — a Native or Codex consent wait ended, timed out or was cancelled; late snapshots must not revive it. |
| `agent.status`          | `{session_id, status, detail?}`                     |
| `task.progress`         | `{task_id, progress, message?}`                     |
| `schedule.updated` | `{job_id, occurrence_id}`；发生执行及状态记账结束后提示客户端重新读取，不代表执行成功。 |
| `permission.request`    | `{request_id, tool, args, session_id?}` — modal triggers; `session_id` (v1.2.0) tags which conversation the gated call belongs to |
| `permission.resolved`   | `{request_id, decision}`                            |
| `run.created`           | `{run}`                                              |
| `run.updated` | `{run}`; queued request claimed for execution. |
| `run.output_cleared` | `{run_id,session_id,output_ref,service_id,cleared_at}`; captured output was cleared, not a task deletion. |
| `run.step.started`      | `{run_id, step}`                                    |
| `run.step.completed`    | `{run_id, step}`                                    |
| `run.completed`         | `{run}`                                              |

## 8. Lifecycle events

### 8.1 WebSocket lifecycle (HTTP mode)

On `GET /ws` connect, the server pushes one envelope:

```json
{"jsonrpc":"2.0","method":"agent.ready","params":{"server":"minimax-code-agent","version":"0.2.0","next_seq":48}}
```

`next_seq` (v1.2.2) is the seq the next broadcast will carry — the
epoch anchor clients compare their resume cursor against (see
"Event replay on reconnect" below).

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
  replayed. v1.2.2: the client detects the epoch reset via the
  `next_seq` anchor on the ready frame — when `next_seq <= its high
  watermark`, the cursor points into the previous process's numbering,
  so the client drops it (resets to 0) and reconnects once without
  `?since=` to get a full ring replay. Live events on the current
  connection are unaffected (fan-out ignores `seq`).

### 8.2 Health probe (HTTP mode)

`GET /health` returns `{"ok": true, "version": "<agent version>",
"uptime_s": <int>}` as long as the agent is alive. Web client uses
this for the "is the agent running?" badge. A failed probe leaves the client
offline; the in-process mock is used only when explicitly configured.

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

## Appendix A. 方法总表（与代码自动对账）

> 本表由 `agent/tests/test_ipc_contract_doc.py` 双向守护：
> 新增 handler 未列入此表、或表中/正文提及的方法在代码中不存在，测试即红。
> 详细的请求/响应形状见上文各命名空间章节；此处保证**每个注册方法都有文档锚点**。

### agent.* — 子 Agent 定义与调用

| 方法 | 说明 |
|------|------|
| `agent.create` | 创建子 Agent 定义 |
| `agent.get` / `agent.update` / `agent.delete` | 子 Agent 定义读取/更新/删除 |
| `agent.invoke` | 直接调用一个子 Agent |
| `agent.cancel_subagent` | 取消正在运行的子 Agent |

### attachment.* — 项目附件原件（Schema043）

| 方法 | 参数 | 返回 |
|------|------|------|
| `attachment.generate_report_pair` | `{project_id, expected_service_id, filename, title, paragraphs?, sections?, formats?}` | `{status, results}`，共享内容逐格式保存并返回各自状态 |
| `attachment.generate_pdf` | `{project_id, expected_service_id, filename, title, paragraphs?, sections?}` | `{attachment, preview_text}`，生成并保存 PDF 文本报告，业务及视觉未验证 |
| `attachment.generate_docx` | `{project_id, expected_service_id, filename, title, paragraphs}` | `{attachment, preview_text}`，生成并保存有界纯文本 DOCX；不验证版式或事实 |
| `attachment.extract_xlsx` | `{project_id, session_id, expected_service_id, attachment_id, expected_sha256, sheet_name}` | 复核会话、服务与附件后返回受控完整工作表行及日期、百分比、货币列；不计算公式 |
| `attachment.list` | `{project_id}` | `{attachments, service_id}`，最近 200 条元数据，不含预览 |
| `attachment.get` | `{project_id, attachment_id}` | `{attachment, preview_text}`，核对原件后返回有界纯文本 |

严格拒绝额外字段；project_id 为非空、最长 128 字符，attachment_id 为 `att_` 加 32 位小写十六进制。
项目须存在，不要求 root_path。服务身份复用 artifact_service，项目删除后历史行/字节保留，但 API 不再可访问。
attachment 字段：id、project_id、service_id、snapshot_id、original_name（1–240 个无控制显示字符）、
declared_type、recognized_type、size_bytes（0–10485760）、sha256、version、parser_status、parser_version:1|2、parser_scope、parser_reason、
preview_truncated:boolean、quality_status:"unverified"、created_at。列表/上传响应均不包含 preview_text。

上传为认证 `POST /attachments?project_id=...&filename=...` 的 raw body，非 multipart。
必须携带当前 list 获得的 `X-Attachment-Service-Id`，与 HTTP bearer/cookie 一起只放 header；
服务在读 body 前及提交事务内复核身份和项目，拒绝同 URL 下旧服务身份。Host/Origin/认证边界与 RPC 共用；
CORS 允许该请求 header，暴露下载的 `X-Attachment-Service-Id`、`X-Attachment-SHA256`。
成功 HTTP201 `{attachment}`；参数错误400、身份/项目/存储一致性409、超限413、接收超时408、存储不可用503。
不依赖 Content-Length：包括缺失/chunked/谎报长度，累计流最多 10 MiB，接收期限 120 秒。
中断/超限没有可用记录，私有 pending 仅清理精确自有文件；取消须等待实际文件线程结束再关闭。
完成文件先 fsync、无覆盖原子发布，再提交 DB；二者之间失败可能保留完整未登记 orphan，不能视为上传成功。
filename 仅显示和下载名称，从不作为主机路径；版本按项目+同名递增，原件互不覆盖。

TXT/CSV 严格 UTF-8、拒绝 NUL；CSV 逐行检查语法、不建立工作簿或执行公式，单字段由标准库限制为 131072 字符。
完整输入检查后最多显示 64 KiB/2000 行，保留 BOM/CRLF，UTF-8 边界截断。
parser_status 为 `parsed`、`empty`、`truncated`、`parse_failed`、`type_mismatch`、`parser_unavailable`、`ocr_not_attempted`。
乱码/NUL/CSV 错误保留原件为 parse_failed；扩展名和内容候选异常为 type_mismatch，不选错解析器。
图片标记 ocr_not_attempted。XLSX 可选 extra 只对 declared=recognized=xlsx 解析，沿用 v2/inert_text，
每表 A1:T100、全局1500展示单元格，保留坐标/类型/原存储值，公式和未验证缓存分开标注，不执行或重算。
缺失缓存、空数字缓存、存储缓存明确区分；raw存储值权威，不推断日期或保证缓存正确。
全部实际单元格空时为 empty/ok（空预览、未截断），仅此精确XLSX分支适用；
展示上限为 truncated/preview_limit，截断后仍完整有界预检。External关系包括超链接一律拒绝。
32表/100000实际cells/50000共享字符串/4096样式与既有10MiB输入和worker预算不增大；
无新单元格结构字段或IPC方法。详见[XLSX有界预览](attachment-xlsx.md)。
PDF 可选 extra 按物理页序提取已有文字，最多200页，不渲染或OCR；使用既有算法v2/inert_text，
范围定义为有界惰性纯文本预览，结合declared_type/recognized_type=pdf和页标签表达来源。
全部无文字时empty/no_extracted_body_text_no_ocr；仅展示上限truncated/preview_limit；
加密unsupported_feature、损坏invalid_package、资源/页数超限package_limit，均parse_failed且清空预览。
页标签与空页提示计入64 KiB/2000行；截断后仍检查后页。详见[PDF有界解析](attachment-pdf.md)。
DOCX 可选 extra 仅解析正文段落/表格，详见 [DOCX 有界解析](attachment-docx.md)。算法 v2 新增稳定 parser_reason 与 parser_scope，旧 v1 两字段为 legacy_v1，不自动重解析。
empty + no_extracted_body_text_no_ocr 仅说明未提取到正文/页面文本，不代表整个文件没有内容。
recognized_type 只是魔数/容器候选，不证明格式有效或内容已读完。Office 候选识别只检查有界 ZIP 目录；DOCX/XLSX 子进程另行校验实际解压计数和全部 XML/rels，拒绝宏/嵌入对象，不执行公式或访问外链。
quality_status 始终 unverified，保存/解析/质量/交付分别处理；不自动注入上下文，不替换输入框附件机制。
mock 模式只在页内存保留原件，Office 为 generic ZIP 候选且不可解析；PDF也不解析，不承诺持久化。

认证 `GET /attachments/{attachment_id}/content?project_id=...` 返回原字节；只按受约束 ID 读 attachment-vault，
核对大小/hash/身份，缺失/损坏拒绝，没有 source fallback。返回 application/octet-stream、
attachment disposition（固定 ASCII fallback + 过滤后的 RFC5987 名称）、no-store、nosniff、sandbox CSP、
上述两个身份 header。前端再次检查服务/项目 epoch、大小和实际 SHA-256 后才发布 Blob 下载。
原件和解析元数据从Schema043/bundle v3起纳入离线包；Schema045/bundle v5扩展MCP传输检查，Schema046/bundle v6新增办公成果关联表；Schema047/bundle v7保存各主机远端服务端口；Schema048/bundle v8保存Webhook投递记录；Schema049/bundle v9记录检查点来源，bundle v10 保存检查点未跟踪文件副本，当前 v11 增加 Git 对象包，均不改变附件字段。PDF/XLSX沿用v2/inert_text但使用各自精确类型校验。
旧v1/041至v7/047仍只读校验/恢复，不自动迁移或重解析，详见data-vault-bundle.md。

DOCX、PDF 与双格式报告参数均可选 `sources:[{title,url,accessed_at?,excerpt?,excerpt_truncated?,title_truncated?}]`，最多 30 条；标题最多 200 字，URL 最多 2048 字且须为不含凭据的 HTTP(S)，访问时间为 ISO 日期或带时区时间，摘录最多 1000 字。生成时按顺序在文末写入编号标题、网址、可选访问时间、摘录和截断提示；正文中的引用编号由调用方提供，不自动核对引用关系。来源只保存提供的内容，不联网抓取、不自动核验或证明其真实性。

手工 `attachment.generate_docx`、`attachment.generate_pdf` 和 `attachment.generate_report_pair` 可选传 `session_id` 与 `page_source_ids`，服务端在生成时按会话、项目和 `expected_service_id` 解析真实来源；手工 `sources` 在前，ID 来源按顺序追加，合计最多 30 条。来源记录不存在、项目或服务不匹配时拒绝；会话绑定在附件登记时再次核对。

普通 Native 聊天的 `read_public_page` 成功后，将实际获取的页面信息写入当前 run 的持久 observation，返回 `page_source_id`；无项目绑定时只返回读取结果、不登记来源。后续同项目任务可调用 `list_research_sources({query?,limit?})` 查询最近最多 20 条，并将 ID 交给 `generate_report_docx`、`generate_report_pdf` 或 `generate_report_pair`。记录保留最终 URL、访问时间、最多 500 字标题、最多 8000 字摘录和相应截断标志；报告中标题最多 200 字、摘录最多 1000 字并标注截断。项目、当前会话及 service_id 均须匹配，未知、重复或跨项目来源 ID 失败；手工 `sources` 在前，ID 来源按请求顺序在后，合计最多 30 条。来源存入已有 run 日志，不新增数据库表；随 run/会话删除而删除，现有 SQLite 数据包会携带这些记录。service_id 是现有服务身份，不是硬件身份；数据包克隆保留该身份和来源，不能证明新主机重新访问了网页。搜索摘要不产生来源 ID，网页与报告事实、正文编号均不自动核验。

`run.research_list({session_id,project_id,query?,offset?})` 返回 `{session_id,project_id,service_id,sources}`，按时间倒序每页最多 20 条，offset 不超过 10000；列表摘录最多 1000 字。`run.research_get({session_id,project_id,expected_service_id,page_source_id})` 返回相同绑定及 `source`，包含最多 8000 字的保存摘录及原始截断标志。两者均校验会话项目、服务及宿主限制，并在读取后复查绑定；详情中旧服务身份或已删除来源被拒绝。

`run.child_workspace({run_id,session_id,action:"preview"})` 仅审阅有持久父运行、会话、项目和工作区绑定的直接子工作区；受限进程、旧无绑定记录及嵌套工作区不通过该入口处理。返回当前父目录与子结果的文件和有限文本差异，以及 `revision` 和 `service_id`；已收集状态附带服务端保存的 `receipt`（merged、noop 和 conflicts），不表示验证通过；大于 500 个变化拒绝预览。`action:"collect"` 必须附带该版本的 `revision` 与 `expected_service_id`，重新检查文件、绑定和冲突。删除及父目录同期修改不会被自动覆盖；复制期间如发生错误可能已有部分文件写入，应重新核对。独立目录不是操作系统沙盒。

已结束团队的成员工作区可附带 `team_run_id` 使用同一审阅/收集入口：服务端核对成员 ID 属于该团队的持久结果，以及会话、项目、服务和固定父目录。旧团队记录缺少绑定、未完成或中断时无已保存成员结果的，不提供自动收集；需手动核对。

团队直接成员开始及结束写入 `run.steps` 的 `team_member_v1` 状态步骤；结束记录包括最多 8000 字文本、截断标记、非验收的执行结果和可用工作区引用。步骤持久化失败不会冒充成功记录；旧团队不保证存在这些步骤。重启或取消时未完成成员结果未知，不自动重放。中断团队的已保存成员可预览；只有唯一持久完成步骤、已完成工作区、受管任务收束记录及无活动服务内 owner 同时成立时，才允许携带 `confirmed_team_stopped:true`、`expected_team_step_id`、审阅版本和服务身份明确收集。旧中断缺少收束记录时拒绝；受管任务收束不证明外逸进程不存在，用户需自行核对进程。

`run.verify_collected` 仅在用户明确确认后，为已收集团队成员创建独立验证运行；请求携带会话、团队和成员 ID、服务身份、已收集审阅版本、审阅时的项目命令及幂等键。接单和出队时重核绑定、命令和权限拒绝规则；命令通过现有有界执行器在项目目录运行。`run.cancel` 可请求停止，结果保存在验证运行的 `result.verification`；超时、中断或无法获得退出码为未知。验证不代表验收，普通 `run.resume` 不重放该运行。

`run.child_list({session_id})` 返回同一父会话及其持久父运行、当前项目绑定的最近 100 条直接子运行，包含 `service_id` 和真实持久 `status`。`live` 仅表示服务内可确认的后台任务，缺失不能证明同步子任务已停止；活动状态但 `live=false` 时客户端应显示待核对，不应推断仍在运行。历史缺少父运行绑定的记录不包含在内。

`run.output_page({run_id,session_id,output_ref,expected_service_id,offset?,limit?})` 按字符分页读取该运行的捕获输出 JSON；最多 8000 字符，返回当前/下一偏移、总字符数和绑定标识。输出仅包含工具捕获到的内容，命令捕获上限和截断标记仍然适用；引用不跨项目、会话或服务使用。

`run.output_inventory({session_id,offset?})` 返回全库与当前会话逻辑用量、当前会话按页 50 条已绑定的输出记录及服务身份；全库统计包括其他会话和不可访问的旧记录，但不暴露其详情。`run.output_clear({run_id,session_id,output_ref,expected_service_id,expected_sha256})` 仅清理明确指定、已终结且不再清理中的运行的正文，保留不可读取的清理标记；调用不会删除运行、摘要或原文件，重试同一记录幂等。清理与新读请求按数据库事务排序，已取出的并发页可能仍被返回；通知供客户端清除已显示或在途页面。数据库文件、旧备份与存储介质可能保留原始字节，并非安全擦除。

`attachment.extract_xlsx` 仅在会话归属项目、服务和附件校验值匹配时读取不可变原件，使用现有隔离解析器完整提取一个可见工作表；上限 2 MiB、A1:T1001、5000 格及 1 MiB 提取数据。公式、隐藏行列、复杂表和不明确数字格式等拒绝；日期转 ISO，百分比和货币为底层数值，不保留格式。返回前再次核对会话和服务；不会修改原件。

`attachment.generate_docx` 接受安全 `.docx` 显示名和单行标题（最多 200 字），内容使用两种互斥格式之一：`paragraphs` 为 1–100 个段落；或 `sections` 为 1–50 个 `{heading, blocks}`，每个 block 为 `{kind:"paragraph",text}` 或 `{kind:"table",rows:[[文本,...],...]}`，顺序保留。合计最多 100 块、2000 个单元格，每表最多 100 行、20 列且各行等宽；每段/格最多 4096 字，总 UTF-8 最多 256 KiB。章节标题也限单行 200 字。使用本地 python-docx 创建，回读并比较全部章节、段落、表格单元格及顺序，再经现有 DOCX 有界解析与不可变附件 vault 登记。不会读取或修改项目文件、调用模型或抓取资料；用户提供的来源文本需自行核实。不保证版式、分页、事实或业务质量。响应丢失时先刷新附件列表；重试会创建新版本。文件与现有附件共享备份协议及未验证状态，不作为已完成报告的质量证明。 普通聊天 Agent 可通过 `generate_report_docx` 生成当前会话项目的文档；提交时复核会话项目绑定，遵循规划模式与写入权限规则。该工具默认要求确认，拒绝规则优先，无法确认时拒绝。

`attachment.generate_report_pair` 的 `filename` 为不带扩展名的公共文件名；`formats` 默认 `["docx","pdf"]`，可只传一种用于重试。每种格式独立保存并返回 `saved` 或 `unconfirmed`；`partial` 不表示全部完成，未确认可能发生于提交后的响应丢失，应先刷新核对附件再仅重试缺失格式。无幂等重放。普通聊天 `generate_report_pair` 使用相同内容并严格合并现有写文件及所请求格式权限，默认询问、规划模式拒绝；会话项目绑定变化停止后续格式。保存只证明可解析，不证明事实或视觉质量。

`attachment.generate_pdf` 与 DOCX 使用相同互斥的段落或章节结构；PDF 表格每表最多 10 列、1000 格。使用 attachments extra 中锁定的 ReportLab 本地生成并嵌入中文 TrueType 字体；Windows 自动尝试系统黑体/等线，Linux 尝试常见文泉驿、文鼎或 Noto 字体，找不到或不覆盖文字时拒绝并提示配置 `MINIMAX_CODE_PDF_FONT` 绝对字体文件路径。字体需为 ReportLab 支持的 TrueType 轮廓，超出字体覆盖的字符拒绝；生成失败时缩短过高的表格行。通过有界 PDF 文本解析后保存，预览可能截断，不等同完整视觉检查。`generate_report_pdf` 仅普通聊天可调用，沿用当前会话项目绑定及 DOCX 的写入权限和规划模式约束；不调用模型生成或访问外网，响应不明时先检查附件列表。

### office.* — 生成纯数据 XLSX（Schema046）

| 方法 | 参数 | 返回 |
|------|------|------|
| `office.generate_xlsx` | `{project_id, expected_service_id, request_key, filename, spec, run_id?}` | `{delivery, attachment, preview_text}` |
| `office.generate_analysis` | 上述字段加 `summaries` 和可选 `chart` | 无图表返回 `{kind:"delivery",delivery,attachment,preview_text,materialized_spec}`；图表返回 `{kind:"attachment",attachment,validation_report,analysis_spec}` |
| `office.list` | `{project_id}` | `{deliveries:[{delivery,attachment}], service_id}`，最近 200 条 |
| `office.get` | `{project_id, delivery_id}` | `{delivery, attachment, preview_text}`，重新检查 vault 原件大小/hash |

汇总沿用静态值生成和纯表格幂等登记。图表只引用原始数据行，以独立项目附件登记；该路径不支持 `run_id`，不保证幂等，响应不明时应先检查项目附件。图表回执核对保存单元格与图表系列引用，不代表视觉或业务验收。

全部参数严格拒绝额外字段。HTTP 入口共用认证、Host/Origin 检查；stdio RPC 为已有受信本地入口。
普通聊天 Agent 可调用 `generate_table_xlsx`，项目由服务端绑定当前会话并在重放与提交时复核；模型不能选择项目。该工具遵循 write_file 与自身权限中较严格的规则，默认确认，规划模式或无法确认时拒绝。请求键在同一会话内与输入绑定，响应丢失时可使用相同键及输入重试，不同输入冲突；新意图使用新键。不发起 run admission/resume 或业务完成状态。
生成要求允许写入的执行上下文、当前持久服务身份及存在的显式绝对项目根。
`expected_service_id` 为 `svc_` 加 32 位小写十六进制，`delivery_id` 为 `off_` 加同样后缀；
project/run ID 最长 128 字符，`request_key` 为 1–128 个 ASCII 字母数字、下划线或连字符。
filename 仅为安全 `.xlsx` 显示名，长度 6–120，不作为路径；拒绝目录、控制字符及 Windows 设备名。

`spec={sheet_name,columns:[{name,kind}],rows:[[value,...],...]}`；kind 只支持 `text|number|boolean`，
严格匹配值类型或 null；不接受公式、格式、命令、主机或文件路径。
1–20 列、0–1000 数据行，连表头最多 5000 格、规范 JSON 输入最多 1 MiB，生成文件最多 2 MiB。
表名 1–31 字符、列名 1–80（大小写折叠后唯一），每行宽度必须匹配；text 最长 4096 字符、
拒绝非法 XML 字符，空串规范化为 null。number 必须有限、绝对值不超过 999999999999999、
最多 15 个有效数字，非零绝对值至少 1e-12；拒绝负零，长编号应使用 text。
以 `=`/`+`/`-`/`@` 开头及 URL、错误标记均按文本保存，不写入公式/超链接。

固定受限子进程使用 openpyxl 3.1.5 新建文件，独立 ZIP/XML 校验比较全部已保存单元格与规范输入，
包括超出文本预览窗口的数据；禁止宏、外部关系、额外部件、公式或额外单元格。
沿用 512 MiB 内存、10 CPU 秒资源门，生成 wall 15 秒、排队 5 秒、服务总期限 40 秒；
stdout 3 MiB/stderr 16 KiB，取消及重复取消等待进程/文件线程回收，不把异常内容或凭据带出 worker。
只往私有 attachment-vault 独占原子发布；attachment 与 office 记录同事务提交，原项目文件不覆盖。
同项目+request_key+相同规范请求重放同一记录，不同内容返回冲突；新 key 才创建同名新版本。
提交边界丢失响应/取消后可刷新或以同 key 重试；发布后 DB 失败可能留下不可访问的完整私有 orphan，
不返回成功记录。无隐式孤儿重扫、补录或旧版数据库迁移。

可选 run 仅为来源引用：必须是同项目、同显式 workspace 的普通 native host 非 plan run，
会话历史包含 admission/internal 执行元数据、其他引擎/隔离配置、损坏或重复键元数据时拒绝；
生成前及事务提交时重复核对，绝不借用该 run 的工具权限或改变其状态。
检查完整会话历史最多 256 条，每条 metadata 最多 64 KiB，超限/嵌套过深/非严格 JSON 拒绝，不抽样放行。
请求指纹还绑定项目的 id/root_path/created_at；项目根变更后不能用旧 key 静默重放为新目标。
已经登记的历史来源被删除后仍可读取该不可变版本。

delivery 为 office_deliveries 的 12 列记录，其中 validation_report 在 RPC 解码为对象：
`version:1,scope:xlsx_plain_cells_v1,input_sha256,file_sha256,size_bytes,sheet_name,data_rows,column_count,cell_count,`
`formula_count:0,external_relationship_count:0,macro_count:0,structure_status:passed,business_quality:unverified,`
`render_status:not_attempted,writer:{name:openpyxl,version:3.1.5},validator_version:1`。
attachment 沿用原附件类型、版本、SHA-256 与 v2/inert_text 预览，不把普通上传转换为 office 记录。
下载复用认证 attachment content HTTP，前端再次比较实际字节大小/hash、项目/主机/服务 epoch。
报告不是数字签名；不保证渲染、重算、OCR、打印分页或业务验收。独立复验命令及 bundle6/46 边界见
[办公成果](office-delivery.md)；mock 明确不生成文件，不构造已验证记录。

### artifact.* — 显式登记文本成果（Schema041）

| 方法 | 参数 | 返回 |
|------|------|------|
| `artifact.register` | `{project_id, source_path, run_id?}` | `{artifact}`，成功登记一个新的不可变版本 |
| `artifact.list` | `{project_id, run_id?}` | `{artifacts, service_id}`，按项目及可选运行筛选后返回最近 200 项 |
| `artifact.get` | `{project_id, artifact_id}` | `{artifact, preview_text}`，经过 vault 大小/hash 核对的纯文本 |

参数均严格拒绝未知字段；host、绝对文件路径、内容及验证结论不能由调用者传入。
`project_id` 必须对应存在且设置明确绝对 root_path 的当前服务项目；`source_path` 使用
规范 POSIX 相对路径，拒绝绝对路径、`..`、反斜杠、ADS、symlink/reparse 与 hardlink，
只支持 TXT/MD/CSV、最大 2 MiB、严格 UTF-8（原有 BOM/CRLF 字节保留）。可选 run 必须
存在且其会话属于该项目，会话显式 workspace 必须等于项目 root，提交时再次核对
项目/run 绑定。源文件不修改；读句柄前后
fstat、路径身份和第二遍 hash 检查拒绝读取期间的替换/变更。POSIX 使用 no-follow
目录描述符，Windows 保留非 reparse 祖先句柄并限制写入/删除；不承诺完整 OS 沙盒。

每次成功登记（即使内容未变）都产生新的 project/source_path version 和 snapshot_id，
完成私有 vault 文件的独占原子发布后才写 DB。返回 artifact 包含 id、project_id、run_id、
服务生成并持久保存的 service_id、source_path、snapshot_id、mime_type、size_bytes、
SHA-256、version、created_at、parse_status=`parseable`、verification_status=`unverified`。
这些状态只代表已登记且可解析，不代表内容质量、任务验证通过或交付完成；run 关联
也不证明文件由该 run 生成。Markdown 在 UI 中转义为纯文本，CSV 仅解析、不计算公式。

原始字节下载为 `GET /artifacts/{artifact_id}/content?project_id=...`，复用主应用
Host/Origin 与 bearer/cookie 认证，不接受 URL token 或其它参数。响应始终为
application/octet-stream attachment，no-store、nosniff，并带 X-Artifact-Service-Id 和
X-Artifact-SHA256；服务先核对 vault 原始字节，不渲染 HTML，不回读 source 作为 fallback。
vault 缺失/损坏显式失败（HTTP 409 / RPC -32000），存储故障为 HTTP 503 / RPC -32603，
参数结构错误为 -32602。浏览器按 project、服务 endpoint 和 service_id 丢弃过期响应；
项目切换/面板关闭取消下载，认证只经现有 header/cookie transport。

项目删除保留历史 project/run ID、元数据和 vault 文件，无 filesystem cascade；正常
list/get/content 仍要求项目存在，因此删除后不从原项目 UI 访问。DB 失败可能留下尚无
元数据引用的完整快照，保留待显式恢复，不做无界自动删除。SQLite 备份或 JSON 导出
不能替代 vault 字节备份。本片不包括 Office/OCR、MCP、模型自动登记或质量验证。
Mock 模式仅模拟虚拟 `demo.txt`，不读取本机文件。

### audit.* — 审计

| 方法 | 说明 |
|------|------|
| `audit.list` | 分页查询审计条目 |
| `audit.stats` | 审计统计摘要 |
| `audit.purge` | 按条件清理审计历史 |

### budget.* — 共享预算账本

| 方法 | 说明 |
|------|------|
| `budget.get` | 读取 `{run_id?, day?}` 的预算家族、Asia/Shanghai 日账、家族 attempts、当日 day_blockers（总数及最多 20 项身份/owner run/session/已知下界）与覆盖起点 |
| `budget.get` 的 `day` | `accounting_resolved` 表示当日无待核对的未知责任，活动请求仍可存在；`usage_complete` 表示指定预留日期的全家族请求均已测得或确证未发送，且无未测得历史责任。两个布尔值与日汇总同事务计算，仅证明当前快照。旧响应缺字段保持未知，不能默认为 true。 |
| `budget.reconcile` | 核定历史/未知责任；要求明确确认、依据和不少于已知下界的累计责任 |
| `budget.settings_get` | 读取服务身份、当前生效的日 token 限额及 Asia/Shanghai 日账快照；设置文件无效时拒绝。 |
| `budget.settings_set` | `{expected_service_id,limit_tokens,expected_saved_limit?}`；正整数保存日限额，null 恢复环境变量回退；提供 expected_saved_limit（包括 null）时在同锁内比较当前保存值，变化则拒绝写入。与同一服务进程的预算预留串行化。设置保存在数据目录 daily-budget-settings.json，现有数据库导出/数据包不包含该文件；在途请求不撤回，Codex 活动 turn 在后续用量通知上读取新限额。 |

### checkpoint.* — 工作区快照

| 方法 | 说明 |
|------|------|
| `checkpoint.create` | 创建工作区快照 |
| `checkpoint.list` | 列出快照 |
| `checkpoint.diff` | 快照与当前工作区对比 |
| `checkpoint.restore` | 恢复到指定快照 |
| `checkpoint.rebind` | 经显式确认和只读验证后，将检查点绑定到当前会话工作区内的新仓库根 |
| `checkpoint.delete` | 删除快照 |

新检查点记录解析后的仓库根 `source_root`，恢复和差异操作须匹配该路径且保持在当前会话工作区内；路径匹配不能证明仓库未被替换。旧记录的 `source_root` 为 null，默认拒绝恢复和差异：确认原始工作目录后，调用 `checkpoint.restore` 或 `checkpoint.diff` 时显式提供 `cwd` 和 `legacy_source_confirmed: true`。该确认由用户承担，系统无法验证旧来源；旧 cwd 可以是会话范围内仓库的子目录。快照文件及 Git stash 对象不包含在数据库备份或 JSON 导出中；bundle v11 保存数据库所列未跟踪文件副本和 Git stash 可达对象包；Git 对象不会自动导入，须使用离线显式导入命令并核实来源。路径和共同基底不证明仓库身份。

迁移后可调用 `checkpoint.rebind`，参数为 `checkpoint_id`、绝对仓库根 `cwd`、`confirm_rebind: true` 和 `confirm_trusted_bundle: true`。用户须先将会话绑定到新工作区并导入可信 v11 包的 Git 对象。服务端校验会话工作区边界、仓库根和 Git 对象存储、pack 和完整闭包、对象格式、HEAD 与 stash 基底，并只更新仍匹配的检查点 `source_root`。规划只读、文件沙箱或受限 host 会话不得执行；Git worktree 的 common 目录位于会话工作区之外时拒绝。原来源未知的旧检查点不能安全重绑定。该操作不修改项目、会话、Git refs、index 或工作区；目录与提交匹配不能证明仓库身份，外部 Git/GC 并发不受锁保护。失败时不重新绑定。

### diag.* — 诊断

| 方法 | 说明 |
|------|------|
| `diag.export` | 导出脱敏诊断包：版本 / 平台 / 配置（仅枚举与计数，绝不含密钥值）/ 各表行数 / 已消毒日志尾；存储不可用时降级为 `db_available: false` 而非报错 |

### git.* — 只读 Git 集成

| 方法 | 说明 |
|------|------|
| `git.status` | 工作区状态（分支/暂存/未跟踪）；v1.3.0 起接受可选 `project_id`，锚定该项目的 `root_path` |
| `git.diff` | 工作区差异（详见正文 `git.*` 章节）；v1.3.0 起接受可选 `project_id` |
| `git.log` | 提交日志；v1.3.0 起接受可选 `project_id` |

### mobile.* — 移动配对

| 方法 | 说明 |
|------|------|
| `mobile.pair_start` | 发起配对，生成配对令牌 |
| `mobile.pair_confirm` | 确认配对，绑定设备 |
| `mobile.list` | 已配对设备列表 |
| `mobile.device_status` | 设备在线状态 |
| `mobile.push_notification` | 向设备推送通知 |
| `mobile.touch` | 设备心跳上报 |
| `mobile.unpair` | 解绑设备 |

### notification.* — 通知中心

| 方法 | 说明 |
|------|------|
| `notification.list` | 通知列表 |
| `notification.mark_read` / `notification.mark_all_read` | 标记已读 |
| `notification.delete` / `notification.purge` | 删除单条/批量清理 |

### permission.* — 工具授权

| 方法 | 说明 |
|------|------|
| `permission.resolve` | 应答权限请求（allow/deny，供前端弹窗回传） |
| 权限规则 CRUD | 详见正文 `permission.*` 章节（list/get/set 等） |

### provider.* — 多服务商

| 方法 | 说明 |
|------|------|
| `provider.create` / `provider.get` / `provider.list` / `provider.update` / `provider.delete` | Provider 配置 CRUD |
| `provider.set_api_key` | 写入该 Provider 的 API 密钥（OS keyring） |
| `provider.clear_api_key` | 清除该 Provider 的密钥 |

### schedule.* — 定时任务

| 方法 | 说明 |
|------|------|
| `schedule.create` / `schedule.list` / `schedule.delete` | 固定上下文提示词任务创建/列表/软删除 |
| `schedule.update` | expected_revision 比较并交换，保留旧发生记录快照 |
| `schedule.occurrences` | 按 job_id 查询准确发生记录和执行/校验证据 |
| `schedule.enable` / `schedule.disable` | 启用/停用 |
| `schedule.run_now` | 立即触发一次 |

### secrets.* — 旧版 MiniMax 密钥

| 方法 | 说明 |
|------|------|
| `secrets.status` | 密钥来源状态（keyring/env/none） |
| `secrets.set` / `secrets.clear` | 写入/清除系统钥匙串条目 |

### session.* — 会话

| 方法 | 说明 |
|------|------|
| `session.get` | 单个会话详情 |
| `session.update` | 更新会话（如标题） |
| `session.stats` | 会话消息统计 |
| `session.export` | 导出单个会话 |
| `session.archive` / `session.unarchive` | 归档/取消归档（archive 见正文） |
| `session.updateProject` / `session.batchUpdateProject` | 移动到项目（单个/批量） |
| `session.batchArchive` | 批量归档 |

### skill.* — 技能

| 方法 | 说明 |
|------|------|
| `skill.get` | 单个技能详情（list/invoke 见正文） |

### task.* — 任务进度

任务行保留 `status`（pending/running/completed/failed/cancelled/budget_paused/interrupted/requires_review），
并返回可空 `run_id`、`execution_status`、`verification`、`cancel_supported`。关联运行的真实状态优先于旧任务缓存，
状态筛选与计数使用相同投影。`result` 对暂停、中断、失败和取消也保留已保存的部分文本；completed 仅表示执行结束。

`verification` v1 包含 `version:1`、`verdict:passed|failed|unknown|not_applicable`、`reason`、
`requires_review`，并保留 `tool_calls`、`failed_tool_calls`、`failures` 与可选 `validation` 命令诊断。
运行回执、运行 metadata、assistant 消息 metadata 和任务历史共享该证据；消息还保留真实 `status/run_status/partial`。
无历史证据为 unknown/legacy_unverified。Native chat/execute 正常完成时，运行接单快照中的项目校验命令执行一次，
不依赖文件事件；exit0 为 passed，非零为 failed，启动失败/超时为 unknown 并要求核对。失败工具调用和已有核对要求不被通过的命令清除。
未配置命令为 unknown/not_configured，本身不强制核对；plan 为 not_applicable/plan_mode；
截断和非正常终态跳过校验，使用显式原因。Codex chat/execute 接单时固定项目命令和工作区；正常结束后先以 `permission.request`（`tool=project_validation`、`approval_policy=explicit_once`）要求一次宿主 shell 执行授权，携带命令、目录和宿主访问范围。拒绝或授权到期为 unknown/validation_denied 并要求核对，绑定变化为 unknown/validation_workspace_changed 并要求核对；获准后执行并记录相同的真实退出码和输出。服务端复核工作区；验证命令不受 Codex 沙箱约束。旧版 Codex 记录仍可能为 unknown/engine_validation_not_supported。
passed 只证明配置的命令通过，不证明全部交付要求通过。

`task.cancel` 仅对本进程可取消的关联运行发出真实 RunService 取消，返回 `{ok:true,requested:true,task}`。
确认请求时任务仍可显示 running；持久终态在 writer 清理完成后发布。未关联的活动任务拒绝取消，
UI 依据 `cancel_supported` 禁用动作，不能只改任务账本制造已停止假象。

| 方法 | 说明 |
|------|------|
| `task.start` / `task.complete` / `task.cancel` | 任务生命周期 |
| `task.get` / `task.list` | 任务查询 |
| `task.update` | 更新任务步骤/进度 |

### team.* — Agent 团队

| 方法 | 说明 |
|------|------|
| `team.create` / `team.get` / `team.list` / `team.update` / `team.delete` | 团队 CRUD |
| `team.enable` / `team.disable` | 启用/停用团队 |
| `team.spawn` | 触发一次团队运行 |
| `team.run.get` | 团队运行详情 |

`team.spawn` requires an existing `session_id` (or, for older callers, an
existing `parent_session_id`); the selected session determines the workspace.
Any supplied parent session is also checked against host-execution restrictions.
Team execution is write-enabled and only accepts `mode: "execute"`; a read-only
execution scope rejects it. Members use the current permission store and a
team-run-scoped explicit-once consent gate, including parallel members. Pending
consents are exposed in `run.steps` while this service process remains alive;
the team does not inherit the ordinary chat run's consent registry entry.
Team admission uses the same per-session RunService queue as Native, Codex and
manual validation. The queued record and budget family are durable; workspace,
project and host permissions are rechecked at dispatch. An optional
`idempotency_key` replays the same request without running it twice; callers
omitting it retain distinct legacy submissions. Queue cancellation is recorded
as cancelled before execution, while an in-flight cancellation is interrupted.
`budget_tokens` is an optional positive integer; otherwise the service default applies.
`max_parallel` optionally sets this team's direct-member concurrency to 1–16.
When omitted the service uses `MINIMAX_CODE_TEAM_MAX_CONCURRENCY`, or 2 if unset
or invalid; an explicit legacy environment value <= 0 remains unlimited. The
effective value is fixed in `team_max_parallel` on admission and appears in team
progress events. Sequential/review orchestration retains its existing order.
The generic background sub-agent semaphore is independent, so this is not a
service-wide or recursively aggregated concurrency limit.
The team has its own durable budget family, shared by concurrent members and the
service daily ledger. The returned `budget` is a completion snapshot or null if
unavailable; unknown usage remains unknown. Requests use estimated reservations,
not a strict monetary ceiling. The standalone team run is not a normal chat run
and does not inherit ordinary run recovery. `run.cancel` can request cancellation
of a live team owned by this service process; it cancels members and drains
tracked background descendants before recording `interrupted`, because in-flight
side effects may need review. After a service restart there is no live owner to
cancel; team runs cannot be resumed through `run.resume`. Its working-copy boundary
is not an OS sandbox.

### webhook.* — Webhook

Inbound `POST /hooks/{path}` requires a configured HMAC secret; hooks without
one return HTTP 403 without dispatch. Signature mismatch retains its HTTP 200
validation response. After verification, the receiver durably claims the SHA-256
body digest per hook before awaiting the mapped action. GitHub/Gitee delivery IDs
are untrusted correlation hints: the signature covers the body, not those headers.
A completed claim with the same ID, digest, source and interpreted event returns
`ok: true, duplicate: true` without dispatch. A changed event or a legacy completed
record without an event fingerprint requires review. The event header itself is
not authenticated; matching fingerprints do not establish its authenticity.
A repeated body with a different or missing ID, or a reused ID
with a different body, returns HTTP 409 for review; identical bodies from independent
events may therefore require review. Pending or failed claims also return 409 on
retry. A dispatch failure returns HTTP 500 with `ok: false` and an `error_id` for
server-log correlation; exception details and payloads are not returned. A failure
or interruption can follow partial side effects, so reconcile the persisted
`webhook_deliveries` state before manual intervention. Restoring an older backup
rolls the ledger back to its snapshot and cannot protect against deliveries executed
since then; reconcile before reopening hooks. Eligible push events mapped to
code-review currently fail closed because webhook execution is not admitted through
the authorized skill execution boundary; non-push or empty-commit events are skipped.


| 方法 | 说明 |
|------|------|
| `webhook.create` / `webhook.list` / `webhook.update` / `webhook.delete` | Webhook CRUD |
| `webhook.regenerate_secret` | 轮换签名密钥 |

### workflow.* — 工作流

| 方法 | 说明 |
|------|------|
| `workflow.create` / `workflow.list` / `workflow.update` / `workflow.delete` | 工作流 CRUD |
| `workflow.enable` / `workflow.disable` | 启用/停用 |
| `workflow.trigger` | 手动触发一次 |


## 固定上下文调度契约（2026-10-26，Schema036）

仅支持 Local / Native 提示词，payload 必须恰为 `{prompt: nonempty_string}`。
command、self_evolution、use_runservice:false、混合或未知字段在创建、修改、启用、手动触发及实际分派路径拒绝。
旧任务升级后 disabled/needs_rebind，原 payload 保留供导出/查看/删除；不会自动转成可执行提示词。

schedule.create/update 的 context 输入选择 project_id、provider_id/model（必须一起指定，均省略则在此刻固定当前选择）、reasoning_effort、mode(chat/execute/plan)、budget_tokens、timezone。
返回的 context v1 固定 canonical workspace+目录身份、local/native、provider ID/协议/endpoint 配置摘要、模型/强度、顺序权限规则及默认策略版本、hooks_policy=disabled、正 token 上限、校验命令及时区。
密钥不序列化。正预算来自显式值或 MINIMAX_CODE_SCHEDULED_BUDGET_TOKENS / MINIMAX_CODE_RUN_BUDGET_TOKENS；无正上限拒绝。全局模型切换不改变任务，Provider 配置/目录身份漂移及缺失密钥均阻止推理。
快照权限与当前限制取严格交集；全局后续 allow 不扩大授权，ask/deny 可收紧。未知工具 ask；permission.request 的 approval_policy=explicit_once 禁止前端全局 alwaysAllow 自动通过，同时保留实际 tool 名称供差异预览。自动任务禁用全局/插件钩子。

schedule.update 要求 job_id/expected_revision/name/cron_expr/payload/context，成功 revision+1，重新计算未来 next_run_at。已接单发生记录保持旧快照；未接单旧记录显式 superseded，释放旧租约。
schedule.run_now 必须提交 job_id、idempotency_key、expected_revision，返回 `{ok:true,occurrence_id,task_id,run_id:null|string,status,created}`，这是持久接单回执，不能视作执行成功。
同键同 revision 重放先于启停/删除/配置检查；不同 revision 冲突。客户端丢回执后保留原 key+revision。另一个手动意图在同任务租约占用时返回 busy，不积压。

每次发生创建独立项目会话及任务，run 使用 schedule:occurrence_id 稳定键；用户消息、run、发生记录关联和 tasks 表的 run_id 原子提交。
Cron 身份是 job/revision/持久 UTC scheduled_for；SQLite 原子推进 next_run_at，遗漏多个周期合并一次并记录 coalesced_through，下一 due 严格晚于当前时刻。
同一任务跨 revision 独占租约，覆盖排队、授权、工具、校验及清理。停用/删除后未接单 work 不启动，正在运行的 work 仍可清理/取消；重新启用从未来开始。
失败/中断/暂停/需核对暂停后续调度；run.resume 审查恢复使用同一快照和发生租约，子运行终态从权威 run 记录投影。放弃原恢复并启用新发生须提交精确 acknowledge_occurrence_id。
重启已有运行只恢复为 interrupted/需要核对，不盲目重放外部效果；未接单发生可复用同一身份继续接单。幂等接单不构成外部副作用 exactly-once 保证。

schedule.occurrences 返回不可变 prompt/context/revision/source/时间区间、会话/任务/当前 run_id、status/reason，以及已有任务的 verification、execution_status 和部分 result；软删除后历史仍可查询。每页最多 100 条，按 created_at、id 降序排列；响应的 next_cursor 非空时，将其作为下次请求的 before 对象（created_at、id）可读取更早记录。省略 before 仍读取最新一页。
此范围不支持远程主机、Codex 调度、一般命令调度，也尚未提供跨运行原子每日预算账本。

`runtime.research_set` 可选 `provider`（`searxng` 或 `brave`）、`brave_key` 和 `clear_brave_key`；密钥写入服务主机系统密钥环，读取仅返回是否配置。Brave 请求固定域名并拒绝携带密钥重定向；配置不等于网络验证。

`run.research_search` 与 `run.research_read` 接受 `session_id`、`project_id`、`expected_service_id` 和分别的 `query` / `url`，由用户显式发起；网络拒绝规则优先，复用公网 HTTPS 受控读取。搜索仅返回摘要；读取成功在同一事务中创建标记为用户手动读取的独立已完成记录和真实来源，并返回来源 ID。

`host.fetch` 仅接受远端绝对路径，文件传输强制使用 SFTP，不回退到旧式远端 shell SCP；本机目标校验后替换，不自动备份。

`host.connection` 的 `state` 可为 `degraded`：SSH 隧道进程仍在，但连续两次远端服务健康探测未通过；恢复探测成功后返回 `connected`。该状态不代表远端任务结果。

`run.project_summary` 接受 `project_id`、`expected_service_id` 和可选 `run_id`；无 run_id 时返回项目内未归档会话的有界持久运行分类及可确认的进程内执行者标记，有 run_id 时返回同项目绑定的有界已保存文本结果。排除内部子任务及未绑定项目旧记录；服务重启或断线后活动状态需重新核对。

`schedule.update` 可附带 `expected_service_id`；服务端在更新前核对该身份。编辑使用打开时的 `expected_revision`；计划已变更时拒绝更新。已接单发生保留原快照，未接单旧发生可被替代；需要核对状态不会因编辑清除。

`schedule.enable` 可携带 `acknowledge_occurrence_id`、`expected_revision` 与 `expected_service_id`：前者确认已核对的确切发生，后两者校验计划版本和服务身份；仍在运行或清理中的发生不能以此解除。启用未来计划不恢复或重放旧运行。

`session.create` 的引擎交接可选同时提供 `expected_service_id` 与 `handoff_source_session_id`：服务端核对原会话、项目、源/目标引擎及本地默认工作区，并要求新建而非复用；只创建会话，不迁移线程或自动发送消息。

`run.resume` 可附带完整的 `expected_service_id`、`expected_session_id`、`expected_project_id` 和 `expected_cursor`，服务在接单前对照持久运行快照；缺项或已变化时拒绝。调用者仍须审阅外部副作用，运行和文件可能在校验后继续变化；这不是 OS 沙盒或精确恢复。
