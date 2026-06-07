/**
 * Generic eviction utilities for capping in-memory store data.
 *
 * Frontend stores (chat, tasks, subAgent, teamRun, notifications)
 * accumulate entries without bound. These helpers enforce a max size
 * by dropping the oldest entries first.
 *
 * Design: pure functions, zero side-effects, easy to test.
 * Stores call them inside `set()` — no hooks or subscriptions needed.
 *
 * @module eviction
 */

// ─── Array trimming ────────────────────────────────────────────────

/**
 * Keep only the **last** `maxSize` items of an array.
 * Returns the same reference when under the limit (avoids unnecessary copies).
 */
export function trimArray<T>(arr: T[], maxSize: number): T[] {
  if (arr.length <= maxSize) return arr;
  return arr.slice(arr.length - maxSize);
}

// ─── Record (map) eviction ─────────────────────────────────────────

/**
 * Evict the oldest entries from a `Record<string, T>` when it
 * exceeds `maxSize`. "Oldest" is determined by `getTimestamp`.
 *
 * Returns the same reference when under the limit.
 * When eviction is needed, returns a **new** object with the oldest
 * entries removed.
 *
 * @example
 * ```ts
 * // Cap subAgent runs at 50
 * set((s) => ({
 *   runs: evictOldest(s.runs, 50, (r) => r.updated_at),
 * }));
 * ```
 */
export function evictOldest<T>(
  record: Record<string, T>,
  maxSize: number,
  getTimestamp: (entry: T) => number,
): Record<string, T> {
  const entries = Object.entries(record);
  if (entries.length <= maxSize) return record;

  // Sort ascending by timestamp (oldest first)
  entries.sort(([, a], [, b]) => getTimestamp(a) - getTimestamp(b));

  // Keep only the last maxSize entries (newest)
  const keepCount = Math.min(maxSize, entries.length);
  const next: Record<string, T> = {};
  for (let i = entries.length - keepCount; i < entries.length; i++) {
    const [key, value] = entries[i];
    next[key] = value;
  }
  return next;
}

// ─── Recommended limits ────────────────────────────────────────────

/** Max messages kept per session in the chat store. */
export const MAX_MESSAGES = 500;

/** Max task progress entries. */
export const MAX_TASKS = 100;

/** Max sub-agent run entries. */
export const MAX_SUBAGENT_RUNS = 50;

/** Max team run entries. */
export const MAX_TEAM_RUNS = 30;

/** Max notification entries. */
export const MAX_NOTIFICATIONS = 200;
