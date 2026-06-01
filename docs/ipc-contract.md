# IPC Contract — `docs/ipc-contract.md`

> Authoritative description of the wire format between the React UI,
> the Tauri Rust shell, and the Python agent. Any change to message
> types or framing MUST be reflected here and bump the protocol
> version in `agent/minimax_code/ipc/protocol.py`.

## 1. Topology

```
┌────────────────┐  invoke("ipc_request")  ┌─────────────────┐  stdin   ┌────────────────┐
│  React (web/)  │ ───────────────────────▶│  Tauri (Rust)   │ ────────▶│ Python sidecar │
│                │ ◀───────────────────────│                 │ ◀────────│ (asyncio)      │
│  listen("ipc:*")│   emit("ipc:response")  │                 │  stdout  │                │
└────────────────┘                          └─────────────────┘          └────────────────┘
       JSON-RPC 2.0                          bridge (ipc.rs)                 JSON-RPC 2.0
```

The Rust bridge is intentionally thin — it parses nothing, only
multiplexes bytes. Validation lives in Python.

## 2. Framing

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
on stdout. The frontend listens to the `ipc:event` Tauri event and
filters by the inner `event` field.

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

## 8. Lifecycle events (Tauri-level, not JSON-RPC)

The Rust sidecar also emits a *non-JSON-RPC* Tauri event
`ipc:sidecar` when the Python process starts or crashes:

```json
{"status":"started"}
{"status":"error","error":"agent exited with code 1"}
```

The frontend uses this to gate the UI (e.g. disable Send if the
agent is down).

## 9. Half-close handling

The Python server treats `stdin.readline() == ""` (EOF) as a clean
shutdown signal. The Rust bridge mirrors this: when the Tauri webview
closes, Tauri's `tauri::AppHandle` drops the `AppState`, which kills
the child via `kill_on_drop(true)`. There is no separate "goodbye"
message — EOF is the goodbye.

## 10. Buffering

- The Python agent sets `PYTHONUNBUFFERED=1` and `PYTHONIOENCODING=utf-8`
  so stdout/stderr flush line-by-line. The Rust bridge spawns with
  `Stdio::piped()` and reads with `BufReader::lines()` which is
  already line-buffered.
- On Windows, `CommandExt::creation_flags(0x08000000)` (`CREATE_NO_WINDOW`)
  is **not** set — the sidecar inherits a console so its stderr is
  visible during development. The Tauri `setup` script can change this
  later if needed.

## 11. Test surface

| Layer       | Test file                          | What it covers                          |
|-------------|------------------------------------|-----------------------------------------|
| Python IPC  | `agent/tests/test_ipc.py`          | `ping`, `status`, parse error, stream   |
| Python unit | `agent/tests/test_*.py` (per task) | per-module                              |
| Frontend    | `web/tests/*.test.ts(x)`           | type contracts + component smoke        |
| End-to-end  | `tests/e2e/smoke.spec.ts`          | Playwright (TBD in `ui-shell` task)     |

## 12. Versioning

The protocol version lives in `agent/minimax_code/__init__.py`
(`__version__`) and is mirrored in the Tauri `Cargo.toml`
(`version`). A breaking change to the IPC contract MUST bump the
**minor** version of the agent and document the migration in this
file.
