# minimax-code-agent

The Python half of MiniMax Code. Runs as a Tauri sidecar and exposes the
agent API to the React frontend over a JSON-RPC 2.0 stdio channel.

## Run

```bash
# From the repo root:
pnpm py:install   # uv sync in this directory
pnpm py:run       # python -m minimax_code

# Or directly:
cd agent
uv sync
uv run python -m minimax_code
```

## Layout

```
minimax_code/
├── __main__.py            # CLI entry
├── config.py
├── logging_setup.py
├── ipc/                   # JSON-RPC over stdio server
│   ├── server.py
│   ├── client.py          # client used in tests
│   └── protocol.py        # message envelopes
├── agent/                 # conversation loop (Phase 1.2)
├── tools/                 # file_ops / terminal / edit / search
├── skills/                # SKILL.md loader + registry
├── storage/               # SQLite (Phase 1.3)
├── scheduler/             # APScheduler wrappers
├── orchestrator/          # multi-agent (Phase 2)
├── auth/                  # permissions
└── mobile/                # mobile pairing (Phase 2)
```

See `../docs/ipc-contract.md` for the wire format.
