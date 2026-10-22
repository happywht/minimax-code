/** Ephemeral browser authentication. Tokens never enter URLs or localStorage. */
const required = new Map<string, boolean>();
const tokens = new Map<string, string>();
const pending = new Map<string, Promise<void>>();

export async function rememberHealth(base: string, response: Response): Promise<void> {
  if (!response.ok || typeof response.json !== "function") return;
  try {
    const body = await response.json() as { auth_required?: boolean };
    required.set(base, body.auth_required === true);
  } catch {
    // Legacy health probes may have no JSON body.
  }
}

export function authHeaders(base: string): Record<string, string> {
  const token = tokens.get(base);
  return token ? { Authorization: `Bearer ${token}` } : {};
}

export function authProtocols(base: string): string[] {
  const token = tokens.get(base);
  return token ? [`minimax-auth.${token}`] : [];
}

export async function ensureSession(base: string, refresh = false): Promise<void> {
  if (!required.get(base) || (tokens.has(base) && !refresh)) return;
  const active = pending.get(base);
  if (active) return active;
  const task = (async () => {
    const response = await fetch(`${base}/auth/session`, {
      method: "POST", credentials: "include",
      headers: { "X-Minimax-Client": "web", ...authHeaders(base) },
    });
    if (!response.ok) throw new Error(`Application authentication failed (HTTP ${response.status})`);
    const body = await response.json() as { token?: string };
    if (!body.token) throw new Error("Application authentication did not return a token");
    tokens.set(base, body.token);
  })();
  pending.set(base, task);
  try { await task; } finally { pending.delete(base); }
}
