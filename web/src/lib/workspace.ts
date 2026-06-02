/**
 * Workspace helpers — localStorage-backed list of named workspaces
 * and the currently-active selection. The backend (`agent/.../db.py`)
 * doesn't have a workspace table yet, so the front-end treats each
 * workspace as an opaque name + path tuple. The IPC layer keys
 * `session.*` calls by workspace later; for now switching just
 * triggers a `sessionStore.refresh()` so the UI shows the correct
 * session set.
 *
 * Keys:
 *   - `minimax-code:workspaces`  -> JSON array of `{name, path}`
 *   - `minimax-code:current-workspace` -> string name
 *
 * All reads are best-effort: malformed JSON is treated as "empty"
 * rather than throwing, so a corrupted entry can't break the boot
 * sequence.
 */

const STORAGE_KEYS = {
  workspaces: "minimax-code:workspaces",
  current: "minimax-code:current-workspace",
} as const;

export interface WorkspaceEntry {
  name: string;
  path: string;
}

/**
 * The first workspace we seed a fresh install with. We try the
 * Vite-injected `MINIMAX_WORKSPACE_NAME` env (developer override)
 * and fall back to "default" so a brand-new window always has at
 * least one selectable entry.
 */
function defaultWorkspaceName(): string {
  try {
    // Vite only exposes `import.meta.env.*` for vars defined in
    // config; for an out-of-the-box experience we just hard-code the
    // fallback. Power users can edit the constant here.
    const envName = (import.meta as ImportMeta & { env?: Record<string, string | undefined> })
      .env?.MINIMAX_WORKSPACE_NAME;
    if (envName && envName.trim().length > 0) return envName.trim();
  } catch {
    // import.meta.env unavailable (e.g. raw node test runner) — skip
  }
  return "default";
}

function safeParse<T>(raw: string | null, fallback: T): T {
  if (raw == null) return fallback;
  try {
    return JSON.parse(raw) as T;
  } catch {
    return fallback;
  }
}

/** Read the workspace list. Seeds a single default entry on first use. */
export function listWorkspaces(): WorkspaceEntry[] {
  if (typeof localStorage === "undefined") return [];
  const raw = localStorage.getItem(STORAGE_KEYS.workspaces);
  const parsed = safeParse<WorkspaceEntry[] | null>(raw, null);
  if (Array.isArray(parsed) && parsed.length > 0) {
    return parsed.filter((w) => w && typeof w.name === "string");
  }
  // First run — seed with a single default workspace.
  const seeded: WorkspaceEntry[] = [{ name: defaultWorkspaceName(), path: "" }];
  saveWorkspaces(seeded);
  return seeded;
}

export function saveWorkspaces(workspaces: WorkspaceEntry[]): void {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(STORAGE_KEYS.workspaces, JSON.stringify(workspaces));
}

/** Read the active workspace name. Seeds the default on first use. */
export function getCurrentWorkspace(): string {
  if (typeof localStorage === "undefined") return defaultWorkspaceName();
  const stored = localStorage.getItem(STORAGE_KEYS.current);
  if (stored && stored.trim().length > 0) return stored;
  const seeded = defaultWorkspaceName();
  setCurrentWorkspace(seeded);
  return seeded;
}

export function setCurrentWorkspace(name: string): void {
  if (typeof localStorage === "undefined") return;
  localStorage.setItem(STORAGE_KEYS.current, name);
}

export const WORKSPACE_STORAGE_KEYS = STORAGE_KEYS;
