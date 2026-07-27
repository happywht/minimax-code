/**
 * Mock backend data — in-memory fixtures and state used by the mock
 * backend (`mock.ts`) so the UI shell can render in a plain browser
 * (or under vitest) without the Python agent.
 *
 * Split out of `client.ts` during the ipc-module restructure; content
 * is preserved verbatim apart from the added `export` modifiers.
 */

import type {
  AgentInfo,
  AgentTeam,
  ModelInfo,
  PluginInfo,
  Project,
  ProviderInfo,
  RunnerInfo,
  ScheduledJob,
  Session,
  SkillInfo,
  TerminalChunk,
  TerminalSession,
} from "../types/ipc";
export const mockSessions = new Map<string, Session>();
export const mockSessionsWithMessages = new Set<string>();
export const mockProjects = new Map<string, Project>([
  [
    "inbox",
    {
      id: "inbox",
      name: "收件箱",
      description: "未归类任务默认目录",
      archived: false,
      created_at: Date.now(),
      updated_at: Date.now(),
    },
  ],
]);
export const mockRuns = new Map<string, { run: import("../types/ipc").AgentRun; steps: import("../types/ipc").AgentRunStep[] }>();
export const mockModels: ModelInfo[] = [
  {
    id: "minimax-M2.7",
    name: "M2.7",
    provider: "MiniMax",
    context_window: 200000,
    supports_tools: true,
    is_default: true,
  },
  {
    id: "minimax-M2.7-fast",
    name: "M2.7 Fast",
    provider: "MiniMax",
    context_window: 128000,
    supports_tools: true,
  },
  {
    id: "minimax-M2.7-pro",
    name: "M2.7 Pro",
    provider: "MiniMax",
    context_window: 1000000,
    supports_tools: true,
  },
  {
    // R59: a mock model that declares reasoning-effort meta, mirroring the
    // backend R58 enrich output (supports_reasoning_effort /
    // reasoning_effort_default / reasoning_effort_options). Keeps the mock
    // backend structurally identical to a real xAI provider's enriched entry
    // so the frontend can render an effort selector in mock mode too. The
    // three MiniMax models above intentionally declare no reasoning-effort
    // meta — they exercise the zero-regression path (no enrich keys).
    id: "grok-1",
    name: "Grok-1",
    provider: "xAI",
    context_window: 200000,
    supports_tools: true,
    provider_id: "provider-mock-xai",
    protocol: "anthropic",
    supports_reasoning_effort: true,
    reasoning_effort_default: "high",
    reasoning_effort_options: [
      { value: "low", id: "low", label: "Low", description: null, default: false },
      { value: "medium", id: "medium", label: "Medium", description: null, default: false },
      { value: "high", id: "high", label: "High", description: null, default: true },
    ],
  },
];
export const mockSkills: SkillInfo[] = [
  {
    id: "commit-helper",
    name: "Commit Helper",
    description: "Draft commit messages from staged diffs",
    enabled: true,
    builtin: true,
  },
  {
    id: "code-review",
    name: "Code Review",
    description: "Review a diff for bugs and style issues",
    enabled: true,
    builtin: true,
  },
];
export const mockAgents: AgentInfo[] = [
  {
    id: "general",
    name: "General",
    description: "General-purpose sub-agent",
    enabled: true,
  },
  {
    id: "researcher",
    name: "Researcher",
    description: "Web research and summarization",
    enabled: true,
  },
];
export const mockJobs: ScheduledJob[] = [];

// v0.8.0 — mock teams
export const mockTeams: AgentTeam[] = [
  {
    id: "team_mock_fullstack",
    name: "fullstack-team",
    description: "A full-stack review team",
    icon: "Users",
    color: "#6366f1",
    agents: ["general", "researcher"],
    orchestration_mode: "parallel",
    enabled: true,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
];

/**
 * Mock plugin registry — mirrors what the Python agent's PluginLoader
 * discovers on disk. Lets the Settings → Plugins tab render in a plain
 * browser. Real data arrives via the ``plugins.*`` IPC namespace.
 */
export const mockPlugins: PluginInfo[] = [
  {
    name: "code-linter",
    version: "0.1.0",
    description: "Pre-edit lint hook for Python and TypeScript.",
    author: "minimax-code",
    homepage: "",
    enabled: true,
    enabled_on_disk: true,
    ok: true,
    error: null,
    path: "agent/plugins/code-linter/plugin.json",
    loaded_at: "2026-07-18T00:00:00",
    has_hooks: true,
    has_mcp: false,
    has_permissions: false,
    entry: null,
  },
  {
    name: "broken-example",
    version: "0.0.0",
    description: "",
    author: "",
    homepage: "",
    enabled: false,
    enabled_on_disk: false,
    ok: false,
    error: "invalid manifest: missing required field `name`",
    path: "agent/plugins/broken-example/plugin.json",
    loaded_at: "",
    has_hooks: false,
    has_mcp: false,
    has_permissions: false,
    entry: null,
  },
];

export const mockProviders: ProviderInfo[] = [
  {
    id: "builtin-minimax",
    name: "MiniMax",
    protocol: "anthropic",
    base_url: "https://api.minimaxi.com/anthropic",
    api_key_configured: false,
    models: [
      { id: "MiniMax-M3", name: "MiniMax-M3", context_window: 200000, supports_tools: true, is_default: true },
      { id: "MiniMax-M3-fast", name: "MiniMax-M3-fast", context_window: 128000, supports_tools: true },
      { id: "MiniMax-Code", name: "MiniMax-Code", context_window: 1000000, supports_tools: true },
    ],
    enabled: true,
    created_at: "2026-06-06T00:00:00Z",
    updated_at: "2026-06-06T00:00:00Z",
  },
];

/**
 * Mock secret store — pretends to be the OS keyring for browser /
 * unit-test runs. Stored values never persist across reloads (it's
 * just an in-process Map), which is fine because the mock is for
 * UI plumbing only. The real backend is the OS Credential Manager.
 */
export const mockSecrets: { keyring: string | null } = { keyring: null };
export const mockTerminalSessions = new Map<string, TerminalSession>();
export const mockTerminalChunks = new Map<string, TerminalChunk[]>();
export const mockRunners: RunnerInfo[] = [
  {
    id: "native",
    label: "Native shell",
    kind: "native",
    available: true,
    command: null,
    version: null,
    reason: null,
    supports_prompt: false,
    supports_terminal: true,
  },
  {
    id: "codex-cli",
    label: "Codex CLI",
    kind: "external_cli",
    available: false,
    command: null,
    version: null,
    reason: "codex executable not found on PATH",
    supports_prompt: true,
    supports_terminal: true,
  },
  {
    id: "claude-code-cli",
    label: "Claude Code CLI",
    kind: "external_cli",
    available: true,
    command: "claude",
    version: "2.1.168",
    reason: null,
    supports_prompt: true,
    supports_terminal: true,
  },
];

export function makeMockTerminalSession(opts: {
  command: string;
  cwd?: string;
  session_id?: string | null;
  output?: string;
}): TerminalSession {
  const now = Date.now() / 1000;
  const id = `term_mock_${Math.random().toString(36).slice(2, 10)}`;
  const session: TerminalSession = {
    id,
    command: opts.command,
    cwd: opts.cwd ?? "",
    session_id: opts.session_id ?? null,
    run_id: opts.session_id ? `run_mock_${Math.random().toString(36).slice(2, 10)}` : null,
    status: "completed",
    started_at: now,
    updated_at: now,
    completed_at: now,
    exit_code: 0,
    error: null,
    next_seq: 2,
  };
  const chunks: TerminalChunk[] = [
    {
      seq: 1,
      stream: "stdout",
      text: opts.output ?? `$ ${opts.command}\n(mock terminal output)\n`,
      received_at: now,
    },
  ];
  mockTerminalSessions.set(id, session);
  mockTerminalChunks.set(id, chunks);
  return session;
}
