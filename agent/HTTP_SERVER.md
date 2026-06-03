# HTTP Server Mode

> Status: v0.2.0 — the Tauri sidecar is gone. The Python agent is now a
> plain localhost HTTP + WebSocket service.

## Quick start

```bash
cd agent
uv sync
uv run python -m minimax_code
# → agent server listening on http://127.0.0.1:8765
```

Override the port with `--http-port` (CLI) or `MINIMAX_CODE_HTTP_PORT` (env).
Override the bind address with `--http-host` or `MINIMAX_CODE_HTTP_HOST`.
**Do not bind `0.0.0.0`** in v0.2.0 — the agent is local-only and ships
without auth.

## Endpoints

| Method | Path     | Purpose                                       |
|--------|----------|-----------------------------------------------|
| GET    | `/health`| Liveness: `{ok, version, uptime_s}`           |
| POST   | `/rpc`   | JSON-RPC 2.0 request → response (one-shot)    |
| GET    | `/ws`    | WebSocket: server-push events                 |

CORS is locked to `http://localhost:5173` + `http://127.0.0.1:5173`
(Vite dev only) with credentials enabled.

## Curl examples

```bash
# Liveness
curl http://127.0.0.1:8765/health
# → {"ok": true, "version": "0.2.0", "uptime_s": 12}

# One-shot RPC — returns 200 with a JSON-RPC envelope
curl -X POST http://127.0.0.1:8765/rpc \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"session.list","params":{}}'

# Unknown method → 200 with an error envelope (not 4xx)
curl -X POST http://127.0.0.1:8765/rpc \
  -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":"1","method":"nope","params":{}}'
# → {"jsonrpc":"2.0","id":"1","error":{"code":-32601,"message":"unknown method: nope"}}
```

## WebSocket

Connect to `ws://127.0.0.1:8765/ws`. The server immediately pushes a
lifecycle frame:

```json
{"jsonrpc":"2.0","method":"agent.ready","params":{"server":"minimax-code-agent","version":"0.2.0"}}
```

After that, every event handlers emit via `ctx.emit(...)` (e.g.
`agent.message_chunk`, `agent.status`, `task.progress`, …) is forwarded
to all connected sockets in the wire shape:

```json
{"jsonrpc":"2.0","method":"agent.message_chunk","params":{"session_id":"...","delta":"Hi","done":false}}
```

Note: the internal `Event` shape (`{event, data}`) is translated to
`{method, params}` on the wire so the web client sees one consistent
envelope. Reconnect is the client's job (exponential backoff).

## Stdio vs HTTP

The two modes share the same handler registry — `register_app_handlers`
lights them both up. Run only one per process.

| Mode     | When to use                                      | Entry point          |
|----------|--------------------------------------------------|----------------------|
| `--http` | **Default.** Web client, curl, anything on TCP.  | uvicorn + FastAPI    |
| `--stdio`| Tests, CLI debugging, ad-hoc local tooling.      | Line-delim JSON-RPC  |

```bash
# Stdio mode (legacy; tests still use it)
uv run python -m minimax_code --stdio
```

The stdio path is unchanged in v0.2.0. The HTTP path is a thin
transport on top of the same `IPCServer.handle_request` method.

## Auth

None in v0.2.0. Binds `127.0.0.1` so other machines on the LAN cannot
reach the agent. Add a token check when multi-user or remote access
arrives.
