export type ScopeChangeGuard = () => boolean;
let guard: ScopeChangeGuard | null = null;

/** Register a UI guard for user-initiated project/session changes. */
export function registerScopeChangeGuard(next: ScopeChangeGuard): () => void {
  guard = next;
  return () => { if (guard === next) guard = null; };
}

export function allowScopeChange(): boolean {
  return guard?.() ?? true;
}
