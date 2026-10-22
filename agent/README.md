# minimax-code-agent

The Python half of MiniMax Code (package version 1.6.0 plus unreleased fixes).
FastAPI serves JSON-RPC 2.0 over HTTP and pushes events over WebSocket to the
React SPA. Production mode also serves the prebuilt frontend. The optional
stdio transport shares the same handler registry for tests and CLI clients.

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
├── http_server.py         # HTTP / WebSocket transport and SPA hosting
├── ipc/                   # shared JSON-RPC handlers + stdio transport
│   ├── server.py
│   ├── client.py          # client used in tests
│   └── protocol.py        # message envelopes
├── agent/                 # conversation loop, tools/, skills/
├── storage/               # SQLite + incremental migrations
├── scheduler/             # APScheduler wrappers
├── orchestrator/          # subagents and teams
├── permissions/           # tool consent and rules
└── mobile/                # experimental mobile pairing
```

For tests, install `uv sync --frozen --extra dev`. From the repository root,
`pnpm verify:core`, `pnpm verify:replay`, and `pnpm verify:full` provide separate
offline suites. Normal tests use an empty keyring and temporary data directory.
Live tests require both the `live` marker and `--run-live`.

See [verification](../docs/verification.md), [capability boundaries](../docs/capability-baseline.md),
and the [IPC contract](../docs/ipc-contract.md). Workspaces and file overlays are
not OS process sandboxes; long-running remote production use remains planned.
