# Web Frontend — AGENTS.md

Agent-focused guidance for the `web/` package of MiniMax Code.

## Package overview

A React 18 single-page app served by Vite.

| Concern | Tech |
|---------|------|
| Language | TypeScript 5.6 (strict mode) |
| Framework | React 18, function components + Hooks |
| Styling | Tailwind CSS 3.4 |
| State | Zustand |
| IPC | JSON-RPC 2.0 over HTTP POST `/rpc` + WebSocket `/ws` |
| Testing | vitest + @testing-library/react |

## Design system (v0.9 "Aurora")

Tokens live as CSS variables in `src/index.css` and are exposed through Tailwind in `tailwind.config.js`.

```
Surface:  surface-0 (app canvas)
          surface-1 (sidebar / topbar)
          surface-2 (cards / panels)
          surface-3 (hover / elevated)

Borders:  line, line-strong
Ink:      ink-0 (primary), ink-1 (secondary), ink-2 (tertiary)
Accent:   accent, accent-hover, accent-active, accent-contrast
Status:   status-error, status-warning, status-success, status-info
```

Legacy aliases `--minimax-*` (e.g. `minimax-bg`) are kept for backwards compatibility, but new code should use the tokens above.

### Shared UI primitives

`src/ui/` contains the single source of truth for buttons, inputs, badges, modals, etc. Reach for these before writing ad-hoc Tailwind classes:

- `Button` — variants `primary | secondary | ghost | danger | subtle`, sizes `sm | md`, loading state
- `IconButton` — square icon-only button, requires `aria-label`
- `Input` / `Textarea` — form field primitives
- `Badge` — status/label pill with semantic `tone`
- `Panel` — card container with optional header
- `Modal` — accessible dialog with backdrop, Esc, focus trap, footer slot
- `DropdownMenu` — accessible toggle menu with focus trap, click-outside/Esc close
- `Spinner` / `EmptyState`

Import from `../ui` (or `@/ui` in tests, although source code currently prefers relative imports).

## Directory conventions

```
src/
  components/
    layout/       Shell chrome: Sidebar, TopBar, NavItem, ErrorBoundary, Toasts, etc.
    chat/         Chat stream: ChatPanel, MessageList, MessageItem, composer pieces
    right-panel/  Inspector panels: progress, sub-agents, runner, terminal, timeline
    modals/       Dialogs: PermissionRequestModal, ConfirmationDialog, etc.
    panels/       Full-page overlay panels: SkillsPanel, PreviewPanel, CodeReviewPanel, etc.
    settings/     Settings tabs + SettingsPage container
  ui/             Design-system primitives (do not import business logic here)
  stores/         Zustand stores, one slice per domain
  lib/            Shared hooks and pure helpers
  ipc/            IPC transport (`client.ts`), typed API (`typed.ts`), mock (`mock.ts`/`mockData.ts`)
  types/          Shared TypeScript types
```

Rules:
- Keep UI primitives in `ui/` free of stores and IPC calls.
- Components in `components/` should be thin. Split files when they exceed ~250 lines.
- Use relative imports in `src/` (the `import-style.test.ts` enforces this and bans `@/` aliases).
- Co-located tests are fine; any `__tests__/` folder should follow the domain of its parent directory.

## Running & testing

```bash
cd web
pnpm install
pnpm dev          # Vite only; assumes agent is running on VITE_AGENT_URL
pnpm build        # tsc + vite build
pnpm lint         # ESLint
pnpm test         # vitest run
```

Cross-stack e2e lives at the repo root:

```bash
cd "D:\工作\城建院\mm code"
pnpm test:e2e     # Playwright; starts agent + Vite automatically
```

## Notes for agents

- **IPC changes**: update `src/types/ipc.ts`, `src/ipc/typed.ts`, `agent/minimax_code/ipc/handlers_*.py`, and `docs/ipc-contract.md` together.
- **New in v0.9.1**: `session.stats` and `session.export` handlers; `CommandPalette` (Cmd/Ctrl+K) for quick navigation.
- **New in v0.11.0**: composer supports `@agent`, `@repo`, and `#file` mentions. `@repo` and `#file` are resolved via `codebase.search` / `codebase.summarize` and the context is prepended to the outgoing prompt in `MessageInput`. `CodeBlock` renders a source chip when the fence info string contains a file path (e.g. ` ```ts src/auth.ts#L10-20 `).
- **Mock backend**: `src/ipc/mock.ts` must handle every IPC method so the frontend can run without an agent (`VITE_AGENT_MODE=mock`).
- **Components index**: `src/components/index.ts` is the public barrel. Keep its export surface stable when moving files.
- **Tests**: preserve `data-testid` values when restyling; e2e and many unit tests depend on them.
- **Accessibility**: `IconButton` requires `aria-label`; `Modal` handles focus trap and `Escape`.
