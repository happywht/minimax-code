# MiniMax Code

Desktop AI coding agent — Tauri 2.x (Rust) + React + Python sidecar.
复刻 MiniMax Code 全量功能。

> Skeleton milestone. The IPC channel and a hello-world round-trip
> work; storage, agent loop, and full UI land in subsequent tasks.

## Stack

| Layer       | Tech                                                 |
|-------------|------------------------------------------------------|
| Shell       | Tauri 2.x (Rust)                                     |
| Frontend    | React 18 + Vite + TypeScript + Tailwind + Zustand    |
| Agent       | Python 3.11+ (asyncio JSON-RPC 2.0 over stdio)       |
| IPC         | JSON-RPC 2.0 line-delimited JSON, single `\n` framing |
| Storage     | SQLite (aiosqlite) — *not yet wired*                 |
| Scheduler   | APScheduler — *not yet wired*                        |
| LLM         | MiniMax API via httpx — *not yet wired*              |

See [`docs/architecture.md`](docs/architecture.md) for the full design
and [`docs/ipc-contract.md`](docs/ipc-contract.md) for the wire format.

## Project layout

```
.
├── docs/                       # Architecture & IPC contract
│   ├── architecture.md
│   └── ipc-contract.md
├── scripts/                    # Standalone runners
│   ├── dev.mjs                 # Vite + Python agent in parallel
│   ├── dev-agent.mjs
│   ├── start-agent.sh
│   └── start-agent.ps1
├── src-tauri/                  # Tauri Rust shell
│   ├── Cargo.toml
│   ├── tauri.conf.json
│   ├── capabilities/default.json
│   └── src/
│       ├── main.rs
│       ├── lib.rs              # Tauri builder + setup
│       ├── ipc.rs              # stdio <-> Tauri event bridge
│       └── commands.rs         # `invoke` handlers
├── web/                        # React + Vite frontend
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── tsconfig.json
│   ├── index.html
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx
│   │   ├── components/         # Sidebar / ChatPanel / MessageInput / ProgressPanel
│   │   ├── stores/             # Zustand chat store
│   │   ├── ipc/                # IPC client (wraps Tauri invoke/listen)
│   │   └── types/              # shared JSON-RPC type contracts
│   └── tests/                  # vitest
├── agent/                      # Python agent (uv-managed)
│   ├── pyproject.toml
│   ├── minimax_code/
│   │   ├── __init__.py
│   │   ├── __main__.py         # `python -m minimax_code`
│   │   ├── app.py              # handler registration seam
│   │   ├── config.py
│   │   ├── logging_setup.py
│   │   ├── ipc/                # asyncio JSON-RPC server
│   │   ├── agent/              # conversation loop (stub)
│   │   ├── tools/              # file_ops / terminal / edit / search (stub)
│   │   ├── skills/             # SKILL.md loader (stub)
│   │   ├── storage/            # SQLite layer (stub)
│   │   ├── scheduler/          # APScheduler (stub)
│   │   ├── orchestrator/       # multi-agent (Phase 2, stub)
│   │   ├── auth/               # permissions (stub)
│   │   └── mobile/             # mobile pairing (Phase 2, stub)
│   ├── skills/                 # built-in SKILL.md files
│   └── tests/                  # pytest + pytest-asyncio
├── tests/                      # e2e (TBD)
├── package.json                # pnpm workspace root
├── pnpm-workspace.yaml
└── README.md
```

## Prerequisites

- **Node.js 20+** (tested on 22.18)
- **pnpm 9+** (run `npm i -g pnpm` if missing)
- **Rust 1.77+** with the MSVC toolchain (Windows) or `build-essential` (Linux)
- **Python 3.11+** (tested on 3.12)
- **uv** — `pip install uv` or grab the standalone binary from
  [astral-sh/uv](https://docs.astral.sh/uv/)

## Quick start (dev)

```bash
# 1. Install JS deps (Tauri CLI, Vite, React, …)
pnpm install

# 2. Install Python deps for the agent
pnpm py:install

# 3. Run Vite + Python agent in parallel
pnpm dev

# 4. Or run the full Tauri app (Vite + Rust shell + Python agent)
pnpm tauri dev
```

The Vite dev server listens on `http://localhost:5173`. Open it in a
browser to see the skeleton UI. To run the full Tauri app, use
`pnpm tauri:dev` — this is what the demo **really** needs because the
Tauri `invoke` / `listen` APIs only exist inside the Tauri webview.

### Run the agent alone

```bash
# bash / WSL
./scripts/start-agent.sh

# PowerShell
.\scripts\start-agent.ps1

# or directly via uv
cd agent && uv run python -m minimax_code
```

You can drive it manually by piping a JSON-RPC request into stdin:

```bash
echo '{"jsonrpc":"2.0","id":"1","method":"ping"}' \
  | uv run python -m minimax_code
```

## The hello round-trip

1. Launch `pnpm tauri:dev` (or open Vite at `localhost:5173` inside
   the Tauri webview).
2. Type `hello` in the input box and press **Enter**.
3. The Rust bridge forwards the request to Python over stdio.
4. The Python agent's `agent.send_message` handler emits 4
   `agent.message_chunk` events, then a final response.
5. The React store appends each chunk to a streaming bubble in the
   chat panel.

## IPC at a glance

- One JSON object per line, `\n`-terminated.
- Frontend → Python: `{jsonrpc, id, method, params}`.
- Python → Frontend: response (`{jsonrpc, id, result/error}`) or push
  event (`{jsonrpc, event, data}`).
- The Rust sidecar (`src-tauri/src/ipc.rs`) is a dumb bridge: it
  spawns Python, reads stdout, and re-emits each line as a Tauri
  event (`ipc:response` for responses, `ipc:event` for pushes).

See [`docs/ipc-contract.md`](docs/ipc-contract.md) for the full
schema, error codes, and event catalogue.

## Tests

```bash
# Python (uv)
pnpm py:test

# Frontend (vitest)
pnpm test
```

## Tasks ahead

This skeleton is the foundation. The next tasks build on it:

- `storage-layer` — SQLite + DAOs (sessions, messages, tasks, …)
- `agent-core` — LLM client, tool registry, conversation loop
- `ui-shell` — full React component tree (ChatPanel + sidebar + …)
- `skills-system` — SKILL.md loader + enable/disable + invocation
- `scheduler` — APScheduler integration

Each task must keep `docs/architecture.md` and `docs/ipc-contract.md`
in sync.
